"""Gaji Operator — per-operator rate, bonus, product addition, history-safe. No live API."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.operator_payroll import (
    BONUS_BLOCK,
    BONUS_ONCE,
    compute_bonus,
    compute_operator_payroll,
    effective_rate_as_of,
    parse_operator_ids,
    snapshot_on_save,
    validate_wage_settings,
)


def _order(output=5000, day="2026-08-10", aid="104", code="PC-16", email="budi@x.com", name="Budi", uid="u-budi", oid=None, item_name="Cup 16oz"):
    oid = oid or f"po-{uid}"
    return {
        "id": oid,
        "po_number": oid.upper(),
        "customer": "Cust",
        "cancelled": False,
        "line_items": [{
            "name": item_name,
            "item_code": code,
            "accurate_item_id": aid,
            "master_item_id": f"m-{aid}",
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


def _wage(operators, overrides=None, bonus=None, additions=None, day=date(2026, 8, 1), extra=None):
    incoming = {
        "operators": operators,
        "overrides": overrides or [],
        "product_additions": additions or [],
    }
    if bonus is not None:
        incoming["bonus"] = bonus
    if extra:
        incoming.update(extra)
    return snapshot_on_save({}, incoming, {"email": "admin"}, f"{day.isoformat()}T00:00:00+07:00", today=day)


def _wage_budi_andi(budi=100, andi=125, overrides=None, day=date(2026, 8, 1)):
    return _wage([
        {"operator_user_id": "u-budi", "operator_email": "budi@x.com", "operator_name": "Budi", "default_rate_per_pcs": budi, "effective_from": day.isoformat()},
        {"operator_user_id": "u-andi", "operator_email": "andi@x.com", "operator_name": "Andi", "default_rate_per_pcs": andi, "effective_from": day.isoformat()},
    ], overrides=overrides, day=day)


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
        "rate_type": "override",
        "active": True,
    }])
    r = compute_operator_payroll([_order()], settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert r["rows"][0]["total_upah"] == 750000
    assert r["rows"][0]["product_addition"] == 0


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
    assert r["missing_base_rate_message"] == "Upah Operator belum disetel"
    assert r["people"][0]["rate_unset"] is True


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
    assert r["rows"][0]["rate_source"] == "product_override"


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
    people_names = {p["name"] for p in r["people"]}
    assert people_names == {"Budi", "Andi"}


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
    assert isinstance(row["base_wage"], float)
    assert isinstance(row["product_addition"], float)


def test_manual_base_rate_per_operator_identity_not_name():
    settings = _wage([
        {"operator_user_id": "u-budi", "operator_email": "budi@x.com", "operator_name": "Wrong", "default_rate_per_pcs": 20},
        {"operator_user_id": "u-andi", "operator_email": "andi@x.com", "operator_name": "Wrong", "default_rate_per_pcs": 25},
    ])
    orders = [
        _order(output=5000, name="Budi", uid="u-budi", email="budi@x.com"),
        _order(output=5000, name="Andi", uid="u-andi", email="andi@x.com"),
    ]
    r = compute_operator_payroll(orders, settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    by_id = {p["operator_user_id"]: p for p in r["people"]}
    assert by_id["u-budi"]["base_wage"] == 100000
    assert by_id["u-andi"]["base_wage"] == 125000


def test_bonus_once_per_period():
    assert compute_bonus(4999, {"target_qty": 5000, "amount": 10000, "mode": BONUS_ONCE}) == 0
    assert compute_bonus(5000, {"target_qty": 5000, "amount": 10000, "mode": BONUS_ONCE}) == 10000
    assert compute_bonus(10000, {"target_qty": 5000, "amount": 10000, "mode": BONUS_ONCE}) == 10000


def test_bonus_per_target_block():
    cfg = {"target_qty": 5000, "amount": 10000, "mode": BONUS_BLOCK}
    assert compute_bonus(4999, cfg) == 0
    assert compute_bonus(5000, cfg) == 10000
    assert compute_bonus(9999, cfg) == 10000
    assert compute_bonus(10000, cfg) == 20000


def test_bonus_once_applied_per_operator_not_combined():
    settings = _wage(
        [
            {"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": 20},
            {"operator_user_id": "u-andi", "operator_email": "andi@x.com", "default_rate_per_pcs": 20},
        ],
        bonus={"target_qty": 5000, "amount": 10000, "mode": BONUS_ONCE, "effective_from": "2026-08-01"},
    )
    orders = [
        _order(output=5000, uid="u-budi", email="budi@x.com", name="Budi"),
        _order(output=4000, uid="u-andi", email="andi@x.com", name="Andi"),
    ]
    r = compute_operator_payroll(orders, settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    by_id = {p["operator_user_id"]: p for p in r["people"]}
    assert by_id["u-budi"]["bonus"] == 10000
    assert by_id["u-andi"]["bonus"] == 0
    assert by_id["u-budi"]["total_upah"] == 5000 * 20 + 10000
    assert by_id["u-andi"]["total_upah"] == 4000 * 20


def test_bonus_not_lifetime_outside_period():
    settings = _wage(
        [{"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": 20}],
        bonus={"target_qty": 5000, "amount": 10000, "mode": BONUS_ONCE, "effective_from": "2026-08-01"},
    )
    orders = [
        _order(output=5000, day="2026-07-10", uid="u-budi"),
        _order(output=1000, day="2026-08-10", uid="u-budi", oid="po-budi-aug"),
    ]
    r = compute_operator_payroll(orders, settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert r["people"][0]["qty"] == 1000
    assert r["people"][0]["bonus"] == 0


def test_product_addition_on_top_of_base_not_replacement():
    settings = _wage(
        [{"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": 20}],
        additions=[{
            "accurate_item_id": "104",
            "master_item_id": "m-104",
            "item_code": "PC-16",
            "item_name": "Product A",
            "additional_rate_per_pcs": 5,
            "effective_from": "2026-08-01",
            "active": True,
        }],
    )
    r = compute_operator_payroll(
        [_order(output=2000, item_name="Product A")],
        settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31),
    )
    row = r["rows"][0]
    assert row["base_wage"] == 40000
    assert row["product_addition"] == 10000
    assert row["total_upah"] == 50000
    other = compute_operator_payroll(
        [_order(output=2000, aid="999", code="PB", item_name="Product B", oid="po-b")],
        settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31),
    )
    assert other["rows"][0]["product_addition"] == 0
    assert other["rows"][0]["base_wage"] == 40000


def test_example_formula_base_addition_bonus():
    settings = _wage(
        [{"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": 20}],
        bonus={"target_qty": 5000, "amount": 10000, "mode": BONUS_ONCE, "effective_from": "2026-08-01"},
        additions=[{
            "accurate_item_id": "104",
            "master_item_id": "m-104",
            "additional_rate_per_pcs": 5,
            "effective_from": "2026-08-01",
            "active": True,
        }],
    )
    r = compute_operator_payroll([_order(output=5000)], settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    p = r["people"][0]
    assert p["base_wage"] == 100000
    assert p["product_addition"] == 25000
    assert p["bonus"] == 10000
    assert p["total_upah"] == 135000


def test_legacy_override_not_converted_to_addition():
    settings = snapshot_on_save({}, {
        "operators": [{"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": 20}],
        "overrides": [{
            "accurate_item_id": "104",
            "master_item_id": "m-104",
            "rate_per_pcs": 5,
            "active": True,
        }],
        "product_additions": [],
    }, {"email": "admin"}, "2026-08-01T00:00:00+07:00", today=date(2026, 8, 1))
    r = compute_operator_payroll([_order(output=2000)], settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    # Legacy override is absolute 5, not base 20 + 5
    assert r["rows"][0]["base_wage"] == 10000
    assert r["rows"][0]["product_addition"] == 0
    assert r["rows"][0]["rate_source"] == "product_override"


def test_historical_effective_from_august_september():
    first = snapshot_on_save({}, {
        "operators": [{"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": 20, "effective_from": "2026-08-01"}],
    }, {"email": "admin"}, "2026-08-01T00:00:00+07:00", today=date(2026, 8, 1))
    later = snapshot_on_save(first, {
        "operators": [{"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": 25, "effective_from": "2026-09-01"}],
    }, {"email": "admin"}, "2026-08-26T00:00:00+07:00", today=date(2026, 8, 26))
    op = {"operator_user_id": "u-budi", "operator_email": "budi@x.com"}
    ident = {"accurate_item_id": "104"}
    assert effective_rate_as_of(ident, date(2026, 8, 15), later, op) == 20
    assert effective_rate_as_of(ident, date(2026, 9, 15), later, op) == 25
    aug = compute_operator_payroll([_order(day="2026-08-10")], later, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    sep = compute_operator_payroll([_order(day="2026-09-10")], later, date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
    assert aug["rows"][0]["base_wage"] == 5000 * 20
    assert sep["rows"][0]["base_wage"] == 5000 * 25
    newest = snapshot_on_save(later, {
        "operators": [{"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": 30, "effective_from": "2026-09-01"}],
    }, {"email": "admin"}, "2026-09-05T00:00:00+07:00", today=date(2026, 9, 5))
    aug2 = compute_operator_payroll([_order(day="2026-08-10")], newest, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert aug2["rows"][0]["base_wage"] == 5000 * 20


def test_bonus_and_addition_historical_safe():
    first = snapshot_on_save({}, {
        "operators": [{"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": 20, "effective_from": "2026-08-01"}],
        "bonus": {"target_qty": 5000, "amount": 10000, "mode": BONUS_ONCE, "effective_from": "2026-08-01"},
        "product_additions": [{"accurate_item_id": "104", "master_item_id": "m-104", "additional_rate_per_pcs": 5, "effective_from": "2026-08-01", "active": True}],
    }, {"email": "admin"}, "2026-08-01T00:00:00+07:00", today=date(2026, 8, 1))
    later = snapshot_on_save(first, {
        "operators": [{"operator_user_id": "u-budi", "operator_email": "budi@x.com", "default_rate_per_pcs": 20, "effective_from": "2026-08-01"}],
        "bonus": {"target_qty": 5000, "amount": 50000, "mode": BONUS_BLOCK, "effective_from": "2026-09-01"},
        "product_additions": [{"accurate_item_id": "104", "master_item_id": "m-104", "additional_rate_per_pcs": 9, "effective_from": "2026-09-01", "active": True}],
    }, {"email": "admin"}, "2026-08-26T00:00:00+07:00", today=date(2026, 8, 26))
    aug = compute_operator_payroll([_order(output=5000, day="2026-08-10")], later, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert aug["people"][0]["bonus"] == 10000
    assert aug["people"][0]["product_addition"] == 25000
    sep = compute_operator_payroll([_order(output=10000, day="2026-09-10")], later, date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
    assert sep["people"][0]["bonus"] == 100000
    assert sep["people"][0]["product_addition"] == 90000


def test_validate_rejects_negative_and_bad_bonus():
    assert validate_wage_settings({"operators": [{"operator_user_id": "u-budi", "default_rate_per_pcs": -1}]})
    assert validate_wage_settings({"bonus": {"target_qty": 0, "amount": 10}})
    assert validate_wage_settings({"bonus": {"target_qty": 100, "amount": -1}})
    assert validate_wage_settings({"product_additions": [{"accurate_item_id": "104", "additional_rate_per_pcs": -2}]})
    assert validate_wage_settings({"operators": [{"operator_user_id": "u-budi", "default_rate_per_pcs": 20}]}) is None


def test_no_hardcoded_production_defaults_in_empty_settings():
    settings = snapshot_on_save({}, {"operators": [], "overrides": []}, {"email": "admin"}, "2026-08-01T00:00:00+07:00", today=date(2026, 8, 1))
    assert settings.get("default_rate_per_pcs") is None
    assert settings.get("bonus") is None
    assert settings.get("product_additions") == []
    r = compute_operator_payroll([_order()], settings, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))
    assert r["rows"][0]["rate_unset"] is True
    assert r["people"][0]["bonus"] == 0
