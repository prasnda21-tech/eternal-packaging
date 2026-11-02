"""Durable multi-worker idempotency for Sales Invoice create (flag-OFF path).

Guarantees for non-empty idempotency_key:
- At most one sales_invoices document per key (unique partial index).
- Operation ownership via sales_invoice_create_intents before billing claims.
- Same key + same payload fingerprint → idempotent replay.
- Same key + different fingerprint → 409 conflict.

Empty/null keys skip these guards (legacy draft creates).
Does not post journals. Does not mutate production indexes outside ensure().
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError, OperationFailure

_logger = logging.getLogger(__name__)

INTENT_COLL = "sales_invoice_create_intents"
INTENT_INDEX_NAME = "uniq_sales_invoice_create_intent_key"
INVOICE_INDEX_NAME = "idempotency_key_partial_unique"
INVOICE_INDEX_PARTIAL = {"idempotency_key": {"$type": "string"}}

FINGERPRINT_FIELD = "idempotency_fingerprint"

ERR_INDEX_PREREQUISITE = "idempotency_index_prerequisite"
ERR_PAYLOAD_CONFLICT = "idempotency_payload_conflict"
ERR_CONCURRENT = "idempotency_concurrent_in_progress"
ERR_DUPLICATE_LEGACY_KEYS = "idempotency_duplicate_legacy_keys"

_FOLLOWER_WAIT_SECONDS = 2.5
_FOLLOWER_POLL_INTERVAL = 0.05


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _norm_scalar(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, (int, float)):
        try:
            return str(Decimal(str(raw)))
        except Exception:
            return str(raw)
    return str(raw).strip()


def _norm_money_or_zero(raw: Any) -> str:
    text = _norm_scalar(raw)
    return "0" if text == "" else text


def normalize_idempotency_key(raw: Any) -> str:
    return str(raw or "").strip()


def create_payload_fingerprint(payload: Dict[str, Any]) -> str:
    """Stable fingerprint for legacy + Model C create payloads (excludes key)."""
    lines_raw = payload.get("lines")
    if lines_raw is None:
        lines_raw = payload.get("line_items") or []
    lines_out: List[dict] = []
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
                "source_po_id": _norm_scalar(ln.get("source_po_id")),
                "source_po_line_id": _norm_scalar(
                    ln.get("source_po_line_id") or ln.get("line_id")
                ),
                "source_po_line_idx": _norm_scalar(ln.get("source_po_line_idx")),
                "source_sj_id": _norm_scalar(ln.get("source_sj_id")),
                "quantity": _norm_scalar(qty),
                "unit_price": _norm_money_or_zero(ln.get("unit_price")),
            }
        )
    lines_out.sort(
        key=lambda r: (
            r["source_sj_id"],
            r["source_po_id"],
            r["source_po_line_id"],
            r["line_idx"],
            r["line_id"],
        )
    )

    charges_out: List[dict] = []
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

    source_po_ids = sorted(
        {_norm_scalar(x) for x in (payload.get("source_po_ids") or []) if _norm_scalar(x)}
    )
    source_sj_ids = sorted(
        {_norm_scalar(x) for x in (payload.get("source_sj_ids") or []) if _norm_scalar(x)}
    )
    source_mode = _norm_scalar(payload.get("source_mode")).lower()
    if not source_mode:
        source_mode = "sj" if source_sj_ids else "po"

    body = {
        "source_mode": source_mode,
        "po_id": _norm_scalar(payload.get("po_id")),
        "source_po_ids": source_po_ids,
        "source_sj_ids": source_sj_ids,
        "invoice_date": _norm_scalar(payload.get("invoice_date"))[:10],
        "payment_term_days": _norm_money_or_zero(payload.get("payment_term_days")),
        "discount_amount": _norm_money_or_zero(payload.get("discount_amount")),
        "ppn_percent": _norm_money_or_zero(payload.get("ppn_percent")),
        "notes": _norm_scalar(payload.get("notes")),
        "lines": lines_out,
        "other_charges": charges_out,
        "finalize_on_save": bool(payload.get("finalize_on_save")),
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _index_key_fields(meta: dict) -> List[str]:
    keys = (meta or {}).get("key")
    if keys is None:
        return []
    out: List[str] = []
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
            return []
    return out


def _partial_string_type(partial: Any) -> bool:
    if not isinstance(partial, dict):
        return False
    if partial == INVOICE_INDEX_PARTIAL:
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


async def sales_invoice_idempotency_index_ready(db) -> bool:
    specs = await _list_index_specs(db.sales_invoices)
    if specs is None:
        return False
    for meta in specs:
        if not meta.get("unique"):
            continue
        if _index_key_fields(meta) != ["idempotency_key"]:
            continue
        if _partial_string_type(meta.get("partialFilterExpression")):
            return True
    return False


async def create_intent_index_ready(db) -> bool:
    specs = await _list_index_specs(db[INTENT_COLL])
    if specs is None:
        return False
    for meta in specs:
        if not meta.get("unique"):
            continue
        if _index_key_fields(meta) == ["idempotency_key"]:
            return True
    return False


async def find_duplicate_idempotency_keys(db, *, sample_limit: int = 20) -> List[str]:
    """Return sample duplicate non-empty string idempotency_keys (read-only)."""
    pipeline = [
        {"$match": {"idempotency_key": {"$type": "string", "$ne": ""}}},
        {"$group": {"_id": "$idempotency_key", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$limit": int(sample_limit)},
    ]
    try:
        rows = await db.sales_invoices.aggregate(pipeline).to_list(length=sample_limit)
    except Exception:
        return ["__aggregate_failed__"]
    return [str(r.get("_id") or "") for r in rows if str(r.get("_id") or "").strip()]


async def ensure_sales_invoice_create_idempotency_indexes(db) -> Dict[str, Any]:
    """Create intent + invoice partial unique indexes when safe.

    Fail-closed: refuses invoice index creation when duplicate string keys exist.
    Idempotent. Safe for local/dummy DBs. Production must still plan index deploy.
    """
    report: Dict[str, Any] = {
        "intent_ready": False,
        "invoice_ready": False,
        "duplicate_keys": [],
        "created": [],
        "errors": [],
    }
    # Intent unique index — empty collection OK.
    if not await create_intent_index_ready(db):
        try:
            await db[INTENT_COLL].create_index(
                "idempotency_key",
                unique=True,
                name=INTENT_INDEX_NAME,
            )
            report["created"].append(INTENT_INDEX_NAME)
        except OperationFailure as exc:
            if not await create_intent_index_ready(db):
                report["errors"].append(f"intent_index:{exc}")
        except Exception as exc:
            if not await create_intent_index_ready(db):
                report["errors"].append(f"intent_index:{exc}")
    report["intent_ready"] = await create_intent_index_ready(db)

    dups = await find_duplicate_idempotency_keys(db)
    report["duplicate_keys"] = dups
    if dups:
        report["errors"].append(ERR_DUPLICATE_LEGACY_KEYS)
        report["invoice_ready"] = await sales_invoice_idempotency_index_ready(db)
        return report

    if not await sales_invoice_idempotency_index_ready(db):
        try:
            await db.sales_invoices.create_index(
                "idempotency_key",
                unique=True,
                name=INVOICE_INDEX_NAME,
                partialFilterExpression=INVOICE_INDEX_PARTIAL,
            )
            report["created"].append(INVOICE_INDEX_NAME)
        except OperationFailure as exc:
            if not await sales_invoice_idempotency_index_ready(db):
                report["errors"].append(f"invoice_index:{exc}")
        except Exception as exc:
            if not await sales_invoice_idempotency_index_ready(db):
                report["errors"].append(f"invoice_index:{exc}")
    report["invoice_ready"] = await sales_invoice_idempotency_index_ready(db)
    return report


def _raise_index_prerequisite(detail: Optional[str] = None) -> None:
    raise HTTPException(
        503,
        {
            "code": ERR_INDEX_PREREQUISITE,
            "message": detail
            or (
                "Idempotency index belum siap. Tidak dapat membuat Faktur dengan "
                "idempotency_key hingga unique partial index aktif."
            ),
        },
    )


def _raise_payload_conflict() -> None:
    raise HTTPException(
        409,
        {
            "code": ERR_PAYLOAD_CONFLICT,
            "message": (
                "idempotency_key sudah dipakai untuk payload Faktur yang berbeda."
            ),
        },
    )


def _raise_concurrent() -> None:
    raise HTTPException(
        409,
        {
            "code": ERR_CONCURRENT,
            "message": (
                "Pembuatan Faktur dengan idempotency_key ini sedang diproses. "
                "Ulangi permintaan yang sama."
            ),
        },
    )


async def _get_intent(db, key: str) -> Optional[dict]:
    return await db[INTENT_COLL].find_one({"idempotency_key": key}, {"_id": 0})


def _invoice_is_provisional(inv: Optional[dict]) -> bool:
    if not isinstance(inv, dict):
        return False
    return inv.get("billing_commit_pending") is True


async def _find_invoice_by_key(db, key: str, *, operational_only: bool = False) -> Optional[dict]:
    query: Dict[str, Any] = {"idempotency_key": key}
    if operational_only:
        query["billing_commit_pending"] = {"$ne": True}
    return await db.sales_invoices.find_one(query, {"_id": 0})


def _fingerprint_conflict(stored: Optional[str], expected: str) -> bool:
    stored_s = str(stored or "").strip()
    if not stored_s:
        return False
    return stored_s != expected


async def _await_sibling_invoice(db, key: str, fingerprint: str) -> Optional[dict]:
    """Wait for sibling operational invoice (billing committed). Never return provisional."""
    deadline = asyncio.get_event_loop().time() + _FOLLOWER_WAIT_SECONDS
    while asyncio.get_event_loop().time() < deadline:
        intent = await _get_intent(db, key)
        if intent and _fingerprint_conflict(intent.get("fingerprint"), fingerprint):
            _raise_payload_conflict()
        inv = await _find_invoice_by_key(db, key, operational_only=True)
        if inv:
            if _fingerprint_conflict(inv.get(FINGERPRINT_FIELD), fingerprint):
                _raise_payload_conflict()
            return inv
        # Any non-operational row still committing — keep waiting.
        pending = await _find_invoice_by_key(db, key, operational_only=False)
        if pending and _fingerprint_conflict(pending.get(FINGERPRINT_FIELD), fingerprint):
            _raise_payload_conflict()
        if intent:
            iid = str(intent.get("invoice_id") or "").strip()
            if iid:
                inv2 = await db.sales_invoices.find_one(
                    {"id": iid, "billing_commit_pending": {"$ne": True}},
                    {"_id": 0},
                )
                if inv2:
                    if _fingerprint_conflict(inv2.get(FINGERPRINT_FIELD), fingerprint):
                        _raise_payload_conflict()
                    return inv2
        await asyncio.sleep(_FOLLOWER_POLL_INTERVAL)
    return None


async def begin_idempotent_create(
    db, payload: Dict[str, Any], user: dict
) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
    """Start durable create ownership.

    Returns (existing_invoice_or_None, idempotency_key_or_None, fingerprint_or_None).
    When key is empty: (None, None, None) — caller proceeds without guards.
    When existing invoice found for same key+fingerprint: returns that invoice.
    When this caller owns the intent: (None, key, fingerprint) to proceed with create.
    """
    key = normalize_idempotency_key(payload.get("idempotency_key"))
    if not key:
        return None, None, None

    report = await ensure_sales_invoice_create_idempotency_indexes(db)
    if not (report.get("intent_ready") and report.get("invoice_ready")):
        if report.get("duplicate_keys"):
            _raise_index_prerequisite(
                "Terdapat idempotency_key duplikat pada data existing. "
                "Unique index tidak dapat diaktifkan secara aman."
            )
        _raise_index_prerequisite()

    fingerprint = create_payload_fingerprint(payload)

    existing = await _find_invoice_by_key(db, key, operational_only=True)
    if existing:
        if _fingerprint_conflict(existing.get(FINGERPRINT_FIELD), fingerprint):
            _raise_payload_conflict()
        intent = await _get_intent(db, key)
        if intent and _fingerprint_conflict(intent.get("fingerprint"), fingerprint):
            _raise_payload_conflict()
        return existing, key, fingerprint

    # Sibling may be mid billing-commit — wait rather than starting a second create.
    pending = await _find_invoice_by_key(db, key, operational_only=False)
    if pending and _invoice_is_provisional(pending):
        if _fingerprint_conflict(pending.get(FINGERPRINT_FIELD), fingerprint):
            _raise_payload_conflict()
        sibling = await _await_sibling_invoice(db, key, fingerprint)
        if sibling:
            return sibling, key, fingerprint
        _raise_concurrent()

    intent_doc = {
        "idempotency_key": key,
        "fingerprint": fingerprint,
        "actor_id": str((user or {}).get("id") or (user or {}).get("_id") or "").strip(),
        "actor_email": str((user or {}).get("email") or "").strip(),
        "invoice_id": None,
        "status": "preparing",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    try:
        await db[INTENT_COLL].insert_one(intent_doc)
        intent_doc.pop("_id", None)
        return None, key, fingerprint
    except DuplicateKeyError:
        pass
    except Exception:
        # Unique index race / network — re-read
        pass

    # Follower path
    again = await _get_intent(db, key)
    if again and _fingerprint_conflict(again.get("fingerprint"), fingerprint):
        _raise_payload_conflict()
    sibling = await _await_sibling_invoice(db, key, fingerprint)
    if sibling:
        return sibling, key, fingerprint

    # Crash-after-claim recovery: reclaim preparing intent (same fingerprint, no invoice).
    from pymongo import ReturnDocument

    reclaimed = await db[INTENT_COLL].find_one_and_update(
        {
            "idempotency_key": key,
            "fingerprint": fingerprint,
            "status": "preparing",
            "$or": [
                {"invoice_id": None},
                {"invoice_id": {"$exists": False}},
                {"invoice_id": ""},
            ],
        },
        {
            "$set": {
                "updated_at": now_iso(),
                "reclaimed_at": now_iso(),
                "actor_id": str((user or {}).get("id") or (user or {}).get("_id") or "").strip(),
                "actor_email": str((user or {}).get("email") or "").strip(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if reclaimed:
        reclaimed.pop("_id", None)
        return None, key, fingerprint

    # Another worker still owns an active create — fail-closed.
    _raise_concurrent()
    return None, key, fingerprint  # pragma: no cover


async def bind_idempotent_create_invoice(
    db, *, idempotency_key: str, fingerprint: str, invoice_id: str
) -> None:
    """Bind intent to created invoice id (CAS). Idempotent if already bound."""
    key = normalize_idempotency_key(idempotency_key)
    iid = str(invoice_id or "").strip()
    if not key or not iid:
        return
    now = now_iso()
    result = await db[INTENT_COLL].update_one(
        {
            "idempotency_key": key,
            "fingerprint": fingerprint,
            "$or": [
                {"invoice_id": None},
                {"invoice_id": {"$exists": False}},
                {"invoice_id": ""},
            ],
        },
        {"$set": {"invoice_id": iid, "status": "bound", "updated_at": now}},
    )
    if int(getattr(result, "matched_count", 0) or 0) == 1:
        return
    current = await _get_intent(db, key)
    if not current:
        return
    if _fingerprint_conflict(current.get("fingerprint"), fingerprint):
        _raise_payload_conflict()
    existing_id = str(current.get("invoice_id") or "").strip()
    if existing_id and existing_id != iid:
        # Another worker won — caller must discard own provisional work.
        raise HTTPException(
            409,
            {
                "code": ERR_CONCURRENT,
                "message": "Operasi idempotency sudah terikat ke Faktur lain.",
                "invoice_id": existing_id,
            },
        )
    await db[INTENT_COLL].update_one(
        {"idempotency_key": key},
        {"$set": {"invoice_id": iid, "status": "bound", "updated_at": now}},
    )


def stamp_invoice_fingerprint(invoice_doc: dict, fingerprint: Optional[str]) -> None:
    if fingerprint and isinstance(invoice_doc, dict):
        invoice_doc[FINGERPRINT_FIELD] = fingerprint


async def resolve_duplicate_key_invoice(
    db, *, idempotency_key: str, fingerprint: Optional[str]
) -> Optional[dict]:
    key = normalize_idempotency_key(idempotency_key)
    if not key:
        return None
    existing = await _find_invoice_by_key(db, key, operational_only=True)
    if not existing and fingerprint:
        existing = await _await_sibling_invoice(db, key, fingerprint)
    if not existing:
        existing = await _find_invoice_by_key(db, key, operational_only=False)
    if not existing:
        return None
    if fingerprint and _fingerprint_conflict(existing.get(FINGERPRINT_FIELD), fingerprint):
        _raise_payload_conflict()
    if _invoice_is_provisional(existing):
        # Never hand a provisional invoice to finalize/create_and_finalize.
        if fingerprint:
            waited = await _await_sibling_invoice(db, key, fingerprint)
            if waited:
                existing = waited
            else:
                _raise_concurrent()
        else:
            _raise_concurrent()
    if fingerprint:
        try:
            await bind_idempotent_create_invoice(
                db,
                idempotency_key=key,
                fingerprint=fingerprint,
                invoice_id=str(existing.get("id") or ""),
            )
        except HTTPException:
            raise
        except Exception:
            _logger.exception("bind after duplicate key failed")
    return existing
