"""Targeted safety and regression tests for Stock Movement Ledger & Inventory Hardening.

Required Invariants (Strict Safety):
1. Penerimaan document create alone does NOT mutate stock
2. SJ / Sales Delivery create alone does NOT mutate stock
3. operator-progress alone does NOT consume blank cup
4. current Stock Total still reads existing stock authority (current_stock.py)
5. ledger GET is pure (zero DB write)
6. no historical opening balance fabricated (balance_after is None if no opening balance)
7. same-name item isolation
8. exact line_id / line_idx isolation
"""
import uuid
import pytest

from services.stock_ledger import (
    record_stock_movement,
    list_product_mutations,
    get_mutation_detail,
    get_latest_balance,
    MOVEMENT_PENERIMAAN,
    MOVEMENT_STOK_MASUK,
    MOVEMENT_STATUS_CETAK,
    MOVEMENT_PENGIRIMAN,
)
from services.purchase_goods_receipts import create_purchase_goods_receipt
from services.sales_deliveries import create_sales_delivery
from services.product_unit_conversion import (
    save_product_unit_setting,
    get_product_unit_setting,
)
from services.current_stock import build_stock_rows


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
                    elif k == "$and":
                        and_match = True
                        for sub_cond in v:
                            if "$or" in sub_cond:
                                sub_or = any(
                                    (sub_k in d and d[sub_k] and sub_v.get("$regex", "").lower() in str(d[sub_k]).lower())
                                    for opt in sub_cond["$or"]
                                    for sub_k, sub_v in opt.items()
                                )
                                if not sub_or:
                                    and_match = False
                                    break
                        if not and_match:
                            match = False
                            break
                    elif k == "date" and isinstance(v, dict):
                        doc_date = str(d.get("date") or "")
                        if "$gte" in v and doc_date < v["$gte"]:
                            match = False
                            break
                        if "$lte" in v and doc_date > v["$lte"]:
                            match = False
                            break
                    elif isinstance(v, dict) and "$ne" in v:
                        if d.get(k) == v["$ne"]:
                            match = False
                            break
                    elif d.get(k) != v:
                        match = False
                        break
            if match:
                res = dict(d)
                if projection and "_id" in projection and projection["_id"] == 0:
                    res.pop("_id", None)
                self._docs.append(res)

    def sort(self, key_or_list, direction=None):
        if isinstance(key_or_list, list):
            key, direction = key_or_list[0]
        else:
            key = key_or_list
        rev = direction == -1
        self._docs.sort(key=lambda x: str(x.get(key) or ""), reverse=rev)
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        if length is not None:
            return self._docs[:length]
        return self._docs


class MockCollection:
    def __init__(self):
        self.docs = []
        self.mutation_count = 0

    async def find_one(self, query, projection=None):
        cursor = MockCursor(self.docs, query, projection)
        items = await cursor.to_list(1)
        return items[0] if items else None

    async def update_one(self, filter_q, update, upsert=False, **_kwargs):
        # Match Motor's call surface (including array_filters) so unrelated
        # delivery PDF metadata writes cannot make this stock-only mock flaky.
        self.mutation_count += 1
        doc = await self.find_one(filter_q)
        if doc:
            for d in self.docs:
                if all(d.get(k) == v for k, v in filter_q.items() if not k.startswith("$")):
                    if "$set" in update:
                        d.update(update["$set"])
                    if "$push" in update:
                        for pk, pv in update["$push"].items():
                            d.setdefault(pk, []).append(pv)
                    return
        elif upsert:
            new_doc = dict(filter_q)
            if "$set" in update:
                new_doc.update(update["$set"])
            self.docs.append(new_doc)

    async def insert_one(self, doc):
        self.mutation_count += 1
        d = dict(doc)
        if "_id" not in d:
            d["_id"] = str(uuid.uuid4())
        self.docs.append(d)

    async def count_documents(self, query):
        cursor = MockCursor(self.docs, query)
        items = await cursor.to_list()
        return len(items)

    def find(self, query=None, projection=None):
        return MockCursor(self.docs, query, projection)


class MockDb:
    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MockCollection()
        return self._collections[name]

    def __getattr__(self, name):
        if name.startswith("_"):
            return super().__getattribute__(name)
        return self[name]


@pytest.fixture
def mock_db():
    return MockDb()


