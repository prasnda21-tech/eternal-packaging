"""Stock Saat Ini — Owner Excel formula contract (PCS).

Reference (files may be absent in workspace):
  ETERNAL_STOCK_SAAT_INI_ATURAN_FINAL.md
  PERHITUNGAN STOCK.xlsx

Columns (all PCS):
  1 Produk
  2 Saldo Awal
  3 Pembelian
  4 Stock
  5 Stock Dapat Dijual
  6 Qty Tercetak
  7 Qty Polos
  8 Tercetak Belum Kirim
  9 Cetak Sudah Dikirim

Formulas (verified against Owner example):
  Stock masuk 10_000, PO 32_000, operator selesai 8_000, cetak terkirim 4_000
    → Stock Dapat Dijual = −22_000
    → Qty Polos = 2_000
    → Tercetak Belum Kirim = 4_000

  stock = saldo_awal + pembelian + adjustment_net − delivered_total
        (or authoritative physical book when provided)
  open_so_commitment = po_ordered − so_delivered
        (so_delivered already reduced Stock — do not subtract again as full PO)
  stock_dapat_dijual = stock − open_so_commitment
                     ≡ (saldo_awal + pembelian + adj) − po_ordered
                       when so_delivered == delivered_total
  qty_tercetak = operator selesai on physical SO parent (NOT sum of warna)
  cetak_sudah_dikirim = confirmed SJ of printed goods (≤ qty_tercetak)
  tercetak_belum_kirim = qty_tercetak − cetak_sudah_dikirim
  qty_polos = stock − tercetak_belum_kirim

Assumptions (documented, not invented history):
  - When print vs polos delivery split is unavailable, cetak_sudah_dikirim =
    min(so_delivered, qty_tercetak). Remaining delivery is treated as polos out.
  - Negative Stock Dapat Dijual is allowed (SO may exceed stock).
  - Cancelled SO contributes 0. Partial shipment uses so_delivered qty.
  - No automatic backfill of missing opening/purchase ledger rows.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional

FORMULA_VERSION = "ssi_excel_v1"

SOURCE_SALDO_AWAL = "physical_stock_ledger.OPENING_BALANCE"
SOURCE_PEMBELIAN = "physical_stock_ledger.PURCHASE_RECEIPT"
SOURCE_STOCK = "physical_stock_balances.physical_qty"
SOURCE_STOCK_COMPUTED = "saldo_awal+pembelian+adjustment-delivered"
SOURCE_PO = "orders.line_items.quantity (active)"
SOURCE_TERCETAK = "orders.operator_splits → physical parent (not sum warna)"
SOURCE_CETAK_DIKIRIM = "SO delivery confirmed (capped by qty_tercetak)"
SOURCE_POLOS = "stock - tercetak_belum_kirim"
SOURCE_STOCK_DAPAT = "stock - (po_ordered - so_delivered)"


def qty_num(v: Any) -> float:
    try:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return 0.0
        n = float(v)
        if math.isnan(n) or math.isinf(n):
            return 0.0
        if n == int(n):
            return float(int(n))
        return n
    except (TypeError, ValueError):
        return 0.0


def _as_pcs(n: float) -> float:
    if n == int(n):
        return float(int(n))
    return n


def compute_stock_book(
    *,
    saldo_awal: Any,
    pembelian: Any,
    adjustment_net: Any = 0,
    delivered_total: Any = 0,
    stock_book: Any = None,
) -> float:
    """Authoritative Stock column.

    Prefer stock_book (R2 physical balance) when provided; else ledger components.
    """
    if stock_book is not None:
        return _as_pcs(qty_num(stock_book))
    return _as_pcs(
        qty_num(saldo_awal)
        + qty_num(pembelian)
        + qty_num(adjustment_net)
        - qty_num(delivered_total)
    )


def compute_cetak_sudah_dikirim(
    *,
    so_delivered: Any,
    qty_tercetak: Any,
    cetak_delivered_explicit: Any = None,
) -> float:
    """Printed goods already shipped.

    Explicit print-delivery qty wins when caller can prove it; otherwise
    min(so_delivered, qty_tercetak) — never invent above tercetak.
    """
    tercetak = max(qty_num(qty_tercetak), 0.0)
    if cetak_delivered_explicit is not None:
        return _as_pcs(min(max(qty_num(cetak_delivered_explicit), 0.0), tercetak))
    return _as_pcs(min(max(qty_num(so_delivered), 0.0), tercetak))


def compute_excel_columns(
    *,
    saldo_awal: Any = 0,
    pembelian: Any = 0,
    adjustment_net: Any = 0,
    delivered_total: Any = 0,
    po_ordered: Any = 0,
    so_delivered: Any = None,
    qty_tercetak: Any = 0,
    cetak_delivered_explicit: Any = None,
    stock_book: Any = None,
) -> Dict[str, Any]:
    """Pure Excel SSI row math. All quantities PCS. Negatives allowed where defined."""
    sa = qty_num(saldo_awal)
    pb = qty_num(pembelian)
    adj = qty_num(adjustment_net)
    delivered = max(qty_num(delivered_total), 0.0)
    # so_delivered defaults to delivered_total when not split (Excel example path).
    so_del = delivered if so_delivered is None else max(qty_num(so_delivered), 0.0)
    po = max(qty_num(po_ordered), 0.0)
    tercetak = max(qty_num(qty_tercetak), 0.0)

    stock = compute_stock_book(
        saldo_awal=sa,
        pembelian=pb,
        adjustment_net=adj,
        delivered_total=delivered,
        stock_book=stock_book,
    )
    open_commitment = po - so_del  # may be > po if over-delivered; keep raw for honesty
    stock_dapat_dijual = _as_pcs(stock - open_commitment)

    cetak_dikirim = compute_cetak_sudah_dikirim(
        so_delivered=so_del,
        qty_tercetak=tercetak,
        cetak_delivered_explicit=cetak_delivered_explicit,
    )
    tercetak_belum = _as_pcs(tercetak - cetak_dikirim)
    qty_polos = _as_pcs(stock - tercetak_belum)

    return {
        "saldo_awal": _as_pcs(sa),
        "pembelian": _as_pcs(pb),
        "adjustment_net": _as_pcs(adj),
        "stock": stock,
        "stock_dapat_dijual": stock_dapat_dijual,
        "qty_tercetak": _as_pcs(tercetak),
        "qty_polos": qty_polos,
        "tercetak_belum_kirim": tercetak_belum,
        "cetak_sudah_dikirim": cetak_dikirim,
        "po_ordered": _as_pcs(po),
        "so_delivered": _as_pcs(so_del),
        "delivered_total": _as_pcs(delivered),
        "open_so_commitment": _as_pcs(open_commitment),
        "formula_version": FORMULA_VERSION,
        "sources": {
            "saldo_awal": SOURCE_SALDO_AWAL,
            "pembelian": SOURCE_PEMBELIAN,
            "stock": SOURCE_STOCK if stock_book is not None else SOURCE_STOCK_COMPUTED,
            "stock_dapat_dijual": SOURCE_STOCK_DAPAT,
            "qty_tercetak": SOURCE_TERCETAK,
            "qty_polos": SOURCE_POLOS,
            "tercetak_belum_kirim": "qty_tercetak - cetak_sudah_dikirim",
            "cetak_sudah_dikirim": SOURCE_CETAK_DIKIRIM,
            "po_ordered": SOURCE_PO,
        },
    }


def apply_excel_fields_to_catalog_row(
    row: Dict[str, Any],
    excel: Dict[str, Any],
    *,
    balance_present: bool = True,
) -> Dict[str, Any]:
    """Merge Excel columns into an existing SSI catalog row (mutates + returns)."""
    out = row
    out["saldo_awal"] = excel["saldo_awal"]
    out["pembelian"] = excel["pembelian"]
    out["stock"] = excel["stock"]
    out["stock_dapat_dijual"] = excel["stock_dapat_dijual"]
    out["qty_tercetak"] = excel["qty_tercetak"]
    out["qty_polos"] = excel["qty_polos"]
    out["tercetak_belum_kirim"] = excel["tercetak_belum_kirim"]
    out["cetak_sudah_dikirim"] = excel["cetak_sudah_dikirim"]
    out["ssi_formula_version"] = excel["formula_version"]
    out["ssi_sources"] = excel.get("sources") or {}
    out["ssi_excel_ready"] = bool(balance_present)
    # Map Owner Excel names onto prior condition keys so FE/export can switch gradually.
    out["qty_polos_gudang"] = excel["qty_polos"]
    out["qty_polos_gudang_status"] = "READY" if balance_present else "BELUM_TERSEDIA"
    out["qty_polos_gudang_reason"] = None if balance_present else "NO_BALANCE"
    out["qty_polos_gudang_note"] = "Qty Polos = Stock − Tercetak Belum Kirim (PCS)"
    out["qty_sudah_cetak_gudang"] = excel["qty_tercetak"]
    out["qty_sudah_cetak_gudang_status"] = "READY"
    out["qty_sudah_cetak_gudang_reason"] = None
    out["qty_sudah_cetak_gudang_note"] = "Qty Tercetak dari konfirmasi operator (parent SO)"
    out["qty_siap_jual"] = excel["stock_dapat_dijual"]
    out["qty_siap_jual_status"] = "READY" if balance_present else "BELUM_TERSEDIA"
    out["qty_siap_jual_reason"] = None if balance_present else "NO_BALANCE"
    out["qty_siap_jual_note"] = (
        "Stock Dapat Dijual = Stock − (PO aktif − qty sudah dikirim); boleh negatif"
    )
    return out


def aggregate_ledger_components(
    movements: Iterable[dict],
    *,
    master_item_ids: Optional[Iterable[str]] = None,
    branch_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    opening_types: Iterable[str] = ("OPENING_BALANCE", "SALDO_AWAL"),
    purchase_types: Iterable[str] = ("PURCHASE_RECEIPT",),
    delivery_types: Iterable[str] = ("SALES_DELIVERY",),
    adjustment_types: Iterable[str] = (
        "STOCK_OPNAME_ADJUSTMENT",
        "ADJUSTMENT",
        "KOREKSI",
    ),
    reversal_type: str = "REVERSAL",
) -> Dict[str, Dict[str, float]]:
    """Sum ledger qty_delta into saldo_awal / pembelian / delivered / adjustment per master.

    Reversal rows that reference a movement id negate that movement's contribution
    when the original is in-scope. No historical invention when ledger empty.
    """
    wanted = {str(m).strip() for m in (master_item_ids or []) if str(m).strip()}
    branch = str(branch_id or "").strip()
    warehouse = str(warehouse_id or "").strip()
    opening_set = {str(x).strip().upper() for x in opening_types}
    purchase_set = {str(x).strip().upper() for x in purchase_types}
    delivery_set = {str(x).strip().upper() for x in delivery_types}
    adjust_set = {str(x).strip().upper() for x in adjustment_types}
    rev = str(reversal_type or "REVERSAL").strip().upper()

    docs: List[dict] = []
    by_id: Dict[str, dict] = {}
    for raw in movements:
        if not isinstance(raw, dict):
            continue
        mid = str(raw.get("master_item_id") or "").strip()
        if wanted and mid not in wanted:
            continue
        if branch and str(raw.get("branch_id") or "").strip() != branch:
            continue
        if warehouse and str(raw.get("warehouse_id") or "").strip() != warehouse:
            continue
        docs.append(raw)
        rid = str(raw.get("id") or "").strip()
        if rid:
            by_id[rid] = raw

    reversed_ids = set()
    for raw in docs:
        mt = str(raw.get("movement_type") or "").strip().upper()
        if mt != rev:
            continue
        ref = str(raw.get("reference_id") or raw.get("reverses_movement_id") or "").strip()
        if ref:
            reversed_ids.add(ref)

    out: Dict[str, Dict[str, float]] = {}

    def slot(mid: str) -> Dict[str, float]:
        return out.setdefault(
            mid,
            {
                "saldo_awal": 0.0,
                "pembelian": 0.0,
                "adjustment_net": 0.0,
                "delivered_total": 0.0,
            },
        )

    for raw in docs:
        mid = str(raw.get("master_item_id") or "").strip()
        if not mid:
            continue
        mid_key = str(raw.get("id") or "").strip()
        if mid_key and mid_key in reversed_ids:
            continue
        mt = str(raw.get("movement_type") or "").strip().upper()
        if mt == rev:
            continue
        delta = qty_num(raw.get("qty_delta"))
        s = slot(mid)
        if mt in opening_set:
            s["saldo_awal"] = _as_pcs(s["saldo_awal"] + delta)
        elif mt in purchase_set:
            s["pembelian"] = _as_pcs(s["pembelian"] + delta)
        elif mt in delivery_set:
            # Deliveries are negative deltas; store absolute qty out.
            s["delivered_total"] = _as_pcs(s["delivered_total"] + abs(delta))
        elif mt in adjust_set:
            s["adjustment_net"] = _as_pcs(s["adjustment_net"] + delta)
    return out


def parent_qty_tercetak_from_progress(progress_row: Optional[dict]) -> float:
    """Qty Tercetak from aggregate_operational_orders slot (parent, not warna sum)."""
    if not isinstance(progress_row, dict):
        return 0.0
    if progress_row.get("completion_unproven"):
        # Fail-closed: ambiguous multiwarna / unlinked process → do not publish fake tercetak.
        return 0.0
    return max(qty_num(progress_row.get("completed_qty")), 0.0)
