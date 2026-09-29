import asyncio
from copy import deepcopy
import pytest
from test_confirm_delivered_sj_stock import mongo, anyio_backend, _order, _seed, _confirm, MASTER, _movements
from services.operational_stock_outbox import FIELD, process_intent, collect_intents
from services.product_stock_state import get_product_stock_state

@pytest.mark.anyio
async def test_delivery_shortage_persists_operation_and_intent(mongo):
    order = _order('short-outbox')
    await _seed(mongo, order, printed=100)
    response = await _confirm(order['id'], 'sj-1', 'short-action')
    assert response.status_code == 200, response.text
    stored = await mongo.orders.find_one({'id': order['id']})
    assert stored['qty_delivered'] == 800
    assert len(stored['delivery_confirmations']) == len(stored[FIELD]) == 1
    task = next(iter(stored[FIELD].values()))
    assert [i['quantity'] for i in task['items']] == [400,400]
    result = await process_intent(mongo, order['id'], task['id'])
    assert result['status'] == 'pending'
    assert (await get_product_stock_state(mongo, MASTER))['printed_qty'] == 100
    assert (await _confirm(order['id'], 'sj-1', 'short-action')).status_code == 200
    again = await mongo.orders.find_one({'id': order['id']})
    assert len(again[FIELD]) == len(again['delivery_confirmations']) == 1

@pytest.mark.anyio
async def test_delivery_applies_once(mongo):
    order = _order('enough-outbox')
    await _seed(mongo, order)
    assert (await _confirm(order['id'], 'sj-1', 'enough-action')).status_code == 200
    stored = await mongo.orders.find_one({'id': order['id']})
    token = next(iter(stored[FIELD]))
    for _ in range(2):
        result = await process_intent(mongo, order['id'], token)
        assert result['status'] == 'applied', result
    assert (await get_product_stock_state(mongo, MASTER))['printed_qty'] == 1200
    assert len(await _movements(mongo)) == 2

@pytest.mark.anyio
async def test_proofs_still_required(mongo):
    order = _order('proofs-outbox'); order['delivery_proofs'] = []
    await _seed(mongo, order, printed=0)
    assert (await _confirm(order['id'], 'sj-1', 'bad-proofs')).status_code == 400
    stored = await mongo.orders.find_one({'id': order['id']})
    assert not stored.get(FIELD) and not stored['delivery_confirmations']

@pytest.mark.anyio
async def test_concurrent_delivery_one_obligation(mongo):
    order = _order('race-outbox'); await _seed(mongo, order, printed=0)
    results = await asyncio.gather(*[_confirm(order['id'], 'sj-1', 'race-action') for _ in range(2)])
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    stored = await mongo.orders.find_one({'id': order['id']})
    assert len(stored[FIELD]) == len(stored['delivery_confirmations']) == 1

@pytest.mark.anyio
async def test_failed_persist_no_stock_work(mongo, monkeypatch):
    import server
    from fastapi import HTTPException
    order = _order('fail-write-outbox'); await _seed(mongo, order)
    async def fail(*a,**kw): raise HTTPException(503, 'write failed')
    monkeypatch.setattr(server, '_persist_order', fail)
    assert (await _confirm(order['id'], 'sj-1', 'fail-action')).status_code == 503
    stored = await mongo.orders.find_one({'id': order['id']})
    assert not stored.get(FIELD) and not stored['delivery_confirmations']
    assert not await _movements(mongo)

def test_only_new_evidence_snapshot_off_not_backfilled():
    before = _order('unit-outbox')
    before['operator_splits'] = [{'id':'op','line_idx':0,'progress_entries':[{'id':'old','qty':500}]}]
    after = deepcopy(before); after['operator_splits'][0]['progress_entries'].append({'id':'new','qty':100})
    tasks = collect_intents(before, after, 'operator', {})
    assert len(tasks) == 1
    assert next(iter(tasks.values()))['items'][0]['quantity'] == 100
    after['physical_stock_snapshot']['enabled'] = False
    assert collect_intents(before, after, 'operator', {}) == {}

@pytest.mark.anyio
async def test_uninitialized_is_durable_pending_and_later_opening_is_not_backfilled(mongo):
    from services.product_stock_state import initialize_opening_balance
    order = _order('no-opening-outbox')
    await mongo.master_items.insert_one({'id': MASTER, 'unit':'PCS'})
    await mongo.orders.insert_one(order)
    assert (await _confirm(order['id'], 'sj-1', 'no-opening')).status_code == 200
    stored = await mongo.orders.find_one({'id':order['id']}); token = next(iter(stored[FIELD]))
    assert (await process_intent(mongo,order['id'],token))['status'] == 'pending'
    assert await get_product_stock_state(mongo, MASTER) is None
    await initialize_opening_balance(mongo, MASTER, {'unprinted_qty':0,'printed_qty':2000,'base_uom':'PCS'},user={})
    result = await process_intent(mongo,order['id'],token)
    assert result['status']=='pending' and 'sesudah' in result['reason']
    assert (await get_product_stock_state(mongo, MASTER))['printed_qty']==2000

