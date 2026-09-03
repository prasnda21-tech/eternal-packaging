"""Internal RBAC routes for Customer Pricing groups."""
from __future__ import annotations

from typing import Optional

from fastapi import File, UploadFile
from fastapi.responses import Response as BinaryResponse
from pydantic import BaseModel, ConfigDict


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_customer_pricing_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_any_perm,
    Depends,
):
    from services import customer_pricing as cp
    from services import customer_pricing_catalog as catalog

    def dump(payload) -> dict:
        if payload is None:
            return {}
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        if hasattr(payload, "dict"):
            return payload.dict()
        return dict(payload or {})

    async def require_view(user):
        await require_any_perm(
            user, cp.PERM_VIEW, cp.PERM_MANAGE, cp.PERM_PUBLISH, "permissions.manage"
        )

    @api_router.get("/settings/customer-pricing")
    async def get_customer_pricing_settings(user=Depends(current_user)):
        await require_view(user)
        return {"settings": await cp.get_global_settings(get_db())}

    @api_router.put("/settings/customer-pricing")
    async def put_customer_pricing_settings(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, cp.PERM_MANAGE)
        settings = await cp.update_global_settings(get_db(), dump(payload), user)
        return {"settings": settings, "message": "Pengaturan Harga Customer disimpan"}

    @api_router.get("/customer-pricing/groups")
    async def list_customer_pricing_groups(
        q: Optional[str] = None, user=Depends(current_user)
    ):
        await require_view(user)
        groups = await cp.list_groups(get_db(), q=q or "")
        return {"groups": groups, "count": len(groups)}

    @api_router.post("/customer-pricing/groups")
    async def create_customer_pricing_group(
        payload: LoosePayload, user=Depends(current_user)
    ):
        await require_any_perm(user, cp.PERM_MANAGE)
        group = await cp.create_group(get_db(), dump(payload), user)
        return {"group": group, "message": "Kelompok harga dibuat"}

    @api_router.get("/customer-pricing/groups/{group_id}")
    async def get_customer_pricing_group(group_id: str, user=Depends(current_user)):
        await require_view(user)
        return {"group": await cp.get_group(get_db(), group_id)}

    @api_router.patch("/customer-pricing/groups/{group_id}")
    async def patch_customer_pricing_group(
        group_id: str, payload: LoosePayload, user=Depends(current_user)
    ):
        await require_any_perm(user, cp.PERM_MANAGE)
        group = await cp.patch_group(get_db(), group_id, dump(payload), user)
        return {"group": group, "message": "Kelompok harga diperbarui"}

    @api_router.post("/customer-pricing/groups/{group_id}/catalog-image")
    async def upload_customer_pricing_catalog_image(
        group_id: str,
        file: UploadFile = File(...),
        user=Depends(current_user),
    ):
        await require_any_perm(user, cp.PERM_MANAGE)
        raw = await file.read(catalog.MAX_CATALOG_IMAGE_BYTES + 1)
        group = await catalog.upload_catalog_image(
            get_db(),
            group_id=group_id,
            filename=file.filename or "",
            content_type=file.content_type or "",
            data=raw,
            user=user,
        )
        return {"group": group, "message": "Foto katalog diperbarui"}

    @api_router.get("/customer-pricing/groups/{group_id}/catalog-image")
    async def get_customer_pricing_catalog_image(
        group_id: str, user=Depends(current_user)
    ):
        await require_view(user)
        data, content_type, _filename = await catalog.get_catalog_image(
            get_db(), group_id=group_id
        )
        return BinaryResponse(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api_router.delete("/customer-pricing/groups/{group_id}/catalog-image")
    async def delete_customer_pricing_catalog_image(
        group_id: str, user=Depends(current_user)
    ):
        await require_any_perm(user, cp.PERM_MANAGE)
        group = await catalog.remove_catalog_image(
            get_db(), group_id=group_id, user=user
        )
        return {"group": group, "message": "Foto katalog dihapus"}

    @api_router.post("/customer-pricing/groups/{group_id}/draft")
    async def create_customer_pricing_draft(group_id: str, user=Depends(current_user)):
        await require_any_perm(user, cp.PERM_MANAGE)
        version = await cp.create_or_get_draft(get_db(), group_id, user)
        return {"version": version, "message": "Draft siap diedit"}

    @api_router.get("/customer-pricing/groups/{group_id}/versions")
    async def list_customer_pricing_versions(group_id: str, user=Depends(current_user)):
        await require_view(user)
        versions = await cp.list_versions(get_db(), group_id)
        return {"versions": versions, "count": len(versions)}

    @api_router.get("/customer-pricing/versions/{version_id}")
    async def get_customer_pricing_version(version_id: str, user=Depends(current_user)):
        await require_view(user)
        return {"version": await cp.get_version(get_db(), version_id)}

    @api_router.put("/customer-pricing/versions/{version_id}")
    async def put_customer_pricing_version(
        version_id: str, payload: LoosePayload, user=Depends(current_user)
    ):
        await require_any_perm(user, cp.PERM_MANAGE)
        version = await cp.update_draft(get_db(), version_id, dump(payload), user)
        return {"version": version, "message": "Draft disimpan"}

    @api_router.post("/customer-pricing/versions/{version_id}/refresh-hpp")
    async def refresh_customer_pricing_hpp(version_id: str, user=Depends(current_user)):
        await require_any_perm(user, cp.PERM_MANAGE)
        version = await cp.refresh_auto_hpp(get_db(), version_id, user)
        return {"version": version, "message": "HPP semua produk diperbarui"}

    @api_router.post("/customer-pricing/versions/{version_id}/preview")
    async def preview_customer_pricing_version(
        version_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_view(user)
        return {"preview": await cp.preview_version(get_db(), version_id, dump(payload))}

    @api_router.post("/customer-pricing/versions/{version_id}/publish")
    async def publish_customer_pricing_version(
        version_id: str, payload: LoosePayload, user=Depends(current_user)
    ):
        await require_any_perm(user, cp.PERM_PUBLISH)
        result = await cp.publish_version(get_db(), version_id, dump(payload), user)
        return {**result, "message": "Kelompok harga berhasil di-Publish"}
