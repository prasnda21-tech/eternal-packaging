"""Visit photo attachments. Reuses project object storage + db.files catalog."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from fastapi import HTTPException

from services import storage as obj_storage
from services.sales_activity import ownership as ownership_svc
from services.sales_activity.clock import iso_now, parse_dt
from services.sales_activity.constants import (
    COL_ACTIVITIES,
    COL_OUTLETS,
    COL_VISIT_PHOTOS,
    MAX_VISIT_PHOTO_BYTES,
    MAX_VISIT_PHOTOS_PER_ACTIVITY,
    PERM_ACTIVITY_CREATE,
    PERM_OWNERSHIP_MANAGE,
    VISIT_PHOTO_KIND,
    VISIT_PHOTO_MIMES,
)
from services.sales_activity.util import actor_id, actor_name, new_id, public_doc, stamp_created

_FAKE_ZERO = 1e-6
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _can_work_outlet(snap: dict, user: dict, perms: set) -> bool:
    if PERM_OWNERSHIP_MANAGE in perms:
        return True
    return bool(snap and snap.get("current_owner_id") == actor_id(user) and not snap.get("in_pool"))


def _can_view_outlet(snap: dict, user: dict, perms: set) -> bool:
    if PERM_OWNERSHIP_MANAGE in perms:
        return True
    return bool(snap and snap.get("current_owner_id") == actor_id(user))


def sniff_image_mime(data: bytes, declared: str = "") -> str:
    if not data or len(data) < 12:
        raise HTTPException(400, "File gambar tidak valid")
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    declared = (declared or "").lower().strip()
    if declared in VISIT_PHOTO_MIMES:
        raise HTTPException(400, "Isi file bukan gambar JPEG/PNG/WEBP yang valid")
    raise HTTPException(400, "Hanya file gambar JPEG, PNG, atau WEBP yang diperbolehkan")


def parse_coordinates(lat_raw, lng_raw, accuracy_raw) -> Tuple[Optional[float], Optional[float], Optional[float], str]:
    if lat_raw in (None, "") and lng_raw in (None, ""):
        return None, None, None, "UNAVAILABLE"
    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (TypeError, ValueError):
        return None, None, None, "UNAVAILABLE"
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None, None, None, "UNAVAILABLE"
    if abs(lat) < _FAKE_ZERO and abs(lng) < _FAKE_ZERO:
        return None, None, None, "UNAVAILABLE"
    accuracy = None
    try:
        if accuracy_raw not in (None, ""):
            accuracy = float(accuracy_raw)
            if accuracy < 0 or accuracy > 100000:
                accuracy = None
    except (TypeError, ValueError):
        accuracy = None
    return lat, lng, accuracy, "OK"


def maps_url(lat: Optional[float], lng: Optional[float]) -> Optional[str]:
    if lat is None or lng is None:
        return None
    return f"https://www.google.com/maps?q={lat},{lng}"


def public_photo(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = public_doc(doc)
    out.pop("storage_path", None)
    out["photo_id"] = out.get("id")
    out["maps_url"] = maps_url(out.get("latitude"), out.get("longitude"))
    out["file_url"] = f"/api/sales-activity/visit-photos/{out['id']}/file"
    return out


async def _assert_can_work(db, outlet_id: str, user: dict, perms: set) -> dict:
    if PERM_ACTIVITY_CREATE not in perms and PERM_OWNERSHIP_MANAGE not in perms:
        raise HTTPException(403, f"Permission '{PERM_ACTIVITY_CREATE}' diperlukan")
    outlet = await db[COL_OUTLETS].find_one({"id": outlet_id}, {"_id": 0})
    if not outlet:
        raise HTTPException(404, "Outlet tidak ditemukan")
    snap = await ownership_svc.get_ownership(db, outlet_id)
    if not _can_work_outlet(snap, user, perms):
        raise HTTPException(403, "Anda tidak berhak mengunggah foto visit outlet ini")
    return snap


async def upload_visit_photo(
    db,
    *,
    user: dict,
    perms: set,
    outlet_id: str,
    filename: str,
    content_type: str,
    data: bytes,
    latitude=None,
    longitude=None,
    accuracy=None,
    location_status_hint: str = "",
    client_captured_at: str = "",
    activity_id: str = "",
    uploaded_by=None,
    sales_id=None,
    owner_id=None,
) -> dict:
    del uploaded_by, sales_id, owner_id
    outlet_id = str(outlet_id or "").strip()
    if not outlet_id:
        raise HTTPException(400, "outlet_id wajib")
    await _assert_can_work(db, outlet_id, user, perms)
    if not data:
        raise HTTPException(400, "File kosong")
    if len(data) > MAX_VISIT_PHOTO_BYTES:
        raise HTTPException(400, "Ukuran file maksimal 5MB")
    mime = sniff_image_mime(data, content_type)
    if mime not in VISIT_PHOTO_MIMES:
        raise HTTPException(400, "Hanya file gambar JPEG, PNG, atau WEBP yang diperbolehkan")

    lat, lng, acc, loc_status = parse_coordinates(latitude, longitude, accuracy)
    hint = str(location_status_hint or "").strip().upper()
    if loc_status != "OK" and hint in {"DENIED", "UNAVAILABLE", "UNSUPPORTED"}:
        loc_status = "DENIED" if hint == "DENIED" else "UNAVAILABLE"
    if lat is None or lng is None:
        lat = lng = acc = None

    activity_id = str(activity_id or "").strip()
    if activity_id:
        activity = await db[COL_ACTIVITIES].find_one({"id": activity_id}, {"_id": 0})
        if not activity or activity.get("outlet_id") != outlet_id:
            raise HTTPException(404, "Aktivitas tidak ditemukan")
        existing = await db[COL_VISIT_PHOTOS].count_documents({"activity_id": activity_id, "is_deleted": {"$ne": True}})
        if existing >= MAX_VISIT_PHOTOS_PER_ACTIVITY:
            raise HTTPException(400, f"Maksimum {MAX_VISIT_PHOTOS_PER_ACTIVITY} foto visit per aktivitas")

    photo_id = new_id()
    ext = obj_storage.ext_for(mime, "jpg")
    orig = Path(filename or f"visit.{ext}").name
    orig = _FILENAME_SAFE.sub("_", orig).strip("._") or f"visit.{ext}"
    storage_path = f"{obj_storage.APP_NAME}/sales-visit/{outlet_id}/{activity_id or 'pending'}/{photo_id}.{ext}"
    try:
        stored = obj_storage.put_object(storage_path, data, mime)
    except Exception:
        raise HTTPException(502, "Gagal mengunggah foto. Silakan coba lagi.") from None

    now = iso_now()
    actor = {"id": actor_id(user), "name": actor_name(user), "email": user.get("email")}
    client_ts = parse_dt(client_captured_at)
    sort_order = 0
    if activity_id:
        sort_order = int(existing)
    doc = stamp_created({
        "id": photo_id,
        "activity_id": activity_id or None,
        "outlet_id": outlet_id,
        "storage_path": stored.get("path") or storage_path,
        "original_filename": orig,
        "content_type": mime,
        "size": stored.get("size") or len(data),
        "uploaded_at": now,
        "uploaded_by": actor["id"],
        "uploaded_by_name": actor["name"],
        "uploaded_by_email": actor["email"],
        "visit_at": now,
        "client_captured_at": client_ts.isoformat() if client_ts else None,
        "latitude": lat,
        "longitude": lng,
        "location_accuracy_meters": acc,
        "location_status": loc_status,
        "sort_order": sort_order,
        "is_deleted": False,
        "kind": VISIT_PHOTO_KIND,
    })
    await db[COL_VISIT_PHOTOS].insert_one(doc)
    await db.files.insert_one({
        "id": photo_id,
        "storage_path": doc["storage_path"],
        "original_filename": orig,
        "content_type": mime,
        "size": doc["size"],
        "kind": VISIT_PHOTO_KIND,
        "outlet_id": outlet_id,
        "activity_id": activity_id or None,
        "uploaded_by": actor["id"],
        "uploaded_at": now,
        "is_deleted": False,
    })
    if activity_id:
        await _sync_activity_photo_fields(db, activity_id)
    return {"photo": public_photo(doc)}


async def load_owned_photos(db, *, photo_ids: Iterable[str], outlet_id: str, user: dict) -> List[dict]:
    ids = [str(pid).strip() for pid in (photo_ids or []) if str(pid).strip()]
    if not ids:
        return []
    if len(ids) > MAX_VISIT_PHOTOS_PER_ACTIVITY:
        raise HTTPException(400, f"Maksimum {MAX_VISIT_PHOTOS_PER_ACTIVITY} foto visit per aktivitas")
    actor = actor_id(user)
    rows = await db[COL_VISIT_PHOTOS].find(
        {"id": {"$in": ids}, "is_deleted": {"$ne": True}},
        {"_id": 0},
    ).to_list(MAX_VISIT_PHOTOS_PER_ACTIVITY + 2)
    by_id = {row["id"]: row for row in rows}
    loaded = []
    for photo_id in ids:
        row = by_id.get(photo_id)
        if not row:
            raise HTTPException(400, "Foto visit tidak ditemukan")
        if row.get("uploaded_by") != actor:
            raise HTTPException(403, "Foto visit bukan milik Anda")
        if row.get("outlet_id") != outlet_id:
            raise HTTPException(400, "Foto visit tidak sesuai outlet")
        loaded.append(row)
    return loaded


async def claim_pending_photos(db, *, photo_ids: Iterable[str], outlet_id: str, activity_id: str, user: dict) -> List[dict]:
    rows = await load_owned_photos(db, photo_ids=photo_ids, outlet_id=outlet_id, user=user)
    claimed = []
    for index, row in enumerate(rows):
        photo_id = row["id"]
        if row.get("activity_id") and row.get("activity_id") != activity_id:
            raise HTTPException(400, "Foto visit sudah terpasang di aktivitas lain")
        await db[COL_VISIT_PHOTOS].update_one(
            {"id": photo_id},
            {"$set": {"activity_id": activity_id, "sort_order": index, "updated_at": iso_now()}},
        )
        await db.files.update_one({"id": photo_id, "kind": VISIT_PHOTO_KIND}, {"$set": {"activity_id": activity_id}})
        row["activity_id"] = activity_id
        row["sort_order"] = index
        claimed.append(row)
    await _sync_activity_photo_fields(db, activity_id)
    return claimed


async def _sync_activity_photo_fields(db, activity_id: str) -> None:
    photos = await list_photos_for_activity(db, activity_id)
    await db[COL_ACTIVITIES].update_one(
        {"id": activity_id},
        {"$set": {
            "visit_photo_ids": [p["id"] for p in photos],
            "visit_photo_count": len(photos),
            "has_visit_location": any(p.get("location_status") == "OK" for p in photos),
            "updated_at": iso_now(),
        }},
    )


async def list_photos_for_activity(db, activity_id: str) -> List[dict]:
    rows = await db[COL_VISIT_PHOTOS].find(
        {"activity_id": activity_id, "is_deleted": {"$ne": True}},
        {"_id": 0},
    ).sort("sort_order", 1).to_list(MAX_VISIT_PHOTOS_PER_ACTIVITY)
    return rows


async def attach_photos_to_activities(db, activities: List[dict]) -> List[dict]:
    ids = [row.get("id") for row in activities if row.get("id")]
    if not ids:
        return activities
    rows = await db[COL_VISIT_PHOTOS].find(
        {"activity_id": {"$in": ids}, "is_deleted": {"$ne": True}},
        {"_id": 0},
    ).sort("sort_order", 1).to_list(MAX_VISIT_PHOTOS_PER_ACTIVITY * max(1, len(ids)))
    grouped = {}
    for row in rows:
        grouped.setdefault(row.get("activity_id"), []).append(public_photo(row))
    out = []
    for activity in activities:
        photos = grouped.get(activity.get("id")) or []
        item = dict(activity)
        item["visit_photos"] = photos
        item["visit_photo_count"] = len(photos)
        item["has_visit_location"] = any(p.get("location_status") == "OK" for p in photos)
        out.append(item)
    return out


async def get_photo(db, photo_id: str) -> dict:
    doc = await db[COL_VISIT_PHOTOS].find_one({"id": photo_id, "is_deleted": {"$ne": True}}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Foto visit tidak ditemukan")
    return doc


async def download_photo(db, photo_id: str, user: dict, perms: set) -> Tuple[bytes, str, str]:
    doc = await get_photo(db, photo_id)
    snap = await ownership_svc.get_ownership(db, doc["outlet_id"])
    if not _can_view_outlet(snap, user, perms) and doc.get("uploaded_by") != actor_id(user):
        raise HTTPException(403, "Anda tidak berhak melihat foto visit ini")
    try:
        data, ct = obj_storage.get_object(doc["storage_path"])
    except FileNotFoundError:
        raise HTTPException(404, "File tidak ditemukan") from None
    except Exception:
        raise HTTPException(502, "Gagal mengambil file.") from None
    return data, doc.get("content_type") or ct, doc.get("original_filename") or "visit.jpg"


async def delete_photo(db, photo_id: str, user: dict, perms: set) -> dict:
    doc = await get_photo(db, photo_id)
    await _assert_can_work(db, doc["outlet_id"], user, perms)
    if PERM_OWNERSHIP_MANAGE not in perms and doc.get("uploaded_by") != actor_id(user):
        raise HTTPException(403, "Anda tidak berhak menghapus foto visit ini")
    now = iso_now()
    await db[COL_VISIT_PHOTOS].update_one({"id": photo_id}, {"$set": {"is_deleted": True, "updated_at": now}})
    await db.files.update_one({"id": photo_id, "kind": VISIT_PHOTO_KIND}, {"$set": {"is_deleted": True}})
    if doc.get("activity_id"):
        await _sync_activity_photo_fields(db, doc["activity_id"])
    return {"ok": True, "photo_id": photo_id}
