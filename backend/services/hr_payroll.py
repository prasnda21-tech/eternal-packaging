"""HR & Payroll domain service for Eternal Packaging.

Payroll calculations use Decimal only. Formula strings are intentionally unsupported.
Finalized periods and their versioned snapshots are immutable; reopening creates a revision.
"""
from __future__ import annotations

import copy
import html
import io
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from permissions import ROLE_LABELS
from services.purchase_common import (
    actor_snapshot,
    get_named_lock,
    insert_with_unique_number,
    next_dated_seq,
    now_iso,
)

EMPLOYEES = "hr_employees"
COMPONENTS = "hr_salary_components"
ATTENDANCE = "hr_attendance_entries"
KASBON = "hr_kasbon_schedules"
PERIODS = "hr_payroll_periods"
LINES = "hr_payroll_lines"
SETTINGS_KEY = "hr_payroll"
FILES_KIND = "hr_payroll"

PERM_EMP_VIEW = "hr.employee.view"
PERM_EMP_MANAGE = "hr.employee.manage"
PERM_PAY_VIEW = "payroll.view"
PERM_PAY_CALC = "payroll.calculate"
PERM_PAY_REVIEW = "payroll.review"
PERM_PAY_APPROVE = "payroll.approve"
PERM_PAY_FINAL = "payroll.finalize"
PERM_PAY_REOPEN = "payroll.reopen"
PERM_PAY_SETTINGS = "payroll.settings"
PERM_SLIP_OWN = "payroll.payslip.view_own"
PERM_SLIP_ALL = "payroll.payslip.view_all"
PERM_ADJUST = "payroll.adjustment"
PERM_PIECE_HOLDING = "payroll.piece_holding.edit"

STATUS_DRAFT = "DRAFT"
STATUS_REVIEW = "REVIEW"
STATUS_APPROVAL = "APPROVAL"
STATUS_FINAL = "FINAL"
WORKFLOW_FINALIZE = "finalize"
WORKFLOW_POST = "post"
WORKFLOW_REOPEN = "reopen"
WORKFLOW_CLAIM_TTL_SECONDS = 120
ALLOWED = {
    STATUS_DRAFT: [STATUS_REVIEW],
    STATUS_REVIEW: [STATUS_APPROVAL, STATUS_DRAFT],
    STATUS_APPROVAL: [STATUS_FINAL, STATUS_REVIEW],
    STATUS_FINAL: [],
}

MONEY_Q = Decimal("0.01")
CODE_RX = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")


def to_money(raw: Any) -> Decimal:
    if raw in (None, ""):
        return Decimal("0.00")
    try:
        value = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"Nilai uang '{raw}' tidak valid.")
    if not value.is_finite():
        raise HTTPException(400, "Nilai uang harus berupa angka terbatas.")
    return value.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def money_float(value: Any) -> float:
    d = value if isinstance(value, Decimal) else to_money(value)
    return float(d.quantize(MONEY_Q, rounding=ROUND_HALF_UP))


def _number(raw: Any, label: str, *, minimum: Optional[Decimal] = None) -> Decimal:
    try:
        value = Decimal(str(raw if raw not in (None, "") else 0).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{label} harus berupa angka.")
    if not value.is_finite() or (minimum is not None and value < minimum):
        raise HTTPException(400, f"{label} tidak valid.")
    return value


def _clean(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = copy.deepcopy(doc)
    out.pop("_id", None)
    out.pop("storage_path", None)
    for value in out.values():
        if isinstance(value, dict):
            value.pop("storage_path", None)
    return out


def public_employee(doc: Optional[dict]) -> Optional[dict]:
    return _clean(doc)


async def _role_label_map(db) -> Dict[str, str]:
    """Built-in labels plus custom role labels from `roles` collection."""
    labels = {str(k): str(v) for k, v in ROLE_LABELS.items()}
    try:
        rows = await db.roles.find({}, {"_id": 0, "role": 1, "label": 1}).to_list(500)
    except Exception:
        rows = []
    for row in rows or []:
        slug = str((row or {}).get("role") or "").strip()
        label = str((row or {}).get("label") or "").strip()
        if slug and label:
            labels[slug] = label
    return labels


def _present_employee(
    doc: Optional[dict],
    *,
    linked_user: Optional[dict] = None,
    role_labels: Optional[Dict[str, str]] = None,
) -> Optional[dict]:
    """Pure read presenter for persistent hr_employees rows. Never writes."""
    out = _clean(doc)
    if not out:
        return None
    labels = role_labels or {}
    out["has_hr_profile"] = True
    out["directory_key"] = f"hr:{out.get('id')}"
    if linked_user:
        slug = str(linked_user.get("role") or out.get("role") or "").strip()
        out["name"] = str(linked_user.get("name") or out.get("name") or "").strip()
        out["email"] = str(linked_user.get("email") or out.get("email") or "").strip()
        out["role"] = slug
        out["role_label"] = labels.get(slug) or ROLE_LABELS.get(slug) or slug or None
        out["active"] = str(linked_user.get("status") or "active").lower() != "inactive"
        out["account_status"] = linked_user.get("status") or "active"
        out["user_id"] = linked_user.get("id")
        out["linked_from_settings"] = True
        out["directory_source"] = "hr_linked"
    else:
        slug = str(out.get("role") or "").strip()
        out["role_label"] = labels.get(slug) or ROLE_LABELS.get(slug) or (slug or None)
        out["linked_from_settings"] = False
        out["directory_source"] = "hr_legacy" if not out.get("user_id") else "hr_orphan"
    return out


def _virtual_user_directory_row(user: dict, role_labels: Optional[Dict[str, str]] = None) -> dict:
    """Settings user without hr_employees profile — directory-only, zero persistence."""
    labels = role_labels or {}
    slug = str(user.get("role") or "").strip()
    active = str(user.get("status") or "active").lower() != "inactive"
    user_id = str(user.get("id") or "").strip()
    return {
        "id": None,
        "directory_key": f"user:{user_id}",
        "user_id": user_id,
        "name": str(user.get("name") or "").strip(),
        "email": str(user.get("email") or "").strip(),
        "role": slug,
        "role_label": labels.get(slug) or ROLE_LABELS.get(slug) or slug or None,
        "active": active,
        "account_status": user.get("status") or "active",
        "employee_number": None,
        "division": None,
        "employment_status": None,
        "join_date": None,
        "end_date": None,
        "worker_type": None,
        "phone": str(user.get("phone") or "").strip() or None,
        "address": None,
        "bank_name": None,
        "bank_account": None,
        "bank_account_name": None,
        "npwp": None,
        "bpjs_kesehatan": None,
        "bpjs_ketenagakerjaan": None,
        "salary_profile": None,
        "employment_history": [],
        "linked_from_settings": True,
        "has_hr_profile": False,
        "directory_source": "settings_user",
    }


async def _load_all_users_for_directory(db) -> Dict[str, dict]:
    users = await db.users.find(
        {},
        {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "employee_id": 1, "status": 1, "phone": 1},
    ).to_list(5000)
    return {str(u["id"]): u for u in users if u and u.get("id")}


def public_period(doc: Optional[dict]) -> Optional[dict]:
    out = _clean(doc)
    if not out:
        return None
    out.pop("workflow_claim", None)
    out.pop("workflow_op", None)
    out.pop("workflow_claim_expires_at", None)
    return out


def public_line(doc: Optional[dict]) -> Optional[dict]:
    return _clean(doc)


def default_settings() -> dict:
    return {
        "attendance": {
            "late_grace_minutes": 10,
            "late_rate_per_minute": 0.0,
            "late_cap": 0.0,
            "alpha_rate_per_day": 0.0,
            "unpaid_leave_rate_per_day": 0.0,
            "unpaid_sick_rate_per_day": 0.0,
        },
        "overtime": {
            "weekday_multiplier": 1.5,
            "weekend_multiplier": 2.0,
            "holiday_multiplier": 2.0,
            "min_hours": 0.0,
            "hourly_base_divisor": 25,
        },
        "kasbon": {
            "default_mode": "full",
            "default_installment_amount": 0.0,
            "default_periods": 1,
            "max_deduction_ratio": 0.5,
        },
        "payslip": {
            "company_name": "Eternal Packaging",
            "header_text": "Slip Gaji",
            "footer_text": "Dokumen ini diterbitkan oleh sistem penggajian Eternal Packaging.",
        },
        "config_version": 1,
    }


def _merge_settings(base: dict, patch: Any) -> dict:
    out = copy.deepcopy(base)
    if not isinstance(patch, dict):
        return out
    for key in ("attendance", "overtime", "kasbon", "payslip"):
        if isinstance(patch.get(key), dict):
            out[key].update(copy.deepcopy(patch[key]))
    out["config_version"] = int(patch.get("config_version") or out["config_version"])
    return out


def _validate_settings(raw: dict) -> dict:
    cfg = _merge_settings(default_settings(), raw)
    for key in (
        "late_grace_minutes", "late_rate_per_minute", "late_cap", "alpha_rate_per_day",
        "unpaid_leave_rate_per_day", "unpaid_sick_rate_per_day",
    ):
        val = _number(cfg["attendance"].get(key), key, minimum=Decimal("0"))
        cfg["attendance"][key] = int(val) if key == "late_grace_minutes" else money_float(val)
    for key in ("weekday_multiplier", "weekend_multiplier", "holiday_multiplier", "min_hours", "hourly_base_divisor"):
        val = _number(cfg["overtime"].get(key), key, minimum=Decimal("0"))
        if key == "hourly_base_divisor" and val <= 0:
            raise HTTPException(400, "Pembagi dasar lembur harus lebih besar dari 0.")
        cfg["overtime"][key] = float(val)
    mode = str(cfg["kasbon"].get("default_mode") or "full")
    if mode not in ("full", "installment_fixed", "installment_periods"):
        raise HTTPException(400, "Mode kasbon tidak valid.")
    cfg["kasbon"]["default_mode"] = mode
    cfg["kasbon"]["default_installment_amount"] = money_float(
        _number(cfg["kasbon"].get("default_installment_amount"), "Cicilan kasbon", minimum=Decimal("0"))
    )
    periods = int(_number(cfg["kasbon"].get("default_periods"), "Jumlah periode", minimum=Decimal("1")))
    cfg["kasbon"]["default_periods"] = periods
    ratio = _number(cfg["kasbon"].get("max_deduction_ratio"), "Batas rasio kasbon", minimum=Decimal("0"))
    if ratio > 1:
        raise HTTPException(400, "Batas rasio kasbon maksimal 1.")
    cfg["kasbon"]["max_deduction_ratio"] = float(ratio)
    for key in ("company_name", "header_text", "footer_text"):
        cfg["payslip"][key] = str(cfg["payslip"].get(key) or "").strip()[:500]
    return cfg


async def get_settings(db) -> dict:
    row = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return _validate_settings((row or {}).get("value") or {})


async def save_settings(db, payload: dict, user: dict) -> dict:
    current = await get_settings(db)
    incoming = _merge_settings(current, payload)
    incoming["config_version"] = int(current.get("config_version") or 1) + 1
    cfg = _validate_settings(incoming)
    await db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"key": SETTINGS_KEY, "value": cfg, "updated_at": now_iso(), "updated_by": actor_snapshot(user)}},
        upsert=True,
    )
    return cfg


