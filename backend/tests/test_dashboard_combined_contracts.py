"""Dashboard contracts using real board output; no server or database writes."""
from datetime import date

import pytest
from fastapi import HTTPException

from services.dashboard_rincian import query_dashboard_rincian
from services.division_tasks import build_kepala_produksi_tasks
from services.kepala_produksi_board import build_kepala_produksi_board
from services.production_analytics import build_plant_analytics


@pytest.fixture
def anyio_backend():
    return "asyncio"


def order(oid="o1", **extra):
    return {
        "id": oid, "po_number": f"PO-{oid}", "customer": "Pelanggan Uji",
        "cancelled": False, "current_stage": 6, "hold": False, "priority": "normal",
        "design_status": "Approved", "payment_status": "Approved",
        "design_files": [{"id": "d"}], "payment_files": [{"id": "p"}],
        "line_items": [
            {"id": "a", "name": "Cup", "quantity": 100, "unit": "pcs", "production_process": "print"},
            {"id": "b", "name": "Box", "quantity": 5, "unit": "box", "production_process": "print"},
        ],
        "operator_splits": [], "effective_delivery_date": "2026-11-03",
        "tanggal_pengiriman": "2026-11-03", "delivery_deadline_status": "normal",
        "delivery_deadline_effective": "2026-11-03T00:00:00+07:00",
        **extra,
    }


def board_for(orders):
    tasks = [task for o in orders for task in build_kepala_produksi_tasks(o)]
    return build_kepala_produksi_board(orders, tasks)


async def kepala(board, widget="sisa_qty", **extra):
    return await query_dashboard_rincian(
        {}, widget=widget, permissions={"dashboard.production.view", "schedule.edit"},
        build_kepala_board=lambda: board, **extra,
    )


@pytest.mark.anyio
async def test_actual_board_fields_and_full_set_summary_before_pagination():
    board = board_for([order()])
    result = await kepala(board, page_size=1)
    assert result["total"] == 2
    assert result["kpi_summary"]["count"] == 1
    assert result["kpi_summary"]["line_count"] == 2
    assert result["kpi_summary"]["by_unit"] == {"pcs": 100, "box": 5}
    row = result["rows"][0]
    assert row["po_number"] == "PO-o1"
    assert row["item_name"] in {"Cup", "Box"}
    assert row["qty_remaining"] in {100, 5}
    assert row["tanggal_kirim"] == "2026-11-03"
    assert row["priority_label"] == "NORMAL"
    assert row["detail_href"] == "/po/o1"
    page2 = await kepala(board, page=2, page_size=1)
    assert page2["kpi_summary"] == result["kpi_summary"]
    assert page2["rows"][0]["row_no"] == 2
    assert page2["rows"][0]["id"] != row["id"]


@pytest.mark.anyio
async def test_actual_board_search_and_unit_filter_apply_before_summary():
    board = board_for([order()])
    by_po = await kepala(board, q="PO-o1")
    assert by_po["total"] == 2
    by_item = await kepala(board, q="box")
    assert by_item["kpi_summary"]["by_unit"] == {"box": 5}
    by_unit = await kepala(board, unit="pcs")
    assert by_unit["kpi_summary"]["by_unit"] == {"pcs": 100}
    assert by_unit["total"] == 1
    assert (await kepala(board, q="missing"))["kpi_summary"]["by_unit"] == {}


