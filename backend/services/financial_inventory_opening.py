"""Financial inventory opening — explicit book value, not Published HPP.

A posted opening is a frozen itemized acquisition and, when the general
ledger does not already contain that inventory, one journal_core entry.
Published Customer Pricing is never copied in as an approved book amount.
Nothing here activates a production effective date or backfills stock.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.accounting_audit import record_accounting_event
from services.accounting_periods import PeriodLockedError
from services.chart_of_accounts import get_account
from services.inventory_accounting import financial_inventory_item_key, parse_business_date
from services.inventory_financial_locks import (
    LOCK_CONFLICT_CODE,
    LOCK_CONFLICT_MESSAGE,
    acquire_item_locks,
    release_item_locks,
)
from services.journal_core import (
    find_primary_journal_for_source,
    post_balanced_journal,
    reverse_journal,
)
from services.opening_balance import SOURCE_OPENING_BALANCE
from services.purchase_accounting_settings import (
    SETTINGS_KEY as PURCHASE_SETTINGS_KEY,
    _validate_inventory,
    get_purchase_accounting_settings,
)
from services.purchase_common import actor_snapshot, money_round, now_iso, qty_round

COLLECTION = "financial_inventory_openings"
SNAPSHOTS = "financial_inventory_opening_snapshots"
CLAIMS = "financial_inventory_opening_claims"
CLAIM_ID = "financial_inventory_opening"
ISSUE_COLLECTION = "inventory_issue_accounting"

SOURCE_TYPE = "financial_inventory_opening"
SOURCE_REVERSAL = "financial_inventory_opening_reversal"
SOURCE_APPROVED = "APPROVED_OPENING"
REJECTED_SOURCES = frozenset({
    "PUBLISHED_CUSTOMER_PRICING_FALLBACK",
    "PUBLISHED",
    "AUTO_HPP",
    "PUBLISHED_PRICING",
})

STATUS_BUILDING = "BUILDING"
STATUS_POSTED = "POSTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_REVERSED = "REVERSED"
STATUS_NEEDS_RECOVERY = "NEEDS_RECOVERY"

TARGET_EFFECTIVE_DATE = "2026-10-01"
TARGET_TIMEZONE = "Asia/Jakarta"
WIB = timezone(timedelta(hours=7))
LEASE_SECONDS = 120

# Counter-account safety. These types are never selected as the opening credit.
# The actual account still has to come from configuration or an existing voucher.
REJECTED_OFFSET_TYPES = frozenset({"APAY", "REVE", "EXPS", "OEXP", "COGS", "AREC", "BANK", "INTR"})
ALLOWED_OFFSET_TYPES = frozenset({"EQTY", "OCLY", "OASS", "OCAS", "LTLY"})

PERM_VIEW = "inventory_valuation.view"
PERM_POST = "inventory_valuation.manage_opening"


class OpeningBlocked(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 409, extra: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.extra = extra or {}

    def as_detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def _http_from_blocked(exc: OpeningBlocked) -> HTTPException:
    return HTTPException(exc.status_code, exc.as_detail())


def _dec(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _money(value: Any) -> Decimal:
    try:
        return _dec(value).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise OpeningBlocked("INVALID_AMOUNT", "Nominal opening tidak valid.", status_code=400)


def wib_business_date(raw: Any) -> Optional[str]:
    """Calendar date in Asia/Jakarta.

    A bare YYYY-MM-DD is already a business date. A timestamp is converted to
    WIB. Ambiguous values return None and must not be treated as in-range.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    if len(text) == 10 and parse_business_date(text):
        return text
    stamp = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return parse_business_date(text[:10]) if len(text) >= 10 else None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(WIB).date().isoformat()


async def _after_step(step: str) -> None:
    """Test seam. Production callers leave this as a no-op."""
    return None


async def ensure_indexes(db) -> None:
    await db[COLLECTION].create_index("id", unique=True)
    await db[COLLECTION].create_index(
        "client_request_id",
        unique=True,
        partialFilterExpression={"client_request_id": {"$type": "string"}},
        name="uniq_financial_opening_request",
    )
    await db[COLLECTION].create_index(
        "effective_date",
        unique=True,
        partialFilterExpression={"status": STATUS_POSTED},
        name="uniq_posted_financial_opening_date",
    )
    await db[SNAPSHOTS].create_index("id", unique=True)
    await db[SNAPSHOTS].create_index(
        "client_request_id",
        unique=True,
        partialFilterExpression={"client_request_id": {"$type": "string"}},
        name="uniq_financial_opening_snapshot_request",
    )
    await db[CLAIMS].update_one(
        {"_id": CLAIM_ID},
        {"$setOnInsert": {"state": "IDLE", "lease_owner": None, "lease_expires_at": None, "client_request_id": None}},
        upsert=True,
    )
    from services.inventory_financial_locks import ensure_indexes as ensure_lock_indexes

    await ensure_lock_indexes(db)


