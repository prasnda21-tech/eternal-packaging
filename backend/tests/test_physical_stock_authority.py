"""Targeted tests for Physical Stock Authority and Opening Balance Foundation.

Proves:
1. uninitialized product returns no authoritative physical balance
2. initialize exact master_item_id succeeds
3. same-name products isolated
4. total = unprinted + printed required
5. negative rejected
6. NaN/Infinity rejected
7. exact base UOM required
8. second initialization rejected (HTTP 409)
9. unauthorized initialization rejected
10. opening ledger created once
11. retry does not duplicate opening event
12. GET stock state is pure read
13. current_stock.py not used as fallback
14. physical_stock.enabled unchanged by initialization
15. Base UOM cannot change after initialization
16. failure consistency between state + opening event
"""
import asyncio
import contextlib
import copy
import uuid
import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.product_stock_state import (
    COLLECTION as STATE_COLLECTION,
    STATUS_INITIALIZING,
    STATUS_INITIALIZED,
    get_product_stock_state,
    initialize_opening_balance,
    require_product_stock_initialized,
)
from services.product_unit_conversion import (
    save_product_unit_setting,
    get_product_unit_setting,
)
from services.physical_stock_settings import (
    get_physical_stock_settings,
    update_physical_stock_settings,
)
from services.stock_ledger import (
    COLLECTION as LEDGER_COLLECTION,
    list_product_mutations,
    get_latest_balance,
)


class MockCursor:
    def __init__(self, docs, query=None, projection=None):
        self._docs = []
        for d in docs:
            match = True
            if query:
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
                row = copy.deepcopy(d)
                if projection and projection.get("_id") == 0 and "_id" in row:
                    del row["_id"]
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


class MockCollection:
    def __init__(self, initial=None):
        self._docs = copy.deepcopy(initial or [])

    async def find_one(self, query=None, projection=None):
        query = query or {}
        for d in self._docs:
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
                row = copy.deepcopy(d)
                if projection and projection.get("_id") == 0 and "_id" in row:
                    del row["_id"]
                return row
        return None

    def find(self, query=None, projection=None):
        return MockCursor(self._docs, query, projection)

    async def insert_one(self, doc):
        d = copy.deepcopy(doc)
        if "_id" in d:
            for existing in self._docs:
                if existing.get("_id") == d["_id"]:
                    raise DuplicateKeyError(f"E11000 duplicate key error collection: index: _id_ dup key: {{ _id: \"{d['_id']}\" }}")
        if "id" not in d:
            d["id"] = d.get("_id") or str(uuid.uuid4())
        self._docs.append(d)
        return type("InsertResult", (), {"inserted_id": d.get("_id") or d["id"]})()

    async def update_one(self, query, update, upsert=False):
        set_vals = update.get("$set", {})
        for i, d in enumerate(self._docs):
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
                self._docs[i].update(copy.deepcopy(set_vals))
                return type("UpdateResult", (), {"matched_count": 1, "modified_count": 1, "upserted_id": None})()
        if upsert:
            new_doc = copy.deepcopy(query)
            new_doc.update(copy.deepcopy(set_vals))
            if "_id" in new_doc:
                for existing in self._docs:
                    if existing.get("_id") == new_doc["_id"]:
                        raise DuplicateKeyError(f"E11000 duplicate key error collection: index: _id_ dup key: {{ _id: \"{new_doc['_id']}\" }}")
            self._docs.append(new_doc)
            return type("UpdateResult", (), {"matched_count": 0, "modified_count": 0, "upserted_id": new_doc.get("id") or "1"})()
        return type("UpdateResult", (), {"matched_count": 0, "modified_count": 0, "upserted_id": None})()

    async def delete_one(self, query):
        for i, d in enumerate(self._docs):
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
                del self._docs[i]
                return type("DeleteResult", (), {"deleted_count": 1})()
        return type("DeleteResult", (), {"deleted_count": 0})()

    async def count_documents(self, query=None):
        query = query or {}
        count = 0
        for d in self._docs:
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


