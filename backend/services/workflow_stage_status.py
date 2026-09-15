"""Per-stage chips for Alur Pekerjaan / Live Tracking.

Mirrors frontend `resolveStageState` + `resolveTimelineChip` so public tracking
does not invent a second workflow engine. Evidence helpers come from
quantity_rules + workflow_gates.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from quantity_rules import (
    PRINT_STATUS_DONE,
    PRINT_STATUS_IN_PROGRESS,
    delivery_batch_identity,
    delivery_batch_qty,
    eligible_delivery_qty,
    is_fully_delivered,
    loaded_qty_for_sj_line,
    loading_confirmations_stored,
    loading_status_semantic,
    match_po_line_idx,
    print_status,
    qty_delivered,
    qty_packed,
    requires_printing,
    shipment_is_delivered,
    sj_lines_stored,
    surat_jalan_created,
    surat_jalan_status_semantic,
)
from services.status_po import STAGES
from workflow_gates import (
    customer_design_approved,
    group2_complete,
    helper_done,
    packing_unlocked,
    penjadwalan_done,
    sample_ok,
    sample_uploaded,
    screener_done,
    workflow_ready,
)

WORKFLOW_STAGES = STAGES
PUBLIC_COMPLETED = "completed"
PUBLIC_IN_PROGRESS = "in_progress"
PUBLIC_WAITING = "waiting"

STAGE_DESIGN = 1
STAGE_APPROVAL_DESIGN = 2
STAGE_PAYMENT = 3
STAGE_APPROVAL_PAYMENT = 4
STAGE_PENJADWALAN = 5
STAGE_SCREENER = 6
STAGE_HELPER = 7
STAGE_OPERATOR = 8
STAGE_KONFIRMASI_SAMPLE = 9
STAGE_CETAK = 10
STAGE_PACKING = 11
STAGE_SURAT_JALAN = 12
STAGE_LOADING = 13
STAGE_TERKIRIM = 14


def _has_value(v) -> bool:
    return v is not None and str(v).strip() != ""


def _has_real_design_upload(order: dict) -> bool:
    files = order.get("design_files") or []
    if not isinstance(files, list) or not files:
        return False
    for f in files:
        if not isinstance(f, dict):
            continue
        if _has_value(f.get("data_url")) or _has_value(f.get("url")):
            return True
        if _has_value(f.get("storage_path")):
            return True
        if _has_value(f.get("name")) and _has_value(f.get("uploaded_at")):
            return True
    return False


def _packing_qty_input(order: dict) -> bool:
    lines = (order.get("packing_info") or {}).get("lines")
    if isinstance(lines, list) and lines:
        return any(_safe_packed(it) > 0 for it in lines)
    return qty_packed(order) > 0


def _safe_packed(it) -> int:
    if not isinstance(it, dict):
        return 0
    try:
        return max(0, int(it.get("packed_qty") or 0))
    except (TypeError, ValueError):
        return 0


def _cs(order: dict) -> int:
    try:
        return int(order.get("current_stage") or 0)
    except (TypeError, ValueError):
        return 0


def _loading_completed(order: dict) -> bool:
    if loading_status_semantic(order) == "completed":
        return True
    lc = order.get("loading_checklist") or {}
    return bool(lc.get("items_loaded")) and bool(lc.get("seal_ok"))


def _terkirim_confirmed(order: dict) -> bool:
    return bool(order.get("terkirim_confirmed")) or is_fully_delivered(order) or order.get("delivery_status") == "Terkirim"


def resolve_stage_chip(idx: int, order: dict, current_stage: int | None = None) -> Dict[str, str]:
    """Same labels as Alur Pekerjaan (`resolveStageState` / `resolveTimelineChip`)."""
    order = order or {}
    cs = _cs(order) if current_stage is None else int(current_stage or 0)
    ready = workflow_ready(order)
    req_print = requires_printing(order)

    if idx == STAGE_DESIGN:
        if order.get("design_bypassed"):
            return {"state": "bypass", "label": "BYPASS"}
        if _has_real_design_upload(order):
            return {"state": "done", "label": "Selesai"}
        return {"state": "current", "label": "Belum dikerjakan"}

    if idx == STAGE_APPROVAL_DESIGN:
        if order.get("design_status") == "Approved" or customer_design_approved(order):
            return {"state": "done", "label": "Selesai"}
        if order.get("design_bypassed"):
            return {"state": "bypass", "label": "BYPASS"}
        if not _has_real_design_upload(order):
            return {"state": "pending", "label": "Menunggu"}
        return {"state": "current", "label": "Menunggu approval"}

    if idx == STAGE_PAYMENT:
        if order.get("payment_bypassed"):
            return {"state": "bypass", "label": "BYPASS"}
        if order.get("payment_files"):
            return {"state": "done", "label": "Selesai"}
        return {"state": "current", "label": "Belum dikerjakan"}

    if idx == STAGE_APPROVAL_PAYMENT:
        if order.get("payment_status") == "Approved":
            return {"state": "done", "label": "Selesai"}
        if order.get("payment_bypassed"):
            return {"state": "bypass", "label": "BYPASS"}
        if order.get("payment_files"):
            return {"state": "current", "label": "Menunggu approval"}
        return {"state": "pending", "label": "Menunggu"}

    non_print_penj_done = (not req_print) and (
        _has_value(order.get("work_date")) or penjadwalan_done(order) or cs > STAGE_PENJADWALAN
    )

    if idx == STAGE_PENJADWALAN:
        if not req_print:
            if non_print_penj_done or group2_complete(order):
                return {"state": "done", "label": "Selesai"}
            if ready.get("penjadwalan"):
                return {"state": "current", "label": "Menunggu jadwal"}
            return {"state": "pending", "label": "Menunggu"}
        if penjadwalan_done(order):
            return {"state": "done", "label": "Selesai"}
        if ready.get("penjadwalan"):
            return {"state": "current", "label": "Menunggu jadwal"}
        return {"state": "pending", "label": "Menunggu"}

    if idx == STAGE_SCREENER:
        if not req_print:
            if non_print_penj_done or group2_complete(order):
                return {"state": "done", "label": "Selesai"}
            return {"state": "pending", "label": "Menunggu"}
        if not penjadwalan_done(order):
            return {"state": "pending", "label": "Menunggu"}
        if screener_done(order):
            return {"state": "done", "label": "Selesai"}
        if ready.get("screener"):
            return {"state": "current", "label": "Menunggu input"}
        return {"state": "pending", "label": "Menunggu"}

    if idx == STAGE_HELPER:
        if not req_print:
            if non_print_penj_done or group2_complete(order):
                return {"state": "done", "label": "Selesai"}
            return {"state": "pending", "label": "Menunggu"}
        if not penjadwalan_done(order):
            return {"state": "pending", "label": "Menunggu"}
        if helper_done(order):
            return {"state": "done", "label": "Selesai"}
        if ready.get("helper"):
            return {"state": "current", "label": "Menunggu input"}
        return {"state": "pending", "label": "Menunggu"}

    if idx == STAGE_OPERATOR:
        if not req_print:
            if non_print_penj_done or group2_complete(order):
                return {"state": "done", "label": "Selesai"}
            return {"state": "pending", "label": "Menunggu"}
        if not helper_done(order):
            return {"state": "pending", "label": "Menunggu"}
        if sample_uploaded(order):
            return {"state": "done", "label": "Selesai"}
        if ready.get("operator"):
            return {"state": "current", "label": "Menunggu upload sampel"}
        return {"state": "pending", "label": "Menunggu"}

    if idx == STAGE_KONFIRMASI_SAMPLE:
        if not req_print:
            if non_print_penj_done or group2_complete(order):
                return {"state": "done", "label": "Selesai"}
            return {"state": "pending", "label": "Menunggu"}
        if not helper_done(order):
            return {"state": "pending", "label": "Menunggu"}
        if sample_ok(order):
            return {"state": "done", "label": "Selesai"}
        if sample_uploaded(order):
            return {"state": "current", "label": "Menunggu approval sampel"}
        return {"state": "pending", "label": "Menunggu"}

    if idx == STAGE_CETAK:
        if not req_print:
            if non_print_penj_done or group2_complete(order):
                return {"state": "done", "label": "Selesai"}
            return {"state": "pending", "label": "Menunggu"}
        if not sample_ok(order):
            return {"state": "pending", "label": "Menunggu"}
        st = print_status(order)
        if st == PRINT_STATUS_DONE:
            return {"state": "done", "label": "SELESAI CETAK"}
        if not ready.get("print_qty"):
            return {"state": "pending", "label": "Menunggu"}
        if st == PRINT_STATUS_IN_PROGRESS:
            return {"state": "current", "label": "BELUM SELESAI CETAK"}
        return {"state": "current", "label": "BELUM MULAI CETAK"}

    if idx == STAGE_PACKING:
        if req_print and not sample_ok(order):
            return {"state": "pending", "label": "Menunggu"}
        if not req_print:
            if not (group2_complete(order) and penjadwalan_done(order)):
                return {"state": "pending", "label": "Menunggu"}
            # Full non-cetak: Packing di-skip — lanjut SJ/Loading tanpa packed_qty.
            return {"state": "done", "label": "Dilewati"}
        if cs >= 12:
            return {"state": "done", "label": "Selesai"}
        if _packing_qty_input(order) and cs == idx:
            return {"state": "current", "label": "Berjalan"}
        if packing_unlocked(order) or ready.get("packing") or cs >= idx:
            return {"state": "current", "label": "Menunggu input qty packing"}
        return {"state": "pending", "label": "Menunggu"}

    if idx == STAGE_SURAT_JALAN:
        created = surat_jalan_created(order) or bool(order.get("surat_jalan_created"))
        sj_status = order.get("surat_jalan_status_semantic") or surat_jalan_status_semantic(order)
        loading_qty = order.get("loading_qty_status") or loading_status_semantic(order)
        if created:
            if sj_status == "completed" or loading_qty == "completed":
                return {"state": "done", "label": "Selesai"}
            if sj_status == "partial":
                return {"state": "current", "label": "SJ parsial — siap Loading"}
            return {"state": "current", "label": "SJ dibuat — siap Loading"}
        if not _packing_qty_input(order) and cs < STAGE_SURAT_JALAN:
            return {"state": "pending", "label": "Menunggu"}
        if cs >= idx:
            return {"state": "current", "label": "Menunggu pembuatan Surat Jalan"}
        return {"state": "pending", "label": "Menunggu"}

    if idx == STAGE_LOADING:
        verification = order.get("loading_verification_status") if isinstance(order.get("loading_verification_status"), dict) else None
        if verification and verification.get("label"):
            code = str(verification.get("code") or "")
            if "complete" in code:
                return {"state": "done", "label": verification.get("label")}
            return {"state": "current", "label": verification.get("label")}
        confirmations = order.get("loading_confirmations") if isinstance(order.get("loading_confirmations"), list) else []
        has_confirm = len(confirmations) > 0
        qty_status = order.get("loading_qty_status") or loading_status_semantic(order)
        info = order.get("loading_info") or {}
        has_legacy = (
            bool((info.get("lines") or []) if isinstance(info, dict) else [])
            or bool((info.get("events") or []) if isinstance(info, dict) else [])
            or bool(order.get("loading_completed"))
            or qty_status in ("completed", "partial")
        )
        if has_confirm:
            if qty_status == "completed" or _loading_completed(order):
                return {"state": "done", "label": "Selesai"}
            if qty_status == "partial":
                return {"state": "current", "label": "Parsial"}
            return {"state": "current", "label": "Verifikasi Kesesuaian SJ"}
        created = surat_jalan_created(order) or bool(order.get("surat_jalan_created"))
        if created:
            return {"state": "current", "label": "Verifikasi Kesesuaian SJ"}
        if has_legacy and not has_confirm:
            return {"state": "done", "label": "Riwayat Loading"}
        if _terkirim_confirmed(order) or order.get("delivery_status") == "Terkirim":
            return {"state": "done", "label": "Dilewati"}
        return {"state": "pending", "label": "Menunggu Surat Jalan"}

    if idx == STAGE_TERKIRIM:
        if _terkirim_confirmed(order) or order.get("delivery_status") == "Terkirim":
            return {"state": "done", "label": "Selesai"}
        if order.get("delivery_status") == "Sebagian Terkirim" or qty_delivered(order) > 0:
            return {"state": "current", "label": "Parsial"}
        try:
            raw_el = order.get("eligible_delivery_qty")
            eligible = int(raw_el if raw_el is not None else eligible_delivery_qty(order) or 0)
        except (TypeError, ValueError):
            eligible = eligible_delivery_qty(order)
        if eligible > 0:
            return {"state": "current", "label": "Menunggu bukti pengiriman"}
        created = surat_jalan_created(order) or bool(order.get("surat_jalan_created"))
        if created:
            return {"state": "pending", "label": "Menunggu konfirmasi Loading"}
        if cs >= STAGE_SURAT_JALAN:
            return {"state": "pending", "label": "Menunggu konfirmasi Loading"}
        return {"state": "pending", "label": "Menunggu"}

    if idx < cs:
        return {"state": "done", "label": "Selesai"}
    if idx == cs:
        return {"state": "current", "label": "Berjalan"}
    return {"state": "pending", "label": "Menunggu"}


def chip_to_public_status(state: str) -> str:
    if state in ("done", "bypass"):
        return PUBLIC_COMPLETED
    if state == "current":
        return PUBLIC_IN_PROGRESS
    return PUBLIC_WAITING


def workflow_stage_rows(order: dict) -> List[Dict[str, str]]:
    """Exactly 15 Alur Pekerjaan stages, same order/labels/status text."""
    cs = _cs(order)
    rows = []
    for idx, name in enumerate(WORKFLOW_STAGES):
        chip = resolve_stage_chip(idx, order, cs)
        rows.append({
            "stage": name,
            "status": chip_to_public_status(chip["state"]),
            "label": chip["label"],
        })
    return rows


# PWA Status per Role — same chips as Alur Pekerjaan, not a second engine.
ROLE_STAGE_INDEX = {
    "cup": STAGE_HELPER,
    "screen": STAGE_SCREENER,
    "prod": STAGE_CETAK,
    "pack": STAGE_PACKING,
    "load": STAGE_LOADING,
    "kirim": STAGE_TERKIRIM,
}


def chip_to_role_tone(state: str) -> str:
    """done/bypass → done (green); current → pending (orange); else idle (dash)."""
    if state in ("done", "bypass"):
        return "done"
    if state == "current":
        return "pending"
    return "idle"


def role_status_from_order(order: dict) -> Dict[str, str]:
    """PO-level Cup/Screen/Prod/Pack/Load/Kirim from resolve_stage_chip."""
    cs = _cs(order)
    return {
        key: chip_to_role_tone(resolve_stage_chip(idx, order, cs)["state"])
        for key, idx in ROLE_STAGE_INDEX.items()
    }


def _shipment_identity_keys(shipment: Optional[dict]) -> set:
    keys = set()
    if not isinstance(shipment, dict):
        return keys
    for k in ("id", "sales_delivery_id", "number", "sj_number", "file_id"):
        val = str(shipment.get(k) or "").strip()
        if val:
            keys.add(val)
    return keys


def _confirmation_for_shipment(order: dict, shipment: Optional[dict]) -> bool:
    keys = _shipment_identity_keys(shipment)
    if not keys:
        return False
    for conf in loading_confirmations_stored(order):
        if str(conf.get("surat_jalan_id") or "").strip() in keys:
            return True
    return False


def _sj_loaded_vs_need(order: dict, shipment: Optional[dict]) -> tuple:
    """Loaded qty vs this SJ's own lines — existing SJ loading helpers only."""
    need = delivery_batch_qty(shipment)
    sj_id = delivery_batch_identity(shipment)
    if not sj_id:
        return 0, need
    lines = sj_lines_stored(order, sj_id)
    if not lines and isinstance(shipment, dict) and isinstance(shipment.get("lines"), list):
        lines = [ln for ln in shipment["lines"] if isinstance(ln, dict)]
    loaded = 0
    line_need = 0
    for ln in lines:
        idx, err = match_po_line_idx(order, ln)
        if err or idx is None:
            try:
                idx = int(ln.get("line_idx"))
            except (TypeError, ValueError):
                continue
        qty = ln.get("qty") if isinstance(ln, dict) and ln.get("qty") is not None else (
            ln.get("quantity") if isinstance(ln, dict) else 0
        )
        try:
            line_need += max(0, int(qty or 0))
        except (TypeError, ValueError):
            pass
        loaded += loaded_qty_for_sj_line(order, sj_id, int(idx))
    if line_need > 0:
        need = line_need
    return loaded, need


