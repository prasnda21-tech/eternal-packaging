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

FORMULA_VERSION = "ssi_excel_v1_3"

STATUS_READY = "READY"
STATUS_UNVERIFIED = "UNVERIFIED"

REASON_PRINT_DELIVERY_UNPROVEN = "PRINT_DELIVERY_UNPROVEN"
REASON_COMPLETION_UNPROVEN = "COMPLETION_UNPROVEN"
REASON_DEPENDENT_UNKNOWN = "DEPENDENT_UNKNOWN"
REASON_NO_OPERATOR_EVIDENCE = "NO_OPERATOR_EVIDENCE"
REASON_WIP_COMPOSITION_UNPROVEN = "WIP_COMPOSITION_UNPROVEN"
REASON_BLANK_PRINT_COMPLETION_UNPROVEN = "BLANK_PRINT_COMPLETION_UNPROVEN"
REASON_BRANCH_DEMAND_WAREHOUSE_SCOPE = "BRANCH_DEMAND_WAREHOUSE_SCOPE"
REASON_ORDERS_FETCH_CAP_REACHED = "ORDERS_FETCH_CAP_REACHED"
# Catalog orders.find(...).to_list(N) hard cap in list_physical_stock_catalog.
# Hitting the cap means the SO walk may be incomplete — never invent READY 0.
ORDERS_FETCH_LIMIT = 20000

SOURCE_SALDO_AWAL = "physical_stock_ledger.OPENING_BALANCE"
SOURCE_PEMBELIAN = "physical_stock_ledger.PURCHASE_RECEIPT"
SOURCE_STOCK = "physical_stock_balances.physical_qty"
SOURCE_STOCK_COMPUTED = "saldo_awal+pembelian+adjustment-delivered"
SOURCE_PO = "orders.line_items.quantity (active)"
SOURCE_TERCETAK = "orders.operator_splits → physical parent (not sum warna)"
SOURCE_CETAK_DIKIRIM = "confirmed SJ lines on print SO parents only"
SOURCE_POLOS = "stock - tercetak_belum_kirim (UNVERIFIED when incomplete print)"
SOURCE_STOCK_DAPAT = "stock - open_so_commitment (active SO/line attributed)"
SOURCE_BLANK_COMMIT = (
    "non_print: max(0, ordered-delivered); "
    "print parent: max(0, ordered-print_completed) — never sum SERVICE/Add On"
)
SOURCE_POLOS_DAPAT = "qty_polos - blank_open_commitment (branch scope; fail-closed on WH view)"
SOURCE_SISA_BELUM_CETAK = (
    "print parent only: max(0, ordered-print_completed); "
    "same completion evidence as blank print need — never subtract shipment again"
)


def open_so_commitment_qty(*, po_ordered: Any = 0, so_delivered: Any = 0) -> float:
    """Outstanding SO commitment never goes negative (cancel / over-delivery)."""
    return _as_pcs(max(0.0, qty_num(po_ordered) - qty_num(so_delivered)))


def _line_identity(line: dict) -> Tuple[Optional[str], Optional[int]]:
    """Return (line_id, line_idx) — prefer stable line_id when present."""
    if not isinstance(line, dict):
        return None, None
    lid = str(line.get("line_id") or line.get("id") or "").strip() or None
    raw_idx = line.get("line_idx")
    idx: Optional[int]
    try:
        idx = int(raw_idx) if raw_idx is not None and str(raw_idx).strip() != "" else None
    except (TypeError, ValueError):
        idx = None
    return lid, idx


