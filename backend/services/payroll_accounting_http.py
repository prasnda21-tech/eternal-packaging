"""HTTP routes for Payroll Accounting Readiness and accrual posting."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_payroll_accounting_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_any_perm,
    Depends,
):
    from services import payroll_accounting_settings as pas
    from services.payroll_accounting_readiness import evaluate_payroll_accounting_readiness
    from services.payroll_journal_posting import PERM_POST, post_payroll_journal, read_period_journal_reference
    from services.payroll_payment import (
        PERM_APPROVE as PAYMENT_APPROVE,
        PERM_CANCEL as PAYMENT_CANCEL,
        PERM_CREATE as PAYMENT_CREATE,
        PERM_POST as PAYMENT_POST,
        PERM_REVERSE as PAYMENT_REVERSE,
        PERM_VIEW as PAYMENT_VIEW,
        approve_payroll_payment,
        cancel_payroll_payment,
        create_payroll_payment,
        get_payment,
        list_payments_for_period,
    )
    from services.payroll_payment_posting import post_payroll_payment, reverse_payroll_payment
    from services.hr_payroll import PERM_PAY_VIEW

    def dump(payload) -> dict:
        if payload is None:
            return {}
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        if hasattr(payload, "dict"):
            return payload.dict()
        return dict(payload or {})

    @api_router.get("/settings/payroll-accounting")
    async def get_payroll_accounting_settings_endpoint(user=Depends(current_user)):
        await require_any_perm(user, pas.PERM_VIEW, pas.PERM_MANAGE, "permissions.manage")
        return {"settings": await pas.get_payroll_accounting_settings(get_db())}

    @api_router.put("/settings/payroll-accounting")
    async def put_payroll_accounting_settings_endpoint(
        payload: LoosePayload, user=Depends(current_user)
    ):
        await require_any_perm(user, pas.PERM_MANAGE)
        settings = await pas.update_payroll_accounting_settings(get_db(), dump(payload), user)
        return {"settings": settings, "message": "Pengaturan Akuntansi Payroll disimpan"}

    @api_router.get("/hr-payroll/periods/{period_id}/accounting-readiness")
    async def get_payroll_accounting_readiness_endpoint(period_id: str, user=Depends(current_user)):
        await require_any_perm(user, pas.PERM_VIEW, pas.PERM_MANAGE, PERM_PAY_VIEW)
        readiness = await evaluate_payroll_accounting_readiness(get_db(), period_id)
        return {"readiness": readiness}

    @api_router.post("/hr-payroll/periods/{period_id}/post-accounting")
    async def post_payroll_period_accounting_endpoint(period_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_POST)
        result = await post_payroll_journal(get_db(), period_id, user)
        return result

    @api_router.get("/hr-payroll/periods/{period_id}/accounting-journal")
    async def get_payroll_period_journal_endpoint(period_id: str, user=Depends(current_user)):
        await require_any_perm(user, pas.PERM_VIEW, pas.PERM_MANAGE, PERM_POST, PERM_PAY_VIEW)
        return await read_period_journal_reference(get_db(), period_id)

    @api_router.post("/hr-payroll/periods/{period_id}/payments")
    async def create_payroll_payment_endpoint(
        period_id: str, payload: LoosePayload, user=Depends(current_user)
    ):
        await require_any_perm(user, PAYMENT_CREATE)
        payment = await create_payroll_payment(get_db(), period_id, dump(payload), user)
        return {"payment": payment}

    @api_router.get("/hr-payroll/periods/{period_id}/payments")
    async def get_period_payroll_payment_endpoint(period_id: str, user=Depends(current_user)):
        await require_any_perm(
            user,
            PAYMENT_VIEW,
            PAYMENT_CREATE,
            PAYMENT_APPROVE,
            PAYMENT_POST,
            PAYMENT_REVERSE,
            PAYMENT_CANCEL,
            PERM_PAY_VIEW,
        )
        active, history = await list_payments_for_period(get_db(), period_id)
        return {"payment": active, "payments": history}

    @api_router.get("/hr-payroll/payments/{payment_id}")
    async def get_payroll_payment_endpoint(payment_id: str, user=Depends(current_user)):
        await require_any_perm(
            user, PAYMENT_VIEW, PAYMENT_CREATE, PAYMENT_APPROVE, PAYMENT_POST, PAYMENT_REVERSE, PAYMENT_CANCEL
        )
        payment = await get_payment(get_db(), payment_id, with_lines=True)
        return {"payment": payment}

    @api_router.post("/hr-payroll/payments/{payment_id}/cancel")
    async def cancel_payroll_payment_endpoint(
        payment_id: str, payload: LoosePayload, user=Depends(current_user)
    ):
        await require_any_perm(user, PAYMENT_CANCEL)
        payment = await cancel_payroll_payment(get_db(), payment_id, user, dump(payload).get("reason"))
        return {"payment": payment}

    @api_router.post("/hr-payroll/payments/{payment_id}/approve")
    async def approve_payroll_payment_endpoint(payment_id: str, user=Depends(current_user)):
        await require_any_perm(user, PAYMENT_APPROVE)
        payment = await approve_payroll_payment(get_db(), payment_id, user)
        return {"payment": payment}

    @api_router.post("/hr-payroll/payments/{payment_id}/post-accounting")
    async def post_payroll_payment_endpoint(payment_id: str, user=Depends(current_user)):
        await require_any_perm(user, PAYMENT_POST)
        result = await post_payroll_payment(get_db(), payment_id, user)
        return result

    @api_router.post("/hr-payroll/payments/{payment_id}/reverse-accounting")
    async def reverse_payroll_payment_endpoint(
        payment_id: str, payload: LoosePayload, user=Depends(current_user)
    ):
        await require_any_perm(user, PAYMENT_REVERSE)
        data = dump(payload)
        return await reverse_payroll_payment(get_db(), payment_id, user, data.get("reason"))
