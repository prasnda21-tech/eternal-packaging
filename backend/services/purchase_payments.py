"""Purchase Payment (Pembayaran Pembelian) against exact Faktur Pembelian.

Lifecycle (payment workflow ≠ invoice financial status):
  BELUM DIAJUKAN (draft) → DIAJUKAN (submitted) → DIBAYAR (confirmed settlement)

Only confirmed (PAID) payments affect invoice paid_total / outstanding / DP / LUNAS.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.purchase_common import (
    PREFIX_PURCHASE_PAYMENT,
    actor_snapshot,
    stamp_create,
    stamp_update,
    cancellation_fields,
    derive_child_number,
    get_named_lock,
    insert_with_unique_number,
    is_cancelled,
    money_round,
    now_iso,
    parse_optional_ymd,
    parse_positive_money,
    with_active_filter,
)
from services.purchase_invoices import get_purchase_invoice

COLLECTION = "purchase_payments"

SUBMISSION_NOT_SUBMITTED = "NOT_SUBMITTED"
SUBMISSION_SUBMITTED = "SUBMITTED"

SETTLEMENT_UNPAID = "UNPAID"
SETTLEMENT_PAID = "PAID"

STATUS_BELUM_DIAJUKAN = "BELUM DIAJUKAN"
STATUS_DIAJUKAN = "DIAJUKAN"
STATUS_DIBAYAR = "DIBAYAR"


def _format_rupiah(num: Any) -> str:
    n = money_round(num)
    return "Rp " + f"{n:,.0f}".replace(",", ".")


def is_settlement_paid(doc: Optional[dict]) -> bool:
    if not doc:
        return False
    return str(doc.get("settlement_status") or "").upper() == SETTLEMENT_PAID


def payment_workflow_status(doc: dict) -> str:
    if is_settlement_paid(doc):
        return STATUS_DIBAYAR
    sub = str(doc.get("submission_status") or SUBMISSION_NOT_SUBMITTED).upper()
    if sub == SUBMISSION_SUBMITTED:
        return STATUS_DIAJUKAN
    return STATUS_BELUM_DIAJUKAN


def _enrich_payment(doc: dict, *, invoice: Optional[dict] = None) -> dict:
    out = dict(doc)
    cancelled = is_cancelled(out)
    out["cancelled"] = cancelled

    sub = str(out.get("submission_status") or SUBMISSION_NOT_SUBMITTED).upper()
    if sub not in {SUBMISSION_NOT_SUBMITTED, SUBMISSION_SUBMITTED}:
        sub = SUBMISSION_NOT_SUBMITTED
    out["submission_status"] = sub
    out["submission_status_label"] = (
        STATUS_DIAJUKAN if sub == SUBMISSION_SUBMITTED else STATUS_BELUM_DIAJUKAN
    )

    settle = str(out.get("settlement_status") or SETTLEMENT_UNPAID).upper()
    if settle not in {SETTLEMENT_UNPAID, SETTLEMENT_PAID}:
        settle = SETTLEMENT_UNPAID
    out["settlement_status"] = settle
    out["settlement_status_label"] = (
        "DIBAYAR" if settle == SETTLEMENT_PAID else "BELUM DIBAYAR"
    )

    workflow = payment_workflow_status(out)
    out["status"] = workflow
    out["workflow_status"] = workflow
    out["payment_state"] = workflow

    paid = settle == SETTLEMENT_PAID
    out["can_submit"] = (not cancelled) and (not paid) and sub != SUBMISSION_SUBMITTED
    out["can_confirm_paid"] = (not cancelled) and (not paid) and sub == SUBMISSION_SUBMITTED
    if cancelled:
        out["can_cancel"] = False
        out["cancel_block_reason"] = "Pembayaran sudah dibatalkan."
    elif paid:
        out["can_cancel"] = False
        out["cancel_block_reason"] = (
            "Pembayaran sudah dikonfirmasi dibayar dan tidak dapat dihapus langsung."
        )
    else:
        out["can_cancel"] = True
        out["cancel_block_reason"] = None

    if invoice:
        out["invoice_grand_total"] = invoice.get("grand_total")
        out["invoice_paid_total"] = invoice.get("paid_total")
        out["invoice_outstanding_amount"] = invoice.get("outstanding_amount")
        out["invoice_payment_status"] = invoice.get("payment_status")
        out["invoice_date"] = invoice.get("invoice_date")
        out["invoice_due_date"] = invoice.get("due_date")
        out["invoice_due_status"] = invoice.get("due_status")

    # Explicit nullable contract for older payments.
    if "requested_payment_date" not in out or out.get("requested_payment_date") in ("",):
        out["requested_payment_date"] = out.get("requested_payment_date") or None

    return out


async def _attach_invoice_summary(db, payment: dict) -> dict:
    inv_id = payment.get("purchase_invoice_id")
    if not inv_id:
        return _enrich_payment(payment)
    try:
        invoice = await get_purchase_invoice(db, inv_id)
    except HTTPException:
        return _enrich_payment(payment)
    return _enrich_payment(payment, invoice=invoice)


async def _build_draft_doc(db, invoice: dict, payload: Dict[str, Any], user: dict) -> dict:
    outstanding = float(invoice.get("outstanding_amount") or 0)
    amount = parse_positive_money(payload.get("amount"), "Jumlah pembayaran")
    if money_round(amount) > money_round(outstanding):
        raise HTTPException(
            400,
            f"Jumlah pembayaran (Rp {amount:,.2f}) melebihi sisa tagihan (Rp {outstanding:,.2f}).",
        )

    ts = now_iso()
    existing_count = await db[COLLECTION].count_documents(
        with_active_filter({"purchase_order_id": invoice["purchase_order_id"]})
    )
    doc = {
        "id": str(uuid.uuid4()),
        "purchase_invoice_id": invoice["id"],
        "invoice_number": invoice.get("invoice_number"),
        "purchase_order_id": invoice["purchase_order_id"],
        "po_number": invoice.get("po_number"),
        "supplier": invoice.get("supplier"),
        "invoice_grand_total": invoice.get("grand_total"),
        "amount": money_round(amount),
        # payment_date = accounting/journal business date for this payment document.
        # Distinct from paid_at (actual settlement confirmation timestamp).
        "payment_date": str(
            payload.get("payment_date") or payload.get("receipt_date") or ts[:10]
        ).strip()[:10],
        # Target / tanggal dibutuhkan for the requester — optional, never invent.
        "requested_payment_date": parse_optional_ymd(payload.get("requested_payment_date")),
        "notes": str(payload.get("notes") or "").strip(),
        "submission_status": SUBMISSION_NOT_SUBMITTED,
        "settlement_status": SETTLEMENT_UNPAID,
        "payment_state": STATUS_BELUM_DIAJUKAN,
        "workflow_status": STATUS_BELUM_DIAJUKAN,
        "status": STATUS_BELUM_DIAJUKAN,
        **stamp_create(user),
    }
    idem = str(payload.get("idempotency_key") or "").strip()
    if idem:
        doc["idempotency_key"] = idem

    async def _alloc():
        idx = existing_count + 1
        candidate = derive_child_number(invoice["po_number"], PREFIX_PURCHASE_PAYMENT, idx)
        while await db[COLLECTION].find_one({"payment_number": candidate}, {"_id": 1}):
            idx += 1
            candidate = derive_child_number(invoice["po_number"], PREFIX_PURCHASE_PAYMENT, idx)
        return candidate

    return await insert_with_unique_number(db, COLLECTION, "payment_number", doc, _alloc)


async def _acquire_invoice_for_new_payment(
    db, invoice: dict, *, claim_token: Optional[str] = None
) -> tuple:
    from services.purchase_invoice_posting import (
        INVOICE_CANCEL_IN_PROGRESS_PAYMENT_CREATE_MESSAGE,
        INVOICE_REVERSE_IN_PROGRESS_PAYMENT_CREATE_MESSAGE,
        PAYMENT_CREATE_CLAIM_CONFLICT_MESSAGE,
        acquire_payment_create_claim,
        clear_payment_create_claim,
        invoice_cancel_claim_is_active,
        reverse_claim_is_active,
    )

    if is_cancelled(invoice):
        raise HTTPException(400, "Faktur sudah dibatalkan; pembayaran tidak dapat dibuat.")
    if reverse_claim_is_active(invoice):
        raise HTTPException(409, INVOICE_REVERSE_IN_PROGRESS_PAYMENT_CREATE_MESSAGE)
    if invoice_cancel_claim_is_active(invoice):
        raise HTTPException(409, INVOICE_CANCEL_IN_PROGRESS_PAYMENT_CREATE_MESSAGE)
    token = str(claim_token or uuid.uuid4())
    claimed = await acquire_payment_create_claim(db, invoice["id"], token=token)
    if claimed is None:
        current = await db.purchase_invoices.find_one({"id": invoice["id"]}, {"_id": 0}) or invoice
        if reverse_claim_is_active(current):
            raise HTTPException(409, INVOICE_REVERSE_IN_PROGRESS_PAYMENT_CREATE_MESSAGE)
        if invoice_cancel_claim_is_active(current):
            raise HTTPException(409, INVOICE_CANCEL_IN_PROGRESS_PAYMENT_CREATE_MESSAGE)
        if is_cancelled(current):
            raise HTTPException(400, "Faktur sudah dibatalkan; pembayaran tidak dapat dibuat.")
        raise HTTPException(409, PAYMENT_CREATE_CLAIM_CONFLICT_MESSAGE)
    if is_cancelled(claimed) or reverse_claim_is_active(claimed) or invoice_cancel_claim_is_active(claimed):
        await clear_payment_create_claim(db, invoice["id"], token=token)
        if is_cancelled(claimed):
            raise HTTPException(400, "Faktur sudah dibatalkan; pembayaran tidak dapat dibuat.")
        if invoice_cancel_claim_is_active(claimed):
            raise HTTPException(409, INVOICE_CANCEL_IN_PROGRESS_PAYMENT_CREATE_MESSAGE)
        raise HTTPException(409, INVOICE_REVERSE_IN_PROGRESS_PAYMENT_CREATE_MESSAGE)
    return claimed, token


async def create_purchase_payment(db, payload: Dict[str, Any], user: dict) -> dict:
    """Create DRAFT payment. Does not settle invoice finances."""
    invoice_id = str(payload.get("purchase_invoice_id") or payload.get("invoice_id") or "").strip()
    if not invoice_id:
        raise HTTPException(400, "ID Faktur Pembelian (purchase_invoice_id) wajib diisi.")

    async with get_named_lock("purchase_payment"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                return await _attach_invoice_summary(db, existing)

        invoice = await get_purchase_invoice(db, invoice_id)
        _claimed, token = await _acquire_invoice_for_new_payment(
            db, invoice, claim_token=idem or None
        )
        from services.purchase_invoice_posting import clear_payment_create_claim

        try:
            saved = await _build_draft_doc(db, invoice, payload, user)
            return await _attach_invoice_summary(db, saved)
        finally:
            await clear_payment_create_claim(db, invoice["id"], token=token)


async def list_purchase_payments(
    db,
    q: Optional[str] = None,
    purchase_invoice_id: Optional[str] = None,
    purchase_order_id: Optional[str] = None,
    include_cancelled: bool = False,
    status_filter: Optional[str] = None,
) -> List[dict]:
    """List payment submissions.

    status_filter:
      - DIAJUKAN: submitted, not yet paid (pengajuan aktif)
      - SELESAI: settlement paid (alias for DIBAYAR)
      - ALL / empty: submitted + paid (exclude plain drafts)
      - DRAFT: NOT_SUBMITTED only (legacy; not used by payment page)
    """
    filters: Dict[str, Any] = {}
    if purchase_invoice_id:
        filters["purchase_invoice_id"] = str(purchase_invoice_id)
    if purchase_order_id:
        filters["purchase_order_id"] = str(purchase_order_id)
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"payment_number": {"$regex": needle, "$options": "i"}},
            {"invoice_number": {"$regex": needle, "$options": "i"}},
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"supplier": {"$regex": needle, "$options": "i"}},
        ]

    sf = str(status_filter or "").strip().upper()
    if sf in {"DIAJUKAN", "SUBMITTED"}:
        filters["submission_status"] = SUBMISSION_SUBMITTED
        filters["settlement_status"] = {"$ne": SETTLEMENT_PAID}
    elif sf in {"SELESAI", "DIBAYAR", "PAID", "DONE"}:
        filters["settlement_status"] = SETTLEMENT_PAID
    elif sf in {"DRAFT", "BELUM DIAJUKAN", "NOT_SUBMITTED"}:
        filters["submission_status"] = SUBMISSION_NOT_SUBMITTED
        filters["settlement_status"] = {"$ne": SETTLEMENT_PAID}
    else:
        # Default payment-page view: only submissions from Faktur (submitted or done).
        filters["submission_status"] = SUBMISSION_SUBMITTED

    if not include_cancelled:
        filters = with_active_filter(filters)
    rows = await db[COLLECTION].find(filters, {"_id": 0}).sort(
        [("submitted_at", -1), ("created_at", -1)]
    ).to_list(1000)
    enriched = [_enrich_payment(r) for r in rows]
    from services.purchase_payment_posting import attach_payment_accounting_fields

    enriched = await attach_payment_accounting_fields(db, enriched)
    for row in enriched:
        # List-facing alias: paid submissions show as SELESAI in filter UX.
        if is_settlement_paid(row):
            row["list_status"] = "SELESAI"
        elif str(row.get("submission_status") or "").upper() == SUBMISSION_SUBMITTED:
            row["list_status"] = "DIAJUKAN"
        else:
            row["list_status"] = row.get("workflow_status") or STATUS_BELUM_DIAJUKAN
    return enriched


async def get_purchase_payment(db, payment_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(payment_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"payment_number": str(payment_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Pembayaran Pembelian '{payment_id}' tidak ditemukan.")
    from services.purchase_payment_posting import attach_payment_accounting_fields

    enriched = await _attach_invoice_summary(db, doc)
    await attach_payment_accounting_fields(db, [enriched])
    return enriched


async def cancel_purchase_payment(
    db, payment_id: str, *, user: Optional[dict], reason: str = ""
) -> dict:
    """Soft-cancel unpaid payment (draft or submitted). Paid settlements are blocked."""
    async with get_named_lock("purchase_payment"):
        doc = await get_purchase_payment(db, payment_id)
        if is_cancelled(doc):
            return doc
        from services.purchase_payment_posting import (
            POSTED_CANCEL_MESSAGE,
            find_purchase_payment_source_journal,
            posting_claim_is_active,
        )

        journal = await find_purchase_payment_source_journal(db, doc["id"])
        if journal:
            raise HTTPException(409, POSTED_CANCEL_MESSAGE)
        if posting_claim_is_active(doc):
            raise HTTPException(409, POSTED_CANCEL_MESSAGE)
        if is_settlement_paid(doc):
            raise HTTPException(
                409,
                "Pembayaran sudah dikonfirmasi dibayar dan tidak dapat dihapus langsung.",
            )
        fields = cancellation_fields(user=user, reason=reason)
        await db[COLLECTION].update_one({"id": doc["id"]}, {"$set": fields})
        return await get_purchase_payment(db, doc["id"])


async def list_payment_submit_targets(
    db, q: str = "", *, exclude_user_id: str = ""
) -> List[dict]:
    """Active users eligible as payment submission targets."""
    filters: Dict[str, Any] = {"status": "active"}
    needle = str(q or "").strip()
    if needle:
        filters["$or"] = [
            {"name": {"$regex": needle, "$options": "i"}},
            {"email": {"$regex": needle, "$options": "i"}},
            {"role": {"$regex": needle, "$options": "i"}},
        ]
    rows = await db.users.find(
        filters,
        {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "status": 1},
    ).sort("name", 1).to_list(50)
    out = []
    ex = str(exclude_user_id or "").strip()
    for r in rows:
        uid = str(r.get("id") or "").strip()
        if not uid or (ex and uid == ex):
            continue
        role = r.get("role") or ""
        out.append(
            {
                "id": uid,
                "name": r.get("name") or r.get("email") or uid,
                "email": r.get("email") or "",
                "role": role,
                "team": role,
            }
        )
    return out


async def _send_submission_chat(
    db,
    *,
    doc: dict,
    submitter: dict,
    submitter_id: str,
    target_id: str,
    notes: str,
    client_key: Optional[str],
    submitted_at: Optional[str] = None,
) -> tuple[str, str, str]:
    """Create/reuse personal chat + send submission message. Raises if chat cannot be delivered.

    DIAJUKAN must only be set after this succeeds. Idempotent via client_message_id.
    """
    from services.chat_indexes import ensure_indexes as ensure_chat_indexes
    from services.chat_rooms import get_or_create_dm
    from services.chat_messages import send_message

    try:
        await ensure_chat_indexes(db)
        chat_user = {
            "id": submitter_id,
            "user_id": submitter_id,
            "name": submitter.get("name"),
            "email": submitter.get("email"),
            "role": submitter.get("role"),
        }
        room = await get_or_create_dm(db, chat_user, target_id)
        room_id = str(room.get("id") or "").strip()
        if not room_id:
            raise HTTPException(502, "Gagal membuat ruang chat pengajuan.")

        submitted_at = submitted_at or now_iso()
        # Format Tanggal Pengajuan for chat body (WIB-friendly display).
        try:
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
            dt_wib = dt.astimezone(timezone(timedelta(hours=7)))
            tanggal_pengajuan = dt_wib.strftime("%d/%m/%Y %H:%M")
        except Exception:
            tanggal_pengajuan = submitted_at
        pay_no = doc.get("payment_number") or "—"
        detail_path = f"/pembelian/pembayaran-pembelian?payment_id={doc.get('id')}"
        msg_lines = [
            "Pengajuan Pembayaran",
            "",
            f"No Pembayaran: {pay_no}",
            f"No Faktur: {doc.get('invoice_number') or '—'}",
            f"Supplier: {doc.get('supplier') or '—'}",
            f"Nominal: {_format_rupiah(doc.get('amount'))}",
            f"Diajukan oleh: {submitter.get('name') or '—'}",
            f"Tanggal Pengajuan: {tanggal_pengajuan}",
        ]
        if notes:
            msg_lines.extend(["", f"Catatan: {notes}"])
        msg_lines.extend(
            [
                "",
                f"Detail pembayaran: {detail_path}",
                "Silakan cek di modul Pembelian.",
            ]
        )
        msg_idem = client_key or f"purchase-payment-submit:{doc['id']}"
        message = await send_message(
            db,
            room_id,
            chat_user,
            content="\n".join(msg_lines),
            client_message_id=msg_idem,
        )
        message_id = str(message.get("id") or message.get("message_id") or "").strip()
        if not message_id:
            raise HTTPException(502, "Gagal mengirim pesan chat pengajuan.")
        return room_id, message_id, submitted_at
    except HTTPException:
        raise
    except Exception as ex:
        raise HTTPException(
            502,
            f"Pengajuan dibatalkan karena chat gagal terkirim: {ex}",
        ) from ex



async def _mark_submitted(
    db,
    doc: dict,
    *,
    target: dict,
    submitter: dict,
    notes: str,
    room_id: Optional[str],
    message_id: Optional[str],
    user: dict,
    submitted_at: Optional[str] = None,
) -> dict:
    target_id = str(target.get("id") or "")
    submitter_id = str(submitter.get("id") or "")
    fields = {
        "submission_status": SUBMISSION_SUBMITTED,
        "payment_state": STATUS_DIAJUKAN,
        "workflow_status": STATUS_DIAJUKAN,
        "status": STATUS_DIAJUKAN,
        "submitted_at": submitted_at or now_iso(),
        "submitted_by": submitter,
        "submitted_by_user_id": submitter_id,
        "submitted_by_name": submitter.get("name"),
        "submitted_to_user_id": target_id,
        "submitted_to_name": target.get("name") or target.get("email") or target_id,
        "submitted_to": {
            "id": target_id,
            "name": target.get("name") or target.get("email") or target_id,
            "email": target.get("email") or "",
            "role": target.get("role") or "",
        },
        "submission_notes": notes,
        "submission_chat_room_id": room_id,
        "submission_chat_message_id": message_id,
        **stamp_update(user),
    }
    if notes and not doc.get("notes"):
        fields["notes"] = notes
    await db[COLLECTION].update_one({"id": doc["id"]}, {"$set": fields})
    return await get_purchase_payment(db, doc["id"])


async def submit_purchase_payment(
    db, payment_id: str, payload: Dict[str, Any], user: dict
) -> dict:
    """Submit draft payment to a target user. Does not settle invoice finances."""
    target_id = str(
        payload.get("target_user_id")
        or payload.get("submitted_to_user_id")
        or ""
    ).strip()
    if not target_id:
        raise HTTPException(400, "User tujuan pengajuan wajib dipilih.")
    notes = str(payload.get("notes") or "").strip()
    client_key = str(payload.get("idempotency_key") or "").strip() or None

    async with get_named_lock("purchase_payment_submit"):
        doc = await get_purchase_payment(db, payment_id)
        if is_cancelled(doc):
            raise HTTPException(400, "Pembayaran sudah dibatalkan; tidak dapat diajukan.")
        if is_settlement_paid(doc):
            raise HTTPException(409, "Pembayaran sudah dikonfirmasi dibayar.")

        if doc.get("submission_status") == SUBMISSION_SUBMITTED:
            existing_to = str(doc.get("submitted_to_user_id") or "").strip()
            if existing_to and existing_to != target_id:
                raise HTTPException(409, "Pembayaran sudah diajukan kepada user lain.")
            return {
                "payment": doc,
                "chat_room_id": doc.get("submission_chat_room_id"),
                "message_id": doc.get("submission_chat_message_id"),
                "reused": True,
            }

        target = await db.users.find_one(
            {"id": target_id, "status": "active"}, {"_id": 0, "password_hash": 0}
        )
        if not target:
            raise HTTPException(404, "User tujuan tidak ditemukan atau tidak aktif.")

        submitter = actor_snapshot(user)
        submitter_id = submitter.get("id") or ""
        if not submitter_id:
            raise HTTPException(400, "Identitas pengaju tidak valid.")
        if submitter_id == target_id:
            raise HTTPException(400, "Tidak dapat mengajukan pembayaran kepada diri sendiri.")

        try:
            room_id, message_id, submitted_at = await _send_submission_chat(
                db,
                doc=doc,
                submitter=submitter,
                submitter_id=submitter_id,
                target_id=target_id,
                notes=notes,
                client_key=client_key,
            )
        except HTTPException:
            raise
        except Exception as ex:
            raise HTTPException(
                502,
                f"Pengajuan dibatalkan karena chat gagal terkirim: {ex}",
            ) from ex
        if not room_id or not message_id:
            raise HTTPException(502, "Pengajuan dibatalkan karena chat gagal terkirim.")
        refreshed = await _mark_submitted(
            db,
            doc,
            target=target,
            submitter=submitter,
            notes=notes,
            room_id=room_id,
            message_id=message_id,
            user=user,
            submitted_at=submitted_at,
        )
        return {
            "payment": refreshed,
            "chat_room_id": room_id,
            "message_id": message_id,
            "reused": False,
        }


async def create_and_submit_purchase_payment(
    db, payload: Dict[str, Any], user: dict
) -> dict:
    """Create payment already in DIAJUKAN state + chat notify. No invoice settlement."""
    target_id = str(
        payload.get("target_user_id")
        or payload.get("submitted_to_user_id")
        or ""
    ).strip()
    if not target_id:
        raise HTTPException(400, "User tujuan pengajuan wajib dipilih.")
    notes = str(payload.get("notes") or "").strip()
    client_key = str(payload.get("idempotency_key") or "").strip() or None

    invoice_id = str(payload.get("purchase_invoice_id") or payload.get("invoice_id") or "").strip()
    if not invoice_id:
        raise HTTPException(400, "ID Faktur Pembelian (purchase_invoice_id) wajib diisi.")

    async with get_named_lock("purchase_payment"):
        existing_draft = None
        if client_key:
            existing = await db[COLLECTION].find_one({"idempotency_key": client_key}, {"_id": 0})
            if existing:
                if (
                    existing.get("submission_status") == SUBMISSION_SUBMITTED
                    and existing.get("submission_chat_room_id")
                    and existing.get("submission_chat_message_id")
                ):
                    enriched = await _attach_invoice_summary(db, existing)
                    return {
                        "payment": enriched,
                        "chat_room_id": existing.get("submission_chat_room_id"),
                        "message_id": existing.get("submission_chat_message_id"),
                        "reused": True,
                    }
                # Draft created earlier but chat/submit incomplete — retry on same doc.
                if is_cancelled(existing) or is_settlement_paid(existing):
                    enriched = await _attach_invoice_summary(db, existing)
                    return {
                        "payment": enriched,
                        "chat_room_id": existing.get("submission_chat_room_id"),
                        "message_id": existing.get("submission_chat_message_id"),
                        "reused": True,
                    }
                existing_draft = existing

        invoice = await get_purchase_invoice(db, invoice_id)
        if is_cancelled(invoice):
            raise HTTPException(400, "Faktur sudah dibatalkan; pembayaran tidak dapat dibuat.")

        target = await db.users.find_one(
            {"id": target_id, "status": "active"}, {"_id": 0, "password_hash": 0}
        )
        if not target:
            raise HTTPException(404, "User tujuan tidak ditemukan atau tidak aktif.")

        submitter = actor_snapshot(user)
        submitter_id = submitter.get("id") or ""
        if not submitter_id:
            raise HTTPException(400, "Identitas pengaju tidak valid.")
        if submitter_id == target_id:
            raise HTTPException(400, "Tidak dapat mengajukan pembayaran kepada diri sendiri.")

        draft_payload = dict(payload)
        if notes:
            draft_payload["notes"] = notes
        if existing_draft:
            saved = existing_draft
        else:
            from services.purchase_invoice_posting import clear_payment_create_claim

            _claimed, token = await _acquire_invoice_for_new_payment(
                db, invoice, claim_token=client_key or None
            )
            try:
                saved = await _build_draft_doc(db, invoice, draft_payload, user)
            finally:
                await clear_payment_create_claim(db, invoice["id"], token=token)

        try:
            room_id, message_id, submitted_at = await _send_submission_chat(
                db,
                doc=saved,
                submitter=submitter,
                submitter_id=submitter_id,
                target_id=target_id,
                notes=notes,
                client_key=client_key,
            )
        except HTTPException:
            raise
        except Exception as ex:
            raise HTTPException(
                502,
                f"Pengajuan dibatalkan karena chat gagal terkirim: {ex}",
            ) from ex
        if not room_id or not message_id:
            raise HTTPException(502, "Pengajuan dibatalkan karena chat gagal terkirim.")
        refreshed = await _mark_submitted(
            db,
            saved,
            target=target,
            submitter=submitter,
            notes=notes,
            room_id=room_id,
            message_id=message_id,
            user=user,
            submitted_at=submitted_at,
        )
        return {
            "payment": refreshed,
            "chat_room_id": room_id,
            "message_id": message_id,
            "reused": False,
        }


async def confirm_purchase_payment(db, payment_id: str, user: dict) -> dict:
    """Confirm settlement: only then invoice paid_total / outstanding update."""
    async with get_named_lock("purchase_payment"):
        doc = await get_purchase_payment(db, payment_id)
        if is_cancelled(doc):
            raise HTTPException(400, "Pembayaran sudah dibatalkan.")
        if is_settlement_paid(doc):
            return {"payment": doc, "reused": True, "invoice": None}

        if doc.get("submission_status") != SUBMISSION_SUBMITTED:
            raise HTTPException(400, "Pembayaran harus diajukan sebelum dikonfirmasi dibayar.")

        invoice = await get_purchase_invoice(db, doc["purchase_invoice_id"])
        if is_cancelled(invoice):
            raise HTTPException(400, "Faktur sudah dibatalkan.")

        amount = money_round(doc.get("amount"))
        outstanding = money_round(invoice.get("outstanding_amount"))
        if amount > outstanding + 1e-9:
            raise HTTPException(
                400,
                f"Konfirmasi ditolak: nominal (Rp {amount:,.2f}) melebihi sisa tagihan (Rp {outstanding:,.2f}).",
            )

        actor = actor_snapshot(user)
        ts = now_iso()
        fields = {
            "settlement_status": SETTLEMENT_PAID,
            "payment_state": STATUS_DIBAYAR,
            "workflow_status": STATUS_DIBAYAR,
            "status": STATUS_DIBAYAR,
            "paid_at": ts,
            "paid_by": actor,
            "paid_by_user_id": actor.get("id"),
            "paid_by_name": actor.get("name"),
            **stamp_update(user),
        }
        await db[COLLECTION].update_one({"id": doc["id"]}, {"$set": fields})
        refreshed = await get_purchase_payment(db, doc["id"])
        invoice_after = await get_purchase_invoice(db, doc["purchase_invoice_id"])
        return {"payment": refreshed, "reused": False, "invoice": invoice_after}


async def repair_dev_payment_settlements(db) -> dict:
    """DEV: backfill settlement_status + sync stored workflow labels."""
    payments = await db[COLLECTION].find({}, {"_id": 0}).to_list(5000)
    settlement_backfilled = 0
    workflow_synced = 0
    for p in payments:
        patch: Dict[str, Any] = {}
        settle = str(p.get("settlement_status") or "").upper()
        if settle not in {SETTLEMENT_PAID, SETTLEMENT_UNPAID}:
            if p.get("paid_at") or p.get("paid_by"):
                patch["settlement_status"] = SETTLEMENT_PAID
            else:
                patch["settlement_status"] = SETTLEMENT_UNPAID
            settlement_backfilled += 1

        merged = {**p, **patch}
        if not merged.get("submission_status"):
            patch["submission_status"] = SUBMISSION_NOT_SUBMITTED
            merged["submission_status"] = SUBMISSION_NOT_SUBMITTED
        workflow = payment_workflow_status(merged)
        if (
            p.get("payment_state") != workflow
            or p.get("status") != workflow
            or p.get("workflow_status") != workflow
        ):
            patch["payment_state"] = workflow
            patch["status"] = workflow
            patch["workflow_status"] = workflow
            workflow_synced += 1

        if patch:
            await db[COLLECTION].update_one({"id": p["id"]}, {"$set": patch})

    return {
        "payments_settlement_backfilled": settlement_backfilled,
        "payments_workflow_synced": workflow_synced,
        "payment_count": len(payments),
    }
