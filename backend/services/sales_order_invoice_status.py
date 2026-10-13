"""Read-only PO invoice-coverage status for Pesanan Penjualan list rows.

Draft + finalized (+ legacy empty → draft) count as invoiced.
Cancelled invoices are ignored. Accounting/journal state is not required.
Page-only batch attach — never N+1 and never scan the full five-tab set.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from services.sales_invoices import FINANCIAL_CANCELLED, effective_financial_status

STATUS_BELUM = "belum"
STATUS_SEBAGIAN = "sebagian"
STATUS_SUDAH = "sudah"
STATUS_PERLU_PEMERIKSAAN = "perlu_pemeriksaan"

STATUS_LABELS = {
    STATUS_BELUM: "Belum",
    STATUS_SEBAGIAN: "Sebagian",
    STATUS_SUDAH: "Sudah",
    STATUS_PERLU_PEMERIKSAAN: "Perlu Pemeriksaan",
}


def creator_display(raw: Any) -> str:
    text = str(raw or "").strip()
    return text if text else "Tidak diketahui"


def _po_ordered_by_line(line_items: Optional[Iterable[dict]]) -> Dict[int, int]:
    ordered: Dict[int, int] = {}
    for idx, li in enumerate(line_items or []):
        if not isinstance(li, dict):
            continue
        raw_idx = li.get("line_idx")
        if raw_idx is None:
            line_idx = idx
        else:
            try:
                line_idx = int(raw_idx)
            except (TypeError, ValueError):
                continue
        try:
            qty = int(li.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty < 0:
            qty = 0
        ordered[line_idx] = ordered.get(line_idx, 0) + qty
    return ordered


def classify_po_invoice_status(
    line_items: Optional[Iterable[dict]],
    invoices: Optional[Iterable[dict]],
) -> str:
    """Classify coverage using per-line quantities (never global qty sums)."""
    ordered = _po_ordered_by_line(line_items)
    covered: Dict[int, int] = {k: 0 for k in ordered}
    orphan = False
    invalid_line = False
    any_active = False

    for inv in invoices or []:
        if not isinstance(inv, dict):
            continue
        if effective_financial_status(inv) == FINANCIAL_CANCELLED:
            continue
        any_active = True
        for line in inv.get("lines") or []:
            if not isinstance(line, dict):
                invalid_line = True
                continue
            if "line_idx" not in line or line.get("line_idx") is None or str(line.get("line_idx")).strip() == "":
                invalid_line = True
                continue
            try:
                line_idx = int(line.get("line_idx"))
            except (TypeError, ValueError):
                invalid_line = True
                continue
            try:
                qty = int(line.get("quantity") or 0)
            except (TypeError, ValueError):
                invalid_line = True
                continue
            if line_idx not in ordered:
                orphan = True
                continue
            covered[line_idx] = covered.get(line_idx, 0) + max(0, qty)

    over = any(covered.get(k, 0) > oq for k, oq in ordered.items())
    if orphan or invalid_line or over:
        return STATUS_PERLU_PEMERIKSAAN

    if not ordered:
        # No PO lines: active invoices cannot be attributed cleanly.
        return STATUS_PERLU_PEMERIKSAAN if any_active else STATUS_BELUM

    if not any_active or all(covered.get(k, 0) == 0 for k in ordered):
        return STATUS_BELUM

    if all(covered.get(k, 0) >= oq for k, oq in ordered.items()):
        return STATUS_SUDAH

    return STATUS_SEBAGIAN


def user_can_see_invoice_status(user: Optional[dict]) -> bool:
    """Status column requires sales.invoice.view (admin always)."""
    if not user:
        return False
    if str(user.get("role") or "") == "admin":
        return True
    perms = user.get("permissions") or []
    if isinstance(perms, (set, frozenset, list, tuple)):
        return "sales.invoice.view" in perms
    return False


async def attach_sales_order_list_invoice_status(db, orders: List[dict]) -> None:
    """Attach invoice_status for the given page rows only (batch by po_id)."""
    if not orders:
        return
    po_ids = [str(o.get("id") or "").strip() for o in orders if o.get("id")]
    po_ids = [pid for pid in po_ids if pid]
    if not po_ids:
        for o in orders:
            o["invoice_status"] = STATUS_BELUM
            o["invoice_status_label"] = STATUS_LABELS[STATUS_BELUM]
        return

    line_docs = await db.orders.find(
        {"id": {"$in": po_ids}},
        {"_id": 0, "id": 1, "line_items.line_idx": 1, "line_items.quantity": 1},
    ).to_list(len(po_ids))
    lines_by_po = {
        str(doc.get("id")): (doc.get("line_items") or [])
        for doc in line_docs
        if doc.get("id")
    }

    inv_docs = await db.sales_invoices.find(
        {"po_id": {"$in": po_ids}},
        {
            "_id": 0,
            "id": 1,
            "po_id": 1,
            "invoice_number": 1,
            "financial_status": 1,
            "lines.line_idx": 1,
            "lines.quantity": 1,
        },
    ).to_list(max(500, len(po_ids) * 20))

    inv_by_po: Dict[str, List[dict]] = {}
    for inv in inv_docs:
        pid = str(inv.get("po_id") or "").strip()
        if not pid:
            continue
        inv_by_po.setdefault(pid, []).append(inv)

    for o in orders:
        pid = str(o.get("id") or "").strip()
        status = classify_po_invoice_status(lines_by_po.get(pid) or [], inv_by_po.get(pid) or [])
        o["invoice_status"] = status
        o["invoice_status_label"] = STATUS_LABELS.get(status, status)