def aggregate_active_so_commitment(
    *,
    active_lines: Iterable[dict],
    attributed_deliveries: Iterable[dict],
) -> Dict[str, float]:
    """Per-master outstanding commitment from active SO lines only.

    Each active_lines entry: order_id, master_item_id, ordered_qty, optional
    line_id / line_idx.

    Each attributed_deliveries entry: order_id, master_item_id, qty, optional
    line_id / line_idx — already filtered to active orders by the caller.
    Deliveries that cannot be matched to exactly one active line identity are
    ignored (fail closed: do not invent a product-level guess).

    Commitment per matched line = max(0, ordered − delivered). Aggregated by
    master_item_id. Cancelled SO deliveries must not appear in attributed_deliveries.
    """
    # Keyed by (order_id, master_id, line_id|None, line_idx|None)
    ordered_slots: Dict[Tuple[str, str, Optional[str], Optional[int]], float] = {}
    # Ambiguous master on an order (multiple lines, delivery lacks line id)
    lines_per_order_master: Dict[Tuple[str, str], int] = {}

    for raw in active_lines:
        if not isinstance(raw, dict):
            continue
        oid = str(raw.get("order_id") or "").strip()
        mid = str(raw.get("master_item_id") or "").strip()
        if not oid or not mid:
            continue
        ordered = max(qty_num(raw.get("ordered_qty")), 0.0)
        if ordered <= 0:
            continue
        lid, idx = _line_identity(raw)
        key = (oid, mid, lid, idx)
        ordered_slots[key] = qty_num(ordered_slots.get(key, 0.0)) + ordered

    for oid, mid, _lid, _idx in ordered_slots:
        om = (oid, mid)
        lines_per_order_master[om] = lines_per_order_master.get(om, 0) + 1

    delivered_slots: Dict[Tuple[str, str, Optional[str], Optional[int]], float] = {}
    for raw in attributed_deliveries:
        if not isinstance(raw, dict):
            continue
        oid = str(raw.get("order_id") or "").strip()
        mid = str(raw.get("master_item_id") or "").strip()
        qty = max(qty_num(raw.get("qty")), 0.0)
        if not oid or not mid or qty <= 0:
            # Missing identity — fail closed (do not reduce any commitment).
            continue
        lid, idx = _line_identity(raw)
        if lid is None and idx is None:
            # Order+master only: attribute only when that order has exactly one
            # active line for the master. Otherwise ambiguous → skip.
            if lines_per_order_master.get((oid, mid), 0) != 1:
                continue
            # Resolve the sole slot key for this order+master.
            matches = [k for k in ordered_slots if k[0] == oid and k[1] == mid]
            if len(matches) != 1:
                continue
            key = matches[0]
        else:
            key = (oid, mid, lid, idx)
            if key not in ordered_slots:
                # Try matching by line_id alone or line_idx alone when the other
                # side only stamped one identity field.
                alt = None
                if lid is not None:
                    cands = [
                        k for k in ordered_slots
                        if k[0] == oid and k[1] == mid and k[2] == lid
                    ]
                    if len(cands) == 1:
                        alt = cands[0]
                if alt is None and idx is not None:
                    cands = [
                        k for k in ordered_slots
                        if k[0] == oid and k[1] == mid and k[3] == idx
                    ]
                    if len(cands) == 1:
                        alt = cands[0]
                if alt is None:
                    continue
                key = alt
        delivered_slots[key] = qty_num(delivered_slots.get(key, 0.0)) + qty

    out: Dict[str, float] = {}
    for key, ordered in ordered_slots.items():
        mid = key[1]
        delivered = qty_num(delivered_slots.get(key, 0.0))
        commitment = max(0.0, ordered - delivered)
        if commitment <= 0:
            continue
        out[mid] = _as_pcs(qty_num(out.get(mid, 0.0)) + commitment)
    return out


def incomplete_print_demand(progress_row: Optional[dict]) -> bool:
    """True when print demand remains unfinished — polos/WIP composition uncertain.

    Does not invent a numeric WIP qty. Relies on print_ordered_qty vs
    print_completed_qty when both are known.
    """
    if not isinstance(progress_row, dict):
        return False
    if not progress_row.get("has_print_line"):
        return False
    if "print_ordered_qty" not in progress_row or "print_completed_qty" not in progress_row:
        return False
    ordered = max(qty_num(progress_row.get("print_ordered_qty")), 0.0)
    completed = max(qty_num(progress_row.get("print_completed_qty")), 0.0)
    return ordered > completed