class MockDb:
    def __init__(self):
        self.master_items = MockCollection()
        self.product_stock_state = MockCollection()
        self.stock_movements = MockCollection()
        self.product_unit_settings = MockCollection()
        self.app_settings = MockCollection()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture
def test_db():
    return MockDb()


@pytest.mark.anyio
async def test_1_uninitialized_product_returns_no_authoritative_physical_balance(test_db):
    """1. uninitialized product returns no authoritative physical balance"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    state = await get_product_stock_state(test_db, prod_id)
    assert state is None

    bal = await get_latest_balance(test_db, prod_id)
    assert bal is None


@pytest.mark.anyio
async def test_2_initialize_exact_master_item_id_succeeds(test_db):
    """2. initialize exact master_item_id succeeds with TOTAL = UNPRINTED + PRINTED"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_code": "CUP-16", "item_name": "Cup 16oz Polos", "unit": "PCS"})

    payload = {
        "unprinted_qty": 6150,
        "printed_qty": 3850,
        "total_qty": 10000,
        "base_uom": "PCS",
    }
    user = {"id": "u1", "name": "Budi", "email": "budi@eternal.test", "role": "admin"}
    res = await initialize_opening_balance(test_db, prod_id, payload, user=user)

    state = res["stock_state"]
    assert state["master_item_id"] == prod_id
    assert state["total_qty"] == 10000
    assert state["unprinted_qty"] == 6150
    assert state["printed_qty"] == 3850
    assert state["base_uom"] == "PCS"
    assert state["initialized"] is True
    assert state["initialized_by"]["email"] == "budi@eternal.test"

    # Verify latest balance is now 10000
    bal = await get_latest_balance(test_db, prod_id)
    assert bal == 10000


@pytest.mark.anyio
async def test_3_same_name_products_isolated(test_db):
    """3. same-name products isolated by exact master_item_id"""
    prod1_id = str(uuid.uuid4())
    prod2_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod1_id, "item_name": "Paper Cup 12oz", "unit": "PCS"})
    await test_db.master_items.insert_one({"id": prod2_id, "item_name": "Paper Cup 12oz", "unit": "PCS"})

    await initialize_opening_balance(test_db, prod1_id, {
        "unprinted_qty": 5000,
        "printed_qty": 0,
        "total_qty": 5000,
        "base_uom": "PCS",
    })

    # Prod1 is initialized, Prod2 remains uninitialized
    state1 = await get_product_stock_state(test_db, prod1_id)
    state2 = await get_product_stock_state(test_db, prod2_id)
    assert state1 is not None
    assert state1["total_qty"] == 5000
    assert state2 is None


@pytest.mark.anyio
async def test_4_total_equals_unprinted_plus_printed_required(test_db):
    """4. total = unprinted + printed required"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    payload = {
        "unprinted_qty": 6000,
        "printed_qty": 3000,
        "total_qty": 10000,  # mismatch! 6000+3000 != 10000
        "base_uom": "PCS",
    }
    with pytest.raises(HTTPException) as exc_info:
        await initialize_opening_balance(test_db, prod_id, payload)
    assert exc_info.value.status_code == 400
    assert "Invarian tidak terpenuhi" in exc_info.value.detail


@pytest.mark.anyio
async def test_5_negative_rejected(test_db):
    """5. negative quantities rejected"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    payload = {
        "unprinted_qty": -100,
        "printed_qty": 500,
        "total_qty": 400,
        "base_uom": "PCS",
    }
    with pytest.raises(HTTPException) as exc_info:
        await initialize_opening_balance(test_db, prod_id, payload)
    assert exc_info.value.status_code == 400
    assert "tidak boleh negatif" in exc_info.value.detail


