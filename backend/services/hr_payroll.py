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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

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

STATUS_DRAFT = "DRAFT"
STATUS_REVIEW = "REVIEW"
STATUS_APPROVAL = "APPROVAL"
STATUS_FINAL = "FINAL"
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


def public_period(doc: Optional[dict]) -> Optional[dict]:
    return _clean(doc)


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


async def list_employees(db, *, include_inactive: bool = True, q: str = "") -> List[dict]:
    filt = {} if include_inactive else {"active": {"$ne": False}}
    rows = await db[EMPLOYEES].find(filt, {"_id": 0}).sort("name", 1).to_list(2000)
    needle = q.strip().lower()
    if needle:
        rows = [
            row for row in rows
            if needle in " ".join(str(row.get(k) or "") for k in ("employee_number", "name", "role", "division", "email")).lower()
        ]
    return [public_employee(row) for row in rows]


async def get_employee(db, employee_id: str) -> dict:
    row = await db[EMPLOYEES].find_one({"id": employee_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Karyawan tidak ditemukan.")
    return public_employee(row)


async def _safe_user(db, user_id: str) -> Optional[dict]:
    if not user_id:
        return None
    return await db.users.find_one(
        {"id": user_id},
        {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "employee_id": 1, "status": 1},
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
    if "employment_history" in payload:
        if not isinstance(payload.get("employment_history"), list):
            raise HTTPException(400, "Riwayat pekerjaan harus berupa daftar.")
        out["employment_history"] = copy.deepcopy(payload.get("employment_history"))[:500]
    return out


async def create_employee(db, payload: dict, user: dict) -> dict:
    linked = await _safe_user(db, str(payload.get("user_id") or "").strip())
    if payload.get("user_id") and not linked:
        raise HTTPException(404, "User yang ditautkan tidak ditemukan.")
    number = str(payload.get("employee_number") or (linked or {}).get("employee_id") or "").strip()
    if not number:
        raise HTTPException(400, "Nomor karyawan wajib diisi.")
    ts = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "employee_number": number[:80],
        "name": str(payload.get("name") or (linked or {}).get("name") or "").strip()[:200],
        "user_id": str(payload.get("user_id") or "").strip() or None,
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
        if not payload.get("employee_number"):
            doc["employee_number"] = str(linked.get("employee_id") or doc["employee_number"])
    if not doc["name"]:
        raise HTTPException(400, "Nama karyawan wajib diisi.")
    try:
        await db[EMPLOYEES].insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "Nomor karyawan atau tautan user sudah dipakai.")
    return public_employee(doc)


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
    if "user_id" in payload:
        linked = await _safe_user(db, str(payload.get("user_id") or "").strip())
        if payload.get("user_id") and not linked:
            raise HTTPException(404, "User yang ditautkan tidak ditemukan.")
        if linked:
            updates.update({
                "name": str(linked.get("name") or updates.get("name") or existing.get("name") or ""),
                "email": str(linked.get("email") or updates.get("email") or existing.get("email") or ""),
                "role": str(linked.get("role") or updates.get("role") or existing.get("role") or ""),
            })
            if not existing.get("employee_number"):
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
    return public_employee(result)


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
    await get_period(db, period_id)
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


async def create_period(db, year: int, month: int, start_date: str, end_date: str, user: dict) -> dict:
    try:
        year, month = int(year), int(month)
    except (TypeError, ValueError):
        raise HTTPException(400, "Tahun dan bulan tidak valid.")
    if year < 2000 or year > 2200 or month < 1 or month > 12:
        raise HTTPException(400, "Tahun atau bulan tidak valid.")
    if await db[PERIODS].find_one({"year": year, "month": month}):
        raise HTTPException(409, "Periode tahun dan bulan tersebut sudah ada.")
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
        if period.get("status") != STATUS_DRAFT:
            raise HTTPException(409, "Perhitungan hanya dapat dilakukan pada status DRAFT.")
        employees = await db[EMPLOYEES].find({"active": {"$ne": False}}, {"_id": 0}).sort("employee_number", 1).to_list(5000)
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
    line[target].append({
        "code": "ADJUSTMENT",
        "name": str(adjustment.get("reason") or "Penyesuaian"),
        "amount": money_float(amount),
        "source": "adjustment",
        "adjustment_id": adjustment.get("id"),
    })
    gross = sum((to_money(row["amount"]) for row in line["earnings"]), Decimal("0"))
    deductions = sum((to_money(row["amount"]) for row in line["deductions"]), Decimal("0"))
    line.update({"gross": money_float(gross), "total_deductions": money_float(deductions), "net": money_float(gross - deductions)})


async def adjust_line(db, period_id: str, line_id: str, amount: Any, reason: str, kind: str, user: dict) -> dict:
    period = await get_period(db, period_id)
    if period.get("status") != STATUS_DRAFT:
        raise HTTPException(409, "Penyesuaian hanya dapat dilakukan pada status DRAFT.")
    if kind not in ("earning", "deduction"):
        raise HTTPException(400, "Jenis penyesuaian harus earning atau deduction.")
    value = to_money(amount)
    if value <= 0:
        raise HTTPException(400, "Nilai penyesuaian harus lebih besar dari 0.")
    if not str(reason or "").strip():
        raise HTTPException(400, "Alasan penyesuaian wajib diisi.")
    line = await get_line(db, line_id)
    if line.get("period_id") != period_id:
        raise HTTPException(404, "Baris payroll tidak ada pada periode ini.")
    adjustment = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "amount": money_float(value),
        "reason": str(reason).strip()[:1000],
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


async def transition_period(db, period_id: str, to_status: str, note: str, user: dict) -> dict:
    to_status = str(to_status or "").upper()
    async with get_named_lock(f"hr_payroll_transition:{period_id}"):
        period = await get_period(db, period_id)
        current = period.get("status")
        if to_status not in ALLOWED.get(current, []):
            raise HTTPException(409, f"Transisi {current} ke {to_status} tidak diizinkan.")
        if current == STATUS_DRAFT and not await db[LINES].find_one({"period_id": period_id}):
            raise HTTPException(409, "Periode harus dihitung sebelum masuk REVIEW.")
        events = list(period.get("audit_events") or [])
        events.append(_audit(f"transition_{to_status.lower()}", user, note))
        updates: Dict[str, Any] = {
            "status": to_status,
            "audit_events": events,
            "updated_at": now_iso(),
            "last_transition": {"from": current, "to": to_status, "note": str(note or "")[:1000], "actor": actor_snapshot(user), "at": now_iso()},
        }
        if to_status == STATUS_FINAL:
            lines = await db[LINES].find({"period_id": period_id}, {"_id": 0}).to_list(5000)
            if not lines:
                raise HTTPException(409, "Periode tidak memiliki baris payroll.")
            updates.update({
                "finalized_at": now_iso(),
                "finalized_by": actor_snapshot(user),
                "final_snapshot": {
                    "config": copy.deepcopy(period.get("config_snapshot") or {}),
                    "calculation": copy.deepcopy(period.get("calculation_snapshot") or {}),
                    "line_count": len(lines),
                },
                "frozen": True,
            })
            await db[LINES].update_many({"period_id": period_id}, {"$set": {"status": STATUS_FINAL, "finalized_at": now_iso()}})
            for line in lines:
                await generate_payslip_for_line(db, line["id"], period_override={**period, **updates})
            await _post_kasbon_payments(db, period_id)
        result = await db[PERIODS].find_one_and_update(
            {"id": period_id, "status": current},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        if not result:
            raise HTTPException(409, "Status periode telah berubah.")
        if to_status != STATUS_FINAL:
            await db[LINES].update_many({"period_id": period_id}, {"$set": {"status": to_status}})
        return public_period(result)


async def _post_kasbon_payments(db, period_id: str) -> None:
    schedules = await db[KASBON].find({"payments.period_id": period_id}, {"_id": 0}).to_list(5000)
    for schedule in schedules:
        payments = list(schedule.get("payments") or [])
        changed = False
        posted = Decimal("0")
        for payment in payments:
            if str(payment.get("period_id")) == period_id and payment.get("status") != "posted":
                payment["status"] = "posted"
                payment["posted_at"] = now_iso()
                posted += to_money(payment.get("amount"))
                changed = True
        if changed:
            outstanding = max(Decimal("0"), to_money(schedule.get("outstanding")) - posted)
            paid = to_money(schedule.get("paid_total")) + posted
            await db[KASBON].update_one(
                {"id": schedule["id"]},
                {"$set": {
                    "payments": payments,
                    "outstanding": money_float(outstanding),
                    "paid_total": money_float(paid),
                    "active": outstanding > 0,
                    "updated_at": now_iso(),
                }},
            )


async def reopen_period(db, period_id: str, reason: str, user: dict) -> dict:
    if not str(reason or "").strip():
        raise HTTPException(400, "Alasan buka ulang wajib diisi.")
    async with get_named_lock(f"hr_payroll_reopen:{period_id}"):
        old = await get_period(db, period_id)
        if old.get("status") != STATUS_FINAL:
            raise HTTPException(409, "Hanya periode FINAL yang dapat dibuka ulang.")
        revision = int(old.get("revision") or 1) + 1
        if await db[PERIODS].find_one({"group_id": old["group_id"], "revision": revision}):
            raise HTTPException(409, "Revisi lanjutan periode ini sudah ada.")
        doc = {
            **{k: copy.deepcopy(old.get(k)) for k in ("year", "month", "start_date", "end_date", "group_id", "config_snapshot", "config_version")},
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
            await db[PERIODS].insert_one(doc)
        except DuplicateKeyError:
            raise HTTPException(409, "Revisi periode sudah ada.")
        old_attendance = await db[ATTENDANCE].find({"period_id": period_id}, {"_id": 0}).to_list(5000)
        for item in old_attendance:
            item.update({"id": str(uuid.uuid4()), "period_id": doc["id"], "created_at": now_iso(), "updated_at": now_iso()})
        if old_attendance:
            await db[ATTENDANCE].insert_many(old_attendance)
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


async def generate_payslip_for_line(db, line_id: str, period_override: Optional[dict] = None) -> dict:
    async with get_named_lock(f"hr_payroll_payslip:{line_id}"):
        line = await get_line(db, line_id)
        existing = line.get("payslip") or {}
        if existing.get("file_id") and existing.get("generated"):
            return public_line(line)
        period = period_override or await get_period(db, line["period_id"])
        if period.get("status") != STATUS_FINAL:
            raise HTTPException(409, "Slip gaji hanya tersedia untuk periode FINAL.")
        data = generate_payslip_pdf_bytes(line, period)
        from services import storage as obj_storage
        file_id = str(uuid.uuid4())
        filename = f"{period.get('period_number')}-{(line.get('employee_snapshot') or {}).get('employee_number') or line['employee_id']}.pdf"
        storage_path = f"{obj_storage.APP_NAME}/hr-payroll/{period['id']}/{line_id}/{file_id}.pdf"
        obj_storage.put_object(storage_path, data, "application/pdf")
        payslip = {
            "generated": True,
            "file_id": file_id,
            "storage_path": storage_path,
            "content_type": "application/pdf",
            "filename": filename,
            "generated_at": now_iso(),
            "snapshot_version": int(line.get("calculation_version") or 1),
        }
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
        await db[LINES].update_one({"id": line_id, "payslip.file_id": {"$exists": False}}, {"$set": {"payslip": payslip, "updated_at": now_iso()}})
        line["payslip"] = payslip
        return public_line(line)


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
