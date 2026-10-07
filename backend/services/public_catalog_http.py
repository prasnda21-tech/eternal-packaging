"""Internal RBAC routes for Public Catalog Settings (Phase A + B + C)."""
from __future__ import annotations

from typing import Optional

from fastapi import File, Form, UploadFile
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

    @api_router.get("/settings/public-catalog/groups")
    async def list_public_catalog_groups(
        q: Optional[str] = None, user=Depends(current_user)
    ):
        await require_view(user)
        groups = await pc.list_presentation_groups(get_db(), q=q or "")
        return {"groups": groups, "count": len(groups)}

    @api_router.get("/settings/public-catalog/groups/{pricing_group_id}")
    async def get_public_catalog_group(pricing_group_id: str, user=Depends(current_user)):
        await require_view(user)
        return {"group": await pc.get_presentation_group(get_db(), pricing_group_id)}

    @api_router.put("/settings/public-catalog/groups/{pricing_group_id}")
    async def put_public_catalog_group(
        pricing_group_id: str, payload: LoosePayload, user=Depends(current_user)
    ):
        await require_manage(user)
        group = await pc.upsert_presentation_group(
            get_db(), pricing_group_id, dump(payload), user
        )
        return {"group": group, "message": "Presentasi kelompok katalog disimpan"}

    @api_router.patch("/settings/public-catalog/groups/{pricing_group_id}")
    async def patch_public_catalog_group(
        pricing_group_id: str, payload: LoosePayload, user=Depends(current_user)
    ):
        await require_manage(user)
        group = await pc.upsert_presentation_group(
            get_db(), pricing_group_id, dump(payload), user
        )
        return {"group": group, "message": "Presentasi kelompok katalog disimpan"}

    @api_router.get(
        "/settings/public-catalog/groups/{pricing_group_id}/variants"
    )
    async def list_public_catalog_variants(
        pricing_group_id: str,
        q: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_view(user)
        variants = await pc.list_presentation_variants(
            get_db(), pricing_group_id, q=q or ""
        )
        return {"variants": variants, "count": len(variants)}

    @api_router.get(
        "/settings/public-catalog/groups/{pricing_group_id}/variants/{accurate_item_id}"
    )
    async def get_public_catalog_variant(
        pricing_group_id: str,
        accurate_item_id: str,
        user=Depends(current_user),
    ):
        await require_view(user)
        return {
            "variant": await pc.get_presentation_variant(
                get_db(), pricing_group_id, accurate_item_id
            )
        }

    @api_router.put(
        "/settings/public-catalog/groups/{pricing_group_id}/variants/{accurate_item_id}"
    )
    async def put_public_catalog_variant(
        pricing_group_id: str,
        accurate_item_id: str,
        payload: LoosePayload,
        user=Depends(current_user),
    ):
        await require_manage(user)
        variant = await pc.upsert_presentation_variant(
            get_db(), pricing_group_id, accurate_item_id, dump(payload), user
        )
        return {"variant": variant, "message": "Metadata varian katalog disimpan"}

    @api_router.patch(
        "/settings/public-catalog/groups/{pricing_group_id}/variants/{accurate_item_id}"
    )
    async def patch_public_catalog_variant(
        pricing_group_id: str,
        accurate_item_id: str,
        payload: LoosePayload,
        user=Depends(current_user),
    ):
        await require_manage(user)
        variant = await pc.upsert_presentation_variant(
            get_db(), pricing_group_id, accurate_item_id, dump(payload), user
        )
        return {"variant": variant, "message": "Metadata varian katalog disimpan"}

    @api_router.get(
        "/settings/public-catalog/groups/{pricing_group_id}/variants/{accurate_item_id}/images"
    )
    async def list_public_catalog_variant_images(
        pricing_group_id: str,
        accurate_item_id: str,
        user=Depends(current_user),
    ):
        await require_view(user)
        gallery = await pc.gallery_context(
            get_db(), pricing_group_id, accurate_item_id
        )
        return gallery

    def _form_bool(raw: Optional[str]):
        if raw is None or raw == "":
            return None
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        return None

    @api_router.post(
        "/settings/public-catalog/groups/{pricing_group_id}/variants/{accurate_item_id}/images"
    )
    async def upload_public_catalog_variant_image(
        pricing_group_id: str,
        accurate_item_id: str,
        file: UploadFile = File(...),
        role: Optional[str] = Form(None),
        display_order: Optional[str] = Form(None),
        is_default: Optional[str] = Form(None),
        enabled: Optional[str] = Form(None),
        user=Depends(current_user),
    ):
        await require_manage(user)
        raw = await file.read(MAX_CATALOG_IMAGE_BYTES + 1)
        order_value = None
        if display_order not in (None, ""):
            try:
                order_value = int(display_order)
            except (TypeError, ValueError):
                order_value = display_order
        image = await images.upload_variant_gallery_image(
            get_db(),
            pricing_group_id=pricing_group_id,
            accurate_item_id=accurate_item_id,
            filename=file.filename or "",
            content_type=file.content_type or "",
            data=raw,
            user=user,
            role=role,
            display_order=order_value,
            is_default=_form_bool(is_default),
            enabled=_form_bool(enabled),
        )
        gallery = await pc.gallery_context(
            get_db(), pricing_group_id, accurate_item_id
        )
        return {
            "image": image,
            "gallery": gallery,
            "message": "Gambar galeri ditambahkan",
        }

    @api_router.patch(
        "/settings/public-catalog/groups/{pricing_group_id}/variants/{accurate_item_id}/images/{image_id}"
    )
    async def patch_public_catalog_variant_image(
        pricing_group_id: str,
        accurate_item_id: str,
        image_id: str,
        payload: LoosePayload,
        user=Depends(current_user),
    ):
        await require_manage(user)
        image = await pc.update_gallery_image(
            get_db(),
            pricing_group_id,
            accurate_item_id,
            image_id,
            dump(payload),
            user,
        )
        gallery = await pc.gallery_context(
            get_db(), pricing_group_id, accurate_item_id
        )
        return {
            "image": image,
            "gallery": gallery,
            "message": "Gambar galeri diperbarui",
        }

    @api_router.delete(
        "/settings/public-catalog/groups/{pricing_group_id}/variants/{accurate_item_id}/images/{image_id}"
    )
    async def delete_public_catalog_variant_image(
        pricing_group_id: str,
        accurate_item_id: str,
        image_id: str,
        user=Depends(current_user),
    ):
        await require_manage(user)
        result = await images.remove_variant_gallery_image(
            get_db(),
            pricing_group_id=pricing_group_id,
            accurate_item_id=accurate_item_id,
            image_id=image_id,
            user=user,
        )
        return {**result, "message": "Gambar galeri dihapus"}

    @api_router.get(
        "/settings/public-catalog/groups/{pricing_group_id}/variants/{accurate_item_id}/images/{image_id}/content"
    )
    async def get_public_catalog_variant_image_content(
        pricing_group_id: str,
        accurate_item_id: str,
        image_id: str,
        user=Depends(current_user),
    ):
        await require_view(user)
        data, content_type, _filename = await images.get_variant_gallery_image(
            get_db(),
            pricing_group_id=pricing_group_id,
            accurate_item_id=accurate_item_id,
            image_id=image_id,
        )
        return BinaryResponse(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
