"""Image storage for Public Catalog Settings (Phase A category + Phase C gallery)."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional, Tuple

from fastapi import HTTPException

from services import public_catalog as pc
from services import storage as obj_storage
from services.customer_pricing_catalog import (
    CATALOG_IMAGE_MIMES,
    MAX_CATALOG_IMAGE_BYTES,
    sniff_catalog_image_mime,
    _safe_original_filename,
)
from services.purchase_common import actor_snapshot, now_iso

CATEGORY_IMAGE_KIND = "public_catalog_category_image"
VARIANT_IMAGE_KIND = "public_catalog_variant_image"


async def upload_category_image(
    db,
    *,
    category_id: str,
    filename: str,
    content_type: str,
    data: bytes,
    user: dict,
) -> dict:
    category = await db[pc.CATEGORIES].find_one({"id": category_id}, {"_id": 0})
    if not category:
        raise HTTPException(404, "Kategori katalog tidak ditemukan.")
    if not data:
        raise HTTPException(400, "File gambar kosong.")
    if len(data) > MAX_CATALOG_IMAGE_BYTES:
        raise HTTPException(400, "Ukuran gambar kategori maksimal 5 MB.")

    mime = sniff_catalog_image_mime(data, content_type)
    if mime not in CATALOG_IMAGE_MIMES:
        raise HTTPException(400, "Tipe gambar tidak didukung.")

    file_id = str(uuid.uuid4())
    extension = obj_storage.ext_for(mime, "jpg")
    original_filename = _safe_original_filename(filename, extension)
    storage_path = (
        f"{obj_storage.APP_NAME}/public-catalog/categories/{category_id}/"
        f"{file_id}.{extension}"
    )
    try:
        stored = obj_storage.put_object(storage_path, data, mime)
    except Exception:
        raise HTTPException(
            502, "Gagal mengunggah gambar kategori. Silakan coba lagi."
        ) from None

    now = now_iso()
    image = {
        "file_id": file_id,
        "content_type": mime,
        "original_filename": original_filename,
        "size": stored.get("size") or len(data),
        "uploaded_at": now,
    }
    file_doc = {
        "id": file_id,
        "kind": CATEGORY_IMAGE_KIND,
        "category_id": category_id,
        "storage_path": stored.get("path") or storage_path,
        "content_type": mime,
        "original_filename": original_filename,
        "size": image["size"],
        "uploaded_at": now,
        "uploaded_by": actor_snapshot(user),
        "is_deleted": False,
    }
    await db.files.insert_one(file_doc)
    await db[pc.CATEGORIES].update_one(
        {"id": category_id},
        {
            "$set": {
                "image": image,
                "updated_at": now,
                "updated_by": actor_snapshot(user),
            }
        },
    )
    previous_file_id = ((category.get("image") or {}).get("file_id"))
    if previous_file_id and previous_file_id != file_id:
        await db.files.update_one(
            {
                "id": previous_file_id,
                "kind": CATEGORY_IMAGE_KIND,
                "category_id": category_id,
            },
            {"$set": {"is_deleted": True}},
        )
    return await pc.get_category(db, category_id)


async def remove_category_image(db, *, category_id: str, user: dict) -> dict:
    category = await db[pc.CATEGORIES].find_one({"id": category_id}, {"_id": 0})
    if not category:
        raise HTTPException(404, "Kategori katalog tidak ditemukan.")
    image = category.get("image") or {}
    now = now_iso()
    await db[pc.CATEGORIES].update_one(
        {"id": category_id},
        {
            "$set": {
                "image": None,
                "updated_at": now,
                "updated_by": actor_snapshot(user),
            }
        },
    )
    if image.get("file_id"):
        await db.files.update_one(
            {
                "id": image["file_id"],
                "kind": CATEGORY_IMAGE_KIND,
                "category_id": category_id,
            },
            {"$set": {"is_deleted": True}},
        )
    return await pc.get_category(db, category_id)


async def get_category_image(db, *, category_id: str) -> Tuple[bytes, str, str]:
    category = await db[pc.CATEGORIES].find_one(
        {"id": category_id}, {"_id": 0, "image": 1, "name": 1}
    )
    image = (category or {}).get("image") or {}
    file_id = str(image.get("file_id") or "").strip()
    if not category or not file_id:
        raise HTTPException(404, "Gambar kategori tidak ditemukan.")
    record = await db.files.find_one(
        {
            "id": file_id,
            "kind": CATEGORY_IMAGE_KIND,
            "category_id": category_id,
            "is_deleted": {"$ne": True},
        },
        {"_id": 0},
    )
    if not record or not record.get("storage_path"):
        raise HTTPException(404, "Gambar kategori tidak ditemukan.")
    try:
        data, fetched_type = obj_storage.get_object(record["storage_path"])
    except Exception:
        raise HTTPException(404, "Gambar kategori tidak ditemukan.") from None
    content_type = (
        str(record.get("content_type") or fetched_type or "application/octet-stream")
    )
    filename = str(
        record.get("original_filename")
        or Path(record["storage_path"]).name
        or "category.jpg"
    )
    return data, content_type, filename


async def upload_variant_gallery_image(
    db,
    *,
    pricing_group_id: str,
    accurate_item_id: str,
    filename: str,
    content_type: str,
    data: bytes,
    user: dict,
    role: Optional[str] = None,
    display_order: Optional[int] = None,
    is_default: Optional[bool] = None,
    enabled: Optional[bool] = None,
) -> dict:
    await pc._require_member_product(db, pricing_group_id, accurate_item_id)
    if not data:
        raise HTTPException(400, "File gambar kosong.")
    if len(data) > MAX_CATALOG_IMAGE_BYTES:
        raise HTTPException(400, "Ukuran gambar galeri maksimal 5 MB.")

    mime = sniff_catalog_image_mime(data, content_type)
    if mime not in CATALOG_IMAGE_MIMES:
        raise HTTPException(400, "Tipe gambar tidak didukung.")

    image_role = pc._normalize_image_role(role or "OTHER")
    order = pc._parse_display_order(0 if display_order is None else display_order)
    want_default = False if is_default is None else bool(is_default)
    is_enabled = True if enabled is None else bool(enabled)

    file_id = str(uuid.uuid4())
    extension = obj_storage.ext_for(mime, "jpg")
    original_filename = _safe_original_filename(filename, extension)
    storage_path = (
        f"{obj_storage.APP_NAME}/public-catalog/groups/{pricing_group_id}/"
        f"variants/{accurate_item_id}/{file_id}.{extension}"
    )
    try:
        stored = obj_storage.put_object(storage_path, data, mime)
    except Exception:
        raise HTTPException(
            502, "Gagal mengunggah gambar galeri. Silakan coba lagi."
        ) from None

    now = now_iso()
    size = stored.get("size") or len(data)
    file_doc = {
        "id": file_id,
        "kind": VARIANT_IMAGE_KIND,
        "pricing_group_id": pricing_group_id,
        "accurate_item_id": accurate_item_id,
        "storage_path": stored.get("path") or storage_path,
        "content_type": mime,
        "original_filename": original_filename,
        "size": size,
        "uploaded_at": now,
        "uploaded_by": actor_snapshot(user),
        "is_deleted": False,
    }
    await db.files.insert_one(file_doc)
    image = await pc.create_gallery_image_record(
        db,
        pricing_group_id=pricing_group_id,
        accurate_item_id=accurate_item_id,
        file_id=file_id,
        content_type=mime,
        original_filename=original_filename,
        size=size,
        role=image_role,
        display_order=order,
        is_default=want_default,
        enabled=is_enabled,
        user=user,
    )
    return image


async def get_variant_gallery_image(
    db, *, pricing_group_id: str, accurate_item_id: str, image_id: str
) -> Tuple[bytes, str, str]:
    meta = await db[pc.CATALOG_IMAGES].find_one(
        {
            "id": image_id,
            "pricing_group_id": pricing_group_id,
            "accurate_item_id": accurate_item_id,
        },
        {"_id": 0},
    )
    if not meta or not meta.get("file_id"):
        raise HTTPException(404, "Gambar galeri tidak ditemukan.")
    record = await db.files.find_one(
        {
            "id": meta["file_id"],
            "kind": VARIANT_IMAGE_KIND,
            "pricing_group_id": pricing_group_id,
            "accurate_item_id": accurate_item_id,
            "is_deleted": {"$ne": True},
        },
        {"_id": 0},
    )
    if not record or not record.get("storage_path"):
        raise HTTPException(404, "Gambar galeri tidak ditemukan.")
    try:
        data, fetched_type = obj_storage.get_object(record["storage_path"])
    except Exception:
        raise HTTPException(404, "Gambar galeri tidak ditemukan.") from None
    content_type = str(
        record.get("content_type") or fetched_type or "application/octet-stream"
    )
    filename = str(
        record.get("original_filename")
        or Path(record["storage_path"]).name
        or "variant.jpg"
    )
    return data, content_type, filename


async def remove_variant_gallery_image(
    db,
    *,
    pricing_group_id: str,
    accurate_item_id: str,
    image_id: str,
    user: dict,
) -> dict:
    deleted = await pc.delete_gallery_image_record(
        db, pricing_group_id, accurate_item_id, image_id
    )
    file_id = deleted.get("file_id")
    if file_id:
        await db.files.update_one(
            {
                "id": file_id,
                "kind": VARIANT_IMAGE_KIND,
                "pricing_group_id": pricing_group_id,
                "accurate_item_id": accurate_item_id,
            },
            {"$set": {"is_deleted": True}},
        )
    context = await pc.gallery_context(db, pricing_group_id, accurate_item_id)
    return {"deleted": True, "id": image_id, "gallery": context}
