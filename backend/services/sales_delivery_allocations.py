"""Item-level source allocations for Sales Deliveries (Surat Jalan).

Stage 1: multi-PO → one SJ without rewriting invoice/Accurate.
Legacy single-po_id documents normalize to one allocation per line at read time.
"""
from __future__ import annotations

import re
import uuid as uuid_mod
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException

from services.customer_identity import extract_stable_customer_id
from services.operational_branch import document_branch


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_addr(value: Any) -> str:
    text = _norm(value).lower()
    return re.sub(r"\s+", " ", text)


def _norm_currency(value: Any) -> str:
    return _norm(value).upper()


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def delivery_source_po_ids(delivery: Optional[dict]) -> List[str]:
    """Stable unique source PO ids for a delivery document (legacy-safe)."""
    if not isinstance(delivery, dict):
        return []
    ordered: List[str] = []
    seen = set()

    def _add(raw: Any) -> None:
        token = _norm(raw)
        if token and token not in seen:
            seen.add(token)
            ordered.append(token)

    for token in delivery.get("source_po_ids") or []:
        _add(token)
    for alloc in delivery.get("source_allocations") or []:
        if isinstance(alloc, dict):
            _add(alloc.get("source_po_id"))
    for line in delivery.get("line_items") or []:
        if isinstance(line, dict):
            _add(line.get("source_po_id"))
    _add(delivery.get("po_id") or delivery.get("order_id"))
    return ordered


def is_multi_source_delivery(delivery: Optional[dict]) -> bool:
    return len(delivery_source_po_ids(delivery)) > 1


def normalize_delivery_source_allocations(delivery: Optional[dict]) -> List[dict]:
    """Read-time normalization. Does not write the database.

    Legacy SJ (single po_id, no source_allocations) → one allocation per line_item.
    """
    if not isinstance(delivery, dict):
        return []
    stored = delivery.get("source_allocations")
    if isinstance(stored, list) and stored:
        out = []
        for row in stored:
            if not isinstance(row, dict):
                continue
            qty = _as_int(row.get("allocated_qty"), default=_as_int(row.get("qty")))
            if qty <= 0:
                continue
            source_po_id = _norm(row.get("source_po_id") or delivery.get("po_id"))
            if not source_po_id:
                continue
            out.append({
                "source_po_id": source_po_id,
                "source_po_number": _norm(row.get("source_po_number") or delivery.get("po_number")),
                "source_line_id": _norm(row.get("source_line_id") or row.get("line_id")),
                "source_line_idx": _as_int(row.get("source_line_idx"), default=_as_int(row.get("line_idx"))),
                "master_item_id": _norm(row.get("master_item_id")),
                "item_code": _norm(row.get("item_code")),
                "name": _norm(row.get("name") or row.get("item_name")),
                "unit": _norm(row.get("unit")) or "pcs",
                "allocated_qty": qty,
                "unit_price": row.get("unit_price"),
                "discount_amount": row.get("discount_amount"),
                "ppn_percent": row.get("ppn_percent"),
                "currency": _norm(row.get("currency")),
            })
        if out:
            return out

    primary = _norm(delivery.get("po_id") or delivery.get("order_id"))
    po_number = _norm(delivery.get("po_number"))
    out = []
    for idx, line in enumerate(delivery.get("line_items") or []):
        if not isinstance(line, dict):
            continue
        qty = _as_int(line.get("qty"), default=_as_int(line.get("quantity")))
        if qty <= 0:
            continue
        source_po_id = _norm(line.get("source_po_id")) or primary
        if not source_po_id:
            continue
        source_line_idx = line.get("source_line_idx")
        if source_line_idx is None:
            source_line_idx = line.get("line_idx", idx)
        out.append({
            "source_po_id": source_po_id,
            "source_po_number": _norm(line.get("source_po_number") or po_number),
            "source_line_id": _norm(line.get("source_line_id") or line.get("line_id")),
            "source_line_idx": _as_int(source_line_idx),
            "master_item_id": _norm(line.get("master_item_id")),
            "item_code": _norm(line.get("item_code")),
            "name": _norm(line.get("name") or line.get("item_name")),
            "unit": _norm(line.get("unit")) or "pcs",
            "allocated_qty": qty,
            "unit_price": line.get("unit_price") if "unit_price" in line else line.get("price"),
            "discount_amount": line.get("discount_amount") if "discount_amount" in line else line.get("discount"),
            "ppn_percent": line.get("ppn_percent"),
            "currency": _norm(line.get("currency") or delivery.get("currency")),
        })
    return out


def line_belongs_to_po(delivery: dict, line: dict, po_id: str) -> bool:
    target = _norm(po_id)
    if not target or not isinstance(line, dict):
        return False
    src = _norm(line.get("source_po_id"))
    if src:
        return src == target
    return _norm(delivery.get("po_id") or delivery.get("order_id")) == target


def source_line_idx_for_po(delivery: dict, line: dict, po_id: str) -> Optional[int]:
    if not line_belongs_to_po(delivery, line, po_id):
        return None
    if line.get("source_line_idx") is not None:
        return _as_int(line.get("source_line_idx"))
    if _norm(line.get("source_po_id")) and line.get("source_line_idx") is None:
        # Multi-source line without source_line_idx is invalid for remaining math.
        if is_multi_source_delivery(delivery):
            return None
    return _as_int(line.get("line_idx"))


def shipped_qty_by_source_line(
    deliveries: Sequence[dict],
    po_id: str,
) -> Dict[int, int]:
    """Sum active allocated qty for one PO, keyed by source_line_idx."""
    target = _norm(po_id)
    by_idx: Dict[int, int] = {}
    for delivery in deliveries or []:
        if not isinstance(delivery, dict):
            continue
        if delivery.get("is_cancelled") is True or delivery.get("cancelled"):
            continue
        status = _norm(delivery.get("status")).lower()
        if status in {"dibatalkan", "cancelled", "deleted", "batal"}:
            continue
        for line in delivery.get("line_items") or []:
            if not isinstance(line, dict):
                continue
            idx = source_line_idx_for_po(delivery, line, target)
            if idx is None:
                continue
            qty = _as_int(line.get("qty"), default=_as_int(line.get("quantity")))
            if qty <= 0:
                continue
            by_idx[idx] = by_idx.get(idx, 0) + qty
    return by_idx


