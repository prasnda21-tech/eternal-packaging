"""Real financial and production builders; isolated read-only document fixtures."""
from datetime import date
import pytest
from fastapi import HTTPException
from services.financial_dashboard import _cash_movement
from services.dashboard_rincian import query_dashboard_rincian, delivery_remaining
from services.production_dashboard_snapshot import build_production_snapshot
from services.production_wave2 import build_wave2
from test_dashboard_combined_contracts import order
from test_dashboard_rincian import _Cursor

@pytest.fixture
def anyio_backend():
    return "asyncio"

class Collection:
    def __init__(self, rows): self.rows = rows
    def find(self, query=None, projection=None):
        def match(row):
            for key, cond in (query or {}).items():
                v = row.get(key)
                if isinstance(cond, dict):
                    for op, target in cond.items():
                        if op == "$in" and v not in target: return False
                        if op == "$gte" and (v is None or v < target): return False
                        if op == "$lte" and (v is None or v > target): return False
                        if op == "$lt" and (v is None or v >= target): return False
                elif v != cond: return False
            return True
        rows = [dict(r) for r in self.rows if match(r)]
        if projection and any(projection.values()):
            rows = [{k: v for k, v in r.items() if projection.get(k)} for r in rows]
        return _Cursor(rows)

class DB(dict):
    def __getitem__(self, key): return dict.get(self, key, Collection([]))
    def __getattr__(self, key): return self[key]

@pytest.mark.anyio
async def test_cash_card_and_drill_same_journals_accounts_and_pagination():
    headers = [
        {"id":"opening", "date":"2026-09-30", "status":"posted"},
        {"id":"in", "date":"2026-10-01", "status":"posted", "number":"IN-1"},
        {"id":"out", "date":"2026-10-02", "status":"posted", "number":"OUT-1"},
        {"id":"transfer", "date":"2026-09-29", "status":"posted", "source_type":"cash_bank_transfer"},
        {"id":"reversed_transfer", "date":"2026-10-02", "status":"posted", "reversal_of":"transfer"},
        {"id":"draft", "date":"2026-10-01", "status":"draft"},
        {"id":"future", "date":"2026-11-01", "status":"posted"},
    ]
    lines = [
        {"id":"l1","journal_id":"opening","account_id":"a","debit":20,"credit":0},
        {"id":"l2","journal_id":"in","account_id":"a","debit":100,"credit":0},
        {"id":"l3","journal_id":"out","account_id":"b","debit":0,"credit":30},
        {"id":"l4","journal_id":"transfer","account_id":"a","debit":0,"credit":10},
        {"id":"l5","journal_id":"reversed_transfer","account_id":"a","debit":10,"credit":0},
        {"id":"l6","journal_id":"draft","account_id":"a","debit":999,"credit":0},
        {"id":"l7","journal_id":"future","account_id":"a","debit":999,"credit":0},
        {"id":"l8","journal_id":"in","account_id":"expense","debit":999,"credit":0},
    ]
    db = DB(journal_entries=Collection(headers),journal_lines=Collection(lines))
    accounts = {"a":{"account_type":"BANK","name":"Kas Jogja"},"b":{"account_type":"BANK","name":"Kas Semarang"},"expense":{"account_type":"EXPS"}}
    card = await _cash_movement(db,"2026-10-01","2026-10-31",accounts)
    async def adapter(db, *, start_date, end_date):
        return await _cash_movement(db,start_date,end_date,accounts,include_rows=True)
    kwargs = dict(widget="cash",permissions={"dashboard.finance.view","cash_bank.view"},start_date="2026-10-01",end_date="2026-10-31",get_cash_movement=adapter)
    result = await query_dashboard_rincian(db, **kwargs,page_size=1)
    assert card["cash_in"] == result["kpi_summary"]["total_debit"] == 100
    assert card["cash_out"] == result["kpi_summary"]["total_credit"] == 30
    assert card["net_change"] == result["kpi_summary"]["net_change"] == 70
    assert result["total"] == 2 and len(result["rows"]) == 1
    assert result["rows"][0]["number"] == "IN-1"
    assert result["kpi_summary"]["comparable_to_card"] is True
    page2 = await query_dashboard_rincian(db,**kwargs,page=2,page_size=1)
    assert page2["rows"][0]["account_name"] == "Kas Semarang"
    filtered = await query_dashboard_rincian(db,**kwargs,q="Semarang")
    assert filtered["total"] == 1 and filtered["kpi_summary"]["net_change"] == -30
    assert filtered["kpi_summary"]["comparable_to_card"] is False
    with pytest.raises(HTTPException) as e:
        await query_dashboard_rincian(db,**kwargs,cash_bank_id="first-bank")
    assert e.value.status_code == 400

@pytest.mark.parametrize("delivered,remaining", [(0,100),(30,70),(100,0),(150,0)])
def test_remaining_uses_delivered_not_print_output(delivered,remaining):
    o=order(qty_delivered=delivered,line_items=[{"quantity":100,"unit":"PCS","production_process":"print"}],operator_splits=[{"line_idx":0,"quantity":100,"qty_completed":100}])
    assert delivery_remaining(o)["by_unit"] == {"pcs":remaining}

