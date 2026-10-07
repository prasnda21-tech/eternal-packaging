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

from operator_analytics import line_item_name, line_matches_item, wib_today
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


async def collect_outputs(
    db, *, wage_start: str, wage_end: str, bonus_start: str, bonus_end: str, integration_start: str,
    list_cap: int = 200,
) -> dict:
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
        **_capped_lists(exclusions, blockers, cap=list_cap),
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


PAYROLL_STATUS_BELUM = "BELUM_MASUK_PAYROLL"
PAYROLL_STATUS_HELD = "HELD"
PAYROLL_STATUS_POSTED = "POSTED"
PAYROLL_STATUS_BLOCKED = "NOT_ELIGIBLE"
_OUTSIDE_REASON = "di luar rentang upah dan bonus"
ELIGIBILITY_REASON_LABELS = {
    "Tanggal kerja tidak tersedia": "Tanpa tanggal kerja",
    "tanpa tanggal pekerjaan": "Tanpa tanggal kerja",
    "tanpa snapshot tarif": "Tanpa snapshot tarif",
    "profil bukan pay_basis piece": "Profil HR belum piece",
    "sebelum batas integrasi": "Sebelum batas integrasi",
    "belum terhubung ke satu profil HR": "Belum terhubung ke profil HR",
}
_REPORT_LIST_CAP = 20000
BONUS_STATUS_FINAL = "FINAL"
BONUS_STATUS_USULAN = "USULAN"
MISSING_WORK_DATE_REASON = "Tanggal kerja tidak tersedia"
BONUS_NOTE = (
    "Upah Eligible = qty × rate_per_pcs snapshot, sama dengan upah borongan payroll. "
    "Tambahan produk tidak ditambahkan lagi. "
    "Bonus Usulan belum final dan tidak masuk Total Payroll. "
    "Total Payroll memuat bonus hanya bila nominal BONUS_OPERATOR final pada baris payroll cocok dengan rentang dan qty ini."
)


def _status_from_claim(claim_status: Optional[str]) -> str:
    if claim_status == POSTED:
        return PAYROLL_STATUS_POSTED
    if claim_status == HELD:
        return PAYROLL_STATUS_HELD
    return PAYROLL_STATUS_BELUM


def _claim_by_source(claims: List[dict]) -> Dict[str, dict]:
    """Map source_key → {status, period_id} using strongest active claim."""
    rank = {HELD: 1, POSTED: 2}
    out: Dict[str, dict] = {}
    for row in claims or []:
        key = str((row or {}).get("source_key") or "")
        status = str((row or {}).get("status") or "")
        if not key or status not in ACTIVE_CLAIM:
            continue
        previous = out.get(key)
        if previous is None or rank.get(status, 0) >= rank.get(previous.get("status"), 0):
            out[key] = {
                "status": status,
                "period_id": str((row or {}).get("period_id") or ""),
            }
    return out


def _row_payment_visibility(payroll_status: str, period_id: str, payment: Optional[dict]) -> Dict[str, Any]:
    """Derive payment visibility for one Gaji Operator output row.

    Relation: source_key → claim → period → active payment.
    Does not mark unpaid POSTED rows as paid.
    """
    from services.payroll_payment import (
        VIS_BELUM_DIBAYAR,
        map_payment_visibility,
        payment_visibility_label,
    )

    empty = {
        "payment_status": None,
        "payment_status_label": "",
        "payroll_period_id": period_id or "",
        "payment_id": None,
    }
    if payroll_status in (PAYROLL_STATUS_BELUM, PAYROLL_STATUS_BLOCKED, PAYROLL_STATUS_HELD):
        return empty
    if payroll_status != PAYROLL_STATUS_POSTED:
        return empty
    if not period_id or not payment:
        return {
            **empty,
            "payroll_period_id": period_id or "",
            "payment_status": VIS_BELUM_DIBAYAR,
            "payment_status_label": payment_visibility_label(VIS_BELUM_DIBAYAR),
        }
    code = map_payment_visibility(payment.get("status"))
    return {
        "payment_status": code,
        "payment_status_label": payment_visibility_label(code),
        "payroll_period_id": period_id,
        "payment_id": payment.get("id"),
    }


