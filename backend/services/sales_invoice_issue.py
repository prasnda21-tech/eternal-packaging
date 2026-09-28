"""One-action sales invoice issue and delete.

Simpan Faktur freezes the commercial snapshot and journal plan together, then
finalizes and posts through the existing invoice services. Hapus Faktur reverses
a complete journal, or removes a document that has none, and hides it from the
active list and Live Tracking. Manual post and reverse endpoints keep their
own permissions. This module does not grant those permissions.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure

from services.accounting_periods import (
    PeriodLockedError,
    STATUS_CLOSED,
    get_period,
    period_key_from_date,
)
from services.journal_core import (
    JournalConflictError,
    JournalValidationError,
    complete_primary_source_lines,
    find_primary_journal_for_source,
    post_balanced_journal,
    primary_lines_match_plan,
)
from services.sales_accounting_settings import (
    _load_account,
    _reject_unusable_ledger_account,
    _validate_output_tax,
    _validate_receivable,
    _validate_revenue,
    get_sales_accounting_settings,
)
from services.sales_invoice_posting import (
    ACCOUNTING_POSTED,
    ACCOUNTING_REVERSED,
    SOURCE_REVERSAL,
    SOURCE_TYPE,
    accounting_status_from_journal,
    find_sales_invoice_reversal_journal,
    find_sales_invoice_source_journal,
    journal_date_from_invoice,
    posting_claim_is_active,
    receipt_create_claim_absent_or_stale_clause,
    receipt_create_claim_is_active,
    reverse_claim_is_active,
    reverse_sales_invoice_accounting,
    _build_journal_lines,
    _money,
)
from services.sales_invoices import (
    FINANCIAL_CANCELLED,
    FINANCIAL_DRAFT,
    FINANCIAL_FINALIZED,
    effective_financial_status,
    finalize_sales_invoice,
    get_sales_invoice_detail,
    now_iso,
)

QTY_COLLECTION = "sales_invoice_line_qty"
HOLD_COLLECTION = "sales_invoice_qty_holds"
_qty_reserve_hook = None
AUDIT_ACTION_ISSUE = "sales.invoice.issue"
AUDIT_ACTION_DELETE = "sales.invoice.remove"
DELETE_REASON = "Pembalikan otomatis karena faktur dihapus oleh pengguna berizin."
FENCE_TTL_SECONDS = 600
ISSUE_STATE_ISSUING = "issuing"
ISSUE_STATE_ISSUED = "issued"


def canonical_payload_hash(payload: Dict[str, Any]) -> str:
    """Stable hash of the commercial request. The journal plan is not part of it."""
    lines = []
    for raw in payload.get("lines") or payload.get("line_items") or []:
        if not isinstance(raw, dict):
            continue
        price = raw.get("unit_price")
        lines.append({
            "line_idx": int(raw.get("line_idx") or 0),
            "line_id": str(raw.get("line_id") or "").strip(),
            "quantity": str(int(raw.get("quantity") or 0)),
            "unit_price": _decimal_text(price) if price not in (None, "") else "",
        })
    lines.sort(key=lambda row: (row["line_idx"], row["line_id"]))
    charges = []
    for raw in payload.get("other_charges") or []:
        if not isinstance(raw, dict):
            continue
        charges.append({
            "account_id": str(raw.get("account_id") or "").strip(),
            "description": str(raw.get("description") or "").strip(),
            "amount": _decimal_text(raw.get("amount")),
        })
    charges.sort(key=lambda row: (row["account_id"], row["description"], row["amount"]))
    body = {
        "po_id": str(payload.get("po_id") or "").strip(),
        "invoice_date": str(payload.get("invoice_date") or "").strip()[:10],
        "payment_term_days": str(int(payload.get("payment_term_days") or 0)),
        "discount_amount": _decimal_text(payload.get("discount_amount") or 0),
        "ppn_percent": _decimal_text(payload.get("ppn_percent") or 0),
        "notes": str(payload.get("notes") or "").strip(),
        "lines": lines,
        "other_charges": charges,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _decimal_text(raw: Any) -> str:
    return str(_money(raw, "nominal"))


def _continuable(message: str, operation_id: str, *, code: str) -> HTTPException:
    return HTTPException(409, {
        "message": message,
        "operation_id": operation_id,
        "continuable": True,
        "code": code,
    })


def _conflict(message: str, operation_id: str) -> HTTPException:
    return HTTPException(409, {
        "message": message,
        "operation_id": operation_id,
        "continuable": False,
        "code": "idempotency_conflict",
    })


async def ensure_indexes(db) -> None:
    """Additive indexes only. A legacy duplicate key does not delete documents."""
    try:
        await db.sales_invoices.create_index(
            "idempotency_key",
            unique=True,
            partialFilterExpression={"idempotency_key": {"$type": "string"}},
            name="sales_invoice_idempotency_key_partial_unique",
        )
    except (DuplicateKeyError, OperationFailure) as exc:
        if "E11000" not in str(exc) and "duplicate" not in str(exc).lower() and "IndexOptionsConflict" not in str(exc):
            if "already exists" not in str(exc).lower():
                raise
    await db[HOLD_COLLECTION].create_index("po_id")
    try:
        await db.audit_logs.create_index(
            "operation_key",
            unique=True,
            partialFilterExpression={"operation_key": {"$type": "string"}},
            name="audit_logs_operation_key_partial_unique",
        )
    except (DuplicateKeyError, OperationFailure) as exc:
        if "already exists" not in str(exc).lower() and "IndexOptionsConflict" not in str(exc):
            if "E11000" not in str(exc) and "duplicate" not in str(exc).lower():
                raise


def invoice_consumes_qty(invoice: Optional[dict]) -> bool:
    if not invoice:
        return False
    if invoice.get("removed_from_active"):
        return False
    return effective_financial_status(invoice) != FINANCIAL_CANCELLED


async def _consumed_qty(db, po_id: str, line_idx: int) -> int:
    total = 0
    cursor = db.sales_invoices.find(
        {"po_id": str(po_id)},
        {"_id": 0, "lines": 1, "financial_status": 1, "removed_from_active": 1},
    )
    async for inv in cursor:
        if not invoice_consumes_qty(inv):
            continue
        for line in inv.get("lines") or []:
            try:
                if int(line.get("line_idx", 0)) == int(line_idx):
                    total += int(line.get("quantity") or 0)
            except (TypeError, ValueError):
                continue
    return total


async def _seed_line(db, po_id: str, line_idx: int, cap: int) -> None:
    line_id = f"{po_id}:{int(line_idx)}"
    current = await db[QTY_COLLECTION].find_one({"_id": line_id})
    if current and current.get("seeded"):
        await db[QTY_COLLECTION].update_one(
            {"_id": line_id, "reserved": {"$lte": int(cap)}},
            {"$set": {"cap": int(cap)}},
        )
        return
    consumed = await _consumed_qty(db, po_id, line_idx)
    try:
        await db[QTY_COLLECTION].update_one(
            {"_id": line_id, "seeded": {"$ne": True}},
            {"$set": {
                "seeded": True,
                "cap": int(cap),
                "reserved": int(consumed),
                "po_id": str(po_id),
                "line_idx": int(line_idx),
            }},
            upsert=True,
        )
    except DuplicateKeyError:
        current = await db[QTY_COLLECTION].find_one({"_id": line_id})
        if not current or not current.get("seeded"):
            raise HTTPException(409, "Klaim kuantitas faktur belum selesai. Coba lagi dengan kunci yang sama.")


async def claim_invoice_qty(
    db,
    hold_id: str,
    po_id: str,
    lines: List[dict],
    payload_hash: Optional[str],
) -> dict:
    """Atomic remaining-qty claim. Same hold is incremented once."""
    await ensure_indexes(db)
    normalized = []
    for raw in lines:
        qty = int(raw.get("qty") or 0)
        if qty <= 0:
            continue
        normalized.append({
            "line_idx": int(raw["line_idx"]),
            "qty": qty,
            "cap": int(raw["cap"]),
            "item_name": str(raw.get("item_name") or ""),
        })
    try:
        await db[HOLD_COLLECTION].insert_one({
            "_id": str(hold_id),
            "po_id": str(po_id),
            "lines": normalized,
            "payload_hash": str(payload_hash or ""),
            "state": "pending",
        })
        created = True
    except DuplicateKeyError:
        created = False
    hold = await db[HOLD_COLLECTION].find_one({"_id": str(hold_id)})
    stored_hash = str((hold or {}).get("payload_hash") or "")
    if payload_hash and stored_hash and stored_hash != payload_hash:
        raise _conflict("Kunci faktur yang sama dipakai untuk isi yang berbeda.", str(hold_id))
    if hold and hold.get("state") in ("held", "attached", "released"):
        if hold.get("state") == "released":
            raise HTTPException(409, "Operasi kuantitas faktur ini sudah ditutup.")
        return hold
    if not created:
        for _ in range(100):
            hold = await db[HOLD_COLLECTION].find_one({"_id": str(hold_id)})
            if hold and hold.get("state") != "pending":
                break
            import asyncio
            await asyncio.sleep(0)
        if not hold or hold.get("state") == "pending":
            raise _continuable(
                "Klaim kuantitas faktur belum selesai. Coba lagi dengan kunci yang sama.",
                str(hold_id),
                code="qty_hold_pending",
            )
        if payload_hash and hold.get("payload_hash") and hold["payload_hash"] != payload_hash:
            raise _conflict("Kunci faktur yang sama dipakai untuk isi yang berbeda.", str(hold_id))
        return hold

    acquired: List[dict] = []
    try:
        for line in normalized:
            await _seed_line(db, po_id, line["line_idx"], line["cap"])
            hook = _qty_reserve_hook
            if hook is not None:
                hooked = hook(str(hold_id), line)
                if hasattr(hooked, "__await__"):
                    await hooked
            result = await db[QTY_COLLECTION].update_one(
                {
                    "_id": f"{po_id}:{line['line_idx']}",
                    "reserved": {"$lte": int(line["cap"]) - int(line["qty"])},
                },
                {"$inc": {"reserved": int(line["qty"])}},
            )
            if int(getattr(result, "modified_count", 0) or 0) != 1:
                name = line["item_name"] or f"baris {line['line_idx']}"
                raise HTTPException(
                    400,
                    f"Kuantitas faktur untuk '{name}' melebihi sisa PO.",
                )
            acquired.append(line)
        await db[HOLD_COLLECTION].update_one(
            {"_id": str(hold_id)},
            {"$set": {"state": "held"}},
        )
    except Exception:
        for line in acquired:
            await db[QTY_COLLECTION].update_one(
                {
                    "_id": f"{po_id}:{line['line_idx']}",
                    "reserved": {"$gte": int(line["qty"])},
                },
                {"$inc": {"reserved": -int(line["qty"])}},
            )
        await db[HOLD_COLLECTION].delete_one({"_id": str(hold_id), "state": "pending"})
        raise
    return await db[HOLD_COLLECTION].find_one({"_id": str(hold_id)})


async def attach_qty_hold(db, hold_id: str, invoice_id: str) -> None:
    if not hold_id:
        return
    await db[HOLD_COLLECTION].update_one(
        {"_id": str(hold_id), "state": "held"},
        {"$set": {"state": "attached", "invoice_id": str(invoice_id)}},
    )


async def release_invoice_qty(db, hold_id: Optional[str]) -> None:
    if not hold_id:
        return
    prev = await db[HOLD_COLLECTION].find_one_and_update(
        {"_id": str(hold_id), "state": {"$in": ["held", "attached"]}},
        {"$set": {"state": "released"}},
        return_document=ReturnDocument.BEFORE,
    )
    if not prev:
        return
    po_id = str(prev.get("po_id") or "")
    for line in prev.get("lines") or []:
        qty = int(line.get("qty") or 0)
        if qty <= 0:
            continue
        await db[QTY_COLLECTION].update_one(
            {"_id": f"{po_id}:{int(line.get('line_idx') or 0)}", "reserved": {"$gte": qty}},
            {"$inc": {"reserved": -qty}},
        )


async def _require_open_period(db, invoice_date: str) -> str:
    key = period_key_from_date(str(invoice_date)[:10])
    period = await get_period(db, key)
    if str(period.get("status") or "") == STATUS_CLOSED:
        raise HTTPException(409, f"Periode akuntansi {key} sudah ditutup.")
    return key


async def _active_posting_accounts(db) -> dict:
    settings = await get_sales_accounting_settings(db)
    receivable_id = str(settings.get("receivable_account_id") or "").strip()
    revenue_id = str(settings.get("revenue_account_id") or "").strip()
    tax_id = str(settings.get("output_tax_account_id") or "").strip()
    if not (receivable_id and revenue_id and tax_id):
        raise HTTPException(400, "Pengaturan Akuntansi Penjualan belum dikonfigurasi.")
    receivable = _validate_receivable(await _load_account(db, receivable_id))
    _reject_unusable_ledger_account(receivable, "Akun Piutang Usaha")
    revenue = _validate_revenue(await _load_account(db, revenue_id))
    _reject_unusable_ledger_account(revenue, "Akun Pendapatan Penjualan")
    output_tax = _validate_output_tax(await _load_account(db, tax_id))
    _reject_unusable_ledger_account(output_tax, "Akun PPN Keluaran")
    return {
        "receivable_account_id": str(receivable["id"]),
        "revenue_account_id": str(revenue["id"]),
        "output_tax_account_id": str(output_tax["id"]),
    }


def build_issue_plan(invoice: dict, mapped: dict) -> dict:
    grand = _money(invoice.get("grand_total"), "grand_total")
    after = _money(invoice.get("after_discount"), "after_discount")
    ppn = _money(invoice.get("ppn_amount"), "ppn_amount")
    if grand <= 0:
        raise HTTPException(400, "grand_total harus lebih besar dari 0 untuk posting accounting.")
    built = _build_journal_lines(
        receivable_account_id=mapped["receivable_account_id"],
        revenue_account_id=mapped["revenue_account_id"],
        output_tax_account_id=mapped["output_tax_account_id"],
        grand_total=grand,
        after_discount=after,
        ppn_amount=ppn,
        other_charges=invoice.get("other_charges") or [],
        invoice_number=str(invoice.get("invoice_number") or ""),
    )
    if len(built) < 2:
        raise HTTPException(400, "Baris jurnal posting tidak valid.")
    lines = []
    for row in built:
        lines.append({
            "account_id": str(row["account_id"]),
            "debit": str(_money(row.get("debit") or 0, "debit")),
            "credit": str(_money(row.get("credit") or 0, "credit")),
            "description": str(row.get("description") or ""),
        })
    return {
        "date": journal_date_from_invoice(invoice),
        "source_type": SOURCE_TYPE,
        "receivable_account_id": mapped["receivable_account_id"],
        "revenue_account_id": mapped["revenue_account_id"],
        "output_tax_account_id": mapped["output_tax_account_id"],
        "lines": lines,
    }


def _plan_post_lines(plan: dict) -> List[dict]:
    out = []
    for row in plan.get("lines") or []:
        out.append({
            "account_id": row.get("account_id"),
            "debit": Decimal(str(row.get("debit") or "0")),
            "credit": Decimal(str(row.get("credit") or "0")),
            "description": row.get("description") or "",
        })
    return out


def _actor(user: Optional[dict]) -> dict:
    user = user or {}
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "email": str(user.get("email") or ""),
        "name": str(user.get("name") or user.get("email") or ""),
        "role": str(user.get("role") or ""),
    }


def _snapshot_summary(invoice: dict) -> dict:
    return {
        "invoice_id": invoice.get("id"),
        "invoice_number": invoice.get("invoice_number"),
        "po_id": invoice.get("po_id"),
        "po_number": invoice.get("po_number"),
        "customer": invoice.get("customer"),
        "invoice_date": invoice.get("invoice_date"),
        "grand_total": invoice.get("grand_total"),
        "after_discount": invoice.get("after_discount"),
        "ppn_amount": invoice.get("ppn_amount"),
        "total_qty": invoice.get("total_qty"),
        "financial_status": effective_financial_status(invoice),
        "issue_state": invoice.get("issue_state"),
    }


async def publish_invoice_audit(
    db,
    *,
    action: str,
    operation_key: str,
    invoice: dict,
    user: dict,
    before_status: str,
    after_status: str,
    journal_id: Optional[str] = None,
    reversal_journal_id: Optional[str] = None,
    reason: str = "",
    recovery: Optional[dict] = None,
) -> dict:
    """Idempotent audit row. The snapshot is stored on the event itself."""
    key = str(operation_key or "").strip()
    if not key:
        raise HTTPException(500, "Kunci audit faktur tidak ada.")
    existing = await db.audit_logs.find_one({"operation_key": key}, {"_id": 0})
    if existing:
        return existing
    doc = {
        "id": str(uuid.uuid4()),
        "action": action,
        "operation_key": key,
        "actor_email": (user or {}).get("email"),
        "actor_role": (user or {}).get("role"),
        "actor_name": (user or {}).get("name"),
        "actor": _actor(user),
        "target_email": f"invoice:{invoice.get('invoice_number') or invoice.get('id')}",
        "at": now_iso(),
        "note": reason or "",
        "before": {"status": before_status, **_snapshot_summary(invoice)},
        "after": {
            "status": after_status,
            "invoice_id": invoice.get("id"),
            "invoice_number": invoice.get("invoice_number"),
            "po_id": invoice.get("po_id"),
            "po_number": invoice.get("po_number"),
            "grand_total": invoice.get("grand_total"),
            "journal_id": journal_id,
            "reversal_journal_id": reversal_journal_id,
            "reason": reason or "",
            "recovery": recovery or None,
        },
    }
    try:
        await db.audit_logs.insert_one(dict(doc))
    except DuplicateKeyError:
        found = await db.audit_logs.find_one({"operation_key": key}, {"_id": 0})
        return found or doc
    doc.pop("_id", None)
    return doc


def _fence_is_fresh(invoice: dict, *, now: Optional[str] = None) -> bool:
    token = str((invoice or {}).get("issue_posting_fence") or "").strip()
    raw = str((invoice or {}).get("issue_posting_fence_at") or "").strip()
    if not token or not raw:
        return False
    from datetime import datetime, timedelta, timezone
    text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        claimed = datetime.fromisoformat(text)
    except ValueError:
        return False
    if claimed.tzinfo is None:
        claimed = claimed.replace(tzinfo=timezone.utc)
    current = datetime.now(timezone.utc)
    return (current - claimed).total_seconds() < FENCE_TTL_SECONDS


async def _run_hook(hooks: Optional[dict], name: str, invoice: dict) -> None:
    fn = (hooks or {}).get(name)
    if fn:
        result = fn(invoice)
        if hasattr(result, "__await__"):
            await result


async def _mark_failure(db, invoice_id: str, operation_id: str, message: str) -> None:
    await db.sales_invoices.update_one(
        {"id": str(invoice_id)},
        {"$set": {"issue_failure": {"at": now_iso(), "operation_id": operation_id, "message": message}}},
    )


async def _ensure_planned_journal(db, invoice: dict, user: dict) -> dict:
    """Write or finish the planned journal. A mismatch is not success and is not deleted."""
    plan = invoice.get("issue_journal_plan") or {}
    if not plan.get("lines"):
        raise HTTPException(409, "Rencana jurnal penerbitan tidak tersimpan. Faktur tidak diposting dari mapping baru.")
    lines = _plan_post_lines(plan)
    journal = await find_sales_invoice_source_journal(db, invoice["id"])
    if _journal_matches(journal, invoice, plan, lines):
        return journal
    if journal is None:
        await _require_open_period(db, plan.get("date"))
        try:
            await post_balanced_journal(
                db,
                date=str(plan.get("date")),
                source_type=SOURCE_TYPE,
                source_id=str(invoice["id"]),
                description=f"Faktur Penjualan {invoice.get('invoice_number') or invoice['id']}",
                lines=lines,
                posted_by=user,
            )
        except PeriodLockedError as exc:
            raise HTTPException(409, str(exc)) from exc
        except JournalValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except JournalConflictError:
            pass
        journal = await find_sales_invoice_source_journal(db, invoice["id"])
    if _journal_matches(journal, invoice, plan, lines):
        return journal
    if not journal:
        raise HTTPException(409, "Jurnal faktur belum lengkap sesuai rencana penerbitan.")
    try:
        from services.journal_core import _missing_plan_slots
        missing = _missing_plan_slots(journal.get("lines") or [], lines)
    except JournalConflictError as exc:
        raise HTTPException(409, "Jurnal faktur tidak sesuai rencana penerbitan dan tidak ditandai berhasil.") from exc
    if not missing:
        raise HTTPException(409, "Jurnal faktur tidak sesuai rencana penerbitan dan tidak ditandai berhasil.")
    await _require_open_period(db, plan.get("date") or journal.get("date"))
    try:
        journal = await complete_primary_source_lines(db, SOURCE_TYPE, str(invoice["id"]), lines)
    except PeriodLockedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except JournalConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except JournalValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    journal = await find_sales_invoice_source_journal(db, invoice["id"])
    if not _journal_matches(journal, invoice, plan, lines):
        raise HTTPException(409, "Jurnal faktur belum lengkap sesuai rencana penerbitan.")
    return journal


def _journal_matches(journal: Optional[dict], invoice: dict, plan: dict, lines: List[dict]) -> bool:
    if not journal:
        return False
    if str(journal.get("source_id") or "") != str(invoice.get("id") or ""):
        return False
    if str(journal.get("date") or "")[:10] != str(plan.get("date") or "")[:10]:
        return False
    if str(journal.get("status") or "") not in ("posted", ""):
        return False
    return primary_lines_match_plan(journal.get("lines") or [], lines)


async def issue_sales_invoice(
    db,
    payload: Dict[str, Any],
    user: dict,
    *,
    serialize_in_process: bool = True,
    hooks: Optional[dict] = None,
) -> Dict[str, Any]:
    """Create or resume one issued invoice. Posting is an internal effect."""
    from services.sales_invoices import create_sales_invoice

    await ensure_indexes(db)
    body = dict(payload or {})
    operation_id = str(body.get("idempotency_key") or "").strip() or str(uuid.uuid4())
    body["idempotency_key"] = operation_id
    payload_hash = canonical_payload_hash(body)
    invoice_date = str(body.get("invoice_date") or now_iso()[:10]).strip()[:10]
    await _require_open_period(db, invoice_date)
    mapped = await _active_posting_accounts(db)

    def _freeze(doc: dict) -> dict:
        plan = build_issue_plan(doc, mapped)
        return {
            "issue_state": ISSUE_STATE_ISSUING,
            "issue_payload_hash": payload_hash,
            "issue_journal_plan": plan,
            "issue_operation_key": operation_id,
            "issue_claimed_at": now_iso(),
        }

    invoice = await create_sales_invoice(
        db,
        body,
        user,
        payload_hash=payload_hash,
        on_document=_freeze,
        serialize_in_process=serialize_in_process,
    )
    return await continue_sales_invoice_issue(
        db,
        invoice,
        user,
        operation_id=operation_id,
        hooks=hooks,
    )


async def continue_sales_invoice_issue(
    db,
    invoice: dict,
    user: dict,
    *,
    operation_id: str,
    hooks: Optional[dict] = None,
) -> Dict[str, Any]:
    current = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or invoice
    if current.get("removed_from_active"):
        raise HTTPException(409, "Faktur ini sudah dihapus.")
    if current.get("issue_payload_hash") and current.get("issue_operation_key") not in (None, "", operation_id):
        if str(current.get("idempotency_key") or "") != operation_id:
            raise _conflict("Faktur ini milik operasi penerbitan lain.", operation_id)
    plan = current.get("issue_journal_plan")
    if not plan:
        raise HTTPException(409, "Rencana jurnal penerbitan tidak tersimpan.")
    await _run_hook(hooks, "after_insert", current)

    journal = await find_sales_invoice_source_journal(db, current["id"])
    lines = _plan_post_lines(plan)
    journal_ready = bool(journal) and primary_lines_match_plan(journal.get("lines") or [], lines)
    if current.get("issue_state") == ISSUE_STATE_ISSUED and journal_ready:
        audit = await publish_invoice_audit(
            db,
            action=AUDIT_ACTION_ISSUE,
            operation_key=f"invoice-issue:{current['id']}",
            invoice=current,
            user=user,
            before_status=str(current.get("issue_failure", {}).get("message") or FINANCIAL_DRAFT),
            after_status=ISSUE_STATE_ISSUED,
            journal_id=journal.get("id") if journal else current.get("accounting_journal_id"),
            recovery=current.get("issue_failure"),
        )
        detail = await get_sales_invoice_detail(db, current["id"])
        return {"invoice": detail, "operation_id": operation_id, "audit_id": audit.get("id"), "issued": True}

    if effective_financial_status(current) == FINANCIAL_DRAFT:
        current = await finalize_sales_invoice(db, current["id"], user)
        current = await db.sales_invoices.find_one({"id": current["id"]}, {"_id": 0}) or current
    await _run_hook(hooks, "after_finalize", current)
    if current.get("removed_from_active") or current.get("removal_state") == "pending":
        raise _continuable(
            "Penghapusan faktur sedang berjalan. Penerbitan tidak diselesaikan.",
            operation_id,
            code="delete_in_progress",
        )

    if not journal_ready:
        token = str(uuid.uuid4())
        fence_cutoff = _fence_cutoff()
        claimed = await db.sales_invoices.find_one_and_update(
            {
                "id": current["id"],
                "issue_state": ISSUE_STATE_ISSUING,
                "removed_from_active": {"$ne": True},
                "removal_state": {"$nin": ["pending", "deleted"]},
                "$or": [
                    {"issue_posting_fence": {"$exists": False}},
                    {"issue_posting_fence": None},
                    {"issue_posting_fence": ""},
                    {"issue_posting_fence_at": {"$lte": fence_cutoff}},
                ],
            },
            {"$set": {"issue_posting_fence": token, "issue_posting_fence_at": now_iso()}},
            return_document=ReturnDocument.AFTER,
        )
        if claimed is None:
            latest = await db.sales_invoices.find_one({"id": current["id"]}, {"_id": 0}) or current
            journal = await find_sales_invoice_source_journal(db, current["id"])
            if (
                latest.get("issue_state") == ISSUE_STATE_ISSUED
                and journal
                and primary_lines_match_plan(journal.get("lines") or [], lines)
            ):
                claimed = latest
                journal_ready = True
            elif _fence_is_fresh(latest) or latest.get("removal_state") == "pending":
                await _mark_failure(db, current["id"], operation_id, "Penerbitan atau penghapusan lain sedang berjalan.")
                raise _continuable(
                    "Penerbitan faktur belum selesai. Coba lagi dengan operasi yang sama.",
                    operation_id,
                    code="issue_busy",
                )
            else:
                raise _continuable(
                    "Penerbitan faktur belum selesai. Coba lagi dengan operasi yang sama.",
                    operation_id,
                    code="issue_busy",
                )
        else:
            await _run_hook(hooks, "after_fence_before_journal", claimed)
            latest = await db.sales_invoices.find_one({"id": current["id"]}, {"_id": 0}) or claimed
            if latest.get("removal_state") in ("pending", "deleted") or latest.get("removed_from_active"):
                await db.sales_invoices.update_one(
                    {"id": current["id"], "issue_posting_fence": token},
                    {"$unset": {"issue_posting_fence": "", "issue_posting_fence_at": ""}},
                )
                raise _continuable(
                    "Penghapusan faktur sedang berjalan. Jurnal tidak ditulis.",
                    operation_id,
                    code="delete_in_progress",
                )
            try:
                journal = await _ensure_planned_journal(db, latest, user)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, str) else (exc.detail or {}).get("message", "")
                await _mark_failure(db, current["id"], operation_id, str(detail))
                await db.sales_invoices.update_one(
                    {"id": current["id"], "issue_posting_fence": token},
                    {"$unset": {"issue_posting_fence": "", "issue_posting_fence_at": ""}},
                )
                raise
            journal_ready = True
            finished = await db.sales_invoices.update_one(
                {
                    "id": current["id"],
                    "issue_posting_fence": token,
                    "removed_from_active": {"$ne": True},
                    "removal_state": {"$nin": ["pending", "deleted"]},
                },
                {"$set": {
                    "issue_state": ISSUE_STATE_ISSUED,
                    "issue_completed_at": now_iso(),
                    "updated_at": now_iso(),
                }, "$unset": {"issue_posting_fence": "", "issue_posting_fence_at": "", "issue_failure": ""}},
            )
            if int(getattr(finished, "modified_count", 0) or 0) != 1:
                latest = await db.sales_invoices.find_one({"id": current["id"]}, {"_id": 0}) or {}
                if latest.get("issue_state") != ISSUE_STATE_ISSUED:
                    await _mark_failure(db, current["id"], operation_id, "Penerbitan belum ditandai selesai.")
                    raise _continuable(
                        "Jurnal sudah lengkap tetapi penerbitan belum selesai. Coba lagi dengan operasi yang sama.",
                        operation_id,
                        code="issue_incomplete",
                    )

    if journal_ready and (await db.sales_invoices.find_one({"id": current["id"]}, {"issue_state": 1}) or {}).get("issue_state") != ISSUE_STATE_ISSUED:
        await db.sales_invoices.update_one(
            {
                "id": current["id"],
                "removed_from_active": {"$ne": True},
                "removal_state": {"$nin": ["pending", "deleted"]},
            },
            {"$set": {"issue_state": ISSUE_STATE_ISSUED, "issue_completed_at": now_iso(), "updated_at": now_iso()}},
        )
    stored = await db.sales_invoices.find_one({"id": current["id"]}, {"_id": 0}) or current
    if stored.get("issue_state") != ISSUE_STATE_ISSUED:
        raise _continuable(
            "Penerbitan faktur belum selesai. Coba lagi dengan operasi yang sama.",
            operation_id,
            code="issue_incomplete",
        )
    journal = await find_sales_invoice_source_journal(db, stored["id"])
    if not journal or accounting_status_from_journal(journal) != ACCOUNTING_POSTED:
        raise HTTPException(409, "Faktur tidak ditandai terbit karena jurnal belum posted.")
    if not primary_lines_match_plan(journal.get("lines") or [], lines):
        raise HTTPException(409, "Jurnal faktur tidak sesuai rencana penerbitan dan tidak ditandai berhasil.")
    audit = await publish_invoice_audit(
        db,
        action=AUDIT_ACTION_ISSUE,
        operation_key=f"invoice-issue:{stored['id']}",
        invoice=stored,
        user=user,
        before_status=FINANCIAL_DRAFT,
        after_status=ISSUE_STATE_ISSUED,
        journal_id=journal.get("id"),
        recovery=stored.get("issue_failure"),
    )
    detail = await get_sales_invoice_detail(db, stored["id"])
    return {"invoice": detail, "operation_id": operation_id, "audit_id": audit.get("id"), "issued": True}


def _fence_cutoff() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(seconds=FENCE_TTL_SECONDS)).isoformat()


async def _active_credit_blockers(db, invoice_id: str) -> List[dict]:
    from services.sales_customer_credit import COLLECTION

    docs = await db[COLLECTION].find(
        {"invoice_id": str(invoice_id), "financial_status": "active", "status": "posted"},
        {"_id": 0, "id": 1, "amount": 1},
    ).to_list(50)
    return [
        {"id": doc.get("id"), "number": doc.get("id"), "amount": doc.get("amount"), "kind": "customer_credit"}
        for doc in docs
    ]


async def _reject_delete_blockers(db, invoice: dict) -> None:
    from services.sales_invoice_posting import collect_invoice_reverse_receipt_blockers, raise_active_receipt_blockers

    blockers = await collect_invoice_reverse_receipt_blockers(db, invoice["id"])
    credits = await _active_credit_blockers(db, invoice["id"])
    if blockers or credits:
        raise HTTPException(409, {
            "message": "Faktur tidak dapat dihapus karena masih memiliki penerimaan atau pemakaian saldo aktif.",
            "blockers": blockers + credits,
        })
    if receipt_create_claim_is_active(invoice):
        raise HTTPException(409, "Faktur sedang menerima Penerimaan Penjualan dan tidak dapat dihapus.")
    if posting_claim_is_active(invoice) or reverse_claim_is_active(invoice):
        raise HTTPException(409, "Faktur sedang diproses accounting dan tidak dapat dihapus saat ini.")


async def remove_issued_sales_invoice(
    db,
    invoice_id: str,
    user: dict,
    *,
    operation_key: Optional[str] = None,
    hooks: Optional[dict] = None,
) -> Dict[str, Any]:
    """Hide one issued invoice. Reverse a complete journal exactly once."""
    await ensure_indexes(db)
    invoice = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not invoice:
        invoice = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not invoice:
        raise HTTPException(404, "Faktur Penjualan tidak ditemukan.")
    operation_id = str(operation_key or invoice.get("delete_operation_key") or "").strip() or str(uuid.uuid4())
    if invoice.get("removed_from_active"):
        audit = await publish_invoice_audit(
            db,
            action=AUDIT_ACTION_DELETE,
            operation_key=f"invoice-delete:{invoice['id']}",
            invoice=invoice,
            user=user,
            before_status=str(invoice.get("financial_status") or ""),
            after_status="deleted",
            journal_id=invoice.get("accounting_journal_id"),
            reversal_journal_id=invoice.get("accounting_reversal_journal_id"),
            reason=DELETE_REASON,
            recovery=invoice.get("delete_failure"),
        )
        return {
            "deleted": True,
            "id": invoice["id"],
            "invoice_number": invoice.get("invoice_number"),
            "operation_id": invoice.get("delete_operation_key") or operation_id,
            "audit_id": audit.get("id"),
        }

    await _reject_delete_blockers(db, invoice)
    await _run_hook(hooks, "after_blocker_check", invoice)
    fresh = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or invoice
    await _reject_delete_blockers(db, fresh)
    if _fence_is_fresh(fresh) and fresh.get("issue_state") == ISSUE_STATE_ISSUING:
        journal = await find_sales_invoice_source_journal(db, fresh["id"])
        plan_lines = _plan_post_lines(fresh.get("issue_journal_plan") or {"lines": []})
        complete = bool(journal) and primary_lines_match_plan(journal.get("lines") or [], plan_lines) if plan_lines else False
        if not complete:
            raise _continuable(
                "Penerbitan faktur masih berjalan. Hapus dapat dilanjutkan setelah penerbitan selesai atau dibatalkan dengan aman.",
                operation_id,
                code="issue_in_progress",
            )

    claimed = await db.sales_invoices.find_one_and_update(
        {
            "id": invoice["id"],
            "removed_from_active": {"$ne": True},
            "$and": [
                {"$or": [
                    {"removal_state": {"$exists": False}},
                    {"removal_state": None},
                    {"removal_state": ""},
                    {"delete_operation_key": operation_id},
                ]},
                receipt_create_claim_absent_or_stale_clause(),
            ],
        },
        {"$set": {
            "removal_state": "pending",
            "delete_operation_key": operation_id,
            "updated_at": now_iso(),
        }},
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        latest = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or invoice
        if latest.get("removed_from_active"):
            return await remove_issued_sales_invoice(
                db, latest["id"], user, operation_key=latest.get("delete_operation_key") or operation_id
            )
        raise _continuable(
            "Penghapusan faktur belum selesai. Coba lagi.",
            latest.get("delete_operation_key") or operation_id,
            code="delete_busy",
        )

    journal = await find_sales_invoice_source_journal(db, invoice["id"])
    plan = claimed.get("issue_journal_plan") or {}
    plan_lines = _plan_post_lines(plan) if plan.get("lines") else []
    status = accounting_status_from_journal(journal)
    reversal_id = claimed.get("accounting_reversal_journal_id")

    try:
        fresh_for_block = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or claimed
        await _reject_delete_blockers(db, fresh_for_block)
    except HTTPException:
        await _clear_own_removal(db, invoice["id"], operation_id)
        raise

    try:
        if status == ACCOUNTING_REVERSED or (journal and str(journal.get("status") or "") == "reversed"):
            reversal = await find_sales_invoice_reversal_journal(db, journal.get("id") if journal else "")
            reversal_id = (reversal or {}).get("id") or reversal_id
        elif journal and plan_lines and primary_lines_match_plan(journal.get("lines") or [], plan_lines):
            await _run_hook(hooks, "before_reverse", claimed)
            reversed_doc = await reverse_sales_invoice_accounting(
                db,
                invoice["id"],
                user,
                DELETE_REASON,
                serialize_in_process=False,
            )
            reversal_id = reversed_doc.get("accounting_reversal_journal_id") or reversal_id
            journal = await find_sales_invoice_source_journal(db, invoice["id"])
            if accounting_status_from_journal(journal) != ACCOUNTING_REVERSED:
                raise HTTPException(409, "Pembalikan jurnal belum selesai.")
        elif journal and not (journal.get("lines") or []):
            if not plan_lines:
                raise HTTPException(409, "Jurnal faktur belum lengkap dan tidak ada rencana pemulihan.")
            try:
                await _require_open_period(db, (journal.get("date") or plan.get("date") or "")[:10])
                await complete_primary_source_lines(db, SOURCE_TYPE, invoice["id"], plan_lines)
            except PeriodLockedError as exc:
                await _clear_own_removal(db, invoice["id"], operation_id)
                raise HTTPException(409, str(exc)) from exc
            journal = await find_sales_invoice_source_journal(db, invoice["id"])
            if not journal or not primary_lines_match_plan(journal.get("lines") or [], plan_lines):
                await _clear_own_removal(db, invoice["id"], operation_id)
                raise _continuable(
                    "Jurnal faktur belum lengkap. Hapus dapat dilanjutkan setelah penerbitan pulih.",
                    operation_id,
                    code="journal_incomplete",
                )
            reversed_doc = await reverse_sales_invoice_accounting(
                db, invoice["id"], user, DELETE_REASON, serialize_in_process=False
            )
            reversal_id = reversed_doc.get("accounting_reversal_journal_id")
        elif journal and plan_lines and not primary_lines_match_plan(journal.get("lines") or [], plan_lines):
            try:
                from services.journal_core import _missing_plan_slots
                missing = _missing_plan_slots(journal.get("lines") or [], plan_lines)
            except JournalConflictError as exc:
                await _clear_own_removal(db, invoice["id"], operation_id)
                raise HTTPException(409, "Jurnal faktur tidak lengkap atau tidak sesuai rencana, jadi belum dihapus.") from exc
            if missing:
                try:
                    await _require_open_period(db, (journal.get("date") or plan.get("date") or "")[:10])
                    await complete_primary_source_lines(db, SOURCE_TYPE, invoice["id"], plan_lines)
                except PeriodLockedError as exc:
                    await _clear_own_removal(db, invoice["id"], operation_id)
                    raise HTTPException(409, str(exc)) from exc
                journal = await find_sales_invoice_source_journal(db, invoice["id"])
            if not journal or not primary_lines_match_plan(journal.get("lines") or [], plan_lines):
                await _clear_own_removal(db, invoice["id"], operation_id)
                raise HTTPException(409, "Jurnal faktur tidak sesuai rencana dan belum dihapus.")
            reversed_doc = await reverse_sales_invoice_accounting(
                db, invoice["id"], user, DELETE_REASON, serialize_in_process=False
            )
            reversal_id = reversed_doc.get("accounting_reversal_journal_id")
        elif journal:
            await _clear_own_removal(db, invoice["id"], operation_id)
            raise HTTPException(409, "Jurnal faktur belum lengkap dan tidak dapat dihapus dengan aman.")
    except HTTPException as exc:
        detail = exc.detail
        message = detail if isinstance(detail, str) else (detail or {}).get("message", "")
        stored_now = await db.sales_invoices.find_one(
            {"id": invoice["id"]}, {"_id": 0, "removed_from_active": 1}
        )
        journal_now = await find_sales_invoice_source_journal(db, invoice["id"])
        already_reversed = accounting_status_from_journal(journal_now) == ACCOUNTING_REVERSED
        if not (stored_now or {}).get("removed_from_active") and not already_reversed:
            if (isinstance(detail, dict) and detail.get("blockers")) or (
                isinstance(detail, str) and "Penerimaan" in detail
            ):
                await _clear_own_removal(db, invoice["id"], operation_id)
        await db.sales_invoices.update_one(
            {"id": invoice["id"], "removed_from_active": {"$ne": True}},
            {"$set": {"delete_failure": {"at": now_iso(), "operation_id": operation_id, "message": str(message)}}},
        )
        raise

    await release_invoice_qty(db, claimed.get("qty_hold_id"))
    now = now_iso()
    before_status = effective_financial_status(claimed)
    await db.sales_invoices.update_one(
        {"id": invoice["id"], "delete_operation_key": operation_id},
        {"$set": {
            "removed_from_active": True,
            "removal_state": "deleted",
            "removed_at": now,
            "removed_by": _actor(user),
            "delete_reason": DELETE_REASON,
            "financial_status": FINANCIAL_CANCELLED if before_status != FINANCIAL_DRAFT else FINANCIAL_DRAFT,
            "updated_at": now,
        }},
    )
    stored = await db.sales_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or claimed
    source = await find_sales_invoice_source_journal(db, invoice["id"])
    audit = await publish_invoice_audit(
        db,
        action=AUDIT_ACTION_DELETE,
        operation_key=f"invoice-delete:{invoice['id']}",
        invoice=stored,
        user=user,
        before_status=before_status,
        after_status="deleted",
        journal_id=(source or {}).get("id"),
        reversal_journal_id=stored.get("accounting_reversal_journal_id") or reversal_id,
        reason=DELETE_REASON,
        recovery=stored.get("delete_failure"),
    )
    return {
        "deleted": True,
        "id": invoice["id"],
        "invoice_number": stored.get("invoice_number"),
        "operation_id": operation_id,
        "audit_id": audit.get("id"),
        "journal_id": (source or {}).get("id"),
        "reversal_journal_id": stored.get("accounting_reversal_journal_id") or reversal_id,
    }


async def _clear_own_removal(db, invoice_id: str, operation_id: str) -> None:
    await db.sales_invoices.update_one(
        {
            "id": str(invoice_id),
            "delete_operation_key": operation_id,
            "removed_from_active": {"$ne": True},
            "removal_state": "pending",
        },
        {"$unset": {"removal_state": "", "delete_operation_key": ""}},
    )
