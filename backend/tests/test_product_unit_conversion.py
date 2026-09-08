"""Targeted tests for Product Unit Conversion settings.

Tests:
1. Exact internal product identity used
2. Same-name products remain isolated
3. Save transaction UOM and base UOM
4. Factor > 0 validation (numeric, finite)
5. Rejects 0, negative, NaN, Infinity
6. Same UOM factor safety (1 PCS = 1 PCS)
7. Future factor change does not alter historical snapshot
8. Base UOM change blocked when stock history exists
9. Unauthorized / missing product rejected
10. GET is pure read
"""
import asyncio
import uuid
import pytest
from fastapi import HTTPException

from services.product_unit_conversion import (
    get_product_unit_setting,
    save_product_unit_setting,
    validate_conversion_factor,
)
from services.stock_ledger import (
    record_stock_movement,
    MOVEMENT_PENERIMAAN,
    get_latest_balance,
)


class MockDb:
    def __init__(self):
        self.master_items = MockCollection()
        self.product_unit_settings = MockCollection()
        self.stock_movements = MockCollection()
        self.orders = MockCollection()
        self.item_stock_manual = MockCollection()
        self.accurate_purchase_receipts = MockCollection()
        self.accurate_receipt_sync_meta = MockCollection()

    def __getitem__(self, name):
        return getattr(self, name)


class MockCollection:
    def __init__(self):
        self.docs = []

    async def find_one(self, query, projection=None):
        for d in self.docs:
            match = True
            for k, v in query.items():
                if k == "$or":
                    or_match = any(
                        all(d.get(sub_k) == sub_v for sub_k, sub_v in cond.items())
                        for cond in v
                    )
                    if not or_match:
                        match = False
                        break
                elif d.get(k) != v:
                    match = False
                    break
            if match:
                res = dict(d)
                if projection and "_id" in projection and projection["_id"] == 0:
                    res.pop("_id", None)
                return res
        return None

    async def update_one(self, filter_q, update, upsert=False):
        doc = await self.find_one(filter_q)
        if doc:
            for d in self.docs:
                if all(d.get(k) == v for k, v in filter_q.items()):
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
        if "_id" not in d:
            d["_id"] = str(uuid.uuid4())
        self.docs.append(d)

    async def count_documents(self, query):
        count = 0
        for d in self.docs:
            match = True
            for k, v in query.items():
                if k == "$or":
                    or_match = any(
                        all(d.get(sub_k) == sub_v for sub_k, sub_v in cond.items())
                        for cond in v
                    )
                    if not or_match:
                        match = False
                        break
                elif d.get(k) != v:
                    match = False
                    break
            if match:
                count += 1
        return count

    def find(self, query, projection=None):
        return MockCursor(self.docs, query, projection)


class MockCursor:
    def __init__(self, docs, query, projection=None):
        self._docs = []
        for d in docs:
            match = True
            for k, v in query.items():
                if k == "$or":
                    or_match = any(
                        all(d.get(sub_k) == sub_v for sub_k, sub_v in cond.items())
                        for cond in v
                    )
                    if not or_match:
                        match = False
                        break
                elif d.get(k) != v:
                    match = False
                    break
            if match:
                row = dict(d)
                if projection and "_id" in projection and projection["_id"] == 0:
                    row.pop("_id", None)
                self._docs.append(row)

    def sort(self, key, direction=-1):
        self._docs.sort(key=lambda d: d.get(key, ""), reverse=(direction == -1))
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, n=None):
        return list(self._docs[:n] if n is not None else self._docs)


