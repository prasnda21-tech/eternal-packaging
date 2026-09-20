"""Chart of Accounts (Akun Perkiraan) — master ledger accounts.

Persists code, name, and Accurate account_type (classification only).
Balance / parent / journal fields belong to later phases.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pymongo.errors import DuplicateKeyError

from services.account_type_reporting import (
    apply_derived_reporting_fields,
    validate_account_type_reporting_map,
)
from services.accounting_audit import (
    ENTITY_COA,
    EVENT_COA_CREATED,
    EVENT_COA_DELETED,
    EVENT_COA_RENAMED,
    EVENT_COA_TYPE_CHANGED,
    record_accounting_event,
)

COLLECTION = "ledger_accounts"
PERM_VIEW = "ledger.accounts.view"
PERM_MANAGE = "ledger.accounts.manage"

# Accurate Online account type codes (static catalog — not a CRUD master).
ACCOUNT_TYPES = (
    "BANK",
    "EXPS",
    "OEXP",
    "REVE",
    "OCAS",
    "DEPR",
    "FASS",
    "OCLY",
    "EQTY",
    "AREC",
    "INTR",
    "OINC",
    "APAY",
    "COGS",
    "LTLY",
    "OASS",
)
ACCOUNT_TYPE_SET = frozenset(ACCOUNT_TYPES)
validate_account_type_reporting_map(ACCOUNT_TYPES)

# Future journal / accounting collections that may reference an account.
# Checked on delete only — never cascade.
_REFERENCE_SPECS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("journal_entries", ("account_id", "lines.account_id")),
    ("journal_lines", ("account_id",)),
    ("ledger_transactions", ("account_id",)),
    ("accounting_transactions", ("account_id", "lines.account_id")),
    ("cash_bank_accounts", ("account_id",)),
    ("cash_bank_receipts", ("cash_bank_account_id", "counter_account_id")),
    ("cash_bank_payments", ("cash_bank_account_id", "counter_account_id")),
    ("cash_bank_transfers", ("source_account_id", "dest_account_id")),
    ("general_journal_vouchers", ("lines.account_id",)),
    ("opening_balance_vouchers", ("lines.account_id",)),
)

SEED_JSON = Path(__file__).resolve().parents[1] / "data" / "chart_of_accounts_seed.json"


class AccountValidationError(ValueError):
    """Invalid code/name/type payload."""


class AccountConflictError(Exception):
    """Duplicate account code."""


class AccountInUseError(Exception):
    """Account is referenced by journal/accounting data."""


TYPE_LOCKED_MESSAGE = "Tipe akun terkunci karena akun sudah digunakan."
DELETE_IN_USE_MESSAGE = "Akun tidak dapat dihapus karena sudah dipakai di jurnal/transaksi akuntansi"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_code(raw: Any) -> str:
    """Persist account codes as trimmed strings (never numeric)."""
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return str(raw).strip()
    if isinstance(raw, int):
        return str(raw).strip()
    if isinstance(raw, float):
        if raw == int(raw):
            return str(int(raw))
        return str(raw).strip()
    return str(raw).strip()


def normalize_name(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw).strip()


def normalize_account_type(raw: Any) -> Optional[str]:
    """Return canonical Accurate type code, empty string if blank, or None if invalid."""
    if raw is None:
        return ""
    s = str(raw).strip().upper()
    if not s:
        return ""
    if s not in ACCOUNT_TYPE_SET:
        return None
    return s


def actor_label(user: Optional[dict]) -> str:
    if not user:
        return ""
    return str(user.get("email") or user.get("name") or user.get("id") or "").strip()


def public_account(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    raw_type = doc.get("account_type")
    account_type = None
    if raw_type is not None and str(raw_type).strip():
        normalized = normalize_account_type(raw_type)
        account_type = normalized if normalized else str(raw_type).strip().upper()
    return apply_derived_reporting_fields({
        "id": doc.get("id"),
        "code": normalize_code(doc.get("code")),
        "name": normalize_name(doc.get("name")),
        "account_type": account_type,
        "account_type_locked": False,
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    })


def validate_payload(raw: dict, *, require_account_type: bool = True) -> Tuple[str, str, Optional[str]]:
    code = normalize_code((raw or {}).get("code"))
    name = normalize_name((raw or {}).get("name"))
    if not code:
        raise AccountValidationError("Kode Perkiraan wajib diisi")
    if not name:
        raise AccountValidationError("Nama Akun wajib diisi")

    has_type_key = "account_type" in (raw or {})
    if require_account_type or has_type_key:
        normalized = normalize_account_type((raw or {}).get("account_type"))
        if normalized is None:
            raise AccountValidationError("Tipe Akun tidak valid")
        if require_account_type and not normalized:
            raise AccountValidationError("Tipe Akun wajib diisi")
        account_type = normalized or None
    else:
        account_type = None
    return code, name, account_type


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("code", unique=True)
    await col.create_index("account_type")


async def list_accounts(db, *, q: Optional[str] = None) -> List[dict]:
    """Read-only list. Never writes."""
    query: Dict[str, Any] = {}
    needle = (q or "").strip()
    if needle:
        query["$or"] = [
            {"code": {"$regex": needle, "$options": "i"}},
            {"name": {"$regex": needle, "$options": "i"}},
            {"account_type": {"$regex": needle, "$options": "i"}},
        ]
    docs = await db[COLLECTION].find(query, {"_id": 0}).sort("code", 1).to_list(5000)
    items = [public_account(d) for d in docs]
    await attach_account_type_lock_flags(db, items)
    return items


async def get_account(db, account_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": str(account_id)}, {"_id": 0})
    if not doc:
        return None
    out = public_account(doc)
    await attach_account_type_lock_flags(db, [out])
    return out


async def create_account(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    code, name, account_type = validate_payload(raw, require_account_type=True)
    existing = await db[COLLECTION].find_one({"code": code}, {"_id": 0, "id": 1})
    if existing:
        raise AccountConflictError(f"Kode Perkiraan '{code}' sudah digunakan")
    now = now_iso()
    actor = actor_label(user)
    doc = {
        "id": str(uuid.uuid4()),
        "code": code,  # string
        "name": name,
        "account_type": account_type,
        "created_at": now,
        "created_by": actor,
        "updated_at": now,
        "updated_by": actor,
    }
    try:
        await db[COLLECTION].insert_one(dict(doc))
    except DuplicateKeyError as exc:
        raise AccountConflictError(f"Kode Perkiraan '{code}' sudah digunakan") from exc
    created = public_account(doc)
    await record_accounting_event(
        db,
        event_type=EVENT_COA_CREATED,
        entity_type=ENTITY_COA,
        entity_id=created["id"],
        actor=user,
        after={"code": created["code"], "name": created["name"], "account_type": created["account_type"]},
    )
    return created


async def update_account(
    db,
    account_id: str,
    raw: dict,
    *,
    user: Optional[dict] = None,
) -> dict:
    """Update name and/or account_type. Code is immutable."""
    aid = str(account_id or "").strip()
    if not aid:
        raise KeyError("missing")
    doc = await db[COLLECTION].find_one({"id": aid}, {"_id": 0})
    if not doc:
        raise KeyError("missing")

    updates: Dict[str, Any] = {}
    if "name" in (raw or {}):
        name = normalize_name((raw or {}).get("name"))
        if not name:
            raise AccountValidationError("Nama Akun wajib diisi")
        updates["name"] = name
    if "account_type" in (raw or {}):
        normalized = normalize_account_type((raw or {}).get("account_type"))
        if normalized is None:
            raise AccountValidationError("Tipe Akun tidak valid")
        if not normalized:
            raise AccountValidationError("Tipe Akun wajib diisi")
        updates["account_type"] = normalized
    if "code" in (raw or {}):
        new_code = normalize_code((raw or {}).get("code"))
        if new_code and new_code != normalize_code(doc.get("code")):
            raise AccountValidationError("Kode Perkiraan tidak dapat diubah")

    if not updates:
        out = public_account(doc)
        await attach_account_type_lock_flags(db, [out])
        return out

    if "account_type" in updates and updates["account_type"] != normalize_account_type(doc.get("account_type")):
        if await account_is_referenced(db, aid, doc.get("code") or ""):
            raise AccountInUseError(TYPE_LOCKED_MESSAGE)

    updates["updated_at"] = now_iso()
    updates["updated_by"] = actor_label(user)
    await db[COLLECTION].update_one({"id": aid}, {"$set": updates})
    refreshed = await db[COLLECTION].find_one({"id": aid}, {"_id": 0})
    out = public_account(refreshed)
    await attach_account_type_lock_flags(db, [out])
    if "name" in updates and updates["name"] != normalize_name(doc.get("name")):
        await record_accounting_event(
            db,
            event_type=EVENT_COA_RENAMED,
            entity_type=ENTITY_COA,
            entity_id=aid,
            actor=user,
            before={"name": normalize_name(doc.get("name"))},
            after={"name": updates["name"]},
        )
    if "account_type" in updates and updates["account_type"] != normalize_account_type(doc.get("account_type")):
        await record_accounting_event(
            db,
            event_type=EVENT_COA_TYPE_CHANGED,
            entity_type=ENTITY_COA,
            entity_id=aid,
            actor=user,
            before={"account_type": normalize_account_type(doc.get("account_type"))},
            after={"account_type": updates["account_type"]},
        )
    return out


async def _mapped_settings_account_ids(db) -> set:
    ids = set()
    sales = await db.app_settings.find_one({"key": "sales_accounting"}, {"_id": 0})
    if sales:
        for field in ("receivable_account_id", "revenue_account_id", "output_tax_account_id"):
            value = str(sales.get(field) or "").strip()
            if value:
                ids.add(value)
    payroll = await db.app_settings.find_one({"key": "payroll_accounting"}, {"_id": 0})
    if payroll:
        payable = str(payroll.get("payroll_payable_account_id") or "").strip()
        if payable:
            ids.add(payable)
        mappings = payroll.get("component_mappings") or {}
        if isinstance(mappings, dict):
            for raw in mappings.values():
                if isinstance(raw, dict):
                    value = str(raw.get("account_id") or "").strip()
                    if value:
                        ids.add(value)
    purchase = await db.app_settings.find_one({"key": "purchase_accounting"}, {"_id": 0})
    if purchase:
        for field in (
            "payable_account_id",
            "purchase_clearing_account_id",
            "inventory_account_id",
            "cogs_account_id",
        ):
            value = str(purchase.get(field) or "").strip()
            if value:
                ids.add(value)
    return ids


async def account_is_referenced(db, account_id: str, code: str) -> bool:
    """True when any journal/accounting doc or settings mapping references this account."""
    aid = str(account_id or "").strip()
    code_s = normalize_code(code)
    if not aid and not code_s:
        return False
    if aid and aid in await _mapped_settings_account_ids(db):
        return True
    for coll_name, fields in _REFERENCE_SPECS:
        coll = db[coll_name]
        clauses = []
        for field in fields:
            if aid:
                clauses.append({field: aid})
            if code_s:
                # tolerate future docs that store code instead of id
                clauses.append({field.replace("account_id", "account_code"): code_s})
                if field.endswith("account_id"):
                    clauses.append({field: code_s})
        if not clauses:
            continue
        try:
            hit = await coll.find_one({"$or": clauses}, {"_id": 1})
        except Exception:
            continue
        if hit:
            return True
    return False


async def attach_account_type_lock_flags(db, items: List[dict]) -> List[dict]:
    rows = [item for item in items if item and item.get("id")]
    if not rows:
        return items
    mapped = await _mapped_settings_account_ids(db)
    locked = {aid for aid in mapped if aid}
    ids = [str(item["id"]) for item in rows]
    for coll_name, fields in _REFERENCE_SPECS:
        remaining = [aid for aid in ids if aid not in locked]
        if not remaining:
            break
        clauses = [{field: {"$in": remaining}} for field in fields]
        try:
            hits = await db[coll_name].find({"$or": clauses}, {"_id": 0, **{field: 1 for field in fields}}).to_list(5000)
        except Exception:
            continue
        for hit in hits:
            for field in fields:
                value = hit.get(field)
                if field == "lines.account_id":
                    continue
                text = str(value or "").strip()
                if text:
                    locked.add(text)
            for line in hit.get("lines") or []:
                text = str((line or {}).get("account_id") or "").strip()
                if text:
                    locked.add(text)
    for item in rows:
        item["account_type_locked"] = str(item.get("id")) in locked
    return items


async def delete_account(db, account_id: str, *, user: Optional[dict] = None) -> dict:
    """Delete exact account by id. Rejects when referenced (409)."""
    aid = str(account_id or "").strip()
    if not aid:
        raise KeyError("missing")
    doc = await db[COLLECTION].find_one({"id": aid}, {"_id": 0})
    if not doc:
        raise KeyError("missing")
    if await account_is_referenced(db, aid, doc.get("code") or ""):
        raise AccountInUseError(DELETE_IN_USE_MESSAGE)
    await db[COLLECTION].delete_one({"id": aid})
    deleted = public_account(doc)
    await record_accounting_event(
        db,
        event_type=EVENT_COA_DELETED,
        entity_type=ENTITY_COA,
        entity_id=aid,
        actor=user,
        before={"code": deleted.get("code"), "name": deleted.get("name"), "account_type": deleted.get("account_type")},
    )
    return deleted


def load_seed_rows(path: Optional[Path] = None) -> List[Dict[str, str]]:
    """Load explicit seed rows: code + name + account_type. Ignore balance/parent fields."""
    src = Path(path) if path else SEED_JSON
    raw = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("seed must be a JSON array")
    out: List[Dict[str, str]] = []
    seen = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        # Explicitly ignore balance / parent / currency / source fields.
        code = normalize_code(row.get("code") or row.get("Kode Perkiraan"))
        name = normalize_name(row.get("name") or row.get("Nama Akun") or row.get("Nama"))
        account_type = normalize_account_type(
            row.get("account_type") or row.get("Tipe Akun")
        )
        if not code or not name:
            continue
        if account_type is None or not account_type:
            raise ValueError(f"seed row '{code}' missing/invalid account_type")
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": name, "account_type": account_type})
    return out


async def seed_accounts(
    db,
    rows: Optional[List[dict]] = None,
    *,
    actor: str = "seed",
) -> Dict[str, int]:
    """Idempotent insert-missing-by-code. Never overwrites or deletes."""
    payload = rows if rows is not None else load_seed_rows()
    inserted = 0
    skipped = 0
    now = now_iso()
    col = db[COLLECTION]
    for row in payload:
        code = normalize_code(row.get("code"))
        name = normalize_name(row.get("name"))
        account_type = normalize_account_type(
            row.get("account_type") or row.get("Tipe Akun")
        )
        if not code or not name:
            skipped += 1
            continue
        if account_type is None or not account_type:
            skipped += 1
            continue
        existing = await col.find_one({"code": code}, {"_id": 1})
        if existing:
            skipped += 1
            continue
        doc = {
            "id": str(uuid.uuid4()),
            "code": code,
            "name": name,
            "account_type": account_type,
            "created_at": now,
            "created_by": actor,
            "updated_at": now,
            "updated_by": actor,
        }
        try:
            await col.insert_one(doc)
            inserted += 1
        except DuplicateKeyError:
            skipped += 1
    return {"inserted": inserted, "skipped": skipped, "total_seed": len(payload)}
