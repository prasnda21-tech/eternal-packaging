import asyncio
from copy import deepcopy
import pytest
from test_confirm_delivered_sj_stock import mongo,anyio_backend,_order,_seed,MASTER
from services.operational_stock_outbox import FIELD,process_intent,archive_before_delete,attach_sales_delivery_intent
from services.product_stock_state import get_product_stock_state

async def setup(mongo):
 o=_order('return-sj');await _seed(mongo,o)
 o['physical_stock_snapshot']['stock_out_trigger']='sales_delivery'
 sj={'id':'return-sj-doc','po_id':o['id'],'line_items':[{'line_idx':0,'qty':100}]}
 attach_sales_delivery_intent(o,sj,{})
 await mongo.sales_deliveries.insert_one(deepcopy(sj))
 return sj,next(iter(sj[FIELD]))

@pytest.mark.anyio
async def test_applied_sj_returns_stock_once(mongo):
 sj,token=await setup(mongo)
 assert (await process_intent(mongo,sj['id'],token,'sales_deliveries'))['status']=='applied'
 assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==1900
 await archive_before_delete(mongo,sj,'sales_deliveries')
 await archive_before_delete(mongo,sj,'sales_deliveries')
 assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==2000
 rows=await mongo.stock_movements.find({'reverses_event_id':f'operational:{token}:0'}).to_list(10)
 assert len(rows)==1
 assert rows[0]['delta_printed']==100

@pytest.mark.anyio
async def test_never_applied_sj_does_not_add_stock(mongo):
 sj,token=await setup(mongo)
 await archive_before_delete(mongo,sj,'sales_deliveries')
 assert (await process_intent(mongo,sj['id'],token,'sales_deliveries'))['status']=='cancelled'
 assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==2000

@pytest.mark.anyio
async def test_delete_wins_before_forward_apply(mongo,monkeypatch):
 from services import physical_stock_engine as engine
 sj,token=await setup(mongo);arrived=asyncio.Event();resume=asyncio.Event()
 real=engine.finalize_durable_stock_event
 async def pause(*a,**k):
  if k['event_id'].startswith('operational:'):
   arrived.set();await resume.wait()
  return await real(*a,**k)
 monkeypatch.setattr(engine,'finalize_durable_stock_event',pause)
 task=asyncio.create_task(process_intent(mongo,sj['id'],token,'sales_deliveries'))
 try:
  await asyncio.wait_for(arrived.wait(),5)
  await archive_before_delete(mongo,sj,'sales_deliveries')
  await mongo.sales_deliveries.delete_one({'id':sj['id']})
  resume.set()
  with pytest.raises(RuntimeError):await asyncio.wait_for(task,5)
  assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==2000
 finally:
  resume.set()
  if not task.done():task.cancel()

@pytest.mark.anyio
async def test_crash_after_debit_before_movement_then_delete_returns_once(mongo,monkeypatch):
 from services import physical_stock_engine as engine
 sj,token=await setup(mongo)
 real=engine.record_stock_movement
 async def crash(*a,**k):raise RuntimeError('movement write interrupted')
 monkeypatch.setattr(engine,'record_stock_movement',crash)
 with pytest.raises(RuntimeError):await process_intent(mongo,sj['id'],token,'sales_deliveries')
 assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==1900
 monkeypatch.setattr(engine,'record_stock_movement',real)
 await archive_before_delete(mongo,sj,'sales_deliveries')
 await archive_before_delete(mongo,sj,'sales_deliveries')
 assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==2000

@pytest.mark.anyio
async def test_legacy_exact_sj_debit_returns_once_without_touching_other_sj(mongo):
 from services import physical_stock_engine as engine
 o=_order('legacy-return');await _seed(mongo,o)
 o['physical_stock_snapshot']['stock_out_trigger']='sales_delivery'
 for did in ('legacy-a','legacy-b'):
  sj={'id':did,'po_id':o['id'],'line_items':[{'line_idx':0,'line_id':'L0','master_item_id':MASTER,'qty':100,'unit':'PCS','production_process':'print'}]}
  await mongo.sales_deliveries.insert_one(deepcopy(sj))
  claims=await engine.claim_sales_delivery_stock(mongo,o,sj['line_items'],sj['id'],{})
  await engine.finalize_sales_delivery_stock(mongo,claims,{})
 assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==1800
 sj=await mongo.sales_deliveries.find_one({'id':'legacy-b'})
 await archive_before_delete(mongo,sj,'sales_deliveries')
 await archive_before_delete(mongo,sj,'sales_deliveries')
 assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==1900
 assert await mongo.stock_movements.count_documents({'reverses_event_id':'stock_out:sales_delivery:legacy-b:L0'})==1
 assert await mongo.stock_movements.count_documents({'reverses_event_id':'stock_out:sales_delivery:legacy-a:L0'})==0

@pytest.mark.anyio
async def test_mapping_corrected_on_same_po_line_resumes_once(mongo):
 from test_confirm_delivered_sj_stock import _confirm
 o=_order('mapping-resume');await _seed(mongo,o)
 await mongo.orders.update_one({'id':o['id']},{'$unset':{'line_items.0.master_item_id':''}})
 assert (await _confirm(o['id'],'sj-1','mapping-resume')).status_code==200
 saved=await mongo.orders.find_one({'id':o['id']});token=next(iter(saved[FIELD]))
 assert (await process_intent(mongo,o['id'],token))['status']=='pending'
 await mongo.orders.update_one({'id':o['id']},{'$set':{'line_items.0.master_item_id':MASTER}})
 assert (await process_intent(mongo,o['id'],token))['status']=='applied'
 assert (await process_intent(mongo,o['id'],token))['status']=='applied'
 assert (await get_product_stock_state(mongo,MASTER))['printed_qty']==1200
 saved=await mongo.orders.find_one({'id':o['id']})
 assert saved[FIELD][token]['items'][0]['mapping_resolution']['to']==MASTER
