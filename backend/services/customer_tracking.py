"""Customer-facing PO tracking — opaque token + minimal public payload.

Read-only. Workflow statuses come from existing evidence helpers
(workflow_gates + quantity_rules), never from a generic stage-index fallback.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from quantity_rules import (
    PRINT_STATUS_DONE,
    PRINT_STATUS_IN_PROGRESS,
    is_fully_delivered,
    loading_status_semantic,
    packing_status_semantic,
    print_status,
    qty_delivered,
    requires_printing,
    shipments_stored,
    surat_jalan_status_semantic,
    valid_delivery_proofs,
    MAX_PUBLIC_DELIVERY_PROOFS,
)
from workflow_gates import (
    group2_complete,
    helper_done,
    packing_unlocked,
    payment_ok,
    penjadwalan_done,
    sample_ok,
    sample_uploaded,
    screener_done,
    workflow_ready,
)

PUBLIC_NOT_FOUND = "Tidak ditemukan"
STATUS_COMPLETED = "completed"
STATUS_IN_PROGRESS = "in_progress"
STATUS_WAITING = "waiting"
ALLOWED_STATUSES = frozenset({STATUS_COMPLETED, STATUS_IN_PROGRESS, STATUS_WAITING})
PRINT_ONLY_STAGES = frozenset({
    "Screener",
    "Persiapan Helper",
    "Operator",
    "Konfirmasi Sample",
    "Cetak",
})
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


def _has_value(v) -> bool:
    return v is not None and str(v).strip() != ""


def _has_design_upload(order: dict) -> bool:
    files = order.get("design_files") or []
    if not isinstance(files, list) or not files:
        return False
    for f in files:
        if not isinstance(f, dict):
            continue
        if _has_value(f.get("data_url")) or _has_value(f.get("url")):
            return True
        if _has_value(f.get("storage_path")):
            return True
        if _has_value(f.get("name")) and _has_value(f.get("uploaded_at")):
            return True
    return False


def _surat_jalan_created(order: dict) -> bool:
    if shipments_stored(order):
        return True
    return bool(
        order.get("delivery_note_number")
        and order.get("delivery_note_created_at")
        and order.get("delivery_note_created_by")
    )


def _loading_done(order: dict) -> bool:
    if loading_status_semantic(order) == "completed":
        return True
    lc = order.get("loading_checklist") or {}
    return bool(lc.get("items_loaded")) and bool(lc.get("seal_ok"))


def _status(completed: bool, in_progress: bool) -> str:
    if completed:
        return STATUS_COMPLETED
    if in_progress:
        return STATUS_IN_PROGRESS
    return STATUS_WAITING


def _design_status(order: dict) -> str:
    if order.get("design_bypassed") or _has_design_upload(order):
        return STATUS_COMPLETED
    return STATUS_WAITING


def _approval_design_status(order: dict) -> str:
    if order.get("design_status") == "Approved" or order.get("design_bypassed"):
        return STATUS_COMPLETED
    if _has_design_upload(order):
        return STATUS_IN_PROGRESS
    return STATUS_WAITING


def _payment_status(order: dict) -> str:
    if payment_ok(order):
        return STATUS_COMPLETED
    if order.get("payment_files"):
        return STATUS_IN_PROGRESS
    return STATUS_WAITING


def _penjadwalan_status(order: dict, ready: dict) -> str:
    req_print = requires_printing(order)
    if req_print:
        return _status(penjadwalan_done(order), bool(ready.get("penjadwalan")))
    non_print_done = (
        _has_value(order.get("work_date"))
        or bool(order.get("operator_splits"))
        or group2_complete(order)
    )
    return _status(non_print_done, bool(ready.get("penjadwalan")))


def _screener_status(order: dict, ready: dict) -> str:
    return _status(screener_done(order), bool(ready.get("screener")))


def _helper_status(order: dict, ready: dict) -> str:
    return _status(helper_done(order), bool(ready.get("helper")))


def _operator_status(order: dict, ready: dict) -> str:
    return _status(sample_uploaded(order), bool(ready.get("operator")))


def _sample_status(order: dict) -> str:
    return _status(sample_ok(order), sample_uploaded(order))


def _cetak_status(order: dict, ready: dict) -> str:
    st = print_status(order)
    if st == PRINT_STATUS_DONE:
        return STATUS_COMPLETED
    if not ready.get("print_qty"):
        return STATUS_WAITING
    if st == PRINT_STATUS_IN_PROGRESS:
        return STATUS_IN_PROGRESS
    return STATUS_IN_PROGRESS


def _packing_status(order: dict, ready: dict) -> str:
    semantic = packing_status_semantic(order)
    if semantic == "Sudah":
        return STATUS_COMPLETED
    if semantic == "Berjalan" or packing_unlocked(order) or ready.get("packing"):
        return STATUS_IN_PROGRESS
    return STATUS_WAITING


def _surat_jalan_status(order: dict) -> str:
    created = _surat_jalan_created(order)
    sj = surat_jalan_status_semantic(order)
    pack = packing_status_semantic(order)
    if created and sj == "completed":
        return STATUS_COMPLETED
    if created or pack == "Sudah":
        return STATUS_IN_PROGRESS
    return STATUS_WAITING


def _loading_status(order: dict) -> str:
    qty_st = loading_status_semantic(order)
    if _loading_done(order):
        return STATUS_COMPLETED
    if qty_st == "partial" or _surat_jalan_created(order):
        return STATUS_IN_PROGRESS
    return STATUS_WAITING


def _terkirim_status(order: dict) -> str:
    if is_fully_delivered(order):
        return STATUS_COMPLETED
    delivered = qty_delivered(order)
    if order.get("delivery_status") == "Sebagian Terkirim" or delivered > 0:
        return STATUS_IN_PROGRESS
    if _loading_done(order) or loading_status_semantic(order) == "partial":
        return STATUS_IN_PROGRESS
    return STATUS_WAITING


def customer_workflow(order: dict) -> List[Dict[str, str]]:
    """Evidence-based customer stages. Hides print-only rows when not applicable."""
    ready = workflow_ready(order)
    req_print = requires_printing(order)
    rows: List[Tuple[str, str]] = [
        ("Design", _design_status(order)),
        ("Approval Design", _approval_design_status(order)),
        ("Payment", _payment_status(order)),
        ("Penjadwalan", _penjadwalan_status(order, ready)),
        ("Screener", _screener_status(order, ready)),
        ("Persiapan Helper", _helper_status(order, ready)),
        ("Operator", _operator_status(order, ready)),
        ("Konfirmasi Sample", _sample_status(order)),
        ("Cetak", _cetak_status(order, ready)),
        ("Packing", _packing_status(order, ready)),
        ("Surat Jalan", _surat_jalan_status(order)),
        ("Loading", _loading_status(order)),
        ("Terkirim", _terkirim_status(order)),
    ]
    out: List[Dict[str, str]] = []
    for stage, status in rows:
        if not req_print and stage in PRINT_ONLY_STAGES:
            continue
        if status not in ALLOWED_STATUSES:
            status = STATUS_WAITING
        out.append({"stage": stage, "status": status})
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
    return out


def public_payload(order: dict, token: Optional[str] = None) -> Dict[str, Any]:
    """Minimal public JSON: customer, order number, workflow, bounded proofs."""
    tok = (token or order.get("tracking_token") or "").strip()
    payload = {
        "customer_name": order.get("customer") or order.get("customer_name") or "",
        "order_number": order.get("po_number") or "",
        "workflow": customer_workflow(order),
        "delivery_proofs": public_delivery_proofs(order, tok),
    }
    try:
        from services.delivery_schedule import public_reschedule_snippet
        snippet = public_reschedule_snippet(order)
    except Exception:
        snippet = None
    if snippet:
        payload["delivery_reschedule"] = snippet
    return payload


def collect_public_keys(payload: dict) -> set:
    keys = set(payload.keys())
    for row in payload.get("workflow") or []:
        if isinstance(row, dict):
            keys.update(row.keys())
    for p in payload.get("delivery_proofs") or []:
        if isinstance(p, dict):
            keys.update(p.keys())
    return keys


def is_public_payload_safe(payload: dict) -> bool:
    allowed = {
        "order_number", "customer_name", "workflow", "delivery_proofs",
        "stage", "status", "id", "url",
        "delivery_reschedule", "scheduled_date",
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
        }
        snippet = payload.get("delivery_reschedule")
        if isinstance(snippet, dict) and snippet.get("scheduled_date"):
            out["delivery_reschedule"] = {"scheduled_date": str(snippet.get("scheduled_date"))}
        return out

    @router.get("/public/tracking/{token}")
    async def public_tracking(token: str):
        if not _token_usable(token):
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        try:
            order = await db.orders.find_one({"tracking_token": token}, {"_id": 0})
        except Exception:
            logger.warning("public tracking lookup failed token=%s", mask_token(token))
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        if not order or order.get("cancelled"):
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        return _safe_public_payload(order, token)

    @router.get("/public/tracking/{token}/proofs/{proof_id}")
    async def public_tracking_proof(token: str, proof_id: str):
        if not _token_usable(token) or not obj_storage:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        pid = (proof_id or "").strip()
        if not pid or len(pid) > 80:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        try:
            order = await db.orders.find_one({"tracking_token": token}, {"_id": 0})
        except Exception:
            raise HTTPException(404, PUBLIC_NOT_FOUND)
        if not order or order.get("cancelled"):
            raise HTTPException(404, PUBLIC_NOT_FOUND)
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
