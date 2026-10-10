"""Master Barang/Jasa — Accurate item sync + local ERP create + PO resolution.

Reuses AccurateClient (same auth, headers, timeouts, pagination as Import PO).
Accurate identity is `accurate_item_id` when present. Never match / auto-link by name.
Local items omit accurate_item_id and use stable internal `id`.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError, OperationFailure

from services.accurate import AccurateClient, normalize_accurate_item

COLLECTION = "master_items"
CODE_CLAIMS_COLLECTION = "master_item_code_claims"
UNKNOWN_ITEM_MSG = (
    "Item belum tersedia di Barang/Jasa. "
    "Sinkronkan Barang/Jasa dari Accurate terlebih dahulu."
)
ACCURATE_OWNED_FIELDS = (
    "item_code",
    "item_name",
    "item_type",
    "unit",
    "is_active",
    "accurate_updated_at",
)
ORIGIN_LOCAL = "local"
ORIGIN_ACCURATE = "accurate"
ALLOWED_LOCAL_ITEM_TYPES = frozenset({"INVENTORY", "SERVICE", "NON_INVENTORY"})
ALLOWED_PRODUCTION_PROCESS = frozenset({"print", "non_print"})
# ERP-owned: never taken from Accurate sync payloads.
ERP_OWNED_FIELDS = frozenset({"production_process"})
ACCURATE_ID_PARTIAL_INDEX = "accurate_item_id_partial_unique"
LEGACY_ACCURATE_ID_INDEX = "accurate_item_id_1"
CODE_CLAIMS_INDEX = "code_key_unique"
DUPLICATE_CODE_MSG = "Kode barang/jasa sudah dipakai."


def normalize_production_process(value: Any) -> Optional[str]:
    """Return print|non_print or None when unset/invalid."""
    proc = str(value or "").strip().lower()
    if proc in ALLOWED_PRODUCTION_PROCESS:
        return proc
    return None

_sync_lock = asyncio.Lock()


class MasterItemUnresolvedError(Exception):
    """PO line could not be bound to a Master Item by Accurate ID."""


class SyncInProgressError(Exception):
    """A Master Item sync is already running in this process."""


class DuplicateMasterItemCodeError(Exception):
    """Normalized item_code already claimed or present on a master row."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_item_code_key(raw: Any) -> str:
    return str(raw or "").strip().lower()


def resolve_master_origin(doc: Optional[Dict[str, Any]]) -> Optional[str]:
    if not doc:
        return None
    explicit = str(doc.get("origin") or "").strip().lower()
    if explicit in (ORIGIN_LOCAL, ORIGIN_ACCURATE):
        return explicit
    if str(doc.get("accurate_item_id") or "").strip():
        return ORIGIN_ACCURATE
    return ORIGIN_LOCAL


