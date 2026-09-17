"""POD electronic signatures — recipient + driver/pengirim.

Additive fields on the order document — never inline huge base64 in Mongo.
Signature PNG bytes go to object storage; Mongo keeps metadata + object identity.

Saving a signature alone MUST NOT mark the shipment delivered.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from quantity_rules import (
    MIN_DELIVERY_PROOFS,
    canonical_shipment_id,
    shipment_is_active,
    valid_delivery_proofs,
    valid_delivery_proofs_for_shipment,
)

logger = logging.getLogger(__name__)

MAX_SIGNATURE_BYTES = 1_500_000  # ~1.5MB PNG after canvas export
MIN_SIGNATURE_BYTES = 64
ALLOWED_SIGNATURE_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp"})
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def normalize_recipient_name(raw: Any) -> Optional[str]:
    name = _text(raw)
    if not name:
        return None
    if len(name) > 120:
        name = name[:120].strip()
    return name or None


def validate_recipient_name(raw: Any) -> Optional[str]:
    """Return error message or None if valid."""
    name = normalize_recipient_name(raw)
    if not name:
        return "Nama penerima wajib diisi sebelum Konfirmasi Terkirim."
    if len(name) < 2:
        return "Nama penerima terlalu pendek."
    return None


def sniff_image_type(body: bytes, content_type: Optional[str] = None) -> Optional[str]:
    if not body:
        return None
    if body.startswith(PNG_MAGIC):
        return "image/png"
    if body.startswith(JPEG_MAGIC):
        return "image/jpeg"
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct in ALLOWED_SIGNATURE_TYPES:
        if ct == "image/jpg":
            return "image/jpeg"
        return ct
    return None


def validate_signature_bytes(
    body: bytes,
    content_type: Optional[str] = None,
    *,
    empty_message: str = "Tanda tangan kosong.",
) -> Tuple[Optional[str], Optional[str]]:
    """Return (media_type, error). error is None when valid."""
    if not body:
        return None, empty_message
    if len(body) < MIN_SIGNATURE_BYTES:
        return None, "Tanda tangan tidak valid (terlalu kecil)."
    if len(body) > MAX_SIGNATURE_BYTES:
        return None, "Tanda tangan terlalu besar (maks 1.5MB)."
    media = sniff_image_type(body, content_type)
    if not media:
        return None, "Tanda tangan harus berupa gambar PNG/JPEG."
    return media, None


def normalize_signature_image_for_pdf(body: bytes) -> Optional[bytes]:
    """Convert signature bytes to opaque RGB PNG ReportLab/PIL can embed reliably.

    Canvas exports are often RGBA with transparency; ReportLab may fail or render blank.
    Failures return None so callers can surface a clear error instead of blank lines.
    Trims near-white margins so strokes sit tightly above the name on the PDF.
    """
    if not body:
        return None
    try:
        from PIL import Image as PILImage
        from PIL import ImageOps
    except Exception:
        logger.warning("PIL unavailable for signature normalize")
        return body if body.startswith(PNG_MAGIC) or body.startswith(JPEG_MAGIC) else None
    try:
        im = PILImage.open(io.BytesIO(body))
        im.load()
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            background = PILImage.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            im = background
        else:
            im = im.convert("RGB")
        # Trim empty canvas margins (common with square signature pads)
        gray = ImageOps.invert(im.convert("L"))
        bbox = gray.point(lambda p: 255 if p > 12 else 0).getbbox()
        if bbox:
            pad = 6
            left = max(0, bbox[0] - pad)
            top = max(0, bbox[1] - pad)
            right = min(im.width, bbox[2] + pad)
            bottom = min(im.height, bbox[3] + pad)
            if right > left and bottom > top:
                im = im.crop((left, top, right, bottom))
        # Ensure a readable footprint on A4 signature cell
        w, h = im.size
        if w < 40 or h < 20:
            # Upscale tiny images so they are not invisible
            scale = max(40 / max(w, 1), 20 / max(h, 1), 1)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), PILImage.NEAREST)
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:
        logger.warning("normalize_signature_image_for_pdf failed", exc_info=True)
        return None


def authoritative_driver_name(order: dict, user: Optional[dict] = None) -> str:
    """Pengirim/Supir display name from shipment assignment — not the login actor.

    Priority:
      1. loading_checklist.driver_name (loading confirmation / pengantar)
      2. latest delivery_confirmations[].driver_name
      3. sales delivery / shipment driver_name when present on order
    Logged-in user is ONLY a last-resort fallback when no shipment driver exists
    (so PDF is not blank). Actor identity belongs in driver_signed_by_user_id.
    """
    lc = order.get("loading_checklist") or {}
    name = _text(lc.get("driver_name"))
    if name:
        return name
    for rec in reversed(order.get("delivery_confirmations") or []):
        if isinstance(rec, dict):
            name = _text(rec.get("driver_name"))
            if name:
                return name
    # Exact sales-delivery / shipment records attached on the order when present
    for ship in order.get("shipments") or []:
        if isinstance(ship, dict):
            name = _text(ship.get("driver_name"))
            if name:
                return name
    sd = order.get("sales_delivery") or order.get("sales_delivery_summary") or {}
    if isinstance(sd, dict):
        name = _text(sd.get("driver_name"))
        if name:
            return name
    if user:
        name = _text(user.get("name"))
        if name:
            return name
    return ""


def resolve_pdf_driver_display_name(
    order: dict,
    driver_sig: Optional[dict] = None,
    delivery: Optional[dict] = None,
    user: Optional[dict] = None,
) -> str:
    """Name printed under Pengirim/Supir — shipment authoritative wins over actor/sig drift."""
    auth = authoritative_driver_name(order, user=None)
    if auth:
        return auth
    for src in (
        (driver_sig or {}).get("driver_name"),
        (delivery or {}).get("driver_name"),
    ):
        name = _text(src)
        if name:
            return name
    return authoritative_driver_name(order, user)


def get_active_signature(order: dict) -> Optional[dict]:
    sig = order.get("delivery_recipient_signature")
    if isinstance(sig, dict) and sig.get("id") and not sig.get("frozen"):
        return sig
    return None


def get_active_driver_signature(order: dict) -> Optional[dict]:
    sig = order.get("delivery_driver_signature")
    if isinstance(sig, dict) and sig.get("id") and not sig.get("frozen"):
        return sig
    return None


def get_frozen_signatures(order: dict) -> List[dict]:
    out = []
    hist = order.get("delivery_recipient_signatures") or []
    if isinstance(hist, list):
        for s in hist:
            if isinstance(s, dict) and s.get("id"):
                out.append(s)
    active = order.get("delivery_recipient_signature")
    if isinstance(active, dict) and active.get("id") and active.get("frozen"):
        if not any(str(x.get("id")) == str(active.get("id")) for x in out):
            out.append(active)
    return out


def get_frozen_driver_signatures(order: dict) -> List[dict]:
    out = []
    hist = order.get("delivery_driver_signatures") or []
    if isinstance(hist, list):
        for s in hist:
            if isinstance(s, dict) and s.get("id"):
                out.append(s)
    active = order.get("delivery_driver_signature")
    if isinstance(active, dict) and active.get("id") and active.get("frozen"):
        if not any(str(x.get("id")) == str(active.get("id")) for x in out):
            out.append(active)
    return out


def _signature_matches_shipment(sig: Optional[dict], shipment: Optional[dict]) -> bool:
    if not isinstance(sig, dict):
        return False
    if not isinstance(shipment, dict):
        return True
    expected = _text(shipment.get("id"))
    actual = _text(sig.get("shipment_id"))
    return bool(expected and actual == expected)


def has_valid_active_signature(order: dict, shipment: Optional[dict] = None) -> bool:
    sig = get_active_signature(order)
    if not sig:
        return False
    if not _signature_matches_shipment(sig, shipment):
        return False
    if not normalize_recipient_name(sig.get("recipient_name")):
        return False
    path = _text(sig.get("signature_storage_path"))
    fid = _text(sig.get("signature_file_id") or sig.get("id"))
    return bool(path and fid)


def has_valid_active_driver_signature(order: dict, shipment: Optional[dict] = None) -> bool:
    sig = get_active_driver_signature(order)
    if not sig:
        return False
    if not _signature_matches_shipment(sig, shipment):
        return False
    path = _text(sig.get("signature_storage_path") or sig.get("driver_signature_storage_path"))
    fid = _text(sig.get("signature_file_id") or sig.get("driver_signature_file_id") or sig.get("id"))
    return bool(path and fid)


def apply_recipient_name_to_active_signature(order: dict, raw: Any) -> Optional[str]:
    """Bind the latest trimmed recipient name onto the active POD signature.

    Returns a validation error string, or None when the name is valid and applied
    (or when there is no active signature — the signature gate owns that error).
    Does not create a signature and does not accept a blank name.
    """
    name_err = validate_recipient_name(raw)
    if name_err:
        return name_err
    sig = get_active_signature(order)
    if not isinstance(sig, dict):
        return None
    sig["recipient_name"] = normalize_recipient_name(raw)
    order["delivery_recipient_signature"] = sig
    return None


def signature_confirm_block_reason(order: dict, shipment: Optional[dict] = None) -> Optional[str]:
    """Extra gates after the existing 2-photo rule. Legacy delivered docs are not blocked here."""
    n = len(valid_delivery_proofs_for_shipment(order, shipment))
    if n < MIN_DELIVERY_PROOFS:
        return None  # photo gate owns this message
    sig = get_active_signature(order)
    driver_sig = get_active_driver_signature(order)
    if not sig or not _signature_matches_shipment(sig, shipment):
        return "Tanda tangan penerima wajib sebelum Konfirmasi Terkirim."
    name_err = validate_recipient_name(sig.get("recipient_name"))
    if name_err:
        return name_err
    if not has_valid_active_signature(order, shipment):
        return "Tanda tangan penerima wajib sebelum Konfirmasi Terkirim."
    if not driver_sig or not has_valid_active_driver_signature(order, shipment):
        return "Tanda tangan driver/pengirim wajib sebelum Konfirmasi Terkirim."
    return None


def resolve_shipment_and_sj(order: dict) -> Tuple[Optional[str], Optional[str]]:
    """Pick exact shipment + surat_jalan file for the current unused proofs."""
    from services.surat_jalan_files import find_generated_sj_file, file_entries

    used = set()
    for rec in order.get("delivery_confirmations") or []:
        for pid in (rec.get("proof_ids") or []):
            s = str(pid or "").strip()
            if s:
                used.add(s)

    sid = None
    for p in reversed(valid_delivery_proofs(order)):
        pid = str(p.get("id") or "").strip()
        if pid and pid in used:
            continue
        psid = _text(p.get("shipment_id"))
        if psid:
            sid = psid
            break
    if not sid:
        for p in reversed(valid_delivery_proofs(order)):
            psid = _text(p.get("shipment_id"))
            if psid:
                sid = psid
                break
    if not sid:
        ships = order.get("shipments") or []
        if ships and isinstance(ships[-1], dict):
            sid = _text(ships[-1].get("id"))

    sj_id = None
    if sid:
        gen = find_generated_sj_file(order, sid)
        if gen and gen.get("id"):
            sj_id = str(gen.get("id"))
        else:
            for f in file_entries(order):
                if str(f.get("shipment_id") or "") == str(sid) and f.get("id"):
                    sj_id = str(f.get("id"))
                    break
    if not sj_id:
        files = file_entries(order)
        if files and files[-1].get("id"):
            sj_id = str(files[-1].get("id"))
    return sid, sj_id


def resolve_requested_signature_identity(
    order: dict,
    *,
    shipment_id: Optional[str] = None,
    sj_file_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve and validate the exact active SJ identity for a new signature.

    Client-provided UUIDs are never trusted blindly.  The selected shipment
    must be active on this order and the SJ file must belong to that same
    shipment, preventing a signature from being attached across sibling SJs.
    """
    from services.surat_jalan_files import find_generated_sj_file, file_entries

    fallback_sid, _fallback_file_id = resolve_shipment_and_sj(order)
    requested_sid = _text(shipment_id) or fallback_sid
    if not requested_sid:
        return None, None, "Surat Jalan aktif wajib dipilih sebelum tanda tangan."

    matches = [
        sh for sh in order.get("shipments") or []
        if isinstance(sh, dict)
        and shipment_is_active(sh)
        and canonical_shipment_id(sh) == requested_sid
    ]
    if len(matches) != 1:
        return None, None, "Surat Jalan tidak valid atau sudah tidak aktif."

    generated = find_generated_sj_file(order, requested_sid)
    candidates = []
    if isinstance(generated, dict) and generated.get("id"):
        candidates.append(generated)
    for row in file_entries(order):
        if (
            isinstance(row, dict)
            and str(row.get("shipment_id") or "") == requested_sid
            and row.get("id")
            and not any(str(item.get("id")) == str(row.get("id")) for item in candidates)
        ):
            candidates.append(row)
    if len(candidates) != 1:
        return None, None, "File Surat Jalan kanonis tidak ditemukan."

    canonical_file_id = str(candidates[0]["id"])
    requested_file_id = _text(sj_file_id)
    if requested_file_id and requested_file_id != canonical_file_id:
        return None, None, "File Surat Jalan tidak sesuai dengan SJ yang dipilih."
    return requested_sid, canonical_file_id, None


