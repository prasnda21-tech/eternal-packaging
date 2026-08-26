"""Gaji Operator — per-operator rate, product override, history-safe. No live API."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.operator_payroll import (
    compute_operator_payroll,
    effective_rate_as_of,
    parse_operator_ids,
    snapshot_on_save,
)


def _order(output=5000, day="2026-08-10", aid="104", code="PC-16", email="budi@x.com", name="Budi", uid="u-budi", oid=None):
    oid = oid or f"po-{uid}"
    return {
        "id": oid,
        "po_number": oid.upper(),
        "customer": "Cust",
        "cancelled": False,
        "line_items": [{
            "name": "Cup 16oz",
            "item_code": code,
            "accurate_item_id": aid,
            "master_item_id": "m-104",
            "quantity": output,
            "production_process": "print",
        }],
        "operator_splits": [{
            "id": "s1",
            "operator_user_id": uid,
            "operator_email": email,
            "operator_name": name,
            "quantity": output,
            "qty_completed": output,
            "line_idx": 0,
            "work_date": day,
            "updated_at": f"{day}T10:00:00+07:00",
        }],
        "timeline": [],
    }


def _wage_budi_andi(budi=100, andi=125, overrides=None, day=date(2026, 8, 1)):
    return snapshot_on_save({}, {
        "operators": [
            {"operator_user_id": "u-budi", "operator_email": "budi@x.com", "operator_name": "Budi", "default_rate_per_pcs": budi},
            {"operator_user_id": "u-andi", "operator_email": "andi@x.com", "operator_name": "Andi", "default_rate_per_pcs": andi},
        ],
        "overrides": overrides or [],
    }, {"email": "admin"}, "2026-08-01T00:00:00+07:00", today=day)


def test_budi_default_5000_times_100():
    settings = _wage_budi_andi()
    r = compute_operator_payroll([_order()], settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert r["kpis"]["output"] == 5000
    assert r["kpis"]["total_upah"] == 500000
    assert r["rows"][0]["rate_per_pcs"] == 100


def test_budi_product_override_150():
    settings = _wage_budi_andi(overrides=[{
        "operator_user_id": "u-budi",
        "operator_email": "budi@x.com",
        "accurate_item_id": "104",
        "master_item_id": "m-104",
        "item_code": "PC-16",
        "item_name": "Cup 16oz",
        "rate_per_pcs": 150,
        "active": True,
    }])
    r = compute_operator_payroll([_order()], settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert r["rows"][0]["total_upah"] == 750000


def test_andi_default_5000_times_125():
    settings = _wage_budi_andi()
    r = compute_operator_payroll(
        [_order(email="andi@x.com", name="Andi", uid="u-andi")],
        settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31),
    )
    assert r["kpis"]["total_upah"] == 625000
    assert r["rows"][0]["rate_per_pcs"] == 125


def test_product_override_does_not_apply_to_other_operator():
    settings = _wage_budi_andi(overrides=[{
        "operator_user_id": "u-budi",
        "accurate_item_id": "104",
        "rate_per_pcs": 150,
        "active": True,
    }])
    r = compute_operator_payroll(
        [_order(email="andi@x.com", name="Andi", uid="u-andi")],
        settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31),
    )
    assert r["rows"][0]["total_upah"] == 625000


def test_unset_operator_rate_is_not_invented():
    settings = snapshot_on_save({}, {
        "operators": [{"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": None}],
        "overrides": [],
    }, {"email": "admin"}, "2026-08-01T00:00:00+07:00", today=date(2026, 8, 1))
    r = compute_operator_payroll([_order()], settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert r["rows"][0]["rate_unset"] is True
    assert r["rows"][0]["total_upah"] == 0


def test_legacy_global_default_still_works():
    settings = snapshot_on_save({}, {"default_rate_per_pcs": 100, "overrides": []}, {"email": "admin"}, "2026-08-01T00:00:00+07:00", today=date(2026, 8, 1))
    r = compute_operator_payroll([_order()], settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert r["kpis"]["total_upah"] == 500000


def test_legacy_product_override_150():
    settings = snapshot_on_save({}, {
        "default_rate_per_pcs": 100,
        "overrides": [{"accurate_item_id": "104", "master_item_id": "m-104", "item_code": "PC-16", "item_name": "Cup 16oz", "rate_per_pcs": 150, "active": True}],
    }, {"email": "admin"}, "2026-08-01T00:00:00+07:00", today=date(2026, 8, 1))
    r = compute_operator_payroll([_order()], settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert r["rows"][0]["total_upah"] == 750000


def test_date_filter_excludes_outside_range():
    settings = _wage_budi_andi()
    r = compute_operator_payroll([_order(day="2026-07-01")], settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert r["kpis"]["output"] == 0
    assert r["rows"] == []


def test_rate_change_does_not_rewrite_history():
    first = _wage_budi_andi(budi=100)
    later = snapshot_on_save(first, {
        "operators": [
            {"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": 200},
            {"operator_user_id": "u-andi", "operator_email": "andi@x.com", "default_rate_per_pcs": 125},
        ],
        "overrides": [],
    }, {"email": "admin"}, "2026-08-20T00:00:00+07:00", today=date(2026, 8, 20))
    ident = {"accurate_item_id": "104"}
    op = {"operator_user_id": "u-budi", "operator_email": "budi@x.com"}
    assert effective_rate_as_of(ident, date(2026, 8, 10), later, op) == 100
    assert effective_rate_as_of(ident, date(2026, 8, 21), later, op) == 200
    r = compute_operator_payroll([_order(day="2026-08-10")], later, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert r["rows"][0]["total_upah"] == 500000


def test_multi_select_budi_andi_only():
    settings = _wage_budi_andi()
    orders = [
        _order(),
        _order(email="andi@x.com", name="Andi", uid="u-andi"),
        _order(email="sinta@x.com", name="Sinta", uid="u-sinta"),
    ]
    r = compute_operator_payroll(
        orders, settings,
        date_from=date(2026, 8, 1), date_to=date(2026, 8, 31),
        operator_ids=["u-budi", "u-andi"],
    )
    names = {row["operator"] for row in r["rows"]}
    assert names == {"Budi", "Andi"}
    assert "Sinta" not in names
    assert r["kpis"]["total_upah"] == 500000 + 625000


def test_parse_operator_ids_empty_means_all():
    assert parse_operator_ids(None, None) == []
    assert parse_operator_ids("", "") == []
    assert parse_operator_ids(None, "u-budi,u-andi") == ["u-budi", "u-andi"]


def test_export_columns_numeric():
    settings = _wage_budi_andi()
    r = compute_operator_payroll([_order()], settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    row = r["rows"][0]
    assert isinstance(row["qty_completed"], int)
    assert isinstance(row["rate_per_pcs"], float)
    assert isinstance(row["total_upah"], float)
