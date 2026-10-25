"""Design Cetakan settings (app_settings key=design_cetakan).

Presentation-only letterhead for Eternal sales PDFs.
Defaults MUST match the deployed hardcoded PDF constants so an empty
settings document leaves PDF output unchanged.

Phase 3 adds optional object-storage asset refs (logo + Faktur signatures).
Binary is never stored in app_settings.
"""
from __future__ import annotations

import copy
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from services import pdf_layout as layout
from services import storage as obj_storage

logger = logging.getLogger(__name__)

SETTINGS_KEY = "design_cetakan"
BRANCH_KEY_PREFIX = "design_cetakan_branch:"
PERM_VIEW = "settings.design_cetakan.view"
PERM_MANAGE = "settings.design_cetakan.manage"
FILE_KIND = "print_design"

# Sparse branch overrides. Empty/malformed values inherit global and are not stored.
BRANCH_INHERITANCE_FIELDS = (
    "company_name",
    "address_lines",
    "company_logo",
    "bank_accounts",
    "prepared_label",
    "prepared_footer",
    "approved_label",
    "approved_footer",
    "prepared_signature_image",
    "approved_signature_image",
)

SLOT_COMPANY_LOGO = "company_logo"
SLOT_PREPARED = "prepared_signature_image"
SLOT_APPROVED = "approved_signature_image"
# Markup-isolated signature assets (do not mutate invoice/proforma slots).
SLOT_MARKUP_PREPARED = "markup_prepared_signature_image"
SLOT_MARKUP_APPROVED = "markup_approved_signature_image"
ASSET_SLOTS = frozenset({
    SLOT_COMPANY_LOGO,
    SLOT_PREPARED,
    SLOT_APPROVED,
    SLOT_MARKUP_PREPARED,
    SLOT_MARKUP_APPROVED,
})

MAX_LOGO_BYTES = 2_000_000
MAX_SIGNATURE_BYTES = 1_500_000
MIN_IMAGE_BYTES = 64

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_TEXT = 200
_MAX_LINE = 240
_MAX_ADDRESS_LINES = 6
_MAX_ESTIMASI_LINES = 8
_MAX_BANKS = 4

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_bank_accounts() -> List[Dict[str, str]]:
    return [
        {
            "bank_label": "BRI",
            "account_number": "024701002548569",
            "account_name": "DIMAS AJI PRASETYA",
        },
        {
            "bank_label": "BCA",
            "account_number": "8467072051",
            "account_name": "SISWADI",
        },
    ]


def default_settings() -> Dict[str, Any]:
    return {
        "key": SETTINGS_KEY,
        "company_name": layout.COMPANY_NAME,
        "address_lines": list(layout.COMPANY_ADDRESS_LINES),
        "accent_color": layout.ACCENT_COLOR_HEX,
        "company_logo": None,
        "po": {
            "signature_labels": ["Operator", "Pengiriman", "Penerima"],
            "footer_name_label": "Nama",
            # Phase 3 generic PO sections — empty means legacy path.
            "header_elements": [],
            "footer_elements": [],
        },
        "invoice": {
            "bank_accounts": default_bank_accounts(),
            "estimasi_title": layout.ESTIMASI_TITLE,
            "estimasi_lines": list(layout.ESTIMASI_LINES),
            "prepared_label": "Disiapkan Oleh",
            "prepared_footer": "Admin",
            "approved_label": "Disetujui Oleh",
            "approved_footer": "Pengelola",
            "prepared_signature_image": None,
            "approved_signature_image": None,
            # Phase 1 generic header — empty means V1 legacy path.
            "header_elements": [],
            # Phase 2 generic footer — empty means V1 legacy footer path.
            "footer_elements": [],
        },
        # Separate Design Cetakan type for Faktur Markup (reseller). Defaults mirror invoice.
        "invoice_markup": {
            "bank_accounts": default_bank_accounts(),
            "estimasi_title": layout.ESTIMASI_TITLE,
            "estimasi_lines": list(layout.ESTIMASI_LINES),
            "prepared_label": "Disiapkan Oleh",
            "prepared_footer": "Admin",
            "approved_label": "Disetujui Oleh",
            "approved_footer": "Pengelola",
            "prepared_signature_image": None,
            "approved_signature_image": None,
            "header_elements": [],
            "footer_elements": [],
        },
        "schema_version": 1,
        "surat_jalan": {
            "signature_labels": ["Gudang", "Cek Barang", "Kurir", "Penerima"],
            "date_label": "Tgl.",
            # Phase 3 generic SJ sections — empty means legacy path.
            "header_elements": [],
            "footer_elements": [],
        },
        "updated_at": None,
        "updated_by": None,
    }


def bank_display_lines(accounts: Optional[List[dict]]) -> List[str]:
    """Format structured bank rows into the PDF letterhead lines."""
    lines: List[str] = []
    for row in accounts or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("bank_label") or "").strip()
        number = str(row.get("account_number") or "").strip()
        name = str(row.get("account_name") or "").strip()
        if label or number:
            lines.append(f"{label} : {number}")
        if name:
            lines.append(f"A.N : {name}")
    return lines