def active_deliveries_query_for_po(po_id: str) -> dict:
    """Mongo filter: legacy po_id OR multi-source membership.

    Provisional inserts (`allocation_commit_pending`) are excluded so a Race-A
    loser cannot inflate remaining/shipped before ownership is committed.
    """
    pid = _norm(po_id)
    return {
        "is_cancelled": {"$ne": True},
        ALLOCATION_COMMIT_PENDING_FIELD: {"$ne": True},
        "$or": [
            {"po_id": pid},
            {"source_po_ids": pid},
            {"line_items.source_po_id": pid},
            {"source_allocations.source_po_id": pid},
        ],
    }


def delivery_is_allocation_pending(delivery: Optional[dict]) -> bool:
    return bool(isinstance(delivery, dict) and delivery.get(ALLOCATION_COMMIT_PENDING_FIELD) is True)


def delivery_is_operationally_active(delivery: Optional[dict]) -> bool:
    """Canonical gate: legacy (no pending flag) and activated SJ are operational.

    Provisional (`allocation_commit_pending=True`) is never operational.
    Cancelled docs are never operational.
    """
    if not isinstance(delivery, dict):
        return False
    if delivery.get("is_cancelled") is True or delivery.get("cancelled"):
        return False
    if delivery_is_allocation_pending(delivery):
        return False
    if delivery.get(RECOVERY_FENCE_FIELD) is True:
        return False
    return True


PENDING_NOT_OPERATIONAL = (
    "Surat Jalan masih provisional (allocation belum di-commit). "
    "Dokumen ini belum sah untuk operasional."
)
PENDING_NOT_FOUND_OR_PROVISIONAL = (
    "Surat Jalan tidak tersedia sebagai dokumen operasional "
    "(tidak ditemukan, dibatalkan, atau masih provisional)."
)


async def load_sales_delivery_by_identity(db, sales_delivery_id: str) -> Optional[dict]:
    sid = _norm(sales_delivery_id)
    if not sid:
        return None
    doc = await db.sales_deliveries.find_one({"id": sid}, {"_id": 0})
    if doc:
        return doc
    return await db.sales_deliveries.find_one({"sj_number": sid}, {"_id": 0})


async def require_operational_sales_delivery(
    db,
    sales_delivery_id: str,
    *,
    attempt_reconcile: bool = True,
) -> dict:
    """Fail-closed: return only an operationally active SJ document.

    Optionally runs safe pending-orphan reconcile once when the doc is still pending.
    """
    sid = _norm(sales_delivery_id)
    if not sid:
        raise HTTPException(404, PENDING_NOT_FOUND_OR_PROVISIONAL)
    doc = await load_sales_delivery_by_identity(db, sid)
    if not doc:
        raise HTTPException(404, PENDING_NOT_FOUND_OR_PROVISIONAL)
    if delivery_is_operationally_active(doc):
        return doc
    if delivery_is_allocation_pending(doc) and attempt_reconcile:
        await reconcile_pending_orphan_delivery(db, str(doc.get("id") or sid))
        doc = await load_sales_delivery_by_identity(db, str(doc.get("id") or sid))
        if doc and delivery_is_operationally_active(doc):
            return doc
        if not doc:
            raise HTTPException(404, PENDING_NOT_FOUND_OR_PROVISIONAL)
    if delivery_is_allocation_pending(doc) or (doc or {}).get(RECOVERY_FENCE_FIELD) is True:
        raise HTTPException(409, PENDING_NOT_OPERATIONAL)
    raise HTTPException(404, PENDING_NOT_FOUND_OR_PROVISIONAL)


