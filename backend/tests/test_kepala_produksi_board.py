"""Kepala Produksi Priority Board — qty, KPI, HOLD, delivery, priority."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.division_tasks import build_kepala_produksi_tasks
from services.kepala_produksi_board import (
    aggregate_kepala_kpis,
    build_kepala_hold_readonly_rows,
    build_kepala_produksi_board,
    enrich_kepala_task_rows,
    filter_rows_for_kpi,
    line_unit,
    priority_rank,
    resolve_kpi_key,
)


def _ready(**kwargs):
    o = {
        "id": "po1",
        "po_number": "PO-001",
        "customer": "Cust A",
        "cancelled": False,
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_files": [{"id": "d1"}],
        "payment_files": [{"id": "p1"}],
        "current_stage": 6,
        "hold": False,
        "priority": "normal",
        "line_items": [
            {
                "id": "L1",
                "name": "Cup A",
                "quantity": 10000,
                "production_process": "print",
                "unit": "pcs",
            }
        ],
        "operator_splits": [],
        "effective_delivery_date": "2026-10-13",
        "tanggal_pengiriman": "2026-10-13",
        "delivery_deadline_status": "normal",
        "delivery_deadline_effective": "2026-10-13T00:00:00+07:00",
        "delivery_deadline_source": "manual",
    }
    o.update(kwargs)
    return o


def test_current_qty_no_invented_original():
    o = _ready(line_items=[{
        "id": "L1", "name": "X", "quantity": 1200, "production_process": "print", "unit": "pcs",
    }])
    rows = build_kepala_produksi_tasks(o)
    enrich_kepala_task_rows(rows, {o["id"]: o})
    assert rows[0]["qty_order_current"] == 1200
    assert "qty_original" not in rows[0]


def test_partial_production_10k_6k_4k():
    o = _ready(operator_splits=[
        {"id": "a", "line_idx": 0, "quantity": 10000, "qty_completed": 6000},
    ])
    rows = build_kepala_produksi_tasks(o)
    enrich_kepala_task_rows(rows, {o["id"]: o})
    assert rows[0]["qty_order_current"] == 10000
    assert rows[0]["qty_produced"] == 6000
    assert rows[0]["qty_production_remaining"] == 4000
    assert rows[0]["qty_scheduled"] == 10000
    assert rows[0]["qty_unscheduled"] == 0
    # Legacy schedule fields unchanged
    assert rows[0]["completed_qty"] == 10000
    assert rows[0]["remaining_qty"] == 0


def test_multi_operator_no_double_count_produced():
    o = _ready(operator_splits=[
        {"id": "a", "line_idx": 0, "quantity": 4000, "qty_completed": 3000},
        {"id": "b", "line_idx": 0, "quantity": 6000, "qty_completed": 3000},
    ])
    rows = build_kepala_produksi_tasks(o)
    enrich_kepala_task_rows(rows, {o["id"]: o})
    assert len(rows) == 1
    assert rows[0]["qty_produced"] == 6000
    assert rows[0]["qty_scheduled"] == 10000


def test_multi_line_unique_kpi_po_count():
    o = _ready(
        id="po-ml",
        line_items=[
            {"id": "L1", "name": "A", "quantity": 1000, "production_process": "print", "unit": "pcs"},
            {"id": "L2", "name": "B", "quantity": 2000, "production_process": "print", "unit": "pcs"},
        ],
        operator_splits=[],
        delivery_deadline_status="overdue",
        effective_delivery_date="2026-10-01",
    )
    with patch("services.kepala_produksi_board.wib_today") as today:
        today.return_value.isoformat.return_value = "2026-10-13"
        from datetime import date
        today.return_value = date(2026, 10, 13)
        board = build_kepala_produksi_board([o], build_kepala_produksi_tasks(o))
    assert board["kpis"]["terlambat"]["count"] == 1
    assert board["kpis"]["perlu-produksi"]["count"] == 1
    assert len(board["tasks"]) == 2


def test_mixed_unit_safe_sisa_totals():
    o = _ready(
        id="po-u",
        line_items=[
            {"id": "L1", "name": "A", "quantity": 100, "production_process": "print", "unit": "pcs"},
            {"id": "L2", "name": "B", "quantity": 5, "production_process": "print", "unit": "box"},
        ],
        operator_splits=[],
    )
    board = build_kepala_produksi_board([o], build_kepala_produksi_tasks(o))
    by_unit = board["kpis"]["sisa-produksi"]["by_unit"]
    assert by_unit["pcs"] == 100
    assert by_unit["box"] == 5
    assert board["kpis"]["sisa-produksi"]["value"] is None  # multi-unit


def test_scheduled_vs_produced_distinct():
    o = _ready(operator_splits=[
        {"id": "a", "line_idx": 0, "quantity": 8000, "qty_completed": 1000},
    ])
    rows = build_kepala_produksi_tasks(o)
    enrich_kepala_task_rows(rows, {o["id"]: o})
    assert rows[0]["qty_scheduled"] == 8000
    assert rows[0]["qty_unscheduled"] == 2000
    assert rows[0]["qty_produced"] == 1000
    assert rows[0]["qty_production_remaining"] == 9000


def test_hold_active_readonly_and_released():
    held = _ready(
        id="po-hold",
        design_status="Waiting",
        payment_status="Waiting",
        design_files=[],
        payment_files=[],
        current_stage=2,
        hold=True,
        hold_reasons=["Menunggu approval design"],
    )
    # group2 incomplete → no actionable tasks
    assert build_kepala_produksi_tasks(held) == []
    hold_rows = build_kepala_hold_readonly_rows(held)
    assert len(hold_rows) == 1
    assert hold_rows[0]["authoritative_hold"] is True
    assert hold_rows[0]["schedule_eligible"] is False
    assert hold_rows[0]["available_actions"] == []

    released = _ready(hold=False, hold_reasons=[], current_stage=6)
    board = build_kepala_produksi_board([released], build_kepala_produksi_tasks(released))
    assert board["kpis"]["hold"]["count"] == 0


def test_explicit_urgent_vs_overdue():
    from datetime import date
    overdue = _ready(
        id="po-od",
        priority="normal",
        delivery_deadline_status="overdue",
        effective_delivery_date="2026-10-01",
    )
    urgent = _ready(
        id="po-ug",
        po_number="PO-UG",
        priority="urgent",
        delivery_deadline_status="normal",
        effective_delivery_date="2026-10-20",
    )
    with patch("services.kepala_produksi_board.wib_today") as today:
        today.return_value = date(2026, 10, 13)
        board = build_kepala_produksi_board(
            [overdue, urgent],
            build_kepala_produksi_tasks(overdue) + build_kepala_produksi_tasks(urgent),
        )
    assert board["kpis"]["terlambat"]["count"] == 1
    assert board["kpis"]["urgent"]["count"] == 1
    od_rows = filter_rows_for_kpi(board["board_rows"], "terlambat")
    ug_rows = filter_rows_for_kpi(board["board_rows"], "urgent")
    assert {r["order_id"] for r in od_rows} == {"po-od"}
    assert {r["order_id"] for r in ug_rows} == {"po-ug"}
    assert priority_rank(od_rows[0]) < priority_rank(ug_rows[0])


def test_due_today_wib():
    o = _ready(
        effective_delivery_date="2026-10-13",
        delivery_deadline_status="overdue",  # global helper may still say overdue
    )
    with patch("services.kepala_produksi_board.wib_today") as today:
        from datetime import date
        today.return_value = date(2026, 10, 13)
        board = build_kepala_produksi_board([o], build_kepala_produksi_tasks(o))
    assert board["kpis"]["kirim-hari-ini"]["count"] == 1
    assert board["kpis"]["terlambat"]["count"] == 0
    flags = board["tasks"][0]["kpi_flags"]
    assert flags["kirim-hari-ini"] is True
    assert flags["terlambat"] is False


def test_exclusive_calendar_overdue_vs_due_today():
    """Owner rule: OVERDUE iff date < today; DUE TODAY iff date == today; never both."""
    from datetime import date

    yesterday = _ready(id="po-y", po_number="PO-Y", effective_delivery_date="2026-10-12",
                       delivery_deadline_status="overdue")
    today_po = _ready(id="po-t", po_number="PO-T", effective_delivery_date="2026-10-13",
                      delivery_deadline_status="overdue")
    tomorrow = _ready(id="po-m", po_number="PO-M", effective_delivery_date="2026-10-14",
                      delivery_deadline_status="normal")
    nodate = _ready(id="po-n", po_number="PO-N", effective_delivery_date=None,
                    tanggal_pengiriman=None, delivery_deadline_status="unknown")
    with patch("services.kepala_produksi_board.wib_today") as today:
        today.return_value = date(2026, 10, 13)
        board = build_kepala_produksi_board(
            [yesterday, today_po, tomorrow, nodate],
            build_kepala_produksi_tasks(yesterday)
            + build_kepala_produksi_tasks(today_po)
            + build_kepala_produksi_tasks(tomorrow)
            + build_kepala_produksi_tasks(nodate),
        )
    by_id = {r["order_id"]: r["kpi_flags"] for r in board["board_rows"]}
    assert by_id["po-y"]["terlambat"] is True and by_id["po-y"]["kirim-hari-ini"] is False
    assert by_id["po-t"]["terlambat"] is False and by_id["po-t"]["kirim-hari-ini"] is True
    assert by_id["po-m"]["terlambat"] is False and by_id["po-m"]["kirim-hari-ini"] is False
    assert by_id["po-n"]["terlambat"] is False and by_id["po-n"]["kirim-hari-ini"] is False
    assert board["kpis"]["terlambat"]["count"] == 1
    assert board["kpis"]["kirim-hari-ini"]["count"] == 1
    for r in board["board_rows"]:
        if r["order_id"] == "po-t":
            assert "DUE TODAY" in r["priority_display"]
            assert "OVERDUE" not in r["priority_display"]
        if r["order_id"] == "po-y":
            assert "OVERDUE" in r["priority_display"]
            assert "DUE TODAY" not in r["priority_display"]


def test_urgent_combines_with_due_today_or_overdue_exclusively():
    from datetime import date
    u_today = _ready(id="po-ut", po_number="PO-UT", priority="urgent",
                     effective_delivery_date="2026-10-13")
    u_yday = _ready(id="po-uy", po_number="PO-UY", priority="urgent",
                    effective_delivery_date="2026-10-12")
    with patch("services.kepala_produksi_board.wib_today") as today:
        today.return_value = date(2026, 10, 13)
        board = build_kepala_produksi_board(
            [u_today, u_yday],
            build_kepala_produksi_tasks(u_today) + build_kepala_produksi_tasks(u_yday),
        )
    assert board["kpis"]["urgent"]["count"] == 2
    assert board["kpis"]["kirim-hari-ini"]["count"] == 1
    assert board["kpis"]["terlambat"]["count"] == 1
    for r in board["board_rows"]:
        flags = r["kpi_flags"]
        assert not (flags["kirim-hari-ini"] and flags["terlambat"])
        if r["order_id"] == "po-ut":
            assert flags["urgent"] and flags["kirim-hari-ini"] and not flags["terlambat"]
        if r["order_id"] == "po-uy":
            assert flags["urgent"] and flags["terlambat"] and not flags["kirim-hari-ini"]


def test_global_deadline_helper_untouched_by_kp_classifier():
    """KP calendar overdue must not alter server._deadline_status_for semantics."""
    import ast
    import inspect
    import server as srv
    src = inspect.getsource(srv._deadline_status_for)
    assert "delta.total_seconds()" in src
    assert "overdue" in src
    import services.kepala_produksi_board as board_mod
    csrc = inspect.getsource(board_mod.classify_row_kpi_flags)
    tree = ast.parse(csrc)
    calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "_deadline_status_for" not in calls
    assert "date_key < today" in csrc
    assert "date_key == today" in csrc


def test_null_delivery_date_not_due_today():
    o = _ready(effective_delivery_date=None, tanggal_pengiriman=None, delivery_deadline_status="unknown")
    with patch("services.kepala_produksi_board.wib_today") as today:
        from datetime import date
        today.return_value = date(2026, 10, 13)
        board = build_kepala_produksi_board([o], build_kepala_produksi_tasks(o))
    assert board["kpis"]["kirim-hari-ini"]["count"] == 0


def test_fully_delivered_excluded_from_delivery_kpis():
    o = _ready(
        qty_delivered=10000,
        delivery_status="Terkirim",
        current_stage=14,
        delivery_deadline_status="overdue",
        effective_delivery_date="2026-10-01",
        priority="urgent",
    )
    board = build_kepala_produksi_board([o], build_kepala_produksi_tasks(o))
    enrich_kepala_task_rows(board["tasks"], {o["id"]: o})
    # is_fully_delivered true → flags false
    flags = board["tasks"][0]["kpi_flags"]
    assert flags["terlambat"] is False
    assert flags["urgent"] is False
    assert flags["kirim-hari-ini"] is False


def test_kpi_detail_reconcile_sisa():
    o = _ready(operator_splits=[
        {"id": "a", "line_idx": 0, "quantity": 4000, "qty_completed": 2500},
    ])
    board = build_kepala_produksi_board([o], build_kepala_produksi_tasks(o))
    detail = filter_rows_for_kpi(board["board_rows"], "sisa-produksi")
    total = sum(r["qty_production_remaining"] for r in detail)
    assert total == board["kpis"]["sisa-produksi"]["by_unit"]["pcs"]
    assert total == 7500


def test_resolve_kpi_key_and_projection_fields():
    assert resolve_kpi_key("kirim_hari_ini") == "kirim-hari-ini"
    assert resolve_kpi_key("nope") is None
    from services.division_tasks import TASK_PROJECTION
    assert TASK_PROJECTION.get("scheduled_delivery_date") == 1
    assert TASK_PROJECTION.get("delivery_reschedules") == 1
    assert TASK_PROJECTION.get("payment_files") == 1


def test_line_unit_default_pcs():
    assert line_unit({}) == "pcs"
    assert line_unit({"unit": "box"}) == "box"


def test_overproduction_visible():
    o = _ready(operator_splits=[
        {"id": "a", "line_idx": 0, "quantity": 10000, "qty_completed": 11000},
    ])
    rows = build_kepala_produksi_tasks(o)
    enrich_kepala_task_rows(rows, {o["id"]: o})
    assert rows[0]["qty_production_remaining"] == 0
    assert rows[0]["qty_production_over"] == 1000
