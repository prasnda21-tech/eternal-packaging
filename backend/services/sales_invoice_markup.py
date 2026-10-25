"""Faktur Markup — reseller presentation documents.

Separate from sales_invoices. Never posts journals, receipts, stock, or AR.
"""
from __future__ import annotations

import copy
import re
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.sales_invoices import (
    DOCUMENT_MARKUP,
    _actor_public,
    _money2,
    _require_explicit_price,
    ensure_other_charge_fields,
    generate_sales_invoice_pdf_bytes,
    get_sales_invoice_detail,
    invoice_pdf_presentation,
    now_iso,
    other_charges_total,
)

COLLECTION = "sales_invoice_markup_documents"
COUNTER_COLLECTION = "sales_invoice_markup_counters"

PERM_VIEW = "sales.invoice_markup.view"
PERM_CREATE = "sales.invoice_markup.create"

_MAX_BANK_NAME = 80
_MAX_ACCOUNT_NUMBER = 40
_MAX_ACCOUNT_HOLDER = 120
_ACCOUNT_NUMBER_RE = re.compile(r"^[0-9A-Za-z\-\s./]+$")
_REVISION_RETRIES = 8

_INDEXES_READY = False


async def ensure_markup_indexes(db) -> None:
    """Additive unique indexes. Safe to call repeatedly."""
    global _INDEXES_READY
    if _INDEXES_READY:
        return
    await db[COLLECTION].create_index("id", unique=True)
    await db[COLLECTION].create_index(
        [("original_invoice_id", 1), ("revision", 1)],
        unique=True,
        name="uniq_invoice_markup_revision",
    )
    await db[COLLECTION].create_index(
        [("original_invoice_id", 1), ("created_at", -1)],
        name="invoice_markup_history",
    )
    await db[COUNTER_COLLECTION].create_index("original_invoice_id", unique=True)
    _INDEXES_READY = True


def validate_markup_bank_fields(
    *,
    bank_name: Any,
    account_number: Any,
    account_holder: Any,
) -> Dict[str, str]:
    name = str(bank_name or "").strip()
    number = str(account_number if account_number is not None else "").strip()
    holder = str(account_holder or "").strip()
    if not name:
        raise HTTPException(400, "Nama bank wajib diisi.")
    if not number:
        raise HTTPException(400, "Nomor rekening wajib diisi.")
    if not holder:
        raise HTTPException(400, "Nama pemilik rekening wajib diisi.")
    if len(name) > _MAX_BANK_NAME:
        raise HTTPException(400, f"Nama bank maksimal {_MAX_BANK_NAME} karakter.")
    if len(number) > _MAX_ACCOUNT_NUMBER:
        raise HTTPException(400, f"Nomor rekening maksimal {_MAX_ACCOUNT_NUMBER} karakter.")
    if len(holder) > _MAX_ACCOUNT_HOLDER:
        raise HTTPException(400, f"Nama pemilik rekening maksimal {_MAX_ACCOUNT_HOLDER} karakter.")
    if not _ACCOUNT_NUMBER_RE.match(number):
        raise HTTPException(
            400,
            "Nomor rekening hanya boleh berisi angka, huruf, spasi, titik, garis miring, atau tanda hubung.",
        )
    return {
        "bank_name": name,
        "account_number": number,
        "account_holder": holder,
    }


def _line_key(line: dict) -> Tuple[str, int]:
    line_id = str(line.get("line_id") or "").strip()
    try:
        line_idx = int(line.get("line_idx", -1))
    except (TypeError, ValueError):
        line_idx = -1
    return line_id, line_idx


def _match_price_row(price_rows: List[dict], line: dict) -> Optional[dict]:
    line_id, line_idx = _line_key(line)
    for row in price_rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("line_id") or "").strip()
        try:
            ridx = int(row.get("line_idx")) if row.get("line_idx") is not None else None
        except (TypeError, ValueError):
            ridx = None
        if line_id and rid and rid == line_id:
            return row
        if ridx is not None and ridx == line_idx:
            return row
    return None