async def ensure_indexes(db) -> None:
    for coll in (EMPLOYEES, COMPONENTS, ATTENDANCE, KASBON, PERIODS, LINES):
        await db[coll].create_index("id", unique=True)
    await db[EMPLOYEES].create_index("employee_number", unique=True)
    await db[EMPLOYEES].create_index(
        "user_id", unique=True, partialFilterExpression={"user_id": {"$type": "string"}}
    )
    await db[COMPONENTS].create_index("code", unique=True)
    await db[ATTENDANCE].create_index([("period_id", 1), ("employee_id", 1)], unique=True)
    await db[KASBON].create_index("request_id", unique=True)
    await db[KASBON].create_index("employee_id")
    await db[PERIODS].create_index("period_number", unique=True)
    await db[PERIODS].create_index([("group_id", 1), ("revision", 1)], unique=True)
    # After migration the partial monthly index is the uniqueness guard.
    # Do not recreate the full unique index, and do not drop one that is still there.
    if not await _partial_monthly_index_name(db) and not await _legacy_monthly_index_name(db):
        await db[PERIODS].create_index([("year", 1), ("month", 1), ("revision", 1)], unique=True)
    await db[LINES].create_index([("period_id", 1), ("employee_id", 1), ("revision", 1)], unique=True)
    await db[LINES].create_index("period_id")


def _audit(action: str, user: dict, note: str = "") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "action": action,
        "actor": actor_snapshot(user),
        "timestamp": now_iso(),
        "note": str(note or "")[:1000],
    }


