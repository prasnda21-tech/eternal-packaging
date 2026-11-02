"""Canonical PO-line billing allocations for Flexible Sales Invoice (Model C).

Lifecycle (adapted from Stage 1 SJ allocation — not a blind copy):
  reserved (+ lease) → invoice insert with billing_commit_pending
  → commit claims → activate (clear pending)

Draft edit rebalance adjusts committed claims atomically with compensation.
Standalone Mongo uses conditional updates; multi-step failures roll back.
"""
from __future__ import annotations

import logging
import time
import uuid as uuid_mod
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException

BILLING_COUNTERS = "sales_invoice_line_billing_counters"
SJ_BILLING_COUNTERS = "sales_invoice_sj_line_billing_counters"

CLAIM_STATE_RESERVED = "reserved"
CLAIM_STATE_COMMITTED = "committed"
# Legacy Stage-2-early claims used "active" — treat as committed.
CLAIM_ACTIVE = "active"
CLAIM_RELEASED = "released"

BILLING_LEASE_SECONDS = 90
BILLING_COMMIT_PENDING_FIELD = "billing_commit_pending"
RECOVERY_FENCE_FIELD = "billing_recovery_fence"

SOURCE_MODE_PO = "po"
SOURCE_MODE_SJ = "sj"

PENDING_NOT_OPERATIONAL = (
    "Faktur masih provisional (billing belum di-commit). Tidak operasional."
)
PENDING_NOT_FOUND_OR_PROVISIONAL = "Faktur Penjualan tidak ditemukan."

_logger = logging.getLogger(__name__)


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _now_epoch() -> float:
    return time.time()


def _now_iso_safe() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def po_billing_counter_id(source_po_id: str, source_line_id: str) -> str:
    return f"{_norm(source_po_id)}::{_norm(source_line_id)}"


def sj_billing_counter_id(source_sj_id: str, source_po_id: str, source_line_id: str) -> str:
    return f"{_norm(source_sj_id)}::{_norm(source_po_id)}::{_norm(source_line_id)}"


def _po_counters(db):
    return getattr(db, BILLING_COUNTERS)


def _sj_counters(db):
    return getattr(db, SJ_BILLING_COUNTERS)


def _claim_path(invoice_id: str) -> str:
    sid = _norm(invoice_id)
    if not sid or "." in sid or "$" in sid:
        raise HTTPException(400, "invoice_id alokasi penagihan tidak valid.")
    return f"claims_by_invoice.{sid}"


def _claim_record_qty(prior: Any) -> int:
    if isinstance(prior, dict):
        return _as_int(prior.get("qty"))
    return _as_int(prior)


def _claim_is_reserved(prior: Any) -> bool:
    return isinstance(prior, dict) and str(prior.get("state") or "") == CLAIM_STATE_RESERVED


def _claim_is_committed(prior: Any) -> bool:
    if prior is None:
        return False
    if isinstance(prior, dict):
        state = str(prior.get("state") or "")
        return state in (CLAIM_STATE_COMMITTED, CLAIM_ACTIVE)
    return True


def _new_reservation_record(qty: int, *, lease_token: str, now: Optional[float] = None) -> dict:
    ts = float(now if now is not None else _now_epoch())
    return {
        "qty": int(qty),
        "state": CLAIM_STATE_RESERVED,
        "lease_token": _norm(lease_token) or str(uuid_mod.uuid4()),
        "lease_until": ts + float(BILLING_LEASE_SECONDS),
        "reserved_at": ts,
    }


def invoice_is_billing_pending(invoice: Optional[dict]) -> bool:
    return bool(isinstance(invoice, dict) and invoice.get(BILLING_COMMIT_PENDING_FIELD) is True)


def invoice_is_operationally_active(invoice: Optional[dict]) -> bool:
    if not isinstance(invoice, dict):
        return False
    if invoice.get(BILLING_COMMIT_PENDING_FIELD) is True:
        return False
    if invoice.get(RECOVERY_FENCE_FIELD) is True:
        return False
    status = str(invoice.get("financial_status") or "draft").strip().lower()
    return status != "cancelled"


async def ensure_billing_counter_indexes(db) -> None:
    try:
        await _po_counters(db).create_index("id", unique=True)
        await _sj_counters(db).create_index("id", unique=True)
    except Exception:
        _logger.warning("billing counter index ensure failed", exc_info=True)


async def _seed_po_invoiced_from_invoices(
    db, *, source_po_id: str, source_line_id: str, source_line_idx: int
) -> int:
    """Sum operational invoice qty for this PO line (excludes provisional pending)."""
    po = _norm(source_po_id)
    lid = _norm(source_line_id)
    idx = int(source_line_idx)
    total = 0
    cursor = db.sales_invoices.find(
        {
            "$or": [
                {"po_id": po},
                {"source_po_ids": po},
                {"lines.source_po_id": po},
            ],
            "financial_status": {"$nin": ["cancelled"]},
            BILLING_COMMIT_PENDING_FIELD: {"$ne": True},
        },
        {"_id": 0, "po_id": 1, "lines": 1, "financial_status": 1, "source_po_ids": 1},
    )
    async for inv in cursor:
        status = str(inv.get("financial_status") or "draft").strip().lower()
        if status == "cancelled":
            continue
        for line in inv.get("lines") or []:
            if not isinstance(line, dict):
                continue
            line_po = _norm(line.get("source_po_id") or inv.get("po_id"))
            if line_po != po:
                continue
            line_lid = _norm(line.get("source_po_line_id") or line.get("line_id"))
            try:
                line_idx = int(line.get("source_po_line_idx", line.get("line_idx", -1)))
            except (TypeError, ValueError):
                line_idx = -1
            if lid and line_lid and line_lid != lid:
                if line_idx != idx:
                    continue
            elif line_idx != idx and line_lid != lid:
                continue
            total += _as_int(line.get("quantity"))
    return total


async def _ensure_po_counter(
    db, *, source_po_id: str, source_line_id: str, source_line_idx: int, ordered_qty: int
) -> None:
    cid = po_billing_counter_id(source_po_id, source_line_id)
    existing = await _po_counters(db).find_one({"id": cid}, {"_id": 0, "id": 1})
    if existing:
        return
    seeded = await _seed_po_invoiced_from_invoices(
        db,
        source_po_id=source_po_id,
        source_line_id=source_line_id,
        source_line_idx=source_line_idx,
    )
    await _po_counters(db).update_one(
        {"id": cid},
        {
            "$setOnInsert": {
                "id": cid,
                "source_po_id": _norm(source_po_id),
                "source_line_id": _norm(source_line_id),
                "source_line_idx": int(source_line_idx),
                "ordered_qty": int(ordered_qty),
                "invoiced_qty": int(seeded),
                "claims_by_invoice": {},
                "updated_at": "",
            }
        },
        upsert=True,
    )


async def heal_expired_billing_reservations_for_po_line(
    db, *, source_po_id: str, source_line_id: str
) -> List[dict]:
    """Release expired reserved claims with no invoice doc; promote if invoice exists."""
    cid = po_billing_counter_id(source_po_id, source_line_id)
    cur = await _po_counters(db).find_one({"id": cid}, {"_id": 0})
    if not cur:
        return []
    now = _now_epoch()
    out: List[dict] = []
    claims = cur.get("claims_by_invoice") or {}
    for iid, prior in list(claims.items()):
        if not _claim_is_reserved(prior):
            continue
        lease_until = float((prior or {}).get("lease_until") or 0)
        if lease_until >= now:
            continue
        inv = await db.sales_invoices.find_one({"id": iid}, {"_id": 0, "id": 1, BILLING_COMMIT_PENDING_FIELD: 1})
        if inv is not None:
            # Crash after insert — promote reserved → committed (activate handled separately).
            await commit_po_line_billing(
                db,
                source_po_id=source_po_id,
                source_line_id=source_line_id,
                invoice_id=iid,
                lease_token=str((prior or {}).get("lease_token") or ""),
                allow_missing_token=True,
            )
            out.append({"invoice_id": iid, "action": "promoted_committed"})
        else:
            released = await release_po_line_billing(
                db, source_po_id=source_po_id, source_line_id=source_line_id, invoice_id=iid
            )
            out.append({"invoice_id": iid, "action": "released_orphan", **released})
    return out