def compute_markup_totals(invoice: dict, price_rows: List[dict]) -> Dict[str, Any]:
    """Recalculate presentation totals from markup unit prices.

    Proven source semantics (sales_invoices.create):
    - discount_amount is nominal (not a stored percent).
    - ppn_amount = after_discount × ppn_percent / 100.
    - other_charges amounts are nominal.
    """
    inv = ensure_other_charge_fields(dict(invoice or {}))
    source_lines = inv.get("lines") or []
    if not isinstance(source_lines, list) or not source_lines:
        raise HTTPException(400, "Faktur tidak memiliki baris untuk markup.")

    if not isinstance(price_rows, list) or not price_rows:
        raise HTTPException(400, "Harga markup per baris wajib dikirim.")

    seen_keys = set()
    markup_lines: List[dict] = []
    subtotal = Decimal("0.00")

    for line in source_lines:
        if not isinstance(line, dict):
            continue
        key = _line_key(line)
        if key in seen_keys:
            raise HTTPException(400, "Baris faktur duplikat pada sumber.")
        seen_keys.add(key)

        matched = _match_price_row(price_rows, line)
        if matched is None:
            label = str(line.get("item_name") or line.get("line_id") or line.get("line_idx") or "?")
            raise HTTPException(400, f"Harga markup untuk baris '{label}' wajib diisi.")

        qty = int(line.get("quantity") or 0)
        if qty <= 0:
            raise HTTPException(400, "Qty baris faktur tidak valid untuk markup.")

        unit_price = _require_explicit_price(
            matched.get("markup_unit_price", matched.get("unit_price")),
            "Harga jual markup",
        )
        # Reject non-finite via Decimal path already; normalize money2.
        unit_dec = _money2(unit_price)
        line_sub = (unit_dec * Decimal(qty)).quantize(Decimal("0.01"))
        subtotal += line_sub

        markup_lines.append({
            "line_id": str(line.get("line_id") or ""),
            "line_idx": int(line.get("line_idx", 0) if line.get("line_idx") is not None else 0),
            "item_code": str(line.get("item_code") or ""),
            "item_name": str(line.get("item_name") or line.get("name") or ""),
            "color": str(line.get("color") or ""),
            "unit": str(line.get("unit") or "pcs"),
            "quantity": qty,
            "original_unit_price": float(_money2(line.get("unit_price"))),
            "markup_unit_price": float(unit_dec),
            "markup_line_subtotal": float(line_sub),
            "unit_price": float(unit_dec),
            "line_subtotal": float(line_sub),
            "line_total": float(line_sub),
        })

    expected_count = len([l for l in source_lines if isinstance(l, dict)])
    if len(markup_lines) != expected_count:
        raise HTTPException(400, "Jumlah baris harga markup harus sama dengan baris faktur.")

    matched_keys = {_line_key(ml) for ml in markup_lines}
    for row in price_rows:
        if not isinstance(row, dict):
            raise HTTPException(400, "Baris harga markup tidak valid.")
        row_key = _line_key(row)
        # Accept match by line_id or line_idx against computed lines.
        if row_key in matched_keys:
            continue
        if _match_price_row(markup_lines, row) is not None:
            continue
        raise HTTPException(400, "Terdapat baris harga markup yang tidak cocok dengan faktur.")

    discount_amount = _money2(inv.get("discount_amount"))
    if discount_amount > subtotal:
        raise HTTPException(
            400,
            (
                f"Diskon nominal faktur asli (Rp {float(discount_amount):,.2f}) melebihi "
                f"subtotal markup (Rp {float(subtotal):,.2f}). Naikkan harga markup atau "
                "buat markup dari faktur dengan diskon yang sesuai."
            ),
        )

    after_discount = (subtotal - discount_amount).quantize(Decimal("0.01"))
    ppn_percent = _money2(inv.get("ppn_percent"))
    ppn_amount = (after_discount * (ppn_percent / Decimal("100"))).quantize(Decimal("0.01"))

    charges = []
    for charge in inv.get("other_charges") or []:
        if not isinstance(charge, dict):
            continue
        charges.append({
            "id": str(charge.get("id") or ""),
            "account_id": str(charge.get("account_id") or ""),
            "account_code": str(charge.get("account_code") or ""),
            "account_name": str(charge.get("account_name") or ""),
            "account_type": str(charge.get("account_type") or ""),
            "description": str(charge.get("description") or ""),
            "amount": float(_money2(charge.get("amount"))),
        })
    charges_total = other_charges_total({"other_charges": charges})
    grand_total = (after_discount + ppn_amount + charges_total).quantize(Decimal("0.01"))

    return {
        "lines": markup_lines,
        "subtotal": float(subtotal),
        "discount_amount": float(discount_amount),
        "after_discount": float(after_discount),
        "ppn_percent": float(ppn_percent),
        "ppn_amount": float(ppn_amount),
        "other_charges": charges,
        "other_charges_total": float(charges_total),
        "grand_total": float(grand_total),
    }


