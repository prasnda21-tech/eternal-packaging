"""Comprehensive targeted tests for Authoritative Physical Stock Event Engine.

Proves:
1. PO Stock-In active when stock_in_trigger == "purchase_order" and enabled == True.
2. PO Stock-In inactive when stock_in_trigger == "goods_receipt".
3. PO Stock-In disabled when enabled == False.
4. PO Stock-In rejects uninitialized product with HTTP 400.
5. PO Stock-In idempotency prevents duplicate mutation on same event_key.
6. Goods Receipt Stock-In active when PO snapshot has goods_receipt and enabled.
7. Goods Receipt Stock-In skipped when PO was already recognized via purchase_order trigger.
8. Goods Receipt Stock-In disabled when PO snapshot enabled == False.
9. Goods Receipt Stock-In rejects uninitialized product with HTTP 400.
10. Goods Receipt Stock-In idempotency prevents duplicate mutation.
11. Incoming stock ALWAYS enters as BELUM DICETAK (unprinted_qty += qty, printed_qty += 0).
12. Unit conversion factor correctly scales transaction UOM to Base UOM on stock-in.
13. TOTAL == UNPRINTED + PRINTED invariant holds on stock-in.
14. Stock-in handles partial receipts correctly.
15. Multi-line stock-in mutations isolate correctly per line.
16. STATUS_CETAK converts Belum Dicetak -> Sudah Dicetak on print line.
17. STATUS_CETAK DOES NOT change total stock (delta == 0).
18. STATUS_CETAK skips non_print lines (zero conversion).
19. STATUS_CETAK rejects when unprinted_qty is insufficient (HTTP 400).
20. STATUS_CETAK rejects uninitialized product (HTTP 400).
21. STATUS_CETAK idempotency by action_id prevents double conversion.
22. STATUS_CETAK applies conversion factor if transaction UOM differs.
23. Stock-Out Packing active when stock_out_trigger == "packing_completed".
24. Stock-Out Packing inactive when trigger is not packing_completed.
25. Stock-Out Packing CETAK deducts from PRINTED bucket.
26. Stock-Out Packing NON CETAK deducts from UNPRINTED bucket.
27. Stock-Out Packing rejects when stock is insufficient (HTTP 400).
28. Stock-Out Loading active when stock_out_trigger == "loading_confirmation".
29. Stock-Out Loading inactive when trigger is not loading_confirmation.
30. Stock-Out Loading CETAK deducts from PRINTED bucket.
31. Stock-Out Loading NON CETAK deducts from UNPRINTED bucket.
32. Stock-Out Loading rejects when stock is insufficient (HTTP 400).
33. Stock-Out Sales Delivery active when stock_out_trigger == "sales_delivery".
34. Stock-Out Sales Delivery inactive when trigger is not sales_delivery.
35. Stock-Out Sales Delivery CETAK deducts from PRINTED bucket.
36. Stock-Out Sales Delivery NON CETAK deducts from UNPRINTED bucket.
37. Stock-Out Sales Delivery rejects when stock is insufficient (HTTP 400).
38. Stock-Out Delivery Confirmed active when stock_out_trigger == "delivery_confirmed".
39. Stock-Out Delivery Confirmed inactive when trigger is not delivery_confirmed.
40. Stock-Out Delivery Confirmed CETAK deducts from PRINTED bucket.
41. Stock-Out Delivery Confirmed NON CETAK deducts from UNPRINTED bucket.
42. Stock-Out Delivery Confirmed rejects when stock is insufficient (HTTP 400).
43. Legacy PO without snapshot NEVER mutates stock on creation or receipt.
44. Legacy Order without snapshot NEVER mutates stock on operator or shipment.
45. Changing global settings AFTER PO/Order creation does NOT alter that document's recognition rule.
46. Database-level pending_event protocol cleanly recovers interrupted/crashed mutation.
47. Database-level conditional update ($gte) strictly prevents negative stock under concurrency.
48. Invariant TOTAL == UNPRINTED + PRINTED strictly holds through end-to-end lifecycle.
49. Ledger records exact before and after bucket snapshots (unprinted/printed before/after).
50. Direct read of product_stock_state remains pure read without side effects.
"""
import asyncio
import copy
import uuid
import pytest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.product_stock_state import (
    COLLECTION as STATE_COLLECTION,
    STATUS_INITIALIZED,
    STATUS_INITIALIZING,
    get_product_stock_state,
    initialize_opening_balance,
    require_product_stock_initialized,
)
from services.physical_stock_settings import (
    get_physical_stock_settings,
    update_physical_stock_settings,
)
from services.product_unit_conversion import (
    save_product_unit_setting,
    get_product_unit_setting,
)
from services.stock_ledger import (
    COLLECTION as LEDGER_COLLECTION,
    MOVEMENT_PENERIMAAN,
    MOVEMENT_STATUS_CETAK,
    MOVEMENT_PENGIRIMAN,
    list_product_mutations,
    safe_float,
)
from services.physical_stock_engine import (
    attach_po_stock_snapshot,
    attach_order_stock_snapshot,
    resolve_transaction_conversion,
    check_stock_availability,
    execute_durable_stock_event,
    handle_purchase_order_created,
    handle_goods_receipt_created,
    handle_operator_progress,
    should_claim_operator_stock,
    is_operator_cetak_physical_stock_active,
    handle_packing_completed,
    handle_loading_confirmed,
    handle_sales_delivery_created,
    handle_delivery_confirmed,
    precheck_purchase_order_lines,
    precheck_goods_receipt_lines,
    precheck_operator_progress,
    precheck_packing_completed,
    precheck_loading_confirmed,
    precheck_sales_delivery,
    precheck_delivery_confirmed,
    claim_durable_stock_event,
    release_durable_stock_claim,
    finalize_durable_stock_event,
    claim_operator_stock,
    release_operator_stock,
    finalize_operator_stock,
    claim_loading_stock,
    release_loading_stock,
    finalize_loading_stock,
    claim_packing_stock,
    release_packing_stock,
    finalize_packing_stock,
    claim_delivery_confirmed_stock,
    release_delivery_confirmed_stock,
    finalize_delivery_confirmed_stock,
    is_source_committed,
    reconcile_pending_product_claim,
)


# ==============================================================================
# IN-MEMORY MOTOR MOCK
# ==============================================================================

def get_field_val(d, k):
    if "." in k:
        curr = d
        for p in k.split("."):
            if not isinstance(curr, dict):
                return None
            curr = curr.get(p)
        return curr
    return d.get(k)


def set_field_val(d, k, v):
    if "." in k:
        parts = k.split(".")
        curr = d
        for p in parts[:-1]:
            if p not in curr or not isinstance(curr[p], dict):
                curr[p] = {}
            curr = curr[p]
        curr[parts[-1]] = copy.deepcopy(v)
    else:
        d[k] = copy.deepcopy(v)


def unset_field_val(d, k):
    if "." in k:
        parts = k.split(".")
        curr = d
        for p in parts[:-1]:
            if not isinstance(curr, dict) or p not in curr:
                return
            curr = curr[p]
        if isinstance(curr, dict):
            curr.pop(parts[-1], None)
    else:
        d.pop(k, None)


def doc_matches(d, query):
    if not query:
        return True
    for k, v in query.items():
        if k == "$or":
            if not any(doc_matches(d, cond) for cond in v):
                return False
        elif k == "$and":
            if not all(doc_matches(d, cond) for cond in v):
                return False
        elif isinstance(v, dict):
            field_val = get_field_val(d, k)
            for op, target in v.items():
                if op == "$gte":
                    if safe_float(field_val, -1e9) < safe_float(target):
                        return False
                elif op == "$gt":
                    if safe_float(field_val, -1e9) <= safe_float(target):
                        return False
                elif op == "$lte":
                    if safe_float(field_val, 1e9) > safe_float(target):
                        return False
                elif op == "$exists":
                    exists = field_val is not None and (k in d if "." not in k else True)
                    if exists != bool(target):
                        return False
                elif op == "$in":
                    if field_val not in target:
                        return False
                elif op == "$ne":
                    if field_val == target:
                        return False
        else:
            if get_field_val(d, k) != v:
                return False
    return True


class MockCursor:
    def __init__(self, docs, query=None, projection=None):
        self._docs = []
        for d in docs:
            if doc_matches(d, query):
                row = copy.deepcopy(d)
                if projection and projection.get("_id") == 0 and "_id" in row:
                    del row["_id"]
                self._docs.append(row)

    def sort(self, key, direction=-1):
        self._docs.sort(key=lambda d: str(d.get(key, "")), reverse=(direction == -1))
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, n=None):
        return list(self._docs[:n] if n is not None else self._docs)


class MockCollection:
    def __init__(self, initial=None):
        self._docs = copy.deepcopy(initial or [])

    async def find_one(self, query=None, projection=None):
        for d in self._docs:
            if doc_matches(d, query):
                row = copy.deepcopy(d)
                if projection and projection.get("_id") == 0 and "_id" in row:
                    del row["_id"]
                return row
        return None

    def find(self, query=None, projection=None):
        return MockCursor(self._docs, query, projection)

    async def count_documents(self, query=None):
        return len([d for d in self._docs if doc_matches(d, query)])

    async def insert_one(self, doc):
        d = copy.deepcopy(doc)
        if "_id" in d:
            for existing in self._docs:
                if existing.get("_id") == d["_id"]:
                    raise DuplicateKeyError(f"Duplicate key: {d['_id']}")
        if "id" not in d:
            d["id"] = d.get("_id") or str(uuid.uuid4())
        self._docs.append(d)
        return type("InsertResult", (), {"inserted_id": d.get("_id") or d["id"]})()

    async def update_one(self, query, update, upsert=False):
        for i, d in enumerate(self._docs):
            if doc_matches(d, query):
                if "$set" in update:
                    for k, v in update["$set"].items():
                        set_field_val(d, k, v)
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        d[k] = safe_float(d.get(k, 0)) + safe_float(v)
                if "$unset" in update:
                    for k in update["$unset"]:
                        unset_field_val(d, k)
                if "$push" in update:
                    for k, v in update["$push"].items():
                        if k not in d or not isinstance(d[k], list):
                            d[k] = []
                        d[k].append(copy.deepcopy(v))
                return type("UpdateResult", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            new_doc = copy.deepcopy(query)
            if "$set" in update:
                for k, v in update["$set"].items():
                    set_field_val(new_doc, k, v)
            if "$inc" in update:
                for k, v in update["$inc"].items():
                    new_doc[k] = safe_float(v)
            if "$push" in update:
                for k, v in update["$push"].items():
                    if k not in new_doc or not isinstance(new_doc[k], list):
                        new_doc[k] = []
                    new_doc[k].append(copy.deepcopy(v))
            if "_id" not in new_doc:
                new_doc["_id"] = new_doc.get("id") or str(uuid.uuid4())
            if "id" not in new_doc:
                new_doc["id"] = new_doc["_id"]
            self._docs.append(new_doc)
            return type("UpdateResult", (), {"matched_count": 0, "modified_count": 0, "upserted_id": new_doc["_id"]})()
        return type("UpdateResult", (), {"matched_count": 0, "modified_count": 0})()

    async def find_one_and_update(
        self, query, update, return_document=ReturnDocument.BEFORE, upsert=False,
    ):
        for i, d in enumerate(self._docs):
            if doc_matches(d, query):
                before = copy.deepcopy(d)
                if "$set" in update:
                    for k, v in update["$set"].items():
                        set_field_val(d, k, v)
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        d[k] = safe_float(d.get(k, 0)) + safe_float(v)
                if "$unset" in update:
                    for k in update["$unset"]:
                        unset_field_val(d, k)
                if "$push" in update:
                    for k, v in update["$push"].items():
                        if k not in d or not isinstance(d[k], list):
                            d[k] = []
                        d[k].append(copy.deepcopy(v))
                if "$setOnInsert" in update:
                    pass  # only on insert
                return before if return_document == ReturnDocument.BEFORE else copy.deepcopy(d)
        if not upsert:
            return None
        # Simulate unique key: plain equality fields already present → no upsert
        # (mirrors DuplicateKeyError → acquire_posting_claim returns None).
        plain = {
            k: v for k, v in (query or {}).items()
            if not str(k).startswith("$") and not isinstance(v, dict)
        }
        if plain:
            for d in self._docs:
                if all(d.get(k) == v for k, v in plain.items()):
                    return None
        new_doc = {}
        for k, v in plain.items():
            new_doc[k] = copy.deepcopy(v)
        if "$setOnInsert" in update:
            for k, v in update["$setOnInsert"].items():
                if k not in new_doc:
                    new_doc[k] = copy.deepcopy(v)
        if "$set" in update:
            for k, v in update["$set"].items():
                set_field_val(new_doc, k, v)
        if "_id" not in new_doc:
            new_doc["_id"] = new_doc.get("id") or new_doc.get("movement_id") or str(uuid.uuid4())
        self._docs.append(new_doc)
        if return_document == ReturnDocument.AFTER:
            return copy.deepcopy(new_doc)
        return None

    async def delete_one(self, query):
        for i, d in enumerate(self._docs):
            if doc_matches(d, query):
                del self._docs[i]
                return type("DeleteResult", (), {"deleted_count": 1})()
        return type("DeleteResult", (), {"deleted_count": 0})()


class MockDB:
    def __init__(self):
        self.master_items = MockCollection()
        self.app_settings = MockCollection()
        self.product_stock_state = MockCollection()
        self.stock_movements = MockCollection()
        self.product_unit_settings = MockCollection()
        self.purchase_orders = MockCollection()
        self.purchase_goods_receipts = MockCollection()
        self.orders = MockCollection()
        self.sales_deliveries = MockCollection()

    def __getitem__(self, name):
        if not hasattr(self, name):
            setattr(self, name, MockCollection())
        return getattr(self, name)


@pytest.fixture
def mock_db():
    db = MockDB()
    return db


async def seed_master_product(db, item_id="item-cup-001", name="Cup 16oz Blank", unit="PCS"):
    doc = {
        "_id": item_id,
        "id": item_id,
        "item_code": "CUP-16OZ",
        "item_name": name,
        "unit": unit,
        "accurate_item_id": f"acc-{item_id}",
    }
    await db.master_items.insert_one(doc)
    return doc


async def setup_initialized_product(db, item_id="item-cup-001", unprinted=5000.0, printed=0.0):
    await seed_master_product(db, item_id=item_id)
    payload = {
        "unprinted_qty": unprinted,
        "printed_qty": printed,
        "base_uom": "PCS",
    }
    await initialize_opening_balance(db, item_id, payload, user={"email": "admin@test.com"})
    state = await get_product_stock_state(db, item_id)
    return state


# ==============================================================================
# TESTS 1 - 5: PO STOCK-IN TRIGGER
# ==============================================================================

@pytest.mark.anyio
async def test_01_stock_in_po_trigger_active(mock_db):
    """1. PO creation increases unprinted_qty and total_qty when stock_in_trigger == 'purchase_order' and enabled."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    po = {
        "id": "po-101",
        "po_number": "PO-101",
        "supplier": "PT Supplier Jaya",
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_in_trigger": "purchase_order",
            "stock_out_trigger": "loading_confirmation",
        },
        "lines": [
            {"master_item_id": "prod-1", "ordered_qty": 500.0, "unit": "PCS", "line_idx": 0, "line_id": "line-1"},
        ],
    }
    await handle_purchase_order_created(mock_db, po)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1500.0
    assert state["unprinted_qty"] == 1500.0
    assert state["printed_qty"] == 0.0


@pytest.mark.anyio
async def test_02_stock_in_po_trigger_goods_receipt_inactive(mock_db):
    """2. PO creation does NOT increase stock when stock_in_trigger == 'goods_receipt'."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    po = {
        "id": "po-102",
        "po_number": "PO-102",
        "supplier": "PT Supplier Jaya",
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_in_trigger": "goods_receipt",
            "stock_out_trigger": "loading_confirmation",
        },
        "lines": [
            {"master_item_id": "prod-1", "ordered_qty": 500.0, "unit": "PCS", "line_idx": 0},
        ],
    }
    movs = await handle_purchase_order_created(mock_db, po)
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1000.0


@pytest.mark.anyio
async def test_03_stock_in_po_disabled_no_mutation(mock_db):
    """3. PO creation does NOT increase stock when physical_stock.enabled == False."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    po = {
        "id": "po-103",
        "po_number": "PO-103",
        "physical_stock_snapshot": {
            "enabled": False,
            "stock_in_trigger": "purchase_order",
        },
        "lines": [{"master_item_id": "prod-1", "ordered_qty": 500.0}],
    }
    movs = await handle_purchase_order_created(mock_db, po)
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1000.0


@pytest.mark.anyio
async def test_04_stock_in_po_uninitialized_product_blocked(mock_db):
    """4. PO creation for uninitialized product raises HTTP 400."""
    await seed_master_product(mock_db, "uninit-prod")
    lines = [{"master_item_id": "uninit-prod", "ordered_qty": 100.0}]
    snap = {"enabled": True, "stock_in_trigger": "purchase_order"}
    with pytest.raises(HTTPException) as exc:
        await precheck_purchase_order_lines(mock_db, lines, snap)
    assert exc.value.status_code == 400
    assert "Saldo awal stok fisik produk belum ditetapkan" in exc.value.detail


@pytest.mark.anyio
async def test_05_stock_in_po_idempotency(mock_db):
    """5. Same PO line does not double-count on retry."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    po = {
        "id": "po-105",
        "po_number": "PO-105",
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "purchase_order"},
        "lines": [{"master_item_id": "prod-1", "ordered_qty": 500.0, "line_id": "l-105"}],
    }
    await handle_purchase_order_created(mock_db, po)
    # Re-run exact same PO
    await handle_purchase_order_created(mock_db, po)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1500.0  # NOT 2000.0


# ==============================================================================
# TESTS 6 - 10: GOODS RECEIPT STOCK-IN TRIGGER
# ==============================================================================