async def claim_po_line_billing(
    db,
    *,
    source_po_id: str,
    source_line_id: str,
    source_line_idx: int,
    ordered_qty: int,
    qty: int,
    invoice_id: str,
    lease_token: Optional[str] = None,
) -> dict:
    """Atomically reserve invoice qty against a PO line (state=reserved + lease)."""
    if qty <= 0:
        raise HTTPException(400, "Qty penagihan harus lebih besar dari 0.")
    iid = _norm(invoice_id)
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    if not iid or not source_po_id or not source_line_id:
        raise HTTPException(400, "Identitas alokasi penagihan PO tidak lengkap.")

    await ensure_billing_counter_indexes(db)
    await _ensure_po_counter(
        db,
        source_po_id=source_po_id,
        source_line_id=source_line_id,
        source_line_idx=int(source_line_idx),
        ordered_qty=int(ordered_qty),
    )
    await heal_expired_billing_reservations_for_po_line(
        db, source_po_id=source_po_id, source_line_id=source_line_id
    )

    cid = po_billing_counter_id(source_po_id, source_line_id)
    claim_path = _claim_path(iid)
    token = _norm(lease_token) or str(uuid_mod.uuid4())
    cur = await _po_counters(db).find_one({"id": cid}, {"_id": 0})
    prior = ((cur or {}).get("claims_by_invoice") or {}).get(iid)
    if prior is not None:
        prior_qty = _claim_record_qty(prior)
        if prior_qty != int(qty):
            raise HTTPException(
                409,
                f"Alokasi penagihan retry bentrok: Faktur sudah claim {prior_qty}, permintaan {qty}.",
            )
        if _claim_is_reserved(prior):
            renewed = await renew_billing_lease(
                db,
                source_po_id=source_po_id,
                source_line_id=source_line_id,
                invoice_id=iid,
                lease_token=str(prior.get("lease_token") or token),
                kind="po",
            )
            return {
                "counter_id": cid,
                "source_po_id": source_po_id,
                "source_line_id": source_line_id,
                "qty": prior_qty,
                "invoice_id": iid,
                "lease_token": renewed.get("lease_token") or prior.get("lease_token") or token,
                "replayed": True,
                "state": CLAIM_STATE_RESERVED,
            }
        return {
            "counter_id": cid,
            "source_po_id": source_po_id,
            "source_line_id": source_line_id,
            "qty": prior_qty,
            "invoice_id": iid,
            "lease_token": str((prior.get("lease_token") if isinstance(prior, dict) else "") or ""),
            "replayed": True,
            "state": CLAIM_STATE_COMMITTED,
        }

    record = _new_reservation_record(int(qty), lease_token=token)
    cap = int(ordered_qty) - int(qty)
    from pymongo import ReturnDocument
    result = await _po_counters(db).find_one_and_update(
        {
            "id": cid,
            "invoiced_qty": {"$lte": cap},
            claim_path: {"$exists": False},
        },
        {
            "$inc": {"invoiced_qty": int(qty)},
            "$set": {
                "ordered_qty": int(ordered_qty),
                "source_line_idx": int(source_line_idx),
                claim_path: record,
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        cur = await _po_counters(db).find_one({"id": cid}, {"_id": 0})
        prior = ((cur or {}).get("claims_by_invoice") or {}).get(iid)
        if prior is not None and _claim_record_qty(prior) == int(qty):
            return {
                "counter_id": cid,
                "source_po_id": source_po_id,
                "source_line_id": source_line_id,
                "qty": int(qty),
                "invoice_id": iid,
                "lease_token": str((prior.get("lease_token") if isinstance(prior, dict) else "") or token),
                "replayed": True,
                "state": CLAIM_STATE_RESERVED if _claim_is_reserved(prior) else CLAIM_STATE_COMMITTED,
            }
        remain = max(0, int(ordered_qty) - int((cur or {}).get("invoiced_qty") or 0))
        raise HTTPException(
            409,
            f"Alokasi penagihan gagal: sisa invoiceable {remain} (permintaan {qty}).",
        )
    return {
        "counter_id": cid,
        "source_po_id": source_po_id,
        "source_line_id": source_line_id,
        "qty": int(qty),
        "invoice_id": iid,
        "lease_token": token,
        "replayed": False,
        "state": CLAIM_STATE_RESERVED,
        "lease_until": record["lease_until"],
    }


async def claim_sj_line_billing(
    db,
    *,
    source_sj_id: str,
    source_po_id: str,
    source_line_id: str,
    sj_line_qty: int,
    qty: int,
    invoice_id: str,
    lease_token: Optional[str] = None,
) -> dict:
    """Second cap for SJ mode — reserved with lease until commit."""
    if qty <= 0:
        raise HTTPException(400, "Qty penagihan SJ harus lebih besar dari 0.")
    iid = _norm(invoice_id)
    source_sj_id = _norm(source_sj_id)
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    if not iid or not source_sj_id or not source_po_id or not source_line_id:
        raise HTTPException(400, "Identitas alokasi penagihan SJ tidak lengkap.")

    await ensure_billing_counter_indexes(db)
    cid = sj_billing_counter_id(source_sj_id, source_po_id, source_line_id)
    claim_path = _claim_path(iid)
    token = _norm(lease_token) or str(uuid_mod.uuid4())
    await _sj_counters(db).update_one(
        {"id": cid},
        {
            "$setOnInsert": {
                "id": cid,
                "source_sj_id": source_sj_id,
                "source_po_id": source_po_id,
                "source_line_id": source_line_id,
                "sj_line_qty": int(sj_line_qty),
                "invoiced_qty": 0,
                "claims_by_invoice": {},
            }
        },
        upsert=True,
    )
    cur = await _sj_counters(db).find_one({"id": cid}, {"_id": 0})
    prior = ((cur or {}).get("claims_by_invoice") or {}).get(iid)
    if prior is not None:
        prior_qty = _claim_record_qty(prior)
        if prior_qty != int(qty):
            raise HTTPException(409, "Alokasi penagihan SJ retry bentrok qty.")
        if _claim_is_reserved(prior):
            renewed = await renew_billing_lease(
                db,
                source_po_id=source_po_id,
                source_line_id=source_line_id,
                invoice_id=iid,
                lease_token=str(prior.get("lease_token") or token),
                kind="sj",
                source_sj_id=source_sj_id,
            )
            return {
                "counter_id": cid,
                "source_sj_id": source_sj_id,
                "source_po_id": source_po_id,
                "source_line_id": source_line_id,
                "qty": prior_qty,
                "invoice_id": iid,
                "lease_token": renewed.get("lease_token") or prior.get("lease_token") or token,
                "replayed": True,
                "state": CLAIM_STATE_RESERVED,
            }
        return {
            "counter_id": cid,
            "source_sj_id": source_sj_id,
            "source_po_id": source_po_id,
            "source_line_id": source_line_id,
            "qty": prior_qty,
            "invoice_id": iid,
            "lease_token": str((prior.get("lease_token") if isinstance(prior, dict) else "") or ""),
            "replayed": True,
            "state": CLAIM_STATE_COMMITTED,
        }

    record = _new_reservation_record(int(qty), lease_token=token)
    cap = int(sj_line_qty) - int(qty)
    from pymongo import ReturnDocument
    result = await _sj_counters(db).find_one_and_update(
        {
            "id": cid,
            "invoiced_qty": {"$lte": cap},
            claim_path: {"$exists": False},
        },
        {
            "$inc": {"invoiced_qty": int(qty)},
            "$set": {
                "sj_line_qty": int(sj_line_qty),
                claim_path: record,
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        cur = await _sj_counters(db).find_one({"id": cid}, {"_id": 0})
        remain = max(0, int(sj_line_qty) - int((cur or {}).get("invoiced_qty") or 0))
        raise HTTPException(
            409,
            f"Alokasi penagihan SJ gagal: sisa pada SJ {remain} (permintaan {qty}).",
        )
    return {
        "counter_id": cid,
        "source_sj_id": source_sj_id,
        "source_po_id": source_po_id,
        "source_line_id": source_line_id,
        "qty": int(qty),
        "invoice_id": iid,
        "lease_token": token,
        "replayed": False,
        "state": CLAIM_STATE_RESERVED,
        "lease_until": record["lease_until"],
    }


async def renew_billing_lease(
    db,
    *,
    source_po_id: str,
    source_line_id: str,
    invoice_id: str,
    lease_token: str,
    kind: str = "po",
    source_sj_id: str = "",
) -> dict:
    """Extend lease for an in-flight reserved claim. Fail-closed if ownership lost."""
    iid = _norm(invoice_id)
    token = _norm(lease_token)
    if not iid or not token:
        raise HTTPException(409, "Kepemilikan alokasi penagihan hilang (lease tidak valid).")
    now = _now_epoch()
    new_until = now + float(BILLING_LEASE_SECONDS)
    claim_path = _claim_path(iid)
    if kind == "sj":
        cid = sj_billing_counter_id(source_sj_id, source_po_id, source_line_id)
        coll = _sj_counters(db)
    else:
        cid = po_billing_counter_id(source_po_id, source_line_id)
        coll = _po_counters(db)
    from pymongo import ReturnDocument
    result = await coll.find_one_and_update(
        {
            "id": cid,
            f"{claim_path}.state": CLAIM_STATE_RESERVED,
            f"{claim_path}.lease_token": token,
            f"{claim_path}.lease_until": {"$gte": now},
        },
        {"$set": {f"{claim_path}.lease_until": new_until}},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise HTTPException(
            409,
            "Kepemilikan alokasi penagihan hilang (lease habis atau diganti). Fail-closed.",
        )
    prior = ((result.get("claims_by_invoice") or {}).get(iid) or {})
    return {
        "lease_token": token,
        "lease_until": float(prior.get("lease_until") or new_until),
        "invoice_id": iid,
    }


async def require_reserved_billing_claims(db, claims: Sequence[dict]) -> None:
    """Before persist: prove this worker still owns every reserved claim."""
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        if str(claim.get("state") or "") != CLAIM_STATE_RESERVED:
            continue
        kind = str(claim.get("kind") or "po")
        await renew_billing_lease(
            db,
            source_po_id=str(claim.get("source_po_id") or ""),
            source_line_id=str(claim.get("source_line_id") or ""),
            invoice_id=str(claim.get("invoice_id") or ""),
            lease_token=str(claim.get("lease_token") or ""),
            kind=kind,
            source_sj_id=str(claim.get("source_sj_id") or ""),
        )


async def commit_po_line_billing(
    db,
    *,
    source_po_id: str,
    source_line_id: str,
    invoice_id: str,
    lease_token: str = "",
    allow_missing_token: bool = False,
) -> dict:
    iid = _norm(invoice_id)
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    token = _norm(lease_token)
    cid = po_billing_counter_id(source_po_id, source_line_id)
    claim_path = _claim_path(iid)
    cur = await _po_counters(db).find_one({"id": cid}, {"_id": 0})
    prior = ((cur or {}).get("claims_by_invoice") or {}).get(iid)
    if prior is None:
        raise HTTPException(
            409,
            "Commit alokasi penagihan gagal: claim tidak ditemukan (lease hilang/di-heal). Fail-closed.",
        )
    if _claim_is_committed(prior):
        return {
            "committed": True,
            "replayed": True,
            "qty": _claim_record_qty(prior),
            "invoice_id": iid,
        }
    if not _claim_is_reserved(prior):
        raise HTTPException(409, "Commit alokasi penagihan ditolak: state claim tidak valid.")
    if token and str(prior.get("lease_token") or "") != token and not allow_missing_token:
        raise HTTPException(409, "Commit alokasi penagihan ditolak: lease_token tidak cocok.")
    if not token and not allow_missing_token:
        raise HTTPException(409, "Commit alokasi penagihan ditolak: lease_token wajib.")

    qty = _claim_record_qty(prior)
    committed = {
        "qty": qty,
        "state": CLAIM_STATE_COMMITTED,
        "lease_token": str(prior.get("lease_token") or token or ""),
        "lease_until": float(prior.get("lease_until") or 0),
        "committed_at": _now_epoch(),
    }
    match: Dict[str, Any] = {
        "id": cid,
        f"{claim_path}.state": CLAIM_STATE_RESERVED,
    }
    if token and not allow_missing_token:
        match[f"{claim_path}.lease_token"] = token
    from pymongo import ReturnDocument
    result = await _po_counters(db).find_one_and_update(
        match,
        {"$set": {claim_path: committed}},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        cur2 = await _po_counters(db).find_one({"id": cid}, {"_id": 0})
        prior2 = ((cur2 or {}).get("claims_by_invoice") or {}).get(iid)
        if _claim_is_committed(prior2):
            return {"committed": True, "replayed": True, "qty": _claim_record_qty(prior2), "invoice_id": iid}
        raise HTTPException(409, "Commit alokasi penagihan gagal: lost race. Fail-closed.")
    return {"committed": True, "replayed": False, "qty": qty, "invoice_id": iid}


async def commit_sj_line_billing(
    db,
    *,
    source_sj_id: str,
    source_po_id: str,
    source_line_id: str,
    invoice_id: str,
    lease_token: str = "",
    allow_missing_token: bool = False,
) -> dict:
    iid = _norm(invoice_id)
    source_sj_id = _norm(source_sj_id)
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    token = _norm(lease_token)
    cid = sj_billing_counter_id(source_sj_id, source_po_id, source_line_id)
    claim_path = _claim_path(iid)
    cur = await _sj_counters(db).find_one({"id": cid}, {"_id": 0})
    prior = ((cur or {}).get("claims_by_invoice") or {}).get(iid)
    if prior is None:
        raise HTTPException(409, "Commit alokasi SJ gagal: claim tidak ditemukan. Fail-closed.")
    if _claim_is_committed(prior):
        return {"committed": True, "replayed": True, "qty": _claim_record_qty(prior), "invoice_id": iid}
    if not _claim_is_reserved(prior):
        raise HTTPException(409, "Commit alokasi SJ ditolak: state claim tidak valid.")
    if token and str(prior.get("lease_token") or "") != token and not allow_missing_token:
        raise HTTPException(409, "Commit alokasi SJ ditolak: lease_token tidak cocok.")
    qty = _claim_record_qty(prior)
    committed = {
        "qty": qty,
        "state": CLAIM_STATE_COMMITTED,
        "lease_token": str(prior.get("lease_token") or token or ""),
        "lease_until": float(prior.get("lease_until") or 0),
        "committed_at": _now_epoch(),
    }
    match: Dict[str, Any] = {"id": cid, f"{claim_path}.state": CLAIM_STATE_RESERVED}
    if token and not allow_missing_token:
        match[f"{claim_path}.lease_token"] = token
    from pymongo import ReturnDocument
    result = await _sj_counters(db).find_one_and_update(
        match,
        {"$set": {claim_path: committed}},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        cur2 = await _sj_counters(db).find_one({"id": cid}, {"_id": 0})
        prior2 = ((cur2 or {}).get("claims_by_invoice") or {}).get(iid)
        if _claim_is_committed(prior2):
            return {"committed": True, "replayed": True, "qty": _claim_record_qty(prior2), "invoice_id": iid}
        raise HTTPException(409, "Commit alokasi SJ gagal: lost race. Fail-closed.")
    return {"committed": True, "replayed": False, "qty": qty, "invoice_id": iid}


async def commit_claimed_billings(db, claims: Sequence[dict]) -> List[dict]:
    out: List[dict] = []
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        kind = str(claim.get("kind") or "po")
        if kind == "sj":
            out.append(
                await commit_sj_line_billing(
                    db,
                    source_sj_id=str(claim.get("source_sj_id") or ""),
                    source_po_id=str(claim.get("source_po_id") or ""),
                    source_line_id=str(claim.get("source_line_id") or ""),
                    invoice_id=str(claim.get("invoice_id") or ""),
                    lease_token=str(claim.get("lease_token") or ""),
                )
            )
        else:
            out.append(
                await commit_po_line_billing(
                    db,
                    source_po_id=str(claim.get("source_po_id") or ""),
                    source_line_id=str(claim.get("source_line_id") or ""),
                    invoice_id=str(claim.get("invoice_id") or ""),
                    lease_token=str(claim.get("lease_token") or ""),
                )
            )
    return out


async def activate_invoice_billing_commit(db, invoice_id: str, *, session=None) -> dict:
    """Clear provisional fence after claims are committed (idempotent)."""
    sid = _norm(invoice_id)
    if not sid:
        return {"activated": False, "reason": "missing_id"}
    kwargs = {}
    if session is not None:
        kwargs["session"] = session
    from pymongo import ReturnDocument
    result = await db.sales_invoices.find_one_and_update(
        {
            "id": sid,
            BILLING_COMMIT_PENDING_FIELD: True,
            RECOVERY_FENCE_FIELD: {"$ne": True},
        },
        {
            "$unset": {
                BILLING_COMMIT_PENDING_FIELD: "",
                RECOVERY_FENCE_FIELD: "",
                "recovery_started_at": "",
                "recovery_token": "",
            },
            "$set": {"updated_at": _now_iso_safe()},
        },
        return_document=ReturnDocument.AFTER,
        **kwargs,
    )
    if result is not None:
        return {"activated": True, "replayed": False, "id": sid}
    cur = await db.sales_invoices.find_one({"id": sid}, {"_id": 0}, **kwargs)
    if not cur:
        raise HTTPException(409, "Aktivasi Faktur gagal: dokumen tidak ditemukan. Fail-closed.")
    if cur.get(RECOVERY_FENCE_FIELD) is True:
        raise HTTPException(
            409,
            "Aktivasi Faktur ditolak: recovery orphan telah mengambil kepemilikan. Fail-closed.",
        )
    if invoice_is_billing_pending(cur):
        raise HTTPException(409, "Aktivasi Faktur gagal: state provisional tidak dapat di-clear.")
    return {"activated": True, "replayed": True, "id": sid}


async def release_po_line_billing(
    db, *, source_po_id: str, source_line_id: str, invoice_id: str, qty: int = 0
) -> dict:
    """Idempotent release gated by claims_by_invoice.<invoice_id>."""
    iid = _norm(invoice_id)
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    if not iid or not source_po_id or not source_line_id:
        return {"released": False, "replayed": True, "reason": "missing_identity"}
    cid = po_billing_counter_id(source_po_id, source_line_id)
    claim_path = _claim_path(iid)
    cur = await _po_counters(db).find_one({"id": cid}, {"_id": 0})
    if not cur:
        return {"released": False, "replayed": True, "reason": "no_counter"}
    prior = (cur.get("claims_by_invoice") or {}).get(iid)
    if prior is None:
        return {"released": False, "replayed": True, "reason": "already_released"}
    release_qty = _claim_record_qty(prior)
    if release_qty <= 0:
        await _po_counters(db).update_one(
            {"id": cid, claim_path: {"$exists": True}},
            {"$unset": {claim_path: ""}},
        )
        return {"released": False, "replayed": True, "reason": "zero_claim"}
    from pymongo import ReturnDocument
    result = await _po_counters(db).find_one_and_update(
        {
            "id": cid,
            claim_path: {"$exists": True},
            "invoiced_qty": {"$gte": release_qty},
        },
        {
            "$inc": {"invoiced_qty": -release_qty},
            "$unset": {claim_path: ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        cur2 = await _po_counters(db).find_one({"id": cid}, {"_id": 0})
        if cur2 and (cur2.get("claims_by_invoice") or {}).get(iid) is None:
            return {"released": False, "replayed": True, "reason": "lost_race"}
        raise HTTPException(503, f"Gagal melepas alokasi penagihan {cid} untuk Faktur {iid}.")
    return {"released": True, "qty": release_qty, "invoiced_qty": int(result.get("invoiced_qty") or 0)}


async def release_sj_line_billing(
    db,
    *,
    source_sj_id: str,
    source_po_id: str,
    source_line_id: str,
    invoice_id: str,
) -> dict:
    iid = _norm(invoice_id)
    source_sj_id = _norm(source_sj_id)
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    if not iid or not source_sj_id or not source_po_id or not source_line_id:
        return {"released": False, "replayed": True, "reason": "missing_identity"}
    cid = sj_billing_counter_id(source_sj_id, source_po_id, source_line_id)
    claim_path = _claim_path(iid)
    cur = await _sj_counters(db).find_one({"id": cid}, {"_id": 0})
    if not cur:
        return {"released": False, "replayed": True, "reason": "no_counter"}
    prior = (cur.get("claims_by_invoice") or {}).get(iid)
    if prior is None:
        return {"released": False, "replayed": True, "reason": "already_released"}
    release_qty = _claim_record_qty(prior)
    from pymongo import ReturnDocument
    result = await _sj_counters(db).find_one_and_update(
        {
            "id": cid,
            claim_path: {"$exists": True},
            "invoiced_qty": {"$gte": release_qty},
        },
        {"$inc": {"invoiced_qty": -release_qty}, "$unset": {claim_path: ""}},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        cur2 = await _sj_counters(db).find_one({"id": cid}, {"_id": 0})
        if cur2 and (cur2.get("claims_by_invoice") or {}).get(iid) is None:
            return {"released": False, "replayed": True, "reason": "already_released"}
        raise HTTPException(503, f"Gagal melepas alokasi penagihan SJ {cid}.")
    return {"released": True, "qty": release_qty}


async def release_invoice_billing_allocations(db, invoice: Optional[dict]) -> List[dict]:
    """Release every PO/SJ billing claim held by one invoice (idempotent)."""
    if not isinstance(invoice, dict):
        return []
    invoice_id = _norm(invoice.get("id"))
    if not invoice_id:
        return []
    out: List[dict] = []
    for line in invoice.get("lines") or []:
        if not isinstance(line, dict):
            continue
        po_id = _norm(line.get("source_po_id") or invoice.get("po_id"))
        line_id = _norm(line.get("source_po_line_id") or line.get("line_id"))
        if po_id and line_id:
            out.append(
                await release_po_line_billing(
                    db,
                    source_po_id=po_id,
                    source_line_id=line_id,
                    invoice_id=invoice_id,
                    qty=_as_int(line.get("quantity")),
                )
            )
        sj_id = _norm(line.get("source_sj_id"))
        if sj_id and po_id and line_id:
            out.append(
                await release_sj_line_billing(
                    db,
                    source_sj_id=sj_id,
                    source_po_id=po_id,
                    source_line_id=line_id,
                    invoice_id=invoice_id,
                )
            )
    return out


async def claim_invoice_line_billings(db, invoice_id: str, lines: Sequence[dict]) -> List[dict]:
    """Claim PO (+ optional SJ) capacity for all invoice lines. All-or-nothing reserved."""
    claims: List[dict] = []
    try:
        for line in lines or []:
            if not isinstance(line, dict):
                continue
            qty = _as_int(line.get("quantity"))
            if qty <= 0:
                continue
            po_claim = await claim_po_line_billing(
                db,
                source_po_id=str(line.get("source_po_id") or ""),
                source_line_id=str(line.get("source_po_line_id") or line.get("line_id") or ""),
                source_line_idx=_as_int(line.get("source_po_line_idx"), default=_as_int(line.get("line_idx"))),
                ordered_qty=_as_int(line.get("ordered_qty")),
                qty=qty,
                invoice_id=invoice_id,
            )
            claims.append({"kind": "po", **po_claim})
            sj_id = _norm(line.get("source_sj_id"))
            if sj_id:
                sj_claim = await claim_sj_line_billing(
                    db,
                    source_sj_id=sj_id,
                    source_po_id=str(line.get("source_po_id") or ""),
                    source_line_id=str(line.get("source_po_line_id") or line.get("line_id") or ""),
                    sj_line_qty=_as_int(line.get("sj_line_qty"), default=qty),
                    qty=qty,
                    invoice_id=invoice_id,
                    lease_token=str(po_claim.get("lease_token") or ""),
                )
                claims.append({"kind": "sj", **sj_claim})
    except Exception:
        for c in reversed(claims):
            if c.get("kind") == "sj":
                await release_sj_line_billing(
                    db,
                    source_sj_id=str(c.get("source_sj_id") or ""),
                    source_po_id=str(c.get("source_po_id") or ""),
                    source_line_id=str(c.get("source_line_id") or ""),
                    invoice_id=invoice_id,
                )
            else:
                await release_po_line_billing(
                    db,
                    source_po_id=str(c.get("source_po_id") or ""),
                    source_line_id=str(c.get("source_line_id") or ""),
                    invoice_id=invoice_id,
                )
        raise
    return claims


async def persist_invoice_with_billing_claims(db, invoice_doc: dict) -> dict:
    """Reserve → insert pending → commit → activate. Compensates on failure."""
    if not isinstance(invoice_doc, dict):
        raise HTTPException(400, "Dokumen Faktur tidak valid.")
    invoice_id = _norm(invoice_doc.get("id"))
    if not invoice_id:
        raise HTTPException(400, "invoice_id wajib.")
    lines = invoice_doc.get("lines") or []
    invoice_doc[BILLING_COMMIT_PENDING_FIELD] = True
    claims = await claim_invoice_line_billings(db, invoice_id, lines)
    try:
        await require_reserved_billing_claims(db, claims)
        await db.sales_invoices.insert_one(invoice_doc)
    except Exception:
        await release_invoice_billing_allocations(db, invoice_doc)
        raise
    try:
        await commit_claimed_billings(db, claims)
        await activate_invoice_billing_commit(db, invoice_id)
        invoice_doc.pop(BILLING_COMMIT_PENDING_FIELD, None)
        invoice_doc.pop(RECOVERY_FENCE_FIELD, None)
    except Exception:
        await release_invoice_billing_allocations(db, invoice_doc)
        await db.sales_invoices.delete_one(
            {"id": invoice_id, BILLING_COMMIT_PENDING_FIELD: True}
        )
        raise
    return claims


async def adjust_po_line_billing_qty(
    db,
    *,
    source_po_id: str,
    source_line_id: str,
    source_line_idx: int,
    ordered_qty: int,
    invoice_id: str,
    old_qty: int,
    new_qty: int,
) -> dict:
    """Atomically rebalance one committed PO claim. Fail-closed on concurrent edit."""
    iid = _norm(invoice_id)
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    old_qty = int(old_qty)
    new_qty = int(new_qty)
    if new_qty <= 0:
        raise HTTPException(400, "Qty penagihan harus lebih besar dari 0.")
    if new_qty == old_qty:
        return {"adjusted": False, "replayed": True, "qty": new_qty, "delta": 0}
    await ensure_billing_counter_indexes(db)
    await _ensure_po_counter(
        db,
        source_po_id=source_po_id,
        source_line_id=source_line_id,
        source_line_idx=int(source_line_idx),
        ordered_qty=int(ordered_qty),
    )
    cid = po_billing_counter_id(source_po_id, source_line_id)
    claim_path = _claim_path(iid)
    delta = new_qty - old_qty
    from pymongo import ReturnDocument

    # Match exact current claim qty so concurrent edits cannot double-claim.
    if delta > 0:
        cap = int(ordered_qty) - int(delta)
        new_record = {
            "qty": new_qty,
            "state": CLAIM_STATE_COMMITTED,
            "rebalanced_at": _now_epoch(),
        }
        result = await _po_counters(db).find_one_and_update(
            {
                "id": cid,
                "invoiced_qty": {"$lte": cap},
                "$or": [
                    {
                        f"{claim_path}.qty": old_qty,
                        f"{claim_path}.state": {"$in": [CLAIM_STATE_COMMITTED, CLAIM_ACTIVE]},
                    },
                    {claim_path: old_qty},
                ],
            },
            {
                "$inc": {"invoiced_qty": int(delta)},
                "$set": {
                    claim_path: new_record,
                    "ordered_qty": int(ordered_qty),
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if result:
            return {
                "adjusted": True,
                "delta": delta,
                "qty": new_qty,
                "invoice_id": iid,
                "kind": "po",
                "source_po_id": source_po_id,
                "source_line_id": source_line_id,
                "old_qty": old_qty,
            }
        cur = await _po_counters(db).find_one({"id": cid}, {"_id": 0})
        prior = ((cur or {}).get("claims_by_invoice") or {}).get(iid)
        if prior is not None and _claim_record_qty(prior) == new_qty:
            return {"adjusted": False, "replayed": True, "qty": new_qty, "delta": 0}
        remain = max(0, int(ordered_qty) - int((cur or {}).get("invoiced_qty") or 0))
        raise HTTPException(
            409,
            f"Rebalance penagihan gagal: sisa invoiceable {remain} (kenaikan {delta}).",
        )

    # delta < 0 — release exact difference once
    release_delta = -delta
    new_record = {
        "qty": new_qty,
        "state": CLAIM_STATE_COMMITTED,
        "rebalanced_at": _now_epoch(),
    }
    result = await _po_counters(db).find_one_and_update(
        {
            "id": cid,
            "invoiced_qty": {"$gte": release_delta},
            "$or": [
                {
                    f"{claim_path}.qty": old_qty,
                    f"{claim_path}.state": {"$in": [CLAIM_STATE_COMMITTED, CLAIM_ACTIVE, CLAIM_STATE_RESERVED]},
                },
                {claim_path: old_qty},
            ],
        },
        {
            "$inc": {"invoiced_qty": -int(release_delta)},
            "$set": {claim_path: new_record},
        },
        return_document=ReturnDocument.AFTER,
    )
    if result:
        return {
            "adjusted": True,
            "delta": delta,
            "qty": new_qty,
            "invoice_id": iid,
            "kind": "po",
            "source_po_id": source_po_id,
            "source_line_id": source_line_id,
            "old_qty": old_qty,
        }
    cur = await _po_counters(db).find_one({"id": cid}, {"_id": 0})
    prior = ((cur or {}).get("claims_by_invoice") or {}).get(iid)
    if prior is not None and _claim_record_qty(prior) == new_qty:
        return {"adjusted": False, "replayed": True, "qty": new_qty, "delta": 0}
    raise HTTPException(409, "Rebalance penagihan gagal: claim bentrok atau sudah berubah.")


async def adjust_sj_line_billing_qty(
    db,
    *,
    source_sj_id: str,
    source_po_id: str,
    source_line_id: str,
    sj_line_qty: int,
    invoice_id: str,
    old_qty: int,
    new_qty: int,
) -> dict:
    iid = _norm(invoice_id)
    source_sj_id = _norm(source_sj_id)
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    old_qty = int(old_qty)
    new_qty = int(new_qty)
    if new_qty <= 0:
        raise HTTPException(400, "Qty penagihan SJ harus lebih besar dari 0.")
    if new_qty == old_qty:
        return {"adjusted": False, "replayed": True, "qty": new_qty, "delta": 0}
    await ensure_billing_counter_indexes(db)
    cid = sj_billing_counter_id(source_sj_id, source_po_id, source_line_id)
    claim_path = _claim_path(iid)
    delta = new_qty - old_qty
    from pymongo import ReturnDocument
    await _sj_counters(db).update_one(
        {"id": cid},
        {
            "$setOnInsert": {
                "id": cid,
                "source_sj_id": source_sj_id,
                "source_po_id": source_po_id,
                "source_line_id": source_line_id,
                "sj_line_qty": int(sj_line_qty),
                "invoiced_qty": 0,
                "claims_by_invoice": {},
            }
        },
        upsert=True,
    )
    if delta > 0:
        cap = int(sj_line_qty) - int(delta)
        new_record = {
            "qty": new_qty,
            "state": CLAIM_STATE_COMMITTED,
            "rebalanced_at": _now_epoch(),
        }
        result = await _sj_counters(db).find_one_and_update(
            {
                "id": cid,
                "invoiced_qty": {"$lte": cap},
                "$or": [
                    {
                        f"{claim_path}.qty": old_qty,
                        f"{claim_path}.state": {"$in": [CLAIM_STATE_COMMITTED, CLAIM_ACTIVE]},
                    },
                    {claim_path: old_qty},
                ],
            },
            {
                "$inc": {"invoiced_qty": int(delta)},
                "$set": {
                    claim_path: new_record,
                    "sj_line_qty": int(sj_line_qty),
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if result:
            return {
                "adjusted": True,
                "delta": delta,
                "qty": new_qty,
                "invoice_id": iid,
                "kind": "sj",
                "source_sj_id": source_sj_id,
                "source_po_id": source_po_id,
                "source_line_id": source_line_id,
                "old_qty": old_qty,
            }
        cur = await _sj_counters(db).find_one({"id": cid}, {"_id": 0})
        prior = ((cur or {}).get("claims_by_invoice") or {}).get(iid)
        if prior is not None and _claim_record_qty(prior) == new_qty:
            return {"adjusted": False, "replayed": True, "qty": new_qty, "delta": 0}
        remain = max(0, int(sj_line_qty) - int((cur or {}).get("invoiced_qty") or 0))
        raise HTTPException(
            409,
            f"Rebalance penagihan SJ gagal: sisa {remain} (kenaikan {delta}).",
        )

    release_delta = -delta
    new_record = {
        "qty": new_qty,
        "state": CLAIM_STATE_COMMITTED,
        "rebalanced_at": _now_epoch(),
    }
    result = await _sj_counters(db).find_one_and_update(
        {
            "id": cid,
            "invoiced_qty": {"$gte": release_delta},
            "$or": [
                {
                    f"{claim_path}.qty": old_qty,
                    f"{claim_path}.state": {"$in": [CLAIM_STATE_COMMITTED, CLAIM_ACTIVE, CLAIM_STATE_RESERVED]},
                },
                {claim_path: old_qty},
            ],
        },
        {
            "$inc": {"invoiced_qty": -int(release_delta)},
            "$set": {claim_path: new_record},
        },
        return_document=ReturnDocument.AFTER,
    )
    if result:
        return {
            "adjusted": True,
            "delta": delta,
            "qty": new_qty,
            "invoice_id": iid,
            "kind": "sj",
            "source_sj_id": source_sj_id,
            "source_po_id": source_po_id,
            "source_line_id": source_line_id,
            "old_qty": old_qty,
        }
    cur = await _sj_counters(db).find_one({"id": cid}, {"_id": 0})
    prior = ((cur or {}).get("claims_by_invoice") or {}).get(iid)
    if prior is not None and _claim_record_qty(prior) == new_qty:
        return {"adjusted": False, "replayed": True, "qty": new_qty, "delta": 0}
    raise HTTPException(409, "Rebalance penagihan SJ gagal: claim bentrok.")


def _line_identity_key(line: dict, invoice: Optional[dict] = None) -> Tuple[str, str, str]:
    """Billing identity for Model C lines only (explicit source_po_line_id).

    Legacy single-PO rows may still store PO line_id without source_* fields.
    Those must not enter counter rebalance — they never claimed billing capacity.
    """
    po = _norm(line.get("source_po_id") or (invoice or {}).get("po_id"))
    lid = _norm(line.get("source_po_line_id"))
    sj = _norm(line.get("source_sj_id"))
    return (po, lid, sj)


async def rebalance_invoice_billing_allocations(
    db,
    invoice: dict,
    old_lines: Sequence[dict],
    new_lines: Sequence[dict],
) -> List[dict]:
    """All-or-nothing qty rebalance for draft edit. Compensates on failure."""
    invoice_id = _norm(invoice.get("id"))
    if not invoice_id:
        raise HTTPException(400, "invoice_id wajib untuk rebalance.")

    old_by_key: Dict[Tuple[str, str, str], dict] = {}
    for line in old_lines or []:
        if not isinstance(line, dict):
            continue
        key = _line_identity_key(line, invoice)
        if key[0] and key[1]:
            old_by_key[key] = line

    applied: List[dict] = []
    try:
        for line in new_lines or []:
            if not isinstance(line, dict):
                continue
            key = _line_identity_key(line, invoice)
            po_id, line_id, sj_id = key
            if not po_id or not line_id:
                continue
            new_qty = _as_int(line.get("quantity"))
            old = old_by_key.get(key)
            old_qty = _as_int((old or {}).get("quantity"))
            if old is None:
                # New line identity on draft edit — claim fresh capacity.
                po_adj = await claim_po_line_billing(
                    db,
                    source_po_id=po_id,
                    source_line_id=line_id,
                    source_line_idx=_as_int(line.get("source_po_line_idx"), default=_as_int(line.get("line_idx"))),
                    ordered_qty=_as_int(line.get("ordered_qty")),
                    qty=new_qty,
                    invoice_id=invoice_id,
                )
                # Draft edit operates on operational invoices — commit immediately.
                await commit_po_line_billing(
                    db,
                    source_po_id=po_id,
                    source_line_id=line_id,
                    invoice_id=invoice_id,
                    lease_token=str(po_adj.get("lease_token") or ""),
                    allow_missing_token=True,
                )
                applied.append({"kind": "po", "op": "claim", "old_qty": 0, "qty": new_qty, **po_adj})
                if sj_id:
                    sj_adj = await claim_sj_line_billing(
                        db,
                        source_sj_id=sj_id,
                        source_po_id=po_id,
                        source_line_id=line_id,
                        sj_line_qty=_as_int(line.get("sj_line_qty"), default=new_qty),
                        qty=new_qty,
                        invoice_id=invoice_id,
                    )
                    await commit_sj_line_billing(
                        db,
                        source_sj_id=sj_id,
                        source_po_id=po_id,
                        source_line_id=line_id,
                        invoice_id=invoice_id,
                        lease_token=str(sj_adj.get("lease_token") or ""),
                        allow_missing_token=True,
                    )
                    applied.append({"kind": "sj", "op": "claim", "old_qty": 0, "qty": new_qty, **sj_adj})
                continue

            if new_qty == old_qty:
                continue

            po_adj = await adjust_po_line_billing_qty(
                db,
                source_po_id=po_id,
                source_line_id=line_id,
                source_line_idx=_as_int(line.get("source_po_line_idx"), default=_as_int(line.get("line_idx"))),
                ordered_qty=_as_int(line.get("ordered_qty") or (old or {}).get("ordered_qty")),
                invoice_id=invoice_id,
                old_qty=old_qty,
                new_qty=new_qty,
            )
            applied.append({**po_adj, "op": "adjust"})
            if sj_id:
                sj_adj = await adjust_sj_line_billing_qty(
                    db,
                    source_sj_id=sj_id,
                    source_po_id=po_id,
                    source_line_id=line_id,
                    sj_line_qty=_as_int(
                        line.get("sj_line_qty") or (old or {}).get("sj_line_qty"),
                        default=max(new_qty, old_qty),
                    ),
                    invoice_id=invoice_id,
                    old_qty=old_qty,
                    new_qty=new_qty,
                )
                applied.append({**sj_adj, "op": "adjust"})
    except Exception:
        # Compensate in reverse — restore previous qty or release new claims.
        for adj in reversed(applied):
            try:
                kind = adj.get("kind")
                op = adj.get("op")
                if op == "claim":
                    if kind == "sj":
                        await release_sj_line_billing(
                            db,
                            source_sj_id=str(adj.get("source_sj_id") or ""),
                            source_po_id=str(adj.get("source_po_id") or ""),
                            source_line_id=str(adj.get("source_line_id") or ""),
                            invoice_id=invoice_id,
                        )
                    else:
                        await release_po_line_billing(
                            db,
                            source_po_id=str(adj.get("source_po_id") or ""),
                            source_line_id=str(adj.get("source_line_id") or ""),
                            invoice_id=invoice_id,
                        )
                elif adj.get("adjusted"):
                    # Reverse adjust: new → old
                    if kind == "sj":
                        await adjust_sj_line_billing_qty(
                            db,
                            source_sj_id=str(adj.get("source_sj_id") or ""),
                            source_po_id=str(adj.get("source_po_id") or ""),
                            source_line_id=str(adj.get("source_line_id") or ""),
                            sj_line_qty=_as_int(adj.get("sj_line_qty"), default=_as_int(adj.get("old_qty"))),
                            invoice_id=invoice_id,
                            old_qty=_as_int(adj.get("qty")),
                            new_qty=_as_int(adj.get("old_qty")),
                        )
                    else:
                        await adjust_po_line_billing_qty(
                            db,
                            source_po_id=str(adj.get("source_po_id") or ""),
                            source_line_id=str(adj.get("source_line_id") or ""),
                            source_line_idx=0,
                            ordered_qty=_as_int(adj.get("ordered_qty"), default=10**9),
                            invoice_id=invoice_id,
                            old_qty=_as_int(adj.get("qty")),
                            new_qty=_as_int(adj.get("old_qty")),
                        )
            except Exception:
                _logger.exception("rebalance compensation failed for %s", adj)
                raise HTTPException(
                    503,
                    "Rebalance gagal dan kompensasi tidak lengkap. Fail-closed — coba lagi.",
                )
        raise
    return applied


async def _find_counter_claims_for_invoice(db, invoice_id: str) -> List[dict]:
    iid = _norm(invoice_id)
    if not iid:
        return []
    out: List[dict] = []
    for coll_name, kind in ((BILLING_COUNTERS, "po"), (SJ_BILLING_COUNTERS, "sj")):
        coll = getattr(db, coll_name)
        cursor = coll.find(
            {f"claims_by_invoice.{iid}": {"$exists": True}},
            {"_id": 0},
        )
        async for doc in cursor:
            prior = (doc.get("claims_by_invoice") or {}).get(iid)
            out.append(
                {
                    "kind": kind,
                    "counter_id": doc.get("id"),
                    "source_po_id": doc.get("source_po_id"),
                    "source_line_id": doc.get("source_line_id"),
                    "source_sj_id": doc.get("source_sj_id"),
                    "qty": _claim_record_qty(prior),
                    "prior": prior,
                }
            )
    return out


async def reconcile_pending_orphan_invoice(db, invoice_id: str) -> dict:
    """Idempotent recovery for provisional invoices left after create crash."""
    sid = _norm(invoice_id)
    if not sid:
        return {"reconciled": False, "reason": "missing_id"}

    token = str(uuid_mod.uuid4())
    from pymongo import ReturnDocument
    fenced = await db.sales_invoices.find_one_and_update(
        {
            "id": sid,
            BILLING_COMMIT_PENDING_FIELD: True,
            RECOVERY_FENCE_FIELD: {"$ne": True},
        },
        {
            "$set": {
                RECOVERY_FENCE_FIELD: True,
                "recovery_token": token,
                "recovery_started_at": _now_iso_safe(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if not fenced:
        cur = await db.sales_invoices.find_one({"id": sid}, {"_id": 0})
        if not cur:
            return {"reconciled": True, "replayed": True, "action": "already_absent", "id": sid}
        if not invoice_is_billing_pending(cur):
            return {"reconciled": False, "replayed": True, "action": "already_active", "id": sid}
        if cur.get(RECOVERY_FENCE_FIELD) is True:
            return {"reconciled": False, "replayed": True, "action": "recovery_in_progress", "id": sid}
        return {"reconciled": False, "reason": "recovery_required", "id": sid}

    claim_rows = await _find_counter_claims_for_invoice(db, sid)
    now = _now_epoch()
    for row in claim_rows:
        prior = row.get("prior")
        if _claim_is_committed(prior):
            await db.sales_invoices.update_one(
                {"id": sid, "recovery_token": token},
                {
                    "$unset": {
                        BILLING_COMMIT_PENDING_FIELD: "",
                        RECOVERY_FENCE_FIELD: "",
                        "recovery_started_at": "",
                        "recovery_token": "",
                    },
                    "$set": {"updated_at": _now_iso_safe()},
                },
            )
            return {
                "reconciled": True,
                "action": "activated_committed_claim",
                "id": sid,
                "claims": len(claim_rows),
            }
        if _claim_is_reserved(prior):
            lease_until = float((prior or {}).get("lease_until") or 0) if isinstance(prior, dict) else 0.0
            if lease_until >= now:
                await db.sales_invoices.update_one(
                    {"id": sid, "recovery_token": token},
                    {
                        "$unset": {
                            RECOVERY_FENCE_FIELD: "",
                            "recovery_started_at": "",
                            "recovery_token": "",
                        }
                    },
                )
                return {
                    "reconciled": False,
                    "reason": "recovery_required",
                    "detail": "active_lease",
                    "id": sid,
                }

    release_results = await release_invoice_billing_allocations(db, fenced)
    for row in claim_rows:
        if row.get("kind") == "sj":
            await release_sj_line_billing(
                db,
                source_sj_id=str(row.get("source_sj_id") or ""),
                source_po_id=str(row.get("source_po_id") or ""),
                source_line_id=str(row.get("source_line_id") or ""),
                invoice_id=sid,
            )
        else:
            await release_po_line_billing(
                db,
                source_po_id=str(row.get("source_po_id") or ""),
                source_line_id=str(row.get("source_line_id") or ""),
                invoice_id=sid,
            )

    deleted = await db.sales_invoices.delete_one(
        {
            "id": sid,
            BILLING_COMMIT_PENDING_FIELD: True,
            RECOVERY_FENCE_FIELD: True,
            "recovery_token": token,
        }
    )
    if getattr(deleted, "deleted_count", 0) <= 0:
        return {
            "reconciled": False,
            "reason": "recovery_required",
            "detail": "delete_lost_race",
            "id": sid,
            "releases": release_results,
        }
    return {
        "reconciled": True,
        "action": "deleted_orphan",
        "id": sid,
        "releases": release_results,
        "replayed": False,
    }


async def reconcile_orphan_reserved_without_invoice(
    db, *, source_po_id: str, source_line_id: str
) -> List[dict]:
    """Pending-without-claim path: heal expired reserved with no invoice document."""
    return await heal_expired_billing_reservations_for_po_line(
        db, source_po_id=source_po_id, source_line_id=source_line_id
    )


async def require_operational_sales_invoice(
    db,
    invoice_id: str,
    *,
    attempt_reconcile: bool = True,
) -> dict:
    """Fail-closed: return only an operationally active invoice document."""
    sid = _norm(invoice_id)
    if not sid:
        raise HTTPException(404, PENDING_NOT_FOUND_OR_PROVISIONAL)
    doc = await db.sales_invoices.find_one({"id": sid}, {"_id": 0})
    if not doc:
        doc = await db.sales_invoices.find_one({"invoice_number": sid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, PENDING_NOT_FOUND_OR_PROVISIONAL)
    if invoice_is_operationally_active(doc) and not invoice_is_billing_pending(doc):
        return doc
    if invoice_is_billing_pending(doc) and attempt_reconcile:
        await reconcile_pending_orphan_invoice(db, str(doc.get("id") or sid))
        doc = await db.sales_invoices.find_one({"id": str(doc.get("id") or sid)}, {"_id": 0})
        if doc and invoice_is_operationally_active(doc) and not invoice_is_billing_pending(doc):
            return doc
        if not doc:
            raise HTTPException(404, PENDING_NOT_FOUND_OR_PROVISIONAL)
    if invoice_is_billing_pending(doc) or (doc or {}).get(RECOVERY_FENCE_FIELD) is True:
        raise HTTPException(409, PENDING_NOT_OPERATIONAL)
    raise HTTPException(404, PENDING_NOT_FOUND_OR_PROVISIONAL)


def assert_invoice_not_provisional(invoice: Optional[dict]) -> None:
    if invoice_is_billing_pending(invoice) or (invoice or {}).get(RECOVERY_FENCE_FIELD) is True:
        raise HTTPException(409, PENDING_NOT_OPERATIONAL)


async def get_po_line_invoiceable_remaining(
    db, *, source_po_id: str, source_line_id: str, source_line_idx: int, ordered_qty: int
) -> int:
    await _ensure_po_counter(
        db,
        source_po_id=source_po_id,
        source_line_id=source_line_id,
        source_line_idx=source_line_idx,
        ordered_qty=ordered_qty,
    )
    cur = await _po_counters(db).find_one(
        {"id": po_billing_counter_id(source_po_id, source_line_id)},
        {"_id": 0, "invoiced_qty": 1},
    )
    invoiced = _as_int((cur or {}).get("invoiced_qty"))
    return max(0, int(ordered_qty) - invoiced)


async def get_sj_line_invoiceable_remaining(
    db, *, source_sj_id: str, source_po_id: str, source_line_id: str, sj_line_qty: int
) -> int:
    cid = sj_billing_counter_id(source_sj_id, source_po_id, source_line_id)
    cur = await _sj_counters(db).find_one({"id": cid}, {"_id": 0, "invoiced_qty": 1})
    invoiced = _as_int((cur or {}).get("invoiced_qty"))
    return max(0, int(sj_line_qty) - invoiced)


async def invoice_ids_for_sales_delivery(db, sales_delivery_id: str) -> List[str]:
    """Active (non-provisional) invoices that reference this SJ."""
    sid = _norm(sales_delivery_id)
    if not sid:
        return []
    ids: List[str] = []
    cursor = db.sales_invoices.find(
        {
            "$or": [
                {"source_sj_ids": sid},
                {"lines.source_sj_id": sid},
            ],
            "financial_status": {"$nin": ["cancelled"]},
            BILLING_COMMIT_PENDING_FIELD: {"$ne": True},
        },
        {"_id": 0, "id": 1, "invoice_number": 1},
    )
    async for inv in cursor:
        iid = _norm(inv.get("id"))
        if iid:
            ids.append(iid)
    return ids


async def assert_sales_delivery_not_invoiced(db, sales_delivery_id: str) -> None:
    ids = await invoice_ids_for_sales_delivery(db, sales_delivery_id)
    if ids:
        raise HTTPException(
            409,
            "Surat Jalan tidak dapat dibatalkan karena sudah terkait Faktur Penjualan aktif: "
            + ", ".join(ids[:5])
            + ("" if len(ids) <= 5 else f" (+{len(ids) - 5} lainnya)")
            + ". Batalkan/selesaikan Faktur terlebih dahulu.",
        )


def validate_orders_invoice_merge_compatible(orders: Sequence[dict]) -> None:
    """Hard-block incompatible multi-PO invoice merge."""
    rows = [o for o in orders if isinstance(o, dict)]
    if len(rows) <= 1:
        return
    from services.customer_identity import extract_stable_customer_id

    customers = set()
    currencies = set()
    branches = set()
    for order in rows:
        if order.get("cancelled"):
            raise HTTPException(
                400,
                f"Pesanan '{order.get('po_number') or order.get('id')}' sudah dibatalkan.",
            )
        cid = _norm(extract_stable_customer_id(order) or order.get("customer_id"))
        if not cid:
            raise HTTPException(
                400,
                "Penggabungan Faktur ditolak: customer_id kanonik wajib ada pada setiap PO.",
            )
        customers.add(cid)
        currencies.add(_norm(order.get("currency") or "IDR").upper() or "IDR")
        branches.add(_norm(order.get("operational_branch_id") or order.get("branch_id")))
    if len(customers) > 1:
        raise HTTPException(400, "Penggabungan Faktur ditolak: customer harus sama.")
    if len(currencies) > 1:
        raise HTTPException(400, "Penggabungan Faktur ditolak: mata uang harus sama.")
    branches.discard("")
    if len(branches) > 1:
        raise HTTPException(400, "Penggabungan Faktur ditolak: cabang operasional harus sama.")


def new_invoice_line_id() -> str:
    return str(uuid_mod.uuid4())


def derive_combined_invoice_number() -> str:
    """Unique namespace for multi-source invoices (not derived from a fictional primary PO)."""
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"FPX-{stamp}-{uuid_mod.uuid4().hex[:10].upper()}"


async def mongo_transactions_available(db) -> bool:
    """Best-effort probe — standalone returns False (fail-closed uses compensation)."""
    try:
        client = db.client
        async with await client.start_session() as session:
            async with session.start_transaction():
                pass
        return True
    except Exception:
        return False