def build_print_snapshot(
    invoice: dict,
    totals: dict,
    bank: dict,
) -> Dict[str, Any]:
    """Invoice-shaped payload for PDF. Omits payment settlement fields."""
    snap = {
        "id": invoice.get("id"),
        "invoice_number": invoice.get("invoice_number"),
        "invoice_date": invoice.get("invoice_date"),
        "due_date": invoice.get("due_date"),
        "customer": invoice.get("customer"),
        "outlet": invoice.get("outlet"),
        "recipient": invoice.get("recipient"),
        "address": invoice.get("address"),
        "delivery_zone": invoice.get("delivery_zone"),
        "sales": invoice.get("sales"),
        "po_id": invoice.get("po_id"),
        "po_number": invoice.get("po_number"),
        "branch_id": invoice.get("branch_id"),
        "notes": invoice.get("notes") or "",
        "presentation_expedition": invoice.get("presentation_expedition"),
        "expedition": invoice.get("expedition"),
        "presentation_shipping_type": invoice.get("presentation_shipping_type"),
        "shipping_type": invoice.get("shipping_type"),
        "lines": totals["lines"],
        "subtotal": totals["subtotal"],
        "discount_amount": totals["discount_amount"],
        "after_discount": totals["after_discount"],
        "ppn_percent": totals["ppn_percent"],
        "ppn_amount": totals["ppn_amount"],
        "other_charges": totals["other_charges"],
        "other_charges_total": totals["other_charges_total"],
        "grand_total": totals["grand_total"],
        # Explicitly zero / omit payment presentation for markup PDF.
        "paid_total": 0,
        "outstanding_amount": None,
        "payment_status": None,
        "markup_bank": dict(bank),
    }
    return snap


def design_snapshot_for_storage(design: dict) -> Dict[str, Any]:
    """Store resolved design without binary _pdf_assets for deterministic reprint text."""
    raw = copy.deepcopy(design or {})
    raw.pop("_pdf_assets", None)
    return raw


def apply_markup_design_and_bank(design: Optional[dict], bank: dict) -> Dict[str, Any]:
    """Use invoice_markup section when present; force manual bank as sole account."""
    from services.print_design_settings import resolve_print_design

    resolved = resolve_print_design(design)
    out = copy.deepcopy(resolved)
    markup_section = out.get("invoice_markup")
    if isinstance(markup_section, dict) and markup_section:
        inv = dict(out.get("invoice") or {})
        for key, value in markup_section.items():
            if key in ("prepared_signature_image", "approved_signature_image"):
                if value is not None:
                    inv[key] = value
                continue
            inv[key] = value
        out["invoice"] = inv
    inv = dict(out.get("invoice") or {})
    inv["bank_accounts"] = [{
        "bank_label": bank["bank_name"],
        "account_number": bank["account_number"],
        "account_name": bank["account_holder"],
    }]
    out["invoice"] = inv
    return out


