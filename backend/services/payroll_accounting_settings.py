"""Payroll Accounting Settings — Phase 0 mappings only.

Persists exact COA IDs and explicit component treatments.
Does not post journals, store balances, or auto-select accounts.
GET is zero-write (no settings create, no component seed, no backfill).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from fastapi import HTTPException

from services.account_type_reporting import reporting_metadata
from services.accounting_audit import (
    ENTITY_PAYROLL_SETTINGS,
    EVENT_PAYROLL_SETTINGS_CHANGED,
    record_accounting_event,
)

SETTINGS_KEY = "payroll_accounting"
PERM_VIEW = "payroll_accounting.view"
PERM_MANAGE = "payroll_accounting.manage"

TREATMENT_EXPENSE_DEBIT = "expense_debit"
TREATMENT_EXPENSE_CREDIT = "expense_credit"
TREATMENT_LIABILITY_CREDIT = "liability_credit"
TREATMENT_ASSET_CREDIT = "asset_credit"

EARNING_TREATMENTS = frozenset({TREATMENT_EXPENSE_DEBIT})
DEDUCTION_TREATMENTS = frozenset({
    TREATMENT_EXPENSE_CREDIT,
    TREATMENT_LIABILITY_CREDIT,
    TREATMENT_ASSET_CREDIT,
})
ALL_TREATMENTS = EARNING_TREATMENTS | DEDUCTION_TREATMENTS

COMPONENTS = "hr_salary_components"
EMPLOYEES = "hr_employees"

SOURCE_SALARY_PROFILE = "salary_profile"
SOURCE_OVERTIME = "overtime"
SOURCE_ATTENDANCE = "attendance"
SOURCE_KASBON = "kasbon"
SOURCE_COMPONENT = "component"
SOURCE_ADJUSTMENT = "adjustment"
SOURCE_OPERATOR_PIECE = "operator_piece"
SOURCE_OPERATOR_BONUS = "operator_bonus"

CODE_BASE = "BASE"
CODE_KASBON = "KASBON"
OT_CODES = ("OT_WEEKDAY", "OT_WEEKEND", "OT_HOLIDAY")
ATT_CODES = ("ATT_LATE", "ATT_ALPHA", "ATT_UNPAID_LEAVE", "ATT_UNPAID_SICK")

UUID_RX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
CODE_RX = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")

SYSTEM_CATALOG: Tuple[Dict[str, str], ...] = (
    {
        "source_key": "salary_profile:BASE",
        "source": SOURCE_SALARY_PROFILE,
        "code": CODE_BASE,
        "kind": "earning",
        "label": "Gaji Pokok",
        "system": True,
    },
    {
        "source_key": "overtime:OT_WEEKDAY",
        "source": SOURCE_OVERTIME,
        "code": "OT_WEEKDAY",
        "kind": "earning",
        "label": "Lembur hari kerja",
        "system": True,
    },
    {
        "source_key": "overtime:OT_WEEKEND",
        "source": SOURCE_OVERTIME,
        "code": "OT_WEEKEND",
        "kind": "earning",
        "label": "Lembur akhir pekan",
        "system": True,
    },
    {
        "source_key": "overtime:OT_HOLIDAY",
        "source": SOURCE_OVERTIME,
        "code": "OT_HOLIDAY",
        "kind": "earning",
        "label": "Lembur hari libur",
        "system": True,
    },
    {
        "source_key": "attendance:ATT_LATE",
        "source": SOURCE_ATTENDANCE,
        "code": "ATT_LATE",
        "kind": "deduction",
        "label": "Potongan keterlambatan",
        "system": True,
    },
    {
        "source_key": "attendance:ATT_ALPHA",
        "source": SOURCE_ATTENDANCE,
        "code": "ATT_ALPHA",
        "kind": "deduction",
        "label": "Potongan alpha",
        "system": True,
    },
    {
        "source_key": "attendance:ATT_UNPAID_LEAVE",
        "source": SOURCE_ATTENDANCE,
        "code": "ATT_UNPAID_LEAVE",
        "kind": "deduction",
        "label": "Potongan izin tanpa bayar",
        "system": True,
    },
    {
        "source_key": "attendance:ATT_UNPAID_SICK",
        "source": SOURCE_ATTENDANCE,
        "code": "ATT_UNPAID_SICK",
        "kind": "deduction",
        "label": "Potongan sakit tanpa bayar",
        "system": True,
    },
    {
        "source_key": "kasbon:KASBON",
        "source": SOURCE_KASBON,
        "code": CODE_KASBON,
        "kind": "deduction",
        "label": "Kasbon",
        "system": True,
    },
    {
        "source_key": "operator_piece:UPAH_BORONGAN",
        "source": SOURCE_OPERATOR_PIECE,
        "code": "UPAH_BORONGAN",
        "kind": "earning",
        "label": "Upah Borongan Operator",
        "system": True,
    },
    {
        "source_key": "operator_bonus:BONUS_OPERATOR",
        "source": SOURCE_OPERATOR_BONUS,
        "code": "BONUS_OPERATOR",
        "kind": "earning",
        "label": "Bonus Operator",
        "system": True,
    },
)

SYSTEM_BY_KEY = {row["source_key"]: row for row in SYSTEM_CATALOG}


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


def _code(raw: Any) -> str:
    return str(raw or "").strip().upper()


def source_key_for_row(row: Optional[dict]) -> Optional[str]:
    """Stable accounting identity. Never uses display name, reason, or array index."""
    row = row or {}
    source = str(row.get("source") or "").strip()
    code = _code(row.get("code"))
    if source == SOURCE_SALARY_PROFILE:
        return f"salary_profile:{code}" if CODE_RX.match(code) else None
    if source == SOURCE_OVERTIME:
        return f"overtime:{code}" if code in OT_CODES else None
    if source == SOURCE_ATTENDANCE:
        return f"attendance:{code}" if code in ATT_CODES else None
    if source == SOURCE_KASBON:
        return "kasbon:KASBON" if code == CODE_KASBON else None
    if source == SOURCE_OPERATOR_PIECE:
        return "operator_piece:UPAH_BORONGAN" if code == "UPAH_BORONGAN" else None
    if source == SOURCE_OPERATOR_BONUS:
        return "operator_bonus:BONUS_OPERATOR" if code == "BONUS_OPERATOR" else None
    if source == SOURCE_COMPONENT:
        component_id = str(row.get("component_id") or "").strip()
        return f"component:{component_id}" if UUID_RX.match(component_id) else None
    if source == SOURCE_ADJUSTMENT:
        adjustment_id = str(row.get("adjustment_id") or "").strip()
        return f"adjustment:{adjustment_id}" if UUID_RX.match(adjustment_id) else None
    return None


def parse_source_key(source_key: str) -> Optional[Dict[str, str]]:
    key = str(source_key or "").strip()
    if key in SYSTEM_BY_KEY:
        row = dict(SYSTEM_BY_KEY[key])
        return row
    if key.startswith("salary_profile:"):
        code = key.split(":", 1)[1]
        if not CODE_RX.match(code) or code == CODE_BASE:
            return None if code != CODE_BASE else dict(SYSTEM_BY_KEY["salary_profile:BASE"])
        return {
            "source_key": key,
            "source": SOURCE_SALARY_PROFILE,
            "code": code,
            "kind": "earning",
            "label": code,
            "system": False,
        }
    if key.startswith("component:"):
        component_id = key.split(":", 1)[1]
        if not UUID_RX.match(component_id):
            return None
        return {
            "source_key": key,
            "source": SOURCE_COMPONENT,
            "code": "",
            "component_id": component_id,
            "kind": "",
            "label": component_id,
            "system": False,
        }
    return None


def _account_display(account: Optional[dict]) -> Optional[Dict[str, Any]]:
    if not account:
        return None
    meta = reporting_metadata(account.get("account_type")) or {}
    return {
        "id": str(account.get("id") or ""),
        "code": str(account.get("code") or ""),
        "name": str(account.get("name") or ""),
        "account_type": str(account.get("account_type") or "").strip().upper(),
        "statement": meta.get("statement"),
        "report_group": meta.get("report_group"),
        "normal_balance": meta.get("normal_balance"),
    }


async def load_account(db, account_id: Optional[str]) -> Optional[dict]:
    if not account_id:
        return None
    return await db.ledger_accounts.find_one({"id": account_id}, {"_id": 0})


def _reporting(account: Optional[dict]) -> Optional[Dict[str, str]]:
    if not account:
        return None
    return reporting_metadata(account.get("account_type"))


def validate_expense_account(account: Optional[dict], *, label: str) -> dict:
    if not account:
        raise HTTPException(400, f"{label} wajib dipilih (COA exact).")
    meta = _reporting(account)
    if not meta or meta["statement"] != "INCOME_STATEMENT" or meta["normal_balance"] != "debit":
        raise HTTPException(
            400,
            f"{label} harus akun Laba/Rugi dengan saldo normal debit (Reporting Map).",
        )
    return account


def validate_liability_account(account: Optional[dict], *, label: str) -> dict:
    if not account:
        raise HTTPException(400, f"{label} wajib dipilih (COA exact).")
    meta = _reporting(account)
    if not meta or meta["statement"] != "BALANCE_SHEET" or meta["report_group"] != "liability":
        raise HTTPException(
            400,
            f"{label} harus akun liabilitas Neraca (Reporting Map: BALANCE_SHEET / liability).",
        )
    return account


def validate_asset_account(account: Optional[dict], *, label: str) -> dict:
    if not account:
        raise HTTPException(400, f"{label} wajib dipilih (COA exact).")
    meta = _reporting(account)
    if not meta or meta["statement"] != "BALANCE_SHEET" or meta["report_group"] != "asset":
        raise HTTPException(
            400,
            f"{label} harus akun aset Neraca (Reporting Map: BALANCE_SHEET / asset).",
        )
    return account


def validate_treatment_account(
    account: Optional[dict],
    treatment: str,
    *,
    label: str,
) -> dict:
    treatment = str(treatment or "").strip()
    if treatment in (TREATMENT_EXPENSE_DEBIT, TREATMENT_EXPENSE_CREDIT):
        return validate_expense_account(account, label=label)
    if treatment == TREATMENT_LIABILITY_CREDIT:
        return validate_liability_account(account, label=label)
    if treatment == TREATMENT_ASSET_CREDIT:
        return validate_asset_account(account, label=label)
    raise HTTPException(400, f"Treatment akuntansi '{treatment}' tidak valid.")


def treatments_for_kind(kind: str) -> FrozenSet[str]:
    if kind == "earning":
        return EARNING_TREATMENTS
    if kind == "deduction":
        return DEDUCTION_TREATMENTS
    return ALL_TREATMENTS


def _normalize_treatment(raw: Any) -> str:
    treatment = str(raw or "").strip()
    if treatment not in ALL_TREATMENTS:
        raise HTTPException(400, "Treatment akuntansi tidak valid.")
    return treatment


def _stored_mappings(doc: Optional[dict]) -> Dict[str, Dict[str, str]]:
    raw = (doc or {}).get("component_mappings") or {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for key, value in raw.items():
        parsed = parse_source_key(str(key))
        if not parsed or not isinstance(value, dict):
            continue
        treatment = str(value.get("treatment") or "").strip()
        account_id = _account_id(value.get("account_id"))
        if treatment not in ALL_TREATMENTS or not account_id:
            continue
        item = {"treatment": treatment, "account_id": account_id}
        if parsed.get("component_id"):
            item["component_id"] = parsed["component_id"]
        out[parsed["source_key"]] = item
    return out


async def _component_by_id(db, component_id: str) -> Optional[dict]:
    return await db[COMPONENTS].find_one({"id": component_id}, {"_id": 0})


async def discover_catalog(db) -> List[dict]:
    """Read-only catalog. Does not seed components or create settings."""
    items: List[dict] = [dict(row) for row in SYSTEM_CATALOG]
    seen = {row["source_key"] for row in items}

    components = await db[COMPONENTS].find({}, {"_id": 0}).to_list(2000)
    for component in components:
        component_id = str(component.get("id") or "").strip()
        if not UUID_RX.match(component_id):
            continue
        key = f"component:{component_id}"
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "source_key": key,
            "source": SOURCE_COMPONENT,
            "code": _code(component.get("code")),
            "component_id": component_id,
            "kind": str(component.get("kind") or ""),
            "label": str(component.get("name") or component.get("code") or component_id),
            "system": False,
        })

    employees = await db[EMPLOYEES].find({"salary_profile.allowances": {"$exists": True}}, {"_id": 0, "salary_profile.allowances": 1}).to_list(5000)
    for employee in employees:
        for allowance in (employee.get("salary_profile") or {}).get("allowances") or []:
            if not isinstance(allowance, dict):
                continue
            code = _code(allowance.get("code"))
            if not CODE_RX.match(code) or code == CODE_BASE:
                continue
            key = f"salary_profile:{code}"
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "source_key": key,
                "source": SOURCE_SALARY_PROFILE,
                "code": code,
                "kind": "earning",
                "label": str(allowance.get("name") or code),
                "system": False,
            })
    return items


def _public_mapping_row(catalog_row: dict, stored: Optional[dict], account: Optional[dict]) -> dict:
    stored = stored or {}
    treatment = str(stored.get("treatment") or "")
    account_id = _account_id(stored.get("account_id"))
    return {
        "source_key": catalog_row["source_key"],
        "source": catalog_row.get("source"),
        "code": catalog_row.get("code") or "",
        "component_id": catalog_row.get("component_id"),
        "kind": catalog_row.get("kind") or "",
        "label": catalog_row.get("label") or catalog_row["source_key"],
        "system": bool(catalog_row.get("system")),
        "treatment": treatment or None,
        "account_id": account_id,
        "account": _account_display(account),
        "mapped": bool(treatment and account_id and account),
    }


async def get_payroll_accounting_settings(db) -> Dict[str, Any]:
    """Zero-write GET. Missing document is an explicit unconfigured state."""
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    stored_mappings = _stored_mappings(doc)
    catalog = await discover_catalog(db)

    extra_keys = [key for key in stored_mappings if key not in {row["source_key"] for row in catalog}]
    for key in extra_keys:
        parsed = parse_source_key(key)
        if parsed:
            catalog.append(parsed)

    payable_id = _account_id((doc or {}).get("payroll_payable_account_id"))
    payable = await load_account(db, payable_id)
    payable_valid = False
    if payable_id and payable:
        try:
            validate_liability_account(payable, label="Akun Utang Gaji")
            payable_valid = True
        except HTTPException:
            payable_valid = False

    mapping_rows = []
    for row in catalog:
        stored = stored_mappings.get(row["source_key"])
        account = await load_account(db, (stored or {}).get("account_id")) if stored else None
        mapping_rows.append(_public_mapping_row(row, stored, account))

    return {
        "configured": bool(payable_valid),
        "payroll_payable_account_id": payable_id if payable_valid else payable_id,
        "payroll_payable_account": _account_display(payable) if payable_valid else (_account_display(payable) if payable else None),
        "payroll_payable_valid": payable_valid,
        "component_mappings": {
            row["source_key"]: {"treatment": row["treatment"], "account_id": row["account_id"]}
            for row in mapping_rows
            if row["treatment"] and row["account_id"]
        },
        "catalog": mapping_rows,
        "updated_at": (doc or {}).get("updated_at"),
        "updated_by": (doc or {}).get("updated_by") or None,
    }


async def resolve_mapping(db, source_key: str) -> Optional[Dict[str, Any]]:
    """Return stored mapping + live account if both exist. No writes."""
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    stored = _stored_mappings(doc).get(source_key)
    if not stored:
        return None
    account = await load_account(db, stored.get("account_id"))
    if not account:
        return {"treatment": stored["treatment"], "account_id": stored["account_id"], "account": None, "valid": False}
    try:
        validate_treatment_account(account, stored["treatment"], label=source_key)
    except HTTPException:
        return {"treatment": stored["treatment"], "account_id": stored["account_id"], "account": account, "valid": False}
    return {
        "treatment": stored["treatment"],
        "account_id": str(account["id"]),
        "account": account,
        "valid": True,
    }


async def active_manual_payment_accounts(db) -> Dict[str, Any]:
    """Read-only ids used by Kas & Bank payment guard.

    Payable is included only while the stored liability mapping is valid.
    Expense ids are active expense_debit / expense_credit component mappings.
    """
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    payable = await resolve_payable(db)
    payable_id = str(payable["account_id"]) if payable and payable.get("valid") else ""
    expense_ids: List[str] = []
    seen = set()
    for row in _stored_mappings(doc).values():
        if row.get("treatment") not in (TREATMENT_EXPENSE_DEBIT, TREATMENT_EXPENSE_CREDIT):
            continue
        account_id = str(row.get("account_id") or "")
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        expense_ids.append(account_id)
    return {
        "payroll_payable_account_id": payable_id or None,
        "expense_account_ids": expense_ids,
    }


async def resolve_payable(db) -> Optional[Dict[str, Any]]:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    payable_id = _account_id((doc or {}).get("payroll_payable_account_id"))
    account = await load_account(db, payable_id)
    if not payable_id:
        return None
    if not account:
        return {"account_id": payable_id, "account": None, "valid": False}
    try:
        validate_liability_account(account, label="Akun Utang Gaji")
    except HTTPException:
        return {"account_id": payable_id, "account": account, "valid": False}
    return {"account_id": str(account["id"]), "account": account, "valid": True}


async def update_payroll_accounting_settings(db, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    payable_id = _account_id(payload.get("payroll_payable_account_id"))
    payable = validate_liability_account(await load_account(db, payable_id), label="Akun Utang Gaji")

    raw_mappings = payload.get("component_mappings") or {}
    if not isinstance(raw_mappings, dict):
        raise HTTPException(400, "component_mappings harus berupa objek keyed by source_key.")

    cleaned: Dict[str, Dict[str, str]] = {}
    for raw_key, raw_value in raw_mappings.items():
        parsed = parse_source_key(str(raw_key))
        if not parsed:
            raise HTTPException(400, f"source_key '{raw_key}' tidak valid.")
        if parsed["source"] == SOURCE_ADJUSTMENT or str(raw_key).startswith("adjustment:"):
            raise HTTPException(400, "Penyesuaian tidak dipetakan secara global.")
        if not isinstance(raw_value, dict):
            raise HTTPException(400, f"Mapping '{parsed['source_key']}' tidak valid.")
        treatment = _normalize_treatment(raw_value.get("treatment"))
        kind = parsed.get("kind") or ""
        if parsed["source"] == SOURCE_COMPONENT:
            component = await _component_by_id(db, parsed["component_id"])
            if component:
                kind = str(component.get("kind") or "")
                parsed["kind"] = kind
        allowed = treatments_for_kind(kind) if kind else ALL_TREATMENTS
        if treatment not in allowed:
            raise HTTPException(
                400,
                f"Treatment '{treatment}' tidak sesuai jenis sumber {parsed['source_key']}.",
            )
        account_id = _account_id(raw_value.get("account_id"))
        account = await load_account(db, account_id)
        validate_treatment_account(account, treatment, label=parsed["source_key"])
        item = {"treatment": treatment, "account_id": str(account["id"])}
        if parsed.get("component_id"):
            item["component_id"] = parsed["component_id"]
        cleaned[parsed["source_key"]] = item

    saved = {
        "key": SETTINGS_KEY,
        "payroll_payable_account_id": str(payable["id"]),
        "component_mappings": cleaned,
        "updated_at": now_iso(),
        "updated_by": _actor(user),
    }
    before = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    await record_accounting_event(
        db,
        event_type=EVENT_PAYROLL_SETTINGS_CHANGED,
        entity_type=ENTITY_PAYROLL_SETTINGS,
        entity_id=SETTINGS_KEY,
        actor=user,
        before={
            "payroll_payable_account_id": (before or {}).get("payroll_payable_account_id"),
            "component_mappings": (before or {}).get("component_mappings") or {},
        },
        after={
            "payroll_payable_account_id": saved["payroll_payable_account_id"],
            "component_mappings": saved["component_mappings"],
        },
    )
    return await get_payroll_accounting_settings(db)


async def validate_adjustment_accounting(
    db,
    *,
    kind: str,
    treatment: Any,
    account_id: Any,
) -> Dict[str, str]:
    kind = str(kind or "").strip()
    if kind not in ("earning", "deduction"):
        raise HTTPException(400, "Jenis penyesuaian harus earning atau deduction.")
    value = _normalize_treatment(treatment)
    if value not in treatments_for_kind(kind):
        raise HTTPException(400, "Treatment akuntansi tidak sesuai jenis penyesuaian.")
    account = await load_account(db, _account_id(account_id))
    validate_treatment_account(account, value, label="Akun penyesuaian payroll")
    return {"accounting_treatment": value, "accounting_account_id": str(account["id"])}