def _public(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {key: value for key, value in doc.items() if key != "_id"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_is_expired(raw, *, now: Optional[datetime] = None) -> bool:
    current = now or _utc_now()
    text = str(raw or "").strip()
    if not text:
        return True
    stamp = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) <= current


async def _acquire_lease(db, request_id: str) -> dict:
    """Take the opening lease with a new token. Request id is not a takeover right."""
    token = str(uuid.uuid4())
    now = _utc_now()
    expires = (now + timedelta(seconds=LEASE_SECONDS)).isoformat()
    try:
        await db[CLAIMS].update_one(
            {"_id": CLAIM_ID},
            {"$setOnInsert": {
                "state": "IDLE",
                "lease_token": None,
                "lease_expires_at": None,
                "client_request_id": None,
            }},
            upsert=True,
        )
    except DuplicateKeyError:
        pass
    claimed = await db[CLAIMS].find_one_and_update(
        {
            "_id": CLAIM_ID,
            "$or": [
                {"state": {"$in": ["IDLE", "FAILED", STATUS_NEEDS_RECOVERY, STATUS_BLOCKED]}},
                {"lease_token": {"$in": [None, ""]}},
                {"lease_expires_at": {"$in": [None, ""]}},
                {"state": STATUS_BUILDING, "lease_expires_at": {"$lt": now.isoformat()}},
            ],
        },
        {"$set": {
            "state": STATUS_BUILDING,
            "lease_token": token,
            "client_request_id": request_id,
            "lease_expires_at": expires,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed or str(claimed.get("lease_token") or "") != token:
        raise OpeningBlocked("OPENING_IN_PROGRESS", "Opening finansial sedang diproses oleh proses lain.")
    claimed.pop("_id", None)
    return claimed


async def _require_lease(db, token: str) -> None:
    """Fencing token must still be the unexpired holder before a critical write."""
    row = await db[CLAIMS].find_one({"_id": CLAIM_ID}, {"_id": 0})
    if (
        not row
        or str(row.get("lease_token") or "") != str(token or "")
        or _lease_is_expired(row.get("lease_expires_at"))
    ):
        raise OpeningBlocked(
            "OPENING_LEASE_LOST",
            "Lease opening tidak lagi sah. Penulisan dihentikan.",
        )


async def _release_lease(db, token: str, state: str) -> None:
    """Release only while this token is still the unexpired owner."""
    now = _utc_now().isoformat()
    await db[CLAIMS].update_one(
        {
            "_id": CLAIM_ID,
            "lease_token": str(token or ""),
            "lease_expires_at": {"$gt": now},
        },
        {"$set": {"state": state, "lease_expires_at": None, "lease_token": None}},
    )


def _line_fingerprint(lines: List[dict]) -> str:
    parts = []
    for line in lines or []:
        parts.append("|".join([
            str(line.get("master_item_id") or ""),
            str(_dec(line.get("qty"))),
            str(_money(line.get("unit_cost"))),
            str(line.get("base_uom") or "").strip().upper(),
        ]))
    return "\n".join(sorted(parts))


def _same_opening_payload(
    existing: dict,
    effective: str,
    lines: List[dict],
    total: Decimal,
    snapshot_id: str = "",
) -> bool:
    stored_snapshot = str(existing.get("snapshot_id") or "")
    incoming_snapshot = str(snapshot_id or "")
    if incoming_snapshot and stored_snapshot and incoming_snapshot != stored_snapshot:
        return False
    return (
        str(existing.get("effective_date") or "") == effective
        and _money(existing.get("total")) == _money(total)
        and _line_fingerprint(existing.get("lines") or []) == _line_fingerprint(lines)
    )


def _normalize_lines(raw_lines: Any) -> List[dict]:
    if not isinstance(raw_lines, list) or not raw_lines:
        raise OpeningBlocked("OPENING_LINES_REQUIRED", "Baris opening finansial wajib diisi.", status_code=400)
    lines = []
    seen = set()
    for raw in raw_lines:
        if not isinstance(raw, dict):
            raise OpeningBlocked("OPENING_LINE_INVALID", "Baris opening tidak valid.", status_code=400)
        source = str(raw.get("source") or SOURCE_APPROVED).strip()
        if source in REJECTED_SOURCES:
            raise OpeningBlocked(
                "PUBLISHED_NOT_BOOK_VALUE",
                "HPP Published bukan saldo buku yang sudah disetujui.",
                status_code=400,
            )
        if source != SOURCE_APPROVED:
            raise OpeningBlocked(
                "OPENING_SOURCE_UNAPPROVED",
                "Sumber opening finansial harus APPROVED_OPENING.",
                status_code=400,
            )
        mid = str(raw.get("master_item_id") or "").strip()
        if not mid:
            raise OpeningBlocked("OPENING_ITEM_REQUIRED", "Item opening wajib diisi.", status_code=400)
        if mid in seen:
            raise OpeningBlocked("OPENING_ITEM_DUPLICATE", "Item opening tidak boleh ganda.", status_code=400)
        seen.add(mid)
        if raw.get("qty") in (None, "") or raw.get("unit_cost") in (None, ""):
            raise OpeningBlocked(
                "OPENING_COST_MISSING",
                "Qty atau harga pokok opening kosong, bukan nol.",
                status_code=400,
            )
        try:
            qty = Decimal(str(raw.get("qty")))
            unit = Decimal(str(raw.get("unit_cost")))
        except (InvalidOperation, ValueError):
            raise OpeningBlocked("OPENING_LINE_INVALID", "Qty atau harga opening tidak valid.", status_code=400)
        if qty < 0:
            raise OpeningBlocked("OPENING_QTY_NEGATIVE", "Qty opening tidak boleh negatif.", status_code=400)
        if unit <= 0:
            raise OpeningBlocked(
                "OPENING_COST_MISSING",
                "Harga pokok opening kosong atau nol tidak boleh diposting sebagai nilai buku.",
                status_code=400,
            )
        base_uom = str(raw.get("base_uom") or "").strip()
        if not base_uom:
            raise OpeningBlocked("OPENING_UOM_MISSING", "Base UOM opening wajib diisi.", status_code=400)
        total = _money(qty * unit)
        lines.append({
            "line_id": str(raw.get("line_id") or uuid.uuid4()),
            "master_item_id": mid,
            "financial_inventory_item_key": financial_inventory_item_key({"master_item_id": mid}),
            "base_uom": base_uom,
            "qty": qty_round(qty),
            "unit_cost": money_round(unit),
            "total": money_round(total),
            "source": SOURCE_APPROVED,
        })
    return lines


async def _validate_uom(db, lines: List[dict]) -> None:
    for line in lines:
        master = await db.master_items.find_one({"id": line["master_item_id"]}, {"_id": 0, "unit": 1, "item_type": 1})
        if not master:
            raise OpeningBlocked("OPENING_ITEM_UNKNOWN", "Master item opening tidak ditemukan.", status_code=400)
        if str(master.get("item_type") or "").upper() not in {"", "INVENTORY"}:
            raise OpeningBlocked("OPENING_ITEM_NOT_INVENTORY", "Item bukan persediaan.", status_code=400)
        master_uom = str(master.get("unit") or "").strip()
        if master_uom and master_uom.upper() != str(line["base_uom"]).upper():
            raise OpeningBlocked(
                "OPENING_UOM_AMBIGUOUS",
                "Base UOM opening tidak sama dengan satuan master.",
                status_code=400,
            )


def _opening_total(lines: List[dict]) -> Decimal:
    return _money(sum((_dec(line["total"]) for line in lines), Decimal("0")))


async def _posted_vouchers(db) -> List[dict]:
    return await db.opening_balance_vouchers.find(
        {"status": "posted"},
        {"_id": 0},
    ).to_list(100)


def _inventory_debit(voucher: dict, inventory_account_id: str) -> Decimal:
    total = Decimal("0")
    for line in voucher.get("lines") or []:
        if str(line.get("account_id") or "") == inventory_account_id:
            total += _money(line.get("debit") or 0)
    return total


async def _voucher_scope_error(
    db,
    *,
    voucher: dict,
    journal: Optional[dict],
    inventory_account_id: str,
    total: Decimal,
    effective_date: str,
) -> Optional[OpeningBlocked]:
    """Same total is not enough. Account, date, provenance, and prior link must match."""
    if str(voucher.get("cutoff_date") or "") != effective_date:
        return OpeningBlocked(
            "GL_OPENING_CUTOFF_AMBIGUOUS",
            "Tanggal saldo awal GL tidak sama dengan tanggal efektif opening.",
        )
    if not journal or str(journal.get("status") or "") != "posted" or journal.get("reversal_of") is not None:
        return OpeningBlocked("GL_OPENING_JOURNAL_MISSING", "Saldo awal posted tidak menunjuk jurnal yang masih aktif.")
    if str(journal.get("source_type") or "") != SOURCE_OPENING_BALANCE:
        return OpeningBlocked("GL_OPENING_SOURCE_MISMATCH", "Jurnal yang dihubungkan bukan saldo awal.")
    reversal = await db.journal_entries.find_one(
        {"reversal_of": journal.get("id"), "status": "posted"},
        {"_id": 1},
    )
    if reversal:
        return OpeningBlocked("GL_OPENING_JOURNAL_MISSING", "Jurnal saldo awal sudah direversal.")
    linked = await db[COLLECTION].find_one(
        {"status": STATUS_POSTED, "posted_journal_id": journal.get("id")},
        {"_id": 0, "id": 1},
    )
    if linked:
        return OpeningBlocked(
            "OPENING_JOURNAL_ALREADY_LINKED",
            "Jurnal saldo awal ini sudah ditautkan ke opening lain.",
            extra={"opening_id": linked.get("id"), "journal_id": journal.get("id")},
        )
    inventory_debit = _inventory_debit(voucher, inventory_account_id)
    if inventory_debit != total:
        return OpeningBlocked(
            "GL_OPENING_AMOUNT_MISMATCH",
            "Debit persediaan pada saldo awal GL tidak sama dengan total opening yang diajukan.",
            extra={"gl_inventory_debit": money_round(inventory_debit), "opening_total": money_round(total)},
        )
    other_inventory = []
    for line in voucher.get("lines") or []:
        account_id = str(line.get("account_id") or "")
        if _money(line.get("debit") or 0) <= 0 or account_id == inventory_account_id:
            continue
        account = await get_account(db, account_id)
        if str((account or {}).get("account_type") or "").upper() == "INTR":
            other_inventory.append(account_id)
    if other_inventory:
        return OpeningBlocked(
            "GL_OPENING_SCOPE_MISMATCH",
            "Saldo awal mendebit akun persediaan lain. Total yang sama tidak cukup untuk menautkan.",
            extra={"other_inventory_accounts": other_inventory},
        )
    credits = _credit_accounts(voucher)
    if len(credits) != 1 or not await _account_allowed_offset(db, credits[0]):
        return OpeningBlocked(
            "GL_OPENING_SCOPE_MISMATCH",
            "Kredit saldo awal bukan satu akun lawan yang diizinkan. Total yang sama tidak menautkan opening.",
            extra={"credit_accounts": credits},
        )
    return None


def _credit_accounts(voucher: dict) -> List[str]:
    accounts = []
    for line in voucher.get("lines") or []:
        if _money(line.get("credit") or 0) > 0:
            account_id = str(line.get("account_id") or "")
            if account_id and account_id not in accounts:
                accounts.append(account_id)
    return accounts


async def _account_allowed_offset(db, account_id: str) -> Optional[dict]:
    account = await get_account(db, account_id)
    if not account:
        return None
    account_type = str(account.get("account_type") or "").strip().upper()
    if account_type in REJECTED_OFFSET_TYPES or account_type not in ALLOWED_OFFSET_TYPES:
        return None
    return account


async def resolve_offset_and_gl(
    db,
    *,
    effective_date: str,
    inventory_account_id: str,
    total: Decimal,
) -> dict:
    """Decide whether to link an existing opening journal or which credit to use.

    Does not invent an account. Ambiguous or missing configuration blocks.
    """
    settings = await db.app_settings.find_one({"key": PURCHASE_SETTINGS_KEY}, {"_id": 0}) or {}
    configured_offset = str(settings.get("inventory_opening_offset_account_id") or "").strip()
    vouchers = await _posted_vouchers(db)
    same_day = [row for row in vouchers if str(row.get("cutoff_date") or "") == effective_date]
    other = [row for row in vouchers if str(row.get("cutoff_date") or "") != effective_date]
    same_debit = sum((_inventory_debit(row, inventory_account_id) for row in same_day), Decimal("0"))
    other_debit = sum((_inventory_debit(row, inventory_account_id) for row in other), Decimal("0"))
    if other_debit > 0 and same_debit == 0:
        raise OpeningBlocked(
            "GL_OPENING_CUTOFF_AMBIGUOUS",
            "Saldo awal GL persediaan ada pada tanggal cut-off yang berbeda dari opening ini.",
            extra={"other_cutoff_dates": sorted({str(row.get("cutoff_date") or "") for row in other})},
        )
    if same_debit > 0:
        if len(same_day) != 1:
            raise OpeningBlocked(
                "GL_OPENING_SOURCE_AMBIGUOUS",
                "Lebih dari satu saldo awal posted untuk tanggal yang sama.",
            )
        voucher = same_day[0]
        journal_id = str(voucher.get("posted_journal_id") or "")
        journal = await db.journal_entries.find_one({"id": journal_id}, {"_id": 0}) if journal_id else None
        scope_error = await _voucher_scope_error(
            db,
            voucher=voucher,
            journal=journal,
            inventory_account_id=inventory_account_id,
            total=total,
            effective_date=effective_date,
        )
        if scope_error:
            raise scope_error
        return {
            "mode": "link_existing",
            "offset_account_id": None,
            "voucher_id": voucher.get("id"),
            "journal_id": journal_id,
        }
    offset_id = None
    resolution = None
    if configured_offset:
        account = await _account_allowed_offset(db, configured_offset)
        if not account:
            raise OpeningBlocked(
                "OPENING_OFFSET_REJECTED",
                "Akun lawan opening tidak valid. Utang, pendapatan, beban, dan persediaan tidak dipakai.",
            )
        offset_id = str(account["id"])
        resolution = "configured"
    elif len(same_day) == 1:
        credits = _credit_accounts(same_day[0])
        if len(credits) == 1:
            account = await _account_allowed_offset(db, credits[0])
            if account:
                offset_id = str(account["id"])
                resolution = "opening_voucher_credit"
    if not offset_id:
        raise OpeningBlocked(
            "OPENING_OFFSET_UNRESOLVED",
            "Akun lawan opening tidak dapat ditentukan dari konfigurasi atau saldo awal yang sudah posted.",
        )
    return {
        "mode": "post_new",
        "offset_account_id": offset_id,
        "offset_resolution": resolution,
        "voucher_id": None,
        "journal_id": None,
    }


def _cutoff_instant(effective_date: str) -> datetime:
    return datetime.fromisoformat(f"{effective_date}T00:00:00").replace(tzinfo=WIB)


async def _live_source_journals(db, source_type: str, source_ids: List[str]) -> List[dict]:
    if not source_ids:
        return []
    rows = await db.journal_entries.find(
        {
            "source_type": source_type,
            "source_id": {"$in": source_ids},
            "status": "posted",
            "reversal_of": None,
        },
        {"_id": 0},
    ).to_list(len(source_ids) + 10)
    live = []
    for row in rows:
        reversal = await db.journal_entries.find_one(
            {"reversal_of": row.get("id"), "status": "posted"},
            {"_id": 1},
        )
        if reversal:
            continue
        live.append(row)
    return live


async def classify_opening_coverage(db, lines: List[dict], effective_date: str) -> dict:
    """Prove which GR stock effects are inside the cutoff. Does not invent qty."""
    from services.hpp_cutover import applied_marker_instant, prove_stock_application_chain

    excluded: List[str] = []
    ambiguous: List[str] = []
    blockers: List[dict] = []
    snapshots: List[dict] = []
    cutoff = _cutoff_instant(effective_date)
    for line in lines:
        mid = str(line.get("master_item_id") or "")
        base_uom = str(line.get("base_uom") or "")
        receipts = await db.purchase_goods_receipts.find(
            {"cancelled": {"$ne": True}, "lines.master_item_id": mid},
            {"_id": 0, "id": 1, "receipt_date": 1, "receipt_number": 1},
        ).to_list(5000)
        state = await db.product_stock_state.find_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}]},
            {"_id": 0, "pending_event": 1, "base_uom": 1, "total_qty": 1},
        )
        if state and state.get("pending_event"):
            blockers.append({
                "code": "STOCK_PENDING",
                "message": "Mutasi stok item masih in-flight. Opening tidak memakai stok saat ini.",
                "master_item_id": mid,
            })
            continue
        proof = await prove_stock_application_chain(db, mid, base_uom=base_uom)
        if not proof.get("ok"):
            reason = proof.get("reason") or "STOCK_SNAPSHOT_UNPROVEN"
            movement_id = str(proof.get("movement_id") or "")
            evidence_reason = str(proof.get("evidence_reason") or "")
            message = "Rantai stok tidak terbukti. Qty pada cutoff tidak direka."
            if reason == "STOCK_COMMIT_UNPROVEN":
                message = (
                    f"Movement {movement_id or '(tanpa id)'} tidak punya bukti penerapan stok yang sah. "
                    f"{evidence_reason or 'Waktu penerapan tidak terbukti.'}"
                )
            blockers.append({
                "code": reason,
                "message": message,
                "master_item_id": mid,
                "movement_id": movement_id or None,
                "evidence_reason": evidence_reason or None,
                "receipt_ids": [str(row.get("id") or "") for row in receipts],
            })
            continue
        chain = proof.get("chain") or []
        by_receipt: Dict[str, List[dict]] = {}
        for movement in chain:
            if str(movement.get("reference_type") or "") != "goods_receipt":
                continue
            by_receipt.setdefault(str(movement.get("reference_id") or ""), []).append(movement)
        from services.hpp_cutover import cutoff_snapshot_qty

        snapshot = cutoff_snapshot_qty(chain, cutoff, require_applied_marker=True)
        if not snapshot.get("ok"):
            movement_id = str(snapshot.get("movement_id") or "")
            evidence_reason = str(snapshot.get("evidence_reason") or "")
            blockers.append({
                "code": snapshot.get("reason") or "STOCK_COMMIT_UNPROVEN",
                "message": (
                    f"Movement {movement_id or '(tanpa id)'} tidak punya bukti penerapan stok yang sah. "
                    f"{evidence_reason or 'Waktu penerapan tidak terbukti.'}"
                ),
                "master_item_id": mid,
                "movement_id": movement_id or None,
                "evidence_reason": evidence_reason or None,
            })
            continue
        before = snapshot.get("events_before") or []
        after = snapshot.get("events_after") or []
        snapshot_qty = snapshot.get("qty")
        if snapshot_qty is not None and _dec(line.get("qty")) != _dec(snapshot_qty):
            blockers.append({
                "code": "OPENING_SNAPSHOT_MISMATCH",
                "message": "Qty opening tidak sama dengan saldo yang terbukti pada cutoff. Saldo sebelum GR pertama tidak dipakai.",
                "master_item_id": mid,
                "snapshot_qty": snapshot_qty,
                "declared_qty": line.get("qty"),
                "snapshot_basis": snapshot.get("basis"),
            })
        snapshots.append({
            "master_item_id": mid,
            "base_uom": base_uom,
            "snapshot_qty": snapshot_qty,
            "snapshot_basis": snapshot.get("basis"),
            "declared_qty": line.get("qty"),
            "events_before_cutoff": len(before),
            "events_after_cutoff": len(after),
            "snapshot_is_current_stock": False,
        })
        for receipt in receipts:
            receipt_id = str(receipt.get("id") or "")
            business = wib_business_date(receipt.get("receipt_date"))
            moves = by_receipt.get(receipt_id) or []
            if business is None:
                ambiguous.append(receipt_id)
                blockers.append({
                    "code": "AMBIGUOUS_RECEIPT_DATE",
                    "message": "Tanggal penerimaan tidak dapat dibaca. Dokumen tidak diloloskan diam-diam.",
                    "receipt_id": receipt_id,
                    "master_item_id": mid,
                })
                continue
            if not moves:
                blockers.append({
                    "code": "STOCK_COVERAGE_UNPROVEN",
                    "message": "Penerimaan belum punya efek stok yang terbukti pada rantai. Bukan bukti sudah masuk opening.",
                    "receipt_id": receipt_id,
                    "receipt_number": receipt.get("receipt_number"),
                    "business_date": business,
                    "master_item_id": mid,
                })
                continue
            writer = applied_marker_instant(moves[0])
            stock_before = bool(writer and writer < cutoff)
            accounting_before = business < effective_date
            source = {
                "receipt_id": receipt_id,
                "receipt_number": receipt.get("receipt_number"),
                "business_date": business,
                "stock_applied_at": moves[0].get("stock_applied_at"),
                "movement_id": str(moves[0].get("id") or moves[0].get("event_key") or ""),
                "balance_before": moves[0].get("balance_before"),
                "master_item_id": mid,
                "base_uom": moves[0].get("base_uom") or base_uom,
            }
            if stock_before and accounting_before:
                excluded.append(receipt_id)
            elif stock_before and not accounting_before:
                blockers.append({
                    "code": "STOCK_APPLIED_BEFORE_ACCOUNTING_DATE",
                    "message": "Stok GR sudah diterapkan sebelum cutoff, tetapi tanggal akuntansi tidak. Keduanya tidak disamakan.",
                    **source,
                })
            elif accounting_before and not stock_before:
                blockers.append({
                    "code": "HISTORICAL_DOCUMENT_STOCK_AFTER_CUTOFF",
                    "message": "Tanggal akuntansi sebelum cutoff, tetapi efek stok belum terbukti sebelum cutoff.",
                    **source,
                })
    return {
        "excluded": excluded,
        "ambiguous": ambiguous,
        "blockers": blockers,
        "snapshots": snapshots,
    }