def _summary_payment_status(rows: List[dict]) -> Optional[str]:
    """Person-level payment visibility with unpaid-first precedence."""
    from services.payroll_payment import summarize_payment_visibility

    return summarize_payment_visibility([
        row.get("payment_status") for row in rows
        if row.get("payroll_status") == PAYROLL_STATUS_POSTED
    ])


def _work_in_range(row: dict, start: str, end: str) -> bool:
    work_date = str((row or {}).get("work_date") or "")[:10]
    return bool(work_date) and start <= work_date <= end


def _line_bits(order: dict, line_idx: Any) -> dict:
    lis = (order or {}).get("line_items") or []
    try:
        idx = int(line_idx)
    except (TypeError, ValueError):
        idx = -1
    li = lis[idx] if 0 <= idx < len(lis) else {}
    name = str(li.get("name") or li.get("item_name") or "").strip() or line_item_name(order or {}, line_idx)
    return {
        "item_code": str(li.get("item_code") or "").strip(),
        "item_name": name,
        "accurate_item_id": str(li.get("accurate_item_id") or "").strip(),
        "line_idx": line_idx,
    }


def _split_on(order: dict, split_id: str) -> dict:
    for split in (order or {}).get("operator_splits") or []:
        if str(split.get("id") or "") == str(split_id or ""):
            return split
    return {}


def _has_work_date(row: dict) -> bool:
    return len(str((row or {}).get("work_date") or "")[:10]) == 10


def _keep_for_report(row: dict, start: str, end: str) -> bool:
    """Dated rows stay inside the filter. Undated rows stay visible and undated.

    A sibling output in range does not assign them a payroll date.
    """
    if not _has_work_date(row):
        return True
    if str(row.get("reason") or "") == _OUTSIDE_REASON:
        return False
    if _work_in_range(row, start, end):
        return True
    return bool(row.get("in_wage_range") or row.get("in_bonus_range"))


def _bonus_operator_match(stored: Optional[dict], qty_bonus: int, start: str, end: str) -> Optional[Decimal]:
    """Final BONUS_OPERATOR only when the stored snapshot is this exact view."""
    if not stored:
        return None
    earnings = stored.get("earnings") or []
    if not any(str((row or {}).get("code") or "").upper() == CODE_BONUS for row in earnings if isinstance(row, dict)):
        return None
    snap = stored.get("bonus_snapshot") or {}
    if str(snap.get("start_date") or "") != start or str(snap.get("end_date") or "") != end:
        return None
    try:
        stored_qty = int(snap.get("base_qty"))
    except (TypeError, ValueError):
        return None
    if stored_qty != int(qty_bonus):
        return None
    return to_money(snap.get("final_amount"))


def _passes_filters(row: dict, order: dict, operator_ids: Optional[List[str]], item: Optional[str]) -> bool:
    if operator_ids:
        identity = {
            str(row.get("operator_user_id") or ""),
            str(row.get("operator_email") or ""),
            str(row.get("operator_name") or ""),
            str(row.get("operator") or ""),
        }
        identity.discard("")
        if not identity.intersection(set(operator_ids)):
            return False
    if item and str(item).strip() and str(item).strip().lower() not in ("all", "*"):
        if not line_matches_item(order, row.get("line_idx"), item):
            return False
    return True


def _group_key(row: dict) -> str:
    return (
        str(row.get("operator_user_id") or "").strip()
        or str(row.get("employee_id") or "").strip()
        or f"name:{str(row.get('operator_name') or '').strip()}"
        or str(row.get("source_key") or "")
    )


def _summary_status(rows: List[dict]) -> str:
    eligible = [row for row in rows if row.get("payroll_status") != PAYROLL_STATUS_BLOCKED]
    if not eligible:
        return PAYROLL_STATUS_BLOCKED if rows else ""
    for status in (PAYROLL_STATUS_BELUM, PAYROLL_STATUS_HELD, PAYROLL_STATUS_POSTED):
        if any(row.get("payroll_status") == status for row in eligible):
            return status
    return str(eligible[0].get("payroll_status") or "")