async def list_employees(
    db,
    *,
    include_inactive: bool = True,
    q: str = "",
    hr_profile_only: bool = False,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
) -> dict:
    """Pure-read directory union of hr_employees + Settings users (no writes).

    Filter/search/sort run on the FINAL union. Optional pagination slices that
    final result (never paginate hr_employees alone then append users).
    """
    hr_rows = await db[EMPLOYEES].find({}, {"_id": 0}).to_list(2000)
    users_by_id = await _load_all_users_for_directory(db)
    labels = await _role_label_map(db)

    presented: List[dict] = []
    linked_user_ids = set()
    for row in hr_rows:
        uid = str(row.get("user_id") or "").strip()
        linked = users_by_id.get(uid) if uid else None
        if linked:
            linked_user_ids.add(uid)
        item = _present_employee(row, linked_user=linked, role_labels=labels)
        if item:
            presented.append(item)

    if not hr_profile_only:
        for uid, user in users_by_id.items():
            if uid in linked_user_ids:
                continue
            presented.append(_virtual_user_directory_row(user, labels))
    else:
        presented = [row for row in presented if row.get("has_hr_profile") is True and row.get("id")]

    if not include_inactive:
        presented = [row for row in presented if row.get("active") is not False]

    needle = q.strip().lower()
    if needle:
        presented = [
            row for row in presented
            if needle in " ".join(
                str(row.get(k) or "")
                for k in (
                    "employee_number", "name", "role", "role_label", "division",
                    "email", "user_id", "directory_key",
                )
            ).lower()
        ]

    presented.sort(key=lambda row: (
        str(row.get("name") or "").lower(),
        str(row.get("directory_key") or row.get("id") or ""),
    ))

    total = len(presented)
    page_num = 1
    size = total or 1
    page_count = 1
    if page is not None or page_size is not None:
        page_num = max(1, int(page or 1))
        size = max(1, min(int(page_size or 50), 200))
        page_count = max(1, (total + size - 1) // size) if total else 1
        start = (page_num - 1) * size
        presented = presented[start:start + size]

    return {
        "employees": presented,
        "count": total,
        "page": page_num,
        "page_size": size if (page is not None or page_size is not None) else total,
        "page_count": page_count,
    }


async def get_employee(db, employee_id: str) -> dict:
    eid = str(employee_id or "").strip()
    if not eid or eid.lower() in {"null", "undefined", "none"} or eid.startswith("user:"):
        raise HTTPException(404, "Karyawan tidak ditemukan.")
    row = await db[EMPLOYEES].find_one({"id": eid}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Karyawan tidak ditemukan.")
    linked = await _safe_user(db, str(row.get("user_id") or "").strip()) if row.get("user_id") else None
    labels = await _role_label_map(db)
    return _present_employee(row, linked_user=linked, role_labels=labels)


async def _safe_user(db, user_id: str) -> Optional[dict]:
    if not user_id:
        return None
    return await db.users.find_one(
        {"id": user_id},
        {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "employee_id": 1, "status": 1, "phone": 1},
    )


def _salary_profile(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    allowances = []
    for idx, item in enumerate(src.get("allowances") or []):
        if not isinstance(item, dict):
            continue
        allowances.append({
            "code": str(item.get("code") or f"ALLOWANCE_{idx + 1}").strip().upper()[:40],
            "name": str(item.get("name") or "Tunjangan").strip()[:120],
            "amount": money_float(to_money(item.get("amount"))),
        })
    return {
        "base_salary": money_float(to_money(src.get("base_salary"))),
        "allowances": allowances,
        "effective_from": str(src.get("effective_from") or "")[:10],
    }


def _employee_fields(payload: dict, existing: Optional[dict] = None) -> dict:
    fields = (
        "name", "user_id", "role", "division", "employment_status", "join_date", "end_date",
        "worker_type", "email", "phone", "address", "bank_name", "bank_account",
        "bank_account_name", "npwp", "bpjs_kesehatan", "bpjs_ketenagakerjaan",
    )
    out = {}
    for key in fields:
        if key in payload:
            out[key] = str(payload.get(key) or "").strip()[:1000 if key == "address" else 200]
    if "user_id" in out and not out["user_id"]:
        out["user_id"] = None
    if "active" in payload:
        out["active"] = bool(payload.get("active"))
    if "salary_profile" in payload:
        out["salary_profile"] = _salary_profile(payload.get("salary_profile"))
    if "pay_basis" in payload:
        basis = str(payload.get("pay_basis") or "monthly").strip().lower()
        if basis not in ("monthly", "piece"):
            raise HTTPException(400, "pay_basis harus monthly atau piece.")
        out["pay_basis"] = basis
    if "employment_history" in payload:
        if not isinstance(payload.get("employment_history"), list):
            raise HTTPException(400, "Riwayat pekerjaan harus berupa daftar.")
        out["employment_history"] = copy.deepcopy(payload.get("employment_history"))[:500]
    return out


async def create_employee(db, payload: dict, user: dict) -> dict:
    raw_uid = str(payload.get("user_id") or "").strip()
    linked = await _safe_user(db, raw_uid) if raw_uid else None
    if raw_uid and not linked:
        raise HTTPException(404, "User yang ditautkan tidak ditemukan.")
    if raw_uid:
        existing_for_user = await db[EMPLOYEES].find_one({"user_id": raw_uid}, {"_id": 0, "id": 1})
        if existing_for_user:
            raise HTTPException(409, "Profil HR untuk user ini sudah ada.")
    number = str(payload.get("employee_number") or (linked or {}).get("employee_id") or "").strip()
    if not number:
        raise HTTPException(400, "Nomor karyawan wajib diisi.")
    ts = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "employee_number": number[:80],
        "name": str(payload.get("name") or (linked or {}).get("name") or "").strip()[:200],
        "user_id": raw_uid or None,
        "role": str(payload.get("role") or (linked or {}).get("role") or "").strip()[:120],
        "division": "",
        "employment_status": "permanent",
        "join_date": "",
        "end_date": "",
        "active": False if payload.get("active") is False else True,
        "worker_type": "monthly",
        "email": str(payload.get("email") or (linked or {}).get("email") or "").strip()[:200],
        "phone": "",
        "address": "",
        "bank_name": "",
        "bank_account": "",
        "bank_account_name": "",
        "npwp": "",
        "bpjs_kesehatan": "",
        "bpjs_ketenagakerjaan": "",
        "salary_profile": _salary_profile(payload.get("salary_profile")),
        "employment_history": copy.deepcopy(payload.get("employment_history") or []),
        "audit_events": [_audit("created", user)],
        "created_at": ts,
        "updated_at": ts,
        "created_by": actor_snapshot(user),
    }
    doc.update(_employee_fields(payload))
    if linked:
        doc["name"] = str(linked.get("name") or doc["name"])
        doc["email"] = str(linked.get("email") or doc["email"])
        doc["role"] = str(linked.get("role") or doc["role"])
        doc["active"] = str(linked.get("status") or "active").lower() != "inactive"
        if not payload.get("employee_number"):
            doc["employee_number"] = str(linked.get("employee_id") or doc["employee_number"])
    if not doc["name"]:
        raise HTTPException(400, "Nama karyawan wajib diisi.")
    try:
        await db[EMPLOYEES].insert_one(doc)
    except DuplicateKeyError:
        if raw_uid and await db[EMPLOYEES].find_one({"user_id": raw_uid}):
            raise HTTPException(409, "Profil HR untuk user ini sudah ada.")
        raise HTTPException(409, "Nomor karyawan atau tautan user sudah dipakai.")
    labels = await _role_label_map(db)
    return _present_employee(doc, linked_user=linked, role_labels=labels)


def _account_payload(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    return {
        "email": str(src.get("email") or "").strip().lower(),
        "name": str(src.get("name") or "").strip(),
        "role": str(src.get("role") or "").strip(),
        "password": src.get("password"),
        "nickname": (str(src.get("nickname") or "").strip() or None),
        "phone": (str(src.get("phone") or "").strip() or None),
        "joined_at": src.get("joined_at"),
        "notes": src.get("notes"),
    }


async def _compensate_created_user(db, user_id: str) -> None:
    """Rollback exact task-created Eternal user + its user.create audit row only."""
    uid = str(user_id or "").strip()
    if not uid:
        return
    await db.users.delete_many({"id": uid})
    # Exact audit side-effect from create_canonical_user — never mass-delete.
    await db.audit_logs.delete_many({"action": "user.create", "after.id": uid})


async def create_employee_with_optional_account(
    db,
    payload: dict,
    user: dict,
    *,
    create_canonical_user,
    actor_may_create_users: bool,
) -> dict:
    """Create HR employee; optionally create Eternal account + exact user_id link.

    Account creation is explicit only (`create_account` present/true). Compensating
    cleanup deletes the exact newly-created user (+ its user.create audit) if HR
    insert fails afterward.
    """
    account_flag = payload.get("create_account")
    wants_account = bool(account_flag) if not isinstance(account_flag, dict) else True
    account_raw = account_flag if isinstance(account_flag, dict) else (payload.get("account") or {})

    if not wants_account:
        # Strip account fields so they cannot silently create users.
        clean = {k: v for k, v in payload.items() if k not in ("create_account", "account")}
        return await create_employee(db, clean, user)

    if not actor_may_create_users:
        raise HTTPException(403, "Anda tidak berhak membuat akun Eternal. Butuh permission users.manage.")
    if payload.get("user_id"):
        raise HTTPException(400, "Tidak dapat menautkan user_id sekaligus membuat akun Eternal baru.")

    account = _account_payload(account_raw)
    if not account["name"]:
        account["name"] = str(payload.get("name") or "").strip()
    if not account["email"]:
        raise HTTPException(400, "Email akun Eternal wajib diisi.")
    if not account["role"]:
        raise HTTPException(400, "Role akun Eternal wajib dipilih.")
    if not account["password"] or len(str(account["password"])) < 6:
        raise HTTPException(400, "Password akun Eternal minimal 6 karakter.")

    # Validate HR payload before creating user (avoid orphan accounts on HR validation errors).
    number = str(payload.get("employee_number") or "").strip()
    if not number:
        raise HTTPException(400, "Nomor karyawan wajib diisi.")
    hr_name = str(payload.get("name") or account["name"] or "").strip()
    if not hr_name:
        raise HTTPException(400, "Nama karyawan wajib diisi.")

    created_user = await create_canonical_user(
        email=account["email"],
        name=account["name"],
        role=account["role"],
        password=account["password"],
        nickname=account.get("nickname"),
        phone=account.get("phone") or str(payload.get("phone") or "").strip() or None,
        joined_at=account.get("joined_at"),
        notes=account.get("notes"),
        permissions_override={},
        actor=user,
        require_password=True,
    )
    uid = created_user["id"]
    hr_payload = {
        k: v for k, v in payload.items()
        if k not in ("create_account", "account", "password", "user_id")
    }
    hr_payload["user_id"] = uid
    hr_payload["name"] = hr_name
    # Do not invent HR jabatan/division from Eternal role — keep HR fields as submitted.
    try:
        return await create_employee(db, hr_payload, user)
    except Exception:
        await _compensate_created_user(db, uid)
        raise


async def create_eternal_account_for_employee(
    db,
    employee_id: str,
    account_payload: dict,
    user: dict,
    *,
    create_canonical_user,
    actor_may_create_users: bool,
) -> dict:
    """Explicit account provisioning for an existing unlinked HR employee."""
    if not actor_may_create_users:
        raise HTTPException(403, "Anda tidak berhak membuat akun Eternal. Butuh permission users.manage.")
    eid = str(employee_id or "").strip()
    if not eid or eid.lower() in {"null", "undefined", "none"}:
        raise HTTPException(404, "Karyawan tidak ditemukan.")
    existing = await db[EMPLOYEES].find_one({"id": eid}, {"_id": 0})
    if not existing:
        raise HTTPException(404, "Karyawan tidak ditemukan.")
    if existing.get("user_id"):
        raise HTTPException(409, "Karyawan ini sudah tertaut ke akun Eternal.")

    account = _account_payload(account_payload)
    if not account["name"]:
        account["name"] = str(existing.get("name") or "").strip()
    if not account["email"]:
        raise HTTPException(400, "Email akun Eternal wajib diisi.")
    if not account["role"]:
        raise HTTPException(400, "Role akun Eternal wajib dipilih.")
    if not account["password"] or len(str(account["password"])) < 6:
        raise HTTPException(400, "Password akun Eternal minimal 6 karakter.")

    created_user = await create_canonical_user(
        email=account["email"],
        name=account["name"],
        role=account["role"],
        password=account["password"],
        nickname=account.get("nickname"),
        phone=account.get("phone") or str(existing.get("phone") or "").strip() or None,
        joined_at=account.get("joined_at"),
        notes=account.get("notes"),
        permissions_override={},
        actor=user,
        require_password=True,
    )
    uid = created_user["id"]
    try:
        result = await db[EMPLOYEES].find_one_and_update(
            {"id": eid, "$or": [{"user_id": None}, {"user_id": {"$exists": False}}, {"user_id": ""}]},
            {
                "$set": {
                    "user_id": uid,
                    "name": str(created_user.get("name") or existing.get("name") or ""),
                    "email": str(created_user.get("email") or existing.get("email") or ""),
                    "role": str(created_user.get("role") or existing.get("role") or ""),
                    "updated_at": now_iso(),
                },
                "$push": {"audit_events": _audit("linked_eternal_account", user, f"user_id={uid}")},
            },
            return_document=ReturnDocument.AFTER,
        )
        if not result:
            await _compensate_created_user(db, uid)
            raise HTTPException(409, "Karyawan ini sudah tertaut ke akun Eternal.")
    except HTTPException:
        raise
    except Exception:
        await _compensate_created_user(db, uid)
        raise

    labels = await _role_label_map(db)
    return _present_employee(result, linked_user=created_user, role_labels=labels)


async def update_employee(db, employee_id: str, payload: dict, user: dict) -> dict:
    existing = await db[EMPLOYEES].find_one({"id": employee_id})
    if not existing:
        raise HTTPException(404, "Karyawan tidak ditemukan.")
    updates = _employee_fields(payload, existing)
    if "employee_number" in payload:
        number = str(payload.get("employee_number") or "").strip()
        if not number:
            raise HTTPException(400, "Nomor karyawan wajib diisi.")
        updates["employee_number"] = number[:80]

    # Resolve linked Settings user (payload user_id wins when provided).
    linked_id = updates["user_id"] if "user_id" in updates else existing.get("user_id")
    linked = await _safe_user(db, str(linked_id or "").strip()) if linked_id else None
    if linked_id and not linked:
        raise HTTPException(404, "User yang ditautkan tidak ditemukan.")

    if linked:
        # Settings is authority for name / role / email / active — do not allow HR overwrite.
        updates.pop("name", None)
        updates.pop("role", None)
        updates.pop("email", None)
        updates.pop("active", None)
        updates.update({
            "name": str(linked.get("name") or existing.get("name") or ""),
            "email": str(linked.get("email") or existing.get("email") or ""),
            "role": str(linked.get("role") or existing.get("role") or ""),
            "active": str(linked.get("status") or "active").lower() != "inactive",
            "user_id": linked.get("id"),
        })
        if not existing.get("employee_number") and not updates.get("employee_number"):
            updates["employee_number"] = str(linked.get("employee_id") or "")

    updates["updated_at"] = now_iso()
    events = list(existing.get("audit_events") or [])
    events.append(_audit("updated", user))
    updates["audit_events"] = events
    try:
        result = await db[EMPLOYEES].find_one_and_update(
            {"id": employee_id}, {"$set": updates}, return_document=ReturnDocument.AFTER
        )
    except DuplicateKeyError:
        raise HTTPException(409, "Nomor karyawan atau tautan user sudah dipakai.")
    labels = await _role_label_map(db)
    return _present_employee(result, linked_user=linked, role_labels=labels)


def _component_doc(payload: dict, existing: Optional[dict] = None) -> dict:
    src = existing or {}
    code = str(payload.get("code", src.get("code") or "")).strip().upper()
    if not CODE_RX.match(code):
        raise HTTPException(400, "Kode komponen harus 2–40 karakter A-Z, angka, atau underscore.")
    kind = str(payload.get("kind", src.get("kind") or "earning")).lower()
    mode = str(payload.get("calc_mode", src.get("calc_mode") or "fixed")).lower()
    if kind not in ("earning", "deduction"):
        raise HTTPException(400, "Jenis komponen harus earning atau deduction.")
    if mode not in ("fixed", "percentage", "per_day", "per_hour", "per_minute"):
        raise HTTPException(400, "Mode perhitungan komponen tidak valid.")
    percentage_of = str(payload.get("percentage_of", src.get("percentage_of") or "base")).lower()
    if percentage_of not in ("base", "gross"):
        raise HTTPException(400, "Dasar persentase harus base atau gross.")
    def strings(key):
        value = payload.get(key, src.get(key) or [])
        return list(dict.fromkeys(str(v).strip() for v in value if str(v).strip())) if isinstance(value, list) else []
    return {
        "code": code,
        "name": str(payload.get("name", src.get("name") or code)).strip()[:120],
        "kind": kind,
        "active": bool(payload.get("active", src.get("active", True))),
        "calc_mode": mode,
        "amount": money_float(to_money(payload.get("amount", src.get("amount")))),
        "percentage_of": percentage_of,
        "taxable": bool(payload.get("taxable", src.get("taxable", False))),
        "recurring": bool(payload.get("recurring", src.get("recurring", True))),
        "employee_ids": strings("employee_ids"),
        "roles": strings("roles"),
        "divisions": strings("divisions"),
        "effective_from": str(payload.get("effective_from", src.get("effective_from") or ""))[:10],
        "order": int(payload.get("order", src.get("order") or 0)),
        "priority": int(payload.get("priority", src.get("priority") or 0)),
    }


async def list_components(db, *, include_inactive: bool = True) -> List[dict]:
    if await db[COMPONENTS].count_documents({}) == 0:
        await seed_default_components(db)
    filt = {} if include_inactive else {"active": {"$ne": False}}
    return await db[COMPONENTS].find(filt, {"_id": 0}).sort([("order", 1), ("priority", 1), ("code", 1)]).to_list(1000)


async def create_component(db, payload: dict, user: dict) -> dict:
    doc = _component_doc(payload)
    doc.update({"id": str(uuid.uuid4()), "created_at": now_iso(), "updated_at": now_iso(), "created_by": actor_snapshot(user)})
    try:
        await db[COMPONENTS].insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "Kode komponen sudah dipakai.")
    return _clean(doc)


async def update_component(db, component_id: str, payload: dict, user: dict) -> dict:
    existing = await db[COMPONENTS].find_one({"id": component_id})
    if not existing:
        raise HTTPException(404, "Komponen gaji tidak ditemukan.")
    updates = _component_doc(payload, existing)
    updates.update({"updated_at": now_iso(), "updated_by": actor_snapshot(user)})
    try:
        row = await db[COMPONENTS].find_one_and_update(
            {"id": component_id}, {"$set": updates}, return_document=ReturnDocument.AFTER
        )
    except DuplicateKeyError:
        raise HTTPException(409, "Kode komponen sudah dipakai.")
    return _clean(row)


def _employee_request_match(employee: dict) -> dict:
    ids = [str(employee.get("id") or ""), str(employee.get("user_id") or "")]
    ids = [x for x in ids if x]
    return {
        "status": "DISETUJUI",
        "$and": [
            {"$or": [{"type_code": "KASBON"}, {"values.nominal": {"$exists": True}, "type_snapshot.category": "Keuangan"}]},
            {"$or": [{"employee.id": {"$in": ids}}, {"employee.user_id": {"$in": ids}}]},
        ],
    }


async def list_eligible_kasbon(db, employee: dict) -> List[dict]:
    rows = await db.employee_requests.find(_employee_request_match(employee), {"_id": 0}).sort("submitted_at", -1).to_list(500)
    request_ids = [str(row.get("id") or "") for row in rows]
    existing = set()
    if request_ids:
        schedules = await db[KASBON].find({"request_id": {"$in": request_ids}}, {"request_id": 1, "_id": 0}).to_list(500)
        existing = {str(row.get("request_id")) for row in schedules}
    return [{**row, "has_schedule": str(row.get("id")) in existing} for row in rows]


async def ensure_kasbon_schedule(db, request_doc: dict, config: dict) -> dict:
    if not request_doc or request_doc.get("status") != "DISETUJUI":
        raise HTTPException(409, "Kasbon harus berstatus DISETUJUI.")
    category = str((request_doc.get("type_snapshot") or {}).get("category") or request_doc.get("category") or "")
    nominal_present = (request_doc.get("values") or {}).get("nominal") not in (None, "")
    if request_doc.get("type_code") != "KASBON" and not (nominal_present and category == "Keuangan"):
        raise HTTPException(400, "Pengajuan bukan kasbon yang memenuhi syarat.")
    request_id = str(request_doc.get("id") or "")
    existing = await db[KASBON].find_one({"request_id": request_id}, {"_id": 0})
    if existing:
        return existing
    nominal = to_money((request_doc.get("values") or {}).get("nominal"))
    if nominal <= 0:
        raise HTTPException(400, "Nominal kasbon harus lebih besar dari 0.")
    emp = request_doc.get("employee") or {}
    employee_id = str(emp.get("hr_employee_id") or emp.get("id") or "")
    linked = await db[EMPLOYEES].find_one(
        {"$or": [{"id": employee_id}, {"user_id": employee_id}]}, {"id": 1, "_id": 0}
    )
    if linked:
        employee_id = linked["id"]
    kas_cfg = _validate_settings(config).get("kasbon") if "attendance" in config else _merge_settings(default_settings(), {"kasbon": config}).get("kasbon")
    mode = str(kas_cfg.get("default_mode") or "full")
    periods = max(1, int(kas_cfg.get("default_periods") or 1))
    installment = nominal
    if mode == "installment_fixed":
        installment = to_money(kas_cfg.get("default_installment_amount"))
        if installment <= 0:
            installment = nominal
    elif mode == "installment_periods":
        installment = (nominal / Decimal(periods)).quantize(MONEY_Q, rounding=ROUND_HALF_UP)
    doc = {
        "id": str(uuid.uuid4()),
        "request_id": request_id,
        "request_number": request_doc.get("request_number"),
        "employee_id": employee_id,
        "mode": mode,
        "original_amount": money_float(nominal),
        "outstanding": money_float(nominal),
        "installment_amount": money_float(installment),
        "periods": periods,
        "paid_total": 0.0,
        "payments": [],
        "active": True,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    try:
        await db[KASBON].insert_one(doc)
    except DuplicateKeyError:
        return await db[KASBON].find_one({"request_id": request_id}, {"_id": 0})
    return _clean(doc)


async def compute_kasbon_deduction(db, schedule: dict, period_id: str, max_cap: Any) -> dict:
    for payment in schedule.get("payments") or []:
        if str(payment.get("period_id")) == str(period_id):
            return {
                "schedule_id": schedule["id"],
                "request_id": schedule.get("request_id"),
                "request_number": schedule.get("request_number"),
                "amount": money_float(payment.get("amount")),
                "payment_status": payment.get("status") or "pending",
            }
    outstanding = to_money(schedule.get("outstanding"))
    installment = to_money(schedule.get("installment_amount")) or outstanding
    cap = to_money(max_cap)
    amount = min(outstanding, installment, cap) if cap > 0 else Decimal("0")
    result = {
        "schedule_id": schedule["id"],
        "request_id": schedule.get("request_id"),
        "request_number": schedule.get("request_number"),
        "amount": money_float(amount),
        "payment_status": "pending",
    }
    if amount > 0:
        payment = {**result, "period_id": period_id, "created_at": now_iso(), "status": "pending"}
        await db[KASBON].update_one(
            {"id": schedule["id"], "payments.period_id": {"$ne": period_id}},
            {"$push": {"payments": payment}, "$set": {"updated_at": now_iso()}},
        )
    return result


async def list_kasbon_schedules(db, *, employee_id: str = "", active_only: bool = False) -> List[dict]:
    filt: Dict[str, Any] = {}
    if employee_id:
        filt["employee_id"] = employee_id
    if active_only:
        filt.update({"active": {"$ne": False}, "outstanding": {"$gt": 0}})
    return await db[KASBON].find(filt, {"_id": 0}).sort("created_at", -1).to_list(2000)


async def upsert_attendance_entry(db, period_id: str, employee_id: str, payload: dict, user: Optional[dict] = None) -> dict:
    period = await get_period(db, period_id)
    if period.get("status") != STATUS_DRAFT or await _posted_accrual(db, period_id):
        raise HTTPException(409, "Kehadiran hanya dapat diubah pada periode DRAFT.")
    await get_employee(db, employee_id)
    nonnegative = (
        "work_days", "late_minutes", "alpha_days", "unpaid_leave_days", "unpaid_sick_days",
        "overtime_weekday_hours", "overtime_weekend_hours", "overtime_holiday_hours",
    )
    doc = {key: float(_number(payload.get(key), key, minimum=Decimal("0"))) for key in nonnegative}
    doc.update({
        "id": str(uuid.uuid4()),
        "period_id": period_id,
        "employee_id": employee_id,
        "notes": str(payload.get("notes") or "")[:1000],
        "updated_at": now_iso(),
        "updated_by": actor_snapshot(user),
    })
    result = await db[ATTENDANCE].find_one_and_update(
        {"period_id": period_id, "employee_id": employee_id},
        {"$set": {k: v for k, v in doc.items() if k != "id"}, "$setOnInsert": {"id": doc["id"], "created_at": now_iso()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return _clean(result)


async def get_attendance_for_period(db, period_id: str) -> List[dict]:
    return await db[ATTENDANCE].find({"period_id": period_id}, {"_id": 0}).sort("employee_id", 1).to_list(5000)


async def _partial_monthly_index_name(db) -> Optional[str]:
    try:
        info = await db[PERIODS].index_information()
    except Exception:
        return None
    for name, spec in (info or {}).items():
        normalized = [(str(field), int(direction)) for field, direction in (spec.get("key") or [])]
        if normalized != [("year", 1), ("month", 1), ("revision", 1)] or not spec.get("unique"):
            continue
        if (spec.get("partialFilterExpression") or {}).get("period_scope") == "monthly":
            return name
    return None


async def _legacy_monthly_index_name(db) -> Optional[str]:
    """Unique (year, month, revision) without a partial filter. Name is read from the server."""
    try:
        info = await db[PERIODS].index_information()
    except Exception:
        return None
    for name, spec in (info or {}).items():
        key = list(spec.get("key") or [])
        normalized = [(str(field), int(direction)) for field, direction in key]
        if normalized != [("year", 1), ("month", 1), ("revision", 1)]:
            continue
        if spec.get("unique") and not spec.get("partialFilterExpression"):
            return name
    return None


async def create_piece_period(db, start_date: str, end_date: str, user: dict) -> dict:
    start = str(start_date or "").strip()[:10]
    end = str(end_date or "").strip()[:10]
    try:
        start_d = datetime.strptime(start, "%Y-%m-%d").date()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Tanggal mulai dan akhir upah harus YYYY-MM-DD.")
    if end_d < start_d:
        raise HTTPException(400, "Tanggal akhir tidak boleh lebih kecil dari tanggal mulai.")
    if (end_d - start_d).days + 1 > 120:
        raise HTTPException(400, "Rentang upah maksimal 120 hari.")
    year, month = end_d.year, end_d.month
    if await _legacy_monthly_index_name(db) and await db[PERIODS].find_one({"year": year, "month": month, "revision": 1}):
        raise HTTPException(
            409,
            "Indeks bulanan lama masih unik untuk seluruh dokumen. Jalankan backend/scripts/migrate_piece_payroll_indexes.py --apply sebelum menambah periode borongan pada bulan yang sama.",
        )
    settings = await get_settings(db)
    ts = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "group_id": str(uuid.uuid4()),
        "year": year,
        "month": month,
        "start_date": start,
        "end_date": end,
        "status": STATUS_DRAFT,
        "revision": 1,
        "period_scope": "piece",
        "parent_period_id": None,
        "piece_bonus": {"start_date": "", "end_date": "", "adjustments": {}},
        "piece_kasbon_inputs": [],
        "piece_blockers": [],
        "piece_exclusions": [],
        "config_snapshot": copy.deepcopy(settings),
        "config_version": settings["config_version"],
        "calculation_version": 0,
        "audit_events": [_audit("created", user)],
        "created_at": ts,
        "updated_at": ts,
        "created_by": actor_snapshot(user),
    }
    async with get_named_lock("hr_payroll_period_number"):
        saved = await insert_with_unique_number(
            db, PERIODS, "period_number", doc,
            lambda: next_dated_seq(db, PERIODS, "period_number", "PRP"),
        )
    return public_period(saved)


EXISTING_PIECE_RANGE_MESSAGE = "Payroll borongan untuk periode ini sudah tersedia."


async def find_piece_period_for_range(db, start_date: str, end_date: str) -> Optional[dict]:
    """Latest piece period with this exact wage range. Periods have no cancelled status."""
    start = str(start_date or "").strip()[:10]
    end = str(end_date or "").strip()[:10]
    rows = await db[PERIODS].find(
        {"period_scope": "piece", "start_date": start, "end_date": end},
        {"_id": 0},
    ).sort([("revision", -1), ("updated_at", -1)]).to_list(20)
    return rows[0] if rows else None


async def _ensure_piece_bonus_range(db, period: dict, start: str, end: str, user: dict) -> dict:
    from services.operator_wage_claims import save_piece_inputs

    bonus = period.get("piece_bonus") or {}
    if str(bonus.get("start_date") or "") and str(bonus.get("end_date") or ""):
        return public_period(period)
    if str(period.get("status") or "") != STATUS_DRAFT:
        return public_period(period)
    return await save_piece_inputs(
        db,
        period,
        {"bonus": {"start_date": start, "end_date": end}},
        user,
    )


async def prepare_piece_period_from_gaji_operator(db, start_date: str, end_date: str, user: dict) -> dict:
    """Create or reuse a piece period for the Gaji Operator date filter.

    Does not calculate claims, post journals, or create payments.
    """
    existing = await find_piece_period_for_range(db, start_date, end_date)
    if existing:
        period = await _ensure_piece_bonus_range(db, existing, str(existing.get("start_date") or start_date)[:10], str(existing.get("end_date") or end_date)[:10], user)
        return {
            "period": period,
            "created": False,
            "reused": True,
            "message": EXISTING_PIECE_RANGE_MESSAGE,
        }
    created = await create_piece_period(db, start_date, end_date, user)
    from services.operator_wage_claims import save_piece_inputs
    period = await save_piece_inputs(
        db,
        created,
        {
            "start_date": created["start_date"],
            "end_date": created["end_date"],
            "bonus": {"start_date": created["start_date"], "end_date": created["end_date"]},
        },
        user,
    )
    await db[PERIODS].update_one(
        {"id": period["id"], "status": STATUS_DRAFT},
        {"$set": {"piece_prepared_from": "gaji_operator"}},
    )
    fresh = await get_period(db, period["id"])
    return {
        "period": fresh,
        "created": True,
        "reused": False,
        "message": "Periode payroll borongan siap. Lanjutkan Hitung Payroll.",
    }


async def create_period(db, year: int, month: int, start_date: str, end_date: str, user: dict) -> dict:
    try:
        year, month = int(year), int(month)
    except (TypeError, ValueError):
        raise HTTPException(400, "Tahun dan bulan tidak valid.")
    if year < 2000 or year > 2200 or month < 1 or month > 12:
        raise HTTPException(400, "Tahun atau bulan tidak valid.")
    monthly_exists = await db[PERIODS].find_one({
        "year": year,
        "month": month,
        "$or": [
            {"period_scope": "monthly"},
            {"period_scope": {"$exists": False}},
            {"period_scope": None},
        ],
    })
    if monthly_exists:
        raise HTTPException(409, "Periode tahun dan bulan tersebut sudah ada.")
    if await _legacy_monthly_index_name(db) and await db[PERIODS].find_one({"year": year, "month": month, "revision": 1}):
        raise HTTPException(
            409,
            "Indeks bulanan lama masih mencakup periode borongan. Jalankan backend/scripts/migrate_piece_payroll_indexes.py --apply.",
        )
    settings = await get_settings(db)
    ts = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "group_id": str(uuid.uuid4()),
        "year": year,
        "month": month,
        "start_date": str(start_date or "")[:10],
        "end_date": str(end_date or "")[:10],
        "status": STATUS_DRAFT,
        "revision": 1,
        "period_scope": "monthly",
        "parent_period_id": None,
        "config_snapshot": copy.deepcopy(settings),
        "config_version": settings["config_version"],
        "calculation_version": 0,
        "audit_events": [_audit("created", user)],
        "created_at": ts,
        "updated_at": ts,
        "created_by": actor_snapshot(user),
    }
    async with get_named_lock("hr_payroll_period_number"):
        saved = await insert_with_unique_number(
            db, PERIODS, "period_number", doc,
            lambda: next_dated_seq(db, PERIODS, "period_number", "PRP"),
        )
    return public_period(saved)


async def get_period(db, period_id: str) -> dict:
    row = await db[PERIODS].find_one({"id": period_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Periode payroll tidak ditemukan.")
    return row


async def list_periods(db, *, status: str = "") -> List[dict]:
    filt = {"status": status} if status else {}
    rows = await db[PERIODS].find(filt, {"_id": 0}).sort([("year", -1), ("month", -1), ("revision", -1)]).to_list(1000)
    return [public_period(row) for row in rows]


def _component_matches(component: dict, employee: dict, period: dict) -> bool:
    if component.get("active") is False or component.get("recurring") is False:
        return False
    effective = str(component.get("effective_from") or "")
    if effective and effective > str(period.get("end_date") or ""):
        return False
    filters = []
    if component.get("employee_ids"):
        filters.append(str(employee.get("id")) in set(component["employee_ids"]))
    if component.get("roles"):
        filters.append(str(employee.get("role")) in set(component["roles"]))
    if component.get("divisions"):
        filters.append(str(employee.get("division")) in set(component["divisions"]))
    return any(filters) if filters else True


def _component_amount(component: dict, base: Decimal, gross_so_far: Decimal, attendance: dict) -> Decimal:
    amount = to_money(component.get("amount"))
    mode = component.get("calc_mode")
    if mode == "fixed":
        return amount
    if mode == "percentage":
        source = gross_so_far if component.get("percentage_of") == "gross" else base
        return (source * amount / Decimal("100")).quantize(MONEY_Q, rounding=ROUND_HALF_UP)
    units = {
        "per_day": _number(attendance.get("work_days"), "Hari kerja", minimum=Decimal("0")),
        "per_hour": (
            _number(attendance.get("overtime_weekday_hours"), "Lembur", minimum=Decimal("0"))
            + _number(attendance.get("overtime_weekend_hours"), "Lembur", minimum=Decimal("0"))
            + _number(attendance.get("overtime_holiday_hours"), "Lembur", minimum=Decimal("0"))
        ),
        "per_minute": _number(attendance.get("late_minutes"), "Menit terlambat", minimum=Decimal("0")),
    }
    return (amount * units.get(mode, Decimal("0"))).quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _attendance_deductions(attendance: dict, cfg: dict) -> List[dict]:
    rows = []
    grace = Decimal(str(cfg.get("late_grace_minutes") or 0))
    late = max(Decimal("0"), _number(attendance.get("late_minutes"), "Menit terlambat") - grace)
    late_amount = late * to_money(cfg.get("late_rate_per_minute"))
    cap = to_money(cfg.get("late_cap"))
    if cap > 0:
        late_amount = min(late_amount, cap)
    specs = [
        ("ATT_LATE", "Potongan keterlambatan", late_amount),
        ("ATT_ALPHA", "Potongan alpha", _number(attendance.get("alpha_days"), "Hari alpha") * to_money(cfg.get("alpha_rate_per_day"))),
        ("ATT_UNPAID_LEAVE", "Potongan izin tanpa bayar", _number(attendance.get("unpaid_leave_days"), "Hari izin") * to_money(cfg.get("unpaid_leave_rate_per_day"))),
        ("ATT_UNPAID_SICK", "Potongan sakit tanpa bayar", _number(attendance.get("unpaid_sick_days"), "Hari sakit") * to_money(cfg.get("unpaid_sick_rate_per_day"))),
    ]
    for code, name, amount in specs:
        amount = amount.quantize(MONEY_Q, rounding=ROUND_HALF_UP)
        if amount > 0:
            rows.append({"code": code, "name": name, "amount": money_float(amount), "source": "attendance"})
    return rows


def _overtime_earnings(base: Decimal, attendance: dict, cfg: dict) -> List[dict]:
    divisor = _number(cfg.get("hourly_base_divisor"), "Pembagi lembur", minimum=Decimal("0.01"))
    hourly = base / divisor / Decimal("8")
    min_hours = _number(cfg.get("min_hours"), "Minimum lembur", minimum=Decimal("0"))
    rows = []
    for suffix, label, hours_key, multiplier_key in (
        ("WEEKDAY", "Lembur hari kerja", "overtime_weekday_hours", "weekday_multiplier"),
        ("WEEKEND", "Lembur akhir pekan", "overtime_weekend_hours", "weekend_multiplier"),
        ("HOLIDAY", "Lembur hari libur", "overtime_holiday_hours", "holiday_multiplier"),
    ):
        hours = _number(attendance.get(hours_key), label, minimum=Decimal("0"))
        if hours <= 0 or hours < min_hours:
            continue
        multiplier = _number(cfg.get(multiplier_key), label, minimum=Decimal("0"))
        amount = (hours * hourly * multiplier).quantize(MONEY_Q, rounding=ROUND_HALF_UP)
        rows.append({"code": f"OT_{suffix}", "name": label, "hours": float(hours), "multiplier": float(multiplier), "amount": money_float(amount), "source": "overtime"})
    return rows


async def _calculate_employee(db, employee: dict, period: dict, components: List[dict], attendance: dict) -> dict:
    cfg = copy.deepcopy(period.get("config_snapshot") or await get_settings(db))
    base = to_money((employee.get("salary_profile") or {}).get("base_salary"))
    earnings = [{"code": "BASE", "name": "Gaji Pokok", "amount": money_float(base), "source": "salary_profile"}]
    for allowance in (employee.get("salary_profile") or {}).get("allowances") or []:
        amount = to_money(allowance.get("amount"))
        earnings.append({"code": allowance.get("code"), "name": allowance.get("name"), "amount": money_float(amount), "source": "salary_profile"})
    deductions: List[dict] = []
    matched = [copy.deepcopy(c) for c in components if _component_matches(c, employee, period)]
    for component in matched:
        gross_now = sum((to_money(row["amount"]) for row in earnings), Decimal("0"))
        amount = _component_amount(component, base, gross_now, attendance)
        row = {"code": component["code"], "name": component["name"], "amount": money_float(amount), "source": "component", "component_id": component["id"]}
        (earnings if component["kind"] == "earning" else deductions).append(row)
    deductions.extend(_attendance_deductions(attendance, cfg["attendance"]))
    earnings.extend(_overtime_earnings(base, attendance, cfg["overtime"]))
    gross_before_kasbon = sum((to_money(row["amount"]) for row in earnings), Decimal("0"))
    cap = (gross_before_kasbon * Decimal(str(cfg["kasbon"].get("max_deduction_ratio") or 0))).quantize(MONEY_Q)
    used = Decimal("0")
    kasbon_snapshot = []
    schedules = await db[KASBON].find(
        {"employee_id": employee["id"], "active": {"$ne": False}, "outstanding": {"$gt": 0}}, {"_id": 0}
    ).sort("created_at", 1).to_list(500)
    for schedule in schedules:
        remaining_cap = max(Decimal("0"), cap - used)
        item = await compute_kasbon_deduction(db, schedule, period["id"], remaining_cap)
        amount = to_money(item["amount"])
        if amount > 0:
            used += amount
            kasbon_snapshot.append(copy.deepcopy(item))
            deductions.append({"code": "KASBON", "name": f"Kasbon {item.get('request_number') or ''}".strip(), "amount": money_float(amount), "source": "kasbon", "schedule_id": schedule["id"]})
    gross = sum((to_money(row["amount"]) for row in earnings), Decimal("0"))
    total_deduction = sum((to_money(row["amount"]) for row in deductions), Decimal("0"))
    net = gross - total_deduction
    return {
        "id": str(uuid.uuid4()),
        "period_id": period["id"],
        "employee_id": employee["id"],
        "revision": int(period.get("revision") or 1),
        "period_revision": int(period.get("revision") or 1),
        "calculation_version": int(period.get("calculation_version") or 0) + 1,
        "status": period["status"],
        "earnings": earnings,
        "deductions": deductions,
        "gross": money_float(gross),
        "total_deductions": money_float(total_deduction),
        "net": money_float(net),
        "attendance_snapshot": copy.deepcopy(attendance),
        "component_snapshot": matched,
        "kasbon_snapshot": kasbon_snapshot,
        "employee_snapshot": copy.deepcopy(public_employee(employee)),
        "config_snapshot": cfg,
        "adjustments": [],
        "audit_events": [],
        "payslip": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


async def calculate_period(db, period_id: str, user: dict) -> dict:
    async with get_named_lock(f"hr_payroll_calc:{period_id}"):
        period = await get_period(db, period_id)
        if period.get("status") != STATUS_DRAFT or await _posted_accrual(db, period_id):
            raise HTTPException(409, "Perhitungan hanya dapat dilakukan pada status DRAFT.")
        if str(period.get("period_scope") or "") == "piece":
            from services.operator_wage_claims import calculate_piece_period
            return await calculate_piece_period(db, period, user)
        employees = await db[EMPLOYEES].find({"active": {"$ne": False}}, {"_id": 0}).sort("employee_number", 1).to_list(5000)
        employees = [row for row in employees if str(row.get("pay_basis") or "monthly") != "piece"]
        components = await list_components(db, include_inactive=False)
        if not components:
            await seed_default_components(db)
            components = await list_components(db, include_inactive=False)
        attendance_rows = await get_attendance_for_period(db, period_id)
        attendance_by_employee = {row["employee_id"]: row for row in attendance_rows}
        cfg = period.get("config_snapshot") or await get_settings(db)
        for employee in employees:
            for req in await list_eligible_kasbon(db, employee):
                await ensure_kasbon_schedule(db, req, cfg)
        # Recalculation replaces generated lines while preserving explicitly entered adjustments.
        old_lines = await db[LINES].find({"period_id": period_id}, {"_id": 0}).to_list(5000)
        adjustments = {row["employee_id"]: copy.deepcopy(row.get("adjustments") or []) for row in old_lines}
        docs = []
        for employee in employees:
            line = await _calculate_employee(db, employee, period, components, attendance_by_employee.get(employee["id"], {}))
            line["created_by"] = actor_snapshot(user)
            for adjustment in adjustments.get(employee["id"], []):
                _apply_adjustment(line, adjustment)
                line["adjustments"].append(adjustment)
            docs.append(line)
        await db[LINES].delete_many({"period_id": period_id})
        if docs:
            await db[LINES].insert_many(docs)
        events = list(period.get("audit_events") or [])
        events.append(_audit("calculated", user, f"{len(docs)} karyawan"))
        result = await db[PERIODS].find_one_and_update(
            {"id": period_id, "status": STATUS_DRAFT},
            {"$set": {
                "calculation_version": int(period.get("calculation_version") or 0) + 1,
                "calculated_at": now_iso(),
                "calculated_by": actor_snapshot(user),
                "calculation_snapshot": {"employee_count": len(docs), "config": copy.deepcopy(period.get("config_snapshot") or {})},
                "audit_events": events,
                "updated_at": now_iso(),
            }},
            return_document=ReturnDocument.AFTER,
        )
        return {"period": public_period(result), "lines": [public_line(row) for row in docs]}


async def list_lines(db, period_id: str) -> List[dict]:
    rows = await db[LINES].find({"period_id": period_id}, {"_id": 0}).sort("employee_snapshot.employee_number", 1).to_list(5000)
    return [public_line(row) for row in rows]


async def get_line(db, line_id: str) -> dict:
    row = await db[LINES].find_one({"id": line_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Baris payroll tidak ditemukan.")
    return row


def _apply_adjustment(line: dict, adjustment: dict) -> None:
    amount = to_money(adjustment.get("amount"))
    target = "earnings" if adjustment.get("kind") == "earning" else "deductions"
    row = {
        "code": "ADJUSTMENT",
        "name": str(adjustment.get("reason") or "Penyesuaian"),
        "amount": money_float(amount),
        "source": "adjustment",
        "adjustment_id": adjustment.get("id"),
    }
    treatment = str(adjustment.get("accounting_treatment") or "").strip()
    account_id = str(adjustment.get("accounting_account_id") or "").strip()
    if treatment:
        row["accounting_treatment"] = treatment
    if account_id:
        row["accounting_account_id"] = account_id
    line[target].append(row)
    gross = sum((to_money(row["amount"]) for row in line["earnings"]), Decimal("0"))
    deductions = sum((to_money(row["amount"]) for row in line["deductions"]), Decimal("0"))
    line.update({"gross": money_float(gross), "total_deductions": money_float(deductions), "net": money_float(gross - deductions)})


async def adjust_line(
    db,
    period_id: str,
    line_id: str,
    amount: Any,
    reason: str,
    kind: str,
    user: dict,
    accounting_treatment: Any = None,
    accounting_account_id: Any = None,
) -> dict:
    period = await get_period(db, period_id)
    if period.get("status") != STATUS_DRAFT or await _posted_accrual(db, period_id):
        raise HTTPException(409, "Penyesuaian hanya dapat dilakukan pada status DRAFT.")
    if kind not in ("earning", "deduction"):
        raise HTTPException(400, "Jenis penyesuaian harus earning atau deduction.")
    value = to_money(amount)
    if value <= 0:
        raise HTTPException(400, "Nilai penyesuaian harus lebih besar dari 0.")
    if not str(reason or "").strip():
        raise HTTPException(400, "Alasan penyesuaian wajib diisi.")
    from services.payroll_accounting_settings import validate_adjustment_accounting
    accounting = await validate_adjustment_accounting(
        db,
        kind=kind,
        treatment=accounting_treatment,
        account_id=accounting_account_id,
    )
    line = await get_line(db, line_id)
    if line.get("period_id") != period_id:
        raise HTTPException(404, "Baris payroll tidak ada pada periode ini.")
    adjustment = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "amount": money_float(value),
        "reason": str(reason).strip()[:1000],
        "accounting_treatment": accounting["accounting_treatment"],
        "accounting_account_id": accounting["accounting_account_id"],
        "actor": actor_snapshot(user),
        "created_at": now_iso(),
    }
    _apply_adjustment(line, adjustment)
    line.setdefault("adjustments", []).append(adjustment)
    line.setdefault("audit_events", []).append(_audit("adjusted", user, reason))
    await db[LINES].update_one(
        {"id": line_id, "period_id": period_id},
        {"$set": {k: line[k] for k in ("earnings", "deductions", "gross", "total_deductions", "net", "adjustments", "audit_events")}},
    )
    return public_line(line)


def _claim_deadline() -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=WORKFLOW_CLAIM_TTL_SECONDS)).isoformat()


def _workflow_claim_free_filter() -> Dict[str, Any]:
    """Blank, expired, or legacy claim with no expiry. A future expiry stays reserved."""
    now = now_iso()
    return {"$or": [
        {"workflow_claim": None},
        {"workflow_claim": ""},
        {"workflow_claim": {"$exists": False}},
        {"workflow_claim_expires_at": {"$lte": now}},
        {"workflow_claim_expires_at": {"$exists": False}},
    ]}


def _claim_is_current(period: Optional[dict]) -> bool:
    if not period:
        return False
    token = str(period.get("workflow_claim") or "")
    expires = str(period.get("workflow_claim_expires_at") or "")
    return bool(token and expires and expires > now_iso())


def _workflow_claim_values(token: str, op: str) -> Dict[str, Any]:
    return {
        "workflow_claim": token,
        "workflow_op": op,
        "workflow_claim_expires_at": _claim_deadline(),
        "updated_at": now_iso(),
    }


def _workflow_claim_unset() -> Dict[str, str]:
    return {"workflow_claim": "", "workflow_op": "", "workflow_claim_expires_at": ""}


async def claim_workflow(db, period_id: str, op: str, expected_status: str) -> dict:
    """Reserve one payroll workflow.

    The token is a lease. The same operation may take over immediately, including
    a lease that has not expired, so a dead process is recovered without waiting
    and without a finally block. A different operation waits until the lease expires.
    """
    token = str(uuid.uuid4())
    claimed = await db[PERIODS].find_one_and_update(
        {"id": str(period_id), "status": expected_status, **_workflow_claim_free_filter()},
        {"$set": _workflow_claim_values(token, op)},
        return_document=ReturnDocument.AFTER,
    )
    if claimed and str(claimed.get("workflow_claim") or "") == token:
        return claimed
    current = await db[PERIODS].find_one({"id": str(period_id)}, {"_id": 0})
    if not current:
        raise HTTPException(404, "Periode payroll tidak ditemukan.")
    if str(current.get("status") or "") != expected_status:
        raise HTTPException(409, "Status periode telah berubah.")
    existing = str(current.get("workflow_claim") or "")
    existing_op = str(current.get("workflow_op") or "")
    if existing and existing_op == op:
        stolen = await db[PERIODS].find_one_and_update(
            {"id": str(period_id), "status": expected_status, "workflow_claim": existing},
            {"$set": _workflow_claim_values(token, op)},
            return_document=ReturnDocument.AFTER,
        )
        if stolen and str(stolen.get("workflow_claim") or "") == token:
            return stolen
        raise HTTPException(409, "Status periode telah berubah.")
    if existing and _claim_is_current(current) and existing_op != op:
        raise HTTPException(409, "Periode payroll sedang diproses.")
    if existing:
        stolen = await db[PERIODS].find_one_and_update(
            {"id": str(period_id), "status": expected_status, "workflow_claim": existing, **_workflow_claim_free_filter()},
            {"$set": _workflow_claim_values(token, op)},
            return_document=ReturnDocument.AFTER,
        )
        if stolen and str(stolen.get("workflow_claim") or "") == token:
            return stolen
    raise HTTPException(409, "Periode payroll sedang diproses.")


async def _touch_workflow_claim(db, period_id: str, token: str, expected_status: str) -> bool:
    """Extend the lease only while this token still owns the expected status."""
    if not token:
        return False
    result = await db[PERIODS].update_one(
        {"id": str(period_id), "status": expected_status, "workflow_claim": token},
        {"$set": {"workflow_claim_expires_at": _claim_deadline(), "updated_at": now_iso()}},
    )
    return result.matched_count == 1


async def release_workflow_claim(db, period_id: str, token: str) -> None:
    if not token:
        return
    await db[PERIODS].update_one(
        {"id": str(period_id), "workflow_claim": token},
        {"$unset": _workflow_claim_unset()},
    )


async def _commit_final_status(db, period_id: str, token: str, updates: dict):
    return await db[PERIODS].find_one_and_update(
        {"id": str(period_id), "status": STATUS_APPROVAL, "workflow_claim": token},
        {"$set": updates, "$unset": _workflow_claim_unset()},
        return_document=ReturnDocument.AFTER,
    )


async def _posted_accrual(db, period_id: str):
    from services.journal_core import STATUS_POSTED, find_primary_journal_for_source
    from services.payroll_journal_posting import SOURCE_TYPE
    journal = await find_primary_journal_for_source(db, SOURCE_TYPE, str(period_id))
    if journal and journal.get("status") == STATUS_POSTED:
        return journal
    return None


async def _require_final_with_journal(db, period_id: str) -> dict:
    fresh = await get_period(db, period_id)
    journal = await _posted_accrual(db, period_id)
    if str(fresh.get("status") or "") == STATUS_FINAL and journal:
        return public_period(fresh)
    raise HTTPException(409, "Finalisasi payroll belum selesai.")


async def _mark_open_lines_final(db, period_id: str, when: str) -> None:
    """First FINAL mark wins. A later writer cannot refresh finalized_at."""
    await db[LINES].update_many(
        {"period_id": period_id, "status": {"$ne": STATUS_FINAL}},
        {"$set": {"status": STATUS_FINAL, "finalized_at": when}},
    )


async def _finalize_claim_state(db, period_id: str, token: str) -> str:
    """Return own, done, or lost. Only own may write further effects."""
    if await _touch_workflow_claim(db, period_id, token, STATUS_APPROVAL):
        return "own"
    fresh = await get_period(db, period_id)
    if str(fresh.get("status") or "") == STATUS_FINAL and await _posted_accrual(db, period_id):
        return "done"
    return "lost"


async def _accrual_payable_snapshot(db, period_id: str) -> dict:
    """Utang Gaji identity stored on the original accrual. Does not read a new mapping."""
    from services.journal_core import JOURNAL_ENTRIES, STATUS_POSTED
    from services.payroll_journal_posting import SOURCE_TYPE, fill_missing_payable_snapshot

    await fill_missing_payable_snapshot(db, period_id)
    doc = await db[JOURNAL_ENTRIES].find_one(
        {
            "source_type": SOURCE_TYPE,
            "source_id": str(period_id),
            "reversal_of": None,
            "status": STATUS_POSTED,
        },
        {"_id": 0, "id": 1, "payroll_payable": 1},
    )
    snap = (doc or {}).get("payroll_payable") or {}
    account_id = str(snap.get("account_id") or "").strip()
    amount = to_money(snap.get("amount"))
    if doc and account_id and amount == 0 and snap.get("zero_net") is True:
        return {"journal_id": str(doc["id"]), "account_id": account_id, "amount": amount}
    if not doc or not account_id or amount <= 0:
        raise HTTPException(409, "Jurnal akrual belum menyimpan Utang Gaji asal.")
    return {"journal_id": str(doc["id"]), "account_id": account_id, "amount": amount}


async def _store_zero_net_snapshot(db, period_id: str) -> None:
    """Net nol tetap punya identitas Utang Gaji bernilai 0. Payment bank tidak memakai snapshot ini."""
    lines = await db[LINES].find({"period_id": period_id}, {"_id": 0, "net": 1}).to_list(5000)
    net = sum((to_money(line.get("net")) for line in lines), Decimal("0"))
    if net != 0:
        return
    from services.journal_core import JOURNAL_ENTRIES, STATUS_POSTED
    from services.payroll_accounting_settings import resolve_payable
    from services.payroll_journal_posting import SOURCE_TYPE

    journal = await db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_TYPE, "source_id": str(period_id), "reversal_of": None, "status": STATUS_POSTED},
        {"_id": 0, "id": 1, "payroll_payable": 1},
    )
    if not journal:
        return
    current = journal.get("payroll_payable") or {}
    if str(current.get("account_id") or "").strip() and to_money(current.get("amount")) == 0 and current.get("zero_net") is True:
        return
    if to_money(current.get("amount")) > 0:
        return
    payable = await resolve_payable(db)
    if not payable or not payable.get("valid"):
        raise HTTPException(409, "Akun Utang Gaji belum dipetakan.")
    await db[JOURNAL_ENTRIES].update_one(
        {"id": journal["id"], "$or": [
            {"payroll_payable": {"$exists": False}},
            {"payroll_payable.amount": {"$lte": 0}},
        ]},
        {"$set": {"payroll_payable": {
            "account_id": payable["account_id"],
            "amount": 0.0,
            "line_code": "PAYROLL_PAYABLE",
            "zero_net": True,
        }}},
    )


async def _apply_final_effects(db, period_id: str, note: str, user: dict, token: str) -> None:
    period = await get_period(db, period_id)
    if str(period.get("status") or "") == STATUS_FINAL:
        return
    state = await _finalize_claim_state(db, period_id, token)
    if state == "done":
        return
    if state != "own":
        raise HTTPException(409, "Finalisasi payroll belum selesai.")
    lines = await db[LINES].find({"period_id": period_id}, {"_id": 0}).to_list(5000)
    if not lines:
        raise HTTPException(409, "Periode tidak memiliki baris payroll.")
    snapshot = await _accrual_payable_snapshot(db, period_id)
    total_net = sum((to_money(line.get("net")) for line in lines), Decimal("0"))
    if total_net != snapshot["amount"]:
        raise HTTPException(409, "Nominal payroll tidak sama dengan Utang Gaji pada jurnal akrual.")
    for line in lines:
        slip = line.get("payslip") or {}
        if not (slip.get("file_id") and slip.get("generated")):
            continue
        if str(slip.get("journal_id") or "") not in ("", snapshot["journal_id"]):
            raise HTTPException(409, "Slip gaji tidak sesuai jurnal akrual asal.")
        if slip.get("net") is not None and to_money(slip.get("net")) != to_money(line.get("net")):
            raise HTTPException(409, "Slip gaji tidak sesuai jurnal akrual asal.")
    events = list(period.get("audit_events") or [])
    events.append(_audit("transition_final", user, note))
    updates: Dict[str, Any] = {
        "status": STATUS_FINAL,
        "audit_events": events,
        "updated_at": now_iso(),
        "last_transition": {
            "from": STATUS_APPROVAL,
            "to": STATUS_FINAL,
            "note": str(note or "")[:1000],
            "actor": actor_snapshot(user),
            "at": now_iso(),
        },
        "finalized_at": now_iso(),
        "finalized_by": actor_snapshot(user),
        "final_snapshot": {
            "config": copy.deepcopy(period.get("config_snapshot") or {}),
            "calculation": copy.deepcopy(period.get("calculation_snapshot") or {}),
            "line_count": len(lines),
        },
        "frozen": True,
    }
    state = await _finalize_claim_state(db, period_id, token)
    if state == "done":
        return
    if state != "own":
        raise HTTPException(409, "Finalisasi payroll belum selesai.")
    await _mark_open_lines_final(db, period_id, now_iso())
    override = {**period, **updates}
    for line in lines:
        state = await _finalize_claim_state(db, period_id, token)
        if state == "done":
            return
        if state != "own":
            raise HTTPException(409, "Finalisasi payroll belum selesai.")
        await generate_payslip_for_line(
            db,
            line["id"],
            period_override=override,
            binding={"net": line.get("net"), "journal_id": snapshot["journal_id"]},
        )
    state = await _finalize_claim_state(db, period_id, token)
    if state == "done":
        return
    if state != "own":
        raise HTTPException(409, "Finalisasi payroll belum selesai.")
    await _post_kasbon_payments(db, period_id)
    state = await _finalize_claim_state(db, period_id, token)
    if state == "done":
        return
    if state != "own":
        raise HTTPException(409, "Finalisasi payroll belum selesai.")
    saved = await _commit_final_status(db, period_id, token, updates)
    if saved:
        return
    fresh = await get_period(db, period_id)
    if str(fresh.get("status") or "") == STATUS_FINAL and await _posted_accrual(db, period_id):
        return
    raise HTTPException(409, "Finalisasi payroll belum selesai.")


async def finalize_period_with_accrual(db, period_id: str, note: str, user: dict) -> dict:
    """APPROVAL → FINAL plus one accrual journal. Not a success until both exist.

    The journal is stored while the period is still APPROVAL. Status becomes
    FINAL only after the journal, payslips, and kasbon effects. Retry continues
    the same claim instead of posting a second journal.
    """
    from services.payroll_accounting_readiness import (
        evaluate_payroll_accounting_readiness,
        explain_readiness_block,
    )
    from services.payroll_journal_posting import (
        _assert_accounting_period_open,
        fill_missing_payable_snapshot,
        post_payroll_journal,
    )

    period = await get_period(db, period_id)
    status = str(period.get("status") or "")
    posted = await _posted_accrual(db, period_id)
    if status == STATUS_FINAL:
        if posted:
            return await _require_final_with_journal(db, period_id)
        raise HTTPException(409, "Periode FINAL belum memiliki jurnal akrual. Gunakan Post Accounting.")
    if status != STATUS_APPROVAL:
        raise HTTPException(409, f"Transisi {status} ke {STATUS_FINAL} tidak diizinkan.")
    if not await db[LINES].find_one({"period_id": period_id}):
        raise HTTPException(409, "Periode tidak memiliki baris payroll.")
    if not posted:
        if str(period.get("period_scope") or "") == "piece":
            from services.operator_wage_claims import assert_piece_ready
            await assert_piece_ready(db, period)
        readiness = await evaluate_payroll_accounting_readiness(db, period_id, for_finalization=True)
        if not readiness.get("accounting_ready"):
            raise HTTPException(409, explain_readiness_block(readiness))
        await _assert_accounting_period_open(db, str(period.get("end_date") or ""))

    claimed = await claim_workflow(db, period_id, WORKFLOW_FINALIZE, STATUS_APPROVAL)
    token = str(claimed.get("workflow_claim") or "")
    try:
        if await _finalize_claim_state(db, period_id, token) != "own":
            raise HTTPException(409, "Finalisasi payroll belum selesai.")
        posted = await _posted_accrual(db, period_id)
        if posted:
            await fill_missing_payable_snapshot(db, period_id)
        else:
            try:
                await post_payroll_journal(
                    db, period_id, user, for_finalization=True, workflow_token=token,
                )
            except HTTPException:
                if not await _posted_accrual(db, period_id):
                    await release_workflow_claim(db, period_id, token)
                    raise
            if not await _posted_accrual(db, period_id):
                await release_workflow_claim(db, period_id, token)
                raise HTTPException(409, "Finalisasi payroll belum selesai.")
        if str(period.get("period_scope") or "") == "piece":
            from services.operator_wage_claims import freeze_piece_after_journal
            await freeze_piece_after_journal(db, period_id)
        await _store_zero_net_snapshot(db, period_id)
        await _apply_final_effects(db, period_id, note, user, token)
        return await _require_final_with_journal(db, period_id)
    except HTTPException:
        raise


async def transition_period(db, period_id: str, to_status: str, note: str, user: dict) -> dict:
    to_status = str(to_status or "").upper()
    async with get_named_lock(f"hr_payroll_transition:{period_id}"):
        if to_status == STATUS_FINAL:
            return await finalize_period_with_accrual(db, period_id, note, user)
        period = await get_period(db, period_id)
        current = period.get("status")
        if to_status not in ALLOWED.get(current, []):
            raise HTTPException(409, f"Transisi {current} ke {to_status} tidak diizinkan.")
        if current == STATUS_DRAFT and not await db[LINES].find_one({"period_id": period_id}):
            raise HTTPException(409, "Periode harus dihitung sebelum masuk REVIEW.")
        if str(period.get("period_scope") or "") == "piece" and period.get("piece_blockers"):
            raise HTTPException(409, "Masih ada operator yang belum terhubung ke profil HR.")
        if await _posted_accrual(db, period_id):
            raise HTTPException(
                409,
                "Periode sudah memiliki jurnal akrual. Selesaikan finalisasi, bukan kembalikan status.",
            )
        if _claim_is_current(period):
            raise HTTPException(409, "Periode payroll sedang diproses.")
        events = list(period.get("audit_events") or [])
        events.append(_audit(f"transition_{to_status.lower()}", user, note))
        updates: Dict[str, Any] = {
            "status": to_status,
            "audit_events": events,
            "updated_at": now_iso(),
            "last_transition": {"from": current, "to": to_status, "note": str(note or "")[:1000], "actor": actor_snapshot(user), "at": now_iso()},
        }
        result = await db[PERIODS].find_one_and_update(
            {"id": period_id, "status": current, **_workflow_claim_free_filter()},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        if not result:
            raise HTTPException(409, "Status periode telah berubah.")
        await db[LINES].update_many({"period_id": period_id}, {"$set": {"status": to_status}})
        return public_period(result)


def _kasbon_post_pipeline(period_id: str, now: str) -> List[dict]:
    """Flip only this period's pending rows and move outstanding in the same update."""
    is_pending = {"$and": [
        {"$eq": ["$$p.period_id", period_id]},
        {"$eq": ["$$p.status", "pending"]},
    ]}
    pending_amount = {"$sum": {"$map": {
        "input": {"$ifNull": ["$payments", []]},
        "as": "p",
        "in": {"$cond": [is_pending, {"$ifNull": ["$$p.amount", 0]}, 0]},
    }}}
    return [
        {"$set": {"_kasbon_post_amount": pending_amount}},
        {"$set": {
            "payments": {"$map": {
                "input": {"$ifNull": ["$payments", []]},
                "as": "p",
                "in": {"$cond": [
                    is_pending,
                    {"$mergeObjects": ["$$p", {"status": "posted", "posted_at": now}]},
                    "$$p",
                ]},
            }},
            "outstanding": {"$max": [0, {"$subtract": [{"$ifNull": ["$outstanding", 0]}, "$_kasbon_post_amount"]}]},
            "paid_total": {"$add": [{"$ifNull": ["$paid_total", 0]}, "$_kasbon_post_amount"]},
            "updated_at": now,
        }},
        {"$set": {"active": {"$gt": ["$outstanding", 0]}}},
        {"$unset": "_kasbon_post_amount"},
    ]


async def _post_one_kasbon_schedule(db, schedule: dict, period_id: str) -> None:
    pending = any(
        str(payment.get("period_id")) == str(period_id) and payment.get("status") == "pending"
        for payment in (schedule.get("payments") or [])
    )
    if not pending:
        return
    now = now_iso()
    await db[KASBON].update_one(
        {"id": schedule["id"], "payments": {"$elemMatch": {"period_id": period_id, "status": "pending"}}},
        _kasbon_post_pipeline(period_id, now),
    )


async def _post_kasbon_payments(db, period_id: str) -> None:
    schedules = await db[KASBON].find({"payments.period_id": period_id}, {"_id": 0}).to_list(5000)
    for schedule in schedules:
        await _post_one_kasbon_schedule(db, schedule, period_id)


async def _restore_posted_kasbon(db, period_id: str) -> None:
    """Return posted installments of this period only. A second call matches nothing."""
    schedules = await db[KASBON].find({"payments.period_id": period_id}, {"_id": 0, "id": 1, "payments": 1}).to_list(5000)
    now = now_iso()
    for schedule in schedules:
        restore = Decimal("0")
        for payment in schedule.get("payments") or []:
            if str(payment.get("period_id")) == str(period_id) and payment.get("status") == "posted":
                restore += to_money(payment.get("amount"))
        if restore <= 0:
            continue
        await db[KASBON].update_one(
            {"id": schedule["id"], "payments": {"$elemMatch": {"period_id": period_id, "status": "posted"}}},
            {
                "$set": {
                    "payments.$[p].status": "restored",
                    "payments.$[p].restored_at": now,
                    "active": True,
                    "updated_at": now,
                },
                "$inc": {
                    "outstanding": money_float(restore),
                    "paid_total": money_float(-restore),
                },
            },
            array_filters=[{"p.period_id": period_id, "p.status": "posted"}],
        )


async def _insert_reopened_period(db, doc: dict) -> None:
    try:
        await db[PERIODS].insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "Revisi periode sudah ada.")


async def reopen_period(db, period_id: str, reason: str, user: dict) -> dict:
    if not str(reason or "").strip():
        raise HTTPException(400, "Alasan buka ulang wajib diisi.")
    async with get_named_lock(f"hr_payroll_reopen:{period_id}"):
        old = await get_period(db, period_id)
        if old.get("status") != STATUS_FINAL:
            raise HTTPException(409, "Hanya periode FINAL yang dapat dibuka ulang.")
        if _claim_is_current(old) and str(old.get("workflow_op") or "") == WORKFLOW_POST:
            raise HTTPException(409, "Periode payroll sedang diposting ke accounting.")
        if await _posted_accrual(db, old["id"]):
            raise HTTPException(
                409,
                "Periode yang sudah memiliki jurnal accounting aktif tidak dapat dibuka ulang.",
            )
        from services.payroll_payment import period_has_posted_payroll_payment
        if await period_has_posted_payroll_payment(db, old["id"]):
            raise HTTPException(
                409,
                "Periode yang sudah memiliki pembayaran payroll posted tidak dapat dibuka ulang.",
            )
        revision = int(old.get("revision") or 1) + 1
        existing_revision = await db[PERIODS].find_one({"group_id": old["group_id"], "revision": revision}, {"_id": 0})
        if existing_revision:
            if str(old.get("period_scope") or "") == "piece" and existing_revision.get("status") == STATUS_DRAFT:
                from services.operator_wage_claims import transfer_piece_ownership
                await transfer_piece_ownership(db, old, existing_revision["id"])
                return public_period(existing_revision)
            raise HTTPException(409, "Revisi lanjutan periode ini sudah ada.")
        claimed = await claim_workflow(db, period_id, WORKFLOW_REOPEN, STATUS_FINAL)
        token = str(claimed.get("workflow_claim") or "")
        try:
            return await _reopen_effects(db, old, reason, user, token, revision)
        except HTTPException:
            raise


async def _reopen_effects(db, old: dict, reason: str, user: dict, token: str, revision: int) -> dict:
    """Restore kasbon and insert one revision only while this token still owns the period."""
    period_id = old["id"]

    async def own() -> None:
        if not await _touch_workflow_claim(db, period_id, token, STATUS_FINAL):
            raise HTTPException(409, "Periode payroll sedang diproses.")

    await own()
    if await _posted_accrual(db, period_id):
        await release_workflow_claim(db, period_id, token)
        raise HTTPException(
            409,
            "Periode yang sudah memiliki jurnal accounting aktif tidak dapat dibuka ulang.",
        )
    if await db[PERIODS].find_one({"group_id": old["group_id"], "revision": revision}):
        await release_workflow_claim(db, period_id, token)
        raise HTTPException(409, "Revisi lanjutan periode ini sudah ada.")
    await own()
    await _restore_posted_kasbon(db, period_id)
    await own()
    doc = {
        **{k: copy.deepcopy(old.get(k)) for k in (
            "year", "month", "start_date", "end_date", "group_id", "config_snapshot", "config_version",
            "period_scope", "piece_bonus", "piece_kasbon_inputs", "piece_exclusions", "piece_blockers",
        )},
        "id": str(uuid.uuid4()),
        "period_number": f"{old.get('period_number')}-R{revision}",
        "status": STATUS_DRAFT,
        "revision": revision,
        "parent_period_id": old["id"],
        "calculation_version": 0,
        "audit_events": [_audit("reopened", user, reason)],
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "created_by": actor_snapshot(user),
        "reopen_reason": str(reason).strip()[:1000],
    }
    try:
        await own()
        if await db[ATTENDANCE].count_documents({"period_id": doc["id"]}) == 0:
            old_attendance = await db[ATTENDANCE].find({"period_id": period_id}, {"_id": 0}).to_list(5000)
            copied = []
            for item in old_attendance:
                item.pop("_id", None)
                item.update({"id": str(uuid.uuid4()), "period_id": doc["id"], "created_at": now_iso(), "updated_at": now_iso()})
                copied.append(item)
            if copied:
                await db[ATTENDANCE].insert_many(copied)
        await own()
        await _insert_reopened_period(db, doc)
        from services.operator_wage_claims import transfer_piece_ownership
        await transfer_piece_ownership(db, old, doc["id"])
    except HTTPException:
        await db[ATTENDANCE].delete_many({"period_id": doc["id"]})
        await release_workflow_claim(db, period_id, token)
        raise
    await release_workflow_claim(db, period_id, token)
    return public_period(doc)


def _safe(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def generate_payslip_pdf_bytes(line: dict, period: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    cfg = (line.get("config_snapshot") or period.get("config_snapshot") or default_settings()).get("payslip") or {}
    employee = line.get("employee_snapshot") or {}
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.4 * cm, bottomMargin=1.4 * cm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("PayTitle", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=colors.HexColor("#0f172a"))
    body = ParagraphStyle("PayBody", parent=styles["Normal"], fontSize=9, leading=13, textColor=colors.HexColor("#1e293b"))
    muted = ParagraphStyle("PayMuted", parent=body, fontSize=8, textColor=colors.HexColor("#64748b"))
    elements = [
        Paragraph(_safe(cfg.get("company_name") or "Eternal Packaging"), title),
        Paragraph(_safe(cfg.get("header_text") or "Slip Gaji"), body),
        Paragraph(f"Periode: {period.get('month'):02d}/{period.get('year')} · Revisi {period.get('revision', 1)}", body),
        Paragraph(
            f"Rentang: {_safe(period.get('start_date') or '-')} – {_safe(period.get('end_date') or '-')}"
            if str(period.get("period_scope") or "") == "piece"
            else f"Rentang tanggal: {_safe(period.get('start_date') or '-')} – {_safe(period.get('end_date') or '-')}",
            body,
        ),
        Spacer(1, 0.15 * cm),
        HRFlowable(width="100%", color=colors.HexColor("#0369a1"), thickness=1.2),
        Spacer(1, 0.15 * cm),
    ]
    meta = [
        ["Karyawan", _safe(employee.get("name") or "-")],
        ["Nomor Karyawan", _safe(employee.get("employee_number") or "-")],
        ["Jabatan / Divisi", f"{_safe(employee.get('role') or '-')} / {_safe(employee.get('division') or '-')}"],
        ["Nomor Periode", _safe(period.get("period_number") or "-")],
    ]
    table = Table(meta, colWidths=[4.2 * cm, 12.2 * cm])
    table.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    elements.append(table)
    rows = [["Komponen", "Pendapatan", "Potongan"]]
    for item in line.get("earnings") or []:
        rows.append([_safe(item.get("name")), f"Rp {to_money(item.get('amount')):,.2f}", ""])
    for item in line.get("deductions") or []:
        rows.append([_safe(item.get("name")), "", f"Rp {to_money(item.get('amount')):,.2f}"])
    rows.extend([
        ["TOTAL", f"Rp {to_money(line.get('gross')):,.2f}", f"Rp {to_money(line.get('total_deductions')):,.2f}"],
        ["GAJI BERSIH", f"Rp {to_money(line.get('net')):,.2f}", ""],
    ])
    detail = Table(rows, colWidths=[8.2 * cm, 4.1 * cm, 4.1 * cm], repeatRows=1)
    detail.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -2), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.extend([Spacer(1, 0.25 * cm), detail, Spacer(1, 0.35 * cm)])
    elements.append(Paragraph(_safe(cfg.get("footer_text") or ""), muted))
    elements.append(Paragraph("Dokumen sistem ini bukan tanda tangan digital tersertifikasi.", muted))
    doc.build(elements)
    return buffer.getvalue()


def _payslip_binding_conflict(existing: dict, line: dict, binding: Optional[dict]) -> bool:
    if not binding:
        return False
    net = to_money(binding.get("net"))
    journal_id = str(binding.get("journal_id") or "")
    if to_money(line.get("net")) != net:
        return True
    if existing.get("net") is not None and to_money(existing.get("net")) != net:
        return True
    stored_journal = str(existing.get("journal_id") or "")
    if stored_journal and journal_id and stored_journal != journal_id:
        return True
    return False


async def _return_existing_payslip(db, line: dict, binding: Optional[dict]) -> dict:
    existing = line.get("payslip") or {}
    if _payslip_binding_conflict(existing, line, binding):
        raise HTTPException(409, "Slip gaji tidak sesuai jurnal akrual asal.")
    if binding and (existing.get("net") is None or not str(existing.get("journal_id") or "")):
        await db[LINES].update_one(
            {"id": line["id"], "payslip.file_id": existing["file_id"], "payslip.generated": True},
            [{"$set": {
                "payslip.net": {"$cond": [
                    {"$eq": [{"$ifNull": ["$payslip.net", None]}, None]},
                    money_float(to_money(binding.get("net"))),
                    "$payslip.net",
                ]},
                "payslip.journal_id": {"$cond": [
                    {"$in": [{"$ifNull": ["$payslip.journal_id", ""]}, ["", None]]},
                    str(binding.get("journal_id") or ""),
                    "$payslip.journal_id",
                ]},
                "updated_at": now_iso(),
            }}],
        )
        line = await get_line(db, line["id"])
    return public_line(line)


async def generate_payslip_for_line(
    db,
    line_id: str,
    period_override: Optional[dict] = None,
    binding: Optional[dict] = None,
) -> dict:
    async with get_named_lock(f"hr_payroll_payslip:{line_id}"):
        line = await get_line(db, line_id)
        existing = line.get("payslip") or {}
        if existing.get("file_id") and existing.get("generated"):
            return await _return_existing_payslip(db, line, binding)
        period = period_override or await get_period(db, line["period_id"])
        if period.get("status") != STATUS_FINAL:
            raise HTTPException(409, "Slip gaji hanya tersedia untuk periode FINAL.")
        if binding and _payslip_binding_conflict(existing, line, binding):
            raise HTTPException(409, "Slip gaji tidak sesuai jurnal akrual asal.")
        from services import storage as obj_storage
        file_id = str(uuid.uuid4())
        filename = f"{period.get('period_number')}-{(line.get('employee_snapshot') or {}).get('employee_number') or line['employee_id']}.pdf"
        storage_path = f"{obj_storage.APP_NAME}/hr-payroll/{period['id']}/{line_id}/{file_id}.pdf"
        payslip = {
            "generated": True,
            "file_id": file_id,
            "storage_path": storage_path,
            "content_type": "application/pdf",
            "filename": filename,
            "generated_at": now_iso(),
            "snapshot_version": int(line.get("calculation_version") or 1),
            "net": money_float(to_money((binding or {}).get("net", line.get("net")))),
        }
        if binding and binding.get("journal_id"):
            payslip["journal_id"] = str(binding["journal_id"])
        won = await db[LINES].update_one(
            {"id": line_id, "payslip.generated": {"$ne": True}},
            {"$set": {"payslip": payslip, "updated_at": now_iso()}},
        )
        if won.matched_count != 1:
            stored = await get_line(db, line_id)
            stored_slip = stored.get("payslip") or {}
            if stored_slip.get("file_id") and stored_slip.get("generated"):
                return await _return_existing_payslip(db, stored, binding)
            raise HTTPException(409, "Slip gaji tidak sesuai jurnal akrual asal.")
        try:
            data = generate_payslip_pdf_bytes(line, period)
            obj_storage.put_object(storage_path, data, "application/pdf")
            await db.files.insert_one({
                "id": file_id,
                "kind": FILES_KIND,
                "period_id": period["id"],
                "line_id": line_id,
                "storage_path": storage_path,
                "content_type": "application/pdf",
                "original_filename": filename,
                "uploaded_at": now_iso(),
                "is_deleted": False,
            })
        except Exception:
            await db[LINES].update_one(
                {"id": line_id, "payslip.file_id": file_id},
                {"$unset": {"payslip": ""}, "$set": {"updated_at": now_iso()}},
            )
            raise
        return public_line(await get_line(db, line_id))


def can_view_payslip(employee_user: dict, line: dict, perms: set) -> bool:
    if PERM_SLIP_ALL in perms:
        return True
    if PERM_SLIP_OWN not in perms:
        return False
    snap = line.get("employee_snapshot") or {}
    user_ids = {
        str(employee_user.get("id") or ""),
        str(employee_user.get("user_id") or ""),
        str(employee_user.get("sub") or ""),
        str(employee_user.get("employee_id") or ""),
    }
    line_ids = {str(line.get("employee_id") or ""), str(snap.get("id") or ""), str(snap.get("user_id") or ""), str(snap.get("employee_number") or "")}
    return bool((user_ids - {""}) & (line_ids - {""}))


async def seed_default_components(db) -> None:
    defaults = [
        {"code": "TUNJANGAN_TETAP", "name": "Tunjangan Tetap", "kind": "earning", "calc_mode": "fixed", "amount": 0, "order": 10},
        {"code": "BPJS_KARYAWAN", "name": "BPJS Karyawan", "kind": "deduction", "calc_mode": "fixed", "amount": 0, "order": 90},
    ]
    for payload in defaults:
        doc = _component_doc(payload)
        doc.update({"id": str(uuid.uuid4()), "created_at": now_iso(), "updated_at": now_iso(), "system_default": True})
        try:
            await db[COMPONENTS].insert_one(doc)
        except DuplicateKeyError:
            continue
