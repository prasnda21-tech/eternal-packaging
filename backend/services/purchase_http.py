"""HTTP routes for purchasing + HPP. Registered onto the existing api_router."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from fastapi import HTTPException, Request
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_purchase_routes(api_router, *, get_db, current_user, require_perm, require_any_perm, Depends):
    from services.purchase_requests import (
        cancel_purchase_request,
        create_purchase_request,
        get_purchase_request,
        list_purchase_requests,
    )
    from services.purchase_orders import (
        cancel_purchase_order,
        create_purchase_order,
        get_purchase_order,
        get_request_po_preview,
        list_purchase_orders,
    )
    from services.purchase_goods_receipts import (
        allocate_accurate_receipt_line,
        cancel_purchase_goods_receipt,
        create_purchase_goods_receipt,
        get_po_receipt_preview,
        get_purchase_goods_receipt,
        list_purchase_goods_receipts,
    )
    from services.purchase_invoices import (
        cancel_purchase_invoice,
        create_purchase_invoice,
        get_po_invoice_preview,
        get_purchase_invoice,
        get_receipt_invoice_preview,
        list_purchase_invoices,
    )
    from services.purchase_payments import (
        cancel_purchase_payment,
        confirm_purchase_payment,
        create_and_submit_purchase_payment,
        create_purchase_payment,
        get_purchase_payment,
        list_payment_submit_targets,
        list_purchase_payments,
        submit_purchase_payment,
    )
    from services.purchase_supplier_advances import (
        cancel_purchase_supplier_advance,
        confirm_purchase_supplier_advance,
        create_purchase_supplier_advance,
        get_purchase_supplier_advance,
        list_purchase_supplier_advances,
        submit_purchase_supplier_advance,
        supplier_advance_availability,
    )
    from services.purchase_supplier_advance_allocations import (
        create_allocation,
        list_allocations,
        reverse_allocation,
    )
    from services.purchase_returns import (
        cancel_purchase_return,
        confirm_purchase_return,
        create_purchase_return,
        get_purchase_return,
        list_purchase_returns,
    )
    from services.purchase_return_finance import (
        allocate_ambiguous_return,
        list_supplier_credits,
        read_return_financial_state,
    )
    from services.purchase_invoice_posting import (
        PERM_POST as PURCHASE_INVOICE_POST_PERM,
        PERM_REVERSE as PURCHASE_INVOICE_REVERSE_PERM,
        post_purchase_invoice_accounting,
        reverse_purchase_invoice_accounting,
    )
    from services.purchase_goods_receipt_posting import (
        PERM_POST as PURCHASE_RECEIPT_POST_PERM,
        PERM_REVERSE as PURCHASE_RECEIPT_REVERSE_PERM,
        get_goods_receipt_accounting_view,
        post_purchase_goods_receipt_accounting,
        reverse_purchase_goods_receipt_accounting,
    )
    from services.purchase_payment_posting import (
        PERM_POST as PURCHASE_PAYMENT_POST_PERM,
        PERM_REVERSE as PURCHASE_PAYMENT_REVERSE_PERM,
        list_cash_bank_options,
        post_purchase_payment_accounting,
        reverse_purchase_payment_accounting,
    )
    from services.purchase_common import actor_user_id
    from services.hpp_valuation import (
        get_hpp_settings,
        query_cost_boundary,
        recalculate_hpp,
        update_hpp_settings,
    )
    from services.master_items import list_master_items

    def _dump(payload: Optional[LoosePayload]) -> dict:
        if payload is None:
            return {}
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

    @api_router.post("/purchase-requests/{request_id}/cancel")
    async def cancel_purchase_request_endpoint(
        request_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_requests.create")
        data = _dump(payload) if payload is not None else {}
        doc = await cancel_purchase_request(get_db(), request_id, user, reason=str(data.get("reason") or ""))
        return {"request": doc, "message": "Permintaan dibatalkan"}

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

    @api_router.post("/purchase-orders/{po_id}/cancel")
    async def cancel_purchase_order_endpoint(
        po_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_orders.create")
        data = _dump(payload) if payload is not None else {}
        doc = await cancel_purchase_order(get_db(), po_id, user, reason=str(data.get("reason") or ""))
        return {"order": doc, "message": "Pesanan Pembelian dibatalkan"}

    @api_router.get("/purchase-goods-receipts")
    async def list_po_goods_receipts_endpoint(
        q: Optional[str] = None,
        purchase_order_id: Optional[str] = None,
        include_cancelled: bool = False,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_receipts.view", "purchase_orders.view")
        rows = await list_purchase_goods_receipts(
            get_db(),
            q=q,
            purchase_order_id=purchase_order_id,
            include_cancelled=include_cancelled,
        )
        return {"receipts": rows, "count": len(rows)}

    @api_router.get("/purchase-goods-receipts/from-po/{po_id}/preview")
    async def po_receipt_preview_endpoint(po_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_receipts.view", "purchase_receipts.receive", "purchase_orders.view")
        return await get_po_receipt_preview(get_db(), po_id)

    @api_router.get("/purchase-goods-receipts/{receipt_id}")
    async def get_po_goods_receipt_endpoint(receipt_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_receipts.view", "purchase_orders.view", "purchase_invoices.create")
        return {"receipt": await get_purchase_goods_receipt(get_db(), receipt_id)}

    @api_router.post("/purchase-goods-receipts")
    async def create_po_goods_receipt_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_receipts.receive", "purchase_receipts.sync")
        doc = await create_purchase_goods_receipt(get_db(), _dump(payload), user)
        return {"receipt": doc, "message": "Penerimaan terhadap Pesanan Pembelian tercatat"}

    @api_router.get("/purchase-returns")
    async def list_purchase_returns_endpoint(
        q: Optional[str] = None,
        purchase_order_id: Optional[str] = None,
        include_cancelled: bool = False,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_receipts.view", "purchase_orders.view")
        rows = await list_purchase_returns(
            get_db(),
            q=q,
            purchase_order_id=purchase_order_id,
            include_cancelled=include_cancelled,
        )
        return {"returns": rows, "count": len(rows)}

    @api_router.get("/purchase-returns/{return_id}")
    async def get_purchase_return_endpoint(return_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_receipts.view", "purchase_orders.view")
        return {"return": await get_purchase_return(get_db(), return_id)}

    @api_router.post("/purchase-returns")
    async def create_purchase_return_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_receipts.receive", "purchase_receipts.sync")
        doc = await create_purchase_return(get_db(), _dump(payload), user)
        return {"return": doc, "message": "Draf retur pembelian tercatat"}

    @api_router.post("/purchase-returns/{return_id}/confirm")
    async def confirm_purchase_return_endpoint(
        return_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_receipts.receive", "purchase_receipts.sync")
        doc = await confirm_purchase_return(get_db(), return_id, _dump(payload), user)
        return {"return": doc, "message": "Retur pembelian dikonfirmasi"}

    @api_router.get("/purchase-returns/{return_id}/financial")
    async def purchase_return_financial_endpoint(return_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_receipts.view", "purchase_orders.view", "purchase_invoices.view")
        return await read_return_financial_state(get_db(), return_id)

    @api_router.post("/purchase-returns/{return_id}/financial-allocations")
    async def purchase_return_financial_allocation_endpoint(
        return_id: str,
        payload: LoosePayload,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_receipts.receive", "purchase_invoices.create")
        return await allocate_ambiguous_return(get_db(), return_id, _dump(payload), user)

    @api_router.get("/purchase-supplier-credits")
    async def list_purchase_supplier_credits_endpoint(
        supplier: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_invoices.view", "purchase_orders.view", "purchase_payments.view")
        rows = await list_supplier_credits(get_db(), supplier=supplier)
        return {"supplier_credits": rows, "count": len(rows)}

    @api_router.post("/purchase-returns/{return_id}/cancel")
    async def cancel_purchase_return_endpoint(
        return_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_receipts.receive", "purchase_receipts.sync")
        data = _dump(payload) if payload is not None else {}
        doc = await cancel_purchase_return(get_db(), return_id, user, reason=str(data.get("reason") or ""))
        return {"return": doc, "message": "Retur pembelian dibatalkan"}

    @api_router.post("/purchase-goods-receipts/{receipt_id}/cancel")
    async def cancel_po_goods_receipt_endpoint(
        receipt_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_receipts.receive", "purchase_receipts.sync")
        data = _dump(payload) if payload is not None else {}
        doc = await cancel_purchase_goods_receipt(
            get_db(), receipt_id, user, reason=str(data.get("reason") or "")
        )
        return {"receipt": doc, "message": "Penerimaan Barang dibatalkan"}

    @api_router.get("/purchase-goods-receipts/{receipt_id}/accounting-readiness")
    async def goods_receipt_accounting_readiness_endpoint(receipt_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_receipts.view", "purchase_orders.view", PURCHASE_RECEIPT_POST_PERM)
        return {"readiness": await get_goods_receipt_accounting_view(get_db(), receipt_id)}

    @api_router.post("/purchase-goods-receipts/{receipt_id}/post-accounting")
    async def post_purchase_goods_receipt_accounting_endpoint(receipt_id: str, user=Depends(current_user)):
        await require_any_perm(user, PURCHASE_RECEIPT_POST_PERM)
        receipt = await post_purchase_goods_receipt_accounting(get_db(), receipt_id, user)
        return {"receipt": receipt, "message": "Penerimaan Barang diposting ke accounting"}

    @api_router.post("/purchase-goods-receipts/{receipt_id}/reverse-accounting")
    async def reverse_purchase_goods_receipt_accounting_endpoint(
        receipt_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, PURCHASE_RECEIPT_REVERSE_PERM)
        data = _dump(payload) if payload is not None else {}
        receipt = await reverse_purchase_goods_receipt_accounting(
            get_db(), receipt_id, user, data.get("reason")
        )
        return {"receipt": receipt, "message": "Penerimaan Barang di-reverse dari accounting"}

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

    @api_router.get("/purchase-invoices/from-receipt/{receipt_id}/preview")
    async def purchase_invoice_from_receipt_preview_endpoint(receipt_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_invoices.view", "purchase_invoices.create", "purchase_receipts.view")
        return await get_receipt_invoice_preview(get_db(), receipt_id)

    @api_router.get("/purchase-invoices/{invoice_id}")
    async def get_purchase_invoice_endpoint(invoice_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_invoices.view", "purchase_payments.create", "purchase_payments.view")
        return {"invoice": await get_purchase_invoice(get_db(), invoice_id)}

    @api_router.post("/purchase-invoices")
    async def create_purchase_invoice_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_invoices.create")
        doc = await create_purchase_invoice(get_db(), _dump(payload), user)
        return {"invoice": doc, "message": "Faktur Pembelian berhasil dibuat"}

    @api_router.post("/purchase-invoices/{invoice_id}/cancel")
    async def cancel_purchase_invoice_endpoint(
        invoice_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_invoices.create")
        data = _dump(payload) if payload is not None else {}
        doc = await cancel_purchase_invoice(get_db(), invoice_id, user, reason=str(data.get("reason") or ""))
        return {"invoice": doc, "message": "Faktur Pembelian dibatalkan"}

    @api_router.post("/purchase-invoices/{invoice_id}/post-accounting")
    async def post_purchase_invoice_accounting_endpoint(invoice_id: str, user=Depends(current_user)):
        await require_any_perm(user, PURCHASE_INVOICE_POST_PERM)
        inv = await post_purchase_invoice_accounting(get_db(), invoice_id, user)
        return {"invoice": inv, "message": "Faktur Pembelian diposting ke accounting"}

    @api_router.post("/purchase-invoices/{invoice_id}/reverse-accounting")
    async def reverse_purchase_invoice_accounting_endpoint(
        invoice_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, PURCHASE_INVOICE_REVERSE_PERM)
        data = _dump(payload) if payload is not None else {}
        inv = await reverse_purchase_invoice_accounting(get_db(), invoice_id, user, data.get("reason"))
        return {"invoice": inv, "message": "Faktur Pembelian di-reverse dari accounting"}

    @api_router.get("/purchase-payments")
    async def list_purchase_payments_endpoint(
        q: Optional[str] = None,
        purchase_invoice_id: Optional[str] = None,
        purchase_order_id: Optional[str] = None,
        status: Optional[str] = None,
        status_filter: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_payments.view", "purchase_invoices.view")
        rows = await list_purchase_payments(
            get_db(),
            q=q,
            purchase_invoice_id=purchase_invoice_id,
            purchase_order_id=purchase_order_id,
            status_filter=status_filter or status,
        )
        return {"payments": rows, "count": len(rows)}

    @api_router.get("/purchase-payments/submit-targets")
    async def purchase_payment_submit_targets(q: Optional[str] = None, user=Depends(current_user)):
        await require_any_perm(user, "purchase_payments.view", "purchase_payments.create", "purchase_invoices.view")
        rows = await list_payment_submit_targets(get_db(), q=q or "", exclude_user_id=actor_user_id(user))
        return {"users": rows, "count": len(rows)}

    @api_router.get("/purchase-payments/cash-bank-options")
    async def purchase_payment_cash_bank_options(user=Depends(current_user)):
        await require_any_perm(
            user,
            "purchase_payments.view",
            "purchase_payments.create",
            PURCHASE_PAYMENT_POST_PERM,
        )
        items = await list_cash_bank_options(get_db())
        return {"items": items, "total": len(items)}

    @api_router.get("/purchase-payments/{payment_id}")
    async def get_purchase_payment_endpoint(payment_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_payments.view", "purchase_invoices.view")
        return {"payment": await get_purchase_payment(get_db(), payment_id)}

    @api_router.post("/purchase-payments")
    async def create_purchase_payment_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_payments.create")
        doc = await create_purchase_payment(get_db(), _dump(payload), user)
        return {"payment": doc, "message": "Pembayaran Pembelian berhasil dicatat"}

    @api_router.post("/purchase-payments/ajukan")
    async def ajukan_purchase_payment_endpoint(payload: LoosePayload, user=Depends(current_user)):
        """Create + submit in one step (DIAJUKAN). Does not settle invoice finances."""
        await require_any_perm(user, "purchase_payments.create")
        result = await create_and_submit_purchase_payment(get_db(), _dump(payload), user)
        return {
            "payment": result.get("payment"),
            "chat_room_id": result.get("chat_room_id"),
            "message_id": result.get("message_id"),
            "reused": result.get("reused", False),
            "message": "Pengajuan pembayaran sudah ada" if result.get("reused") else "Pengajuan pembayaran terkirim",
        }

    @api_router.post("/purchase-payments/{payment_id}/cancel")
    async def cancel_purchase_payment_endpoint(
        payment_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_payments.create")
        data = _dump(payload) if payload is not None else {}
        doc = await cancel_purchase_payment(get_db(), payment_id, user=user, reason=str(data.get("reason") or ""))
        return {"payment": doc, "message": "Pembayaran Pembelian dibatalkan"}

    @api_router.post("/purchase-payments/{payment_id}/submit")
    async def purchase_payment_submit_endpoint(
        payment_id: str,
        payload: LoosePayload,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_payments.create", "purchase_payments.view")
        result = await submit_purchase_payment(get_db(), payment_id, _dump(payload), user)
        return {
            "payment": result.get("payment"),
            "chat_room_id": result.get("chat_room_id"),
            "message_id": result.get("message_id"),
            "reused": result.get("reused", False),
            "message": "Pembayaran sudah diajukan" if result.get("reused") else "Pengajuan pembayaran terkirim",
        }

    @api_router.post("/purchase-payments/{payment_id}/confirm-paid")
    async def purchase_payment_confirm_paid_endpoint(payment_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_payments.create", "purchase_payments.view")
        result = await confirm_purchase_payment(get_db(), payment_id, user)
        return {
            "payment": result.get("payment"),
            "invoice": result.get("invoice"),
            "reused": result.get("reused", False),
            "message": "Pembayaran sudah dikonfirmasi" if result.get("reused") else "Pembayaran dikonfirmasi dibayar",
        }

    @api_router.get("/purchase-supplier-advances")
    async def list_purchase_supplier_advances_endpoint(
        purchase_order_id: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_orders.view", "purchase_payments.view")
        rows = await list_purchase_supplier_advances(get_db(), purchase_order_id=purchase_order_id)
        summary = None
        if purchase_order_id:
            summary = await supplier_advance_availability(get_db(), purchase_order_id)
        return {"advances": rows, "count": len(rows), "summary": summary}

    @api_router.get("/purchase-supplier-advances/availability")
    async def purchase_supplier_advance_availability_endpoint(
        purchase_order_id: str,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_orders.view", "purchase_payments.view")
        return {"summary": await supplier_advance_availability(get_db(), purchase_order_id)}

    @api_router.get("/purchase-supplier-advances/{advance_id}")
    async def get_purchase_supplier_advance_endpoint(advance_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_orders.view", "purchase_payments.view")
        return {"advance": await get_purchase_supplier_advance(get_db(), advance_id)}

    @api_router.post("/purchase-supplier-advances")
    async def create_purchase_supplier_advance_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_orders.create", "purchase_payments.create")
        doc = await create_purchase_supplier_advance(get_db(), _dump(payload), user)
        return {"advance": doc, "message": "Uang muka pembelian tercatat"}

    @api_router.post("/purchase-supplier-advances/{advance_id}/submit")
    async def submit_purchase_supplier_advance_endpoint(advance_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_orders.create", "purchase_payments.create")
        doc = await submit_purchase_supplier_advance(get_db(), advance_id, user)
        return {"advance": doc, "message": "Uang muka diajukan"}

    @api_router.post("/purchase-supplier-advances/{advance_id}/confirm-paid")
    async def confirm_purchase_supplier_advance_endpoint(
        advance_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_orders.create", "purchase_payments.create")
        data = _dump(payload) if payload is not None else {}
        doc = await confirm_purchase_supplier_advance(get_db(), advance_id, user, data)
        return {"advance": doc, "message": "Uang muka dikonfirmasi dibayar"}

    @api_router.post("/purchase-supplier-advances/{advance_id}/cancel")
    async def cancel_purchase_supplier_advance_endpoint(
        advance_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_orders.create", "purchase_payments.create")
        data = _dump(payload) if payload is not None else {}
        doc = await cancel_purchase_supplier_advance(
            get_db(), advance_id, user, reason=str(data.get("reason") or "")
        )
        return {"advance": doc, "message": "Uang muka dibatalkan"}

    @api_router.get("/purchase-supplier-advance-allocations")
    async def list_purchase_supplier_advance_allocations_endpoint(
        purchase_invoice_id: Optional[str] = None,
        supplier_advance_id: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "purchase_invoices.view", "purchase_orders.view", "purchase_payments.view")
        rows = await list_allocations(
            get_db(),
            purchase_invoice_id=purchase_invoice_id,
            supplier_advance_id=supplier_advance_id,
        )
        return {"allocations": rows, "count": len(rows)}

    @api_router.post("/purchase-supplier-advance-allocations")
    async def create_purchase_supplier_advance_allocation_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "purchase_invoices.create", "purchase_payments.create")
        doc = await create_allocation(get_db(), _dump(payload), user)
        return {"allocation": doc, "message": "Uang muka dialokasikan ke faktur"}

    @api_router.post("/purchase-supplier-advance-allocations/{allocation_id}/reverse")
    async def reverse_purchase_supplier_advance_allocation_endpoint(allocation_id: str, user=Depends(current_user)):
        await require_any_perm(user, "purchase_invoices.create", "purchase_payments.create")
        doc = await reverse_allocation(get_db(), allocation_id, user)
        return {"allocation": doc, "message": "Alokasi uang muka dibatalkan"}

    @api_router.post("/purchase-payments/{payment_id}/post-accounting")
    async def post_purchase_payment_accounting_endpoint(
        payment_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, PURCHASE_PAYMENT_POST_PERM)
        data = _dump(payload) if payload is not None else {}
        payment = await post_purchase_payment_accounting(
            get_db(), payment_id, user, cash_bank_id=data.get("cash_bank_id")
        )
        return {"payment": payment, "message": "Pembayaran Pembelian diposting ke accounting"}

    @api_router.post("/purchase-payments/{payment_id}/reverse-accounting")
    async def reverse_purchase_payment_accounting_endpoint(
        payment_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, PURCHASE_PAYMENT_REVERSE_PERM)
        data = _dump(payload) if payload is not None else {}
        payment = await reverse_purchase_payment_accounting(get_db(), payment_id, user, data.get("reason"))
        return {"payment": payment, "message": "Pembayaran Pembelian di-reverse dari accounting"}


    # --- Purchase documents (multi-file lampiran per transaksi) ---
    from services import purchase_documents as purchase_docs

    @api_router.get("/purchase-documents")
    async def list_purchase_documents_endpoint(
        entity_type: str,
        entity_id: str,
        user=Depends(current_user),
    ):
        await require_any_perm(
            user,
            "purchase_orders.view",
            "purchase_receipts.view",
            "purchase_invoices.view",
            "purchase_payments.view",
        )
        rows = await purchase_docs.list_documents(get_db(), entity_type=entity_type, entity_id=entity_id)
        return {"documents": rows, "count": len(rows)}

    @api_router.post("/purchase-documents", status_code=201)
    async def upload_purchase_document_endpoint(request: Request, user=Depends(current_user)):
        await require_any_perm(
            user,
            "purchase_orders.create",
            "purchase_receipts.receive",
            "purchase_invoices.create",
            "purchase_payments.create",
        )
        form = await request.form()
        entity_type = str(form.get("entity_type") or "")
        entity_id = str(form.get("entity_id") or "")
        document_name = str(form.get("document_name") or "")
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            from fastapi import HTTPException
            raise HTTPException(400, "File wajib diunggah")
        data = await file.read()
        meta = await purchase_docs.store_document_bytes(
            get_db(),
            entity_type=entity_type,
            entity_id=entity_id,
            user=user,
            filename=getattr(file, "filename", None) or "dokumen",
            data=data,
            content_type=getattr(file, "content_type", "") or "",
            document_name=document_name,
        )
        return {"document": meta, "message": "Dokumen diunggah"}

    @api_router.get("/purchase-documents/{document_id}/content")
    async def get_purchase_document_content_endpoint(
        document_id: str,
        download: bool = False,
        user=Depends(current_user),
    ):
        await require_any_perm(
            user,
            "purchase_orders.view",
            "purchase_receipts.view",
            "purchase_invoices.view",
            "purchase_payments.view",
        )
        data, ct, filename, _rec = await purchase_docs.load_document_bytes(get_db(), document_id)
        return FastAPIResponse(
            content=data,
            media_type=ct,
            headers={
                "Content-Disposition": purchase_docs.content_disposition(filename, download=download),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api_router.delete("/purchase-documents/{document_id}")
    async def delete_purchase_document_endpoint(document_id: str, user=Depends(current_user)):
        await require_any_perm(
            user,
            "purchase_orders.create",
            "purchase_receipts.receive",
            "purchase_invoices.create",
            "purchase_payments.create",
        )
        meta = await purchase_docs.soft_delete_document(get_db(), document_id, user)
        return {"document": meta, "message": "Dokumen dihapus"}

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

    @api_router.post("/settings/hpp/cutover/confirm")
    async def confirm_hpp_cutover_endpoint(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, "settings.hpp.manage")
        from services.hpp_cutover import confirm_cutover
        return await confirm_cutover(get_db(), _dump(payload), user)

    @api_router.post("/settings/hpp/cutover/recover")
    async def recover_hpp_cutover_endpoint(user=Depends(current_user)):
        await require_any_perm(user, "settings.hpp.manage")
        from services.hpp_cutover import recover_stale_cutover_lease
        return await recover_stale_cutover_lease(get_db())

    @api_router.get("/settings/hpp/cutover/reconciliation")
    async def hpp_cutover_reconciliation_endpoint(
        cutover_id: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "settings.hpp.view", "settings.hpp.manage")
        from services.hpp_cutover import list_reconciliation
        return {"rows": await list_reconciliation(get_db(), cutover_id)}

    @api_router.get("/pembelian/hpp")
    async def list_purchase_hpp_endpoint(
        q: Optional[str] = None,
        supplier: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        user=Depends(require_perm("purchase_hpp.view")),
    ):
        from services.purchase_hpp import list_purchase_hpp
        return await list_purchase_hpp(
            get_db(),
            q=q or "",
            supplier=supplier or "",
            status=status or "",
            date_from=date_from or "",
            date_to=date_to or "",
        )

    @api_router.get("/pembelian/hpp/export")
    async def export_purchase_hpp_endpoint(
        q: Optional[str] = None,
        supplier: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        user=Depends(require_perm("purchase_hpp.view")),
    ):
        from datetime import datetime, timezone

        from fastapi.responses import Response as FastAPIResponse

        from services.purchase_hpp import export_xlsx, list_purchase_hpp

        listing = await list_purchase_hpp(
            get_db(),
            q=q or "",
            supplier=supplier or "",
            status=status or "",
            date_from=date_from or "",
            date_to=date_to or "",
        )
        filename = f"hpp-pembelian-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.xlsx"
        return FastAPIResponse(
            content=export_xlsx(listing.get("rows") or []),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @api_router.get("/pembelian/hpp/{master_item_id}")
    async def get_purchase_hpp_item_endpoint(
        master_item_id: str,
        user=Depends(require_perm("purchase_hpp.view")),
    ):
        from services.purchase_hpp import get_purchase_hpp_item
        return await get_purchase_hpp_item(get_db(), master_item_id)