async def _assert_no_active_covered_journals(db, excluded: List[str], inventory_account_id: str) -> None:
    from services.purchase_goods_receipt_posting import SOURCE_TYPE as GR_SOURCE

    journals = await _live_source_journals(db, GR_SOURCE, excluded)
    if not journals:
        return
    sources = []
    for journal in journals:
        amount = Decimal("0")
        for line in await db.journal_lines.find({"journal_id": journal.get("id")}, {"_id": 0}).to_list(20):
            if str(line.get("account_id") or "") == inventory_account_id:
                amount += _money(line.get("debit") or 0)
        sources.append({
            "receipt_id": journal.get("source_id"),
            "journal_id": journal.get("id"),
            "journal_number": journal.get("number"),
            "date": journal.get("date"),
            "source_type": journal.get("source_type"),
            "inventory_account_id": inventory_account_id,
            "inventory_debit": money_round(amount),
        })
    raise OpeningBlocked(
        "HISTORICAL_GR_JOURNAL_ACTIVE",
        "Jurnal GR yang sudah tercakup opening masih aktif. Opening tidak diposting agar persediaan tidak ganda. Reversal otomatis tidak dilakukan.",
        extra={"sources": sources},
    )


async def _freeze_receipt_exclusion(db, lines: List[dict], effective_date: str) -> dict:
    coverage = await classify_opening_coverage(db, lines, effective_date)
    if coverage["blockers"]:
        first = coverage["blockers"][0]
        raise OpeningBlocked(
            str(first.get("code") or "STOCK_COVERAGE_UNPROVEN"),
            str(first.get("message") or "Cakupan stok pada cutoff tidak terbukti."),
            extra={"blockers": coverage["blockers"], "snapshots": coverage["snapshots"]},
        )
    return coverage


def _chain_fingerprint(chain: List[dict]) -> str:
    parts = []
    for movement in chain or []:
        parts.append("|".join([
            str(movement.get("id") or movement.get("event_key") or ""),
            str(movement.get("stock_applied_at") or ""),
            str(movement.get("balance_before")),
            str(movement.get("balance_after")),
            str(movement.get("reference_type") or ""),
            str(movement.get("reference_id") or ""),
            str(movement.get("base_uom") or ""),
        ]))
    return "\n".join(parts)


def _state_version(state: Optional[dict]) -> int:
    try:
        return int((state or {}).get("version") or 0)
    except (TypeError, ValueError):
        return 0


def _annotate_reconstruction_blockers(blockers: List[dict]) -> List[dict]:
    needs_snapshot = {
        "STOCK_COMMIT_UNPROVEN",
        "STOCK_SNAPSHOT_UNPROVEN",
        "STOCK_CHAIN_UNPROVEN",
        "STOCK_CHAIN_AMBIGUOUS",
        "STOCK_CHAIN_ORDER_CONFLICT",
        "STOCK_PENDING",
        "STOCK_COVERAGE_UNPROVEN",
    }
    annotated = []
    for row in blockers or []:
        item = dict(row)
        if str(item.get("code") or "") in needs_snapshot:
            item["snapshot_required"] = True
            item["valid_snapshot_rule"] = (
                "Rekonstruksi historis ke cutoff tidak terbukti. Ambil snapshot pada tanggal "
                "efektif, tidak sebelum 00:00 WIB tanggal itu. Snapshot yang dibuat kemudian "
                "tidak boleh diberi tanggal mundur."
            )
        annotated.append(item)
    return annotated


async def _read_item_boundary(db, master_item_id: str, base_uom: str, *, settle_pending: bool) -> dict:
    from services.hpp_cutover import prove_stock_application_chain
    from services.physical_stock_engine import reconcile_pending_product_claim

    mid = str(master_item_id or "")
    if settle_pending:
        state = await db.product_stock_state.find_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}]},
            {"_id": 0, "pending_event": 1, "total_qty": 1},
        )
        if state and state.get("pending_event"):
            await reconcile_pending_product_claim(db, mid)
    proof = await prove_stock_application_chain(db, mid, base_uom=base_uom)
    state = proof.get("state") or await db.product_stock_state.find_one(
        {"$or": [{"_id": mid}, {"master_item_id": mid}]},
        {"_id": 0},
    )
    return {"proof": proof, "state": state}


