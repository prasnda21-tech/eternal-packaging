"""Customer-facing PO tracking — opaque token + minimal public payload.

Read-only. Workflow statuses come from existing evidence helpers
(workflow_gates + quantity_rules), never from a generic stage-index fallback.
"""
from __future__ import annotations

import base64
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo.errors import DuplicateKeyError

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response

from quantity_rules import (
    valid_delivery_proofs,
    MAX_PUBLIC_DELIVERY_PROOFS,
)
from services.tracking_public_url import tracking_legacy_path, tracking_public_path
from services.workflow_stage_status import workflow_stage_rows

PUBLIC_NOT_FOUND = "Tidak ditemukan"
STATUS_COMPLETED = "completed"
STATUS_IN_PROGRESS = "in_progress"
STATUS_WAITING = "waiting"
ALLOWED_STATUSES = frozenset({STATUS_COMPLETED, STATUS_IN_PROGRESS, STATUS_WAITING})
TOKEN_BYTES = 32
TOKEN_MIN_LEN = 16
TOKEN_MAX_LEN = 128
ALIAS_BYTES = 16
ALIAS_LEN = 22
ENSURE_ATTEMPTS = 8
PUBLIC_CODE_INDEX = "tracking_public_code_unique"
_PUBLIC_CODE_INDEXED = set()
_TRACKING_LOG_RE = re.compile(r"(/api/public/tracking/)([^/\s\"'?]+)")
_TRACKING_PAGE_LOG_RE = re.compile(r"(/t/)([^/\s\"'?]+)/([^/\s\"'?]+)/([^/\s\"'?]+)")