@pytest.mark.anyio
async def test_foreign_claim_is_not_released_or_operational_blocker(mongo):
    from services import physical_stock_engine as engine
    order=_order('foreign-outbox'); await _seed(mongo,order)
    await engine.claim_durable_stock_event(mongo,event_id='foreign',master_item_id=MASTER,
        movement_type=engine.MOVEMENT_PENGIRIMAN,transaction_qty=100,transaction_uom='PCS',is_print=True)
    assert (await _confirm(order['id'],'sj-1','foreign-action')).status_code==200
    stored=await mongo.orders.find_one({'id':order['id']}); token=next(iter(stored[FIELD]))
    assert (await process_intent(mongo,order['id'],token))['status']=='pending'
    state=await get_product_stock_state(mongo,MASTER)
    assert state['pending_event']['event_id']=='foreign' and state['printed_qty']==2000

@pytest.mark.anyio
async def test_crash_after_apply_before_movement_resumes_without_second_debit(mongo,monkeypatch):
    from services import physical_stock_engine as engine
    order=_order('crash-outbox'); await _seed(mongo,order)
    assert (await _confirm(order['id'],'sj-1','crash-action')).status_code==200
    stored=await mongo.orders.find_one({'id':order['id']}); token=next(iter(stored[FIELD]))
    real=engine.record_stock_movement
    async def fail(*args,**kwargs): raise RuntimeError('movement stopped')
    monkeypatch.setattr(engine,'record_stock_movement',fail)
    with pytest.raises(RuntimeError,match='movement stopped'):
        await process_intent(mongo,order['id'],token)
    state=await get_product_stock_state(mongo,MASTER)
    assert state['printed_qty']==1600 and state['pending_event']['stage']=='STATE_APPLIED'
    assert await engine.is_source_committed(mongo,state['pending_event'])
    monkeypatch.setattr(engine,'record_stock_movement',real)
    assert (await process_intent(mongo,order['id'],token))['status']=='applied'
    assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==1200
    assert len(await _movements(mongo))==2

@pytest.mark.anyio
async def test_parallel_workers_debit_once(mongo):
    order=_order('worker-race'); await _seed(mongo,order)
    assert (await _confirm(order['id'],'sj-1','worker-race')).status_code==200
    stored=await mongo.orders.find_one({'id':order['id']}); token=next(iter(stored[FIELD]))
    await asyncio.gather(*[process_intent(mongo,order['id'],token) for _ in range(2)])
    assert (await process_intent(mongo,order['id'],token))['status']=='applied'
    assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==1200
    assert len(await _movements(mongo))==2

def test_seeded_legacy_not_replayed_and_packing_rows_not_collapsed():
    before=_order('legacy-unit'); after=deepcopy(before)
    after['operator_splits']=[{'id':'split','line_idx':0,'progress_entries':[
        {'id':'legacy','legacy':True,'qty':500}, {'id':'new','qty':100}]}]
    tasks=collect_intents(before,after,'operator',{})
    assert len(tasks)==1 and next(iter(tasks.values()))['items'][0]['quantity']==100
    after['physical_stock_snapshot']['stock_out_trigger']='packing_completed'
    after['packing_info']={'events':[{'id':'p0','action_id':'same','line_idx':0,'qty':100},
                                    {'id':'p1','action_id':'same','line_idx':1,'qty':200}]}
    assert len(collect_intents(before,after,'packing',{}))==2

async def _post(order_id,path,payload):
    import httpx, server
    from test_confirm_delivered_sj_stock import _as_admin
    _as_admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
        return await client.post(f'/api/orders/{order_id}/{path}',json=payload)


def _production_order(oid):
    order=_order(oid)
    order.update(design_status='Approved',payment_status='Approved',sample_approved=True,
        helper_prep={'qty_prepared':2000,'items':[{'line_idx':0,'qty_prepared':1000},{'line_idx':1,'qty_prepared':1000}]},
        operator_splits=[{'id':'op0','line_idx':0,'quantity':1000,'qty_completed':0,'operator_name':'Admin Uji','operator_email':'admin@test.local'},
                         {'id':'op1','line_idx':1,'quantity':1000,'qty_completed':0,'operator_name':'Admin Uji','operator_email':'admin@test.local'}],
        packing_info={'qty_packed':0,'lines':[]},loading_confirmations=[])
    order['surat_jalan_files']=[{'id':'sj-1','name':'SJ Uji','shipment_id':'sj-1','lines':order['shipments'][0]['lines']}]
    return order

