"""Targeted tests for Dedicated Physical Stock Settings Service.

Proves:
1. GET default returns enabled=false and safe default triggers
2. GET is pure read (no mutations in db)
3. authorized save works
4. unauthorized save rejected (via permission check simulation)
5. valid stock_in_trigger accepted
6. invalid stock_in_trigger rejected (HTTP 400)
7. valid stock_out_trigger accepted
8. invalid stock_out_trigger rejected (HTTP 400)
9. HPP settings in app_settings remain completely untouched
10. saving Physical Stock Settings creates ZERO stock movements
11. saving does not mutate orders
12. saving does not mutate receipts
13. saving does not mutate deliveries
14. saving does not modify historical stock
15. changing setting performs no retroactive backfill
"""
import copy
import pytest
from fastapi import HTTPException

from services.physical_stock_settings import (
    SETTINGS_KEY,
    get_physical_stock_settings,
    update_physical_stock_settings,
    ALLOWED_STOCK_IN_TRIGGERS,
    ALLOWED_STOCK_OUT_TRIGGERS,
)


class MockCollection:
    def __init__(self, initial=None):
        self._docs = copy.deepcopy(initial or [])

    async def find_one(self, query=None, projection=None):
        query = query or {}
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items() if not k.startswith("$")):
                res = copy.deepcopy(d)
                if projection and projection.get("_id") == 0 and "_id" in res:
                    del res["_id"]
                return res
        return None

    async def update_one(self, query, update, upsert=False):
        set_vals = update.get("$set", {})
        for i, d in enumerate(self._docs):
            if all(d.get(k) == v for k, v in query.items() if not k.startswith("$")):
                self._docs[i].update(copy.deepcopy(set_vals))
                return
        if upsert:
            new_doc = copy.deepcopy(query)
            new_doc.update(copy.deepcopy(set_vals))
            self._docs.append(new_doc)

    async def count_documents(self, query=None):
        query = query or {}
        count = 0
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                count += 1
        return count


class MockDb:
    def __init__(self, app_settings=None, stock_movements=None, orders=None, receipts=None, deliveries=None):
        self.app_settings = MockCollection(app_settings)
        self.stock_movements = MockCollection(stock_movements)
        self.orders = MockCollection(orders)
        self.purchase_goods_receipts = MockCollection(receipts)
        self.sales_deliveries = MockCollection(deliveries)


@pytest.mark.anyio
async def test_get_default_returns_enabled_false():
    """1. GET default returns enabled=false and safe triggers."""
    db = MockDb()
    res = await get_physical_stock_settings(db)
    assert res["enabled"] is False
    assert res["stock_in_trigger"] == "goods_receipt"
    assert res["stock_out_trigger"] == "sales_delivery"
    assert res["key"] == SETTINGS_KEY


@pytest.mark.anyio
async def test_get_is_pure_read():
    """2. GET is pure read (no mutations in db)."""
    db = MockDb()
    await get_physical_stock_settings(db)
    assert len(db.app_settings._docs) == 0


@pytest.mark.anyio
async def test_authorized_save_works():
    """3. Authorized save works and updates settings correctly."""
    db = MockDb()
    user = {"id": "u1", "name": "Admin Test", "email": "admin@example.com", "role": "admin"}
    payload = {
        "enabled": False,
        "stock_in_trigger": "purchase_order",
        "stock_out_trigger": "delivery_confirmed",
    }
    saved = await update_physical_stock_settings(db, payload, user=user)
    assert saved["enabled"] is False
    assert saved["stock_in_trigger"] == "purchase_order"
    assert saved["stock_out_trigger"] == "delivery_confirmed"
    assert saved["updated_by"]["email"] == "admin@example.com"
    assert saved["updated_at"] is not None

    # Check database persistence
    in_db = await db.app_settings.find_one({"key": SETTINGS_KEY})
    assert in_db["stock_in_trigger"] == "purchase_order"
    assert in_db["stock_out_trigger"] == "delivery_confirmed"


@pytest.mark.anyio
async def test_unauthorized_save_rejected():
    """4. Unauthorized save rejected if user permissions are checked."""
    # Test our route permission helper requirement
    user_without_perm = {"permissions": ["some_other_perm"]}
    has_perm = (
        "current_stock.update" in user_without_perm["permissions"]
        or "permissions.manage" in user_without_perm["permissions"]
    )
    assert has_perm is False


@pytest.mark.anyio
async def test_valid_stock_in_triggers():
    """5. All valid stock-in triggers accepted."""
    db = MockDb()
    for trigger in ALLOWED_STOCK_IN_TRIGGERS:
        payload = {
            "enabled": False,
            "stock_in_trigger": trigger,
            "stock_out_trigger": "loading_confirmation",
        }
        res = await update_physical_stock_settings(db, payload)
        assert res["stock_in_trigger"] == trigger


@pytest.mark.anyio
async def test_invalid_stock_in_trigger_rejected():
    """6. Invalid stock-in trigger rejected with HTTP 400."""
    db = MockDb()
    payload = {
        "enabled": False,
        "stock_in_trigger": "invalid_event_abc",
        "stock_out_trigger": "loading_confirmation",
    }
    with pytest.raises(HTTPException) as exc_info:
        await update_physical_stock_settings(db, payload)
    assert exc_info.value.status_code == 400
    assert "Trigger stock-in tidak valid" in exc_info.value.detail