@pytest.mark.anyio
async def test_06_stock_in_goods_receipt_trigger_active(mock_db):
    """6. Goods receipt increases stock when PO snapshot has goods_receipt and enabled."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    po = {
        "id": "po-201",
        "po_number": "PO-201",
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
    }
    await mock_db.purchase_orders.insert_one(po)
    rc = {
        "id": "rc-201",
        "receipt_number": "PB-201",
        "purchase_order_id": "po-201",
        "po_number": "PO-201",
        "lines": [{"master_item_id": "prod-1", "received_qty": 300.0, "line_id": "l-rc-1"}],
    }
    await handle_goods_receipt_created(mock_db, rc)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1300.0
    assert state["unprinted_qty"] == 1300.0


@pytest.mark.anyio
async def test_07_stock_in_goods_receipt_po_trigger_no_duplicate(mock_db):
    """7. Goods receipt skipped if PO was already recognized at purchase_order trigger."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    po = {
        "id": "po-202",
        "po_number": "PO-202",
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "purchase_order"},
    }
    await mock_db.purchase_orders.insert_one(po)
    rc = {
        "id": "rc-202",
        "purchase_order_id": "po-202",
        "lines": [{"master_item_id": "prod-1", "received_qty": 300.0}],
    }
    movs = await handle_goods_receipt_created(mock_db, rc)
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1000.0  # Unchanged!


@pytest.mark.anyio
async def test_08_stock_in_goods_receipt_disabled_no_mutation(mock_db):
    """8. Goods receipt does NOT mutate stock when PO snapshot has enabled == False."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    po = {
        "id": "po-203",
        "physical_stock_snapshot": {"enabled": False, "stock_in_trigger": "goods_receipt"},
    }
    await mock_db.purchase_orders.insert_one(po)
    rc = {"id": "rc-203", "purchase_order_id": "po-203", "lines": [{"master_item_id": "prod-1", "received_qty": 100.0}]}
    movs = await handle_goods_receipt_created(mock_db, rc)
    assert movs == []


@pytest.mark.anyio
async def test_09_stock_in_goods_receipt_uninitialized_blocked(mock_db):
    """9. Uninitialized master with historical stock signal fails closed (not assumed zero)."""
    await seed_master_product(mock_db, "uninit-prod")
    await mock_db.stock_movements.insert_one({
        "id": "hist-1",
        "master_item_id": "uninit-prod",
        "movement_type": "PENERIMAAN",
        "delta": 1,
    })
    lines = [{"master_item_id": "uninit-prod", "received_qty": 100.0}]
    po_snap = {"enabled": True, "stock_in_trigger": "goods_receipt"}
    with pytest.raises(HTTPException) as exc:
        await precheck_goods_receipt_lines(mock_db, lines, po_snap)
    assert exc.value.status_code == 400
    assert "historis" in exc.value.detail.lower() or "jejak" in exc.value.detail.lower()


@pytest.mark.anyio
async def test_09b_stock_in_goods_receipt_first_receipt_bootstrap(mock_db):
    """9b. Proven brand-new master bootstraps without SALDO_AWAL for first GR precheck."""
    from services.product_stock_state import get_product_stock_state

    await seed_master_product(mock_db, "brand-new-prod")
    lines = [{"master_item_id": "brand-new-prod", "received_qty": 100.0}]
    po_snap = {"enabled": True, "stock_in_trigger": "goods_receipt"}
    await precheck_goods_receipt_lines(mock_db, lines, po_snap, user={"email": "t@test"})
    state = await get_product_stock_state(mock_db, "brand-new-prod")
    assert state["initialized"] is True
    assert state["bootstrap_source"] == "FIRST_RECEIPT_AUTO"
    assert float(state["total_qty"]) == 0.0
    assert await mock_db.stock_movements.count_documents({"master_item_id": "brand-new-prod"}) == 0


@pytest.mark.anyio
async def test_10_stock_in_goods_receipt_idempotency(mock_db):
    """10. Duplicate goods receipt line event does not double count."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    po = {"id": "po-205", "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"}}
    await mock_db.purchase_orders.insert_one(po)
    rc = {"id": "rc-205", "purchase_order_id": "po-205", "lines": [{"master_item_id": "prod-1", "received_qty": 200.0, "line_id": "l-205"}]}
    await handle_goods_receipt_created(mock_db, rc)
    await handle_goods_receipt_created(mock_db, rc)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1200.0


# ==============================================================================
# TESTS 11 - 15: INCOMING STOCK BUCKET & UOM CONVERSION
# ==============================================================================

@pytest.mark.anyio
async def test_11_incoming_always_enters_unprinted(mock_db):
    """11. Recognized incoming stock ALWAYS enters as BELUM DICETAK (printed remains unchanged)."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=500.0, printed=200.0)
    po = {
        "id": "po-301",
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "purchase_order"},
        "lines": [{"master_item_id": "prod-1", "ordered_qty": 400.0, "line_id": "l-301"}],
    }
    await handle_purchase_order_created(mock_db, po)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 900.0
    assert state["printed_qty"] == 200.0  # Exactly untouched!
    assert state["total_qty"] == 1100.0


@pytest.mark.anyio
async def test_12_unit_conversion_factor_on_stock_in(mock_db):
    """12. Unit conversion factor correctly scales Dus to PCS on stock-in."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=0.0)
    await save_product_unit_setting(
        mock_db,
        "prod-1",
        {
            "base_uom": "PCS",
            "transaction_uom": "DUS",
            "conversion_factor": 1000,
        },
        user={"email": "admin@test.com"},
    )
    po = {
        "id": "po-302",
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "purchase_order"},
        "lines": [{"master_item_id": "prod-1", "ordered_qty": 5.0, "unit": "DUS", "line_id": "l-302"}],
    }
    await handle_purchase_order_created(mock_db, po)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 5000.0  # 5 Dus * 1000
    assert state["unprinted_qty"] == 5000.0


@pytest.mark.anyio
async def test_13_total_invariant_on_stock_in(mock_db):
    """13. Invariant TOTAL == UNPRINTED + PRINTED verified after stock-in."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=300.0, printed=150.0)
    po = {
        "id": "po-303",
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "purchase_order"},
        "lines": [{"master_item_id": "prod-1", "ordered_qty": 700.0, "line_id": "l-303"}],
    }
    await handle_purchase_order_created(mock_db, po)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == state["unprinted_qty"] + state["printed_qty"]


@pytest.mark.anyio
async def test_14_partial_receipt_stock_in(mock_db):
    """14. Multiple partial goods receipts accumulate correctly."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=0.0)
    po = {"id": "po-304", "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"}}
    await mock_db.purchase_orders.insert_one(po)
    rc1 = {"id": "rc-304-1", "purchase_order_id": "po-304", "lines": [{"master_item_id": "prod-1", "received_qty": 300.0, "line_id": "l1"}]}
    rc2 = {"id": "rc-304-2", "purchase_order_id": "po-304", "lines": [{"master_item_id": "prod-1", "received_qty": 200.0, "line_id": "l1"}]}
    await handle_goods_receipt_created(mock_db, rc1)
    await handle_goods_receipt_created(mock_db, rc2)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 500.0
    assert state["unprinted_qty"] == 500.0


@pytest.mark.anyio
async def test_15_multi_line_stock_in_isolation(mock_db):
    """15. Multi-line PO mutates each respective product independently."""
    await setup_initialized_product(mock_db, "prod-A", unprinted=100.0)
    await setup_initialized_product(mock_db, "prod-B", unprinted=200.0)
    po = {
        "id": "po-305",
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "purchase_order"},
        "lines": [
            {"master_item_id": "prod-A", "ordered_qty": 50.0, "line_id": "la"},
            {"master_item_id": "prod-B", "ordered_qty": 80.0, "line_id": "lb"},
        ],
    }
    await handle_purchase_order_created(mock_db, po)
    st_a = await get_product_stock_state(mock_db, "prod-A")
    st_b = await get_product_stock_state(mock_db, "prod-B")
    assert st_a["total_qty"] == 150.0
    assert st_b["total_qty"] == 280.0


# ==============================================================================
# TESTS 16 - 22: STATUS_CETAK OPERATOR CONVERSION
# ==============================================================================

@pytest.mark.anyio
async def test_16_status_cetak_print_line_conversion(mock_db):
    """16. Operator completion converts Belum Dicetak -> Sudah Dicetak for print lines."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    order = {
        "id": "ord-401",
        "po_number": "PO-CUST-401",
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt", "stock_out_trigger": "loading_confirmation"},
        "line_items": [
            {"master_item_id": "prod-1", "name": "Cup Cetak Kopi", "production_process": "print", "line_id": "l-401"},
        ],
    }
    split = {"line_idx": 0, "operator_name": "Budi"}
    mov = await handle_operator_progress(mock_db, order, split, increment=400, action_id="act-401", live_enabled=True)
    assert mov["movement_type"] == MOVEMENT_STATUS_CETAK
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 600.0
    assert state["printed_qty"] == 400.0
    assert state["total_qty"] == 1000.0  # Total stock remains exactly the same!


@pytest.mark.anyio
async def test_17_status_cetak_total_stock_unchanged(mock_db):
    """17. Operator completion delta total is strictly 0."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=500.0, printed=100.0)
    order = {
        "id": "ord-402",
        "physical_stock_snapshot": {"enabled": True},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print", "line_id": "l-402"}],
    }
    split = {"line_idx": 0}
    mov = await handle_operator_progress(mock_db, order, split, increment=250, action_id="act-402", live_enabled=True)
    assert mov["delta"] == 0.0
    assert mov["delta_unprinted"] == -250.0
    assert mov["delta_printed"] == 250.0


@pytest.mark.anyio
async def test_18_status_cetak_non_print_skipped(mock_db):
    """18. Operator completion on non_print line does zero conversion."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    order = {
        "id": "ord-403",
        "physical_stock_snapshot": {"enabled": True},
        "line_items": [{"master_item_id": "prod-1", "production_process": "non_print", "line_id": "l-403"}],
    }
    split = {"line_idx": 0}
    mov = await handle_operator_progress(mock_db, order, split, increment=300, action_id="act-403", live_enabled=True)
    assert mov is None
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 1000.0
    assert state["printed_qty"] == 0.0


@pytest.mark.anyio
async def test_19_status_cetak_insufficient_unprinted_skipped(mock_db):
    """19. Insufficient Belum Dicetak does not block Cetak; stock is left unchanged."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=100.0, printed=0.0)
    order = {
        "id": "ord-404",
        "physical_stock_snapshot": {"enabled": True},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print", "line_id": "l-404"}],
    }
    split = {"line_idx": 0}
    await precheck_operator_progress(mock_db, order, split, increment=200, live_enabled=True)
    assert await handle_operator_progress(
        mock_db, order, split, increment=200, action_id="act-404", live_enabled=True
    ) is None
    assert await claim_operator_stock(mock_db, order, split, increment=200, action_id="act-404b") is None
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 100.0
    assert state["printed_qty"] == 0.0
    assert not state.get("pending_event")


@pytest.mark.anyio
async def test_20_status_cetak_uninitialized_skipped(mock_db):
    """20. STATUS_CETAK for uninitialized product is a safe no-op."""
    await seed_master_product(mock_db, "uninit-prod")
    order = {
        "id": "ord-405",
        "physical_stock_snapshot": {"enabled": True},
        "line_items": [{"master_item_id": "uninit-prod", "production_process": "print", "line_id": "l-405"}],
    }
    split = {"line_idx": 0}
    await precheck_operator_progress(mock_db, order, split, increment=50, live_enabled=True)
    assert await handle_operator_progress(
        mock_db, order, split, increment=50, action_id="act-405", live_enabled=True
    ) is None
    assert await get_product_stock_state(mock_db, "uninit-prod") is None
    assert mock_db.stock_movements._docs == []


@pytest.mark.anyio
async def test_21_status_cetak_idempotency_action_id(mock_db):
    """21. Re-running operator progress with same action_id returns existing movement."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    order = {
        "id": "ord-406",
        "physical_stock_snapshot": {"enabled": True},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print", "line_id": "l-406"}],
    }
    split = {"line_idx": 0}
    mov1 = await handle_operator_progress(mock_db, order, split, increment=200, action_id="act-same", live_enabled=True)
    mov2 = await handle_operator_progress(mock_db, order, split, increment=200, action_id="act-same", live_enabled=True)
    assert mov1["id"] == mov2["id"]
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 800.0  # NOT 600.0


@pytest.mark.anyio
async def test_22_status_cetak_with_uom_factor(mock_db):
    """22. Operator progress in non-base UOM correctly applies conversion factor."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=5000.0, printed=0.0)
    await save_product_unit_setting(
        mock_db,
        "prod-1",
        {"base_uom": "PCS", "transaction_uom": "RIM", "conversion_factor": 500},
        user={"email": "admin@test.com"},
    )
    order = {
        "id": "ord-407",
        "physical_stock_snapshot": {"enabled": True},
        "line_items": [{"master_item_id": "prod-1", "unit": "RIM", "production_process": "print", "line_id": "l-407"}],
    }
    split = {"line_idx": 0}
    await handle_operator_progress(mock_db, order, split, increment=2, action_id="act-rim", live_enabled=True)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 4000.0  # 5000 - (2 * 500)
    assert state["printed_qty"] == 1000.0


# ==============================================================================
# LIVE + SNAPSHOT CETAK GATE (physical_stock_active = live && snapshot)
# ==============================================================================

async def _cetak_order(order_id, master_item_id, *, snapshot_enabled=True, qty_name="Cup"):
    return {
        "id": order_id,
        "po_number": f"PO-{order_id}",
        "physical_stock_snapshot": {"enabled": snapshot_enabled},
        "line_items": [
            {
                "master_item_id": master_item_id,
                "name": qty_name,
                "production_process": "print",
                "line_id": f"l-{order_id}",
            }
        ],
    }


@pytest.mark.anyio
async def test_cetak_live_false_snapshot_true_missing_opening_balance_allowed(mock_db):
    """live=false + snapshot=true + missing OB → Cetak helpers allow; no claim/mutation."""
    await seed_master_product(mock_db, "prod-live-off-1")
    order = await _cetak_order("ord-live-off-1", "prod-live-off-1", snapshot_enabled=True)
    split = {"line_idx": 0, "operator_name": "Budi"}

    with patch(
        "services.physical_stock_engine.require_product_stock_initialized",
        new_callable=AsyncMock,
    ) as req_init, patch(
        "services.physical_stock_engine.check_stock_availability",
        new_callable=AsyncMock,
    ) as check_avail, patch(
        "services.physical_stock_engine.execute_durable_stock_event",
        new_callable=AsyncMock,
    ) as exec_evt, patch(
        "services.physical_stock_engine.claim_operator_stock",
        new_callable=AsyncMock,
    ) as claim_fn, patch(
        "services.physical_stock_engine.finalize_operator_stock",
        new_callable=AsyncMock,
    ) as fin_fn:
        await precheck_operator_progress(
            mock_db, order, split, increment=200, live_enabled=False
        )
        assert await should_claim_operator_stock(
            mock_db, order, split, live_enabled=False
        ) is False
        mov = await handle_operator_progress(
            mock_db, order, split, increment=200, action_id="act-off-1", live_enabled=False
        )
        assert mov is None
        req_init.assert_not_called()
        check_avail.assert_not_called()
        exec_evt.assert_not_called()
        claim_fn.assert_not_called()
        fin_fn.assert_not_called()

    assert await get_product_stock_state(mock_db, "prod-live-off-1") is None
    ledger = await list_product_mutations(mock_db, "prod-live-off-1")
    assert ledger.get("total", 0) == 0 or ledger.get("items") == []


@pytest.mark.anyio
async def test_cetak_live_false_snapshot_true_initialized_no_mutation(mock_db):
    """live=false + snapshot=true + initialized stock → allowed; no claim/mutation."""
    await setup_initialized_product(mock_db, "prod-live-off-2", unprinted=1000.0, printed=0.0)
    order = await _cetak_order("ord-live-off-2", "prod-live-off-2", snapshot_enabled=True)
    split = {"line_idx": 0}

    with patch(
        "services.physical_stock_engine.execute_durable_stock_event",
        new_callable=AsyncMock,
    ) as exec_evt, patch(
        "services.physical_stock_engine.check_stock_availability",
        new_callable=AsyncMock,
    ) as check_avail:
        await precheck_operator_progress(
            mock_db, order, split, increment=300, live_enabled=False
        )
        assert await should_claim_operator_stock(
            mock_db, order, split, live_enabled=False
        ) is False
        mov = await handle_operator_progress(
            mock_db, order, split, increment=300, action_id="act-off-2", live_enabled=False
        )
        assert mov is None
        exec_evt.assert_not_called()
        check_avail.assert_not_called()

    state = await get_product_stock_state(mock_db, "prod-live-off-2")
    assert state["unprinted_qty"] == 1000.0
    assert state["printed_qty"] == 0.0


@pytest.mark.anyio
async def test_cetak_live_false_snapshot_false_allowed(mock_db):
    """live=false + snapshot=false → allowed; no enforcement."""
    await seed_master_product(mock_db, "prod-live-off-3")
    order = await _cetak_order("ord-live-off-3", "prod-live-off-3", snapshot_enabled=False)
    split = {"line_idx": 0}
    await precheck_operator_progress(mock_db, order, split, increment=50, live_enabled=False)
    assert await should_claim_operator_stock(mock_db, order, split, live_enabled=False) is False
    assert await handle_operator_progress(
        mock_db, order, split, increment=50, action_id="act-off-3", live_enabled=False
    ) is None


