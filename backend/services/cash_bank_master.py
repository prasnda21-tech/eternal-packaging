"""Kas & Bank master accounts — 1 cash/bank ↔ exact 1 COA."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pymongo.errors import DuplicateKeyError

from services.chart_of_accounts import get_account
from services.purchase_common import actor_snapshot, now_iso

COLLECTION = "cash_bank_accounts"
# Readers are attempt ids for one commit call, not the business preview id.
# A second request of the same preview pins a different attempt.
# currency_writer is set only while a currency change is being committed.
# currency_writer_until makes a dead writer eligible for takeover. Expiry does
# not delete the writer by itself; the next claim replaces it and the old
# write is fenced by the writer id.
IMPORT_READERS = "import_currency_readers"
CURRENCY_WRITER = "currency_writer"
CURRENCY_WRITER_UNTIL = "currency_writer_until"
COORDINATION_LEASE = timedelta(seconds=120)

PERM_VIEW = "cash_bank.view"
PERM_MANAGE = "cash_bank.manage"
PERM_POST = "cash_bank.post"
PERM_REVERSE = "cash_bank.reverse"
PERM_RECONCILE = "cash_bank.reconcile"
PERM_PURGE_UNMATCHED = "cash_bank.reconcile.purge_unmatched"


class CashBankValidationError(ValueError):
    pass


class CashBankConflictError(Exception):
    pass


BANK_ACCOUNT_TYPE = "BANK"
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")
# Explicit application allowlist. Empty stays unknown. This is not inferred
# from a bank name and it is not a foreign-exchange master.
SUPPORTED_ACCOUNT_CURRENCIES = frozenset({
    "IDR", "USD", "EUR", "SGD", "JPY", "CNY", "GBP", "AUD",
    "MYR", "THB", "HKD", "KRW", "CHF", "CAD", "NZD", "SAR", "AED", "INR",
})


def coordination_deadline() -> str:
    return (datetime.now(timezone.utc) + COORDINATION_LEASE).isoformat()


def reader_blocks_currency(reader: Any, now: str) -> bool:
    """Live readers block a currency change. Expired attempt objects do not.

    A bare string is a legacy preview id. It still blocks until the fence
    takeover that owns that id pulls it. It is not removed by age alone.
    """
    if isinstance(reader, str):
        return bool(reader)
    if isinstance(reader, dict):
        until = reader.get("until")
        if not until:
            return bool(reader.get("attempt_id"))
        return str(until) > now
    return False


def _no_live_reader_filter(now: str) -> Dict[str, Any]:
    return {
        "$nor": [
            {IMPORT_READERS: {"$elemMatch": {"$type": "string"}}},
            {IMPORT_READERS: {"$elemMatch": {"until": {"$gt": now}}}},
            {IMPORT_READERS: {"$elemMatch": {"attempt_id": {"$exists": True}, "until": {"$exists": False}}}},
        ]
    }


def _writer_takeover_filter(now: str) -> Dict[str, Any]:
    """None, an expired lease, or a writer left without a lease (dead owner)."""
    return {
        "$or": [
            {CURRENCY_WRITER: None},
            {CURRENCY_WRITER_UNTIL: {"$lte": now}},
            {
                "$and": [
                    {CURRENCY_WRITER: {"$ne": None}},
                    {CURRENCY_WRITER_UNTIL: {"$exists": False}},
                ]
            },
        ]
    }


def normalize_currency_code(raw: Any, *, allow_empty: bool = False) -> Optional[str]:
    """Explicit supported code. Empty is unknown, never an implied IDR default."""
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if not text:
        if allow_empty:
            return None
        raise CashBankValidationError("Mata uang rekening harus diisi jika field dikirim")
    if not _CURRENCY_CODE.fullmatch(text) or text not in SUPPORTED_ACCOUNT_CURRENCIES:
        raise CashBankValidationError(
            "Mata uang tidak ada pada daftar yang didukung aplikasi. Kosong berarti tidak diketahui, bukan IDR."
        )
    return text


async def _recon_currency_locked(db, cash_bank_id: str) -> bool:
    cid = str(cash_bank_id)
    if await db["cash_bank_statement_imports"].count_documents({"cash_bank_id": cid}, limit=1):
        return True
    if await db["cash_bank_statement_lines"].count_documents({"cash_bank_id": cid}, limit=1):
        return True
    if await db["cash_bank_reconciliation_matches"].count_documents(
        {"cash_bank_id": cid, "match_kind": "pair"}, limit=1
    ):
        return True
    return False


def require_bank_coa(coa: Optional[dict]) -> dict:
    """Kas/Bank may only link to a COA with account_type == BANK.

    Unknown / missing COA and missing/null account_type are rejected.
    Legacy masters already linked to non-BANK remain readable via get/list.
    """
    if not coa:
        raise CashBankValidationError("Akun Perkiraan tidak ditemukan")
    raw_type = coa.get("account_type")
    account_type = str(raw_type).strip().upper() if raw_type is not None else ""
    if not account_type:
        raise CashBankValidationError(
            "Akun Perkiraan harus bertipe BANK (tipe akun kosong)"
        )
    if account_type != BANK_ACCOUNT_TYPE:
        raise CashBankValidationError(
            f"Akun Perkiraan harus bertipe BANK (saat ini: {account_type})"
        )
    return coa


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("account_id", unique=True)
    await col.create_index("name")
    await col.create_index("active")


def public_account(doc: Optional[dict], *, coa: Optional[dict] = None) -> Optional[dict]:
    if not doc:
        return None
    out = {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "account_id": doc.get("account_id"),
        "description": doc.get("description") or "",
        "active": bool(doc.get("active", True)),
        "currency_code": doc.get("currency_code") or None,
        "currency_note": "Konfigurasi operator, bukan bukti independen bank.",
        "currency_history": doc.get("currency_history") or [],
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }
    if coa:
        out["account"] = {
            "id": coa.get("id"),
            "code": coa.get("code"),
            "name": coa.get("name"),
        }
    elif doc.get("account_code") or doc.get("account_name"):
        out["account"] = {
            "id": doc.get("account_id"),
            "code": doc.get("account_code"),
            "name": doc.get("account_name"),
        }
    return out


async def enrich_account(db, doc: dict) -> dict:
    coa = await get_account(db, doc.get("account_id") or "")
    return public_account(doc, coa=coa) or public_account(doc)


async def list_cash_banks(
    db,
    *,
    q: Optional[str] = None,
    active_only: bool = False,
) -> List[dict]:
    query: Dict[str, Any] = {}
    if active_only:
        query["active"] = True
    needle = (q or "").strip()
    if needle:
        query["$or"] = [
            {"name": {"$regex": needle, "$options": "i"}},
            {"description": {"$regex": needle, "$options": "i"}},
            {"account_code": {"$regex": needle, "$options": "i"}},
            {"account_name": {"$regex": needle, "$options": "i"}},
        ]
    docs = await db[COLLECTION].find(query, {"_id": 0}).sort("name", 1).to_list(2000)
    out = []
    for d in docs:
        out.append(await enrich_account(db, d))
    return out


async def get_cash_bank(db, cash_bank_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": str(cash_bank_id)}, {"_id": 0})
    if not doc:
        return None
    return await enrich_account(db, doc)


async def get_cash_bank_raw(db, cash_bank_id: str) -> Optional[dict]:
    return await db[COLLECTION].find_one({"id": str(cash_bank_id)}, {"_id": 0})


async def create_cash_bank(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    name = str((raw or {}).get("name") or "").strip()
    account_id = str((raw or {}).get("account_id") or "").strip()
    description = str((raw or {}).get("description") or "").strip()
    active = (raw or {}).get("active", True)
    if isinstance(active, str):
        active = active.strip().lower() not in ("0", "false", "no", "nonaktif")
    else:
        active = bool(active)

    if not name:
        raise CashBankValidationError("Nama Kas / Bank wajib diisi")
    if not account_id:
        raise CashBankValidationError("Akun Perkiraan wajib diisi")
    if "currency_code" in (raw or {}):
        currency_code = normalize_currency_code((raw or {}).get("currency_code"), allow_empty=True)
    else:
        currency_code = None

    coa = require_bank_coa(await get_account(db, account_id))

    existing = await db[COLLECTION].find_one({"account_id": account_id}, {"_id": 0, "id": 1, "name": 1})
    if existing:
        raise CashBankConflictError(
            f"Akun Perkiraan sudah dipakai oleh Kas/Bank '{existing.get('name')}'"
        )

    now = now_iso()
    actor = actor_snapshot(user)
    doc = {
        "id": str(uuid.uuid4()),
        "name": name,
        "account_id": account_id,
        "account_code": coa.get("code"),
        "account_name": coa.get("name"),
        "description": description,
        "active": active,
        "currency_code": currency_code,
        "currency_history": [{
            "before": None,
            "after": currency_code,
            "at": now,
            "actor": actor,
        }] if currency_code else [],
        "created_at": now,
        "created_by": actor,
        "updated_at": now,
        "updated_by": actor,
    }
    try:
        await db[COLLECTION].insert_one(dict(doc))
    except DuplicateKeyError as exc:
        raise CashBankConflictError(
            "Akun Perkiraan sudah dipakai oleh Kas/Bank lain"
        ) from exc
    return await enrich_account(db, doc)


async def update_cash_bank(
    db, cash_bank_id: str, raw: dict, *, user: Optional[dict] = None
) -> dict:
    doc = await get_cash_bank_raw(db, cash_bank_id)
    if not doc:
        raise KeyError("missing")

    name = str((raw or {}).get("name") if "name" in (raw or {}) else doc.get("name") or "").strip()
    description = str(
        (raw or {}).get("description") if "description" in (raw or {}) else doc.get("description") or ""
    ).strip()
    if "active" in (raw or {}):
        active = (raw or {}).get("active")
        if isinstance(active, str):
            active = active.strip().lower() not in ("0", "false", "no", "nonaktif")
        else:
            active = bool(active)
    else:
        active = bool(doc.get("active", True))

    updates: Dict[str, Any] = {
        "name": name,
        "description": description,
        "active": active,
    }
    currency_event = None
    currency_writer = None
    if "currency_code" in (raw or {}):
        new_currency = normalize_currency_code(
            (raw or {}).get("currency_code"), allow_empty=True
        )
        old_currency = doc.get("currency_code") or None
        if new_currency != old_currency:
            if await _recon_currency_locked(db, doc["id"]):
                raise CashBankConflictError(
                    "Mata uang tidak diubah karena sudah ada impor atau pencocokan. "
                    "Tidak ada konversi atau backfill. Perlu alur koreksi terpisah."
                )
            writer = str(uuid.uuid4())
            now_claim = now_iso()
            claimed = await db[COLLECTION].update_one(
                {
                    "id": doc["id"],
                    "currency_code": old_currency,
                    "$and": [
                        _writer_takeover_filter(now_claim),
                        _no_live_reader_filter(now_claim),
                    ],
                },
                {
                    "$set": {
                        CURRENCY_WRITER: writer,
                        CURRENCY_WRITER_UNTIL: coordination_deadline(),
                    },
                    "$pull": {IMPORT_READERS: {"until": {"$lte": now_claim}}},
                },
            )
            if claimed.matched_count != 1:
                current = await get_cash_bank_raw(db, doc["id"])
                readers = [
                    item for item in ((current or {}).get(IMPORT_READERS) or [])
                    if reader_blocks_currency(item, now_iso())
                ]
                if readers:
                    first = readers[0]
                    operation = first.get("operation_id") if isinstance(first, dict) else first
                    raise CashBankConflictError(
                        "Impor sedang menyimpan mutasi pada mata uang rekening ini. "
                        f"Ulangi penyimpanan pratinjau {operation} untuk melepaskan rekening. "
                        "Perubahan mata uang ditolak."
                    )
                raise CashBankConflictError(
                    "Mata uang rekening berubah atau sedang digunakan impor. Muat ulang."
                )
            try:
                if await _recon_currency_locked(db, doc["id"]):
                    raise CashBankConflictError(
                        "Mata uang tidak diubah karena sudah ada impor atau pencocokan. "
                        "Tidak ada konversi atau backfill. Perlu alur koreksi terpisah."
                    )
                updates["currency_code"] = new_currency
                currency_event = {
                    "before": old_currency,
                    "after": new_currency,
                    "at": now_iso(),
                    "actor": actor_snapshot(user),
                }
                currency_writer = writer
            except Exception:
                await db[COLLECTION].update_one(
                    {"id": doc["id"], CURRENCY_WRITER: writer},
                    {"$unset": {CURRENCY_WRITER: "", CURRENCY_WRITER_UNTIL: ""}},
                )
                raise

    # Account link may change, but only to an unused BANK COA.
    # Leaving the existing link unchanged is always allowed (legacy non-BANK readable/editable).
    try:
        if "account_id" in (raw or {}):
            new_aid = str((raw or {}).get("account_id") or "").strip()
            if not new_aid:
                raise CashBankValidationError("Akun Perkiraan wajib diisi")
            if new_aid != doc.get("account_id"):
                coa = require_bank_coa(await get_account(db, new_aid))
                existing = await db[COLLECTION].find_one(
                    {"account_id": new_aid}, {"_id": 0, "id": 1, "name": 1}
                )
                if existing and existing.get("id") != doc.get("id"):
                    raise CashBankConflictError(
                        f"Akun Perkiraan sudah dipakai oleh Kas/Bank '{existing.get('name')}'"
                    )
                updates["account_id"] = new_aid
                updates["account_code"] = coa.get("code")
                updates["account_name"] = coa.get("name")

        if not name:
            raise CashBankValidationError("Nama Kas / Bank wajib diisi")

        now = now_iso()
        actor = actor_snapshot(user)
        updates["updated_at"] = now
        updates["updated_by"] = actor
        write: Dict[str, Any] = {"$set": updates}
        if currency_event:
            write["$push"] = {"currency_history": currency_event}
        write_filter: Dict[str, Any] = {"id": doc["id"]}
        if currency_writer:
            write_filter[CURRENCY_WRITER] = currency_writer
        try:
            written = await db[COLLECTION].update_one(write_filter, write)
        except DuplicateKeyError as exc:
            raise CashBankConflictError(
                "Akun Perkiraan sudah dipakai oleh Kas/Bank lain"
            ) from exc
        if currency_writer and written.matched_count != 1:
            raise CashBankConflictError(
                "Mata uang rekening berubah atau sedang digunakan impor. Muat ulang."
            )
    finally:
        if currency_writer:
            await db[COLLECTION].update_one(
                {"id": doc["id"], CURRENCY_WRITER: currency_writer},
                {"$unset": {CURRENCY_WRITER: "", CURRENCY_WRITER_UNTIL: ""}},
            )
    return await get_cash_bank(db, doc["id"])


async def require_active_cash_bank(db, cash_bank_id: str) -> dict:
    doc = await get_cash_bank_raw(db, cash_bank_id)
    if not doc:
        raise CashBankValidationError("Kas/Bank tidak ditemukan")
    if not doc.get("active", True):
        raise CashBankValidationError("Kas/Bank nonaktif tidak dapat digunakan")
    return doc