def build_signature_record(
    *,
    recipient_name: str,
    signature_file_id: str,
    storage_path: str,
    content_type: str,
    size: int,
    user: dict,
    shipment_id: Optional[str],
    sj_file_id: Optional[str],
    signed_at: Optional[str] = None,
) -> dict:
    actor_id = _text(user.get("user_id") or user.get("sub") or user.get("id"))
    return {
        "id": signature_file_id,
        "signature_file_id": signature_file_id,
        "recipient_name": normalize_recipient_name(recipient_name),
        "signature_storage_path": storage_path,
        "content_type": content_type,
        "size": size,
        "recipient_signed_at": signed_at or now_iso(),
        "signed_by_driver_user_id": actor_id,
        "signed_by_driver_email": _text(user.get("email")),
        "signed_by_driver_name": _text(user.get("name")),
        "shipment_id": shipment_id,
        "sj_file_id": sj_file_id,
        "delivery_note_id": sj_file_id,
        "frozen": False,
        "signed_sj_storage_path": None,
        "signed_sj_generated_at": None,
    }


def build_driver_signature_record(
    *,
    signature_file_id: str,
    storage_path: str,
    content_type: str,
    size: int,
    user: dict,
    order: dict,
    shipment_id: Optional[str],
    sj_file_id: Optional[str],
    signed_at: Optional[str] = None,
    driver_name: Optional[str] = None,
) -> dict:
    actor_id = _text(user.get("user_id") or user.get("sub") or user.get("id"))
    # Never let client/admin-supplied name override shipment-authoritative driver.
    auth = authoritative_driver_name(order, user=None)
    name = auth or _text(driver_name) or authoritative_driver_name(order, user)
    return {
        "id": signature_file_id,
        "driver_signature_file_id": signature_file_id,
        "signature_file_id": signature_file_id,
        "driver_signature_storage_path": storage_path,
        "signature_storage_path": storage_path,
        "content_type": content_type,
        "size": size,
        "driver_signed_at": signed_at or now_iso(),
        "driver_signed_by_user_id": actor_id,
        "driver_user_id": actor_id,
        "driver_signed_by_name": _text(user.get("name")),
        "driver_signed_by_email": _text(user.get("email")),
        "driver_name": name,
        "driver_email": _text(user.get("email")),
        "shipment_id": shipment_id,
        "sj_file_id": sj_file_id,
        "delivery_note_id": sj_file_id,
        "frozen": False,
    }