async def capture_financial_opening_snapshot(db, payload: dict, user: Optional[dict] = None) -> dict:
    """Record current proven stock. Does not change qty to manufacture a snapshot."""
    request_id = str((payload or {}).get("client_request_id") or "").strip()
    if not request_id:
        raise OpeningBlocked("REQUEST_ID_REQUIRED", "client_request_id wajib diisi.", status_code=400)
    identities = (payload or {}).get("lines") or (payload or {}).get("items")
    if not isinstance(identities, list) or not identities:
        raise OpeningBlocked("OPENING_LINES_REQUIRED", "Item snapshot wajib diisi.", status_code=400)
    before_stock = await db.product_stock_state.count_documents({})
    before_movements = await db.stock_movements.count_documents({})
    existing = await db[SNAPSHOTS].find_one({"client_request_id": request_id}, {"_id": 0})
    wanted = []
    seen = set()
    for raw in identities:
        if not isinstance(raw, dict):
            raise OpeningBlocked("OPENING_LINE_INVALID", "Baris snapshot tidak valid.", status_code=400)
        mid = str(raw.get("master_item_id") or "").strip()
        base_uom = str(raw.get("base_uom") or "").strip().upper()
        if not mid or not base_uom:
            raise OpeningBlocked("OPENING_LINE_INVALID", "Item dan base UOM snapshot wajib diisi.", status_code=400)
        if mid in seen:
            raise OpeningBlocked("OPENING_LINE_DUPLICATE", "Item snapshot tidak boleh ganda.", status_code=400)
        seen.add(mid)
        wanted.append({"master_item_id": mid, "base_uom": base_uom})
    if existing:
        stored = sorted(str(row.get("master_item_id") or "") for row in existing.get("lines") or [])
        if stored != sorted(row["master_item_id"] for row in wanted):
            raise OpeningBlocked(
                "OPENING_PAYLOAD_MISMATCH",
                "Request snapshot yang sama tidak boleh mengganti item yang sudah diambil.",
            )
        return existing
    captured_at = _utc_now()
    actor = actor_snapshot(user)
    lines = []
    blockers = []
    for row in wanted:
        mid = row["master_item_id"]
        observed = await _read_item_boundary(db, mid, row["base_uom"], settle_pending=True)
        state = observed.get("state") or {}
        if state.get("pending_event"):
            blockers.append({
                "code": "STOCK_PENDING",
                "message": "Mutasi stok masih in-flight. Snapshot tidak mengubah qty dan tidak dibuat.",
                "master_item_id": mid,
                "snapshot_required": True,
            })
            continue
        proof = observed.get("proof") or {}
        if not proof.get("ok"):
            blockers.append({
                "code": proof.get("reason") or "STOCK_COMMIT_UNPROVEN",
                "message": "Batas sebelum/sesudah snapshot tidak terbukti. Timestamp tidak diisi mundur.",
                "master_item_id": mid,
                "movement_id": proof.get("movement_id"),
                "evidence_reason": proof.get("evidence_reason"),
                "snapshot_required": True,
            })
            continue
        again = await _read_item_boundary(db, mid, row["base_uom"], settle_pending=False)
        again_state = again.get("state") or {}
        again_proof = again.get("proof") or {}
        chain = again_proof.get("chain") or []
        if (
            again_state.get("pending_event")
            or not again_proof.get("ok")
            or _state_version(again_state) != _state_version(state)
            or _chain_fingerprint(chain) != _chain_fingerprint(proof.get("chain") or [])
        ):
            blockers.append({
                "code": "STALE_SNAPSHOT",
                "message": "Stok berubah saat snapshot diambil. Snapshot tidak disimpan.",
                "master_item_id": mid,
            })
            continue
        physical_qty = again_state.get("total_qty")
        if physical_qty is None:
            physical_qty = chain[-1].get("balance_after") if chain else None
        state_uom = str(again_state.get("base_uom") or row["base_uom"]).strip().upper()
        if state_uom != row["base_uom"]:
            blockers.append({
                "code": "OPENING_UOM_AMBIGUOUS",
                "message": "Base UOM snapshot tidak sama dengan satuan stok.",
                "master_item_id": mid,
            })
            continue
        lines.append({
            "master_item_id": mid,
            "base_uom": state_uom,
            "qty": qty_round(physical_qty) if physical_qty is not None else None,
            "revision": _state_version(again_state),
            "fingerprint": _chain_fingerprint(chain),
            "event_count": len(chain),
            "identity": financial_inventory_item_key({"master_item_id": mid}),
            "provenance": {
                "source": "CAPTURED_STOCK_BOUNDARY",
                "actor": actor,
                "captured_at": captured_at.isoformat(),
                "published_price_ignored": True,
                "qty_adjusted_for_snapshot": False,
            },
        })
    if blockers or len(lines) != len(wanted):
        if await db.product_stock_state.count_documents({}) != before_stock:
            raise OpeningBlocked("SNAPSHOT_MUTATED_STOCK", "Snapshot mengubah jumlah dokumen stok.", status_code=500)
        raise OpeningBlocked(
            str(blockers[0].get("code") if blockers else "STOCK_SNAPSHOT_UNPROVEN"),
            str(blockers[0].get("message") if blockers else "Snapshot tidak terbukti."),
            extra={"blockers": _annotate_reconstruction_blockers(blockers)},
        )
    doc = {
        "id": str(uuid.uuid4()),
        "client_request_id": request_id,
        "status": "CAPTURED",
        "captured_at": captured_at.isoformat(),
        "boundary_at": captured_at.isoformat(),
        "business_date": captured_at.astimezone(WIB).date().isoformat(),
        "timezone": TARGET_TIMEZONE,
        "lines": lines,
        "created_by": actor,
        "created_at": captured_at.isoformat(),
        "backdated": False,
    }
    try:
        await db[SNAPSHOTS].insert_one(dict(doc))
    except DuplicateKeyError:
        raced = await db[SNAPSHOTS].find_one({"client_request_id": request_id}, {"_id": 0})
        if raced:
            return raced
        raise OpeningBlocked("OPENING_CONFLICT", "Snapshot bentrok dengan proses lain.")
    doc.pop("_id", None)
    if await db.stock_movements.count_documents({}) != before_movements and any(
        (observed_line.get("provenance") or {}).get("qty_adjusted_for_snapshot") for observed_line in lines
    ):
        raise OpeningBlocked("SNAPSHOT_MUTATED_STOCK", "Snapshot mengubah qty.", status_code=500)
    await record_accounting_event(
        db,
        event_type="financial_inventory_opening_snapshot_captured",
        entity_type="financial_inventory_opening_snapshot",
        entity_id=doc["id"],
        actor=user,
        after={"business_date": doc["business_date"], "line_count": len(lines)},
        operation_key=f"fio-snapshot:{doc['id']}",
    )
    return doc


async def _coverage_from_snapshot(db, lines: List[dict], effective_date: str, snapshot_id: str) -> dict:
    from services.hpp_cutover import applied_marker_instant, cutoff_snapshot_qty

    snapshot = await db[SNAPSHOTS].find_one({"id": str(snapshot_id)}, {"_id": 0})
    if not snapshot or snapshot.get("status") != "CAPTURED":
        raise OpeningBlocked("SNAPSHOT_NOT_FOUND", "Snapshot opening tidak ditemukan.", status_code=404)
    business = str(snapshot.get("business_date") or "")
    if effective_date < business:
        raise OpeningBlocked(
            "OPENING_SNAPSHOT_BACKDATED",
            "Snapshot dibuat setelah tanggal yang diminta. Opening tidak diberi tanggal mundur.",
            extra={"snapshot_business_date": business, "effective_date": effective_date},
        )
    if effective_date != business:
        raise OpeningBlocked(
            "OPENING_SNAPSHOT_DATE_MISMATCH",
            "Tanggal opening harus sama dengan tanggal WIB saat snapshot diambil.",
            extra={"snapshot_business_date": business, "effective_date": effective_date},
        )
    captured_lines = {
        str(row.get("master_item_id") or ""): row
        for row in snapshot.get("lines") or []
    }
    boundary = _cutoff_instant(business)
    parsed_boundary = snapshot.get("boundary_at")
    stamp = str(parsed_boundary or "")
    if stamp.endswith("Z"):
        stamp = stamp[:-1] + "+00:00"
    try:
        boundary = datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise OpeningBlocked("SNAPSHOT_BOUNDARY_UNPROVEN", "Waktu batas snapshot tidak terbaca.") from exc
    if boundary.tzinfo is None:
        raise OpeningBlocked("SNAPSHOT_BOUNDARY_UNPROVEN", "Waktu batas snapshot tidak memiliki zona waktu.")
    excluded: List[str] = []
    ambiguous: List[str] = []
    blockers: List[dict] = []
    snapshots: List[dict] = []
    for line in lines:
        mid = str(line.get("master_item_id") or "")
        captured = captured_lines.get(mid)
        if not captured:
            blockers.append({
                "code": "SNAPSHOT_ITEM_MISSING",
                "message": "Item opening tidak ada pada snapshot yang dipilih.",
                "master_item_id": mid,
            })
            continue
        if str(line.get("base_uom") or "").strip().upper() != str(captured.get("base_uom") or "").strip().upper():
            blockers.append({
                "code": "OPENING_UOM_AMBIGUOUS",
                "message": "Base UOM opening tidak sama dengan snapshot.",
                "master_item_id": mid,
            })
            continue
        if captured.get("qty") is None or _dec(line.get("qty")) != _dec(captured.get("qty")):
            blockers.append({
                "code": "OPENING_SNAPSHOT_MISMATCH",
                "message": "Qty opening harus sama dengan qty snapshot. Qty tidak diubah agar snapshot ada.",
                "master_item_id": mid,
                "snapshot_qty": captured.get("qty"),
                "declared_qty": line.get("qty"),
            })
            continue
        observed = await _read_item_boundary(db, mid, captured.get("base_uom") or "", settle_pending=False)
        state = observed.get("state") or {}
        proof = observed.get("proof") or {}
        chain = proof.get("chain") or []
        if state.get("pending_event") or not proof.get("ok"):
            blockers.append({
                "code": "STALE_SNAPSHOT" if proof.get("ok") else (proof.get("reason") or "STALE_SNAPSHOT"),
                "message": "Snapshot tidak lagi menggambarkan stok. Posting ditolak.",
                "master_item_id": mid,
                "snapshot_required": True,
            })
            continue
        if (
            _state_version(state) != int(captured.get("revision") or 0)
            or _chain_fingerprint(chain) != str(captured.get("fingerprint") or "")
            or _dec(state.get("total_qty") or 0) != _dec(captured.get("qty"))
        ):
            blockers.append({
                "code": "STALE_SNAPSHOT",
                "message": "Revisi stok berubah setelah snapshot. Snapshot basi tidak diposting.",
                "master_item_id": mid,
                "captured_revision": captured.get("revision"),
                "current_revision": _state_version(state),
            })
            continue
        placed = cutoff_snapshot_qty(chain, boundary, require_applied_marker=True)
        if not placed.get("ok"):
            blockers.append({
                "code": placed.get("reason") or "STOCK_COMMIT_UNPROVEN",
                "message": "Event sebelum dan sesudah batas snapshot tidak terbukti.",
                "master_item_id": mid,
                "movement_id": placed.get("movement_id"),
                "snapshot_required": True,
            })
            continue
        receipts = await db.purchase_goods_receipts.find(
            {"cancelled": {"$ne": True}, "lines.master_item_id": mid},
            {"_id": 0, "id": 1, "receipt_date": 1, "receipt_number": 1},
        ).to_list(5000)
        by_receipt: Dict[str, List[dict]] = {}
        for movement in chain:
            if str(movement.get("reference_type") or "") != "goods_receipt":
                continue
            by_receipt.setdefault(str(movement.get("reference_id") or ""), []).append(movement)
        snapshots.append({
            "master_item_id": mid,
            "base_uom": captured.get("base_uom"),
            "snapshot_qty": captured.get("qty"),
            "snapshot_id": snapshot["id"],
            "boundary_at": snapshot.get("boundary_at"),
            "events_before_cutoff": len(placed.get("events_before") or []),
            "events_after_cutoff": len(placed.get("events_after") or []),
            "snapshot_is_current_stock": True,
            "backdated": False,
        })
        for receipt in receipts:
            receipt_id = str(receipt.get("id") or "")
            business_date = wib_business_date(receipt.get("receipt_date"))
            moves = by_receipt.get(receipt_id) or []
            if business_date is None:
                ambiguous.append(receipt_id)
                blockers.append({
                    "code": "AMBIGUOUS_RECEIPT_DATE",
                    "message": "Tanggal penerimaan tidak dapat dibaca.",
                    "receipt_id": receipt_id,
                    "master_item_id": mid,
                })
                continue
            if not moves:
                blockers.append({
                    "code": "STOCK_COVERAGE_UNPROVEN",
                    "message": "Penerimaan belum punya efek stok yang terbukti terhadap batas snapshot.",
                    "receipt_id": receipt_id,
                    "master_item_id": mid,
                })
                continue
            writer = applied_marker_instant(moves[0])
            stock_before = bool(writer and writer < boundary)
            accounting_before = business_date < effective_date or (business_date == effective_date and stock_before)
            if stock_before and accounting_before:
                excluded.append(receipt_id)
            elif stock_before and not accounting_before:
                blockers.append({
                    "code": "STOCK_APPLIED_BEFORE_ACCOUNTING_DATE",
                    "message": "Stok GR sudah masuk sebelum batas snapshot, tetapi tanggal akuntansi belum.",
                    "receipt_id": receipt_id,
                    "master_item_id": mid,
                })
            elif not stock_before and business_date < effective_date:
                blockers.append({
                    "code": "HISTORICAL_DOCUMENT_STOCK_AFTER_CUTOFF",
                    "message": "Tanggal akuntansi sebelum batas, tetapi efek stok belum terbukti sebelumnya.",
                    "receipt_id": receipt_id,
                    "master_item_id": mid,
                })
    return {
        "excluded": excluded,
        "ambiguous": ambiguous,
        "blockers": blockers,
        "snapshots": snapshots,
        "snapshot_id": snapshot["id"],
    }