async def _allocate_revision(db, original_invoice_id: str) -> int:
    from pymongo import ReturnDocument

    counter = await db[COUNTER_COLLECTION].find_one_and_update(
        {"original_invoice_id": original_invoice_id},
        {"$inc": {"next_revision": 1}, "$setOnInsert": {"original_invoice_id": original_invoice_id}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if not counter:
        counter = await db[COUNTER_COLLECTION].find_one({"original_invoice_id": original_invoice_id})
    return int((counter or {}).get("next_revision") or 1)


def public_markup_doc(doc: dict) -> Dict[str, Any]:
    if not doc:
        return {}
    out = {k: v for k, v in doc.items() if k != "_id"}
    return out


async def list_markup_documents(db, invoice_id: str) -> Dict[str, Any]:
    await ensure_markup_indexes(db)
    rows = await db[COLLECTION].find(
        {"original_invoice_id": str(invoice_id)},
        {"_id": 0},
    ).sort([("revision", -1)]).to_list(500)
    return {"items": rows, "count": len(rows)}


async def get_markup_document(db, invoice_id: str, markup_id: str) -> Dict[str, Any]:
    await ensure_markup_indexes(db)
    doc = await db[COLLECTION].find_one(
        {"id": str(markup_id), "original_invoice_id": str(invoice_id)},
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(404, "Dokumen Faktur Markup tidak ditemukan.")
    return doc


async def create_markup_document(
    db,
    invoice_id: str,
    payload: Dict[str, Any],
    user: dict,
) -> Dict[str, Any]:
    """Insert a new immutable markup revision. Does not mutate sales_invoices."""
    await ensure_markup_indexes(db)
    invoice = await get_sales_invoice_detail(db, invoice_id)
    if invoice.get("cancelled") is True or str(invoice.get("financial_status") or "").lower() == "cancelled":
        raise HTTPException(409, "Faktur dibatalkan. Faktur Markup tidak tersedia.")

    bank = validate_markup_bank_fields(
        bank_name=(payload or {}).get("bank_name"),
        account_number=(payload or {}).get("account_number"),
        account_holder=(payload or {}).get("account_holder"),
    )
    price_rows = (payload or {}).get("lines") or (payload or {}).get("prices") or []
    totals = compute_markup_totals(invoice, price_rows)

    shown = await invoice_pdf_presentation(db, invoice)
    print_snapshot = build_print_snapshot(shown, totals, bank)

    from services.print_design_settings import get_print_design_for_pdf

    design = await get_print_design_for_pdf(db, branch_id=invoice.get("branch_id"))
    design_for_print = apply_markup_design_and_bank(design, bank)
    design_snap = design_snapshot_for_storage(design_for_print)

    idempotency_key = str((payload or {}).get("idempotency_key") or "").strip()
    if idempotency_key:
        existing = await db[COLLECTION].find_one(
            {"original_invoice_id": invoice["id"], "idempotency_key": idempotency_key},
            {"_id": 0},
        )
        if existing:
            return existing

    last_err = None
    for _ in range(_REVISION_RETRIES):
        revision = await _allocate_revision(db, invoice["id"])
        doc = {
            "id": str(uuid.uuid4()),
            "original_invoice_id": invoice["id"],
            "original_invoice_number": invoice.get("invoice_number") or "",
            "branch_id": invoice.get("branch_id"),
            "po_id": invoice.get("po_id"),
            "revision": revision,
            "created_by": _actor_public(user),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "lines": totals["lines"],
            "markup_subtotal": totals["subtotal"],
            "discount_amount": totals["discount_amount"],
            "after_discount": totals["after_discount"],
            "ppn_percent": totals["ppn_percent"],
            "ppn_amount": totals["ppn_amount"],
            "other_charges": totals["other_charges"],
            "other_charges_total": totals["other_charges_total"],
            "markup_grand_total": totals["grand_total"],
            "bank_name": bank["bank_name"],
            "account_number": bank["account_number"],
            "account_holder": bank["account_holder"],
            "print_snapshot": print_snapshot,
            "print_design_snapshot": design_snap,
            "idempotency_key": idempotency_key or None,
        }
        try:
            await db[COLLECTION].insert_one(doc)
            doc.pop("_id", None)
            return doc
        except DuplicateKeyError as exc:
            last_err = exc
            continue
    raise HTTPException(409, "Gagal mengalokasikan revisi Faktur Markup. Coba lagi.") from last_err


async def render_markup_pdf_bytes(db, markup_doc: dict) -> bytes:
    """Reprint from stored snapshots only."""
    snap = markup_doc.get("print_snapshot")
    if not isinstance(snap, dict):
        raise HTTPException(500, "Snapshot cetakan markup tidak valid.")
    bank = {
        "bank_name": markup_doc.get("bank_name") or (snap.get("markup_bank") or {}).get("bank_name"),
        "account_number": markup_doc.get("account_number")
        or (snap.get("markup_bank") or {}).get("account_number"),
        "account_holder": markup_doc.get("account_holder")
        or (snap.get("markup_bank") or {}).get("account_holder"),
    }
    bank = validate_markup_bank_fields(**bank)

    design_snap = markup_doc.get("print_design_snapshot")
    if isinstance(design_snap, dict) and design_snap:
        from services.print_design_settings import prefetch_layered_assets, resolve_print_design

        design = resolve_print_design(design_snap)
        # Re-attach live asset bytes for logo/signatures referenced in snapshot.
        global_design = design
        design["_pdf_assets"] = await prefetch_layered_assets(global_design, None)
    else:
        from services.print_design_settings import get_print_design_for_pdf

        design = await get_print_design_for_pdf(db, branch_id=markup_doc.get("branch_id"))

    design = apply_markup_design_and_bank(design, bank)
    # Re-bind signature/logo bytes to refs after markup section overlay.
    from services.print_design_settings import prefetch_pdf_assets

    design["_pdf_assets"] = await prefetch_pdf_assets(db, design)
    return generate_sales_invoice_pdf_bytes(
        snap,
        document_type=DOCUMENT_MARKUP,
        design=design,
    )
