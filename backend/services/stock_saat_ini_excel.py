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

Safety (PR #664 + #667 verification rule):
  - Never estimate cetak_sudah_dikirim via min(so_delivered, qty_tercetak).
  - Without proven print-delivery attribution → null / UNVERIFIED on that column.
  - completion_unproven / missing operator evidence → qty_tercetak null (not silent 0).
  - No SO + no operator must NOT hide proven Saldo Awal / Pembelian / Stock.
  - SO alone or operator alone is enough to activate metrics that have evidence;
    both are not required together.
  - Verified zero remains 0 when evidence proves empty.
  - Ambiguous delivery → only affected print/polos columns UNVERIFIED.
  - Cancelled SO / reversed ledger excluded by callers / aggregators.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

FORMULA_VERSION = "ssi_excel_v1_2"

STATUS_READY = "READY"
STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_INDICATIVE = "INDICATIVE"

# Display precedence (highest wins): UNVERIFIED > INDICATIVE > READY(VERIFIED).
STATUS_PRECEDENCE = (STATUS_UNVERIFIED, STATUS_INDICATIVE, STATUS_READY)

REASON_PRINT_DELIVERY_UNPROVEN = "PRINT_DELIVERY_UNPROVEN"
REASON_COMPLETION_UNPROVEN = "COMPLETION_UNPROVEN"
REASON_DEPENDENT_UNKNOWN = "DEPENDENT_UNKNOWN"
REASON_NO_OPERATOR_EVIDENCE = "NO_OPERATOR_EVIDENCE"
REASON_BASELINE_INCOMPLETE = "BASELINE_INCOMPLETE"
REASON_SIMPLE_POLOS_NO_WIP_COVERAGE = "SIMPLE_POLOS_NO_WIP_COVERAGE"

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


def _normalize_status(status: Optional[str], *, qty: Any = None) -> str:
    st = str(status or "").strip().upper()
    if st in (STATUS_UNVERIFIED, STATUS_INDICATIVE, STATUS_READY):
        return st
    if st in ("BELUM_TERSEDIA", "UNKNOWN"):
        return STATUS_UNVERIFIED
    if qty is None:
        return STATUS_UNVERIFIED
    return STATUS_READY


def qualify_column_status(
    *,
    formula_status: Optional[str],
    qty: Any,
    physical_baseline_complete: bool,
    column: str,
) -> Tuple[str, Optional[str]]:
    """Qualify display status without changing numeric payloads.

    column:
      - \"dapat\": Stock Dapat Dijual — READY only when baseline complete
      - \"polos\": Qty Polos — never READY; no API WIP/rusak/plain coverage field
    Precedence: UNVERIFIED > INDICATIVE > READY.
    """
    st = _normalize_status(formula_status, qty=qty)
    if qty is None or st == STATUS_UNVERIFIED:
        return STATUS_UNVERIFIED, None

    if column == "dapat":
        if not physical_baseline_complete:
            return STATUS_INDICATIVE, REASON_BASELINE_INCOMPLETE
        return STATUS_READY, None

    if column == "polos":
        # Safe subset (P0-B1 STOP): no field proves WIP/rusak/plain-shipment coverage.
        # Keep numeric when formula computed; never label VERIFIED/READY.
        if not physical_baseline_complete:
            return STATUS_INDICATIVE, REASON_BASELINE_INCOMPLETE
        return STATUS_INDICATIVE, REASON_SIMPLE_POLOS_NO_WIP_COVERAGE

    return st, None


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
    """Qty Tercetak from print-line operator completion only (parent, not warna).

    Uses ``print_completed_qty`` when present so non_print siap-kirim never
    inflates Qty Tercetak. Falls back to completed_qty only when operator
    evidence is explicitly present.

    Rules:
    - Catalog-only (no SO / no operator) → UNVERIFIED, never fake 0.
    - Print SO without operator confirmation → UNVERIFIED (history may be incomplete).
    - Non-print-only SO → verified 0 (nothing to print).
    - Operator evidence with 0 → verified 0.
    """
    if not isinstance(progress_row, dict):
        return _unknown(REASON_NO_OPERATOR_EVIDENCE)
    if progress_row.get("completion_unproven"):
        return _unknown(REASON_COMPLETION_UNPROVEN)

    has_print_line = bool(progress_row.get("has_print_line"))
    operator_evidence = bool(progress_row.get("operator_evidence_present"))

    if operator_evidence:
        if "print_completed_qty" in progress_row:
            return _known(max(qty_num(progress_row.get("print_completed_qty")), 0.0))
        return _known(max(qty_num(progress_row.get("completed_qty")), 0.0))

    if has_print_line:
        # Print demand exists but no usable operator confirmation yet.
        return _unknown(REASON_NO_OPERATOR_EVIDENCE)

    if "print_completed_qty" in progress_row:
        # Non-print aggregator path recorded an explicit print qty (usually 0).
        return _known(max(qty_num(progress_row.get("print_completed_qty")), 0.0))

    if qty_num(progress_row.get("ordered_qty")) > 0:
        # Valid SO baseline with no print lines → Qty Tercetak proven empty.
        return _known(0.0)

    # Catalog-only / no SO baseline / no operator evidence.
    return _unknown(REASON_NO_OPERATOR_EVIDENCE)


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
    qty_tercetak: Any = None,
    qty_tercetak_status: Optional[str] = None,
    qty_tercetak_reason: Optional[str] = None,
    cetak_delivered_proven: Any = None,
    print_delivery_proven: bool = False,
    # Deprecated alias — treated as proven only when explicitly provided.
    cetak_delivered_explicit: Any = None,
    stock_book: Any = None,
) -> Dict[str, Any]:
    """Pure Excel SSI row math with column-level verification.

    Saldo Awal / Pembelian / Stock / Stock Dapat Dijual stay numeric whenever
    book components are known. Activity columns (Tercetak / Cetak / Polos /
    Belum Kirim) are READY only with sufficient evidence; otherwise null on
    the affected column only — never blank the whole row.
    """
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
    # Outstanding SO may be zero (no SO / all cancelled) — still a valid baseline.
    open_commitment = po - so_del
    stock_dapat_dijual = _as_pcs(stock - open_commitment)

    # Qty Tercetak: None / UNVERIFIED = no usable operator evidence (not verified 0).
    tercetak_unverified = (
        qty_tercetak is None
        or str(qty_tercetak_status or "").upper() == STATUS_UNVERIFIED
    )
    if tercetak_unverified:
        tercetak_slot = _unknown(
            str(qty_tercetak_reason or "").strip() or REASON_NO_OPERATOR_EVIDENCE
        )
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

    # Polos / Belum Kirim need both tercetak and cetak READY. If only delivery is
    # ambiguous, tercetak can stay numeric while dependents are UNVERIFIED.
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
    physical_baseline_complete: Optional[bool] = None,
) -> Dict[str, Any]:
    """Merge Excel columns into an existing SSI catalog row (mutates + returns).

    Numeric formulas are preserved. Status fields are qualified for display:
    incomplete physical baseline → INDICATIVE on Stock Dapat Dijual; Qty Polos
    never READY (no WIP/rusak/plain coverage contract in API).
    """
    out = row
    # Default: treat balance_present as completeness (legacy callers / unit tests).
    # Catalog wiring should pass explicit physical_baseline_complete from
    # reconciled warehouse coverage.
    baseline_complete = (
        bool(balance_present)
        if physical_baseline_complete is None
        else bool(physical_baseline_complete)
    )
    out["saldo_awal"] = excel["saldo_awal"]
    out["pembelian"] = excel["pembelian"]
    out["stock"] = excel["stock"]
    out["stock_dapat_dijual"] = excel["stock_dapat_dijual"]
    out["qty_tercetak"] = excel["qty_tercetak"]
    out["qty_tercetak_status"] = excel.get("qty_tercetak_status") or STATUS_READY
    out["qty_tercetak_reason"] = excel.get("qty_tercetak_reason")
    out["qty_polos"] = excel["qty_polos"]
    out["tercetak_belum_kirim"] = excel["tercetak_belum_kirim"]
    out["tercetak_belum_kirim_status"] = excel.get("tercetak_belum_kirim_status") or STATUS_READY
    out["tercetak_belum_kirim_reason"] = excel.get("tercetak_belum_kirim_reason")
    out["cetak_sudah_dikirim"] = excel["cetak_sudah_dikirim"]
    out["cetak_sudah_dikirim_status"] = excel.get("cetak_sudah_dikirim_status") or STATUS_READY
    out["cetak_sudah_dikirim_reason"] = excel.get("cetak_sudah_dikirim_reason")
    out["ssi_formula_version"] = excel["formula_version"]
    out["ssi_sources"] = excel.get("sources") or {}
    out["ssi_excel_ready"] = bool(balance_present)
    out["physical_baseline_complete"] = baseline_complete

    def _unverified_note(reason: Optional[str], fallback: str) -> str:
        code = str(reason or "").strip().upper()
        mapping = {
            REASON_PRINT_DELIVERY_UNPROVEN: (
                "Belum terverifikasi: pengiriman cetak belum terbukti dari SJ print "
                "(jangan pakai estimasi min(so_delivered, qty_tercetak))."
            ),
            REASON_COMPLETION_UNPROVEN: (
                "Belum terverifikasi: konfirmasi selesai cetak induk ambigu "
                "(multiwarna/Add On/proses belum terikat)."
            ),
            REASON_NO_OPERATOR_EVIDENCE: (
                "Belum terverifikasi: belum ada konfirmasi cetak selesai operator "
                "yang valid (bukan nol palsu)."
            ),
            REASON_DEPENDENT_UNKNOWN: (
                "Belum terverifikasi: bergantung kolom Qty Tercetak / Cetak Sudah Dikirim "
                "yang belum pasti; WIP/rusak tidak dihitung dari data fiktif."
            ),
            REASON_BASELINE_INCOMPLETE: (
                "Indikatif — saldo fisik belum lengkap / belum direkonsiliasi untuk semua "
                "gudang operasional dalam lingkup."
            ),
            REASON_SIMPLE_POLOS_NO_WIP_COVERAGE: (
                "Indikatif — Qty Polos = Stock − Tercetak Belum Kirim (formula sederhana); "
                "belum ada field bukti terpisah untuk WIP/rusak/pengiriman polos."
            ),
        }
        return mapping.get(code, fallback)

    # --- Qty Polos: qualify; keep numeric when formula produced one ---
    polos_formula_st = excel.get("qty_polos_status")
    polos_q = excel["qty_polos"]
    polos_st, polos_qual_rs = qualify_column_status(
        formula_status=polos_formula_st,
        qty=polos_q,
        physical_baseline_complete=baseline_complete,
        column="polos",
    )
    if polos_st == STATUS_UNVERIFIED:
        polos_rs = excel.get("qty_polos_reason") or REASON_DEPENDENT_UNKNOWN
        out["qty_polos_status"] = STATUS_UNVERIFIED
        out["qty_polos_reason"] = polos_rs
        out["qty_polos_gudang"] = None
        out["qty_polos_gudang_status"] = "BELUM_TERSEDIA"
        out["qty_polos_gudang_reason"] = polos_rs
        out["qty_polos_gudang_note"] = _unverified_note(
            polos_rs,
            "Qty Polos belum terverifikasi — bukti cetak/kirim/WIP/rusak tidak cukup.",
        )
    else:
        # INDICATIVE (safe subset) — number retained, never READY/terverifikasi
        out["qty_polos_status"] = STATUS_INDICATIVE
        out["qty_polos_reason"] = polos_qual_rs
        out["qty_polos_gudang"] = polos_q
        out["qty_polos_gudang_status"] = STATUS_INDICATIVE
        out["qty_polos_gudang_reason"] = polos_qual_rs
        out["qty_polos_gudang_note"] = _unverified_note(
            polos_qual_rs,
            "Qty Polos indikatif (formula sederhana; tanpa bukti WIP/rusak terpisah).",
        )

    # --- Qty Tercetak legacy gudang mapping (unchanged readiness semantics) ---
    tercetak_st = _normalize_status(
        excel.get("qty_tercetak_status"), qty=excel["qty_tercetak"]
    )
    if tercetak_st == STATUS_UNVERIFIED or excel["qty_tercetak"] is None:
        cetak_rs = excel.get("qty_tercetak_reason") or REASON_DEPENDENT_UNKNOWN
        out["qty_sudah_cetak_gudang"] = None
        out["qty_sudah_cetak_gudang_status"] = "BELUM_TERSEDIA"
        out["qty_sudah_cetak_gudang_reason"] = cetak_rs
        out["qty_sudah_cetak_gudang_note"] = _unverified_note(
            cetak_rs,
            "Qty Tercetak belum terverifikasi — proses multiwarna/Add On ambigu.",
        )
    else:
        out["qty_sudah_cetak_gudang"] = excel["qty_tercetak"]
        out["qty_sudah_cetak_gudang_status"] = STATUS_READY
        out["qty_sudah_cetak_gudang_reason"] = None
        out["qty_sudah_cetak_gudang_note"] = (
            "Qty Tercetak dari konfirmasi operator (parent SO)"
        )

    # --- Stock Dapat Dijual: keep numeric; qualify status ---
    dapat_q = excel["stock_dapat_dijual"]
    dapat_st, dapat_rs = qualify_column_status(
        formula_status=STATUS_READY,
        qty=dapat_q,
        physical_baseline_complete=baseline_complete,
        column="dapat",
    )
    out["qty_siap_jual"] = dapat_q
    out["qty_siap_jual_status"] = dapat_st
    out["qty_siap_jual_reason"] = dapat_rs
    out["stock_dapat_dijual_status"] = dapat_st
    out["stock_dapat_dijual_reason"] = dapat_rs
    if dapat_st == STATUS_INDICATIVE:
        out["qty_siap_jual_note"] = (
            "Indikatif — stok fisik belum lengkap; angka komitmen SO tetap dihitung "
            "dari stock buku yang tersedia (boleh negatif)."
        )
    else:
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
        "MANUAL_ADJUSTMENT",
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