def freeze_active_signature(order: dict, *, confirmation_id: Optional[str] = None) -> Optional[dict]:
    """Freeze active recipient signature into history. Does not change delivery status."""
    sig = get_active_signature(order)
    if not sig:
        return None
    frozen = dict(sig)
    frozen["frozen"] = True
    frozen["confirmation_id"] = confirmation_id
    order["delivery_recipient_signature"] = frozen
    hist = list(order.get("delivery_recipient_signatures") or [])
    hist = [h for h in hist if str(h.get("id")) != str(frozen.get("id"))]
    hist.append(frozen)
    order["delivery_recipient_signatures"] = hist[-50:]
    return frozen


def freeze_active_driver_signature(order: dict, *, confirmation_id: Optional[str] = None) -> Optional[dict]:
    sig = get_active_driver_signature(order)
    if not sig:
        return None
    frozen = dict(sig)
    frozen["frozen"] = True
    frozen["confirmation_id"] = confirmation_id
    order["delivery_driver_signature"] = frozen
    hist = list(order.get("delivery_driver_signatures") or [])
    hist = [h for h in hist if str(h.get("id")) != str(frozen.get("id"))]
    hist.append(frozen)
    order["delivery_driver_signatures"] = hist[-50:]
    return frozen


def clear_active_signature(order: dict) -> bool:
    """Clear mutable recipient signature before confirm. Rejects if frozen."""
    sig = order.get("delivery_recipient_signature")
    if not isinstance(sig, dict):
        order.pop("delivery_recipient_signature", None)
        return True
    if sig.get("frozen"):
        return False
    order.pop("delivery_recipient_signature", None)
    return True


