"""Sales Customer Master Service.

Provides local customer master management for Sales orders (Penjualan),
including duplicate protection with normalized customer names, canonical IDs,
search, and auto-creation during PO creation flow.
"""
import uuid
import re
import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from fastapi import HTTPException
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError, OperationFailure

from services.customer_identity import (
    build_identity_context,
    display_customer_name,
    resolve_customer_identity,
)

logger = logging.getLogger(__name__)

COLLECTION = "sales_customers"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_customer_name(name: str) -> str:
    """Normalize customer name for duplicate protection:
    - trim whitespace
    - lowercase
    - normalize multiple spaces into a single space
    """
    if not name:
        return ""
    return " ".join(name.strip().lower().split())


class SalesCustomerCreatePayload(BaseModel):
    name: str = Field(..., description="Nama Pelanggan")
    phone: str = Field(..., description="No. HP / WhatsApp")
    address: str = Field(..., description="Alamat")
    pic: Optional[str] = ""
    email: Optional[str] = ""
    city: Optional[str] = ""
    notes: Optional[str] = ""


class SalesCustomerUpdatePayload(BaseModel):
    name: str = Field(..., description="Nama Pelanggan")
    phone: str = Field(..., description="No. HP / WhatsApp")
    address: str = Field(..., description="Alamat")
    pic: Optional[str] = ""
    email: Optional[str] = ""
    city: Optional[str] = ""
    notes: Optional[str] = ""


class SalesCustomerByKeyUpdatePayload(BaseModel):
    """Update or materialize a customer profile by canonical customer_key."""
    customer_key: str = Field(..., description="Canonical customer identity key")
    name: str = Field(..., description="Nama Pelanggan")
    phone: str = Field(..., description="No. HP / WhatsApp")
    address: str = Field(..., description="Alamat")
    pic: Optional[str] = ""
    email: Optional[str] = ""
    city: Optional[str] = ""
    notes: Optional[str] = ""


class SalesCustomerStatusPayload(BaseModel):
    is_active: bool = Field(..., description="True = Aktif, False = Nonaktif")


class SalesCustomerStatusByKeyPayload(BaseModel):
    customer_key: str = Field(..., description="Canonical customer identity key")
    is_active: bool = Field(..., description="True = Aktif, False = Nonaktif")


def customer_is_active(doc: Optional[dict]) -> bool:
    """Legacy missing/null is_active means Aktif. Zero-write on read."""
    if not doc:
        return True
    if "is_active" not in doc or doc.get("is_active") is None:
        return True
    return bool(doc.get("is_active"))


def parse_customer_key(customer_key: str) -> Tuple[str, str]:
    """Return (kind, value) for id: / name: / ambiguous: keys."""
    raw = str(customer_key or "").strip()
    if not raw or ":" not in raw:
        raise HTTPException(400, "customer_key tidak valid.")
    kind, _, value = raw.partition(":")
    kind = kind.strip().lower()
    value = value.strip()
    if kind not in ("id", "name", "ambiguous") or not value:
        raise HTTPException(400, "customer_key tidak valid.")
    if kind == "ambiguous":
        raise HTTPException(
            400,
            "Identitas pelanggan ambigu. Pilih / buat pelanggan master yang jelas sebelum mengubah data.",
        )
    return kind, value


async def ensure_sales_customer_indexes(db):
    """Ensure unique and lookup indexes on sales_customers collection.

    The unique normalized_name index is required for concurrency-safe duplicate protection.
    If creation fails because duplicates already exist or an operational failure occurs,
    it reports clearly and raises rather than swallowing silently.
    """
    try:
        await db[COLLECTION].create_index("id", unique=True)
        await db[COLLECTION].create_index("normalized_name", unique=True)
        await db[COLLECTION].create_index("created_at")
    except (DuplicateKeyError, OperationFailure) as exc:
        msg = f"Failed to ensure unique index on collection '{COLLECTION}': duplicates exist or operation failed ({exc})"
        logger.error(msg)
        raise RuntimeError(msg) from exc


