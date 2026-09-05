"""Customer-facing PO tracking — opaque token + minimal public payload.

Read-only. Workflow statuses come from existing evidence helpers
(workflow_gates + quantity_rules), never from a generic stage-index fallback.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from quantity_rules import (
    valid_delivery_proofs,
    MAX_PUBLIC_DELIVERY_PROOFS,
)
from services.workflow_stage_status import workflow_stage_rows

PUBLIC_NOT_FOUND = "Tidak ditemukan"
STATUS_COMPLETED = "completed"
STATUS_IN_PROGRESS = "in_progress"
STATUS_WAITING = "waiting"
ALLOWED_STATUSES = frozenset({STATUS_COMPLETED, STATUS_IN_PROGRESS, STATUS_WAITING})
TOKEN_BYTES = 32
TOKEN_MIN_LEN = 16
TOKEN_MAX_LEN = 128
ENSURE_ATTEMPTS = 8
_TRACKING_LOG_RE = re.compile(r"(/api/public/tracking/)([^/\s\"'?]+)")


def new_tracking_token() -> str:
    """Opaque, non-sequential token. Not derived from PO number or DB id."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def mask_token(token: Optional[str]) -> str:
    t = (token or "").strip()
    if len(t) < 8:
        return "****"
    return f"{t[:4]}…"


def redact_tracking_in_text(text: str) -> str:
    def _sub(m):
        return m.group(1) + mask_token(m.group(2))
    return _TRACKING_LOG_RE.sub(_sub, text or "")