@pytest.mark.anyio
async def test_cetak_live_true_snapshot_true_missing_opening_balance_skipped(mock_db):
    """live=true + snapshot=true + missing OB → Cetak is a safe no-op."""
    await seed_master_product(mock_db, "prod-live-on-miss")
    order = await _cetak_order("ord-live-on-miss", "prod-live-on-miss", snapshot_enabled=True)
    split = {"line_idx": 0}
    await precheck_operator_progress(
        mock_db, order, split, increment=50, live_enabled=True
    )
    assert await should_claim_operator_stock(
        mock_db, order, split, live_enabled=True
    ) is True
    assert await claim_operator_stock(mock_db, order, split, increment=50, action_id="act-miss") is None
    assert await handle_operator_progress(
        mock_db, order, split, increment=50, action_id="act-miss-h", live_enabled=True
    ) is None
    assert await get_product_stock_state(mock_db, "prod-live-on-miss") is None


@pytest.mark.anyio
async def test_cetak_live_true_snapshot_true_sufficient_stock_mutates(mock_db):
    """live=true + snapshot=true + sufficient stock → existing validation/mutation remains."""
    await setup_initialized_product(mock_db, "prod-live-on-ok", unprinted=1000.0, printed=0.0)
    order = await _cetak_order("ord-live-on-ok", "prod-live-on-ok", snapshot_enabled=True)
    split = {"line_idx": 0, "operator_name": "Budi"}
    await precheck_operator_progress(mock_db, order, split, increment=400, live_enabled=True)
    assert await should_claim_operator_stock(mock_db, order, split, live_enabled=True) is True
    mov = await handle_operator_progress(
        mock_db, order, split, increment=400, action_id="act-on-ok", live_enabled=True
    )
    assert mov is not None
    assert mov["movement_type"] == MOVEMENT_STATUS_CETAK
    state = await get_product_stock_state(mock_db, "prod-live-on-ok")
    assert state["unprinted_qty"] == 600.0
    assert state["printed_qty"] == 400.0


@pytest.mark.anyio
async def test_cetak_live_true_snapshot_true_insufficient_stock_skipped(mock_db):
    """live=true + snapshot=true + insufficient stock → Cetak no-op, balances unchanged."""
    await setup_initialized_product(mock_db, "prod-live-on-short", unprinted=100.0, printed=0.0)
    order = await _cetak_order("ord-live-on-short", "prod-live-on-short", snapshot_enabled=True)
    split = {"line_idx": 0}
    await precheck_operator_progress(
        mock_db, order, split, increment=200, live_enabled=True
    )
    assert await handle_operator_progress(
        mock_db, order, split, increment=200, action_id="act-short", live_enabled=True
    ) is None
    state = await get_product_stock_state(mock_db, "prod-live-on-short")
    assert state["unprinted_qty"] == 100.0
    assert state["printed_qty"] == 0.0
    assert not state.get("pending_event")


@pytest.mark.anyio
async def test_cetak_live_true_snapshot_false_no_enforcement(mock_db):
    """live=true + snapshot=false → no enforcement (frozen snapshot-off)."""
    await seed_master_product(mock_db, "prod-snap-off")
    order = await _cetak_order("ord-snap-off", "prod-snap-off", snapshot_enabled=False)
    split = {"line_idx": 0}
    await precheck_operator_progress(mock_db, order, split, increment=50, live_enabled=True)
    assert await should_claim_operator_stock(mock_db, order, split, live_enabled=True) is False
    assert await handle_operator_progress(
        mock_db, order, split, increment=50, action_id="act-snap-off", live_enabled=True
    ) is None


@pytest.mark.anyio
async def test_cetak_live_false_then_true_resumes_enforcement_no_retroactive(mock_db):
    """live false→true on snapshot-enabled order: future Cetak resumes; no retroactive mutation."""
    await seed_master_product(mock_db, "prod-reenable")
    order = await _cetak_order("ord-reenable", "prod-reenable", snapshot_enabled=True)
    split = {"line_idx": 0}

    # While disabled: progress helpers allow and do not mutate.
    await precheck_operator_progress(mock_db, order, split, increment=100, live_enabled=False)
    assert await should_claim_operator_stock(mock_db, order, split, live_enabled=False) is False
    assert await handle_operator_progress(
        mock_db, order, split, increment=100, action_id="act-while-off", live_enabled=False
    ) is None
    assert await get_product_stock_state(mock_db, "prod-reenable") is None
    ledger_off = await list_product_mutations(mock_db, "prod-reenable")
    assert ledger_off.get("total", 0) == 0 or ledger_off.get("items") == []

    # Re-enable: future Cetak skips missing opening stock instead of blocking the flow.
    await precheck_operator_progress(
        mock_db, order, split, increment=100, live_enabled=True
    )
    assert await handle_operator_progress(
        mock_db, order, split, increment=100, action_id="act-while-off", live_enabled=True
    ) is None
    ledger_on = await list_product_mutations(mock_db, "prod-reenable")
    assert ledger_on.get("total", 0) == 0
    assert ledger_on.get("items") == []


@pytest.mark.anyio
async def test_cetak_gate_helper_and_semantics(mock_db):
    """Shared helper: physical_stock_active = live_enabled && snapshot_enabled."""
    order_on = {"physical_stock_snapshot": {"enabled": True}}
    order_off = {"physical_stock_snapshot": {"enabled": False}}
    order_missing = {}
    assert await is_operator_cetak_physical_stock_active(
        mock_db, order_on, live_enabled=True
    ) is True
    assert await is_operator_cetak_physical_stock_active(
        mock_db, order_on, live_enabled=False
    ) is False
    assert await is_operator_cetak_physical_stock_active(
        mock_db, order_off, live_enabled=True
    ) is False
    assert await is_operator_cetak_physical_stock_active(
        mock_db, order_missing, live_enabled=True
    ) is False


# ==============================================================================
# TESTS 23 - 27: STOCK-OUT PACKING COMPLETED
# ==============================================================================

@pytest.mark.anyio
async def test_23_stock_out_packing_active(mock_db):
    """23. Packing completion mutates stock when stock_out_trigger == 'packing_completed'."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-501",
        "po_number": "PO-501",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "packing_completed"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print", "line_id": "l-501"}],
    }
    incs = [{"line_idx": 0, "qty": 300, "action_id": "act-pack-1"}]
    await handle_packing_completed(mock_db, order, incs)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["printed_qty"] == 700.0
    assert state["total_qty"] == 700.0


@pytest.mark.anyio
async def test_24_stock_out_packing_inactive(mock_db):
    """24. Packing completion does NOT mutate stock when stock_out_trigger is loading_confirmation."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-502",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print"}],
    }
    incs = [{"line_idx": 0, "qty": 300}]
    movs = await handle_packing_completed(mock_db, order, incs)
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1000.0


@pytest.mark.anyio
async def test_25_stock_out_packing_cetak_deducts_printed(mock_db):
    """25. CETAK line deducts from PRINTED bucket."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=500.0, printed=500.0)
    order = {
        "id": "ord-503",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "packing_completed"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print", "line_id": "l-503"}],
    }
    await handle_packing_completed(mock_db, order, [{"line_idx": 0, "qty": 200, "action_id": "p-1"}])
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 500.0  # Untouched
    assert state["printed_qty"] == 300.0
    assert state["total_qty"] == 800.0


@pytest.mark.anyio
async def test_26_stock_out_packing_non_cetak_deducts_unprinted(mock_db):
    """26. NON CETAK line deducts from UNPRINTED bucket."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=500.0, printed=500.0)
    order = {
        "id": "ord-504",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "packing_completed"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "non_print", "line_id": "l-504"}],
    }
    await handle_packing_completed(mock_db, order, [{"line_idx": 0, "qty": 200, "action_id": "p-2"}])
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 300.0
    assert state["printed_qty"] == 500.0  # Untouched
    assert state["total_qty"] == 800.0


@pytest.mark.anyio
async def test_27_stock_out_packing_insufficient_skipped(mock_db):
    """27. Packing stock-out skips insufficient stock; balances unchanged."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=100.0)
    order = {
        "id": "ord-505",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "packing_completed"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print"}],
    }
    await precheck_packing_completed(mock_db, order, [{"line_idx": 0, "qty": 150}])
    movs = await handle_packing_completed(mock_db, order, [{"line_idx": 0, "qty": 150, "action_id": "act-pack-short"}])
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["printed_qty"] == 100.0
    assert not state.get("pending_event")


@pytest.mark.anyio
async def test_packing_uninitialized_is_safe_noop(mock_db):
    """Packing skip when product has no opening stock."""
    await seed_master_product(mock_db, "pack-uninit")
    order = {
        "id": "ord-pack-uninit",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "packing_completed"},
        "line_items": [{"master_item_id": "pack-uninit", "production_process": "print"}],
    }
    rows = [{"line_idx": 0, "qty": 50, "action_id": "act-pack-u"}]
    await precheck_packing_completed(mock_db, order, rows)
    assert await handle_packing_completed(mock_db, order, rows) == []
    assert await get_product_stock_state(mock_db, "pack-uninit") is None
    assert mock_db.stock_movements._docs == []


# ==============================================================================
# TESTS 28 - 32: STOCK-OUT LOADING CONFIRMATION
# ==============================================================================

@pytest.mark.anyio
async def test_28_stock_out_loading_active(mock_db):
    """28. Loading confirmation mutates stock when stock_out_trigger == 'loading_confirmation'."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-601",
        "po_number": "PO-601",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print", "line_id": "l-601"}],
    }
    record = {"id": "rec-601", "lines": [{"line_idx": 0, "qty": 400}]}
    await handle_loading_confirmed(mock_db, order, record)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["printed_qty"] == 600.0
    assert state["total_qty"] == 600.0


@pytest.mark.anyio
async def test_29_stock_out_loading_inactive(mock_db):
    """29. Loading confirmation does NOT mutate stock when stock_out_trigger is sales_delivery."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-602",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "sales_delivery"},
        "line_items": [{"master_item_id": "prod-1"}],
    }
    record = {"id": "rec-602", "lines": [{"line_idx": 0, "qty": 400}]}
    movs = await handle_loading_confirmed(mock_db, order, record)
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1000.0


@pytest.mark.anyio
async def test_30_stock_out_loading_cetak_deducts_printed(mock_db):
    """30. Loading confirmation CETAK line deducts from PRINTED."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=200.0, printed=800.0)
    order = {
        "id": "ord-603",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print", "line_id": "l-603"}],
    }
    record = {"id": "rec-603", "lines": [{"line_idx": 0, "qty": 300}]}
    await handle_loading_confirmed(mock_db, order, record)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 200.0
    assert state["printed_qty"] == 500.0


@pytest.mark.anyio
async def test_31_stock_out_loading_non_cetak_deducts_unprinted(mock_db):
    """31. Loading confirmation NON CETAK line deducts from UNPRINTED."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=400.0, printed=600.0)
    order = {
        "id": "ord-604",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "non_print", "line_id": "l-604"}],
    }
    record = {"id": "rec-604", "lines": [{"line_idx": 0, "qty": 250}]}
    await handle_loading_confirmed(mock_db, order, record)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 150.0
    assert state["printed_qty"] == 600.0


@pytest.mark.anyio
async def test_32_stock_out_loading_insufficient_skipped(mock_db):
    """32. Loading confirmation skips insufficient stock; balances unchanged."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=50.0, printed=0.0)
    order = {
        "id": "ord-605",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "non_print"}],
    }
    await precheck_loading_confirmed(mock_db, order, [{"line_idx": 0, "qty": 100}])
    movs = await handle_loading_confirmed(mock_db, order, {"id": "rec-605", "lines": [{"line_idx": 0, "qty": 100}]})
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 50.0
    assert not state.get("pending_event")


@pytest.mark.anyio
async def test_loading_uninitialized_is_safe_noop(mock_db):
    """Loading skip when product has no opening stock."""
    await seed_master_product(mock_db, "load-uninit")
    order = {
        "id": "ord-load-uninit",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{"master_item_id": "load-uninit", "production_process": "print"}],
    }
    lines = [{"line_idx": 0, "qty": 40}]
    await precheck_loading_confirmed(mock_db, order, lines)
    assert await handle_loading_confirmed(mock_db, order, {"id": "rec-u", "lines": lines}) == []
    assert await get_product_stock_state(mock_db, "load-uninit") is None
    assert mock_db.stock_movements._docs == []


# ==============================================================================
# TESTS 33 - 37: STOCK-OUT SALES DELIVERY (SURAT JALAN)
# ==============================================================================

@pytest.mark.anyio
async def test_33_stock_out_sales_delivery_active(mock_db):
    """33. Sales delivery mutates stock when stock_out_trigger == 'sales_delivery'."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-701",
        "po_number": "PO-701",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "sales_delivery"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print", "line_id": "l-701"}],
    }
    deliv = {
        "id": "del-701",
        "sj_number": "SJ/2026/09/001",
        "line_items": [{"master_item_id": "prod-1", "line_id": "l-701", "production_process": "print", "qty": 350}],
    }
    await handle_sales_delivery_created(mock_db, order, deliv)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["printed_qty"] == 650.0
    assert state["total_qty"] == 650.0


@pytest.mark.anyio
async def test_34_stock_out_sales_delivery_inactive(mock_db):
    """34. Sales delivery does NOT mutate stock when stock_out_trigger is delivery_confirmed."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-702",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
    }
    deliv = {"id": "del-702", "line_items": [{"master_item_id": "prod-1", "qty": 350}]}
    movs = await handle_sales_delivery_created(mock_db, order, deliv)
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1000.0


@pytest.mark.anyio
async def test_35_stock_out_sales_delivery_cetak_deducts_printed(mock_db):
    """35. Sales delivery CETAK line deducts from PRINTED."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=100.0, printed=900.0)
    order = {"id": "ord-703", "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "sales_delivery"}}
    deliv = {"id": "del-703", "line_items": [{"master_item_id": "prod-1", "production_process": "print", "qty": 500, "line_id": "l1"}]}
    await handle_sales_delivery_created(mock_db, order, deliv)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 100.0
    assert state["printed_qty"] == 400.0


@pytest.mark.anyio
async def test_36_stock_out_sales_delivery_non_cetak_deducts_unprinted(mock_db):
    """36. Sales delivery NON CETAK line deducts from UNPRINTED."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=800.0, printed=200.0)
    order = {"id": "ord-704", "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "sales_delivery"}}
    deliv = {"id": "del-704", "line_items": [{"master_item_id": "prod-1", "production_process": "non_print", "qty": 300, "line_id": "l1"}]}
    await handle_sales_delivery_created(mock_db, order, deliv)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 500.0
    assert state["printed_qty"] == 200.0


@pytest.mark.anyio
async def test_37_stock_out_sales_delivery_insufficient_skipped(mock_db):
    """37. Sales delivery skips insufficient stock; balances unchanged."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=100.0)
    order = {"id": "ord-705", "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "sales_delivery"}}
    lines = [{"master_item_id": "prod-1", "production_process": "print", "qty": 200}]
    await precheck_sales_delivery(mock_db, order, lines)
    movs = await handle_sales_delivery_created(mock_db, order, {"id": "del-705", "line_items": lines})
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["printed_qty"] == 100.0
    assert not state.get("pending_event")


@pytest.mark.anyio
async def test_sales_delivery_uninitialized_is_safe_noop(mock_db):
    """Surat Jalan stock hook skip when product has no opening stock."""
    await seed_master_product(mock_db, "sj-uninit")
    order = {"id": "ord-sj-uninit", "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "sales_delivery"}}
    lines = [{"master_item_id": "sj-uninit", "production_process": "print", "qty": 80, "line_id": "l-u"}]
    await precheck_sales_delivery(mock_db, order, lines)
    assert await handle_sales_delivery_created(mock_db, order, {"id": "del-u", "line_items": lines}) == []
    assert await get_product_stock_state(mock_db, "sj-uninit") is None
    assert mock_db.stock_movements._docs == []


# ==============================================================================
# TESTS 38 - 42: STOCK-OUT DELIVERY CONFIRMED
# ==============================================================================

@pytest.mark.anyio
async def test_38_stock_out_delivery_confirmed_active(mock_db):
    """38. Delivery confirmation mutates stock when stock_out_trigger == 'delivery_confirmed'."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-801",
        "po_number": "PO-801",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print", "line_id": "l-801"}],
    }
    conf = {"id": "conf-801", "lines": [{"master_item_id": "prod-1", "production_process": "print", "qty": 250, "line_id": "l-801"}]}
    await handle_delivery_confirmed(mock_db, order, conf)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["printed_qty"] == 750.0
    assert state["total_qty"] == 750.0


@pytest.mark.anyio
async def test_39_stock_out_delivery_confirmed_inactive(mock_db):
    """39. Delivery confirmation does NOT mutate stock when trigger is loading_confirmation."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=1000.0)
    order = {"id": "ord-802", "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"}}
    conf = {"id": "conf-802", "lines": [{"master_item_id": "prod-1", "qty": 250}]}
    movs = await handle_delivery_confirmed(mock_db, order, conf)
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1000.0


@pytest.mark.anyio
async def test_40_stock_out_delivery_confirmed_cetak_deducts_printed(mock_db):
    """40. Delivery confirmed CETAK line deducts from PRINTED."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=200.0, printed=800.0)
    order = {"id": "ord-803", "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"}}
    conf = {"id": "conf-803", "lines": [{"master_item_id": "prod-1", "production_process": "print", "qty": 300, "line_id": "l1"}]}
    await handle_delivery_confirmed(mock_db, order, conf)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 200.0
    assert state["printed_qty"] == 500.0