async def list_sales_customers(db, q: Optional[str] = None) -> List[dict]:
    query = {}
    if q and str(q).strip():
        term = str(q).strip()
        rgx = {"$regex": re.escape(term), "$options": "i"}
        query["$or"] = [
            {"name": rgx},
            {"phone": rgx},
            {"pic": rgx},
            {"email": rgx},
            {"city": rgx},
            {"address": rgx},
        ]
    cursor = db[COLLECTION].find(query, {"_id": 0}).sort("name", 1)
    return await cursor.to_list(1000)


_ORDER_IDENTITY_PROJECTION = {
    "_id": 0,
    "customer": 1,
    "customer_name": 1,
    "customer_id": 1,
    "outlet": 1,
    "cancelled": 1,
    "is_test": 1,
    "is_demo": 1,
    "is_preview": 1,
    "test_record": 1,
    "po_number": 1,
}

_HISTORY_IDENTITY_PROJECTION = {
    "_id": 0,
    "customer": 1,
    "customer_name": 1,
    "customer_id": 1,
    "outlet": 1,
    "cancelled": 1,
}


def _public_master_row(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "name": doc.get("name") or "",
        "normalized_name": doc.get("normalized_name") or normalize_customer_name(doc.get("name") or ""),
        "phone": doc.get("phone") or "",
        "address": doc.get("address") or "",
        "pic": doc.get("pic") or "",
        "email": doc.get("email") or "",
        "city": doc.get("city") or "",
        "notes": doc.get("notes") or "",
        "is_active": customer_is_active(doc),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "created_by": doc.get("created_by"),
        "updated_by": doc.get("updated_by"),
        "is_master": True,
        "source": "master",
        "customer_key": f"id:{doc.get('id')}" if doc.get("id") else "",
    }


def _discovered_row(resolved, *, source: str) -> dict:
    return {
        "id": None,
        "name": resolved.display_name,
        "normalized_name": resolved.normalized_name,
        "phone": "",
        "address": "",
        "pic": "",
        "email": "",
        "city": "",
        "notes": "",
        "is_active": True,
        "is_master": False,
        "source": source,
        "customer_key": resolved.key,
        "identity_ambiguous": resolved.ambiguous,
    }


def _usable_identity_record(record: dict) -> bool:
    if not record:
        return False
    if record.get("cancelled") is True:
        return False
    if record.get("is_test") or record.get("is_demo") or record.get("is_preview") or record.get("test_record"):
        return False
    return bool(display_customer_name(record))


def _matches_customer_query(row: dict, term: str) -> bool:
    if not term:
        return True
    q = term.lower()
    blob = " ".join([
        str(row.get("name") or ""),
        str(row.get("phone") or ""),
        str(row.get("pic") or ""),
        str(row.get("email") or ""),
        str(row.get("city") or ""),
        str(row.get("address") or ""),
        str(row.get("customer_key") or ""),
    ]).lower()
    return q in blob


def _source_union(current: str, incoming: str) -> str:
    if current == incoming:
        return current
    return "mixed"


async def list_unified_sales_customers(db, q: Optional[str] = None) -> List[dict]:
    """Read-only union of master + live PO customers + prediction history. Zero writes."""
    masters = await db[COLLECTION].find({}, {"_id": 0}).to_list(1000)
    orders = await db.orders.find({}, _ORDER_IDENTITY_PROJECTION).to_list(5000)
    history = await db.order_prediction_history.find(
        {"cancelled": {"$ne": True}}, _HISTORY_IDENTITY_PROJECTION,
    ).to_list(10000)

    live = [o for o in orders if _usable_identity_record(o)]
    hist = [h for h in history if _usable_identity_record(h)]
    context = build_identity_context(masters, live, hist)

    buckets: dict = {}
    for master in masters:
        resolved = resolve_customer_identity(master, context, kind="master")
        if not resolved.key:
            continue
        buckets[resolved.key] = _public_master_row(master)

    for rec, src in [(o, "live_po") for o in live] + [(h, "prediction_history") for h in hist]:
        resolved = resolve_customer_identity(rec, context)
        if not resolved.key:
            continue
        existing = buckets.get(resolved.key)
        if existing:
            existing["source"] = _source_union(existing.get("source") or src, src)
            continue
        buckets[resolved.key] = _discovered_row(resolved, source=src)

    rows = list(buckets.values())
    term = str(q or "").strip()
    if term:
        rows = [r for r in rows if _matches_customer_query(r, term)]
    rows.sort(key=lambda r: (str(r.get("name") or "").lower(), str(r.get("customer_key") or "")))
    return rows[:1000]


