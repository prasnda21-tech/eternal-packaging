"""Legacy package grants stay inactive until the one-time cutover removes them."""
from services.manual_grant_cutover import plan_role_document, plan_user_document, summarize_plans
from services.purchasing_inventory_access import (
    filter_package_permissions,
    reconcile_manual_package_grant,
    reconcile_manual_user_override,
)


def test_cutover_drops_ineffective_package_keys_and_keeps_effective_ones():
    plan = plan_role_document({
        "role": "crm",
        "permissions": ["po.view", "purchase_orders.view", "purchase_hpp.view"],
        "package_production_confirmed_keys": ["purchase_orders.view"],
    })
    assert plan["action"] == "normalize"
    assert plan["removed_package_keys"] == ["purchase_hpp.view"]
    assert plan["kept_package_keys"] == ["purchase_orders.view"]
    assert plan["next_permissions"] == ["po.view", "purchase_orders.view"]
    assert plan["next_confirmed_keys"] == ["purchase_orders.view"]

    filtered = filter_package_permissions(
        plan["next_permissions"],
        {"role": "crm"},
        "production",
        "mongodb://prod:27017",
        role_normalized=True,
        role_permission_keys=plan["next_permissions"],
        role_confirmed_keys=[],
    )
    assert "purchase_orders.view" in filtered
    assert "purchase_hpp.view" not in filtered
    assert "po.view" in filtered


def test_cutover_does_not_activate_an_unconfirmed_grant_before_it_runs():
    stored = ["po.view", "purchase_hpp.view"]
    before = filter_package_permissions(
        stored,
        {"role": "head_production"},
        "production",
        "mongodb://prod:27017",
        role_confirmed_keys=[],
        role_permission_keys=stored,
        role_normalized=False,
    )
    assert "purchase_hpp.view" not in before
    assert "po.view" in before


def test_holding_and_director_grants_stay_checked():
    holding = plan_role_document({
        "role": "holding_team",
        "permissions": ["purchase_hpp.view", "po.view"],
        "package_production_confirmed_keys": [],
    })
    director = plan_role_document({
        "role": "direktur_utama",
        "permissions": ["current_stock.view"],
        "package_production_confirmed_keys": [],
    })
    assert holding["removed_package_keys"] == []
    assert holding["kept_package_keys"] == ["purchase_hpp.view"]
    assert director["removed_package_keys"] == []
    assert director["kept_package_keys"] == ["current_stock.view"]


def test_second_cutover_skips_normalized_documents():
    role = plan_role_document({
        "role": "crm",
        "manual_grant_normalized": True,
        "permissions": ["purchase_orders.view"],
        "package_production_confirmed_keys": ["purchase_orders.view"],
    })
    user = plan_user_document({
        "email": "crm@example.test",
        "role": "crm",
        "manual_grant_normalized": True,
        "permissions_override": {"purchase_hpp.view": True, "po.view": False},
    })
    assert role["action"] == "skip"
    assert user["action"] == "skip"
    summary = summarize_plans([role, user])
    assert summary["normalize"] == 0
    assert summary["skip"] == 2
    assert summary["removals"] == []


def test_user_cutover_removes_only_ineffective_override_true():
    plan = plan_user_document({
        "email": "clerk@example.test",
        "role": "crm",
        "permissions_override": {
            "purchase_orders.view": True,
            "purchase_hpp.view": True,
            "po.view": False,
        },
        "package_production_confirmed_keys": ["purchase_orders.view"],
    })
    assert plan["removed_package_keys"] == ["purchase_hpp.view"]
    assert plan["next_override"]["purchase_orders.view"] is True
    assert "purchase_hpp.view" not in plan["next_override"]
    assert plan["next_override"]["po.view"] is False


def test_editor_save_activates_a_new_key_and_leaves_legacy_keys_off():
    cleaned, confirmed = reconcile_manual_package_grant(
        ["po.view", "purchase_hpp.view"],
        ["po.view", "purchase_hpp.view", "purchase_orders.view", "paint.view"],
        [],
        already_normalized=False,
    )
    assert "purchase_orders.view" in cleaned
    assert "paint.view" in cleaned
    assert "purchase_hpp.view" not in cleaned
    assert confirmed == ["purchase_orders.view"]

    again, confirmed_again = reconcile_manual_package_grant(
        cleaned,
        [key for key in cleaned if key != "purchase_orders.view"],
        confirmed,
        already_normalized=True,
    )
    assert "purchase_orders.view" not in again
    assert confirmed_again == []


def test_normalized_override_true_is_effective_and_false_still_denies():
    cleaned, confirmed = reconcile_manual_user_override(
        {},
        {"purchase_hpp.view": True, "po.view": False},
        [],
        already_normalized=False,
    )
    assert cleaned["purchase_hpp.view"] is True
    assert confirmed == ["purchase_hpp.view"]
    filtered = filter_package_permissions(
        {"purchase_hpp.view", "po.view", "current_stock.view"},
        {"role": "crm", "permissions_override": cleaned},
        "production",
        "mongodb://prod:27017",
        user_normalized=True,
        user_override=cleaned,
        role_normalized=True,
        role_permission_keys=["current_stock.view"],
    )
    assert "purchase_hpp.view" in filtered
    assert "current_stock.view" in filtered
    assert "po.view" in filtered


def test_super_admin_wildcard_does_not_gain_unstored_package_keys():
    wildcard = {"purchase_hpp.view", "purchase_orders.view", "po.view"}
    filtered = filter_package_permissions(
        wildcard,
        {"role": "admin", "is_super_admin": True},
        "production",
        "mongodb://prod:27017",
        role_normalized=True,
        role_permission_keys=["po.view"],
        role_confirmed_keys=["purchase_hpp.view"],
        user_normalized=False,
    )
    assert filtered == {"po.view"}