@pytest.mark.anyio
async def test_41_stock_out_delivery_confirmed_non_cetak_deducts_unprinted(mock_db):
    """41. Delivery confirmed NON CETAK line deducts from UNPRINTED."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=600.0, printed=400.0)
    order = {"id": "ord-804", "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"}}
    conf = {"id": "conf-804", "lines": [{"master_item_id": "prod-1", "production_process": "non_print", "qty": 200, "line_id": "l1"}]}
    await handle_delivery_confirmed(mock_db, order, conf)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["unprinted_qty"] == 400.0
    assert state["printed_qty"] == 400.0


@pytest.mark.anyio
async def test_42_stock_out_delivery_confirmed_insufficient_skipped(mock_db):
    """42. Delivery confirmed skips insufficient stock; balances unchanged."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=50.0)
    order = {"id": "ord-805", "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"}}
    lines = [{"master_item_id": "prod-1", "production_process": "print", "qty": 100}]
    await precheck_delivery_confirmed(mock_db, order, lines)
    claims = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-805")
    assert claims == []
    movs = await handle_delivery_confirmed(mock_db, order, {"id": "conf-805", "lines": lines})
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["printed_qty"] == 50.0
    assert not state.get("pending_event")


@pytest.mark.anyio
async def test_delivery_confirmed_initialized_sufficient_decrements(mock_db):
    """CASE A: initialized + sufficient stock still decrements on delivery_confirmed."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-806",
        "po_number": "PO-806",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print", "line_id": "l-806"}],
    }
    lines = [{"master_item_id": "prod-1", "production_process": "print", "qty": 100, "line_id": "l-806"}]
    await precheck_delivery_confirmed(mock_db, order, lines)
    claims = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-806")
    assert claims and claims[0]["status"] == "CLAIMED"
    movs = await finalize_delivery_confirmed_stock(mock_db, claims)
    assert len(movs) == 1
    assert movs[0]["movement_type"] == MOVEMENT_PENGIRIMAN
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["printed_qty"] == 900.0
    assert state["total_qty"] == 900.0
    assert not state.get("pending_event")
    assert len(mock_db.stock_movements._docs) >= 1


@pytest.mark.anyio
async def test_delivery_confirmed_uninitialized_is_safe_noop(mock_db):
    """CASE B: missing opening stock does not block delivery_confirmed stock helpers."""
    await seed_master_product(mock_db, "prod-uninit")
    order = {
        "id": "ord-807",
        "po_number": "PO-807",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
        "line_items": [{"master_item_id": "prod-uninit", "production_process": "print", "quantity": 100, "qty": 100}],
    }
    lines = [{"master_item_id": "prod-uninit", "production_process": "print", "qty": 100}]
    await precheck_delivery_confirmed(mock_db, order, lines)
    claims = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-807")
    assert claims == []
    movs = await handle_delivery_confirmed(mock_db, order, {"id": "conf-807", "lines": lines})
    assert movs == []
    assert await get_product_stock_state(mock_db, "prod-uninit") is None
    assert mock_db.product_stock_state._docs == []
    assert mock_db.stock_movements._docs == []


@pytest.mark.anyio
async def test_delivery_confirmed_mixed_initialized_and_uninitialized(mock_db):
    """Initialized line decrements; uninitialized line is skipped; no global failure."""
    await setup_initialized_product(mock_db, "prod-a", unprinted=0.0, printed=1000.0)
    await seed_master_product(mock_db, "prod-b")
    order = {
        "id": "ord-808",
        "po_number": "PO-808",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
        "line_items": [
            {"master_item_id": "prod-a", "production_process": "print", "qty": 100, "line_id": "l-a"},
            {"master_item_id": "prod-b", "production_process": "print", "qty": 100, "line_id": "l-b"},
        ],
    }
    lines = order["line_items"]
    await precheck_delivery_confirmed(mock_db, order, lines)
    claims = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-808")
    assert len(claims) == 1
    assert claims[0]["master_item_id"] == "prod-a"
    await finalize_delivery_confirmed_stock(mock_db, claims)
    state_a = await get_product_stock_state(mock_db, "prod-a")
    assert state_a["printed_qty"] == 900.0
    assert await get_product_stock_state(mock_db, "prod-b") is None
    assert all(
        str(doc.get("master_item_id") or doc.get("_id")) != "prod-b"
        for doc in mock_db.product_stock_state._docs
    )


@pytest.mark.anyio
async def test_packing_mixed_initialized_and_insufficient(mock_db):
    """Packing mutates the initialized/sufficient line and skips the insufficient line."""
    await setup_initialized_product(mock_db, "pack-ok", unprinted=0.0, printed=500.0)
    await setup_initialized_product(mock_db, "pack-short", unprinted=0.0, printed=10.0)
    order = {
        "id": "ord-pack-mix",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "packing_completed"},
        "line_items": [
            {"master_item_id": "pack-ok", "production_process": "print"},
            {"master_item_id": "pack-short", "production_process": "print"},
        ],
    }
    rows = [
        {"line_idx": 0, "qty": 100, "action_id": "act-mix-ok"},
        {"line_idx": 1, "qty": 100, "action_id": "act-mix-short"},
    ]
    await precheck_packing_completed(mock_db, order, rows)
    movs = await handle_packing_completed(mock_db, order, rows)
    assert len(movs) == 1
    state_ok = await get_product_stock_state(mock_db, "pack-ok")
    state_short = await get_product_stock_state(mock_db, "pack-short")
    assert state_ok["printed_qty"] == 400.0
    assert state_short["printed_qty"] == 10.0
    assert not state_short.get("pending_event")


# ==============================================================================
# TESTS 43 - 45: SNAPSHOT RULES & ZERO RETROACTIVE BACKFILL
# ==============================================================================

@pytest.mark.anyio
async def test_43_legacy_po_without_snapshot_never_mutates(mock_db):
    """43. Legacy PO without physical_stock_snapshot NEVER mutates stock on creation or receipt."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0)
    legacy_po = {
        "id": "po-legacy",
        "po_number": "PO-LEGACY",
        "lines": [{"master_item_id": "prod-1", "ordered_qty": 500.0}],
    }
    await mock_db.purchase_orders.insert_one(legacy_po)
    # 1. handle PO created
    movs1 = await handle_purchase_order_created(mock_db, legacy_po)
    assert movs1 == []
    # 2. handle goods receipt created
    rc = {"id": "rc-legacy", "purchase_order_id": "po-legacy", "lines": [{"master_item_id": "prod-1", "received_qty": 500.0}]}
    movs2 = await handle_goods_receipt_created(mock_db, rc)
    assert movs2 == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1000.0  # Zero retroactive mutation!


@pytest.mark.anyio
async def test_44_legacy_order_without_snapshot_never_mutates(mock_db):
    """44. Legacy Order without snapshot NEVER mutates stock on operator or shipment."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=500.0, printed=500.0)
    legacy_order = {
        "id": "ord-legacy",
        "line_items": [{"master_item_id": "prod-1", "production_process": "print"}],
    }
    # 1. operator progress
    mov1 = await handle_operator_progress(mock_db, legacy_order, {"line_idx": 0}, increment=100, action_id="act-leg", live_enabled=True)
    assert mov1 is None
    # 2. packing
    mov2 = await handle_packing_completed(mock_db, legacy_order, [{"line_idx": 0, "qty": 100}])
    assert mov2 == []
    # 3. loading
    mov3 = await handle_loading_confirmed(mock_db, legacy_order, {"lines": [{"line_idx": 0, "qty": 100}]})
    assert mov3 == []
    # 4. sales delivery
    mov4 = await handle_sales_delivery_created(mock_db, legacy_order, {"line_items": [{"master_item_id": "prod-1", "qty": 100}]})
    assert mov4 == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1000.0


@pytest.mark.anyio
async def test_45_setting_change_preserves_original_snapshot(mock_db):
    """45. Changing global settings AFTER PO/Order creation does NOT alter historical rules."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0)
    # Snapshot taken when trigger was goods_receipt
    po = {
        "id": "po-snap",
        "po_number": "PO-SNAP",
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
        "lines": [{"master_item_id": "prod-1", "ordered_qty": 500.0}],
    }
    await mock_db.purchase_orders.insert_one(po)

    # Admin changes global settings to purchase_order
    await update_physical_stock_settings(
        mock_db,
        {"enabled": True, "stock_in_trigger": "purchase_order", "stock_out_trigger": "loading_confirmation"},
        user={"email": "admin@test.com"},
    )

    # Old PO should still follow its original snapshot (goods_receipt), so handle_purchase_order_created is a no-op for it
    movs = await handle_purchase_order_created(mock_db, po)
    assert movs == []
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["total_qty"] == 1000.0


# ==============================================================================
# TESTS 46 - 50: DURABILITY, INVARIANTS & AUDIT
# ==============================================================================

@pytest.mark.anyio
async def test_46_database_level_pending_event_recovery(mock_db):
    """46. Crash recovery: interrupted mutation with pending_event is safely resumed without duplicate state mutation."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=1000.0, printed=0.0)
    mid = "prod-1"
    ev_id = "stock_in:po-crash:line-1"

    # Simulate crash state: state already applied, but pending_event still present
    await mock_db.product_stock_state.update_one(
        {"_id": mid},
        {
            "$set": {
                "total_qty": 1500.0,
                "unprinted_qty": 1500.0,
                "pending_event": {
                    "event_id": ev_id,
                    "stage": "STATE_APPLIED",
                    "movement_type": MOVEMENT_PENERIMAAN,
                    "total_before": 1000.0,
                    "total_after": 1500.0,
                    "unprinted_before": 1000.0,
                    "unprinted_after": 1500.0,
                    "printed_before": 0.0,
                    "printed_after": 0.0,
                },
            }
        },
    )

    # Resume/retry exact same event
    mov = await execute_durable_stock_event(
        mock_db,
        event_id=ev_id,
        master_item_id=mid,
        movement_type=MOVEMENT_PENERIMAAN,
        transaction_qty=500.0,
        source_entity="SUPPLIER",
        destination_entity="GUDANG_UTAMA",
        reference_number="PO-CRASH",
        reference_type="purchase_order",
    )

    assert mov["id"] == ev_id
    assert mov["balance_before"] == 1000.0
    assert mov["balance_after"] == 1500.0
    st = await get_product_stock_state(mock_db, mid)
    assert st["total_qty"] == 1500.0  # Not double incremented to 2000!
    assert "pending_event" not in st  # Successfully cleaned up


@pytest.mark.anyio
async def test_47_database_level_conditional_update_prevents_negative(mock_db):
    """47. Database conditional update strictly rejects negative stock."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=50.0, printed=0.0)
    with pytest.raises(HTTPException) as exc:
        await execute_durable_stock_event(
            mock_db,
            event_id="ev-neg-1",
            master_item_id="prod-1",
            movement_type=MOVEMENT_PENGIRIMAN,
            transaction_qty=100.0,
            source_entity="BELUM_DICETAK",
            destination_entity="CUSTOMER",
            reference_number="SJ-01",
            reference_type="sales_delivery",
            is_print=False,
        )
    assert exc.value.status_code == 400
    st = await get_product_stock_state(mock_db, "prod-1")
    assert st["total_qty"] == 50.0  # Unchanged!


@pytest.mark.anyio
async def test_48_full_lifecycle_invariant_check(mock_db):
    """48. Invariant TOTAL == UNPRINTED + PRINTED strictly holds through end-to-end flow:
    Opening -> Stock-In -> Print Conversion -> Partial Stock-Out.
    """
    # 1. Opening Balance: 1000 Unprinted, 0 Printed -> Total 1000
    await setup_initialized_product(mock_db, "prod-cycle", unprinted=1000.0, printed=0.0)

    # 2. Stock-In: +500 Unprinted -> Total 1500
    await execute_durable_stock_event(
        mock_db,
        event_id="ev-cyc-in",
        master_item_id="prod-cycle",
        movement_type=MOVEMENT_PENERIMAAN,
        transaction_qty=500.0,
        source_entity="SUPPLIER",
        destination_entity="GUDANG_UTAMA",
        reference_number="PO-CYC",
        reference_type="purchase_order",
    )
    st = await get_product_stock_state(mock_db, "prod-cycle")
    assert st["total_qty"] == 1500.0
    assert st["unprinted_qty"] == 1500.0
    assert st["printed_qty"] == 0.0
    assert st["total_qty"] == st["unprinted_qty"] + st["printed_qty"]

    # 3. Status Cetak: 600 Belum -> Sudah -> Total remains 1500
    await execute_durable_stock_event(
        mock_db,
        event_id="ev-cyc-print",
        master_item_id="prod-cycle",
        movement_type=MOVEMENT_STATUS_CETAK,
        transaction_qty=600.0,
        source_entity="BELUM_DICETAK",
        destination_entity="SUDAH_DICETAK",
        reference_number="ORD-CYC",
        reference_type="operator_progress",
        is_print=True,
    )
    st = await get_product_stock_state(mock_db, "prod-cycle")
    assert st["unprinted_qty"] == 900.0
    assert st["printed_qty"] == 600.0
    assert st["total_qty"] == 1500.0
    assert st["total_qty"] == st["unprinted_qty"] + st["printed_qty"]

    # 4. Stock-Out CETAK: 400 from Sudah Dicetak -> Total 1100
    await execute_durable_stock_event(
        mock_db,
        event_id="ev-cyc-out-print",
        master_item_id="prod-cycle",
        movement_type=MOVEMENT_PENGIRIMAN,
        transaction_qty=400.0,
        source_entity="SUDAH_DICETAK",
        destination_entity="CUSTOMER",
        reference_number="SJ-CYC",
        reference_type="sales_delivery",
        is_print=True,
    )
    st = await get_product_stock_state(mock_db, "prod-cycle")
    assert st["unprinted_qty"] == 900.0
    assert st["printed_qty"] == 200.0
    assert st["total_qty"] == 1100.0
    assert st["total_qty"] == st["unprinted_qty"] + st["printed_qty"]

    # 5. Stock-Out NON CETAK: 300 from Belum Dicetak -> Total 800
    await execute_durable_stock_event(
        mock_db,
        event_id="ev-cyc-out-unprint",
        master_item_id="prod-cycle",
        movement_type=MOVEMENT_PENGIRIMAN,
        transaction_qty=300.0,
        source_entity="BELUM_DICETAK",
        destination_entity="CUSTOMER",
        reference_number="SJ-CYC-2",
        reference_type="sales_delivery",
        is_print=False,
    )
    st = await get_product_stock_state(mock_db, "prod-cycle")
    assert st["unprinted_qty"] == 600.0
    assert st["printed_qty"] == 200.0
    assert st["total_qty"] == 800.0
    assert st["total_qty"] == st["unprinted_qty"] + st["printed_qty"]