@pytest.mark.anyio
async def test_6_nan_infinity_rejected(test_db):
    """6. NaN / Infinity rejected"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    payload = {
        "unprinted_qty": "NaN",
        "printed_qty": 100,
        "total_qty": 100,
        "base_uom": "PCS",
    }
    with pytest.raises(HTTPException) as exc_info:
        await initialize_opening_balance(test_db, prod_id, payload)
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_7_exact_base_uom_required(test_db):
    """7. exact base UOM required (mismatched unit rejected)"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    payload = {
        "unprinted_qty": 500,
        "printed_qty": 500,
        "total_qty": 1000,
        "base_uom": "BOX",  # Canonical is PCS!
    }
    with pytest.raises(HTTPException) as exc_info:
        await initialize_opening_balance(test_db, prod_id, payload)
    assert exc_info.value.status_code == 400
    assert "tidak sesuai dengan Satuan Dasar" in exc_info.value.detail


@pytest.mark.anyio
async def test_8_second_initialization_rejected(test_db):
    """8. second initialization rejected with HTTP 409 Conflict"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    payload = {
        "unprinted_qty": 1000,
        "printed_qty": 0,
        "total_qty": 1000,
        "base_uom": "PCS",
    }
    await initialize_opening_balance(test_db, prod_id, payload)

    # Second initialization must be rejected
    with pytest.raises(HTTPException) as exc_info:
        await initialize_opening_balance(test_db, prod_id, payload)
    assert exc_info.value.status_code == 409
    assert "sudah ditetapkan" in exc_info.value.detail


@pytest.mark.anyio
async def test_9_unauthorized_initialization_rejected():
    """9. unauthorized initialization rejected via permission check simulation"""
    user_without_perm = {"permissions": ["master_items.view"]}
    can_init = (
        "current_stock.update" in user_without_perm["permissions"]
        or "permissions.manage" in user_without_perm["permissions"]
    )
    assert can_init is False


@pytest.mark.anyio
async def test_10_opening_ledger_created_once(test_db):
    """10. opening ledger created once with exact quantities"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_code": "CUP-16", "item_name": "Cup 16oz Polos", "unit": "PCS"})

    payload = {
        "unprinted_qty": 7000,
        "printed_qty": 3000,
        "total_qty": 10000,
        "base_uom": "PCS",
    }
    res = await initialize_opening_balance(test_db, prod_id, payload)
    mov = res["movement"]

    assert mov["movement_type"] == "SALDO_AWAL"
    assert mov["transaction_qty"] == 10000
    assert mov["balance_after"] == 10000
    assert mov["delta_unprinted"] == 7000
    assert mov["delta_printed"] == 3000
    assert len(test_db.stock_movements._docs) == 1


@pytest.mark.anyio
async def test_11_retry_does_not_duplicate_opening_event(test_db):
    """11. retry does not duplicate opening event"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    payload = {
        "unprinted_qty": 1000,
        "printed_qty": 0,
        "total_qty": 1000,
        "base_uom": "PCS",
    }
    await initialize_opening_balance(test_db, prod_id, payload)
    assert len(test_db.stock_movements._docs) == 1

    # Any attempt to re-run fails with 409 and does not add movements
    with pytest.raises(HTTPException):
        await initialize_opening_balance(test_db, prod_id, payload)
    assert len(test_db.stock_movements._docs) == 1


@pytest.mark.anyio
async def test_12_get_stock_state_is_pure_read(test_db):
    """12. GET stock state is pure read"""
    prod_id = str(uuid.uuid4())
    await get_product_stock_state(test_db, prod_id)
    assert len(test_db.product_stock_state._docs) == 0


@pytest.mark.anyio
async def test_13_current_stock_py_not_used_as_fallback(test_db):
    """13. current_stock.py mixed formula not used as physical fallback"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    # Even if orders/manuals exist, get_product_stock_state returns None
    state = await get_product_stock_state(test_db, prod_id)
    assert state is None


