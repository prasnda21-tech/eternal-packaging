"""Customer Design Approval — secure external link, signature, hour-based expiry.

Terintegrasi dengan Approval Design existing:
  - Sukses customer approve → order.design_status = "Approved" + history entry.
  - Customer reject → design tetap Waiting + reject reason ditampilkan di internal.
  - Re-upload design → link customer aktif otomatis di-invalidate (versi berubah).

Data model:
  Collection `app_settings`:
    { key: "customer_approval", expiry_hours, allow_approve, allow_reject,
      signature_enabled, signature_required_approve, signature_required_reject,
      reject_reason_required, updated_by, updated_by_name, updated_at }
  Collection `customer_approvals`:
    { id, order_id, po_number, design_version, token_hash, status,
      generated_by, generated_by_name, generated_at, expires_at, expiry_hours,
      response, signer_name, signature_storage_path, customer_note, responded_at,
      revoked_by, revoked_by_name, revoked_at, revoke_reason }
  Order doc gains additive fields:
    customer_approval (snapshot pointer), customer_approval_history[]

Token: `secrets.token_urlsafe(32)` (43 chars). Hanya SHA-256 hash yang disimpan
di DB — plaintext tidak pernah disimpan (tidak di audit log juga).
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field


# ---- Settings ----
DEFAULT_SETTINGS = {
    "expiry_hours": 4,
    "allow_approve": True,
    "allow_reject": True,
    "signature_enabled": True,
    "signature_required_approve": True,
    "signature_required_reject": False,
    "reject_reason_required": True,
}

VALID_EXPIRY_HOUR_CHOICES = [1, 2, 4, 6, 12, 24]
MAX_CUSTOM_HOURS = 24 * 7  # sanity cap: 1 minggu


def _now():
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


PUBLIC_DESIGN_VIEW_STATUSES = ("active", "approved", "rejected")


def public_design_file_path(token: str) -> str:
    """Token-scoped design bytes. No file id — only the bound design_version."""
    return f"/api/public/customer-approval/{token}/design"


def bound_design_file(rec: dict, order: dict) -> Optional[dict]:
    design_version = rec.get("design_version")
    if not design_version:
        return None
    for file in order.get("design_files") or []:
        if isinstance(file, dict) and file.get("id") == design_version:
            return file
    return None


def current_design_pdf_file(order: dict) -> Optional[dict]:
    """Resolve the authoritative current Design PDF file.

    The authoritative current design is the latest design attachment in order.design_files.
    It must exist and be an active PDF attachment.
    """
    files = [f for f in (order.get("design_files") or []) if isinstance(f, dict)]
    if not files:
        return None
    latest = files[-1]
    if is_pdf_design(latest):
        return latest
    return None



def design_content_type(file: dict) -> Optional[str]:
    ct = file.get("content_type")
    if ct:
        return str(ct).lower()
    data_url = str(file.get("data_url") or "")
    if data_url.startswith("data:"):
        try:
            return data_url.split("data:", 1)[1].split(";", 1)[0].lower()
        except Exception:
            return None
    return None


def is_pdf_design(file: dict, ct: Optional[str] = None) -> bool:
    media = (ct or design_content_type(file) or "").lower()
    name = str(file.get("name") or "").lower()
    data_url = str(file.get("data_url") or "")
    return media == "application/pdf" or name.endswith(".pdf") or data_url.startswith("data:application/pdf")


def public_design_media_type(file: dict, data: bytes, claimed: str) -> str:
    """Preview-safe MIME for this approval design path only."""
    name = str(file.get("name") or "").lower()
    claimed = (claimed or "").lower()
    if claimed == "application/pdf" or name.endswith(".pdf") or (data or b"").startswith(b"%PDF"):
        return "application/pdf"
    return claimed or "application/octet-stream"


def load_bound_design_bytes(file: dict, obj_storage=None) -> Tuple[bytes, str]:
    data = None
    media = design_content_type(file) or "application/octet-stream"
    storage_path = str(file.get("storage_path") or "").strip()
    if storage_path and obj_storage is not None:
        try:
            data, ct = obj_storage.get_object(storage_path)
            media = file.get("content_type") or ct or media
        except FileNotFoundError:
            data = None
        except Exception:
            data = None
    if data is None:
        data_url = str(file.get("data_url") or "")
        if data_url.startswith("data:"):
            header, b64 = data_url.split(",", 1)
            if "data:" in header:
                media = header.split("data:", 1)[1].split(";", 1)[0] or media
            data = base64.b64decode(b64)
    if not data:
        raise FileNotFoundError("design file bytes unavailable")
    return data, public_design_media_type(file, data, media)


def _parse_byte_range(range_header: str, total_len: int) -> Optional[Tuple[int, int]]:
    if not range_header or not range_header.startswith("bytes="):
        return None
    val = range_header.split("bytes=", 1)[1].strip()
    if "," in val:
        return None
    parts = val.split("-")
    if len(parts) != 2:
        return None
    start_str, end_str = parts[0].strip(), parts[1].strip()
    if not start_str and not end_str:
        return None
    try:
        if not start_str:
            suffix = int(end_str)
            if suffix <= 0:
                return None
            start = max(0, total_len - suffix)
            end = total_len - 1
        elif not end_str:
            start = int(start_str)
            end = total_len - 1
        else:
            start = int(start_str)
            end = int(end_str)
        if start > end or start >= total_len or start < 0:
            return None
        end = min(end, total_len - 1)
        return (start, end)
    except ValueError:
        return None


def _design_disposition_headers(filename: str, disposition: str = "inline") -> dict:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "design").strip("._") or "design"
    mode = "attachment" if str(disposition or "").lower() in ("attachment", "download") else "inline"
    return {
        "Content-Disposition": f"{mode}; filename=\"{safe}\"; filename*=UTF-8''{quote(filename or safe)}",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
    }


def _snapshot_for_order(rec: dict) -> dict:
    """Fields disimpan di order.customer_approval untuk display internal.
    JANGAN sertakan token/hash. Signature URL diserve via /api/files/<path>."""
    return {
        "approval_id": rec["id"],
        "status": rec.get("status"),
        "design_version": rec.get("design_version"),
        "generated_at": rec.get("generated_at"),
        "expires_at": rec.get("expires_at"),
        "expiry_hours": rec.get("expiry_hours"),
        "response": rec.get("response"),
        "signer_name": rec.get("signer_name"),
        "signature_storage_path": rec.get("signature_storage_path"),
        "customer_note": rec.get("customer_note"),
        "reject_reason": rec.get("reject_reason"),
        "responded_at": rec.get("responded_at"),
        "revoked_at": rec.get("revoked_at"),
        "revoke_reason": rec.get("revoke_reason"),
    }


def _public_view(rec: dict, order: dict, settings: dict, allow_active_view: bool, token: Optional[str] = None) -> dict:
    """Data safe untuk customer publik. TIDAK mengekspos audit, ID mongo,
    permission, catatan internal, credentials, dsb."""
    line_items = [
        {
            "name": li.get("name"),
            "color": li.get("color"),
            "quantity": li.get("quantity"),
            "unit": li.get("unit"),
        }
        for li in (order.get("line_items") or [])
    ]
    # Ambil hanya file design VERSI YANG DIAPPROVE (bukan seluruh riwayat).
    file = bound_design_file(rec, order)
    design_file_view = None
    if file:
        ct = design_content_type(file)
        data_url = None
        if allow_active_view and not is_pdf_design(file, ct):
            data_url = file.get("data_url")
        view_url = public_design_file_path(token) if (allow_active_view and token) else None
        design_file_view = {
            "id": file.get("id"),
            "name": file.get("name"),
            "data_url": data_url,
            "view_url": view_url,
            "content_type": ct,
        }
    return {
        "status": rec.get("status"),
        "po_number": order.get("po_number"),
        "customer": order.get("customer"),
        "outlet": order.get("outlet"),
        "line_items": line_items,
        "design_file": design_file_view,
        "generated_at": rec.get("generated_at"),
        "expires_at": rec.get("expires_at"),
        "expiry_hours": rec.get("expiry_hours"),
        "settings": {
            "allow_approve": bool(settings.get("allow_approve", True)),
            "allow_reject": bool(settings.get("allow_reject", True)),
            "signature_enabled": bool(settings.get("signature_enabled", True)),
            "signature_required_approve": bool(settings.get("signature_required_approve", True)),
            "signature_required_reject": bool(settings.get("signature_required_reject", False)),
            "reject_reason_required": bool(settings.get("reject_reason_required", True)),
        },
        "response": rec.get("response"),
        "signer_name": rec.get("signer_name"),
        "customer_note": rec.get("customer_note"),
        "reject_reason": rec.get("reject_reason"),
        "responded_at": rec.get("responded_at"),
    }


# ---- Payloads ----
class SettingsPayload(BaseModel):
    expiry_hours: int = Field(default=4, ge=1, le=MAX_CUSTOM_HOURS)
    allow_approve: bool = True
    allow_reject: bool = True
    signature_enabled: bool = True
    signature_required_approve: bool = True
    signature_required_reject: bool = False
    reject_reason_required: bool = True


class GeneratePayload(BaseModel):
    expiry_hours: Optional[int] = None  # opsional override; default pakai settings


class RevokePayload(BaseModel):
    reason: Optional[str] = None


class PublicSubmitPayload(BaseModel):
    action: str = Field(..., description="approve | reject")
    signer_name: Optional[str] = None
    signature_data_url: Optional[str] = None
    note: Optional[str] = None


# ---- Router factory ----
def build_router(deps):
    """Register endpoints. `deps` menyediakan hook ke server.py:

      deps = {
        "db": motor db,
        "current_user": FastAPI Depends,
        "require_perm": callable(user, perm),  # coroutine yang raise 403 kalau invalid
        "persist_order": callable(order),  # coroutine
        "audit_po": callable(order, action, actor, before, after, note),
        "add_timeline": callable(order, stage, user, note),
        "obj_storage": module,
        "logger": logging.Logger,
      }
    """
    db = deps["db"]
    current_user = deps["current_user"]
    require_perm = deps["require_perm"]
    persist_order = deps["persist_order"]
    audit_po = deps["audit_po"]
    add_timeline = deps["add_timeline"]
    obj_storage = deps["obj_storage"]
    logger = deps["logger"]

    router = APIRouter(prefix="/api", tags=["customer-approval"])

    # ------ Helpers scoped to router ------
    async def get_settings() -> dict:
        doc = await db.app_settings.find_one({"key": "customer_approval"}, {"_id": 0})
        merged = {**DEFAULT_SETTINGS}
        if doc:
            for k in DEFAULT_SETTINGS:
                if k in doc:
                    merged[k] = doc[k]
        return merged

    def _derive_status(rec: dict) -> str:
        """Menghitung status effective dari record + waktu sekarang.
        JANGAN mutate rec; caller yang persist."""
        st = rec.get("status") or "active"
        if st in ("approved", "rejected", "revoked", "invalidated"):
            return st
        # Active → cek expiry
        try:
            exp = datetime.fromisoformat(rec["expires_at"])
        except Exception:
            return st
        if _now() >= exp:
            return "expired"
        return st

    async def _mark_status_if_changed(rec: dict, new_status: str, extra: Optional[dict] = None):
        if rec.get("status") == new_status:
            return rec
        patch = {"status": new_status, **(extra or {})}
        await db.customer_approvals.update_one({"id": rec["id"]}, {"$set": patch})
        rec.update(patch)
        # Update snapshot on order
        order = await db.orders.find_one({"id": rec["order_id"]}, {"_id": 0})
        if order:
            order["customer_approval"] = _snapshot_for_order(rec)
            order.setdefault("customer_approval_history", []).append({
                "action": f"status.{new_status}",
                "at": _iso(_now()),
                "approval_id": rec["id"],
                "design_version": rec.get("design_version"),
                "note": (extra or {}).get("note") or "",
            })
            await persist_order(order)
        return rec

    async def _load_active_by_order(order_id: str) -> Optional[dict]:
        return await db.customer_approvals.find_one(
            {"order_id": order_id, "status": "active"},
            {"_id": 0},
        )

    async def _load_latest_by_order(order_id: str) -> Optional[dict]:
        try:
            ret = await db.customer_approvals.find_one(
                {"order_id": order_id},
                {"_id": 0},
                sort=[("generated_at", -1)],
            )
            if ret:
                return ret
        except TypeError:
            pass
        except Exception:
            pass
        try:
            cursor = db.customer_approvals.find({"order_id": order_id}, {"_id": 0}).sort("generated_at", -1).limit(1)
            docs = await cursor.to_list(1)
            return docs[0] if docs else None
        except Exception:
            return None


    # ------ Internal endpoints ------
    @router.get("/customer-approval/settings")
    async def read_settings(user=Depends(current_user)):
        await require_perm(user, "customer_approval.view")
        return {"settings": await get_settings(), "expiry_choices": VALID_EXPIRY_HOUR_CHOICES}

    @router.put("/customer-approval/settings")
    async def write_settings(payload: SettingsPayload, user=Depends(current_user)):
        await require_perm(user, "customer_approval.settings")
        data = payload.model_dump()
        data["updated_by"] = user.get("email")
        data["updated_by_name"] = user.get("name")
        data["updated_at"] = _iso(_now())
        data["key"] = "customer_approval"
        await db.app_settings.update_one(
            {"key": "customer_approval"}, {"$set": data}, upsert=True,
        )
        await audit_po(
            {"po_number": "-"}, "customer_approval.settings.update", user,
            after={k: v for k, v in data.items() if k != "key"}, note="",
        )
        return {"settings": await get_settings()}

    @router.post("/orders/{order_id}/customer-approval/generate")
    async def generate_link(order_id: str, payload: GeneratePayload, user=Depends(current_user)):
        await require_perm(user, "customer_approval.manage")
        order = await db.orders.find_one({"id": order_id}, {"_id": 0})
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")
        if order.get("cancelled"):
            raise HTTPException(400, "PO dibatalkan — tidak dapat membuat link approval")
        current_pdf = current_design_pdf_file(order)
        if not current_pdf:
            raise HTTPException(400, "File Design PDF aktif belum tersedia.")
        current_version = current_pdf.get("id")
        if not current_version:
            raise HTTPException(500, "Attachment design tidak memiliki id")

        settings = await get_settings()
        expiry_hours = payload.expiry_hours if payload.expiry_hours else settings["expiry_hours"]
        if expiry_hours < 1 or expiry_hours > MAX_CUSTOM_HOURS:
            raise HTTPException(400, f"expiry_hours harus 1-{MAX_CUSTOM_HOURS}")

        prior = await _load_active_by_order(order_id)
        prior_action = "regenerated" if prior else "generated"

        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        now = _now()
        rec = {
            "id": str(uuid.uuid4()),
            "order_id": order_id,
            "po_number": order.get("po_number"),
            "design_version": current_version,
            "token_hash": token_hash,
            "status": "active",
            "generated_by": user.get("email"),
            "generated_by_name": user.get("name"),
            "generated_at": _iso(now),
            "expires_at": _iso(now + timedelta(hours=expiry_hours)),
            "expiry_hours": expiry_hours,
            "response": None,
            "signer_name": None,
            "signature_storage_path": None,
            "customer_note": None,
            "reject_reason": None,
            "responded_at": None,
            "revoked_by": None,
            "revoked_by_name": None,
            "revoked_at": None,
            "revoke_reason": None,
        }
        await db.customer_approvals.insert_one(dict(rec))

        # Atomic revocation of prior active link(s) only after new link insertion succeeds
        if prior:
            await db.customer_approvals.update_many(
                {"order_id": order_id, "id": {"$ne": rec["id"]}, "status": "active"},
                {"$set": {
                    "status": "revoked",
                    "revoked_by": user.get("email"),
                    "revoked_by_name": user.get("name"),
                    "revoked_at": _iso(now),
                    "revoke_reason": "Digantikan oleh link approval design terbaru",
                }},
            )

        # Update order snapshot + history
        order["customer_approval"] = _snapshot_for_order(rec)
        order.setdefault("customer_approval_history", []).append({
            "action": prior_action,  # "generated" | "regenerated"
            "by_email": user.get("email"),
            "by_name": user.get("name"),
            "at": _iso(now),
            "approval_id": rec["id"],
            "design_version": current_version,
            "expiry_hours": expiry_hours,
        })
        await persist_order(order)
        await audit_po(order, f"customer_approval.{prior_action}", user,
                       after={"approval_id": rec["id"], "expiry_hours": expiry_hours, "design_version": current_version},
                       note="")
        # Return plaintext token HANYA sekali untuk staff internal (staff kirim manual).
        return {"approval": _snapshot_for_order(rec), "token": token, "order": order}


    @router.post("/orders/{order_id}/customer-approval/revoke")
    async def revoke_link(order_id: str, payload: RevokePayload, user=Depends(current_user)):
        await require_perm(user, "customer_approval.manage")
        order = await db.orders.find_one({"id": order_id}, {"_id": 0})
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")
        rec = await _load_active_by_order(order_id)
        if not rec:
            raise HTTPException(400, "Tidak ada link approval aktif untuk PO ini")
        reason = (payload.reason or "").strip() or None
        await db.customer_approvals.update_one(
            {"id": rec["id"]},
            {"$set": {
                "status": "revoked",
                "revoked_by": user.get("email"),
                "revoked_by_name": user.get("name"),
                "revoked_at": _iso(_now()),
                "revoke_reason": reason,
            }},
        )
        rec.update({"status": "revoked", "revoked_at": _iso(_now()), "revoke_reason": reason,
                    "revoked_by": user.get("email"), "revoked_by_name": user.get("name")})
        order["customer_approval"] = _snapshot_for_order(rec)
        order.setdefault("customer_approval_history", []).append({
            "action": "revoked",
            "by_email": user.get("email"),
            "by_name": user.get("name"),
            "at": _iso(_now()),
            "approval_id": rec["id"],
            "note": reason or "",
        })
        await persist_order(order)
        await audit_po(order, "customer_approval.revoke", user,
                       after={"approval_id": rec["id"]}, note=reason or "")
        return {"approval": _snapshot_for_order(rec), "order": order}

    # ------ Public endpoints (NO auth) ------
    async def _load_by_token(token: str) -> Optional[dict]:
        if not token:
            return None
        h = _hash_token(token)
        rec = await db.customer_approvals.find_one({"token_hash": h}, {"_id": 0})
        if rec:
            return rec
        # Existing Live Tracking token may open the same approval record.
        # Does not generate a new approval token.
        order = await db.orders.find_one(
            {"tracking_token": token},
            {"_id": 0, "id": 1, "customer_approval": 1},
        )
        if not order:
            return None
        snap = order.get("customer_approval") or {}
        aid = str(snap.get("approval_id") or "").strip()
        if aid:
            by_id = await db.customer_approvals.find_one({"id": aid}, {"_id": 0})
            if by_id:
                return by_id
        return await _load_latest_by_order(order.get("id"))

    @router.get("/public/customer-approval/{token}")
    async def public_view(token: str):
        rec = await _load_by_token(token)
        if not rec:
            raise HTTPException(404, "Link tidak ditemukan atau sudah tidak berlaku")
        # Derive expired
        st = _derive_status(rec)
        if st != rec.get("status"):
            await _mark_status_if_changed(rec, st)

        order = await db.orders.find_one({"id": rec["order_id"]}, {"_id": 0})
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")

        # Check latest approval record for this order — older links are revoked/superseded
        latest = await _load_latest_by_order(rec["order_id"])
        if latest and latest.get("id") != rec.get("id"):
            if rec.get("status") == "active":
                await _mark_status_if_changed(rec, "revoked",
                                              {"note": "Digantikan oleh link approval design terbaru"})

        # Cek design version binding — invalidate jika design sudah diupload ulang.
        files = order.get("design_files") or []
        current_version = files[-1].get("id") if files else None
        if current_version and rec.get("design_version") != current_version and rec.get("status") == "active":
            await _mark_status_if_changed(rec, "invalidated",
                                          {"note": "Design di-upload ulang; link versi lama tidak berlaku"})

        is_latest = not latest or latest.get("id") == rec.get("id")
        settings = await get_settings()
        allow_active_view = is_latest and (rec.get("status") in PUBLIC_DESIGN_VIEW_STATUSES)
        return {"view": _public_view(rec, order, settings, allow_active_view, token=token)}

    @router.api_route("/public/customer-approval/{token}/design", methods=["GET", "HEAD"])
    async def public_design_file(token: str, request: Request, disposition: str = "inline"):
        rec = await _load_by_token(token)
        if not rec:
            raise HTTPException(404, "Link tidak ditemukan atau sudah tidak berlaku")
        st = _derive_status(rec)
        if st != rec.get("status"):
            await _mark_status_if_changed(rec, st)
        if rec.get("status") not in PUBLIC_DESIGN_VIEW_STATUSES:
            raise HTTPException(404, "Link tidak ditemukan atau sudah tidak berlaku")

        # Only the latest approval link for this order may serve a PDF
        latest = await _load_latest_by_order(rec["order_id"])
        if latest and latest.get("id") != rec.get("id"):
            if rec.get("status") == "active":
                await _mark_status_if_changed(rec, "revoked",
                                              {"note": "Digantikan oleh link approval design terbaru"})
            raise HTTPException(404, "Link tidak ditemukan atau sudah tidak berlaku")

        order = await db.orders.find_one({"id": rec["order_id"]}, {"_id": 0})
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")
        files = order.get("design_files") or []
        current_version = files[-1].get("id") if files else None
        if current_version and rec.get("design_version") != current_version and rec.get("status") == "active":
            await _mark_status_if_changed(rec, "invalidated",
                                          {"note": "Design di-upload ulang; link versi lama tidak berlaku"})
            raise HTTPException(404, "Link tidak ditemukan atau sudah tidak berlaku")
        file = bound_design_file(rec, order)
        if not file:
            raise HTTPException(404, "File design tidak ditemukan")
        try:
            data, media = load_bound_design_bytes(file, obj_storage)
        except FileNotFoundError:
            raise HTTPException(404, "File design tidak ditemukan")
        except Exception:
            logger.exception("Public approval design fetch failed")
            raise HTTPException(404, "File design tidak ditemukan")
        filename = file.get("name") or "design.pdf"
        headers = _design_disposition_headers(filename, disposition=disposition)
        total_len = len(data)

        if request.method == "HEAD":
            headers["Content-Length"] = str(total_len)
            return Response(
                content=b"",
                media_type=media,
                headers=headers,
            )

        range_header = request.headers.get("range")
        if range_header:
            parsed_range = _parse_byte_range(range_header, total_len)
            if parsed_range is not None:
                start, end = parsed_range
                chunk_len = end - start + 1
                headers["Content-Range"] = f"bytes {start}-{end}/{total_len}"
                headers["Content-Length"] = str(chunk_len)
                return Response(
                    content=data[start:end + 1],
                    status_code=206,
                    media_type=media,
                    headers=headers,
                )

        headers["Content-Length"] = str(total_len)
        return Response(
            content=data,
            media_type=media,
            headers=headers,
        )

    @router.post("/public/customer-approval/{token}/submit")
    async def public_submit(token: str, payload: PublicSubmitPayload):
        rec = await _load_by_token(token)
        if not rec:
            raise HTTPException(404, "Link tidak ditemukan")
        # Derive expired
        st = _derive_status(rec)
        if st != rec.get("status"):
            await _mark_status_if_changed(rec, st)
            rec["status"] = st

        # Only the latest approval link for this order may accept submissions
        latest = await _load_latest_by_order(rec["order_id"])
        if latest and latest.get("id") != rec.get("id"):
            if rec.get("status") == "active":
                await _mark_status_if_changed(rec, "revoked",
                                              {"note": "Digantikan oleh link approval design terbaru"})
            raise HTTPException(400, "Link approval sudah tidak berlaku")

        # Duplicate protection: sudah completed
        if rec.get("status") in ("approved", "rejected"):
            raise HTTPException(400, "Approval sudah dikirim sebelumnya")
        if rec.get("status") == "revoked":
            raise HTTPException(400, "Link approval sudah dicabut")
        if rec.get("status") == "expired":

            raise HTTPException(400, "Link approval sudah kedaluwarsa")
        if rec.get("status") == "invalidated":
            raise HTTPException(400, "Link approval tidak berlaku karena design telah di-upload ulang")

        order = await db.orders.find_one({"id": rec["order_id"]}, {"_id": 0})
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")
        # Recheck design version binding
        files = order.get("design_files") or []
        current_version = files[-1].get("id") if files else None
        if current_version and rec.get("design_version") != current_version:
            await _mark_status_if_changed(rec, "invalidated",
                                          {"note": "Design di-upload ulang; link tidak berlaku"})
            raise HTTPException(400, "Link approval tidak berlaku karena design telah di-upload ulang")

        settings = await get_settings()
        action = (payload.action or "").strip().lower()
        if action not in ("approve", "reject"):
            raise HTTPException(400, "Aksi tidak valid")
        if action == "approve" and not settings.get("allow_approve", True):
            raise HTTPException(400, "Approve dinonaktifkan oleh admin")
        if action == "reject" and not settings.get("allow_reject", True):
            raise HTTPException(400, "Reject dinonaktifkan oleh admin")

        signer_name = (payload.signer_name or "").strip()
        if not signer_name:
            raise HTTPException(400, "Nama penandatangan wajib diisi")
        if len(signer_name) > 120:
            raise HTTPException(400, "Nama penandatangan terlalu panjang")

        sig_required = (
            action == "approve" and settings.get("signature_required_approve", True)
        ) or (
            action == "reject" and settings.get("signature_required_reject", False)
        )
        if sig_required and settings.get("signature_enabled", True) and not payload.signature_data_url:
            raise HTTPException(400, "Tanda tangan wajib diisi")
        if action == "reject" and settings.get("reject_reason_required", True):
            if not (payload.note or "").strip():
                raise HTTPException(400, "Alasan reject wajib diisi")

        # Upload signature ke object storage (kalau ada).
        signature_storage_path = None
        if payload.signature_data_url and settings.get("signature_enabled", True):
            data_url = payload.signature_data_url
            try:
                header, b64 = data_url.split(",", 1)
                ct = "image/png"
                if "data:" in header and ";base64" in header:
                    ct = header.split("data:")[1].split(";")[0] or "image/png"
                import base64 as _b64
                raw = _b64.b64decode(b64)
                if len(raw) > 400_000:  # ~400KB cap untuk signature PNG
                    raise HTTPException(400, "Signature terlalu besar")
                ext = obj_storage.ext_for(ct, "png")
                key = f"{obj_storage.APP_NAME}/customer-signature/{order['po_number']}/{rec['id']}.{ext}"
                res = obj_storage.put_object(key, raw, ct)
                signature_storage_path = res["path"]
                # File metadata (agar /api/files/{path} dapat serve)
                await db.files.insert_one({
                    "id": str(uuid.uuid4()),
                    "storage_path": res["path"],
                    "original_filename": f"signature-{rec['id']}.{ext}",
                    "content_type": ct,
                    "size": res.get("size") or len(raw),
                    "kind": "customer-signature",
                    "order_id": order["id"],
                    "po_number": order["po_number"],
                    "uploaded_by": None,
                    "uploaded_by_role": "customer",
                    "uploaded_by_name": signer_name,
                    "uploaded_at": _iso(_now()),
                    "is_deleted": False,
                })
            except HTTPException:
                raise
            except Exception as e:
                logger.exception("Signature upload failed")
                raise HTTPException(502, f"Gagal menyimpan tanda tangan: {e}")

        note = (payload.note or "").strip() or None
        now_str = _iso(_now())
        set_patch = {
            "status": "approved" if action == "approve" else "rejected",
            "response": "approved" if action == "approve" else "rejected",
            "signer_name": signer_name,
            "signature_storage_path": signature_storage_path,
            "customer_note": note if action == "approve" else None,
            "reject_reason": note if action == "reject" else None,
            "responded_at": now_str,
        }
        # Atomic guard: hanya update kalau status masih 'active' (duplicate double-click)
        result = await db.customer_approvals.update_one(
            {"id": rec["id"], "status": "active"},
            {"$set": set_patch},
        )
        if result.modified_count == 0:
            # Race: sudah keburu diubah oleh request lain
            latest = await db.customer_approvals.find_one({"id": rec["id"]}, {"_id": 0})
            if latest and latest.get("status") in ("approved", "rejected"):
                raise HTTPException(400, "Approval sudah dikirim sebelumnya")
            raise HTTPException(400, "Link approval tidak lagi aktif")
        rec.update(set_patch)

        # Integrasi dengan Design Approval existing
        history_action = "customer-approve" if action == "approve" else "customer-reject"
        design_note = f"Customer {signer_name} · {history_action}"
        if action == "approve":
            order["design_status"] = "Approved"
            order["design_approved_by"] = f"customer:{signer_name}"
            order["design_approved_by_name"] = signer_name
            order["design_approved_at"] = now_str
            order.setdefault("design_approval_history", []).append({
                "action": history_action,
                "by_email": None,
                "by_name": signer_name,
                "at": now_str,
                "note": note or "",
                "customer_approval_id": rec["id"],
            })
            # Advance stage seperti internal approve — dari 1/2 ke 3.
            if (order.get("current_stage") or 0) < 3:
                order["current_stage"] = 3
            sync_fn = deps.get("sync_workflow_stage")
            if sync_fn:
                sync_fn(order)
            add_timeline(order, order["current_stage"], {"email": None, "name": f"Customer: {signer_name}", "role": "customer"},
                         note="Design di-approve oleh customer")
        else:
            # Reject: design tetap Waiting; kalau sebelumnya Approved (tidak seharusnya, karena
            # customer submit hanya boleh saat active), tetap konsisten set Waiting.
            if order.get("design_status") == "Approved":
                order["design_status"] = "Waiting"
                order["design_approved_by"] = None
                order["design_approved_by_name"] = None
                order["design_approved_at"] = None
            order.setdefault("design_approval_history", []).append({
                "action": history_action,
                "by_email": None,
                "by_name": signer_name,
                "at": now_str,
                "note": note or "",
                "customer_approval_id": rec["id"],
            })
            add_timeline(order, order.get("current_stage", 0), {"email": None, "name": f"Customer: {signer_name}", "role": "customer"},
                         note=f"Design di-reject oleh customer: {note or ''}")

        order["customer_approval"] = _snapshot_for_order(rec)
        order.setdefault("customer_approval_history", []).append({
            "action": history_action,
            "by_email": None,
            "by_name": signer_name,
            "at": now_str,
            "approval_id": rec["id"],
            "design_version": rec.get("design_version"),
            "note": note or "",
        })
        await persist_order(order)

        # Side-effect: in-app notifications for Designer/CRM.
        # Must not fail or roll back the authoritative customer decision.
        try:
            from services.notifications import emit_customer_design_approval_notifications
            await emit_customer_design_approval_notifications(
                db,
                order=order,
                approval=rec,
                decision="approved" if action == "approve" else "rejected",
                logger=logger,
            )
        except Exception:
            logger.exception(
                "customer_design_approval_notification_hook_failed approval_id=%s action=%s",
                rec.get("id"),
                action,
            )

        # Audit (jangan pernah tulis plaintext token)
        await audit_po(order, f"customer_approval.{action}",
                       {"email": None, "name": f"Customer: {signer_name}", "role": "customer"},
                       after={"approval_id": rec["id"], "signer_name": signer_name,
                              "design_version": rec.get("design_version")},
                       note=note or "")
        settings = await get_settings()
        return {"view": _public_view(rec, order, settings, True, token=token)}

    # ------ Order-side: invalidate on new design upload ------
    async def invalidate_on_design_reupload(order: dict, actor: dict) -> Optional[dict]:
        """Dipanggil dari server.py setelah design baru di-upload — cari link aktif
        yang masih terkait versi lama & set status='invalidated'."""
        rec = await db.customer_approvals.find_one(
            {"order_id": order["id"], "status": "active"}, {"_id": 0},
        )
        if not rec:
            return None
        await db.customer_approvals.update_one(
            {"id": rec["id"]},
            {"$set": {
                "status": "invalidated",
                "revoke_reason": "Design di-upload ulang — versi lama tidak berlaku",
            }},
        )
        rec.update({"status": "invalidated"})
        order["customer_approval"] = _snapshot_for_order(rec)
        order.setdefault("customer_approval_history", []).append({
            "action": "invalidated-on-reupload",
            "by_email": (actor or {}).get("email"),
            "by_name": (actor or {}).get("name"),
            "at": _iso(_now()),
            "approval_id": rec["id"],
            "design_version": rec.get("design_version"),
        })
        return rec

    async def invalidate_on_design_file_delete(order: dict, file_id: str, actor: dict) -> Optional[dict]:
        """Dipanggil saat file design dihapus — jika file tersebut terikat pada link
        approval yang masih aktif, set status='invalidated'."""
        rec = await db.customer_approvals.find_one(
            {"order_id": order["id"], "design_version": file_id, "status": "active"},
            {"_id": 0},
        )
        if not rec:
            return None
        now_str = _iso(_now())
        await db.customer_approvals.update_one(
            {"id": rec["id"]},
            {"$set": {
                "status": "invalidated",
                "revoke_reason": "File design yang terikat telah dihapus",
                "revoked_by": (actor or {}).get("email"),
                "revoked_by_name": (actor or {}).get("name"),
                "revoked_at": now_str,
            }},
        )
        rec.update({
            "status": "invalidated",
            "revoke_reason": "File design yang terikat telah dihapus",
            "revoked_by": (actor or {}).get("email"),
            "revoked_by_name": (actor or {}).get("name"),
            "revoked_at": now_str,
        })
        order["customer_approval"] = _snapshot_for_order(rec)
        order.setdefault("customer_approval_history", []).append({
            "action": "invalidated-on-file-delete",
            "by_email": (actor or {}).get("email"),
            "by_name": (actor or {}).get("name"),
            "at": now_str,
            "approval_id": rec["id"],
            "design_version": file_id,
            "note": "File design yang terikat telah dihapus",
        })
        return rec

    # Expose helpers for server.py
    router._get_settings = get_settings
    router._invalidate_on_design_reupload = invalidate_on_design_reupload
    router._invalidate_on_design_file_delete = invalidate_on_design_file_delete
    return router
