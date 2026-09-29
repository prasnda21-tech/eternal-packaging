from copy import deepcopy
import pytest
from services.sales_delivery_delete_guard import deletion_blocker
from test_confirm_delivered_sj_stock import mongo, anyio_backend, _order
D={'id':'sj-b','sj_number':'SJ-B','po_id':'scope'}
def doc():
 return {'id':'scope','shipments':[{'id':'sj-a','number':'SJ-A'},{'id':'sj-b','number':'SJ-B'}],'delivery_status':'Terkirim'}
@pytest.mark.parametrize('field',['loading_confirmations','delivery_confirmations','delivery_proofs','delivery_recipient_signatures','delivery_driver_signatures'])
def test_scoped_evidence(field):
 o=doc();o[field]=[{'id':'evidence','shipment_id':'sj-a'}]
 assert deletion_blocker(o,D) is None
 o[field][0]['shipment_id']='sj-b'
 assert deletion_blocker(o,D)
@pytest.mark.parametrize('key',['shipment_id','sales_delivery_id','surat_jalan_id','sj_file_id'])
def test_loading_alias(key):
 o=doc();o['loading_confirmations']=[{key:'sj-b'}]
 assert 'Loading' in deletion_blocker(o,D)
def test_legacy():
 o=doc();o['delivery_proofs']=[{'id':'old'}]
 assert 'belum terhubung' in deletion_blocker(o,D)
 o['delivery_proofs'][0]['is_deleted']=True
 assert deletion_blocker(o,D) is None
@pytest.mark.parametrize('field',['delivery_recipient_signature','delivery_driver_signature'])
def test_active_signature(field):
 o=doc();o[field]={'id':'sig','shipment_id':'sj-a'}
 assert deletion_blocker(o,D) is None
 o[field]['shipment_id']='sj-b'
 assert deletion_blocker(o,D)
@pytest.mark.anyio
@pytest.mark.parametrize('bulk',[False,True,'file'])
async def test_delete_with_payment_keeps_sibling(mongo,bulk):
 import server,httpx
 o=_order("scope");o.update(doc());o.pop("delivery_recipient_signature",None);o.pop("delivery_driver_signature",None)
 for f in ('delivery_proofs','loading_confirmations','delivery_confirmations'):
  o[f]=[{'id':f,'shipment_id':'sj-a','qty':100}]
 o['surat_jalan_files']=[{'id':'sj-b','shipment_id':'sj-b','source':server._SJ_SOURCE_GENERATED,'name':'SJ-B.pdf'}]
 await mongo.orders.insert_one(deepcopy(o))
 await mongo.sales_deliveries.insert_many([dict(D),{'id':'sj-a','po_id':'scope','sj_number':'SJ-A'}])
 payment={'id':'paid','po_id':'scope','amount':500000}
 await mongo.sales_receipts.insert_one(dict(payment))
 server.app.dependency_overrides[server.current_user]=lambda:{'id':'admin','email':'test@test.local','role':'admin'}
 try:
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test') as c:
   if bulk=='file': r=await c.delete('/api/orders/scope/surat-jalan/files/sj-b')
   elif bulk: r=await c.post('/api/sales-deliveries-bulk/delete',json={'ids':['sj-b'],'reason':'Unused SJ'})
   else: r=await c.delete('/api/sales-deliveries/sj-b')
   assert r.status_code==200,r.text
   r=await c.delete('/api/sales-deliveries/sj-a');assert r.status_code==400,r.text
  assert await mongo.sales_deliveries.find_one({'id':'sj-b'}) is None
  assert await mongo.sales_deliveries.find_one({'id':'sj-a'})
  saved=await mongo.orders.find_one({'id':'scope'})
  for f in ('delivery_proofs','loading_confirmations','delivery_confirmations'):assert saved[f]==o[f]
  assert await mongo.sales_receipts.find_one({'id':'paid'},{'_id':0})==payment
 finally:server.app.dependency_overrides.clear()
