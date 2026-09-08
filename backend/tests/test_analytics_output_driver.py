"""Analitik Output — canonical Customer + Driver Pengiriman report."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from output_reports import (
    EXPORT_COLUMNS,
    compute_driver_report,
    compute_screener_report,
    iter_driver_export_rows,
    iter_helper_export_rows,
    iter_operator_export_rows,
    iter_screener_export_rows,
)

PERIOD_FROM = date(2026, 8, 1)
PERIOD_TO = date(2026, 8, 31)
WIB = timezone(timedelta(hours=7))


def _at(day, hour=10):
    return datetime(2026, 8, day, hour, 0, tzinfo=WIB).isoformat()


def _line(name, qty, line_id=None, idx_name=None):
    row = {"name": name, "quantity": qty, "production_process": "print"}
    if line_id:
        row["id"] = line_id
        row["line_id"] = line_id
    return row


def _base(**extra):
    customer = extra.pop("customer", "PT ABC")
    order = {
        "id": extra.pop("id", "ord-1"),
        "po_number": extra.pop("po_number", "PO-1"),
        "customer": customer,
        "customer_name": extra.pop("customer_name", customer),
        "customer_id": extra.pop("customer_id", "cust-abc"),
        "outlet": extra.pop("outlet", "Cabang Yogyakarta"),
        "cancelled": False,
        "line_items": extra.pop("line_items", [
            _line("Cup 12", 4000, "line-a"),
            _line("Cup 12", 3000, "line-b"),
        ]),
        "qty_delivered": extra.pop("qty_delivered", 0),
        "delivery_status": extra.pop("delivery_status", "Belum Terkirim"),
        "current_stage": extra.pop("current_stage", 13),
        "packing_info": extra.pop("packing_info", {"qty_packed": 4000}),
        "shipments": extra.pop("shipments", [{"id": "s1", "number": "SJ-1"}]),
        "loading_info": extra.pop("loading_info", {"lines": [{"line_idx": 0, "loaded_qty": 4000}]}),
        "loading_checklist": extra.pop("loading_checklist", {"driver_name": "Budi"}),
        "delivery_confirmations": extra.pop("delivery_confirmations", []),
        "delivered_at": extra.pop("delivered_at", None),
    }
    order.update(extra)
    return order


def _conf(*, cid="c1", driver_id="u-budi", driver_name="Budi", day=10, qty=2000, items=None):
    return {
        "id": cid,
        "driver_id": driver_id,
        "driver_name": driver_name,
        "confirmed_at": _at(day),
        "qty": qty,
        "items": items if items is not None else [
            {"line_idx": 0, "name": "Cup 12", "qty": qty},
        ],
    }


def test_export_columns_are_customer_not_outlet():
    for tab in ("operator", "helper", "screener", "driver"):
        labels = [c[1] for c in EXPORT_COLUMNS[tab]]
        assert "Customer" in labels
        assert "Outlet / Customer" not in labels


def test_screener_helper_operator_use_canonical_customer():
    order = _base(
        id="PO-C",
        po_number="PO-C",
        customer="PT ABC",
        outlet="Cabang Yogyakarta",
        operator_splits=[{
            "id": "s1",
            "operator_email": "op@x.com",
            "operator_name": "Op",
            "quantity": 100,
            "qty_completed": 100,
            "work_date": "2026-08-10",
            "line_idx": 0,
        }],
        timeline=[
            {"at": _at(10), "note": "Qty Op: 100/100", "by_email": "op@x.com", "by_name": "Op"},
            {"at": _at(10), "note": "Screen qty: 100", "by_email": "sc@x.com", "by_name": "Scr", "by_role": "screener"},
            {"at": _at(10), "note": "Persiapan Helper: 100/100", "by_email": "hp@x.com", "by_name": "Help", "by_role": "helper"},
        ],
        helper_prep={"qty_prepared": 100},
        screener_info={"screen_qty": 100},
    )
    op_rows = iter_operator_export_rows([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    sc_rows = iter_screener_export_rows([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    hp_rows = iter_helper_export_rows([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert op_rows[0]["customer"] == "PT ABC"
    assert sc_rows[0]["customer"] == "PT ABC"
    assert hp_rows[0]["customer"] == "PT ABC"
    assert all(r["customer"] != "Cabang Yogyakarta" for r in op_rows + sc_rows + hp_rows)
    screen = compute_screener_report([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert all(r["customer"] == "PT ABC" for r in screen["cup_lines"])


def test_confirmed_delivered_qty_counts():
    order = _base(
        qty_delivered=2000,
        delivered_at=_at(10),
        delivery_status="Sebagian Terkirim",
        delivery_confirmations=[_conf(qty=2000)],
    )
    out = compute_driver_report([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert out["kpis"]["total_qty"] == 2000
    assert out["rows"][0]["qty_delivered"] == 2000
    assert out["rows"][0]["driver"] == "Budi"
    assert out["rows"][0]["tanggal"] == "2026-08-10"


def test_loading_sj_packing_only_do_not_count():
    loading = _base(id="L1", po_number="PO-L", qty_delivered=0, delivery_confirmations=[])
    sj = _base(id="S1", po_number="PO-S", qty_delivered=0, delivery_confirmations=[],
               shipments=[{"id": "sj", "number": "SJ-9", "lines": [{"line_idx": 0, "qty": 4000}]}])
    packing = _base(id="P1", po_number="PO-P", qty_delivered=0, delivery_confirmations=[],
                    packing_info={"qty_packed": 8000}, loading_info={})
    out = compute_driver_report([loading, sj, packing], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert out["kpis"]["total_qty"] == 0
    assert out["rows"] == []


def test_zero_qty_delivered_does_not_inflate():
    order = _base(
        qty_delivered=0,
        delivery_confirmations=[_conf(qty=0, items=[{"line_idx": 0, "name": "Cup 12", "qty": 0}])],
        delivered_at=_at(10),
    )
    out = compute_driver_report([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert out["kpis"]["total_qty"] == 0
    assert out["rows"] == []


def test_distinct_po_and_customer_deduped():
    a = _base(
        id="PO-1", po_number="PO-1", customer_id="cust-abc", customer="PT ABC", outlet="Yogya",
        qty_delivered=3000, delivered_at=_at(10),
        delivery_confirmations=[
            _conf(cid="c1", day=10, qty=1000, items=[{"line_idx": 0, "name": "Cup 12", "qty": 1000}]),
            _conf(cid="c2", day=11, qty=2000, items=[{"line_idx": 1, "name": "Cup 12", "qty": 2000}]),
        ],
    )
    b = _base(
        id="PO-2", po_number="PO-2", customer_id="cust-abc", customer="PT ABC", outlet="Solo",
        qty_delivered=500, delivered_at=_at(12),
        delivery_confirmations=[_conf(cid="c3", day=12, qty=500)],
    )
    out = compute_driver_report([a, b], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert out["kpis"]["total_qty"] == 3500
    assert out["kpis"]["po_count"] == 2
    assert out["kpis"]["customer_count"] == 1
    person = out["people"][0]
    assert person["po_count"] == 2
    assert person["customer_count"] == 1
    assert all(r["customer"] == "PT ABC" for r in out["rows"])
    assert "Cabang Yogyakarta" not in [r["customer"] for r in out["rows"]]
    assert "Yogya" not in [r["customer"] for r in out["rows"]]
    assert "Solo" not in [r["customer"] for r in out["rows"]]


def test_two_delivery_rows_same_po_do_not_double_po_count():
    order = _base(
        qty_delivered=3000,
        delivered_at=_at(12),
        delivery_confirmations=[
            _conf(cid="c1", day=10, qty=1000),
            _conf(cid="c2", day=12, qty=2000, items=[{"line_idx": 1, "name": "Cup 12", "qty": 2000}]),
        ],
    )
    out = compute_driver_report([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert len(out["rows"]) == 2
    assert out["kpis"]["po_count"] == 1
    assert out["people"][0]["po_count"] == 1
    assert out["kpis"]["total_qty"] == 3000


def test_same_display_name_distinct_driver_ids_not_merged():
    order = _base(
        qty_delivered=3000,
        delivered_at=_at(12),
        delivery_confirmations=[
            _conf(cid="c1", driver_id="u-1", driver_name="Budi", day=10, qty=1000),
            _conf(cid="c2", driver_id="u-2", driver_name="Budi", day=12, qty=2000,
                  items=[{"line_idx": 1, "name": "Cup 12", "qty": 2000}]),
        ],
    )
    out = compute_driver_report([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert len(out["people"]) == 2
    by_id = {p["id"]: p for p in out["people"]}
    assert by_id["id:u-1"]["qty"] == 1000
    assert by_id["id:u-2"]["qty"] == 2000


def test_same_name_lines_keep_line_identity():
    order = _base(
        qty_delivered=5000,
        delivered_at=_at(10),
        line_items=[_line("Cup 12", 4000, "line-a"), _line("Cup 12", 3000, "line-b")],
        delivery_confirmations=[_conf(
            qty=5000,
            items=[
                {"line_idx": 0, "name": "Cup 12", "qty": 2000},
                {"line_idx": 1, "name": "Cup 12", "qty": 3000},
            ],
        )],
    )
    rows = iter_driver_export_rows([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert len(rows) == 2
    by_idx = {r["line_idx"]: r for r in rows}
    assert by_idx[0]["line_id"] == "line-a"
    assert by_idx[1]["line_id"] == "line-b"
    assert by_idx[0]["qty_delivered"] == 2000
    assert by_idx[1]["qty_delivered"] == 3000
    assert by_idx[0]["_key"] != by_idx[1]["_key"]


def test_legacy_qty_delivered_without_confirmations_uses_delivered_at():
    order = _base(
        qty_delivered=1500,
        delivered_at=_at(15),
        delivery_status="Sebagian Terkirim",
        delivery_confirmations=[],
        loading_checklist={"driver_name": "Andi"},
    )
    out = compute_driver_report([order], date_from=PERIOD_FROM, date_to=PERIOD_TO)
    assert out["kpis"]["total_qty"] == 1500
    assert out["rows"][0]["tanggal"] == "2026-08-15"
    assert out["rows"][0]["driver"] == "Andi"
    assert out["rows"][0]["customer"] == "PT ABC"
