"""Purchase Accounting Settings — Phase 4A AP mappings + Phase 4B.1 inventory foundation.

Persists exact ledger account IDs. Does not post journals, store balances,
or auto-select accounts. GET is zero-write. Missing document is unconfigured.

`configured` remains AP-ready (payable + clearing). Inventory/COGS/effective date
are a separate `inventory_configured` flag and never required for Phase 4A posting.
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
from services.inventory_accounting import parse_business_date

SETTINGS_KEY = "purchase_accounting"
PERM_VIEW = "settings.purchase_accounting.view"
PERM_MANAGE = "settings.purchase_accounting.manage"

ACCOUNT_FIELDS = (
    "payable_account_id",
    "purchase_clearing_account_id",
)
INVENTORY_ACCOUNT_FIELDS = (
    "inventory_account_id",
    "cogs_account_id",
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


def _payload_has_value(payload: Dict[str, Any], key: str) -> bool:
    if key not in payload:
        return False
    raw = payload.get(key)
    return raw is not None and str(raw).strip() != ""


def parse_inventory_effective_date(raw: Any, *, required: bool) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        if required:
            raise HTTPException(
                400,
                "Tanggal Mulai Accounting Persediaan wajib diisi (YYYY-MM-DD).",
            )
        return None
    parsed = parse_business_date(text)
    if not parsed:
        raise HTTPException(
            400,
            "Tanggal Mulai Accounting Persediaan harus tanggal kalender YYYY-MM-DD.",
        )
    return parsed


def _empty_inventory_fields() -> Dict[str, Any]:
    return {
        "inventory_configured": False,
        "inventory_account_id": None,
        "cogs_account_id": None,
        "inventory_accounting_effective_date": None,
    }


def _public_unconfigured(stored: Optional[dict] = None) -> Dict[str, Any]:
    stored = stored or {}
    return {
        "configured": False,
        "payable_account_id": _account_id(stored.get("payable_account_id")),
        "purchase_clearing_account_id": _account_id(stored.get("purchase_clearing_account_id")),
        "accounts": {
            "payable": None,
            "purchase_clearing": None,
            "inventory": None,
            "cogs": None,
        },
        **_empty_inventory_fields(),
        "inventory_account_id": _account_id(stored.get("inventory_account_id")),
        "cogs_account_id": _account_id(stored.get("cogs_account_id")),
        "inventory_accounting_effective_date": parse_business_date(
            stored.get("inventory_accounting_effective_date")
        ),
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


def _validate_inventory(account: Optional[dict]) -> dict:
    if not account:
        raise HTTPException(400, "Akun Persediaan wajib dipilih (COA exact).")
    account_type = str(account.get("account_type") or "").strip().upper()
    if account_type != "INTR":
        raise HTTPException(400, "Akun Persediaan harus bertipe INTR (Persediaan).")
    return account


def _validate_cogs(account: Optional[dict]) -> dict:
    if not account:
        raise HTTPException(400, "Akun HPP / COGS wajib dipilih (COA exact).")
    account_type = str(account.get("account_type") or "").strip().upper()
    if account_type != "COGS":
        raise HTTPException(400, "Akun HPP / COGS harus bertipe COGS (Beban Pokok Penjualan).")
    return account


def _audit_snapshot(doc: Optional[dict]) -> Dict[str, Optional[str]]:
    doc = doc or {}
    return {
        "payable_account_id": _account_id(doc.get("payable_account_id")),
        "purchase_clearing_account_id": _account_id(doc.get("purchase_clearing_account_id")),
        "inventory_account_id": _account_id(doc.get("inventory_account_id")),
        "cogs_account_id": _account_id(doc.get("cogs_account_id")),
        "inventory_accounting_effective_date": parse_business_date(
            doc.get("inventory_accounting_effective_date")
        ),
    }


async def _inventory_public(db, stored: Optional[dict]) -> Dict[str, Any]:
    stored = stored or {}
    inventory_id = _account_id(stored.get("inventory_account_id"))
    cogs_id = _account_id(stored.get("cogs_account_id"))
    effective = parse_business_date(stored.get("inventory_accounting_effective_date"))
    inventory = await _load_account(db, inventory_id)
    cogs = await _load_account(db, cogs_id)
    configured = False
    if inventory_id and cogs_id and effective:
        try:
            _validate_inventory(inventory)
            _validate_cogs(cogs)
            configured = True
        except HTTPException:
            configured = False
    return {
        "inventory_configured": configured,
        "inventory_account_id": inventory_id,
        "cogs_account_id": cogs_id,
        "inventory_accounting_effective_date": effective,
        "inventory": _account_display(inventory) if configured else None,
        "cogs": _account_display(cogs) if configured else None,
    }


async def mappings_configured(db) -> bool:
    settings = await get_purchase_accounting_settings(db)
    return bool(settings.get("configured"))


async def inventory_mappings_configured(db) -> bool:
    settings = await get_purchase_accounting_settings(db)
    return bool(settings.get("inventory_configured"))


async def get_purchase_accounting_settings(db) -> Dict[str, Any]:
    """Zero-write GET. Missing document is an explicit unconfigured state."""
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    if not doc:
        return _public_unconfigured()

    payable_id = _account_id(doc.get("payable_account_id"))
    clearing_id = _account_id(doc.get("purchase_clearing_account_id"))
    inventory_public = await _inventory_public(db, doc)
    if not (payable_id and clearing_id):
        out = _public_unconfigured(doc)
        out.update({
            "inventory_configured": inventory_public["inventory_configured"],
            "inventory_account_id": inventory_public["inventory_account_id"],
            "cogs_account_id": inventory_public["cogs_account_id"],
            "inventory_accounting_effective_date": inventory_public["inventory_accounting_effective_date"],
        })
        out["accounts"]["inventory"] = inventory_public["inventory"]
        out["accounts"]["cogs"] = inventory_public["cogs"]
        return out

    payable = await _load_account(db, payable_id)
    clearing = await _load_account(db, clearing_id)
    try:
        _validate_payable(payable)
        _validate_purchase_clearing(clearing)
    except HTTPException:
        out = _public_unconfigured(doc)
        out.update({
            "inventory_configured": inventory_public["inventory_configured"],
            "inventory_account_id": inventory_public["inventory_account_id"],
            "cogs_account_id": inventory_public["cogs_account_id"],
            "inventory_accounting_effective_date": inventory_public["inventory_accounting_effective_date"],
        })
        out["accounts"]["inventory"] = inventory_public["inventory"]
        out["accounts"]["cogs"] = inventory_public["cogs"]
        return out

    return {
        "configured": True,
        "payable_account_id": payable_id,
        "purchase_clearing_account_id": clearing_id,
        "inventory_configured": inventory_public["inventory_configured"],
        "inventory_account_id": inventory_public["inventory_account_id"],
        "cogs_account_id": inventory_public["cogs_account_id"],
        "inventory_accounting_effective_date": inventory_public["inventory_accounting_effective_date"],
        "accounts": {
            "payable": _account_display(payable),
            "purchase_clearing": _account_display(clearing),
            "inventory": inventory_public["inventory"],
            "cogs": inventory_public["cogs"],
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

    before = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    saved = {
        "key": SETTINGS_KEY,
        "payable_account_id": str(payable["id"]),
        "purchase_clearing_account_id": str(clearing["id"]),
        "updated_at": now_iso(),
        "updated_by": _actor(user),
    }

    inventory_touched = any(
        _payload_has_value(payload, key)
        for key in ("inventory_account_id", "cogs_account_id", "inventory_accounting_effective_date")
    )
    if inventory_touched:
        inventory_id = _account_id(payload.get("inventory_account_id"))
        cogs_id = _account_id(payload.get("cogs_account_id"))
        effective = parse_inventory_effective_date(
            payload.get("inventory_accounting_effective_date"),
            required=True,
        )
        inventory = _validate_inventory(await _load_account(db, inventory_id))
        cogs = _validate_cogs(await _load_account(db, cogs_id))
        ids = {str(payable["id"]), str(clearing["id"]), str(inventory["id"]), str(cogs["id"])}
        if len(ids) != 4:
            raise HTTPException(400, "Akun Hutang, Clearing, Persediaan, dan HPP harus akun yang berbeda.")
        saved["inventory_account_id"] = str(inventory["id"])
        saved["cogs_account_id"] = str(cogs["id"])
        saved["inventory_accounting_effective_date"] = effective

    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    after_doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    await record_accounting_event(
        db,
        event_type=EVENT_PURCHASE_SETTINGS_CHANGED,
        entity_type=ENTITY_PURCHASE_SETTINGS,
        entity_id=SETTINGS_KEY,
        actor=user,
        before=_audit_snapshot(before),
        after=_audit_snapshot(after_doc),
    )
    return await get_purchase_accounting_settings(db)
