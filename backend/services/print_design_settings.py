"""Design Cetakan settings (app_settings key=design_cetakan).

Presentation-only letterhead for Eternal sales PDFs.
Defaults MUST match the deployed hardcoded PDF constants so an empty
settings document leaves PDF output unchanged.

Not cash_bank. Not logo/signature upload (phase 1 text only).
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services import pdf_layout as layout

SETTINGS_KEY = "design_cetakan"
PERM_VIEW = "settings.design_cetakan.view"
PERM_MANAGE = "settings.design_cetakan.manage"

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_MAX_TEXT = 200
_MAX_LINE = 240
_MAX_ADDRESS_LINES = 6
_MAX_ESTIMASI_LINES = 8
_MAX_BANKS = 4


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
        "po": {
            "signature_labels": ["Operator", "Pengiriman", "Penerima"],
            "footer_name_label": "Nama",
        },
        "invoice": {
            "bank_accounts": default_bank_accounts(),
            "estimasi_title": layout.ESTIMASI_TITLE,
            "estimasi_lines": list(layout.ESTIMASI_LINES),
            "prepared_label": "Disiapkan Oleh",
            "prepared_footer": "Admin",
            "approved_label": "Disetujui Oleh",
            "approved_footer": "Pengelola",
        },
        "surat_jalan": {
            "signature_labels": ["Gudang", "Cek Barang", "Kurir", "Penerima"],
            "date_label": "Tgl.",
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
    sj_raw = doc.get("surat_jalan") if isinstance(doc.get("surat_jalan"), dict) else {}

    po = {
        "signature_labels": _labels(po_raw.get("signature_labels"), base["po"]["signature_labels"]),
        "footer_name_label": _clip(po_raw.get("footer_name_label")) or base["po"]["footer_name_label"],
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
    }
    surat_jalan = {
        "signature_labels": _labels(
            sj_raw.get("signature_labels"),
            base["surat_jalan"]["signature_labels"],
        ),
        "date_label": _clip(sj_raw.get("date_label"), 32) or base["surat_jalan"]["date_label"],
    }

    return {
        "key": SETTINGS_KEY,
        "company_name": company,
        "address_lines": address,
        "accent_color": accent,
        "po": po,
        "invoice": invoice,
        "surat_jalan": surat_jalan,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


def public_settings(doc: Optional[dict] = None) -> Dict[str, Any]:
    return resolve_print_design(doc)


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

    resolved = resolve_print_design(payload)
    # Drop meta from client; server sets on save.
    resolved["updated_at"] = None
    resolved["updated_by"] = None
    return resolved


async def get_print_design_settings(db) -> Dict[str, Any]:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return public_settings(doc)


async def save_print_design_settings(db, payload: Dict[str, Any], user: Optional[dict] = None) -> Dict[str, Any]:
    cleaned = _validate_put_payload(payload)
    actor = None
    if isinstance(user, dict):
        actor = {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": user.get("name") or user.get("email") or "User",
            "email": user.get("email") or "",
            "role": user.get("role") or "",
        }
    saved = {
        **cleaned,
        "key": SETTINGS_KEY,
        "updated_at": now_iso(),
        "updated_by": actor,
    }
    await db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": saved},
        upsert=True,
    )
    return public_settings(saved)