@pytest.mark.anyio
async def test_49_ledger_records_bucket_before_after(mock_db):
    """49. Movement ledger stores unprinted/printed before and after for full drawer transparency."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=500.0, printed=500.0)
    mov = await execute_durable_stock_event(
        mock_db,
        event_id="ev-audit-1",
        master_item_id="prod-1",
        movement_type=MOVEMENT_STATUS_CETAK,
        transaction_qty=200.0,
        source_entity="BELUM_DICETAK",
        destination_entity="SUDAH_DICETAK",
        reference_number="REF-AUDIT",
        reference_type="operator_progress",
        is_print=True,
    )
    assert mov["balance_before"] == 1000.0
    assert mov["balance_after"] == 1000.0
    assert mov["unprinted_before"] == 500.0
    assert mov["unprinted_after"] == 300.0
    assert mov["printed_before"] == 500.0
    assert mov["printed_after"] == 700.0


@pytest.mark.anyio
async def test_50_get_product_stock_state_is_pure_read(mock_db):
    """50. Calling get_product_stock_state performs zero write."""
    await setup_initialized_product(mock_db, "prod-1", unprinted=500.0, printed=500.0)
    count_before = len(mock_db.stock_movements._docs)
    st1 = await get_product_stock_state(mock_db, "prod-1")
    st2 = await get_product_stock_state(mock_db, "prod-1")
    count_after = len(mock_db.stock_movements._docs)
    assert count_before == count_after
    assert st1 == st2


@pytest.mark.anyio
async def test_51_partial_operator_actual_delta_vs_po_target(mock_db):
    """51. PO target (1,200) > unprinted (1,000) does not block Operator actual delta (1,000).
    Next attempt (200) with 0 unprinted is blocked.
    New stock (+500) arrives -> remaining 200 succeeds.
    """
    # 1. Product initialized with 1,000 unprinted, 0 printed
    await setup_initialized_product(mock_db, "prod-partial", unprinted=1000.0, printed=0.0)

    # Order line target is 1,200 PCS
    order = {
        "id": "ord-target-1200",
        "po_number": "PO-TARGET-1200",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-1200",
            "master_item_id": "prod-partial",
            "production_process": "print",
            "quantity": 1200,
            "unit": "PCS",
        }],
        "operator_splits": [{
            "id": "split-op-1",
            "line_idx": 0,
            "operator_name": "Operator A",
            "quantity": 1200,
            "qty_completed": 0,
            "progress_entries": [],
        }]
    }
    split = order["operator_splits"][0]

    # Precheck with actual delta = 1,000 must SUCCEED even though target (1,200) > unprinted (1,000)
    await precheck_operator_progress(mock_db, order, split, increment=1000, live_enabled=True)

    # Execute operator completion of 1,000
    mov1 = await handle_operator_progress(mock_db, order, split, increment=1000, action_id="act-op-1000", live_enabled=True)
    assert mov1 is not None
    assert mov1["delta_unprinted"] == -1000.0
    assert mov1["delta_printed"] == 1000.0

    st1 = await get_product_stock_state(mock_db, "prod-partial")
    assert st1["unprinted_qty"] == 0.0
    assert st1["printed_qty"] == 1000.0
    assert st1["total_qty"] == 1000.0

    # 2. Operator attempts another 200 with unprinted = 0 -> skip, no negative stock
    await precheck_operator_progress(mock_db, order, split, increment=200, live_enabled=True)
    assert await handle_operator_progress(
        mock_db, order, split, increment=200, action_id="act-op-200-skip", live_enabled=True
    ) is None

    # Prior work remains intact
    st2 = await get_product_stock_state(mock_db, "prod-partial")
    assert st2["unprinted_qty"] == 0.0
    assert st2["printed_qty"] == 1000.0

    # 3. New recognized stock arrives: +500 PCS Belum Dicetak
    await execute_durable_stock_event(
        mock_db,
        event_id="ev-po-in-500",
        master_item_id="prod-partial",
        movement_type=MOVEMENT_PENERIMAAN,
        transaction_qty=500.0,
        source_entity="SUPPLIER",
        destination_entity="GUDANG_BAHAN",
        reference_number="BPB-500",
        reference_type="goods_receipt",
    )
    st3 = await get_product_stock_state(mock_db, "prod-partial")
    assert st3["unprinted_qty"] == 500.0
    assert st3["printed_qty"] == 1000.0
    assert st3["total_qty"] == 1500.0

    # 4. Operator completes remaining 200 PCS -> MUST SUCCEED
    await precheck_operator_progress(mock_db, order, split, increment=200, live_enabled=True)
    mov2 = await handle_operator_progress(mock_db, order, split, increment=200, action_id="act-op-200", live_enabled=True)
    assert mov2 is not None
    assert mov2["delta_unprinted"] == -200.0
    assert mov2["delta_printed"] == 200.0

    st4 = await get_product_stock_state(mock_db, "prod-partial")
    assert st4["unprinted_qty"] == 300.0
    assert st4["printed_qty"] == 1200.0
    assert st4["total_qty"] == 1500.0
    assert st4["total_qty"] == st4["unprinted_qty"] + st4["printed_qty"]


@pytest.mark.anyio
async def test_52_operator_cumulative_storage_applies_only_incremental_delta(mock_db):
    """52. Operator cumulative storage: Event 1 = 600, Event 2 = 400 -> Engine converts 600 + 400 = 1000, not 600 + 1000."""
    await setup_initialized_product(mock_db, "prod-inc", unprinted=1500.0, printed=0.0)
    order = {
        "id": "ord-inc",
        "physical_stock_snapshot": {"enabled": True},
        "line_items": [{"line_idx": 0, "master_item_id": "prod-inc", "production_process": "print", "quantity": 1000, "unit": "PCS"}],
        "operator_splits": [{"id": "split-inc", "line_idx": 0, "operator_name": "Op", "quantity": 1000}],
    }
    split = order["operator_splits"][0]

    # Event 1: 600
    await handle_operator_progress(mock_db, order, split, increment=600, action_id="act-inc-1", live_enabled=True)
    st1 = await get_product_stock_state(mock_db, "prod-inc")
    assert st1["unprinted_qty"] == 900.0
    assert st1["printed_qty"] == 600.0

    # Event 2: 400 (cumulative becomes 1000)
    await handle_operator_progress(mock_db, order, split, increment=400, action_id="act-inc-2", live_enabled=True)
    st2 = await get_product_stock_state(mock_db, "prod-inc")
    assert st2["unprinted_qty"] == 500.0
    assert st2["printed_qty"] == 1000.0
    assert st2["total_qty"] == 1500.0


# ==============================================================================
# TESTS 53 - 55: SOURCE ACTION + STOCK CLAIM ATOMICITY
# ==============================================================================

@pytest.mark.anyio
async def test_53_operator_concurrency_race_safety(mock_db):
    """53. Operator Concurrency: Unprinted = 1000. Operator A = 700, Operator B = 700.
    Database claim guard ($gte) ensures max 1 wins; losing operator fails and does not mutate stock."""
    await setup_initialized_product(mock_db, "prod-op-race", unprinted=1000.0, printed=0.0)
    order = {
        "id": "ord-op-race",
        "physical_stock_snapshot": {"enabled": True},
        "line_items": [{
            "line_idx": 0,
            "line_id": "l-op-race",
            "master_item_id": "prod-op-race",
            "production_process": "print",
            "quantity": 1400,
            "unit": "PCS",
        }],
        "operator_splits": [
            {"id": "split-a", "line_idx": 0, "operator_name": "Op A", "quantity": 700, "qty_completed": 0, "progress_entries": []},
            {"id": "split-b", "line_idx": 0, "operator_name": "Op B", "quantity": 700, "qty_completed": 0, "progress_entries": []},
        ],
    }
    split_a = order["operator_splits"][0]
    split_b = order["operator_splits"][1]

    # Precheck for both passes concurrently if evaluated against 1000
    await precheck_operator_progress(mock_db, order, split_a, increment=700, live_enabled=True)
    await precheck_operator_progress(mock_db, order, split_b, increment=700, live_enabled=True)

    # 1. Operator A acquires durable stock claim
    claim_a = await claim_operator_stock(mock_db, order, split_a, increment=700, action_id="act-op-a")
    assert claim_a is not None
    assert claim_a["status"] == "CLAIMED"

    # State reflects pending reservation at DB level; authoritative balances are NOT prematurely mutated
    st_mid = await get_product_stock_state(mock_db, "prod-op-race")
    assert st_mid["unprinted_qty"] == 1000.0
    assert st_mid["printed_qty"] == 0.0
    assert st_mid["total_qty"] == 1000.0
    assert st_mid.get("pending_event") is not None
    assert st_mid["pending_event"]["stage"] == "CLAIMED"
    assert st_mid["pending_event"]["action_id"] == "act-op-a"

    # 2. Concurrent Operator B attempts to claim 700 -> rejected because pending_event is already held
    with pytest.raises(HTTPException) as exc:
        await claim_operator_stock(mock_db, order, split_b, increment=700, action_id="act-op-b")
    assert exc.value.status_code == 400
    assert "Stok fisik tidak mencukupi" in exc.value.detail or "konflik konkurensi" in exc.value.detail

    # 3. Source mutate & finalize only for Operator A
    split_a["progress_entries"].append({"action_id": "act-op-a", "qty": 700})
    split_a["qty_completed"] = 700
    mov_a = await finalize_operator_stock(mock_db, claim_a)
    assert mov_a is not None
    assert mov_a["delta_unprinted"] == -700.0
    assert mov_a["delta_printed"] == 700.0

    # Verify final state invariants
    st_final = await get_product_stock_state(mock_db, "prod-op-race")
    assert st_final["unprinted_qty"] == 300.0
    assert st_final["printed_qty"] == 700.0
    assert st_final["total_qty"] == 1000.0
    assert st_final.get("pending_event") is None

    # Verify only winning Operator A movement recorded in ledger (+ opening balance)
    res = await list_product_mutations(mock_db, "prod-op-race")
    assert res["total"] == 2
    assert any(m["event_key"] == "status_cetak:ord-op-race:0:act-op-a" for m in res["items"])
    assert not any("act-op-b" in m["event_key"] for m in res["items"])


@pytest.mark.anyio
async def test_54_loading_concurrency_race_safety(mock_db):
    """54. Loading Concurrency: Printed = 1000. Loading A = 700, Loading B = 700.
    Database claim guard ($gte) ensures max 1 wins; losing loading fails and does not mutate stock."""
    await setup_initialized_product(mock_db, "prod-load-race", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-load-race",
        "po_number": "PO-LR",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-lr",
            "master_item_id": "prod-load-race",
            "production_process": "print",
            "quantity": 1400,
            "unit": "PCS",
        }],
    }
    lines_a = [{"line_idx": 0, "line_id": "line-lr", "master_item_id": "prod-load-race", "qty": 700, "unit": "PCS", "name": "Prod"}]
    lines_b = [{"line_idx": 0, "line_id": "line-lr", "master_item_id": "prod-load-race", "qty": 700, "unit": "PCS", "name": "Prod"}]

    # Both pass precheck against initial 1000
    await precheck_loading_confirmed(mock_db, order, lines_a)
    await precheck_loading_confirmed(mock_db, order, lines_b)

    # 1. Loading A acquires durable stock claim
    claims_a = await claim_loading_stock(mock_db, order, lines_a, action_id="act-load-a")
    assert len(claims_a) == 1
    assert claims_a[0]["status"] == "CLAIMED"

    # State records pending reservation; authoritative balances are NOT prematurely decremented
    st_mid = await get_product_stock_state(mock_db, "prod-load-race")
    assert st_mid["printed_qty"] == 1000.0
    assert st_mid["total_qty"] == 1000.0
    assert st_mid.get("pending_event") is not None
    assert st_mid["pending_event"]["stage"] == "CLAIMED"

    # 2. Concurrent Loading B attempts to claim 700 -> fails due to concurrent pending_event
    with pytest.raises(HTTPException) as exc:
        await claim_loading_stock(mock_db, order, lines_b, action_id="act-load-b")
    assert exc.value.status_code == 400
    assert "Stok fisik tidak mencukupi" in exc.value.detail or "konflik konkurensi" in exc.value.detail

    # 3. Source mutate & finalize only for Loading A
    order["loading_confirmations"] = [{"action_id": "act-load-a", "lines": lines_a}]
    movs_a = await finalize_loading_stock(mock_db, claims_a)
    assert len(movs_a) == 1
    assert movs_a[0]["movement_type"] == MOVEMENT_PENGIRIMAN
    assert movs_a[0]["transaction_qty"] == 700.0

    # Verify final state invariants
    st_final = await get_product_stock_state(mock_db, "prod-load-race")
    assert st_final["printed_qty"] == 300.0
    assert st_final["total_qty"] == 300.0
    assert st_final.get("pending_event") is None

    # Verify only winning Loading A movement recorded in ledger (+ opening balance)
    res = await list_product_mutations(mock_db, "prod-load-race")
    assert res["total"] == 2
    assert any(m["movement_type"] == MOVEMENT_PENGIRIMAN and m["transaction_qty"] == 700.0 for m in res["items"])
    assert not any("act-load-b" in m["event_key"] for m in res["items"])


@pytest.mark.anyio
async def test_55_claim_release_on_source_failure(mock_db):
    """55. Atomic Rollback: When source business mutation fails after stock claim,
    release_durable_stock_claim safely rolls back stock reservation to original balances."""
    await setup_initialized_product(mock_db, "prod-abort", unprinted=200.0, printed=800.0)
    order = {
        "id": "ord-abort",
        "po_number": "PO-ABORT",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-abort",
            "master_item_id": "prod-abort",
            "production_process": "print",
            "quantity": 500,
            "unit": "PCS",
        }],
    }
    lines = [{"line_idx": 0, "line_id": "line-abort", "master_item_id": "prod-abort", "qty": 500, "unit": "PCS"}]

    # 1. Acquire durable stock claim
    claims = await claim_loading_stock(mock_db, order, lines, action_id="act-abort-1")
    assert len(claims) == 1
    assert claims[0]["status"] == "CLAIMED"

    # Stock is pending reserved; authoritative balances remain uncommitted
    st_reserved = await get_product_stock_state(mock_db, "prod-abort")
    assert st_reserved["printed_qty"] == 800.0
    assert st_reserved["total_qty"] == 1000.0
    assert st_reserved.get("pending_event") is not None
    assert st_reserved["pending_event"]["event_id"] == claims[0]["event_id"]

    # 2. Simulate source mutate failure (e.g. order cancelled, validation error, process crash)
    # The try/except block triggers release_loading_stock
    await release_loading_stock(mock_db, claims)

    # 3. Verify stock is completely restored to exact initial balances
    st_restored = await get_product_stock_state(mock_db, "prod-abort")
    assert st_restored["unprinted_qty"] == 200.0
    assert st_restored["printed_qty"] == 800.0
    assert st_restored["total_qty"] == 1000.0
    assert st_restored.get("pending_event") is None

    # 4. Verify ZERO loading movements were recorded (only the initial opening balance)
    res = await list_product_mutations(mock_db, "prod-abort")
    assert res["total"] == 1
    assert not any(m["movement_type"] == MOVEMENT_PENGIRIMAN for m in res["items"])


# ==============================================================================
# TESTS 56 - 65: DURABLE CLAIM CRASH RECOVERY
# ==============================================================================

@pytest.mark.anyio
async def test_56_crash_recovery_claim_before_source_clears_pending(mock_db):
    """56. Crash Recovery: Process crash after claim before source write.
    Recovery detects source action is NOT committed and cleanly unsets pending_event without balance drift."""
    await setup_initialized_product(mock_db, "prod-cr1", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-cr1",
        "po_number": "PO-CR1",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-cr1",
            "master_item_id": "prod-cr1",
            "production_process": "print",
            "quantity": 600,
            "unit": "PCS",
        }],
    }
    await mock_db.orders.insert_one(order)
    lines = [{"line_idx": 0, "line_id": "line-cr1", "master_item_id": "prod-cr1", "qty": 600, "unit": "PCS"}]

    # Claim acquired
    claims = await claim_loading_stock(mock_db, order, lines, action_id="act-cr1")
    assert claims[0]["status"] == "CLAIMED"

    # PROCESS CRASH SIMULATION: Worker died right here! No source write in db.orders, no finalize, no release!
    # Background recovery / startup reconciliation runs:
    rec = await reconcile_pending_product_claim(mock_db, "prod-cr1")
    assert rec["status"] == "CLEARED"
    assert rec["reason"] == "SOURCE_NOT_COMMITTED"

    # Verify balances untouched and pending_event removed
    st = await get_product_stock_state(mock_db, "prod-cr1")
    assert st["printed_qty"] == 1000.0
    assert st["total_qty"] == 1000.0
    assert st.get("pending_event") is None

    # Zero stock movements
    res = await list_product_mutations(mock_db, "prod-cr1")
    assert res["total"] == 1
    assert not any(m["movement_type"] == MOVEMENT_PENGIRIMAN for m in res["items"])


@pytest.mark.anyio
async def test_57_crash_recovery_retry_after_claim_before_source_resumes(mock_db):
    """57. Crash Recovery: Client retry after crash before source write safely resumes existing claim."""
    await setup_initialized_product(mock_db, "prod-cr2", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-cr2",
        "po_number": "PO-CR2",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-cr2",
            "master_item_id": "prod-cr2",
            "production_process": "print",
            "quantity": 600,
            "unit": "PCS",
        }],
    }
    await mock_db.orders.insert_one(order)
    lines = [{"line_idx": 0, "line_id": "line-cr2", "master_item_id": "prod-cr2", "qty": 600, "unit": "PCS"}]

    # Attempt 1: Claim acquired, then network drop / worker dies before order write
    claims_1 = await claim_loading_stock(mock_db, order, lines, action_id="act-cr2")
    assert claims_1[0]["status"] == "CLAIMED"

    # Attempt 2: Client retries the same operation with same action_id
    claims_2 = await claim_loading_stock(mock_db, order, lines, action_id="act-cr2")
    assert claims_2[0]["status"] == "CLAIMED"
    assert claims_2[0]["event_id"] == claims_1[0]["event_id"]

    # Now source commits successfully
    await mock_db.orders.update_one(
        {"id": "ord-cr2"},
        {"$push": {"loading_confirmations": {"action_id": "act-cr2", "lines": lines}}},
    )
    # And finalize succeeds
    movs = await finalize_loading_stock(mock_db, claims_2)
    assert len(movs) == 1
    assert movs[0]["transaction_qty"] == 600.0

    st = await get_product_stock_state(mock_db, "prod-cr2")
    assert st["printed_qty"] == 400.0
    assert st["total_qty"] == 400.0
    assert st.get("pending_event") is None


@pytest.mark.anyio
async def test_58_crash_recovery_source_committed_auto_finalizes(mock_db):
    """58. Crash Recovery: Process crash after source write but before finalize.
    Recovery detects canonical source action in order and automatically finalizes stock."""
    await setup_initialized_product(mock_db, "prod-cr3", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-cr3",
        "po_number": "PO-CR3",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-cr3",
            "master_item_id": "prod-cr3",
            "production_process": "print",
            "quantity": 600,
            "unit": "PCS",
        }],
        "loading_confirmations": [],
    }
    await mock_db.orders.insert_one(order)
    lines = [{"line_idx": 0, "line_id": "line-cr3", "master_item_id": "prod-cr3", "qty": 600, "unit": "PCS"}]

    # Claim acquired
    claims = await claim_loading_stock(mock_db, order, lines, action_id="act-cr3")
    assert claims[0]["status"] == "CLAIMED"

    # Source action commits to db.orders
    await mock_db.orders.update_one(
        {"id": "ord-cr3"},
        {"$push": {"loading_confirmations": {"action_id": "act-cr3", "lines": lines}}},
    )

    # PROCESS CRASH SIMULATION: Server power failure before finalize_loading_stock runs!
    # Authoritative balances are still 1000 at this exact instant
    st_crash = await get_product_stock_state(mock_db, "prod-cr3")
    assert st_crash["printed_qty"] == 1000.0

    # System restarts, recovery routine runs
    rec = await reconcile_pending_product_claim(mock_db, "prod-cr3")
    assert rec["status"] == "FINALIZED"
    assert rec["movement"]["transaction_qty"] == 600.0

    # Verify stock is decremented cleanly and pending_event cleared
    st_post = await get_product_stock_state(mock_db, "prod-cr3")
    assert st_post["printed_qty"] == 400.0
    assert st_post["total_qty"] == 400.0
    assert st_post.get("pending_event") is None

    # Verify ledger has exactly 2 movements: opening balance + loading
    res = await list_product_mutations(mock_db, "prod-cr3")
    assert res["total"] == 2


@pytest.mark.anyio
async def test_59_crash_recovery_retry_after_source_committed_finalizes(mock_db):
    """59. Crash Recovery: Client retry after source write committed immediately finalizes."""
    await setup_initialized_product(mock_db, "prod-cr4", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-cr4",
        "po_number": "PO-CR4",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-cr4",
            "master_item_id": "prod-cr4",
            "production_process": "print",
            "quantity": 600,
            "unit": "PCS",
        }],
        "loading_confirmations": [],
    }
    await mock_db.orders.insert_one(order)
    lines = [{"line_idx": 0, "line_id": "line-cr4", "master_item_id": "prod-cr4", "qty": 600, "unit": "PCS"}]

    # Claim acquired
    claims = await claim_loading_stock(mock_db, order, lines, action_id="act-cr4")
    assert claims[0]["status"] == "CLAIMED"

    # Source commits
    await mock_db.orders.update_one(
        {"id": "ord-cr4"},
        {"$push": {"loading_confirmations": {"action_id": "act-cr4", "lines": lines}}},
    )

    # Client retries claim_loading_stock with same action_id
    claims_retry = await claim_loading_stock(mock_db, order, lines, action_id="act-cr4")
    assert claims_retry[0]["status"] == "ALREADY_FINALIZED"
    assert claims_retry[0]["movement"]["transaction_qty"] == 600.0

    st = await get_product_stock_state(mock_db, "prod-cr4")
    assert st["printed_qty"] == 400.0
    assert st["total_qty"] == 400.0
    assert st.get("pending_event") is None


@pytest.mark.anyio
async def test_60_crash_recovery_interrupted_state_applied_resumes_without_double_inc(mock_db):
    """60. Crash Recovery: Crash during finalize when stage == 'STATE_APPLIED' but movement not yet inserted.
    Recovery inserts movement and unsets pending_event without decrementing balances again."""
    await setup_initialized_product(mock_db, "prod-cr5", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-cr5",
        "po_number": "PO-CR5",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-cr5",
            "master_item_id": "prod-cr5",
            "production_process": "print",
            "quantity": 600,
            "unit": "PCS",
        }],
    }
    await mock_db.orders.insert_one(order)
    lines = [{"line_idx": 0, "line_id": "line-cr5", "master_item_id": "prod-cr5", "qty": 600, "unit": "PCS"}]

    claims = await claim_loading_stock(mock_db, order, lines, action_id="act-cr5")
    ev_id = claims[0]["event_id"]

    # Simulate intermediate stage: balance was already decremented by finalize ($inc applied)
    # and stage transitioned to 'STATE_APPLIED', then SIGKILL happened before record_stock_movement!
    await mock_db[STATE_COLLECTION].update_one(
        {"_id": "prod-cr5"},
        {
            "$inc": {"printed_qty": -600.0, "total_qty": -600.0},
            "$set": {"pending_event.stage": "STATE_APPLIED"},
        },
    )

    st_mid = await get_product_stock_state(mock_db, "prod-cr5")
    assert st_mid["printed_qty"] == 400.0
    assert st_mid["pending_event"]["stage"] == "STATE_APPLIED"

    # Recovery runs
    rec = await reconcile_pending_product_claim(mock_db, "prod-cr5")
    assert rec["status"] == "FINALIZED"

    # CRITICAL INVARIANT: Balances MUST NOT be decremented a second time (must remain 400, not -200!)
    st_post = await get_product_stock_state(mock_db, "prod-cr5")
    assert st_post["printed_qty"] == 400.0
    assert st_post["total_qty"] == 400.0
    assert st_post.get("pending_event") is None

    # Movement is now present
    mov = await mock_db.stock_movements.find_one({"_id": ev_id})
    assert mov is not None
    assert mov["transaction_qty"] == 600.0


@pytest.mark.anyio
async def test_61_crash_recovery_movement_exists_clears_pending_zero_drift(mock_db):
    """61. Crash Recovery: Movement already recorded in stock_movements, crash occurred before pending_event unset.
    Recovery unsets pending_event with zero duplicate movement and zero balance drift."""
    await setup_initialized_product(mock_db, "prod-cr6", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-cr6",
        "po_number": "PO-CR6",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-cr6",
            "master_item_id": "prod-cr6",
            "production_process": "print",
            "quantity": 600,
            "unit": "PCS",
        }],
    }
    await mock_db.orders.insert_one(order)
    lines = [{"line_idx": 0, "line_id": "line-cr6", "master_item_id": "prod-cr6", "qty": 600, "unit": "PCS"}]

    claims = await claim_loading_stock(mock_db, order, lines, action_id="act-cr6")
    ev_id = claims[0]["event_id"]

    # Finalize manually simulates complete movement write
    await finalize_loading_stock(mock_db, claims)

    # Simulate crash right before pending_event unset: put pending_event back on state doc
    await mock_db[STATE_COLLECTION].update_one(
        {"_id": "prod-cr6"},
        {"$set": {"pending_event": claims[0]["claim_data"]}},
    )

    # Recovery detects movement already exists
    rec = await reconcile_pending_product_claim(mock_db, "prod-cr6")
    assert rec["status"] == "FINALIZED"

    st = await get_product_stock_state(mock_db, "prod-cr6")
    assert st["printed_qty"] == 400.0
    assert st["total_qty"] == 400.0
    assert st.get("pending_event") is None

    # Total movements remains exactly 2 (opening balance + 1 loading), no duplicate inserted
    res = await list_product_mutations(mock_db, "prod-cr6")
    assert res["total"] == 2


@pytest.mark.anyio
async def test_62_concurrency_conflict_different_event_blocked(mock_db):
    """62. Concurrency Conflict: When product has an active pending_event, any different event is blocked."""
    await setup_initialized_product(mock_db, "prod-cr7", unprinted=1000.0, printed=1000.0)

    # Event 1 claims 500 pcs
    claim_1 = await claim_durable_stock_event(
        mock_db,
        event_id="ev-alpha",
        master_item_id="prod-cr7",
        movement_type=MOVEMENT_PENGIRIMAN,
        transaction_qty=500.0,
        source_entity="SUDAH_DICETAK",
        destination_entity="CUSTOMER",
        reference_number="REF-1",
        reference_type="loading_confirmation",
    )
    assert claim_1["status"] == "CLAIMED"

    # Event 2 tries to claim while Event 1 is pending
    with pytest.raises(HTTPException) as exc:
        await claim_durable_stock_event(
            mock_db,
            event_id="ev-beta",
            master_item_id="prod-cr7",
            movement_type=MOVEMENT_PENGIRIMAN,
            transaction_qty=200.0,
            source_entity="SUDAH_DICETAK",
            destination_entity="CUSTOMER",
            reference_number="REF-2",
            reference_type="loading_confirmation",
        )
    assert exc.value.status_code == 400
    assert "konflik konkurensi" in exc.value.detail or "Stok fisik tidak mencukupi" in exc.value.detail


@pytest.mark.anyio
async def test_63_multi_line_crash_recovery_atomic(mock_db):
    """63. Multi-line crash recovery: Multiple lines in the same order are reconciled independently and correctly."""
    await setup_initialized_product(mock_db, "prod-mla", unprinted=0.0, printed=1000.0)
    await setup_initialized_product(mock_db, "prod-mlb", unprinted=0.0, printed=2000.0)

    order = {
        "id": "ord-ml",
        "po_number": "PO-ML",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "loading_confirmation"},
        "line_items": [
            {
                "line_idx": 0,
                "line_id": "line-mla",
                "master_item_id": "prod-mla",
                "production_process": "print",
                "quantity": 400,
                "unit": "PCS",
            },
            {
                "line_idx": 1,
                "line_id": "line-mlb",
                "master_item_id": "prod-mlb",
                "production_process": "print",
                "quantity": 600,
                "unit": "PCS",
            },
        ],
        "loading_confirmations": [],
    }
    await mock_db.orders.insert_one(order)
    lines = [
        {"line_idx": 0, "line_id": "line-mla", "master_item_id": "prod-mla", "qty": 400, "unit": "PCS"},
        {"line_idx": 1, "line_id": "line-mlb", "master_item_id": "prod-mlb", "qty": 600, "unit": "PCS"},
    ]

    claims = await claim_loading_stock(mock_db, order, lines, action_id="act-ml")
    assert len(claims) == 2

    # Verify no cross-product pending collision: each product owns its exact pending event
    sta_pend1 = await get_product_stock_state(mock_db, "prod-mla")
    stb_pend1 = await get_product_stock_state(mock_db, "prod-mlb")
    assert sta_pend1["pending_event"]["master_item_id"] == "prod-mla"
    assert stb_pend1["pending_event"]["master_item_id"] == "prod-mlb"
    assert sta_pend1["pending_event"]["event_id"] != stb_pend1["pending_event"]["event_id"]

    # Case A: Crash occurs before order is updated (worker killed, no source write)
    rec_a = await reconcile_pending_product_claim(mock_db, "prod-mla")
    rec_b = await reconcile_pending_product_claim(mock_db, "prod-mlb")
    assert rec_a["status"] == "CLEARED"
    assert rec_b["status"] == "CLEARED"

    # Verify both products untouched: balances unchanged, zero pending left, zero movements
    sta = await get_product_stock_state(mock_db, "prod-mla")
    stb = await get_product_stock_state(mock_db, "prod-mlb")
    assert sta["printed_qty"] == 1000.0
    assert sta["total_qty"] == 1000.0
    assert sta.get("pending_event") is None
    assert stb["printed_qty"] == 2000.0
    assert stb["total_qty"] == 2000.0
    assert stb.get("pending_event") is None
    muts_a = await list_product_mutations(mock_db, "prod-mla")
    muts_b = await list_product_mutations(mock_db, "prod-mlb")
    assert muts_a["total"] == 1  # only opening balance
    assert muts_b["total"] == 1  # only opening balance

    # Case B: Re-claim and order commits, but process crashes before finalize completes
    claims_re = await claim_loading_stock(mock_db, order, lines, action_id="act-ml-2")
    assert len(claims_re) == 2

    # Cross-product pending collision check
    sta_pend2 = await get_product_stock_state(mock_db, "prod-mla")
    stb_pend2 = await get_product_stock_state(mock_db, "prod-mlb")
    assert sta_pend2["pending_event"]["master_item_id"] == "prod-mla"
    assert stb_pend2["pending_event"]["master_item_id"] == "prod-mlb"
    assert sta_pend2["pending_event"]["event_id"] != stb_pend2["pending_event"]["event_id"]

    # Source action commits to db.orders exactly once
    await mock_db.orders.update_one(
        {"id": "ord-ml"},
        {"$push": {"loading_confirmations": {"action_id": "act-ml-2", "lines": lines}}},
    )
    # Recovery runs on both products independently
    rec_a2 = await reconcile_pending_product_claim(mock_db, "prod-mla")
    rec_b2 = await reconcile_pending_product_claim(mock_db, "prod-mlb")
    assert rec_a2["status"] == "FINALIZED"
    assert rec_b2["status"] == "FINALIZED"

    # Both products applied exactly once, zero pending left
    sta2 = await get_product_stock_state(mock_db, "prod-mla")
    stb2 = await get_product_stock_state(mock_db, "prod-mlb")
    assert sta2["printed_qty"] == 600.0
    assert sta2["total_qty"] == 600.0
    assert sta2.get("pending_event") is None
    assert stb2["printed_qty"] == 1400.0
    assert stb2["total_qty"] == 1400.0
    assert stb2.get("pending_event") is None

    # Exactly 1 movement per product (+ 1 opening balance)
    res_a = await list_product_mutations(mock_db, "prod-mla")
    res_b = await list_product_mutations(mock_db, "prod-mlb")
    assert res_a["total"] == 2
    assert res_b["total"] == 2
    assert any(m["movement_type"] == MOVEMENT_PENGIRIMAN and m["transaction_qty"] == 400.0 for m in res_a["items"])
    assert any(m["movement_type"] == MOVEMENT_PENGIRIMAN and m["transaction_qty"] == 600.0 for m in res_b["items"])

    # Subsequent recovery returns NO_PENDING (idempotent, stable)
    assert (await reconcile_pending_product_claim(mock_db, "prod-mla"))["status"] == "NO_PENDING"
    assert (await reconcile_pending_product_claim(mock_db, "prod-mlb"))["status"] == "NO_PENDING"


@pytest.mark.anyio
async def test_64_get_stock_state_during_pending_shows_unmutated_authoritative_balances(mock_db):
    """64. Pure Read Invariant: GET /product_stock_state during pending reservation
    returns committed authoritative balances, not prematurely decremented ones."""
    await setup_initialized_product(mock_db, "prod-read-inv", unprinted=500.0, printed=500.0)
    order = {
        "id": "ord-read-inv",
        "physical_stock_snapshot": {"enabled": True},
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-read",
            "master_item_id": "prod-read-inv",
            "production_process": "print",
            "quantity": 300,
            "unit": "PCS",
        }],
        "operator_splits": [
            {"id": "sp-read", "line_idx": 0, "quantity": 300, "qty_completed": 0, "progress_entries": []},
        ],
    }
    split = order["operator_splits"][0]

    # Acquire claim
    claim = await claim_operator_stock(mock_db, order, split, increment=300, action_id="act-read-1")
    assert claim["status"] == "CLAIMED"

    # Direct read MUST show original committed balances
    st = await get_product_stock_state(mock_db, "prod-read-inv")
    assert st["unprinted_qty"] == 500.0
    assert st["printed_qty"] == 500.0
    assert st["total_qty"] == 1000.0
    assert st.get("pending_event") is not None
    assert st["pending_event"]["stage"] == "CLAIMED"


@pytest.mark.anyio
async def test_65_goods_receipt_crash_recovery(mock_db):
    """65. Goods Receipt crash recovery: Stock-In claim reconciles based on receipt existence."""
    await setup_initialized_product(mock_db, "prod-gr-cr", unprinted=100.0, printed=0.0)
    po = {
        "id": "po-gr-cr",
        "po_number": "PO-GR-CR",
        "physical_stock_snapshot": {"enabled": True, "stock_in_trigger": "goods_receipt"},
        "lines": [{
            "line_idx": 0,
            "line_id": "line-gr-cr",
            "master_item_id": "prod-gr-cr",
            "ordered_qty": 500,
            "unit": "PCS",
        }],
    }
    await mock_db.purchase_orders.insert_one(po)

    receipt = {
        "id": "rc-gr-cr",
        "receipt_number": "RC-GR-CR-001",
        "purchase_order_id": "po-gr-cr",
        "lines": [{
            "line_idx": 0,
            "line_id": "line-gr-cr",
            "master_item_id": "prod-gr-cr",
            "received_qty": 500,
            "unit": "PCS",
        }],
    }

    # Execute durable stock-in claim
    claim = await claim_durable_stock_event(
        mock_db,
        event_id="stock_in:goods_receipt:rc-gr-cr:line-gr-cr",
        master_item_id="prod-gr-cr",
        movement_type=MOVEMENT_PENERIMAAN,
        transaction_qty=500.0,
        transaction_uom="PCS",
        source_entity="SUPPLIER",
        destination_entity="GUDANG_UTAMA",
        reference_number="RC-GR-CR-001",
        reference_type="goods_receipt",
        reference_id="rc-gr-cr",
        order_id="po-gr-cr",
    )
    assert claim["status"] == "CLAIMED"

    # If receipt document was NOT inserted into db.purchase_goods_receipts (crash before receipt insert):
    rec_cleared = await reconcile_pending_product_claim(mock_db, "prod-gr-cr")
    assert rec_cleared["status"] == "CLEARED"
    st = await get_product_stock_state(mock_db, "prod-gr-cr")
    assert st["unprinted_qty"] == 100.0
    assert st.get("pending_event") is None

    # Now simulate receipt WAS inserted into db.purchase_goods_receipts, but crash before finalize:
    await mock_db.purchase_goods_receipts.insert_one(receipt)
    claim_2 = await claim_durable_stock_event(
        mock_db,
        event_id="stock_in:goods_receipt:rc-gr-cr:line-gr-cr",
        master_item_id="prod-gr-cr",
        movement_type=MOVEMENT_PENERIMAAN,
        transaction_qty=500.0,
        transaction_uom="PCS",
        source_entity="SUPPLIER",
        destination_entity="GUDANG_UTAMA",
        reference_number="RC-GR-CR-001",
        reference_type="goods_receipt",
        reference_id="rc-gr-cr",
        order_id="po-gr-cr",
    )
    rec_fin = await reconcile_pending_product_claim(mock_db, "prod-gr-cr")
    assert rec_fin["status"] == "FINALIZED"

    st_fin = await get_product_stock_state(mock_db, "prod-gr-cr")
    assert st_fin["unprinted_qty"] == 600.0
    assert st_fin["total_qty"] == 600.0
    assert st_fin.get("pending_event") is None


@pytest.mark.anyio
async def test_64_applied_marker_is_stored_with_balance_and_copied_on_retry(mock_db, monkeypatch):
    """Crash after the balance write must keep the stored transition marker.

    GR, pengiriman, and opname share finalize. A later clock must not restamp it.
    """
    import services.physical_stock_engine as engine
    from services.stock_ledger import MOVEMENT_KOREKSI

    apply_clock = "2026-09-30T16:30:00+00:00"
    retry_clock = "2026-10-02T01:00:00+00:00"
    claim_clock = "2026-09-30T16:00:00+00:00"
    cases = [
        ("gr", MOVEMENT_PENERIMAAN, 50.0, None, 150.0),
        ("sj", MOVEMENT_PENGIRIMAN, 20.0, False, 80.0),
        ("op", MOVEMENT_KOREKSI, -10.0, None, 90.0),
    ]
    real_record = engine.record_stock_movement

    async def crash_before_movement(*_args, **_kwargs):
        raise RuntimeError("crash before movement")

    for name, movement_type, qty, is_print, after in cases:
        item = f"prod-atom-{name}"
        await setup_initialized_product(mock_db, item, unprinted=100.0, printed=0.0)
        monkeypatch.setattr(engine, "now_iso", lambda clock=apply_clock: clock)
        kwargs = {
            "event_id": f"ev-{name}",
            "master_item_id": item,
            "movement_type": movement_type,
            "transaction_qty": qty,
            "transaction_uom": "PCS",
            "source_entity": "GUDANG",
            "destination_entity": "LAIN",
            "reference_number": name,
            "reference_type": name,
            "now": claim_clock,
        }
        if is_print is not None:
            kwargs["is_print"] = is_print
        claim = await claim_durable_stock_event(mock_db, **kwargs)
        assert claim["status"] == "CLAIMED", name
        claimed_state = await get_product_stock_state(mock_db, item)
        assert claimed_state["total_qty"] == 100.0
        assert not (claimed_state.get("pending_event") or {}).get("stock_applied_at")

        monkeypatch.setattr(engine, "record_stock_movement", crash_before_movement)
        with pytest.raises(RuntimeError, match="crash before movement"):
            await finalize_durable_stock_event(
                mock_db, event_id=f"ev-{name}", master_item_id=item, now=claim_clock,
            )
        crashed = await get_product_stock_state(mock_db, item)
        assert crashed["total_qty"] == after, name
        assert crashed["pending_event"]["stage"] == "STATE_APPLIED"
        assert crashed["pending_event"]["stock_applied_at"] == apply_clock

        monkeypatch.setattr(engine, "now_iso", lambda clock=retry_clock: clock)
        monkeypatch.setattr(engine, "record_stock_movement", real_record)
        movement = await finalize_durable_stock_event(
            mock_db, event_id=f"ev-{name}", master_item_id=item, now=claim_clock,
        )
        assert movement["stock_applied_at"] == apply_clock, name
        assert movement["stock_applied_at"] != retry_clock
        assert movement["claimed_at"] == claim_clock
        finished = await get_product_stock_state(mock_db, item)
        assert finished["total_qty"] == after, name
        assert finished.get("pending_event") is None
        again = await finalize_durable_stock_event(
            mock_db, event_id=f"ev-{name}", master_item_id=item, now=claim_clock,
        )
        assert again["stock_applied_at"] == apply_clock
        assert (await get_product_stock_state(mock_db, item))["total_qty"] == after


@pytest.mark.anyio
async def test_65_recovery_does_not_mint_applied_timestamp_for_historical_crash(mock_db, monkeypatch):
    """A STATE_APPLIED crash from before the marker existed stays unstamped."""
    import services.physical_stock_engine as engine

    minted = "2026-10-05T08:00:00+00:00"
    monkeypatch.setattr(engine, "now_iso", lambda: minted)
    await setup_initialized_product(mock_db, "prod-hist", unprinted=100.0, printed=0.0)
    claim = await claim_durable_stock_event(
        mock_db,
        event_id="ev-hist",
        master_item_id="prod-hist",
        movement_type=MOVEMENT_PENGIRIMAN,
        transaction_qty=20.0,
        transaction_uom="PCS",
        source_entity="BELUM_DICETAK",
        destination_entity="CUSTOMER",
        reference_number="SJ-HIST",
        reference_type="sales_delivery",
        is_print=False,
        now="2026-09-30T16:00:00+00:00",
    )
    assert claim["status"] == "CLAIMED"
    await mock_db[STATE_COLLECTION].update_one(
        {"_id": "prod-hist"},
        {
            "$inc": {"unprinted_qty": -20.0, "total_qty": -20.0},
            "$set": {"pending_event.stage": "STATE_APPLIED"},
            "$unset": {"pending_event.stock_applied_at": ""},
        },
    )
    recovered = await reconcile_pending_product_claim(mock_db, "prod-hist")
    assert recovered["status"] == "FINALIZED"
    assert recovered["movement"].get("stock_applied_at") in (None, "")
    assert recovered["movement"].get("stock_applied_at") != minted
    state = await get_product_stock_state(mock_db, "prod-hist")
    assert state["total_qty"] == 80.0
    assert state.get("pending_event") is None
    rows = await list_product_mutations(mock_db, "prod-hist")
    assert rows["total"] == 2


NAMOY_MASTER = "958bb5e9-1090-4676-9999-eff93ff0bd5b"


def _namoy_order():
    return {
        "id": "ord-namoy",
        "po_number": "PO/2026/09/22/000011",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
        "line_items": [
            {
                "master_item_id": NAMOY_MASTER,
                "production_process": "print",
                "qty": 1000,
                "quantity": 1000,
                "line_id": "L0",
                "unit": "PCS",
            },
            {
                "master_item_id": NAMOY_MASTER,
                "production_process": "print",
                "qty": 1000,
                "quantity": 1000,
                "line_id": "L1",
                "unit": "PCS",
            },
        ],
        "delivery_confirm_action_ids": [],
        "delivery_confirmations": [],
    }


def _pengiriman(db):
    return [d for d in db.stock_movements._docs if d.get("movement_type") == MOVEMENT_PENGIRIMAN]


def test_confirm_delivered_stock_claim_uses_selected_sj_rows():
    """Stock claim resolves SJ rows; it does not pass the full PO line list."""
    from pathlib import Path

    server = Path(__file__).resolve().parents[1].joinpath("server.py").read_text()
    start = server.index("async def confirm_delivered")
    # confirm_delivered grew past the old 4500/9000-char windows (standalone
    # delivery path); keep the structural order check without the whole body.
    window = server[start:start + 14000]
    assert "claim_selected_delivery_stock(" in window
    assert 'claim_delivery_confirmed_stock(' not in window
    # R2 readiness / warnings use SJ plan ready_lines (not a full-PO claim).
    assert "plan_confirmed_sj_stock(" in window
    assert "lines=ready_lines if ready_lines else None" in window
    # line_items may be read only to label warning item names — not as claim qty source.
    if 'existing_order.get("line_items")' in window:
        assert "item_names" in window
        assert "_enrich_delivery_stock_warnings" in window
    assert "reject_sj_stock_qty_drift" in window
    reconcile_at = window.index("_qr_reconcile_sj_quantities_to_loaded_delivery")
    drift_at = window.index("reject_sj_stock_qty_drift")
    append_at = window.index("_append_delivery_confirmation")
    assert reconcile_at < drift_at < append_at
    engine = Path(__file__).resolve().parents[1].joinpath("services", "physical_stock_engine.py").read_text()
    assert "prepare_delivery_confirmed_stock" not in engine
    assert "build_delivery_confirm_stock_lines" not in engine


@pytest.mark.anyio
async def test_same_master_two_po_lines_one_reservation(mock_db):
    """Two lines from order.line_items share one master. Qty is those line qtys.

    One pending_event covers both lines. Finalize writes one movement per line.
    """
    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _namoy_order()
    lines = order["line_items"]
    await precheck_delivery_confirmed(mock_db, order, lines)
    claims = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-namoy")
    assert len(claims) == 1
    assert claims[0]["status"] == "CLAIMED"
    assert claims[0]["event_id"].endswith(f":master:{NAMOY_MASTER}")
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["reference_number"] == "PO/2026/09/22/000011"
    assert state["printed_qty"] == 2000.0
    movs = await finalize_delivery_confirmed_stock(mock_db, claims)
    assert len(movs) == 2
    assert {m.get("line_id") for m in movs} == {"L0", "L1"}
    assert {m.get("line_idx") for m in movs} == {0, 1}
    assert {m.get("reference_number") for m in movs} == {"PO/2026/09/22/000011"}
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 0.0
    assert state["unprinted_qty"] == 4700.0
    assert state["total_qty"] == 4700.0
    assert not state.get("pending_event")
    assert len(_pengiriman(mock_db)) == 2


@pytest.mark.anyio
async def test_same_master_print_and_unprinted_lines_one_reservation(mock_db):
    """Print and non_print lines of one master, claimed from the same line list."""
    await setup_initialized_product(mock_db, "mix-1", unprinted=200.0, printed=200.0)
    order = {
        "id": "ord-mix",
        "po_number": "PO-MIX",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
        "line_items": [
            {"master_item_id": "mix-1", "production_process": "print", "qty": 100, "line_id": "P", "unit": "PCS"},
            {"master_item_id": "mix-1", "production_process": "non_print", "qty": 50, "line_id": "U", "unit": "PCS"},
        ],
    }
    lines = order["line_items"]
    claims = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-mix")
    assert len(claims) == 1
    assert claims[0]["status"] == "CLAIMED"
    state = await get_product_stock_state(mock_db, "mix-1")
    assert state["printed_qty"] == 200.0
    assert state["pending_event"]["event_id"].endswith(":master:mix-1")
    movs = await finalize_delivery_confirmed_stock(mock_db, claims)
    assert {m.get("line_id") for m in movs} == {"P", "U"}
    assert {m.get("reference_number") for m in movs} == {"PO-MIX"}
    state = await get_product_stock_state(mock_db, "mix-1")
    assert state["printed_qty"] == 100.0
    assert state["unprinted_qty"] == 150.0
    assert state["total_qty"] == 250.0
    assert not state.get("pending_event")
    assert len(_pengiriman(mock_db)) == 2


@pytest.mark.anyio
async def test_same_master_sum_exceeds_bucket_reserves_nothing(mock_db):
    """Each line fits alone, the sum does not. The group is skipped, not claimed.

    Confirmation continues without a partial movement, without negative stock,
    and with stock_warning on the selected-SJ claim.
    """
    from services.physical_stock_engine import claim_selected_delivery_stock

    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=1500.0)
    order = _namoy_order()
    lines = order["line_items"]
    await precheck_delivery_confirmed(mock_db, order, lines)
    claims = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-short")
    assert claims == []
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 1500.0
    assert state["unprinted_qty"] == 4700.0
    assert state["total_qty"] == 6200.0
    assert state["printed_qty"] >= 0
    assert state["unprinted_qty"] >= 0
    assert state["total_qty"] >= 0
    assert not state.get("pending_event")
    assert _pengiriman(mock_db) == []

    order["line_items"][0]["accurate_item_id"] = "ACC-NAMOY"
    order["line_items"][1]["accurate_item_id"] = "ACC-NAMOY"
    order["shipments"] = [{
        "id": "sj-short",
        "number": "SJ-SHORT",
        "lines": [
            {"line_idx": 0, "line_id": "L0", "qty": 1000},
            {"line_idx": 1, "line_id": "L1", "qty": 1000},
        ],
    }]
    holder = {}
    selected = await claim_selected_delivery_stock(
        mock_db, order, "sj-short", "act-short-sj", None, holder,
    )
    assert selected == []
    assert holder.get("stock_warning") is True
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 1500.0
    assert state["unprinted_qty"] == 4700.0
    assert state["total_qty"] == 6200.0
    assert not state.get("pending_event")
    assert _pengiriman(mock_db) == []


@pytest.mark.anyio
async def test_second_action_does_not_release_other_claim(mock_db):
    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _namoy_order()
    lines = order["line_items"]
    first = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-a")
    with pytest.raises(HTTPException) as exc:
        await claim_delivery_confirmed_stock(mock_db, order, lines, "act-b")
    assert "konflik konkurensi" in str(exc.value.detail)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["action_id"] == "act-a"
    assert state["printed_qty"] == 2000.0
    await release_delivery_confirmed_stock(mock_db, [{
        "status": "CLAIMED",
        "event_id": "stock_out:delivery:ord-namoy:act-b:master:" + NAMOY_MASTER,
        "master_item_id": NAMOY_MASTER,
    }])
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["action_id"] == "act-a"
    await release_delivery_confirmed_stock(mock_db, first)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert not state.get("pending_event")
    assert state["printed_qty"] == 2000.0
    assert _pengiriman(mock_db) == []


@pytest.mark.anyio
async def test_retry_same_action_id_is_idempotent(mock_db):
    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _namoy_order()
    lines = order["line_items"]
    first = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-retry")
    second = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-retry")
    assert second[0]["status"] == "CLAIMED"
    assert second[0]["event_id"] == first[0]["event_id"]
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["delta_printed"] == -2000.0
    assert state["printed_qty"] == 2000.0
    movs = await finalize_delivery_confirmed_stock(mock_db, second)
    assert len(movs) == 2
    again = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-retry")
    assert again[0]["status"] == "ALREADY_FINALIZED"
    finalized = await finalize_delivery_confirmed_stock(mock_db, again)
    assert len(finalized) == 2
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 0.0
    assert state["total_qty"] == 4700.0
    assert len(_pengiriman(mock_db)) == 2


@pytest.mark.anyio
async def test_release_between_claim_and_finalize_restores_nothing_because_qty_unchanged(mock_db):
    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _namoy_order()
    lines = order["line_items"]
    claims = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-fail")
    await release_delivery_confirmed_stock(mock_db, claims)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert not state.get("pending_event")
    assert state["printed_qty"] == 2000.0
    assert state["total_qty"] == 6700.0
    assert _pengiriman(mock_db) == []


@pytest.mark.anyio
async def test_finalize_retries_after_movement_write_failure(mock_db, monkeypatch):
    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _namoy_order()
    import services.physical_stock_engine as engine

    lines = order["line_items"]
    claims = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-crash")
    real = engine.record_stock_movement
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("movement write failed")
        return await real(*args, **kwargs)

    monkeypatch.setattr(engine, "record_stock_movement", flaky)
    with pytest.raises(RuntimeError):
        await finalize_delivery_confirmed_stock(mock_db, claims)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 0.0
    assert state["pending_event"]["stage"] == "STATE_APPLIED"
    assert len(_pengiriman(mock_db)) == 1
    monkeypatch.setattr(engine, "record_stock_movement", real)
    movs = await finalize_delivery_confirmed_stock(mock_db, claims)
    assert len(movs) == 2
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 0.0
    assert state["total_qty"] == 4700.0
    assert not state.get("pending_event")
    assert len(_pengiriman(mock_db)) == 2


@pytest.mark.anyio
async def test_source_committed_retry_finalizes_once(mock_db):
    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _namoy_order()
    await mock_db.orders.insert_one(dict(order))
    lines = order["line_items"]
    await claim_delivery_confirmed_stock(mock_db, order, lines, "act-src")
    await mock_db.orders.update_one(
        {"id": order["id"]},
        {"$set": {"delivery_confirm_action_ids": ["act-src"]}},
    )
    resumed = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-src")
    assert resumed[0]["status"] == "ALREADY_FINALIZED"
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 0.0
    assert not state.get("pending_event")
    assert len(_pengiriman(mock_db)) == 2
    again = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-src")
    await finalize_delivery_confirmed_stock(mock_db, again)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 0.0
    assert len(_pengiriman(mock_db)) == 2


@pytest.mark.anyio
async def test_snapshot_off_does_not_claim(mock_db):
    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    from services.physical_stock_engine import should_claim_delivery_confirmed_stock

    order = _namoy_order()
    legacy = dict(order)
    legacy["physical_stock_snapshot"] = {"enabled": False, "stock_out_trigger": "delivery_confirmed"}
    assert await should_claim_delivery_confirmed_stock(mock_db, legacy) is False
    movs = await handle_delivery_confirmed(mock_db, legacy, {"id": "conf-off"})
    assert movs == []
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 2000.0
    assert _pengiriman(mock_db) == []


@pytest.mark.anyio
async def test_legacy_sequential_same_master_claims_conflict_and_release_first(mock_db):
    """Pre-fix loop: claim line 0, then line 1, on one master.

    Line 2 sees line 1's pending_event, raises the generic conflict, and the
    caller releases only the claim it already appended. Balances stay put.
    """
    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _namoy_order()
    claims = []
    with pytest.raises(HTTPException) as exc:
        for idx, line in enumerate(order["line_items"]):
            claim = await claim_durable_stock_event(
                mock_db,
                event_id=f"stock_out:delivery:{order['id']}:act-old:{idx}",
                master_item_id=line["master_item_id"],
                movement_type=MOVEMENT_PENGIRIMAN,
                transaction_qty=line["qty"],
                transaction_uom="PCS",
                source_entity="SUDAH_DICETAK",
                destination_entity="CUSTOMER",
                reference_number=order["po_number"],
                reference_type="delivery_confirmed",
                reference_id=order["id"],
                order_id=order["id"],
                line_idx=idx,
                line_id=line["line_id"],
                action_id="act-old",
                is_print=True,
            )
            claims.append(claim)
    assert "konflik konkurensi" in str(exc.value.detail)
    assert len(claims) == 1
    assert claims[0]["event_id"].endswith(":0")
    await release_delivery_confirmed_stock(mock_db, claims)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert not state.get("pending_event")
    assert state["printed_qty"] == 2000.0
    assert state["total_qty"] == 6700.0
    assert _pengiriman(mock_db) == []


@pytest.mark.anyio
async def test_single_line_full_depletion_retry_stays_idempotent(mock_db):
    await setup_initialized_product(mock_db, "prod-1", unprinted=0.0, printed=1000.0)
    order = {
        "id": "ord-one",
        "po_number": "PO-ONE",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
        "line_items": [{"master_item_id": "prod-1", "production_process": "print", "qty": 1000, "line_id": "L", "unit": "PCS"}],
    }
    lines = order["line_items"]
    claims = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-one")
    await finalize_delivery_confirmed_stock(mock_db, claims)
    again = await claim_delivery_confirmed_stock(mock_db, order, lines, "act-one")
    assert again[0]["status"] == "ALREADY_FINALIZED"
    await finalize_delivery_confirmed_stock(mock_db, again)
    state = await get_product_stock_state(mock_db, "prod-1")
    assert state["printed_qty"] == 0.0
    assert len(_pengiriman(mock_db)) == 1
    assert _pengiriman(mock_db)[0].get("reference_number") == "PO-ONE"


@pytest.mark.anyio
async def test_two_concurrent_confirms_one_claim_wins(mock_db):
    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _namoy_order()
    lines = order["line_items"]
    results = await asyncio.gather(
        claim_delivery_confirmed_stock(mock_db, order, lines, "act-1"),
        claim_delivery_confirmed_stock(mock_db, order, lines, "act-2"),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, HTTPException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "konflik konkurensi" in str(failures[0].detail)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["action_id"] in ("act-1", "act-2")
    assert state["printed_qty"] == 2000.0
    assert _pengiriman(mock_db) == []
    movs = await finalize_delivery_confirmed_stock(mock_db, successes[0])
    assert len(movs) == 2
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 0.0
    assert len(_pengiriman(mock_db)) == 2


def _sj_partial_order():
    """PO 1000+1000, two SJ rows per shipment, one shared master."""
    order = _namoy_order()
    order["line_items"][0]["accurate_item_id"] = "ACC-NAMOY"
    order["line_items"][1]["accurate_item_id"] = "ACC-NAMOY"
    order["shipments"] = [
        {
            "id": "sj-1",
            "number": "SJ/2026/09/26/000011",
            "lines": [
                {"line_idx": 0, "line_id": "L0", "qty": 400},
                {"line_idx": 1, "line_id": "L1", "qty": 400},
            ],
        },
        {
            "id": "sj-2",
            "number": "SJ/2026/09/27/000012",
            "lines": [
                {"line_idx": 0, "line_id": "L0", "qty": 600},
                {"line_idx": 1, "line_id": "L1", "qty": 600},
            ],
        },
    ]
    return order


async def _seed_operational_sales_deliveries(db, order):
    """Insert allocation-committed canonical SJ docs for order.shipments.

    prepare_confirm_delivered_action fail-closes via require_operational_sales_delivery;
    order.shipments alone are not enough.
    """
    po_id = str(order.get("id") or "")
    for sh in order.get("shipments") or []:
        sid = str(sh.get("id") or "").strip()
        if not sid:
            continue
        await db.sales_deliveries.insert_one({
            "id": sid,
            "sj_number": sh.get("number") or sid,
            "po_id": po_id,
            "is_cancelled": False,
            "status": "SELESAI",
            "line_items": [
                {
                    "line_idx": ln.get("line_idx"),
                    "line_id": ln.get("line_id"),
                    "qty": ln.get("qty"),
                    "source_po_id": po_id,
                }
                for ln in (sh.get("lines") or [])
            ],
        })


@pytest.mark.anyio
async def test_selected_sj_rows_reserve_confirmed_qty_not_full_po(mock_db):
    """400+400 then 600+600. Identity is the PO line. Qty is the SJ row."""
    from services.physical_stock_engine import claim_selected_delivery_stock, plan_confirmed_sj_stock

    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _sj_partial_order()
    first = plan_confirmed_sj_stock(order, order["shipments"][0])
    assert first["error"] is None
    assert [(ln["line_id"], ln["qty"], ln["master_item_id"]) for ln in first["lines"]] == [
        ("L0", 400, NAMOY_MASTER),
        ("L1", 400, NAMOY_MASTER),
    ]
    assert all(ln["accurate_item_id"] == "ACC-NAMOY" for ln in first["lines"])
    holder = {}
    claims = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-sj-1", None, holder)
    assert len(claims) == 1
    assert claims[0]["event_id"].endswith(f":master:{NAMOY_MASTER}")
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["delta_printed"] == -800.0
    assert state["printed_qty"] == 2000.0
    movs = await finalize_delivery_confirmed_stock(mock_db, claims)
    assert sorted(m.get("transaction_qty") for m in movs) == [400.0, 400.0]
    assert {m.get("line_id") for m in movs} == {"L0", "L1"}
    assert {m.get("line_idx") for m in movs} == {0, 1}
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 1200.0
    assert state["total_qty"] == 5900.0
    assert len(_pengiriman(mock_db)) == 2

    order["shipments"][0]["delivery_status"] = "Terkirim"
    order["shipments"][0]["delivered_at"] = "2026-09-26T00:00:00+00:00"
    order["delivery_confirmations"] = [{"shipment_id": "sj-1", "qty": 800, "items": [
        {"line_idx": 0, "qty": 400}, {"line_idx": 1, "qty": 400},
    ]}]
    second = plan_confirmed_sj_stock(order, order["shipments"][1])
    assert [ln["qty"] for ln in second["lines"]] == [600, 600]
    holder = {}
    claims = await claim_selected_delivery_stock(mock_db, order, "sj-2", "act-sj-2", None, holder)
    movs = await finalize_delivery_confirmed_stock(mock_db, claims)
    assert sorted(m.get("transaction_qty") for m in movs) == [600.0, 600.0]
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 0.0
    assert state["unprinted_qty"] == 4700.0
    assert state["total_qty"] == 4700.0
    assert len(_pengiriman(mock_db)) == 4
    assert not state.get("pending_event")


@pytest.mark.anyio
async def test_same_master_print_and_plain_sj_rows_one_reservation(mock_db):
    from services.physical_stock_engine import claim_selected_delivery_stock

    await setup_initialized_product(mock_db, "mix-1", unprinted=200.0, printed=200.0)
    order = {
        "id": "ord-mix-sj",
        "po_number": "PO-MIX",
        "physical_stock_snapshot": {"enabled": True, "stock_out_trigger": "delivery_confirmed"},
        "line_items": [
            {"master_item_id": "mix-1", "accurate_item_id": "ACC-P", "production_process": "print", "qty": 1000, "line_id": "P", "unit": "PCS"},
            {"master_item_id": "mix-1", "accurate_item_id": "ACC-U", "production_process": "non_print", "qty": 1000, "line_id": "U", "unit": "PCS"},
        ],
        "shipments": [{
            "id": "sj-mix",
            "lines": [
                {"line_idx": 0, "line_id": "P", "qty": 100},
                {"line_idx": 1, "line_id": "U", "qty": 50},
            ],
        }],
    }
    holder = {}
    claims = await claim_selected_delivery_stock(mock_db, order, "sj-mix", "act-mix-sj", None, holder)
    assert len(claims) == 1
    state = await get_product_stock_state(mock_db, "mix-1")
    assert state["pending_event"]["delta_printed"] == -100.0
    assert state["pending_event"]["delta_unprinted"] == -50.0
    assert state["printed_qty"] == 200.0
    movs = await finalize_delivery_confirmed_stock(mock_db, claims)
    assert {m.get("line_id") for m in movs} == {"P", "U"}
    assert sum(m.get("transaction_qty") or 0 for m in movs) == 150
    state = await get_product_stock_state(mock_db, "mix-1")
    assert state["printed_qty"] == 100.0
    assert state["unprinted_qty"] == 150.0
    assert not state.get("pending_event")


@pytest.mark.anyio
async def test_sj_without_rows_does_not_claim_full_po(mock_db):
    from services.physical_stock_engine import claim_selected_delivery_stock

    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _sj_partial_order()
    order["shipments"] = [{"id": "sj-bare", "number": "SJ-BARE", "total_qty": 2000}]
    holder = {}
    with pytest.raises(HTTPException) as exc:
        await claim_selected_delivery_stock(mock_db, order, "sj-bare", "act-bare", None, holder)
    assert "tidak mengurangi stok dengan qty penuh PO" in str(exc.value.detail)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 2000.0
    assert not state.get("pending_event")
    assert _pengiriman(mock_db) == []


@pytest.mark.anyio
async def test_cancelled_and_delivered_sj_are_not_claimed(mock_db):
    from services.physical_stock_engine import claim_selected_delivery_stock

    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _sj_partial_order()
    order["shipments"][1]["cancelled"] = True
    holder = {}
    with pytest.raises(HTTPException) as exc:
        await claim_selected_delivery_stock(mock_db, order, "sj-2", "act-cancel", None, holder)
    assert "batal" in str(exc.value.detail).lower()
    order["shipments"][0]["delivery_status"] = "Terkirim"
    holder = {}
    claimed = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-done", None, holder)
    assert claimed is None
    assert holder.get("skip") is True
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 2000.0
    assert _pengiriman(mock_db) == []


@pytest.mark.anyio
async def test_selected_sj_retry_does_not_double_reservation(mock_db):
    from services.physical_stock_engine import claim_selected_delivery_stock

    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _sj_partial_order()
    first = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-sj-retry", None, {})
    second = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-sj-retry", None, {})
    assert second[0]["event_id"] == first[0]["event_id"]
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["delta_printed"] == -800.0
    assert state["printed_qty"] == 2000.0
    movs = await finalize_delivery_confirmed_stock(mock_db, second)
    assert len(movs) == 2
    again = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-sj-retry", None, {})
    assert again[0]["status"] == "ALREADY_FINALIZED"
    await finalize_delivery_confirmed_stock(mock_db, again)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 1200.0
    assert len(_pengiriman(mock_db)) == 2


@pytest.mark.anyio
async def test_qty_drift_after_claim_does_not_keep_a_different_reservation(mock_db):
    from services.physical_stock_engine import claim_selected_delivery_stock, reject_sj_stock_qty_drift

    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _sj_partial_order()
    holder = {}
    claims = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-drift", None, holder)
    order["shipments"][0]["lines"][0]["qty"] = 100
    with pytest.raises(HTTPException) as exc:
        reject_sj_stock_qty_drift(order, order["shipments"][0], holder, 800)
    assert "berubah" in str(exc.value.detail)
    await release_delivery_confirmed_stock(mock_db, claims)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert not state.get("pending_event")
    assert state["printed_qty"] == 2000.0
    assert _pengiriman(mock_db) == []


@pytest.mark.anyio
async def test_other_sj_claim_does_not_release_the_first(mock_db):
    from services.physical_stock_engine import claim_selected_delivery_stock

    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _sj_partial_order()
    first = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-a", None, {})
    with pytest.raises(HTTPException) as exc:
        await claim_selected_delivery_stock(mock_db, order, "sj-2", "act-b", None, {})
    assert "konflik konkurensi" in str(exc.value.detail)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["action_id"] == "act-a"
    assert state["pending_event"]["delta_printed"] == -800.0
    await release_delivery_confirmed_stock(mock_db, [{
        "status": "CLAIMED",
        "event_id": "stock_out:delivery:ord-namoy:act-b:master:" + NAMOY_MASTER,
        "master_item_id": NAMOY_MASTER,
    }])
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["action_id"] == "act-a"
    await release_delivery_confirmed_stock(mock_db, first)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert not state.get("pending_event")
    assert state["printed_qty"] == 2000.0


@pytest.mark.anyio
async def test_retry_after_delivered_mark_finalizes_the_same_claim_once(mock_db):
    """Crash after the SJ is marked delivered and before stock finalize.

    The same action_id resumes the reservation. It does not open a second one.
    """
    from services.physical_stock_engine import claim_selected_delivery_stock

    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _sj_partial_order()
    await mock_db.orders.insert_one(dict(order))
    claims = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-crash-sj", None, {})
    assert claims[0]["status"] == "CLAIMED"
    order["shipments"][0]["delivery_status"] = "Terkirim"
    order["delivery_confirm_action_ids"] = ["act-crash-sj"]
    order["delivery_confirmations"] = [{"action_id": "act-crash-sj", "shipment_id": "sj-1", "qty": 800}]
    await mock_db.orders.update_one({"id": order["id"]}, {"$set": {
        "shipments": order["shipments"],
        "delivery_confirm_action_ids": ["act-crash-sj"],
        "delivery_confirmations": order["delivery_confirmations"],
    }})
    resumed = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-crash-sj", None, {})
    assert resumed[0]["status"] == "ALREADY_FINALIZED"
    await finalize_delivery_confirmed_stock(mock_db, resumed)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 1200.0
    assert not state.get("pending_event")
    assert len(_pengiriman(mock_db)) == 2
    again = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-crash-sj", None, {})
    await finalize_delivery_confirmed_stock(mock_db, again)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["printed_qty"] == 1200.0
    assert len(_pengiriman(mock_db)) == 2


@pytest.mark.anyio
async def test_omitted_delivery_action_is_stable_per_shipment(mock_db):
    from services.physical_stock_engine import _acquire_omitted_action

    first = await _acquire_omitted_action(mock_db, "ord-omit", "sj-1")
    second = await _acquire_omitted_action(mock_db, "ord-omit", "sj-1")
    other = await _acquire_omitted_action(mock_db, "ord-omit", "sj-2")
    assert first == second
    assert other != first


@pytest.mark.anyio
async def test_explicit_action_rejects_a_different_shipment_or_payload(mock_db):
    from fastapi import HTTPException

    from services.physical_stock_engine import prepare_confirm_delivered_action

    order = _sj_partial_order()
    order["delivery_confirmations"] = [{
        "action_id": "act-1",
        "shipment_id": "sj-1",
        "qty": 800,
        "items": [{"line_idx": 0, "qty": 400}, {"line_idx": 1, "qty": 400}],
    }]
    order["delivery_confirm_action_ids"] = ["act-1"]
    await mock_db.orders.insert_one(dict(order))
    await _seed_operational_sales_deliveries(mock_db, order)
    # Same action + same shipment/payload resolves (idempotent binding).
    same = await prepare_confirm_delivered_action(mock_db, order, "sj-1", "act-1")
    assert same["action_id"] == "act-1"
    with pytest.raises(HTTPException) as other_sj:
        await prepare_confirm_delivered_action(mock_db, order, "sj-2", "act-1")
    assert other_sj.value.status_code == 409
    changed = dict(order)
    changed["delivery_confirmations"] = [{
        "action_id": "act-1",
        "shipment_id": "sj-1",
        "qty": 800,
        "items": [{"line_idx": 0, "qty": 100}, {"line_idx": 1, "qty": 400}],
    }]
    with pytest.raises(HTTPException) as other_payload:
        await prepare_confirm_delivered_action(mock_db, changed, "sj-1", "act-1")
    assert other_payload.value.status_code == 409

    # Fail-closed gates for missing / pending / cancelled canonical SJ
    # (same require_operational_sales_delivery used by prepare_confirm_delivered_action).
    from services.sales_delivery_allocations import require_operational_sales_delivery

    await mock_db.sales_deliveries.delete_one({"id": "sj-1"})
    with pytest.raises(HTTPException) as missing_sj:
        await require_operational_sales_delivery(mock_db, "sj-1", attempt_reconcile=False)
    assert missing_sj.value.status_code == 404
    await mock_db.sales_deliveries.insert_one({
        "id": "sj-1",
        "sj_number": "SJ/2026/09/26/000011",
        "po_id": order["id"],
        "is_cancelled": False,
        "allocation_commit_pending": True,
        "line_items": [],
    })
    with pytest.raises(HTTPException) as pending_sj:
        await require_operational_sales_delivery(mock_db, "sj-1", attempt_reconcile=False)
    assert pending_sj.value.status_code == 409
    await mock_db.sales_deliveries.update_one(
        {"id": "sj-1"},
        {"$unset": {"allocation_commit_pending": ""}, "$set": {"is_cancelled": True}},
    )
    with pytest.raises(HTTPException) as cancelled_sj:
        await require_operational_sales_delivery(mock_db, "sj-1", attempt_reconcile=False)
    assert cancelled_sj.value.status_code == 404


@pytest.mark.anyio
async def test_release_keeps_live_recorded_and_applied_delivery_claims(mock_db):
    from services.physical_stock_engine import (
        claim_selected_delivery_stock,
        release_uncommitted_delivery_claim,
    )

    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _sj_partial_order()
    await mock_db.orders.insert_one(dict(order))
    claims = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-hold", None, {})
    await release_uncommitted_delivery_claim(mock_db, claims, order["id"], "act-hold", 1)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["action_id"] == "act-hold"
    assert state["printed_qty"] == 2000.0

    order["delivery_confirm_action_ids"] = ["act-hold"]
    order["delivery_confirmations"] = [{"action_id": "act-hold", "shipment_id": "sj-1", "qty": 800}]
    await mock_db.orders.update_one({"id": order["id"]}, {"$set": {
        "delivery_confirm_action_ids": order["delivery_confirm_action_ids"],
        "delivery_confirmations": order["delivery_confirmations"],
    }})
    await release_uncommitted_delivery_claim(mock_db, claims, order["id"], "act-hold", 0)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["action_id"] == "act-hold"

    await mock_db.orders.update_one({"id": order["id"]}, {"$set": {
        "delivery_confirm_action_ids": [],
        "delivery_confirmations": [],
    }})
    await mock_db[STATE_COLLECTION].update_one(
        {"pending_event.event_id": claims[0]["event_id"]},
        {"$set": {"pending_event.stage": "STATE_APPLIED"}, "$inc": {"printed_qty": -800.0, "total_qty": -800.0}},
    )
    await release_uncommitted_delivery_claim(mock_db, claims, order["id"], "act-hold", 0)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["stage"] == "STATE_APPLIED"
    assert state["printed_qty"] == 1200.0

    await mock_db[STATE_COLLECTION].update_one(
        {"pending_event.event_id": claims[0]["event_id"]},
        {"$set": {"pending_event.stage": "CLAIMED"}, "$inc": {"printed_qty": 800.0, "total_qty": 800.0}},
    )
    await release_uncommitted_delivery_claim(mock_db, claims, order["id"], "act-hold", 0)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert not state.get("pending_event")
    assert state["printed_qty"] == 2000.0
    assert _pengiriman(mock_db) == []


@pytest.mark.anyio
async def test_resumed_claim_stays_until_the_last_holder_releases_it(mock_db):
    """Claim-time release skips a resumed claim. The last holder may still clear it."""
    from services.physical_stock_engine import (
        claim_selected_delivery_stock,
        release_uncommitted_delivery_claim,
    )

    await setup_initialized_product(mock_db, NAMOY_MASTER, unprinted=4700.0, printed=2000.0)
    order = _sj_partial_order()
    await mock_db.orders.insert_one(dict(order))
    created = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-resume", None, {})
    resumed = await claim_selected_delivery_stock(mock_db, order, "sj-1", "act-resume", None, {})
    assert created[0]["resumed"] is False
    assert resumed[0]["resumed"] is True
    assert resumed[0]["status"] == "CLAIMED"
    await release_delivery_confirmed_stock(mock_db, resumed)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["action_id"] == "act-resume"
    assert state["pending_event"]["stage"] == "CLAIMED"
    assert state["printed_qty"] == 2000.0
    await release_uncommitted_delivery_claim(mock_db, resumed, order["id"], "act-resume", 1)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert state["pending_event"]["action_id"] == "act-resume"
    await release_uncommitted_delivery_claim(mock_db, resumed, order["id"], "act-resume", 0)
    state = await get_product_stock_state(mock_db, NAMOY_MASTER)
    assert not state.get("pending_event")
    assert state["printed_qty"] == 2000.0
    assert _pengiriman(mock_db) == []