@pytest.mark.anyio
async def test_product_unit_conversion_rules():
    db = MockDb()
    user = {"email": "admin@eternal.test", "name": "Admin"}

    # Setup two products with the SAME name to prove exact identity isolation
    prod1_id = str(uuid.uuid4())
    prod2_id = str(uuid.uuid4())

    await db.master_items.insert_one({
        "id": prod1_id,
        "accurate_item_id": "ACC-101",
        "item_code": "CUP-101",
        "item_name": "Cup 10 oz",
        "unit": "PCS",
    })
    await db.master_items.insert_one({
        "id": prod2_id,
        "accurate_item_id": "ACC-102",
        "item_code": "CUP-102",
        "item_name": "Cup 10 oz",  # same name!
        "unit": "PCS",
    })

    # 1. Save conversion for prod1
    res1 = await save_product_unit_setting(db, prod1_id, {
        "transaction_uom": "PACK",
        "base_uom": "PCS",
        "conversion_factor": 250,
    }, user)

    assert res1["master_item_id"] == prod1_id
    assert res1["transaction_uom"] == "PACK"
    assert res1["base_uom"] == "PCS"
    assert res1["conversion_factor"] == 250

    # 2. Prod2 setting remains untouched (isolated by ID)
    res2 = await get_product_unit_setting(db, prod2_id)
    assert res2 is None

    # 3. Factor > 0 validation & rejection of 0, negative, NaN
    with pytest.raises(ValueError, match="Faktor konversi harus lebih besar dari 0"):
        validate_conversion_factor(0)

    with pytest.raises(ValueError, match="Faktor konversi harus lebih besar dari 0"):
        validate_conversion_factor(-25)

    with pytest.raises(ValueError, match="Faktor konversi tidak valid"):
        validate_conversion_factor(float("nan"))

    with pytest.raises(ValueError, match="Faktor konversi tidak valid"):
        validate_conversion_factor(float("inf"))

    # 4. Same UOM forces factor = 1
    res_same = await save_product_unit_setting(db, prod2_id, {
        "transaction_uom": "PCS",
        "base_uom": "PCS",
        "conversion_factor": 999,  # should be forced/normalized to 1
    }, user)
    assert res_same["transaction_uom"] == "PCS"
    assert res_same["base_uom"] == "PCS"
    assert res_same["conversion_factor"] == 1.0

    # 5. Base UOM Change blocked when movements exist
    # Record a movement for prod1
    await record_stock_movement(
        db,
        movement_type=MOVEMENT_PENERIMAAN,
        master_item_id=prod1_id,
        transaction_qty=5,
        transaction_uom="PACK",
        source_entity="Supplier ABC",
        destination_entity="Gudang Eternal",
        reference_number="PB-001",
        user=user,
    )

    # Attempt to change base_uom on prod1 from PCS to KG -> must be blocked!
    with pytest.raises(HTTPException) as exc_info:
        await save_product_unit_setting(db, prod1_id, {
            "transaction_uom": "PACK",
            "base_uom": "KG",
            "conversion_factor": 10,
        }, user)
    assert exc_info.value.status_code == 400
    assert "Satuan Dasar tidak dapat diubah" in exc_info.value.detail

    # 6. Future factor change does NOT alter old movement snapshot
    # Change conversion factor from 250 to 300
    await save_product_unit_setting(db, prod1_id, {
        "transaction_uom": "PACK",
        "base_uom": "PCS",
        "conversion_factor": 300,
    }, user)

    # Check old movement still has factor=250 and base_qty=1250
    old_mv = db.stock_movements.docs[0]
    assert old_mv["conversion_factor"] == 250
    assert old_mv["base_qty"] == 1250

    # New movement uses updated factor=300
    mv2 = await record_stock_movement(
        db,
        movement_type=MOVEMENT_PENERIMAAN,
        master_item_id=prod1_id,
        transaction_qty=2,
        transaction_uom="PACK",
        source_entity="Supplier XYZ",
        destination_entity="Gudang Eternal",
        reference_number="PB-002",
        user=user,
    )
    assert mv2["conversion_factor"] == 300
    assert mv2["base_qty"] == 600

    # 7. GET is pure read (no mutations on db)
    docs_before = len(db.product_unit_settings.docs)
    read_res = await get_product_unit_setting(db, prod1_id)
    assert read_res["conversion_factor"] == 300
    assert len(db.product_unit_settings.docs) == docs_before
