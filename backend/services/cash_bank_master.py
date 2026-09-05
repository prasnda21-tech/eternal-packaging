"""Kas & Bank master accounts — 1 cash/bank ↔ exact 1 COA."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from pymongo.errors import DuplicateKeyError

from services.chart_of_accounts import get_account
from services.purchase_common import actor_snapshot, now_iso

COLLECTION = "cash_bank_accounts"

PERM_VIEW = "cash_bank.view"
PERM_MANAGE = "cash_bank.manage"
PERM_POST = "cash_bank.post"
PERM_REVERSE = "cash_bank.reverse"
PERM_RECONCILE = "cash_bank.reconcile"


class CashBankValidationError(ValueError):
    pass


class CashBankConflictError(Exception):
    pass


BANK_ACCOUNT_TYPE = "BANK"


def require_bank_coa(coa: Optional[dict]) -> dict:
    """Kas/Bank may only link to a COA with account_type == BANK.

    Unknown / missing COA and missing/null account_type are rejected.
    Legacy masters already linked to non-BANK remain readable via get/list.
    """
    if not coa:
        raise CashBankValidationError("Akun Perkiraan tidak ditemukan")
    raw_type = coa.get("account_type")
    account_type = str(raw_type).strip().upper() if raw_type is not None else ""
    if not account_type:
        raise CashBankValidationError(
            "Akun Perkiraan harus bertipe BANK (tipe akun kosong)"
        )
    if account_type != BANK_ACCOUNT_TYPE:
        raise CashBankValidationError(
            f"Akun Perkiraan harus bertipe BANK (saat ini: {account_type})"
        )
    return coa


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("account_id", unique=True)
    await col.create_index("name")
    await col.create_index("active")


def public_account(doc: Optional[dict], *, coa: Optional[dict] = None) -> Optional[dict]:
    if not doc:
        return None
    out = {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "account_id": doc.get("account_id"),
        "description": doc.get("description") or "",
        "active": bool(doc.get("active", True)),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }
    if coa:
        out["account"] = {
            "id": coa.get("id"),
            "code": coa.get("code"),
            "name": coa.get("name"),
        }
    elif doc.get("account_code") or doc.get("account_name"):
        out["account"] = {
            "id": doc.get("account_id"),
            "code": doc.get("account_code"),
            "name": doc.get("account_name"),
        }
    return out


async def enrich_account(db, doc: dict) -> dict:
    coa = await get_account(db, doc.get("account_id") or "")
    return public_account(doc, coa=coa) or public_account(doc)


async def list_cash_banks(
    db,
    *,
    q: Optional[str] = None,
    active_only: bool = False,
) -> List[dict]:
    query: Dict[str, Any] = {}
    if active_only:
        query["active"] = True
    needle = (q or "").strip()
    if needle:
        query["$or"] = [
            {"name": {"$regex": needle, "$options": "i"}},
            {"description": {"$regex": needle, "$options": "i"}},
            {"account_code": {"$regex": needle, "$options": "i"}},
            {"account_name": {"$regex": needle, "$options": "i"}},
        ]
    docs = await db[COLLECTION].find(query, {"_id": 0}).sort("name", 1).to_list(2000)
    out = []
    for d in docs:
        out.append(await enrich_account(db, d))
    return out


async def get_cash_bank(db, cash_bank_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": str(cash_bank_id)}, {"_id": 0})
    if not doc:
        return None
    return await enrich_account(db, doc)


async def get_cash_bank_raw(db, cash_bank_id: str) -> Optional[dict]:
    return await db[COLLECTION].find_one({"id": str(cash_bank_id)}, {"_id": 0})


async def create_cash_bank(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    name = str((raw or {}).get("name") or "").strip()
    account_id = str((raw or {}).get("account_id") or "").strip()
    description = str((raw or {}).get("description") or "").strip()
    active = (raw or {}).get("active", True)
    if isinstance(active, str):
        active = active.strip().lower() not in ("0", "false", "no", "nonaktif")
    else:
        active = bool(active)

    if not name:
        raise CashBankValidationError("Nama Kas / Bank wajib diisi")
    if not account_id:
        raise CashBankValidationError("Akun Perkiraan wajib diisi")

    coa = require_bank_coa(await get_account(db, account_id))

    existing = await db[COLLECTION].find_one({"account_id": account_id}, {"_id": 0, "id": 1, "name": 1})
    if existing:
        raise CashBankConflictError(
            f"Akun Perkiraan sudah dipakai oleh Kas/Bank '{existing.get('name')}'"
        )

    now = now_iso()
    actor = actor_snapshot(user)
    doc = {
        "id": str(uuid.uuid4()),
        "name": name,
        "account_id": account_id,
        "account_code": coa.get("code"),
        "account_name": coa.get("name"),
        "description": description,
        "active": active,
        "created_at": now,
        "created_by": actor,
        "updated_at": now,
        "updated_by": actor,
    }
    try:
        await db[COLLECTION].insert_one(dict(doc))
    except DuplicateKeyError as exc:
        raise CashBankConflictError(
            "Akun Perkiraan sudah dipakai oleh Kas/Bank lain"
        ) from exc
    return await enrich_account(db, doc)


async def update_cash_bank(
    db, cash_bank_id: str, raw: dict, *, user: Optional[dict] = None
) -> dict:
    doc = await get_cash_bank_raw(db, cash_bank_id)
    if not doc:
        raise KeyError("missing")

    name = str((raw or {}).get("name") if "name" in (raw or {}) else doc.get("name") or "").strip()
    description = str(
        (raw or {}).get("description") if "description" in (raw or {}) else doc.get("description") or ""
    ).strip()
    if "active" in (raw or {}):
        active = (raw or {}).get("active")
        if isinstance(active, str):
            active = active.strip().lower() not in ("0", "false", "no", "nonaktif")
        else:
            active = bool(active)
    else:
        active = bool(doc.get("active", True))

    updates: Dict[str, Any] = {
        "name": name,
        "description": description,
        "active": active,
    }

    # Account link may change, but only to an unused BANK COA.
    # Leaving the existing link unchanged is always allowed (legacy non-BANK readable/editable).
    if "account_id" in (raw or {}):
        new_aid = str((raw or {}).get("account_id") or "").strip()
        if not new_aid:
            raise CashBankValidationError("Akun Perkiraan wajib diisi")
        if new_aid != doc.get("account_id"):
            coa = require_bank_coa(await get_account(db, new_aid))
            existing = await db[COLLECTION].find_one(
                {"account_id": new_aid}, {"_id": 0, "id": 1, "name": 1}
            )
            if existing and existing.get("id") != doc.get("id"):
                raise CashBankConflictError(
                    f"Akun Perkiraan sudah dipakai oleh Kas/Bank '{existing.get('name')}'"
                )
            updates["account_id"] = new_aid
            updates["account_code"] = coa.get("code")
            updates["account_name"] = coa.get("name")

    if not name:
        raise CashBankValidationError("Nama Kas / Bank wajib diisi")

    now = now_iso()
    actor = actor_snapshot(user)
    updates["updated_at"] = now
    updates["updated_by"] = actor
    try:
        await db[COLLECTION].update_one({"id": doc["id"]}, {"$set": updates})
    except DuplicateKeyError as exc:
        raise CashBankConflictError(
            "Akun Perkiraan sudah dipakai oleh Kas/Bank lain"
        ) from exc
    return await get_cash_bank(db, doc["id"])


async def require_active_cash_bank(db, cash_bank_id: str) -> dict:
    doc = await get_cash_bank_raw(db, cash_bank_id)
    if not doc:
        raise CashBankValidationError("Kas/Bank tidak ditemukan")
    if not doc.get("active", True):
        raise CashBankValidationError("Kas/Bank nonaktif tidak dapat digunakan")
    return doc