def _clip(value: Any, limit: int = _MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def _normalize_hex(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if not _HEX_RE.match(raw):
        return None
    body = raw[1:]
    if len(body) == 3:
        body = "".join(ch * 2 for ch in body)
    return f"#{body.lower()}"


def _string_list(raw: Any, *, fallback: List[str], max_items: int, max_len: int = _MAX_LINE) -> List[str]:
    if not isinstance(raw, (list, tuple)):
        return list(fallback)
    out: List[str] = []
    for item in raw:
        text = _clip(item, max_len)
        if text:
            out.append(text)
        if len(out) >= max_items:
            break
    return out or list(fallback)


def _labels(raw: Any, fallback: List[str]) -> List[str]:
    if not isinstance(raw, (list, tuple)):
        return list(fallback)
    out: List[str] = []
    for idx, fallback_label in enumerate(fallback):
        if idx < len(raw):
            text = _clip(raw[idx], _MAX_TEXT)
            out.append(text or fallback_label)
        else:
            out.append(fallback_label)
    return out


def _bank_accounts(raw: Any, fallback: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        return copy.deepcopy(fallback)
    out: List[Dict[str, str]] = []
    for row in raw[:_MAX_BANKS]:
        if not isinstance(row, dict):
            continue
        label = _clip(row.get("bank_label"))
        number = _clip(row.get("account_number"), 64)
        name = _clip(row.get("account_name"))
        if not (label or number or name):
            continue
        out.append({
            "bank_label": label or "Bank",
            "account_number": number,
            "account_name": name,
        })
    return out or copy.deepcopy(fallback)


def _sanitize_asset_ref(raw: Any) -> Optional[Dict[str, Any]]:
    """Keep only a complete server-owned asset reference. Malformed → None."""
    if not isinstance(raw, dict):
        return None
    file_id = _clip(raw.get("file_id"), 80)
    storage_path = _clip(raw.get("storage_path"), 500)
    content_type = _clip(raw.get("content_type"), 80).lower()
    if not file_id or not storage_path:
        return None
    if not storage_path.startswith(f"{obj_storage.APP_NAME}/print-design/"):
        return None
    if ".." in storage_path or storage_path.startswith("/"):
        return None
    try:
        size = int(raw.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return {
        "file_id": file_id,
        "storage_path": storage_path,
        "content_type": content_type or "application/octet-stream",
        "size": max(size, 0),
        "original_filename": _clip(raw.get("original_filename"), 180),
        "updated_at": raw.get("updated_at"),
    }


def _public_asset(ref: Optional[dict]) -> Dict[str, Any]:
    clean = _sanitize_asset_ref(ref)
    if not clean:
        return {"has_custom": False}
    return {
        "has_custom": True,
        "filename": clean.get("original_filename") or "asset",
        "content_type": clean.get("content_type"),
        "updated_at": clean.get("updated_at"),
    }


def _sanitize_header_elements(
    raw: Any,
    *,
    strict: bool,
    document: str = "invoice",
) -> List[Dict[str, Any]]:
    """Normalize *.header_elements. strict=True raises HTTP 400 on invalid input."""
    from services.print_field_registry import (
        ALLOWED_ALIGNS,
        ALLOWED_ELEMENT_TYPES,
        ELEMENT_TYPE_FIELD,
        ELEMENT_TYPE_STATIC,
        FIELD_SOURCES,
        MAX_ELEMENT_ID,
        MAX_ELEMENT_LABEL,
        MAX_HEADER_ELEMENTS,
        MAX_STATIC_TEXT,
        SOURCE_STATIC_TEXT,
        is_header_source,
        is_known_source,
        source_allowed_for_document,
    )

    doc = str(document or "invoice").strip() or "invoice"
    if raw is None:
        return []
    if not isinstance(raw, list):
        if strict:
            raise HTTPException(400, "header_elements harus berupa array")
        return []
    if len(raw) > MAX_HEADER_ELEMENTS:
        if strict:
            raise HTTPException(400, f"Maksimal {MAX_HEADER_ELEMENTS} elemen header")
        raw = raw[:MAX_HEADER_ELEMENTS]

    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(raw):
        if not isinstance(row, dict):
            if strict:
                raise HTTPException(400, f"Elemen header #{idx + 1} tidak valid")
            continue
        el_type = str(row.get("type") or "").strip()
        if el_type not in ALLOWED_ELEMENT_TYPES:
            if strict:
                raise HTTPException(400, f"Tipe elemen header tidak dikenali: {el_type or '-'}")
            continue
        source = str(row.get("source") or "").strip()
        if el_type == ELEMENT_TYPE_STATIC:
            source = SOURCE_STATIC_TEXT
        elif (
            source not in FIELD_SOURCES
            or not is_known_source(source)
            or not is_header_source(source)
            or not source_allowed_for_document(source, doc)
        ):
            if strict:
                raise HTTPException(400, f"Sumber elemen header tidak diizinkan: {source or '-'}")
            continue
        align = str(row.get("align") or "left").strip().lower()
        if align not in ALLOWED_ALIGNS:
            if strict:
                raise HTTPException(400, f"Align elemen header tidak valid: {align}")
            align = "left"
        eid = _clip(row.get("id"), MAX_ELEMENT_ID) or f"hdr-{idx + 1}"
        label = _clip(row.get("label"), MAX_ELEMENT_LABEL)
        text = _clip(row.get("text"), MAX_STATIC_TEXT) if el_type == ELEMENT_TYPE_STATIC or source == SOURCE_STATIC_TEXT else ""
        visible = row.get("visible")
        bold = row.get("bold")
        if visible is not None and not isinstance(visible, bool):
            if strict:
                raise HTTPException(400, "visible harus boolean")
            visible = bool(visible)
        if bold is not None and not isinstance(bold, bool):
            if strict:
                raise HTTPException(400, "bold harus boolean")
            bold = bool(bold)
        if strict and text and ("<" in text or ">" in text):
            raise HTTPException(400, "Teks elemen tidak boleh berisi markup HTML")
        if el_type == ELEMENT_TYPE_FIELD and source == SOURCE_STATIC_TEXT:
            if strict:
                raise HTTPException(400, "Sumber static.text harus bertipe static_text")
            el_type = ELEMENT_TYPE_STATIC
        out.append({
            "id": eid,
            "type": el_type,
            "source": source,
            "label": label,
            "align": align,
            "bold": bool(bold) if bold is not None else False,
            "visible": True if visible is None else bool(visible),
            "text": text,
        })
    return out


def _sanitize_footer_elements(
    raw: Any,
    *,
    strict: bool,
    document: str = "invoice",
) -> List[Dict[str, Any]]:
    """Normalize *.footer_elements. strict=True raises HTTP 400 on invalid input."""
    from services.print_field_registry import (
        ALLOWED_ALIGNS,
        ALLOWED_FOOTER_ELEMENT_TYPES,
        DOCUMENT_DELIVERY,
        DOCUMENT_INVOICE,
        DOCUMENT_MARKUP,
        DOCUMENT_PO,
        DOCUMENT_PROFORMA,
        ELEMENT_TYPE_FIELD,
        ELEMENT_TYPE_SIGNATURE,
        ELEMENT_TYPE_STATIC,
        FOOTER_FIELD_SOURCES,
        MAX_ELEMENT_ID,
        MAX_ELEMENT_LABEL,
        MAX_FOOTER_ELEMENTS,
        MAX_STATIC_TEXT,
        SIGNATURE_SOURCES,
        SOURCE_STATIC_TEXT,
        is_footer_source,
        source_allowed_for_document,
    )

    doc = str(document or "invoice").strip() or "invoice"
    if raw is None:
        return []
    if not isinstance(raw, list):
        if strict:
            raise HTTPException(400, "footer_elements harus berupa array")
        return []
    if len(raw) > MAX_FOOTER_ELEMENTS:
        if strict:
            raise HTTPException(400, f"Maksimal {MAX_FOOTER_ELEMENTS} elemen footer")
        raw = raw[:MAX_FOOTER_ELEMENTS]

    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(raw):
        if not isinstance(row, dict):
            if strict:
                raise HTTPException(400, f"Elemen footer #{idx + 1} tidak valid")
            continue
        el_type = str(row.get("type") or "").strip()
        # SJ/PO do not use invoice signature element type (POD stays frozen).
        if el_type == ELEMENT_TYPE_SIGNATURE and doc not in {
            DOCUMENT_INVOICE,
            DOCUMENT_PROFORMA,
            DOCUMENT_MARKUP,
        }:
            if strict:
                raise HTTPException(400, f"Tipe signature tidak diizinkan untuk dokumen {doc}")
            continue
        if el_type not in ALLOWED_FOOTER_ELEMENT_TYPES:
            if strict:
                raise HTTPException(400, f"Tipe elemen footer tidak dikenali: {el_type or '-'}")
            continue
        source = str(row.get("source") or "").strip()
        if el_type == ELEMENT_TYPE_STATIC:
            source = SOURCE_STATIC_TEXT
        elif el_type == ELEMENT_TYPE_SIGNATURE:
            if source not in SIGNATURE_SOURCES or not is_footer_source(source) or not source_allowed_for_document(source, doc):
                if strict:
                    raise HTTPException(400, f"Sumber tanda tangan footer tidak diizinkan: {source or '-'}")
                continue
        elif (
            source not in FOOTER_FIELD_SOURCES
            or not is_footer_source(source)
            or not source_allowed_for_document(source, doc)
        ):
            if strict:
                raise HTTPException(400, f"Sumber elemen footer tidak diizinkan: {source or '-'}")
            continue
        align = str(row.get("align") or "left").strip().lower()
        if align not in ALLOWED_ALIGNS:
            if strict:
                raise HTTPException(400, f"Align elemen footer tidak valid: {align}")
            align = "left"
        eid = _clip(row.get("id"), MAX_ELEMENT_ID) or f"ftr-{idx + 1}"
        label = _clip(row.get("label"), MAX_ELEMENT_LABEL)
        text = (
            _clip(row.get("text"), MAX_STATIC_TEXT)
            if el_type == ELEMENT_TYPE_STATIC or source == SOURCE_STATIC_TEXT
            else ""
        )
        visible = row.get("visible")
        bold = row.get("bold")
        if visible is not None and not isinstance(visible, bool):
            if strict:
                raise HTTPException(400, "visible harus boolean")
            visible = bool(visible)
        if bold is not None and not isinstance(bold, bool):
            if strict:
                raise HTTPException(400, "bold harus boolean")
            bold = bool(bold)
        if strict and text and ("<" in text or ">" in text):
            raise HTTPException(400, "Teks elemen tidak boleh berisi markup HTML")
        if el_type == ELEMENT_TYPE_FIELD and source == SOURCE_STATIC_TEXT:
            if strict:
                raise HTTPException(400, "Sumber static.text harus bertipe static_text")
            el_type = ELEMENT_TYPE_STATIC
        if el_type == ELEMENT_TYPE_FIELD and source in SIGNATURE_SOURCES:
            if strict:
                raise HTTPException(400, "Sumber tanda tangan harus bertipe signature")
            el_type = ELEMENT_TYPE_SIGNATURE
        out.append({
            "id": eid,
            "type": el_type,
            "source": source,
            "label": label,
            "align": align,
            "bold": bool(bold) if bold is not None else False,
            "visible": True if visible is None else bool(visible),
            "text": text,
        })
    return out


def _section_active(elements: Optional[list]) -> bool:
    return isinstance(elements, list) and len(elements) > 0


def schema_version_for_invoice(
    header_elements: Optional[list] = None,
    footer_elements: Optional[list] = None,
    po_header_elements: Optional[list] = None,
    po_footer_elements: Optional[list] = None,
    sj_header_elements: Optional[list] = None,
    sj_footer_elements: Optional[list] = None,
) -> int:
    """v1 = no generic sections; v2 = any generic header/footer present."""
    if any(
        _section_active(x)
        for x in (
            header_elements,
            footer_elements,
            po_header_elements,
            po_footer_elements,
            sj_header_elements,
            sj_footer_elements,
        )
    ):
        return 2
    return 1


def resolve_print_design(raw: Optional[dict] = None) -> Dict[str, Any]:
    """Merge saved/partial settings with defaults. Never raises for PDF use."""
    base = default_settings()
    doc = raw if isinstance(raw, dict) else {}

    company = _clip(doc.get("company_name"), _MAX_TEXT) or base["company_name"]
    address = _string_list(
        doc.get("address_lines"),
        fallback=base["address_lines"],
        max_items=_MAX_ADDRESS_LINES,
    )
    accent = _normalize_hex(doc.get("accent_color")) or base["accent_color"]

    po_raw = doc.get("po") if isinstance(doc.get("po"), dict) else {}
    inv_raw = doc.get("invoice") if isinstance(doc.get("invoice"), dict) else {}
    markup_raw = doc.get("invoice_markup") if isinstance(doc.get("invoice_markup"), dict) else {}
    sj_raw = doc.get("surat_jalan") if isinstance(doc.get("surat_jalan"), dict) else {}
    header_elements = _sanitize_header_elements(inv_raw.get("header_elements"), strict=False, document="invoice")
    footer_elements = _sanitize_footer_elements(inv_raw.get("footer_elements"), strict=False, document="invoice")
    markup_header_elements = _sanitize_header_elements(
        markup_raw.get("header_elements"), strict=False, document="markup",
    )
    markup_footer_elements = _sanitize_footer_elements(
        markup_raw.get("footer_elements"), strict=False, document="markup",
    )
    po_header_elements = _sanitize_header_elements(po_raw.get("header_elements"), strict=False, document="po")
    po_footer_elements = _sanitize_footer_elements(po_raw.get("footer_elements"), strict=False, document="po")
    sj_header_elements = _sanitize_header_elements(sj_raw.get("header_elements"), strict=False, document="delivery")
    sj_footer_elements = _sanitize_footer_elements(sj_raw.get("footer_elements"), strict=False, document="delivery")

    po = {
        "signature_labels": _labels(po_raw.get("signature_labels"), base["po"]["signature_labels"]),
        "footer_name_label": _clip(po_raw.get("footer_name_label")) or base["po"]["footer_name_label"],
        "header_elements": po_header_elements,
        "footer_elements": po_footer_elements,
    }
    invoice = {
        "bank_accounts": _bank_accounts(inv_raw.get("bank_accounts"), base["invoice"]["bank_accounts"]),
        "estimasi_title": _clip(inv_raw.get("estimasi_title"), _MAX_LINE) or base["invoice"]["estimasi_title"],
        "estimasi_lines": _string_list(
            inv_raw.get("estimasi_lines"),
            fallback=base["invoice"]["estimasi_lines"],
            max_items=_MAX_ESTIMASI_LINES,
        ),
        "prepared_label": _clip(inv_raw.get("prepared_label")) or base["invoice"]["prepared_label"],
        "prepared_footer": _clip(inv_raw.get("prepared_footer")) or base["invoice"]["prepared_footer"],
        "approved_label": _clip(inv_raw.get("approved_label")) or base["invoice"]["approved_label"],
        "approved_footer": _clip(inv_raw.get("approved_footer")) or base["invoice"]["approved_footer"],
        "prepared_signature_image": _sanitize_asset_ref(inv_raw.get("prepared_signature_image")),
        "approved_signature_image": _sanitize_asset_ref(inv_raw.get("approved_signature_image")),
        "header_elements": header_elements,
        "footer_elements": footer_elements,
    }
    # Backward compatible: missing invoice_markup inherits invoice defaults.
    markup_base = base["invoice_markup"]
    invoice_markup = {
        "bank_accounts": _bank_accounts(
            markup_raw.get("bank_accounts") if markup_raw else None,
            invoice["bank_accounts"] if not markup_raw else markup_base["bank_accounts"],
        ),
        "estimasi_title": (
            _clip(markup_raw.get("estimasi_title"), _MAX_LINE)
            or (invoice["estimasi_title"] if not markup_raw else markup_base["estimasi_title"])
        ),
        "estimasi_lines": _string_list(
            markup_raw.get("estimasi_lines") if markup_raw else None,
            fallback=invoice["estimasi_lines"] if not markup_raw else markup_base["estimasi_lines"],
            max_items=_MAX_ESTIMASI_LINES,
        ),
        "prepared_label": (
            _clip(markup_raw.get("prepared_label"))
            or (invoice["prepared_label"] if not markup_raw else markup_base["prepared_label"])
        ),
        "prepared_footer": (
            _clip(markup_raw.get("prepared_footer"))
            or (invoice["prepared_footer"] if not markup_raw else markup_base["prepared_footer"])
        ),
        "approved_label": (
            _clip(markup_raw.get("approved_label"))
            or (invoice["approved_label"] if not markup_raw else markup_base["approved_label"])
        ),
        "approved_footer": (
            _clip(markup_raw.get("approved_footer"))
            or (invoice["approved_footer"] if not markup_raw else markup_base["approved_footer"])
        ),
        "prepared_signature_image": (
            _sanitize_asset_ref(markup_raw.get("prepared_signature_image"))
            if markup_raw
            else invoice.get("prepared_signature_image")
        ),
        "approved_signature_image": (
            _sanitize_asset_ref(markup_raw.get("approved_signature_image"))
            if markup_raw
            else invoice.get("approved_signature_image")
        ),
        "header_elements": markup_header_elements if markup_raw else list(header_elements),
        "footer_elements": markup_footer_elements if markup_raw else list(footer_elements),
    }
    surat_jalan = {
        "signature_labels": _labels(
            sj_raw.get("signature_labels"),
            base["surat_jalan"]["signature_labels"],
        ),
        "date_label": _clip(sj_raw.get("date_label"), 32) or base["surat_jalan"]["date_label"],
        "header_elements": sj_header_elements,
        "footer_elements": sj_footer_elements,
    }

    out = {
        "key": SETTINGS_KEY,
        "schema_version": schema_version_for_invoice(
            header_elements,
            footer_elements,
            po_header_elements,
            po_footer_elements,
            sj_header_elements,
            sj_footer_elements,
        ),
        "company_name": company,
        "address_lines": address,
        "accent_color": accent,
        "company_logo": _sanitize_asset_ref(doc.get("company_logo")),
        "po": po,
        "invoice": invoice,
        "invoice_markup": invoice_markup,
        "surat_jalan": surat_jalan,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }
    # Prefetched bytes from get_print_design_for_pdf — pass through for sync renderers.
    if isinstance(doc.get("_pdf_assets"), dict):
        out["_pdf_assets"] = doc["_pdf_assets"]
    return out


def public_settings(doc: Optional[dict] = None) -> Dict[str, Any]:
    """API-safe view: text fields + asset presentation without storage paths."""
    resolved = resolve_print_design(doc)
    inv = resolved.get("invoice") or {}
    return {
        "key": SETTINGS_KEY,
        "schema_version": resolved.get("schema_version") or 1,
        "company_name": resolved.get("company_name"),
        "address_lines": resolved.get("address_lines"),
        "accent_color": resolved.get("accent_color"),
        "company_logo": _public_asset(resolved.get("company_logo")),
        "po": {
            "signature_labels": (resolved.get("po") or {}).get("signature_labels"),
            "footer_name_label": (resolved.get("po") or {}).get("footer_name_label"),
            "header_elements": list((resolved.get("po") or {}).get("header_elements") or []),
            "footer_elements": list((resolved.get("po") or {}).get("footer_elements") or []),
        },
        "invoice": {
            "bank_accounts": inv.get("bank_accounts"),
            "estimasi_title": inv.get("estimasi_title"),
            "estimasi_lines": inv.get("estimasi_lines"),
            "prepared_label": inv.get("prepared_label"),
            "prepared_footer": inv.get("prepared_footer"),
            "approved_label": inv.get("approved_label"),
            "approved_footer": inv.get("approved_footer"),
            "prepared_signature_image": _public_asset(inv.get("prepared_signature_image")),
            "approved_signature_image": _public_asset(inv.get("approved_signature_image")),
            "header_elements": list(inv.get("header_elements") or []),
            "footer_elements": list(inv.get("footer_elements") or []),
        },
        "invoice_markup": {
            "bank_accounts": (resolved.get("invoice_markup") or {}).get("bank_accounts"),
            "estimasi_title": (resolved.get("invoice_markup") or {}).get("estimasi_title"),
            "estimasi_lines": (resolved.get("invoice_markup") or {}).get("estimasi_lines"),
            "prepared_label": (resolved.get("invoice_markup") or {}).get("prepared_label"),
            "prepared_footer": (resolved.get("invoice_markup") or {}).get("prepared_footer"),
            "approved_label": (resolved.get("invoice_markup") or {}).get("approved_label"),
            "approved_footer": (resolved.get("invoice_markup") or {}).get("approved_footer"),
            "prepared_signature_image": _public_asset(
                (resolved.get("invoice_markup") or {}).get("prepared_signature_image")
            ),
            "approved_signature_image": _public_asset(
                (resolved.get("invoice_markup") or {}).get("approved_signature_image")
            ),
            "header_elements": list((resolved.get("invoice_markup") or {}).get("header_elements") or []),
            "footer_elements": list((resolved.get("invoice_markup") or {}).get("footer_elements") or []),
        },
        "surat_jalan": {
            "signature_labels": (resolved.get("surat_jalan") or {}).get("signature_labels"),
            "date_label": (resolved.get("surat_jalan") or {}).get("date_label"),
            "header_elements": list((resolved.get("surat_jalan") or {}).get("header_elements") or []),
            "footer_elements": list((resolved.get("surat_jalan") or {}).get("footer_elements") or []),
        },
        "updated_at": resolved.get("updated_at"),
        "updated_by": resolved.get("updated_by"),
    }


def _validate_put_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(400, "Payload tidak valid")

    if "accent_color" in payload and payload.get("accent_color") not in (None, ""):
        if _normalize_hex(payload.get("accent_color")) is None:
            raise HTTPException(400, "Warna aksen harus format #RGB atau #RRGGBB")

    invoice_payload = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else {}
    if "bank_accounts" in invoice_payload:
        banks = invoice_payload.get("bank_accounts")
        if banks is not None and not isinstance(banks, list):
            raise HTTPException(400, "Daftar rekening harus berupa array")
        if isinstance(banks, list) and len(banks) > _MAX_BANKS:
            raise HTTPException(400, f"Maksimal {_MAX_BANKS} rekening pada Design Cetakan")

    markup_payload = payload.get("invoice_markup") if isinstance(payload.get("invoice_markup"), dict) else {}
    if "bank_accounts" in markup_payload:
        banks = markup_payload.get("bank_accounts")
        if banks is not None and not isinstance(banks, list):
            raise HTTPException(400, "Daftar rekening Faktur Markup harus berupa array")
        if isinstance(banks, list) and len(banks) > _MAX_BANKS:
            raise HTTPException(400, f"Maksimal {_MAX_BANKS} rekening pada Design Cetakan Faktur Markup")

    # Strict validate header/footer elements when present on PUT.
    touched = False
    if "header_elements" in invoice_payload or "footer_elements" in invoice_payload:
        invoice_payload = dict(invoice_payload)
        if "header_elements" in invoice_payload:
            invoice_payload["header_elements"] = _sanitize_header_elements(
                invoice_payload.get("header_elements"),
                strict=True,
                document="invoice",
            )
        if "footer_elements" in invoice_payload:
            invoice_payload["footer_elements"] = _sanitize_footer_elements(
                invoice_payload.get("footer_elements"),
                strict=True,
                document="invoice",
            )
        payload = dict(payload)
        payload["invoice"] = invoice_payload
        touched = True

    if "header_elements" in markup_payload or "footer_elements" in markup_payload:
        markup_payload = dict(markup_payload)
        if "header_elements" in markup_payload:
            markup_payload["header_elements"] = _sanitize_header_elements(
                markup_payload.get("header_elements"),
                strict=True,
                document="markup",
            )
        if "footer_elements" in markup_payload:
            markup_payload["footer_elements"] = _sanitize_footer_elements(
                markup_payload.get("footer_elements"),
                strict=True,
                document="markup",
            )
        payload = dict(payload)
        payload["invoice_markup"] = markup_payload
        touched = True

    po_payload = payload.get("po") if isinstance(payload.get("po"), dict) else {}
    if "header_elements" in po_payload or "footer_elements" in po_payload:
        po_payload = dict(po_payload)
        if "header_elements" in po_payload:
            po_payload["header_elements"] = _sanitize_header_elements(
                po_payload.get("header_elements"),
                strict=True,
                document="po",
            )
        if "footer_elements" in po_payload:
            po_payload["footer_elements"] = _sanitize_footer_elements(
                po_payload.get("footer_elements"),
                strict=True,
                document="po",
            )
        payload = dict(payload)
        payload["po"] = po_payload
        touched = True

    sj_payload = payload.get("surat_jalan") if isinstance(payload.get("surat_jalan"), dict) else {}
    if "header_elements" in sj_payload or "footer_elements" in sj_payload:
        sj_payload = dict(sj_payload)
        if "header_elements" in sj_payload:
            sj_payload["header_elements"] = _sanitize_header_elements(
                sj_payload.get("header_elements"),
                strict=True,
                document="delivery",
            )
        if "footer_elements" in sj_payload:
            sj_payload["footer_elements"] = _sanitize_footer_elements(
                sj_payload.get("footer_elements"),
                strict=True,
                document="delivery",
            )
        payload = dict(payload)
        payload["surat_jalan"] = sj_payload
        touched = True
    _ = touched

    resolved = resolve_print_design(payload)
    # Client PUT must not set asset refs — preserved from existing on save.
    resolved["company_logo"] = None
    resolved["invoice"]["prepared_signature_image"] = None
    resolved["invoice"]["approved_signature_image"] = None
    if isinstance(resolved.get("invoice_markup"), dict):
        resolved["invoice_markup"]["prepared_signature_image"] = None
        resolved["invoice_markup"]["approved_signature_image"] = None
    resolved["updated_at"] = None
    resolved["updated_by"] = None
    return resolved


def _actor_snapshot(user: Optional[dict]) -> Optional[dict]:
    if not isinstance(user, dict):
        return None
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "name": user.get("name") or user.get("email") or "User",
        "email": user.get("email") or "",
        "role": user.get("role") or "",
    }


async def get_print_design_settings(db) -> Dict[str, Any]:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return public_settings(doc)


async def get_print_design_internal(db) -> Dict[str, Any]:
    """Resolved settings including internal asset refs for PDF/asset ops."""
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return resolve_print_design(doc)


async def save_print_design_settings(db, payload: Dict[str, Any], user: Optional[dict] = None) -> Dict[str, Any]:
    cleaned = _validate_put_payload(payload)
    existing = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0}) or {}
    existing_resolved = resolve_print_design(existing)
    cleaned["company_logo"] = existing_resolved.get("company_logo")
    cleaned["invoice"]["prepared_signature_image"] = (existing_resolved.get("invoice") or {}).get(
        "prepared_signature_image"
    )
    cleaned["invoice"]["approved_signature_image"] = (existing_resolved.get("invoice") or {}).get(
        "approved_signature_image"
    )
    cleaned.setdefault("invoice_markup", {})
    cleaned["invoice_markup"]["prepared_signature_image"] = (
        existing_resolved.get("invoice_markup") or {}
    ).get("prepared_signature_image")
    cleaned["invoice_markup"]["approved_signature_image"] = (
        existing_resolved.get("invoice_markup") or {}
    ).get("approved_signature_image")
    # Preserve V2 header/footer when client omits the key (older clients / partial payloads).
    inv_in = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else {}
    markup_in = payload.get("invoice_markup") if isinstance(payload.get("invoice_markup"), dict) else {}
    po_in = payload.get("po") if isinstance(payload.get("po"), dict) else {}
    sj_in = payload.get("surat_jalan") if isinstance(payload.get("surat_jalan"), dict) else {}
    if "header_elements" not in inv_in:
        cleaned["invoice"]["header_elements"] = list(
            (existing_resolved.get("invoice") or {}).get("header_elements") or []
        )
    if "footer_elements" not in inv_in:
        cleaned["invoice"]["footer_elements"] = list(
            (existing_resolved.get("invoice") or {}).get("footer_elements") or []
        )
    if "invoice_markup" not in payload:
        cleaned["invoice_markup"] = copy.deepcopy(existing_resolved.get("invoice_markup") or cleaned["invoice_markup"])
    else:
        if "header_elements" not in markup_in:
            cleaned["invoice_markup"]["header_elements"] = list(
                (existing_resolved.get("invoice_markup") or {}).get("header_elements") or []
            )
        if "footer_elements" not in markup_in:
            cleaned["invoice_markup"]["footer_elements"] = list(
                (existing_resolved.get("invoice_markup") or {}).get("footer_elements") or []
            )
    if "header_elements" not in po_in:
        cleaned["po"]["header_elements"] = list(
            (existing_resolved.get("po") or {}).get("header_elements") or []
        )
    if "footer_elements" not in po_in:
        cleaned["po"]["footer_elements"] = list(
            (existing_resolved.get("po") or {}).get("footer_elements") or []
        )
    if "header_elements" not in sj_in:
        cleaned["surat_jalan"]["header_elements"] = list(
            (existing_resolved.get("surat_jalan") or {}).get("header_elements") or []
        )
    if "footer_elements" not in sj_in:
        cleaned["surat_jalan"]["footer_elements"] = list(
            (existing_resolved.get("surat_jalan") or {}).get("footer_elements") or []
        )
    cleaned["schema_version"] = schema_version_for_invoice(
        cleaned["invoice"]["header_elements"],
        cleaned["invoice"]["footer_elements"],
        cleaned["po"]["header_elements"],
        cleaned["po"]["footer_elements"],
        cleaned["surat_jalan"]["header_elements"],
        cleaned["surat_jalan"]["footer_elements"],
    )
    saved = {
        **cleaned,
        "key": SETTINGS_KEY,
        "updated_at": now_iso(),
        "updated_by": _actor_snapshot(user),
    }
    await db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": saved},
        upsert=True,
    )
    return public_settings(saved)


