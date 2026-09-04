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
