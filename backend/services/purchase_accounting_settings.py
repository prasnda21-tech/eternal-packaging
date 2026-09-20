"""Purchase Accounting Settings — Phase 4A COA mappings only.

Persists exact ledger account IDs. Does not post journals, store balances,
or auto-select accounts. GET is zero-write. Missing document is unconfigured.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from services.accounting_audit import (
    ENTITY_PURCHASE_SETTINGS,
    EVENT_PURCHASE_SETTINGS_CHANGED,
    record_accounting_event,
)

SETTINGS_KEY = "purchase_accounting"
PERM_VIEW = "settings.purchase_accounting.view"
PERM_MANAGE = "settings.purchase_accounting.manage"

ACCOUNT_FIELDS = (
    "payable_account_id",
    "purchase_clearing_account_id",
)

CLEARING_ACCOUNT_TYPES = frozenset({"OCLY", "OASS"})
REJECTED_CLEARING_TYPES = frozenset({"REVE", "EXPS", "OEXP", "COGS", "AREC", "BANK"})


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _actor(user: Optional[dict]) -> Dict[str, str]:
    user = user or {}
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "name": str(user.get("name") or user.get("email") or ""),
        "role": str(user.get("role") or ""),
    }


def _account_id(raw: Any) -> Optional[str]:
    value = str(raw or "").strip()
    return value or None


def _public_unconfigured(stored: Optional[dict] = None) -> Dict[str, Any]:
    stored = stored or {}
    return {
        "configured": False,
        "payable_account_id": _account_id(stored.get("payable_account_id")),
        "purchase_clearing_account_id": _account_id(stored.get("purchase_clearing_account_id")),
        "accounts": {
            "payable": None,
            "purchase_clearing": None,
        },
    }


def _account_display(account: Optional[dict]) -> Optional[Dict[str, str]]:
    if not account:
        return None
    return {
        "id": str(account.get("id") or ""),
        "code": str(account.get("code") or ""),
        "name": str(account.get("name") or ""),
        "account_type": str(account.get("account_type") or "").strip().upper(),
    }


async def _load_account(db, account_id: Optional[str]) -> Optional[dict]:
    if not account_id:
        return None
    return await db.ledger_accounts.find_one({"id": account_id}, {"_id": 0})


def _validate_payable(account: Optional[dict]) -> dict:
    if not account:
        raise HTTPException(400, "Akun Hutang Usaha wajib dipilih (COA exact).")
    account_type = str(account.get("account_type") or "").strip().upper()
    if account_type != "APAY":
        raise HTTPException(400, "Akun Hutang Usaha harus bertipe APAY (Utang Usaha).")
    return account


def _validate_purchase_clearing(account: Optional[dict]) -> dict:
    if not account:
        raise HTTPException(400, "Akun Purchase Clearing / GRNI wajib dipilih (COA exact).")
    account_type = str(account.get("account_type") or "").strip().upper()
    if account_type in REJECTED_CLEARING_TYPES:
        raise HTTPException(
            400,
            "Akun Purchase Clearing / GRNI tidak boleh bertipe pendapatan, beban, HPP, piutang, atau kas/bank.",
        )
    if account_type not in CLEARING_ACCOUNT_TYPES:
        raise HTTPException(
            400,
            "Akun Purchase Clearing / GRNI harus akun Neraca bertipe OCLY atau OASS.",
        )
    return account


async def mappings_configured(db) -> bool:
    settings = await get_purchase_accounting_settings(db)
    return bool(settings.get("configured"))


async def get_purchase_accounting_settings(db) -> Dict[str, Any]:
    """Zero-write GET. Missing document is an explicit unconfigured state."""
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    if not doc:
        return _public_unconfigured()

    payable_id = _account_id(doc.get("payable_account_id"))
    clearing_id = _account_id(doc.get("purchase_clearing_account_id"))
    if not (payable_id and clearing_id):
        return _public_unconfigured(doc)

    payable = await _load_account(db, payable_id)
    clearing = await _load_account(db, clearing_id)
    try:
        _validate_payable(payable)
        _validate_purchase_clearing(clearing)
    except HTTPException:
        return _public_unconfigured(doc)

    return {
        "configured": True,
        "payable_account_id": payable_id,
        "purchase_clearing_account_id": clearing_id,
        "accounts": {
            "payable": _account_display(payable),
            "purchase_clearing": _account_display(clearing),
        },
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by") or None,
    }


async def update_purchase_accounting_settings(db, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    payable_id = _account_id(payload.get("payable_account_id"))
    clearing_id = _account_id(payload.get("purchase_clearing_account_id"))

    payable = _validate_payable(await _load_account(db, payable_id))
    clearing = _validate_purchase_clearing(await _load_account(db, clearing_id))
    if str(payable["id"]) == str(clearing["id"]):
        raise HTTPException(400, "Akun Hutang Usaha dan Purchase Clearing tidak boleh akun yang sama.")

    saved = {
        "key": SETTINGS_KEY,
        "payable_account_id": str(payable["id"]),
        "purchase_clearing_account_id": str(clearing["id"]),
        "updated_at": now_iso(),
        "updated_by": _actor(user),
    }
    before = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    await record_accounting_event(
        db,
        event_type=EVENT_PURCHASE_SETTINGS_CHANGED,
        entity_type=ENTITY_PURCHASE_SETTINGS,
        entity_id=SETTINGS_KEY,
        actor=user,
        before={
            "payable_account_id": (before or {}).get("payable_account_id"),
            "purchase_clearing_account_id": (before or {}).get("purchase_clearing_account_id"),
        },
        after={
            "payable_account_id": saved["payable_account_id"],
            "purchase_clearing_account_id": saved["purchase_clearing_account_id"],
        },
    )
    return await get_purchase_accounting_settings(db)