def eligibility_summary_from_rows(rows: List[dict]) -> dict:
    """Group NOT_ELIGIBLE report rows by existing payroll_reason. Rules are unchanged."""
    blocked = [row for row in rows if row.get("payroll_status") == PAYROLL_STATUS_BLOCKED]
    counts: Dict[str, int] = {}
    for row in blocked:
        reason = str(row.get("payroll_reason") or "tidak eligible").strip() or "tidak eligible"
        counts[reason] = counts.get(reason, 0) + 1
    by_reason = [
        {
            "reason": reason,
            "label": ELIGIBILITY_REASON_LABELS.get(reason, reason),
            "count": count,
        }
        for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {"total": len(blocked), "by_reason": by_reason}


def assemble_gaji_operator_rows(
    found: dict,
    claims: List[dict],
    *,
    start: str,
    end: str,
    orders: Dict[str, dict],
    operator_ids: Optional[List[str]] = None,
    item: Optional[str] = None,
    bonus_by_employee: Optional[Dict[str, dict]] = None,
    bonus_config: Optional[dict] = None,
    payments_by_period: Optional[Dict[str, dict]] = None,
) -> dict:
    """Shape canonical collect_outputs rows for the read-only Gaji Operator report.

    Wage amounts are the collect_outputs amounts. This function does not rate
    rows again and does not write claims.
    """
    from services.payroll_payment import payment_visibility_label

    claim_map = _claim_by_source(claims)
    bonus_by_employee = bonus_by_employee or {}
    payments_by_period = payments_by_period or {}

    def present(raw: dict, *, eligible: bool, apply_filters: bool) -> Optional[dict]:
        order = orders.get(str(raw.get("order_id") or "")) or {}
        split = _split_on(order, str(raw.get("split_id") or ""))
        bits = _line_bits(order, split.get("line_idx"))
        row = {
            **raw,
            "operator_email": str(split.get("operator_email") or ""),
            "operator_name": raw.get("operator_name") or split.get("operator_name") or "",
            **bits,
        }
        if not _keep_for_report(row, start, end):
            return None
        if apply_filters and not _passes_filters(row, order, operator_ids, item):
            return None
        claim = claim_map.get(str(row.get("source_key") or "")) or {}
        claim_status = claim.get("status")
        period_id = str(claim.get("period_id") or "")
        if not _has_work_date(row):
            status = PAYROLL_STATUS_BLOCKED
            reason = MISSING_WORK_DATE_REASON
        elif eligible and row.get("has_rate"):
            status = _status_from_claim(claim_status)
            reason = ""
        elif claim_status:
            status = _status_from_claim(claim_status)
            reason = str(row.get("reason") or "")
        else:
            status = PAYROLL_STATUS_BLOCKED
            reason = str(row.get("reason") or "tidak eligible")
        amount = money_float(row.get("amount")) if status != PAYROLL_STATUS_BLOCKED else 0.0
        if status == PAYROLL_STATUS_BLOCKED:
            amount = 0.0
        qty = int(row.get("qty") or 0)
        payment = payments_by_period.get(period_id) if period_id else None
        pay_vis = _row_payment_visibility(status, period_id, payment)
        return {
            "source_key": row.get("source_key") or "",
            "order_id": row.get("order_id") or "",
            "split_id": row.get("split_id") or "",
            "action_id": row.get("action_id") or "",
            "po_number": row.get("po_number") or "",
            "operator_user_id": row.get("operator_user_id") or "",
            "operator_id": row.get("operator_user_id") or row.get("operator_email") or "",
            "operator_email": row.get("operator_email") or "",
            "operator": row.get("employee_name") or row.get("operator_name") or row.get("operator_user_id") or "",
            "operator_name": row.get("operator_name") or "",
            "employee_id": row.get("employee_id") or "",
            "employee_name": row.get("employee_name") or "",
            "tanggal": str(row.get("work_date") or "")[:10],
            "date": str(row.get("work_date") or "")[:10],
            "work_date": str(row.get("work_date") or "")[:10],
            "qty_completed": qty,
            "qty": qty,
            "rate_per_pcs": row.get("rate_per_pcs") if row.get("has_rate") else "",
            "base_rate_per_pcs": row.get("rate_per_pcs") if row.get("has_rate") else "",
            "addition_per_pcs": 0,
            "base_wage": amount,
            "product_addition": 0,
            "total_upah": amount,
            "item_code": bits["item_code"],
            "item_name": bits["item_name"],
            "accurate_item_id": bits["accurate_item_id"],
            "line_idx": bits["line_idx"],
            "payroll_status": status,
            "payroll_reason": reason,
            "rate_unset": status == PAYROLL_STATUS_BLOCKED and reason == "tanpa snapshot tarif",
            "rate_source": "entry_snapshot" if row.get("has_rate") else "",
            "payroll_period_id": pay_vis.get("payroll_period_id") or "",
            "payment_status": pay_vis.get("payment_status"),
            "payment_status_label": pay_vis.get("payment_status_label") or "",
            "payment_id": pay_vis.get("payment_id"),
        }

    def collect(apply_filters: bool) -> List[dict]:
        chosen: List[dict] = []
        seen = set()
        for raw in found.get("wage") or []:
            row = present(raw, eligible=True, apply_filters=apply_filters)
            if row and row["source_key"] not in seen:
                seen.add(row["source_key"])
                chosen.append(row)
        for bucket in ("blockers", "exclusions"):
            for raw in found.get(bucket) or []:
                row = present(raw, eligible=False, apply_filters=apply_filters)
                if row and row["source_key"] not in seen:
                    seen.add(row["source_key"])
                    chosen.append(row)
        return chosen

    catalog = collect(False)
    visible = collect(True)

    bonus_qty: Dict[str, int] = {}
    for raw in found.get("bonus") or []:
        order = orders.get(str(raw.get("order_id") or "")) or {}
        split = _split_on(order, str(raw.get("split_id") or ""))
        probed = {**raw, "operator_email": str(split.get("operator_email") or ""), "line_idx": split.get("line_idx")}
        if not _work_in_range(probed, start, end):
            continue
        if not _passes_filters(probed, order, operator_ids, item):
            continue
        employee_id = str(raw.get("employee_id") or "")
        if employee_id:
            bonus_qty[employee_id] = bonus_qty.get(employee_id, 0) + int(raw.get("qty") or 0)

    grouped: Dict[str, List[dict]] = {}
    for row in visible:
        grouped.setdefault(_group_key(row), []).append(row)

    people = []
    for key, rows in grouped.items():
        eligible = [row for row in rows if row["payroll_status"] != PAYROLL_STATUS_BLOCKED]
        base = sum((to_money(row["base_wage"]) for row in eligible), Decimal("0"))
        employee_ids = [str(row.get("employee_id") or "") for row in rows if row.get("employee_id")]
        employee_id = employee_ids[0] if employee_ids else ""
        qty_bonus = bonus_qty.get(employee_id, 0) if employee_id else 0
        proposed = to_money(compute_bonus(qty_bonus, bonus_config))
        final_bonus = _bonus_operator_match(
            bonus_by_employee.get(employee_id) if employee_id else None,
            qty_bonus,
            start,
            end,
        )
        if final_bonus is not None:
            bonus = final_bonus
            bonus_final = final_bonus
            bonus_source = "payroll_line"
            bonus_status = BONUS_STATUS_FINAL
            payroll_total = base + final_bonus
        else:
            bonus = proposed
            bonus_final = Decimal("0")
            bonus_source = "proposed"
            bonus_status = BONUS_STATUS_USULAN
            payroll_total = base
        head = rows[0]
        counts: Dict[str, int] = {}
        for row in rows:
            counts[row["payroll_status"]] = counts.get(row["payroll_status"], 0) + 1
        pay_status = _summary_payment_status(rows)
        people.append({
            "id": key,
            "operator_user_id": head.get("operator_user_id") or "",
            "email": head.get("operator_email") or head.get("operator_user_id") or "",
            "name": head.get("operator") or head.get("operator_name") or key,
            "employee_id": employee_id,
            "qty": sum(int(row["qty_completed"] or 0) for row in eligible),
            "blocked_qty": sum(int(row["qty_completed"] or 0) for row in rows if row["payroll_status"] == PAYROLL_STATUS_BLOCKED),
            "base_wage": money_float(base),
            "product_addition": 0,
            "bonus": money_float(bonus),
            "bonus_source": bonus_source,
            "bonus_status": bonus_status,
            "bonus_proposed_amount": money_float(proposed),
            "bonus_final_amount": money_float(bonus_final),
            "total_upah": money_float(payroll_total),
            "total_payroll": money_float(payroll_total),
            "estimate_total": money_float(base + proposed if bonus_status == BONUS_STATUS_USULAN else payroll_total),
            "wage_basis": money_float(base),
            "payroll_status": _summary_status(rows),
            "payroll_counts": counts,
            "rate_unset": any(row.get("rate_unset") for row in rows),
            "payment_status": pay_status,
            "payment_status_label": payment_visibility_label(pay_status) if pay_status else "",
        })
    people.sort(key=lambda row: (row.get("name") or "", row.get("id") or ""))
    visible.sort(key=lambda row: (row.get("tanggal") or "", row.get("operator") or "", row.get("source_key") or ""))

    operators: Dict[str, dict] = {}
    items: Dict[str, dict] = {}
    for row in catalog:
        op_key = row.get("operator_user_id") or row.get("operator_email") or row.get("operator")
        if op_key and op_key not in operators:
            operators[op_key] = {
                "id": row.get("operator_user_id") or row.get("operator_email") or op_key,
                "operator_user_id": row.get("operator_user_id") or "",
                "email": row.get("operator_email") or "",
                "name": row.get("operator") or row.get("operator_name") or op_key,
            }
        item_key = row.get("item_code") or row.get("item_name")
        if item_key and item_key not in items:
            items[item_key] = {
                "item_code": row.get("item_code") or "",
                "item_name": row.get("item_name") or "",
                "accurate_item_id": row.get("accurate_item_id") or "",
            }
    blocked = [row for row in visible if row["payroll_status"] == PAYROLL_STATUS_BLOCKED]
    return {
        "rows": visible,
        "people": people,
        "filters": {
            "operators": sorted(operators.values(), key=lambda row: row.get("name") or ""),
            "items": sorted(items.values(), key=lambda row: (row.get("item_code") or "", row.get("item_name") or "")),
        },
        "not_eligible_count": len(blocked),
        "eligibility_summary": eligibility_summary_from_rows(visible),
        "missing_base_rate": sorted({row.get("operator") or row.get("operator_user_id") for row in blocked if row.get("rate_unset")}),
        "read_only": True,
        "canonical_source": "progress_entries.work_date.rate_per_pcs",
    }


async def _orders_for_display(db, order_ids: List[str]) -> Dict[str, dict]:
    if not order_ids:
        return {}
    docs = await db.orders.find(
        {"id": {"$in": order_ids}},
        {"_id": 0, "id": 1, "line_items": 1, "operator_splits": 1, "po_number": 1, "product_name": 1},
    ).to_list(5000)
    return {str(doc.get("id") or ""): doc for doc in docs}


async def _claims_for_keys(db, source_keys: List[str]) -> List[dict]:
    if not source_keys:
        return []
    return await db[WAGE_CLAIMS].find(
        {"source_key": {"$in": source_keys}, "status": {"$in": list(ACTIVE_CLAIM)}},
        {"_id": 0, "source_key": 1, "status": 1, "period_id": 1},
    ).to_list(len(source_keys) + 1)


async def _stored_bonus(db, employee_ids: List[str], start: str, end: str) -> Dict[str, dict]:
    if not employee_ids:
        return {}
    rows = await db[LINES].find(
        {
            "employee_id": {"$in": employee_ids},
            "bonus_snapshot.start_date": start,
            "bonus_snapshot.end_date": end,
        },
        {"_id": 0, "employee_id": 1, "bonus_snapshot": 1, "earnings": 1, "updated_at": 1},
    ).to_list(5000)
    chosen: Dict[str, dict] = {}
    for row in rows:
        employee_id = str(row.get("employee_id") or "")
        previous = chosen.get(employee_id)
        if previous is None or str(row.get("updated_at") or "") >= str(previous.get("updated_at") or ""):
            chosen[employee_id] = row
    return chosen


async def build_gaji_operator_report(
    db,
    *,
    date_from: date,
    date_to: date,
    operator_ids: Optional[List[str]] = None,
    item: Optional[str] = None,
) -> dict:
    """Read-only Gaji Operator view. Uses collect_outputs. Does not write."""
    start = date_from.isoformat()
    end = date_to.isoformat()
    if end < start:
        raise HTTPException(400, "Tanggal akhir tidak boleh lebih kecil dari tanggal mulai.")
    integration = await get_integration(db)
    found = await collect_outputs(
        db,
        wage_start=start,
        wage_end=end,
        bonus_start=start,
        bonus_end=end,
        integration_start=str(integration.get("start_date") or ""),
        list_cap=_REPORT_LIST_CAP,
    )
    order_ids = sorted({
        str(row.get("order_id") or "")
        for bucket in ("wage", "bonus", "blockers", "exclusions")
        for row in (found.get(bucket) or [])
        if row.get("order_id")
    })
    orders = await _orders_for_display(db, order_ids)
    source_keys = [str(row.get("source_key") or "") for row in (found.get("wage") or []) if row.get("source_key")]
    for bucket in ("blockers", "exclusions"):
        for row in found.get(bucket) or []:
            if row.get("source_key"):
                source_keys.append(str(row["source_key"]))
    claims = await _claims_for_keys(db, source_keys)
    period_ids = sorted({
        str(row.get("period_id") or "")
        for row in claims
        if row.get("period_id")
    })
    from services.payroll_payment import VIS_SUDAH_DIBAYAR, active_payments_by_period
    payments_by_period = await active_payments_by_period(db, period_ids)
    employee_ids = sorted({
        str(row.get("employee_id") or "")
        for row in (found.get("bonus") or [])
        if row.get("employee_id")
    })
    stored_bonus = await _stored_bonus(db, employee_ids, start, end)
    wage_doc = await db.app_settings.find_one({"key": WAGE_SETTINGS_KEY}, {"_id": 0}) or {}
    bonus_config = effective_bonus_as_of(date_to, normalize_wage_settings(wage_doc))
    assembled = assemble_gaji_operator_rows(
        found,
        claims,
        start=start,
        end=end,
        orders=orders,
        operator_ids=operator_ids or None,
        item=item,
        bonus_by_employee=stored_bonus,
        bonus_config=bonus_config,
        payments_by_period=payments_by_period,
    )
    people = assembled["people"]
    rows = assembled["rows"]
    eligible = [row for row in rows if row.get("payroll_status") != PAYROLL_STATUS_BLOCKED]
    missing = assembled["missing_base_rate"]
    payroll_total = sum(person["total_payroll"] for person in people)
    posted_people = [p for p in people if p.get("payroll_status") == PAYROLL_STATUS_POSTED]
    paid_people = sum(1 for p in posted_people if p.get("payment_status") == VIS_SUDAH_DIBAYAR)
    unpaid_people = sum(1 for p in posted_people if p.get("payment_status") != VIS_SUDAH_DIBAYAR)
    return {
        "rows": rows,
        "people": people,
        "kpis": {
            "output": sum(int(row.get("qty_completed") or 0) for row in eligible),
            "base_wage": sum(person["wage_basis"] for person in people),
            "product_addition": 0,
            "bonus": sum(person["bonus_final_amount"] for person in people),
            "bonus_proposed": sum(
                person["bonus_proposed_amount"] for person in people
                if person.get("bonus_status") == BONUS_STATUS_USULAN
            ),
            "total_upah": payroll_total,
            "total_payroll": payroll_total,
            "estimate_total": sum(person["estimate_total"] for person in people),
            "operator_count": len(people),
            "sudah_dibayar": paid_people,
            "belum_dibayar": unpaid_people,
            "payment_posted_operators": len(posted_people),
        },
        "filters": assembled["filters"],
        "period": {"from": start, "to": end},
        "read_only": True,
        "canonical_source": assembled["canonical_source"],
        "bonus_note": BONUS_NOTE,
        "not_eligible_count": assembled["not_eligible_count"],
        "eligibility_summary": assembled["eligibility_summary"],
        "missing_base_rate": missing,
        "missing_base_rate_message": (
            "Output tanpa snapshot tarif tidak masuk payroll borongan"
            if missing else None
        ),
        "source": "collect_outputs progress_entries",
    }
