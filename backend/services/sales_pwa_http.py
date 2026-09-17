"""Read-only PWA Sales HTTP. Session sales_user_id is the only authorization source."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query
from fastapi.responses import Response as FastAPIResponse

from services.sales_user_mapping import is_sales_pwa_role, session_user_id


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
            status=status or "",
            date_from=date_from or "",
            date_to=date_to or "",
            sort=sort or "tanggal_kirim",
            direction=direction or "asc",
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
        path = ""
        for f in order.get("surat_jalan_files") or []:
            if isinstance(f, dict) and str(f.get("shipment_id") or "") == sid:
                path = str(f.get("storage_path") or "").strip()
                if path:
                    break
        if not path:
            raise HTTPException(404, "File Surat Jalan belum tersedia")
        data, ct = await _file_bytes(path)
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

    # Mutation verbs are not registered for /sales-pwa/*.
