"""Piece-wage claims, bonus-day ownership, and Holding kasbon reservations.

Python locks are not the cross-process guard. Wage sources and bonus days are
unique in Mongo while status is held or posted. Each calculation stamps
calc_attempt on rows it inserts or adopts. Cleanup deletes a held row, or
restores a kasbon reservation, only while that same attempt and period still
own it. A newer calculation that adopted the row is left in place. A hard
stop does not run this cleanup; the next calculation of the owning period
finishes the reservation.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from operator_analytics import wib_today
from services.hr_payroll import (
    EMPLOYEES,
    KASBON,
    LINES,
    PERIODS,
    STATUS_DRAFT,
    money_float,
    to_money,
)
from services.operator_payroll import (
    SETTINGS_KEY as WAGE_SETTINGS_KEY,
    compute_bonus,
    effective_bonus_as_of,
    normalize_wage_settings,
)
from services.purchase_common import actor_snapshot, now_iso

WAGE_CLAIMS = "hr_operator_wage_claims"
BONUS_DAYS = "hr_operator_bonus_days"
INTEGRATION_KEY = "operator_piece_integration"
HELD = "held"
POSTED = "posted"
ACTIVE_CLAIM = (HELD, POSTED)
SOURCE_PIECE = "operator_piece"
SOURCE_BONUS = "operator_bonus"
CODE_WAGE = "UPAH_BORONGAN"
CODE_BONUS = "BONUS_OPERATOR"
WAGE_INDEX = "uniq_operator_wage_source"
BONUS_INDEX = "uniq_operator_bonus_day"
MAX_RANGE_DAYS = 120


def _date(raw: Any, label: str) -> str:
    text = str(raw or "").strip()[:10]
    try:
        date.fromisoformat(text)
    except ValueError:
        raise HTTPException(400, f"{label} harus YYYY-MM-DD.")
    return text


def _span(start: str, end: str) -> List[str]:
    cur = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if last < cur:
        raise HTTPException(400, "Tanggal akhir tidak boleh lebih kecil dari tanggal mulai.")
    days = (last - cur).days + 1
    if days > MAX_RANGE_DAYS:
        raise HTTPException(400, f"Rentang maksimal {MAX_RANGE_DAYS} hari.")
    out = []
    while cur <= last:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


async def ensure_piece_indexes(db) -> None:
    await db[WAGE_CLAIMS].create_index("id", unique=True)
    await db[WAGE_CLAIMS].create_index(
        "source_key",
        unique=True,
        name=WAGE_INDEX,
        partialFilterExpression={"status": {"$in": list(ACTIVE_CLAIM)}},
    )
    await db[WAGE_CLAIMS].create_index([("period_id", 1), ("status", 1)])
    await db[BONUS_DAYS].create_index("id", unique=True)
    await db[BONUS_DAYS].create_index(
        [("employee_id", 1), ("bonus_date", 1)],
        unique=True,
        name=BONUS_INDEX,
        partialFilterExpression={"status": {"$in": list(ACTIVE_CLAIM)}},
    )
    await db[BONUS_DAYS].create_index([("period_id", 1), ("status", 1)])


async def get_integration(db) -> dict:
    row = await db.app_settings.find_one({"key": INTEGRATION_KEY}, {"_id": 0}) or {}
    value = row.get("value") if isinstance(row.get("value"), dict) else {}
    return {
        "start_date": str(value.get("start_date") or "")[:10],
        "locked": bool(value.get("locked")),
        "locked_at": value.get("locked_at"),
        "first_period_id": value.get("first_period_id"),
    }


async def set_integration_start(db, start_date: str, user: dict) -> dict:
    start = _date(start_date, "Batas mulai integrasi")
    current = await get_integration(db)
    if current.get("locked"):
        raise HTTPException(409, "Batas mulai integrasi sudah dikunci setelah akrual borongan pertama.")
    value = {
        "start_date": start,
        "locked": False,
        "updated_at": now_iso(),
        "updated_by": actor_snapshot(user),
    }
    await db.app_settings.update_one(
        {"key": INTEGRATION_KEY},
        {"$set": {"key": INTEGRATION_KEY, "value": value}},
        upsert=True,
    )
    return await get_integration(db)


async def lock_integration_after_accrual(db, period_id: str) -> None:
    """First stored piece accrual wins. Later writers do not move the boundary."""
    current = await get_integration(db)
    if current.get("locked") or not current.get("start_date"):
        return
    await db.app_settings.update_one(
        {"key": INTEGRATION_KEY, "value.locked": {"$ne": True}},
        {"$set": {
            "value.locked": True,
            "value.locked_at": now_iso(),
            "value.first_period_id": str(period_id),
        }},
    )


def _is_legacy(entry: dict) -> bool:
    if entry.get("legacy") is True:
        return True
    action = str(entry.get("action_id") or "")
    return action.startswith("legacy-") or entry.get("id") == "legacy"


def _entry_work_date(entry: dict) -> str:
    return str(entry.get("work_date") or "")[:10]


def _safe_qty(entry: dict) -> int:
    try:
        qty = int(entry.get("qty") or 0)
    except (TypeError, ValueError):
        return 0
    return qty if qty > 0 else 0


async def _employees_by_user(db) -> Dict[str, dict]:
    rows = await db[EMPLOYEES].find({"active": {"$ne": False}}, {"_id": 0}).to_list(5000)
    out: Dict[str, List[dict]] = {}
    for row in rows:
        uid = str(row.get("user_id") or "").strip()
        if uid:
            out.setdefault(uid, []).append(row)
    return out


async def _load_orders(db, start: str, end: str) -> List[dict]:
    """Orders with an in-window date, or an entry whose work date is missing.

    A dated entry that only sits outside the wage and bonus windows does not
    pull the order in by itself.
    """
    query = {"$or": [
        {"operator_splits.progress_entries.work_date": {"$gte": start, "$lte": end}},
        {"operator_splits.progress_entries": {"$elemMatch": {"$or": [
            {"work_date": {"$exists": False}},
            {"work_date": None},
            {"work_date": ""},
            {"work_date": {"$not": {"$regex": r"^\d{4}-\d{2}-\d{2}"}}},
        ]}}},
    ]}
    projection = {
        "_id": 0,
        "id": 1,
        "po_number": 1,
        "cancelled": 1,
        "operator_splits.id": 1,
        "operator_splits.operator_user_id": 1,
        "operator_splits.operator_name": 1,
        "operator_splits.progress_entries": 1,
    }
    return await db.orders.find(query, projection).to_list(5000)


def _walk_entries(orders: List[dict]):
    for order in orders:
        if not order.get("id"):
            continue
        for split in order.get("operator_splits") or []:
            if not isinstance(split, dict) or not split.get("id"):
                continue
            for entry in split.get("progress_entries") or []:
                if isinstance(entry, dict):
                    yield order, split, entry


def _classify(order, split, entry, integration_start: str) -> Tuple[str, str]:
    """Return (kind, reason). kind is wage, block, or skip."""
    if order.get("cancelled") is True:
        return "skip", "order dibatalkan"
    if entry.get("cancelled") is True or entry.get("replaced") is True:
        return "skip", "entri dibatalkan atau diganti"
    if _is_legacy(entry):
        return "skip", "sumber legacy"
    qty = _safe_qty(entry)
    if qty <= 0:
        return "skip", "qty tidak positif"
    action = str(entry.get("action_id") or "").strip()
    if not action or not entry.get("po_id") and not order.get("id"):
        return "skip", "identitas sumber tidak lengkap"
    if not str(entry.get("split_id") or split.get("id") or "").strip():
        return "skip", "split tidak lengkap"
    work_date = _entry_work_date(entry)
    if len(work_date) != 10:
        return "skip", "tanpa tanggal pekerjaan"
    if integration_start and work_date < integration_start:
        return "skip", "sebelum batas integrasi"
    uid = str(entry.get("operator_user_id") or split.get("operator_user_id") or "").strip()
    if not uid:
        return "block", "operator_user_id kosong, tidak ditautkan lewat nama atau email"
    entry_uid = str(entry.get("operator_user_id") or "").strip()
    split_uid = str(split.get("operator_user_id") or "").strip()
    if entry_uid and split_uid and entry_uid != split_uid:
        return "block", "identitas split berubah sesudah konfirmasi"
    return "ok", ""


def _source_from(order, split, entry) -> dict:
    uid = str(entry.get("operator_user_id") or split.get("operator_user_id") or "").strip()
    order_id = str(entry.get("po_id") or order.get("id"))
    split_id = str(entry.get("split_id") or split.get("id"))
    action_id = str(entry.get("action_id"))
    rate = entry.get("rate_per_pcs")
    qty = _safe_qty(entry)
    amount = Decimal("0.00")
    if rate not in (None, ""):
        amount = (Decimal(qty) * to_money(rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "source_key": f"{order_id}:{split_id}:{action_id}",
        "order_id": order_id,
        "split_id": split_id,
        "action_id": action_id,
        "po_number": order.get("po_number") or "",
        "operator_user_id": uid,
        "operator_name": split.get("operator_name") or "",
        "work_date": _entry_work_date(entry),
        "qty": qty,
        "rate_per_pcs": None if rate in (None, "") else money_float(rate),
        "amount": money_float(amount),
        "has_rate": rate not in (None, ""),
    }


async def collect_outputs(db, *, wage_start: str, wage_end: str, bonus_start: str, bonus_end: str, integration_start: str) -> dict:
    window_start = min(wage_start, bonus_start)
    window_end = max(wage_end, bonus_end)
    orders = await _load_orders(db, window_start, window_end)
    people = await _employees_by_user(db)
    exclusions: List[dict] = []
    blockers: List[dict] = []
    wage_rows: List[dict] = []
    bonus_rows: List[dict] = []
    seen = set()
    for order, split, entry in _walk_entries(orders):
        work_date = _entry_work_date(entry)
        in_wage = bool(work_date) and wage_start <= work_date <= wage_end
        in_bonus = bool(work_date) and bonus_start <= work_date <= bonus_end
        if not work_date:
            kind, reason = _classify(order, split, entry, integration_start)
            source = _source_from(order, split, entry)
            if source["source_key"] in seen:
                continue
            seen.add(source["source_key"])
            item = {**source, "reason": reason or "tanpa tanggal pekerjaan", "in_wage_range": False, "in_bonus_range": False}
            if kind == "block":
                blockers.append(item)
            else:
                exclusions.append(item)
            continue
        if not in_wage and not in_bonus:
            kind, reason = _classify(order, split, entry, integration_start)
            if reason in (
                "order dibatalkan",
                "entri dibatalkan atau diganti",
                "qty tidak positif",
                "identitas sumber tidak lengkap",
                "split tidak lengkap",
            ):
                continue
            source = _source_from(order, split, entry)
            if source["source_key"] in seen:
                continue
            seen.add(source["source_key"])
            outside_reason = reason if reason == "sumber legacy" else "di luar rentang upah dan bonus"
            exclusions.append({
                **source,
                "reason": outside_reason,
                "in_wage_range": False,
                "in_bonus_range": False,
            })
            continue
        kind, reason = _classify(order, split, entry, integration_start)
        source = _source_from(order, split, entry)
        if source["source_key"] in seen:
            continue
        seen.add(source["source_key"])
        if kind == "skip":
            if in_wage or in_bonus:
                exclusions.append({**source, "reason": reason, "in_wage_range": in_wage, "in_bonus_range": in_bonus})
            continue
        if kind == "block":
            blockers.append({**source, "reason": reason})
            continue
        uid = source["operator_user_id"]
        matches = people.get(uid) or []
        if len(matches) != 1:
            blockers.append({**source, "reason": "belum terhubung ke satu profil HR"})
            continue
        employee = matches[0]
        if str(employee.get("pay_basis") or "monthly") != "piece":
            exclusions.append({**source, "reason": "profil bukan pay_basis piece", "employee_id": employee["id"]})
            continue
        tagged = {**source, "employee_id": employee["id"], "employee_name": employee.get("name") or ""}
        if in_bonus:
            bonus_rows.append(tagged)
        if in_wage:
            if not tagged["has_rate"]:
                exclusions.append({**tagged, "reason": "tanpa snapshot tarif"})
            else:
                wage_rows.append(tagged)
    return {
        "wage": wage_rows,
        "bonus": bonus_rows,
        **_capped_lists(exclusions, blockers),
    }


def _capped_lists(exclusions: List[dict], blockers: List[dict], cap: int = 200) -> dict:
    def pack(rows: List[dict], count_key: str, qty_key: str, list_key: str) -> dict:
        shown = rows[:cap]
        hidden = rows[cap:]
        return {
            list_key: shown,
            count_key: len(hidden),
            qty_key: sum(int(row.get("qty") or 0) for row in hidden),
        }

    return {
        **pack(exclusions, "omitted_exclusions", "omitted_exclusion_qty", "exclusions"),
        **pack(blockers, "omitted_blockers", "omitted_blocker_qty", "blockers"),
    }


def _stored_source_lists(found: dict) -> dict:
    return {
        "piece_blockers": found["blockers"],
        "piece_exclusions": found["exclusions"],
        "piece_omitted_exclusions": int(found.get("omitted_exclusions") or 0),
        "piece_omitted_exclusion_qty": int(found.get("omitted_exclusion_qty") or 0),
        "piece_omitted_blockers": int(found.get("omitted_blockers") or 0),
        "piece_omitted_blocker_qty": int(found.get("omitted_blocker_qty") or 0),
    }


async def _period_number(db, period_id: str) -> str:
    row = await db[PERIODS].find_one({"id": period_id}, {"_id": 0, "period_number": 1})
    return str((row or {}).get("period_number") or period_id)


async def _claim_wage(db, period: dict, source: dict, attempt_id: str) -> Tuple[str, dict]:
    for _ in range(2):
        doc = {
            "id": str(uuid.uuid4()),
            "source_key": source["source_key"],
            "order_id": source["order_id"],
            "split_id": source["split_id"],
            "action_id": source["action_id"],
            "period_id": period["id"],
            "group_id": period.get("group_id"),
            "employee_id": source["employee_id"],
            "work_date": source["work_date"],
            "qty": source["qty"],
            "rate_per_pcs": source["rate_per_pcs"],
            "amount": source["amount"],
            "operator_user_id": source["operator_user_id"],
            "status": HELD,
            "calc_attempt": attempt_id,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        try:
            await db[WAGE_CLAIMS].insert_one(dict(doc))
            return "inserted", doc
        except DuplicateKeyError:
            existing = await db[WAGE_CLAIMS].find_one(
                {"source_key": source["source_key"], "status": {"$in": list(ACTIVE_CLAIM)}},
                {"_id": 0},
            )
            if not existing:
                continue
            same = existing.get("period_id") == period["id"] or (
                existing.get("group_id") and existing.get("group_id") == period.get("group_id") and existing.get("status") == HELD
            )
            if same and existing.get("status") == HELD and existing.get("period_id") == period["id"]:
                adopted = await db[WAGE_CLAIMS].find_one_and_update(
                    {"id": existing["id"], "status": HELD, "period_id": period["id"]},
                    {"$set": {
                        "qty": source["qty"],
                        "rate_per_pcs": source["rate_per_pcs"],
                        "amount": source["amount"],
                        "employee_id": source["employee_id"],
                        "work_date": source["work_date"],
                        "operator_user_id": source["operator_user_id"],
                        "calc_attempt": attempt_id,
                        "updated_at": now_iso(),
                    }},
                    return_document=ReturnDocument.AFTER,
                )
                if adopted:
                    return "owned", adopted
                continue
            if same:
                return "owned", existing
            return "conflict", existing
    raise HTTPException(409, "Klaim upah belum pulih. Hitung ulang periode ini.")


async def _rollback_wage(db, ids: List[str], attempt_id: str, period_id: str) -> None:
    if ids:
        await db[WAGE_CLAIMS].delete_many({
            "id": {"$in": ids},
            "status": HELD,
            "calc_attempt": attempt_id,
            "period_id": period_id,
        })


async def _claim_bonus_day(db, period: dict, employee_id: str, bonus_date: str, attempt_id: str) -> Tuple[str, dict]:
    for _ in range(2):
        doc = {
            "id": str(uuid.uuid4()),
            "employee_id": employee_id,
            "bonus_date": bonus_date,
            "period_id": period["id"],
            "group_id": period.get("group_id"),
            "status": HELD,
            "calc_attempt": attempt_id,
            "created_at": now_iso(),
        }
        try:
            await db[BONUS_DAYS].insert_one(dict(doc))
            return "inserted", doc
        except DuplicateKeyError:
            existing = await db[BONUS_DAYS].find_one(
                {"employee_id": employee_id, "bonus_date": bonus_date, "status": {"$in": list(ACTIVE_CLAIM)}},
                {"_id": 0},
            )
            if not existing:
                continue
            owned = existing.get("period_id") == period["id"] or existing.get("group_id") == period.get("group_id")
            if owned and existing.get("status") == HELD:
                adopted = await db[BONUS_DAYS].find_one_and_update(
                    {"id": existing["id"], "status": HELD},
                    {"$set": {
                        "period_id": period["id"],
                        "calc_attempt": attempt_id,
                        "updated_at": now_iso(),
                    }},
                    return_document=ReturnDocument.AFTER,
                )
                if adopted:
                    return "owned", adopted
                continue
            if owned:
                return "owned", existing
            return "conflict", existing or {}
    raise HTTPException(409, "Reservasi bonus belum pulih. Hitung ulang periode ini.")


async def _rollback_days(db, ids: List[str], attempt_id: str, period_id: str) -> None:
    if ids:
        await db[BONUS_DAYS].delete_many({
            "id": {"$in": ids},
            "status": HELD,
            "calc_attempt": attempt_id,
            "period_id": period_id,
        })


def _other_reserved_expr(period_id: str) -> dict:
    return {"$sum": {"$map": {
        "input": {"$ifNull": ["$payments", []]},
        "as": "p",
        "in": {"$cond": [
            {"$and": [
                {"$eq": ["$$p.status", "pending"]},
                {"$ne": ["$$p.period_id", period_id]},
            ]},
            {"$ifNull": ["$$p.amount", 0]},
            0,
        ]},
    }}}


def _period_pending_attempt_expr(period_id: str) -> dict:
    return {"$let": {
        "vars": {
            "mine": {"$filter": {
                "input": {"$ifNull": ["$payments", []]},
                "as": "p",
                "cond": {"$and": [
                    {"$eq": ["$$p.status", "pending"]},
                    {"$eq": ["$$p.period_id", period_id]},
                ]},
            }},
        },
        "in": {"$let": {
            "vars": {"row": {"$first": "$$mine"}},
            "in": {"$ifNull": ["$$row.calc_attempt", None]},
        }},
    }}


async def reserve_kasbon(
    db,
    schedule_id: str,
    period_id: str,
    amount: Decimal,
    payment: dict,
    attempt_id: Optional[str] = None,
    only_if_attempt: Optional[str] = None,
) -> dict:
    """Replace this period's pending row only when amount fits outstanding minus other drafts.

    only_if_attempt leaves the row untouched when a newer calculation already
    replaced it. That is how a late rollback cannot erase the winner.
    """
    amount_f = money_float(amount)
    payment_doc = {**payment, "period_id": period_id, "amount": amount_f, "status": "pending"}
    if attempt_id:
        payment_doc["calc_attempt"] = attempt_id
    keep_others = {"$filter": {
        "input": {"$ifNull": ["$payments", []]},
        "as": "p",
        "cond": {"$not": {"$and": [
            {"$eq": ["$$p.period_id", period_id]},
            {"$eq": ["$$p.status", "pending"]},
        ]}},
    }}
    appended = keep_others if amount <= 0 else {"$concatArrays": [keep_others, [payment_doc]]}
    guard = True if only_if_attempt is None else {"$eq": ["$_current_attempt", only_if_attempt]}
    result = await db[KASBON].find_one_and_update(
        {"id": schedule_id},
        [{"$set": {"_other_reserved": _other_reserved_expr(period_id)}},
        {"$set": {"_available": {"$subtract": [{"$ifNull": ["$outstanding", 0]}, "$_other_reserved"]}}},
        {"$set": {"_current_attempt": _period_pending_attempt_expr(period_id)}},
        {"$set": {"_guard": guard}},
        {"$set": {"_accept": {"$and": ["$_guard", {"$lte": [amount_f, "$_available"]}]}}},
        {"$set": {
            "payments": {"$cond": ["$_accept", appended, "$payments"]},
            "piece_reserve_ok": {"$or": ["$_accept", {"$not": ["$_guard"]}]},
            "piece_reserve_skipped": {"$not": ["$_guard"]},
            "updated_at": now_iso(),
        }},
        {"$unset": ["_other_reserved", "_available", "_current_attempt", "_guard", "_accept"]}],
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise HTTPException(404, "Jadwal kasbon tidak ditemukan.")
    ok = bool(result.get("piece_reserve_ok"))
    skipped = bool(result.get("piece_reserve_skipped"))
    await db[KASBON].update_one(
        {"id": schedule_id},
        {"$unset": {"piece_reserve_ok": "", "piece_reserve_skipped": ""}},
    )
    available = to_money(result.get("outstanding")) - to_money(sum(
        to_money(p.get("amount"))
        for p in (result.get("payments") or [])
        if p.get("status") == "pending" and str(p.get("period_id")) != str(period_id)
    ))
    return {"ok": ok, "skipped": skipped, "available": available if available > 0 else Decimal("0.00")}


async def _pending_row(db, schedule_id: str, period_id: str) -> dict:
    row = await db[KASBON].find_one({"id": schedule_id}, {"_id": 0, "payments": 1})
    for payment in (row or {}).get("payments") or []:
        if str(payment.get("period_id")) == str(period_id) and payment.get("status") == "pending":
            return payment
    return {}


async def _pending_amount(db, schedule_id: str, period_id: str) -> Decimal:
    return to_money((await _pending_row(db, schedule_id, period_id)).get("amount"))


def _inputs_by_schedule(period: dict) -> Dict[str, dict]:
    out = {}
    for row in period.get("piece_kasbon_inputs") or []:
        if isinstance(row, dict) and row.get("schedule_id"):
            out[str(row["schedule_id"])] = row
    return out


def _adjustments(period: dict) -> Dict[str, dict]:
    raw = ((period.get("piece_bonus") or {}).get("adjustments")) or {}
    return raw if isinstance(raw, dict) else {}


async def _active_schedules(db, employee_id: str) -> List[dict]:
    return await db[KASBON].find(
        {"employee_id": employee_id, "active": {"$ne": False}, "outstanding": {"$gt": 0}},
        {"_id": 0},
    ).sort("created_at", 1).to_list(100)


def _bonus_snapshot(period: dict, employee_id: str, qty: int, proposed: Decimal, config: Optional[dict], user: dict) -> dict:
    bonus = period.get("piece_bonus") or {}
    adj = _adjustments(period).get(employee_id) or {}
    has_final = adj.get("final_amount") not in (None, "")
    final = to_money(adj.get("final_amount")) if has_final else proposed
    reason = str(adj.get("reason") or "").strip()
    if final != proposed and not reason:
        raise HTTPException(409, "Alasan wajib diisi bila nominal bonus berbeda dari usulan.")
    if final < 0:
        raise HTTPException(400, "Nominal bonus tidak boleh negatif.")
    return {
        "start_date": bonus.get("start_date"),
        "end_date": bonus.get("end_date"),
        "base_qty": qty,
        "config": {
            "mode": (config or {}).get("mode"),
            "target_qty": (config or {}).get("target_qty"),
            "amount": (config or {}).get("amount"),
            "active": (config or {}).get("active", True),
        },
        "proposed_amount": money_float(proposed),
        "final_amount": money_float(final),
        "reason": reason,
        "actor": adj.get("actor") or actor_snapshot(user),
        "decided_at": adj.get("decided_at") or now_iso(),
    }


async def calculate_piece_period(db, period: dict, user: dict) -> dict:
    from services.hr_payroll import public_line, public_period

    await ensure_piece_indexes(db)
    if str(period.get("period_scope") or "") != "piece":
        raise HTTPException(409, "Periode ini bukan payroll borongan.")
    if period.get("status") != STATUS_DRAFT:
        raise HTTPException(409, "Perhitungan borongan hanya pada status DRAFT.")
    integration = await get_integration(db)
    if not integration.get("start_date"):
        raise HTTPException(409, "Batas mulai integrasi belum diisi Holding.")
    bonus = period.get("piece_bonus") or {}
    wage_start = _date(period.get("start_date"), "Tanggal mulai upah")
    wage_end = _date(period.get("end_date"), "Tanggal akhir upah")
    if wage_end < wage_start:
        raise HTTPException(400, "Tanggal akhir upah tidak boleh lebih kecil dari tanggal mulai.")
    bonus_start = _date(bonus.get("start_date"), "Tanggal mulai bonus")
    bonus_end = _date(bonus.get("end_date"), "Tanggal akhir bonus")
    _span(wage_start, wage_end)
    bonus_days = _span(bonus_start, bonus_end)
    found = await collect_outputs(
        db,
        wage_start=wage_start,
        wage_end=wage_end,
        bonus_start=bonus_start,
        bonus_end=bonus_end,
        integration_start=integration["start_date"],
    )
    if found["blockers"]:
        names = ", ".join(sorted({b.get("operator_name") or b.get("operator_user_id") or "?" for b in found["blockers"]})[:8])
        await db[PERIODS].update_one(
            {"id": period["id"], "status": STATUS_DRAFT},
            {"$set": {**_stored_source_lists(found), "updated_at": now_iso()}},
        )
        raise HTTPException(409, f"Operator belum terhubung ke profil HR: {names}. Sumber itu tidak dihitung.")

    inserted_claims: List[str] = []
    inserted_days: List[str] = []
    owned_keys = []
    conflicts = []
    durable = False
    attempt_id = str(uuid.uuid4())
    try:
        for source in found["wage"]:
            state, row = await _claim_wage(db, period, source, attempt_id)
            if state == "inserted":
                inserted_claims.append(row["id"])
                owned_keys.append(source["source_key"])
            elif state == "owned":
                owned_keys.append(source["source_key"])
            else:
                owner = await _period_number(db, str(row.get("period_id") or ""))
                conflicts.append(f"{source['source_key']} milik {owner}")
        if conflicts:
            await _rollback_wage(db, inserted_claims, attempt_id, period["id"])
            inserted_claims = []
            raise HTTPException(409, "Qty sudah dimiliki payroll lain: " + "; ".join(conflicts[:8]))

        employees: Dict[str, dict] = {}
        for source in found["wage"]:
            employees.setdefault(source["employee_id"], {"employee_id": source["employee_id"], "name": source.get("employee_name") or "", "wage": [], "bonus_qty": 0})
            employees[source["employee_id"]]["wage"].append(source)
        for source in found["bonus"]:
            employees.setdefault(source["employee_id"], {"employee_id": source["employee_id"], "name": source.get("employee_name") or "", "wage": [], "bonus_qty": 0})
            employees[source["employee_id"]]["bonus_qty"] += int(source["qty"])

        for employee_id in employees:
            for day in bonus_days:
                state, row = await _claim_bonus_day(db, period, employee_id, day, attempt_id)
                if state == "inserted":
                    inserted_days.append(row["id"])
                elif state == "conflict":
                    owner = await _period_number(db, str((row or {}).get("period_id") or ""))
                    await _rollback_days(db, inserted_days, attempt_id, period["id"])
                    await _rollback_wage(db, inserted_claims, attempt_id, period["id"])
                    inserted_days = []
                    inserted_claims = []
                    raise HTTPException(409, f"Rentang bonus bertabrakan dengan periode {owner} pada {day}.")

        wage_doc = await db.app_settings.find_one({"key": WAGE_SETTINGS_KEY}, {"_id": 0}) or {}
        bonus_cfg = effective_bonus_as_of(date.fromisoformat(bonus_end), normalize_wage_settings(wage_doc))
        snapshots = {}
        for employee_id, bucket in employees.items():
            proposed = to_money(compute_bonus(bucket["bonus_qty"], bonus_cfg))
            snapshots[employee_id] = _bonus_snapshot(period, employee_id, bucket["bonus_qty"], proposed, bonus_cfg, user)

        inputs = _inputs_by_schedule(period)
        reservations = []
        previous = []
        for employee_id, bucket in employees.items():
            gross = sum((to_money(row["amount"]) for row in bucket["wage"]), Decimal("0")) + to_money(snapshots[employee_id]["final_amount"])
            schedules = await _active_schedules(db, employee_id)
            chosen = []
            for schedule in schedules:
                raw = inputs.get(schedule["id"])
                if raw is None or raw.get("amount") in (None, ""):
                    await _rollback_days(db, inserted_days, attempt_id, period["id"])
                    await _rollback_wage(db, inserted_claims, attempt_id, period["id"])
                    raise HTTPException(409, f"Nominal potongan kasbon {schedule.get('request_number') or schedule['id']} belum diisi Holding.")
                if str(raw.get("employee_id") or employee_id) not in ("", employee_id):
                    raise HTTPException(409, "Input kasbon tidak sesuai operator.")
                amount = to_money(raw.get("amount"))
                if amount < 0:
                    raise HTTPException(400, "Potongan kasbon tidak boleh negatif.")
                chosen.append((schedule, amount))
            total_cut = sum((amount for _s, amount in chosen), Decimal("0"))
            if total_cut > gross:
                await _rollback_days(db, inserted_days, attempt_id, period["id"])
                await _rollback_wage(db, inserted_claims, attempt_id, period["id"])
                raise HTTPException(
                    409,
                    f"Potongan kasbon {money_float(total_cut)} membuat net negatif. Maksimum {money_float(gross)} agar net tetap nol.",
                )
            for schedule, amount in chosen:
                prev = await _pending_row(db, schedule["id"], period["id"])
                previous.append((schedule, to_money(prev.get("amount")), prev.get("calc_attempt")))
                payment = {
                    "schedule_id": schedule["id"],
                    "request_id": schedule.get("request_id"),
                    "request_number": schedule.get("request_number"),
                    "created_at": now_iso(),
                }
                reserved = await reserve_kasbon(
                    db, schedule["id"], period["id"], amount, payment, attempt_id=attempt_id,
                )
                reservations.append((schedule, amount))
                if not reserved["ok"]:
                    for sched, old, old_attempt in previous:
                        pay = {
                            "schedule_id": sched["id"],
                            "request_id": sched.get("request_id"),
                            "request_number": sched.get("request_number"),
                            "created_at": now_iso(),
                        }
                        await reserve_kasbon(
                            db, sched["id"], period["id"], old, pay,
                            attempt_id=str(old_attempt) if old_attempt else None,
                            only_if_attempt=attempt_id,
                        )
                    await _rollback_days(db, inserted_days, attempt_id, period["id"])
                    await _rollback_wage(db, inserted_claims, attempt_id, period["id"])
                    raise HTTPException(
                        409,
                        f"Potongan {money_float(amount)} melebihi outstanding tersedia {money_float(reserved['available'])} pada {schedule.get('request_number') or schedule['id']}. Nominal tidak diperkecil.",
                    )

        old_lines = await db[LINES].find({"period_id": period["id"]}, {"_id": 0, "id": 1, "employee_id": 1}).to_list(5000)
        old_ids = {row["employee_id"]: row["id"] for row in old_lines}
        docs = []
        for employee_id, bucket in employees.items():
            emp = await db[EMPLOYEES].find_one({"id": employee_id}, {"_id": 0})
            wage_amount = sum((to_money(row["amount"]) for row in bucket["wage"]), Decimal("0"))
            snap = snapshots[employee_id]
            earnings = []
            if wage_amount > 0:
                earnings.append({
                    "code": CODE_WAGE,
                    "name": "Upah Borongan Operator",
                    "amount": money_float(wage_amount),
                    "source": SOURCE_PIECE,
                    "claim_ids": [row["source_key"] for row in bucket["wage"]],
                    "sources": bucket["wage"],
                })
            if to_money(snap["final_amount"]) > 0 or snap.get("proposed_amount") is not None:
                earnings.append({
                    "code": CODE_BONUS,
                    "name": "Bonus Operator",
                    "amount": money_float(snap["final_amount"]),
                    "source": SOURCE_BONUS,
                    "bonus_snapshot": snap,
                })
            deductions = []
            kasbon_snapshot = []
            for schedule, amount in reservations:
                if schedule.get("employee_id") != employee_id or amount <= 0:
                    continue
                deductions.append({
                    "code": "KASBON",
                    "name": f"Kasbon {schedule.get('request_number') or ''}".strip(),
                    "amount": money_float(amount),
                    "source": "kasbon",
                    "schedule_id": schedule["id"],
                })
                kasbon_snapshot.append({"schedule_id": schedule["id"], "amount": money_float(amount), "payment_status": "pending"})
            gross = sum((to_money(row["amount"]) for row in earnings), Decimal("0"))
            total_deduction = sum((to_money(row["amount"]) for row in deductions), Decimal("0"))
            docs.append({
                "id": old_ids.get(employee_id) or str(uuid.uuid4()),
                "period_id": period["id"],
                "employee_id": employee_id,
                "revision": int(period.get("revision") or 1),
                "period_revision": int(period.get("revision") or 1),
                "calculation_version": int(period.get("calculation_version") or 0) + 1,
                "status": period["status"],
                "earnings": earnings,
                "deductions": deductions,
                "gross": money_float(gross),
                "total_deductions": money_float(total_deduction),
                "net": money_float(gross - total_deduction),
                "kasbon_snapshot": kasbon_snapshot,
                "employee_snapshot": emp or {"id": employee_id, "name": bucket["name"], "pay_basis": "piece"},
                "piece_sources": bucket["wage"],
                "bonus_snapshot": snap,
                "adjustments": [],
                "audit_events": [],
                "payslip": None,
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "created_by": actor_snapshot(user),
            })
        for doc in docs:
            await db[LINES].replace_one(
                {"period_id": period["id"], "employee_id": doc["employee_id"], "revision": doc["revision"]},
                doc,
                upsert=True,
            )
        durable = True
        keep = [doc["employee_id"] for doc in docs]
        await db[LINES].delete_many({"period_id": period["id"], "employee_id": {"$nin": keep}})
        stale = await db[WAGE_CLAIMS].find(
            {"period_id": period["id"], "status": HELD, "source_key": {"$nin": owned_keys}},
            {"_id": 0, "id": 1},
        ).to_list(5000)
        if stale:
            await db[WAGE_CLAIMS].update_many(
                {"id": {"$in": [row["id"] for row in stale]}, "status": HELD, "period_id": period["id"]},
                {"$set": {"status": "released", "released_reason": "di luar hitungan terakhir", "updated_at": now_iso()}},
            )
        await db[BONUS_DAYS].update_many(
            {"period_id": period["id"], "status": HELD, "bonus_date": {"$nin": bonus_days}},
            {"$set": {"status": "released", "released_reason": "di luar rentang bonus"}},
        )
        await db[BONUS_DAYS].update_many(
            {"period_id": period["id"], "status": HELD, "employee_id": {"$nin": list(employees)}},
            {"$set": {"status": "released", "released_reason": "operator tidak masuk hitungan"}},
        )
        reserved_ids = {schedule["id"] for schedule, _amount in reservations}
        leftover = await db[KASBON].find({"payments.period_id": period["id"]}, {"_id": 0}).to_list(500)
        for schedule in leftover:
            if schedule["id"] in reserved_ids:
                continue
            prev = await _pending_amount(db, schedule["id"], period["id"])
            if prev > 0:
                await reserve_kasbon(db, schedule["id"], period["id"], Decimal("0.00"), {
                    "schedule_id": schedule["id"],
                    "request_id": schedule.get("request_id"),
                    "request_number": schedule.get("request_number"),
                    "created_at": now_iso(),
                })
        await db[PERIODS].update_one(
            {"id": period["id"], "status": STATUS_DRAFT},
            {"$set": {
                **_stored_source_lists({**found, "blockers": []}),
                "piece_sources": found["wage"],
                "calculation_version": int(period.get("calculation_version") or 0) + 1,
                "calculated_at": now_iso(),
                "calculated_by": actor_snapshot(user),
                "updated_at": now_iso(),
            }},
        )
    except HTTPException:
        if not durable:
            await _rollback_days(db, inserted_days, attempt_id, period["id"])
            await _rollback_wage(db, inserted_claims, attempt_id, period["id"])
        raise
    except Exception:
        if not durable:
            await _rollback_days(db, inserted_days, attempt_id, period["id"])
            await _rollback_wage(db, inserted_claims, attempt_id, period["id"])
        raise
    fresh = await db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
    lines = await db[LINES].find({"period_id": period["id"]}, {"_id": 0}).to_list(5000)
    return {"period": public_period(fresh), "lines": [public_line(row) for row in lines]}


async def assert_piece_ready(db, period: dict) -> None:
    """Re-read sources before a new journal. Refuse when the live output moved."""
    if str(period.get("period_scope") or "") != "piece":
        return
    integration = await get_integration(db)
    bonus = period.get("piece_bonus") or {}
    if not bonus.get("end_date"):
        raise HTTPException(409, "Rentang bonus belum diisi.")
    end = _date(bonus.get("end_date"), "Tanggal akhir bonus")
    if end >= wib_today().isoformat():
        raise HTTPException(409, "Rentang bonus harus sudah selesai sebelum finalisasi.")
    blockers = period.get("piece_blockers") or []
    if blockers:
        raise HTTPException(409, "Masih ada operator yang belum terhubung ke profil HR.")
    lines = await db[LINES].find({"period_id": period["id"]}, {"_id": 0}).to_list(5000)
    if not lines:
        raise HTTPException(409, "Periode borongan belum dihitung.")
    found = await collect_outputs(
        db,
        wage_start=_date(period.get("start_date"), "Tanggal mulai upah"),
        wage_end=_date(period.get("end_date"), "Tanggal akhir upah"),
        bonus_start=_date(bonus.get("start_date"), "Tanggal mulai bonus"),
        bonus_end=end,
        integration_start=integration.get("start_date") or "0001-01-01",
    )
    if found["blockers"]:
        raise HTTPException(409, "Sumber berubah: ada operator yang belum terhubung. Hitung ulang sebelum finalisasi.")
    live = {row["source_key"]: to_money(row["amount"]) for row in found["wage"]}
    stored = {}
    for line in lines:
        for source in line.get("piece_sources") or []:
            stored[source["source_key"]] = to_money(source.get("amount"))
    if live.keys() != stored.keys() or any(live[key] != stored[key] for key in live):
        raise HTTPException(409, "Output atau tarif berubah sejak hitungan terakhir. Hitung ulang sebelum finalisasi.")
    claims = await db[WAGE_CLAIMS].find(
        {"period_id": period["id"], "status": {"$in": list(ACTIVE_CLAIM)}},
        {"_id": 0, "source_key": 1},
    ).to_list(5000)
    if {row["source_key"] for row in claims} != set(stored):
        raise HTTPException(409, "Klaim upah tidak sama dengan baris payroll. Hitung ulang sebelum finalisasi.")
    bonus_start = _date(bonus.get("start_date"), "Tanggal mulai bonus")
    bonus_days = _span(bonus_start, end)
    employee_ids = sorted({str(line.get("employee_id") or "") for line in lines if line.get("employee_id")})
    reserved_days = await db[BONUS_DAYS].count_documents({
        "period_id": period["id"],
        "status": {"$in": list(ACTIVE_CLAIM)},
        "employee_id": {"$in": employee_ids},
        "bonus_date": {"$in": bonus_days},
    })
    if reserved_days < len(employee_ids) * len(bonus_days):
        raise HTTPException(409, "Reservasi bonus belum lengkap. Hitung ulang sebelum finalisasi.")
    for line in lines:
        gross = to_money(line.get("gross"))
        total_deduction = to_money(line.get("total_deductions"))
        net = to_money(line.get("net"))
        if total_deduction > gross or net < 0:
            raise HTTPException(
                409,
                "Potongan membuat net negatif. Nominal tidak diperkecil. Hitung ulang sebelum finalisasi.",
            )
        for deduction in line.get("deductions") or []:
            if str(deduction.get("code") or "") != "KASBON" and str(deduction.get("source") or "") != "kasbon":
                continue
            amount = to_money(deduction.get("amount"))
            if amount <= 0:
                continue
            schedule = await db[KASBON].find_one(
                {"id": deduction.get("schedule_id")},
                {"_id": 0, "outstanding": 1, "payments": 1},
            )
            if not schedule:
                raise HTTPException(409, "Jadwal kasbon tidak ditemukan. Hitung ulang sebelum finalisasi.")
            other = Decimal("0.00")
            for payment in schedule.get("payments") or []:
                if payment.get("status") == "pending" and str(payment.get("period_id")) != str(period["id"]):
                    other += to_money(payment.get("amount"))
            available = to_money(schedule.get("outstanding")) - other
            if amount > available:
                raise HTTPException(
                    409,
                    f"Potongan {money_float(amount)} melebihi outstanding tersedia {money_float(available if available > 0 else 0)}. Nominal tidak diperkecil.",
                )


async def freeze_piece_after_journal(db, period_id: str) -> None:
    now = now_iso()
    await db[WAGE_CLAIMS].update_many(
        {"period_id": period_id, "status": HELD},
        {"$set": {"status": POSTED, "posted_at": now}},
    )
    await db[BONUS_DAYS].update_many(
        {"period_id": period_id, "status": HELD},
        {"$set": {"status": POSTED, "posted_at": now}},
    )
    await lock_integration_after_accrual(db, period_id)


async def transfer_piece_ownership(db, old: dict, new_period_id: str) -> None:
    """Move held claims and pending kasbon onto the revision. Do not release first."""
    if str(old.get("period_scope") or "") != "piece":
        return
    now = now_iso()
    await db[WAGE_CLAIMS].update_many(
        {"period_id": old["id"], "group_id": old.get("group_id"), "status": HELD},
        {"$set": {"period_id": new_period_id, "updated_at": now}},
    )
    await db[BONUS_DAYS].update_many(
        {"period_id": old["id"], "group_id": old.get("group_id"), "status": HELD},
        {"$set": {"period_id": new_period_id}},
    )
    schedules = await db[KASBON].find({"payments.period_id": old["id"]}, {"_id": 0, "id": 1}).to_list(500)
    for schedule in schedules:
        await db[KASBON].update_one(
            {
                "id": schedule["id"],
                "payments": {"$elemMatch": {"period_id": old["id"], "status": "pending"}},
                "payments.period_id": {"$ne": new_period_id},
            },
            {"$set": {"payments.$[p].period_id": new_period_id, "updated_at": now}},
            array_filters=[{"p.period_id": old["id"], "p.status": "pending"}],
        )


async def save_piece_inputs(db, period: dict, payload: dict, user: dict) -> dict:
    from services.hr_payroll import public_period

    if str(period.get("period_scope") or "") != "piece":
        raise HTTPException(409, "Input Holding hanya untuk periode borongan.")
    if period.get("status") != STATUS_DRAFT:
        raise HTTPException(409, "Rentang dan nominal hanya dapat diubah sebelum finalisasi, pada status DRAFT.")
    from services.hr_payroll import _posted_accrual
    if await _posted_accrual(db, period["id"]):
        raise HTTPException(409, "Periode yang sudah memiliki jurnal tidak dapat diubah.")
    bonus_in = payload.get("bonus") if isinstance(payload.get("bonus"), dict) else {}
    start = _date(bonus_in.get("start_date") or (period.get("piece_bonus") or {}).get("start_date"), "Tanggal mulai bonus") if (bonus_in.get("start_date") or (period.get("piece_bonus") or {}).get("start_date")) else ""
    end = _date(bonus_in.get("end_date") or (period.get("piece_bonus") or {}).get("end_date"), "Tanggal akhir bonus") if (bonus_in.get("end_date") or (period.get("piece_bonus") or {}).get("end_date")) else ""
    if start and end:
        _span(start, end)
    adjustments = dict(_adjustments(period))
    for row in payload.get("bonus_adjustments") or []:
        if not isinstance(row, dict) or not row.get("employee_id"):
            continue
        final = to_money(row.get("final_amount"))
        if final < 0:
            raise HTTPException(400, "Nominal bonus tidak boleh negatif.")
        reason = str(row.get("reason") or "").strip()
        adjustments[str(row["employee_id"])] = {
            "final_amount": money_float(final),
            "reason": reason[:1000],
            "actor": actor_snapshot(user),
            "decided_at": now_iso(),
        }
    kasbon_inputs = list(period.get("piece_kasbon_inputs") or [])
    by_sched = {str(row.get("schedule_id")): row for row in kasbon_inputs if isinstance(row, dict)}
    for row in payload.get("kasbon_inputs") or []:
        if not isinstance(row, dict) or not row.get("schedule_id"):
            raise HTTPException(400, "Jadwal kasbon wajib dipilih.")
        amount = to_money(row.get("amount"))
        if amount < 0:
            raise HTTPException(400, "Potongan kasbon tidak boleh negatif.")
        by_sched[str(row["schedule_id"])] = {
            "employee_id": str(row.get("employee_id") or ""),
            "schedule_id": str(row["schedule_id"]),
            "amount": money_float(amount),
            "actor": actor_snapshot(user),
            "decided_at": now_iso(),
        }
    piece_bonus = {
        "start_date": start,
        "end_date": end,
        "adjustments": adjustments,
    }
    wage_patch = {}
    if payload.get("start_date"):
        wage_patch["start_date"] = _date(payload.get("start_date"), "Tanggal mulai upah")
    if payload.get("end_date"):
        wage_patch["end_date"] = _date(payload.get("end_date"), "Tanggal akhir upah")
    if wage_patch.get("end_date"):
        end_parts = wage_patch["end_date"].split("-")
        wage_patch["year"] = int(end_parts[0])
        wage_patch["month"] = int(end_parts[1])
    if wage_patch.get("start_date") and wage_patch.get("end_date"):
        _span(wage_patch["start_date"], wage_patch["end_date"])
    elif wage_patch.get("start_date") or wage_patch.get("end_date"):
        merged_start = wage_patch.get("start_date") or str(period.get("start_date") or "")
        merged_end = wage_patch.get("end_date") or str(period.get("end_date") or "")
        if merged_start and merged_end:
            _span(merged_start, merged_end)
    await db[PERIODS].update_one(
        {"id": period["id"], "status": STATUS_DRAFT},
        {"$set": {
            "piece_bonus": piece_bonus,
            "piece_kasbon_inputs": list(by_sched.values()),
            "updated_at": now_iso(),
            **wage_patch,
        }},
    )
    fresh = await db[PERIODS].find_one({"id": period["id"]}, {"_id": 0})
    return public_period(fresh)