def test_mixed_units_and_legacy_final_delivery():
    assert delivery_remaining(order())["by_unit"] == {"pcs":100,"box":5}
    assert delivery_remaining(order(qty_delivered=10))["value"] is None
    assert "campuran" in delivery_remaining(order(qty_delivered=10))["label"]
    assert delivery_remaining(order(delivery_status="Terkirim",current_stage=14))["by_unit"] == {"pcs":0,"box":0}

def test_completed_orders_stay_in_historical_analytics_not_active_kpi():
    completed=order("done",qty_delivered=105,delivered_at="2026-10-30T08:00:00Z",priority="urgent",created_at="2026-10-01T00:00:00Z")
    active=order("active",priority="urgent",created_at="2026-10-01T00:00:00Z")
    cancelled=order("cancelled",cancelled=True)
    snapshot=build_production_snapshot([completed,active,cancelled])
    assert snapshot["po_aktif"]["count"] == 1
    assert snapshot["wave2"]["population"] == "non_cancelled_all"
    assert snapshot["wave2"]["aging"]["total"]["selesai"] == 1
    assert snapshot["wave2"]["aging"]["total"]["aktif"] == 1
    assert sum(snapshot["wave2"]["ketepatan"]["pengiriman"].values()) == 2
    # Legacy completion never re-enters production bottleneck despite absent print evidence.
    completed["production_schedule"]={"state":"finalized"}
    completed["production_finish_target"]={"initial_date":"2026-10-01","date":"2026-10-01","locked":True}
    assert build_wave2([completed],today=date(2026,10,31))["bottleneck"]["po"] == 0
    completed["qty_delivered"] = 0
    assert build_wave2([completed],today=date(2026,10,31))["bottleneck"]["po"] == 1

@pytest.mark.anyio
async def test_empty_cash_does_not_become_zero_money():
    async def empty(_db, **kw):
        return {"status":"empty", "cash_in":None, "cash_out":None, "items":[]}
    result=await query_dashboard_rincian({},widget="cash",permissions={"dashboard.finance.view","cash_bank.view"},get_cash_movement=empty)
    assert result["kpi_summary"]["total_debit"] is None
    assert result["kpi_summary"]["net_change"] is None

@pytest.mark.parametrize("value", [None,"bad",True,"NaN",1.5])
def test_invalid_physical_quantity_is_not_zero(value):
    assert delivery_remaining(order(line_items=[{"quantity":value,"unit":"pcs"}]))["label"] == "Qty tidak valid"

def test_completed_cohort_keeps_its_finalized_target_and_realization():
    completed=order("done",qty_delivered=100,line_items=[{"quantity":100,"unit":"pcs","production_process":"print"}],production_schedule={"state":"finalized"},
        production_schedule_events=[{"kind":"schedule_finalized","split_id":"s","qty":100,"unit":"pcs","work_date":"2026-10-01"}],
        operator_splits=[{"id":"s","line_idx":0,"quantity":100,"qty_completed":100,"work_date":"2026-10-01"}])
    result=build_production_snapshot([completed])
    assert result["po_aktif"]["count"] == 0
    assert result["wave2"]["target_realisasi"]["series"][0]["awal"] == 100
    assert result["wave2"]["target_realisasi"]["series"][0]["realisasi"] == 100

@pytest.mark.anyio
async def test_registered_cash_route_uses_canonical_builder_without_legacy_mutasi(monkeypatch):
    from services.dashboard_rincian_http import register_dashboard_rincian_routes
    from services import production_dashboard_http
    from services.chart_of_accounts import COLLECTION
    db=DB({COLLECTION:Collection([{"id":"bank","code":"1101","name":"Bank Utama","account_type":"BANK"}]),
           "journal_entries":Collection([{"id":"j","number":"J-1","date":"2026-10-01","status":"posted"}]),
           "journal_lines":Collection([{"id":"l","journal_id":"j","account_id":"bank","debit":75,"credit":0}])})
    class Router:
        def get(self,path):
            def capture(fn): self.endpoint=fn; return fn
            return capture
    router=Router()
    async def perms(_): return {"dashboard.finance.view","cash_bank.view"}
    async def settings(): return {"sla_working_days":3}
    async def scope(*_): return {}
    monkeypatch.setattr(production_dashboard_http,"order_branch_scope",scope)
    def forbidden_legacy(*a,**k): raise AssertionError("Legacy mutasi must not be called")
    register_dashboard_rincian_routes(router,get_db=lambda:db,current_user=lambda:None,user_permissions=perms,
        apply_hold=lambda o:None,attach_deadline=lambda *a,**k:None,get_deadline_settings=settings,
        status_po_projection={},sales_owner_match=lambda _: {},list_delivery_schedule=None,
        query_sales_invoices=None,list_purchase_invoices=None,get_mutasi=forbidden_legacy,
        list_cash_banks=forbidden_legacy,get_profit_loss=None,build_kepala_board_for_user=None)
    result=await router.endpoint(widget="cash",dashboard="keuangan",q="",page=1,page_size=50,
        start_date="2026-10-01",end_date="2026-10-31",unit=None,status=None,cash_bank_id=None,basis=None,user={})
    assert result["kpi_summary"]["total_debit"] == 75
    assert result["rows"][0]["account_name"] == "Bank Utama"