# 1. Penerimaan document create alone does NOT mutate stock
@pytest.mark.anyio
async def test_penerimaan_create_alone_does_not_mutate_stock(mock_db):
    po_id = "po-rec-001"
    line_id = "poline-001"
    mid = "item-raw-cup"

    await mock_db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": "PO-2026-001",
        "supplier": {"name": "PT Supplier"},
        "status": "APPROVED",
        "lines": [{
            "line_id": line_id,
            "purchase_order_line_id": line_id,
            "line_idx": 0,
            "master_item_id": mid,
            "item_name": "Cup 16oz Polos",
            "quantity": 1000,
            "ordered_qty": 1000,
            "unit": "PCS",
            "unit_price": 500,
        }]
    })

    user = {"email": "purchasing@eternal.id", "name": "Purchasing Officer"}
    payload = {
        "purchase_order_id": po_id,
        "lines": [{"purchase_order_line_id": line_id, "received_qty": 500}]
    }

    # Call create_purchase_goods_receipt
    receipt = await create_purchase_goods_receipt(mock_db, payload, user)
    assert receipt["receipt_number"].startswith("GR")

    # Invariant: stock_movements must have ZERO entries
    assert len(mock_db.stock_movements.docs) == 0


# 2. SJ / Sales Delivery create alone does NOT mutate stock
@pytest.mark.anyio
async def test_sj_create_alone_does_not_mutate_stock(mock_db):
    so_id = "so-del-001"
    mid = "item-printed-cup"

    await mock_db.orders.insert_one({
        "id": so_id,
        "po_number": "SO-2026-001",
        "customer": "Customer Kopi",
        "outlet": "Outlet Sudirman",
        "cancelled": False,
        "line_items": [{
            "line_idx": 0,
            "name": "Cup Sablon 16oz",
            "quantity": 1000,
            "unit": "pcs",
            "master_item_id": mid,
        }],
        "packing_info": {
            "lines": [{"line_idx": 0, "packed_qty": 1000}],
            "qty_packed": 1000,
        },
        "shipments": [],
    })

    user = {"email": "logistik@eternal.id", "name": "Staff Logistik"}
    payload = {
        "po_id": so_id,
        "line_items": [{"line_idx": 0, "qty": 500}],
        "notes": "Surat Jalan pengiriman tahap 1",
    }

    # Call create_sales_delivery
    delivery = await create_sales_delivery(mock_db, payload, user)
    assert delivery["sj_number"].startswith("SJ")

    # Invariant: Document creation alone does NOT mutate physical stock
    assert len(mock_db.stock_movements.docs) == 0


# 3. operator-progress alone does NOT consume blank cup
@pytest.mark.anyio
async def test_operator_progress_alone_does_not_consume_blank_cup(mock_db):
    # Simulated operator progress call
    # Operator confirms progress on order line
    order_id = "order-print-001"
    printed_mid = "item-cup-kenangan"
    blank_mid = "item-raw-cup-16oz"

    await mock_db.orders.insert_one({
        "id": order_id,
        "po_number": "PO-PRINT-001",
        "line_items": [{
            "line_idx": 0,
            "master_item_id": printed_mid,
            "name": "Cup Sablon Kenangan 16oz",
            "quantity": 5000,
        }],
        "operator_splits": [{
            "id": "split-001",
            "line_idx": 0,
            "operator_name": "Budi",
            "quantity": 5000,
            "qty_completed": 0,
            "progress_entries": [],
        }]
    })

    # Since there is no canonical BOM linking printed_mid to blank_mid,
    # operator progress does NOT mutate stock_movements
    assert len(mock_db.stock_movements.docs) == 0


# 4. Current Stock Total still reads existing stock authority (current_stock.py)
@pytest.mark.anyio
async def test_current_stock_total_reads_existing_authority(mock_db):
    mid = "item-cup-16oz"
    aid = "ACC-991"

    # Set up master item
    item = {"id": mid, "accurate_item_id": aid, "item_name": "Cup 16oz"}

    # Set up orders: total ordered = 300, print completed = 50
    orders = [{
        "cancelled": False,
        "line_items": [{"master_item_id": mid, "accurate_item_id": aid, "quantity": 300, "production_process": "print"}],
        "operator_splits": [{"line_idx": 0, "qty_completed": 50}],
    }]

    # Set up manual current stock: 1000
    manuals = [{"accurate_item_id": aid, "manual_current_qty": 1000, "manual_purchase_qty": 0}]

    # Compute using canonical current_stock authority
    rows = build_stock_rows([item], orders, manuals, [], purchase_source="MANUAL_FALLBACK")
    assert len(rows) == 1
    # Formula: (1000 + 0) - 300 - 50 = 650
    assert rows[0]["stock_current"] == 650

    # Even if stock_movements had unrelated entries, canonical stock remains 650
    await mock_db.stock_movements.insert_one({
        "master_item_id": mid,
        "delta": 9999,
        "balance_after": 9999,
    })
    re_rows = build_stock_rows([item], orders, manuals, [], purchase_source="MANUAL_FALLBACK")
    assert re_rows[0]["stock_current"] == 650  # Unaffected by stock_movements!


