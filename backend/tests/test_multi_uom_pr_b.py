"""PR-B Multi-UOM + classification focused tests (Gate B1/B2)."""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from services.inventory_accounting import (
    ITEM_TYPE_INVENTORY,
    ITEM_TYPE_NON_INVENTORY,
    ITEM_TYPE_SERVICE,
    classify_inventory_item_type,
    is_physical_stock_item_type,
    resolve_conversion_from_setting,
    stock_uom_snapshot,
)
from services.product_unit_conversion import (
    BASE_UOM_PCS,
    UnitConversionError,
    canonicalize_to_base,
    get_product_unit_setting,
    master_has_stock_history,
    save_product_unit_setting,
    validate_conversion_factor,
)


class MockCollection:
    def __init__(self):
        self.docs = []

    async def find_one(self, query, projection=None):
        for d in self.docs:
            if self._match(d, query):
                res = dict(d)
                if projection and projection.get("_id") == 0:
                    res.pop("_id", None)
                return res
        return None

    def _match(self, d, query):
        for k, v in query.items():
            if k == "$or":
                if not any(self._match(d, cond) for cond in v):
                    return False
            elif d.get(k) != v:
                return False
        return True

    async def update_one(self, filter_q, update, upsert=False):
        doc = await self.find_one(filter_q)
        if doc:
            for d in self.docs:
                if self._match(d, filter_q):
                    if "$set" in update:
                        d.update(update["$set"])
                    return
        elif upsert:
            new_doc = dict(filter_q)
            if "$set" in update:
                new_doc.update(update["$set"])
            self.docs.append(new_doc)

    async def insert_one(self, doc):
        d = dict(doc)
        self.docs.append(d)

    async def count_documents(self, query):
        return sum(1 for d in self.docs if self._match(d, query))


class MockDb:
    def __init__(self):
        self.master_items = MockCollection()
        self.product_unit_settings = MockCollection()
        self.stock_movements = MockCollection()
        self.physical_stock_ledger = MockCollection()
        self.physical_stock_balances = MockCollection()
        self.product_stock_state = MockCollection()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture
def db():
    return MockDb()


@pytest.fixture
def user():
    return {"id": "u1", "email": "a@example.com", "name": "Admin"}


async def _seed_inv(db, code="BOX"):
    item = {
        "id": f"item-{uuid.uuid4().hex[:8]}",
        "item_code": code,
        "item_name": code,
        "unit": "PCS",
        "item_type": ITEM_TYPE_INVENTORY,
    }
    await db.master_items.insert_one(item)
    return item


def test_classification_physical_stock():
    assert is_physical_stock_item_type(ITEM_TYPE_INVENTORY) is True
    assert is_physical_stock_item_type(ITEM_TYPE_SERVICE) is False
    assert is_physical_stock_item_type(ITEM_TYPE_NON_INVENTORY) is False
    assert is_physical_stock_item_type(None) is True  # legacy blank
    assert is_physical_stock_item_type("") is True
    ok, reason = classify_inventory_item_type(ITEM_TYPE_NON_INVENTORY)
    assert ok is False and reason


def test_validate_factor_rejects_bad():
    with pytest.raises(ValueError):
        validate_conversion_factor(0)
    with pytest.raises(ValueError):
        validate_conversion_factor(-1)
    with pytest.raises(ValueError):
        validate_conversion_factor("x")
    assert validate_conversion_factor(500) == 500


def test_canonicalize_crt_and_pack():
    setting = {
        "base_uom": "PCS",
        "alternate_uoms": [
            {"uom": "CRT", "factor_to_base": 500},
            {"uom": "PACK", "factor_to_base": 50},
        ],
    }
    snap = canonicalize_to_base(
        transaction_qty=2, transaction_uom="CRT", setting=setting,
    )
    assert snap["base_qty"] == 1000
    assert snap["base_uom"] == BASE_UOM_PCS
    assert snap["conversion_factor"] == 500
    snap2 = canonicalize_to_base(
        transaction_qty=3, transaction_uom="PACK", setting=setting,
    )
    assert snap2["base_qty"] == 150


def test_canonicalize_unknown_and_fractional_and_double():
    setting = {
        "base_uom": "PCS",
        "alternate_uoms": [{"uom": "CRT", "factor_to_base": 500}],
    }
    with pytest.raises(UnitConversionError) as e1:
        canonicalize_to_base(transaction_qty=1, transaction_uom="BOX", setting=setting)
    assert e1.value.code == "UNKNOWN_UOM"
    with pytest.raises(UnitConversionError) as e2:
        canonicalize_to_base(
            transaction_qty=0.5, transaction_uom="CRT",
            setting={"base_uom": "PCS", "alternate_uoms": [{"uom": "CRT", "factor_to_base": 3}]},
        )
    # 0.5 * 3 = 1.5 fractional PCS
    assert e2.value.code == "FRACTIONAL_BASE_QTY"
    with pytest.raises(UnitConversionError) as e3:
        canonicalize_to_base(
            transaction_qty=10, transaction_uom="CRT", setting=setting, already_base=True,
        )
    assert e3.value.code == "DOUBLE_CONVERSION"
    # already in PCS OK
    snap = canonicalize_to_base(
        transaction_qty=10, transaction_uom="PCS", setting=setting, already_base=True,
    )
    assert snap["base_qty"] == 10


