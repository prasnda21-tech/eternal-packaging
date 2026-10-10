"""Phase 2.2A — automatic Sales Invoice create → finalize → first-time post.

Feature flag `auto_sales_invoice_lifecycle_enabled` defaults OFF.
When OFF, callers must not invoke orchestration or recovery side effects.

Provenance for orphan recovery uses crash-safe intents in
`sales_invoice_auto_create_intents` (not sales_invoices.py).

Does NOT enable journal revision.
Does NOT auto-process historical drafts.
Does NOT create production indexes at startup.
Does NOT modify Sales Receipt services.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument

FLAG_KEY = "auto_sales_invoice_lifecycle_enabled"

# Technical recovery status — distinct from financial_status / accounting_status / payment_status.
AUTO_STATUS_PENDING = "pending"
AUTO_STATUS_POSTING = "posting"
AUTO_STATUS_POSTED = "posted"
AUTO_STATUS_FAILED = "failed"
AUTO_STATUS_RETRY_PENDING = "retry_pending"

AUTO_STATUS_FIELD = "auto_accounting_status"
AUTO_ATTEMPT_FIELD = "auto_accounting_attempt_id"
AUTO_ERROR_FIELD = "auto_accounting_last_error_code"
AUTO_CLAIM_FIELD = "auto_accounting_claim_token"
AUTO_CLAIM_AT_FIELD = "auto_accounting_claim_at"
AUTO_POSTED_AT_FIELD = "auto_accounting_posted_at"
AUTO_ELIGIBLE_FIELD = "auto_lifecycle_eligible"
AUTO_FINGERPRINT_FIELD = "auto_lifecycle_fingerprint"

AUTO_CLAIM_TTL_SECONDS = 120
INTENT_COLL = "sales_invoice_auto_create_intents"
INTENT_TTL_HOURS = 72

# Proposed production index — never auto-created here.
PROPOSED_IDEMPOTENCY_INDEX_NAME = "idempotency_key_partial_unique"
PROPOSED_IDEMPOTENCY_INDEX_KEY = "idempotency_key"
PROPOSED_IDEMPOTENCY_PARTIAL = {"idempotency_key": {"$type": "string"}}

# Sanitized reason codes (never raw DB/exception text).
ERR_VALIDATION = "validation_rejected"
ERR_FINALIZE = "finalize_failed"
ERR_PERIOD_LOCKED = "period_locked"
ERR_SETTINGS = "accounting_settings_missing"
ERR_POSTING = "posting_failed"
ERR_JOURNAL_REVERSED = "journal_reversed_blocked"
ERR_JOURNAL_AMBIGUOUS = "journal_ambiguous"
ERR_CLAIM_HELD = "posting_claim_held"
ERR_CONCURRENT = "concurrent_conflict"
ERR_NOT_ELIGIBLE = "not_auto_lifecycle_eligible"
ERR_PERMISSION = "permission_denied"
ERR_CRASH_RECOVERY = "recovery_required"
ERR_PAYLOAD_CONFLICT = "idempotency_payload_conflict"
ERR_ORPHAN_UNPROVEN = "orphan_provenance_unproven"
ERR_FLAG_OFF = "auto_lifecycle_flag_off"
ERR_MISSING_IDEMPOTENCY_KEY = "idempotency_key_required"
ERR_INDEX_PREREQUISITE = "idempotency_index_prerequisite"
ERR_INTENT_INDEX_PREREQUISITE = "intent_idempotency_index_prerequisite"
ERR_INTENT_BIND_CONFLICT = "intent_bind_conflict"
ERR_INTENT_EXPIRED = "intent_expired_manual_recovery_required"
ERR_UNAUTHORIZED_RETRY = "unauthorized_idempotent_retry"
AUTO_LIFECYCLE_ACTIVATION_BLOCKED = "AUTO_LIFECYCLE_ACTIVATION_BLOCKED"


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
        # Stable money/qty text without scientific notation surprises.
        try:
            from decimal import Decimal

            return str(Decimal(str(raw)))
        except Exception:
            return str(raw)
    return str(raw).strip()


def _norm_money_or_zero(raw: Any) -> str:
    """Treat missing/blank numeric create fields as 0 (matches payload defaults)."""
    text = _norm_scalar(raw)
    return "0" if text == "" else text


def canonical_create_fingerprint(payload: Dict[str, Any]) -> str:
    """Immutable request identity from create payload fields (not live PO state).

    Excludes idempotency_key (that is the outer key). Uses client-submitted
    values only so retries compare the same accepted request body.
    """
    lines_raw = payload.get("lines")
    if lines_raw is None:
        lines_raw = payload.get("line_items") or []
    lines_out = []
    for idx, ln in enumerate(lines_raw or []):
        if not isinstance(ln, dict):
            continue
        try:
            line_idx = int(ln.get("line_idx", idx))
        except (TypeError, ValueError):
            line_idx = idx
        qty = ln.get("quantity")
        if qty is None or qty == "":
            qty = ln.get("invoice_qty")
        lines_out.append(
            {
                "line_idx": line_idx,
                "line_id": _norm_scalar(ln.get("line_id")),
                "quantity": _norm_scalar(qty),
                "unit_price": _norm_money_or_zero(ln.get("unit_price")),
            }
        )
    lines_out.sort(key=lambda r: (r["line_idx"], r["line_id"]))

    charges_out = []
    for ch in payload.get("other_charges") or []:
        if not isinstance(ch, dict):
            continue
        charges_out.append(
            {
                "account_id": _norm_scalar(ch.get("account_id")),
                "amount": _norm_money_or_zero(ch.get("amount")),
                "description": _norm_scalar(ch.get("description")),
            }
        )
    charges_out.sort(key=lambda r: (r["account_id"], r["amount"], r["description"]))

    body = {
        "po_id": _norm_scalar(payload.get("po_id")),
        "invoice_date": _norm_scalar(payload.get("invoice_date"))[:10],
        "payment_term_days": _norm_money_or_zero(payload.get("payment_term_days")),
        "discount_amount": _norm_money_or_zero(payload.get("discount_amount")),
        "ppn_percent": _norm_money_or_zero(payload.get("ppn_percent")),
        "notes": _norm_scalar(payload.get("notes")),
        "lines": lines_out,
        "other_charges": charges_out,
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def is_auto_sales_invoice_lifecycle_enabled(db) -> bool:
    """Global flag. Default OFF. Missing/malformed never enables."""
    try:
        doc = await db.app_settings.find_one(
            {"key": FLAG_KEY},
            {"_id": 0, "enabled": 1, "value": 1},
        )
    except Exception:
        return False
    if not doc:
        return False
    if "enabled" in doc:
        return doc.get("enabled") is True
    return doc.get("value") is True


def _index_key_fields(meta: dict) -> List[str]:
    """Extract ordered field names from a list_indexes / index_information key."""
    keys = (meta or {}).get("key")
    if keys is None:
        return []
    out: List[str] = []
    # list_indexes: SON/list of (field, direction); index_information: same list
    if isinstance(keys, dict):
        out.extend(str(k) for k in keys.keys())
        return out
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
            # Unexpected shape — reject by returning empty sentinel later
            return []
    return out


def _partial_idempotency_string_type(partial: Any) -> bool:
    if not isinstance(partial, dict):
        return False
    if partial == PROPOSED_IDEMPOTENCY_PARTIAL:
        return True
    typed = partial.get("idempotency_key")
    return isinstance(typed, dict) and typed.get("$type") == "string"


def _index_spec_unique(meta: dict) -> bool:
    return bool(isinstance(meta, dict) and meta.get("unique") is True)


async def _list_index_specs(collection) -> Optional[List[dict]]:
    """Read-only list_indexes specs. None on query failure (fail closed)."""
    try:
        cursor = collection.list_indexes()
        specs = await cursor.to_list(length=200)
    except Exception:
        return None
    if not isinstance(specs, list):
        return None
    return [s for s in specs if isinstance(s, dict)]


async def sales_invoice_idempotency_index_ready(db) -> bool:
    """True only when a compatible unique partial idempotency index exists.

    Validates via Mongo list_indexes: key fields/order, unique=true, and
    partialFilterExpression {idempotency_key: {$type: "string"}}.
    Never trusts index name alone. Read-only. Does not create indexes.
    """
    specs = await _list_index_specs(db.sales_invoices)
    if specs is None:
        return False
    for meta in specs:
        if not _index_spec_unique(meta):
            continue
        if _index_key_fields(meta) != [PROPOSED_IDEMPOTENCY_INDEX_KEY]:
            continue
        if _partial_idempotency_string_type(meta.get("partialFilterExpression")):
            return True
    return False


async def intent_idempotency_index_ready(db) -> bool:
    """True only when intents.idempotency_key has a unique single-field index.

    Validates via Mongo list_indexes: key + unique. Name is irrelevant. Read-only.
    """
    specs = await _list_index_specs(db[INTENT_COLL])
    if specs is None:
        return False
    for meta in specs:
        if not _index_spec_unique(meta):
            continue
        if _index_key_fields(meta) == ["idempotency_key"]:
            return True
    return False


async def auto_lifecycle_activation_blockers(db) -> List[str]:
    """Fail-closed readiness for flag-ON activation. No schema mutation.

    Requires BOTH sales_invoices and intent unique idempotency indexes.
    Missing/incompatible indexes → AUTO_LIFECYCLE_ACTIVATION_BLOCKED.
    """
    blockers: List[str] = []
    try:
        if not await is_auto_sales_invoice_lifecycle_enabled(db):
            blockers.append(ERR_FLAG_OFF)
        invoice_ok = await sales_invoice_idempotency_index_ready(db)
        intent_ok = await intent_idempotency_index_ready(db)
        if not invoice_ok:
            blockers.append(ERR_INDEX_PREREQUISITE)
        if not intent_ok:
            blockers.append(ERR_INTENT_INDEX_PREREQUISITE)
        if not invoice_ok or not intent_ok:
            if AUTO_LIFECYCLE_ACTIVATION_BLOCKED not in blockers:
                blockers.append(AUTO_LIFECYCLE_ACTIVATION_BLOCKED)
    except Exception:
        if AUTO_LIFECYCLE_ACTIVATION_BLOCKED not in blockers:
            blockers.append(AUTO_LIFECYCLE_ACTIVATION_BLOCKED)
    return blockers


async def _require_activation_indexes(db) -> Optional[Dict[str, Any]]:
    """Runtime fail-closed when required unique indexes are absent/incompatible."""
    invoice_ok = await sales_invoice_idempotency_index_ready(db)
    intent_ok = await intent_idempotency_index_ready(db)
    if invoice_ok and intent_ok:
        return None
    code = ERR_INDEX_PREREQUISITE if not invoice_ok else ERR_INTENT_INDEX_PREREQUISITE
    return _conflict_result(
        {},
        code=AUTO_LIFECYCLE_ACTIVATION_BLOCKED,
        message=(
            "Lifecycle otomatis diblokir: indeks idempotensi wajib belum siap "
            f"({code})."
        ),
    )


def sanitize_error_code(exc: BaseException) -> str:
    """Map exceptions to stable public codes. Never leak stack/DB text."""
    if isinstance(exc, HTTPException):
        detail = exc.detail
        text = detail if isinstance(detail, str) else str(detail or "")
        lower = text.lower()
        if exc.status_code == 409 and ("periode" in lower or "period" in lower or "dikunci" in lower):
            return ERR_PERIOD_LOCKED
        if exc.status_code == 409 and ("klaim" in lower or "claim" in lower or "bersamaan" in lower):
            return ERR_CLAIM_HELD if "posting" in lower or "accounting" in lower else ERR_CONCURRENT
        if exc.status_code == 400 and "pengaturan" in lower:
            return ERR_SETTINGS
        if exc.status_code in (400, 404, 422):
            return ERR_VALIDATION
        if exc.status_code == 409:
            return ERR_CONCURRENT
        return ERR_POSTING
    from services.accounting_periods import PeriodLockedError

    if isinstance(exc, PeriodLockedError):
        return ERR_PERIOD_LOCKED
    return ERR_POSTING


def _auto_claim_active(invoice: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    token = str((invoice or {}).get(AUTO_CLAIM_FIELD) or "").strip()
    if not token:
        return False
    claimed_at = _parse_iso((invoice or {}).get(AUTO_CLAIM_AT_FIELD))
    if claimed_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return (current - claimed_at).total_seconds() < AUTO_CLAIM_TTL_SECONDS


async def ensure_auto_intent_indexes(db) -> None:
    """Additive indexes for the intent collection only. Call from tests/local setup.

    Never called from production server startup by this module.
    Tolerates an equivalent unique idempotency index under another name.
    """
    from pymongo.errors import OperationFailure

    coll = db[INTENT_COLL]
    if not await intent_idempotency_index_ready(db):
        try:
            await coll.create_index(
                "idempotency_key", unique=True, name="auto_intent_idempotency_unique"
            )
        except OperationFailure:
            # Equivalent unique index may already exist under a different name.
            if not await intent_idempotency_index_ready(db):
                raise
    try:
        await coll.create_index("invoice_id", sparse=True, name="auto_intent_invoice_id")
    except OperationFailure:
        pass


async def _mark_auto_fields(
    db,
    invoice_id: str,
    *,
    status: str,
    attempt_id: Optional[str] = None,
    error_code: Optional[str] = None,
    claim_token: Optional[str] = None,
    clear_claim: bool = False,
    posted: bool = False,
    fingerprint: Optional[str] = None,
) -> None:
    now = now_iso()
    patch: Dict[str, Any] = {
        AUTO_STATUS_FIELD: status,
        "updated_at": now,
    }
    if attempt_id is not None:
        patch[AUTO_ATTEMPT_FIELD] = attempt_id
    if error_code is not None:
        patch[AUTO_ERROR_FIELD] = error_code
    if claim_token is not None:
        patch[AUTO_CLAIM_FIELD] = claim_token
        patch[AUTO_CLAIM_AT_FIELD] = now
    if posted:
        patch[AUTO_POSTED_AT_FIELD] = now
    if fingerprint is not None:
        patch[AUTO_FINGERPRINT_FIELD] = fingerprint
    unset: Dict[str, str] = {}
    if clear_claim:
        unset[AUTO_CLAIM_FIELD] = ""
        unset[AUTO_CLAIM_AT_FIELD] = ""
    update: Dict[str, Any] = {"$set": patch}
    if unset:
        update["$unset"] = unset
    if status == AUTO_STATUS_POSTED:
        update.setdefault("$unset", {})[AUTO_ERROR_FIELD] = ""
        patch.pop(AUTO_ERROR_FIELD, None)
    await db.sales_invoices.update_one({"id": str(invoice_id)}, update)


async def _load_invoice(db, invoice_id: str) -> dict:
    from services.sales_invoices import get_sales_invoice_detail

    return await get_sales_invoice_detail(db, invoice_id)


async def _confirm_posted_primary(db, invoice_id: str) -> Tuple[bool, Optional[dict], Optional[str]]:
    """Return (ok, journal, fail_code). Fail closed on reversed/ambiguous."""
    from services.sales_invoice_posting import (
        ACCOUNTING_POSTED,
        ACCOUNTING_REVERSED,
        accounting_status_from_journal,
        find_sales_invoice_source_journal,
    )

    journal = await find_sales_invoice_source_journal(db, invoice_id)
    status = accounting_status_from_journal(journal)
    if status == ACCOUNTING_POSTED and journal:
        return True, journal, None
    if status == ACCOUNTING_REVERSED:
        return False, journal, ERR_JOURNAL_REVERSED
    if journal:
        return False, journal, ERR_JOURNAL_AMBIGUOUS
    return False, None, None


def _public_message(exc: HTTPException) -> Optional[str]:
    detail = exc.detail
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail.get("message"))
    return None


def _result(
    invoice: dict,
    *,
    financial_complete: bool,
    status: str,
    error_code: Optional[str] = None,
    http_status: int = 200,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "invoice": invoice,
        "financial_complete": financial_complete,
        AUTO_STATUS_FIELD: status,
        "error_code": error_code,
        "http_status": 200 if financial_complete else (http_status if http_status != 200 else 409),
        "message": message
        or (
            "Faktur Penjualan berhasil dibuat, difinalisasi, dan diposting ke accounting"
            if financial_complete
            else "Faktur tersimpan tetapi posting akuntansi belum selesai."
        ),
    }


def _conflict_result(invoice: Optional[dict], *, code: str, message: str) -> Dict[str, Any]:
    return {
        "invoice": invoice or {},
        "financial_complete": False,
        AUTO_STATUS_FIELD: AUTO_STATUS_FAILED,
        "error_code": code,
        "http_status": 409,
        "message": message,
    }


async def _get_intent(db, idempotency_key: str) -> Optional[dict]:
    return await db[INTENT_COLL].find_one({"idempotency_key": idempotency_key}, {"_id": 0})


async def _upsert_preparing_intent(
    db,
    *,
    idempotency_key: str,
    fingerprint: str,
    user: dict,
    po_id: str,
) -> dict:
    """Create or resume intent. Different fingerprint → conflict."""
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
        "po_id": str(po_id or "").strip(),
        "invoice_id": None,
        "status": "preparing",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    try:
        await db[INTENT_COLL].insert_one(doc)
        doc.pop("_id", None)
        return doc
    except Exception:
        # Race: sibling inserted intent
        again = await _get_intent(db, idempotency_key)
        if again and str(again.get("fingerprint") or "") == fingerprint:
            return again
        if again:
            raise _PayloadConflict()
        raise


class _PayloadConflict(Exception):
    pass


class _IntentBindConflict(Exception):
    pass


class _UnauthorizedRetry(Exception):
    pass


class _IntentExpired(Exception):
    def __init__(self, invoice_id: Optional[str] = None):
        self.invoice_id = invoice_id


def _intent_is_expired(intent: Optional[dict]) -> bool:
    if not intent:
        return False
    intent_at = _parse_iso(intent.get("created_at"))
    if not intent_at:
        return False
    return (datetime.now(timezone.utc) - intent_at) > timedelta(hours=INTENT_TTL_HOURS)


async def _bind_intent_invoice(db, idempotency_key: str, invoice_id: str, fingerprint: str) -> dict:
    """CAS-bind intent.invoice_id: null→expected, or already-equal idempotent success.

    Uses update_one matched/modified counts. Never overwrites a different invoice_id.
    Re-reads intent after write — does not trust stale in-memory snapshots.
    """
    expected = str(invoice_id)
    now = now_iso()
    filter_unbound = {
        "idempotency_key": idempotency_key,
        "fingerprint": fingerprint,
        "$or": [
            {"invoice_id": None},
            {"invoice_id": {"$exists": False}},
            {"invoice_id": ""},
        ],
    }
    result = await db[INTENT_COLL].update_one(
        filter_unbound,
        {
            "$set": {
                "invoice_id": expected,
                "status": "bound",
                "updated_at": now,
            }
        },
    )
    matched = int(getattr(result, "matched_count", 0) or 0)
    modified = int(getattr(result, "modified_count", 0) or 0)
    if matched == 1 and modified in (0, 1):
        current = await _get_intent(db, idempotency_key)
        if (
            current
            and str(current.get("fingerprint") or "") == fingerprint
            and str(current.get("invoice_id") or "").strip() == expected
        ):
            return current
        raise _IntentBindConflict()

    # Idempotent path: already bound to expected invoice_id
    current = await _get_intent(db, idempotency_key)
    if not current or str(current.get("fingerprint") or "") != fingerprint:
        raise _IntentBindConflict()
    existing_id = str(current.get("invoice_id") or "").strip()
    if existing_id == expected:
        if str(current.get("status") or "") != "bound":
            status_result = await db[INTENT_COLL].update_one(
                {
                    "idempotency_key": idempotency_key,
                    "fingerprint": fingerprint,
                    "invoice_id": expected,
                },
                {"$set": {"status": "bound", "updated_at": now}},
            )
            if int(getattr(status_result, "matched_count", 0) or 0) < 1:
                raise _IntentBindConflict()
            current = await _get_intent(db, idempotency_key) or current
        return current
    # Bound to a different invoice — fail closed, do not overwrite.
    raise _IntentBindConflict()


def _invoice_actor_matches(invoice: dict, user: dict) -> bool:
    created = invoice.get("created_by") or {}
    inv_actor = str(created.get("id") or "").strip()
    return bool(inv_actor) and inv_actor == _actor_id(user)


async def _assert_retry_authorized(
    db,
    *,
    user: dict,
    invoice: dict,
    intent: Optional[dict],
    fingerprint: str,
    request_po_id: str,
) -> None:
    """Actor + PO + fingerprint + ownership. Fail closed without leaking details."""
    if not _invoice_actor_matches(invoice, user):
        raise _UnauthorizedRetry()
    if intent is not None:
        if str(intent.get("actor_id") or "") != _actor_id(user):
            raise _UnauthorizedRetry()
        if str(intent.get("fingerprint") or "") != fingerprint:
            raise _PayloadConflict()
        intent_po = str(intent.get("po_id") or "").strip()
        if intent_po and request_po_id and intent_po != request_po_id:
            raise _UnauthorizedRetry()
    stored_fp = str(invoice.get(AUTO_FINGERPRINT_FIELD) or "").strip()
    if stored_fp and stored_fp != fingerprint:
        raise _PayloadConflict()
    inv_po = str(invoice.get("po_id") or "").strip()
    if request_po_id and inv_po and request_po_id != inv_po:
        raise _UnauthorizedRetry()
    from services.sales_user_mapping import ensure_owned_po_id

    try:
        await ensure_owned_po_id(db, user, inv_po or request_po_id)
    except HTTPException:
        raise _UnauthorizedRetry() from None


def _unauthorized_result() -> Dict[str, Any]:
    return {
        "invoice": {},
        "financial_complete": False,
        AUTO_STATUS_FIELD: AUTO_STATUS_FAILED,
        "error_code": ERR_UNAUTHORIZED_RETRY,
        "http_status": 403,
        "message": "Permintaan idempoten tidak diizinkan.",
    }


def _expired_intent_result(invoice: Optional[dict] = None) -> Dict[str, Any]:
    """Preserve intent + linked invoice; require manual recovery.

    Safe recovery prerequisites (no unrestricted repair endpoint):
    - Feature flag may remain OFF for create; use recover-auto-accounting only
      when invoice is auto_lifecycle_eligible and actor owns the PO.
    - Caller must prove create permission + PO ownership.
    - Do not delete/reuse the expired idempotency_key for another payload.
    - Do not auto-adopt historical drafts or bypass period/qty/journal guards.
    - If invoice exists and is eligible but incomplete: authorized recover path.
    - If invoice is draft without eligibility: Owner-guided repair only.
    """
    safe_inv: Dict[str, Any] = {}
    if invoice and invoice.get("id"):
        safe_inv = {"id": str(invoice.get("id"))}
    return {
        "invoice": safe_inv,
        "financial_complete": False,
        AUTO_STATUS_FIELD: AUTO_STATUS_FAILED,
        "error_code": ERR_INTENT_EXPIRED,
        "http_status": 409,
        "message": (
            "Intent lifecycle kedaluwarsa. Pemulihan manual diperlukan; "
            "kunci idempotensi tidak dapat dipakai ulang untuk payload lain."
        ),
    }


def _intent_proves_orphan(intent: dict, invoice: dict, user: dict, fingerprint: str) -> bool:
    """Prove draft originated from this auto-create intent (not historical)."""
    from services.sales_invoices import FINANCIAL_DRAFT, effective_financial_status

    if not intent:
        return False
    if _intent_is_expired(intent):
        return False
    if str(intent.get("fingerprint") or "") != fingerprint:
        return False
    if str(intent.get("actor_id") or "") != _actor_id(user):
        return False
    if not _invoice_actor_matches(invoice, user):
        return False
    if effective_financial_status(invoice) != FINANCIAL_DRAFT:
        if invoice.get(AUTO_ELIGIBLE_FIELD) is True:
            return False
        if str(intent.get("invoice_id") or "") == str(invoice.get("id") or ""):
            return True
        return False
    bound = str(intent.get("invoice_id") or "").strip()
    if bound and bound != str(invoice.get("id") or ""):
        return False
    intent_at = _parse_iso(intent.get("created_at"))
    inv_at = _parse_iso(invoice.get("created_at"))
    if intent_at and inv_at and inv_at + timedelta(seconds=5) < intent_at:
        return False
    return True


async def _cas_mark_eligible(
    db,
    invoice_id: str,
    *,
    attempt_id: str,
    fingerprint: str,
) -> Optional[dict]:
    return await db.sales_invoices.find_one_and_update(
        {
            "id": str(invoice_id),
            "$or": [
                {AUTO_ELIGIBLE_FIELD: {"$ne": True}},
                {AUTO_ELIGIBLE_FIELD: True, AUTO_FINGERPRINT_FIELD: fingerprint},
            ],
        },
        {
            "$set": {
                AUTO_ELIGIBLE_FIELD: True,
                AUTO_FINGERPRINT_FIELD: fingerprint,
                AUTO_STATUS_FIELD: AUTO_STATUS_PENDING,
                AUTO_ATTEMPT_FIELD: attempt_id,
                "updated_at": now_iso(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def _finalize_and_post(
    db,
    invoice: dict,
    user: dict,
    *,
    attempt_id: str,
) -> Dict[str, Any]:
    """Finalize then first-time post. Returns structured lifecycle result."""
    from services.sales_invoice_posting import (
        ACCOUNTING_POSTED,
        accounting_status_from_journal,
        find_sales_invoice_source_journal,
        post_sales_invoice_accounting,
        posting_claim_is_active,
    )
    from services.sales_invoices import (
        FINANCIAL_CANCELLED,
        FINANCIAL_FINALIZED,
        effective_financial_status,
        finalize_sales_invoice,
    )

    invoice_id = str(invoice["id"])

    ok, journal, fail_code = await _confirm_posted_primary(db, invoice_id)
    if ok:
        await _mark_auto_fields(
            db,
            invoice_id,
            status=AUTO_STATUS_POSTED,
            attempt_id=attempt_id,
            clear_claim=True,
            posted=True,
        )
        detail = await _load_invoice(db, invoice_id)
        return _result(detail, financial_complete=True, status=AUTO_STATUS_POSTED)

    if fail_code in (ERR_JOURNAL_REVERSED, ERR_JOURNAL_AMBIGUOUS):
        await _mark_auto_fields(
            db,
            invoice_id,
            status=AUTO_STATUS_FAILED,
            attempt_id=attempt_id,
            error_code=fail_code,
            clear_claim=True,
        )
        detail = await _load_invoice(db, invoice_id)
        return _result(
            detail,
            financial_complete=False,
            status=AUTO_STATUS_FAILED,
            error_code=fail_code,
        )

    status = effective_financial_status(invoice)
    if status == FINANCIAL_CANCELLED:
        await _mark_auto_fields(
            db,
            invoice_id,
            status=AUTO_STATUS_FAILED,
            attempt_id=attempt_id,
            error_code=ERR_NOT_ELIGIBLE,
            clear_claim=True,
        )
        detail = await _load_invoice(db, invoice_id)
        return _result(
            detail,
            financial_complete=False,
            status=AUTO_STATUS_FAILED,
            error_code=ERR_NOT_ELIGIBLE,
        )

    # Refresh claim fields from DB (stale in-memory invoice)
    current_doc = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0}) or invoice
    if posting_claim_is_active(current_doc) or _auto_claim_active(current_doc):
        await _mark_auto_fields(
            db,
            invoice_id,
            status=AUTO_STATUS_PENDING,
            attempt_id=attempt_id,
            error_code=ERR_CLAIM_HELD,
        )
        detail = await _load_invoice(db, invoice_id)
        return _result(
            detail,
            financial_complete=False,
            status=AUTO_STATUS_PENDING,
            error_code=ERR_CLAIM_HELD,
            http_status=409,
        )

    claim_token = str(uuid.uuid4())
    await _mark_auto_fields(
        db,
        invoice_id,
        status=AUTO_STATUS_POSTING,
        attempt_id=attempt_id,
        claim_token=claim_token,
    )

    try:
        if status != FINANCIAL_FINALIZED:
            await finalize_sales_invoice(db, invoice_id, user)
    except Exception as exc:
        code = sanitize_error_code(exc)
        await _mark_auto_fields(
            db,
            invoice_id,
            status=AUTO_STATUS_FAILED,
            attempt_id=attempt_id,
            error_code=code if code != ERR_POSTING else ERR_FINALIZE,
            clear_claim=True,
        )
        if isinstance(exc, HTTPException):
            detail = await _load_invoice(db, invoice_id)
            return _result(
                detail,
                financial_complete=False,
                status=AUTO_STATUS_FAILED,
                error_code=code if code != ERR_POSTING else ERR_FINALIZE,
                http_status=exc.status_code,
                message=_public_message(exc),
            )
        raise

    ok, journal, fail_code = await _confirm_posted_primary(db, invoice_id)
    if ok:
        await _mark_auto_fields(
            db,
            invoice_id,
            status=AUTO_STATUS_POSTED,
            attempt_id=attempt_id,
            clear_claim=True,
            posted=True,
        )
        detail = await _load_invoice(db, invoice_id)
        return _result(detail, financial_complete=True, status=AUTO_STATUS_POSTED)
    if fail_code:
        await _mark_auto_fields(
            db,
            invoice_id,
            status=AUTO_STATUS_FAILED,
            attempt_id=attempt_id,
            error_code=fail_code,
            clear_claim=True,
        )
        detail = await _load_invoice(db, invoice_id)
        return _result(
            detail,
            financial_complete=False,
            status=AUTO_STATUS_FAILED,
            error_code=fail_code,
        )

    try:
        await post_sales_invoice_accounting(db, invoice_id, user)
    except Exception as exc:
        ok, journal, fail_code = await _confirm_posted_primary(db, invoice_id)
        if ok:
            await _mark_auto_fields(
                db,
                invoice_id,
                status=AUTO_STATUS_POSTED,
                attempt_id=attempt_id,
                clear_claim=True,
                posted=True,
            )
            detail = await _load_invoice(db, invoice_id)
            return _result(detail, financial_complete=True, status=AUTO_STATUS_POSTED)
        code = fail_code or sanitize_error_code(exc)
        await _mark_auto_fields(
            db,
            invoice_id,
            status=AUTO_STATUS_FAILED,
            attempt_id=attempt_id,
            error_code=code,
            clear_claim=True,
        )
        detail = await _load_invoice(db, invoice_id)
        http_status = exc.status_code if isinstance(exc, HTTPException) else 409
        return _result(
            detail,
            financial_complete=False,
            status=AUTO_STATUS_FAILED,
            error_code=code,
            http_status=http_status,
            message=_public_message(exc) if isinstance(exc, HTTPException) else None,
        )

    ok, journal, fail_code = await _confirm_posted_primary(db, invoice_id)
    if not ok:
        code = fail_code or ERR_POSTING
        await _mark_auto_fields(
            db,
            invoice_id,
            status=AUTO_STATUS_FAILED,
            attempt_id=attempt_id,
            error_code=code,
            clear_claim=True,
        )
        detail = await _load_invoice(db, invoice_id)
        return _result(
            detail,
            financial_complete=False,
            status=AUTO_STATUS_FAILED,
            error_code=code,
        )

    await _mark_auto_fields(
        db,
        invoice_id,
        status=AUTO_STATUS_POSTED,
        attempt_id=attempt_id,
        clear_claim=True,
        posted=True,
    )
    detail = await _load_invoice(db, invoice_id)
    if accounting_status_from_journal(
        await find_sales_invoice_source_journal(db, invoice_id)
    ) != ACCOUNTING_POSTED:
        await _mark_auto_fields(
            db,
            invoice_id,
            status=AUTO_STATUS_FAILED,
            attempt_id=attempt_id,
            error_code=ERR_JOURNAL_AMBIGUOUS,
            clear_claim=True,
        )
        detail = await _load_invoice(db, invoice_id)
        return _result(
            detail,
            financial_complete=False,
            status=AUTO_STATUS_FAILED,
            error_code=ERR_JOURNAL_AMBIGUOUS,
        )
    return _result(detail, financial_complete=True, status=AUTO_STATUS_POSTED)


async def _resume_eligible(
    db,
    invoice: dict,
    user: dict,
    *,
    attempt_id: str,
    fingerprint: str,
    intent: Optional[dict],
    request_po_id: str,
) -> Dict[str, Any]:
    try:
        await _assert_retry_authorized(
            db,
            user=user,
            invoice=invoice,
            intent=intent,
            fingerprint=fingerprint,
            request_po_id=request_po_id,
        )
    except _UnauthorizedRetry:
        return _unauthorized_result()
    except _PayloadConflict:
        return _conflict_result(
            {"id": invoice.get("id")} if invoice.get("id") else {},
            code=ERR_PAYLOAD_CONFLICT,
            message="Permintaan bentrok dengan faktur idempoten yang sudah ada.",
        )
    stored_fp = str(invoice.get(AUTO_FINGERPRINT_FIELD) or "").strip()
    if not stored_fp:
        await db.sales_invoices.update_one(
            {"id": invoice["id"], AUTO_ELIGIBLE_FIELD: True},
            {"$set": {AUTO_FINGERPRINT_FIELD: fingerprint, "updated_at": now_iso()}},
        )
    return await _finalize_and_post(db, invoice, user, attempt_id=attempt_id)


async def create_sales_invoice_with_auto_lifecycle(
    db, payload: Dict[str, Any], user: dict
) -> Dict[str, Any]:
    """Create invoice then auto finalize+post when flag ON.

    Requires idempotency_key. Uses intent provenance for orphan recovery.
    Fail-closed when required unique idempotency indexes are absent.
    """
    from services.sales_invoices import create_sales_invoice

    attempt_id = str(uuid.uuid4())
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not idempotency_key:
        return _conflict_result(
            {},
            code=ERR_MISSING_IDEMPOTENCY_KEY,
            message="idempotency_key wajib untuk lifecycle otomatis.",
        )

    index_block = await _require_activation_indexes(db)
    if index_block is not None:
        return index_block

    fingerprint = canonical_create_fingerprint(payload)
    po_id = str(payload.get("po_id") or "").strip()

    existing = await db.sales_invoices.find_one(
        {"idempotency_key": idempotency_key}, {"_id": 0}
    )
    intent = await _get_intent(db, idempotency_key)

    if intent and _intent_is_expired(intent):
        # Never reuse key for another payload; do not auto-heal via create path.
        owned = None
        if existing and _invoice_actor_matches(existing, user):
            owned = {"id": existing.get("id")}
        elif intent.get("invoice_id"):
            # Only expose id when actor matches intent (no cross-user leak).
            if str(intent.get("actor_id") or "") == _actor_id(user):
                owned = {"id": intent.get("invoice_id")}
        return _expired_intent_result(owned)

    if existing and existing.get(AUTO_ELIGIBLE_FIELD) is True:
        return await _resume_eligible(
            db,
            existing,
            user,
            attempt_id=attempt_id,
            fingerprint=fingerprint,
            intent=intent,
            request_po_id=po_id,
        )

    if existing and existing.get(AUTO_ELIGIBLE_FIELD) is not True:
        if intent and _intent_proves_orphan(intent, existing, user, fingerprint):
            try:
                await _assert_retry_authorized(
                    db,
                    user=user,
                    invoice=existing,
                    intent=intent,
                    fingerprint=fingerprint,
                    request_po_id=po_id,
                )
                await _bind_intent_invoice(db, idempotency_key, existing["id"], fingerprint)
            except _UnauthorizedRetry:
                return _unauthorized_result()
            except _PayloadConflict:
                return _conflict_result(
                    {"id": existing.get("id")} if existing.get("id") else {},
                    code=ERR_PAYLOAD_CONFLICT,
                    message="Permintaan bentrok dengan faktur idempoten yang sudah ada.",
                )
            except _IntentBindConflict:
                return _conflict_result(
                    {},
                    code=ERR_INTENT_BIND_CONFLICT,
                    message="Intent sudah terikat ke faktur lain.",
                )
            claimed = await _cas_mark_eligible(
                db, existing["id"], attempt_id=attempt_id, fingerprint=fingerprint
            )
            if claimed is None:
                refreshed = await db.sales_invoices.find_one({"id": existing["id"]}, {"_id": 0})
                if refreshed and refreshed.get(AUTO_ELIGIBLE_FIELD) is True:
                    return await _resume_eligible(
                        db,
                        refreshed,
                        user,
                        attempt_id=attempt_id,
                        fingerprint=fingerprint,
                        intent=await _get_intent(db, idempotency_key),
                        request_po_id=po_id,
                    )
                return _conflict_result(
                    {},
                    code=ERR_ORPHAN_UNPROVEN,
                    message="Faktur orphan tidak dapat diverifikasi untuk lifecycle otomatis.",
                )
            return await _finalize_and_post(db, claimed, user, attempt_id=attempt_id)

        if intent and str(intent.get("fingerprint") or "") != fingerprint:
            return _conflict_result(
                {},
                code=ERR_PAYLOAD_CONFLICT,
                message="Permintaan bentrok dengan faktur idempoten yang sudah ada.",
            )

        if intent and str(intent.get("fingerprint") or "") == fingerprint:
            # Same key/fingerprint but ownership/orphan proof failed — no leak.
            if (
                str(intent.get("actor_id") or "") != _actor_id(user)
                or not _invoice_actor_matches(existing, user)
            ):
                return _unauthorized_result()

        return _conflict_result(
            {},
            code=ERR_NOT_ELIGIBLE if not intent else ERR_ORPHAN_UNPROVEN,
            message=(
                "Faktur sudah ada dan tidak memenuhi syarat lifecycle otomatis."
                if not intent
                else "Faktur orphan tidak dapat diverifikasi untuk lifecycle otomatis."
            ),
        )

    # No invoice yet — register intent before create (crash-safe identity).
    try:
        intent = await _upsert_preparing_intent(
            db,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            user=user,
            po_id=po_id,
        )
    except _PayloadConflict:
        return _conflict_result(
            {},
            code=ERR_PAYLOAD_CONFLICT,
            message="Permintaan bentrok dengan operasi idempoten yang sudah ada.",
        )

    # Re-read intent (never trust stale snapshot after races).
    intent = await _get_intent(db, idempotency_key) or intent
    if _intent_is_expired(intent):
        return _expired_intent_result(
            {"id": intent.get("invoice_id")} if str(intent.get("actor_id") or "") == _actor_id(user) else None
        )

    bound_id = str(intent.get("invoice_id") or "").strip()
    if bound_id:
        bound_inv = await db.sales_invoices.find_one({"id": bound_id}, {"_id": 0})
        if bound_inv:
            if bound_inv.get(AUTO_ELIGIBLE_FIELD) is True:
                return await _resume_eligible(
                    db,
                    bound_inv,
                    user,
                    attempt_id=attempt_id,
                    fingerprint=fingerprint,
                    intent=intent,
                    request_po_id=po_id,
                )
            if _intent_proves_orphan(intent, bound_inv, user, fingerprint):
                try:
                    await _assert_retry_authorized(
                        db,
                        user=user,
                        invoice=bound_inv,
                        intent=intent,
                        fingerprint=fingerprint,
                        request_po_id=po_id,
                    )
                except _UnauthorizedRetry:
                    return _unauthorized_result()
                except _PayloadConflict:
                    return _conflict_result(
                        {},
                        code=ERR_PAYLOAD_CONFLICT,
                        message="Permintaan bentrok dengan faktur idempoten yang sudah ada.",
                    )
                claimed = await _cas_mark_eligible(
                    db, bound_inv["id"], attempt_id=attempt_id, fingerprint=fingerprint
                )
                if claimed is not None:
                    return await _finalize_and_post(db, claimed, user, attempt_id=attempt_id)

    invoice = await create_sales_invoice(db, payload, user)
    invoice_id = str(invoice["id"])

    stored = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0}) or invoice
    if stored.get("idempotency_key") == idempotency_key and stored.get(AUTO_ELIGIBLE_FIELD) is True:
        return await _resume_eligible(
            db,
            stored,
            user,
            attempt_id=attempt_id,
            fingerprint=fingerprint,
            intent=await _get_intent(db, idempotency_key),
            request_po_id=po_id,
        )

    try:
        await _bind_intent_invoice(db, idempotency_key, invoice_id, fingerprint)
    except _IntentBindConflict:
        return _conflict_result(
            {},
            code=ERR_INTENT_BIND_CONFLICT,
            message="Intent sudah terikat ke faktur lain.",
        )

    claimed = await _cas_mark_eligible(
        db, invoice_id, attempt_id=attempt_id, fingerprint=fingerprint
    )
    if claimed is None:
        refreshed = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0}) or stored
        if refreshed.get(AUTO_ELIGIBLE_FIELD) is True:
            return await _resume_eligible(
                db,
                refreshed,
                user,
                attempt_id=attempt_id,
                fingerprint=fingerprint,
                intent=await _get_intent(db, idempotency_key),
                request_po_id=po_id,
            )
        return _conflict_result(
            {},
            code=ERR_ORPHAN_UNPROVEN,
            message="Faktur tidak dapat ditandai eligible untuk lifecycle otomatis.",
        )

    return await _finalize_and_post(db, claimed, user, attempt_id=attempt_id)


async def recover_auto_sales_invoice_accounting(
    db, invoice_id: str, user: dict
) -> Dict[str, Any]:
    """Idempotent recovery for an auto-lifecycle eligible invoice.

    Fail-closed when feature flag is OFF — no finalize/post side effects.
    """
    if not await is_auto_sales_invoice_lifecycle_enabled(db):
        raise HTTPException(
            409,
            {
                "financial_complete": False,
                "error_code": ERR_FLAG_OFF,
                "message": "Lifecycle otomatis faktur tidak aktif.",
                "auto_accounting_status": None,
            },
        )

    attempt_id = str(uuid.uuid4())
    inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not inv:
        inv = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not inv:
        raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

    if inv.get(AUTO_ELIGIBLE_FIELD) is not True:
        raise HTTPException(409, "Faktur ini tidak dibuat melalui lifecycle otomatis.")

    status = str(inv.get(AUTO_STATUS_FIELD) or "").strip()
    if status == AUTO_STATUS_POSTED:
        ok, _, fail_code = await _confirm_posted_primary(db, inv["id"])
        if ok:
            detail = await _load_invoice(db, inv["id"])
            return _result(detail, financial_complete=True, status=AUTO_STATUS_POSTED)
        await _mark_auto_fields(
            db,
            inv["id"],
            status=AUTO_STATUS_FAILED,
            attempt_id=attempt_id,
            error_code=fail_code or ERR_CRASH_RECOVERY,
            clear_claim=True,
        )
        detail = await _load_invoice(db, inv["id"])
        return _result(
            detail,
            financial_complete=False,
            status=AUTO_STATUS_FAILED,
            error_code=fail_code or ERR_CRASH_RECOVERY,
        )

    if _auto_claim_active(inv):
        detail = await _load_invoice(db, inv["id"])
        return _result(
            detail,
            financial_complete=False,
            status=AUTO_STATUS_PENDING,
            error_code=ERR_CLAIM_HELD,
            http_status=409,
        )

    await _mark_auto_fields(
        db,
        inv["id"],
        status=AUTO_STATUS_RETRY_PENDING,
        attempt_id=attempt_id,
        clear_claim=True,
    )
    refreshed = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0}) or inv
    return await _finalize_and_post(db, refreshed, user, attempt_id=attempt_id)