async def preview_financial_opening(db, payload: dict) -> dict:
    """Zero-write plan. Does not insert, post, or activate a date."""
    before_openings = await db[COLLECTION].count_documents({})
    before_journals = await db.journal_entries.count_documents({})
    effective = str((payload or {}).get("effective_date") or "").strip()
    if effective != TARGET_EFFECTIVE_DATE and not parse_business_date(effective):
        raise OpeningBlocked("OPENING_DATE_INVALID", "Tanggal efektif opening harus YYYY-MM-DD.", status_code=400)
    if not parse_business_date(effective):
        raise OpeningBlocked("OPENING_DATE_INVALID", "Tanggal efektif opening harus YYYY-MM-DD.", status_code=400)
    lines = _normalize_lines((payload or {}).get("lines"))
    await _validate_uom(db, lines)
    settings = await get_purchase_accounting_settings(db)
    inventory_id = str(settings.get("inventory_account_id") or "")
    try:
        _validate_inventory(await get_account(db, inventory_id) if inventory_id else None)
    except HTTPException as exc:
        raise OpeningBlocked("INVENTORY_ACCOUNT_UNCONFIGURED", str(exc.detail), status_code=409) from exc
    total = _opening_total(lines)
    if total <= 0:
        raise OpeningBlocked("OPENING_TOTAL_ZERO", "Total opening nol tidak diposting sebagai jurnal.", status_code=400)
    resolution = await resolve_offset_and_gl(
        db,
        effective_date=effective,
        inventory_account_id=inventory_id,
        total=total,
    )
    snapshot_id = str((payload or {}).get("snapshot_id") or "").strip()
    if snapshot_id:
        coverage = await _coverage_from_snapshot(db, lines, effective, snapshot_id)
    else:
        coverage = await classify_opening_coverage(db, lines, effective)
        coverage["blockers"] = _annotate_reconstruction_blockers(coverage["blockers"])
    excluded = coverage["excluded"]
    ambiguous = coverage["ambiguous"]
    watched = list(dict.fromkeys(excluded + [
        str(row.get("receipt_id") or "") for row in coverage["blockers"] if row.get("receipt_id")
    ]))
    try:
        await _assert_no_active_covered_journals(db, watched, inventory_id)
    except OpeningBlocked as exc:
        coverage["blockers"].insert(0, {"code": exc.code, "message": exc.message, **exc.extra})
    if await db[COLLECTION].count_documents({}) != before_openings or await db.journal_entries.count_documents({}) != before_journals:
        raise OpeningBlocked("PREVIEW_WROTE", "Preview opening menulis data.", status_code=500)
    return {
        "status": "PREVIEW",
        "writes": 0,
        "effective_date": effective,
        "timezone": TARGET_TIMEZONE,
        "target_bookkeeping_start": TARGET_EFFECTIVE_DATE,
        "activates_configuration": False,
        "inventory_account_id": inventory_id,
        "total": money_round(total),
        "lines": lines,
        "resolution": resolution,
        "excluded_receipt_count": len(excluded),
        "ambiguous_receipt_count": len(ambiguous),
        "excluded_receipt_ids": excluded,
        "snapshots": coverage["snapshots"],
        "blockers": coverage["blockers"],
        "can_post": not coverage["blockers"],
        "book_value_source": SOURCE_APPROVED,
        "published_is_book_value": False,
        "snapshot_id": snapshot_id or None,
        "historical_reconstruction": not bool(snapshot_id),
    }


async def list_financial_openings(db) -> List[dict]:
    rows = await db[COLLECTION].find({}, {"_id": 0}).sort("created_at", -1).to_list(50)
    for row in rows:
        if row.get("status") == STATUS_POSTED:
            problem = await _opening_journal_problem(db, row)
            row["journal_authority"] = "valid" if problem is None else str(problem.get("code") or "")
        else:
            row["journal_authority"] = None
    return rows


async def get_financial_opening(db, opening_id: str) -> Optional[dict]:
    return await db[COLLECTION].find_one({"id": str(opening_id)}, {"_id": 0})


def opening_acquisition_rows(opening: dict) -> List[dict]:
    if str(opening.get("status") or "") != STATUS_POSTED:
        return []
    rows = []
    for line in opening.get("lines") or []:
        qty = _dec(line.get("qty"))
        if qty <= 0:
            continue
        line_id = str(line.get("line_id") or "")
        rows.append({
            "acquisition_key": f"opening:{opening.get('id')}:{line_id}",
            "receipt_id": str(opening.get("id") or ""),
            "receipt_line_id": line_id,
            "purchase_order_line_id": "",
            "financial_inventory_item_key": line.get("financial_inventory_item_key"),
            "inventory_account_id": opening.get("inventory_account_id"),
            "receipt_date": opening.get("effective_date"),
            "base_qty": qty,
            "base_unit_cost": _money(line.get("unit_cost")),
            "total_cost": _money(line.get("total")),
            "base_uom": line.get("base_uom"),
            "source": SOURCE_APPROVED,
        })
    return rows


async def receipt_blocked_for_new_acquisition(db, receipt: dict) -> Optional[str]:
    """Posted openings keep historical and ambiguous receipts out of new acquisitions."""
    receipt_id = str((receipt or {}).get("id") or "")
    business = wib_business_date((receipt or {}).get("receipt_date"))
    item_ids = {
        str(line.get("master_item_id") or "")
        for line in (receipt or {}).get("lines") or []
        if isinstance(line, dict)
    }
    good, problems = await partition_posted_openings(db)
    for problem in problems:
        covered = {
            key[2:] if str(key).startswith("m:") else str(key)
            for key in (problem.get("item_keys") or [])
            if str(key or "").strip()
        }
        if item_ids and covered and item_ids.isdisjoint(covered):
            continue
        return str(problem.get("code") or "OPENING_JOURNAL_NOT_AUTHORITATIVE")
    for opening in good:
        covered = {str(line.get("master_item_id") or "") for line in opening.get("lines") or []}
        if item_ids and covered and item_ids.isdisjoint(covered):
            continue
        if receipt_id and receipt_id in set(opening.get("excluded_receipt_ids") or []):
            return "HISTORICAL_RECEIPT_EXCLUDED"
        if receipt_id and receipt_id in set(opening.get("ambiguous_receipt_ids") or []):
            return "AMBIGUOUS_RECEIPT_DATE"
        effective = str(opening.get("effective_date") or "")
        if business is None:
            return "AMBIGUOUS_RECEIPT_DATE"
        if effective and business < effective:
            return "HISTORICAL_RECEIPT_EXCLUDED"
    return None


async def load_posted_openings(db) -> List[dict]:
    return await db[COLLECTION].find({"status": STATUS_POSTED}, {"_id": 0}).to_list(None)