class TrackingAccessLogFilter(logging.Filter):
    """Mask tracking tokens in uvicorn/FastAPI access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_tracking_in_text(record.msg)
        args = record.args
        if isinstance(args, dict):
            for k, v in list(args.items()):
                if isinstance(v, str) and "/public/tracking/" in v:
                    args[k] = redact_tracking_in_text(v)
        elif isinstance(args, tuple):
            record.args = tuple(
                redact_tracking_in_text(a) if isinstance(a, str) and "/public/tracking/" in a else a
                for a in args
            )
        return True


def install_access_log_mask() -> None:
    filt = TrackingAccessLogFilter()
    for name in ("uvicorn.access", "uvicorn", "server", "customer_tracking"):
        logging.getLogger(name).addFilter(filt)


def strip_tracking_secret(order: Optional[dict]) -> None:
    """Remove persisted tracking fields from API responses (not from DB)."""
    if not order:
        return
    order.pop("tracking_token", None)
    order.pop("tracking_token_created_at", None)


def customer_workflow(order: dict) -> List[Dict[str, str]]:
    """Same 15 stages and labels as Alur Pekerjaan (workflow_stage_status)."""
    out: List[Dict[str, str]] = []
    for row in workflow_stage_rows(order):
        status = row.get("status") if row.get("status") in ALLOWED_STATUSES else STATUS_WAITING
        label = str(row.get("label") or "").strip() or None
        item = {"stage": row.get("stage") or "", "status": status}
        if label:
            item["label"] = label
        if item["stage"]:
            out.append(item)
    return out


def public_delivery_proofs(order: dict, token: str) -> List[Dict[str, str]]:
    """Newest proofs first, max 5. Public proxy URL only — no storage keys."""
    proofs = valid_delivery_proofs(order)
    def _sort_key(p):
        return str(p.get("uploaded_at") or p.get("created_at") or "")
    ordered = sorted(proofs, key=_sort_key, reverse=True)[:MAX_PUBLIC_DELIVERY_PROOFS]
    t = (token or "").strip()
    out = []
    for p in ordered:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        out.append({
            "id": pid,
            "url": f"/api/public/tracking/{t}/proofs/{pid}",
        })
        sid = str(p.get("shipment_id") or "").strip()
        if sid:
            out[-1]["shipment_id"] = sid
    return out


def _approved_design_files(order: dict) -> List[dict]:
    """Authoritative approved design files only (status Approved, not draft/rejected)."""
    if (order.get("design_status") or "").strip() != "Approved":
        return []
    files = order.get("design_files") or []
    out = []
    for f in files:
        if not isinstance(f, dict):
            continue
        fid = str(f.get("id") or "").strip()
        if not fid:
            continue
        # Prefer files with actual content (data_url or storage_path)
        if not (f.get("data_url") or f.get("storage_path")):
            continue
        out.append(f)
    return out


def public_design_documents(order: dict, token: str) -> List[Dict[str, Any]]:
    t = (token or "").strip()
    docs = []
    for f in _approved_design_files(order):
        fid = str(f.get("id") or "").strip()
        docs.append({
            "id": fid,
            "name": f.get("name") or "Design Approved",
            "content_type": f.get("content_type") or "",
            "kind": "design_approved",
            "preview_url": f"/api/public/tracking/{t}/design/{fid}?disposition=inline",
            "download_url": f"/api/public/tracking/{t}/design/{fid}?disposition=attachment",
        })
    return docs


def public_surat_jalan_documents(order: dict, token: str) -> List[Dict[str, Any]]:
    """Exact SJ files created for this order — proxy URLs only."""
    from services.surat_jalan_files import file_entries
    t = (token or "").strip()
    docs = []
    for f in file_entries(order):
        fid = str(f.get("id") or "").strip()
        if not fid:
            continue
        if not (f.get("storage_path") or f.get("signed_storage_path") or f.get("data_url")):
            continue
        docs.append({
            "id": fid,
            "name": f.get("name") or f.get("sj_number") or "Surat Jalan",
            "sj_number": f.get("sj_number") or "",
            "shipment_id": f.get("shipment_id") or None,
            "content_type": f.get("content_type") or "application/pdf",
            "kind": "surat_jalan",
            "has_signed": bool(str(f.get("signed_storage_path") or "").strip()),
            "preview_url": f"/api/public/tracking/{t}/surat-jalan/{fid}?disposition=inline",
            "download_url": f"/api/public/tracking/{t}/surat-jalan/{fid}?disposition=attachment",
        })
    return docs


def public_shipment_evidence(order: dict, token: str) -> List[Dict[str, Any]]:
    """Per-confirmation / per-shipment POD evidence with exact IDs."""
    from services.delivery_pod_signature import (
        get_frozen_driver_signatures,
        get_frozen_signatures,
        public_driver_signature_view,
        public_signature_view,
    )

    t = (token or "").strip()
    proofs_by_id = {str(p.get("id")): p for p in valid_delivery_proofs(order)}
    sigs = get_frozen_signatures(order)
    driver_sigs = get_frozen_driver_signatures(order)
    active = order.get("delivery_recipient_signature")
    if isinstance(active, dict) and active.get("frozen") and active.get("id"):
        if not any(str(s.get("id")) == str(active.get("id")) for s in sigs):
            sigs = list(sigs) + [active]
    d_active = order.get("delivery_driver_signature")
    if isinstance(d_active, dict) and d_active.get("frozen") and d_active.get("id"):
        if not any(str(s.get("id")) == str(d_active.get("id")) for s in driver_sigs):
            driver_sigs = list(driver_sigs) + [d_active]

    confirmations = [r for r in (order.get("delivery_confirmations") or []) if isinstance(r, dict)]
    out = []
    if confirmations:
        for rec in confirmations:
            pids = [str(x) for x in (rec.get("proof_ids") or []) if str(x).strip()]
            photos = []
            for pid in pids[:MAX_PUBLIC_DELIVERY_PROOFS]:
                if pid not in proofs_by_id:
                    continue
                photos.append({
                    "id": pid,
                    "url": f"/api/public/tracking/{t}/proofs/{pid}",
                })
            shipment_id = rec.get("shipment_id") or None
            sig = next(
                (s for s in sigs if str(s.get("confirmation_id") or "") == str(rec.get("id") or "")),
                None,
            )
            if not sig and shipment_id:
                sig = next((s for s in sigs if str(s.get("shipment_id") or "") == str(shipment_id)), None)
            if not sig and len(sigs) == 1 and len(confirmations) == 1:
                sig = sigs[0]
            dsig = next(
                (s for s in driver_sigs if str(s.get("confirmation_id") or "") == str(rec.get("id") or "")),
                None,
            )
            if not dsig and shipment_id:
                dsig = next((s for s in driver_sigs if str(s.get("shipment_id") or "") == str(shipment_id)), None)
            if not dsig and len(driver_sigs) == 1 and len(confirmations) == 1:
                dsig = driver_sigs[0]
            entry = {
                "confirmation_id": rec.get("id"),
                "shipment_id": shipment_id,
                "sj_file_id": (sig or {}).get("sj_file_id") or (sig or {}).get("delivery_note_id")
                    or (dsig or {}).get("sj_file_id"),
                "time": rec.get("confirmed_at"),
                "photos": photos,
            }
            if sig:
                entry["recipient"] = public_signature_view(sig, t)
            if dsig:
                entry["driver"] = public_driver_signature_view(dsig, t)
            out.append(entry)
    elif sigs or driver_sigs:
        for sig in (sigs or [None]):
            pids = [str(p.get("id")) for p in valid_delivery_proofs(order) if p.get("id")]
            if sig and sig.get("shipment_id"):
                pids = [
                    str(p.get("id"))
                    for p in valid_delivery_proofs(order)
                    if str(p.get("shipment_id") or "") == str(sig.get("shipment_id")) and p.get("id")
                ] or pids
            dsig = None
            if sig and sig.get("shipment_id"):
                dsig = next(
                    (s for s in driver_sigs if str(s.get("shipment_id") or "") == str(sig.get("shipment_id"))),
                    None,
                )
            if not dsig and len(driver_sigs) == 1:
                dsig = driver_sigs[0]
            entry = {
                "confirmation_id": (sig or {}).get("confirmation_id"),
                "shipment_id": (sig or {}).get("shipment_id") or (dsig or {}).get("shipment_id"),
                "sj_file_id": (sig or {}).get("sj_file_id") or (sig or {}).get("delivery_note_id"),
                "time": (sig or {}).get("recipient_signed_at") or (dsig or {}).get("driver_signed_at"),
                "photos": [
                    {"id": pid, "url": f"/api/public/tracking/{t}/proofs/{pid}"}
                    for pid in pids[:MAX_PUBLIC_DELIVERY_PROOFS]
                ],
            }
            if sig:
                entry["recipient"] = public_signature_view(sig, t)
            if dsig:
                entry["driver"] = public_driver_signature_view(dsig, t)
            out.append(entry)
    return out


def public_payload(order: dict, token: Optional[str] = None) -> Dict[str, Any]:
    """Minimal public JSON: customer, order number, workflow, documents, bounded proofs."""
    tok = (token or order.get("tracking_token") or "").strip()
    payload = {
        "customer_name": order.get("customer") or order.get("customer_name") or "",
        "order_number": order.get("po_number") or "",
        "workflow": customer_workflow(order),
        "delivery_proofs": public_delivery_proofs(order, tok),
        "documents": {
            "design_approved": public_design_documents(order, tok),
            "surat_jalan": public_surat_jalan_documents(order, tok),
        },
        "shipments_evidence": public_shipment_evidence(order, tok),
    }
    proofs = payload["delivery_proofs"]
    allowed_photo_ids = {str(p.get("id")) for p in proofs if p.get("id")}
    try:
        from services.delivery_history import public_delivery_history
        payload["delivery_history"] = public_delivery_history(order, tok, allowed_photo_ids)
    except Exception:
        payload["delivery_history"] = []
    try:
        from services.delivery_schedule import public_reschedule_snippet
        snippet = public_reschedule_snippet(order)
    except Exception:
        snippet = None
    if snippet:
        payload["delivery_reschedule"] = snippet
    return payload


def collect_public_keys(payload: dict) -> set:
    keys = set()

    def walk(obj):
        if isinstance(obj, dict):
            keys.update(obj.keys())
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(payload)
    return keys


def is_public_payload_safe(payload: dict) -> bool:
    allowed = {
        "order_number", "customer_name", "workflow", "delivery_proofs",
        "stage", "status", "label", "id", "url",
        "delivery_reschedule", "scheduled_date",
        "delivery_history", "driver_name", "item", "qty", "time", "vehicle_plate", "photos",
        "documents", "design_approved", "surat_jalan", "name", "content_type", "kind",
        "preview_url", "download_url", "sj_number", "shipment_id", "has_signed",
        "shipments_evidence", "confirmation_id", "sj_file_id", "recipient",
        "recipient_name", "recipient_signed_at", "has_signed_sj",
        "driver", "driver_name", "driver_signed_at",
    }
    return collect_public_keys(payload) <= allowed


def _token_usable(token: Optional[str]) -> bool:
    t = (token or "").strip()
    if len(t) < TOKEN_MIN_LEN or len(t) > TOKEN_MAX_LEN:
        return False
    if any(ch.isspace() for ch in t):
        return False
    return True


async def ensure_tracking_token(db, order: dict) -> str:
    """Return existing token or persist a new unique one (additive, lazy)."""
    existing = (order.get("tracking_token") or "").strip()
    if existing:
        return existing
    order_id = order.get("id")
    if not order_id:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(ENSURE_ATTEMPTS):
        token = new_tracking_token()
        clash = await db.orders.find_one({"tracking_token": token}, {"id": 1})
        if clash:
            continue
        result = await db.orders.update_one(
            {
                "id": order_id,
                "$or": [
                    {"tracking_token": {"$exists": False}},
                    {"tracking_token": None},
                    {"tracking_token": ""},
                ],
            },
            {"$set": {"tracking_token": token, "tracking_token_created_at": now}},
        )
        fresh = await db.orders.find_one({"id": order_id}, {"tracking_token": 1})
        got = ((fresh or {}).get("tracking_token") or "").strip()
        if got:
            order["tracking_token"] = got
            return got
        if result.matched_count:
            continue
    raise HTTPException(500, "Tidak dapat membuat tautan tracking")


def build_router(deps: dict) -> APIRouter:
    db = deps["db"]
    current_user = deps["current_user"]
    require_perm = deps["require_perm"]
    logger = deps["logger"]
    obj_storage = deps.get("obj_storage")

    router = APIRouter(prefix="/api", tags=["customer-tracking"])

    def _safe_public_payload(order: dict, token: str) -> dict:
        payload = public_payload(order, token)
        if is_public_payload_safe(payload):
            return payload
        logger.warning("public tracking payload sanitized token=%s", mask_token(token))
        out = {
            "customer_name": payload.get("customer_name") or "",
            "order_number": payload.get("order_number") or "",
            "workflow": [
                {"stage": r.get("stage"), "status": r.get("status")}
                for r in (payload.get("workflow") or [])
                if r.get("stage") and r.get("status") in ALLOWED_STATUSES
            ],
            "delivery_proofs": [
                {"id": p.get("id"), "url": p.get("url")}
                for p in (payload.get("delivery_proofs") or [])
                if isinstance(p, dict) and p.get("id") and p.get("url")
            ][:MAX_PUBLIC_DELIVERY_PROOFS],
            "documents": {"design_approved": [], "surat_jalan": []},
            "shipments_evidence": [],
        }
        docs = payload.get("documents") or {}
        if isinstance(docs, dict):
            out["documents"] = {
                "design_approved": [
                    {
                        "id": d.get("id"),
                        "name": d.get("name"),
                        "content_type": d.get("content_type") or "",
                        "kind": "design_approved",
                        "preview_url": d.get("preview_url"),
                        "download_url": d.get("download_url"),
                    }
                    for d in (docs.get("design_approved") or [])
                    if isinstance(d, dict) and d.get("id") and d.get("preview_url")
                ],
                "surat_jalan": [
                    {
                        "id": d.get("id"),
                        "name": d.get("name"),
                        "sj_number": d.get("sj_number") or "",
                        "shipment_id": d.get("shipment_id"),
                        "content_type": d.get("content_type") or "application/pdf",
                        "kind": "surat_jalan",
                        "has_signed": bool(d.get("has_signed")),
                        "preview_url": d.get("preview_url"),
                        "download_url": d.get("download_url"),
                    }
                    for d in (docs.get("surat_jalan") or [])
                    if isinstance(d, dict) and d.get("id") and d.get("preview_url")
                ],
            }
        ev = []
        for row in payload.get("shipments_evidence") or []:
            if not isinstance(row, dict):
                continue
            item = {
                "confirmation_id": row.get("confirmation_id"),
                "shipment_id": row.get("shipment_id"),
                "sj_file_id": row.get("sj_file_id"),
                "time": row.get("time"),
                "photos": [
                    {"id": p.get("id"), "url": p.get("url")}
                    for p in (row.get("photos") or [])
                    if isinstance(p, dict) and p.get("id") and p.get("url")
                ],
            }
            recp = row.get("recipient")
            if isinstance(recp, dict) and recp.get("id") and recp.get("url"):
                item["recipient"] = {
                    "id": recp.get("id"),
                    "recipient_name": recp.get("recipient_name") or "",
                    "recipient_signed_at": recp.get("recipient_signed_at") or "",
                    "shipment_id": recp.get("shipment_id"),
                    "sj_file_id": recp.get("sj_file_id"),
                    "url": recp.get("url"),
                    "has_signed_sj": bool(recp.get("has_signed_sj")),
                }
            drv = row.get("driver")
            if isinstance(drv, dict) and drv.get("id") and drv.get("url"):
                item["driver"] = {
                    "id": drv.get("id"),
                    "driver_name": drv.get("driver_name") or "",
                    "driver_signed_at": drv.get("driver_signed_at") or "",
                    "shipment_id": drv.get("shipment_id"),
                    "sj_file_id": drv.get("sj_file_id"),
                    "url": drv.get("url"),
                }
            ev.append(item)
        out["shipments_evidence"] = ev
        snippet = payload.get("delivery_reschedule")
        if isinstance(snippet, dict) and snippet.get("scheduled_date"):
            out["delivery_reschedule"] = {"scheduled_date": str(snippet.get("scheduled_date"))}
        hist = []
        for row in payload.get("delivery_history") or []:
            if not isinstance(row, dict):
                continue
            hist.append({
                "driver_name": row.get("driver_name"),
                "item": row.get("item"),
                "qty": row.get("qty"),
                "time": row.get("time"),
                "vehicle_plate": row.get("vehicle_plate"),
                "photos": [
                    {"id": p.get("id"), "url": p.get("url")}
                    for p in (row.get("photos") or [])
                    if isinstance(p, dict) and p.get("id") and p.get("url")
                ],
            })
        out["delivery_history"] = hist
        return out

    async def _order_for_token(token: str) -> dict:
        if not _token_usable(token):
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        try:
            order = await db.orders.find_one({"tracking_token": token}, {"_id": 0})
        except Exception:
            logger.warning("public tracking lookup failed token=%s", mask_token(token))
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        if not order or order.get("cancelled"):
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        return order

    def _disposition_headers(filename: str, disposition: str, media: str) -> dict:
        from urllib.parse import quote
        disp = "attachment" if (disposition or "").lower() == "attachment" else "inline"
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "file").strip("._") or "file"
        return {
            "Content-Disposition": f"{disp}; filename=\"{safe}\"; filename*=UTF-8''{quote(filename or safe)}",
            "Cache-Control": "private, no-store",
        }

    @router.get("/public/tracking/{token}")
    async def public_tracking(token: str):
        order = await _order_for_token(token)
        return _safe_public_payload(order, token)

    @router.get("/public/tracking/{token}/proofs/{proof_id}")
    async def public_tracking_proof(token: str, proof_id: str):
        if not obj_storage:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        pid = (proof_id or "").strip()
        if not pid or len(pid) > 80:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        order = await _order_for_token(token)
        proof = next((p for p in valid_delivery_proofs(order) if str(p.get("id")) == pid), None)
        if not proof:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        path = (proof.get("storage_path") or "").strip()
        if not path:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        try:
            data, ct = obj_storage.get_object(path)
        except FileNotFoundError:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        except Exception:
            logger.warning("public tracking proof fetch failed token=%s", mask_token(token))
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        media = proof.get("content_type") or ct or "image/jpeg"
        if not str(media).startswith("image/"):
            media = "image/jpeg"
        return Response(content=data, media_type=media)

    @router.get("/public/tracking/{token}/design/{file_id}")
    async def public_tracking_design(token: str, file_id: str, disposition: str = "inline"):
        fid = (file_id or "").strip()
        if not fid or len(fid) > 80:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        order = await _order_for_token(token)
        if (order.get("design_status") or "").strip() != "Approved":
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        file_entry = next(
            (f for f in (order.get("design_files") or []) if isinstance(f, dict) and str(f.get("id")) == fid),
            None,
        )
        if not file_entry:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        data = None
        media = file_entry.get("content_type") or "application/octet-stream"
        filename = file_entry.get("name") or "design"
        storage_path = (file_entry.get("storage_path") or "").strip()
        if storage_path and obj_storage:
            try:
                data, ct = obj_storage.get_object(storage_path)
                media = file_entry.get("content_type") or ct or media
            except Exception:
                data = None
        if data is None:
            data_url = str(file_entry.get("data_url") or "")
            if data_url.startswith("data:"):
                import base64
                try:
                    header, b64 = data_url.split(",", 1)
                    media = header[5:].split(";")[0] or media
                    data = base64.b64decode(b64)
                except Exception:
                    raise HTTPException(404, PUBLIC_NOT_FOUND)
        if not data:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        headers = _disposition_headers(filename, disposition, media)
        return Response(content=data, media_type=media, headers=headers)

    @router.get("/public/tracking/{token}/surat-jalan/{file_id}")
    async def public_tracking_sj(token: str, file_id: str, disposition: str = "inline"):
        if not obj_storage:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        fid = (file_id or "").strip()
        if not fid or len(fid) > 80:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        order = await _order_for_token(token)
        from services.surat_jalan_files import find_file
        entry = find_file(order, fid)
        if not entry:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        # Prefer signed rendition for customer-facing after electronic signature
        path = (
            str(entry.get("signed_storage_path") or "").strip()
            or str(entry.get("storage_path") or "").strip()
        )
        data = None
        media = entry.get("content_type") or "application/pdf"
        filename = entry.get("name") or entry.get("sj_number") or "surat-jalan.pdf"
        if path:
            try:
                data, ct = obj_storage.get_object(path)
                media = entry.get("content_type") or ct or media
            except FileNotFoundError:
                data = None
            except Exception:
                logger.warning("public tracking SJ fetch failed token=%s", mask_token(token))
                raise HTTPException(404, PUBLIC_NOT_FOUND)
        if data is None and str(entry.get("data_url") or "").startswith("data:"):
            import base64
            try:
                header, b64 = entry["data_url"].split(",", 1)
                media = header[5:].split(";")[0] or media
                data = base64.b64decode(b64)
            except Exception:
                raise HTTPException(404, PUBLIC_NOT_FOUND)
        if not data:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        if not str(media).startswith("application/") and "pdf" in filename.lower():
            media = "application/pdf"
        headers = _disposition_headers(filename, disposition, media)
        return Response(content=data, media_type=media, headers=headers)

    @router.get("/public/tracking/{token}/signatures/{sig_id}")
    async def public_tracking_signature(token: str, sig_id: str):
        if not obj_storage:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        sid = (sig_id or "").strip()
        if not sid or len(sid) > 80:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        order = await _order_for_token(token)
        from services.delivery_pod_signature import find_signature_by_id
        sig = find_signature_by_id(order, sid)
        if not sig:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        # Only expose after frozen (delivered evidence) OR if already tied to confirmation
        # Before delivery: do not leak unfinished POD signature as public evidence
        delivered = (order.get("delivery_status") or "") in ("Terkirim", "Sebagian Terkirim")
        if not sig.get("frozen") and not delivered:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        path = str(sig.get("signature_storage_path") or "").strip()
        if not path:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        try:
            data, ct = obj_storage.get_object(path)
        except FileNotFoundError:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        except Exception:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        media = sig.get("content_type") or ct or "image/png"
        if not str(media).startswith("image/"):
            media = "image/png"
        return Response(content=data, media_type=media)

    @router.get("/public/tracking/{token}/driver-signatures/{sig_id}")
    async def public_tracking_driver_signature(token: str, sig_id: str):
        if not obj_storage:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        sid = (sig_id or "").strip()
        if not sid or len(sid) > 80:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        order = await _order_for_token(token)
        from services.delivery_pod_signature import find_driver_signature_by_id
        sig = find_driver_signature_by_id(order, sid)
        if not sig:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        delivered = (order.get("delivery_status") or "") in ("Terkirim", "Sebagian Terkirim")
        if not sig.get("frozen") and not delivered:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        path = str(
            sig.get("driver_signature_storage_path")
            or sig.get("signature_storage_path")
            or ""
        ).strip()
        if not path:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        try:
            data, ct = obj_storage.get_object(path)
        except FileNotFoundError:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        except Exception:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        media = sig.get("content_type") or ct or "image/png"
        if not str(media).startswith("image/"):
            media = "image/png"
        return Response(content=data, media_type=media)

    @router.post("/orders/{order_id}/tracking-link")
    async def tracking_link(order_id: str, user=Depends(current_user)):
        await require_perm(user, "po.view")
        order = await db.orders.find_one({"id": order_id}, {"_id": 0})
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")
        token = await ensure_tracking_token(db, order)
        return {
            "token": token,
            "path": f"/tracking/{token}",
        }

    return router
