"""Wave 2 write routes: schedule finalization and the operational holiday calendar.

Dashboard reads stay on the existing snapshot route. These writes require
schedule.edit or settings.operational_calendar.manage. They do not change
stock, journals, payroll, or surat jalan.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from services.production_wave2 import (
    ScheduleError,
    apply_finalize,
    apply_finish_target,
    list_holidays,
    mirror_schedule_events,
    mutate_holiday,
)


class FinalizeBody(BaseModel):
    action_id: str = Field(..., min_length=1)
    finish_target_date: str | None = None


class FinishTargetBody(BaseModel):
    action_id: str = Field(..., min_length=1)
    date: str = Field(..., min_length=1)


class HolidayBody(BaseModel):
    action_id: str = Field(..., min_length=1)
    date: str = Field(..., min_length=1)
    note: str = ""


class HolidayNoteBody(BaseModel):
    action_id: str = Field(..., min_length=1)
    note: str = ""


def _http(exc: ScheduleError) -> HTTPException:
    return HTTPException(exc.status_code, {"code": exc.code, "detail": exc.detail})


def register_production_wave2_routes(
    api_router,
    *,
    get_db,
    require_perm,
    with_quantity_lock,
    skip_mutation,
    now_iso,
    Depends=Depends,
):
    @api_router.post("/orders/{order_id}/production-schedule/finalize")
    async def finalize_schedule(order_id: str, payload: FinalizeBody, user=Depends(require_perm("schedule.edit"))):
        created: list = []

        def mutate(order: dict):
            if order.get("id") != order_id and order.get("id"):
                return
            try:
                result = apply_finalize(
                    order,
                    action_id=payload.action_id,
                    user=user,
                    now=now_iso(),
                    finish_date=payload.finish_target_date,
                )
            except ScheduleError as exc:
                raise _http(exc) from exc
            if result == "replay":
                raise skip_mutation()
            created.clear()
            created.extend(order.get("production_schedule_events") or [])

        try:
            order = await with_quantity_lock(order_id, mutate)
        except Exception as exc:
            if exc.__class__.__name__ == "_OrderMutationSkipped":
                db = get_db()
                order = await db.orders.find_one({"id": order_id}, {"_id": 0})
                return {"order": order, "replay": True}
            raise
        fresh = [row for row in created if row.get("action_id") == payload.action_id]
        mirror = await mirror_schedule_events(get_db(), fresh)
        return {"order": order, "replay": False, "mirror": mirror}

    @api_router.post("/orders/{order_id}/production-schedule/finish-target")
    async def set_finish_target(order_id: str, payload: FinishTargetBody, user=Depends(require_perm("schedule.edit"))):
        created: list = []

        def mutate(order: dict):
            try:
                result = apply_finish_target(
                    order, action_id=payload.action_id, finish_date=payload.date, user=user, now=now_iso(),
                )
            except ScheduleError as exc:
                raise _http(exc) from exc
            if result == "replay":
                raise skip_mutation()
            created.clear()
            if result == "ok":
                created.extend([row for row in (order.get("production_schedule_events") or []) if row.get("action_id") == payload.action_id])

        try:
            order = await with_quantity_lock(order_id, mutate)
        except Exception as exc:
            if exc.__class__.__name__ == "_OrderMutationSkipped":
                order = await get_db().orders.find_one({"id": order_id}, {"_id": 0})
                return {"order": order, "replay": True}
            raise
        mirror = await mirror_schedule_events(get_db(), created)
        return {"order": order, "replay": False, "mirror": mirror}

    @api_router.get("/settings/operational-holidays")
    async def get_holidays(user=Depends(require_perm("settings.operational_calendar.manage"))):
        return {"holidays": await list_holidays(get_db())}

    @api_router.post("/settings/operational-holidays")
    async def add_holiday(payload: HolidayBody, user=Depends(require_perm("settings.operational_calendar.manage"))):
        try:
            result = await mutate_holiday(
                get_db(), action="add", action_id=payload.action_id, day=payload.date, note=payload.note, user=user, now=now_iso(),
            )
        except ScheduleError as exc:
            raise _http(exc) from exc
        return result

    @api_router.patch("/settings/operational-holidays/{day}")
    async def update_holiday(day: str, payload: HolidayNoteBody, user=Depends(require_perm("settings.operational_calendar.manage"))):
        try:
            result = await mutate_holiday(
                get_db(), action="update", action_id=payload.action_id, day=day, note=payload.note, user=user, now=now_iso(),
            )
        except ScheduleError as exc:
            raise _http(exc) from exc
        return result

    @api_router.delete("/settings/operational-holidays/{day}")
    async def delete_holiday(day: str, payload: HolidayNoteBody, user=Depends(require_perm("settings.operational_calendar.manage"))):
        try:
            result = await mutate_holiday(
                get_db(), action="delete", action_id=payload.action_id, day=day, note=payload.note, user=user, now=now_iso(),
            )
        except ScheduleError as exc:
            raise _http(exc) from exc
        return result
