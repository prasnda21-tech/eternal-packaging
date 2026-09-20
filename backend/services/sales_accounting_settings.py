"""Sales Accounting Settings — Phase 0 COA mappings only.

Persists exact ledger account IDs. Does not post journals, store balances,
or auto-select accounts. GET is zero-write.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from services.account_type_reporting import reporting_metadata
from services.accounting_audit import (
    ENTITY_SALES_SETTINGS,
    EVENT_SALES_SETTINGS_CHANGED,
    record_accounting_event,
)

SETTINGS_KEY = "sales_accounting"
PERM_VIEW = "settings.sales_accounting.view"
PERM_MANAGE = "settings.sales_accounting.manage"

DISCOUNT_TREATMENT = "NET_TO_REVENUE"
FUTURE_REVENUE_BASIS = "after_discount"
FUTURE_TAX_BASIS = "ppn_amount"

ACCOUNT_FIELDS = (
    "receivable_account_id",
    "revenue_account_id",
    "output_tax_account_id",
)


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
        "receivable_account_id": _account_id(stored.get("receivable_account_id")),
        "revenue_account_id": _account_id(stored.get("revenue_account_id")),
        "output_tax_account_id": _account_id(stored.get("output_tax_account_id")),
        "discount_treatment": DISCOUNT_TREATMENT,
        "future_revenue_basis": FUTURE_REVENUE_BASIS,
        "future_tax_basis": FUTURE_TAX_BASIS,
        "accounts": {
            "receivable": None,
            "revenue": None,
            "output_tax": None,
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


def _validate_receivable(account: Optional[dict]) -> dict:
    if not account:
        raise HTTPException(400, "Akun Piutang Usaha wajib dipilih (COA exact).")
    account_type = str(account.get("account_type") or "").strip().upper()
    if account_type != "AREC":
        raise HTTPException(400, "Akun Piutang Usaha harus bertipe AREC (Piutang Usaha).")
    return account


def _validate_revenue(account: Optional[dict]) -> dict:
    if not account:
        raise HTTPException(400, "Akun Pendapatan Penjualan wajib dipilih (COA exact).")
    account_type = str(account.get("account_type") or "").strip().upper()
    if account_type != "REVE":
        raise HTTPException(400, "Akun Pendapatan Penjualan harus bertipe REVE (Pendapatan).")
    return account


def _validate_output_tax(account: Optional[dict]) -> dict:
    if not account:
        raise HTTPException(400, "Akun PPN Keluaran wajib dipilih (COA exact).")
    account_type = str(account.get("account_type") or "").strip().upper()
    meta = reporting_metadata(account_type)
    if not meta:
        raise HTTPException(
            400,
            "Akun PPN Keluaran harus akun liabilitas Neraca pada Reporting Map.",
        )
    if meta["statement"] != "BALANCE_SHEET" or meta["report_group"] != "liability":
        raise HTTPException(
            400,
            "Akun PPN Keluaran harus akun liabilitas Neraca (Reporting Map: BALANCE_SHEET / liability).",
        )
    return account


async def mappings_configured(db) -> bool:
    settings = await get_sales_accounting_settings(db)
    return bool(settings.get("configured"))


async def get_sales_accounting_settings(db) -> Dict[str, Any]:
    """Zero-write GET. Missing document is an explicit unconfigured state."""
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    if not doc:
        return _public_unconfigured()

    receivable_id = _account_id(doc.get("receivable_account_id"))
    revenue_id = _account_id(doc.get("revenue_account_id"))
    output_tax_id = _account_id(doc.get("output_tax_account_id"))
    if not (receivable_id and revenue_id and output_tax_id):
        return _public_unconfigured(doc)

    receivable = await _load_account(db, receivable_id)
    revenue = await _load_account(db, revenue_id)
    output_tax = await _load_account(db, output_tax_id)
    try:
        _validate_receivable(receivable)
        _validate_revenue(revenue)
        _validate_output_tax(output_tax)
    except HTTPException:
        # Stored IDs are no longer valid; do not rewrite. Report unconfigured.
        return _public_unconfigured(doc)

    return {
        "configured": True,
        "receivable_account_id": receivable_id,
        "revenue_account_id": revenue_id,
        "output_tax_account_id": output_tax_id,
        "discount_treatment": DISCOUNT_TREATMENT,
        "future_revenue_basis": FUTURE_REVENUE_BASIS,
        "future_tax_basis": FUTURE_TAX_BASIS,
        "accounts": {
            "receivable": _account_display(receivable),
            "revenue": _account_display(revenue),
            "output_tax": _account_display(output_tax),
        },
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by") or None,
    }


async def update_sales_accounting_settings(db, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    receivable_id = _account_id(payload.get("receivable_account_id"))
    revenue_id = _account_id(payload.get("revenue_account_id"))
    output_tax_id = _account_id(payload.get("output_tax_account_id"))

    receivable = _validate_receivable(await _load_account(db, receivable_id))
    revenue = _validate_revenue(await _load_account(db, revenue_id))
    output_tax = _validate_output_tax(await _load_account(db, output_tax_id))

    saved = {
        "key": SETTINGS_KEY,
        "receivable_account_id": str(receivable["id"]),
        "revenue_account_id": str(revenue["id"]),
        "output_tax_account_id": str(output_tax["id"]),
        "updated_at": now_iso(),
        "updated_by": _actor(user),
    }
    before = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    await record_accounting_event(
        db,
        event_type=EVENT_SALES_SETTINGS_CHANGED,
        entity_type=ENTITY_SALES_SETTINGS,
        entity_id=SETTINGS_KEY,
        actor=user,
        before={
            "receivable_account_id": (before or {}).get("receivable_account_id"),
            "revenue_account_id": (before or {}).get("revenue_account_id"),
            "output_tax_account_id": (before or {}).get("output_tax_account_id"),
        },
        after={
            "receivable_account_id": saved["receivable_account_id"],
            "revenue_account_id": saved["revenue_account_id"],
            "output_tax_account_id": saved["output_tax_account_id"],
        },
    )
    return await get_sales_accounting_settings(db)