def clear_active_driver_signature(order: dict) -> bool:
    sig = order.get("delivery_driver_signature")
    if not isinstance(sig, dict):
        order.pop("delivery_driver_signature", None)
        return True
    if sig.get("frozen"):
        return False
    order.pop("delivery_driver_signature", None)
    return True


def find_signature_by_id(order: dict, sig_id: str) -> Optional[dict]:
    want = _text(sig_id)
    if not want:
        return None
    active = order.get("delivery_recipient_signature")
    if isinstance(active, dict) and str(active.get("id") or "") == want:
        return active
    for s in get_frozen_signatures(order):
        if str(s.get("id") or "") == want:
            return s
    return None


def find_driver_signature_by_id(order: dict, sig_id: str) -> Optional[dict]:
    want = _text(sig_id)
    if not want:
        return None
    active = order.get("delivery_driver_signature")
    if isinstance(active, dict) and str(active.get("id") or "") == want:
        return active
    for s in get_frozen_driver_signatures(order):
        if str(s.get("id") or "") == want:
            return s
    return None


def public_signature_view(sig: dict, token: str) -> dict:
    """Safe public fields — proxy URL only, no storage path."""
    sid = str(sig.get("id") or "")
    return {
        "id": sid,
        "recipient_name": sig.get("recipient_name") or "",
        "recipient_signed_at": sig.get("recipient_signed_at") or "",
        "shipment_id": sig.get("shipment_id") or None,
        "sj_file_id": sig.get("sj_file_id") or sig.get("delivery_note_id") or None,
        "url": f"/api/public/tracking/{token}/signatures/{sid}",
        "has_signed_sj": bool(_text(sig.get("signed_sj_storage_path"))),
    }


