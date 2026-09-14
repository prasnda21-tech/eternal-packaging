"""Append-only delivery confirmation history for Bukti Kirim.

Does not mutate deadline, proofs, packing, loading qty, or delivery_status.
Historical rows without snapshot fields display as missing (None → UI '-').
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from quantity_rules import (
    MAX_PUBLIC_DELIVERY_PROOFS,
    _line_count,
    _line_name,
    packing_required_total,
    qty_delivered,
    qty_loaded_for_line,
    valid_delivery_proofs,
)


def _text(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _actor_id(user: Optional[dict]) -> Optional[str]:
    if not user:
        return None
    return _text(user.get("user_id") or user.get("sub") or user.get("id"))


def _previous_line_qtys(order: dict) -> Dict[int, int]:
    totals: Dict[int, int] = {}
    for rec in order.get("delivery_confirmations") or []:
        for it in rec.get("items") or []:
            if not isinstance(it, dict):
                continue
            try:
                idx = int(it.get("line_idx"))
            except (TypeError, ValueError):
                continue
            if idx < 0:
                continue
            totals[idx] = totals.get(idx, 0) + max(0, int(it.get("qty") or 0))
    return totals


def _used_proof_ids(order: dict) -> set:
    ids = set()
    for rec in order.get("delivery_confirmations") or []:
        for pid in rec.get("proof_ids") or []:
            s = str(pid or "").strip()
            if s:
                ids.add(s)
    return ids


def _shipment_plate(order: dict, shipment_id: Optional[str]) -> Optional[str]:
    sid = _text(shipment_id)
    if not sid:
        return None
    for sh in order.get("shipments") or []:
        if not isinstance(sh, dict):
            continue
        if str(sh.get("id") or "") == sid:
            return _text(sh.get("vehicle_plate"))
    return None


def _resolve_plate(order: dict, rec: dict) -> Optional[str]:
    plate = _text(rec.get("vehicle_plate"))
    if plate:
        return plate
    plate = _shipment_plate(order, rec.get("shipment_id"))
    if plate:
        return plate
    lc = order.get("loading_checklist") or {}
    return _text(lc.get("vehicle_plate"))


def _item_deltas(order: dict) -> List[dict]:
    prev = _previous_line_qtys(order)
    rows = []
    n = _line_count(order)
    for idx in range(n):
        loaded = qty_loaded_for_line(order, idx)
        delta = max(0, loaded - prev.get(idx, 0))
        if delta <= 0:
            continue
        name = _text(_line_name(order, idx))
        rows.append({"line_idx": idx, "name": name, "qty": delta})
    return rows


def _item_label(items: List[dict], fallback_qty: int) -> Optional[str]:
    names = []
    for it in items or []:
        name = _text(it.get("name"))
        qty = it.get("qty")
        if name and qty not in (None, ""):
            names.append(f"{name} ({qty})")
        elif name:
            names.append(name)
    if names:
        return "; ".join(names)
    if fallback_qty:
        return None
    return None


def _new_proof_ids(order: dict) -> List[str]:
    used = _used_proof_ids(order)
    out = []
    for p in valid_delivery_proofs(order):
        pid = str(p.get("id") or "").strip()
        if pid and pid not in used:
            out.append(pid)
    return out


def _latest_shipment_id(order: dict) -> Optional[str]:
    for p in reversed(valid_delivery_proofs(order)):
        sid = _text(p.get("shipment_id"))
        if sid:
            return sid
    ships = order.get("shipments") or []
    if ships and isinstance(ships[-1], dict):
        return _text(ships[-1].get("id"))
    return None


def _items_for_shipment(order: dict, shipment: Optional[dict], qty_increment: int) -> List[dict]:
    if isinstance(shipment, dict):
        rows = []
        for ln in shipment.get("lines") or []:
            if not isinstance(ln, dict):
                continue
            try:
                idx = int(ln.get("line_idx"))
            except (TypeError, ValueError):
                continue
            if idx < 0:
                continue
            qty = max(0, int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity") or 0))
            if qty <= 0:
                continue
            name = _text(ln.get("name")) or _text(_line_name(order, idx))
            rows.append({"line_idx": idx, "name": name, "qty": qty})
        if rows:
            return rows
    items = _item_deltas(order)
    if items:
        return items
    return [{"line_idx": 0, "name": _text(_line_name(order, 0)), "qty": qty_increment}]


def append_confirmation(
    order: dict,
    user: dict,
    *,
    qty_increment: int,
    now: str,
    action_id: Optional[str] = None,
    shipment_id: Optional[str] = None,
    shipment: Optional[dict] = None,
) -> Optional[dict]:
    """Persist one confirmation snapshot. No-op when qty did not increase."""
    if qty_increment <= 0:
        return None
    if action_id:
        for rec in order.get("delivery_confirmations") or []:
            if rec.get("action_id") and rec.get("action_id") == action_id:
                return rec
    lc = order.get("loading_checklist") or {}
    actor_id = _actor_id(user)
    role = _text((user or {}).get("role"))
    display_driver = _text(lc.get("driver_name")) or _text((user or {}).get("name"))
    driver_id = actor_id if role == "driver" else None
    items = _items_for_shipment(order, shipment, qty_increment)
    rec = {
        "id": str(uuid.uuid4()),
        "action_id": _text(action_id),
        "confirmed_at": now,
        "confirmed_by_id": actor_id,
        "confirmed_by_name": _text((user or {}).get("name")),
        "driver_id": driver_id,
        "driver_name": display_driver,
        "vehicle_plate": _text(lc.get("vehicle_plate")),
        "shipment_id": _text(shipment_id) or _text((shipment or {}).get("id")) or _latest_shipment_id(order),
        "qty": int(qty_increment),
        "items": items,
        "proof_ids": _new_proof_ids(order),
    }
    order.setdefault("delivery_confirmations", []).append(rec)
    return rec


def _photos_for_ids(order: dict, proof_ids: List[str]) -> List[dict]:
    by_id = {str(p.get("id")): p for p in valid_delivery_proofs(order)}
    out = []
    for pid in proof_ids or []:
        p = by_id.get(str(pid))
        if p:
            out.append(p)
    return out


def _row_from_rec(order: dict, rec: dict) -> dict:
    items = [it for it in (rec.get("items") or []) if isinstance(it, dict)]
    qty = rec.get("qty")
    try:
        qty_n = int(qty) if qty is not None else None
    except (TypeError, ValueError):
        qty_n = None
    return {
        "id": rec.get("id"),
        "driver_name": _text(rec.get("driver_name")),
        "item": _item_label(items, qty_n or 0),
        "items": items,
        "qty": qty_n,
        "time": _text(rec.get("confirmed_at")),
        "vehicle_plate": _resolve_plate(order, rec),
        "proof_ids": [str(x) for x in (rec.get("proof_ids") or []) if str(x).strip()],
        "photos": _photos_for_ids(order, rec.get("proof_ids") or []),
    }


def _synthetic_rows(order: dict) -> List[dict]:
    proofs = valid_delivery_proofs(order)
    delivered = qty_delivered(order)
    if not proofs and delivered <= 0:
        return []
    lc = order.get("loading_checklist") or {}
    items = []
    n = _line_count(order)
    for idx in range(n):
        loaded = qty_loaded_for_line(order, idx)
        if loaded <= 0:
            continue
        items.append({"line_idx": idx, "name": _text(_line_name(order, idx)), "qty": loaded})
    time = _text(order.get("delivered_at"))
    if not time and proofs:
        time = _text(proofs[-1].get("uploaded_at") or proofs[-1].get("created_at"))
    plate = _text(lc.get("vehicle_plate"))
    if not plate:
        plate = _shipment_plate(order, _latest_shipment_id(order))
    return [{
        "id": "legacy",
        "driver_name": _text(lc.get("driver_name")) or _text(order.get("delivered_by_name")),
        "item": _item_label(items, delivered),
        "items": items,
        "qty": delivered or None,
        "time": time,
        "vehicle_plate": plate,
        "proof_ids": [str(p.get("id")) for p in proofs if p.get("id")],
        "photos": proofs,
    }]


def delivery_history_rows(order: dict) -> List[dict]:
    recs = [r for r in (order.get("delivery_confirmations") or []) if isinstance(r, dict)]
    if recs:
        return [_row_from_rec(order, r) for r in recs]
    return _synthetic_rows(order)


def terkirim_qty_summary(order: dict) -> dict:
    total = packing_required_total(order)
    delivered = qty_delivered(order)
    return {
        "total_po_qty": total,
        "qty_delivered": delivered,
        "qty_remaining": max(0, total - delivered),
    }


def attach_delivery_history(order: dict) -> None:
    order["delivery_history"] = delivery_history_rows(order)
    summary = terkirim_qty_summary(order)
    order["terkirim_total_po_qty"] = summary["total_po_qty"]
    order["terkirim_remaining_qty"] = summary["qty_remaining"]


def public_delivery_history(order: dict, token: str, allowed_photo_ids: Optional[set] = None) -> List[dict]:
    """Customer-safe rows. Photos limited to the public max-5 id set."""
    t = (token or "").strip()
    allowed = allowed_photo_ids
    rows = []
    for rec in delivery_history_rows(order):
        photos = []
        for p in rec.get("photos") or []:
            pid = str(p.get("id") or "").strip()
            if not pid:
                continue
            if allowed is not None and pid not in allowed:
                continue
            photos.append({
                "id": pid,
                "url": f"/api/public/tracking/{t}/proofs/{pid}",
            })
        rows.append({
            "driver_name": rec.get("driver_name"),
            "item": rec.get("item"),
            "qty": rec.get("qty"),
            "time": rec.get("time"),
            "vehicle_plate": rec.get("vehicle_plate"),
            "photos": photos,
        })
    return rows
