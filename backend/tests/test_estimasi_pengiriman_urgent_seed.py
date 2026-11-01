"""Unit tests: Urgent Approved seeds manual from target_completion_date."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.urgent_requests import _apply_order_urgent
from services.tanggal_pengiriman import delivery_sla_effective, manual_tanggal_pengiriman, wib_calendar_date


def _run(coro):
    return asyncio.run(coro)


def test_urgent_approve_seeds_target_completion_when_manual_missing():
    order = {
        "id": "po-1",
        "priority": "normal",
        "current_stage": 2,
        "timeline": [],
        "cancelled": False,
    }
    request = {
        "id": "req-1",
        "target_completion_date": "2026-11-10",
        "decided_by_email": "dir@x.com",
        "decided_by_name": "Director",
    }
    user = {"email": "dir@x.com", "name": "Director", "role": "director"}

    async def _fake_find_one(query, proj=None):
        return dict(order)

    updated = {}

    async def _fake_find_one_and_update(query, update, return_document=None):
        updated.update(update.get("$set") or {})
        if "$push" in update:
            order.setdefault("timeline", []).append(update["$push"]["timeline"])
        out = {**order, **updated}
        return out

    db = MagicMock()
    db.orders = MagicMock()
    db.orders.find_one = AsyncMock(side_effect=_fake_find_one)
    db.orders.find_one_and_update = AsyncMock(side_effect=_fake_find_one_and_update)

    applied = _run(_apply_order_urgent(db, request=request, order=order, user=user, claim_id="c1"))
    assert applied["priority"] == "urgent"
    assert wib_calendar_date(applied["delivery_deadline_manual"]) == "2026-11-10"
    assert applied["scheduled_delivery_date"] == "2026-11-10"
    fact = delivery_sla_effective(applied, 3)
    assert fact["source"] == "urgent_manual"
    assert fact["date"] == "2026-11-10"
    assert fact["final"] is True


def test_urgent_approve_does_not_overwrite_existing_manual():
    order = {
        "id": "po-2",
        "priority": "normal",
        "current_stage": 2,
        "timeline": [],
        "cancelled": False,
        "delivery_deadline_manual": "2026-11-05T00:00:00+07:00",
        "scheduled_delivery_date": "2026-11-05",
    }
    request = {
        "id": "req-2",
        "target_completion_date": "2026-11-20",
        "decided_by_email": "dir@x.com",
        "decided_by_name": "Director",
    }
    user = {"email": "dir@x.com", "name": "Director", "role": "director"}

    async def _fake_find_one(query, proj=None):
        return dict(order)

    captured = {}

    async def _fake_find_one_and_update(query, update, return_document=None):
        captured["set"] = dict(update.get("$set") or {})
        out = {**order, **captured["set"]}
        if "$push" in update:
            out.setdefault("timeline", []).append(update["$push"]["timeline"])
        return out

    db = MagicMock()
    db.orders = MagicMock()
    db.orders.find_one = AsyncMock(side_effect=_fake_find_one)
    db.orders.find_one_and_update = AsyncMock(side_effect=_fake_find_one_and_update)

    applied = _run(_apply_order_urgent(db, request=request, order=order, user=user, claim_id="c2"))
    assert "delivery_deadline_manual" not in captured["set"]
    assert manual_tanggal_pengiriman(applied) == "2026-11-05"


def test_urgent_approve_invalid_target_leaves_unscheduled():
    order = {
        "id": "po-3",
        "priority": "normal",
        "current_stage": 2,
        "timeline": [],
        "cancelled": False,
    }
    request = {
        "id": "req-3",
        "target_completion_date": "bukan-tanggal",
        "decided_by_email": "dir@x.com",
        "decided_by_name": "Director",
    }
    user = {"email": "dir@x.com", "name": "Director", "role": "director"}

    async def _fake_find_one(query, proj=None):
        return dict(order)

    async def _fake_find_one_and_update(query, update, return_document=None):
        return {**order, **(update.get("$set") or {})}

    db = MagicMock()
    db.orders = MagicMock()
    db.orders.find_one = AsyncMock(side_effect=_fake_find_one)
    db.orders.find_one_and_update = AsyncMock(side_effect=_fake_find_one_and_update)

    applied = _run(_apply_order_urgent(db, request=request, order=order, user=user, claim_id="c3"))
    assert applied["priority"] == "urgent"
    assert not manual_tanggal_pengiriman(applied)
    fact = delivery_sla_effective(applied, 3)
    assert fact["display_state"] == "belum_dijadwalkan"
    assert fact["final"] is False
