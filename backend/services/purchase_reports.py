"""Laporan kelompok Pembelian — Wave 1 + Wave 2.

Wave 1: Daftar Faktur Pembelian (satu baris per faktur aktif).
Wave 2: Pembelian per Barang (group ``purchase_invoices.lines`` by ``item_identity``).
Wave 2: Pembelian per Supplier (group by AP ``supplier_identity_key`` name-based).

Sumber: ``purchase_invoices`` aktif (cancelled != true).
- Tanggal filter: ``invoice_date``.
- Nilai bruto faktur; retur tidak mengurangi.
- Pajak hanya field eksplisit; tidak dikarang.
- Early invoice tetap tampil.
Read-only: tidak menulis faktur, stok, jurnal, atau master supplier.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from services.accounts_payable import supplier_identity_key
from services.purchase_common import is_cancelled, item_identity, line_subtotal
from services.sales_reports import (
    BRANCH_OPTIONS,
    BRANCH_SEMUA,
    _dec,
    _display_number,
    _invoice_branch,
    _iter_docs,
    _load_user_doc,
    _money,
    _money_str,
    _page,
    _text,
    assert_branch_allowed,
    format_period_date,
    format_row_date,
    parse_report_date,
    user_branch_options,
)
from services.sales_user_mapping import is_sales_pwa_role

PERM_VIEW = "reports.purchase.view"
PERM_EXPORT = "reports.purchase.export"
MEMORIZE_COLLECTION = "purchase_report_memorized"

REF_OPEN = (
    "https://help.accurate.id/product/accurate-online/fitur-aol/laporan/"
    "daftar-laporan/cara-membuka-laporan-accurate-online/"
)

REPORT_INVOICE = "daftar-faktur-pembelian"
REPORT_ITEM = "pembelian-per-barang"
REPORT_SUPPLIER = "pembelian-per-supplier"

NO_SUPPLIER_KEY = "__no_supplier__"
NO_SUPPLIER_LABEL = "Tanpa Supplier"
WEAK_ITEM_LABEL = "Identitas Barang Belum Terverifikasi"

_PCS_UOM = frozenset({"pcs", "pc", "piece", "pieces"})

_CATALOG: Tuple[dict, ...] = (
    {
        "id": REPORT_INVOICE,
        "name": "Daftar Faktur Pembelian",
        "description": "Daftar faktur pembelian aktif per periode invoice_date",
        "implemented": True,
        "reference": REF_OPEN,
    },
    {
        "id": REPORT_ITEM,
        "name": "Pembelian per Barang",
        "description": "Nilai pembelian bruto per identitas barang dari baris faktur aktif",
        "implemented": True,
        "reference": REF_OPEN,
    },
    {
        "id": REPORT_SUPPLIER,
        "name": "Pembelian per Supplier",
        "description": "Nilai pembelian bruto per supplier_identity_key (nama faktur)",
        "implemented": True,
        "reference": REF_OPEN,
    },
)

INVOICE_COLUMNS = (
    {"key": "tanggal", "label": "Tanggal Faktur", "align": "left", "default": True},
    {"key": "nomor", "label": "Nomor Faktur", "align": "left", "default": True},
    {"key": "nomor_po", "label": "Nomor PO", "align": "left", "default": True},
    {"key": "supplier", "label": "Supplier", "align": "left", "default": True},
    {"key": "cabang", "label": "Cabang", "align": "left", "default": True},
    {"key": "subtotal", "label": "Subtotal", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "pajak", "label": "Pajak/PPN", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "total", "label": "Total Faktur", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "status", "label": "Status", "align": "left", "default": True},
    {"key": "status_posting", "label": "Status Posting", "align": "left", "default": True},
    {"key": "indikator_awal", "label": "Indikator", "align": "left", "default": True},
)

ITEM_COLUMNS = (
    {"key": "kode_barang", "label": "Kode Barang", "align": "left", "default": True},
    {"key": "nama_barang", "label": "Nama Barang", "align": "left", "default": True},
    {"key": "identitas_barang", "label": "Identitas Barang", "align": "left", "default": False},
    {"key": "satuan", "label": "Satuan", "align": "left", "default": True},
    {"key": "jumlah_faktur", "label": "Jumlah Faktur", "align": "right", "default": True, "numeric": True},
    {"key": "kuantitas", "label": "Kuantitas", "align": "right", "default": True, "numeric": True},
    {"key": "kuantitas_pcs", "label": "Kuantitas PCS Terverifikasi", "align": "right", "default": True, "numeric": True},
    {"key": "nilai_pembelian", "label": "Nilai Pembelian", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "kelengkapan", "label": "Kelengkapan", "align": "left", "default": True},
)

SUPPLIER_COLUMNS = (
    {"key": "supplier", "label": "Supplier", "align": "left", "default": True},
    {"key": "supplier_key", "label": "Supplier Identity Key", "align": "left", "default": False},
    {"key": "jumlah_faktur", "label": "Jumlah Faktur", "align": "right", "default": True, "numeric": True},
    {"key": "subtotal", "label": "Subtotal", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "pajak", "label": "PPN", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "total", "label": "Grand Total", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "kelengkapan", "label": "Kelengkapan", "align": "left", "default": True},
)

_COLUMNS_BY_REPORT = {
    REPORT_INVOICE: INVOICE_COLUMNS,
    REPORT_ITEM: ITEM_COLUMNS,
    REPORT_SUPPLIER: SUPPLIER_COLUMNS,
}

IMPLEMENTED_IDS = frozenset(row["id"] for row in _CATALOG if row["implemented"])


class PurchaseReportError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def catalog() -> List[dict]:
    rows = []
    for row in _CATALOG:
        item = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "implemented": bool(row["implemented"]),
            "reference": row["reference"],
            "group": "Pembelian",
        }
        if row["implemented"]:
            cols = _COLUMNS_BY_REPORT[row["id"]]
            item["parameters"] = ["tanggal_dari", "tanggal_sd", "cabang"]
            item["columns"] = [
                {"key": col["key"], "label": col["label"], "align": col["align"], "default": col["default"]}
                for col in cols
            ]
        rows.append(item)
    return rows


def _selected_columns(layout: Optional[dict], report_id: str) -> List[dict]:
    source = _COLUMNS_BY_REPORT.get(report_id) or INVOICE_COLUMNS
    requested = None
    if isinstance(layout, dict) and isinstance(layout.get("columns"), list) and layout.get("columns"):
        requested = list(dict.fromkeys(_text(key) for key in layout["columns"]))
    if requested is None:
        chosen = [dict(col) for col in source if col["default"]]
    else:
        by_key = {col["key"]: col for col in source}
        chosen = [dict(by_key[key]) for key in requested if key in by_key]
    if not chosen:
        chosen = [dict(col) for col in source if col["default"]]
    return chosen


def _explicit_tax(invoice: dict) -> Optional[Decimal]:
    """Only return tax when an explicit trusted field is present. Never invent 0."""
    for key in ("tax_amount", "ppn_amount", "vat_amount", "ppn", "tax"):
        if key not in invoice:
            continue
        raw = invoice.get(key)
        if raw in (None, ""):
            continue
        return _money(_dec(raw))
    return None


def _explicit_discount(invoice: dict) -> Optional[Decimal]:
    for key in ("discount_amount", "discount", "diskon"):
        if key not in invoice:
            continue
        raw = invoice.get(key)
        if raw in (None, ""):
            continue
        return _money(_dec(raw))
    return None


def _status_label(invoice: dict) -> str:
    if is_cancelled(invoice):
        return "Dibatalkan"
    pay = _text(invoice.get("payment_status") or invoice.get("financial_status"))
    if pay:
        return pay
    return "Aktif"


def _posting_label(invoice: dict) -> str:
    status = _text(invoice.get("accounting_status"))
    if not status:
        if invoice.get("accounting_posted") is True:
            return "posted"
        if invoice.get("accounting_posted") is False:
            return "not_posted"
        return ""
    return status


def _branch_for_invoice(invoice: dict, po: Optional[dict], request: Optional[dict]) -> str:
    return _invoice_branch(invoice, po) or _invoice_branch(invoice, request) or _invoice_branch(invoice, None)


def _is_pcs_uom(value: Any) -> bool:
    return _text(value).casefold() in _PCS_UOM


def _norm_uom(value: Any) -> str:
    return _text(value).casefold()


async def _purchase_context(db) -> dict:
    invoices = await _iter_docs(db, "purchase_invoices")
    po_ids = {_text(row.get("purchase_order_id")) for row in invoices}
    po_ids.discard("")
    orders = {}
    if po_ids:
        for row in await _iter_docs(db, "purchase_orders", {"id": {"$in": list(po_ids)}}):
            orders[_text(row.get("id"))] = row
    request_ids = {_text(row.get("request_id") or (orders.get(_text(row.get("purchase_order_id"))) or {}).get("request_id")) for row in invoices}
    request_ids.discard("")
    requests = {}
    if request_ids:
        for row in await _iter_docs(db, "purchase_requests", {"id": {"$in": list(request_ids)}}):
            requests[_text(row.get("id"))] = row
    return {"invoices": invoices, "orders": orders, "requests": requests}


def _prepared_invoices(ctx: dict, start: str, end: str, branch: str, q: str = "") -> Tuple[List[dict], dict]:
    rows: List[dict] = []
    seen_ids = set()
    cancelled_excluded = 0
    needle = _text(q).casefold()
    for invoice in ctx["invoices"]:
        inv_id = _text(invoice.get("id") or invoice.get("invoice_number"))
        if not inv_id or inv_id in seen_ids:
            continue
        if is_cancelled(invoice):
            cancelled_excluded += 1
            continue
        # Active predicate mirrors with_active_filter / cancelled != true.
        seen_ids.add(inv_id)
        business = _text(invoice.get("invoice_date"))[:10]
        if not business or business < start or business > end:
            continue
        po = ctx["orders"].get(_text(invoice.get("purchase_order_id")))
        req = ctx["requests"].get(_text(invoice.get("request_id") or (po or {}).get("request_id")))
        inv_branch = _branch_for_invoice(invoice, po, req)
        if branch != BRANCH_SEMUA and inv_branch != branch:
            continue
        supplier_raw = _text(invoice.get("supplier") or (po or {}).get("supplier"))
        if needle:
            hay = " ".join([
                _text(invoice.get("invoice_number")),
                _text(invoice.get("po_number") or (po or {}).get("po_number")),
                supplier_raw,
            ]).casefold()
            if needle not in hay:
                continue
        tax = _explicit_tax(invoice)
        discount = _explicit_discount(invoice)
        early = bool(invoice.get("precedes_goods_receipt"))
        rows.append({
            "kind": "line",
            "invoice_id": invoice.get("id"),
            "invoice_number": _text(invoice.get("invoice_number")),
            "invoice_date": business,
            "po_number": _text(invoice.get("po_number") or (po or {}).get("po_number")),
            "supplier": supplier_raw,
            "branch": inv_branch,
            "subtotal": _money(_dec(invoice.get("subtotal"))) if invoice.get("subtotal") not in (None, "") else None,
            "discount": discount,
            "tax": tax,
            "grand_total": _money(_dec(invoice.get("grand_total"))) if invoice.get("grand_total") not in (None, "") else None,
            "status": _status_label(invoice),
            "posting_status": _posting_label(invoice),
            "early_invoice": early,
            "early_label": "Faktur mendahului penerimaan" if early else "",
            "lines": list(invoice.get("lines") or []),
            "drill": {
                "href": f"/pembelian/faktur-pembelian?q={_text(invoice.get('invoice_number'))}",
                "invoice_id": invoice.get("id"),
                "invoice_number": _text(invoice.get("invoice_number")),
            },
        })
    rows.sort(key=lambda row: (row["invoice_date"], row["invoice_number"] or ""))
    meta = {
        "cancelled_excluded": cancelled_excluded,
        "returns_not_netted": True,
        "tax_fabricated": False,
        "active_filter": "cancelled != true",
    }
    return rows, meta


def _line_qty(line: dict) -> Optional[Decimal]:
    if line.get("invoice_qty") not in (None, ""):
        return _dec(line.get("invoice_qty"))
    if line.get("quantity") not in (None, ""):
        return _dec(line.get("quantity"))
    return None


def _line_nilai(line: dict) -> Optional[Decimal]:
    """Nilai baris = line_subtotal, atau qty×unit_price bila ekuivalen create path."""
    if line.get("line_subtotal") not in (None, ""):
        return _money(_dec(line.get("line_subtotal")))
    qty = _line_qty(line)
    if qty is None or line.get("unit_price") in (None, ""):
        return None
    # Proven equivalent to purchase_common.line_subtotal used at invoice create.
    return _money(_dec(line_subtotal(float(qty), float(_dec(line.get("unit_price"))))))


def _item_identity_for_line(invoice_row: dict, line: dict, line_index: int) -> Tuple[str, bool]:
    verified = bool(_text(line.get("accurate_item_id")) or _text(line.get("master_item_id")))
    if verified:
        return item_identity(dict(line)), True
    # Weak identity: stable per invoice+line; never merge by display name alone.
    inv = _text(invoice_row.get("invoice_id") or invoice_row.get("invoice_number"))
    lid = _text(line.get("line_id")) or f"idx{line_index}"
    return f"weak:{inv}:{lid}", False


def _item_rows(invoices: Sequence[dict]) -> Tuple[List[dict], dict]:
    buckets: Dict[str, dict] = {}
    order: List[str] = []
    wave1_subtotal = Decimal("0.00")
    wave1_subtotal_known = True
    proven_nilai = Decimal("0.00")
    unreconciled = Decimal("0.00")
    invoices_without_lines = 0
    incomplete_line_nilai = 0

    for inv in invoices:
        if inv.get("subtotal") is None:
            wave1_subtotal_known = False
        else:
            wave1_subtotal = _money(wave1_subtotal + inv["subtotal"])
        lines = inv.get("lines") or []
        if not lines:
            invoices_without_lines += 1
            if inv.get("subtotal") is not None:
                unreconciled = _money(unreconciled + inv["subtotal"])
            continue
        line_sum = Decimal("0.00")
        line_sum_complete = True
        for idx, line in enumerate(lines):
            key, verified = _item_identity_for_line(inv, line, idx)
            nilai = _line_nilai(line)
            qty = _line_qty(line)
            unit = _text(line.get("unit") or "pcs") or "pcs"
            if key not in buckets:
                buckets[key] = {
                    "kind": "line",
                    "item_key": key,
                    "identity_verified": verified,
                    "item_code": _text(line.get("item_code")),
                    "item_name": _text(line.get("item_name")) or (WEAK_ITEM_LABEL if not verified else "—"),
                    "invoice_ids": set(),
                    "nilai": Decimal("0.00"),
                    "nilai_complete": True,
                    "qty_by_uom": {},
                    "pcs_qty": Decimal("0"),
                    "has_pcs": False,
                    "has_non_pcs": False,
                }
                order.append(key)
            bucket = buckets[key]
            if verified and _text(line.get("item_code")) and not bucket["item_code"]:
                bucket["item_code"] = _text(line.get("item_code"))
            if verified and _text(line.get("item_name")):
                bucket["item_name"] = _text(line.get("item_name"))
            inv_token = _text(inv.get("invoice_id") or inv.get("invoice_number"))
            if inv_token:
                bucket["invoice_ids"].add(inv_token)
            if nilai is None:
                bucket["nilai_complete"] = False
                line_sum_complete = False
                incomplete_line_nilai += 1
            else:
                bucket["nilai"] = _money(bucket["nilai"] + nilai)
                proven_nilai = _money(proven_nilai + nilai)
                line_sum = _money(line_sum + nilai)
            if qty is None:
                # Missing qty does not invent zero; mark UOM incomplete via empty token.
                uom_key = _norm_uom(unit) or "?"
                bucket["qty_by_uom"][uom_key] = None
            else:
                uom_key = _norm_uom(unit) or "?"
                if bucket["qty_by_uom"].get(uom_key) is None and uom_key in bucket["qty_by_uom"]:
                    pass  # already incomplete for this uom
                elif uom_key not in bucket["qty_by_uom"]:
                    bucket["qty_by_uom"][uom_key] = _dec(qty)
                elif bucket["qty_by_uom"][uom_key] is None:
                    pass
                else:
                    bucket["qty_by_uom"][uom_key] = bucket["qty_by_uom"][uom_key] + _dec(qty)
                if _is_pcs_uom(unit):
                    bucket["has_pcs"] = True
                    bucket["pcs_qty"] = bucket["pcs_qty"] + _dec(qty)
                else:
                    bucket["has_non_pcs"] = True
        if line_sum_complete and inv.get("subtotal") is not None:
            delta = _money(inv["subtotal"] - line_sum)
            if abs(delta) > Decimal("0.009"):
                unreconciled = _money(unreconciled + delta)

    rows: List[dict] = []
    for key in order:
        bucket = buckets[key]
        uoms = bucket["qty_by_uom"]
        uom_keys = [k for k, v in uoms.items()]
        qty_incomplete = (
            len(uom_keys) > 1
            or any(v is None for v in uoms.values())
            or (bucket["has_pcs"] and bucket["has_non_pcs"])
        )
        if qty_incomplete or len(uom_keys) != 1 or any(v is None for v in uoms.values()):
            quantity = None
            satuan = "campuran" if len(uom_keys) > 1 else (next(iter(uom_keys), "") if uom_keys else "")
            qty_incomplete = True
            kelengkapan_parts = ["Qty belum dapat disatukan karena satuan historis berbeda."]
        else:
            only = uom_keys[0]
            quantity = uoms[only]
            satuan = only or ""
            kelengkapan_parts = []
        pcs_verified = bucket["pcs_qty"] if bucket["has_pcs"] else None
        if not bucket["identity_verified"]:
            kelengkapan_parts.append(WEAK_ITEM_LABEL)
        if not bucket["nilai_complete"]:
            kelengkapan_parts.append("Nilai baris tidak lengkap")
        proven_nilai_row = bucket["nilai"] if bucket["nilai"] != Decimal("0.00") or bucket["nilai_complete"] else None
        if not bucket["nilai_complete"] and bucket["nilai"] == Decimal("0.00"):
            proven_nilai_row = None
        elif bucket["nilai_complete"] or bucket["nilai"] != Decimal("0.00"):
            proven_nilai_row = bucket["nilai"]
        rows.append({
            "kind": "line",
            "item_key": key,
            "identity_verified": bucket["identity_verified"],
            "item_code": bucket["item_code"],
            "item_name": bucket["item_name"] if bucket["identity_verified"] else (
                bucket["item_name"] or WEAK_ITEM_LABEL
            ),
            "satuan": satuan,
            "jumlah_faktur": len(bucket["invoice_ids"]),
            "quantity": quantity,
            "qty_incomplete": qty_incomplete,
            "pcs_verified": pcs_verified,
            "nilai": proven_nilai_row,
            "nilai_complete": bucket["nilai_complete"],
            "kelengkapan": " ".join(kelengkapan_parts).strip(),
            "drill": None,
        })

    rows.sort(key=lambda row: (
        0 if row.get("identity_verified") else 1,
        (row.get("item_name") or "").casefold(),
        row.get("item_key") or "",
    ))
    recon_status = "PASS"
    if invoices_without_lines or incomplete_line_nilai or (wave1_subtotal_known and unreconciled != Decimal("0.00")):
        recon_status = "INCOMPLETE"
    if not wave1_subtotal_known:
        recon_status = "INCOMPLETE"
    meta = {
        "reconciliation_status": recon_status,
        "wave1_subtotal": _money_str(wave1_subtotal) if wave1_subtotal_known else None,
        "item_nilai_proven": _money_str(proven_nilai),
        "unreconciled_amount": _money_str(unreconciled) if recon_status != "PASS" else _money_str(Decimal("0.00")),
        "invoices_without_lines": invoices_without_lines,
        "incomplete_line_nilai": incomplete_line_nilai,
    }
    return rows, meta


def _supplier_rows(invoices: Sequence[dict]) -> Tuple[List[dict], dict]:
    buckets: Dict[str, dict] = {}
    order: List[str] = []
    wave1_sub = Decimal("0.00")
    wave1_grand = Decimal("0.00")
    wave1_sub_known = True
    wave1_grand_known = True

    for inv in invoices:
        if inv.get("subtotal") is None:
            wave1_sub_known = False
        else:
            wave1_sub = _money(wave1_sub + inv["subtotal"])
        if inv.get("grand_total") is None:
            wave1_grand_known = False
        else:
            wave1_grand = _money(wave1_grand + inv["grand_total"])

        raw_name = _text(inv.get("supplier"))
        if not raw_name:
            key = NO_SUPPLIER_KEY
            display = NO_SUPPLIER_LABEL
            identity = ""
        else:
            identity = supplier_identity_key(raw_name)
            key = identity
            display = raw_name

        if key not in buckets:
            buckets[key] = {
                "kind": "line",
                "supplier_key": identity if key != NO_SUPPLIER_KEY else "",
                "supplier": display,
                "invoice_ids": set(),
                "subtotal": Decimal("0.00"),
                "subtotal_complete": True,
                "grand_total": Decimal("0.00"),
                "grand_complete": True,
                "tax_values": [],
            }
            order.append(key)
        bucket = buckets[key]
        # Keep first non-empty snapshot name; rename creates separate key by design.
        if key != NO_SUPPLIER_KEY and raw_name and (not bucket["supplier"] or bucket["supplier"] == NO_SUPPLIER_LABEL):
            bucket["supplier"] = raw_name
        inv_token = _text(inv.get("invoice_id") or inv.get("invoice_number"))
        if inv_token:
            bucket["invoice_ids"].add(inv_token)
        if inv.get("subtotal") is None:
            bucket["subtotal_complete"] = False
        else:
            bucket["subtotal"] = _money(bucket["subtotal"] + inv["subtotal"])
        if inv.get("grand_total") is None:
            bucket["grand_complete"] = False
        else:
            bucket["grand_total"] = _money(bucket["grand_total"] + inv["grand_total"])
        bucket["tax_values"].append(inv.get("tax"))

    rows: List[dict] = []
    for key in order:
        bucket = buckets[key]
        if any(t is None for t in bucket["tax_values"]):
            tax = None
            tax_complete = False
        else:
            tax = _money(sum((t for t in bucket["tax_values"]), Decimal("0.00")))
            tax_complete = True
        notes = []
        if key == NO_SUPPLIER_KEY:
            notes.append("Tanpa nama supplier pada faktur")
        if not tax_complete:
            notes.append("PPN tidak lengkap (hanya field eksplisit)")
        if not bucket["subtotal_complete"]:
            notes.append("Subtotal tidak lengkap")
        if not bucket["grand_complete"]:
            notes.append("Grand total tidak lengkap")
        rows.append({
            "kind": "line",
            "supplier_key": bucket["supplier_key"],
            "supplier": bucket["supplier"],
            "jumlah_faktur": len(bucket["invoice_ids"]),
            "subtotal": bucket["subtotal"] if bucket["subtotal_complete"] or bucket["subtotal"] else (
                bucket["subtotal"] if bucket["subtotal_complete"] else bucket["subtotal"]
            ),
            "subtotal_complete": bucket["subtotal_complete"],
            "tax": tax,
            "tax_complete": tax_complete,
            "grand_total": bucket["grand_total"] if bucket["grand_complete"] else bucket["grand_total"],
            "grand_complete": bucket["grand_complete"],
            "kelengkapan": "; ".join(notes),
            "drill": None,
        })
        if not bucket["subtotal_complete"] and bucket["subtotal"] == Decimal("0.00"):
            rows[-1]["subtotal"] = None
        if not bucket["grand_complete"] and bucket["grand_total"] == Decimal("0.00"):
            rows[-1]["grand_total"] = None

    rows.sort(key=lambda row: (
        1 if not row.get("supplier_key") else 0,
        (row.get("supplier") or "").casefold(),
        row.get("supplier_key") or "",
    ))

    sum_sub = _money(sum((r["subtotal"] for r in rows if r.get("subtotal") is not None), Decimal("0.00")))
    sum_grand = _money(sum((r["grand_total"] for r in rows if r.get("grand_total") is not None), Decimal("0.00")))
    recon = "PASS"
    if not wave1_sub_known or not wave1_grand_known:
        recon = "INCOMPLETE"
    elif sum_sub != wave1_sub or sum_grand != wave1_grand:
        recon = "INCOMPLETE"
    meta = {
        "reconciliation_status": recon,
        "wave1_subtotal": _money_str(wave1_sub) if wave1_sub_known else None,
        "wave1_grand_total": _money_str(wave1_grand) if wave1_grand_known else None,
        "supplier_subtotal": _money_str(sum_sub),
        "supplier_grand_total": _money_str(sum_grand),
        "grouping": "supplier_identity_key (name-based; rename may split groups)",
    }
    return rows, meta


def _cell(column: dict, value: Any) -> Any:
    if column.get("money"):
        return _money_str(value) if value is not None else None
    return value if value is not None else ""


def _project_invoice(row: dict, columns: Sequence[dict]) -> dict:
    values = {}
    for column in columns:
        key = column["key"]
        if key == "tanggal":
            raw = format_row_date(row["invoice_date"]) if row.get("invoice_date") else ""
        elif key == "nomor":
            raw = row.get("invoice_number") or ""
        elif key == "nomor_po":
            raw = row.get("po_number") or ""
        elif key == "supplier":
            raw = row.get("supplier") or ""
        elif key == "cabang":
            raw = row.get("branch") or ""
        elif key == "subtotal":
            raw = row.get("subtotal")
        elif key == "pajak":
            raw = row.get("tax")
        elif key == "total":
            raw = row.get("grand_total")
        elif key == "status":
            raw = row.get("status") or ""
        elif key == "status_posting":
            raw = row.get("posting_status") or ""
        elif key == "indikator_awal":
            raw = row.get("early_label") or ""
        else:
            raw = row.get(key)
        values[key] = _cell(column, raw)
    return {"kind": "line", "values": values, "drill": row.get("drill")}


def _project_item(row: dict, columns: Sequence[dict]) -> dict:
    values = {}
    for column in columns:
        key = column["key"]
        if key == "kode_barang":
            raw = row.get("item_code") or ""
        elif key == "nama_barang":
            raw = row.get("item_name") or ""
        elif key == "identitas_barang":
            raw = row.get("item_key") or ""
        elif key == "satuan":
            raw = row.get("satuan") or ""
        elif key == "jumlah_faktur":
            raw = row.get("jumlah_faktur")
        elif key == "kuantitas":
            raw = row.get("quantity")
        elif key == "kuantitas_pcs":
            raw = row.get("pcs_verified")
        elif key == "nilai_pembelian":
            raw = row.get("nilai")
        elif key == "kelengkapan":
            raw = row.get("kelengkapan") or ""
        else:
            raw = row.get(key)
        values[key] = _cell(column, raw)
    return {"kind": "line", "values": values, "drill": row.get("drill")}


def _project_supplier(row: dict, columns: Sequence[dict]) -> dict:
    values = {}
    for column in columns:
        key = column["key"]
        if key == "supplier":
            raw = row.get("supplier") or NO_SUPPLIER_LABEL
        elif key == "supplier_key":
            raw = row.get("supplier_key") or ""
        elif key == "jumlah_faktur":
            raw = row.get("jumlah_faktur")
        elif key == "subtotal":
            raw = row.get("subtotal")
        elif key == "pajak":
            raw = row.get("tax")
        elif key == "total":
            raw = row.get("grand_total")
        elif key == "kelengkapan":
            raw = row.get("kelengkapan") or ""
        else:
            raw = row.get(key)
        values[key] = _cell(column, raw)
    return {"kind": "line", "values": values, "drill": row.get("drill")}


def _totals_payload_invoice(rows: Sequence[dict], columns: Sequence[dict]) -> dict:
    totals: Dict[str, Any] = {"row_count": len(rows)}
    for column in columns:
        key = column["key"]
        if key == "subtotal":
            known = [row["subtotal"] for row in rows if row.get("subtotal") is not None]
            totals[key] = _money_str(sum(known, Decimal("0.00"))) if known or not rows else None
        elif key == "total":
            known = [row["grand_total"] for row in rows if row.get("grand_total") is not None]
            totals[key] = _money_str(sum(known, Decimal("0.00"))) if known or not rows else None
        elif key == "pajak":
            if any(row.get("tax") is None for row in rows):
                totals[key] = None
            else:
                totals[key] = _money_str(sum((row["tax"] for row in rows), Decimal("0.00")))
    return totals


def _totals_payload_item(rows: Sequence[dict], columns: Sequence[dict]) -> dict:
    totals: Dict[str, Any] = {"row_count": len(rows)}
    for column in columns:
        key = column["key"]
        if key == "nilai_pembelian":
            known = [row["nilai"] for row in rows if row.get("nilai") is not None]
            totals[key] = _money_str(sum(known, Decimal("0.00"))) if known or not rows else None
        elif key == "jumlah_faktur":
            totals[key] = sum(int(row.get("jumlah_faktur") or 0) for row in rows)
        elif key == "kuantitas":
            if any(row.get("qty_incomplete") or row.get("quantity") is None for row in rows):
                totals[key] = None
            else:
                totals[key] = str(sum((_dec(row.get("quantity")) for row in rows), Decimal("0")))
        elif key == "kuantitas_pcs":
            if any(row.get("pcs_verified") is None for row in rows):
                # Partial PCS across groups: sum only verified groups; if any group lacks PCS entirely it's ok
                # Spec: incomplete when mixed incompatible — total PCS null if any row has incomplete qty AND has_pcs ambiguity
                known = [row["pcs_verified"] for row in rows if row.get("pcs_verified") is not None]
                # If some rows are non-PCS-only (pcs_verified None by design), still sum verified PCS groups.
                totals[key] = str(sum(known, Decimal("0"))) if known else None
            else:
                totals[key] = str(sum((_dec(row.get("pcs_verified")) for row in rows), Decimal("0")))
    return totals


def _totals_payload_supplier(rows: Sequence[dict], columns: Sequence[dict]) -> dict:
    totals: Dict[str, Any] = {"row_count": len(rows)}
    for column in columns:
        key = column["key"]
        if key == "subtotal":
            known = [row["subtotal"] for row in rows if row.get("subtotal") is not None]
            totals[key] = _money_str(sum(known, Decimal("0.00"))) if known or not rows else None
        elif key == "total":
            known = [row["grand_total"] for row in rows if row.get("grand_total") is not None]
            totals[key] = _money_str(sum(known, Decimal("0.00"))) if known or not rows else None
        elif key == "pajak":
            if any(row.get("tax") is None for row in rows):
                totals[key] = None
            else:
                totals[key] = _money_str(sum((row["tax"] for row in rows), Decimal("0.00")))
        elif key == "jumlah_faktur":
            totals[key] = sum(int(row.get("jumlah_faktur") or 0) for row in rows)
    return totals


async def _company_name(db) -> str:
    from services.sales_reports import _company_name as shared

    return await shared(db)


async def run_report(
    db,
    *,
    user: Optional[dict],
    report_id: str,
    date_from: Any,
    date_to: Any,
    branch: Any = BRANCH_SEMUA,
    q: Any = "",
    layout: Optional[dict] = None,
    page: int = 1,
    page_size: int = 25,
    include_all: bool = False,
) -> dict:
    if is_sales_pwa_role(user):
        raise PurchaseReportError(403, "Laporan ERP tidak tersedia untuk peran ini.")
    spec = next((row for row in _CATALOG if row["id"] == report_id), None)
    if spec is None or not spec["implemented"]:
        raise PurchaseReportError(404, "Laporan pembelian ini tidak tersedia.")
    start = parse_report_date(date_from, "Dari")
    end = parse_report_date(date_to, "s/d")
    if end < start:
        raise PurchaseReportError(400, "Tanggal s/d tidak boleh lebih kecil dari tanggal Dari.")
    user_doc = await _load_user_doc(db, user)
    options = user_branch_options(user, user_doc)
    chosen = assert_branch_allowed(_text(branch), options)
    columns = _selected_columns(layout, report_id)
    ctx = await _purchase_context(db)
    invoices, meta = _prepared_invoices(ctx, start, end, chosen, q=_text(q))
    company = await _company_name(db)
    branch_label = next(row["label"] for row in BRANCH_OPTIONS if row["id"] == chosen)

    recon_meta: dict = {}
    if report_id == REPORT_INVOICE:
        display_source = invoices
        display_rows = [_project_invoice(row, columns) for row in display_source]
        totals = _totals_payload_invoice(display_source, columns)
        grouped = False
    elif report_id == REPORT_ITEM:
        display_source, recon_meta = _item_rows(invoices)
        display_rows = [_project_item(row, columns) for row in display_source]
        totals = _totals_payload_item(display_source, columns)
        grouped = True
    elif report_id == REPORT_SUPPLIER:
        display_source, recon_meta = _supplier_rows(invoices)
        display_rows = [_project_supplier(row, columns) for row in display_source]
        totals = _totals_payload_supplier(display_source, columns)
        grouped = True
    else:
        raise PurchaseReportError(404, "Laporan pembelian ini tidak tersedia.")

    data_count = len(display_source)
    if include_all:
        paged = display_rows
        page_meta = {
            "page": 1,
            "page_size": max(data_count, 1),
            "filtered_count": data_count,
            "page_count": 1,
        }
    else:
        paged, page_meta = _page(display_rows, page, page_size)
        page_meta["filtered_count"] = data_count

    methodology = {
        "source": "purchase_invoices cancelled != true",
        "date": "invoice_date inklusif",
        "returns": "bruto faktur; purchase_returns tidak dinet",
        "tax": "hanya field pajak eksplisit",
        "branch": chosen,
        "report_id": report_id,
    }
    if report_id == REPORT_ITEM:
        methodology["item_identity"] = "purchase_common.item_identity (accurate_item_id → master_item_id → weak per-line)"
        methodology["nilai"] = "SUM(line_subtotal); bukan grand_total"
        methodology["qty_pcs"] = "hanya bila unit transaksi historis PCS"
    if report_id == REPORT_SUPPLIER:
        methodology["supplier_identity"] = "accounts_payable.supplier_identity_key (name-based)"
        methodology["nilai"] = "subtotal / PPN eksplisit / grand_total terpisah"

    gap = None
    gap_detail = None
    if report_id == REPORT_INVOICE:
        gap_parts = []
        if invoices and any(row.get("tax") is None for row in invoices) and any(col["key"] == "pajak" for col in columns):
            gap_parts.append(
                "Pajak/PPN dikosongkan bila tidak tersimpan eksplisit pada faktur. Nilai nol tidak dikarang."
            )
        if any(row.get("early_invoice") for row in invoices):
            gap_parts.append(
                "Faktur yang mendahului penerimaan barang tetap ditampilkan dengan indikator informatif."
            )
        gap = " ".join(gap_parts) if gap_parts else None
    elif report_id == REPORT_ITEM:
        # Compact UX: short banner (≤2 lines) + full disclosure in gap_detail.
        short_bits = ["Nilai = faktur aktif bruto (sebelum retur)."]
        detail_bits = [
            "Sumber: purchase_invoices aktif (cancelled != true), filter invoice_date.",
            "Retur pembelian tidak mengurangi nilai laporan (bruto faktur).",
            "Qty PCS hanya bila satuan transaksi historis PCS; faktor konversi master terkini tidak dipakai.",
            "Nilai barang = SUM(line_subtotal); bukan grand_total; PPN tidak dialokasi ke baris tanpa snapshot pajak.",
            f"Identitas: {methodology['item_identity']}.",
        ]
        if any(row.get("qty_incomplete") for row in display_source):
            short_bits.append("Sebagian qty belum dapat disatukan (satuan historis berbeda).")
            detail_bits.append(
                "Qty belum dapat disatukan karena satuan historis berbeda pada satu kelompok barang."
            )
        recon_status = recon_meta.get("reconciliation_status")
        if recon_status == "INCOMPLETE":
            short_bits.append(f"Rekonsiliasi Wave 1: {recon_status}.")
            detail_bits.append(
                f"Rekonsiliasi subtotal Wave 1: {recon_status} "
                f"(terbukti {recon_meta.get('item_nilai_proven')}; "
                f"selisih/unreconciled {recon_meta.get('unreconciled_amount')})."
            )
        elif recon_status:
            detail_bits.append(f"Rekonsiliasi subtotal Wave 1: {recon_status}.")
        if any(not row.get("identity_verified") for row in display_source):
            detail_bits.append(
                "Baris tanpa accurate_item_id/master_item_id memakai identitas lemah weak:{invoice}:{line}; "
                "tidak digabung hanya karena nama sama."
            )
        gap = " ".join(short_bits[:2]) if len(short_bits) <= 2 else f"{short_bits[0]} {short_bits[1]}"
        # Keep INCOMPLETE visible even if more than two short bits.
        if recon_status == "INCOMPLETE" and "INCOMPLETE" not in gap:
            gap = f"{short_bits[0]} Rekonsiliasi Wave 1: INCOMPLETE."
        gap_detail = " ".join(detail_bits)
    elif report_id == REPORT_SUPPLIER:
        short_bits = [
            "Pengelompokan supplier sementara berdasarkan nama faktur (bukan ID permanen).",
        ]
        detail_bits = [
            "Sumber: purchase_invoices aktif (cancelled != true), filter invoice_date.",
            "Retur pembelian tidak mengurangi nilai laporan (bruto faktur).",
            "Pengelompokan: accounts_payable.supplier_identity_key (name-based). "
            "Rename/ejaan berbeda dapat memisahkan kelompok; nama ter-normalisasi sama dapat tergabung.",
            "Subtotal, PPN, dan grand total dihitung terpisah; PPN hanya dari field pajak eksplisit.",
        ]
        if any(row.get("tax") is None for row in display_source):
            short_bits.append("PPN total tidak lengkap bila ada faktur tanpa pajak eksplisit.")
            detail_bits.append(
                "PPN dikosongkan pada total bila ada faktur tanpa pajak eksplisit. Subtotal dan grand total tetap terpisah."
            )
        gap = " ".join(short_bits[:2])
        gap_detail = " ".join(detail_bits)

    return {
        "report_id": report_id,
        "name": spec["name"],
        "description": spec["description"],
        "reference": spec["reference"],
        "company_name": company,
        "period_label": f"Dari {format_period_date(start)} s/d {format_period_date(end)}",
        "branch_label": f"Cabang : {branch_label}",
        "date_from": start,
        "date_to": end,
        "branch": chosen,
        "columns": [{"key": col["key"], "label": col["label"], "align": col["align"]} for col in columns],
        "rows": paged,
        "totals": totals,
        "totals_scope": "seluruh hasil filter, bukan halaman ini saja",
        "grouped": grouped,
        "gap": gap,
        "gap_detail": gap_detail,
        "methodology": methodology,
        "source_meta": {**meta, **recon_meta},
        **page_meta,
    }


def export_workbook(report: dict) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = (report.get("name") or "Laporan")[:31]
    sheet["A1"] = report.get("company_name") or ""
    sheet["A2"] = report.get("name") or ""
    sheet["A3"] = report.get("period_label") or ""
    sheet["A4"] = report.get("branch_label") or ""
    headers = [col["label"] for col in report.get("columns") or []]
    keys = [col["key"] for col in report.get("columns") or []]
    header_row = 6
    for index, label in enumerate(headers, start=1):
        cell = sheet.cell(header_row, index, label)
        cell.font = Font(bold=True)
    row_index = header_row + 1
    moneyish = {"subtotal", "pajak", "total", "nilai_pembelian"}
    for row in report.get("rows") or []:
        values = row.get("values") or {}
        for index, key in enumerate(keys, start=1):
            raw = values.get(key)
            shown = _display_number(raw) if key in moneyish else raw
            cell = sheet.cell(row_index, index, "" if shown is None else shown)
            if key in moneyish and shown not in (None, ""):
                cell.alignment = Alignment(horizontal="right")
        row_index += 1
    totals = report.get("totals") or {}
    if any(key in totals for key in keys):
        sheet.cell(row_index, 1, "Total")
        sheet.cell(row_index, 1).font = Font(bold=True)
        for index, key in enumerate(keys, start=1):
            if key in totals and totals.get(key) is not None:
                shown = _display_number(totals.get(key)) if key in moneyish else totals.get(key)
                cell = sheet.cell(row_index, index, shown)
                cell.font = Font(bold=True)
                if key in moneyish:
                    cell.alignment = Alignment(horizontal="right")
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def list_memorized(db, user: Optional[dict]) -> List[dict]:
    if is_sales_pwa_role(user):
        raise PurchaseReportError(403, "Laporan ERP tidak tersedia untuk peran ini.")
    email = _text((user or {}).get("email")) or _text((user or {}).get("id"))
    rows = await _iter_docs(db, MEMORIZE_COLLECTION, {"owner": email})
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return [
        {
            "id": row.get("id"),
            "name": row.get("name"),
            "description": row.get("description") or "",
            "report_id": row.get("report_id"),
            "layout": row.get("layout") or {},
            "created_at": row.get("created_at"),
        }
        for row in rows
    ]


async def save_memorized(db, user: Optional[dict], payload: dict) -> dict:
    if is_sales_pwa_role(user):
        raise PurchaseReportError(403, "Laporan ERP tidak tersedia untuk peran ini.")
    report_id = _text(payload.get("report_id"))
    if report_id not in IMPLEMENTED_IDS:
        raise PurchaseReportError(400, "Laporan tidak dikenal.")
    name = _text(payload.get("name"))
    if not name:
        raise PurchaseReportError(400, "Nama tampilan wajib diisi.")
    email = _text((user or {}).get("email")) or _text((user or {}).get("id"))
    doc = {
        "id": str(uuid.uuid4()),
        "owner": email,
        "report_id": report_id,
        "name": name,
        "description": _text(payload.get("description")),
        "layout": payload.get("layout") if isinstance(payload.get("layout"), dict) else {},
        "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    await db[MEMORIZE_COLLECTION].insert_one(dict(doc))
    return doc


async def delete_memorized(db, user: Optional[dict], memorize_id: str) -> dict:
    if is_sales_pwa_role(user):
        raise PurchaseReportError(403, "Laporan ERP tidak tersedia untuk peran ini.")
    email = _text((user or {}).get("email")) or _text((user or {}).get("id"))
    result = await db[MEMORIZE_COLLECTION].delete_one({"id": _text(memorize_id), "owner": email})
    if result.deleted_count != 1:
        raise PurchaseReportError(404, "Tampilan tersimpan tidak ditemukan.")
    return {"ok": True}