async def get_sales_customer(db, customer_id: str) -> Optional[dict]:
    return await db[COLLECTION].find_one({"id": customer_id}, {"_id": 0})


async def find_customer_by_normalized_name(db, name: str) -> Optional[dict]:
    norm = normalize_customer_name(name)
    if not norm:
        return None
    return await db[COLLECTION].find_one({"normalized_name": norm}, {"_id": 0})


async def create_sales_customer(db, payload: SalesCustomerCreatePayload, user_email: Optional[str] = None) -> dict:
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "Nama Pelanggan wajib diisi.")
    phone = (payload.phone or "").strip()
    if not phone:
        raise HTTPException(400, "No. HP / WhatsApp wajib diisi.")
    address = (payload.address or "").strip()
    if not address:
        raise HTTPException(400, "Alamat wajib diisi.")

    norm = normalize_customer_name(name)
    existing = await db[COLLECTION].find_one({"normalized_name": norm}, {"_id": 0})
    if existing:
        raise HTTPException(409, f"Pelanggan dengan nama '{name}' sudah terdaftar.")

    cid = str(uuid.uuid4())
    now = now_iso()
    doc = {
        "id": cid,
        "name": name,
        "normalized_name": norm,
        "phone": phone,
        "address": address,
        "pic": (payload.pic or "").strip(),
        "email": (payload.email or "").strip(),
        "city": (payload.city or "").strip(),
        "notes": (payload.notes or "").strip(),
        "created_at": now,
        "updated_at": now,
        "created_by": user_email,
    }

    try:
        await db[COLLECTION].insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, f"Pelanggan dengan nama '{name}' sudah terdaftar.")

    return {k: v for k, v in doc.items() if k != "_id"}


async def update_sales_customer(db, customer_id: str, payload: SalesCustomerUpdatePayload, user_email: Optional[str] = None) -> dict:
    existing = await db[COLLECTION].find_one({"id": customer_id})
    if not existing:
        raise HTTPException(404, "Pelanggan tidak ditemukan.")

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(400, "Nama Pelanggan wajib diisi.")
    phone = (payload.phone or "").strip()
    if not phone:
        raise HTTPException(400, "No. HP / WhatsApp wajib diisi.")
    address = (payload.address or "").strip()
    if not address:
        raise HTTPException(400, "Alamat wajib diisi.")

    norm = normalize_customer_name(name)
    conflict = await db[COLLECTION].find_one({"normalized_name": norm, "id": {"$ne": customer_id}}, {"_id": 0})
    if conflict:
        raise HTTPException(409, f"Pelanggan dengan nama '{name}' sudah digunakan oleh data lain.")

    now = now_iso()
    updates = {
        "name": name,
        "normalized_name": norm,
        "phone": phone,
        "address": address,
        "pic": (payload.pic or "").strip(),
        "email": (payload.email or "").strip(),
        "city": (payload.city or "").strip(),
        "notes": (payload.notes or "").strip(),
        "updated_at": now,
        "updated_by": user_email,
    }

    await db[COLLECTION].update_one({"id": customer_id}, {"$set": updates})
    updated = await db[COLLECTION].find_one({"id": customer_id}, {"_id": 0})
    return updated


