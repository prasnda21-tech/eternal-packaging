"""Read-only PWA Sales HTTP. Session sales_user_id is the only authorization source."""
from __future__ import annotations

from typing import Optional

from fastapi import File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import Response as FastAPIResponse

from services.sales_user_mapping import is_sales_pwa_role, session_user_id


class SalesPwaVisitPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    outlet_id: str
    result: str = ""
    next_action: str = ""
    next_follow_up_at: Optional[str] = None
    notes: str = ""
    visit_photo_ids: list[str] = Field(default_factory=list)


def register_sales_pwa_routes(
    api_router,
    *,
    get_db,
    current_user,
    Depends,
    obj_storage,
    user_permissions=None,
):
    from services import sales_pwa as spwa
    from services.sales_invoices import generate_sales_invoice_pdf_bytes
    from services.sales_user_mapping import SALES_PWA_PERM

    async def _pwa_user(user=Depends(current_user)):
        spwa.require_sales_pwa(user)
        if user_permissions is not None:
            perms = await user_permissions(user)
            if SALES_PWA_PERM not in perms:
                raise HTTPException(403, "PWA Sales hanya untuk role sales_eternal")
        return user

    def _ignore_spoofed_sales_id(sales_user_id: Optional[str], sales_id: Optional[str]) -> None:
        # Query/body spoof is ignored. Authorization is session-only.
        del sales_user_id, sales_id

    async def _file_bytes(path: str):
        try:
            data, ct = obj_storage.get_object(path)
        except FileNotFoundError:
            raise HTTPException(404, "File tidak ditemukan")
        except Exception:
            raise HTTPException(502, "Gagal mengambil file.")
        return data, ct

    @api_router.get("/sales-pwa/me")
    async def sales_pwa_me(
        sales_user_id: Optional[str] = None,
        sales_id: Optional[str] = None,
        user=Depends(_pwa_user),
    ):
        _ignore_spoofed_sales_id(sales_user_id, sales_id)
        profile = await spwa.profile_summary(get_db(), user)
        return profile

    @api_router.get("/sales-pwa/orders")
    async def sales_pwa_orders(
        q: Optional[str] = None,
        customer_q: Optional[str] = None,
        po_q: Optional[str] = None,
        stage: Optional[str] = None,
        buffer: Optional[str] = None,
        progress_min: Optional[int] = None,
        progress_max: Optional[int] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sort: Optional[str] = None,
        direction: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
        sales_user_id: Optional[str] = None,
        sales_id: Optional[str] = None,
        user=Depends(_pwa_user),
    ):
        _ignore_spoofed_sales_id(sales_user_id, sales_id)
        return await spwa.list_sales_pwa_orders(
            get_db(),
            user,
            q=q or "",
            customer_q=customer_q or "",
            po_q=po_q or "",
            stage=stage or "",
            buffer=buffer or "",
            progress_min=progress_min,
            progress_max=progress_max,
            status=status or "",
            date_from=date_from or "",
            date_to=date_to or "",
            sort=sort or "order_date",
            direction=direction or "desc",
            skip=skip,
            limit=limit,
        )

    @api_router.get("/sales-pwa/orders/{order_id}")
    async def sales_pwa_order_detail(
        order_id: str,
        sales_user_id: Optional[str] = None,
        sales_id: Optional[str] = None,
        user=Depends(_pwa_user),
    ):
        _ignore_spoofed_sales_id(sales_user_id, sales_id)
        return await spwa.get_sales_pwa_order(get_db(), user, order_id)

    @api_router.get("/sales-pwa/orders/{order_id}/documents")
    async def sales_pwa_order_documents(
        order_id: str,
        sales_user_id: Optional[str] = None,
        sales_id: Optional[str] = None,
        user=Depends(_pwa_user),
    ):
        _ignore_spoofed_sales_id(sales_user_id, sales_id)
        return await spwa.get_sales_pwa_documents(get_db(), user, order_id)

    @api_router.get("/sales-pwa/orders/{order_id}/files/po")
    async def sales_pwa_po_file(order_id: str, user=Depends(_pwa_user)):
        db = get_db()
        order = await spwa.require_owned_order(db, user, order_id)
        path = str((order.get("source_pdf") or {}).get("storage_path") or "").strip()
        if not path:
            raise HTTPException(404, "Dokumen PO belum tersedia")
        data, ct = await _file_bytes(path)
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
        return FastAPIResponse(content=data, media_type=ct or "application/pdf", headers=headers)

    @api_router.get("/sales-pwa/orders/{order_id}/files/sj/{shipment_id}")
    async def sales_pwa_sj_file(order_id: str, shipment_id: str, user=Depends(_pwa_user)):
        db = get_db()
        order = await spwa.require_owned_order(db, user, order_id)
        sid = str(shipment_id or "").strip()
        delivery = await db.sales_deliveries.find_one(
            {"id": sid, "po_id": order.get("id"), "is_cancelled": {"$ne": True}},
            {"_id": 0, "id": 1},
        )
        if not delivery:
            raise HTTPException(404, "Surat Jalan tidak ditemukan")
        entry = spwa.sj_file_for_shipment(order.get("surat_jalan_files"), sid)
        kind, path = spwa.sj_byte_source(entry)
        if not path:
            raise HTTPException(404, "File Surat Jalan belum tersedia")
        try:
            data, ct = await _file_bytes(path)
        except HTTPException as exc:
            if exc.status_code == 404 and kind == "signed":
                raise HTTPException(404, "PDF Surat Jalan bertanda tangan tidak ditemukan")
            raise
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
        return FastAPIResponse(content=data, media_type=ct or "application/pdf", headers=headers)

    @api_router.get("/sales-pwa/orders/{order_id}/files/invoices/{invoice_id}")
    async def sales_pwa_invoice_file(order_id: str, invoice_id: str, user=Depends(_pwa_user)):
        db = get_db()
        order = await spwa.require_owned_order(db, user, order_id)
        inv = await db.sales_invoices.find_one(
            {"id": invoice_id, "po_id": order.get("id"), "is_cancelled": {"$ne": True}},
            {"_id": 0},
        )
        if not inv:
            raise HTTPException(404, "Invoice tidak ditemukan")
        pdf_bytes = generate_sales_invoice_pdf_bytes(inv)
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="{(inv.get("invoice_number") or "invoice")}.pdf"',
        }
        return FastAPIResponse(content=pdf_bytes, media_type="application/pdf", headers=headers)

    @api_router.get("/sales-pwa/orders/{order_id}/files/proofs/{proof_id}")
    async def sales_pwa_proof_file(order_id: str, proof_id: str, user=Depends(_pwa_user)):
        db = get_db()
        order = await spwa.require_owned_order(db, user, order_id)
        proof = None
        for p in order.get("delivery_proofs") or []:
            if isinstance(p, dict) and str(p.get("id") or "") == str(proof_id):
                proof = p
                break
        if not proof:
            raise HTTPException(404, "Bukti kirim tidak ditemukan")
        path = str(proof.get("storage_path") or "").strip()
        if not path:
            raise HTTPException(404, "File bukti kirim belum tersedia")
        data, ct = await _file_bytes(path)
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
        return FastAPIResponse(content=data, media_type=ct or "image/jpeg", headers=headers)

    @api_router.get("/sales-pwa/customers")
    async def sales_pwa_customers(
        q: Optional[str] = None,
        sales_user_id: Optional[str] = None,
        sales_id: Optional[str] = None,
        user=Depends(_pwa_user),
    ):
        _ignore_spoofed_sales_id(sales_user_id, sales_id)
        return await spwa.list_sales_pwa_customers(get_db(), user, q=q or "")

    @api_router.get("/sales-pwa/predictions")
    async def sales_pwa_predictions(
        sales_user_id: Optional[str] = None,
        sales_id: Optional[str] = None,
        user=Depends(_pwa_user),
    ):
        _ignore_spoofed_sales_id(sales_user_id, sales_id)
        return await spwa.build_sales_pwa_predictions(get_db(), user)

    @api_router.get("/sales-pwa/predictions/{customer_key}")
    async def sales_pwa_prediction_detail(
        customer_key: str,
        sales_user_id: Optional[str] = None,
        sales_id: Optional[str] = None,
        user=Depends(_pwa_user),
    ):
        _ignore_spoofed_sales_id(sales_user_id, sales_id)
        return await spwa.build_sales_pwa_predictions(get_db(), user, customer_key=customer_key)

    @api_router.get("/sales-pwa/dashboard")
    async def sales_pwa_dashboard(
        month: Optional[str] = None,
        sales_user_id: Optional[str] = None,
        sales_id: Optional[str] = None,
        user=Depends(_pwa_user),
    ):
        _ignore_spoofed_sales_id(sales_user_id, sales_id)
        return await spwa.sales_pwa_dashboard(get_db(), user, month=month or "")

    @api_router.get("/sales-pwa/visits")
    async def sales_pwa_visits(
        month: Optional[str] = None,
        q: Optional[str] = None,
        sales_user_id: Optional[str] = None,
        sales_id: Optional[str] = None,
        user=Depends(_pwa_user),
    ):
        _ignore_spoofed_sales_id(sales_user_id, sales_id)
        return await spwa.list_sales_pwa_visits(get_db(), user, month=month or "", q=q or "")

    @api_router.post("/sales-pwa/visits")
    async def create_sales_pwa_visit(
        payload: SalesPwaVisitPayload,
        user=Depends(_pwa_user),
    ):
        if user_permissions is not None:
            perms = await user_permissions(user)
            if "sales.pwa.visit.create" not in perms:
                raise HTTPException(403, "Tidak memiliki izin mencatat visit")
        return await spwa.create_sales_pwa_visit(get_db(), user, payload.model_dump())

    @api_router.post("/sales-pwa/visits/photos")
    async def upload_sales_pwa_visit_photo(
        file: UploadFile = File(...),
        outlet_id: str = Form(...),
        latitude: str = Form(""),
        longitude: str = Form(""),
        accuracy: str = Form(""),
        location_status: str = Form(""),
        client_captured_at: str = Form(""),
        user=Depends(_pwa_user),
    ):
        if user_permissions is not None:
            perms = await user_permissions(user)
            if "sales.pwa.visit.create" not in perms:
                raise HTTPException(403, "Tidak memiliki izin mencatat visit")
        from services.sales_activity import ownership as ownership_svc
        from services.sales_activity import photos as photo_svc
        from services.sales_activity.constants import PERM_ACTIVITY_CREATE

        snap = await ownership_svc.get_ownership(get_db(), outlet_id)
        if not snap or snap.get("current_owner_id") != session_user_id(user) or snap.get("in_pool"):
            raise HTTPException(404, "Outlet tidak ditemukan")
        content = await file.read()
        return await photo_svc.upload_visit_photo(
            get_db(),
            user=user,
            perms={PERM_ACTIVITY_CREATE},
            outlet_id=outlet_id,
            filename=file.filename or "visit.jpg",
            content_type=file.content_type or "",
            data=content,
            latitude=latitude,
            longitude=longitude,
            accuracy=accuracy,
            location_status_hint=location_status,
            client_captured_at=client_captured_at,
            activity_id="",
            uploaded_by="",
            sales_id="",
            owner_id="",
        )

    @api_router.get("/sales-pwa/visits/photos/{photo_id}/file")
    async def sales_pwa_visit_photo(photo_id: str, user=Depends(_pwa_user)):
        from services.sales_activity import photos as photo_svc
        from services.sales_activity.constants import PERM_ACTIVITY_CREATE

        data, media, filename = await photo_svc.download_photo(
            get_db(),
            photo_id,
            user,
            {PERM_ACTIVITY_CREATE},
        )
        return FastAPIResponse(
            content=data,
            media_type=media,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
                "Content-Disposition": f'inline; filename="{filename}"',
            },
        )

    # PO, documents, financial summaries, and ownership remain read-only.
    # The only PWA mutation is a scoped visit for the authenticated owner.
