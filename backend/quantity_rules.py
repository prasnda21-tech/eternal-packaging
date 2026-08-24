"""Pure quantity invariants for production workflow (print vs non-print).

Used by server endpoints and unit tests. No DB / FastAPI dependency.
"""
from __future__ import annotations

from typing import Optional


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
    """Per-line helper prepared. None jika items per-line belum ada."""
    hp = order.get("helper_prep") or {}
    items = hp.get("items") or []
    for it in items:
        if _safe_int(it.get("line_idx"), default=-1) == int(line_idx):
            req = max(0, _safe_int(it.get("qty_required")))
            prep = max(0, _safe_int(it.get("qty_prepared")))
            return min(prep, req) if req else prep
    return None


def operator_assignment_cap(order: dict) -> int:
    """Hard cap assignment Operator = min(print_qty, helper_prepared jika sudah diisi)."""
    print_total = print_qty_total(order)
    prepared = helper_prepared_print_qty(order)
    if prepared is None:
        return print_total
    return min(print_total, prepared)


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


def qty_packed_for_line(order: dict, line_idx: int) -> int:
    """Packed qty satu line. Legacy tanpa packing_info.lines → 0 (jangan asumsi)."""
    lines = packing_lines_stored(order)
    if lines is None:
        return 0
    for it in lines:
        if _safe_int(it.get("line_idx"), default=-1) == int(line_idx):
            return max(0, _safe_int(it.get("packed_qty")))
    return 0


def qty_packed(order: dict) -> int:
    """Total packed = SUM(line packed) bila ada packing_info.lines, else qty_packed lama."""
    lines = packing_lines_stored(order)
    if lines is not None:
        return sum(max(0, _safe_int(it.get("packed_qty"))) for it in lines)
    return max(0, _safe_int((order.get("packing_info") or {}).get("qty_packed")))


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
    """Barang fisik yang boleh di-packing.

    available_for_packing =
      ready_non_print_qty
      + ready_completed_print_qty

    ready_completed_print_qty = min(print_completed_qty, helper_prepared_print_qty)
    Qty Tidak Cetak tidak melewati Operator. Tidak double-count.
    """
    required = packing_required_total(order)
    avail = non_print_qty_total(order) + print_available_for_packing(order)
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
    Non-cetak: qty_po (langsung, tanpa Operator).
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
        return qty
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
        qty = max(0, _safe_int(li.get("quantity")))
        print_line = is_print_line(li)
        process = "print" if print_line else "non_print"
        if print_line:
            completed = print_completed_for_line(order, idx)
            if len(print_idxs) == 1 and print_idxs[0] == idx:
                completed += _unassigned_print_completed(order)
            completed = min(completed, qty)
            prepared = helper_prepared_for_line(order, idx)
            if prepared is None and helper_agg is not None and len(print_idxs) == 1:
                prepared = helper_agg
            print_remaining = max(0, qty - completed)
        else:
            completed = 0
            prepared = None
            print_remaining = 0
        avail = available_for_packing_line(order, idx)
        packed = qty_packed_for_line(order, idx)
        rows.append({
            "line_idx": idx,
            "name": li.get("name") or f"Line #{idx + 1}",
            "color": li.get("color"),
            "production_process": process,
            "is_print": print_line,
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
    """Belum / Berjalan / Sudah — quantity-aware, backward-compatible dengan stage."""
    cs = _safe_int(order.get("current_stage"))
    required = packing_required_total(order)
    packed = qty_packed(order)

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


def validate_operator_assignment_total(
    total_split: int,
    print_total: int,
    prepared_print_qty: Optional[int] = None,
) -> Optional[str]:
    if total_split < 0:
        return "Qty assignment tidak boleh negatif."
    cap = print_total
    if prepared_print_qty is not None:
        cap = min(print_total, max(0, _safe_int(prepared_print_qty)))
    if total_split > cap:
        if prepared_print_qty is not None and cap < print_total:
            return (
                f"Total pembagian Operator ({total_split}) melebihi qty Helper Persiapan ({cap}). "
                f"Operator hanya boleh mengerjakan qty Cetak yang sudah disiapkan Helper "
                f"(maks {cap} dari {print_total} qty Cetak)."
            )
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


def validate_delivery_qty(new_delivered: int, order: dict) -> Optional[str]:
    if new_delivered is None or new_delivered < 0:
        return "Qty pengiriman tidak valid."
    packed = qty_packed(order)
    required = packing_required_total(order)
    current = qty_delivered(order)
    if new_delivered > packed:
        return f"Qty dikirim ({new_delivered}) melebihi qty packing ({packed})."
    if required and new_delivered > required:
        return f"Qty dikirim ({new_delivered}) melebihi total qty PO ({required})."
    if new_delivered < current:
        return f"Qty dikirim tidak boleh dikurangi (saat ini {current})."
    return None


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