def new_tracking_token() -> str:
    """Opaque, non-sequential token. Not derived from PO number or DB id."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def new_tracking_alias() -> str:
    """22-char base64url code with 128 bits of entropy.

    This is a new random value, not a slice of the stored tracking token.
    """
    raw = secrets.token_bytes(ALIAS_BYTES)
    code = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if len(raw) != ALIAS_BYTES or len(code) != ALIAS_LEN or "=" in code:
        raise RuntimeError("tracking alias length")
    return code


def mask_token(token: Optional[str]) -> str:
    t = (token or "").strip()
    if len(t) < 8:
        return "****"
    return f"{t[:4]}…"


def redact_tracking_in_text(text: str) -> str:
    def _sub(m):
        return m.group(1) + mask_token(m.group(2))

    def _page(m):
        return f"{m.group(1)}{m.group(2)}/{m.group(3)}/{mask_token(m.group(4))}"

    red = _TRACKING_LOG_RE.sub(_sub, text or "")
    return _TRACKING_PAGE_LOG_RE.sub(_page, red)


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
    order.pop("tracking_public_code", None)
    order.pop("tracking_public_code_created_at", None)


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
        signed_at = str(f.get("signed_at") or "").strip()
        recipient_name = str(f.get("signed_recipient_name") or "").strip()
        row = {
            "id": fid,
            "name": f.get("name") or f.get("sj_number") or "Surat Jalan",
            "sj_number": f.get("sj_number") or "",
            "shipment_id": f.get("shipment_id") or None,
            "content_type": f.get("content_type") or "application/pdf",
            "kind": "surat_jalan",
            "has_signed": bool(str(f.get("signed_storage_path") or "").strip()),
            "preview_url": f"/api/public/tracking/{t}/surat-jalan/{fid}?disposition=inline",
            "download_url": f"/api/public/tracking/{t}/surat-jalan/{fid}?disposition=attachment",
        }
        if signed_at:
            row["signed_at"] = signed_at
        if recipient_name:
            row["recipient_name"] = recipient_name
        docs.append(row)
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


_DESIGN_APPROVAL_STATUSES = frozenset({
    "active", "approved", "rejected", "expired", "revoked", "invalidated",
})
_DESIGN_PREVIEW_STATUSES = frozenset({"active", "approved", "rejected"})


def public_order_summary(order: dict) -> Dict[str, Any]:
    """Customer-safe PO facts only. No line_id, prices, or internal flags."""
    items: List[Dict[str, Any]] = []
    for li in order.get("line_items") or []:
        if not isinstance(li, dict):
            continue
        name = str(li.get("name") or "").strip()
        if not name:
            continue
        row: Dict[str, Any] = {"name": name}
        color = str(li.get("color") or "").strip()
        if color:
            row["color"] = color
        unit = str(li.get("unit") or "").strip()
        if unit:
            row["unit"] = unit
        raw_q = li.get("quantity")
        if raw_q is not None and str(raw_q).strip() != "":
            try:
                row["quantity"] = int(raw_q)
            except (TypeError, ValueError):
                row["quantity"] = str(raw_q).strip()
        items.append(row)
    out: Dict[str, Any] = {}
    if items:
        out["items"] = items
    for key in ("order_date", "deadline", "estimated_delivery", "outlet"):
        val = str(order.get(key) or "").strip()
        if val:
            out[key] = val
    return out


def public_design_approval(order: dict, token: str) -> Optional[Dict[str, Any]]:
    """Read existing customer_approval snapshot only. Never generate a link."""
    snap = order.get("customer_approval")
    if not isinstance(snap, dict):
        return None
    status = str(snap.get("status") or "").strip()
    if status not in _DESIGN_APPROVAL_STATUSES:
        return None
    if not str(snap.get("approval_id") or "").strip():
        return None
    t = (token or "").strip()
    out: Dict[str, Any] = {"status": status}
    if status == "active" and t:
        out["path"] = f"/customer/design-approval/{t}"
    if status in _DESIGN_PREVIEW_STATUSES and t:
        version = snap.get("design_version")
        files = order.get("design_files") or []
        bound = next(
            (f for f in files if isinstance(f, dict) and f.get("id") == version),
            None,
        )
        ct = str((bound or {}).get("content_type") or "").lower()
        if bound and ct.startswith("image/"):
            out["preview_url"] = f"/api/public/customer-approval/{t}/design"
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
    summary = public_order_summary(order)
    if summary:
        payload["summary"] = summary
    approval = public_design_approval(order, tok)
    if approval:
        payload["design_approval"] = approval
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


def _public_sj_row(d: dict) -> Dict[str, Any]:
    """Customer-safe Surat Jalan row. Omits empty signature fields."""
    row = {
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
    signed_at = str(d.get("signed_at") or "").strip()
    if signed_at:
        row["signed_at"] = signed_at
    recipient_name = str(d.get("recipient_name") or "").strip()
    if recipient_name:
        row["recipient_name"] = recipient_name
    return row


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
        "signed_at",
        "shipments_evidence", "confirmation_id", "sj_file_id", "recipient",
        "recipient_name", "recipient_signed_at", "has_signed_sj",
        "driver", "driver_name", "driver_signed_at",
        "design_approval", "path", "action",
        "summary", "items", "color", "unit", "quantity",
        "order_date", "deadline", "estimated_delivery", "outlet",
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


async def ensure_public_code_index(db) -> None:
    """Unique index for the public alias. Does not write alias values."""
    key = id(db)
    if key in _PUBLIC_CODE_INDEXED:
        return
    await db.orders.create_index(
        [("tracking_public_code", 1)],
        unique=True,
        name=PUBLIC_CODE_INDEX,
        partialFilterExpression={"tracking_public_code": {"$exists": True, "$gt": ""}},
    )
    _PUBLIC_CODE_INDEXED.add(key)


async def ensure_tracking_public_code(db, order: dict) -> str:
    """Return the stored public alias, or persist a new unique one.

    Only an authorized share may call this. A public GET must not. Repeated
    and overlapping calls keep the first stored alias.
    """
    order_id = order.get("id")
    if not order_id:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    await ensure_public_code_index(db)
    for _ in range(ENSURE_ATTEMPTS):
        fresh = await db.orders.find_one(
            {"id": order_id},
            {"tracking_public_code": 1},
        )
        got = ((fresh or {}).get("tracking_public_code") or "").strip()
        if got:
            order["tracking_public_code"] = got
            return got
        code = new_tracking_alias()
        clash = await db.orders.find_one(
            {"$or": [{"tracking_public_code": code}, {"tracking_token": code}]},
            {"id": 1},
        )
        if clash:
            continue
        now = datetime.now(timezone.utc).isoformat()
        try:
            await db.orders.update_one(
                {
                    "id": order_id,
                    "$or": [
                        {"tracking_public_code": {"$exists": False}},
                        {"tracking_public_code": None},
                        {"tracking_public_code": ""},
                    ],
                },
                {"$set": {
                    "tracking_public_code": code,
                    "tracking_public_code_created_at": now,
                }},
            )
        except DuplicateKeyError:
            continue
    fresh = await db.orders.find_one({"id": order_id}, {"tracking_public_code": 1})
    got = ((fresh or {}).get("tracking_public_code") or "").strip()
    if got:
        order["tracking_public_code"] = got
        return got
    raise HTTPException(500, "Tidak dapat membuat tautan tracking")


def build_router(deps: dict) -> APIRouter:
    db = deps["db"]
    get_db = deps.get("get_db") or (lambda: db)
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
        summary = payload.get("summary")
        if isinstance(summary, dict):
            clean_items = []
            for it in summary.get("items") or []:
                if not isinstance(it, dict) or not it.get("name"):
                    continue
                row = {"name": str(it.get("name"))}
                if it.get("color"):
                    row["color"] = str(it.get("color"))
                if it.get("unit"):
                    row["unit"] = str(it.get("unit"))
                if it.get("quantity") is not None:
                    row["quantity"] = it.get("quantity")
                clean_items.append(row)
            clean_sum = {}
            if clean_items:
                clean_sum["items"] = clean_items
            for key in ("order_date", "deadline", "estimated_delivery", "outlet"):
                if summary.get(key):
                    clean_sum[key] = str(summary.get(key))
            if clean_sum:
                out["summary"] = clean_sum
        approval = payload.get("design_approval")
        if isinstance(approval, dict) and approval.get("status") in {
            "active", "approved", "rejected", "expired", "revoked", "invalidated",
        }:
            clean_ap = {"status": approval.get("status")}
            path = str(approval.get("path") or "")
            if clean_ap["status"] == "active" and path.startswith("/customer/design-approval/"):
                clean_ap["path"] = path
            preview = str(approval.get("preview_url") or "")
            if preview.startswith("/api/public/customer-approval/") and preview.endswith("/design"):
                clean_ap["preview_url"] = preview
            out["design_approval"] = clean_ap
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
                    _public_sj_row(d)
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
            active = get_db()
            order = await active.orders.find_one({"tracking_token": token}, {"_id": 0})
            if not order:
                order = await active.orders.find_one({"tracking_public_code": token}, {"_id": 0})
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

    _INVOICE_HEADERS = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }

    @router.get("/public/tracking/{token}/invoices")
    async def public_tracking_invoices(token: str):
        order = await _order_for_token(token)
        from services.customer_tracking_invoices import public_invoice_list
        body = await public_invoice_list(get_db(), order, token.strip())
        return JSONResponse(body, headers=_INVOICE_HEADERS)

    @router.get("/public/tracking/{token}/invoices/{invoice_id}")
    async def public_tracking_invoice_detail(token: str, invoice_id: str):
        order = await _order_for_token(token)
        from services.customer_tracking_invoices import public_invoice_detail_payload
        body = await public_invoice_detail_payload(get_db(), order, token.strip(), invoice_id)
        if body is None:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        return JSONResponse(body, headers=_INVOICE_HEADERS)

    @router.get("/public/tracking/{token}/invoices/{invoice_id}/pdf")
    async def public_tracking_invoice_pdf(token: str, invoice_id: str, disposition: str = "attachment"):
        order = await _order_for_token(token)
        from services.customer_tracking_invoices import render_customer_invoice_pdf, visible_invoice_for_order
        inv = await visible_invoice_for_order(get_db(), order, invoice_id)
        if not inv:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        pdf_bytes = render_customer_invoice_pdf(inv, order)
        number = str(inv.get("invoice_number") or "faktur").replace("/", "_").replace("\\", "_")
        headers = _disposition_headers(f"Faktur_{number}.pdf", disposition, "application/pdf")
        headers["Cache-Control"] = "private, no-store"
        headers["X-Content-Type-Options"] = "nosniff"
        return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)

    @router.get("/public/tracking/{token}/payment-proofs")
    async def public_tracking_payment_proofs(token: str):
        order = await _order_for_token(token)
        from services.customer_tracking_payment_proofs import public_payment_proof_list
        body = await public_payment_proof_list(get_db(), order)
        return JSONResponse(body, headers=_INVOICE_HEADERS)

    @router.get("/public/tracking/{token}/payment-proofs/{receipt_id}")
    async def public_tracking_payment_proof_file(
        token: str,
        receipt_id: str,
        disposition: str = "inline",
    ):
        order = await _order_for_token(token)
        from services.customer_tracking_payment_proofs import public_payment_proof_bytes
        loaded = await public_payment_proof_bytes(get_db(), order, receipt_id)
        if not loaded:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        data, media, filename = loaded
        headers = _disposition_headers(filename, disposition, media)
        headers["Cache-Control"] = "private, no-store"
        headers["X-Content-Type-Options"] = "nosniff"
        return Response(content=data, media_type=media, headers=headers)

    @router.post("/orders/{order_id}/tracking-link")
    async def tracking_link(order_id: str, user=Depends(current_user)):
        """Share link for one order.

        Reuses the stored tracking token and its public alias. The alias is a
        separate 128-bit code, created here for an authorized share and never
        by a public GET. Customer and PO segments are labels computed at share
        time. No backfill is required: an old /tracking/:token link keeps the
        stored token, and a later share returns the same alias.
        """
        await require_perm(user, "po.view")
        active = get_db()
        order = await active.orders.find_one({"id": order_id}, {"_id": 0})
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")
        token = await ensure_tracking_token(active, order)
        code = await ensure_tracking_public_code(active, order)
        public_path, po_label = tracking_public_path(order, code)
        legacy = tracking_legacy_path(token)
        return {
            "token": token,
            "code": code,
            "path": public_path,
            "public_path": public_path,
            "legacy_path": legacy,
            "po_label": po_label,
        }

    return router
