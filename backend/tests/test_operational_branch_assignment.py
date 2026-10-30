import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException


def service():
    path = Path(__file__).parents[1] / 'services/operational_branch.py'
    assert path.exists(), 'Missing shared operational branch authority'
    spec = importlib.util.spec_from_file_location('operational_branch', path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


class Cursor:
    def __init__(self, rows): self.rows = rows
    async def to_list(self, *a): return self.rows

class Branches:
    def __init__(self): self.rows = [{'id': 'ygy', 'code': 'YGY', 'active': True}, {'id': 'smg', 'code': 'SMG', 'active': True}]
    async def find_one(self, query, *args):
        return next((r for r in self.rows if all(r.get(k) == v for k,v in query.items() if not isinstance(v, dict))), None)
    def find(self, *a): return Cursor(self.rows)


def test_assignment_only_holding_director_and_primary_in_allowed_set():
    m=service(); db=SimpleNamespace(branches=Branches())
    with pytest.raises(HTTPException):
        asyncio.run(m.validate_assignment(db, {'role':'admin'}, ['ygy'], 'ygy'))
    with pytest.raises(HTTPException):
        asyncio.run(m.validate_assignment(db, {'role':'holding_team'}, ['ygy'], 'smg'))
    assert asyncio.run(m.validate_assignment(db, {'role':'holding_team'}, ['ygy','ygy'], 'ygy')) == {'branch_ids':['ygy'], 'primary_branch_id':'ygy', 'branch_id':'ygy'}


def test_new_po_defaults_to_assigned_primary_and_rejects_other_branch():
    m=service(); db=SimpleNamespace(branches=Branches())
    user={'role':'crm','branch_ids':['ygy'],'primary_branch_id':'ygy'}
    assert asyncio.run(m.resolve_write_branch(db,user)) == 'ygy'
    with pytest.raises(HTTPException): asyncio.run(m.resolve_write_branch(db,user,'smg'))


def test_unassigned_user_gets_actionable_error_not_empty_table():
    m=service(); db=SimpleNamespace(branches=Branches())
    with pytest.raises(HTTPException, match='Akses cabang belum ditetapkan'):
        asyncio.run(m.operational_access(db,{'role':'crm'}))


def test_canonical_branch_wins_over_external_id():
    m=service()
    assert m.document_branch({'operational_branch_id':'ygy','branch_id':'50'}) == 'ygy'
    assert m.document_branch({'branch_id':'smg'}) == 'smg'


@pytest.fixture(autouse=True)
def production_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://production.test")


def test_cutover_preserves_external_id_and_assigns_nested_shipments():
    import importlib.util
    path=Path(__file__).parents[1]/'scripts/migrate_ygy_operational.py'
    assert path.exists(), 'Missing explicit, dry-run-first migration'
    spec=importlib.util.spec_from_file_location('cutover',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    doc={'branch_id':'50','shipments':[{'id':'sj1','branch_id':'50'}]}
    changes=m.changes_for('orders',doc,'ygy')
    assert 'branch_id' not in changes
    assert changes['operational_branch_id']=='ygy'
    assert changes['shipments'][0]['branch_id']=='50'
    assert changes['shipments'][0]['operational_branch_id']=='ygy'
    assert m.changes_for('orders',{**doc,**changes},'ygy')=={}


@pytest.mark.anyio
async def test_ygy_cutover_dry_run_apply_repeat_and_safe_rollback(tmp_path, capsys):
    import importlib.util
    AsyncMongoMockClient = pytest.importorskip("mongomock_motor").AsyncMongoMockClient
    from bson import json_util
    scripts=Path(__file__).parents[1]/'scripts'
    def module(name):
        spec=importlib.util.spec_from_file_location(name,scripts/(name+'.py'))
        result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
    migration=module('migrate_ygy_operational');rollback=module('rollback_ygy_operational')
    db=AsyncMongoMockClient().test
    await db.users.insert_one({'id':'crm','role':'crm'})
    await db.orders.insert_one({'id':'po','branch_id':'50','grand_total':1234,'total_quantity':50,'shipments':[{'id':'sj','branch_id':'50','qty':20}]})
    await db.sales_invoices.insert_one({'id':'invoice','po_id':'po','branch_id':'50','grand_total':1234})
    await db.pra_pos.insert_one({'id':'pra'})
    before=await db.orders.find_one({'id':'po'})
    await migration.run(db,SimpleNamespace(apply=False,all_existing_ygy=False,backup=None))
    assert await db.orders.find_one({'id':'po'}) == before
    assert await db.branches.count_documents({}) == 0
    backup=tmp_path/'before.jsonl'
    await migration.run(db,SimpleNamespace(apply=True,all_existing_ygy=True,backup=str(backup)))
    ygy=(await db.branches.find_one({'code':'YGY'}))['id']
    assert (await db.pra_pos.find_one({'id':'pra'}))['operational_branch_id']==ygy
    with pytest.raises(RuntimeError, match='sudah selesai'):
        await migration.run(db,SimpleNamespace(apply=True,all_existing_ygy=True,backup=str(tmp_path/'repeat.jsonl')))
    user=await db.users.find_one({'id':'crm'})
    m=service(); query=await m.operational_query(db,user)
    assert await db.orders.count_documents(query)==1
    current=await db.orders.find_one({'id':'po'})
    assert current['branch_id']=='50' and current['grand_total']==1234 and current['total_quantity']==50
    assert current['shipments'][0]['operational_branch_id']==ygy
    assert migration.changes_for('orders',current,ygy)=={}
    records=[json_util.loads(line) for line in backup.read_text().splitlines()]
    await rollback.restore(db,records,True)
    assert await db.orders.find_one({'id':'po'}) == before


@pytest.mark.anyio
async def test_scope_read_and_direct_url_deny_other_branch():
    AsyncMongoMockClient = pytest.importorskip("mongomock_motor").AsyncMongoMockClient
    m=service();db=AsyncMongoMockClient().test
    await db.branches.insert_many([{'id':'ygy','code':'YGY'},{'id':'smg','code':'SMG'}])
    await db.orders.insert_many([
        {'id':'a','branch_id':'50','operational_branch_id':'ygy'},
        {'id':'b','branch_id':'50','operational_branch_id':'smg'},
        {'id':'legacy-internal','branch_id':'ygy'},
        {'id':'unmapped-external','branch_id':'50'}])
    user={'role':'crm','branch_ids':['ygy'],'primary_branch_id':'ygy'}
    rows=await db.orders.find(await m.operational_query(db,user)).to_list(None)
    assert {r['id'] for r in rows}=={'a','legacy-internal'}
    req=SimpleNamespace(path_params={'order_id':'b'})
    with pytest.raises(HTTPException): await m.guard_document_request(db,req,user)
    await m.guard_document_request(db,SimpleNamespace(path_params={'order_id':'a'}),user)


def test_bulk_sales_delete_replica_set_scope_atomicity_and_side_effects(monkeypatch):
    """Replica-set bulk delete: YGY succeeds, SMG stays hidden, mixed batches do not partial-delete."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs")
    monkeypatch.setenv("DB_NAME", "eternal_ygy_bulk_rs")
    monkeypatch.setenv("SEED_DEMO_ORDERS", "0")
    asyncio.run(_bulk_replica_set_case())


def _detail_text(exc):
    return str(exc.value.detail)


async def _bulk_replica_set_case():
    from motor.motor_asyncio import AsyncIOMotorClient
    import server

    client = AsyncIOMotorClient(
        "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs",
        serverSelectionTimeoutMS=8000,
    )
    name = "eternal_ygy_bulk_rs"
    db = client[name]
    server.client = client
    server.db = db
    await client.drop_database(name)
    try:
        hello = await client.admin.command("hello")
        assert hello.get("setName") == "eternal_local_rs"
        assert hello.get("isWritablePrimary") is True
        await _seed_bulk_branch_documents(db)
        admin = {
            "role": "admin", "branch_ids": ["ygy"], "primary_branch_id": "ygy",
            "email": "admin@example.test",
        }
        holding = {
            "role": "holding_team", "email": "holding@example.test",
            "permissions_override": {
                "sales.invoice.delete": True,
                "sales.receipt.delete": True,
                "sales.delivery.delete": True,
            },
        }
        director = {"role": "direktur_utama", "email": "director@example.test"}

        with pytest.raises(HTTPException) as invoice_mixed:
            await server.bulk_delete_sales_invoices_endpoint(
                server.BulkPayload(ids=["inv-ygy", "inv-pay", "inv-smg"], reason="uji"),
                user=admin,
            )
        invoice_body = _detail_text(invoice_mixed)
        assert "INV-SMG-DRAFT" not in invoice_body
        assert "INV-PAY" in invoice_body
        assert await db.sales_invoices.count_documents({}) == 4
        assert (await db.sales_invoices.find_one({"id": "inv-smg"}))["grand_total"] == 9876
        assert (await db.sales_invoices.find_one({"id": "inv-ygy"}))["branch_id"] == "50"

        removed_invoices = await server.bulk_delete_sales_invoices_endpoint(
            server.BulkPayload(ids=["inv-ygy", "inv-ygy-b"], reason="uji"),
            user=admin,
        )
        assert removed_invoices["deleted_count"] == 2
        assert await db.sales_invoices.find_one({"id": "inv-ygy"}) is None
        assert await db.sales_invoices.find_one({"id": "inv-ygy-b"}) is None
        assert await db.sales_invoices.find_one({"id": "inv-pay"})
        assert await db.sales_invoices.find_one({"id": "inv-smg"})

        with pytest.raises(HTTPException) as receipt_mixed:
            await server.bulk_delete_sales_receipts_endpoint(
                server.BulkPayload(ids=["rec-ygy", "rec-smg"], reason="uji"),
                user=admin,
            )
        assert "RCV-SMG" not in _detail_text(receipt_mixed)
        assert (await db.sales_receipts.find_one({"id": "rec-smg"}))["amount"] == 9876
        assert (await db.sales_receipts.find_one({"id": "rec-ygy"}))["amount"] == 40
        assert (await db.files.find_one({"id": "proof-ygy"}))["is_deleted"] is not True
        assert (await db.sales_invoices.find_one({"id": "inv-pay"}))["paid_total"] == 40

        removed_receipts = await server.bulk_delete_sales_receipts_endpoint(
            server.BulkPayload(ids=["rec-ygy", "rec-ygy-b"], reason="uji"),
            user=admin,
        )
        assert removed_receipts["deleted_count"] == 2
        assert await db.sales_receipts.find_one({"id": "rec-ygy"}) is None
        assert await db.sales_receipts.find_one({"id": "rec-ygy-b"}) is None
        assert await db.sales_receipts.find_one({"id": "rec-smg"})
        proof = await db.files.find_one({"id": "proof-ygy"})
        assert proof["is_deleted"] is True
        assert proof["deleted_reason"] == "sales_receipt_bulk_deleted"
        assert proof["deleted_by"] == "admin@example.test"
        paid = await db.sales_invoices.find_one({"id": "inv-pay"})
        assert paid["paid_total"] == 0
        assert paid["outstanding_amount"] == 100
        assert paid["payment_status"] == "BELUM DIBAYAR"
        assert paid["grand_total"] == 100
        assert paid["branch_id"] == "50"
        order = await db.orders.find_one({"id": "po-ygy"})
        assert order["payment_receipts"] == []
        assert order["payment_status"] == "Waiting"
        assert order["payment_fact"] is None
        assert order["grand_total"] == 100
        assert order["branch_id"] == "50"
        cleared = await server.bulk_delete_sales_invoices_endpoint(
            server.BulkPayload(ids=["inv-pay"], reason="uji"),
            user=admin,
        )
        assert cleared["deleted_count"] == 1
        assert await db.sales_invoices.find_one({"id": "inv-pay"}) is None

        with pytest.raises(HTTPException) as delivery_mixed:
            await server.bulk_delete_sales_deliveries_endpoint(
                server.BulkPayload(ids=["del-ygy", "del-smg"], reason="uji"),
                user=admin,
            )
        delivery_body = _detail_text(delivery_mixed)
        assert "SJ-SMG" not in delivery_body
        assert "BULK_DELETE_BLOCKED" in delivery_body
        assert await db.sales_deliveries.count_documents({}) == 3
        assert (await db.orders.find_one({"id": "po-ygy"}))["delivery_note_number"] == "SJ-KEEP"
        assert any(
            sh.get("id") == "ship-ygy"
            for sh in (await db.orders.find_one({"id": "po-ygy"}))["shipments"]
        )

        removed_deliveries = await server.bulk_delete_sales_deliveries_endpoint(
            server.BulkPayload(ids=["del-ygy", "del-ygy-b"], reason="uji"),
            user=admin,
        )
        assert removed_deliveries["deleted_count"] == 2
        assert await db.sales_deliveries.find_one({"id": "del-ygy"}) is None
        assert await db.sales_deliveries.find_one({"id": "del-ygy-b"}) is None
        assert await db.sales_deliveries.find_one({"id": "del-smg"})
        kept = await db.orders.find_one({"id": "po-ygy"})
        assert [sh.get("id") for sh in kept["shipments"]] == ["ship-keep"]
        assert kept["delivery_note_number"] == "SJ-KEEP"
        assert kept["branch_id"] == "50"
        assert kept["grand_total"] == 100
        sibling = await db.orders.find_one({"id": "po-ygy-b"})
        assert sibling["shipments"] == []
        assert sibling["delivery_note_number"] is None
        assert sibling["grand_total"] == 20
        assert sibling["branch_id"] == "50"
        generated = await db.files.find_one({"id": "del-ygy", "kind": "surat_jalan"})
        assert generated["is_deleted"] is True
        assert generated["deleted_reason"] == "sales.delivery.bulk_delete"
        audit = await db.audit_logs.find_one({"action": "sales.delivery.bulk_delete"})
        assert audit["after"]["deleted_count"] == 2
        assert await db.physical_stock_ledger.count_documents({}) == 0

        holding_invoice = await server.bulk_delete_sales_invoices_endpoint(
            server.BulkPayload(ids=["inv-smg"], reason="uji"),
            user=holding,
        )
        assert holding_invoice["deleted_count"] == 1
        assert await db.sales_invoices.find_one({"id": "inv-smg"}) is None
        director_receipt = await server.bulk_delete_sales_receipts_endpoint(
            server.BulkPayload(ids=["rec-smg"], reason="uji"),
            user=director,
        )
        assert director_receipt["deleted_count"] == 1
        assert (await db.sales_receipts.find_one({"id": "rec-smg"})) is None
        director_delivery = await server.bulk_delete_sales_deliveries_endpoint(
            server.BulkPayload(ids=["del-smg"], reason="uji"),
            user=director,
        )
        assert director_delivery["deleted_count"] == 1
        assert await db.sales_deliveries.find_one({"id": "del-smg"}) is None
        smg_order = await db.orders.find_one({"id": "po-smg"})
        assert smg_order["shipments"] == []
        assert smg_order["grand_total"] == 9876
        assert smg_order["branch_id"] == "50"
        assert smg_order["operational_branch_id"] == "smg"

        stock = await db.physical_stock_balances.find_one({"id": "stk"})
        journal = await db.general_journals.find_one({"id": "jnl"})
        assert stock["qty"] == 40 and stock["amount"] == 4000 and stock["accurate_id"] == "ACC-STK"
        assert journal["amount"] == 4000 and journal["accurate_id"] == "ACC-JNL"
    finally:
        await client.drop_database(name)
        client.close()


async def _seed_bulk_branch_documents(db):
    await db.branches.insert_many([
        {"id": "ygy", "code": "YGY", "active": True},
        {"id": "smg", "code": "SMG", "active": True},
    ])
    await db.sales_invoices.insert_many([
        {"id": "inv-ygy", "invoice_number": "INV-YGY", "financial_status": "draft",
         "operational_branch_id": "ygy", "branch_id": "50", "grand_total": 10},
        {"id": "inv-ygy-b", "invoice_number": "INV-YGY-B", "financial_status": "draft",
         "operational_branch_id": "ygy", "branch_id": "50", "grand_total": 20},
        {"id": "inv-pay", "invoice_number": "INV-PAY", "financial_status": "draft",
         "operational_branch_id": "ygy", "branch_id": "50", "grand_total": 100,
         "paid_total": 40, "outstanding_amount": 60, "payment_status": "DP"},
        {"id": "inv-smg", "invoice_number": "INV-SMG-DRAFT", "financial_status": "draft",
         "operational_branch_id": "smg", "branch_id": "50", "grand_total": 9876},
    ])
    await db.sales_receipts.insert_many([
        {"id": "rec-ygy", "receipt_number": "RCV-YGY", "operational_branch_id": "ygy",
         "branch_id": "50", "amount": 40, "invoice_id": "inv-pay", "po_id": "po-ygy"},
        {"id": "rec-ygy-b", "receipt_number": "RCV-YGY-B", "operational_branch_id": "ygy",
         "branch_id": "50", "amount": 5},
        {"id": "rec-smg", "receipt_number": "RCV-SMG", "operational_branch_id": "smg",
         "branch_id": "50", "amount": 9876},
    ])
    await db.orders.insert_many([
        {
            "id": "po-ygy", "po_number": "PO-YGY-001", "branch_id": "50",
            "operational_branch_id": "ygy", "grand_total": 100, "current_stage": 1,
            "payment_status": "Approved", "payment_approved_by": "system",
            "payment_receipts": [{"receipt_id": "rec-ygy", "amount": 40, "receipt_number": "RCV-YGY"}],
            "delivery_note_number": "SJ-KEEP",
            "shipments": [
                {"id": "ship-ygy", "sales_delivery_id": "del-ygy", "qty": 3,
                 "branch_id": "50", "operational_branch_id": "ygy"},
                {"id": "ship-keep", "qty": 1},
            ],
            "surat_jalan_files": [
                {"id": "del-ygy", "source": "SYSTEM_GENERATED", "sj_number": "SJ-YGY"},
            ],
        },
        {
            "id": "po-ygy-b", "branch_id": "50", "operational_branch_id": "ygy",
            "grand_total": 20, "delivery_note_number": "SJ-YGY-B",
            "shipments": [
                {"id": "ship-ygy-b", "sales_delivery_id": "del-ygy-b", "qty": 2, "branch_id": "50"},
            ],
        },
        {
            "id": "po-smg", "branch_id": "50", "operational_branch_id": "smg",
            "grand_total": 9876, "delivery_note_number": "SJ-SMG",
            "shipments": [
                {"id": "ship-smg", "sales_delivery_id": "del-smg", "qty": 9, "branch_id": "50"},
            ],
        },
    ])
    await db.sales_deliveries.insert_many([
        {"id": "del-ygy", "delivery_id": "del-ygy", "po_id": "po-ygy", "sj_number": "SJ-YGY",
         "operational_branch_id": "ygy", "branch_id": "50", "is_cancelled": False,
         "status": "DRAFT", "line_items": [{"line_idx": 0, "qty": 1, "name": "Cup"}], "total_qty": 1},
        {"id": "del-ygy-b", "delivery_id": "del-ygy-b", "po_id": "po-ygy-b", "sj_number": "SJ-YGY-B",
         "operational_branch_id": "ygy", "branch_id": "50", "is_cancelled": False,
         "status": "DRAFT", "line_items": [{"line_idx": 0, "qty": 1, "name": "Cup"}], "total_qty": 1},
        {"id": "del-smg", "delivery_id": "del-smg", "po_id": "po-smg", "sj_number": "SJ-SMG",
         "operational_branch_id": "smg", "branch_id": "50", "is_cancelled": False,
         "status": "DRAFT", "line_items": [{"line_idx": 0, "qty": 1, "name": "Cup"}], "total_qty": 1},
    ])
    await db.files.insert_many([
        {"id": "proof-ygy", "receipt_id": "rec-ygy", "kind": "sales_receipt_payment_proof", "is_deleted": False},
        {"id": "del-ygy", "kind": "surat_jalan", "order_id": "po-ygy", "source": "SYSTEM_GENERATED", "is_deleted": False},
    ])
    await db.physical_stock_balances.insert_one(
        {"id": "stk", "qty": 40, "amount": 4000, "accurate_id": "ACC-STK"},
    )
    await db.general_journals.insert_one({"id": "jnl", "amount": 4000, "accurate_id": "ACC-JNL"})


@pytest.fixture
def anyio_backend():
    return "asyncio"
