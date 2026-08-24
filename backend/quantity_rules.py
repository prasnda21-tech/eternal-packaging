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


def qty_packed(order: dict) -> int:
    return max(0, _safe_int((order.get("packing_info") or {}).get("qty_packed")))


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
    packed = qty_packed(order)
    packed_print = max(0, packed - non_print_qty_total(order))
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