async def activate_delivery_allocation_commit(db, sales_delivery_id: str, *, session=None) -> dict:
    """Clear provisional fence after claims are committed (idempotent).

    Fail-closed if recovery has fenced this provisional SJ — old workers cannot
    activate after orphan recovery took ownership.
    """
    sid = _norm(sales_delivery_id)
    if not sid:
        return {"activated": False, "reason": "missing_id"}
    kwargs = {}
    if session is not None:
        kwargs["session"] = session
    from pymongo import ReturnDocument
    result = await db.sales_deliveries.find_one_and_update(
        {
            "id": sid,
            ALLOCATION_COMMIT_PENDING_FIELD: True,
            RECOVERY_FENCE_FIELD: {"$ne": True},
        },
        {
            "$unset": {
                ALLOCATION_COMMIT_PENDING_FIELD: "",
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
    cur = await db.sales_deliveries.find_one({"id": sid}, {"_id": 0}, **kwargs)
    if not cur:
        raise HTTPException(409, "Aktivasi SJ gagal: dokumen tidak ditemukan. Fail-closed.")
    if cur.get(RECOVERY_FENCE_FIELD) is True:
        raise HTTPException(
            409,
            "Aktivasi SJ ditolak: recovery orphan telah mengambil kepemilikan. Fail-closed.",
        )
    if delivery_is_allocation_pending(cur):
        raise HTTPException(409, "Aktivasi SJ gagal: state provisional tidak dapat di-clear.")
    return {"activated": True, "replayed": True, "id": sid}


def _now_iso_safe() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def order_price_snapshot(line: dict) -> Dict[str, Any]:
    price = line.get("unit_price")
    if price is None:
        price = line.get("price")
    if price is None:
        price = line.get("harga")
    discount = line.get("discount_amount")
    if discount is None:
        discount = line.get("discount")
    return {
        "unit_price": price,
        "discount_amount": discount,
        "ppn_percent": line.get("ppn_percent") if line.get("ppn_percent") is not None else line.get("ppn"),
        "currency": _norm(line.get("currency")),
    }


def validate_orders_merge_compatible(orders: Sequence[dict]) -> None:
    """Hard validation for multi-PO SJ. Fail closed when canonical ids are missing."""
    rows = [o for o in orders if isinstance(o, dict)]
    if len(rows) <= 1:
        return

    for order in rows:
        if order.get("cancelled"):
            raise HTTPException(
                400,
                f"Pesanan Penjualan '{order.get('po_number') or order.get('id')}' sudah dibatalkan.",
            )

    customer_ids = [extract_stable_customer_id(o) for o in rows]
    if any(not cid for cid in customer_ids):
        raise HTTPException(
            400,
            "Penggabungan PO ditolak: setiap PO harus punya customer_id kanonik yang sama. "
            "Pencocokan nama customer tidak diizinkan.",
        )
    if len(set(customer_ids)) != 1:
        raise HTTPException(400, "Penggabungan PO ditolak: customer harus sama (customer_id).")

    # Seller / legal entity — only when the field exists on any PO.
    entity_keys = ("legal_entity_id", "seller_entity_id", "company_id", "entity_id")
    present_entity_vals: List[str] = []
    missing_entity = False
    for order in rows:
        found = ""
        for key in entity_keys:
            found = _norm(order.get(key))
            if found:
                break
        if found:
            present_entity_vals.append(found)
        else:
            # If any order declares an entity key with empty value while another has one,
            # treat as incompatible only when at least one entity id is present overall.
            missing_entity = True
    if present_entity_vals:
        if missing_entity or len(set(present_entity_vals)) != 1:
            raise HTTPException(
                400,
                "Penggabungan PO ditolak: entitas penjual (legal entity) harus sama.",
            )

    branches = [document_branch(o) for o in rows]
    if any(not b for b in branches):
        raise HTTPException(
            400,
            "Penggabungan PO ditolak: setiap PO harus punya cabang operasional yang jelas.",
        )
    if len(set(branches)) != 1:
        raise HTTPException(
            400,
            "Penggabungan PO ditolak: cabang operasional harus sama.",
        )

    # Destination: prefer stable outlet id; else exact normalized address (not fuzzy name).
    outlet_ids = [_norm(o.get("outlet_id") or o.get("delivery_outlet_id")) for o in rows]
    if all(outlet_ids):
        if len(set(outlet_ids)) != 1:
            raise HTTPException(
                400,
                "Penggabungan PO ditolak: alamat/outlet tujuan harus sama (outlet_id).",
            )
    else:
        if any(outlet_ids) and not all(outlet_ids):
            raise HTTPException(
                400,
                "Penggabungan PO ditolak: identitas outlet tujuan tidak lengkap pada semua PO. "
                "Tidak memakai pencocokan nama longgar.",
            )
        addresses = [
            _norm_addr(
                o.get("address") or o.get("shipping_address") or o.get("delivery_address")
            )
            for o in rows
        ]
        if any(not a for a in addresses):
            raise HTTPException(
                400,
                "Penggabungan PO ditolak: alamat tujuan harus terisi dan sama persis pada semua PO.",
            )
        if len(set(addresses)) != 1:
            raise HTTPException(
                400,
                "Penggabungan PO ditolak: alamat tujuan harus sama (perbandingan tepat, bukan fuzzy).",
            )

    currencies = [_norm_currency(o.get("currency") or o.get("mata_uang")) for o in rows]
    if any(currencies) and (not all(currencies) or len(set(c for c in currencies if c)) > 1):
        raise HTTPException(400, "Penggabungan PO ditolak: mata uang harus sama.")

    # Tax treatment: compare explicit order-level flags when present.
    tax_keys = ("ppn_percent", "tax_percent", "vat_percent", "tax_inclusive")
    for key in tax_keys:
        vals = []
        any_present = False
        for order in rows:
            if key in order and order.get(key) is not None and _norm(order.get(key)) != "":
                any_present = True
                vals.append(_norm(order.get(key)).lower())
            else:
                vals.append("")
        if any_present and (any(v == "" for v in vals) or len(set(vals)) > 1):
            raise HTTPException(
                400,
                "Penggabungan PO ditolak: perlakuan perpajakan pada header PO harus kompatibel.",
            )


def allocation_fingerprint(allocations: Sequence[dict]) -> Tuple[Tuple[str, str, int], ...]:
    rows = []
    for alloc in allocations or []:
        if not isinstance(alloc, dict):
            continue
        po = _norm(alloc.get("source_po_id"))
        lid = _norm(alloc.get("source_line_id")) or f"idx:{_as_int(alloc.get('source_line_idx'))}"
        qty = _as_int(alloc.get("allocated_qty"), default=_as_int(alloc.get("qty")))
        if po and qty > 0:
            rows.append((po, lid, qty))
    return tuple(sorted(rows))


ALLOCATION_COUNTERS = "sales_delivery_line_allocation_counters"
# Nested map: claims_by_delivery.<sales_delivery_id> =
#   legacy int qty  → treated as committed (cancel-only release)
#   {qty, state, lease_token, lease_until, reserved_at}
#     state=reserved  → pre-persist fence; healable only after lease expiry + no SJ
#     state=committed → SJ persisted; release only via cancel/hard-delete

CLAIM_STATE_RESERVED = "reserved"
CLAIM_STATE_COMMITTED = "committed"
# Lease bounds active create ownership. Expiry alone never deletes a claim that
# still has a sales_deliveries document — that claim is promoted to committed.
ALLOCATION_LEASE_SECONDS = 90
# Insert fence: SJ is not active for remaining/seed until claim commit succeeds.
ALLOCATION_COMMIT_PENDING_FIELD = "allocation_commit_pending"
# Orphan recovery ownership fence — blocks late activate by crashed create workers.
RECOVERY_FENCE_FIELD = "allocation_recovery_fence"


def allocation_counter_id(source_po_id: str, source_line_id: str) -> str:
    return f"{_norm(source_po_id)}::{_norm(source_line_id)}"


def _counters(db):
    """Resolve allocation counter collection (attr access works with CachedDb test doubles)."""
    return getattr(db, ALLOCATION_COUNTERS)


def _claim_qty_field(sales_delivery_id: str) -> str:
    """Mongo path for per-SJ claim record. Delivery ids are UUID hex (safe as key)."""
    sid = _norm(sales_delivery_id)
    if not sid or "." in sid or "$" in sid:
        raise HTTPException(400, "sales_delivery_id alokasi tidak valid.")
    return f"claims_by_delivery.{sid}"


def _now_epoch() -> float:
    import time
    return time.time()


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
        return str(prior.get("state") or "") == CLAIM_STATE_COMMITTED
    # Legacy bare integer claims are treated as committed.
    return True


def _new_reservation_record(qty: int, *, lease_token: str, now: Optional[float] = None) -> dict:
    ts = float(now if now is not None else _now_epoch())
    return {
        "qty": int(qty),
        "state": CLAIM_STATE_RESERVED,
        "lease_token": _norm(lease_token) or str(uuid_mod.uuid4()),
        "lease_until": ts + float(ALLOCATION_LEASE_SECONDS),
        "reserved_at": ts,
    }


async def ensure_allocation_counter_indexes(db) -> None:
    try:
        coll = _counters(db)
        await coll.create_index("id", unique=True)
        await coll.create_index(
            [("source_po_id", 1), ("source_line_id", 1)],
            unique=True,
        )
    except Exception:
        # Index creation is best-effort; uniqueness still enforced when indexes exist.
        import logging
        logging.getLogger(__name__).warning(
            "allocation counter index ensure failed", exc_info=True
        )


async def _seed_counter_allocated_from_deliveries(
    db,
    *,
    source_po_id: str,
    source_line_id: str,
    source_line_idx: int,
) -> int:
    """Sum active SJ allocations for this PO line (legacy + multi-source)."""
    shipped = 0
    cursor = db.sales_deliveries.find(
        active_deliveries_query_for_po(source_po_id),
        {
            "_id": 0,
            "id": 1,
            "po_id": 1,
            "line_items": 1,
            "source_allocations": 1,
            "is_cancelled": 1,
            "status": 1,
            "cancelled": 1,
            RECOVERY_FENCE_FIELD: 1,
        },
    )
    async for delivery in cursor:
        # Seed from the same operational SJ population used by the PO remaining
        # summary. Cancelled/superseded SJ must not permanently consume quantity.
        if not delivery_is_operationally_active(delivery):
            continue
        if _norm(delivery.get("status")).lower() in {"superseded", "cancelled", "deleted"}:
            continue
        for line in delivery.get("line_items") or []:
            if not isinstance(line, dict):
                continue
            if not line_belongs_to_po(delivery, line, source_po_id):
                continue
            lid = _norm(line.get("source_line_id") or line.get("line_id"))
            idx = source_line_idx_for_po(delivery, line, source_po_id)
            if lid and lid == _norm(source_line_id):
                shipped += _as_int(line.get("qty"), default=_as_int(line.get("quantity")))
            elif not lid and idx is not None and idx == int(source_line_idx):
                shipped += _as_int(line.get("qty"), default=_as_int(line.get("quantity")))
    return shipped


async def _ensure_counter_bootstrapped(
    db,
    *,
    source_po_id: str,
    source_line_id: str,
    source_line_idx: int,
    ordered_qty: int,
) -> None:
    """Create counter once via $setOnInsert. Concurrent bootstraps cannot overwrite live qty."""
    cid = allocation_counter_id(source_po_id, source_line_id)
    existing = await _counters(db).find_one({"id": cid}, {"_id": 0, "id": 1})
    if existing:
        return
    seeded = await _seed_counter_allocated_from_deliveries(
        db,
        source_po_id=source_po_id,
        source_line_id=source_line_id,
        source_line_idx=source_line_idx,
    )
    await _counters(db).update_one(
        {"id": cid},
        {
            "$setOnInsert": {
                "id": cid,
                "source_po_id": source_po_id,
                "source_line_id": source_line_id,
                "source_line_idx": int(source_line_idx),
                "allocated_qty": int(seeded),
                "ordered_qty": int(ordered_qty),
                "claims_by_delivery": {},
                "active_delivery_ids": [],
                "updated_at": "",
            }
        },
        upsert=True,
    )


async def _sales_delivery_exists(db, sales_delivery_id: str) -> bool:
    sid = _norm(sales_delivery_id)
    if not sid:
        return False
    row = await db.sales_deliveries.find_one({"id": sid}, {"_id": 0, "id": 1})
    return bool(row)


async def heal_expired_reservations_for_line(
    db,
    *,
    source_po_id: str,
    source_line_id: str,
) -> List[dict]:
    """Recover expired *reserved* claims that cannot still commit.

    Rules (fail-closed for active work):
    - Never touch committed / legacy-int claims here.
    - Never release a reserved claim whose lease is still valid.
    - If lease expired AND SJ document exists → promote to committed (crash after insert).
    - If lease expired AND no SJ document → idempotent release (true orphan).
    """
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    if not source_po_id or not source_line_id:
        return []
    cid = allocation_counter_id(source_po_id, source_line_id)
    cur = await _counters(db).find_one({"id": cid}, {"_id": 0})
    if not cur:
        return []
    now = _now_epoch()
    out: List[dict] = []
    for sid, prior in list((cur.get("claims_by_delivery") or {}).items()):
        if not _claim_is_reserved(prior):
            continue
        lease_until = float(prior.get("lease_until") or 0)
        if lease_until >= now:
            continue
        if await _sales_delivery_exists(db, sid):
            promoted = await commit_source_line_allocation(
                db,
                source_po_id=source_po_id,
                source_line_id=source_line_id,
                sales_delivery_id=str(sid),
                lease_token=str(prior.get("lease_token") or ""),
                allow_missing_token=True,
                raise_on_failure=False,
            )
            if promoted.get("committed"):
                await activate_delivery_allocation_commit(db, str(sid))
            out.append({"sales_delivery_id": sid, "action": "promoted_committed", **promoted})
        else:
            released = await release_source_line_allocation(
                db,
                source_po_id=source_po_id,
                source_line_id=source_line_id,
                qty=_claim_record_qty(prior),
                sales_delivery_id=str(sid),
            )
            out.append({"sales_delivery_id": sid, "action": "released_orphan", **released})
    return out


async def claim_source_line_allocation(
    db,
    *,
    source_po_id: str,
    source_line_id: str,
    source_line_idx: int,
    ordered_qty: int,
    qty: int,
    sales_delivery_id: str,
    lease_token: Optional[str] = None,
) -> dict:
    """Atomically reserve qty for one PO line (state=reserved + lease fence).

    Safe on standalone Mongo via conditional $inc. Same sales_delivery_id retry is
    idempotent. Expired reserved orphans for this line are healed first.
    """
    if qty <= 0:
        raise HTTPException(400, "Qty alokasi harus lebih besar dari 0.")
    sid = _norm(sales_delivery_id)
    if not sid:
        raise HTTPException(400, "sales_delivery_id wajib untuk claim alokasi.")
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    if not source_po_id or not source_line_id:
        raise HTTPException(400, "source_po_id dan source_line_id wajib untuk claim alokasi.")

    await ensure_allocation_counter_indexes(db)
    await _ensure_counter_bootstrapped(
        db,
        source_po_id=source_po_id,
        source_line_id=source_line_id,
        source_line_idx=int(source_line_idx),
        ordered_qty=int(ordered_qty),
    )
    # Opportunistic recovery of expired reservations on this line (not global sweep).
    await heal_expired_reservations_for_line(
        db, source_po_id=source_po_id, source_line_id=source_line_id,
    )

    cid = allocation_counter_id(source_po_id, source_line_id)
    claim_path = _claim_qty_field(sid)
    token = _norm(lease_token) or str(uuid_mod.uuid4())

    cur = await _counters(db).find_one({"id": cid}, {"_id": 0})
    prior = ((cur or {}).get("claims_by_delivery") or {}).get(sid)
    if prior is not None:
        prior_qty = _claim_record_qty(prior)
        if prior_qty != int(qty):
            raise HTTPException(
                409,
                f"Alokasi retry bentrok: SJ sudah claim {prior_qty}, permintaan {qty}.",
            )
        # Same identity already holds qty — renew lease if still reserved.
        if _claim_is_reserved(prior):
            renewed = await renew_allocation_lease(
                db,
                source_po_id=source_po_id,
                source_line_id=source_line_id,
                sales_delivery_id=sid,
                lease_token=str(prior.get("lease_token") or token),
            )
            return {
                "counter_id": cid,
                "source_po_id": source_po_id,
                "source_line_id": source_line_id,
                "qty": prior_qty,
                "sales_delivery_id": sid,
                "lease_token": renewed.get("lease_token") or prior.get("lease_token") or token,
                "state": CLAIM_STATE_RESERVED,
                "replayed": True,
            }
        return {
            "counter_id": cid,
            "source_po_id": source_po_id,
            "source_line_id": source_line_id,
            "qty": prior_qty,
            "sales_delivery_id": sid,
            "lease_token": str((prior.get("lease_token") if isinstance(prior, dict) else "") or ""),
            "state": CLAIM_STATE_COMMITTED,
            "replayed": True,
        }

    await _counters(db).update_one(
        {"id": cid},
        {"$set": {"ordered_qty": int(ordered_qty), "source_line_idx": int(source_line_idx)}},
    )
    record = _new_reservation_record(int(qty), lease_token=token)
    cap = int(ordered_qty) - int(qty)
    from pymongo import ReturnDocument
    result = await _counters(db).find_one_and_update(
        {
            "id": cid,
            "allocated_qty": {"$lte": cap},
            claim_path: {"$exists": False},
        },
        {
            "$inc": {"allocated_qty": int(qty)},
            "$set": {
                "ordered_qty": int(ordered_qty),
                "source_line_idx": int(source_line_idx),
                "last_sales_delivery_id": sid,
                claim_path: record,
            },
            "$addToSet": {"active_delivery_ids": sid},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        cur = await _counters(db).find_one({"id": cid}, {"_id": 0})
        prior = ((cur or {}).get("claims_by_delivery") or {}).get(sid)
        if prior is not None and _claim_record_qty(prior) == int(qty):
            return {
                "counter_id": cid,
                "source_po_id": source_po_id,
                "source_line_id": source_line_id,
                "qty": int(qty),
                "sales_delivery_id": sid,
                "lease_token": str((prior.get("lease_token") if isinstance(prior, dict) else "") or token),
                "state": (
                    CLAIM_STATE_RESERVED if _claim_is_reserved(prior) else CLAIM_STATE_COMMITTED
                ),
                "replayed": True,
            }
        remain = max(0, int(ordered_qty) - int((cur or {}).get("allocated_qty") or 0))
        raise HTTPException(
            409,
            f"Alokasi gagal: sisa tersedia {remain} (permintaan {qty}). "
            "Kemungkinan bentrok dengan permintaan paralel.",
        )
    return {
        "counter_id": cid,
        "source_po_id": source_po_id,
        "source_line_id": source_line_id,
        "qty": int(qty),
        "sales_delivery_id": sid,
        "lease_token": token,
        "state": CLAIM_STATE_RESERVED,
        "replayed": False,
        "lease_until": record["lease_until"],
    }


async def renew_allocation_lease(
    db,
    *,
    source_po_id: str,
    source_line_id: str,
    sales_delivery_id: str,
    lease_token: str,
) -> dict:
    """Extend lease for an in-flight reserved claim. Fail-closed if ownership lost."""
    sid = _norm(sales_delivery_id)
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    token = _norm(lease_token)
    if not sid or not source_po_id or not source_line_id or not token:
        raise HTTPException(409, "Lease alokasi tidak valid.")
    cid = allocation_counter_id(source_po_id, source_line_id)
    claim_path = _claim_qty_field(sid)
    now = _now_epoch()
    new_until = now + float(ALLOCATION_LEASE_SECONDS)
    from pymongo import ReturnDocument
    result = await _counters(db).find_one_and_update(
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
            "Kepemilikan alokasi hilang (lease habis atau diganti). "
            "Operasi create dibatalkan (fail-closed).",
        )
    prior = (result.get("claims_by_delivery") or {}).get(sid) or {}
    return {
        "sales_delivery_id": sid,
        "lease_token": token,
        "lease_until": float(prior.get("lease_until") or new_until),
        "state": CLAIM_STATE_RESERVED,
    }


async def require_reserved_allocations(db, claims: Sequence[dict]) -> None:
    """Before persist: prove this worker still owns every reserved claim (renew lease)."""
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        if str(claim.get("state") or "") == CLAIM_STATE_COMMITTED:
            continue
        await renew_allocation_lease(
            db,
            source_po_id=str(claim.get("source_po_id") or ""),
            source_line_id=str(claim.get("source_line_id") or ""),
            sales_delivery_id=str(claim.get("sales_delivery_id") or ""),
            lease_token=str(claim.get("lease_token") or ""),
        )


async def commit_source_line_allocation(
    db,
    *,
    source_po_id: str,
    source_line_id: str,
    sales_delivery_id: str,
    lease_token: str = "",
    allow_missing_token: bool = False,
    session=None,
    raise_on_failure: bool = False,
) -> dict:
    """Mark a reservation committed after SJ persist (idempotent).

    When raise_on_failure=True (create path), missing/stolen claims raise 409
    instead of returning committed=False — prevents silent overship.
    """
    sid = _norm(sales_delivery_id)
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    token = _norm(lease_token)
    find_kwargs: Dict[str, Any] = {}
    update_kwargs: Dict[str, Any] = {}
    if session is not None:
        find_kwargs["session"] = session
        update_kwargs["session"] = session
    if not sid or not source_po_id or not source_line_id:
        result = {"committed": False, "reason": "missing_identity"}
        if raise_on_failure:
            raise HTTPException(409, "Commit alokasi gagal: identitas claim tidak lengkap.")
        return result
    cid = allocation_counter_id(source_po_id, source_line_id)
    claim_path = _claim_qty_field(sid)
    cur = await _counters(db).find_one({"id": cid}, {"_id": 0}, **find_kwargs)
    prior = ((cur or {}).get("claims_by_delivery") or {}).get(sid)
    if prior is None:
        result = {"committed": False, "replayed": True, "reason": "no_claim"}
        if raise_on_failure:
            raise HTTPException(
                409,
                "Commit alokasi gagal: claim tidak ditemukan (lease hilang/di-heal). Fail-closed.",
            )
        return result
    if _claim_is_committed(prior):
        return {
            "committed": True,
            "replayed": True,
            "qty": _claim_record_qty(prior),
            "state": CLAIM_STATE_COMMITTED,
        }
    if not _claim_is_reserved(prior):
        result = {"committed": False, "reason": "not_reserved"}
        if raise_on_failure:
            raise HTTPException(409, "Commit alokasi gagal: claim bukan reserved. Fail-closed.")
        return result
    if token and str(prior.get("lease_token") or "") != token and not allow_missing_token:
        raise HTTPException(409, "Commit alokasi ditolak: lease_token tidak cocok.")
    qty = _claim_record_qty(prior)
    committed_rec = {
        "qty": qty,
        "state": CLAIM_STATE_COMMITTED,
        "lease_token": str(prior.get("lease_token") or token or ""),
        "lease_until": float(prior.get("lease_until") or 0),
        "reserved_at": float(prior.get("reserved_at") or 0),
        "committed_at": _now_epoch(),
    }
    match = {
        "id": cid,
        f"{claim_path}.state": CLAIM_STATE_RESERVED,
        f"{claim_path}.qty": qty,
    }
    if token and not allow_missing_token:
        match[f"{claim_path}.lease_token"] = token
    from pymongo import ReturnDocument
    result = await _counters(db).find_one_and_update(
        match,
        {"$set": {claim_path: committed_rec}},
        return_document=ReturnDocument.AFTER,
        **update_kwargs,
    )
    if not result:
        cur2 = await _counters(db).find_one({"id": cid}, {"_id": 0}, **find_kwargs)
        prior2 = ((cur2 or {}).get("claims_by_delivery") or {}).get(sid)
        if _claim_is_committed(prior2):
            return {
                "committed": True,
                "replayed": True,
                "qty": _claim_record_qty(prior2),
                "state": CLAIM_STATE_COMMITTED,
            }
        raise HTTPException(
            409,
            "Commit alokasi gagal: reservasi tidak lagi dimiliki. Fail-closed.",
        )
    return {"committed": True, "replayed": False, "qty": qty, "state": CLAIM_STATE_COMMITTED}


async def commit_claimed_allocations(
    db,
    claims: Sequence[dict],
    *,
    session=None,
    raise_on_failure: bool = True,
) -> List[dict]:
    """Commit every required claim. Default fail-closed: any non-committed → raise.

    Partial success is never treated as create success. Callers must compensate
    (delete provisional SJ / abort txn). Does not invent new claims.
    """
    results: List[dict] = []
    required = [c for c in (claims or []) if isinstance(c, dict)]
    for claim in required:
        row = await commit_source_line_allocation(
            db,
            source_po_id=str(claim.get("source_po_id") or ""),
            source_line_id=str(claim.get("source_line_id") or ""),
            sales_delivery_id=str(claim.get("sales_delivery_id") or ""),
            lease_token=str(claim.get("lease_token") or ""),
            session=session,
            raise_on_failure=raise_on_failure,
        )
        if raise_on_failure and not row.get("committed"):
            reason = str(row.get("reason") or "ownership_lost")
            raise HTTPException(
                409,
                f"Commit alokasi gagal ({reason}). Fail-closed — create dibatalkan.",
            )
        results.append(row)
    if raise_on_failure and required and len(results) != len(required):
        raise HTTPException(409, "Commit alokasi parsial ditolak. Fail-closed.")
    return results


async def release_source_line_allocation(
    db,
    *,
    source_po_id: str,
    source_line_id: str,
    qty: int,
    sales_delivery_id: str,
) -> dict:
    """Idempotent release gated by claims_by_delivery.<sj_id> existence.

    Decrements using stored claim qty (dict or legacy int). Concurrent double
    release is a no-op.
    """
    sid = _norm(sales_delivery_id)
    source_po_id = _norm(source_po_id)
    source_line_id = _norm(source_line_id)
    if not sid or not source_po_id or not source_line_id:
        return {"released": False, "replayed": True, "reason": "missing_identity"}

    cid = allocation_counter_id(source_po_id, source_line_id)
    claim_path = _claim_qty_field(sid)
    cur = await _counters(db).find_one({"id": cid}, {"_id": 0})
    if not cur:
        return {"released": False, "replayed": True, "reason": "no_counter"}

    prior = (cur.get("claims_by_delivery") or {}).get(sid)
    if prior is None:
        active = [str(x) for x in (cur.get("active_delivery_ids") or [])]
        if sid not in active:
            return {"released": False, "replayed": True, "reason": "already_released"}
        release_qty = int(qty) if int(qty or 0) > 0 else 0
        if release_qty <= 0:
            from pymongo import ReturnDocument
            await _counters(db).find_one_and_update(
                {"id": cid, "active_delivery_ids": sid},
                {"$pull": {"active_delivery_ids": sid}},
                return_document=ReturnDocument.AFTER,
            )
            return {"released": False, "replayed": True, "reason": "legacy_id_only"}
        from pymongo import ReturnDocument
        result = await _counters(db).find_one_and_update(
            {
                "id": cid,
                "active_delivery_ids": sid,
                "allocated_qty": {"$gte": release_qty},
            },
            {
                "$inc": {"allocated_qty": -release_qty},
                "$pull": {"active_delivery_ids": sid},
            },
            return_document=ReturnDocument.AFTER,
        )
        if not result:
            return {"released": False, "replayed": True, "reason": "legacy_race_or_missing"}
        return {
            "released": True,
            "replayed": False,
            "qty": release_qty,
            "legacy": True,
            "allocated_qty": int(result.get("allocated_qty") or 0),
        }

    release_qty = _claim_record_qty(prior)
    if release_qty <= 0:
        from pymongo import ReturnDocument
        await _counters(db).find_one_and_update(
            {"id": cid, claim_path: {"$exists": True}},
            {
                "$unset": {claim_path: ""},
                "$pull": {"active_delivery_ids": sid},
            },
            return_document=ReturnDocument.AFTER,
        )
        return {"released": False, "replayed": True, "reason": "zero_claim"}

    from pymongo import ReturnDocument
    # Match on existence + qty field (works for dict and legacy int via separate paths).
    if isinstance(prior, dict):
        match = {
            "id": cid,
            claim_path: {"$exists": True},
            f"{claim_path}.qty": release_qty,
            "allocated_qty": {"$gte": release_qty},
        }
    else:
        match = {
            "id": cid,
            claim_path: release_qty,
            "allocated_qty": {"$gte": release_qty},
        }
    result = await _counters(db).find_one_and_update(
        match,
        {
            "$inc": {"allocated_qty": -release_qty},
            "$unset": {claim_path: ""},
            "$pull": {"active_delivery_ids": sid},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        cur2 = await _counters(db).find_one({"id": cid}, {"_id": 0})
        if cur2 and (cur2.get("claims_by_delivery") or {}).get(sid) is None:
            return {"released": False, "replayed": True, "reason": "lost_race_already_released"}
        raise HTTPException(
            503,
            f"Gagal melepas alokasi counter {cid} untuk SJ {sid}; ulangi pembatalan.",
        )
    return {
        "released": True,
        "replayed": False,
        "qty": release_qty,
        "allocated_qty": int(result.get("allocated_qty") or 0),
    }


async def release_claimed_allocations(db, claims: Sequence[dict]) -> List[dict]:
    results = []
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        results.append(
            await release_source_line_allocation(
                db,
                source_po_id=str(claim.get("source_po_id") or ""),
                source_line_id=str(claim.get("source_line_id") or ""),
                qty=int(claim.get("qty") or 0),
                sales_delivery_id=str(claim.get("sales_delivery_id") or ""),
            )
        )
    return results


async def release_delivery_allocations(db, delivery: Optional[dict]) -> List[dict]:
    """Release every source-line claim held by one SJ document (idempotent)."""
    if not isinstance(delivery, dict):
        return []
    delivery_id = _norm(delivery.get("id") or delivery.get("delivery_id"))
    if not delivery_id:
        return []
    results = []
    for alloc in normalize_delivery_source_allocations(delivery):
        results.append(
            await release_source_line_allocation(
                db,
                source_po_id=str(alloc.get("source_po_id") or ""),
                source_line_id=str(alloc.get("source_line_id") or ""),
                qty=int(alloc.get("allocated_qty") or 0),
                sales_delivery_id=delivery_id,
            )
        )
    return results


async def _find_counter_claims_for_delivery(db, sales_delivery_id: str) -> List[dict]:
    """Locate every allocation-counter claim record keyed by this SJ id."""
    sid = _norm(sales_delivery_id)
    if not sid:
        return []
    claim_path = _claim_qty_field(sid)
    cursor = _counters(db).find(
        {claim_path: {"$exists": True}},
        {"_id": 0, "id": 1, "source_po_id": 1, "source_line_id": 1, "claims_by_delivery": 1},
    )
    out: List[dict] = []
    async for cur in cursor:
        prior = ((cur or {}).get("claims_by_delivery") or {}).get(sid)
        if prior is None:
            continue
        out.append({
            "counter_id": cur.get("id"),
            "source_po_id": cur.get("source_po_id"),
            "source_line_id": cur.get("source_line_id"),
            "prior": prior,
            "qty": _claim_record_qty(prior),
        })
    return out


async def _pull_delivery_mirrors(db, delivery: dict, sales_delivery_id: str) -> List[str]:
    """Remove order shipment mirrors that exactly reference this SJ id."""
    sid = _norm(sales_delivery_id)
    pulled: List[str] = []
    for pid in delivery_source_po_ids(delivery):
        await db.orders.update_one(
            {"id": pid},
            {"$pull": {"shipments": {"sales_delivery_id": sid}}},
        )
        await db.orders.update_one(
            {"id": pid},
            {"$pull": {"shipments": {"id": sid}}},
        )
        await db.orders.update_one(
            {"id": pid},
            {"$pull": {"surat_jalan_files": {"shipment_id": sid}}},
        )
        await db.orders.update_one(
            {"id": pid},
            {"$pull": {"surat_jalan_files": {"surat_jalan_id": sid}}},
        )
        pulled.append(pid)
    return pulled


async def reconcile_pending_orphan_delivery(db, sales_delivery_id: str) -> dict:
    """Idempotent recovery for provisional SJ left after create crash.

    Safe delete only when:
    - doc is still pending
    - this worker wins the recovery fence (blocks late activate)
    - no active (unexpired) reserved lease for this SJ
    - no committed claim for this SJ (those activate instead of delete)

    Never touches other workers' claims/SJ ids.
    """
    sid = _norm(sales_delivery_id)
    if not sid:
        return {"reconciled": False, "reason": "missing_id"}

    token = str(uuid_mod.uuid4())
    from pymongo import ReturnDocument
    fenced = await db.sales_deliveries.find_one_and_update(
        {
            "id": sid,
            ALLOCATION_COMMIT_PENDING_FIELD: True,
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
        cur = await db.sales_deliveries.find_one({"id": sid}, {"_id": 0})
        if not cur:
            return {"reconciled": True, "replayed": True, "action": "already_absent", "id": sid}
        if not delivery_is_allocation_pending(cur):
            return {"reconciled": False, "replayed": True, "action": "already_active", "id": sid}
        if cur.get(RECOVERY_FENCE_FIELD) is True:
            return {"reconciled": False, "replayed": True, "action": "recovery_in_progress", "id": sid}
        return {"reconciled": False, "reason": "recovery_required", "id": sid}

    # Ownership / claim inspection after fence (create activate can no longer win).
    claim_rows = await _find_counter_claims_for_delivery(db, sid)
    now = _now_epoch()
    for row in claim_rows:
        prior = row.get("prior")
        if _claim_is_committed(prior):
            # Crash after claim commit but before activate — promote visibility.
            await db.sales_deliveries.update_one(
                {"id": sid, "recovery_token": token},
                {
                    "$unset": {
                        ALLOCATION_COMMIT_PENDING_FIELD: "",
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
                # Active create may still be mid-flight — fail-closed, release fence.
                await db.sales_deliveries.update_one(
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

    # Release any expired reserved / leftover claims owned solely by this SJ.
    release_results = await release_delivery_allocations(db, fenced)
    # Also release any counter rows discovered by scan (covers incomplete line_items).
    for row in claim_rows:
        await release_source_line_allocation(
            db,
            source_po_id=str(row.get("source_po_id") or ""),
            source_line_id=str(row.get("source_line_id") or ""),
            qty=int(row.get("qty") or 0),
            sales_delivery_id=sid,
        )

    pulled = await _pull_delivery_mirrors(db, fenced, sid)
    deleted = await db.sales_deliveries.delete_one(
        {
            "id": sid,
            ALLOCATION_COMMIT_PENDING_FIELD: True,
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
            "mirrors_pulled": pulled,
            "releases": release_results,
        }
    return {
        "reconciled": True,
        "action": "deleted_orphan",
        "id": sid,
        "mirrors_pulled": pulled,
        "releases": release_results,
        "replayed": False,
    }


def assess_multi_source_slice_fulfillment(
    delivery: Optional[dict],
    orders_by_id: Dict[str, dict],
) -> dict:
    """Read-only: per-PO loading/POD completeness for one combined SJ.

    Does not auto-confirm other POs. A combined SJ is physically complete only
    when every required source slice reports loading + delivered on its mirror.
    """
    if not isinstance(delivery, dict):
        return {"is_multi_source": False, "complete": False, "sources": []}
    delivery_id = _norm(delivery.get("id") or delivery.get("delivery_id"))
    identity = {
        delivery_id,
        _norm(delivery.get("sj_number")),
        _norm(delivery.get("number")),
    }
    identity.discard("")
    sources = []
    all_loading = True
    all_delivered = True
    for po_id in delivery_source_po_ids(delivery):
        order = orders_by_id.get(po_id) or {}
        slice_lines = slice_lines_for_po(delivery, po_id)
        slice_qty = sum(int(x.get("qty") or 0) for x in slice_lines)
        shipment = None
        for sh in order.get("shipments") or []:
            if not isinstance(sh, dict):
                continue
            tokens = {
                _norm(sh.get("id")),
                _norm(sh.get("sales_delivery_id")),
                _norm(sh.get("number")),
                _norm(sh.get("sj_number")),
            }
            if identity & tokens:
                shipment = sh
                break
        loading_qty = 0
        for conf in order.get("loading_confirmations") or []:
            if not isinstance(conf, dict):
                continue
            sj_token = _norm(conf.get("surat_jalan_id") or conf.get("shipment_id"))
            if sj_token and sj_token in identity:
                for ln in conf.get("lines") or []:
                    if isinstance(ln, dict):
                        loading_qty += _as_int(ln.get("qty"))
        delivered_qty = 0
        delivered = False
        if shipment:
            if shipment.get("delivered_at") or str(shipment.get("status") or "").lower() in {
                "delivered", "terkirim", "confirmed",
            }:
                delivered = True
            delivered_qty = _as_int(
                shipment.get("qty_delivered"),
                default=_as_int(shipment.get("total_qty") if delivered else 0),
            )
        for rec in order.get("delivery_confirmations") or []:
            if not isinstance(rec, dict):
                continue
            tokens = {
                _norm(rec.get("shipment_id")),
                _norm(rec.get("surat_jalan_id")),
                _norm(rec.get("sales_delivery_id")),
                _norm(rec.get("delivery_id")),
            }
            if identity & tokens and not rec.get("is_cancelled") and not rec.get("cancelled"):
                delivered = True
                delivered_qty = max(delivered_qty, _as_int(rec.get("qty"), default=slice_qty))
        loading_ok = slice_qty <= 0 or loading_qty >= slice_qty
        delivered_ok = slice_qty <= 0 or delivered
        if not loading_ok:
            all_loading = False
        if not delivered_ok:
            all_delivered = False
        sources.append({
            "source_po_id": po_id,
            "slice_qty": slice_qty,
            "loading_qty": loading_qty,
            "loading_complete": loading_ok,
            "delivered": delivered_ok,
            "delivered_qty": delivered_qty,
            "shipment_id": _norm((shipment or {}).get("id") or (shipment or {}).get("sales_delivery_id")),
            "has_mirror": shipment is not None,
        })
    multi = len(sources) > 1
    return {
        "is_multi_source": multi,
        "sales_delivery_id": delivery_id,
        "sj_number": _norm(delivery.get("sj_number")),
        "loading_complete": all_loading if sources else False,
        "pod_complete": all_delivered if sources else False,
        "complete": bool(sources) and all_loading and all_delivered,
        "sources": sources,
    }


async def mongo_transactions_available(db) -> bool:
    """True only when a real multi-doc transaction can commit on this deployment.

    Standalone Mongo may allow start/abort but reject transactional writes with
    IllegalOperation ("Transaction numbers are only allowed on a replica set").
    Probe with an inert transactional update on a throwaway collection.
    """
    try:
        client = getattr(db, "client", None)
        if client is None:
            return False
        probe = db["__txn_probe__"]
        async with await client.start_session() as session:
            session.start_transaction()
            await probe.update_one(
                {"_id": "__probe__"},
                {"$set": {"t": 1}},
                upsert=True,
                session=session,
            )
            await session.abort_transaction()
        return True
    except Exception:
        return False


def slice_lines_for_po(delivery: dict, po_id: str) -> List[dict]:
    """Shipment mirror lines for one source PO (loading/POD stay per-PO accurate)."""
    target = _norm(po_id)
    out = []
    for line in delivery.get("line_items") or []:
        if not isinstance(line, dict):
            continue
        if not line_belongs_to_po(delivery, line, target):
            continue
        src_idx = source_line_idx_for_po(delivery, line, target)
        if src_idx is None:
            continue
        qty = _as_int(line.get("qty"), default=_as_int(line.get("quantity")))
        out.append({
            "line_idx": src_idx,
            "line_id": _norm(line.get("source_line_id") or line.get("line_id")),
            "qty": qty,
            "name": line.get("name"),
            "unit": line.get("unit") or "pcs",
            "color": line.get("color"),
            "production_process": line.get("production_process"),
            "source_po_id": target,
            "source_line_id": _norm(line.get("source_line_id") or line.get("line_id")),
            "source_line_idx": src_idx,
            "master_item_id": line.get("master_item_id"),
        })
    return out