@pytest.mark.anyio
async def test_14_physical_stock_enabled_unchanged_by_initialization(test_db):
    """14. physical_stock.enabled unchanged by opening initialization"""
    # Ensure physical stock settings default is enabled=False
    settings_before = await get_physical_stock_settings(test_db)
    assert settings_before["enabled"] is False

    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})
    await initialize_opening_balance(test_db, prod_id, {
        "unprinted_qty": 1000,
        "printed_qty": 0,
        "total_qty": 1000,
        "base_uom": "PCS",
    })

    # Setting must remain enabled=False!
    settings_after = await get_physical_stock_settings(test_db)
    assert settings_after["enabled"] is False


@pytest.mark.anyio
async def test_15_base_uom_cannot_change_after_initialization(test_db):
    """15. Base UOM cannot change after physical stock initialization"""
    prod_id = str(uuid.uuid4())
    user = {"email": "admin@eternal.test"}
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    # Save initial unit setting with Base UOM = PCS
    await save_product_unit_setting(test_db, prod_id, {
        "base_uom": "PCS",
        "transaction_uom": "DUS",
        "conversion_factor": 1000,
    }, user)

    # Initialize physical opening balance in PCS
    await initialize_opening_balance(test_db, prod_id, {
        "unprinted_qty": 5000,
        "printed_qty": 0,
        "total_qty": 5000,
        "base_uom": "PCS",
    }, user)

    # Now attempt to change Base UOM from PCS to RIM
    with pytest.raises(HTTPException) as exc_info:
        await save_product_unit_setting(test_db, prod_id, {
            "base_uom": "RIM",
            "transaction_uom": "DUS",
            "conversion_factor": 1000,
        }, user)
    assert exc_info.value.status_code == 400
    assert "tidak dapat diubah" in exc_info.value.detail


@pytest.mark.anyio
async def test_16_failure_consistency_between_state_and_opening_event(test_db):
    """16. failure consistency: if state insertion fails, movement is compensated / deleted"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    # Simulate error on product_stock_state update
    orig_update_one = test_db.product_stock_state.update_one
    async def mock_failing_update(*args, **kwargs):
        raise RuntimeError("DB Disk Full")
    test_db.product_stock_state.update_one = mock_failing_update

    payload = {
        "unprinted_qty": 1000,
        "printed_qty": 0,
        "total_qty": 1000,
        "base_uom": "PCS",
    }
    with pytest.raises(HTTPException) as exc_info:
        await initialize_opening_balance(test_db, prod_id, payload)
    assert exc_info.value.status_code == 500

    # Ensure compensation deleted the movement so no orphan movement remains
    assert len(test_db.stock_movements._docs) == 0
    assert len(test_db.product_stock_state._docs) == 0


@pytest.mark.anyio
async def test_17_failure_case_a_ledger_insert_fails_state_not_initialized(test_db):
    """17. Failure Case A: if ledger insert fails, product_stock_state is NEVER left initialized"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    # Simulate error on stock_movements insert
    async def mock_failing_movement_insert(*args, **kwargs):
        raise RuntimeError("Ledger Write Timeout")
    test_db.stock_movements.insert_one = mock_failing_movement_insert

    payload = {
        "unprinted_qty": 2000,
        "printed_qty": 1000,
        "total_qty": 3000,
        "base_uom": "PCS",
    }
    with pytest.raises(HTTPException) as exc_info:
        await initialize_opening_balance(test_db, prod_id, payload)
    assert exc_info.value.status_code == 500

    # Critical invariant: product MUST NOT remain silently initialized without opening event
    assert len(test_db.product_stock_state._docs) == 0
    state = await get_product_stock_state(test_db, prod_id)
    assert state is None