@pytest.mark.anyio
async def test_operator_shortage_captured_sample_and_qty_rules_still_hold(mongo):
    order=_production_order('operator-queue'); await _seed(mongo,order,printed=0,unprinted=0)
    await mongo.app_settings.insert_one({'key':'physical_stock','enabled':True})
    r=await _post(order['id'],'operator-progress',{'operator_id':'op0','qty':100,'action_id':'print1'})
    assert r.status_code==200,r.text
    stored=await mongo.orders.find_one({'id':order['id']}); token=next(iter(stored[FIELD]))
    assert stored['operator_splits'][0]['qty_completed']==100
    assert (await process_intent(mongo,order['id'],token))['status']=='pending'
    r=await _post(order['id'],'operator-progress',{'operator_id':'op0','qty':1001,'action_id':'over'})
    assert r.status_code==400,r.text
    await mongo.orders.update_one({'id':order['id']},{'$set':{'sample_approved':False}})
    r=await _post(order['id'],'operator-progress',{'operator_id':'op0','qty':10,'action_id':'unapproved'})
    assert r.status_code==400,r.text
    assert len((await mongo.orders.find_one({'id':order['id']}))[FIELD])==1

@pytest.mark.anyio
async def test_packing_shortage_captured_and_over_completed_qty_rejected(mongo):
    order=_production_order('packing-queue');order['physical_stock_snapshot']['stock_out_trigger']='packing_completed'
    order['operator_splits'][0]['qty_completed']=100
    await _seed(mongo,order,printed=0,unprinted=0)
    r=await _post(order['id'],'packing-info',{'lines':[{'line_idx':0,'qty':50,'action_id':'pack1'}]})
    assert r.status_code==200,r.text
    stored=await mongo.orders.find_one({'id':order['id']});token=next(iter(stored[FIELD]))
    assert stored['packing_info']['qty_packed']==50
    assert (await process_intent(mongo,order['id'],token))['status']=='pending'
    r=await _post(order['id'],'packing-info',{'lines':[{'line_idx':0,'qty':100,'action_id':'over'}]})
    assert r.status_code==400,r.text

@pytest.mark.anyio
@pytest.mark.parametrize('path', ['loading-confirm','loading-confirm-match'])
async def test_loading_shortage_does_not_block_and_sj_rules_hold(mongo,path):
    order=_production_order('loading-queue');order['physical_stock_snapshot']['stock_out_trigger']='loading_confirmation'
    order['operator_splits'][0]['qty_completed']=1000;order['operator_splits'][1]['qty_completed']=1000
    order['packing_info']={'qty_packed':2000,'lines':[{'line_idx':0,'packed_qty':1000},{'line_idx':1,'packed_qty':1000}]}
    await _seed(mongo,order,printed=0,unprinted=0)
    payload={'surat_jalan_id':'sj-1','action_id':'load1'}
    if path=='loading-confirm':payload['lines']=[{'line_idx':0,'qty':400},{'line_idx':1,'qty':400}]
    r=await _post(order['id'],path,payload)
    assert r.status_code==200,r.text
    stored=await mongo.orders.find_one({'id':order['id']});token=next(iter(stored[FIELD]))
    assert len(stored['loading_confirmations'])==1
    assert (await process_intent(mongo,order['id'],token))['status']=='pending'
    payload['surat_jalan_id']='not-real';payload['action_id']='bad-sj'
    r=await _post(order['id'],path,payload)
    assert r.status_code==400,r.text

@pytest.mark.anyio
async def test_applied_pending_id_cleanup_does_not_erase_new_task(mongo):
    order=_order('pending-list'); await _seed(mongo,order)
    assert (await _confirm(order['id'],'sj-1','first')).status_code==200
    stored=await mongo.orders.find_one({'id':order['id']});token=next(iter(stored[FIELD]))
    assert stored['operational_stock_pending']==[token]
    await mongo.orders.update_one({'id':order['id']},{'$addToSet':{'operational_stock_pending':'new-concurrent'}})
    assert (await process_intent(mongo,order['id'],token))['status']=='applied'
    assert (await mongo.orders.find_one({'id':order['id']}))['operational_stock_pending']==['new-concurrent']