def _load_tone_for_shipment(order: dict, shipment: dict) -> str:
    """Loading chip meaning, scoped to one SJ so SJ1 cannot mark SJ2 done."""
    loaded, need = _sj_loaded_vs_need(order, shipment)
    if need > 0 and loaded >= need:
        return "done"
    if loaded > 0 or _confirmation_for_shipment(order, shipment):
        return "pending"
    # Canonical SJ row exists → same as Loading current after Surat Jalan created.
    return "pending"


def _kirim_tone_for_shipment(order: dict, shipment: dict) -> str:
    """Pengiriman chip meaning, scoped to one SJ."""
    if shipment_is_delivered(order, shipment):
        return "done"
    loaded, need = _sj_loaded_vs_need(order, shipment)
    if need > 0 and loaded >= need:
        return "pending"
    return "idle"


def role_status_for_schedule_row(order: dict, shipment: Optional[dict] = None) -> Dict[str, str]:
    """Schedule Status per Role: PO chips + SJ-scoped Load/Kirim when a shipment is present."""
    roles = role_status_from_order(order)
    if isinstance(shipment, dict) and delivery_batch_identity(shipment):
        roles["load"] = _load_tone_for_shipment(order, shipment)
        roles["kirim"] = _kirim_tone_for_shipment(order, shipment)
    return roles
