"""Pure quantity invariants for production workflow (print vs non-print).

Used by server endpoints and unit tests. No DB / FastAPI dependency.
"""
from __future__ import annotations

from typing import List, Optional


def is_print_line(li: dict) -> bool:
    """Legacy line tanpa production_process dianggap print (backward compat)."""
    p = (li.get("production_process") or "").strip().lower()
    return p != "non_print"


def requires_printing(order: dict) -> bool:
    lis = order.get("line_items") or []
    if not lis:
        return True
    any_print = False
    any_classified = False
    for li in lis:
        p = (li.get("production_process") or "").strip().lower()
        if p in ("print", "non_print"):
            any_classified = True
            if p == "print":
                any_print = True
    if not any_classified:
        return True
    return any_print


def print_line_indices(order: dict) -> list:
    return [i for i, li in enumerate(order.get("line_items") or []) if is_print_line(li)]


def _safe_int(v, default=0) -> int:
    try:
        if v is None or str(v).strip() == "":
            return default
        return int(v)
    except (ValueError, TypeError):
        return default


def print_qty_total(order: dict) -> int:
    """Qty yang boleh di-assign Operator / disiapkan Helper (scope Cetak saja)."""
    lis = order.get("line_items") or []
    if not lis:
        # Legacy PO tanpa line_items: seluruh total_quantity dianggap Cetak.
        return max(0, _safe_int(order.get("total_quantity")))
    total = 0
    for li in lis:
        if is_print_line(li):
            total += max(0, _safe_int(li.get("quantity")))
    return total


def packing_required_total(order: dict) -> int:
    """Target Packing = seluruh line (print + non_print)."""
    total = 0
    for li in (order.get("line_items") or []):
        total += max(0, _safe_int(li.get("quantity")))
    if total <= 0:
        total = max(0, _safe_int(order.get("total_quantity")))
    if total <= 0:
        # Legacy synthetic/old docs used `quantity` instead of total_quantity.
        total = max(0, _safe_int(order.get("quantity")))
    return total


def non_print_qty_total(order: dict) -> int:
    return max(0, packing_required_total(order) - print_qty_total(order))


def print_completed_qty(order: dict) -> int:
    """Sum qty_completed dari operator_splits (tidak boleh negatif)."""
    total = 0
    for s in (order.get("operator_splits") or []):
        total += max(0, _safe_int(s.get("qty_completed")))
    return total


def operator_assigned_qty(order: dict) -> int:
    total = 0
    for s in (order.get("operator_splits") or []):
        total += max(0, _safe_int(s.get("quantity")))
    return total


PRINT_STATUS_NOT_STARTED = "BELUM MULAI CETAK"
PRINT_STATUS_IN_PROGRESS = "BELUM SELESAI CETAK"
PRINT_STATUS_DONE = "SELESAI CETAK"


def print_qty_target(order: dict) -> int:
    """Target produksi Cetak = Qty Cetak Penjadwalan (assigned), bukan total PO.

    Fallback ke print_qty_total hanya jika belum ada assignment.
    """
    assigned = operator_assigned_qty(order)
    if assigned > 0:
        return assigned
    return print_qty_total(order)


def print_qty_remaining(order: dict) -> int:
    return max(0, print_qty_target(order) - print_completed_qty(order))


def print_status(order: dict) -> str:
    """Status Cetak dari qty selesai vs target Penjadwalan — bukan current_stage."""
    if not requires_printing(order):
        return ""
    completed = print_completed_qty(order)
    target = print_qty_target(order)
    if completed <= 0:
        return PRINT_STATUS_NOT_STARTED
    if target > 0 and completed >= target:
        return PRINT_STATUS_DONE
    return PRINT_STATUS_IN_PROGRESS


def helper_prepared_print_qty(order: dict) -> Optional[int]:
    """Qty Helper Persiapan untuk CETAK.

    None = belum diinput → tidak menambah cap di atas print_qty_total
    (kompatibel PO lama / Penjadwalan sebelum Helper mengisi).
    Nilai 0 yang eksplisit adalah hard cap 0.
    """
    hp = order.get("helper_prep") or {}
    v = hp.get("qty_prepared")
    if v is None or str(v).strip() == "":
        return None
    print_total = print_qty_total(order)
    return max(0, min(_safe_int(v), print_total))


def helper_prepared_for_line(order: dict, line_idx: int) -> Optional[int]:
    """Per-line helper prepared actual. None jika items per-line belum ada.

    Tidak di-clamp ke qty_required: kelebihan persiapan vs jadwal harus tetap terbaca.
    """
    hp = order.get("helper_prep") or {}
    items = hp.get("items") or []
    for it in items:
        if _safe_int(it.get("line_idx"), default=-1) == int(line_idx):
            return max(0, _safe_int(it.get("qty_prepared")))
    return None


def helper_target_qty(order: dict) -> int:
    """Target Persiapan Helper = active operator schedule. Bukan actual Helper.

    Jika belum ada assignment, fallback ke qty Cetak PO.
    """
    splits = order.get("operator_splits") or []
    if splits:
        return operator_assigned_qty(order)
    return print_qty_total(order)


def helper_target_for_line(order: dict, line_idx: int) -> int:
    """Target Helper per line Cetak dari active schedule."""
    splits = order.get("operator_splits") or []
    idx = int(line_idx)
    if splits:
        total = 0
        for s in splits:
            if s.get("line_idx") is None:
                continue
            if _safe_int(s.get("line_idx"), default=-1) == idx:
                total += max(0, _safe_int(s.get("quantity")))
        return total
    lis = order.get("line_items") or []
    if 0 <= idx < len(lis):
        return max(0, _safe_int(lis[idx].get("quantity")))
    return 0


def helper_over_prepared_qty(order: dict) -> int:
    """Actual Helper di atas target jadwal. 0 jika tidak kelebihan."""
    hp = order.get("helper_prep") or {}
    raw = hp.get("qty_prepared")
    if raw is None or str(raw).strip() == "":
        return 0
    return max(0, _safe_int(raw) - helper_target_qty(order))


def operator_assignment_cap(order: dict) -> int:
    """Cap Penjadwalan Operator = qty Cetak PO. Tidak dibatasi Helper Prepared."""
    return print_qty_total(order)


def print_available_for_packing(order: dict) -> int:
    """Qty CETAK yang benar-benar bisa di-packing: min(completed, helper prepared)."""
    completed = print_completed_qty(order)
    prepared = helper_prepared_print_qty(order)
    if prepared is None:
        return completed
    return max(0, min(completed, prepared))


def packing_lines_stored(order: dict) -> Optional[list]:
    """Persisted per-line packed rows. None = PO masih pakai qty_packed total lama."""
    raw = (order.get("packing_info") or {}).get("lines")
    if not isinstance(raw, list) or not raw:
        return None
    return raw