def _validate_profile_fields(name: str, phone: str, address: str) -> Tuple[str, str, str]:
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "Nama Pelanggan wajib diisi.")
    phone = (phone or "").strip()
    if not phone:
        raise HTTPException(400, "No. HP / WhatsApp wajib diisi.")
    address = (address or "").strip()
    if not address:
        raise HTTPException(400, "Alamat wajib diisi.")
    return name, phone, address


async def _insert_master_profile(
    db,
    *,
    cid: str,
    name: str,
    phone: str,
    address: str,
    pic: str = "",
    email: str = "",
    city: str = "",
    notes: str = "",
    is_active: Optional[bool] = None,
    user_email: Optional[str] = None,
) -> dict:
    norm = normalize_customer_name(name)
    conflict = await db[COLLECTION].find_one({"normalized_name": norm}, {"_id": 0})
    if conflict and conflict.get("id") != cid:
        raise HTTPException(409, f"Pelanggan dengan nama '{name}' sudah terdaftar.")

    now = now_iso()
    doc = {
        "id": cid,
        "name": name,
        "normalized_name": norm,
        "phone": phone,
        "address": address,
        "pic": (pic or "").strip(),
        "email": (email or "").strip(),
        "city": (city or "").strip(),
        "notes": (notes or "").strip(),
        "created_at": now,
        "updated_at": now,
        "created_by": user_email,
    }
    if is_active is not None:
        doc["is_active"] = bool(is_active)

    try:
        await db[COLLECTION].insert_one(doc)
    except DuplicateKeyError:
        # Concurrent create: if same id won, return it; if name conflict, 409.
        existing_by_id = await db[COLLECTION].find_one({"id": cid}, {"_id": 0})
        if existing_by_id:
            return existing_by_id
        raise HTTPException(409, f"Pelanggan dengan nama '{name}' sudah terdaftar.")
    return {k: v for k, v in doc.items() if k != "_id"}


async def upsert_sales_customer_by_key(
    db,
    payload: SalesCustomerByKeyUpdatePayload,
    user_email: Optional[str] = None,
) -> dict:
    """Update existing master or materialize one profile for a history/live identity.

    Does not rewrite orders/history. Uses canonical customer_key only.
    """
    kind, value = parse_customer_key(payload.customer_key)
    name, phone, address = _validate_profile_fields(payload.name, payload.phone, payload.address)
    profile = SalesCustomerUpdatePayload(
        name=name,
        phone=phone,
        address=address,
        pic=payload.pic or "",
        email=payload.email or "",
        city=payload.city or "",
        notes=payload.notes or "",
    )

    if kind == "id":
        existing = await db[COLLECTION].find_one({"id": value}, {"_id": 0})
        if existing:
            return await update_sales_customer(db, value, profile, user_email=user_email)
        return await _insert_master_profile(
            db,
            cid=value,
            name=name,
            phone=phone,
            address=address,
            pic=payload.pic or "",
            email=payload.email or "",
            city=payload.city or "",
            notes=payload.notes or "",
            user_email=user_email,
        )

    # name:{normalized}
    existing = await db[COLLECTION].find_one({"normalized_name": value}, {"_id": 0})
    if existing:
        return await update_sales_customer(db, existing["id"], profile, user_email=user_email)

    # Prefer payload name; if normalize differs from key, still use payload name but
    # require that its normalized form matches the identity key to avoid hijacking.
    if normalize_customer_name(name) != value:
        raise HTTPException(
            400,
            "Nama pelanggan harus cocok dengan identitas histori yang dipilih.",
        )
    return await _insert_master_profile(
        db,
        cid=str(uuid.uuid4()),
        name=name,
        phone=phone,
        address=address,
        pic=payload.pic or "",
        email=payload.email or "",
        city=payload.city or "",
        notes=payload.notes or "",
        user_email=user_email,
    )