@pytest.mark.anyio
async def test_save_alternate_uoms_and_duplicate_reject(db, user):
    item = await _seed_inv(db)
    saved = await save_product_unit_setting(
        db,
        item["id"],
        {
            "base_uom": "PCS",
            "alternate_uoms": [
                {"uom": "PACK", "factor_to_base": 50},
                {"uom": "CRT", "factor_to_base": 500},
            ],
        },
        user,
    )
    assert saved["base_uom"] == "PCS"
    assert len(saved["alternate_uoms"]) == 2
    assert saved["transaction_uom"] == "PACK"  # legacy primary
    assert saved["conversion_factor"] == 50

    with pytest.raises(HTTPException) as exc:
        await save_product_unit_setting(
            db,
            item["id"],
            {
                "alternate_uoms": [
                    {"uom": "PACK", "factor_to_base": 50},
                    {"uom": "pack", "factor_to_base": 60},
                ],
            },
            user,
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_base_change_blocked_by_physical_ledger(db, user):
    item = await _seed_inv(db)
    await save_product_unit_setting(
        db, item["id"], {"base_uom": "PCS", "transaction_uom": "PCS", "conversion_factor": 1}, user,
    )
    await db.physical_stock_ledger.insert_one({
        "master_item_id": item["id"], "qty_delta": 10,
    })
    assert await master_has_stock_history(db, item["id"], item) is True
    with pytest.raises(HTTPException) as exc:
        await save_product_unit_setting(
            db, item["id"], {"base_uom": "BOX", "transaction_uom": "BOX", "conversion_factor": 1}, user,
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_factor_change_does_not_rewrite_old_snapshot(db, user):
    item = await _seed_inv(db)
    await save_product_unit_setting(
        db,
        item["id"],
        {"alternate_uoms": [{"uom": "CRT", "factor_to_base": 500}]},
        user,
    )
    old = stock_uom_snapshot(
        transaction_qty=2,
        transaction_uom="CRT",
        setting=await get_product_unit_setting(db, item["id"]),
        master=item,
    )
    assert old["base_qty"] == 1000
    # Change factor for FUTURE only — old snapshot dict unchanged
    await save_product_unit_setting(
        db,
        item["id"],
        {"alternate_uoms": [{"uom": "CRT", "factor_to_base": 600}]},
        user,
    )
    assert old["base_qty"] == 1000
    assert old["conversion_factor"] == 500
    new = stock_uom_snapshot(
        transaction_qty=2,
        transaction_uom="CRT",
        setting=await get_product_unit_setting(db, item["id"]),
        master=item,
    )
    assert new["base_qty"] == 1200


def test_resolve_conversion_uses_alternates():
    setting = {
        "base_uom": "PCS",
        "conversion_factor": 50,
        "transaction_uom": "PACK",
        "alternate_uoms": [
            {"uom": "PACK", "factor_to_base": 50},
            {"uom": "CRT", "factor_to_base": 500},
        ],
    }
    factor, tx, base, err = resolve_conversion_from_setting(
        master={"unit": "PCS"}, setting=setting, transaction_uom="CRT",
    )
    assert err is None and factor == 500 and tx == "CRT" and base == "PCS"
    factor2, _, _, err2 = resolve_conversion_from_setting(
        master={"unit": "PCS"}, setting=setting, transaction_uom="BOX",
    )
    assert err2 is not None and factor2 is None


def test_po_crt_gr_pcs_no_double_convert():
    """Document in CRT; ledger boundary already in PCS → already_base."""
    setting = {
        "base_uom": "PCS",
        "alternate_uoms": [{"uom": "CRT", "factor_to_base": 500}],
    }
    # PO line: 2 CRT → snapshot 1000 PCS stored on doc
    po_snap = canonicalize_to_base(transaction_qty=2, transaction_uom="CRT", setting=setting)
    assert po_snap["base_qty"] == 1000
    # GR posts using base_qty already PCS — must not apply CRT again
    gr_snap = canonicalize_to_base(
        transaction_qty=po_snap["base_qty"],
        transaction_uom="PCS",
        setting=setting,
        already_base=True,
    )
    assert gr_snap["base_qty"] == 1000
    with pytest.raises(UnitConversionError):
        canonicalize_to_base(
            transaction_qty=po_snap["base_qty"],
            transaction_uom="CRT",
            setting=setting,
            already_base=True,
        )


@pytest.fixture
def anyio_backend():
    return "asyncio"