def blank_need_for_physical_line(
    *,
    ordered_qty: Any,
    is_print: bool,
    print_completed_qty: Any = None,
    print_completion_proven: bool = False,
    delivered_qty: Any = 0,
) -> Dict[str, Any]:
    """Blank physical stock still required for one active SO parent line.

    non_print: outstanding ordered − attributed delivery.
    print: ordered − proven print_completed (finished print no longer needs blank).
    Unproven print completion → UNVERIFIED (never invent 0 need).
    SERVICE/Add On lines must not be passed here (caller excludes them).
    """
    ordered = max(qty_num(ordered_qty), 0.0)
    delivered = max(qty_num(delivered_qty), 0.0)
    if ordered <= 0:
        return _known(0.0)
    if not is_print:
        return _known(_as_pcs(max(0.0, ordered - delivered)))
    if not print_completion_proven or print_completed_qty is None:
        return _unknown(REASON_BLANK_PRINT_COMPLETION_UNPROVEN)
    completed = max(qty_num(print_completed_qty), 0.0)
    return _known(_as_pcs(max(0.0, ordered - completed)))


def sisa_belum_cetak_for_print_line(
    *,
    ordered_qty: Any,
    print_completed_qty: Any = None,
    print_completion_proven: bool = False,
) -> Dict[str, Any]:
    """Remaining print demand on one physical print parent line.

    Reuses print branch of ``blank_need_for_physical_line`` — same completion
    evidence. Does **not** subtract shipped qty (shipment is independent via
    tercetak_belum_kirim = qty_tercetak − cetak_sudah_dikirim).
    """
    return blank_need_for_physical_line(
        ordered_qty=ordered_qty,
        is_print=True,
        print_completed_qty=print_completed_qty,
        print_completion_proven=print_completion_proven,
        delivered_qty=0,
    )


def resolve_sisa_belum_cetak_catalog_slot(
    pack_by_master: Optional[Dict[str, Dict[str, Any]]],
    master_item_id: Any,
    *,
    proven_no_activity: bool = False,
    orders_fetch_capped: bool = False,
) -> Dict[str, Any]:
    """Catalog-row slot for Sisa Belum Cetak after the SO walk.

    - Fetch cap hit → UNVERIFIED (incomplete scan; do not invent READY 0).
    - proven_no_activity → READY 0.
    - Master present in pack → use pack slot.
    - Master absent after a complete walk → READY 0 (no print demand seen).
    """
    if orders_fetch_capped:
        return {
            "qty": None,
            "status": STATUS_UNVERIFIED,
            "reason": REASON_ORDERS_FETCH_CAP_REACHED,
        }
    if proven_no_activity:
        return {"qty": 0.0, "status": STATUS_READY, "reason": None}
    mid = str(master_item_id or "").strip()
    pack = pack_by_master or {}
    slot = pack.get(mid) if mid else None
    if isinstance(slot, dict):
        return {
            "qty": slot.get("qty"),
            "status": slot.get("status") or STATUS_READY,
            "reason": slot.get("reason"),
        }
    return {"qty": 0.0, "status": STATUS_READY, "reason": None}


def aggregate_blank_open_commitment(
    line_needs: Iterable[dict],
) -> Dict[str, Dict[str, Any]]:
    """Sum per-line blank needs by master_item_id.

    Any UNVERIFIED line for a master fail-closes that master's blank commitment
    (do not mix proven partial sums with inventing the unknown remainder).
    """
    acc: Dict[str, Dict[str, Any]] = {}
    for raw in line_needs:
        if not isinstance(raw, dict):
            continue
        mid = str(raw.get("master_item_id") or "").strip()
        if not mid:
            continue
        slot = acc.setdefault(
            mid,
            {"qty": 0.0, "status": STATUS_READY, "reason": None},
        )
        st = str(raw.get("status") or STATUS_READY).strip().upper()
        if st == STATUS_UNVERIFIED or raw.get("qty") is None:
            slot["status"] = STATUS_UNVERIFIED
            slot["reason"] = (
                str(raw.get("reason") or "").strip()
                or REASON_BLANK_PRINT_COMPLETION_UNPROVEN
            )
            slot["qty"] = None
            continue
        if slot["status"] == STATUS_UNVERIFIED:
            continue
        slot["qty"] = _as_pcs(qty_num(slot.get("qty")) + qty_num(raw.get("qty")))
    return acc