async def set_sales_customer_active_status(
    db,
    customer_id: str,
    is_active: bool,
    user_email: Optional[str] = None,
) -> dict:
    """Set active/inactive on an existing master. Idempotent. No hard delete.

    Legacy docs without is_active are treated as active with ZERO WRITE until
    the user explicitly changes status (including reaffirming Aktif → write True).
    Reaffirming Aktif on a legacy-missing doc still writes is_active=True once
    only when the client explicitly requests a status change; identical-status
    calls that already match effective state without an explicit field remain
    zero-write for the common legacy+Aktif case.
    """
    existing = await db[COLLECTION].find_one({"id": customer_id})
    if not existing:
        raise HTTPException(404, "Pelanggan tidak ditemukan.")

    desired = bool(is_active)
    has_field = "is_active" in existing and existing.get("is_active") is not None
    current = customer_is_active(existing)

    # Legacy missing + request Aktif → zero write (still conceptually Aktif)
    if not has_field and desired is True:
        return {k: v for k, v in existing.items() if k != "_id"}

    # Explicit same status already stored → idempotent return
    if has_field and current == desired:
        return {k: v for k, v in existing.items() if k != "_id"}

    now = now_iso()
    await db[COLLECTION].update_one(
        {"id": customer_id},
        {"$set": {"is_active": desired, "updated_at": now, "updated_by": user_email}},
    )
    updated = await db[COLLECTION].find_one({"id": customer_id}, {"_id": 0})
    return updated


async def set_sales_customer_active_status_by_key(
    db,
    payload: SalesCustomerStatusByKeyPayload,
    user_email: Optional[str] = None,
) -> dict:
    """Set status by canonical key; materialize minimal profile for history-only rows."""
    kind, value = parse_customer_key(payload.customer_key)
    desired = bool(payload.is_active)

    if kind == "id":
        existing = await db[COLLECTION].find_one({"id": value}, {"_id": 0})
        if existing:
            return await set_sales_customer_active_status(db, value, desired, user_email=user_email)
        # Materialize with stable id from identity; placeholders for required contact fields.
        display = value
        # Try to find a display name from orders/history for nicer master name
        order = await db.orders.find_one(
            {"customer_id": value, "cancelled": {"$ne": True}},
            {"customer": 1, "customer_name": 1, "_id": 0},
        )
        if not order:
            order = await db.order_prediction_history.find_one(
                {"customer_id": value, "cancelled": {"$ne": True}},
                {"customer": 1, "customer_name": 1, "_id": 0},
            )
        if order:
            display = display_customer_name(order) or value
        created = await _insert_master_profile(
            db,
            cid=value,
            name=display,
            phone="-",
            address="-",
            is_active=desired,
            user_email=user_email,
        )
        return created

    # name:{normalized}
    existing = await db[COLLECTION].find_one({"normalized_name": value}, {"_id": 0})
    if existing:
        return await set_sales_customer_active_status(db, existing["id"], desired, user_email=user_email)

    # Reconstruct a reasonable display name from the normalized key
    display = value
    order = await db.orders.find_one(
        {
            "$or": [
                {"customer": {"$regex": f"^{re.escape(value)}$", "$options": "i"}},
                {"customer_name": {"$regex": f"^{re.escape(value)}$", "$options": "i"}},
            ],
            "cancelled": {"$ne": True},
        },
        {"customer": 1, "customer_name": 1, "_id": 0},
    )
    if not order:
        order = await db.order_prediction_history.find_one(
            {
                "$or": [
                    {"customer": {"$regex": f"^{re.escape(value)}$", "$options": "i"}},
                    {"customer_name": {"$regex": f"^{re.escape(value)}$", "$options": "i"}},
                ],
                "cancelled": {"$ne": True},
            },
            {"customer": 1, "customer_name": 1, "_id": 0},
        )
    if order:
        display = display_customer_name(order) or value

    if normalize_customer_name(display) != value:
        display = value

    created = await _insert_master_profile(
        db,
        cid=str(uuid.uuid4()),
        name=display,
        phone="-",
        address="-",
        is_active=desired,
        user_email=user_email,
    )
    return created


