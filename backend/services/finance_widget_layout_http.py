"""Authenticated layout routes for Dashboard Keuangan. No financial writes."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from services.finance_widget_layout import LayoutError, get_finance_layout, reset_finance_layout, save_finance_layout


class LayoutSaveBody(BaseModel):
    version: int = Field(..., ge=0)
    widgets: list = Field(default_factory=list)


def _raise(exc: LayoutError) -> None:
    payload: dict = {"code": exc.code, "detail": exc.detail}
    if exc.version is not None:
        payload["version"] = exc.version
    raise HTTPException(exc.status_code, payload) from exc


def register_finance_widget_layout_routes(
    api_router,
    *,
    get_db,
    current_user,
    user_permissions,
    Depends=Depends,
):
    async def _perms(user) -> set:
        try:
            return set(await user_permissions(user))
        except Exception as exc:
            raise HTTPException(403, "Izin pengguna tidak dapat dibaca") from exc

    @api_router.get("/dashboard/keuangan/layout")
    async def get_my_finance_layout(user=Depends(current_user)):
        """Layout metadata for the signed-in user. Session id is the only owner key."""
        db = get_db()
        try:
            return await get_finance_layout(db, user, await _perms(user))
        except LayoutError as exc:
            _raise(exc)

    @api_router.put("/dashboard/keuangan/layout")
    async def save_my_finance_layout(body: LayoutSaveBody, user=Depends(current_user)):
        """Replace the caller's editable widgets when version still matches."""
        db = get_db()
        try:
            return await save_finance_layout(db, user, await _perms(user), body.version, body.widgets)
        except LayoutError as exc:
            _raise(exc)

    @api_router.post("/dashboard/keuangan/layout/reset")
    async def reset_my_finance_layout(user=Depends(current_user)):
        """Drop the caller's saved layout and return the permission-filtered default."""
        db = get_db()
        try:
            return await reset_finance_layout(db, user, await _perms(user))
        except LayoutError as exc:
            _raise(exc)
