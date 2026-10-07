"""Internal RBAC routes for Public Catalog Settings (Phase A)."""
from __future__ import annotations

from typing import Optional

from fastapi import File, UploadFile
from fastapi.responses import Response as BinaryResponse
from pydantic import BaseModel, ConfigDict

from services.customer_pricing_catalog import MAX_CATALOG_IMAGE_BYTES


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_public_catalog_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_any_perm,
    Depends,
):
    from services import public_catalog as pc
    from services import public_catalog_images as images

    def dump(payload) -> dict:
        if payload is None:
            return {}
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        if hasattr(payload, "dict"):
            return payload.dict()
        return dict(payload or {})

    async def require_view(user):
        await require_any_perm(user, pc.PERM_VIEW, pc.PERM_MANAGE, "permissions.manage")

    async def require_manage(user):
        await require_any_perm(user, pc.PERM_MANAGE)

    @api_router.get("/settings/public-catalog")
    async def get_public_catalog_settings(user=Depends(current_user)):
        await require_view(user)
        return {"settings": await pc.get_global_settings(get_db())}

    @api_router.put("/settings/public-catalog")
    async def put_public_catalog_settings(payload: LoosePayload, user=Depends(current_user)):
        await require_manage(user)
        settings = await pc.update_global_settings(get_db(), dump(payload), user)
        return {"settings": settings, "message": "Pengaturan Katalog Publik disimpan"}

    @api_router.get("/public-catalog/categories")
    async def list_public_catalog_categories(
        q: Optional[str] = None, user=Depends(current_user)
    ):
        await require_view(user)
        categories = await pc.list_categories(get_db(), q=q or "")
        return {"categories": categories, "count": len(categories)}

    @api_router.post("/public-catalog/categories")
    async def create_public_catalog_category(
        payload: LoosePayload, user=Depends(current_user)
    ):
        await require_manage(user)
        category = await pc.create_category(get_db(), dump(payload), user)
        return {"category": category, "message": "Kategori katalog dibuat"}

    @api_router.get("/public-catalog/categories/{category_id}")
    async def get_public_catalog_category(category_id: str, user=Depends(current_user)):
        await require_view(user)
        return {"category": await pc.get_category(get_db(), category_id)}

    @api_router.patch("/public-catalog/categories/{category_id}")
    async def patch_public_catalog_category(
        category_id: str, payload: LoosePayload, user=Depends(current_user)
    ):
        await require_manage(user)
        category = await pc.update_category(get_db(), category_id, dump(payload), user)
        return {"category": category, "message": "Kategori katalog diperbarui"}

    @api_router.put("/public-catalog/categories/{category_id}")
    async def put_public_catalog_category(
        category_id: str, payload: LoosePayload, user=Depends(current_user)
    ):
        await require_manage(user)
        category = await pc.update_category(get_db(), category_id, dump(payload), user)
        return {"category": category, "message": "Kategori katalog diperbarui"}

    @api_router.post("/public-catalog/categories/{category_id}/image")
    async def upload_public_catalog_category_image(
        category_id: str,
        file: UploadFile = File(...),
        user=Depends(current_user),
    ):
        await require_manage(user)
        raw = await file.read(MAX_CATALOG_IMAGE_BYTES + 1)
        category = await images.upload_category_image(
            get_db(),
            category_id=category_id,
            filename=file.filename or "",
            content_type=file.content_type or "",
            data=raw,
            user=user,
        )
        return {"category": category, "message": "Gambar kategori diperbarui"}

    @api_router.get("/public-catalog/categories/{category_id}/image")
    async def get_public_catalog_category_image(
        category_id: str, user=Depends(current_user)
    ):
        await require_view(user)
        data, content_type, _filename = await images.get_category_image(
            get_db(), category_id=category_id
        )
        return BinaryResponse(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api_router.delete("/public-catalog/categories/{category_id}/image")
    async def delete_public_catalog_category_image(
        category_id: str, user=Depends(current_user)
    ):
        await require_manage(user)
        category = await images.remove_category_image(
            get_db(), category_id=category_id, user=user
        )
        return {"category": category, "message": "Gambar kategori dihapus"}