@pytest.mark.anyio
async def test_deleted_sj_obligation_remains_visible_without_auto_debit(mongo):
    from services.operational_stock_outbox import attach_sales_delivery_intent, archive_before_delete, pending_rows
    order=_order('deleted-sj');await _seed(mongo,order)
    order['physical_stock_snapshot']['stock_out_trigger']='sales_delivery'
    delivery={'id':'new-sj','po_id':order['id'],'line_items':[{'line_idx':0,'qty':100}]}
    attach_sales_delivery_intent(order,delivery,{})
    await mongo.sales_deliveries.insert_one(delivery)
    token=next(iter(delivery[FIELD]))
    await archive_before_delete(mongo,delivery,'sales_deliveries')
    assert (await process_intent(mongo,delivery['id'],token,'sales_deliveries'))['status']=='cancelled'
    await mongo.sales_deliveries.delete_one({'id':delivery['id']})
    rows=await pending_rows(mongo)
    assert rows == []
    assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==2000
    assert not await _movements(mongo)

@pytest.mark.anyio
async def test_permission_denied_cannot_confirm_or_retry_stock(mongo):
    import httpx, server
    order=_order('no-perm'); await _seed(mongo,order)
    server.app.dependency_overrides[server.current_user]=lambda:{'id':'viewer','email':'view@test.local','role':'viewer','permissions_override':{}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
        r=await client.post(f'/api/orders/{order["id"]}/confirm-delivered',json={'shipment_id':'sj-1','action_id':'forbidden'})
        assert r.status_code==403,r.text
        r=await client.post('/api/operational-stock/anything/anything/retry')
        assert r.status_code==403,r.text
    assert not (await mongo.orders.find_one({'id':order['id']})).get(FIELD)

@pytest.mark.anyio
async def test_sj_without_rows_cannot_confirm_full_po(mongo):
    order=_order('bare-rows');order['shipments']=[{'id':'sj-1','number':'bare','total_qty':2000}]
    await _seed(mongo,order,printed=0)
    r=await _confirm(order['id'],'sj-1','bare')
    assert r.status_code==400,r.text
    assert not (await mongo.orders.find_one({'id':order['id']})).get(FIELD)

@pytest.mark.anyio
async def test_live_disabled_print_does_not_create_future_backfill(mongo):
    order=_production_order('off-print');await _seed(mongo,order,printed=0,unprinted=0)
    await mongo.app_settings.insert_one({'key':'physical_stock','enabled':False})
    r=await _post(order['id'],'operator-progress',{'operator_id':'op0','qty':100,'action_id':'off'})
    assert r.status_code==200,r.text
    assert not (await mongo.orders.find_one({'id':order['id']})).get(FIELD)

@pytest.mark.anyio
async def test_real_sj_creation_with_shortage_records_pending_and_caps_qty(mongo,monkeypatch):
    import server,httpx
    from test_confirm_delivered_sj_stock import _as_admin
    from services import sales_deliveries
    order=_production_order('sj-create-outbox')
    order['po_number']='PO/2026/09/29/099999';order['shipments']=[];order['surat_jalan_files']=[]
    order['physical_stock_snapshot']['stock_out_trigger']='sales_delivery'
    order['operator_splits'][0]['qty_completed']=1000;order['operator_splits'][1]['qty_completed']=1000
    order['packing_info']={'qty_packed':2000,'lines':[{'line_idx':0,'packed_qty':1000},{'line_idx':1,'packed_qty':1000}]}
    await _seed(mongo,order,printed=0,unprinted=0)
    _as_admin()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as client:
        r=await client.post('/api/sales-deliveries',json={'po_id':order['id'],'delivery_date':'2026-09-29','line_items':[{'line_idx':0,'qty':400}]})
        assert r.status_code==200,r.text
        delivery=await mongo.sales_deliveries.find_one({'po_id':order['id']})
        token=next(iter(delivery[FIELD]))
        result=await process_intent(mongo,delivery['id'],token,'sales_deliveries')
        assert result['status']=='pending',result
        r=await client.post('/api/sales-deliveries',json={'po_id':order['id'],'delivery_date':'2026-09-29','line_items':[{'line_idx':0,'qty':1000}]})
        assert r.status_code in (400,409),r.text
    assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==0

@pytest.mark.anyio
async def test_sj_applied_movement_keeps_po_identity(mongo):
    from services.operational_stock_outbox import attach_sales_delivery_intent
    from services import physical_stock_engine as engine
    order=_order('sj-po-identity');await _seed(mongo,order)
    order['physical_stock_snapshot']['stock_out_trigger']='sales_delivery'
    delivery={'id':'sj-identity','po_id':order['id'],'line_items':[{'line_idx':0,'qty':100}]}
    attach_sales_delivery_intent(order,delivery,{})
    await mongo.sales_deliveries.insert_one(delivery)
    token=next(iter(delivery[FIELD]))
    assert (await process_intent(mongo,delivery['id'],token,'sales_deliveries'))['status']=='applied'
    movements=await _movements(mongo)
    assert len(movements)==1 and movements[0]['order_id']==order['id']
    assert movements[0]['reference_id']==delivery['id']

@pytest.mark.anyio
async def test_unknown_unit_never_defaults_to_one(mongo):
    order=_order('unknown-uom');order['line_items'][0]['unit']='BOX'
    await _seed(mongo,order)
    assert (await _confirm(order['id'],'sj-1','unknown-uom')).status_code==200
    stored=await mongo.orders.find_one({'id':order['id']}); token=next(iter(stored[FIELD]))
    result=await process_intent(mongo,order['id'],token)
    assert result['status']=='pending' and 'Konversi' in result['reason']
    assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==2000


def test_absolute_packing_does_not_backfill_old_snapshot():
    before=_order('old-packing');before['physical_stock_snapshot']['stock_out_trigger']='packing_completed'
    before['packing_info']={'lines':[{'line_idx':0,'packed_qty':50}], 'qty_packed':50}
    after=deepcopy(before);after['packing_info']={'lines':[{'line_idx':0,'packed_qty':100}], 'qty_packed':100,
        'events':[{'id':'new-absolute','line_idx':0,'qty':100}]}
    tasks=collect_intents(before,after,'packing',{})
    assert next(iter(tasks.values()))['items'][0]['quantity']==50

@pytest.mark.anyio
async def test_rollout_old_delivery_fence_never_creates_second_stock_debit(mongo):
    order=_order('legacy-rollout')
    order['delivery_confirm_fence']={'protocol':1,'ops':{'old-slot':{
        'action_id':'old-action','status':'active','generation':1,'tokens':[]}}}
    await _seed(mongo,order)
    assert (await _confirm(order['id'],'sj-1','old-action')).status_code==200
    stored=await mongo.orders.find_one({'id':order['id']}); token=next(iter(stored[FIELD]))
    result=await process_intent(mongo,order['id'],token)
    assert result['status']=='review_required',result
    await mongo.orders.update_one({'id':order['id']},{'$unset':{'delivery_confirm_fence':''}})
    assert (await process_intent(mongo,order['id'],token))['status']=='review_required'
    assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==2000
    assert not await _movements(mongo)

@pytest.mark.anyio
async def test_same_action_old_claim_is_not_replayed_or_released(mongo):
    from services import physical_stock_engine as engine
    order=_order('legacy-claim-overlap');await _seed(mongo,order)
    await engine.claim_durable_stock_event(mongo,event_id='legacy-event',master_item_id=MASTER,
        movement_type=engine.MOVEMENT_PENGIRIMAN,transaction_qty=100,transaction_uom='PCS',
        is_print=True,reference_type='delivery_confirmed',order_id=order['id'],action_id='same-old')
    assert (await _confirm(order['id'],'sj-1','same-old')).status_code==200
    stored=await mongo.orders.find_one({'id':order['id']}); token=next(iter(stored[FIELD]))
    result=await process_intent(mongo,order['id'],token)
    assert result['status']=='review_required',result
    state=await get_product_stock_state(mongo,MASTER)
    assert state['pending_event']['event_id']=='legacy-event' and state['printed_qty']==2000

@pytest.mark.anyio
async def test_worker_retries_failed_write_without_losing_stock_obligation(mongo, monkeypatch):
    from services import physical_stock_engine as engine
    order = _order('worker-retry')
    await _seed(mongo,order)
    assert (await _confirm(order['id'],'sj-1','worker-retry')).status_code == 200
    saved = await mongo.orders.find_one({'id':order['id']})
    token = next(iter(saved[FIELD]))
    real = engine.finalize_durable_stock_event
    async def fail(*args, **kwargs):
        raise RuntimeError('storage temporarily unavailable')
    monkeypatch.setattr(engine,'finalize_durable_stock_event',fail)
    with pytest.raises(RuntimeError):
        await process_intent(mongo,order['id'],token)
    saved = await mongo.orders.find_one({'id':order['id']})
    assert len(saved['delivery_confirmations']) == 1
    assert token in saved['operational_stock_pending']
    monkeypatch.setattr(engine,'finalize_durable_stock_event',real)
    assert (await process_intent(mongo,order['id'],token))['status']=='applied'
    assert (await process_intent(mongo,order['id'],token))['status']=='applied'
    assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==1200
    assert len(await _movements(mongo))==2
