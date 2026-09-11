"""Canonical order driver assignment — loading_checklist.driver_user_id only.

Writes ONLY dotted driver fields. Does NOT:
- set loaded qty / loading_confirmations
- mark Loading complete (items_loaded / seal_ok)
- advance Loading or Pengiriman stages
- invent a second driver field

Unassignment is NOT supported by current business rules.

Assignable Driver (candidate) ≠ Assigner permission:
- Candidate: users.role == "driver" and status != inactive
- Assigner: delivery.location.manage (independent; do not infer from role name)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from fastapi import HTTPException

# Who may BE selected as Driver (delivery assignee). Exact user IDs only.
# Canonical builtin role is "driver". Management roles are NOT drivers unless
# their user document role is literally "driver" (single-role model; no dual flag).
# Legacy /delivery/drivers also listed admin/heads for old vehicle readiness —
# that conflated assigner with assignee and is NOT the assignable-driver rule.
ELIGIBLE_DRIVER_ROLES = frozenset({"driver"})

# Who may ASSIGN/change Driver on an order (schedule-edit permission).
# Head production/ops have it; driver role does not. Do not invent a new permission.
ASSIGN_DRIVER_PERM = "delivery.location.manage"

# Read-only candidate list may also be opened by vehicle managers.
LIST_DRIVER_PERMS = (ASSIGN_DRIVER_PERM, "delivery.vehicle.manage")


def _text(value: Any) -> str:
    return str(value or "").strip()


def public_driver(doc: dict) -> dict:
    return {
        "id": _text(doc.get("id")),
        "name": _text(doc.get("name") or doc.get("email")) or _text(doc.get("id")),
        "email": _text(doc.get("email")) or None,
        "role": _text(doc.get("role")) or None,
    }


def is_assignable_driver_user(doc: Optional[dict]) -> bool:
    """True iff user may appear as a selectable Delivery Driver."""
    if not doc:
        return False
    if _text(doc.get("status")).lower() == "inactive":
        return False
    return _text(doc.get("role")) in ELIGIBLE_DRIVER_ROLES


async def list_eligible_drivers(db) -> List[dict]:
    """Read-only: active users with role==driver. Exact IDs only. ZERO writes."""
    docs = await db.users.find(
        {"role": "driver", "status": {"$ne": "inactive"}},
        {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "status": 1},
    ).to_list(500)
    out = []
    for d in docs:
        if not is_assignable_driver_user(d):
            continue
        uid = _text(d.get("id"))
        if not uid:
            continue
        out.append(public_driver(d))
    out.sort(key=lambda x: (x.get("name") or "", x.get("id") or ""))
    return out


async def resolve_eligible_driver(db, driver_user_id: str) -> dict:
    """Lookup by exact user id only — never by display name. Must be role==driver."""
    uid = _text(driver_user_id)
    if not uid:
        raise HTTPException(400, "driver_user_id wajib.")
    doc = await db.users.find_one(
        {"id": uid},
        {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "status": 1},
    )
    if not doc or _text(doc.get("status")).lower() == "inactive":
        raise HTTPException(404, "Driver tidak ditemukan.")
    if not is_assignable_driver_user(doc):
        raise HTTPException(400, "User bukan driver yang eligible.")
    return public_driver(doc)


def current_driver_from_order(order: dict) -> dict:
    lc = order.get("loading_checklist") or {}
    uid = _text(lc.get("driver_user_id") or order.get("driver_user_id")) or None
    name = _text(lc.get("driver_name") or order.get("driver_name")) or None
    return {"driver_user_id": uid, "driver_name": name}


def _loading_snapshot(order: dict) -> dict:
    """Fields that must remain unchanged by driver assignment."""
    lc = order.get("loading_checklist") or {}
    return {
        "loaded_qty_proxy": list(order.get("loading_confirmations") or []),
        "loading_confirmations_len": len(order.get("loading_confirmations") or []),
        "items_loaded": lc.get("items_loaded"),
        "seal_ok": lc.get("seal_ok"),
        "loading_completed_at": lc.get("loading_completed_at") or order.get("loading_completed_at"),
        "packing_info": order.get("packing_info"),
        "shipments": order.get("shipments"),
        "delivery_status": order.get("delivery_status"),
        "qty_delivered": order.get("qty_delivered"),
        "workflow_stage": order.get("workflow_stage"),
        "stage": order.get("stage"),
    }


async def assign_driver_to_order(
    db,
    *,
    order_id: str,
    driver_user_id: str,
    actor: Optional[dict] = None,
) -> dict:
    """Assign exact driver_user_id to one order. No unassign. No Loading mutation."""
    oid = _text(order_id)
    if not oid:
        raise HTTPException(400, "order_id wajib.")

    driver = await resolve_eligible_driver(db, driver_user_id)
    order = await db.orders.find_one({"id": oid}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Pengiriman tidak ditemukan.")

    before = current_driver_from_order(order)
    if before["driver_user_id"] == driver["id"]:
        return {
            "sales_order_id": oid,
            "status": "unchanged",
            "driver_user_id": driver["id"],
            "driver_name": driver["name"],
            "previous_driver_user_id": before["driver_user_id"],
            "previous_driver_name": before["driver_name"],
        }

    before_snap = _loading_snapshot(order)

    # Canonical fields only; no Loading completion timestamps.
    # When loading_checklist is null (common on imported/manual POs), MongoDB
    # rejects dotted $set ("Cannot create field ... in element {loading_checklist: null}").
    # Only then set a minimal object; otherwise dotted $set preserves sibling keys.
    lc = order.get("loading_checklist")
    if isinstance(lc, dict):
        update_fields = {
            "loading_checklist.driver_user_id": driver["id"],
            "loading_checklist.driver_name": driver["name"],
        }
    else:
        update_fields = {
            "loading_checklist": {
                "driver_user_id": driver["id"],
                "driver_name": driver["name"],
            },
        }
    await db.orders.update_one({"id": oid}, {"$set": update_fields})

    after = await db.orders.find_one({"id": oid}, {"_id": 0})
    after_snap = _loading_snapshot(after or {})
    # Safety assert in-process (tests also cover).
    if after_snap["loading_confirmations_len"] != before_snap["loading_confirmations_len"]:
        raise HTTPException(500, "Driver assignment must not alter loading_confirmations.")
    if after_snap["items_loaded"] != before_snap["items_loaded"] or after_snap["seal_ok"] != before_snap["seal_ok"]:
        raise HTTPException(500, "Driver assignment must not alter loading completion flags.")
    if after_snap["delivery_status"] != before_snap["delivery_status"]:
        raise HTTPException(500, "Driver assignment must not alter Pengiriman status.")
    if after_snap["qty_delivered"] != before_snap["qty_delivered"]:
        raise HTTPException(500, "Driver assignment must not alter delivered qty.")

    return {
        "sales_order_id": oid,
        "status": "updated",
        "driver_user_id": driver["id"],
        "driver_name": driver["name"],
        "previous_driver_user_id": before["driver_user_id"],
        "previous_driver_name": before["driver_name"],
    }


async def assign_driver_bulk(
    db,
    *,
    order_ids: Sequence[str],
    driver_user_id: str,
    actor: Optional[dict] = None,
) -> dict:
    """Assign exact selected IDs only. Per-row results; no mass updateMany."""
    ids: List[str] = []
    seen = set()
    for raw in order_ids or []:
        oid = _text(raw)
        if not oid or oid in seen:
            continue
        seen.add(oid)
        ids.append(oid)
    if not ids:
        raise HTTPException(400, "order_ids wajib (exact IDs).")

    # Validate driver once; then apply per exact id.
    driver = await resolve_eligible_driver(db, driver_user_id)

    results: List[dict] = []
    counts = {"updated": 0, "unchanged": 0, "failed": 0}
    for oid in ids:
        try:
            row = await assign_driver_to_order(
                db, order_id=oid, driver_user_id=driver["id"], actor=actor
            )
            results.append(row)
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        except HTTPException as exc:
            results.append({
                "sales_order_id": oid,
                "status": "failed",
                "error": str(exc.detail),
                "driver_user_id": driver["id"],
                "driver_name": driver["name"],
            })
            counts["failed"] += 1
        except Exception as exc:  # noqa: BLE001 — surface per-row failure
            results.append({
                "sales_order_id": oid,
                "status": "failed",
                "error": str(exc) or "Gagal menugaskan driver.",
                "driver_user_id": driver["id"],
                "driver_name": driver["name"],
            })
            counts["failed"] += 1

    ok = counts["failed"] == 0
    partial = (not ok) and (counts["updated"] > 0 or counts["unchanged"] > 0)
    return {
        "ok": ok,
        "partial": partial,
        "driver_user_id": driver["id"],
        "driver_name": driver["name"],
        "counts": counts,
        "results": results,
    }