def public_master_item(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    aid = doc.get("accurate_item_id")
    aid_out = None if aid is None or str(aid).strip() == "" else str(aid).strip()
    return {
        "id": doc.get("id"),
        "accurate_item_id": aid_out,
        "item_code": doc.get("item_code"),
        "item_name": doc.get("item_name"),
        "item_type": doc.get("item_type"),
        "unit": doc.get("unit"),
        "is_active": doc.get("is_active"),
        "production_process": normalize_production_process(doc.get("production_process")),
        "origin": resolve_master_origin(doc),
        "accurate_updated_at": doc.get("accurate_updated_at"),
        "last_synced_at": doc.get("last_synced_at"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def _owned_payload(incoming: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key in ACCURATE_OWNED_FIELDS:
        if key not in incoming:
            continue
        val = incoming.get(key)
        # Do not wipe a stored value just because this list row omitted the field.
        if val is None or val == "":
            continue
        out[key] = val
    return out


def _fields_changed(existing: Dict[str, Any], incoming_owned: Dict[str, Any]) -> bool:
    for key, val in incoming_owned.items():
        if existing.get(key) != val:
            return True
    return False


async def ensure_master_item_indexes(db) -> Dict[str, Any]:
    """Startup-safe index ensure. Never drops indexes.

    Creates (idempotent):
    - partial unique on non-empty accurate_item_id
    - unique code_key claims for concurrent local create

    Does NOT drop the legacy full unique accurate_item_id_1. Structural drop
    belongs in prepare_master_item_indexes_for_local_create (explicit migrate).
    Never deletes master documents.
    """
    col = db[COLLECTION]
    claims = db[CODE_CLAIMS_COLLECTION]
    result: Dict[str, Any] = {
        "partial_accurate_id": False,
        "code_claims": False,
        "legacy_full_unique_present": False,
        "local_create_ready": False,
    }

    await col.create_index(
        [("accurate_item_id", 1)],
        unique=True,
        name=ACCURATE_ID_PARTIAL_INDEX,
        partialFilterExpression={
            "accurate_item_id": {"$exists": True, "$type": "string", "$gt": ""},
        },
    )
    result["partial_accurate_id"] = True

    await claims.create_index(
        [("code_key", 1)],
        unique=True,
        name=CODE_CLAIMS_INDEX,
    )
    result["code_claims"] = True

    status = await local_master_create_index_status(db)
    result["legacy_full_unique_present"] = status["legacy_full_unique_present"]
    result["local_create_ready"] = status["local_create_ready"]
    return result


async def prepare_master_item_indexes_for_local_create(
    db,
    *,
    allow_drop_legacy: bool = False,
) -> Dict[str, Any]:
    """Explicit structural prep for multi-local masters.

    Always ensures partial + claims. Drops legacy full unique accurate_item_id_1
    only when allow_drop_legacy=True (preview/migrate tooling — never implied by
    ordinary server startup).
    """
    result = await ensure_master_item_indexes(db)
    result["dropped_legacy_accurate_id"] = False
    if not allow_drop_legacy:
        return result
    if not result.get("legacy_full_unique_present"):
        return result
    col = db[COLLECTION]
    existing = await col.index_information()
    if ACCURATE_ID_PARTIAL_INDEX not in existing:
        return result
    try:
        await col.drop_index(LEGACY_ACCURATE_ID_INDEX)
        result["dropped_legacy_accurate_id"] = True
        result["legacy_full_unique_present"] = False
        result["local_create_ready"] = True
    except OperationFailure:
        pass
    return result


async def local_master_create_index_status(db) -> Dict[str, Any]:
    """Readiness check only — no create/drop."""
    col = db[COLLECTION]
    claims = db[CODE_CLAIMS_COLLECTION]
    existing = await col.index_information()
    claim_idx = await claims.index_information()
    legacy = existing.get(LEGACY_ACCURATE_ID_INDEX) or {}
    legacy_full = bool(
        LEGACY_ACCURATE_ID_INDEX in existing and not legacy.get("partialFilterExpression")
    )
    return {
        "partial_accurate_id": ACCURATE_ID_PARTIAL_INDEX in existing,
        "code_claims": CODE_CLAIMS_INDEX in claim_idx,
        "legacy_full_unique_present": legacy_full,
        "local_create_ready": (
            ACCURATE_ID_PARTIAL_INDEX in existing
            and CODE_CLAIMS_INDEX in claim_idx
            and not legacy_full
        ),
    }


async def _code_taken_in_masters(db, code_key: str, *, session=None) -> bool:
    if not code_key:
        return False
    kwargs = {"session": session} if session is not None else {}
    try:
        docs = await db[COLLECTION].find(
            {"item_code": {"$exists": True, "$ne": ""}},
            {"_id": 0, "item_code": 1},
            **kwargs,
        ).to_list(20000)
    except TypeError:
        # Fake / sync-style collections may not accept session=.
        docs = await db[COLLECTION].find(
            {"item_code": {"$exists": True, "$ne": ""}},
            {"_id": 0, "item_code": 1},
        ).to_list(20000)
    for row in docs:
        if normalize_item_code_key(row.get("item_code")) == code_key:
            return True
    return False


async def create_local_master_item(db, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create ERP-local master metadata only. No stock / HPP / Accurate ID."""
    code_raw = str((payload or {}).get("item_code") or "").strip()
    name = str((payload or {}).get("item_name") or "").strip()
    item_type = str((payload or {}).get("item_type") or "").strip().upper()
    unit = str((payload or {}).get("unit") or "").strip()
    is_active = (payload or {}).get("is_active", True)
    if isinstance(is_active, str):
        is_active = is_active.strip().lower() not in ("0", "false", "no", "nonaktif", "inactive")
    else:
        is_active = bool(is_active)
    production_process = normalize_production_process((payload or {}).get("production_process"))
    if (payload or {}).get("production_process") not in (None, "") and production_process is None:
        raise HTTPException(400, "Default proses harus 'print' (Cetak) atau 'non_print' (Tidak cetak).")

    if not code_raw:
        raise HTTPException(400, "Kode barang/jasa wajib diisi.")
    if not name:
        raise HTTPException(400, "Nama barang/jasa wajib diisi.")
    if item_type not in ALLOWED_LOCAL_ITEM_TYPES:
        raise HTTPException(400, "Jenis barang/jasa tidak valid.")
    if not unit:
        raise HTTPException(400, "Satuan wajib diisi.")
    if "accurate_item_id" in (payload or {}) and str((payload or {}).get("accurate_item_id") or "").strip():
        raise HTTPException(400, "Accurate ID tidak diisi pada create lokal.")

    code_key = normalize_item_code_key(code_raw)
    new_id = str(uuid.uuid4())
    now = now_iso()
    claim = {
        "code_key": code_key,
        "master_item_id": new_id,
        "item_code": code_raw,
        "created_at": now,
    }
    doc = {
        "id": new_id,
        "item_code": code_raw,
        "item_name": name,
        "item_type": item_type,
        "unit": unit,
        "is_active": is_active,
        "origin": ORIGIN_LOCAL,
        "created_at": now,
        "updated_at": now,
    }
    if production_process:
        doc["production_process"] = production_process

    if await _code_taken_in_masters(db, code_key):
        raise HTTPException(409, DUPLICATE_CODE_MSG)

    claimed = False
    try:
        await db[CODE_CLAIMS_COLLECTION].insert_one(claim)
        claimed = True
    except DuplicateKeyError as exc:
        raise HTTPException(409, DUPLICATE_CODE_MSG) from exc

    # Re-check after claim to close the race with Accurate sync inserting same code.
    if await _code_taken_in_masters(db, code_key):
        await db[CODE_CLAIMS_COLLECTION].delete_one({"code_key": code_key, "master_item_id": new_id})
        raise HTTPException(409, DUPLICATE_CODE_MSG)

    try:
        await db[COLLECTION].insert_one(doc)
    except Exception:
        if claimed:
            await db[CODE_CLAIMS_COLLECTION].delete_one({"code_key": code_key, "master_item_id": new_id})
        raise

    fresh = await db[COLLECTION].find_one({"id": new_id}, {"_id": 0})
    return public_master_item(fresh)


async def upsert_master_item(db, incoming: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Upsert one Master Item keyed by accurate_item_id.

    Returns (public_doc, action) where action is 'created' | 'updated' | 'unchanged'.
    Never matches or updates origin=local rows (they omit accurate_item_id).
    Never overwrites ERP-owned production_process from Accurate payloads.
    """
    if incoming.get("accurate_item_id") is None and incoming.get("id") is not None:
        incoming = normalize_accurate_item(incoming) or {}
    aid = str(incoming.get("accurate_item_id") or "").strip()
    if not aid:
        raise ValueError("accurate_item_id wajib")
    owned = _owned_payload(incoming)
    for erp_key in ERP_OWNED_FIELDS:
        owned.pop(erp_key, None)
    now = now_iso()
    col = db[COLLECTION]
    existing = await col.find_one({"accurate_item_id": aid}, {"_id": 0})
    if existing:
        if resolve_master_origin(existing) == ORIGIN_LOCAL:
            # Should not happen (local omits Accurate ID). Refuse auto-merge.
            raise ValueError("Konflik identitas: Accurate ID menunjuk barang lokal")
        changed = _fields_changed(existing, owned)
        update = {
            "last_synced_at": now,
            "updated_at": now,
            "origin": ORIGIN_ACCURATE,
            **owned,
        }
        await col.update_one({"accurate_item_id": aid}, {"$set": update})
        fresh = await col.find_one({"accurate_item_id": aid}, {"_id": 0})
        return public_master_item(fresh), ("updated" if changed else "unchanged")

    new_id = str(uuid.uuid4())
    doc = {
        "id": new_id,
        "accurate_item_id": aid,
        "origin": ORIGIN_ACCURATE,
        **{k: owned.get(k) for k in ACCURATE_OWNED_FIELDS},
        "last_synced_at": now,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await col.update_one(
            {"accurate_item_id": aid},
            {"$setOnInsert": doc},
            upsert=True,
        )
    except DuplicateKeyError:
        # Concurrent insert of the same Accurate ID — retry as update.
        return await upsert_master_item(db, incoming)
    fresh = await col.find_one({"accurate_item_id": aid}, {"_id": 0})
    # If another worker inserted first, treat as update/unchanged of that row.
    if fresh and fresh.get("id") != new_id:
        if resolve_master_origin(fresh) == ORIGIN_LOCAL:
            raise ValueError("Konflik identitas: Accurate ID menunjuk barang lokal")
        changed = _fields_changed(fresh, owned)
        await col.update_one(
            {"accurate_item_id": aid},
            {"$set": {"last_synced_at": now, "updated_at": now, "origin": ORIGIN_ACCURATE, **owned}},
        )
        fresh = await col.find_one({"accurate_item_id": aid}, {"_id": 0})
        return public_master_item(fresh), ("updated" if changed else "unchanged")
    return public_master_item(fresh), "created"


async def update_master_production_process(
    db,
    item_id: str,
    production_process: Any,
    *,
    user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Set ERP default Cetak/Tidak cetak. Does not mutate orders or Accurate fields."""
    item_id = str(item_id or "").strip()
    if not item_id:
        raise HTTPException(400, "ID barang/jasa wajib.")
    proc = normalize_production_process(production_process)
    if proc is None:
        raise HTTPException(400, "Default proses harus 'print' (Cetak) atau 'non_print' (Tidak cetak).")
    existing = await db[COLLECTION].find_one(
        {"$or": [{"id": item_id}, {"accurate_item_id": item_id}]},
        {"_id": 0},
    )
    if not existing:
        raise HTTPException(404, "Barang/jasa tidak ditemukan.")
    now = now_iso()
    actor = ""
    if isinstance(user, dict):
        actor = str(user.get("email") or user.get("id") or "").strip()
    await db[COLLECTION].update_one(
        {"id": existing["id"]},
        {"$set": {
            "production_process": proc,
            "production_process_updated_at": now,
            "production_process_updated_by": actor or None,
            "updated_at": now,
        }},
    )
    fresh = await db[COLLECTION].find_one({"id": existing["id"]}, {"_id": 0})
    return public_master_item(fresh)


async def resolve_line_production_process(
    db,
    *,
    master_item_id: Optional[str] = None,
    accurate_item_id: Optional[str] = None,
    explicit: Any = None,
) -> str:
    """Resolve production_process for a new order line.

    Explicit print|non_print from the transaction wins. Otherwise master default,
    then legacy fallback print. Never mutates master or existing orders.
    """
    proc = normalize_production_process(explicit)
    if proc:
        return proc
    mid = str(master_item_id or "").strip()
    aid = str(accurate_item_id or "").strip()
    master = None
    if mid:
        master = await db[COLLECTION].find_one({"id": mid}, {"_id": 0, "production_process": 1})
    if master is None and aid:
        master = await db[COLLECTION].find_one(
            {"accurate_item_id": aid},
            {"_id": 0, "production_process": 1},
        )
    from_master = normalize_production_process((master or {}).get("production_process"))
    return from_master or "print"


async def sync_master_items(db, client: AccurateClient) -> Dict[str, Any]:
    """Pull Accurate items and upsert. Never truncates. Idempotent by accurate_item_id.

    Local masters (origin=local / no accurate_item_id) are never overwritten or name-linked.
    """
    if _sync_lock.locked():
        raise SyncInProgressError("Sinkronisasi Barang/Jasa sedang berjalan")
    async with _sync_lock:
        rows = await client.fetch_all_items()
        created = updated = unchanged = failed = 0
        for row in rows:
            try:
                if not row or not row.get("accurate_item_id"):
                    failed += 1
                    continue
                _doc, action = await upsert_master_item(db, row)
                if action == "created":
                    created += 1
                elif action == "updated":
                    updated += 1
                else:
                    unchanged += 1
            except Exception:
                failed += 1
        last = await db[COLLECTION].find(
            {"last_synced_at": {"$exists": True}},
            {"_id": 0, "last_synced_at": 1},
        ).sort("last_synced_at", -1).limit(1).to_list(1)
        last_synced_at = (last[0].get("last_synced_at") if last else None)
        total = await db[COLLECTION].count_documents({})
        return {
            "status": "ok",
            "mode": "live" if client.is_live else "mock",
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "failed": failed,
            "total": total,
            "last_synced_at": last_synced_at,
            "message": (
                f"Baru: {created} · Diperbarui: {updated} · "
                f"Tidak Berubah: {unchanged} · Gagal: {failed}"
            ),
        }


async def lookup_masters_by_accurate_ids(db, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    clean = [str(i).strip() for i in ids if i is not None and str(i).strip()]
    if not clean:
        return {}
    docs = await db[COLLECTION].find(
        {"accurate_item_id": {"$in": clean}},
        {"_id": 0},
    ).to_list(len(clean) + 10)
    return {d["accurate_item_id"]: d for d in docs if d.get("accurate_item_id")}


async def resolve_po_line_items(
    db,
    client: AccurateClient,
    line_items: List[Dict[str, Any]],
    *,
    require_master: bool,
) -> List[Dict[str, Any]]:
    """Bind Accurate PO lines to Master Items by accurate_item_id.

    Targeted item/detail.do fetch is used for IDs missing from Master.
    Never matches by name. Historical / file imports pass require_master=False.
    """
    items = list(line_items or [])
    if not require_master:
        return items

    missing_id = [li for li in items if not str(li.get("accurate_item_id") or "").strip()]
    if missing_id:
        raise MasterItemUnresolvedError(UNKNOWN_ITEM_MSG)

    ids = [str(li.get("accurate_item_id")).strip() for li in items]
    masters = await lookup_masters_by_accurate_ids(db, ids)
    missing = [i for i in ids if i not in masters]
    # Targeted fetch is supported (same AccurateClient, item/detail.do).
    for aid in missing:
        try:
            fetched = await client.fetch_item_detail(aid)
        except Exception as e:
            raise MasterItemUnresolvedError(UNKNOWN_ITEM_MSG) from e
        if not fetched or not fetched.get("accurate_item_id"):
            raise MasterItemUnresolvedError(UNKNOWN_ITEM_MSG)
        doc, _action = await upsert_master_item(db, fetched)
        masters[aid] = {
            "id": doc["id"],
            "accurate_item_id": doc["accurate_item_id"],
            "item_code": doc.get("item_code"),
            "item_name": doc.get("item_name"),
        }

    out: List[Dict[str, Any]] = []
    for li in items:
        row = dict(li)
        aid = str(li.get("accurate_item_id")).strip()
        master = masters.get(aid) or {}
        row["accurate_item_id"] = aid
        row["master_item_id"] = master.get("id")
        if not row.get("name"):
            row["name"] = master.get("item_name") or row.get("name")
        if not row.get("item_code"):
            row["item_code"] = master.get("item_code") or row.get("item_code")
        out.append(row)
    return out


def build_list_query(
    q: str = "",
    item_type: str = "",
    status: str = "",
) -> Dict[str, Any]:
    filt: Dict[str, Any] = {}
    clauses: List[Dict[str, Any]] = []
    needle = (q or "").strip()
    if needle:
        rx = re.escape(needle)
        clauses.append({"$or": [
            {"item_code": {"$regex": rx, "$options": "i"}},
            {"item_name": {"$regex": rx, "$options": "i"}},
            {"accurate_item_id": {"$regex": rx, "$options": "i"}},
        ]})
    itype = (item_type or "").strip()
    if itype and itype not in ("all", "*"):
        clauses.append({"item_type": itype})
    st = (status or "").strip().lower()
    if st == "active":
        clauses.append({"is_active": True})
    elif st in ("inactive", "nonaktif"):
        clauses.append({"is_active": False})
    if len(clauses) == 1:
        filt = clauses[0]
    elif clauses:
        filt = {"$and": clauses}
    return filt


PICKER_PAGE_SIZE_MAX = 40


async def list_master_items_picker(
    db,
    *,
    q: str = "",
    status: str = "",
    page_size: int = 40,
) -> Dict[str, Any]:
    """Autocomplete candidates only. One find+limit. No summary/distinct/counts."""
    page_size = max(1, min(int(page_size or 40), PICKER_PAGE_SIZE_MAX))
    filt = build_list_query(q, "", status)
    col = db[COLLECTION]
    docs = await col.find(filt, {"_id": 0}).sort("item_name", 1).limit(page_size).to_list(page_size)
    return {
        "items": [public_master_item(d) for d in docs],
    }


async def list_master_items(
    db,
    *,
    q: str = "",
    item_type: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    filt = build_list_query(q, item_type, status)
    col = db[COLLECTION]
    total = await col.count_documents(filt)
    skip = (page - 1) * page_size
    docs = await col.find(filt, {"_id": 0}).sort("item_name", 1).skip(skip).limit(page_size).to_list(page_size)
    all_total = await col.count_documents({})
    active = await col.count_documents({"is_active": True})
    inactive = await col.count_documents({"is_active": False})
    known_status = await col.count_documents({"is_active": {"$in": [True, False]}})
    last = await col.find({}, {"_id": 0, "last_synced_at": 1}).sort("last_synced_at", -1).limit(1).to_list(1)
    types_raw = await col.distinct("item_type")
    item_types = sorted([t for t in types_raw if t])
    return {
        "items": [public_master_item(d) for d in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": max(1, (total + page_size - 1) // page_size) if total else 1,
        "summary": {
            "total": all_total,
            "active": active if known_status else None,
            "inactive": inactive if known_status else None,
            "last_synced_at": last[0].get("last_synced_at") if last else None,
            "has_active_status": bool(known_status),
        },
        "item_types": item_types,
    }