@pytest.mark.anyio
async def test_priority_includes_zero_print_remaining_without_quantity_fallback():
    o = order(line_items=[{"id": "a", "name": "Cup", "quantity": 100, "unit": "pcs", "production_process": "print"}],
              operator_splits=[{"id": "s", "line_idx": 0, "quantity": 100, "qty_completed": 100}])
    # Real board enrichment of an upstream scheduling row; scheduling qty must
    # never be substituted for zero remaining print qty.
    board = build_kepala_produksi_board([o], [{"order_id": "o1", "line_idx": 0, "order_number": "PO-o1", "item": "Cup", "qty": 999}])
    assert board["board_rows"][0]["qty_production_remaining"] == 0
    assert (await kepala(board, widget="perlu_produksi"))["total"] == 0
    result = await kepala(board, widget="prioritas")
    assert result["total"] == 1
    assert result["rows"][0]["qty_remaining"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("widget", ["po_aktif", "po_urgent", "po_hold", "deadline_6h", "deadline_24h", "approval_design", "approval_payment"])
@pytest.mark.parametrize("dataset_perm", ["nav.reports.status_po", "schedule.view"])
async def test_status_po_requires_both_permissions(widget, dataset_perm):
    with pytest.raises(HTTPException) as exc:
        await query_dashboard_rincian({}, widget=widget, permissions={"dashboard.production.view", dataset_perm})
    assert exc.value.status_code == 403


async def statement(_db, **_kwargs):
    return {"groups": [
        {"report_group": "revenue", "items": [{"account_id": "r", "code": "4", "name": "Pendapatan", "amount": 1000}]},
        {"report_group": "cogs", "items": [{"account_id": "c", "code": "5", "name": "HPP", "amount": 400}]},
        {"report_group": "expense", "items": [
            {"account_id": "e1", "code": "6.1", "name": "Sewa", "amount": 100},
            {"account_id": "e2", "code": "6.2", "name": "Koreksi", "amount": -20},
            {"account_id": "e3", "code": "6.3", "name": "Nol", "amount": 0, "balance": 999},
        ]},
    ], "totals": {"net_profit": 520, "expense_total": 80, "revenue_total": 1000}}


@pytest.mark.anyio
async def test_expense_excludes_other_groups_preserves_signed_zero_and_full_total():
    args = {"widget": "expense", "permissions": {"dashboard.finance.view", "profit_loss.view"}, "get_profit_loss": statement}
    result = await query_dashboard_rincian({}, **args, page_size=1)
    assert result["total"] == 3
    assert result["kpi_summary"]["amount"] == 80
    zero = await query_dashboard_rincian({}, **args, q="Nol")
    assert zero["rows"][0]["amount"] == 0
    assert zero["kpi_summary"]["amount"] == 0
    negative = await query_dashboard_rincian({}, **args, q="Koreksi")
    assert negative["kpi_summary"]["amount"] == -20


@pytest.mark.anyio
async def test_profit_card_permission_can_open_its_detail():
    result = await query_dashboard_rincian({}, widget="profit", permissions={"profit_loss.view"}, get_profit_loss=statement)
    assert result["total"] == 5
    assert result["kpi_summary"]["net_profit"] == 520


@pytest.mark.anyio
async def test_purchase_zero_grand_total_never_falls_back_to_other_total():
    async def invoices(_db, **_kwargs):
        return [{"id": "i", "grand_total": 0, "total": 999}]
    result = await query_dashboard_rincian({}, widget="purchases", permissions={"purchase_invoices.view"}, list_purchase_invoices=invoices)
    assert result["rows"][0]["grand_total"] == 0
    assert result["kpi_summary"]["amount"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("amount", [None, "", "NaN", "bad"])
async def test_purchase_invalid_amount_is_incomplete_not_zero(amount):
    async def invoices(_db, **_kwargs):
        return [{"id": "i", "grand_total": amount}]
    result = await query_dashboard_rincian({}, widget="purchases", permissions={"purchase_invoices.view"}, list_purchase_invoices=invoices)
    assert result["kpi_summary"]["amount"] is None
    assert result["kpi_summary"]["status"] == "error"


@pytest.mark.anyio
async def test_status_po_real_rows_use_enriched_deadline_without_inventing_qty():
    from test_dashboard_rincian import _DB, _Coll
    db = _DB({"orders": _Coll([order(order_date="2026-10-01")])})
    result = await query_dashboard_rincian(
        db, widget="po_aktif", permissions={"dashboard.production.view", "nav.reports.status_po", "schedule.view"},
        apply_hold=lambda o: None, attach_deadline=lambda o, *args, **kwargs: None,
    )
    assert result["rows"][0]["deadline"] == "2026-11-03T00:00:00+07:00"
    assert result["rows"][0]["qty_remaining"] is None
    assert result["total"] == 1


@pytest.mark.anyio
async def test_cash_actual_field_names_zero_balance_and_scope_disclosure():
    async def banks(_db):
        return [{"id": "b1"}]
    async def mutasi(_db, **kwargs):
        assert kwargs["exclude_internal_transfer"] is True
        return {"cash_bank_name": "Kas Uji", "total_debit": 10, "total_credit": 10, "items": [
            {"journal_line_id": "l1", "journal_id": "j1", "transaction_number": "OUT-1", "running_balance": 0, "balance": 999, "debit": 0, "credit": 10},
        ]}
    result = await query_dashboard_rincian({}, widget="cash", permissions={"dashboard.finance.view", "cash_bank.view"}, list_cash_banks=banks, get_mutasi=mutasi)
    assert result["rows"][0]["id"] == "l1"
    assert result["rows"][0]["number"] == "OUT-1"
    assert result["rows"][0]["running_balance"] == 0
    assert result["kpi_summary"]["comparable_to_card"] is False
    assert "Kas Uji" in result["kpi_summary"]["detail"]


def test_confirmation_analytics_keeps_units_days_and_duplicate_action_separate():
    o = order(operator_splits=[{"line_idx": 0, "operator_user_id": "u", "progress_entries": [
        {"action_id": "a", "qty": 10, "confirmed_at": "2026-10-31T01:00:00Z"},
        {"action_id": "a", "qty": 10, "confirmed_at": "2026-10-31T01:00:00Z"},
        {"action_id": "b", "qty": 20, "confirmed_at": "2026-10-30T01:00:00Z"},
        {"action_id": "legacy", "qty": 999, "legacy": True},
    ]}])
    result = build_plant_analytics([o], today=date(2026, 10, 31), days=7)
    assert result["today"]["by_unit"] == {"pcs": 10}
    assert [p["qty"] for p in result["trend"]["series"]["pcs"]] == [0, 0, 0, 0, 0, 20, 10]
    assert result["coverage"]["duplicate"] == 1
    assert result["coverage"]["legacy"] == 1