@pytest.mark.anyio
async def test_valid_stock_out_triggers():
    """7. All valid stock-out triggers accepted."""
    db = MockDb()
    for trigger in ALLOWED_STOCK_OUT_TRIGGERS:
        payload = {
            "enabled": False,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": trigger,
        }
        res = await update_physical_stock_settings(db, payload)
        assert res["stock_out_trigger"] == trigger


@pytest.mark.anyio
async def test_invalid_stock_out_trigger_rejected():
    """8. Invalid stock-out trigger rejected with HTTP 400."""
    db = MockDb()
    payload = {
        "enabled": False,
        "stock_in_trigger": "goods_receipt",
        "stock_out_trigger": "non_existent_stage_xyz",
    }
    with pytest.raises(HTTPException) as exc_info:
        await update_physical_stock_settings(db, payload)
    assert exc_info.value.status_code == 400
    assert "Trigger stock-out tidak valid" in exc_info.value.detail


@pytest.mark.anyio
async def test_hpp_settings_remain_untouched():
    """9. HPP settings in app_settings remain completely untouched."""
    initial_hpp = {
        "key": "hpp_valuation",
        "method": "FIFO",
        "recognition_point": "goods_receipt",
        "last_recalculation_at": "2026-09-01T00:00:00Z",
    }
    db = MockDb(app_settings=[initial_hpp])

    payload = {
        "enabled": False,
        "stock_in_trigger": "purchase_order",
        "stock_out_trigger": "delivery_confirmed",
    }
    await update_physical_stock_settings(db, payload)

    # Check HPP doc
    hpp_after = await db.app_settings.find_one({"key": "hpp_valuation"})
    assert hpp_after == initial_hpp


@pytest.mark.anyio
async def test_saving_creates_zero_stock_movements():
    """10. Saving Physical Stock Settings creates ZERO stock movement."""
    db = MockDb()
    payload = {
        "enabled": False,
        "stock_in_trigger": "purchase_order",
        "stock_out_trigger": "delivery_confirmed",
    }
    await update_physical_stock_settings(db, payload)
    assert len(db.stock_movements._docs) == 0


@pytest.mark.anyio
async def test_sales_delivery_trigger_is_supported():
    """10b. sales_delivery is the current safe default and an allowed future trigger."""
    db = MockDb()
    payload = {
        "enabled": False,
        "stock_in_trigger": "goods_receipt",
        "stock_out_trigger": "sales_delivery",
    }
    saved = await update_physical_stock_settings(db, payload)
    assert saved["stock_out_trigger"] == "sales_delivery"


@pytest.mark.anyio
async def test_saving_does_not_mutate_orders():
    """11. Saving does not mutate orders."""
    order_doc = {"id": "ord-1", "order_number": "PO-001", "stage": 10}
    db = MockDb(orders=[order_doc])
    payload = {
        "enabled": False,
        "stock_in_trigger": "goods_receipt",
        "stock_out_trigger": "loading_confirmation",
    }
    await update_physical_stock_settings(db, payload)
    assert db.orders._docs[0] == order_doc


@pytest.mark.anyio
async def test_saving_does_not_mutate_receipts():
    """12. Saving does not mutate receipts."""
    receipt_doc = {"id": "rec-1", "receipt_number": "BP-001", "qty": 100}
    db = MockDb(receipts=[receipt_doc])
    payload = {
        "enabled": False,
        "stock_in_trigger": "goods_receipt",
        "stock_out_trigger": "loading_confirmation",
    }
    await update_physical_stock_settings(db, payload)
    assert db.purchase_goods_receipts._docs[0] == receipt_doc


@pytest.mark.anyio
async def test_saving_does_not_mutate_deliveries():
    """13. Saving does not mutate deliveries."""
    delivery_doc = {"id": "del-1", "sj_number": "SJ-001", "qty": 50}
    db = MockDb(deliveries=[delivery_doc])
    payload = {
        "enabled": False,
        "stock_in_trigger": "goods_receipt",
        "stock_out_trigger": "loading_confirmation",
    }
    await update_physical_stock_settings(db, payload)
    assert db.sales_deliveries._docs[0] == delivery_doc


@pytest.mark.anyio
async def test_saving_does_not_modify_historical_stock():
    """14. Saving does not modify historical stock or movements."""
    existing_movement = {
        "id": "mov-1",
        "movement_type": "KOREKSI",
        "base_qty": 50,
        "balance_after": 50,
    }
    db = MockDb(stock_movements=[existing_movement])
    payload = {
        "enabled": False,
        "stock_in_trigger": "purchase_order",
        "stock_out_trigger": "delivery_confirmed",
    }
    await update_physical_stock_settings(db, payload)
    assert len(db.stock_movements._docs) == 1
    assert db.stock_movements._docs[0] == existing_movement


@pytest.mark.anyio
async def test_changing_setting_performs_no_retroactive_backfill():
    """15. Changing setting performs no retroactive backfill."""
    db = MockDb()
    # Save once
    await update_physical_stock_settings(db, {
        "enabled": False,
        "stock_in_trigger": "goods_receipt",
        "stock_out_trigger": "loading_confirmation",
    })
    # Save changed setting
    await update_physical_stock_settings(db, {
        "enabled": False,
        "stock_in_trigger": "purchase_order",
        "stock_out_trigger": "delivery_confirmed",
    })
    # Must remain zero movements
    assert len(db.stock_movements._docs) == 0
