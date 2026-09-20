"""Canonical Loading → Pengiriman gate for new delivery mutations.

Read-model helpers also classify historical fully-delivered POs that never
stored Loading evidence. That classification is display-only and must not
authorize a new confirm/mark-delivered without Loading.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from quantity_rules import (
    _line_count,
    _safe_int,
    active_shipments_stored,
    canonical_shipment_id,
    delivery_batch_identity,
    delivery_batch_qty,
    find_shipment,
    is_fully_delivered,
    loaded_qty_for_sj_line,
    loading_confirmations_stored,
    packing_required_total,
    po_qty_for_line,
    qty_delivered,
    qty_delivered_for_shipment,
    qty_loaded,
    shipment_is_active,
    shipment_is_delivered,
    sj_qty_for_line,
)

MSG_DELIVERY_LOADING_INCOMPLETE = (
    "Pengiriman belum dapat dikonfirmasi karena Loading untuk Surat Jalan ini belum lengkap."
)
MSG_DELIVERY_QTY_INVALID = "Qty pengiriman tidak valid."
MSG_DELIVERY_QTY_ZERO = "Qty dikirim harus lebih dari 0."
MSG_DELIVERY_SJ_REQUIRED = "Surat Jalan belum dibuat."
REASON_LEGACY_NO_LOADING = "legacy_no_loading_evidence"


def _has_qty_lines(rows: Any) -> bool:
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            continue
        qty = row.get("qty")
        if qty is None:
            qty = row.get("quantity")
        if qty is None:
            qty = row.get("loaded_qty")
        if _safe_int(qty) > 0:
            return True
    return False


def has_canonical_loading_evidence(order: dict) -> bool:
    """True only from Loading records — never from SJ qty or delivery_status."""
    order = order or {}
    if qty_loaded(order) > 0:
        return True
    if loading_confirmations_stored(order) and any(
        _has_qty_lines(c.get("lines")) for c in loading_confirmations_stored(order)
    ):
        return True
    info = order.get("loading_info") if isinstance(order.get("loading_info"), dict) else {}
    if _has_qty_lines(info.get("lines")) or _has_qty_lines(info.get("events")):
        return True
    if order.get("loading_completed"):
        return True
    qty_status = str(order.get("loading_qty_status") or "").strip().lower()
    if qty_status in ("completed", "partial"):
        return True
    return False


def po_is_fully_delivered(order: dict) -> bool:
    order = order or {}
    if is_fully_delivered(order):
        return True
    required = packing_required_total(order)
    delivered = qty_delivered(order)
    if required > 0 and delivered >= required:
        return True
    return False


def legacy_fully_delivered_without_loading(order: dict) -> bool:
    """Read-model only: already Terkirim, no stored Loading evidence."""
    return po_is_fully_delivered(order) and not has_canonical_loading_evidence(order)


def remaining_order_qty_for_line(order: dict, line_idx: int) -> int:
    po_qty = po_qty_for_line(order, int(line_idx))
    if po_qty <= 0:
        return 0
    delivered = 0
    for sh in active_shipments_stored(order):
        if not shipment_is_delivered(order, sh):
            continue
        sid = delivery_batch_identity(sh) or canonical_shipment_id(sh)
        delivered += sj_qty_for_line(order, sid, int(line_idx)) if sid else 0
    if delivered <= 0:
        # Fall back to PO-level remainder spread only when no per-SJ delivery facts.
        total_delivered = qty_delivered(order)
        if total_delivered > 0 and _line_count(order) == 1:
            delivered = min(po_qty, total_delivered)
    return max(0, po_qty - delivered)


def remaining_sj_qty_for_line(order: dict, surat_jalan_id: str, line_idx: int) -> int:
    need = sj_qty_for_line(order, surat_jalan_id, int(line_idx))
    sh = find_shipment(order, surat_jalan_id)
    already = 0
    if shipment_is_delivered(order, sh):
        already = need
    elif sh:
        batch_delivered = qty_delivered_for_shipment(order, sh)
        batch_need = delivery_batch_qty(sh)
        if batch_need > 0 and need > 0 and batch_delivered > 0:
            already = min(need, int(round(need * (batch_delivered / batch_need))))
    return max(0, need - already)


def remaining_loaded_qty_for_sj_line(order: dict, surat_jalan_id: str, line_idx: int) -> int:
    loaded = loaded_qty_for_sj_line(order, surat_jalan_id, int(line_idx))
    if loaded <= 0 and len(active_shipments_stored(order)) <= 1:
        from quantity_rules import qty_loaded_for_line
        loaded = qty_loaded_for_line(order, int(line_idx))
    delivered_from_load = remaining_sj_qty_for_line(order, surat_jalan_id, int(line_idx))
    already_delivered = max(0, sj_qty_for_line(order, surat_jalan_id, int(line_idx)) - delivered_from_load)
    return max(0, loaded - already_delivered)


def _resolve_target_shipments(order: dict, shipment: Optional[dict]) -> List[dict]:
    if isinstance(shipment, dict) and shipment_is_active(shipment):
        return [shipment]
    return [sh for sh in active_shipments_stored(order) if not shipment_is_delivered(order, sh)]


def _intended_line_qty(order: dict, sid: str, shipment: dict, line_idx: int) -> int:
    sj_left = remaining_sj_qty_for_line(order, sid, int(line_idx))
    if sj_left > 0:
        return sj_left
    loaded_left = remaining_loaded_qty_for_sj_line(order, sid, int(line_idx))
    if loaded_left > 0:
        return loaded_left
    if _line_count(order) == 1:
        return max(0, delivery_batch_qty(shipment) - qty_delivered_for_shipment(order, shipment))
    return 0


def _line_requests_for_shipment(order: dict, shipment: dict, increment: Optional[int]) -> List[Tuple[int, int]]:
    sid = delivery_batch_identity(shipment) or canonical_shipment_id(shipment)
    lines = shipment.get("lines") if isinstance(shipment.get("lines"), list) else []
    requests: List[Tuple[int, int]] = []
    if lines:
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            try:
                idx = int(ln.get("line_idx"))
            except (TypeError, ValueError):
                continue
            need = _intended_line_qty(order, sid, shipment, idx)
            if need <= 0:
                continue
            requests.append((idx, need))
    else:
        for idx in range(_line_count(order)):
            need = _intended_line_qty(order, sid, shipment, idx)
            if need > 0:
                requests.append((idx, need))
    if increment is not None and requests:
        remaining_inc = max(0, int(increment))
        scaled: List[Tuple[int, int]] = []
        for idx, need in requests:
            take = min(need, remaining_inc)
            if take > 0:
                scaled.append((idx, take))
            remaining_inc -= take
            if remaining_inc <= 0:
                break
        return scaled
    return requests


def new_delivery_block_reason(
    order: dict,
    shipment: Optional[dict] = None,
    *,
    requested_qty: Optional[int] = None,
) -> Optional[str]:
    """Block a *new* delivery mutation. Never used to rewrite historical rows."""
    order = order or {}
    current_delivered = qty_delivered(order)
    # Already fully delivered: no new qty to authorize. Do not rewrite history.
    if po_is_fully_delivered(order) and (
        requested_qty is None or _safe_int(requested_qty) <= current_delivered
    ):
        return None
    if requested_qty is not None:
        try:
            req = int(requested_qty)
        except (TypeError, ValueError):
            return MSG_DELIVERY_QTY_INVALID
        if req < 0:
            return MSG_DELIVERY_QTY_INVALID
        if req == 0:
            return MSG_DELIVERY_QTY_ZERO

    targets = _resolve_target_shipments(order, shipment)
    if not targets:
        if not (
            order.get("surat_jalan_created")
            or order.get("delivery_note_number")
            or order.get("shipments")
        ):
            return MSG_DELIVERY_SJ_REQUIRED
        if not has_canonical_loading_evidence(order):
            return MSG_DELIVERY_LOADING_INCOMPLETE
        return MSG_DELIVERY_LOADING_INCOMPLETE

    if not has_canonical_loading_evidence(order):
        return MSG_DELIVERY_LOADING_INCOMPLETE

    increment = None
    if requested_qty is not None:
        increment = max(0, int(requested_qty) - qty_delivered(order))
        if increment <= 0 and int(requested_qty) > 0:
            # Cumulative already at/above request — nothing new to gate.
            return None

    any_line = False
    for sh in targets:
        sid = delivery_batch_identity(sh) or canonical_shipment_id(sh)
        if not sid:
            return MSG_DELIVERY_LOADING_INCOMPLETE
        for line_idx, need in _line_requests_for_shipment(order, sh, increment):
            any_line = True
            if need <= 0:
                return MSG_DELIVERY_QTY_ZERO
            loaded_left = remaining_loaded_qty_for_sj_line(order, sid, line_idx)
            sj_left = remaining_sj_qty_for_line(order, sid, line_idx)
            order_left = remaining_order_qty_for_line(order, line_idx)
            if need > loaded_left or loaded_left <= 0:
                return MSG_DELIVERY_LOADING_INCOMPLETE
            if sj_left > 0 and need > sj_left:
                return MSG_DELIVERY_LOADING_INCOMPLETE
            if order_left > 0 and need > order_left:
                return f"Qty dikirim melebihi sisa qty order untuk item ini ({order_left})."
    if not any_line and increment:
        return MSG_DELIVERY_LOADING_INCOMPLETE
    if not any_line and not has_canonical_loading_evidence(order):
        return MSG_DELIVERY_LOADING_INCOMPLETE
    return None


def dispatch_stage_states(order: dict) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Canonical SJ / Loading / Pengiriman states + optional reasons."""
    order = order or {}
    reasons: Dict[str, str] = {}
    full = po_is_fully_delivered(order)
    delivered = qty_delivered(order)
    has_sj = bool(
        order.get("surat_jalan_created")
        or order.get("delivery_note_number")
        or active_shipments_stored(order)
        or (
            order.get("delivery_note_number")
            and order.get("delivery_note_created_at")
            and order.get("delivery_note_created_by")
        )
    )
    has_load = has_canonical_loading_evidence(order)
    load_sem = str(order.get("loading_qty_status") or "").strip().lower()

    if full:
        sj = "completed"
    elif has_load or delivered > 0:
        sj = "completed"
    elif has_sj:
        sj = "active"
    else:
        sj = "pending"

    if full and not has_load:
        loading = "skipped"
        reasons["loading"] = REASON_LEGACY_NO_LOADING
    elif load_sem == "completed" or (has_load and full):
        loading = "completed"
    elif has_load:
        from quantity_rules import loading_status_semantic
        sem = load_sem or loading_status_semantic(order)
        loading = "completed" if sem == "completed" else "active"
    else:
        loading = "pending"

    if full:
        kirim = "completed"
    elif delivered > 0 or str(order.get("delivery_status") or "") == "Sebagian Terkirim":
        kirim = "active"
    else:
        kirim = "pending"

    return {"sj": sj, "loading": loading, "kirim": kirim}, reasons
