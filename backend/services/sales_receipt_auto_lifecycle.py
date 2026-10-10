"""Phase 2.2B — automatic Sales Receipt create → post → settlement-effective.

Flags (both default OFF):
  - receipt_create_requires_invoice_journal_posted
  - auto_sales_receipt_lifecycle_enabled

When auto lifecycle is ON, invoice posted journal is mandatory even if gate A is OFF.
Does NOT create production indexes. Does NOT process historical receipts.
Does NOT grant general journal posting permission.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo import ReturnDocument

GATE_FLAG_KEY = "receipt_create_requires_invoice_journal_posted"
AUTO_FLAG_KEY = "auto_sales_receipt_lifecycle_enabled"

AUTO_STATUS_PENDING = "pending"
AUTO_STATUS_POSTING = "posting"
AUTO_STATUS_POSTED = "posted"
AUTO_STATUS_FAILED = "failed"
AUTO_STATUS_RETRY_PENDING = "retry_pending"

AUTO_STATUS_FIELD = "auto_accounting_status"
AUTO_ATTEMPT_FIELD = "auto_accounting_attempt_id"
AUTO_ERROR_FIELD = "auto_accounting_last_error_code"
AUTO_ELIGIBLE_FIELD = "auto_lifecycle_eligible"
AUTO_FINGERPRINT_FIELD = "auto_lifecycle_fingerprint"
AUTO_CLAIM_FIELD = "auto_accounting_claim_token"
AUTO_CLAIM_AT_FIELD = "auto_accounting_claim_at"

INTENT_COLL = "sales_receipt_auto_create_intents"
INTENT_TTL_HOURS = 72
AUTO_CLAIM_TTL_SECONDS = 120

ERR_FLAG_OFF = "auto_lifecycle_flag_off"
ERR_MISSING_IDEMPOTENCY_KEY = "idempotency_key_required"
ERR_INDEX_PREREQUISITE = "receipt_idempotency_index_prerequisite"
ERR_INTENT_INDEX_PREREQUISITE = "intent_idempotency_index_prerequisite"
ERR_PAYLOAD_CONFLICT = "idempotency_payload_conflict"
ERR_INTENT_BIND_CONFLICT = "intent_bind_conflict"
ERR_INTENT_EXPIRED = "intent_expired_manual_recovery_required"
ERR_UNAUTHORIZED_RETRY = "unauthorized_idempotent_retry"
ERR_NOT_ELIGIBLE = "not_auto_lifecycle_eligible"
ERR_VALIDATION = "validation_rejected"
ERR_PERIOD_LOCKED = "period_locked"
ERR_POSTING = "posting_failed"
ERR_SETTLEMENT = "settlement_incomplete"
ERR_SETTLEMENT_RECONCILIATION = "settlement_reconciliation_pending"
ERR_CLAIM_HELD = "posting_claim_held"
ERR_CANCELLED = "receipt_cancelled"
ERR_INVOICE_NOT_POSTED = "invoice_journal_not_posted"
ERR_CASH_BANK = "cash_bank_required"
ERR_GATE_UNAVAILABLE = "receipt_invoice_gate_unavailable"
AUTO_LIFECYCLE_ACTIVATION_BLOCKED = "AUTO_LIFECYCLE_ACTIVATION_BLOCKED"

PROPOSED_IDEMPOTENCY_INDEX_KEY = "idempotency_key"
PROPOSED_IDEMPOTENCY_PARTIAL = {"idempotency_key": {"$type": "string"}}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _actor_id(user: Optional[dict]) -> str:
    user = user or {}
    return str(user.get("id") or user.get("_id") or "").strip()


def _norm_scalar(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, (int, float)):
        try:
            from decimal import Decimal

            return str(Decimal(str(raw)))
        except Exception:
            return str(raw)
    return str(raw).strip()


def _norm_money(raw: Any) -> str:
    text = _norm_scalar(raw)
    return "0" if text == "" else text


async def _flag_enabled(db, key: str) -> bool:
    try:
        doc = await db.app_settings.find_one({"key": key}, {"_id": 0, "enabled": 1, "value": 1})
    except Exception:
        return False
    if not doc:
        return False
    if "enabled" in doc:
        return doc.get("enabled") is True
    return doc.get("value") is True


async def is_receipt_create_requires_invoice_journal_posted(db) -> bool:
    return await _flag_enabled(db, GATE_FLAG_KEY)


async def is_auto_sales_receipt_lifecycle_enabled(db) -> bool:
    return await _flag_enabled(db, AUTO_FLAG_KEY)


def _index_key_fields(meta: dict) -> List[str]:
    keys = (meta or {}).get("key")
    if keys is None:
        return []
    out: List[str] = []
    if isinstance(keys, dict):
        return [str(k) for k in keys.keys()]
    try:
        items = list(keys)
    except TypeError:
        return []
    for item in items:
        if isinstance(item, (list, tuple)) and item:
            out.append(str(item[0]))
        elif isinstance(item, dict):
            out.extend(str(k) for k in item.keys())
        else:
            return []
    return out


def _partial_idempotency_string_type(partial: Any) -> bool:
    if not isinstance(partial, dict):
        return False
    if partial == PROPOSED_IDEMPOTENCY_PARTIAL:
        return True
    typed = partial.get("idempotency_key")
    return isinstance(typed, dict) and typed.get("$type") == "string"


async def _list_index_specs(collection) -> Optional[List[dict]]:
    try:
        specs = await collection.list_indexes().to_list(length=200)
    except Exception:
        return None
    if not isinstance(specs, list):
        return None
    return [s for s in specs if isinstance(s, dict)]


async def sales_receipt_idempotency_index_ready(db) -> bool:
    specs = await _list_index_specs(db.sales_receipts)
    if specs is None:
        return False
    for meta in specs:
        if meta.get("unique") is not True:
            continue
        if _index_key_fields(meta) != [PROPOSED_IDEMPOTENCY_INDEX_KEY]:
            continue
        if _partial_idempotency_string_type(meta.get("partialFilterExpression")):
            return True
    return False


async def intent_idempotency_index_ready(db) -> bool:
    specs = await _list_index_specs(db[INTENT_COLL])
    if specs is None:
        return False
    for meta in specs:
        if meta.get("unique") is not True:
            continue
        if _index_key_fields(meta) == ["idempotency_key"]:
            return True
    return False


async def ensure_auto_receipt_intent_indexes(db) -> None:
    """Test/local only. Never called from production startup by this module."""
    from pymongo.errors import OperationFailure

    coll = db[INTENT_COLL]
    if not await intent_idempotency_index_ready(db):
        try:
            await coll.create_index(
                "idempotency_key", unique=True, name="auto_receipt_intent_idempotency_unique"
            )
        except OperationFailure:
            if not await intent_idempotency_index_ready(db):
                raise
    try:
        await coll.create_index("receipt_id", sparse=True, name="auto_receipt_intent_receipt_id")
    except OperationFailure:
        pass


async def auto_receipt_lifecycle_activation_blockers(db) -> List[str]:
    blockers: List[str] = []
    try:
        if not await is_auto_sales_receipt_lifecycle_enabled(db):
            blockers.append(ERR_FLAG_OFF)
        invoice_ok = await sales_receipt_idempotency_index_ready(db)
        intent_ok = await intent_idempotency_index_ready(db)
        if not invoice_ok:
            blockers.append(ERR_INDEX_PREREQUISITE)
        if not intent_ok:
            blockers.append(ERR_INTENT_INDEX_PREREQUISITE)
        if not invoice_ok or not intent_ok:
            blockers.append(AUTO_LIFECYCLE_ACTIVATION_BLOCKED)
    except Exception:
        blockers.append(AUTO_LIFECYCLE_ACTIVATION_BLOCKED)
    return blockers


async def _require_activation_indexes(db) -> Optional[Dict[str, Any]]:
    if await sales_receipt_idempotency_index_ready(db) and await intent_idempotency_index_ready(db):
        return None
    return _conflict_result(
        {},
        code=AUTO_LIFECYCLE_ACTIVATION_BLOCKED,
        message="Lifecycle otomatis penerimaan diblokir: indeks idempotensi wajib belum siap.",
    )


def canonical_receipt_create_fingerprint(payload: Dict[str, Any]) -> str:
    body = {
        "invoice_id": _norm_scalar(payload.get("invoice_id")),
        "amount": _norm_money(payload.get("amount")),
        "allocated_amount": _norm_money(payload.get("allocated_amount"))
        if "allocated_amount" in payload and payload.get("allocated_amount") is not None
        else "",
        "cash_bank_id": _norm_scalar(payload.get("cash_bank_id")),
        "receipt_date": _norm_scalar(payload.get("receipt_date"))[:10],
        "notes": _norm_scalar(payload.get("notes")),
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sanitize_error_code(exc: BaseException) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("error_code"):
            return str(detail.get("error_code"))
        text = detail if isinstance(detail, str) else str(detail or "")
        lower = text.lower()
        if exc.status_code == 409 and ("periode" in lower or "period" in lower or "dikunci" in lower):
            return ERR_PERIOD_LOCKED
        if "dibatalkan" in lower or "cancelled" in lower:
            return ERR_CANCELLED
        if "dependency gate" in lower or "tidak dapat diverifikasi" in lower or "tidak dapat dijalankan" in lower:
            return ERR_GATE_UNAVAILABLE
        if "faktur harus diposting" in lower or ("invoice" in lower and "post" in lower):
            return ERR_INVOICE_NOT_POSTED
        if "kas/bank" in lower:
            return ERR_CASH_BANK
        if exc.status_code in (400, 404, 422):
            return ERR_VALIDATION
        if exc.status_code == 409:
            return ERR_POSTING
        return ERR_POSTING
    from services.accounting_periods import PeriodLockedError

    if isinstance(exc, PeriodLockedError):
        return ERR_PERIOD_LOCKED
    return ERR_POSTING


def _result(
    receipt: dict,
    *,
    financial_complete: bool,
    status: str,
    error_code: Optional[str] = None,
    http_status: int = 200,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "receipt": receipt,
        "financial_complete": financial_complete,
        AUTO_STATUS_FIELD: status,
        "error_code": error_code,
        "http_status": 200 if financial_complete else (http_status if http_status != 200 else 409),
        "message": message
        or (
            "Penerimaan Penjualan berhasil dibuat, diposting, dan settlement efektif."
            if financial_complete
            else "Penerimaan tersimpan tetapi accounting/settlement belum selesai."
        ),
    }


def _conflict_result(receipt: Optional[dict], *, code: str, message: str, http_status: int = 409) -> Dict[str, Any]:
    return {
        "receipt": receipt or {},
        "financial_complete": False,
        AUTO_STATUS_FIELD: AUTO_STATUS_FAILED,
        "error_code": code,
        "http_status": http_status,
        "message": message,
    }


class _PayloadConflict(Exception):
    pass


class _IntentBindConflict(Exception):
    pass


class _UnauthorizedRetry(Exception):
    pass


def _intent_is_expired(intent: Optional[dict]) -> bool:
    if not intent:
        return False
    intent_at = _parse_iso(intent.get("created_at"))
    if not intent_at:
        return False
    return (datetime.now(timezone.utc) - intent_at) > timedelta(hours=INTENT_TTL_HOURS)


async def _get_intent(db, idempotency_key: str) -> Optional[dict]:
    return await db[INTENT_COLL].find_one({"idempotency_key": idempotency_key}, {"_id": 0})


async def _upsert_preparing_intent(
    db, *, idempotency_key: str, fingerprint: str, user: dict, invoice_id: str
) -> dict:
    existing = await _get_intent(db, idempotency_key)
    if existing:
        if str(existing.get("fingerprint") or "") != fingerprint:
            raise _PayloadConflict()
        return existing
    doc = {
        "idempotency_key": idempotency_key,
        "fingerprint": fingerprint,
        "actor_id": _actor_id(user),
        "actor_email": str((user or {}).get("email") or "").strip(),
        "invoice_id": str(invoice_id or "").strip(),
        "receipt_id": None,
        "status": "preparing",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    try:
        await db[INTENT_COLL].insert_one(doc)
        doc.pop("_id", None)
        return doc
    except Exception:
        again = await _get_intent(db, idempotency_key)
        if again and str(again.get("fingerprint") or "") == fingerprint:
            return again
        if again:
            raise _PayloadConflict()
        raise


async def _bind_intent_receipt(db, idempotency_key: str, receipt_id: str, fingerprint: str) -> dict:
    expected = str(receipt_id)
    now = now_iso()
    result = await db[INTENT_COLL].update_one(
        {
            "idempotency_key": idempotency_key,
            "fingerprint": fingerprint,
            "$or": [
                {"receipt_id": None},
                {"receipt_id": {"$exists": False}},
                {"receipt_id": ""},
            ],
        },
        {"$set": {"receipt_id": expected, "status": "bound", "updated_at": now}},
    )
    matched = int(getattr(result, "matched_count", 0) or 0)
    modified = int(getattr(result, "modified_count", 0) or 0)
    if matched == 1 and modified in (0, 1):
        current = await _get_intent(db, idempotency_key)
        if (
            current
            and str(current.get("fingerprint") or "") == fingerprint
            and str(current.get("receipt_id") or "").strip() == expected
        ):
            return current
        raise _IntentBindConflict()
    current = await _get_intent(db, idempotency_key)
    if not current or str(current.get("fingerprint") or "") != fingerprint:
        raise _IntentBindConflict()
    existing_id = str(current.get("receipt_id") or "").strip()
    if existing_id == expected:
        return current
    raise _IntentBindConflict()


def _receipt_actor_matches(receipt: dict, user: dict) -> bool:
    created = receipt.get("created_by") or {}
    return bool(str(created.get("id") or "").strip()) and str(created.get("id") or "").strip() == _actor_id(user)


async def _mark_auto_fields(
    db,
    receipt_id: str,
    *,
    status: str,
    attempt_id: Optional[str] = None,
    error_code: Optional[str] = None,
    fingerprint: Optional[str] = None,
    clear_claim: bool = False,
) -> None:
    now = now_iso()
    patch: Dict[str, Any] = {AUTO_STATUS_FIELD: status, "updated_at": now}
    if attempt_id is not None:
        patch[AUTO_ATTEMPT_FIELD] = attempt_id
    if error_code is not None:
        patch[AUTO_ERROR_FIELD] = error_code
    if fingerprint is not None:
        patch[AUTO_FINGERPRINT_FIELD] = fingerprint
    update: Dict[str, Any] = {"$set": patch}
    if clear_claim:
        update["$unset"] = {AUTO_CLAIM_FIELD: "", AUTO_CLAIM_AT_FIELD: ""}
    if status == AUTO_STATUS_POSTED:
        update.setdefault("$unset", {})[AUTO_ERROR_FIELD] = ""
    await db.sales_receipts.update_one({"id": str(receipt_id)}, update)


async def _load_public_receipt(db, receipt_id: str) -> dict:
    from services.sales_receipts import get_sales_receipt_detail

    return await get_sales_receipt_detail(db, receipt_id)


def _unauthorized_result() -> Dict[str, Any]:
    return _conflict_result(
        {},
        code=ERR_UNAUTHORIZED_RETRY,
        message="Permintaan idempoten tidak diizinkan.",
        http_status=403,
    )


def _expired_intent_result(receipt: Optional[dict] = None) -> Dict[str, Any]:
    safe = {"id": str(receipt.get("id"))} if receipt and receipt.get("id") else {}
    return _conflict_result(
        safe,
        code=ERR_INTENT_EXPIRED,
        message=(
            "Intent lifecycle kedaluwarsa. Pemulihan manual diperlukan; "
            "kunci idempotensi tidak dapat dipakai ulang untuk payload lain."
        ),
    )


async def _assert_retry_authorized(
    db, *, user: dict, receipt: dict, intent: Optional[dict], fingerprint: str, request_invoice_id: str
) -> None:
    if not _receipt_actor_matches(receipt, user):
        raise _UnauthorizedRetry()
    if intent is not None:
        if str(intent.get("actor_id") or "") != _actor_id(user):
            raise _UnauthorizedRetry()
        if str(intent.get("fingerprint") or "") != fingerprint:
            raise _PayloadConflict()
        intent_inv = str(intent.get("invoice_id") or "").strip()
        if intent_inv and request_invoice_id and intent_inv != request_invoice_id:
            raise _UnauthorizedRetry()
    stored_fp = str(receipt.get(AUTO_FINGERPRINT_FIELD) or "").strip()
    if stored_fp and stored_fp != fingerprint:
        raise _PayloadConflict()
    inv_id = str(receipt.get("invoice_id") or "").strip()
    if request_invoice_id and inv_id and request_invoice_id != inv_id:
        raise _UnauthorizedRetry()
    from services.sales_user_mapping import ensure_owned_po_id

    try:
        await ensure_owned_po_id(db, user, receipt.get("po_id"))
    except HTTPException:
        raise _UnauthorizedRetry() from None


async def _finalize_post_and_settle(
    db, receipt: dict, user: dict, *, attempt_id: str, fingerprint: str
) -> Dict[str, Any]:
    """Post journal via authoritative helper, then CAS settlement + projections.

    financial_complete=true only after verify_settlement_projections passes.
    Never reposts when journal already exists; heal projections only.
    """
    from services.sales_invoice_posting import ACCOUNTING_POSTED, accounting_status_from_journal
    from services.sales_receipt_posting import (
        SETTLEMENT_EFFECTIVE_FIELD,
        find_sales_receipt_source_journal,
        post_sales_receipt_accounting,
        receipt_is_cancelled,
    )
    from services.sales_receipts import (
        apply_operational_settlement_for_receipt,
        verify_settlement_projections,
    )

    live = await db.sales_receipts.find_one({"id": receipt["id"]}, {"_id": 0}) or receipt
    if receipt_is_cancelled(live):
        await _mark_auto_fields(
            db, live["id"], status=AUTO_STATUS_FAILED, attempt_id=attempt_id, error_code=ERR_CANCELLED
        )
        return _conflict_result(
            await _load_public_receipt(db, live["id"]),
            code=ERR_CANCELLED,
            message="Penerimaan dibatalkan; lifecycle otomatis dihentikan. Jurnal (jika ada) tidak di-reverse otomatis.",
        )

    await _mark_auto_fields(
        db,
        live["id"],
        status=AUTO_STATUS_POSTING,
        attempt_id=attempt_id,
        fingerprint=fingerprint,
    )

    existing = await find_sales_receipt_source_journal(db, live["id"])
    if accounting_status_from_journal(existing) != ACCOUNTING_POSTED:
        # Cancel check before attempting post.
        again = await db.sales_receipts.find_one({"id": live["id"]}, {"_id": 0}) or live
        if receipt_is_cancelled(again):
            await _mark_auto_fields(
                db, again["id"], status=AUTO_STATUS_FAILED, attempt_id=attempt_id, error_code=ERR_CANCELLED
            )
            return _conflict_result(
                await _load_public_receipt(db, again["id"]),
                code=ERR_CANCELLED,
                message="Penerimaan dibatalkan selama recovery; posting tidak dilanjutkan.",
            )
        try:
            await post_sales_receipt_accounting(
                db,
                live["id"],
                user,
                cash_bank_id=live.get("cash_bank_id") or receipt.get("cash_bank_id"),
            )
        except HTTPException as exc:
            code = sanitize_error_code(exc)
            await _mark_auto_fields(
                db,
                live["id"],
                status=AUTO_STATUS_FAILED,
                attempt_id=attempt_id,
                error_code=code,
                clear_claim=True,
            )
            detail = await _load_public_receipt(db, live["id"])
            msg = (
                str(exc.detail.get("message") or exc.detail)
                if isinstance(exc.detail, dict)
                else (str(exc.detail) if isinstance(exc.detail, str) else "Posting accounting gagal.")
            )
            return _result(
                detail,
                financial_complete=False,
                status=AUTO_STATUS_FAILED,
                error_code=code,
                message=msg,
            )

    journal = await find_sales_receipt_source_journal(db, live["id"])
    if accounting_status_from_journal(journal) != ACCOUNTING_POSTED:
        await _mark_auto_fields(
            db, live["id"], status=AUTO_STATUS_FAILED, attempt_id=attempt_id, error_code=ERR_POSTING
        )
        return _result(
            await _load_public_receipt(db, live["id"]),
            financial_complete=False,
            status=AUTO_STATUS_FAILED,
            error_code=ERR_POSTING,
        )

    try:
        settled = await apply_operational_settlement_for_receipt(db, live["id"], user)
    except HTTPException as exc:
        code = sanitize_error_code(exc)
        if code not in (ERR_CANCELLED, ERR_SETTLEMENT, ERR_SETTLEMENT_RECONCILIATION):
            code = ERR_SETTLEMENT
        await _mark_auto_fields(
            db, live["id"], status=AUTO_STATUS_FAILED, attempt_id=attempt_id, error_code=code
        )
        msg = (
            "Penerimaan dibatalkan selama recovery; settlement tidak diterapkan."
            if code == ERR_CANCELLED
            else "Jurnal diposting tetapi settlement operasional belum selesai."
        )
        return _result(
            await _load_public_receipt(db, live["id"]),
            financial_complete=False,
            status=AUTO_STATUS_FAILED,
            error_code=code,
            message=msg,
        )

    if settled.get(SETTLEMENT_EFFECTIVE_FIELD) is not True:
        await _mark_auto_fields(
            db, live["id"], status=AUTO_STATUS_FAILED, attempt_id=attempt_id, error_code=ERR_SETTLEMENT
        )
        return _result(
            await _load_public_receipt(db, live["id"]),
            financial_complete=False,
            status=AUTO_STATUS_FAILED,
            error_code=ERR_SETTLEMENT,
        )

    verified = await verify_settlement_projections(db, live["id"])
    if not verified.get("ok"):
        reason = str(verified.get("reason") or "")
        code = ERR_CANCELLED if reason == "cancelled" else ERR_SETTLEMENT_RECONCILIATION
        # Heal once more without reposting, then re-verify.
        if code != ERR_CANCELLED:
            try:
                await apply_operational_settlement_for_receipt(db, live["id"], user)
            except HTTPException:
                pass
            verified = await verify_settlement_projections(db, live["id"])
        if not verified.get("ok"):
            reason = str(verified.get("reason") or "")
            code = ERR_CANCELLED if reason == "cancelled" else ERR_SETTLEMENT_RECONCILIATION
            await _mark_auto_fields(
                db, live["id"], status=AUTO_STATUS_FAILED, attempt_id=attempt_id, error_code=code
            )
            return _result(
                await _load_public_receipt(db, live["id"]),
                financial_complete=False,
                status=AUTO_STATUS_FAILED,
                error_code=code,
                message=(
                    "Penerimaan dibatalkan; settlement tidak efektif."
                    if code == ERR_CANCELLED
                    else "Settlement menunggu rekonsiliasi proyeksi Invoice/PO."
                ),
            )

    await _mark_auto_fields(
        db, live["id"], status=AUTO_STATUS_POSTED, attempt_id=attempt_id, clear_claim=True
    )
    detail = await _load_public_receipt(db, live["id"])
    return _result(detail, financial_complete=True, status=AUTO_STATUS_POSTED)


async def create_sales_receipt_with_auto_lifecycle(
    db, payload: Dict[str, Any], user: dict
) -> Dict[str, Any]:
    """Create receipt, auto-post, and settle when auto flag ON.

    Requires idempotency_key and cash_bank_id. Fail-closed without indexes.
    """
    from services.sales_receipt_posting import SETTLEMENT_EFFECTIVE_FIELD, receipt_is_cancelled
    from services.sales_receipts import create_sales_receipt

    attempt_id = str(uuid.uuid4())
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not idempotency_key:
        return _conflict_result(
            {},
            code=ERR_MISSING_IDEMPOTENCY_KEY,
            message="idempotency_key wajib untuk lifecycle otomatis penerimaan.",
        )
    if not str(payload.get("cash_bank_id") or "").strip():
        return _conflict_result(
            {},
            code=ERR_CASH_BANK,
            message="Kas/Bank wajib dipilih untuk lifecycle otomatis penerimaan.",
        )

    index_block = await _require_activation_indexes(db)
    if index_block is not None:
        return index_block

    fingerprint = canonical_receipt_create_fingerprint(payload)
    invoice_id = str(payload.get("invoice_id") or "").strip()

    existing = await db.sales_receipts.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
    intent = await _get_intent(db, idempotency_key)

    if intent and _intent_is_expired(intent):
        owned = None
        if existing and _receipt_actor_matches(existing, user):
            owned = {"id": existing.get("id")}
        elif intent.get("receipt_id") and str(intent.get("actor_id") or "") == _actor_id(user):
            owned = {"id": intent.get("receipt_id")}
        return _expired_intent_result(owned)

    if existing and existing.get(AUTO_ELIGIBLE_FIELD) is True:
        try:
            await _assert_retry_authorized(
                db,
                user=user,
                receipt=existing,
                intent=intent,
                fingerprint=fingerprint,
                request_invoice_id=invoice_id,
            )
        except _UnauthorizedRetry:
            return _unauthorized_result()
        except _PayloadConflict:
            return _conflict_result(
                {"id": existing.get("id")} if existing.get("id") else {},
                code=ERR_PAYLOAD_CONFLICT,
                message="Permintaan bentrok dengan penerimaan idempoten yang sudah ada.",
            )
        return await _finalize_post_and_settle(
            db, existing, user, attempt_id=attempt_id, fingerprint=fingerprint
        )

    if existing and existing.get(AUTO_ELIGIBLE_FIELD) is not True:
        if intent and str(intent.get("fingerprint") or "") == fingerprint:
            if (
                str(intent.get("actor_id") or "") != _actor_id(user)
                or not _receipt_actor_matches(existing, user)
            ):
                return _unauthorized_result()
            # Orphan: receipt inserted, not yet eligible — adopt under claim.
            try:
                await _bind_intent_receipt(db, idempotency_key, existing["id"], fingerprint)
            except _IntentBindConflict:
                return _conflict_result(
                    {},
                    code=ERR_INTENT_BIND_CONFLICT,
                    message="Intent sudah terikat ke penerimaan lain.",
                )
            await db.sales_receipts.update_one(
                {"id": existing["id"]},
                {
                    "$set": {
                        AUTO_ELIGIBLE_FIELD: True,
                        AUTO_FINGERPRINT_FIELD: fingerprint,
                        SETTLEMENT_EFFECTIVE_FIELD: False
                        if existing.get(SETTLEMENT_EFFECTIVE_FIELD) is not True
                        else True,
                        AUTO_STATUS_FIELD: AUTO_STATUS_PENDING,
                        "updated_at": now_iso(),
                    }
                },
            )
            refreshed = await db.sales_receipts.find_one({"id": existing["id"]}, {"_id": 0}) or existing
            return await _finalize_post_and_settle(
                db, refreshed, user, attempt_id=attempt_id, fingerprint=fingerprint
            )
        if intent and str(intent.get("fingerprint") or "") != fingerprint:
            return _conflict_result(
                {},
                code=ERR_PAYLOAD_CONFLICT,
                message="Permintaan bentrok dengan penerimaan idempoten yang sudah ada.",
            )
        return _conflict_result(
            {},
            code=ERR_NOT_ELIGIBLE,
            message="Penerimaan sudah ada dan tidak memenuhi syarat lifecycle otomatis.",
        )

    try:
        intent = await _upsert_preparing_intent(
            db,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            user=user,
            invoice_id=invoice_id,
        )
    except _PayloadConflict:
        return _conflict_result(
            {},
            code=ERR_PAYLOAD_CONFLICT,
            message="Permintaan bentrok dengan operasi idempoten yang sudah ada.",
        )

    intent = await _get_intent(db, idempotency_key) or intent
    if _intent_is_expired(intent):
        return _expired_intent_result(
            {"id": intent.get("receipt_id")}
            if str(intent.get("actor_id") or "") == _actor_id(user) and intent.get("receipt_id")
            else None
        )

    bound_id = str(intent.get("receipt_id") or "").strip()
    if bound_id:
        bound = await db.sales_receipts.find_one({"id": bound_id}, {"_id": 0})
        if bound and bound.get(AUTO_ELIGIBLE_FIELD) is True:
            try:
                await _assert_retry_authorized(
                    db,
                    user=user,
                    receipt=bound,
                    intent=intent,
                    fingerprint=fingerprint,
                    request_invoice_id=invoice_id,
                )
            except _UnauthorizedRetry:
                return _unauthorized_result()
            except _PayloadConflict:
                return _conflict_result(
                    {"id": bound.get("id")},
                    code=ERR_PAYLOAD_CONFLICT,
                    message="Permintaan bentrok dengan penerimaan idempoten yang sudah ada.",
                )
            return await _finalize_post_and_settle(
                db, bound, user, attempt_id=attempt_id, fingerprint=fingerprint
            )

    try:
        receipt = await create_sales_receipt(
            db,
            payload,
            user,
            defer_operational_settlement=True,
            require_posted_invoice_journal=True,
        )
    except HTTPException as exc:
        return _conflict_result(
            {},
            code=sanitize_error_code(exc),
            message=str(exc.detail) if isinstance(exc.detail, str) else "Validasi penerimaan gagal.",
            http_status=int(exc.status_code or 409),
        )

    receipt_id = str(receipt["id"])
    try:
        await _bind_intent_receipt(db, idempotency_key, receipt_id, fingerprint)
    except _IntentBindConflict:
        return _conflict_result(
            {"id": receipt_id},
            code=ERR_INTENT_BIND_CONFLICT,
            message="Intent sudah terikat ke penerimaan lain.",
        )

    await db.sales_receipts.update_one(
        {"id": receipt_id},
        {
            "$set": {
                AUTO_ELIGIBLE_FIELD: True,
                AUTO_FINGERPRINT_FIELD: fingerprint,
                SETTLEMENT_EFFECTIVE_FIELD: False,
                AUTO_STATUS_FIELD: AUTO_STATUS_PENDING,
                AUTO_ATTEMPT_FIELD: attempt_id,
                "updated_at": now_iso(),
            }
        },
    )
    stored = await db.sales_receipts.find_one({"id": receipt_id}, {"_id": 0}) or receipt
    if receipt_is_cancelled(stored):
        return _conflict_result(stored, code=ERR_CANCELLED, message="Penerimaan dibatalkan.")

    return await _finalize_post_and_settle(
        db, stored, user, attempt_id=attempt_id, fingerprint=fingerprint
    )


async def recover_auto_sales_receipt_accounting(db, receipt_id: str, user: dict) -> Dict[str, Any]:
    """Idempotent recovery for auto-lifecycle eligible receipts only."""
    if not await is_auto_sales_receipt_lifecycle_enabled(db):
        raise HTTPException(
            409,
            {
                "financial_complete": False,
                "error_code": ERR_FLAG_OFF,
                "message": "Lifecycle otomatis penerimaan tidak aktif.",
                "auto_accounting_status": None,
            },
        )

    from services.sales_receipt_posting import receipt_is_cancelled
    from services.sales_user_mapping import ensure_owned_po_id

    attempt_id = str(uuid.uuid4())
    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        rec = await db.sales_receipts.find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
    if not rec:
        raise HTTPException(404, f"Penerimaan Penjualan '{receipt_id}' tidak ditemukan.")

    await ensure_owned_po_id(db, user, rec.get("po_id"))

    if rec.get(AUTO_ELIGIBLE_FIELD) is not True:
        raise HTTPException(409, "Penerimaan ini tidak dibuat melalui lifecycle otomatis.")

    if receipt_is_cancelled(rec):
        raise HTTPException(409, "Penerimaan yang dibatalkan tidak dapat dipulihkan.")

    if not _receipt_actor_matches(rec, user):
        # Narrow create-scoped recovery: same actor ownership on the document.
        raise HTTPException(403, "Pemulihan tidak diizinkan untuk aktor ini.")

    fingerprint = str(rec.get(AUTO_FINGERPRINT_FIELD) or "").strip()
    return await _finalize_post_and_settle(
        db, rec, user, attempt_id=attempt_id, fingerprint=fingerprint or "recovery"
    )