async def prepare_customer_for_po(
    db,
    customer_name: str,
    customer_id: Optional[str] = None,
    phone: Optional[str] = None,
    address: Optional[str] = None,
    pic: Optional[str] = None,
    email: Optional[str] = None,
    city: Optional[str] = None,
    user_email: Optional[str] = None,
) -> Tuple[str, str, bool, Optional[dict]]:
    """Resolves existing customer or prepares candidate customer doc for PO creation.
    DOES NOT persist to database yet.
    Returns (customer_id, customer_name, is_new, candidate_doc).
    """
    raw_name = (customer_name or "").strip()
    if not raw_name:
        raise HTTPException(400, "Customer wajib diisi.")

    # 1. By ID if specified
    if customer_id and str(customer_id).strip():
        existing_by_id = await db[COLLECTION].find_one({"id": customer_id.strip()}, {"_id": 0})
        if existing_by_id:
            return existing_by_id["id"], existing_by_id["name"], False, None

    # 2. By normalized name
    norm = normalize_customer_name(raw_name)
    existing_by_norm = await db[COLLECTION].find_one({"normalized_name": norm}, {"_id": 0})
    if existing_by_norm:
        return existing_by_norm["id"], existing_by_norm["name"], False, None

    # 3. New customer: prepare candidate doc without persisting yet
    req_phone = (phone or "").strip() or "-"
    req_addr = (address or "").strip() or "-"

    cid = str(uuid.uuid4())
    now = now_iso()
    candidate_doc = {
        "id": cid,
        "name": raw_name,
        "normalized_name": norm,
        "phone": req_phone,
        "address": req_addr,
        "pic": (pic or "").strip(),
        "email": (email or "").strip(),
        "city": (city or "").strip(),
        "notes": "",
        "created_at": now,
        "updated_at": now,
        "created_by": user_email,
    }
    return cid, raw_name, True, candidate_doc


async def link_or_create_customer_for_po(
    db,
    order_id: str,
    candidate_doc: dict,
) -> str:
    """Persists candidate customer master doc for an already persisted PO order.
    - If unique normalized_name collision occurs (race condition), reuses winner.
    - If winner ID differs from candidate ID, updates the PO's customer_id to point to winner.
    - If customer persistence fails operationally, rolls back ONLY the exact order_id PO.
    Returns the resolved customer_id.
    """
    cid = candidate_doc["id"]
    norm = candidate_doc["normalized_name"]
    try:
        try:
            await db[COLLECTION].insert_one(candidate_doc)
            return cid
        except DuplicateKeyError:
            winner = await db[COLLECTION].find_one({"normalized_name": norm}, {"_id": 0, "id": 1})
            if winner:
                winner_id = winner["id"]
                if winner_id != cid:
                    await db.orders.update_one({"id": order_id}, {"$set": {"customer_id": winner_id}})
                return winner_id
            raise
    except Exception:
        # Roll back ONLY the exact newly created PO by its exact order ID
        await db.orders.delete_one({"id": order_id})
        raise


async def resolve_or_create_customer_for_po(
    db,
    customer_name: str,
    customer_id: Optional[str] = None,
    phone: Optional[str] = None,
    address: Optional[str] = None,
    pic: Optional[str] = None,
    email: Optional[str] = None,
    city: Optional[str] = None,
    user_email: Optional[str] = None,
) -> Tuple[str, str, bool]:
    """Backward-compatible helper that prepares and persists customer immediately."""
    cid, name, is_new, candidate = await prepare_customer_for_po(
        db, customer_name, customer_id, phone, address, pic, email, city, user_email
    )
    if is_new and candidate:
        try:
            await db[COLLECTION].insert_one(candidate)
            return cid, name, True
        except DuplicateKeyError:
            winner = await db[COLLECTION].find_one({"normalized_name": candidate["normalized_name"]}, {"_id": 0, "id": 1, "name": 1})
            if winner:
                return winner["id"], winner.get("name") or name, False
            raise
    return cid, name, False
