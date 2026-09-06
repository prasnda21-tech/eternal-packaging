"""Static Accurate account_type → reporting metadata.

Reporting foundation only. Never used to flip journal debit/credit arithmetic.
Not persisted on ledger_accounts.

Labels and type catalog: Accurate Online E-MODUL (16 Tipe Akun) + Accurate
account import codes. Statement / report_group / normal_balance follow those
official type classes (Neraca vs Laba/Rugi; contra-asset DEPR = credit).
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional, Tuple

# Official Accurate Online labels (E-MODUL / import template).
ACCOUNT_TYPE_REPORTING_MAP: Dict[str, Dict[str, str]] = {
    "BANK": {
        "label": "Kas/Bank",
        "statement": "BALANCE_SHEET",
        "report_group": "asset",
        "normal_balance": "debit",
    },
    "AREC": {
        "label": "Piutang Usaha",
        "statement": "BALANCE_SHEET",
        "report_group": "asset",
        "normal_balance": "debit",
    },
    "INTR": {
        "label": "Persediaan",
        "statement": "BALANCE_SHEET",
        "report_group": "asset",
        "normal_balance": "debit",
    },
    "OCAS": {
        "label": "Aset lancar lainnya",
        "statement": "BALANCE_SHEET",
        "report_group": "asset",
        "normal_balance": "debit",
    },
    "FASS": {
        "label": "Aset tetap",
        "statement": "BALANCE_SHEET",
        "report_group": "asset",
        "normal_balance": "debit",
    },
    "DEPR": {
        "label": "Akumulasi Depresiasi",
        "statement": "BALANCE_SHEET",
        "report_group": "asset",
        "normal_balance": "credit",
    },
    "OASS": {
        "label": "Aset lainnya",
        "statement": "BALANCE_SHEET",
        "report_group": "asset",
        "normal_balance": "debit",
    },
    "APAY": {
        "label": "Utang Usaha",
        "statement": "BALANCE_SHEET",
        "report_group": "liability",
        "normal_balance": "credit",
    },
    "OCLY": {
        "label": "Utang lancar lain-lain",
        "statement": "BALANCE_SHEET",
        "report_group": "liability",
        "normal_balance": "credit",
    },
    "LTLY": {
        "label": "Utang jangka panjang",
        "statement": "BALANCE_SHEET",
        "report_group": "liability",
        "normal_balance": "credit",
    },
    "EQTY": {
        "label": "Ekuitas",
        "statement": "BALANCE_SHEET",
        "report_group": "equity",
        "normal_balance": "credit",
    },
    "REVE": {
        "label": "Pendapatan",
        "statement": "INCOME_STATEMENT",
        "report_group": "revenue",
        "normal_balance": "credit",
    },
    "COGS": {
        "label": "Beban Pokok Penjualan",
        "statement": "INCOME_STATEMENT",
        "report_group": "cogs",
        "normal_balance": "debit",
    },
    "EXPS": {
        "label": "Beban",
        "statement": "INCOME_STATEMENT",
        "report_group": "expense",
        "normal_balance": "debit",
    },
    "OEXP": {
        "label": "Beban lain-lain",
        "statement": "INCOME_STATEMENT",
        "report_group": "other_expense",
        "normal_balance": "debit",
    },
    "OINC": {
        "label": "Pendapatan lain-lain",
        "statement": "INCOME_STATEMENT",
        "report_group": "other_income",
        "normal_balance": "credit",
    },
}

STATEMENTS: FrozenSet[str] = frozenset({"BALANCE_SHEET", "INCOME_STATEMENT"})
REPORT_GROUPS: FrozenSet[str] = frozenset({
    "asset",
    "liability",
    "equity",
    "revenue",
    "cogs",
    "expense",
    "other_income",
    "other_expense",
})
NORMAL_BALANCES: FrozenSet[str] = frozenset({"debit", "credit"})
VALID_STATEMENT_GROUPS: FrozenSet[Tuple[str, str]] = frozenset({
    ("BALANCE_SHEET", "asset"),
    ("BALANCE_SHEET", "liability"),
    ("BALANCE_SHEET", "equity"),
    ("INCOME_STATEMENT", "revenue"),
    ("INCOME_STATEMENT", "cogs"),
    ("INCOME_STATEMENT", "expense"),
    ("INCOME_STATEMENT", "other_income"),
    ("INCOME_STATEMENT", "other_expense"),
})

PERSISTED_ACCOUNT_FIELDS: FrozenSet[str] = frozenset({"code", "name", "account_type"})
DERIVED_ACCOUNT_FIELDS: FrozenSet[str] = frozenset({
    "account_type_label",
    "report_group",
    "normal_balance",
    "statement",
})


class AccountTypeMapError(ValueError):
    """Static reporting map is incomplete or invalid."""


def validate_account_type_reporting_map(allowed_types: Tuple[str, ...]) -> None:
    allowed = tuple(allowed_types)
    keys = tuple(ACCOUNT_TYPE_REPORTING_MAP.keys())
    if len(keys) != len(set(keys)):
        raise AccountTypeMapError("duplicate account_type machine key")
    allowed_set = set(allowed)
    mapped_set = set(keys)
    missing = sorted(allowed_set - mapped_set)
    extra = sorted(mapped_set - allowed_set)
    if missing:
        raise AccountTypeMapError(f"allowed account_type missing from map: {missing}")
    if extra:
        raise AccountTypeMapError(f"unknown account_type added to map: {extra}")
    if len(keys) != len(allowed):
        raise AccountTypeMapError("map size does not match allowed account types")

    for code, row in ACCOUNT_TYPE_REPORTING_MAP.items():
        if set(row.keys()) != {"label", "statement", "report_group", "normal_balance"}:
            raise AccountTypeMapError(f"{code}: invalid map entry keys")
        label = str(row.get("label") or "").strip()
        statement = str(row.get("statement") or "").strip()
        report_group = str(row.get("report_group") or "").strip()
        normal_balance = str(row.get("normal_balance") or "").strip()
        if not label:
            raise AccountTypeMapError(f"{code}: empty label")
        if statement not in STATEMENTS:
            raise AccountTypeMapError(f"{code}: invalid statement")
        if report_group not in REPORT_GROUPS:
            raise AccountTypeMapError(f"{code}: invalid report_group")
        if normal_balance not in NORMAL_BALANCES:
            raise AccountTypeMapError(f"{code}: invalid normal_balance")
        if (statement, report_group) not in VALID_STATEMENT_GROUPS:
            raise AccountTypeMapError(
                f"{code}: invalid statement/report_group combination"
            )


def reporting_metadata(account_type: Optional[str]) -> Optional[Dict[str, str]]:
    """Derive reporting fields from account_type. None when type is blank/unknown."""
    if account_type is None:
        return None
    code = str(account_type).strip().upper()
    if not code:
        return None
    row = ACCOUNT_TYPE_REPORTING_MAP.get(code)
    if not row:
        return None
    return {
        "account_type": code,
        "account_type_label": row["label"],
        "statement": row["statement"],
        "report_group": row["report_group"],
        "normal_balance": row["normal_balance"],
    }


def catalog_items(allowed_types: Tuple[str, ...]) -> list:
    validate_account_type_reporting_map(allowed_types)
    items = []
    for code in allowed_types:
        meta = reporting_metadata(code)
        assert meta is not None
        items.append({
            "code": code,
            "label": meta["account_type_label"],
            "account_type": code,
            "account_type_label": meta["account_type_label"],
            "statement": meta["statement"],
            "report_group": meta["report_group"],
            "normal_balance": meta["normal_balance"],
        })
    return items


def apply_derived_reporting_fields(public: Dict[str, Any]) -> Dict[str, Any]:
    """Attach derived reporting fields. Does not write to persistence."""
    meta = reporting_metadata(public.get("account_type"))
    if not meta:
        public["account_type_label"] = None
        public["statement"] = None
        public["report_group"] = None
        public["normal_balance"] = None
        return public
    public["account_type_label"] = meta["account_type_label"]
    public["statement"] = meta["statement"]
    public["report_group"] = meta["report_group"]
    public["normal_balance"] = meta["normal_balance"]
    return public
