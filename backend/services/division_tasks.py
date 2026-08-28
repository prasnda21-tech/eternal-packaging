"""Normalized per-division work queues. No extra permission system.

Own-task vs view-all uses existing submit perms + schedule.edit /
dashboard.production.view. Operator rows are assignment-scoped (email/user id),
never PO print totals.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from quantity_rules import (
    can_confirm_delivery,
    eligible_delivery_qty,
    helper_prepared_for_line,
    helper_target_for_line,
    is_fully_delivered,
    is_print_line,
    packing_line_breakdown,
    print_qty_total,
    has_surat_jalan_file,
    qty_confirmed_loading,
    available_for_loading_confirm_line,
    qty_loaded,
    requires_printing,
    valid_delivery_proof_count,
)
from workflow_gates import group2_complete

VIEW_ALL_PERMS = frozenset({
    "schedule.edit",
    "dashboard.production.view",
    "dashboard.production.input",
})

DIVISION_DEFS = {
    "designer": {
        "id": "designer",
        "slug": "designer",
        "label": "Designer",
        "submit_perm": "design.upload",
        "nav_key": "nav.tasks.designer",
        "required_input": "Upload Design",
        "required_input_type": "upload_design",
        "action_label": "Kerjakan",
    },
    "kepala_produksi": {
        "id": "kepala_produksi",
        "slug": "kepala-produksi",
        "label": "Kepala Produksi",
        "submit_perm": "schedule.edit",
        "nav_key": "nav.tasks.kepala_produksi",
        "required_input": "Penjadwalan",
        "required_input_type": "penjadwalan",
        "action_label": "Jadwalkan",
    },
    "screener": {
        "id": "screener",
        "slug": "screener",
        "label": "Screener",
        "submit_perm": "screen.update",
        "nav_key": "nav.tasks.screener",
        "required_input": "Qty Screener",
        "required_input_type": "qty_screener",
        "action_label": "Kerjakan",
    },
    "helper": {
        "id": "helper",
        "slug": "helper",
        "label": "Helper",
        "submit_perm": "helper.prepare",
        "nav_key": "nav.tasks.helper",
        "required_input": "Qty Prepared",
        "required_input_type": "qty_prepared",
        "action_label": "Kerjakan",
    },
    "operator": {
        "id": "operator",
        "slug": "operator",
        "label": "Operator",
        "submit_perm": "operator.update",
        "nav_key": "nav.tasks.operator",
        "required_input": "Qty Cetak",
        "required_input_type": "qty_cetak",
        "action_label": "Kerjakan",
    },
    "packing": {
        "id": "packing",
        "slug": "packing",
        "label": "Packing",
        "submit_perm": "packing.update",
        "nav_key": "nav.tasks.packing",
        "required_input": "Qty Packing",
        "required_input_type": "qty_packing",
        "action_label": "Kerjakan",
    },
    "surat_jalan": {
        "id": "surat_jalan",
        "slug": "surat-jalan",
        "label": "Surat Jalan",
        "submit_perm": "delivery_note.manage",
        "nav_key": "nav.tasks.surat_jalan",
        "required_input": "Upload File Surat Jalan",
        "required_input_type": "upload_sj",
        "action_label": "Kerjakan",
    },
    "loading": {
        "id": "loading",
        "slug": "loading",
        "label": "Loading",
        "submit_perm": "loading.update",
        "nav_key": "nav.tasks.loading",
        "required_input": "Konfirmasi Item & Qty",
        "required_input_type": "qty_loading",
        "action_label": "Kerjakan",
    },
    "terkirim": {
        "id": "terkirim",
        "slug": "delivery",
        "label": "Driver / Terkirim",
        "submit_perm": "delivery.update",
        "nav_key": "nav.tasks.delivery",
        "required_input": "Upload Foto",
        "required_input_type": "upload_foto",
        "action_label": "Upload Foto",
    },
}

SLUG_TO_DIVISION = {d["slug"]: key for key, d in DIVISION_DEFS.items()}
SLUG_TO_DIVISION.update({key: key for key in DIVISION_DEFS})

TASK_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "customer_name": 1,
    "outlet": 1,
    "cancelled": 1,
    "hold": 1,
    "current_stage": 1,
    "delivery_status": 1,
    "delivered_at": 1,
    "line_items": 1,
    "operator_splits": 1,
    "screener_info": 1,
    "helper_prep": 1,
    "packing_info": 1,
    "loading_info": 1,
    "loading_checklist": 1,
    "surat_jalan_files": 1,
    "surat_jalan_created": 1,
    "delivery_note": 1,
    "shipments": 1,
    "loading_confirmations": 1,
    "delivery_proofs": 1,
    "sample_approved": 1,
    "sample_status": 1,
    "design_status": 1,
    "design_bypassed": 1,
    "design_files.id": 1,
    "design_files.name": 1,
    "design_files.uploaded_at": 1,
    "customer_approval": 1,
    "customer_approval_history": 1,
    "payment_status": 1,
    "payment_bypassed": 1,
}


def _safe_int(v, default: int = 0) -> int:
    try:
        if v is None or str(v).strip() == "":
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def resolve_division(raw: str) -> Optional[str]:
    key = (raw or "").strip()
    return SLUG_TO_DIVISION.get(key)


def can_view_all_division(perms: Iterable[str]) -> bool:
    p = set(perms or [])
    return bool(p & VIEW_ALL_PERMS)


def _qty_status(done: int, target: int) -> str:
    if done <= 0:
        return "belum"
    if target > 0 and done >= target:
        return "selesai"
    return "sebagian"


def _status_label(code: str) -> str:
    return {
        "belum": "Belum Mulai",
        "sebagian": "Sebagian",
        "siap": "Siap Konfirmasi",
        "selesai": "Selesai",
    }.get(code, code)


def _line_name(order: dict, idx: Optional[int]) -> str:
    lis = order.get("line_items") or []
    if idx is None:
        names = [li.get("name") for li in lis if li.get("name")]
        if len(names) == 1:
            return names[0]
        if names:
            return f"{len(names)} item"
        return "—"
    if 0 <= int(idx) < len(lis):
        return lis[int(idx)].get("name") or f"Line #{int(idx) + 1}"
    return f"Line #{int(idx) + 1}"


def _line_id(order: dict, idx: Optional[int]) -> Optional[str]:
    lis = order.get("line_items") or []
    if idx is None or not (0 <= int(idx) < len(lis)):
        return None
    li = lis[int(idx)]
    return li.get("id") or li.get("line_id")


def _customer(order: dict) -> str:
    return order.get("customer") or order.get("customer_name") or ""


def _base_row(order: dict, division: str, *, line_idx=None, split_id=None) -> dict:
    cfg = DIVISION_DEFS[division]
    oid = order.get("id")
    tid = f"{division}:{oid}:{line_idx if line_idx is not None else 'po'}:{split_id or '-'}"
    return {
        "task_id": tid,
        "order_id": oid,
        "order_number": order.get("po_number"),
        "customer_name": _customer(order),
        "line_id": _line_id(order, line_idx),
        "line_idx": line_idx,
        "item": _line_name(order, line_idx),
        "division": division,
        "work_type": division,
        "assigned_user_id": None,
        "assigned_user_email": None,
        "assigned_user_name": None,
        "target_qty": None,
        "completed_qty": None,
        "remaining_qty": None,
        "status": "belum",
        "status_label": _status_label("belum"),
        "required_input": cfg["required_input"],
        "required_input_type": cfg["required_input_type"],
        "available_actions": ["kerjakan"],
        "action_label": cfg["action_label"],
        "hold": bool(order.get("hold")),
    }


def _split_is_mine(split: dict, user: dict) -> bool:
    email = (user.get("email") or "").strip().lower()
    uid = str(user.get("id") or "").strip()
    se = (split.get("operator_email") or "").strip().lower()
    su = str(split.get("operator_user_id") or split.get("operator_id") or "").strip()
    if email and se and se == email:
        return True
    if uid and su and su == uid:
        return True
    return False


def _sj_file_count(order: dict) -> int:
    files = order.get("surat_jalan_files") or []
    n = sum(1 for f in files if isinstance(f, dict) and (f.get("id") or f.get("storage_path")))
    if n:
        return n
    if order.get("surat_jalan_created") or order.get("delivery_note"):
        return 1
    shipments = order.get("shipments") or []
    return sum(1 for s in shipments if isinstance(s, dict) and (s.get("id") or s.get("file_id")))


def _screener_done(order: dict) -> int:
    sc = order.get("screener_info") or {}
    entries = sc.get("entries") or []
    if entries:
        return sum(max(0, _safe_int(e.get("qty"))) for e in entries if not e.get("cancelled"))
    return max(0, _safe_int(sc.get("screen_qty")))


def _helper_done_for_line(order: dict, idx: int) -> int:
    v = helper_prepared_for_line(order, idx)
    if v is not None:
        return max(0, v)
    hp = order.get("helper_prep") or {}
    items = hp.get("items") or []
    if items:
        return 0
    print_idxs = [i for i, li in enumerate(order.get("line_items") or []) if is_print_line(li)]
    if len(print_idxs) == 1 and print_idxs[0] == int(idx):
        raw = hp.get("qty_prepared")
        if raw is None or str(raw).strip() == "":
            return 0
        return max(0, _safe_int(raw))
    return 0


def _has_design_file(order: dict) -> bool:
    files = order.get("design_files") or []
    return any(
        isinstance(f, dict) and (f.get("id") or f.get("name") or f.get("uploaded_at") or f.get("storage_path"))
        for f in files
    )


def _latest_design_upload_at(order: dict) -> Optional[str]:
    times = [
        f.get("uploaded_at")
        for f in (order.get("design_files") or [])
        if isinstance(f, dict) and f.get("uploaded_at")
    ]
    return max(str(t) for t in times) if times else None


def _customer_revisi_at(order: dict) -> Optional[str]:
    snap = order.get("customer_approval") or {}
    if (snap.get("status") or "").strip().lower() == "rejected":
        return snap.get("responded_at") or snap.get("generated_at") or ""
    last = None
    for h in (order.get("customer_approval_history") or []):
        if (h.get("action") or "") == "customer-reject":
            last = h.get("at") or h.get("responded_at")
    return last


def needs_design_upload(order: dict) -> bool:
    """True when Designer still needs to upload / re-upload. No fake assignment."""
    if order.get("design_bypassed"):
        return False
    if not _has_design_file(order):
        return True
    rev = _customer_revisi_at(order)
    if rev is None:
        return False
    uploaded = _latest_design_upload_at(order)
    if not uploaded:
        return True
    if rev == "":
        return True
    return str(uploaded) <= str(rev)


def _design_status_label(order: dict) -> str:
    if order.get("design_bypassed"):
        return "Bypass"
    if order.get("design_status") == "Approved":
        return "Approved"
    if needs_design_upload(order) and _has_design_file(order):
        return "Revisi"
    if _has_design_file(order):
        return "Menunggu approval"
    return "Belum ada design"


def build_designer_tasks(order: dict) -> List[dict]:
    if order.get("design_bypassed") and not _has_design_file(order):
        return []
    need = needs_design_upload(order)
    if not need and not _has_design_file(order):
        return []
    row = _base_row(order, "designer")
    row["design_status"] = order.get("design_status") or "Waiting"
    row["design_status_label"] = _design_status_label(order)
    row["required_input"] = "Upload Design"
    row["required_input_type"] = "upload_design"
    row["action_label"] = "Kerjakan"
    if need:
        row["status"] = "belum"
    else:
        row["status"] = "selesai"
    row["status_label"] = _status_label(row["status"])
    return [row]


def active_scheduled_qty_for_line(order: dict, line_idx: int) -> int:
    """SUM active operator assignment qty for one printable line.

    Empty schedule is 0 — do not fall back to printable qty.
    `quantity` is the current assignment (cancel already subtracted).
    """
    total = 0
    idx = int(line_idx)
    for s in (order.get("operator_splits") or []):
        if s.get("line_idx") is None:
            continue
        if _safe_int(s.get("line_idx"), default=-1) == idx:
            total += max(0, _safe_int(s.get("quantity")))
    return total


def build_kepala_produksi_tasks(order: dict) -> List[dict]:
    if not requires_printing(order):
        return []
    if not group2_complete(order):
        return []
    out = []
    for idx, li in enumerate(order.get("line_items") or []):
        if not is_print_line(li):
            continue
        printable = max(0, _safe_int(li.get("quantity")))
        if printable <= 0:
            continue
        scheduled = max(0, active_scheduled_qty_for_line(order, idx))
        remaining = max(0, printable - scheduled)
        row = _base_row(order, "kepala_produksi", line_idx=idx)
        row["target_qty"] = printable
        row["completed_qty"] = scheduled
        row["remaining_qty"] = remaining
        row["required_input"] = "Penjadwalan"
        row["required_input_type"] = "penjadwalan"
        row["action_label"] = "Jadwalkan"
        if remaining <= 0 and scheduled > 0:
            row["status"] = "selesai"
            row["status_label"] = "Selesai Dijadwalkan"
        elif scheduled <= 0:
            row["status"] = "belum"
            row["status_label"] = _status_label("belum")
        else:
            row["status"] = "sebagian"
            row["status_label"] = _status_label("sebagian")
        out.append(row)
    return out


def build_screener_tasks(order: dict) -> List[dict]:
    if not requires_printing(order):
        return []
    if not (order.get("operator_splits") or []):
        return []
    target = print_qty_total(order)
    if target <= 0:
        return []
    done = _screener_done(order)
    row = _base_row(order, "screener")
    row["target_qty"] = target
    row["completed_qty"] = done
    row["remaining_qty"] = max(0, target - done)
    row["status"] = _qty_status(done, target)
    row["status_label"] = _status_label(row["status"])
    return [row]


def build_helper_tasks(order: dict) -> List[dict]:
    if not requires_printing(order):
        return []
    splits = order.get("operator_splits") or []
    if not splits:
        return []
    out = []
    for idx, li in enumerate(order.get("line_items") or []):
        if not is_print_line(li):
            continue
        target = helper_target_for_line(order, idx)
        if target <= 0:
            continue
        done = _helper_done_for_line(order, idx)
        row = _base_row(order, "helper", line_idx=idx)
        row["target_qty"] = target
        row["completed_qty"] = done
        row["remaining_qty"] = max(0, target - done)
        row["status"] = _qty_status(done, target)
        row["status_label"] = _status_label(row["status"])
        out.append(row)
    return out


def build_operator_tasks(order: dict, user: dict, *, mine_only: bool) -> List[dict]:
    if not requires_printing(order):
        return []
    out = []
    lis = order.get("line_items") or []
    for s in order.get("operator_splits") or []:
        qty = max(0, _safe_int(s.get("quantity")))
        if qty <= 0:
            continue
        try:
            idx = int(s.get("line_idx"))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(lis)) or not is_print_line(lis[idx]):
            continue
        if mine_only and not _split_is_mine(s, user):
            continue
        done = max(0, _safe_int(s.get("qty_completed")))
        row = _base_row(order, "operator", line_idx=idx, split_id=s.get("id"))
        row["assigned_user_id"] = s.get("operator_user_id") or s.get("operator_id")
        row["assigned_user_email"] = s.get("operator_email")
        row["assigned_user_name"] = s.get("operator_name")
        row["target_qty"] = qty
        row["completed_qty"] = done
        row["remaining_qty"] = max(0, qty - done)
        row["status"] = _qty_status(done, qty)
        row["status_label"] = _status_label(row["status"])
        out.append(row)
    return out


def build_packing_tasks(order: dict) -> List[dict]:
    out = []
    for br in packing_line_breakdown(order):
        avail = max(0, _safe_int(br.get("available")))
        packed = max(0, _safe_int(br.get("packed")))
        if avail <= 0 and packed <= 0:
            continue
        target = avail if avail > 0 else packed
        row = _base_row(order, "packing", line_idx=br.get("line_idx"))
        row["item"] = br.get("name") or row["item"]
        row["target_qty"] = target
        row["completed_qty"] = packed
        row["remaining_qty"] = max(0, _safe_int(br.get("remaining"), target - packed))
        row["status"] = _qty_status(packed, target)
        row["status_label"] = _status_label(row["status"])
        out.append(row)
    return out


def build_surat_jalan_tasks(order: dict) -> List[dict]:
    files = _sj_file_count(order)
    if is_fully_delivered(order) and files <= 0:
        return []
    row = _base_row(order, "surat_jalan")
    row["target_qty"] = None
    row["completed_qty"] = files
    row["remaining_qty"] = None
    if files > 0:
        row["status"] = "selesai"
        row["required_input"] = "Upload File Surat Jalan"
        row["action_label"] = "Kerjakan"
    else:
        row["status"] = "belum"
    row["status_label"] = _status_label(row["status"])
    row["sj_file_count"] = files
    row["sj_status"] = "Ada file" if files else "Belum upload"
    return [row]


def build_loading_tasks(order: dict) -> List[dict]:
    if not has_surat_jalan_file(order) and not order.get("surat_jalan_created") and _sj_file_count(order) <= 0:
        return []
    done = qty_confirmed_loading(order) or qty_loaded(order)
    remaining = 0
    lis = order.get("line_items") or []
    n = len(lis) if lis else 1
    for i in range(n):
        remaining += max(0, available_for_loading_confirm_line(order, i))
    target = done + remaining
    row = _base_row(order, "loading")
    row["target_qty"] = target if target > 0 else None
    row["completed_qty"] = done
    row["remaining_qty"] = remaining if remaining > 0 else 0
    if remaining <= 0 and done > 0:
        row["status"] = "selesai"
        row["required_input"] = "Konfirmasi Item & Qty"
        row["action_label"] = "Kerjakan"
    elif done > 0:
        row["status"] = "sebagian"
        row["required_input"] = "Konfirmasi Item & Qty"
        row["action_label"] = "Kerjakan"
    elif remaining <= 0:
        row["status"] = "belum"
        row["required_input"] = "Konfirmasi Item & Qty"
        row["action_label"] = "Kerjakan"
    else:
        row["status"] = "siap"
        row["required_input"] = "Konfirmasi Item & Qty"
        row["action_label"] = "Konfirmasi Loading"
    row["status_label"] = _status_label(row["status"])
    row["sj_file_count"] = _sj_file_count(order)
    return [row]


def build_delivery_tasks(order: dict) -> List[dict]:
    photos = valid_delivery_proof_count(order)
    confirmed = is_fully_delivered(order) or bool(order.get("delivered_at")) or (
        (order.get("delivery_status") or "") == "Terkirim"
    )
    eligible = max(0, eligible_delivery_qty(order))
    if photos <= 0 and not confirmed and eligible <= 0:
        return []
    row = _base_row(order, "terkirim")
    row["photo_count"] = photos
    row["target_qty"] = 2
    row["completed_qty"] = photos
    row["remaining_qty"] = max(0, 2 - photos)
    if confirmed:
        row["status"] = "selesai"
        row["required_input"] = "Konfirmasi Terkirim"
        row["required_input_type"] = "confirm_terkirim"
        row["action_label"] = "Kerjakan"
        row["available_actions"] = ["kerjakan"]
    elif can_confirm_delivery(order):
        row["status"] = "siap"
        row["required_input"] = "Konfirmasi Terkirim"
        row["required_input_type"] = "confirm_terkirim"
        row["action_label"] = "Konfirmasi Terkirim"
        row["available_actions"] = ["kerjakan", "confirm_terkirim"]
        row["remaining_qty"] = 0
    elif photos > 0:
        row["status"] = "sebagian"
        row["required_input"] = "Upload Foto"
        row["required_input_type"] = "upload_foto"
        row["action_label"] = "Upload Foto"
    else:
        row["status"] = "belum"
        row["required_input"] = "Upload Foto"
        row["required_input_type"] = "upload_foto"
        row["action_label"] = "Upload Foto"
    row["status_label"] = _status_label(row["status"])
    return [row]


BUILDERS = {
    "designer": lambda o, u, mine: build_designer_tasks(o),
    "kepala_produksi": lambda o, u, mine: build_kepala_produksi_tasks(o),
    "screener": lambda o, u, mine: build_screener_tasks(o),
    "helper": lambda o, u, mine: build_helper_tasks(o),
    "operator": lambda o, u, mine: build_operator_tasks(o, u, mine_only=mine),
    "packing": lambda o, u, mine: build_packing_tasks(o),
    "surat_jalan": lambda o, u, mine: build_surat_jalan_tasks(o),
    "loading": lambda o, u, mine: build_loading_tasks(o),
    "terkirim": lambda o, u, mine: build_delivery_tasks(o),
}


def build_division_tasks(
    orders: Iterable[dict],
    division: str,
    user: dict,
    *,
    mine_only: bool,
) -> List[dict]:
    builder = BUILDERS.get(division)
    if not builder:
        return []
    tasks: List[dict] = []
    for o in orders:
        if o.get("cancelled"):
            continue
        tasks.extend(builder(o, user, mine_only))
    return tasks
