"""Chart of Accounts (Akun Perkiraan) — master ledger accounts.

Phase 1 stores code + name only. Balance / type / parent / journal fields
belong to later phases and must not be invented here.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pymongo.errors import DuplicateKeyError

COLLECTION = "ledger_accounts"
PERM_VIEW = "ledger.accounts.view"
PERM_MANAGE = "ledger.accounts.manage"

# Future journal / accounting collections that may reference an account.
# Checked on delete only — never cascade.
_REFERENCE_SPECS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("journal_entries", ("account_id", "lines.account_id")),
    ("journal_lines", ("account_id",)),
    ("ledger_transactions", ("account_id",)),
    ("accounting_transactions", ("account_id", "lines.account_id")),
)

SEED_JSON = Path(__file__).resolve().parents[1] / "data" / "chart_of_accounts_seed.json"


class AccountValidationError(ValueError):
    """Invalid code/name payload."""


class AccountConflictError(Exception):
    """Duplicate account code."""


class AccountInUseError(Exception):
    """Account is referenced by journal/accounting data."""


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


def actor_label(user: Optional[dict]) -> str:
    if not user:
        return ""
    return str(user.get("email") or user.get("name") or user.get("id") or "").strip()


def public_account(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "code": normalize_code(doc.get("code")),
        "name": normalize_name(doc.get("name")),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


def validate_payload(raw: dict) -> Tuple[str, str]:
    code = normalize_code((raw or {}).get("code"))
    name = normalize_name((raw or {}).get("name"))
    if not code:
        raise AccountValidationError("Kode Perkiraan wajib diisi")
    if not name:
        raise AccountValidationError("Nama Akun wajib diisi")
    return code, name


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("code", unique=True)


async def list_accounts(db, *, q: Optional[str] = None) -> List[dict]:
    """Read-only list. Never writes."""
    query: Dict[str, Any] = {}
    needle = (q or "").strip()
    if needle:
        query["$or"] = [
            {"code": {"$regex": needle, "$options": "i"}},
            {"name": {"$regex": needle, "$options": "i"}},
        ]
    docs = await db[COLLECTION].find(query, {"_id": 0}).sort("code", 1).to_list(5000)
    return [public_account(d) for d in docs]


async def get_account(db, account_id: str) -> Optional[dict]:
    doc = await db[COLLECTION].find_one({"id": str(account_id)}, {"_id": 0})
    return public_account(doc) if doc else None


async def create_account(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    code, name = validate_payload(raw)
    existing = await db[COLLECTION].find_one({"code": code}, {"_id": 0, "id": 1})
    if existing:
        raise AccountConflictError(f"Kode Perkiraan '{code}' sudah digunakan")
    now = now_iso()
    actor = actor_label(user)
    doc = {
        "id": str(uuid.uuid4()),
        "code": code,  # string
        "name": name,
        "created_at": now,
        "created_by": actor,
        "updated_at": now,
        "updated_by": actor,
    }
    try:
        await db[COLLECTION].insert_one(dict(doc))
    except DuplicateKeyError as exc:
        raise AccountConflictError(f"Kode Perkiraan '{code}' sudah digunakan") from exc
    return public_account(doc)


async def account_is_referenced(db, account_id: str, code: str) -> bool:
    """True when any future journal/accounting doc references this account."""
    aid = str(account_id or "").strip()
    code_s = normalize_code(code)
    if not aid and not code_s:
        return False
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


async def delete_account(db, account_id: str) -> dict:
    """Delete exact account by id. Rejects when referenced (409)."""
    aid = str(account_id or "").strip()
    if not aid:
        raise KeyError("missing")
    doc = await db[COLLECTION].find_one({"id": aid}, {"_id": 0})
    if not doc:
        raise KeyError("missing")
    if await account_is_referenced(db, aid, doc.get("code") or ""):
        raise AccountInUseError(
            "Akun tidak dapat dihapus karena sudah dipakai di jurnal/transaksi akuntansi"
        )
    await db[COLLECTION].delete_one({"id": aid})
    return public_account(doc)


def load_seed_rows(path: Optional[Path] = None) -> List[Dict[str, str]]:
    """Load explicit seed rows. Only code + name; ignore balance/source fields."""
    src = Path(path) if path else SEED_JSON
    raw = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("seed must be a JSON array")
    out: List[Dict[str, str]] = []
    seen = set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        # Explicitly ignore balance / type / parent / currency / source fields.
        code = normalize_code(row.get("code") or row.get("Kode Perkiraan"))
        name = normalize_name(row.get("name") or row.get("Nama Akun") or row.get("Nama"))
        if not code or not name:
            continue
        if code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": name})
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
        if not code or not name:
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