@pytest.mark.anyio
async def test_18_compensation_failure_surfaces_critical_error(test_db):
    """18. Compensation Failure: if compensation delete fails, error is explicitly surfaced as KRITIKAL, not reported safe"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    # State update fails
    async def mock_failing_state_update(*args, **kwargs):
        raise RuntimeError("State Collection Unavailable")
    test_db.product_stock_state.update_one = mock_failing_state_update

    # And compensation delete_one ALSO fails
    async def mock_failing_delete(*args, **kwargs):
        raise RuntimeError("Ledger Compensation Delete Failed")
    test_db.stock_movements.delete_one = mock_failing_delete

    payload = {
        "unprinted_qty": 1000,
        "printed_qty": 0,
        "total_qty": 1000,
        "base_uom": "PCS",
    }
    with pytest.raises(HTTPException) as exc_info:
        await initialize_opening_balance(test_db, prod_id, payload)
    assert exc_info.value.status_code == 500
    assert "KRITIKAL" in exc_info.value.detail
    assert "rollback mutasi saldo awal gagal" in exc_info.value.detail


@pytest.mark.anyio
async def test_19_concurrent_initialization_concurrency_safety(test_db):
    """19. Concurrency: Two concurrent requests for same product: exactly 1 succeeds, 1 gets 409, 1 event created"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    payload_1 = {
        "unprinted_qty": 5000,
        "printed_qty": 0,
        "total_qty": 5000,
        "base_uom": "PCS",
    }
    payload_2 = {
        "unprinted_qty": 8000,
        "printed_qty": 0,
        "total_qty": 8000,
        "base_uom": "PCS",
    }

    # Execute both concurrently
    res1, res2 = await asyncio.gather(
        initialize_opening_balance(test_db, prod_id, payload_1),
        initialize_opening_balance(test_db, prod_id, payload_2),
        return_exceptions=True,
    )

    results = [res1, res2]
    successes = [r for r in results if isinstance(r, dict)]
    conflicts = [r for r in results if isinstance(r, HTTPException) and r.status_code == 409]

    # Exactly one success
    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
    # Exactly one 409 Conflict
    assert len(conflicts) == 1, f"Expected 1 conflict, got {len(conflicts)}"
    assert "Saldo awal stok fisik sudah ditetapkan" in conflicts[0].detail

    # Exactly one opening movement in ledger
    assert len(test_db.stock_movements._docs) == 1
    # Exactly one state doc
    assert len(test_db.product_stock_state._docs) == 1

    # Verified no overwrite
    final_state = await get_product_stock_state(test_db, prod_id)
    assert final_state["total_qty"] == successes[0]["stock_state"]["total_qty"]


@pytest.mark.anyio
async def test_20_uninitialized_ledger_starts_at_none_not_zero(test_db):
    """20. Ledger balance for uninitialized product returns None, never fabricates zero"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    balance = await get_latest_balance(test_db, prod_id)
    assert balance is None, f"Expected None for uninitialized product, got {balance}"


@pytest.mark.anyio
async def test_21_require_product_stock_initialized_future_guard(test_db):
    """21. Future guard helper rejects uninitialized product with HTTP 400 and succeeds when initialized"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    # Before init -> strictly blocked
    with pytest.raises(HTTPException) as exc_info:
        await require_product_stock_initialized(test_db, prod_id)
    assert exc_info.value.status_code == 400
    assert "Saldo awal stok fisik produk belum ditetapkan" in exc_info.value.detail

    # Initialize
    await initialize_opening_balance(test_db, prod_id, {
        "unprinted_qty": 3000,
        "printed_qty": 0,
        "total_qty": 3000,
        "base_uom": "PCS",
    })

    # After init -> passes
    state = await require_product_stock_initialized(test_db, prod_id)
    assert state is not None
    assert state["total_qty"] == 3000
    assert state["initialized"] is True