def _slot_max_bytes(slot: str) -> int:
    if slot == SLOT_COMPANY_LOGO:
        return MAX_LOGO_BYTES
    return MAX_SIGNATURE_BYTES


def _sniff_image_type(body: bytes, content_type: Optional[str] = None) -> Optional[str]:
    if not body:
        return None
    if body.startswith(PNG_MAGIC):
        return "image/png"
    if body.startswith(JPEG_MAGIC):
        return "image/jpeg"
    # WEBP: RIFF....WEBP
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct == "image/jpg":
        return "image/jpeg"
    if ct in {"image/png", "image/jpeg", "image/webp"}:
        return ct
    return None


def validate_design_image(
    body: bytes,
    content_type: Optional[str],
    *,
    max_bytes: int,
) -> str:
    """Return media type or raise HTTP 400."""
    if not body:
        raise HTTPException(400, "File kosong.")
    if len(body) < MIN_IMAGE_BYTES:
        raise HTTPException(400, "File terlalu kecil.")
    if len(body) > max_bytes:
        mb = "2MB" if max_bytes >= 2_000_000 else "1.5MB"
        raise HTTPException(400, f"File terlalu besar (maks {mb}).")
    if body[:2] == b"MZ" or body[:4] == b"\x7fELF":
        raise HTTPException(400, "Tipe file tidak didukung.")
    head = body[:512].lower()
    claimed = (content_type or "").lower()
    if b"<svg" in head or "image/svg" in claimed:
        raise HTTPException(400, "SVG tidak diizinkan untuk Design Cetakan.")
    if body[:4] == b"%PDF" or claimed == "application/pdf":
        raise HTTPException(400, "PDF tidak diizinkan. Gunakan PNG, JPEG, atau WEBP.")
    media = _sniff_image_type(body, content_type)
    if not media:
        raise HTTPException(400, "File harus berupa gambar PNG, JPEG, atau WEBP.")
    if media == "image/png" and not body.startswith(PNG_MAGIC):
        raise HTTPException(400, "Isi file bukan PNG yang valid.")
    if media == "image/jpeg" and not body.startswith(JPEG_MAGIC):
        raise HTTPException(400, "Isi file bukan JPEG yang valid.")
    if media == "image/webp" and not (len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP"):
        raise HTTPException(400, "Isi file bukan WEBP yang valid.")
    return media


def _branch_owned_asset_path(path: str, branch_id: str) -> bool:
    """True only for objects stored under this branch's print-design prefix."""
    safe_branch = _FILENAME_SAFE.sub("_", str(branch_id or "")) or "branch"
    prefix = f"{obj_storage.APP_NAME}/print-design/branch-{safe_branch}/"
    raw = str(path or "").strip()
    return bool(raw) and raw.startswith(prefix) and ".." not in raw and not raw.startswith("/")


def _storage_key(slot: str, file_id: str, ext: str, branch_id: Optional[str] = None) -> str:
    safe_slot = _FILENAME_SAFE.sub("_", slot) or "asset"
    safe_id = _FILENAME_SAFE.sub("_", file_id) or str(uuid.uuid4())
    safe_ext = _FILENAME_SAFE.sub("", (ext or "bin").lstrip(".")) or "bin"
    prefix = f"{obj_storage.APP_NAME}/print-design"
    if branch_id:
        safe_branch = _FILENAME_SAFE.sub("_", branch_id) or "branch"
        return f"{prefix}/branch-{safe_branch}/{safe_slot}/{safe_id}.{safe_ext}"
    return f"{prefix}/{safe_slot}/{safe_id}.{safe_ext}"


def _get_slot_ref(settings: dict, slot: str) -> Optional[dict]:
    if slot == SLOT_COMPANY_LOGO:
        return _sanitize_asset_ref(settings.get("company_logo"))
    inv = settings.get("invoice") or {}
    if slot == SLOT_PREPARED:
        return _sanitize_asset_ref(inv.get("prepared_signature_image"))
    if slot == SLOT_APPROVED:
        return _sanitize_asset_ref(inv.get("approved_signature_image"))
    markup = settings.get("invoice_markup") or {}
    if slot == SLOT_MARKUP_PREPARED:
        return _sanitize_asset_ref(markup.get("prepared_signature_image"))
    if slot == SLOT_MARKUP_APPROVED:
        return _sanitize_asset_ref(markup.get("approved_signature_image"))
    return None


def _set_slot_ref(settings: dict, slot: str, ref: Optional[dict]) -> dict:
    out = copy.deepcopy(settings)
    if slot == SLOT_COMPANY_LOGO:
        out["company_logo"] = ref
    elif slot == SLOT_PREPARED:
        out.setdefault("invoice", {})["prepared_signature_image"] = ref
    elif slot == SLOT_APPROVED:
        out.setdefault("invoice", {})["approved_signature_image"] = ref
    elif slot == SLOT_MARKUP_PREPARED:
        out.setdefault("invoice_markup", {})["prepared_signature_image"] = ref
    elif slot == SLOT_MARKUP_APPROVED:
        out.setdefault("invoice_markup", {})["approved_signature_image"] = ref
    return out


async def _persist_settings_doc(db, doc: dict, user: Optional[dict] = None) -> Dict[str, Any]:
    saved = {
        **resolve_print_design(doc),
        "key": SETTINGS_KEY,
        "updated_at": now_iso(),
        "updated_by": _actor_snapshot(user),
    }
    saved.pop("_pdf_assets", None)
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    return saved


async def upload_print_design_asset(
    db,
    *,
    slot: str,
    body: bytes,
    content_type: Optional[str],
    filename: str,
    user: Optional[dict],
) -> Dict[str, Any]:
    slot = str(slot or "").strip()
    if slot not in ASSET_SLOTS:
        raise HTTPException(400, "Slot asset tidak valid.")
    media = validate_design_image(body, content_type, max_bytes=_slot_max_bytes(slot))
    file_id = str(uuid.uuid4())
    ext = obj_storage.ext_for(media, "bin")
    path = _storage_key(slot, file_id, ext)
    stamp = now_iso()
    safe_name = _FILENAME_SAFE.sub("_", (filename or f"{slot}.{ext}").strip()) or f"{slot}.{ext}"

    try:
        stored = obj_storage.put_object(path, body, media)
    except Exception as exc:
        logger.exception("print_design put_object failed slot=%s", slot)
        raise HTTPException(502, "Gagal menyimpan asset Design Cetakan.") from exc

    storage_path = stored.get("path") or path
    new_ref = {
        "file_id": file_id,
        "storage_path": storage_path,
        "content_type": media,
        "size": stored.get("size") or len(body),
        "original_filename": safe_name[:180],
        "updated_at": stamp,
    }

    current = await get_print_design_internal(db)
    old_ref = _get_slot_ref(current, slot)
    next_doc = _set_slot_ref(current, slot, new_ref)

    try:
        saved = await _persist_settings_doc(db, next_doc, user=user)
    except Exception:
        try:
            obj_storage.delete_object(storage_path)
        except Exception:
            logger.warning("cleanup new print_design object failed path=%s", storage_path, exc_info=True)
        raise

    file_doc = {
        "id": file_id,
        "storage_path": storage_path,
        "original_filename": safe_name[:180],
        "content_type": media,
        "size": new_ref["size"],
        "kind": FILE_KIND,
        "slot": slot,
        "uploaded_at": stamp,
        "uploaded_by": (user or {}).get("email") or (user or {}).get("name"),
        "uploaded_by_name": (user or {}).get("name"),
        "uploaded_by_role": (user or {}).get("role"),
        "is_deleted": False,
    }
    try:
        await db.files.insert_one(dict(file_doc))
    except Exception:
        logger.warning("print_design files metadata insert failed id=%s", file_id, exc_info=True)

    if old_ref and old_ref.get("storage_path") and old_ref.get("storage_path") != storage_path:
        await _best_effort_delete_asset(db, old_ref)

    return public_settings(saved)


async def remove_print_design_asset(db, *, slot: str, user: Optional[dict]) -> Dict[str, Any]:
    slot = str(slot or "").strip()
    if slot not in ASSET_SLOTS:
        raise HTTPException(400, "Slot asset tidak valid.")
    current = await get_print_design_internal(db)
    old_ref = _get_slot_ref(current, slot)
    next_doc = _set_slot_ref(current, slot, None)
    saved = await _persist_settings_doc(db, next_doc, user=user)
    if old_ref:
        await _best_effort_delete_asset(db, old_ref)
    return public_settings(saved)


async def _best_effort_delete_asset(db, ref: dict) -> None:
    path = str((ref or {}).get("storage_path") or "").strip()
    file_id = str((ref or {}).get("file_id") or "").strip()
    if path:
        try:
            obj_storage.delete_object(path)
        except Exception:
            logger.warning("print_design delete_object failed path=%s", path, exc_info=True)
    if file_id:
        try:
            await db.files.update_one(
                {"id": file_id, "kind": FILE_KIND},
                {"$set": {"is_deleted": True, "deleted_at": now_iso()}},
            )
        except Exception:
            logger.warning("print_design files soft-delete failed id=%s", file_id, exc_info=True)


async def load_slot_bytes(db, slot: str) -> Tuple[bytes, str, str]:
    """Return (bytes, content_type, filename) for preview. Raises 404 if absent."""
    slot = str(slot or "").strip()
    if slot not in ASSET_SLOTS:
        raise HTTPException(400, "Slot asset tidak valid.")
    settings = await get_print_design_internal(db)
    ref = _get_slot_ref(settings, slot)
    if not ref:
        raise HTTPException(404, "Asset Design Cetakan tidak ditemukan.")
    path = ref["storage_path"]
    rec = await db.files.find_one(
        {"id": ref["file_id"], "kind": FILE_KIND, "is_deleted": {"$ne": True}},
        {"_id": 0},
    )
    if rec and str(rec.get("storage_path") or "") != path:
        path = str(rec.get("storage_path") or path)
    try:
        data, ct = obj_storage.get_object(path)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Asset Design Cetakan tidak ditemukan.") from exc
    except Exception as exc:
        logger.exception("print_design get_object failed slot=%s", slot)
        raise HTTPException(502, "Gagal membaca asset Design Cetakan.") from exc
    if not data:
        raise HTTPException(404, "Asset Design Cetakan tidak ditemukan.")
    media = (rec or {}).get("content_type") or ref.get("content_type") or ct or "application/octet-stream"
    name = (rec or {}).get("original_filename") or ref.get("original_filename") or f"{slot}.bin"
    return data, media, name


async def prefetch_pdf_assets(db, design: Optional[dict] = None) -> Dict[str, Optional[bytes]]:
    """Load custom asset bytes for sync PDF renderers. Failures → None (fallback)."""
    settings = design if isinstance(design, dict) else await get_print_design_internal(db)
    out: Dict[str, Optional[bytes]] = {
        "company_logo": None,
        "prepared_signature_image": None,
        "approved_signature_image": None,
    }
    for slot, key in (
        (SLOT_COMPANY_LOGO, "company_logo"),
        (SLOT_PREPARED, "prepared_signature_image"),
        (SLOT_APPROVED, "approved_signature_image"),
    ):
        ref = _get_slot_ref(settings, slot)
        if not ref:
            continue
        try:
            data, _ct = obj_storage.get_object(ref["storage_path"])
            if data and len(data) >= MIN_IMAGE_BYTES:
                out[key] = data
        except Exception:
            logger.warning("prefetch print_design asset failed slot=%s", slot, exc_info=True)
            out[key] = None
    return out


def canonical_branch_id(raw: Any) -> Optional[str]:
    """Digit string only. None when missing or not a digit id."""
    if raw is None:
        return None
    bid = str(raw).strip()
    if not bid or not bid.isdigit():
        return None
    return bid


def branch_settings_key(branch_id: str) -> str:
    """Authoritative app_settings key. Rejects non-digit ids. Never takes a client path."""
    bid = canonical_branch_id(branch_id)
    if not bid:
        raise HTTPException(400, "branch_id tidak valid")
    return f"{BRANCH_KEY_PREFIX}{bid}"


async def assert_known_branch_id(db, branch_id: str) -> str:
    """Settings writes accept a digit id only when some order already stores it."""
    bid = canonical_branch_id(branch_id)
    if not bid:
        raise HTTPException(400, "branch_id tidak valid")
    found = await db.orders.find_one({"branch_id": bid}, {"_id": 1})
    if not found:
        raise HTTPException(400, "Cabang tidak dikenal")
    return bid


def _strict_bank_accounts(raw: Any) -> List[Dict[str, str]]:
    """Valid rows only. Empty/malformed → [] so the caller can inherit global."""
    if not isinstance(raw, list) or not raw:
        return []
    out: List[Dict[str, str]] = []
    for row in raw[:_MAX_BANKS]:
        if not isinstance(row, dict):
            continue
        label = _clip(row.get("bank_label"))
        number = _clip(row.get("account_number"), 64)
        name = _clip(row.get("account_name"))
        if not (label or number or name):
            continue
        out.append({
            "bank_label": label or "Bank",
            "account_number": number,
            "account_name": name,
        })
    return out


def _meaningful_address(raw: Any) -> List[str]:
    if not isinstance(raw, list) or not raw:
        return []
    out: List[str] = []
    for item in raw:
        text = _clip(item, _MAX_LINE)
        if text:
            out.append(text)
        if len(out) >= _MAX_ADDRESS_LINES:
            break
    return out


def branch_override_active(doc: Optional[dict], field: str) -> bool:
    """True only when the sparse document holds a real override for `field`."""
    src = doc if isinstance(doc, dict) else {}
    inv = src.get("invoice") if isinstance(src.get("invoice"), dict) else {}
    if field == "company_name":
        return "company_name" in src and bool(_clip(src.get("company_name")))
    if field == "address_lines":
        return "address_lines" in src and bool(_meaningful_address(src.get("address_lines")))
    if field == "company_logo":
        return "company_logo" in src and _sanitize_asset_ref(src.get("company_logo")) is not None
    if field == "bank_accounts":
        return "bank_accounts" in inv and bool(_strict_bank_accounts(inv.get("bank_accounts")))
    if field in {"prepared_label", "prepared_footer", "approved_label", "approved_footer"}:
        return field in inv and bool(_clip(inv.get(field)))
    if field in {SLOT_PREPARED, SLOT_APPROVED}:
        return field in inv and _sanitize_asset_ref(inv.get(field)) is not None
    return False


def inheritance_status(doc: Optional[dict]) -> Dict[str, Dict[str, bool]]:
    status: Dict[str, Dict[str, bool]] = {}
    for field in BRANCH_INHERITANCE_FIELDS:
        overridden = branch_override_active(doc, field)
        status[field] = {"inherited": not overridden, "overridden": overridden}
    return status


def sanitize_sparse_branch_payload(payload: Any) -> Dict[str, Any]:
    """Keep only explicit, non-empty branch overrides. Global-only fields are dropped."""
    if not isinstance(payload, dict):
        raise HTTPException(400, "Payload tidak valid")
    out: Dict[str, Any] = {}
    if "company_name" in payload:
        text = _clip(payload.get("company_name"))
        if text:
            out["company_name"] = text
    if "address_lines" in payload:
        lines = _meaningful_address(payload.get("address_lines"))
        if lines:
            out["address_lines"] = lines
    inv_in = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else {}
    inv_out: Dict[str, Any] = {}
    if "bank_accounts" in inv_in:
        banks = _strict_bank_accounts(inv_in.get("bank_accounts"))
        if banks:
            inv_out["bank_accounts"] = banks
    for field in ("prepared_label", "prepared_footer", "approved_label", "approved_footer"):
        if field in inv_in:
            text = _clip(inv_in.get(field))
            if text:
                inv_out[field] = text
    if inv_out:
        out["invoice"] = inv_out
    return out


def _sparse_from_doc(doc: Optional[dict]) -> Dict[str, Any]:
    """Project a stored branch document down to meaningful override fields only."""
    src = doc if isinstance(doc, dict) else {}
    out: Dict[str, Any] = {}
    if branch_override_active(src, "company_name"):
        out["company_name"] = _clip(src.get("company_name"))
    if branch_override_active(src, "address_lines"):
        out["address_lines"] = _meaningful_address(src.get("address_lines"))
    if branch_override_active(src, "company_logo"):
        out["company_logo"] = _sanitize_asset_ref(src.get("company_logo"))
    inv_out: Dict[str, Any] = {}
    inv = src.get("invoice") if isinstance(src.get("invoice"), dict) else {}
    if branch_override_active(src, "bank_accounts"):
        inv_out["bank_accounts"] = _strict_bank_accounts(inv.get("bank_accounts"))
    for field in ("prepared_label", "prepared_footer", "approved_label", "approved_footer"):
        if branch_override_active(src, field):
            inv_out[field] = _clip(inv.get(field))
    for field in (SLOT_PREPARED, SLOT_APPROVED):
        if branch_override_active(src, field):
            inv_out[field] = _sanitize_asset_ref(inv.get(field))
    if inv_out:
        out["invoice"] = inv_out
    return out


def public_sparse_override(doc: Optional[dict]) -> Dict[str, Any]:
    """API view of the sparse override. Asset refs never include storage paths."""
    sparse = _sparse_from_doc(doc)
    out: Dict[str, Any] = {}
    if "company_name" in sparse:
        out["company_name"] = sparse["company_name"]
    if "address_lines" in sparse:
        out["address_lines"] = list(sparse["address_lines"])
    if "company_logo" in sparse:
        out["company_logo"] = _public_asset(sparse.get("company_logo"))
    inv = sparse.get("invoice") if isinstance(sparse.get("invoice"), dict) else {}
    inv_out: Dict[str, Any] = {}
    if "bank_accounts" in inv:
        inv_out["bank_accounts"] = inv["bank_accounts"]
    for field in ("prepared_label", "prepared_footer", "approved_label", "approved_footer"):
        if field in inv:
            inv_out[field] = inv[field]
    for field in (SLOT_PREPARED, SLOT_APPROVED):
        if field in inv:
            inv_out[field] = _public_asset(inv.get(field))
    if inv_out:
        out["invoice"] = inv_out
    return out


def overlay_branch_design(global_design: dict, branch_doc: Optional[dict]) -> Dict[str, Any]:
    """defaults+global (already resolved) then sparse branch fields that are actually set."""
    out = copy.deepcopy(global_design if isinstance(global_design, dict) else resolve_print_design(None))
    sparse = _sparse_from_doc(branch_doc)
    if "company_name" in sparse:
        out["company_name"] = sparse["company_name"]
    if "address_lines" in sparse:
        out["address_lines"] = list(sparse["address_lines"])
    if "company_logo" in sparse:
        out["company_logo"] = sparse["company_logo"]
    inv = out.setdefault("invoice", {})
    sparse_inv = sparse.get("invoice") if isinstance(sparse.get("invoice"), dict) else {}
    if "bank_accounts" in sparse_inv:
        inv["bank_accounts"] = copy.deepcopy(sparse_inv["bank_accounts"])
    for field in ("prepared_label", "prepared_footer", "approved_label", "approved_footer"):
        if field in sparse_inv:
            inv[field] = sparse_inv[field]
    for field in (SLOT_PREPARED, SLOT_APPROVED):
        if field in sparse_inv:
            inv[field] = sparse_inv[field]
    return out


def _merge_text_with_existing_assets(text_sparse: dict, existing: Optional[dict]) -> Dict[str, Any]:
    """Text PUT replaces text overrides and keeps branch asset refs already stored."""
    merged = copy.deepcopy(text_sparse)
    assets = _sparse_from_doc(existing)
    if "company_logo" in assets:
        merged["company_logo"] = assets["company_logo"]
    asset_inv = (assets.get("invoice") or {}) if isinstance(assets.get("invoice"), dict) else {}
    kept = {
        field: asset_inv[field]
        for field in (SLOT_PREPARED, SLOT_APPROVED)
        if field in asset_inv
    }
    if kept:
        inv = merged.setdefault("invoice", {})
        inv.update(kept)
    return merged


async def _load_branch_raw(db, branch_id: str) -> Dict[str, Any]:
    key = branch_settings_key(branch_id)
    doc = await db.app_settings.find_one({"key": key}, {"_id": 0})
    return doc if isinstance(doc, dict) else {}


async def _replace_branch_document(db, branch_id: str, sparse: Dict[str, Any], user: Optional[dict]) -> Dict[str, Any]:
    """Replace the branch document. Empty sparse deletes it. Global key is never written."""
    key = branch_settings_key(branch_id)
    clean = _sparse_from_doc(sparse)
    await db.app_settings.delete_one({"key": key})
    if not clean:
        return {"key": key, "branch_id": branch_id}
    saved = {
        "key": key,
        "branch_id": branch_id,
        **clean,
        "updated_at": now_iso(),
        "updated_by": _actor_snapshot(user),
    }
    await db.app_settings.insert_one(saved)
    return saved


async def list_design_cetakan_branches(db, name_lookup=None) -> List[Dict[str, str]]:
    """Distinct digit orders.branch_id. One failed name lookup does not fail the catalog."""
    raw_ids = await db.orders.distinct("branch_id")
    ids = sorted(
        {bid for bid in (canonical_branch_id(item) for item in (raw_ids or [])) if bid},
        key=lambda value: int(value),
    )
    rows: List[Dict[str, str]] = []
    for bid in ids:
        label = ""
        if name_lookup is not None:
            try:
                looked = await name_lookup(bid)
                label = str(looked or "").strip()
            except Exception:
                logger.warning("branch catalog name lookup failed id=%s", bid, exc_info=True)
                label = ""
        rows.append({"branch_id": bid, "name": label or f"Cabang {bid}"})
    return rows


async def get_branch_design_view(db, branch_id: str) -> Dict[str, Any]:
    bid = await assert_known_branch_id(db, branch_id)
    raw = await _load_branch_raw(db, bid)
    global_design = await get_print_design_internal(db)
    effective = overlay_branch_design(global_design, raw)
    return {
        "branch_id": bid,
        "override": public_sparse_override(raw),
        "effective": public_settings(effective),
        "inherited": inheritance_status(raw),
    }


async def save_branch_design_settings(db, branch_id: str, payload: Dict[str, Any], user: Optional[dict] = None) -> Dict[str, Any]:
    bid = await assert_known_branch_id(db, branch_id)
    text_sparse = sanitize_sparse_branch_payload(payload or {})
    existing = await _load_branch_raw(db, bid)
    merged = _merge_text_with_existing_assets(text_sparse, existing)
    await _replace_branch_document(db, bid, merged, user)
    return await get_branch_design_view(db, bid)


async def _delete_branch_only_asset(
    db,
    ref: Optional[dict],
    global_design: dict,
    slot: str,
    branch_id: str,
) -> None:
    clean = _sanitize_asset_ref(ref)
    if not clean:
        return
    global_ref = _get_slot_ref(global_design, slot)
    if global_ref and global_ref.get("storage_path") == clean.get("storage_path"):
        return
    if not _branch_owned_asset_path(clean.get("storage_path") or "", branch_id):
        logger.warning(
            "skip print_design delete; path not owned by branch %s slot=%s",
            branch_id,
            slot,
        )
        return
    await _best_effort_delete_asset(db, clean)


async def reset_branch_design_settings(db, branch_id: str, user: Optional[dict] = None) -> Dict[str, Any]:
    """Delete the branch override document and its objects. Global settings stay."""
    bid = await assert_known_branch_id(db, branch_id)
    raw = await _load_branch_raw(db, bid)
    refs = {
        SLOT_COMPANY_LOGO: _get_slot_ref(raw, SLOT_COMPANY_LOGO),
        SLOT_PREPARED: _get_slot_ref(raw, SLOT_PREPARED),
        SLOT_APPROVED: _get_slot_ref(raw, SLOT_APPROVED),
    }
    global_design = await get_print_design_internal(db)
    await db.app_settings.delete_one({"key": branch_settings_key(bid)})
    for slot, ref in refs.items():
        await _delete_branch_only_asset(db, ref, global_design, slot, bid)
    return await get_branch_design_view(db, bid)


async def _read_asset_bytes(ref: Optional[dict]) -> Optional[bytes]:
    clean = _sanitize_asset_ref(ref)
    if not clean:
        return None
    try:
        data, _ct = obj_storage.get_object(clean["storage_path"])
    except Exception:
        logger.warning("prefetch print_design asset failed", exc_info=True)
        return None
    if data and len(data) >= MIN_IMAGE_BYTES:
        return data
    return None


async def prefetch_layered_assets(global_design: dict, branch_doc: Optional[dict]) -> Dict[str, Optional[bytes]]:
    """Branch bytes, then global bytes. Missing branch object falls back to global."""
    out: Dict[str, Optional[bytes]] = {
        "company_logo": None,
        "prepared_signature_image": None,
        "approved_signature_image": None,
    }
    for slot, key in (
        (SLOT_COMPANY_LOGO, "company_logo"),
        (SLOT_PREPARED, "prepared_signature_image"),
        (SLOT_APPROVED, "approved_signature_image"),
    ):
        data = None
        if branch_override_active(branch_doc, slot if slot != SLOT_COMPANY_LOGO else "company_logo"):
            data = await _read_asset_bytes(_get_slot_ref(branch_doc or {}, slot))
        if data is None:
            data = await _read_asset_bytes(_get_slot_ref(global_design, slot))
        out[key] = data
    return out


async def get_print_design_for_pdf(db, branch_id: Any = None) -> Dict[str, Any]:
    """Resolved design + `_pdf_assets`. Missing/invalid branch_id keeps global behavior."""
    global_design = await get_print_design_internal(db)
    bid = canonical_branch_id(branch_id)
    branch_doc = None
    if bid:
        branch_doc = await db.app_settings.find_one({"key": branch_settings_key(bid)}, {"_id": 0})
    design = overlay_branch_design(global_design, branch_doc) if bid else global_design
    design["_pdf_assets"] = await prefetch_layered_assets(global_design, branch_doc if bid else None)
    return design


async def upload_branch_print_design_asset(
    db,
    *,
    branch_id: str,
    slot: str,
    body: bytes,
    content_type: Optional[str],
    filename: str,
    user: Optional[dict],
) -> Dict[str, Any]:
    bid = await assert_known_branch_id(db, branch_id)
    slot = str(slot or "").strip()
    if slot not in ASSET_SLOTS:
        raise HTTPException(400, "Slot asset tidak valid.")
    media = validate_design_image(body, content_type, max_bytes=_slot_max_bytes(slot))
    file_id = str(uuid.uuid4())
    ext = obj_storage.ext_for(media, "bin")
    path = _storage_key(slot, file_id, ext, branch_id=bid)
    stamp = now_iso()
    safe_name = _FILENAME_SAFE.sub("_", (filename or f"{slot}.{ext}").strip()) or f"{slot}.{ext}"
    try:
        stored = obj_storage.put_object(path, body, media)
    except Exception as exc:
        logger.exception("print_design branch put_object failed slot=%s branch=%s", slot, bid)
        raise HTTPException(502, "Gagal menyimpan asset Design Cetakan.") from exc
    storage_path = stored.get("path") or path
    new_ref = {
        "file_id": file_id,
        "storage_path": storage_path,
        "content_type": media,
        "size": stored.get("size") or len(body),
        "original_filename": safe_name[:180],
        "updated_at": stamp,
    }
    current = await _load_branch_raw(db, bid)
    old_ref = _get_slot_ref(current, slot)
    next_doc = _set_slot_ref(current or {}, slot, new_ref)
    try:
        await _replace_branch_document(db, bid, next_doc, user)
    except Exception:
        try:
            obj_storage.delete_object(storage_path)
        except Exception:
            logger.warning("cleanup new branch print_design object failed path=%s", storage_path, exc_info=True)
        raise
    try:
        await db.files.insert_one({
            "id": file_id,
            "storage_path": storage_path,
            "original_filename": safe_name[:180],
            "content_type": media,
            "size": new_ref["size"],
            "kind": FILE_KIND,
            "slot": slot,
            "branch_id": bid,
            "uploaded_at": stamp,
            "uploaded_by": (user or {}).get("email") or (user or {}).get("name"),
            "uploaded_by_name": (user or {}).get("name"),
            "uploaded_by_role": (user or {}).get("role"),
            "is_deleted": False,
        })
    except Exception:
        logger.warning("print_design branch files metadata insert failed id=%s", file_id, exc_info=True)
    global_design = await get_print_design_internal(db)
    await _delete_branch_only_asset(db, old_ref, global_design, slot, bid)
    return await get_branch_design_view(db, bid)


async def remove_branch_print_design_asset(db, *, branch_id: str, slot: str, user: Optional[dict]) -> Dict[str, Any]:
    """Drop the branch asset override. Global asset object is not deleted."""
    bid = await assert_known_branch_id(db, branch_id)
    slot = str(slot or "").strip()
    if slot not in ASSET_SLOTS:
        raise HTTPException(400, "Slot asset tidak valid.")
    current = await _load_branch_raw(db, bid)
    old_ref = _get_slot_ref(current, slot)
    next_doc = _set_slot_ref(current or {}, slot, None)
    await _replace_branch_document(db, bid, next_doc, user)
    global_design = await get_print_design_internal(db)
    await _delete_branch_only_asset(db, old_ref, global_design, slot, bid)
    return await get_branch_design_view(db, bid)


async def load_branch_slot_bytes(db, branch_id: str, slot: str) -> Tuple[bytes, str, str]:
    bid = await assert_known_branch_id(db, branch_id)
    slot = str(slot or "").strip()
    if slot not in ASSET_SLOTS:
        raise HTTPException(400, "Slot asset tidak valid.")
    raw = await _load_branch_raw(db, bid)
    ref = _get_slot_ref(raw, slot)
    if not ref:
        raise HTTPException(404, "Asset Design Cetakan tidak ditemukan.")
    path = ref["storage_path"]
    rec = await db.files.find_one(
        {"id": ref["file_id"], "kind": FILE_KIND, "is_deleted": {"$ne": True}},
        {"_id": 0},
    )
    if rec and str(rec.get("storage_path") or "") != path:
        path = str(rec.get("storage_path") or path)
    try:
        data, ct = obj_storage.get_object(path)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Asset Design Cetakan tidak ditemukan.") from exc
    except Exception as exc:
        logger.exception("print_design branch get_object failed slot=%s", slot)
        raise HTTPException(502, "Gagal membaca asset Design Cetakan.") from exc
    if not data:
        raise HTTPException(404, "Asset Design Cetakan tidak ditemukan.")
    media = (rec or {}).get("content_type") or ref.get("content_type") or ct or "application/octet-stream"
    name = (rec or {}).get("original_filename") or ref.get("original_filename") or f"{slot}.bin"
    return data, media, name