def resolve_stock_polos_dapat_dijual(
    *,
    qty_polos: Any,
    qty_polos_status: Optional[str] = None,
    blank_open_commitment: Any = None,
    blank_open_commitment_status: Optional[str] = None,
    blank_open_commitment_reason: Optional[str] = None,
    warehouse_scoped: bool = False,
) -> Dict[str, Any]:
    """Stock Polos Dapat Dijual = proven Qty Polos − proven blank commitment.

    Distinct from Jalur A ``stock_dapat_dijual`` (stock − all SO commitment).
    Warehouse-selected views fail closed: branch SO blank demand is not allocated
    to a single gudang.
    """
    if warehouse_scoped:
        return _unknown(REASON_BRANCH_DEMAND_WAREHOUSE_SCOPE)
    polos_st = str(qty_polos_status or "").strip().upper()
    if qty_polos is None or polos_st == STATUS_UNVERIFIED:
        return _unknown(REASON_DEPENDENT_UNKNOWN)
    blank_st = str(blank_open_commitment_status or "").strip().upper()
    if blank_open_commitment is None or blank_st == STATUS_UNVERIFIED:
        return _unknown(
            str(blank_open_commitment_reason or "").strip()
            or REASON_BLANK_PRINT_COMPLETION_UNPROVEN
        )
    return _known(_as_pcs(qty_num(qty_polos) - qty_num(blank_open_commitment)))


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
    open_so_commitment: Any = None,
    qty_tercetak: Any = None,
    qty_tercetak_status: Optional[str] = None,
    qty_tercetak_reason: Optional[str] = None,
    cetak_delivered_proven: Any = None,
    print_delivery_proven: bool = False,
    # Deprecated alias — treated as proven only when explicitly provided.
    cetak_delivered_explicit: Any = None,
    stock_book: Any = None,
    incomplete_print: bool = False,
) -> Dict[str, Any]:
    """Pure Excel SSI row math with column-level verification.

    Saldo Awal / Pembelian / Stock / Stock Dapat Dijual stay numeric whenever
    book components are known. Activity columns (Tercetak / Cetak / Polos /
    Belum Kirim) are READY only with sufficient evidence; otherwise null on
    the affected column only — never blank the whole row.

    open_so_commitment: when provided (active SO/line attribution), used as-is
    after max(0, …). Otherwise derived as max(0, po_ordered − so_delivered).
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
    # Never allow negative outstanding commitment (cancel / over-delivery).
    if open_so_commitment is not None:
        open_commitment = max(0.0, qty_num(open_so_commitment))
    else:
        open_commitment = open_so_commitment_qty(po_ordered=po, so_delivered=so_del)
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
    # Incomplete print demand → Qty Polos UNVERIFIED (do not invent numeric WIP).
    if tercetak_slot["qty"] is None or cetak_slot["qty"] is None:
        belum_slot = _unknown(REASON_DEPENDENT_UNKNOWN)
        polos_slot = _unknown(REASON_DEPENDENT_UNKNOWN)
    else:
        belum_slot = _known(tercetak_slot["qty"] - cetak_slot["qty"])
        if incomplete_print:
            polos_slot = _unknown(REASON_WIP_COMPOSITION_UNPROVEN)
        else:
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
    out["open_so_commitment"] = excel.get("open_so_commitment")
    out["po_ordered"] = excel.get("po_ordered")
    out["so_delivered"] = excel.get("so_delivered")
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
            REASON_WIP_COMPOSITION_UNPROVEN: (
                "Belum terverifikasi: permintaan cetak induk belum selesai — sisa stok "
                "bisa polos atau masih proses; WIP numerik tidak diinventaris dari demand."
            ),
        }
        return mapping.get(code, fallback)

    def _map_condition(qty, status, reason, note_ready: str, note_unverified: str):
        st = status or (STATUS_UNVERIFIED if qty is None else STATUS_READY)
        if st == STATUS_UNVERIFIED or qty is None:
            code = reason or REASON_DEPENDENT_UNKNOWN
            return None, "BELUM_TERSEDIA", code, _unverified_note(code, note_unverified)
        return qty, "READY", None, note_ready

    polos_q, polos_st, polos_rs, polos_note = _map_condition(
        excel["qty_polos"],
        excel.get("qty_polos_status"),
        excel.get("qty_polos_reason"),
        "Qty Polos = Stock − Barang Tercetak Belum Kirim (PCS); tanpa WIP/rusak terpisah",
        "Qty Polos belum terverifikasi — bukti cetak/kirim/WIP/rusak tidak cukup.",
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
        "Stock Dapat Dijual = Stock − (PO aktif − qty sudah dikirim); boleh negatif "
        "(Jalur A — bukan Stock Polos Dapat Dijual)"
    )

    # Option A blank availability (separate from Jalur A ATP).
    if "blank_open_commitment" in excel or "stock_polos_dapat_dijual" in excel:
        blank_q = excel.get("blank_open_commitment")
        blank_st = excel.get("blank_open_commitment_status") or (
            STATUS_UNVERIFIED if blank_q is None else STATUS_READY
        )
        blank_rs = excel.get("blank_open_commitment_reason")
        out["blank_open_commitment"] = blank_q
        out["blank_open_commitment_status"] = blank_st
        out["blank_open_commitment_reason"] = blank_rs
        out["blank_open_commitment_note"] = (
            "Sisa kebutuhan blank fisik dari SO (non_print: ordered−kirim; "
            "print: ordered−selesai cetak induk). Bukan alokasi per gudang."
        )
        polos_dapat = resolve_stock_polos_dapat_dijual(
            qty_polos=excel.get("qty_polos"),
            qty_polos_status=excel.get("qty_polos_status"),
            blank_open_commitment=blank_q,
            blank_open_commitment_status=blank_st,
            blank_open_commitment_reason=blank_rs,
            warehouse_scoped=bool(excel.get("blank_warehouse_scoped")),
        )
        # Allow precomputed slot from caller (already scoped).
        if "stock_polos_dapat_dijual" in excel and excel.get("stock_polos_dapat_dijual_status"):
            out["stock_polos_dapat_dijual"] = excel.get("stock_polos_dapat_dijual")
            out["stock_polos_dapat_dijual_status"] = excel.get("stock_polos_dapat_dijual_status")
            out["stock_polos_dapat_dijual_reason"] = excel.get("stock_polos_dapat_dijual_reason")
        else:
            out["stock_polos_dapat_dijual"] = polos_dapat["qty"]
            out["stock_polos_dapat_dijual_status"] = polos_dapat["status"]
            out["stock_polos_dapat_dijual_reason"] = polos_dapat.get("reason")
        out["stock_polos_dapat_dijual_note"] = (
            "Stock Polos Dapat Dijual = Qty Polos − blank commitment (bukti lengkap); "
            "terpisah dari Stock Dapat Dijual Jalur A."
        )
        sources = dict(out.get("ssi_sources") or {})
        sources["blank_open_commitment"] = SOURCE_BLANK_COMMIT
        sources["stock_polos_dapat_dijual"] = SOURCE_POLOS_DAPAT
        out["ssi_sources"] = sources

    # Sisa Belum Cetak — same print completion walk; never subtract shipment.
    if "sisa_belum_cetak" in excel or "sisa_belum_cetak_status" in excel:
        sisa_q = excel.get("sisa_belum_cetak")
        sisa_st = excel.get("sisa_belum_cetak_status") or (
            STATUS_UNVERIFIED if sisa_q is None else STATUS_READY
        )
        sisa_rs = excel.get("sisa_belum_cetak_reason")
        out["sisa_belum_cetak"] = sisa_q
        out["sisa_belum_cetak_status"] = sisa_st
        out["sisa_belum_cetak_reason"] = sisa_rs
        out["sisa_belum_cetak_note"] = (
            "Sisa Belum Cetak = max(0, ordered − selesai cetak induk) pada SO cetak aktif; "
            "tidak mengurangi pengiriman lagi (tercetak belum kirim terpisah)."
        )
        sources = dict(out.get("ssi_sources") or {})
        sources["sisa_belum_cetak"] = SOURCE_SISA_BELUM_CETAK
        out["ssi_sources"] = sources
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
                line_mid = _line_master_id(line, owners)
                parent_mid = _physical_delivery_master_id(
                    order_lines, idx, owners, process_ids,
                )
                if not parent_mid:
                    # Unlinked SERVICE: skip line only — do not blast sibling print
                    # masters on this shipment (parity with aggregate_legacy_delivered_qty).
                    if line_mid and line_mid in process_ids:
                        continue
                    _mark_physical_masters_unproven(
                        order_lines, owners, process_ids, unproven_masters,
                    )
                    continue
                # Collapse SERVICE process onto parent once (max already handled
                # upstream for multi-warna); credit native print parent qty only.
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