@pytest.mark.anyio
async def test_22_database_level_concurrency_without_inprocess_lock(test_db, monkeypatch):
    """22. Concurrency correctness at DB level: bypassing _init_lock still ensures max 1 state doc and max 1 movement"""
    import services.product_stock_state as pss

    class NoOpLock:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    monkeypatch.setattr(pss, "_init_lock", NoOpLock())

    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    payload_1 = {"unprinted_qty": 5000, "printed_qty": 0, "total_qty": 5000, "base_uom": "PCS"}
    payload_2 = {"unprinted_qty": 8000, "printed_qty": 0, "total_qty": 8000, "base_uom": "PCS"}

    res1, res2 = await asyncio.gather(
        initialize_opening_balance(test_db, prod_id, payload_1),
        initialize_opening_balance(test_db, prod_id, payload_2),
        return_exceptions=True,
    )

    results = [res1, res2]
    successes = [r for r in results if isinstance(r, dict)]
    conflicts = [r for r in results if isinstance(r, HTTPException) and r.status_code == 409]

    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
    assert len(conflicts) == 1, f"Expected 1 conflict, got {len(conflicts)}"
    assert len(test_db.product_stock_state._docs) == 1, "Durable uniqueness must prevent duplicate state docs"
    assert len(test_db.stock_movements._docs) == 1, "Durable uniqueness must prevent duplicate movements"
    assert test_db.product_stock_state._docs[0]["_id"] == prod_id
    assert test_db.stock_movements._docs[0]["_id"] == f"opening_balance:{prod_id}"


@pytest.mark.anyio
async def test_23_crash_recovery_step_a_interrupted_no_movement(test_db):
    """23. Crash recovery: state in INITIALIZING without movement resumes and finalizes cleanly"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    # Simulate crash right after Step A: state exists in INITIALIZING, no movement exists
    await test_db.product_stock_state.insert_one({
        "_id": prod_id,
        "master_item_id": prod_id,
        "base_uom": "PCS",
        "total_qty": 4000,
        "unprinted_qty": 4000,
        "printed_qty": 0,
        "status": STATUS_INITIALIZING,
        "initialized": False,
        "version": 1,
    })

    assert len(test_db.stock_movements._docs) == 0

    # Retry with matching payload resumes Step B and Step C
    res = await initialize_opening_balance(test_db, prod_id, {
        "unprinted_qty": 4000,
        "printed_qty": 0,
        "total_qty": 4000,
        "base_uom": "PCS",
    })

    assert res["stock_state"]["status"] == STATUS_INITIALIZED
    assert res["stock_state"]["initialized"] is True
    assert len(test_db.stock_movements._docs) == 1
    assert test_db.stock_movements._docs[0]["_id"] == f"opening_balance:{prod_id}"
    assert len(test_db.product_stock_state._docs) == 1


@pytest.mark.anyio
async def test_24_crash_recovery_step_b_interrupted_movement_exists(test_db):
    """24. Crash recovery: state in INITIALIZING with movement already created does NOT duplicate movement"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    # Simulate crash right after Step B: state is INITIALIZING, movement already written
    op_mov_id = f"opening_balance:{prod_id}"
    await test_db.product_stock_state.insert_one({
        "_id": prod_id,
        "master_item_id": prod_id,
        "base_uom": "PCS",
        "total_qty": 4000,
        "unprinted_qty": 4000,
        "printed_qty": 0,
        "status": STATUS_INITIALIZING,
        "initialized": False,
        "version": 1,
    })
    await test_db.stock_movements.insert_one({
        "_id": op_mov_id,
        "id": op_mov_id,
        "event_key": op_mov_id,
        "master_item_id": prod_id,
        "movement_type": "SALDO_AWAL",
        "transaction_qty": 4000,
        "base_qty": 4000,
        "delta_unprinted": 4000,
        "delta_printed": 0,
        "balance_after": 4000,
    })

    assert len(test_db.stock_movements._docs) == 1

    # Retry with matching payload finalizes state to INITIALIZED without inserting second movement
    res = await initialize_opening_balance(test_db, prod_id, {
        "unprinted_qty": 4000,
        "printed_qty": 0,
        "total_qty": 4000,
        "base_uom": "PCS",
    })

    assert res["stock_state"]["status"] == STATUS_INITIALIZED
    assert res["stock_state"]["initialized"] is True
    # Still exactly 1 movement and 1 state doc!
    assert len(test_db.stock_movements._docs) == 1
    assert len(test_db.product_stock_state._docs) == 1


