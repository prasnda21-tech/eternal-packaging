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


@pytest.fixture
def anyio_backend():
    return "asyncio"
