"""Stock Saat Ini — Owner Excel formula contract (PCS) with honest unknowns.

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

Formulas (Owner Excel — unchanged business meaning):
  stock = saldo_awal + pembelian + adjustment_net − delivered_total
        (or authoritative physical book when provided)
  open_so_commitment = po_ordered − so_delivered
  stock_dapat_dijual = stock − open_so_commitment
  qty_tercetak = operator selesai on physical SO parent (NOT sum of warna)
  cetak_sudah_dikirim = confirmed SJ of **printed** goods only (proven)
  tercetak_belum_kirim = qty_tercetak − cetak_sudah_dikirim
  qty_polos = stock − tercetak_belum_kirim

Safety (PR #664 fix):
  - Never estimate cetak_sudah_dikirim via min(so_delivered, qty_tercetak).
  - Without proven print-delivery attribution → null / UNVERIFIED.
  - completion_unproven → qty_tercetak null (not silent 0); dependents null.
  - Verified zero remains 0 when evidence proves empty.
  - Cancelled SO / reversed ledger excluded by callers / aggregators.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

FORMULA_VERSION = "ssi_excel_v1_1"

STATUS_READY = "READY"
STATUS_UNVERIFIED = "UNVERIFIED"

REASON_PRINT_DELIVERY_UNPROVEN = "PRINT_DELIVERY_UNPROVEN"
REASON_COMPLETION_UNPROVEN = "COMPLETION_UNPROVEN"
REASON_DEPENDENT_UNKNOWN = "DEPENDENT_UNKNOWN"

SOURCE_SALDO_AWAL = "physical_stock_ledger.OPENING_BALANCE"
SOURCE_PEMBELIAN = "physical_stock_ledger.PURCHASE_RECEIPT"
SOURCE_STOCK = "physical_stock_balances.physical_qty"
SOURCE_STOCK_COMPUTED = "saldo_awal+pembelian+adjustment-delivered"
SOURCE_PO = "orders.line_items.quantity (active)"
SOURCE_TERCETAK = "orders.operator_splits → physical parent (not sum warna)"
SOURCE_CETAK_DIKIRIM = "confirmed SJ lines on print SO parents only"
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


def _unknown(reason: str) -> Dict[str, Any]:
    return {"qty": None, "status": STATUS_UNVERIFIED, "reason": reason}


def _known(qty: Any, *, reason: Optional[str] = None) -> Dict[str, Any]:
    return {
        "qty": _as_pcs(qty_num(qty)),
        "status": STATUS_READY,
        "reason": reason,
    }


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


def resolve_cetak_sudah_dikirim(
    *,
    qty_tercetak: Any,
    cetak_delivered_proven: Any = None,
    print_delivery_proven: bool = False,
) -> Dict[str, Any]:
    """Printed goods already shipped — proven print SJ only.

    - print_delivery_proven + numeric qty → READY (capped by qty_tercetak when known)
    - otherwise → UNVERIFIED null (never min(so_delivered, qty_tercetak))
    """
    if not print_delivery_proven or cetak_delivered_proven is None:
        return _unknown(REASON_PRINT_DELIVERY_UNPROVEN)
    cetak = max(qty_num(cetak_delivered_proven), 0.0)
    if qty_tercetak is None:
        # Print out proven but tercetak unknown — still publish proven shipped print qty.
        return _known(cetak)
    tercetak = max(qty_num(qty_tercetak), 0.0)
    return _known(min(cetak, tercetak))


def resolve_qty_tercetak(progress_row: Optional[dict]) -> Dict[str, Any]:
    """Qty Tercetak from aggregate_operational_orders (parent, not warna sum)."""
    if not isinstance(progress_row, dict):
        return _known(0.0)
    if progress_row.get("completion_unproven"):
        return _unknown(REASON_COMPLETION_UNPROVEN)
    return _known(max(qty_num(progress_row.get("completed_qty")), 0.0))


# Backward-compatible name used by older tests/callers.
def parent_qty_tercetak_from_progress(progress_row: Optional[dict]) -> Any:
    """Return READY qty or None when unproven (never silent fake 0 for unproven)."""
    resolved = resolve_qty_tercetak(progress_row)
    return resolved["qty"]


def compute_excel_columns(
    *,
    saldo_awal: Any = 0,
    pembelian: Any = 0,
    adjustment_net: Any = 0,
    delivered_total: Any = 0,
    po_ordered: Any = 0,
    so_delivered: Any = None,
    qty_tercetak: Any = 0,
    qty_tercetak_status: Optional[str] = None,
    cetak_delivered_proven: Any = None,
    print_delivery_proven: bool = False,
    # Deprecated alias — treated as proven only when explicitly provided.
    cetak_delivered_explicit: Any = None,
    stock_book: Any = None,
) -> Dict[str, Any]:
    """Pure Excel SSI row math. Unknown print/completion → null dependents."""
    sa = qty_num(saldo_awal)
    pb = qty_num(pembelian)
    adj = qty_num(adjustment_net)
    delivered = max(qty_num(delivered_total), 0.0)
    so_del = delivered if so_delivered is None else max(qty_num(so_delivered), 0.0)
    po = max(qty_num(po_ordered), 0.0)

    stock = compute_stock_book(
        saldo_awal=sa,
        pembelian=pb,
        adjustment_net=adj,
        delivered_total=delivered,
        stock_book=stock_book,
    )
    open_commitment = po - so_del
    stock_dapat_dijual = _as_pcs(stock - open_commitment)

    # Qty Tercetak: None means unverified (not verified zero).
    tercetak_unverified = (
        qty_tercetak is None
        or str(qty_tercetak_status or "").upper() == STATUS_UNVERIFIED
    )
    if tercetak_unverified:
        tercetak_slot = _unknown(REASON_COMPLETION_UNPROVEN)
    else:
        tercetak_slot = _known(max(qty_num(qty_tercetak), 0.0))

    proven_flag = bool(print_delivery_proven)
    proven_qty = cetak_delivered_proven
    if cetak_delivered_explicit is not None and not proven_flag:
        # Explicit proven print delivery from caller/tests (Owner Excel path).
        proven_flag = True
        proven_qty = cetak_delivered_explicit

    cetak_slot = resolve_cetak_sudah_dikirim(
        qty_tercetak=tercetak_slot["qty"],
        cetak_delivered_proven=proven_qty,
        print_delivery_proven=proven_flag,
    )

    if tercetak_slot["qty"] is None or cetak_slot["qty"] is None:
        belum_slot = _unknown(REASON_DEPENDENT_UNKNOWN)
        polos_slot = _unknown(REASON_DEPENDENT_UNKNOWN)
    else:
        belum_slot = _known(tercetak_slot["qty"] - cetak_slot["qty"])
        polos_slot = _known(stock - belum_slot["qty"])

    return {
        "saldo_awal": _as_pcs(sa),
        "pembelian": _as_pcs(pb),
        "adjustment_net": _as_pcs(adj),
        "stock": stock,
        "stock_dapat_dijual": stock_dapat_dijual,
        "qty_tercetak": tercetak_slot["qty"],
        "qty_tercetak_status": tercetak_slot["status"],
        "qty_tercetak_reason": tercetak_slot.get("reason"),
        "qty_polos": polos_slot["qty"],
        "qty_polos_status": polos_slot["status"],
        "qty_polos_reason": polos_slot.get("reason"),
        "tercetak_belum_kirim": belum_slot["qty"],
        "tercetak_belum_kirim_status": belum_slot["status"],
        "tercetak_belum_kirim_reason": belum_slot.get("reason"),
        "cetak_sudah_dikirim": cetak_slot["qty"],
        "cetak_sudah_dikirim_status": cetak_slot["status"],
        "cetak_sudah_dikirim_reason": cetak_slot.get("reason"),
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
    out["qty_tercetak_status"] = excel.get("qty_tercetak_status") or STATUS_READY
    out["qty_tercetak_reason"] = excel.get("qty_tercetak_reason")
    out["qty_polos"] = excel["qty_polos"]
    out["qty_polos_status"] = excel.get("qty_polos_status") or STATUS_READY
    out["qty_polos_reason"] = excel.get("qty_polos_reason")
    out["tercetak_belum_kirim"] = excel["tercetak_belum_kirim"]
    out["tercetak_belum_kirim_status"] = excel.get("tercetak_belum_kirim_status") or STATUS_READY
    out["tercetak_belum_kirim_reason"] = excel.get("tercetak_belum_kirim_reason")
    out["cetak_sudah_dikirim"] = excel["cetak_sudah_dikirim"]
    out["cetak_sudah_dikirim_status"] = excel.get("cetak_sudah_dikirim_status") or STATUS_READY
    out["cetak_sudah_dikirim_reason"] = excel.get("cetak_sudah_dikirim_reason")
    out["ssi_formula_version"] = excel["formula_version"]
    out["ssi_sources"] = excel.get("sources") or {}
    out["ssi_excel_ready"] = bool(balance_present)

    def _map_condition(qty, status, reason, note_ready: str, note_unverified: str):
        st = status or (STATUS_UNVERIFIED if qty is None else STATUS_READY)
        if st == STATUS_UNVERIFIED or qty is None:
            return None, "BELUM_TERSEDIA", reason or REASON_DEPENDENT_UNKNOWN, note_unverified
        return qty, "READY", None, note_ready

    polos_q, polos_st, polos_rs, polos_note = _map_condition(
        excel["qty_polos"],
        excel.get("qty_polos_status"),
        excel.get("qty_polos_reason"),
        "Qty Polos = Stock − Tercetak Belum Kirim (PCS)",
        "Qty Polos belum terverifikasi — bukti cetak/kirim ambigu.",
    )
    out["qty_polos_gudang"] = polos_q
    out["qty_polos_gudang_status"] = (
        "BELUM_TERSEDIA" if not balance_present and polos_q is None else polos_st
    )
    out["qty_polos_gudang_reason"] = polos_rs if polos_q is None else None
    out["qty_polos_gudang_note"] = polos_note

    cetak_q, cetak_st, cetak_rs, cetak_note = _map_condition(
        excel["qty_tercetak"],
        excel.get("qty_tercetak_status"),
        excel.get("qty_tercetak_reason"),
        "Qty Tercetak dari konfirmasi operator (parent SO)",
        "Qty Tercetak belum terverifikasi — proses multiwarna/Add On ambigu.",
    )
    out["qty_sudah_cetak_gudang"] = cetak_q
    out["qty_sudah_cetak_gudang_status"] = cetak_st
    out["qty_sudah_cetak_gudang_reason"] = cetak_rs
    out["qty_sudah_cetak_gudang_note"] = cetak_note

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
            s["delivered_total"] = _as_pcs(s["delivered_total"] + abs(delta))
        elif mt in adjust_set:
            s["adjustment_net"] = _as_pcs(s["adjustment_net"] + delta)
    return out


def aggregate_proven_print_delivered_qty(
    orders: Iterable[dict],
    accurate_owners: Optional[Dict[str, str]] = None,
    *,
    process_master_ids: Optional[Iterable[str]] = None,
) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Cetak Sudah Dikirim per master from proven SJ lines on **print** SO parents.

    Reuses Compatibility shipment confirmation attribution, then keeps only qty
    attributed to ``is_print_line`` physical parents (not non_print / polos).

    Returns (qty_by_master, provenance_by_master):
      - confirmed_print_shipment
      - unproven (ambiguous → caller must publish null, not estimate)
    Masters with neither proven print delivery nor unproven flag are absent
    (caller treats as verified 0 when total delivery attribution is otherwise clean).
    """
    from quantity_rules import is_print_line
    from services.stock_compatibility_read import (
        _delivery_line_qty,
        _line_master_id,
        _mark_physical_masters_unproven,
        _order_cancelled,
        _physical_delivery_master_id,
        _qty_num,
    )
    from services.r2_go_live import (
        _confirmation_line_qtys,
        _delivery_identity_tokens,
        _proven_shipped_qty_for_shipment,
        _resolve_proven_line_attribution,
    )

    owners = accurate_owners or {}
    process_ids = {
        str(mid).strip() for mid in (process_master_ids or []) if str(mid).strip()
    }
    order_by_id: Dict[str, dict] = {}
    for order in orders:
        if _order_cancelled(order):
            continue
        oid = str(order.get("id") or "").strip()
        if oid:
            order_by_id[oid] = order

    delivered: Dict[str, float] = {}
    provenance: Dict[str, str] = {}
    unproven_masters: Dict[str, bool] = {}

    def add(mid: str, qty: float) -> None:
        if not mid or mid in process_ids or qty <= 0:
            return
        delivered[mid] = _as_pcs(_qty_num(delivered.get(mid, 0)) + qty)
        if provenance.get(mid) != "unproven":
            provenance[mid] = "confirmed_print_shipment"

    for order in order_by_id.values():
        order_id = str(order.get("id") or "").strip()
        order_lines = list(order.get("line_items") or [])
        if not order_lines:
            continue
        seen_identities: set = set()
        for sh in order.get("shipments") or []:
            if not isinstance(sh, dict):
                continue
            # Cancelled / void shipment — skip
            st = str(sh.get("status") or sh.get("delivery_status") or "").strip().lower()
            if st in ("cancelled", "dibatalkan", "void"):
                continue
            proven = _proven_shipped_qty_for_shipment(order, sh)
            if proven <= 0:
                continue
            tokens = _delivery_identity_tokens(sh)
            if tokens and any(t in seen_identities for t in tokens):
                # Retry/duplicate identity — do not double-count
                continue
            for t in tokens:
                seen_identities.add(t)

            confirmation_lines = _confirmation_line_qtys(
                order, sh, order_line_count=len(order_lines),
            )
            shipment_lines: Dict[int, float] = {}
            if not confirmation_lines:
                line_rows = sh.get("lines") if isinstance(sh.get("lines"), list) else []
                unresolved = False
                for ln in line_rows:
                    if not isinstance(ln, dict):
                        continue
                    qty = _delivery_line_qty(ln)
                    if qty <= 0:
                        continue
                    try:
                        idx = int(ln.get("line_idx"))
                    except (TypeError, ValueError):
                        unresolved = True
                        continue
                    if idx < 0 or idx >= len(order_lines):
                        unresolved = True
                        continue
                    shipment_lines[idx] = float(shipment_lines.get(idx, 0.0)) + qty
                if unresolved and len(order_lines) > 1:
                    _mark_physical_masters_unproven(
                        order_lines, owners, process_ids, unproven_masters,
                    )
                    continue

            sh_tokens = _delivery_identity_tokens(sh)
            attributed, resolve_issues = _resolve_proven_line_attribution(
                order_id=order_id or "(missing-order)",
                identity=sh_tokens[0] if sh_tokens else "(missing-id)",
                proven=proven,
                order_line_count=len(order_lines),
                confirmation_lines=confirmation_lines,
                shipment_lines=shipment_lines,
            )
            if resolve_issues or not attributed:
                _mark_physical_masters_unproven(
                    order_lines, owners, process_ids, unproven_masters,
                )
                continue

            for idx, qty in attributed.items():
                if idx < 0 or idx >= len(order_lines):
                    continue
                line = order_lines[idx]
                if not isinstance(line, dict):
                    continue
                # Only print SO lines count as Cetak Sudah Dikirim.
                if not is_print_line(line):
                    continue
                parent_mid = _physical_delivery_master_id(
                    order_lines, idx, owners, process_ids,
                )
                if not parent_mid:
                    _mark_physical_masters_unproven(
                        order_lines, owners, process_ids, unproven_masters,
                    )
                    continue
                # Collapse SERVICE process onto parent once (max already handled
                # upstream for multi-warna); credit native print parent qty only.
                line_mid = _line_master_id(line, owners)
                if line_mid in process_ids:
                    # Process/SERVICE print line — credit parent once via max later.
                    # For print-delivery column, use physical parent credit only when
                    # line itself is process; take qty as attributed (already collapsed
                    # by caller patterns). Prefer parent_mid credit of this qty once.
                    add(parent_mid, float(qty))
                else:
                    add(parent_mid, float(qty))

    for mid in unproven_masters:
        if mid not in process_ids:
            provenance[mid] = "unproven"

    return delivered, provenance