def _po_line_identity_keys(li: Optional[dict]) -> list:
    if not isinstance(li, dict):
        return []
    out = []
    for k in ("line_id", "id"):
        v = str(li.get(k) or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def qty_packed_for_line(order: dict, line_idx: int) -> int:
    """Packed qty satu line. Legacy tanpa packing_info.lines → 0 (jangan asumsi).

    Packing rows use the same identity hierarchy as SJ/Loading
    (``match_po_line_idx``): unique line_id, then accurate_item_id, then
    explicit line_idx. A stale packing ``line_idx`` must not win over a
    unique ``line_id``. Ambiguous matches stay 0.
    """
    lines = packing_lines_stored(order)
    if lines is None:
        return 0
    idx = int(line_idx)
    hits = []
    for it in lines:
        if not isinstance(it, dict):
            continue
        resolved, _err = match_po_line_idx(order, it)
        if resolved is None:
            continue
        if int(resolved) == idx:
            hits.append(it)
    if len(hits) == 1:
        return max(0, _safe_int(hits[0].get("packed_qty")))
    return 0


def packed_qty_for_line(order: dict, line_idx: int) -> int:
    lines = packing_lines_stored(order)
    if lines is not None:
        return qty_packed_for_line(order, line_idx)
    pi = (order.get("packing_info") or {}).get("qty_packed")
    if pi is not None and str(pi).strip() != "":
        total_p = max(0, _safe_int(pi))
        if int(line_idx) == 0 and len(order.get("line_items") or []) <= 1:
            return total_p
    if not order.get("packing_info"):
        lis = order.get("line_items") or []
        if 0 <= int(line_idx) < len(lis):
            return max(0, _safe_int(lis[line_idx].get("quantity")))
    if _safe_int(order.get("current_stage")) >= 11:
        lis = order.get("line_items") or []
        if 0 <= int(line_idx) < len(lis):
            return max(0, _safe_int(lis[line_idx].get("quantity")))
    return 0


def qty_packed(order: dict) -> int:
    """Total packed = SUM(line packed) bila ada packing_info.lines, else qty_packed lama."""
    lines = packing_lines_stored(order)
    if lines is not None:
        return sum(max(0, _safe_int(it.get("packed_qty"))) for it in lines)
    pi = (order.get("packing_info") or {}).get("qty_packed")
    if pi is not None and str(pi).strip() != "":
        return max(0, _safe_int(pi))
    return max(0, _safe_int(order.get("qty_packed")))


def qty_packed_print(order: dict) -> int:
    """Qty packing pada line Cetak. Legacy tanpa lines: packed − non_print."""
    lines = packing_lines_stored(order)
    lis = order.get("line_items") or []
    if lines is None:
        return max(0, qty_packed(order) - non_print_qty_total(order))
    total = 0
    for it in lines:
        idx = _safe_int(it.get("line_idx"), default=-1)
        if not lis:
            total += max(0, _safe_int(it.get("packed_qty")))
            continue
        if 0 <= idx < len(lis) and is_print_line(lis[idx]):
            total += max(0, _safe_int(it.get("packed_qty")))
    return total


def qty_delivered(order: dict) -> int:
    """Persisted qty_delivered. Legacy full-deliver hanya jika status/stage final."""
    explicit = order.get("qty_delivered")
    if explicit is not None and str(explicit).strip() != "":
        return max(0, _safe_int(explicit))
    # Legacy: PO benar-benar Terkirim penuh tanpa field qty_delivered.
    # JANGAN anggap partial (Sebagian Terkirim + delivered_at) sebagai penuh.
    if (
        order.get("delivery_status") == "Terkirim"
        and _safe_int(order.get("current_stage")) >= 14
    ):
        return packing_required_total(order)
    return 0


def available_for_packing(order: dict) -> int:
    """Barang fisik yang boleh di-packing (Cetak saja).

    Non-cetak skip Packing — tidak masuk availability Packing dan tidak
    memakai packed_qty sintetis. Loading non-cetak memakai PO qty.
    """
    lis = order.get("line_items") or []
    if lis and print_qty_total(order) <= 0:
        return 0
    required = print_qty_total(order) if lis else packing_required_total(order)
    avail = print_available_for_packing(order)
    if required <= 0:
        return max(0, avail)
    return max(0, min(required, avail))


def print_completed_for_line(order: dict, line_idx: int) -> int:
    """Qty cetak selesai Operator untuk satu line. Split tanpa line_idx diabaikan."""
    total = 0
    for s in (order.get("operator_splits") or []):
        if s.get("line_idx") is None:
            continue
        if _safe_int(s.get("line_idx"), default=-1) == int(line_idx):
            total += max(0, _safe_int(s.get("qty_completed")))
    return total


def _unassigned_print_completed(order: dict) -> int:
    total = 0
    for s in (order.get("operator_splits") or []):
        if s.get("line_idx") is None:
            total += max(0, _safe_int(s.get("qty_completed")))
    return total


def available_for_packing_line(order: dict, line_idx: int) -> int:
    """Availability satu line. Tidak memakai sisa availability line lain.

    Cetak: MIN(print_completed, helper_prepared, qty_po)
    Non-cetak: 0 (skip Packing — Loading memakai PO qty).
    """
    lis = order.get("line_items") or []
    if not lis:
        return available_for_packing(order) if int(line_idx) == 0 else 0
    idx = int(line_idx)
    if idx < 0 or idx >= len(lis):
        return 0
    li = lis[idx]
    qty = max(0, _safe_int(li.get("quantity")))
    if not is_print_line(li):
        return 0
    completed = print_completed_for_line(order, idx)
    print_idxs = print_line_indices(order)
    if len(print_idxs) == 1 and print_idxs[0] == idx:
        completed += _unassigned_print_completed(order)
    completed = min(completed, qty)
    prepared = helper_prepared_for_line(order, idx)
    if prepared is None and helper_prepared_print_qty(order) is not None and len(print_idxs) == 1:
        prepared = helper_prepared_print_qty(order)
    if prepared is None:
        return min(qty, completed)
    return min(qty, completed, prepared)


def packing_line_breakdown(order: dict) -> list:
    """Derived per-line packing tracker (tidak persist).

    packed per line dari packing_info.lines. Legacy total-only tidak
    dianggap packed pada line (jangan asumsi).
    """
    lis = list(order.get("line_items") or [])
    if not lis:
        total = max(0, _safe_int(order.get("total_quantity")))
        if total <= 0:
            return []
        avail = available_for_packing(order)
        packed = qty_packed_for_line(order, 0)
        if packing_lines_stored(order) is None:
            packed = min(qty_packed(order), avail)
        return [{
            "line_idx": 0,
            "name": "Total PO",
            "color": None,
            "production_process": "print",
            "is_print": True,
            "qty_po": total,
            "helper_prepared": helper_prepared_print_qty(order),
            "print_completed": print_completed_qty(order),
            "print_remaining": max(0, total - print_completed_qty(order)),
            "available": avail,
            "packed": packed,
            "remaining": max(0, avail - packed),
        }]

    print_idxs = print_line_indices(order)
    helper_agg = helper_prepared_print_qty(order)
    rows = []
    for idx, li in enumerate(lis):
        # Non-cetak skip Packing — tidak dirender di daftar Packing.
        if not is_print_line(li):
            continue
        qty = max(0, _safe_int(li.get("quantity")))
        completed = print_completed_for_line(order, idx)
        if len(print_idxs) == 1 and print_idxs[0] == idx:
            completed += _unassigned_print_completed(order)
        completed = min(completed, qty)
        prepared = helper_prepared_for_line(order, idx)
        if prepared is None and helper_agg is not None and len(print_idxs) == 1:
            prepared = helper_agg
        print_remaining = max(0, qty - completed)
        avail = available_for_packing_line(order, idx)
        packed = qty_packed_for_line(order, idx)
        rows.append({
            "line_idx": idx,
            "name": li.get("name") or f"Line #{idx + 1}",
            "color": li.get("color"),
            "production_process": "print",
            "is_print": True,
            "qty_po": qty,
            "helper_prepared": prepared,
            "print_completed": completed,
            "print_remaining": print_remaining,
            "available": avail,
            "packed": packed,
            "remaining": max(0, avail - packed),
        })
    return rows


def packing_status_semantic(order: dict) -> str:
    """Belum / Berjalan / Sudah — quantity-aware, backward-compatible dengan stage.

    Non-cetak skip Packing: target Packing = qty Cetak saja. Full non-print → Sudah.
    """
    cs = _safe_int(order.get("current_stage"))
    lis = order.get("line_items") or []
    if lis and print_qty_total(order) <= 0:
        return "Sudah"
    required = print_qty_total(order) if lis else packing_required_total(order)
    packed = qty_packed_print(order) if lis else qty_packed(order)

    if required > 0 and packed >= required:
        return "Sudah"
    # Legacy stage>=12 tanpa qty detail / sudah ditandai Sudah
    if cs >= 12 and packed <= 0 and (order.get("packing_status") == "Sudah" or required <= 0):
        return "Sudah"
    if cs < 11 and packed <= 0:
        return "Belum"
    if packed > 0 or cs >= 11:
        return "Berjalan"
    return "Belum"


def apply_confirmed_packing_progress(order: dict) -> bool:
    """After packing qty is confirmed, packed qty is immediately eligible downstream.

    Partial packing opens Surat Jalan (stage 12). Does not invent packed qty.
    Non-cetak tidak dihitung sebagai target Packing.
    Returns True if packing_status or current_stage changed.
    """
    packed_total = qty_packed(order)
    if packed_total <= 0:
        return False
    changed = False
    lis = order.get("line_items") or []
    required = print_qty_total(order) if lis else packing_required_total(order)
    packed = qty_packed_print(order) if lis else packed_total
    status = "Sudah" if (required <= 0 or (required and packed >= required)) else "Berjalan"
    if order.get("packing_status") != status:
        order["packing_status"] = status
        changed = True
    cs = _safe_int(order.get("current_stage"))
    if cs < 12:
        order["current_stage"] = 12
        changed = True
    return changed


def validate_operator_assignment_total(
    total_split: int,
    print_total: int,
    prepared_print_qty: Optional[int] = None,
) -> Optional[str]:
    """Scheduling is free of Helper qty. prepared_print_qty is ignored (kept for callers).

    Actual Operator print still uses validate_print_completed_total vs Helper.
    """
    if total_split < 0:
        return "Qty assignment tidak boleh negatif."
    if total_split > print_total:
        return (
            f"Total pembagian Operator ({total_split}) melebihi total qty Cetak PO ({print_total}). "
            "Operator hanya untuk barang dengan proses Cetak."
        )
    return None


def validate_print_completed_total(completed_total: int, order: dict) -> Optional[str]:
    if completed_total < 0:
        return "Qty cetak selesai tidak boleh negatif."
    target = print_qty_target(order)
    if target > 0 and completed_total > target:
        return (
            f"Total qty cetak selesai ({completed_total}) melebihi Qty Cetak Penjadwalan ({target})."
        )
    print_total = print_qty_total(order)
    if completed_total > print_total:
        return (
            f"Total qty cetak selesai ({completed_total}) melebihi qty Cetak PO ({print_total})."
        )
    prepared = helper_prepared_print_qty(order)
    if prepared is not None and completed_total > prepared:
        return (
            f"Total qty cetak selesai ({completed_total}) melebihi qty Helper Persiapan ({prepared}). "
            "Operator tidak boleh menyelesaikan qty Cetak yang belum disiapkan Helper."
        )
    return None


def validate_helper_prepared_qty(prepared: int, order: dict) -> Optional[str]:
    if prepared < 0:
        return "Qty persiapan tidak boleh negatif."
    print_total = print_qty_total(order)
    if prepared > print_total:
        return (
            f"Qty disiapkan Helper ({prepared}) melebihi total qty Cetak ({print_total})."
        )
    completed = print_completed_qty(order)
    if prepared < completed:
        return (
            f"Qty persiapan Helper ({prepared}) tidak boleh lebih kecil dari "
            f"qty cetak selesai Operator ({completed})."
        )
    packed_print = qty_packed_print(order)
    if prepared < packed_print:
        return (
            f"Qty persiapan Helper ({prepared}) tidak boleh lebih kecil dari "
            f"qty cetak yang sudah di-packing ({packed_print})."
        )
    return None


def validate_packing_qty(packed: int, order: dict) -> Optional[str]:
    if packed is None or packed <= 0:
        return "Qty Packing tidak valid."
    required = packing_required_total(order)
    avail = available_for_packing(order)
    delivered = qty_delivered(order)
    if required and packed > required:
        return f"Qty Packing ({packed}) melebihi total qty PO ({required})."
    if packed > avail:
        return (
            f"Qty Packing ({packed}) melebihi qty tersedia untuk packing ({avail}). "
            "Qty Cetak baru tersedia setelah Operator menyelesaikan "
            "(tidak melebihi qty Helper Persiapan); "
            "Qty Tidak Cetak tersedia tanpa menunggu Operator."
        )
    if packed < delivered:
        return (
            f"Qty Packing ({packed}) tidak boleh lebih kecil dari qty sudah dikirim ({delivered})."
        )
    return None


def validate_packing_lines(updates: list, order: dict) -> Optional[str]:
    """Validasi packed_qty kumulatif per line. updates: [{line_idx, packed_qty}]."""
    lis = list(order.get("line_items") or [])
    merged = {}
    existing = packing_lines_stored(order) or []
    for it in existing:
        idx = _safe_int(it.get("line_idx"), default=-1)
        merged[idx] = max(0, _safe_int(it.get("packed_qty")))
    if not updates:
        return "Qty Packing per line wajib diisi."
    for u in updates:
        idx = _safe_int(u.get("line_idx"), default=-1)
        qty = _safe_int(u.get("packed_qty"))
        if not lis:
            if idx not in (0, -1) and idx != 0:
                return f"line_idx {idx} tidak valid."
            idx = 0
            avail = available_for_packing(order)
            po = packing_required_total(order)
            if qty > po:
                return f"Qty Packing ({qty}) melebihi total qty PO ({po})."
            if qty > avail:
                return (
                    f"Qty Packing ({qty}) melebihi qty tersedia untuk packing ({avail})."
                )
            merged[0] = qty
            continue
        if idx < 0 or idx >= len(lis):
            return f"line_idx {idx} tidak valid."
        if qty < 0:
            return "Qty Packing tidak boleh negatif."
        name = lis[idx].get("name") or f"Line #{idx + 1}"
        if not is_print_line(lis[idx]):
            return (
                f"Line '{name}' adalah non-cetak — tidak perlu Packing "
                "(langsung ke Loading setelah prerequisite valid)."
            )
        po = max(0, _safe_int(lis[idx].get("quantity")))
        avail = available_for_packing_line(order, idx)
        if qty > po:
            return f"Qty Packing '{name}' ({qty}) melebihi qty PO ({po})."
        if qty > avail:
            return (
                f"Qty Packing '{name}' ({qty}) melebihi qty tersedia untuk packing "
                f"line tersebut ({avail})."
            )
        merged[idx] = qty
    total = sum(max(0, v) for v in merged.values())
    if total <= 0:
        return "Qty Packing tidak valid."
    delivered = qty_delivered(order)
    if total < delivered:
        return (
            f"Qty Packing ({total}) tidak boleh lebih kecil dari qty sudah dikirim ({delivered})."
        )
    required = packing_required_total(order)
    if required and total > required:
        return f"Qty Packing ({total}) melebihi total qty PO ({required})."
    packed_print = 0
    if lis:
        for idx, q in merged.items():
            if 0 <= idx < len(lis) and is_print_line(lis[idx]):
                packed_print += max(0, q)
    else:
        packed_print = total
    print_cap = print_available_for_packing(order)
    if packed_print > print_cap:
        return (
            f"Total qty packing Cetak ({packed_print}) melebihi qty Cetak tersedia "
            f"untuk packing ({print_cap})."
        )
    return None


def merge_packing_line_updates(order: dict, updates: list) -> list:
    """Gabungkan packed per line; hasil dipakai persist packing_info.lines."""
    lis = list(order.get("line_items") or [])
    n = len(lis) if lis else 1
    merged = {i: qty_packed_for_line(order, i) for i in range(n)}
    for u in updates:
        idx = _safe_int(u.get("line_idx"), default=-1)
        if not lis:
            idx = 0
        merged[idx] = max(0, _safe_int(u.get("packed_qty")))
    return [{"line_idx": i, "packed_qty": merged.get(i, 0)} for i in range(n)]


def packing_events_stored(order: dict) -> list:
    return list((order.get("packing_info") or {}).get("events") or [])


def apply_packing_increments(order: dict, updates: list) -> tuple:
    """Append packing events; packing_info.lines stays the running total."""
    lis = list(order.get("line_items") or [])
    n = len(lis) if lis else 1
    merged = {i: qty_packed_for_line(order, i) for i in range(n)}
    events = list(packing_events_stored(order))
    for u in updates:
        idx = _safe_int(u.get("line_idx"), default=-1)
        qty = _safe_int(u.get("qty"))
        if qty <= 0:
            continue
        if idx < 0:
            idx = 0
        merged[idx] = merged.get(idx, 0) + qty
        events.append({
            "line_idx": idx,
            "qty": qty,
            "packed_qty_after": merged[idx],
            "action_id": u.get("action_id"),
        })
    lines = [{"line_idx": i, "packed_qty": merged.get(i, 0)} for i in range(n)]
    return lines, events


def validate_delivery_qty(new_delivered: int, order: dict) -> Optional[str]:
    if new_delivered is None or new_delivered < 0:
        return "Qty pengiriman tidak valid."
    if new_delivered == 0:
        return "Qty dikirim harus lebih dari 0."
    required = packing_required_total(order)
    current = qty_delivered(order)
    eligible = eligible_delivery_qty(order)
    if new_delivered > eligible:
        return f"Qty dikirim ({new_delivered}) melebihi qty Loading terkonfirmasi ({eligible})."
    # Physical ceiling that already authorized Loading (print=packed, non_print=PO).
    # Do not use raw packing aggregate — inventory-only / non_print lines intentionally
    # have packed=0 and must not block Delivery after valid Loading.
    physical = sum(
        physical_qty_for_loading_line(order, i) for i in range(_line_count(order))
    )
    if physical > 0 and new_delivered > physical:
        return (
            f"Qty dikirim ({new_delivered}) melebihi qty fisik eligible "
            f"untuk Loading ({physical})."
        )
    shipped = qty_shipped(order)
    if shipped > 0 and new_delivered > shipped:
        return f"Qty dikirim ({new_delivered}) melebihi alokasi Surat Jalan ({shipped})."
    if required and new_delivered > required:
        return f"Qty dikirim ({new_delivered}) melebihi total qty PO ({required})."
    if new_delivered < current:
        return f"Qty dikirim tidak boleh dikurangi (saat ini {current})."
    return None


def apply_delivery_from_proof_evidence(order: dict) -> Optional[dict]:
    """Delivery fields from confirmed Loading after explicit Konfirmasi Terkirim.

    Historical SJ line quantities remain eligible when no loading_confirmations exist.
    Upload of photos does NOT call this — confirmation does.

    When the order uses SJ-linked Loading (loading_confirmations present and/or
    sales-delivery shipments that require Loading), Confirm must pass the same
    Loading-vs-SJ per-line readiness as photo upload — aggregate totals with the
    wrong item composition must not succeed.

    If no active delivery batch/SJ shipment exists yet (reason_code SJ_NOT_READY),
    keep the legacy eligible-qty path from loading_confirmations — the composition
    gate applies only when a concrete SJ batch is present to compare against.
    """
    if loading_confirmations_stored(order) or _shipments_require_loading_confirm(order):
        ready = delivery_batch_photo_readiness(order)
        if not ready.get("allowed") and ready.get("reason_code") != REASON_SJ_NOT_READY:
            return None
    eligible = eligible_delivery_qty(order)
    if eligible <= 0:
        return None
    current = qty_delivered(order)
    new_delivered = max(current, eligible)
    required = packing_required_total(order)
    if required:
        new_delivered = min(new_delivered, required)
    err = validate_delivery_qty(new_delivered, order)
    if err:
        return None
    full = bool(required) and new_delivered >= required
    return {
        "qty_delivered": new_delivered,
        "delivery_status": "Terkirim" if full else "Sebagian Terkirim",
        "full": full,
        "required": required,
        "loaded": eligible,
        "eligible": eligible,
    }


def is_fully_delivered(order: dict) -> bool:
    required = packing_required_total(order)
    if required <= 0:
        return False
    explicit = order.get("qty_delivered")
    if explicit is not None and str(explicit).strip() != "":
        return _safe_int(explicit) >= required
    # Legacy: hanya jika sudah final Terkirim (stage 14), bukan partial.
    if (
        order.get("qty_delivered") is None
        and order.get("delivery_status") == "Terkirim"
        and _safe_int(order.get("current_stage")) >= 14
    ):
        return True
    return False


MIN_DELIVERY_PROOFS = 2
MAX_PUBLIC_DELIVERY_PROOFS = 5


def valid_delivery_proofs(order: dict) -> list:
    return [p for p in (order.get("delivery_proofs") or []) if isinstance(p, dict) and p.get("id")]


def valid_delivery_proof_count(order: dict) -> int:
    return len(valid_delivery_proofs(order))


def can_confirm_delivery(order: dict) -> bool:
    return valid_delivery_proof_count(order) >= MIN_DELIVERY_PROOFS


def confirm_delivery_block_reason(order: dict) -> Optional[str]:
    n = valid_delivery_proof_count(order)
    if n < MIN_DELIVERY_PROOFS:
        return (
            f"Wajib upload minimal {MIN_DELIVERY_PROOFS} foto bukti kirim "
            f"sebelum Konfirmasi Terkirim (saat ini {n})."
        )
    # Electronic recipient signature + name (POD). Lazy import keeps this module pure for most callers.
    try:
        from services.delivery_pod_signature import signature_confirm_block_reason
    except ImportError:
        return "Tanda tangan penerima wajib sebelum Konfirmasi Terkirim."
    sig_blocked = signature_confirm_block_reason(order)
    if sig_blocked:
        return sig_blocked
    # Authoritative Loading-vs-SJ gate when this PO uses the Loading workflow.
    # Historical SJ-only deliveries (no loading_confirmations / no sales_delivery_id
    # requirement) keep the legacy eligible path and are not blocked here.
    # SJ_NOT_READY (no active batch) also keeps the eligible path — composition
    # checks require a concrete SJ shipment to evaluate.
    if loading_confirmations_stored(order) or _shipments_require_loading_confirm(order):
        ready = delivery_batch_photo_readiness(order)
        if not ready.get("allowed") and ready.get("reason_code") != REASON_SJ_NOT_READY:
            return ready.get("reason") or MSG_PHOTO_LOADING_INSUFFICIENT
    return None


# ---------------------------------------------------------------------------
# Qty Screener — workflow gate only (NOT a production / packing / SJ cap)
# ---------------------------------------------------------------------------

def validate_screener_qty(screen_qty, order: Optional[dict] = None) -> Optional[str]:
    """Basic safety for Qty Screener.

    Intentionally ignores helper prepared / required prepared / remaining
    prepared. Those fields cap Cetak, not Screener.
    `order` is accepted so callers can pass the PO without coupling.
    """
    _ = order  # unused by design — prepared qty is not a screener constraint
    if screen_qty is None or str(screen_qty).strip() == "":
        return "Qty Screener tidak valid."
    try:
        if isinstance(screen_qty, bool):
            return "Qty Screener tidak valid."
        q = int(screen_qty)
    except (TypeError, ValueError):
        return "Qty Screener tidak valid."
    if q < 0:
        return "Screen qty tidak boleh negatif"
    return None


# ---------------------------------------------------------------------------
# Partial Surat Jalan + Loading per line
# ---------------------------------------------------------------------------

SHIPMENT_SOURCE_MANUAL = "manual"
SHIPMENT_SOURCE_ACCURATE = "accurate"

SJ_STATUS_PENDING = "pending"
SJ_STATUS_PARTIAL = "partial"
SJ_STATUS_COMPLETED = "completed"


def _line_count(order: dict) -> int:
    lis = order.get("line_items") or []
    return len(lis) if lis else 1


def packed_qty_for_line(order: dict, line_idx: int) -> int:
    """Packed qty one line, including legacy total-only on line 0."""
    if packing_lines_stored(order) is not None:
        return qty_packed_for_line(order, line_idx)
    if int(line_idx) == 0:
        return qty_packed(order)
    return 0


def po_qty_for_line(order: dict, line_idx: int) -> int:
    """Ordered qty for a stable PO line. Never merge by item name."""
    lis = order.get("line_items") or []
    idx = int(line_idx)
    if lis:
        if 0 <= idx < len(lis):
            return max(0, _safe_int(lis[idx].get("quantity")))
        return 0
    if idx == 0:
        return max(0, _safe_int(order.get("total_quantity") or order.get("quantity")))
    return 0


def shipments_stored(order: dict) -> list:
    raw = order.get("shipments")
    return list(raw) if isinstance(raw, list) else []


def has_shipment_line_quantities(order: dict) -> bool:
    for sh in shipments_stored(order):
        lines = sh.get("lines") or []
        if isinstance(lines, list) and any(_safe_int(it.get("qty") or it.get("quantity")) > 0 for it in lines):
            return True
    return False


def qty_shipped_for_line(order: dict, line_idx: int, *, exclude_shipment_id: Optional[str] = None) -> int:
    total = 0
    skip = str(exclude_shipment_id or "").strip()
    for sh in shipments_stored(order):
        sid = str(sh.get("id") or "")
        acc = str(sh.get("accurate_shipment_id") or "")
        if skip and skip in (sid, acc):
            continue
        for it in sh.get("lines") or []:
            if _safe_int(it.get("line_idx"), default=-1) == int(line_idx):
                total += max(0, _safe_int(it.get("qty") if it.get("qty") is not None else it.get("quantity")))
    return total


def qty_shipped(order: dict) -> int:
    return sum(qty_shipped_for_line(order, i) for i in range(_line_count(order)))


def available_for_delivery_note_line(order: dict, line_idx: int, *, exclude_shipment_id: Optional[str] = None) -> int:
    """Sisa qty yang boleh dialokasi ke Surat Jalan per line.

    Cetak: packed − shipped.
    Non-cetak: PO qty − shipped (skip Packing — tidak invent packed_qty).
    """
    physical = physical_qty_for_loading_line(order, line_idx)
    shipped = qty_shipped_for_line(order, line_idx, exclude_shipment_id=exclude_shipment_id)
    return max(0, physical - shipped)


def loading_lines_stored(order: dict) -> Optional[list]:
    raw = (order.get("loading_info") or {}).get("lines")
    if not isinstance(raw, list) or not raw:
        return None
    return raw


def loading_events_stored(order: dict) -> list:
    raw = (order.get("loading_info") or {}).get("events")
    if not isinstance(raw, list):
        return []
    return raw


def _legacy_loaded_qty_for_line(order: dict, line_idx: int) -> int:
    """Historical loading_info totals. Not used for new SJ-linked confirmations."""
    lines = loading_lines_stored(order)
    if lines is not None:
        for it in lines:
            if _safe_int(it.get("line_idx"), default=-1) == int(line_idx):
                return max(0, _safe_int(it.get("loaded_qty")))
        return 0
    events = loading_events_stored(order)
    if events:
        return sum(
            max(0, _safe_int(ev.get("qty")))
            for ev in events
            if _safe_int(ev.get("line_idx"), default=-1) == int(line_idx)
        )
    return 0


def loading_confirmations_stored(order: dict) -> list:
    """Active (non-cancelled) SJ-linked Loading confirmations."""
    raw = order.get("loading_confirmations")
    if not isinstance(raw, list):
        return []
    out = []
    for x in raw:
        if not isinstance(x, dict):
            continue
        if x.get("cancelled") or x.get("deleted"):
            continue
        out.append(x)
    return out


def loading_confirmations_all(order: dict) -> list:
    """All Loading confirmations including cancelled (audit / history)."""
    raw = order.get("loading_confirmations")
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


def find_loading_confirmation(order: dict, confirmation_id: str, *, include_cancelled: bool = False) -> Optional[dict]:
    want = str(confirmation_id or "").strip()
    if not want:
        return None
    source = loading_confirmations_all(order) if include_cancelled else loading_confirmations_stored(order)
    for c in source:
        if str(c.get("id") or "").strip() == want:
            return c
    return None


def confirmation_qty_total(conf: Optional[dict]) -> int:
    if not isinstance(conf, dict):
        return 0
    total = 0
    for ln in conf.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        total += max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
    return total


def delivered_qty_for_loading_confirmation(order: dict, confirmation_id: str) -> int:
    """FIFO allocate persisted qty_delivered onto active Loading confirmations."""
    want = str(confirmation_id or "").strip()
    leftover = max(0, qty_delivered(order))
    for conf in loading_confirmations_stored(order):
        cap = confirmation_qty_total(conf)
        allocated = min(cap, leftover)
        leftover -= allocated
        if str(conf.get("id") or "").strip() == want:
            return allocated
    return 0


def validate_cancel_loading_confirmation(order: dict, confirmation_id: str) -> Optional[str]:
    if is_fully_delivered(order):
        return "Pengiriman PO sudah selesai seluruhnya — Loading tidak bisa dihapus."
    conf = find_loading_confirmation(order, confirmation_id, include_cancelled=False)
    if not conf:
        return "Konfirmasi Loading tidak ditemukan."
    if delivered_qty_for_loading_confirmation(order, confirmation_id) > 0:
        return "Loading ini sudah mempunyai qty terkirim — tidak bisa dihapus."
    return None


def cancel_loading_confirmation(
    order: dict,
    confirmation_id: str,
    *,
    actor: Optional[dict] = None,
    cancelled_at: Optional[str] = None,
    reason: Optional[str] = None,
) -> Optional[dict]:
    """Soft-cancel one Loading confirmation. Preserves SJ files and delivery history."""
    err = validate_cancel_loading_confirmation(order, confirmation_id)
    if err:
        raise ValueError(err)
    actor = actor or {}
    want = str(confirmation_id or "").strip()
    for conf in loading_confirmations_all(order):
        if str(conf.get("id") or "").strip() != want:
            continue
        conf["cancelled"] = True
        conf["cancelled_by"] = actor.get("email")
        conf["cancelled_by_name"] = actor.get("name") or actor.get("email")
        conf["cancelled_at"] = cancelled_at
        conf["cancelled_reason"] = reason or "Hapus data Loading"
        return conf
    return None


def find_sj_file(order: dict, file_id: str) -> Optional[dict]:
    """Live Surat Jalan file only. Shipment leftovers after file delete are not a file."""
    want = str(file_id or "").strip()
    if not want:
        return None
    files = order.get("surat_jalan_files")
    if isinstance(files, list):
        for f in files:
            if isinstance(f, dict) and (
                str(f.get("id") or "").strip() == want
                or str(f.get("shipment_id") or "").strip() == want
                or str(f.get("sj_number") or "").strip() == want
            ):
                return f
    return None


def has_surat_jalan_file(order: dict) -> bool:
    """True only when a live Surat Jalan file entry exists.

    Bare shipments (allocation / sales-delivery rows) are NOT files — Loading and
    FILE TERSIMPAN already require surat_jalan_files. Soft-deleted / missing-file
    ghosts must not keep has-file semantics active.
    """
    files = order.get("surat_jalan_files")
    if isinstance(files, list) and files:
        for f in files:
            if not isinstance(f, dict):
                continue
            if f.get("is_deleted"):
                continue
            if str(f.get("id") or "").strip() and (
                str(f.get("data_url") or "").startswith("data:")
                or str(f.get("storage_path") or "").strip()
                or str(f.get("name") or "").strip()
                or f.get("uploaded_at")
            ):
                return True
    return False


def sj_lines_stored(order: dict, surat_jalan_id: str) -> list:
    """Exact item lines for a specific selected Surat Jalan.

    Loading summary and readiness/shortfall must read the same collection.
    Prefer shipment / sales-delivery lines (current allocation + identity),
    then live file metadata. File-only lines are used when the shipment has
    no line breakdown.
    """
    want = str(surat_jalan_id or "").strip()
    if not want:
        return []
    entry = find_sj_file(order, want)
    target_ids = {want}
    if isinstance(entry, dict):
        for k in ("id", "shipment_id", "sales_delivery_id", "sj_number", "number"):
            v = str(entry.get(k) or "").strip()
            if v:
                target_ids.add(v)
    for sh in shipments_stored(order):
        if not isinstance(sh, dict):
            continue
        aliases = {
            str(sh.get("id") or "").strip(),
            str(sh.get("number") or "").strip(),
            str(sh.get("sales_delivery_id") or "").strip(),
            str(sh.get("file_id") or "").strip(),
        }
        if target_ids & {a for a in aliases if a}:
            sh_lines = sh.get("lines")
            if isinstance(sh_lines, list) and sh_lines:
                return [l for l in sh_lines if isinstance(l, dict)]
    if isinstance(entry, dict):
        lines = entry.get("lines")
        if isinstance(lines, list) and lines:
            return [l for l in lines if isinstance(l, dict)]
    return []


def sj_qty_for_line(order: dict, surat_jalan_id: str, line_idx: int) -> int:
    """Exact target qty for this line in this specific Surat Jalan."""
    lines = sj_lines_stored(order, surat_jalan_id)
    total = 0
    found = False
    want = int(line_idx)
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        qty = max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
        if qty <= 0:
            continue
        idx, _err = match_po_line_idx(order, ln)
        if idx is None:
            if _safe_int(ln.get("line_idx"), default=-1) == want:
                total += qty
                found = True
            continue
        if int(idx) == want:
            total += qty
            found = True
    if found:
        return total
    if not lines and want == 0:
        entry = find_sj_file(order, surat_jalan_id)
        if entry:
            return max(0, _safe_int(entry.get("total_qty") or entry.get("qty") or 0))
    return 0


def loaded_qty_for_sj_line(order: dict, surat_jalan_id: str, line_idx: int) -> int:
    """Cumulative confirmed loaded qty for this exact Surat Jalan and line."""
    want = str(surat_jalan_id or "").strip()
    entry = find_sj_file(order, surat_jalan_id)
    target_ids = {want}
    if entry:
        for k in ("id", "number", "shipment_id", "sales_delivery_id", "sj_number"):
            v = str(entry.get(k) or "").strip()
            if v:
                target_ids.add(v)
    total = 0
    for conf in loading_confirmations_stored(order):
        conf_sj = str(conf.get("surat_jalan_id") or "").strip()
        if conf_sj in target_ids:
            for ln in conf.get("lines") or []:
                if isinstance(ln, dict) and _safe_int(ln.get("line_idx"), default=-1) == int(line_idx):
                    total += max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
    return total


def available_for_sj_loading_line(order: dict, surat_jalan_id: str, line_idx: int) -> int:
    """Available loading for this exact line on this exact Surat Jalan."""
    sj_qty = sj_qty_for_line(order, surat_jalan_id, line_idx)
    loaded_qty = loaded_qty_for_sj_line(order, surat_jalan_id, line_idx)
    sj_avail = max(0, sj_qty - loaded_qty)
    physical_avail = available_for_loading_confirm_line(order, line_idx)
    return min(sj_avail, physical_avail)


def qty_confirmed_loading_for_line(order: dict, line_idx: int) -> int:
    total = 0
    for conf in loading_confirmations_stored(order):
        for ln in conf.get("lines") or []:
            if not isinstance(ln, dict):
                continue
            if _safe_int(ln.get("line_idx"), default=-1) == int(line_idx):
                total += max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
    return total


def qty_confirmed_loading(order: dict) -> int:
    return sum(qty_confirmed_loading_for_line(order, i) for i in range(_line_count(order)))


def qty_loaded_for_line(order: dict, line_idx: int) -> int:
    """Cumulative Loading: new SJ-linked confirmations + historical loaded_qty."""
    return qty_confirmed_loading_for_line(order, line_idx) + _legacy_loaded_qty_for_line(order, line_idx)


def qty_loaded(order: dict) -> int:
    return sum(qty_loaded_for_line(order, i) for i in range(_line_count(order)))


def effective_loaded_qty(order: dict) -> int:
    """Loaded qty for delivery evidence. Historical checklist is read-only fallback."""
    loaded = qty_loaded(order)
    if loaded > 0:
        return loaded
    lc = order.get("loading_checklist") or {}
    if lc.get("items_loaded") and lc.get("seal_ok"):
        return qty_packed(order)
    return 0


def surat_jalan_created(order: dict) -> bool:
    """Current active SJ for stageflow / FILE TERSIMPAN.

    Live file entries count. Legacy stamp-only POs without shipments still count.
    Bare shipments after file delete (or PDF never generated) do NOT count — they
    are allocation history/ghosts and must not disagree with Loading's
    \"Belum ada file Surat Jalan.\"
    """
    if has_surat_jalan_file(order):
        return True
    if shipments_stored(order):
        # Shipment without a live file is not an active SJ document.
        return False
    return bool(
        order.get("delivery_note_number")
        and order.get("delivery_note_created_at")
        and order.get("delivery_note_created_by")
    )


def physical_qty_for_loading_line(order: dict, line_idx: int) -> int:
    """Physical qty eligible for Loading.

    Cetak: packed qty (wajib Packing).
    Non-cetak: PO qty (skip Packing — no synthetic packed_qty).
    """
    lis = order.get("line_items") or []
    idx = int(line_idx)
    if 0 <= idx < len(lis) and not is_print_line(lis[idx]):
        return po_qty_for_line(order, idx)
    return packed_qty_for_line(order, idx)


def loading_cap_for_line(order: dict, line_idx: int) -> int:
    """Historical increment cap = min(physical, shipped-if-SJ-lines).

    Cetak physical = packed. Non-cetak physical = PO qty (skip Packing).
    New SJ-linked confirmations use `available_for_loading_confirm_line`.
    """
    physical = physical_qty_for_loading_line(order, line_idx)
    if has_shipment_line_quantities(order):
        shipped = qty_shipped_for_line(order, line_idx)
        return max(0, min(physical, shipped))
    return max(0, physical)


def loading_confirm_cap_for_line(order: dict, line_idx: int) -> int:
    """Physical cap for SJ-linked Loading confirm; never above PO qty.

    Cetak: packed qty. Non-cetak: PO qty (skip Packing).
    """
    physical = physical_qty_for_loading_line(order, line_idx)
    po = po_qty_for_line(order, line_idx)
    if po > 0:
        return max(0, min(physical, po))
    return max(0, physical)


def _shipments_require_loading_confirm(order: dict) -> bool:
    """True when shipments come from sales-delivery create (incl. early prepare).

    Those records allocate SJ qty but are NOT physical Loading/Pengiriman evidence.
    Historical shipments without sales_delivery_id keep the legacy SJ-qty fallback.
    """
    for sh in shipments_stored(order):
        if not isinstance(sh, dict):
            continue
        if str(sh.get("sales_delivery_id") or "").strip():
            return True
    return False


def eligible_delivery_qty(order: dict) -> int:
    """Qty legally eligible to deliver.

    New workflow: confirmed Loading (SJ-linked). Early-created Pengiriman Pesanan
    shipments must NOT become deliverable merely because the delivery record /
    SJ allocation exists — Loading remains the physical gate.

    Historical POs with SJ item/qty lines and no sales_delivery_id remain
    deliverable without rewriting those records.
    """
    if loading_confirmations_stored(order):
        return qty_confirmed_loading(order)
    legacy = sum(_legacy_loaded_qty_for_line(order, i) for i in range(_line_count(order)))
    if legacy > 0:
        return legacy
    if has_shipment_line_quantities(order) and not _shipments_require_loading_confirm(order):
        return qty_shipped(order)
    return 0


def available_for_loading_confirm_line(order: dict, line_idx: int) -> int:
    """Remaining Loading confirm qty = physical eligible minus already loaded."""
    return max(0, loading_confirm_cap_for_line(order, line_idx) - qty_loaded_for_line(order, line_idx))


def available_for_loading_line(order: dict, line_idx: int) -> int:
    return max(0, loading_cap_for_line(order, line_idx) - qty_loaded_for_line(order, line_idx))


def validate_loading_confirmation(order: dict, surat_jalan_id: str, lines: list) -> Optional[str]:
    """Manual Item/Qty against a specific Surat Jalan. Cap = remaining exact SJ line qty."""
    entry = find_sj_file(order, surat_jalan_id)
    if not entry:
        return "Surat Jalan tidak ditemukan. Loading harus terhubung ke file Surat Jalan."
    if not lines:
        return "Item dan Qty Loading wajib diisi sesuai isi Surat Jalan."
    seen = set()
    n = _line_count(order)
    for u in lines:
        if not isinstance(u, dict):
            return "Item Loading tidak valid."
        idx, err = match_po_line_idx(order, u)
        if err:
            return err
        if idx is None or idx < 0 or idx >= n:
            return f"line_idx {idx} tidak valid."
        if idx in seen:
            return "Item Loading duplikat. Gunakan line PO yang berbeda."
        seen.add(idx)
        qty = _safe_int(u.get("qty") if u.get("qty") is not None else u.get("quantity"))
        if qty <= 0:
            return "Qty Loading harus angka lebih dari 0."

        # If Surat Jalan has specific line quantities, enforce exact SJ availability
        sj_lines = sj_lines_stored(order, surat_jalan_id)
        if sj_lines or (int(idx) == 0 and (entry.get("total_qty") or entry.get("qty"))):
            avail = available_for_sj_loading_line(order, surat_jalan_id, idx)
        else:
            avail = available_for_loading_confirm_line(order, idx)

        name = _line_name(order, idx)
        if qty > avail:
            physical_avail = available_for_loading_confirm_line(order, idx)
            sj_qty_cap = sj_qty_for_line(order, surat_jalan_id, idx)
            loaded_qty_sj = loaded_qty_for_sj_line(order, surat_jalan_id, idx)
            sj_avail = max(0, sj_qty_cap - loaded_qty_sj)
            
            if avail <= 0:
                if physical_avail <= 0:
                    lis = order.get("line_items") or []
                    if 0 <= int(idx) < len(lis) and not is_print_line(lis[int(idx)]):
                        return (
                            f"Loading tidak dapat dilakukan karena qty eligible "
                            f"untuk '{name}' belum tersedia."
                        )
                    return (
                        f"Loading tidak dapat dilakukan karena belum ada qty Packing "
                        f"yang tersedia untuk '{name}'."
                    )
                return (
                    f"Qty Loading '{name}' belum bisa diisi. "
                    "Seluruh kuantitas Surat Jalan untuk item ini sudah selesai di-loading."
                )
            
            if physical_avail < sj_avail and qty > physical_avail:
                lis = order.get("line_items") or []
                if 0 <= int(idx) < len(lis) and not is_print_line(lis[int(idx)]):
                    return (
                        f"Qty Loading '{name}' ({qty}) melebihi qty eligible yang tersedia ({physical_avail})."
                    )
                return (
                    f"Qty Loading '{name}' ({qty}) melebihi qty Packing yang tersedia ({physical_avail})."
                )
            return (
                f"Qty Loading '{name}' ({qty}) melebihi sisa Surat Jalan yang siap loading ({avail})."
            )
    return None


def apply_loading_confirmation(
    order: dict,
    *,
    surat_jalan_id: str,
    lines: list,
    actor: Optional[dict] = None,
    confirmation_id: Optional[str] = None,
    confirmed_at: Optional[str] = None,
    action_id: Optional[str] = None,
) -> dict:
    """Append-only SJ-linked Loading confirmation. Does not rewrite loading_info."""
    actor = actor or {}
    entry = find_sj_file(order, surat_jalan_id) or {}
    normalized = []
    for u in lines:
        idx, _err = match_po_line_idx(order, u)
        qty = _safe_int(u.get("qty") if u.get("qty") is not None else u.get("quantity"))
        row = {
            "line_idx": idx,
            "qty": qty,
            "name": _line_name(order, idx),
        }
        aid = str(u.get("accurate_item_id") or "").strip() or _line_accurate_item_id(order, idx)
        if aid:
            row["accurate_item_id"] = aid
        normalized.append(row)
    record = {
        "id": confirmation_id or "",
        "surat_jalan_id": str(surat_jalan_id).strip(),
        "surat_jalan_name": entry.get("name") or "Surat Jalan",
        "lines": normalized,
        "confirmed_by": actor.get("email"),
        "confirmed_by_name": actor.get("name") or actor.get("email"),
        "confirmed_at": confirmed_at,
        "action_id": action_id,
    }
    order.setdefault("loading_confirmations", []).append(record)
    return record


def line_shipment_mismatch(order: dict, line_idx: int) -> bool:
    return qty_shipped_for_line(order, line_idx) > physical_qty_for_loading_line(order, line_idx)


def match_po_line_idx(order: dict, ship_line: dict) -> tuple:
    """Map a shipment/SJ/Loading line to PO line_idx. Never matches by item name.

    Returns (line_idx, error_message). error_message is None on success.

    Identity priority (exact, never name):
    1. unique ``line_id`` — wins over a stale/disagreed ``line_idx``
    2. ``accurate_item_id`` (unique, or disambiguated by ``line_idx``)
    3. explicit ``line_idx``
    """
    if not isinstance(ship_line, dict):
        return None, "Line Surat Jalan tidak valid."
    lis = list(order.get("line_items") or [])
    lid = str(ship_line.get("line_id") or "").strip()
    aid = str(ship_line.get("accurate_item_id") or "").strip()
    explicit = ship_line.get("line_idx")
    has_explicit = explicit is not None and str(explicit).strip() != ""
    idx_explicit = _safe_int(explicit, default=-1) if has_explicit else None

    if lid:
        matches = [
            i for i, li in enumerate(lis)
            if lid in _po_line_identity_keys(li)
        ]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            if idx_explicit is not None and idx_explicit in matches:
                return idx_explicit, None
            return None, f"line_id {lid} ambigu pada beberapa line PO — sertakan line_idx."

    if aid:
        matches = [
            i for i, li in enumerate(lis)
            if str(li.get("accurate_item_id") or "").strip() == aid
        ]
        if idx_explicit is not None and idx_explicit in matches:
            return idx_explicit, None
        if len(matches) == 1:
            return matches[0], None
        if not matches:
            return None, f"accurate_item_id {aid} tidak ditemukan pada line PO ini."
        if idx_explicit is not None:
            return None, f"line_idx {idx_explicit} tidak cocok dengan accurate_item_id {aid}."
        return None, f"accurate_item_id {aid} ambigu pada beberapa line PO — sertakan line_idx."

    if idx_explicit is not None:
        if not lis and idx_explicit == 0:
            return 0, None
        if 0 <= idx_explicit < len(lis):
            return idx_explicit, None
        return None, f"line_idx {idx_explicit} tidak valid."

    return None, "Line Surat Jalan wajib punya line_id, accurate_item_id, atau line_idx."


def remaining_delivery_note_updates(order: dict) -> list:
    """Allocate remaining packed-not-yet-SJ qty per line (partial SJ)."""
    updates = []
    for i in range(_line_count(order)):
        avail = available_for_delivery_note_line(order, i)
        if avail > 0:
            updates.append({"line_idx": i, "qty": avail})
    return updates


def validate_shipment_lines(
    updates: list,
    order: dict,
    *,
    source: str = SHIPMENT_SOURCE_MANUAL,
    exclude_shipment_id: Optional[str] = None,
) -> Optional[str]:
    """Validate a new/updated shipment's line quantities.

    Manual SJ: cannot exceed remaining physical eligible (print = packed;
    non-print = PO qty — Packing skipped). Accurate import: mapping must be
    valid; qty > physical is recorded as mismatch later.
    """
    if not updates:
        return "Qty Surat Jalan per line wajib diisi."
    seen = set()
    total = 0
    for u in updates:
        qty = _safe_int(u.get("qty") if u.get("qty") is not None else u.get("quantity"))
        if qty < 0:
            return "Qty Surat Jalan tidak boleh negatif."
        idx, err = match_po_line_idx(order, u)
        if err:
            return err
        if idx in seen:
            return f"Line #{idx + 1} duplikat pada Surat Jalan yang sama."
        seen.add(idx)
        total += qty
        physical = physical_qty_for_loading_line(order, idx)
        already = qty_shipped_for_line(order, idx, exclude_shipment_id=exclude_shipment_id)
        if source != SHIPMENT_SOURCE_ACCURATE:
            avail = max(0, physical - already)
            if qty > avail:
                name = _line_name(order, idx)
                lis = order.get("line_items") or []
                if 0 <= int(idx) < len(lis) and not is_print_line(lis[int(idx)]):
                    return (
                        f"Qty Surat Jalan '{name}' ({qty}) melebihi qty eligible "
                        f"yang belum dialokasikan ({avail})."
                    )
                return (
                    f"Qty Surat Jalan '{name}' ({qty}) melebihi qty packing "
                    f"yang belum dialokasikan ({avail})."
                )
    if total <= 0:
        return "Qty Surat Jalan tidak valid."
    return None


def shipment_line_mismatches(updates: list, order: dict, *, exclude_shipment_id: Optional[str] = None) -> list:
    """Per-line warnings when Accurate shipped qty exceeds Eternal physical eligible."""
    warnings = []
    for u in updates:
        idx, err = match_po_line_idx(order, u)
        if err or idx is None:
            continue
        qty = _safe_int(u.get("qty") if u.get("qty") is not None else u.get("quantity"))
        packed = packed_qty_for_line(order, idx)
        physical = physical_qty_for_loading_line(order, idx)
        already = qty_shipped_for_line(order, idx, exclude_shipment_id=exclude_shipment_id)
        projected = already + qty
        if projected > physical:
            lis = order.get("line_items") or []
            non_print = 0 <= int(idx) < len(lis) and not is_print_line(lis[int(idx)])
            warnings.append({
                "line_idx": idx,
                "name": _line_name(order, idx),
                "packed": packed,
                "shipped": projected,
                "message": (
                    f"Qty Surat Jalan Accurate '{_line_name(order, idx)}' ({projected}) "
                    + (
                        f"melebihi qty eligible non-cetak ({physical})."
                        if non_print
                        else f"melebihi qty Packing Helper ({packed}). Loading dibatasi packing."
                    )
                ),
            })
    return warnings


def _line_name(order: dict, line_idx: int) -> str:
    lis = order.get("line_items") or []
    if not lis:
        return "Total PO"
    if 0 <= int(line_idx) < len(lis):
        return lis[int(line_idx)].get("name") or f"Line #{int(line_idx) + 1}"
    return f"Line #{int(line_idx) + 1}"


def _line_accurate_item_id(order: dict, line_idx: int) -> Optional[str]:
    lis = order.get("line_items") or []
    if 0 <= int(line_idx) < len(lis):
        aid = lis[int(line_idx)].get("accurate_item_id")
        return str(aid) if aid not in (None, "") else None
    return None


def normalize_shipment_line_updates(updates: list, order: dict) -> list:
    rows = []
    for u in updates:
        idx, err = match_po_line_idx(order, u)
        if err or idx is None:
            continue
        qty = max(0, _safe_int(u.get("qty") if u.get("qty") is not None else u.get("quantity")))
        packed = packed_qty_for_line(order, idx)  # actual packed only — never invent
        physical = physical_qty_for_loading_line(order, idx)
        rows.append({
            "line_idx": idx,
            "qty": qty,
            "accurate_item_id": str(u.get("accurate_item_id") or _line_accurate_item_id(order, idx) or "") or None,
            "packed_qty": packed,
            "mismatch": qty > physical,
        })
    return rows


def find_shipment_by_accurate_id(order: dict, accurate_shipment_id: str) -> Optional[dict]:
    key = str(accurate_shipment_id or "").strip()
    if not key:
        return None
    for sh in shipments_stored(order):
        if str(sh.get("accurate_shipment_id") or "").strip() == key:
            return sh
    return None


def apply_shipment_record(
    order: dict,
    *,
    number: str,
    lines: list,
    source: str,
    actor: Optional[dict] = None,
    accurate_shipment_id: Optional[str] = None,
    notes: Optional[str] = None,
    shipment_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> dict:
    """Append or update a shipment on the order (idempotent by accurate_shipment_id)."""
    acc_id = str(accurate_shipment_id or "").strip() or None
    existing = find_shipment_by_accurate_id(order, acc_id) if acc_id else None
    exclude = str(existing.get("id") or acc_id) if existing else None
    normalized = normalize_shipment_line_updates(lines, order)
    mismatches = shipment_line_mismatches(lines, order, exclude_shipment_id=exclude)
    actor = actor or {}
    record = {
        "id": (existing or {}).get("id") or shipment_id or "",
        "source": source,
        "accurate_shipment_id": acc_id,
        "number": number,
        "notes": (notes or "").strip() or None,
        "lines": normalized,
        "mismatch": bool(mismatches),
        "mismatch_reasons": [m["message"] for m in mismatches],
        "created_by": actor.get("email") or (existing or {}).get("created_by"),
        "created_by_name": actor.get("name") or (existing or {}).get("created_by_name"),
        "created_at": (existing or {}).get("created_at") or created_at,
        "updated_by": actor.get("email"),
        "updated_by_name": actor.get("name"),
        "updated_at": created_at,
    }
    shipments = shipments_stored(order)
    if existing:
        next_list = []
        for sh in shipments:
            if str(sh.get("accurate_shipment_id") or "") == acc_id or str(sh.get("id") or "") == str(existing.get("id") or ""):
                next_list.append(record)
            else:
                next_list.append(sh)
        order["shipments"] = next_list
    else:
        order.setdefault("shipments", []).append(record)
    return record


def validate_loading_lines(updates: list, order: dict) -> Optional[str]:
    if not updates:
        return "Qty Loading per line wajib diisi."
    seen = set()
    total = 0
    for u in updates:
        idx = _safe_int(u.get("line_idx"), default=-1)
        qty = _safe_int(u.get("loaded_qty") if u.get("loaded_qty") is not None else u.get("qty"))
        n = _line_count(order)
        if idx < 0 or idx >= n:
            return f"line_idx {idx} tidak valid."
        if idx in seen:
            return f"Line #{idx + 1} duplikat pada Loading."
        seen.add(idx)
        if qty < 0:
            return "Qty Loading tidak boleh negatif."
        cap = loading_cap_for_line(order, idx)
        physical = physical_qty_for_loading_line(order, idx)
        name = _line_name(order, idx)
        if qty > physical:
            lis = order.get("line_items") or []
            if 0 <= idx < len(lis) and not is_print_line(lis[idx]):
                return f"Qty Loading '{name}' ({qty}) melebihi qty eligible ({physical})."
            return f"Qty Loading '{name}' ({qty}) melebihi qty packing ({physical})."
        if qty > cap:
            shipped = qty_shipped_for_line(order, idx)
            if has_shipment_line_quantities(order) and qty > shipped:
                return (
                    f"Qty Loading '{name}' ({qty}) melebihi qty Surat Jalan line tersebut ({shipped})."
                )
            return (
                f"Qty Loading '{name}' ({qty}) melebihi qty tersedia untuk loading ({cap})."
            )
        total += qty
    merged_total = 0
    n = _line_count(order)
    current = {i: qty_loaded_for_line(order, i) for i in range(n)}
    for u in updates:
        idx = _safe_int(u.get("line_idx"), default=-1)
        current[idx] = max(0, _safe_int(u.get("loaded_qty") if u.get("loaded_qty") is not None else u.get("qty")))
    merged_total = sum(current.values())
    if merged_total < 0:
        return "Qty Loading tidak valid."
    return None


def merge_loading_line_updates(order: dict, updates: list) -> list:
    n = _line_count(order)
    merged = {i: qty_loaded_for_line(order, i) for i in range(n)}
    for u in updates:
        idx = _safe_int(u.get("line_idx"), default=-1)
        merged[idx] = max(0, _safe_int(u.get("loaded_qty") if u.get("loaded_qty") is not None else u.get("qty")))
    return [{"line_idx": i, "loaded_qty": merged.get(i, 0)} for i in range(n)]


def _loading_increment_qty(u: dict) -> int:
    if u.get("qty") is not None:
        return _safe_int(u.get("qty"))
    return _safe_int(u.get("add_qty"))


def validate_loading_increments(updates: list, order: dict) -> Optional[str]:
    """Validate additive loading qty (this save) against remaining available."""
    if not updates:
        return "Qty Loading per line wajib diisi."
    seen = set()
    for u in updates:
        idx = _safe_int(u.get("line_idx"), default=-1)
        qty = _loading_increment_qty(u)
        n = _line_count(order)
        if idx < 0 or idx >= n:
            return f"line_idx {idx} tidak valid."
        if idx in seen:
            return f"Line #{idx + 1} duplikat pada Loading."
        seen.add(idx)
        if qty <= 0:
            return "Qty Loading harus lebih dari 0."
        physical = physical_qty_for_loading_line(order, idx)
        cap = loading_cap_for_line(order, idx)
        current = qty_loaded_for_line(order, idx)
        new_total = current + qty
        name = _line_name(order, idx)
        if new_total > physical:
            lis = order.get("line_items") or []
            if 0 <= idx < len(lis) and not is_print_line(lis[idx]):
                return f"Qty Loading '{name}' ({new_total}) melebihi qty eligible ({physical})."
            return f"Qty Loading '{name}' ({new_total}) melebihi qty packing ({physical})."
        if new_total > cap:
            shipped = qty_shipped_for_line(order, idx)
            if has_shipment_line_quantities(order) and new_total > shipped:
                return (
                    f"Qty Loading '{name}' ({new_total}) melebihi qty Surat Jalan line tersebut ({shipped})."
                )
            avail = available_for_loading_line(order, idx)
            return (
                f"Qty Loading '{name}' ({qty}) melebihi qty tersedia untuk loading ({avail})."
            )
    return None


def apply_loading_increments(order: dict, updates: list) -> tuple:
    """Append loading events and return cumulative lines + full event list.

    Does not replace prior events. `loading_info.lines` stays the running total.
    """
    n = _line_count(order)
    merged = {i: qty_loaded_for_line(order, i) for i in range(n)}
    events = list(loading_events_stored(order))
    for u in updates:
        idx = _safe_int(u.get("line_idx"), default=-1)
        qty = _loading_increment_qty(u)
        merged[idx] = merged.get(idx, 0) + qty
        events.append({
            "line_idx": idx,
            "qty": qty,
            "loaded_qty_after": merged[idx],
        })
    lines = [{"line_idx": i, "loaded_qty": merged.get(i, 0)} for i in range(n)]
    return lines, events


def shipment_line_breakdown(order: dict) -> list:
    """Derived per-line SJ + loading tracker (not persisted).

    Includes print + non-print (non-print Loading uses PO qty, not packed).
    Does not depend on packing_line_breakdown (print-only).
    """
    n = _line_count(order)
    out = []
    for idx in range(n):
        packed = packed_qty_for_line(order, idx)
        shipped = qty_shipped_for_line(order, idx)
        loaded = qty_loaded_for_line(order, idx)
        avail_sj = available_for_delivery_note_line(order, idx)
        cap_load = loading_confirm_cap_for_line(order, idx)
        avail_load = available_for_loading_confirm_line(order, idx)
        physical = physical_qty_for_loading_line(order, idx)
        mismatch = shipped > physical
        lis = order.get("line_items") or []
        li = lis[idx] if 0 <= idx < len(lis) else {}
        out.append({
            "line_idx": idx,
            "name": _line_name(order, idx),
            "item_code": li.get("item_code") if isinstance(li, dict) else None,
            "accurate_item_id": _line_accurate_item_id(order, idx),
            "color": li.get("color") if isinstance(li, dict) else None,
            "qty_po": po_qty_for_line(order, idx),
            "packed": packed,
            "shipped": shipped,
            "available_sj": avail_sj,
            "loaded": loaded,
            "loading_cap": cap_load,
            "available_loading": avail_load,
            "remaining_loading": avail_load,
            "mismatch": mismatch,
            "production_process": (
                "non_print" if isinstance(li, dict) and not is_print_line(li) else "print"
            ),
        })
    return out


def loading_fulfills_all_required_lines(order: dict) -> bool:
    """True only when every required PO line is fully loaded.

    Uses existing Loading caps (SJ-linked confirmations, cumulative, stable
    line_idx). Never treats a grand-total match as complete if one line is short.
    """
    if loading_status_semantic(order) != SJ_STATUS_COMPLETED:
        return False
    n = _line_count(order)
    use_confirm = bool(loading_confirmations_stored(order))
    for i in range(n):
        remaining = (
            available_for_loading_confirm_line(order, i)
            if use_confirm
            else available_for_loading_line(order, i)
        )
        if remaining > 0:
            return False
        cap = loading_confirm_cap_for_line(order, i) if use_confirm else loading_cap_for_line(order, i)
        if cap > 0 and qty_loaded_for_line(order, i) < cap:
            return False
    return True


def surat_jalan_status_semantic(order: dict) -> str:
    """pending / partial / completed from quantity evidence.

    Selesai only when a valid Surat Jalan exists AND Loading fulfills every
    required line. Allocation of packed qty to SJ is not enough.
    Ghost shipments without a live file must stay pending for stage/badge.
    """
    created = surat_jalan_created(order)
    if not created:
        return SJ_STATUS_PENDING
    if loading_fulfills_all_required_lines(order):
        return SJ_STATUS_COMPLETED
    packed = qty_packed(order)
    shipped = qty_shipped(order)
    if shipped <= 0:
        if has_surat_jalan_file(order) or (
            order.get("delivery_note_number") and order.get("delivery_note_created_at") and order.get("delivery_note_created_by")
        ):
            return SJ_STATUS_COMPLETED if packed <= 0 else SJ_STATUS_PARTIAL
        return SJ_STATUS_PENDING
    if packed > 0 and shipped >= packed:
        return SJ_STATUS_PARTIAL
    if shipped > 0:
        return SJ_STATUS_PARTIAL
    return SJ_STATUS_PENDING


def loading_status_semantic(order: dict) -> str:
    if loading_confirmations_stored(order):
        loaded = qty_confirmed_loading(order)
        remaining = sum(available_for_loading_confirm_line(order, i) for i in range(_line_count(order)))
        if loaded <= 0:
            return SJ_STATUS_PENDING
        if remaining <= 0:
            return SJ_STATUS_COMPLETED
        return SJ_STATUS_PARTIAL
    packed = qty_packed(order)
    loaded = qty_loaded(order)
    cap = sum(loading_cap_for_line(order, i) for i in range(_line_count(order)))
    if loaded <= 0:
        return SJ_STATUS_PENDING
    target = cap if cap > 0 else packed
    if target > 0 and loaded >= target:
        return SJ_STATUS_COMPLETED
    return SJ_STATUS_PARTIAL



# ---------------------------------------------------------------------------
# Loading SJ one-click match + qty discrepancy (Packing → SJ → Loading → Delivery)
# ---------------------------------------------------------------------------

LOADING_STATUS_WAITING = "waiting_verification"
LOADING_STATUS_PACKING_SHORT = "packing_insufficient"
LOADING_STATUS_READY = "ready_to_verify"
LOADING_STATUS_HELD_MISMATCH = "held_qty_mismatch"
LOADING_STATUS_COMPLETE = "loading_complete"

MSG_LOADING_PACKING_SHORT_FMT = (
    "Packing belum mencukupi untuk {item}: diperlukan {need}, tersedia {have}."
)


def _fmt_qty_id(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")



def sj_fingerprint(order: dict, surat_jalan_id: str) -> str:
    """Deterministic fingerprint of current SJ line composition for snapshot binding."""
    import hashlib
    sid = str(surat_jalan_id or "").strip()
    parts = [sid]
    lines = sj_lines_stored(order, sid)
    if lines:
        for ln in sorted(lines, key=lambda x: _safe_int((x or {}).get("line_idx"), default=-1)):
            if not isinstance(ln, dict):
                continue
            idx = _safe_int(ln.get("line_idx"), default=-1)
            qty = max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
            if idx < 0 or qty <= 0:
                continue
            parts.append(f"{idx}:{qty}")
    else:
        entry = find_sj_file(order, sid) or {}
        parts.append(f"0:{max(0, _safe_int(entry.get('total_qty') or entry.get('qty') or 0))}")
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def build_sj_loading_lines(order: dict, surat_jalan_id: str) -> list:
    """Authoritative Loading lines = exact current SJ required qtys (remaining)."""
    sid = str(surat_jalan_id or "").strip()
    out = []
    lines = sj_lines_stored(order, sid)
    if lines:
        merged = {}
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            need = max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
            if need <= 0:
                continue
            idx, _err = match_po_line_idx(order, ln)
            if idx is None:
                continue
            merged[int(idx)] = merged.get(int(idx), 0) + need
        for idx in sorted(merged):
            already = loaded_qty_for_sj_line(order, sid, idx)
            rem = max(0, merged[idx] - already)
            if rem <= 0:
                continue
            lis = order.get("line_items") or []
            out.append({
                "line_idx": idx,
                "qty": rem,
                "name": _line_name(order, idx),
                "accurate_item_id": _line_accurate_item_id(order, idx),
                "is_print": bool(0 <= idx < len(lis) and is_print_line(lis[idx])),
            })
        return out
    # Legacy total-only SJ
    entry = find_sj_file(order, sid) or {}
    total = max(0, _safe_int(entry.get("total_qty") or entry.get("qty") or 0))
    already = loaded_qty_for_sj_line(order, sid, 0)
    rem = max(0, total - already)
    if rem > 0:
        out.append({
            "line_idx": 0,
            "qty": rem,
            "name": _line_name(order, 0),
            "accurate_item_id": _line_accurate_item_id(order, 0),
            "is_print": bool(_line_count(order) and is_print_line((order.get("line_items") or [ {}])[0])),
        })
    return out


def packing_shortfall_for_sj_match(order: dict, surat_jalan_id: str) -> Optional[str]:
    """Print lines need packing >= SJ required. Non-print exempt from production Packing."""
    sid = str(surat_jalan_id or "").strip()
    lis = order.get("line_items") or []
    for ln in build_sj_loading_lines(order, sid) or []:
        idx = int(ln.get("line_idx"))
        need = int(ln.get("qty") or 0)
        if idx < 0 or need <= 0:
            continue
        if 0 <= idx < len(lis) and not is_print_line(lis[idx]):
            continue
        have = packed_qty_for_line(order, idx)
        # remaining packing after prior loads on this PO line
        already_loaded = qty_loaded_for_line(order, idx)
        avail_pack = max(0, have - already_loaded) if is_print_line(lis[idx] if 0 <= idx < len(lis) else {"production_process": "print"}) else need
        # For print: physical available must cover this SJ remainder
        physical = physical_qty_for_loading_line(order, idx)
        remain_phys = max(0, physical - qty_loaded_for_line(order, idx))
        if remain_phys < need:
            name = _line_name(order, idx)
            return MSG_LOADING_PACKING_SHORT_FMT.format(item=name, need=_fmt_qty_id(need), have=_fmt_qty_id(remain_phys))
    return None


def validate_loading_match_confirmation(order: dict, surat_jalan_id: str) -> Optional[str]:
    """Validate one-click 'Semua Barang Sesuai SJ'.

    Already-complete verification for the same SJ fingerprint is idempotent success.
    Fingerprint mismatch (SJ changed) allows re-verify even when remaining qty is 0.
    """
    sid = str(surat_jalan_id or "").strip()
    if not find_sj_file(order, sid):
        return "Surat Jalan tidak ditemukan. Loading harus terhubung ke file Surat Jalan."
    fp = sj_fingerprint(order, sid)
    snaps = [
        s for s in (order.get("loading_snapshots") or [])
        if isinstance(s, dict)
        and str(s.get("surat_jalan_id") or "").strip() == sid
        and s.get("status") not in ("cancelled", "superseded", "deleted")
    ]
    fingerprint_ok = bool(snaps and fp and str(snaps[-1].get("sj_fingerprint") or "") == fp)
    open_disc = [
        d for d in (order.get("loading_discrepancies") or [])
        if isinstance(d, dict)
        and d.get("status") in ("open", "held", None, "")
        and str(d.get("surat_jalan_id") or "").strip() == sid
    ]
    lines = build_sj_loading_lines(order, sid)
    if not lines:
        if fingerprint_ok and not open_disc:
            return None  # idempotent complete
        # SJ changed or open discrepancy while qty already covers → allow snapshot refresh
        if open_disc or (snaps and not fingerprint_ok):
            return None
        return "Tidak ada qty Surat Jalan yang tersisa untuk diverifikasi."
    short = packing_shortfall_for_sj_match(order, sid)
    if short:
        return short
    payload = [{"line_idx": ln["line_idx"], "qty": ln["qty"]} for ln in lines]
    return validate_loading_confirmation(order, sid, payload)


def apply_loading_match_confirmation(
    order: dict,
    *,
    surat_jalan_id: str,
    actor: Optional[dict] = None,
    confirmation_id: Optional[str] = None,
    confirmed_at: Optional[str] = None,
    action_id: Optional[str] = None,
) -> dict:
    """Generate Loading lines from current SJ and persist match snapshot.

    When remaining SJ qty is already covered but fingerprint/discrepancy requires
    re-verify, persist a fresh snapshot without duplicating Loading quantities.
    """
    sid = str(surat_jalan_id or "").strip()
    actor = actor or {}
    gen = build_sj_loading_lines(order, sid)
    fp = sj_fingerprint(order, sid)
    if gen:
        payload = [{"line_idx": ln["line_idx"], "qty": ln["qty"]} for ln in gen]
        record = apply_loading_confirmation(
            order,
            surat_jalan_id=sid,
            lines=payload,
            actor=actor,
            confirmation_id=confirmation_id,
            confirmed_at=confirmed_at,
            action_id=action_id,
        )
        record["source"] = "sj_match"
        record["sj_fingerprint"] = fp
        confs = order.get("loading_confirmations") or []
        if confs and isinstance(confs[-1], dict):
            confs[-1]["source"] = "sj_match"
            confs[-1]["sj_fingerprint"] = fp
        snap_lines = list(record.get("lines") or [])
        rec_id = record.get("id") or confirmation_id
    else:
        # Snapshot-only attestation from current SJ composition
        snap_lines = []
        stored = sj_lines_stored(order, sid)
        if stored:
            for ln in stored:
                if not isinstance(ln, dict):
                    continue
                idx = _safe_int(ln.get("line_idx"), default=-1)
                qty = max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
                if idx < 0 or qty <= 0:
                    continue
                snap_lines.append({
                    "line_idx": idx,
                    "qty": qty,
                    "name": _line_name(order, idx),
                    "accurate_item_id": _line_accurate_item_id(order, idx),
                })
        rec_id = confirmation_id or str(__import__("uuid").uuid4())
        record = {
            "id": rec_id,
            "surat_jalan_id": sid,
            "lines": snap_lines,
            "source": "sj_match",
            "sj_fingerprint": fp,
            "confirmed_by": actor.get("email"),
            "confirmed_by_name": actor.get("name") or actor.get("email"),
            "confirmed_at": confirmed_at,
            "action_id": action_id,
            "snapshot_only": True,
        }
    # Supersede prior snapshots for this SJ
    for s in order.get("loading_snapshots") or []:
        if isinstance(s, dict) and str(s.get("surat_jalan_id") or "").strip() == sid and s.get("status") == "complete":
            s["status"] = "superseded"
    snap = {
        "id": rec_id,
        "surat_jalan_id": sid,
        "sj_fingerprint": fp,
        "lines": snap_lines,
        "source": "sj_match",
        "status": "complete",
        "confirmed_by": actor.get("email"),
        "confirmed_by_name": actor.get("name") or actor.get("email"),
        "confirmed_at": confirmed_at,
        "action_id": action_id,
    }
    order.setdefault("loading_snapshots", []).append(snap)
    for d in order.get("loading_discrepancies") or []:
        if not isinstance(d, dict):
            continue
        if str(d.get("surat_jalan_id") or "").strip() != sid:
            continue
        if d.get("status") in ("open", "held", None, ""):
            d["status"] = "resolved"
            d["resolved_at"] = confirmed_at
            d["resolved_by"] = actor.get("email")
            d["resolved_via"] = "sj_match_reverify"
    return record


def loading_discrepancies_open(order: dict) -> list:
    return loading_discrepancies_stored(order)


def loading_discrepancies_stored(order: dict) -> list:
    raw = order.get("loading_discrepancies")
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, dict) and d.get("status") in ("open", "held", None, "")]


def validate_loading_discrepancy(
    order: dict,
    surat_jalan_id: str,
    mismatches: list,
) -> Optional[str]:
    sid = str(surat_jalan_id or "").strip()
    if not find_sj_file(order, sid):
        return "Surat Jalan tidak ditemukan."
    if not isinstance(mismatches, list) or not mismatches:
        return "Pilih minimal satu baris yang qty fisiknya berbeda dari Surat Jalan."
    seen = set()
    genuine = 0
    for row in mismatches:
        if not isinstance(row, dict):
            return "Data selisih tidak valid."
        idx = _safe_int(row.get("line_idx"), default=-1)
        if idx < 0 or idx in seen:
            return "Baris selisih tidak valid atau duplikat."
        seen.add(idx)
        sj_qty = sj_qty_for_line(order, sid, idx)
        if sj_qty <= 0:
            return f"Baris {idx} tidak ada pada Surat Jalan terpilih."
        if "actual_qty" not in row and "qty_fisik" not in row and row.get("qty") is None:
            return "Qty Fisik Aktual wajib diisi untuk baris yang ditandai."
        actual = row.get("actual_qty")
        if actual is None:
            actual = row.get("qty_fisik")
        if actual is None:
            actual = row.get("qty")
        try:
            if actual is None or str(actual).strip() == "":
                return "Qty Fisik Aktual tidak boleh kosong."
            actual_i = int(actual)
        except (TypeError, ValueError):
            return "Qty Fisik Aktual harus angka."
        if actual_i < 0:
            return "Qty Fisik Aktual tidak boleh negatif."
        if actual_i == sj_qty:
            return (
                f"Qty Fisik Aktual untuk '{_line_name(order, idx)}' sama dengan Qty SJ "
                f"({sj_qty}). Hapus tanda selisih atau ubah qty."
            )
        genuine += 1
    if genuine <= 0:
        return "Tidak ada selisih qty yang valid."
    return None


def apply_loading_discrepancy(
    order: dict,
    *,
    surat_jalan_id: str,
    mismatches: list,
    note: Optional[str] = None,
    actor: Optional[dict] = None,
    discrepancy_id: Optional[str] = None,
    reported_at: Optional[str] = None,
    action_id: Optional[str] = None,
) -> dict:
    """Persist qty mismatch report. Does NOT complete Loading / unlock Delivery."""
    sid = str(surat_jalan_id or "").strip()
    actor = actor or {}
    lines_out = []
    for row in mismatches:
        idx = _safe_int(row.get("line_idx"), default=-1)
        sj_qty = sj_qty_for_line(order, sid, idx)
        actual = row.get("actual_qty")
        if actual is None:
            actual = row.get("qty_fisik")
        if actual is None:
            actual = row.get("qty")
        actual_i = int(actual)
        diff = actual_i - sj_qty
        lines_out.append({
            "line_idx": idx,
            "name": _line_name(order, idx),
            "accurate_item_id": _line_accurate_item_id(order, idx),
            "is_print": bool(0 <= idx < len(order.get("line_items") or []) and is_print_line((order.get("line_items") or [])[idx])),
            "qty_sj": sj_qty,
            "qty_actual": actual_i,
            "difference": diff,
            "classification": "lebih" if diff > 0 else "kurang",
        })
    rec = {
        "id": discrepancy_id or "",
        "surat_jalan_id": sid,
        "sj_fingerprint": sj_fingerprint(order, sid),
        "lines": lines_out,
        "note": (note or "").strip() or None,
        "status": "open",
        "reported_by": actor.get("email"),
        "reported_by_name": actor.get("name") or actor.get("email"),
        "reported_at": reported_at,
        "action_id": action_id,
        "branch_id": order.get("branch_id") or order.get("cabang_id"),
    }
    order.setdefault("loading_discrepancies", []).append(rec)
    return rec


def loading_verification_status(order: dict, surat_jalan_id: Optional[str] = None) -> dict:
    """UI/status helper for Loading SJ verification stage (stage name remains Loading)."""
    ships = shipments_stored(order)
    sid = str(surat_jalan_id or "").strip()
    if not sid:
        sh = active_delivery_batch(order)
        sid = delivery_batch_identity(sh) if sh else ""
        if not sid and ships:
            sid = delivery_batch_identity(ships[-1])
    open_disc = [
        d for d in (order.get("loading_discrepancies") or [])
        if isinstance(d, dict)
        and d.get("status") in ("open", "held", None, "")
        and (not sid or str(d.get("surat_jalan_id") or "").strip() == sid)
    ]
    if open_disc:
        return {
            "code": LOADING_STATUS_HELD_MISMATCH,
            "label": "Tertahan — Qty Tidak Sesuai SJ",
            "surat_jalan_id": sid,
            "discrepancies": open_disc,
        }
    if not sid or not find_sj_file(order, sid):
        return {"code": LOADING_STATUS_WAITING, "label": "Menunggu Verifikasi", "surat_jalan_id": sid or ""}

    snaps = [
        s for s in (order.get("loading_snapshots") or [])
        if isinstance(s, dict)
        and str(s.get("surat_jalan_id") or "").strip() == sid
        and s.get("status") not in ("cancelled", "superseded", "deleted")
    ]
    fp = sj_fingerprint(order, sid)
    if snaps and fp and not any(str(s.get("sj_fingerprint") or "") == fp for s in snaps):
        return {
            "code": LOADING_STATUS_WAITING,
            "label": "Menunggu Verifikasi",
            "surat_jalan_id": sid,
            "reason": "Surat Jalan berubah — verifikasi ulang diperlukan.",
            "sj_changed": True,
        }

    if delivery_batch_photo_readiness(order).get("allowed"):
        return {
            "code": LOADING_STATUS_COMPLETE,
            "label": "Loading Selesai",
            "surat_jalan_id": sid,
        }
    short = packing_shortfall_for_sj_match(order, sid)
    if short:
        return {
            "code": LOADING_STATUS_PACKING_SHORT,
            "label": "Packing Belum Mencukupi",
            "surat_jalan_id": sid,
            "reason": short,
        }
    return {
        "code": LOADING_STATUS_READY,
        "label": "Siap Diverifikasi",
        "surat_jalan_id": sid,
    }


# ---------------------------------------------------------------------------
# Delivery-batch photo/gallery readiness + SJ reconcile on actual delivery
# ---------------------------------------------------------------------------

MSG_PHOTO_PACKING_INSUFFICIENT = (
    "Qty Packing untuk pengiriman ini belum mencukupi."
)
MSG_PHOTO_LOADING_INSUFFICIENT = (
    "Qty Loading untuk pengiriman ini belum mencukupi."
)
MSG_PHOTO_PACKING_AND_LOADING_INSUFFICIENT = (
    "Foto pengiriman dapat diinput setelah qty Packing dan Loading untuk pengiriman "
    "ini mencukupi."
)
MSG_PHOTO_SJ_NOT_READY = (
    "Surat Jalan untuk pengiriman ini belum siap."
)
MSG_PHOTO_LOADING_ITEM_MISMATCH = (
    "Qty Loading per item belum mencukupi untuk Surat Jalan pengiriman ini."
)

# Machine-readable Delivery readiness codes (UI must not parse Indonesian text alone).
REASON_SJ_NOT_READY = "SJ_NOT_READY"
REASON_LOADING_INCOMPLETE = "LOADING_INCOMPLETE"
REASON_LOADING_ITEM_MISMATCH = "LOADING_ITEM_MISMATCH"


def active_delivery_batch(order: dict) -> Optional[dict]:
    """Canonical delivery batch = latest shipment (sales delivery / SJ allocation)."""
    ships = shipments_stored(order)
    if not ships:
        return None
    last = ships[-1]
    return last if isinstance(last, dict) else None


def delivery_batch_identity(shipment: Optional[dict]) -> str:
    if not isinstance(shipment, dict):
        return ""
    for key in ("id", "sales_delivery_id", "number"):
        val = str(shipment.get(key) or "").strip()
        if val:
            return val
    return ""


def delivery_batch_qty(shipment: Optional[dict]) -> int:
    if not isinstance(shipment, dict):
        return 0
    total = 0
    lines = shipment.get("lines")
    if isinstance(lines, list) and lines:
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            total += max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
        return total
    return max(0, _safe_int(shipment.get("total_qty") or shipment.get("qty") or 0))


def packed_available_for_delivery_batch(order: dict, shipment: Optional[dict] = None) -> int:
    """Packed qty available to cover this exact shipment (exclude this shipment from shipped).

    Does not treat loaded>packed excess as packed. Whole-PO incompleteness elsewhere
    does not reduce this batch's available packed below what covers this shipment.
    """
    sh = shipment if isinstance(shipment, dict) else active_delivery_batch(order)
    if not sh:
        return 0
    sid = delivery_batch_identity(sh)
    lines = sh.get("lines") if isinstance(sh.get("lines"), list) else []
    if lines:
        avail = 0
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            idx, _err = match_po_line_idx(order, ln)
            if idx is None:
                continue
            need = max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
            line_avail = max(
                0,
                packed_qty_for_line(order, idx)
                - qty_shipped_for_line(order, idx, exclude_shipment_id=sid or None),
            )
            # Cap contribution by this SJ line allocation (never invent beyond SJ)
            avail += min(line_avail, need) if need else line_avail
        return avail
    # Legacy shipment without lines: single-line / total packed minus other shipments
    q = delivery_batch_qty(sh)
    packed = qty_packed(order)
    other = 0
    for other_sh in shipments_stored(order):
        if not isinstance(other_sh, dict):
            continue
        other_id = delivery_batch_identity(other_sh)
        if sid and other_id == sid:
            continue
        other += delivery_batch_qty(other_sh)
    return max(0, min(max(0, packed - other), q if q else max(0, packed - other)))


def loaded_qty_for_delivery_batch(order: dict, shipment: Optional[dict] = None) -> int:
    sh = shipment if isinstance(shipment, dict) else active_delivery_batch(order)
    if not sh:
        return 0
    sid = delivery_batch_identity(sh)
    if not sid:
        return 0
    lines = sh.get("lines") if isinstance(sh.get("lines"), list) else []
    if lines:
        total = 0
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            idx, _err = match_po_line_idx(order, ln)
            if idx is None:
                continue
            total += loaded_qty_for_sj_line(order, sid, idx)
        return total
    # No line breakdown: sum confirmations linked to this SJ id/number
    target_ids = {sid}
    for k in ("id", "number", "sales_delivery_id"):
        v = str(sh.get(k) or "").strip()
        if v:
            target_ids.add(v)
    total = 0
    for conf in loading_confirmations_stored(order):
        if str(conf.get("surat_jalan_id") or "").strip() in target_ids:
            total += confirmation_qty_total(conf)
    if total > 0:
        return total
    # Legacy loading_info only when this is the sole / latest shipment context
    return qty_loaded(order) if len(shipments_stored(order)) <= 1 else 0


def _delivery_batch_resolved_lines(order: dict, shipment: dict) -> list:
    """SJ line rows for readiness: shipment.lines, else surat_jalan_files linkage."""
    raw = shipment.get("lines") if isinstance(shipment.get("lines"), list) else []
    lines = [ln for ln in raw if isinstance(ln, dict)]
    if lines:
        return lines
    sid = delivery_batch_identity(shipment)
    if not sid:
        return []
    return list(sj_lines_stored(order, sid) or [])


def delivery_batch_line_readiness(order: dict, shipment: Optional[dict] = None) -> List[dict]:
    """Per SJ-line Loading readiness for the active delivery batch.

    Packing is intentionally not consulted here — Packing already gated Loading
    for packable (print) lines via physical_qty_for_loading_line.

    Legacy shipments without line identity are only safe when the PO has a single
    attributable line. Multi-item POs without resolvable SJ lines must not pass
    via aggregate Loading totals alone.
    """
    sh = shipment if isinstance(shipment, dict) else active_delivery_batch(order)
    if not sh:
        return []
    sid = delivery_batch_identity(sh)
    if not sid:
        return []
    lines = _delivery_batch_resolved_lines(order, sh)
    out: List[dict] = []
    if lines:
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            idx, _err = match_po_line_idx(order, ln)
            if idx is None:
                continue
            required = max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
            if required <= 0:
                continue
            loaded = loaded_qty_for_sj_line(order, sid, idx)
            shortage = max(0, required - loaded)
            lis = order.get("line_items") or []
            li = lis[idx] if 0 <= idx < len(lis) else {}
            out.append({
                "line_idx": idx,
                "name": _line_name(order, idx),
                "item_code": li.get("item_code") if isinstance(li, dict) else None,
                "accurate_item_id": _line_accurate_item_id(order, idx) if 0 <= idx < len(lis) else None,
                "production_process": (
                    "non_print" if isinstance(li, dict) and not is_print_line(li) else "print"
                ),
                "required": required,
                "loaded": loaded,
                "shortage": shortage,
            })
        return out
    # Multi-item PO without SJ line identity → refuse aggregate-only bypass
    if _line_count(order) > 1:
        return []
    # Legacy single-line shipment without line breakdown: one synthetic line
    required = delivery_batch_qty(sh)
    if required <= 0:
        return []
    loaded = loaded_qty_for_delivery_batch(order, sh)
    out.append({
        "line_idx": 0,
        "name": _line_name(order, 0),
        "item_code": None,
        "accurate_item_id": None,
        "production_process": "print",
        "required": required,
        "loaded": loaded,
        "shortage": max(0, required - loaded),
    })
    return out


def delivery_batch_photo_readiness(order: dict, shipment: Optional[dict] = None) -> dict:
    """Photo/gallery write eligibility for one delivery batch.

    Authoritative Delivery readiness (shared by web + PWA):

      delivery_ready = every required SJ line is fully covered by valid confirmed Loading

    Packing is NOT re-checked at Delivery. Packing protects Loading; Loading protects
    Delivery. Aggregate equality alone must not hide a per-line shortage.
    """
    sh = shipment if isinstance(shipment, dict) else active_delivery_batch(order)
    sj_qty = delivery_batch_qty(sh)
    packed_avail = packed_available_for_delivery_batch(order, sh)
    loaded = loaded_qty_for_delivery_batch(order, sh)
    line_rows = delivery_batch_line_readiness(order, sh)
    sid = delivery_batch_identity(sh) if sh else ""

    reason = None
    reason_code = None
    allowed = False

    if not sh or not sid or sj_qty <= 0:
        reason = MSG_PHOTO_SJ_NOT_READY
        reason_code = REASON_SJ_NOT_READY
    elif loading_discrepancies_open(order) and any(
        str(d.get("surat_jalan_id") or "").strip() == sid
        for d in loading_discrepancies_open(order)
    ):
        reason = "Loading tertahan — qty fisik tidak sesuai Surat Jalan. Selesaikan selisih dan verifikasi ulang."
        reason_code = REASON_LOADING_INCOMPLETE
    elif not line_rows:
        # Empty rows: either no SJ qty breakdown, or multi-item PO refused aggregate-only
        if sh and _line_count(order) > 1 and not _delivery_batch_resolved_lines(order, sh):
            reason = (
                f"{MSG_PHOTO_LOADING_ITEM_MISMATCH} "
                "Komposisi item Loading tidak dapat diverifikasi untuk Surat Jalan multi-item."
            )
            reason_code = REASON_LOADING_ITEM_MISMATCH
        else:
            reason = MSG_PHOTO_LOADING_INSUFFICIENT
            reason_code = REASON_LOADING_INCOMPLETE
    else:
        short_rows = [r for r in line_rows if r.get("shortage", 0) > 0]
        if not short_rows:
            # Loading snapshot must match current SJ fingerprint when snapshots exist
            snaps = [
                s for s in (order.get("loading_snapshots") or [])
                if isinstance(s, dict)
                and str(s.get("surat_jalan_id") or "").strip() == sid
                and s.get("status") not in ("cancelled", "superseded", "deleted")
            ]
            fp = sj_fingerprint(order, sid) if sid else ""
            if snaps and fp and not any(str(s.get("sj_fingerprint") or "") == fp for s in snaps):
                allowed = False
                reason = "Surat Jalan berubah — verifikasi Loading ulang diperlukan."
                reason_code = REASON_LOADING_INCOMPLETE
            else:
                allowed = True
        else:
            total_short = sum(int(r.get("shortage") or 0) for r in short_rows)
            # Aggregate loaded covers SJ but composition wrong → item mismatch
            if loaded >= sj_qty and total_short > 0:
                reason = MSG_PHOTO_LOADING_ITEM_MISMATCH
                reason_code = REASON_LOADING_ITEM_MISMATCH
                names = [str(r.get("name") or f"line {r.get('line_idx')}") for r in short_rows[:3]]
                if names:
                    detail = ", ".join(names)
                    reason = (
                        f"{MSG_PHOTO_LOADING_ITEM_MISMATCH} "
                        f"Kekurangan: {detail}."
                    )
            else:
                reason = MSG_PHOTO_LOADING_INSUFFICIENT
                reason_code = REASON_LOADING_INCOMPLETE
                first = short_rows[0]
                name = str(first.get("name") or "").strip()
                if name and first.get("shortage"):
                    reason = (
                        f"Qty Loading '{name}' belum mencukupi "
                        f"(kurang {int(first['shortage'])} dari {int(first['required'])})."
                    )

    ready_q = sj_qty if allowed else min(loaded, sj_qty) if sj_qty > 0 else 0
    return {
        "allowed": allowed,
        "delivery_ready": allowed,
        "batch_qty": sj_qty,
        "required_total": sj_qty,
        "loaded_total": loaded,
        "ready_qty": ready_q,
        "packed_available": packed_avail,
        "loaded": loaded,
        "shipment_id": sid,
        "reason": reason,
        "reason_code": reason_code,
        "lines": line_rows,
    }


def delivery_batch_photo_block_reason(order: dict, shipment: Optional[dict] = None) -> Optional[str]:
    return delivery_batch_photo_readiness(order, shipment).get("reason")


def delivery_batch_photo_block_code(order: dict, shipment: Optional[dict] = None) -> Optional[str]:
    return delivery_batch_photo_readiness(order, shipment).get("reason_code")


def _set_line_qty(ln: dict, qty: int) -> None:
    ln["qty"] = int(qty)
    if "quantity" in ln:
        ln["quantity"] = int(qty)


def _delivered_qty_by_sj_line(order: dict) -> dict:
    """FIFO-allocate persisted qty_delivered onto loading confirmation lines → (sj_id, line_idx)."""
    leftover = max(0, qty_delivered(order))
    out: dict = {}
    for conf in loading_confirmations_stored(order):
        if leftover <= 0:
            break
        sj = str(conf.get("surat_jalan_id") or "").strip()
        if not sj:
            continue
        conf_lines = [ln for ln in (conf.get("lines") or []) if isinstance(ln, dict)]
        conf_cap = confirmation_qty_total(conf)
        take = min(conf_cap, leftover)
        leftover -= take
        if take <= 0 or not conf_lines:
            continue
        remaining_take = take
        for ln in conf_lines:
            if remaining_take <= 0:
                break
            idx = _safe_int(ln.get("line_idx"), default=-1)
            if idx < 0:
                continue
            line_cap = max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
            line_take = min(line_cap, remaining_take)
            remaining_take -= line_take
            key = (sj, idx)
            out[key] = out.get(key, 0) + line_take
    return out


def reconcile_sj_quantities_to_loaded_delivery(order: dict) -> dict:
    """Reduce current/effective SJ line qtys to actual delivered allocation per exact line.

    When confirm-delivered sets qty_delivered equal to total loaded (normal path),
    this matches loaded-for-SJ. When delivered < loaded, SJ follows delivered;
    Loading history is never silently reduced.

    - Packing unchanged
    - Loading unchanged (no silent unload)
    - Historical PDF bytes untouched
    - Remain returns via lower shipped/SJ allocation (idempotent)
    - Never increases SJ qty
    """
    returned_total = 0
    touched_shipment_ids: List[str] = []
    delivered_by = _delivered_qty_by_sj_line(order)
    # If nothing delivered yet, do not mutate SJ (confirm path always delivers > 0 first)
    if qty_delivered(order) <= 0 and not delivered_by:
        return {"returned_qty": 0, "shipment_ids": []}

    for sh in shipments_stored(order):
        if not isinstance(sh, dict):
            continue
        sid = delivery_batch_identity(sh)
        if not sid:
            continue
        lines = sh.get("lines")
        if not isinstance(lines, list) or not lines:
            continue
        batch_loaded = loaded_qty_for_delivery_batch(order, sh)
        if batch_loaded <= 0 and not any(
            (sid == k[0] or str(sh.get("number") or "") == k[0]) for k in delivered_by.keys()
        ):
            continue

        # Resolve delivered keys against possible SJ identity aliases
        alias_ids = {sid}
        for k in ("id", "number", "sales_delivery_id"):
            v = str(sh.get(k) or "").strip()
            if v:
                alias_ids.add(v)

        reduced = 0
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            idx = _safe_int(ln.get("line_idx"), default=-1)
            if idx < 0:
                continue
            sj_q = max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
            loaded_q = loaded_qty_for_sj_line(order, sid, idx)
            delivered_q = 0
            for alias in alias_ids:
                delivered_q = max(delivered_q, delivered_by.get((alias, idx), 0))
            # Actual operational SJ qty = min(SJ, delivered attribution).
            # Never raise SJ; never invent from loaded when delivered is lower.
            if delivered_q <= 0 and loaded_q <= 0:
                continue
            actual_q = min(sj_q, delivered_q if delivered_q > 0 else loaded_q)
            # Cap by loaded as well — delivery cannot exceed loading physically
            if loaded_q > 0:
                actual_q = min(actual_q, loaded_q)
            if actual_q < sj_q:
                reduced += sj_q - actual_q
                _set_line_qty(ln, actual_q)
        new_total = 0
        for ln in lines:
            if isinstance(ln, dict):
                new_total += max(0, _safe_int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")))
        if reduced > 0:
            sh["total_qty"] = new_total
            if "qty" in sh:
                sh["qty"] = new_total
            returned_total += reduced
            touched_shipment_ids.append(sid)
            # Mirror operational qty onto surat_jalan_files metadata (not PDF bytes)
            files = order.get("surat_jalan_files")
            if isinstance(files, list):
                for f in files:
                    if not isinstance(f, dict):
                        continue
                    file_ids = {
                        str(f.get("id") or "").strip(),
                        str(f.get("shipment_id") or "").strip(),
                        str(f.get("sales_delivery_id") or "").strip(),
                        str(f.get("sj_number") or "").strip(),
                        str(f.get("number") or "").strip(),
                    }
                    if sid not in file_ids and str(sh.get("number") or "").strip() not in file_ids:
                        continue
                    f_lines = f.get("lines")
                    if isinstance(f_lines, list) and f_lines:
                        by_idx = {
                            _safe_int(ln.get("line_idx"), default=-1): ln
                            for ln in lines
                            if isinstance(ln, dict)
                        }
                        for fl in f_lines:
                            if not isinstance(fl, dict):
                                continue
                            fidx = _safe_int(fl.get("line_idx"), default=-1)
                            src = by_idx.get(fidx)
                            if src is not None:
                                _set_line_qty(
                                    fl,
                                    max(0, _safe_int(src.get("qty") if src.get("qty") is not None else src.get("quantity"))),
                                )
                        f["total_qty"] = new_total
                        if "qty" in f:
                            f["qty"] = new_total
    return {
        "returned_qty": returned_total,
        "shipment_ids": touched_shipment_ids,
    }
