"""HTTP routes for purchasing + HPP. Registered onto the existing api_router."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_purchase_routes(api_router, *, get_db, current_user, require_perm, require_any_perm, Depends):
    from services.purchase_requests import (
        create_purchase_request,
        get_purchase_request,
        list_purchase_requests,
    )
    from services.purchase_orders import (
        create_purchase_order,
        get_purchase_order,
        get_request_po_preview,
        list_purchase_orders,
    )
    from services.purchase_goods_receipts import (
        allocate_accurate_receipt_line,
        create_purchase_goods_receipt,
        get_po_receipt_preview,
        list_purchase_goods_receipts,
    )
    from services.purchase_invoices import (
        create_purchase_invoice,
        get_po_invoice_preview,
        get_purchase_invoice,
        list_purchase_invoices,
    )
    from services.purchase_payments import (
        create_purchase_payment,
        get_purchase_payment,
        list_purchase_payments,
    )
    from services.hpp_valuation import (
        get_hpp_settings,
        query_cost_boundary,
        recalculate_hpp,
        update_hpp_settings,
    )
    from services.master_items import list_master_items

    def _dump(payload: LoosePayload) -> dict:
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        return payload.dict()

    @api_router.get("/purchase-requests")
    async def list_purchase_requests_endpoint(
        q: Optional[str] = None,
        eligible_for_po: bool = False,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_requests.view", "purchase_orders.view", "purchase_requests.create")
        rows = await list_purchase_requests(get_db(), q=q, eligible_for_po=eligible_for_po)
        return {"requests": rows, "count": len(rows)}

    @api_router.get("/purchase-requests/options")
    async def purchase_request_options(user=Depends(current_user)):
        await require_any_perm(user, "purchase_requests.view", "purchase_requests.create", "purchase_orders.create")
        items = await list_master_items(get_db(), q="", page=1, page_size=200)
        return {"items": items.get("items") if isinstance(items, dict) else items}

    @api_router.get("/purchase-requests/{request_id}")
    async def get_purchase_request_endpoint(request_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_requests.view", "purchase_orders.create", "purchase_orders.view")
        return {"request": await get_purchase_request(get_db(), request_id)}

    @api_router.post("/purchase-requests")
    async def create_purchase_request_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_requests.create")
        doc = await create_purchase_request(get_db(), _dump(payload), user)
        return {"request": doc, "message": "Permintaan berhasil dibuat"}

    @api_router.get("/purchase-orders")
    async def list_purchase_orders_endpoint(
        q: Optional[str] = None,
        request_id: Optional[str] = None,
        eligible_for_invoice: bool = False,
        eligible_for_receipt: bool = False,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_orders.view", "purchase_invoices.view", "purchase_receipts.view")
        rows = await list_purchase_orders(
            get_db(), q=q, request_id=request_id,
            eligible_for_invoice=eligible_for_invoice,
            eligible_for_receipt=eligible_for_receipt,
        )
        return {"orders": rows, "count": len(rows)}

    @api_router.get("/purchase-orders/from-request/{request_id}/preview")
    async def purchase_order_preview_endpoint(request_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_orders.view", "purchase_orders.create", "purchase_requests.view")
        return await get_request_po_preview(get_db(), request_id)

    @api_router.get("/purchase-orders/{po_id}")
    async def get_purchase_order_endpoint(po_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_orders.view", "purchase_invoices.create", "purchase_receipts.view")
        return {"order": await get_purchase_order(get_db(), po_id)}

    @api_router.post("/purchase-orders")
    async def create_purchase_order_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_orders.create")
        doc = await create_purchase_order(get_db(), _dump(payload), user)
        return {"order": doc, "message": "Pesanan Pembelian berhasil dibuat"}

    @api_router.get("/purchase-goods-receipts")
    async def list_po_goods_receipts_endpoint(
        q: Optional[str] = None,
        purchase_order_id: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_receipts.view", "purchase_orders.view")
        rows = await list_purchase_goods_receipts(get_db(), q=q, purchase_order_id=purchase_order_id)
        return {"receipts": rows, "count": len(rows)}

    @api_router.get("/purchase-goods-receipts/from-po/{po_id}/preview")
    async def po_receipt_preview_endpoint(po_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_receipts.view", "purchase_receipts.receive", "purchase_orders.view")
        return await get_po_receipt_preview(get_db(), po_id)

    @api_router.post("/purchase-goods-receipts")
    async def create_po_goods_receipt_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_receipts.receive", "purchase_receipts.sync")
        doc = await create_purchase_goods_receipt(get_db(), _dump(payload), user)
        return {"receipt": doc, "message": "Penerimaan terhadap Pesanan Pembelian tercatat"}

    @api_router.post("/purchase-receipts/allocate")
    async def allocate_accurate_receipt_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_receipts.receive", "purchase_receipts.sync")
        doc = await allocate_accurate_receipt_line(get_db(), _dump(payload), user)
        return {"allocation": doc, "message": "Baris Penerimaan Accurate dihubungkan ke Pesanan Pembelian"}

    @api_router.get("/purchase-invoices")
    async def list_purchase_invoices_endpoint(
        q: Optional[str] = None,
        purchase_order_id: Optional[str] = None,
        with_outstanding: bool = False,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_invoices.view", "purchase_payments.view", "purchase_orders.view")
        rows = await list_purchase_invoices(get_db(), q=q, purchase_order_id=purchase_order_id, with_outstanding=with_outstanding)
        return {"invoices": rows, "count": len(rows)}

    @api_router.get("/purchase-invoices/from-po/{po_id}/preview")
    async def purchase_invoice_preview_endpoint(po_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_invoices.view", "purchase_invoices.create", "purchase_orders.view")
        return await get_po_invoice_preview(get_db(), po_id)

    @api_router.get("/purchase-invoices/{invoice_id}")
    async def get_purchase_invoice_endpoint(invoice_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_invoices.view", "purchase_payments.create", "purchase_payments.view")
        return {"invoice": await get_purchase_invoice(get_db(), invoice_id)}

    @api_router.post("/purchase-invoices")
    async def create_purchase_invoice_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_invoices.create")
        doc = await create_purchase_invoice(get_db(), _dump(payload), user)
        return {"invoice": doc, "message": "Faktur Pembelian berhasil dibuat"}

    @api_router.get("/purchase-payments")
    async def list_purchase_payments_endpoint(
        q: Optional[str] = None,
        purchase_invoice_id: Optional[str] = None,
        purchase_order_id: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_payments.view", "purchase_invoices.view")
        rows = await list_purchase_payments(get_db(), q=q, purchase_invoice_id=purchase_invoice_id, purchase_order_id=purchase_order_id)
        return {"payments": rows, "count": len(rows)}

    @api_router.get("/purchase-payments/{payment_id}")
    async def get_purchase_payment_endpoint(payment_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_payments.view", "purchase_invoices.view")
        return {"payment": await get_purchase_payment(get_db(), payment_id)}

    @api_router.post("/purchase-payments")
    async def create_purchase_payment_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_payments.create")
        doc = await create_purchase_payment(get_db(), _dump(payload), user)
        return {"payment": doc, "message": "Pembayaran Pembelian berhasil dicatat"}

    @api_router.get("/settings/hpp")
    async def get_hpp_settings_endpoint(user=Depends(current_user)):
        await require_any_perm(user, "settings.hpp.view", "settings.hpp.manage", "permissions.manage")
        return {"settings": await get_hpp_settings(get_db())}

    @api_router.put("/settings/hpp")
    async def put_hpp_settings_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "settings.hpp.manage")
        settings = await update_hpp_settings(get_db(), _dump(payload), user)
        return {"settings": settings, "message": "Pengaturan HPP disimpan dan histori dihitung ulang"}

    @api_router.post("/settings/hpp/recalculate")
    async def recalc_hpp_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "settings.hpp.manage")
        data = _dump(payload)
        current = await get_hpp_settings(get_db())
        method = data.get("method") or current.get("method")
        recognition = data.get("recognition_point") or data.get("recognition") or current.get("recognition_point")
        settings = await recalculate_hpp(get_db(), method=method, recognition_point=recognition, user=user)
        return {"settings": settings, "message": "Perhitungan ulang HPP selesai"}

    @api_router.get("/hpp/cost-query")
    async def hpp_cost_query_endpoint(
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        item_identity: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "settings.hpp.view", "settings.hpp.manage", "purchase_orders.view")
        return await query_cost_boundary(get_db(), date_from=date_from, date_to=date_to, item_identity_key=item_identity)