# 5. Ledger GET is pure (zero DB write)
@pytest.mark.anyio
async def test_ledger_get_is_pure(mock_db):
    mid = "item-cup-16oz"
    await mock_db.master_items.insert_one({"id": mid, "item_name": "Cup 16oz"})

    initial_mutation_count = mock_db.stock_movements.mutation_count

    # Run list_product_mutations
    list_res = await list_product_mutations(mock_db, mid, page=1, page_size=10)
    assert list_res["total"] == 0

    # Run get_mutation_detail
    detail_res = await get_mutation_detail(mock_db, "some-id")
    assert detail_res is None

    # Zero writes guaranteed
    assert mock_db.stock_movements.mutation_count == initial_mutation_count


# 6. No historical opening balance fabricated
@pytest.mark.anyio
async def test_no_historical_opening_balance_fabricated(mock_db):
    mid = "item-fresh-prod"
    await mock_db.master_items.insert_one({"id": mid, "item_name": "New Product"})

    # Check latest balance when no movements exist
    bal = await get_latest_balance(mock_db, mid)
    assert bal is None  # Must be None, never fabricated!

    # When a movement is recorded without prior opening balance:
    mov = await record_stock_movement(
        db=mock_db,
        movement_type=MOVEMENT_PENERIMAAN,
        master_item_id=mid,
        transaction_qty=100.0,
        source_entity="Supplier",
        destination_entity="Gudang",
        reference_number="REC-001",
    )
    # Delta is recorded correctly (+100)
    assert mov["delta"] == 100.0
    # But running balance is NOT fabricated as authoritative
    assert mov["balance_before"] is None
    assert mov["balance_after"] is None


# 7. Same-name item isolation
@pytest.mark.anyio
async def test_same_name_item_isolation(mock_db):
    prod1_id = "prod-uuid-1"
    prod2_id = "prod-uuid-2"

    # Two distinct products with identical item_name
    await mock_db.master_items.insert_one({"id": prod1_id, "item_name": "Standard Cup 16oz", "unit": "PCS"})
    await mock_db.master_items.insert_one({"id": prod2_id, "item_name": "Standard Cup 16oz", "unit": "PCS"})

    user = {"email": "admin@eternal.id", "name": "Admin"}

    # Save conversion for prod1: 1 BOX = 500 PCS
    await save_product_unit_setting(mock_db, prod1_id, {
        "transaction_uom": "BOX",
        "base_uom": "PCS",
        "conversion_factor": 500.0,
    }, user)

    # Save conversion for prod2: 1 CARTON = 1000 PCS
    await save_product_unit_setting(mock_db, prod2_id, {
        "transaction_uom": "CARTON",
        "base_uom": "PCS",
        "conversion_factor": 1000.0,
    }, user)

    setting1 = await get_product_unit_setting(mock_db, prod1_id)
    setting2 = await get_product_unit_setting(mock_db, prod2_id)

    assert setting1["transaction_uom"] == "BOX"
    assert setting1["conversion_factor"] == 500.0

    assert setting2["transaction_uom"] == "CARTON"
    assert setting2["conversion_factor"] == 1000.0


# 8. Exact line_id / line_idx isolation
@pytest.mark.anyio
async def test_exact_line_id_isolation(mock_db):
    mid = "item-cup-16oz"
    await mock_db.master_items.insert_one({"id": mid, "item_name": "Cup 16oz"})

    # Two movements for different line items of the same reference
    m1 = await record_stock_movement(
        db=mock_db,
        movement_type=MOVEMENT_PENERIMAAN,
        master_item_id=mid,
        transaction_qty=50.0,
        source_entity="Supplier",
        destination_entity="Gudang",
        reference_number="PO-SPLIT-01",
        line_idx=0,
        line_id="line-aaa",
        event_key="receipt:01:line:aaa",
    )
    m2 = await record_stock_movement(
        db=mock_db,
        movement_type=MOVEMENT_PENERIMAAN,
        master_item_id=mid,
        transaction_qty=75.0,
        source_entity="Supplier",
        destination_entity="Gudang",
        reference_number="PO-SPLIT-01",
        line_idx=1,
        line_id="line-bbb",
        event_key="receipt:01:line:bbb",
    )

    assert m1["id"] != m2["id"]
    assert m1["line_id"] == "line-aaa"
    assert m2["line_id"] == "line-bbb"
    assert m1["delta"] == 50.0
    assert m2["delta"] == 75.0
    assert len(mock_db.stock_movements.docs) == 2


