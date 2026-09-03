"""Group-level presentation image support for Customer Pricing."""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Optional, Tuple

from fastapi import HTTPException

from services import customer_pricing as cp
from services import storage as obj_storage
from services.purchase_common import actor_snapshot, now_iso

CATALOG_IMAGE_KIND = "customer_pricing_catalog_image"
MAX_CATALOG_IMAGE_BYTES = 5 * 1024 * 1024
CATALOG_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def sniff_catalog_image_mime(data: bytes, declared: str = "") -> str:
    if not data or len(data) < 12:
        raise HTTPException(400, "File gambar tidak valid.")
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if str(declared or "").lower().strip().startswith("image/"):
        raise HTTPException(
            400, "Isi file bukan gambar JPEG, PNG, atau WEBP yang valid."
        )
    raise HTTPException(400, "Hanya gambar JPEG, PNG, atau WEBP yang diperbolehkan.")


def _safe_original_filename(filename: str, extension: str) -> str:
    original = Path(filename or f"catalog.{extension}").name[:160]
    return (
        _FILENAME_SAFE.sub("_", original).strip("._")[:120]
        or f"catalog.{extension}"
    )


async def upload_catalog_image(
    db,
    *,
    group_id: str,
    filename: str,
    content_type: str,
    data: bytes,
    user: dict,
) -> dict:
    group = await db[cp.GROUPS].find_one({"id": group_id}, {"_id": 0})
    if not group:
        raise HTTPException(404, "Kelompok harga tidak ditemukan.")
    if not data:
        raise HTTPException(400, "File gambar kosong.")
    if len(data) > MAX_CATALOG_IMAGE_BYTES:
        raise HTTPException(400, "Ukuran foto katalog maksimal 5 MB.")

    mime = sniff_catalog_image_mime(data, content_type)
    if mime not in CATALOG_IMAGE_MIMES:
        raise HTTPException(400, "Tipe gambar tidak didukung.")

    file_id = str(uuid.uuid4())
    extension = obj_storage.ext_for(mime, "jpg")
    original_filename = _safe_original_filename(filename, extension)
    storage_path = (
        f"{obj_storage.APP_NAME}/customer-pricing/{group_id}/"
        f"{file_id}.{extension}"
    )
    try:
        stored = obj_storage.put_object(storage_path, data, mime)
    except Exception:
        raise HTTPException(
            502, "Gagal mengunggah foto katalog. Silakan coba lagi."
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
        "kind": CATALOG_IMAGE_KIND,
        "group_id": group_id,
        "storage_path": stored.get("path") or storage_path,
        "content_type": mime,
        "original_filename": original_filename,
        "size": image["size"],
        "uploaded_at": now,
        "uploaded_by": actor_snapshot(user),
        "is_deleted": False,
    }
    await db.files.insert_one(file_doc)
    await db[cp.GROUPS].update_one(
        {"id": group_id},
        {
            "$set": {
                "catalog_image": image,
                "updated_at": now,
                "updated_by": actor_snapshot(user),
            }
        },
    )
    previous_file_id = ((group.get("catalog_image") or {}).get("file_id"))
    if previous_file_id and previous_file_id != file_id:
        await db.files.update_one(
            {
                "id": previous_file_id,
                "kind": CATALOG_IMAGE_KIND,
                "group_id": group_id,
            },
            {"$set": {"is_deleted": True}},
        )
    return await cp.get_group(db, group_id)


async def remove_catalog_image(db, *, group_id: str, user: dict) -> dict:
    group = await db[cp.GROUPS].find_one({"id": group_id}, {"_id": 0})
    if not group:
        raise HTTPException(404, "Kelompok harga tidak ditemukan.")
    image = group.get("catalog_image") or {}
    now = now_iso()
    await db[cp.GROUPS].update_one(
        {"id": group_id},
        {
            "$set": {
                "catalog_image": None,
                "updated_at": now,
                "updated_by": actor_snapshot(user),
            }
        },
    )
    if image.get("file_id"):
        await db.files.update_one(
            {
                "id": image["file_id"],
                "kind": CATALOG_IMAGE_KIND,
                "group_id": group_id,
            },
            {"$set": {"is_deleted": True}},
        )
    return await cp.get_group(db, group_id)


async def get_catalog_image(
    db, *, group_id: str
) -> Tuple[bytes, str, Optional[str]]:
    group = await db[cp.GROUPS].find_one(
        {"id": group_id}, {"_id": 0, "catalog_image": 1}
    )
    image = (group or {}).get("catalog_image") or {}
    file_id = str(image.get("file_id") or "").strip()
    if not file_id:
        raise HTTPException(404, "Foto katalog tidak tersedia.")
    record = await db.files.find_one(
        {
            "id": file_id,
            "kind": CATALOG_IMAGE_KIND,
            "group_id": group_id,
            "is_deleted": {"$ne": True},
        },
        {"_id": 0},
    )
    if not record or not record.get("storage_path"):
        raise HTTPException(404, "Foto katalog tidak tersedia.")
    media_type = str(record.get("content_type") or image.get("content_type") or "")
    if media_type not in CATALOG_IMAGE_MIMES:
        raise HTTPException(404, "Foto katalog tidak tersedia.")
    try:
        data, fetched_type = obj_storage.get_object(record["storage_path"])
    except FileNotFoundError:
        raise HTTPException(404, "Foto katalog tidak tersedia.") from None
    except Exception:
        raise HTTPException(502, "Foto katalog tidak dapat dimuat.") from None
    return (
        data,
        media_type if media_type in CATALOG_IMAGE_MIMES else fetched_type,
        record.get("original_filename"),
    )
