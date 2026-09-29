import asyncio
from services.sales_order_invoice_status import invoice_coverage, attach_invoice_coverage

ORDER={'id':'p1','line_items':[{'line_idx':0,'quantity':100},{'line_idx':1,'quantity':50}]}
def inv(lines=None,**extra):
    return dict(po_id='p1', financial_status='finalized', issue_state='issued',
                lines=lines if lines is not None else [{'line_idx':0,'quantity':100},{'line_idx':1,'quantity':50}], **extra)

def test_none():
    assert invoice_coverage(ORDER,[])['status']=='none'
def test_full():
    assert invoice_coverage(ORDER,[inv()])['status']=='full'
def test_partial_compares_each_line_not_sum():
    assert invoice_coverage(ORDER,[inv([{'line_idx':0,'quantity':150}])])['status']=='partial'
def test_multiple_invoices_combine():
    assert invoice_coverage(ORDER,[inv([{'line_idx':0,'quantity':100}]),inv([{'line_idx':1,'quantity':50}])])['status']=='full'
def test_cancelled_removed_draft_and_pending_do_not_count():
    for change in [{'financial_status':'cancelled'},{'removed_from_active':True},{'removal_state':'pending'},{'removal_state':'deleted'},{'financial_status':'draft'},{'issue_state':'issuing'}]:
        row=inv();row.update(change)
        assert invoice_coverage(ORDER,[row])['status']=='none'
def test_processing_is_explicit():
    row=inv();row['issue_state']='issuing'
    assert invoice_coverage(ORDER,[row])=={'status':'none','processing':True}
def test_legacy_finalized_counts_but_missing_status_does_not():
    row=inv();row.pop('issue_state')
    assert invoice_coverage(ORDER,[row])['status']=='full'
    row.pop('financial_status')
    assert invoice_coverage(ORDER,[row])['status']=='none'
def test_missing_or_invalid_lines_are_unknown():
    assert invoice_coverage({'line_items':[]},[])['status']=='unknown'
    assert invoice_coverage(ORDER,[inv([{'quantity':50}])])['status']=='unknown'
    assert invoice_coverage(ORDER,[inv([{'line_idx':0,'quantity':'NaN'}])])['status']=='unknown'
def test_original_index_fallback_and_zero_lines():
    order={'line_items':[{'quantity':10},{'quantity':0}]}
    assert invoice_coverage(order,[inv([{'line_idx':0,'quantity':10}])])['status']=='full'
def test_batch_only_queries_authorized_po_ids():
    class Cursor:
        def __aiter__(self):
            async def rows():
                yield inv()
            return rows()
    class Collection:
        def find(self,query,projection):
            assert query=={'po_id':{'$in':['p1']}}
            assert 'grand_total' not in projection
            return Cursor()
    class DB:
        sales_invoices=Collection()
    orders=[dict(ORDER)]
    asyncio.run(attach_invoice_coverage(DB(),orders))
    assert orders[0]['invoice_summary']['status']=='full'


def test_filter_applies_across_pages_and_keeps_unknown_out_of_unbilled():
    from services.sales_order_invoice_status import filter_invoice_coverage
    rows=[{'id':i,'invoice_summary':{'status':'full' if i < 10 else 'none'}} for i in range(13)]
    rows.append({'id':99,'invoice_summary':{'status':'unknown'}})
    assert [o['id'] for o in filter_invoice_coverage(rows,'none')]==[10,11,12]
    assert len(filter_invoice_coverage(rows,'full'))==10
    assert filter_invoice_coverage(rows,'all')==rows