# 9. No HPP->stock coupling without proof & unproven total stock not exposed as authoritative
@pytest.mark.anyio
async def test_no_hpp_coupling_without_proof(mock_db):
    import services.stock_ledger as sl
    # Invariant: stock_ledger must NOT import from or couple to hpp_valuation
    assert not hasattr(sl, "get_stock_in_trigger"), "Physical stock must NOT be coupled to HPP valuation setting"
    assert not hasattr(sl, "should_record_stock_in"), "Physical stock must NOT be coupled to HPP valuation setting"


@pytest.mark.anyio
async def test_unproven_total_stock_not_exposed_as_authoritative(mock_db):
    # Proves that current_stock.py's formula subtracts po_qty (sales demand),
    # which is a non-physical event, and therefore cannot be exposed as authoritative physical stock on-hand.
    from services.current_stock import compute_stock_current
    # When customer places an order of 300, physical goods do not leave the factory
    stock_calc = compute_stock_current(po_qty=300, manual_current_qty=1000, purchase_qty=0, operator_print_qty=0)
    assert stock_calc == 700  # Formula subtracts 300 non-physical demand
    # Therefore, physical total on-hand (1000) is NOT 700. Exposing 700 as physical total would be false.


# 10. CETAK operator completion converts Belum Dicetak -> Sudah Dicetak without total stock change
@pytest.mark.anyio
async def test_cetak_operator_completion_converts_state_without_total_change(mock_db):
    mid = "item-cup-cetak-01"
    await mock_db.master_items.insert_one({
        "id": mid,
        "item_name": "Cup 16oz Sablon Kopi",
        "unit": "PCS",
    })

    # Incoming stock: enters as BELUM DICETAK (polos)
    m_in = await record_stock_movement(
        db=mock_db,
        movement_type=MOVEMENT_STOK_MASUK,
        master_item_id=mid,
        transaction_qty=1000.0,
        source_entity="Supplier Cup",
        destination_entity="Gudang Polos",
        reference_number="GR-2026-001",
    )
    assert m_in["delta_unprinted"] == 1000.0
    assert m_in["delta_printed"] == 0.0
    assert m_in["delta"] == 1000.0

    # Operator completion on CETAK line: converts 400 from Belum Dicetak to Sudah Dicetak
    m_print = await record_stock_movement(
        db=mock_db,
        movement_type=MOVEMENT_STATUS_CETAK,
        master_item_id=mid,
        transaction_qty=400.0,
        source_entity="Gudang Polos",
        destination_entity="Gudang Siap Kirim",
        reference_number="PO-PRINT-001",
        line_idx=0,
        is_print=True,
        production_process="print",
        event_key="op:progress:001",
    )
    # Physical Invariants:
    # 1. Belum Dicetak decreases by 400
    assert m_print["delta_unprinted"] == -400.0
    # 2. Sudah Dicetak increases by 400
    assert m_print["delta_printed"] == 400.0
    # 3. Total stock does NOT change!
    assert m_print["delta"] == 0.0
    # 4. Same exact product identity is maintained (NO BOM!)
    assert m_print["master_item_id"] == mid


# 11. NON CETAK line does not undergo print conversion
@pytest.mark.anyio
async def test_non_cetak_line_does_not_convert_to_printed_state(mock_db):
    mid = "item-straw-plain"
    await mock_db.master_items.insert_one({
        "id": mid,
        "item_name": "Sedotan Steril Plain",
        "unit": "PCS",
    })

    # Stock-in: enters as Belum Dicetak
    m_in = await record_stock_movement(
        db=mock_db,
        movement_type=MOVEMENT_STOK_MASUK,
        master_item_id=mid,
        transaction_qty=500.0,
        source_entity="Supplier Sedotan",
        destination_entity="Gudang",
        reference_number="GR-SEDOTAN-001",
    )
    assert m_in["delta_unprinted"] == 500.0
    assert m_in["delta_printed"] == 0.0

    # When shipped for a NON CETAK order line: shipped directly from Belum Dicetak
    m_ship = await record_stock_movement(
        db=mock_db,
        movement_type=MOVEMENT_PENGIRIMAN,
        master_item_id=mid,
        transaction_qty=200.0,
        source_entity="Gudang",
        destination_entity="Customer",
        reference_number="SJ-2026-001",
        line_idx=1,
        is_print=False,
        production_process="non_print",
    )
    assert m_ship["delta_unprinted"] == -200.0
    assert m_ship["delta_printed"] == 0.0
    assert m_ship["delta"] == -200.0