def _opening_item_keys(opening: dict) -> List[str]:
    keys = []
    for line in (opening or {}).get("lines") or []:
        key = str(line.get("financial_inventory_item_key") or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


async def _opening_is_consumed(db, opening: dict) -> bool:
    prefix = re.escape(f"opening:{opening.get('id')}:")
    found = await db[ISSUE_COLLECTION].find_one(
        {"$or": [
            {"accounting_posting_basis.consumed_acquisitions.acquisition_key": {"$regex": f"^{prefix}"}},
            {"accounting_posting_basis.pool_acquisition_keys": {"$regex": f"^{prefix}"}},
        ]},
        {"_id": 1},
    )
    return found is not None


async def guard_consumed_opening_journal(db, journal: dict) -> None:
    """Block a direct journal reversal after COGS has consumed the opening.

    Linked vouchers are also covered. The opening-balance path still takes
    the item lock first; this guard is the journal_core backstop.
    """
    from services.journal_core import JournalConflictError

    journal_id = str((journal or {}).get("id") or "")
    if not journal_id:
        return
    source_type = str(journal.get("source_type") or "")
    source_id = str(journal.get("source_id") or "")
    clauses = [{"posted_journal_id": journal_id}]
    if source_type == SOURCE_TYPE and source_id:
        clauses.append({"id": source_id})
    if source_type == SOURCE_OPENING_BALANCE and source_id:
        clauses.append({
            "journal_origin": "existing_opening_voucher",
            "existing_opening_voucher_id": source_id,
        })
    openings = await db[COLLECTION].find(
        {"status": STATUS_POSTED, "$or": clauses},
        {"_id": 0},
    ).to_list(20)
    for opening in openings:
        if await _opening_is_consumed(db, opening):
            raise JournalConflictError(
                "Jurnal opening yang sudah dikonsumsi HPP tidak dapat direversal."
            )


async def _journal_has_reversal(db, journal: Optional[dict]) -> bool:
    if not journal:
        return True
    if str(journal.get("status") or "") == "reversed" or journal.get("reversal_of") is not None:
        return True
    reversal = await db.journal_entries.find_one(
        {"reversal_of": journal.get("id"), "status": "posted"},
        {"_id": 1},
    )
    return reversal is not None


async def _opening_journal_problem(db, opening: dict) -> Optional[dict]:
    """Posted opening is an acquisition only when its source journal still matches."""
    keys = _opening_item_keys(opening)

    def problem(message: str) -> dict:
        return {
            "code": "OPENING_JOURNAL_NOT_AUTHORITATIVE",
            "message": message,
            "opening_id": opening.get("id"),
            "item_keys": keys,
        }

    journal_id = str(opening.get("posted_journal_id") or "")
    journal = await db.journal_entries.find_one({"id": journal_id}, {"_id": 0}) if journal_id else None
    if not journal:
        return problem("Jurnal sumber opening tidak ada.")
    if await _journal_has_reversal(db, journal) or str(journal.get("status") or "") != "posted":
        return problem("Jurnal sumber opening tidak posted atau sudah direverse.")
    if str(journal.get("date") or "") != str(opening.get("effective_date") or ""):
        return problem("Tanggal jurnal tidak sama dengan tanggal opening yang dibekukan.")
    lines = await db.journal_lines.find({"journal_id": journal_id}, {"_id": 0}).to_list(30)
    inventory_id = str(opening.get("inventory_account_id") or "")
    total = _money(opening.get("total"))
    debit = sum(
        (_money(line.get("debit") or 0) for line in lines if str(line.get("account_id") or "") == inventory_id),
        Decimal("0"),
    )
    if debit != total:
        return problem("Debit persediaan pada jurnal tidak sama dengan total opening yang dibekukan.")
    if opening.get("journal_origin") == "existing_opening_voucher":
        if str(journal.get("source_type") or "") != SOURCE_OPENING_BALANCE:
            return problem("Jurnal tertaut bukan saldo awal.")
        voucher_id = str(opening.get("existing_opening_voucher_id") or "")
        if voucher_id and str(journal.get("source_id") or "") != voucher_id:
            return problem("Jurnal tertaut bukan voucher opening yang dibekukan.")
        credits = []
        for line in lines:
            if _money(line.get("credit") or 0) <= 0:
                continue
            account_id = str(line.get("account_id") or "")
            if account_id and account_id not in credits:
                credits.append(account_id)
        if len(credits) != 1 or not await _account_allowed_offset(db, credits[0]):
            return problem("Kredit jurnal saldo awal tidak lagi satu akun lawan yang diizinkan.")
        return None
    if str(journal.get("source_type") or "") != SOURCE_TYPE or str(journal.get("source_id") or "") != str(opening.get("id") or ""):
        return problem("Jurnal bukan jurnal opening yang dibekukan.")
    offset_id = str(opening.get("offset_account_id") or "")
    credit = sum(
        (_money(line.get("credit") or 0) for line in lines if str(line.get("account_id") or "") == offset_id),
        Decimal("0"),
    )
    if credit != total:
        return problem("Kredit akun lawan tidak sama dengan total opening yang dibekukan.")
    return None


async def partition_posted_openings(db) -> Tuple[List[dict], List[dict]]:
    good: List[dict] = []
    problems: List[dict] = []
    for opening in await load_posted_openings(db):
        problem = await _opening_journal_problem(db, opening)
        if problem:
            problems.append(problem)
        else:
            good.append(opening)
    return good, problems


async def _frozen_basis_error(db, doc: dict) -> Optional[OpeningBlocked]:
    """Frozen accounts stay frozen. A newer setting must not be substituted."""
    try:
        _validate_inventory(await get_account(db, doc.get("inventory_account_id")))
    except HTTPException as exc:
        return OpeningBlocked(
            "FROZEN_BASIS_INVALID",
            f"Akun persediaan yang dibekukan tidak lagi sah. Settings baru tidak menggantikannya. {exc.detail}",
        )
    if _money(doc.get("total")) <= 0 or not parse_business_date(str(doc.get("effective_date") or "")):
        return OpeningBlocked(
            "FROZEN_BASIS_INVALID",
            "Total atau tanggal opening yang dibekukan tidak sah.",
        )
    if doc.get("journal_origin") == "existing_opening_voucher":
        return None
    if not await _account_allowed_offset(db, doc.get("offset_account_id")):
        return OpeningBlocked(
            "FROZEN_BASIS_INVALID",
            "Akun lawan yang dibekukan tidak lagi sah. Settings baru tidak menggantikannya.",
        )
    return None


async def begin_voucher_reversal_guard(db, voucher: dict) -> List[dict]:
    """Hold item locks and refuse when a linked opening was already consumed."""
    from services.opening_balance import OpeningBalanceConflictError

    voucher_id = str((voucher or {}).get("id") or "")
    journal_id = str((voucher or {}).get("posted_journal_id") or "")
    openings = await db[COLLECTION].find(
        {
            "status": STATUS_POSTED,
            "journal_origin": "existing_opening_voucher",
            "$or": [
                {"existing_opening_voucher_id": voucher_id},
                {"posted_journal_id": journal_id},
            ],
        },
        {"_id": 0},
    ).to_list(20)
    keys = []
    for opening in openings:
        for key in _opening_item_keys(opening):
            if key not in keys:
                keys.append(key)
    if keys:
        from services.inventory_financial_locks import ensure_indexes as ensure_lock_indexes

        await ensure_lock_indexes(db)
    locks = await acquire_item_locks(db, keys, f"voucher-reverse:{voucher_id}") if keys else []
    if keys and locks is None:
        raise OpeningBalanceConflictError(LOCK_CONFLICT_MESSAGE)
    try:
        for opening in openings:
            fresh = await get_financial_opening(db, opening.get("id"))
            if not fresh or fresh.get("status") != STATUS_POSTED:
                continue
            if await _opening_is_consumed(db, fresh):
                raise OpeningBalanceConflictError(
                    "Opening finansial yang menempel pada saldo awal ini sudah dikonsumsi HPP dan tidak dapat dibatalkan."
                )
    except Exception:
        await release_item_locks(db, locks)
        raise
    return locks or []


async def financial_display_by_item(db) -> Dict[str, dict]:
    """Read-only map for Nilai Persediaan. Does not post or repair."""
    openings = await db[COLLECTION].find({}, {"_id": 0}).sort("created_at", -1).to_list(20)
    posted = [row for row in openings if row.get("status") == STATUS_POSTED]
    active = posted[0] if posted else (openings[0] if openings else None)
    by_item: Dict[str, dict] = {}
    if not active:
        return by_item
    problem = None
    if active.get("status") == STATUS_POSTED:
        problem = await _opening_journal_problem(db, active)
    for line in active.get("lines") or []:
        mid = str(line.get("master_item_id") or "")
        if not mid:
            continue
        by_item[mid] = {
            "financial_posting_status": active.get("status"),
            "financial_opening_qty": line.get("qty"),
            "financial_opening_unit_cost": line.get("unit_cost"),
            "financial_opening_total": line.get("total"),
            "financial_opening_source": line.get("source"),
            "financial_effective_date": active.get("effective_date"),
            "financial_journal_id": active.get("posted_journal_id"),
            "financial_journal_origin": active.get("journal_origin"),
            "financial_blocker": (problem or {}).get("code") or active.get("blocker_code"),
            "financial_blocker_message": (problem or {}).get("message") or active.get("blocker_message"),
            "financial_book_authoritative": problem is None and active.get("status") == STATUS_POSTED,
            "published_is_book_value": False,
        }
    return by_item


async def _mark(db, opening_id: str, fields: dict) -> None:
    await db[COLLECTION].update_one({"id": opening_id}, {"$set": fields})


async def _finish_posted(db, doc: dict, journal: dict, *, user: Optional[dict]) -> dict:
    candidate = {**doc, "status": STATUS_POSTED, "posted_journal_id": journal.get("id")}
    problem = await _opening_journal_problem(db, candidate)
    if problem:
        raise OpeningBlocked(str(problem["code"]), str(problem["message"]), extra=problem)
    await _after_step("status")
    await _mark(db, doc["id"], {
        "status": STATUS_POSTED,
        "posted_journal_id": journal.get("id"),
        "journal_number": journal.get("number"),
        "posted_at": now_iso(),
        "blocker_code": None,
        "blocker_message": None,
        "updated_at": now_iso(),
    })
    saved = await get_financial_opening(db, doc["id"])
    await record_accounting_event(
        db,
        event_type="financial_inventory_opening_posted",
        entity_type=SOURCE_TYPE,
        entity_id=doc["id"],
        actor=user,
        after={"journal_id": journal.get("id"), "effective_date": doc.get("effective_date")},
        operation_key=f"fio-post:{doc['id']}",
    )
    return saved


async def _write_opening_journal(db, doc: dict, user: Optional[dict]) -> dict:
    """Post or reuse the journal described by the frozen opening document."""
    source_id = doc["id"]
    if doc.get("journal_origin") == "existing_opening_voucher":
        journal_id = str(doc.get("posted_journal_id") or "")
        journal = await db.journal_entries.find_one({"id": journal_id}, {"_id": 0}) if journal_id else None
        candidate = {**doc, "status": STATUS_POSTED, "posted_journal_id": (journal or {}).get("id")}
        problem = await _opening_journal_problem(db, candidate)
        if problem:
            await _mark(db, source_id, {
                "status": STATUS_BLOCKED,
                "blocker_code": problem["code"],
                "blocker_message": problem["message"],
                "updated_at": now_iso(),
            })
            raise OpeningBlocked(str(problem["code"]), str(problem["message"]), extra=problem)
        await _mark(db, source_id, {
            "posted_journal_id": journal["id"],
            "journal_origin": "existing_opening_voucher",
            "updated_at": now_iso(),
        })
        return journal
    journal = await find_primary_journal_for_source(db, SOURCE_TYPE, source_id)
    if journal and await _journal_has_reversal(db, journal):
        raise OpeningBlocked(
            "OPENING_JOURNAL_NOT_AUTHORITATIVE",
            "Jurnal opening sudah direverse. Retry tidak membuat jurnal baru.",
        )
    if journal:
        candidate = {**doc, "status": STATUS_POSTED, "posted_journal_id": journal.get("id")}
        problem = await _opening_journal_problem(db, candidate)
        if problem:
            raise OpeningBlocked(
                "FROZEN_BASIS_MISMATCH",
                "Jurnal yang sudah ada tidak sama dengan basis opening yang dibekukan.",
                extra=problem,
            )
        return journal
    description = f"Opening persediaan finansial {doc.get('effective_date')}"
    amount = _money(doc.get("total"))
    journal_lines = [
        {"account_id": doc.get("inventory_account_id"), "debit": money_round(amount), "credit": 0, "description": description},
        {"account_id": doc.get("offset_account_id"), "debit": 0, "credit": money_round(amount), "description": description},
    ]
    try:
        journal = await post_balanced_journal(
            db,
            date=str(doc.get("effective_date") or ""),
            source_type=SOURCE_TYPE,
            source_id=source_id,
            description=description,
            lines=journal_lines,
            posted_by=user,
        )
    except PeriodLockedError as exc:
        await _mark(db, source_id, {
            "status": STATUS_BLOCKED,
            "blocker_code": "PERIOD_CLOSED",
            "blocker_message": str(exc),
            "updated_at": now_iso(),
        })
        raise OpeningBlocked("PERIOD_CLOSED", str(exc)) from exc
    if await _journal_has_reversal(db, journal):
        raise OpeningBlocked(
            "OPENING_JOURNAL_NOT_AUTHORITATIVE",
            "Jurnal opening sudah direverse. Retry tidak membuat jurnal baru.",
        )
    await _mark(db, source_id, {
        "posted_journal_id": journal.get("id"),
        "journal_origin": "new_journal",
        "updated_at": now_iso(),
    })
    return journal


async def post_financial_opening(db, payload: dict, user: Optional[dict] = None) -> dict:
    request_id = str((payload or {}).get("client_request_id") or "").strip()
    if not request_id:
        raise OpeningBlocked("REQUEST_ID_REQUIRED", "client_request_id wajib diisi.", status_code=400)
    effective = str((payload or {}).get("effective_date") or "").strip()
    if not parse_business_date(effective):
        raise OpeningBlocked("OPENING_DATE_INVALID", "Tanggal efektif opening harus YYYY-MM-DD.", status_code=400)
    lines = _normalize_lines((payload or {}).get("lines"))
    await _validate_uom(db, lines)
    total = _opening_total(lines)
    if total <= 0:
        raise OpeningBlocked("OPENING_TOTAL_ZERO", "Total opening nol tidak diposting sebagai jurnal.", status_code=400)
    snapshot_id = str((payload or {}).get("snapshot_id") or "").strip()
    lease = await _acquire_lease(db, request_id)
    token = str(lease.get("lease_token") or "")
    try:
        existing = await db[COLLECTION].find_one({"client_request_id": request_id}, {"_id": 0})
        if existing and not _same_opening_payload(existing, effective, lines, total, snapshot_id):
            raise OpeningBlocked(
                "OPENING_PAYLOAD_MISMATCH",
                "Request yang sama tidak boleh mengubah qty, harga, tanggal, atau item yang sudah dibekukan.",
            )
        if existing and existing.get("status") == STATUS_POSTED:
            problem = await _opening_journal_problem(db, existing)
            if problem:
                raise OpeningBlocked(str(problem["code"]), str(problem["message"]), extra=problem)
            return existing
        if existing and existing.get("status") == STATUS_REVERSED:
            raise OpeningBlocked("OPENING_REVERSED", "Opening ini sudah direversal.")
        if existing:
            frozen_error = await _frozen_basis_error(db, existing)
            if frozen_error:
                raise frozen_error
            inventory_id = str(existing.get("inventory_account_id") or "")
            effective = str(existing.get("effective_date") or "")
            total = _money(existing.get("total"))
            frozen_snapshot = str(existing.get("snapshot_id") or "")
        else:
            settings = await get_purchase_accounting_settings(db)
            inventory_id = str(settings.get("inventory_account_id") or "")
            try:
                _validate_inventory(await get_account(db, inventory_id) if inventory_id else None)
            except HTTPException as exc:
                raise OpeningBlocked("INVENTORY_ACCOUNT_UNCONFIGURED", str(exc.detail), status_code=409) from exc
            resolution = await resolve_offset_and_gl(
                db,
                effective_date=effective,
                inventory_account_id=inventory_id,
                total=total,
            )
            frozen_snapshot = snapshot_id
        posted_same_day = await db[COLLECTION].find_one(
            {"status": STATUS_POSTED, "effective_date": effective, "client_request_id": {"$ne": request_id}},
            {"_id": 0, "id": 1},
        )
        if posted_same_day:
            raise OpeningBlocked("OPENING_ALREADY_POSTED", "Opening finansial untuk tanggal ini sudah diposting.")
        if frozen_snapshot:
            coverage = await _coverage_from_snapshot(db, lines, effective, frozen_snapshot)
        else:
            coverage = await classify_opening_coverage(db, lines, effective)
            coverage["blockers"] = _annotate_reconstruction_blockers(coverage["blockers"])
        excluded = coverage["excluded"]
        ambiguous = coverage["ambiguous"]
        watched = list(dict.fromkeys(excluded + [
            str(row.get("receipt_id") or "")
            for row in coverage["blockers"]
            if row.get("receipt_id")
        ]))
        await _assert_no_active_covered_journals(db, watched, inventory_id)
        if coverage["blockers"]:
            first = coverage["blockers"][0]
            raise OpeningBlocked(
                str(first.get("code") or "STOCK_COVERAGE_UNPROVEN"),
                str(first.get("message") or "Cakupan stok pada cutoff tidak terbukti."),
                extra={"blockers": coverage["blockers"], "snapshots": coverage["snapshots"]},
            )
        if existing:
            frozen_excluded = {str(rid) for rid in existing.get("excluded_receipt_ids") or []}
            if frozen_excluded != {str(rid) for rid in excluded}:
                raise OpeningBlocked(
                    "OPENING_COVERAGE_CHANGED",
                    "Cakupan penerimaan berubah sejak basis dibekukan. Settings dan stok baru tidak mengganti basis diam-diam.",
                )
        actor = actor_snapshot(user)
        stamp = now_iso()
        if not existing:
            await _require_lease(db, token)
            opening_id = str(uuid.uuid4())
            frozen_lines = []
            for line in lines:
                frozen_lines.append({
                    **line,
                    "provenance": {
                        "source": SOURCE_APPROVED,
                        "actor": actor,
                        "frozen_at": stamp,
                        "effective_date": effective,
                        "timezone": TARGET_TIMEZONE,
                        "published_price_ignored": True,
                        "snapshot_id": snapshot_id or None,
                    },
                })
            doc = {
                "id": opening_id,
                "client_request_id": request_id,
                "status": STATUS_BUILDING,
                "effective_date": effective,
                "timezone": TARGET_TIMEZONE,
                "inventory_account_id": inventory_id,
                "offset_account_id": resolution.get("offset_account_id"),
                "offset_resolution": resolution.get("offset_resolution"),
                "journal_origin": "existing_opening_voucher" if resolution["mode"] == "link_existing" else "new_journal",
                "existing_opening_voucher_id": resolution.get("voucher_id"),
                "posted_journal_id": resolution.get("journal_id"),
                "snapshot_id": snapshot_id or None,
                "lines": frozen_lines,
                "total": money_round(total),
                "excluded_receipt_ids": excluded,
                "ambiguous_receipt_ids": ambiguous,
                "stock_snapshots": coverage["snapshots"],
                "created_at": stamp,
                "created_by": actor,
                "updated_at": stamp,
            }
            try:
                await db[COLLECTION].insert_one(dict(doc))
            except DuplicateKeyError:
                raced = await db[COLLECTION].find_one({"client_request_id": request_id}, {"_id": 0})
                if raced and raced.get("status") == STATUS_POSTED:
                    problem = await _opening_journal_problem(db, raced)
                    if problem:
                        raise OpeningBlocked(str(problem["code"]), str(problem["message"]), extra=problem)
                    return raced
                raise OpeningBlocked("OPENING_CONFLICT", "Opening finansial bentrok dengan proses lain.")
            doc.pop("_id", None)
        else:
            doc = existing
        await _after_step("lines")
        await _require_lease(db, token)
        journal = await _write_opening_journal(db, doc, user)
        await _after_step("journal")
        await _require_lease(db, token)
        doc = await get_financial_opening(db, doc["id"])
        saved = await _finish_posted(db, doc, journal, user=user)
        return saved
    except OpeningBlocked:
        raise
    except Exception as exc:
        held = await db[CLAIMS].find_one({"_id": CLAIM_ID}, {"_id": 0})
        if (
            not held
            or str(held.get("lease_token") or "") != token
            or _lease_is_expired(held.get("lease_expires_at"))
        ):
            raise OpeningBlocked(
                "OPENING_LEASE_LOST",
                "Lease opening tidak lagi sah. Penulisan dihentikan.",
            ) from exc
        existing = await db[COLLECTION].find_one({"client_request_id": request_id}, {"_id": 0, "id": 1, "status": 1})
        if existing and existing.get("status") != STATUS_POSTED:
            await _mark(db, existing["id"], {
                "status": STATUS_NEEDS_RECOVERY,
                "blocker_code": "OPENING_NEEDS_RECOVERY",
                "blocker_message": str(exc),
                "updated_at": now_iso(),
            })
        raise OpeningBlocked("OPENING_NEEDS_RECOVERY", "Opening finansial belum selesai dan tidak berstatus sukses.") from exc
    finally:
        await _release_lease(db, token, "IDLE")


async def _complete_interrupted_reversal(db, doc: dict, user: Optional[dict]) -> dict:
    if await _opening_is_consumed(db, doc):
        raise OpeningBlocked(
            "OPENING_REVERSED_WHILE_CONSUMED",
            "Jurnal opening sudah direverse tetapi HPP masih mengonsumsinya. Status tidak dijadikan sukses.",
        )
    await _mark(db, doc["id"], {
        "status": STATUS_REVERSED,
        "reversed_at": doc.get("reversed_at") or now_iso(),
        "reversed_by": doc.get("reversed_by") or actor_snapshot(user),
        "updated_at": now_iso(),
    })
    return await get_financial_opening(db, doc["id"])


async def recover_financial_opening(db, *, client_request_id: str, user: Optional[dict] = None) -> dict:
    request_id = str(client_request_id or "").strip()
    doc = await db[COLLECTION].find_one({"client_request_id": request_id}, {"_id": 0})
    if not doc:
        raise OpeningBlocked("OPENING_NOT_FOUND", "Opening finansial tidak ditemukan.", status_code=404)
    if doc.get("status") == STATUS_POSTED:
        posted_journal = await db.journal_entries.find_one({"id": doc.get("posted_journal_id")}, {"_id": 0})
        if posted_journal and await _journal_has_reversal(db, posted_journal):
            return await _complete_interrupted_reversal(db, doc, user)
        problem = await _opening_journal_problem(db, doc)
        if problem:
            raise OpeningBlocked(str(problem["code"]), str(problem["message"]), extra=problem)
        journals = await db.journal_entries.count_documents({
            "source_type": SOURCE_TYPE,
            "source_id": doc["id"],
            "reversal_of": None,
            "status": "posted",
        })
        if doc.get("journal_origin") == "new_journal" and journals != 1:
            raise OpeningBlocked("OPENING_JOURNAL_DUPLICATE", "Jurnal opening tidak unik.")
        return doc
    if doc.get("status") not in {STATUS_BUILDING, STATUS_NEEDS_RECOVERY}:
        raise OpeningBlocked("OPENING_NOT_RECOVERABLE", "Status opening tidak dapat dipulihkan.")
    journal = await find_primary_journal_for_source(db, SOURCE_TYPE, doc["id"])
    if doc.get("journal_origin") == "existing_opening_voucher":
        linked = await db.journal_entries.find_one({"id": doc.get("posted_journal_id")}, {"_id": 0})
        journal = linked or journal
    if journal and await _journal_has_reversal(db, journal):
        return await _complete_interrupted_reversal(db, doc, user)
    if not journal:
        return await post_financial_opening(
            db,
            {
                "client_request_id": request_id,
                "effective_date": doc.get("effective_date"),
                "lines": doc.get("lines") or [],
                "snapshot_id": doc.get("snapshot_id") or "",
            },
            user,
        )
    candidate = {**doc, "status": STATUS_POSTED, "posted_journal_id": journal.get("id")}
    problem = await _opening_journal_problem(db, candidate)
    if problem:
        raise OpeningBlocked(str(problem["code"]), str(problem["message"]), extra=problem)
    saved = await _finish_posted(db, doc, journal, user=user)
    journals = await db.journal_entries.count_documents({
        "source_type": SOURCE_TYPE,
        "source_id": doc["id"],
        "reversal_of": None,
    })
    if doc.get("journal_origin") != "existing_opening_voucher" and journals != 1:
        raise OpeningBlocked("OPENING_JOURNAL_DUPLICATE", "Recovery menghasilkan jurnal ganda.")
    return saved


async def reverse_financial_opening(db, opening_id: str, user: Optional[dict], reason: str) -> dict:
    doc = await get_financial_opening(db, opening_id)
    if not doc or doc.get("status") != STATUS_POSTED:
        raise OpeningBlocked("OPENING_NOT_POSTED", "Opening posted tidak ditemukan.", status_code=404)
    text = str(reason or "").strip()
    if len(text) < 3:
        raise OpeningBlocked("REVERSAL_REASON_REQUIRED", "Alasan reversal wajib diisi.", status_code=400)
    keys = _opening_item_keys(doc)
    from services.inventory_financial_locks import ensure_indexes as ensure_lock_indexes

    await ensure_lock_indexes(db)
    locks = await acquire_item_locks(db, keys, f"opening-reverse:{doc['id']}")
    if keys and locks is None:
        raise OpeningBlocked(LOCK_CONFLICT_CODE, LOCK_CONFLICT_MESSAGE)
    try:
        doc = await get_financial_opening(db, opening_id)
        if not doc or doc.get("status") != STATUS_POSTED:
            raise OpeningBlocked("OPENING_NOT_POSTED", "Opening posted tidak ditemukan.", status_code=404)
        if await _opening_is_consumed(db, doc):
            raise OpeningBlocked(
                "OPENING_ALREADY_CONSUMED",
                "Opening sudah dikonsumsi HPP dan tidak dapat direversal.",
            )
        if doc.get("journal_origin") == "existing_opening_voucher":
            raise OpeningBlocked(
                "OPENING_LINKED_VOUCHER",
                "Opening ini menempel pada saldo awal GL yang sudah ada. Batalkan lewat saldo awal, bukan jurnal kedua.",
            )
        journal = await db.journal_entries.find_one({"id": doc.get("posted_journal_id")}, {"_id": 0})
        if journal and await _journal_has_reversal(db, journal):
            return await _complete_interrupted_reversal(db, doc, user)
        problem = await _opening_journal_problem(db, doc)
        if problem:
            raise OpeningBlocked(str(problem["code"]), str(problem["message"]), extra=problem)
        await _after_step("reverse-checked")
        try:
            await reverse_journal(
                db,
                str(doc.get("posted_journal_id") or ""),
                source_type=SOURCE_REVERSAL,
                source_id=f"{doc['id']}:reversal",
                posted_by=user,
                description=text,
            )
        except PeriodLockedError as exc:
            raise OpeningBlocked("PERIOD_CLOSED", str(exc)) from exc
        if await _opening_is_consumed(db, doc):
            raise OpeningBlocked(
                "OPENING_REVERSED_WHILE_CONSUMED",
                "Jurnal opening sudah direverse tetapi HPP masih mengonsumsinya. Status tidak dijadikan sukses.",
            )
        await _mark(db, doc["id"], {
            "status": STATUS_REVERSED,
            "reversed_at": now_iso(),
            "reversed_by": actor_snapshot(user),
            "reversal_reason": text,
            "updated_at": now_iso(),
        })
        await record_accounting_event(
            db,
            event_type="financial_inventory_opening_reversed",
            entity_type=SOURCE_TYPE,
            entity_id=doc["id"],
            actor=user,
            reason=text,
            operation_key=f"fio-reverse:{doc['id']}",
        )
        return await get_financial_opening(db, doc["id"])
    finally:
        await release_item_locks(db, locks)


def apply_opening_to_acquisitions(
    acquisitions: List[dict],
    openings: List[dict],
    report: Optional[dict] = None,
) -> List[dict]:
    """Drop historical GR acquisitions already inside a posted opening.

    Ambiguous receipt dates are not silently treated as new acquisitions.
    """
    posted = [row for row in openings or [] if row.get("status") == STATUS_POSTED]
    if not posted:
        return list(acquisitions or [])
    covered = {}
    excluded = set()
    ambiguous = set()
    for opening in posted:
        for line in opening.get("lines") or []:
            key = str(line.get("financial_inventory_item_key") or "")
            if key:
                covered[key] = str(opening.get("effective_date") or "")
        excluded.update(str(rid) for rid in opening.get("excluded_receipt_ids") or [])
        ambiguous.update(str(rid) for rid in opening.get("ambiguous_receipt_ids") or [])
    kept: List[dict] = []
    for acq in acquisitions or []:
        if str(acq.get("source") or "") == SOURCE_APPROVED:
            kept.append(acq)
            continue
        key = str(acq.get("financial_inventory_item_key") or "")
        if key not in covered:
            kept.append(acq)
            continue
        receipt_id = str(acq.get("receipt_id") or "")
        if receipt_id in ambiguous:
            if report is not None:
                report.setdefault("receipt_ids", []).append(receipt_id)
                report.setdefault("item_keys", set()).add(key)
            continue
        if receipt_id in excluded:
            continue
        business = wib_business_date(acq.get("receipt_date"))
        if business is None or business < covered[key]:
            if business is None and report is not None:
                report.setdefault("receipt_ids", []).append(receipt_id)
                report.setdefault("item_keys", set()).add(key)
            continue
        kept.append(acq)
    for opening in posted:
        kept.extend(opening_acquisition_rows(opening))
    return kept


async def build_financial_book_reconciliation(db) -> dict:
    """Read-only opening + posted GR − posted COGS against the inventory GL.

    A linked opening voucher is counted once. Overlap and other GL sources stay
    visible. Missing coverage is not reported as zero or MATCH.
    """
    from services.inventory_issue_accounting import (
        load_live_acquisitions,
        load_live_issue_bases,
        load_live_issue_journals_by_movement,
        load_pengiriman_movements,
        movement_id_of,
    )

    before_openings = await db[COLLECTION].count_documents({})
    before_journals = await db.journal_entries.count_documents({})
    _good, problems = await partition_posted_openings(db)
    if problems:
        if (
            await db[COLLECTION].count_documents({}) != before_openings
            or await db.journal_entries.count_documents({}) != before_journals
        ):
            raise OpeningBlocked("RECONCILIATION_WROTE", "Rekonsiliasi menulis data.", status_code=500)
        return {
            "writes": 0,
            "inventory_account_id": None,
            "opening_value": None,
            "goods_receipt_value": None,
            "cogs_value": None,
            "opening_qty": None,
            "goods_receipt_qty": None,
            "cogs_qty": None,
            "financial_qty_remaining": None,
            "financial_remaining": None,
            "inventory_gl_balance": None,
            "historical_gr_overlap": None,
            "other_gl_sources": {},
            "other_gl_total": None,
            "sources": {},
            "coverage_complete": False,
            "reconciliation_status": "BLOCKED",
            "blockers": problems,
            "published_is_book_value": False,
            "pending_cogs_count": None,
            "physical_vs_financial": (
                "Qty fisik adalah saldo operasional saat ini. Qty finansial adalah opening dan GR yang jurnalnya masih sah, dikurangi COGS posted. "
                "Keduanya berbeda saat posting keuangan masih pending."
            ),
            "item_hpp_scope": (
                "HPP barang hanya dari acquisition opening atau GR yang disetujui. "
                "Harga cup kosong dan Published Pricing tidak menutup biaya sablon. "
                "Biaya produksi yang sudah dijurnal tetap pada klasifikasi akunnya."
            ),
            "identity": "opening_posted + gr_posted - cogs_posted = financial_remaining",
            "historical_as_of": False,
        }
    settings = await get_purchase_accounting_settings(db)
    inventory_id = str(settings.get("inventory_account_id") or "")
    acquisitions = await load_live_acquisitions(db)
    openings = await load_posted_openings(db)
    opening_value = _money(sum(
        (_money(row.get("total_cost")) for row in acquisitions if row.get("source") == SOURCE_APPROVED),
        Decimal("0"),
    ))
    gr_value = _money(sum(
        (_money(row.get("total_cost")) for row in acquisitions if row.get("source") != SOURCE_APPROVED),
        Decimal("0"),
    ))
    issues = await load_live_issue_bases(db)
    cogs_value = _money(sum(
        (_money((row.get("basis") or {}).get("total_cogs")) for row in issues),
        Decimal("0"),
    ))
    opening_qty = sum(
        (_dec(row.get("base_qty")) for row in acquisitions if row.get("source") == SOURCE_APPROVED),
        Decimal("0"),
    )
    gr_qty = sum(
        (_dec(row.get("base_qty")) for row in acquisitions if row.get("source") != SOURCE_APPROVED),
        Decimal("0"),
    )
    cogs_qty = sum(
        (_dec((row.get("basis") or {}).get("issue_base_qty") or (row.get("basis") or {}).get("base_qty")) for row in issues),
        Decimal("0"),
    )
    has_basis = bool(acquisitions or issues or openings)
    remaining = _money(opening_value + gr_value - cogs_value) if has_basis else None
    excluded = {str(rid) for opening in openings for rid in (opening.get("excluded_receipt_ids") or [])}
    ambiguous = {str(rid) for opening in openings for rid in (opening.get("ambiguous_receipt_ids") or [])}
    linked_ids = {
        str(opening.get("posted_journal_id") or "")
        for opening in openings
        if opening.get("journal_origin") == "existing_opening_voucher"
    }
    buckets: Dict[str, Decimal] = {}
    if inventory_id:
        gl_lines = await db.journal_lines.find({"account_id": inventory_id}, {"_id": 0}).to_list(None)
        journal_ids = sorted({str(line.get("journal_id") or "") for line in gl_lines if line.get("journal_id")})
        journals = {
            str(row.get("id") or ""): row
            for row in await db.journal_entries.find({"id": {"$in": journal_ids}}, {"_id": 0}).to_list(len(journal_ids) + 5)
        } if journal_ids else {}
        for line in gl_lines:
            journal = journals.get(str(line.get("journal_id") or "")) or {}
            if str(journal.get("status") or "") != "posted":
                continue
            signed = _money(line.get("debit") or 0) - _money(line.get("credit") or 0)
            source = str(journal.get("source_type") or "unknown")
            source_id = str(journal.get("source_id") or "")
            if source == "purchase_goods_receipt" and source_id in excluded:
                key = "historical_gr_overlap"
            elif journal.get("id") in linked_ids:
                key = "opening_linked_voucher"
            elif source == SOURCE_TYPE:
                key = "opening_journal"
            elif source == "purchase_goods_receipt":
                key = "goods_receipt"
            elif source == "inventory_issue":
                key = "cogs"
            else:
                key = f"other:{source}"
            buckets[key] = _money(buckets.get(key, 0) + signed)
    gl_total = _money(sum(buckets.values(), Decimal("0"))) if buckets else (Decimal("0.00") if inventory_id else None)
    overlap = buckets.get("historical_gr_overlap", Decimal("0.00"))
    other_keys = {key: money_round(value) for key, value in buckets.items() if key.startswith("other:")}
    other_total = _money(sum((_dec(value) for value in other_keys.values()), Decimal("0")))
    live_issue_journals = await load_live_issue_journals_by_movement(db)
    pending_cogs = [
        movement_id_of(movement)
        for movement in await load_pengiriman_movements(db)
        if movement_id_of(movement) not in live_issue_journals
    ]
    coverage_complete = not ambiguous and overlap == 0 and not other_keys and not pending_cogs
    if remaining is None:
        status = "INCOMPLETE"
    elif pending_cogs or not coverage_complete:
        status = "INCOMPLETE"
    elif gl_total is not None and remaining == gl_total:
        status = "MATCH"
    else:
        status = "DIFFERENCE"
    if (
        await db[COLLECTION].count_documents({}) != before_openings
        or await db.journal_entries.count_documents({}) != before_journals
    ):
        raise OpeningBlocked("RECONCILIATION_WROTE", "Rekonsiliasi menulis data.", status_code=500)
    return {
        "writes": 0,
        "inventory_account_id": inventory_id or None,
        "opening_value": money_round(opening_value),
        "goods_receipt_value": money_round(gr_value),
        "cogs_value": money_round(cogs_value),
        "opening_qty": float(qty_round(opening_qty)) if has_basis else None,
        "goods_receipt_qty": float(qty_round(gr_qty)) if has_basis else None,
        "cogs_qty": float(qty_round(cogs_qty)) if has_basis else None,
        "financial_qty_remaining": float(qty_round(opening_qty + gr_qty - cogs_qty)) if has_basis else None,
        "financial_remaining": None if remaining is None else money_round(remaining),
        "inventory_gl_balance": None if gl_total is None else money_round(gl_total),
        "historical_gr_overlap": money_round(overlap),
        "other_gl_sources": other_keys,
        "other_gl_total": money_round(other_total),
        "sources": {key: money_round(value) for key, value in buckets.items()},
        "coverage_complete": coverage_complete,
        "reconciliation_status": status,
        "published_is_book_value": False,
        "pending_cogs_count": len(pending_cogs),
        "physical_vs_financial": (
            "Qty fisik adalah saldo operasional saat ini. Qty finansial adalah opening dan GR yang jurnalnya masih sah, dikurangi COGS posted. "
            "Keduanya berbeda saat posting keuangan masih pending."
        ),
        "item_hpp_scope": (
            "HPP barang hanya dari acquisition opening atau GR yang disetujui. "
            "Harga cup kosong dan Published Pricing tidak menutup biaya sablon. "
            "Biaya produksi yang sudah dijurnal tetap pada klasifikasi akunnya."
        ),
        "identity": "opening_posted + gr_posted - cogs_posted = financial_remaining",
        "historical_as_of": False,
    }
