"""HTTP routes for Urgent PO approval workflow (Phase A)."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from services import urgent_requests as urg


class UrgentSubmitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(..., min_length=1)
    target_completion_date: str
    notes: str = ""
    idempotency_key: Optional[str] = None


class UrgentDecidePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str  # approved | rejected
    decision_notes: str = ""


def register_urgent_request_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_opt_in_perm,
    Depends=Depends,
):
    @api_router.post("/orders/{order_id}/urgent-requests")
    async def submit_urgent_request(
        order_id: str,
        payload: UrgentSubmitPayload,
        user=Depends(current_user),
        idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    ):
        await require_opt_in_perm(user, urg.PERM_REQUEST)
        db = get_db()
        order = await db.orders.find_one({"id": order_id}, {"_id": 0})
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")
        key = payload.idempotency_key or idempotency_key
        doc = await urg.submit_request(
            db,
            order=order,
            user=user,
            reason=payload.reason,
            target_completion_date=payload.target_completion_date,
            notes=payload.notes or "",
            idempotency_key=key,
        )
        # Best-effort in-app notifications — never fail the business write
        await urg.emit_request_notifications(db, order=order, request=doc)
        doc = await urg.flush_urgent_notifications(db, request=doc, order=order) or doc
        # Phase B: durable email outbox (independent of in-app notify); never blocks response
        try:
            from services.urgent_email_outbox import enqueue_request_emails

            await enqueue_request_emails(db, order=order, request=doc)
        except Exception:
            import logging

            logging.getLogger("urgent_requests_http").exception(
                "urgent email enqueue_request failed request=%s", doc.get("id")
            )
        display = urg.classify_display(order.get("priority"), doc)
        return {
            "request": urg.public_request(doc),
            "order": order,
            "display_state": display,
            "message": "Pengajuan Urgent terkirim. Prioritas PO tetap Normal sampai disetujui.",
        }

    @api_router.get("/orders/{order_id}/urgent-requests/latest")
    async def get_latest_urgent_request(order_id: str, user=Depends(current_user)):
        db = get_db()
        order = await db.orders.find_one({"id": order_id}, {"_id": 0, "id": 1, "priority": 1, "po_number": 1})
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")
        doc = await urg.get_latest_request_for_order(db, order_id)
        # Visibility: reviewer/approver OR owner of request OR anyone with po.view seeing display-only empty
        # Lightweight opt-in checks without raising
        async def _has(perm: str) -> bool:
            try:
                await require_opt_in_perm(user, perm)
                return True
            except HTTPException:
                return False

        has_review = await _has(urg.PERM_REVIEW)
        has_approve = await _has(urg.PERM_APPROVE)
        if doc and not urg.can_read_request(user, doc, has_review=has_review, has_approve=has_approve):
            # Hide other users' requests from non-privileged viewers
            doc = None
        if doc:
            doc = await urg.flush_urgent_notifications(db, request=doc, order=order) or doc
        return {
            "request": urg.public_request(doc),
            "display_state": urg.classify_display(order.get("priority"), doc),
            "order_priority": order.get("priority") or "normal",
        }

    @api_router.get("/urgent-requests")
    async def list_urgent_requests(
        status: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
        user=Depends(current_user),
    ):
        # Approvers and reviewers can list
        try:
            await require_opt_in_perm(user, urg.PERM_APPROVE)
        except HTTPException:
            await require_opt_in_perm(user, urg.PERM_REVIEW)
        db = get_db()
        items = await urg.list_requests(db, status=status, limit=limit)
        return {"requests": items}

    @api_router.get("/urgent-requests/{request_id}")
    async def get_urgent_request(request_id: str, user=Depends(current_user)):
        db = get_db()
        doc = await urg.get_request(db, request_id)
        if not doc:
            raise HTTPException(404, "Pengajuan tidak ditemukan")

        async def _has(perm: str) -> bool:
            try:
                await require_opt_in_perm(user, perm)
                return True
            except HTTPException:
                return False

        has_review = await _has(urg.PERM_REVIEW)
        has_approve = await _has(urg.PERM_APPROVE)
        if not urg.can_read_request(user, doc, has_review=has_review, has_approve=has_approve):
            raise HTTPException(403, "Anda tidak punya akses ke pengajuan ini")
        order = await db.orders.find_one({"id": doc.get("order_id")}, {"_id": 0})
        doc = await urg.flush_urgent_notifications(db, request=doc, order=order) or doc
        return {"request": urg.public_request(doc)}

    @api_router.post("/urgent-requests/{request_id}/decide")
    async def decide_urgent_request(
        request_id: str,
        payload: UrgentDecidePayload,
        user=Depends(current_user),
    ):
        await require_opt_in_perm(user, urg.PERM_APPROVE)
        db = get_db()
        doc = await urg.get_request(db, request_id)
        if not doc:
            raise HTTPException(404, "Pengajuan tidak ditemukan")
        order = await db.orders.find_one({"id": doc["order_id"]}, {"_id": 0})
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")
        result = await urg.decide_request(
            db,
            request=doc,
            order=order,
            user=user,
            decision=payload.decision,
            decision_notes=payload.decision_notes or "",
        )
        req = result["request"]
        ord_doc = result.get("order") or order
        # Refresh order after approve
        if req.get("status") == urg.STATUS_APPROVED:
            ord_doc = await db.orders.find_one({"id": doc["order_id"]}, {"_id": 0}) or ord_doc
        # Also recover any undelivered request-side in-app notifications
        await urg.emit_decision_notification(db, order=ord_doc or {}, request=req)
        req = await urg.flush_urgent_notifications(db, request=req, order=ord_doc) or req
        # Phase B: decision email outbox — failures never roll back approval/rejection
        try:
            from services.urgent_email_outbox import enqueue_decision_email

            await enqueue_decision_email(db, order=ord_doc or {}, request=req)
        except Exception:
            import logging

            logging.getLogger("urgent_requests_http").exception(
                "urgent email enqueue_decision failed request=%s", req.get("id")
            )
        return {
            "request": urg.public_request(req),
            "order": ord_doc,
            "display_state": urg.classify_display((ord_doc or {}).get("priority"), req),
            "message": (
                "Pengajuan disetujui. PO berstatus Urgent."
                if req.get("status") == urg.STATUS_APPROVED
                else "Pengajuan ditolak. Prioritas PO tetap Normal."
            ),
        }

    @api_router.post("/urgent-requests/{request_id}/cancel")
    async def cancel_urgent_request(request_id: str, user=Depends(current_user)):
        db = get_db()
        doc = await urg.get_request(db, request_id)
        if not doc:
            raise HTTPException(404, "Pengajuan tidak ditemukan")
        # Owner cancel — no special perm beyond being requester (checked inside)
        # Also allow if they have request perm and own it
        await require_opt_in_perm(user, urg.PERM_REQUEST)
        updated = await urg.cancel_request(db, request=doc, user=user)
        order = await db.orders.find_one({"id": doc["order_id"]}, {"_id": 0})
        return {
            "request": urg.public_request(updated),
            "order": order,
            "display_state": urg.classify_display((order or {}).get("priority"), updated),
            "message": "Pengajuan Urgent dibatalkan.",
        }