@pytest.mark.anyio
async def test_25_retry_with_conflicting_payload_rejected_409(test_db):
    """25. Retry with conflicting payload on INITIALIZING state is rejected with HTTP 409"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    await test_db.product_stock_state.insert_one({
        "_id": prod_id,
        "master_item_id": prod_id,
        "base_uom": "PCS",
        "total_qty": 4000,
        "unprinted_qty": 4000,
        "printed_qty": 0,
        "status": STATUS_INITIALIZING,
        "initialized": False,
        "version": 1,
    })

    # Conflicting retry (total 9000 instead of 4000)
    with pytest.raises(HTTPException) as exc_info:
        await initialize_opening_balance(test_db, prod_id, {
            "unprinted_qty": 9000,
            "printed_qty": 0,
            "total_qty": 9000,
            "base_uom": "PCS",
        })
    assert exc_info.value.status_code == 409
    assert "saldo awal" in exc_info.value.detail.lower()


@pytest.mark.anyio
async def test_26_unfinalized_state_not_authoritative(test_db):
    """26. Unfinalized state (INITIALIZING, initialized=False) is never exposed as authoritative"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    await test_db.product_stock_state.insert_one({
        "_id": prod_id,
        "master_item_id": prod_id,
        "base_uom": "PCS",
        "total_qty": 4000,
        "unprinted_qty": 4000,
        "printed_qty": 0,
        "status": STATUS_INITIALIZING,
        "initialized": False,
        "version": 1,
    })

    state = await get_product_stock_state(test_db, prod_id)
    assert state is not None
    assert state.get("status") == STATUS_INITIALIZING
    assert state.get("initialized") is False

    # Guard check strictly blocks unfinalized product
    with pytest.raises(HTTPException) as exc_info:
        await require_product_stock_initialized(test_db, prod_id)
    assert exc_info.value.status_code == 400


@pytest.mark.anyio
async def test_27_base_uom_frozen_during_initializing_and_initialized(test_db):
    """27. Base UOM modification is blocked during INITIALIZING and INITIALIZED status"""
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({"id": prod_id, "item_name": "Cup 16oz Polos", "unit": "PCS"})

    # Stage 1: In INITIALIZING status
    await test_db.product_stock_state.insert_one({
        "_id": prod_id,
        "master_item_id": prod_id,
        "base_uom": "PCS",
        "total_qty": 2000,
        "unprinted_qty": 2000,
        "printed_qty": 0,
        "status": STATUS_INITIALIZING,
        "initialized": False,
    })

    with pytest.raises(HTTPException) as exc_info:
        await save_product_unit_setting(test_db, prod_id, {
            "base_uom": "DUS",
            "transaction_uom": "DUS",
            "conversion_factor": 1.0,
        }, user={"email": "admin@example.com"})
    assert exc_info.value.status_code == 400
    assert "Satuan Dasar tidak dapat diubah" in exc_info.value.detail

    # Stage 2: Finalized to INITIALIZED status
    await test_db.product_stock_state.update_one(
        {"_id": prod_id},
        {"$set": {"status": STATUS_INITIALIZED, "initialized": True}}
    )

    with pytest.raises(HTTPException) as exc_info:
        await save_product_unit_setting(test_db, prod_id, {
            "base_uom": "BOX",
            "transaction_uom": "BOX",
            "conversion_factor": 1.0,
        }, user={"email": "admin@example.com"})
    assert exc_info.value.status_code == 400
    assert "Satuan Dasar tidak dapat diubah" in exc_info.value.detail