def public_driver_signature_view(sig: dict, token: str) -> dict:
    sid = str(sig.get("id") or "")
    return {
        "id": sid,
        "driver_name": sig.get("driver_name") or "",
        "driver_signed_at": sig.get("driver_signed_at") or "",
        "shipment_id": sig.get("shipment_id") or None,
        "sj_file_id": sig.get("sj_file_id") or sig.get("delivery_note_id") or None,
        "url": f"/api/public/tracking/{token}/driver-signatures/{sid}",
    }


def decode_data_url_png(data_url: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Decode data:image/png;base64,... → bytes. Reject non-image."""
    raw = (data_url or "").strip()
    if not raw.startswith("data:"):
        return None, "Payload tanda tangan tidak valid."
    try:
        header, b64 = raw.split(",", 1)
    except ValueError:
        return None, "Payload tanda tangan tidak valid."
    meta = header[5:]  # after data:
    ct = meta.split(";")[0].lower() if meta else ""
    if "base64" not in meta.lower():
        return None, "Payload tanda tangan harus base64."
    import base64
    try:
        body = base64.b64decode(b64, validate=False)
    except Exception:
        return None, "Payload tanda tangan tidak dapat dibaca."
    media, err = validate_signature_bytes(body, ct or "image/png")
    if err:
        return None, err
    return body, media


def storage_key_for_signature(po_number: str, file_id: str, ext: str = "png") -> str:
    safe_po = re.sub(r"[^A-Za-z0-9._-]+", "_", (po_number or "po").strip()) or "po"
    safe_ext = re.sub(r"[^a-z0-9]+", "", (ext or "png").lower()) or "png"
    return f"eternal-packaging/delivery-signature/{safe_po}/{file_id}.{safe_ext}"


def storage_key_for_driver_signature(po_number: str, file_id: str, ext: str = "png") -> str:
    safe_po = re.sub(r"[^A-Za-z0-9._-]+", "_", (po_number or "po").strip()) or "po"
    safe_ext = re.sub(r"[^a-z0-9]+", "", (ext or "png").lower()) or "png"
    return f"eternal-packaging/delivery-driver-signature/{safe_po}/{file_id}.{safe_ext}"


def storage_key_for_signed_sj(po_id: str, sj_file_id: str) -> str:
    safe_po = re.sub(r"[^A-Za-z0-9._-]+", "_", (po_id or "po").strip()) or "po"
    safe_sj = re.sub(r"[^A-Za-z0-9._-]+", "_", (sj_file_id or "sj").strip()) or "sj"
    return f"eternal-packaging/surat-jalan/{safe_po}/{safe_sj}-signed.pdf"
