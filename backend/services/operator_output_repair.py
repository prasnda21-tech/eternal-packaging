"""Guarded field corrections for operator progress_entries used by Gaji Operator.

Entries remain append-only for qty/identity. This module only allows filling an
empty work_date or missing rate_per_pcs on an existing action_id via compare-and-set.
It never creates a new source_key, never mutates qty, and never touches payroll claims.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Tuple

from fastapi import HTTPException

from services.hr_payroll import PERIODS, STATUS_FINAL, money_float, to_money
from services.operator_output_coord import coordinate_source_key, require_coord_holder
from services.operator_wage_claims import ACTIVE_CLAIM, HELD, POSTED, WAGE_CLAIMS
from services.payroll_payment import STATUS_PAID, active_payments_by_period
from services.purchase_common import now_iso
from services.workflow_entries import find_by_action_id

PERM_REPAIR_WORK_DATE = "reports.gaji_operator.repair_work_date"
PERM_REPAIR_RATE = "reports.gaji_operator.repair_rate"


def _parse_iso_date(raw: Any, label: str) -> str:
    text = str(raw or "").strip()[:10]
    try:
        date.fromisoformat(text)
    except ValueError:
        raise HTTPException(400, f"{label} harus YYYY-MM-DD.")
    return text


def _source_key(order_id: str, split_id: str, action_id: str) -> str:
    return f"{order_id}:{split_id}:{action_id}"


def _entry_guards(entry: dict) -> None:
    if not entry:
        raise HTTPException(404, "Output operator tidak ditemukan.")
    if entry.get("cancelled") is True or entry.get("replaced") is True:
        raise HTTPException(409, "Output dibatalkan atau diganti; tidak dapat dikoreksi.")
    if entry.get("legacy") is True:
        raise HTTPException(409, "Output legacy tidak dapat dikoreksi lewat jalur ini.")
    action = str(entry.get("action_id") or "")
    if action.startswith("legacy-") or entry.get("id") == "legacy":
        raise HTTPException(409, "Output legacy tidak dapat dikoreksi lewat jalur ini.")


async def _reject_if_claimed(db, source_key: str) -> None:
    claim = await db[WAGE_CLAIMS].find_one(
        {"source_key": source_key, "status": {"$in": list(ACTIVE_CLAIM)}},
        {"_id": 0, "status": 1, "period_id": 1},
    )
    if not claim:
        return
    status = str(claim.get("status") or "")
    period_id = str(claim.get("period_id") or "")
    if status == HELD:
        raise HTTPException(409, "Output sudah diklaim HELD pada payroll; koreksi ditolak.")
    if status == POSTED:
        raise HTTPException(409, "Output sudah POSTED pada payroll; koreksi ditolak.")
    if period_id:
        period = await db[PERIODS].find_one({"id": period_id}, {"_id": 0, "status": 1})
        if period and str(period.get("status") or "") == STATUS_FINAL:
            raise HTTPException(409, "Periode payroll FINAL; koreksi ditolak.")
        payments = await active_payments_by_period(db, [period_id])
        payment = payments.get(period_id) or {}
        if str(payment.get("status") or "") == STATUS_PAID:
            raise HTTPException(409, "Pembayaran payroll sudah PAID; koreksi ditolak.")


def _find_split_and_entry(order: dict, split_id: str, action_id: str) -> Tuple[dict, dict, int]:
    splits = list(order.get("operator_splits") or [])
    for idx, split in enumerate(splits):
        if not isinstance(split, dict):
            continue
        if str(split.get("id") or "") != str(split_id):
            continue
        entries = list(split.get("progress_entries") or [])
        entry = find_by_action_id(entries, action_id)
        if entry is None:
            # find_by_action_id skips cancelled; still locate for clearer errors
            for e in entries:
                if isinstance(e, dict) and str(e.get("action_id") or "") == str(action_id):
                    _entry_guards(e)
            raise HTTPException(404, "Output operator tidak ditemukan pada split ini.")
        _entry_guards(entry)
        return split, entry, idx
    raise HTTPException(404, "Operator split tidak ditemukan.")


async def _load_order(db, order_id: str) -> dict:
    order = await db.orders.find_one({"id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order tidak ditemukan.")
    if order.get("cancelled") is True:
        raise HTTPException(409, "Order dibatalkan; tidak dapat dikoreksi.")
    return order


async def repair_work_date(
    db,
    *,
    order_id: str,
    split_id: str,
    action_id: str,
    work_date: str,
    expected_work_date: Optional[str],
    reason: str,
    user: dict,
    write_audit,
) -> dict:
    """Fill empty progress_entries.work_date with CAS. Does not invent dates from other fields."""
    new_date = _parse_iso_date(work_date, "Tanggal kerja")
    note = str(reason or "").strip()
    if len(note) < 5:
        raise HTTPException(400, "Alasan koreksi wajib diisi (minimal 5 karakter).")
    order = await _load_order(db, order_id)
    split, entry, _split_idx = _find_split_and_entry(order, split_id, action_id)
    current = str(entry.get("work_date") or "")[:10]
    expected = str(expected_work_date if expected_work_date is not None else current)[:10]
    if current and len(current) == 10:
        raise HTTPException(409, "Tanggal kerja sudah terisi; koreksi ulang tidak diizinkan di V1.")
    if expected and len(expected) == 10:
        raise HTTPException(409, "Data berubah sejak dibuka (tanggal sudah terisi). Muat ulang.")
    if current != expected and not (not current and not expected):
        raise HTTPException(409, "Data berubah sejak dibuka. Muat ulang lalu coba lagi.")

    key = _source_key(order_id, split_id, action_id)
    async with coordinate_source_key(db, key, purpose="repair_work_date") as holder:
        await require_coord_holder(db, key, holder)
        await _reject_if_claimed(db, key)

        # CAS: match empty/missing/invalid work_date only.
        await require_coord_holder(db, key, holder)
        result = await db.orders.update_one(
            {
                "id": order_id,
                "cancelled": {"$ne": True},
                "operator_splits": {
                    "$elemMatch": {
                        "id": split_id,
                        "progress_entries": {
                            "$elemMatch": {
                                "action_id": action_id,
                                "cancelled": {"$ne": True},
                                "replaced": {"$ne": True},
                                "legacy": {"$ne": True},
                                "$or": [
                                    {"work_date": {"$exists": False}},
                                    {"work_date": None},
                                    {"work_date": ""},
                                ],
                            }
                        },
                    }
                },
            },
            {"$set": {
                "operator_splits.$[s].progress_entries.$[e].work_date": new_date,
                "operator_splits.$[s].progress_entries.$[e].work_date_repaired_at": now_iso(),
                "operator_splits.$[s].progress_entries.$[e].work_date_repaired_by": (user or {}).get("email") or "",
            }},
            array_filters=[
                {"s.id": split_id},
                {"e.action_id": action_id, "e.legacy": {"$ne": True}, "e.cancelled": {"$ne": True}},
            ],
        )
        if result.matched_count != 1 or result.modified_count != 1:
            raise HTTPException(409, "Koreksi gagal: record berubah atau tidak memenuhi syarat CAS.")

        # Still under the shared fence: wage claim cannot insert for this key.
        await require_coord_holder(db, key, holder)
        await _reject_if_claimed(db, key)

        before = {"source_key": key, "work_date": current or None, "split_id": split_id, "action_id": action_id}
        after = {"source_key": key, "work_date": new_date, "split_id": split_id, "action_id": action_id}
        await write_audit(
            "gaji_operator.repair_work_date",
            user,
            target_email=f"po:{order.get('po_number') or order_id}",
            before=before,
            after=after,
            note=note,
        )
        return {
            "ok": True,
            "source_key": key,
            "work_date": new_date,
            "po_number": order.get("po_number") or "",
            "operator_name": split.get("operator_name") or "",
        }


async def repair_rate_snapshot(
    db,
    *,
    order_id: str,
    split_id: str,
    action_id: str,
    rate_per_pcs: Any,
    expected_rate: Optional[Any],
    reason: str,
    user: dict,
    write_audit,
) -> dict:
    """Fill missing progress_entries.rate_per_pcs manually. Never copies master wage rates."""
    note = str(reason or "").strip()
    if len(note) < 5:
        raise HTTPException(400, "Alasan koreksi wajib diisi (minimal 5 karakter).")
    try:
        new_rate = to_money(rate_per_pcs)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(400, "Tarif per pcs tidak valid.")
    if new_rate <= Decimal("0"):
        raise HTTPException(400, "Tarif per pcs harus lebih dari 0.")

    order = await _load_order(db, order_id)
    split, entry, _ = _find_split_and_entry(order, split_id, action_id)
    work_date = str(entry.get("work_date") or "")[:10]
    if len(work_date) != 10:
        raise HTTPException(409, "Isi tanggal kerja output terlebih dahulu sebelum koreksi tarif.")
    current = entry.get("rate_per_pcs")
    if current not in (None, ""):
        raise HTTPException(409, "Snapshot tarif sudah ada; koreksi ulang tidak diizinkan di V1.")
    if expected_rate not in (None, ""):
        raise HTTPException(409, "Data berubah sejak dibuka (tarif sudah terisi). Muat ulang.")

    key = _source_key(order_id, split_id, action_id)
    rate_f = money_float(new_rate)
    async with coordinate_source_key(db, key, purpose="repair_rate") as holder:
        await require_coord_holder(db, key, holder)
        await _reject_if_claimed(db, key)

        await require_coord_holder(db, key, holder)
        result = await db.orders.update_one(
            {
                "id": order_id,
                "cancelled": {"$ne": True},
                "operator_splits": {
                    "$elemMatch": {
                        "id": split_id,
                        "progress_entries": {
                            "$elemMatch": {
                                "action_id": action_id,
                                "cancelled": {"$ne": True},
                                "replaced": {"$ne": True},
                                "legacy": {"$ne": True},
                                "work_date": {"$regex": r"^\d{4}-\d{2}-\d{2}"},
                                "$or": [
                                    {"rate_per_pcs": {"$exists": False}},
                                    {"rate_per_pcs": None},
                                    {"rate_per_pcs": ""},
                                ],
                            }
                        },
                    }
                },
            },
            {"$set": {
                "operator_splits.$[s].progress_entries.$[e].rate_per_pcs": rate_f,
                "operator_splits.$[s].progress_entries.$[e].rate_repaired_at": now_iso(),
                "operator_splits.$[s].progress_entries.$[e].rate_repaired_by": (user or {}).get("email") or "",
            }},
            array_filters=[
                {"s.id": split_id},
                {"e.action_id": action_id, "e.legacy": {"$ne": True}, "e.cancelled": {"$ne": True}},
            ],
        )
        if result.matched_count != 1 or result.modified_count != 1:
            raise HTTPException(409, "Koreksi tarif gagal: record berubah atau tidak memenuhi syarat CAS.")

        await require_coord_holder(db, key, holder)
        await _reject_if_claimed(db, key)

        before = {"source_key": key, "rate_per_pcs": None, "work_date": work_date}
        after = {"source_key": key, "rate_per_pcs": rate_f, "work_date": work_date}
        await write_audit(
            "gaji_operator.repair_rate_snapshot",
            user,
            target_email=f"po:{order.get('po_number') or order_id}",
            before=before,
            after=after,
            note=note,
        )
        return {
            "ok": True,
            "source_key": key,
            "rate_per_pcs": rate_f,
            "work_date": work_date,
            "po_number": order.get("po_number") or "",
            "operator_name": split.get("operator_name") or "",
        }
