"""Legacy package grants stay inactive until a manual save after the cutover."""
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

from permissions import effective_permissions
from services.manual_grant_cutover import (
    CutoverConflict,
    apply_cutover,
    load_targets,
    plan_role_document,
    plan_user_document,
    require_explicit_database,
    rollback_cutover,
    summarize_plans,
)
from services.purchasing_inventory_access import (
    PACKAGE_PERMISSIONS,
    filter_package_permissions,
    reconcile_manual_package_grant,
    reconcile_manual_user_override,
)

BACKEND = Path(__file__).resolve().parents[1]
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
CUTOVER_DB = "eternal_manual_grant_cutover_691"


def test_cutover_drops_ineffective_package_keys_and_keeps_effective_ones():
    plan = plan_role_document({
        "role": "crm",
        "permissions": ["po.view", "purchase_orders.view", "purchase_hpp.view", "paint.view"],
        "package_production_confirmed_keys": ["purchase_orders.view"],
    })
    assert plan["action"] == "normalize"
    assert plan["removed_package_keys"] == ["purchase_hpp.view", "purchase_orders.view"]
    assert plan["kept_package_keys"] == []
    assert plan["kept_opt_in_keys"] == ["paint.view"]
    assert plan["removed_opt_in_keys"] == []
    assert plan["ignored_confirmation_keys"] == ["purchase_orders.view"]
    assert plan["next_permissions"] == ["paint.view", "po.view"]
    assert plan["next_confirmed_keys"] == []

    filtered = filter_package_permissions(
        plan["next_permissions"],
        {"role": "crm"},
        "production",
        "mongodb://prod:27017",
        role_normalized=True,
        role_permission_keys=plan["next_permissions"],
        role_confirmed_keys=[],
    )
    assert "purchase_orders.view" not in filtered
    assert "purchase_hpp.view" not in filtered
    assert "paint.view" in filtered
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
            "paint.view": True,
            "po.view": False,
        },
        "package_production_confirmed_keys": ["purchase_orders.view"],
    })
    assert plan["removed_package_keys"] == ["purchase_hpp.view", "purchase_orders.view"]
    assert "purchase_orders.view" not in plan["next_override"]
    assert "purchase_hpp.view" not in plan["next_override"]
    assert plan["next_override"]["paint.view"] is True
    assert plan["kept_opt_in_keys"] == ["paint.view"]
    assert plan["next_override"]["po.view"] is False
    assert plan["ignored_confirmation_keys"] == ["purchase_orders.view"]


def test_holding_user_override_keeps_package_keys_without_a_stamp():
    plan = plan_user_document({
        "email": "holding@example.test",
        "role": "holding_team",
        "permissions_override": {"purchase_hpp.view": True, "paint.view": True},
        "package_production_confirmed_keys": [],
    })
    assert plan["removed_package_keys"] == []
    assert plan["kept_package_keys"] == ["purchase_hpp.view"]
    assert plan["next_override"]["purchase_hpp.view"] is True
    assert plan["kept_opt_in_keys"] == ["paint.view"]


def test_reapply_keeps_a_manual_grant_added_after_normalization():
    migrated = plan_role_document({
        "role": "crm",
        "manual_grant_normalized": True,
        "permissions": ["po.view", "purchase_hpp.view", "paint.view"],
        "package_production_confirmed_keys": ["purchase_hpp.view"],
    })
    assert migrated["action"] == "skip"
    assert migrated["next_permissions"] == ["po.view", "purchase_hpp.view", "paint.view"]
    assert "purchase_hpp.view" in migrated["kept_package_keys"]


def test_effective_access_matches_the_production_baseline():
    role_doc = {
        "role": "crm",
        "permissions": ["po.view", "purchase_orders.view", "paint.view"],
        "package_production_confirmed_keys": ["purchase_orders.view"],
    }
    user = {
        "email": "crm@example.test",
        "role": "crm",
        "permissions_override": {"purchase_hpp.view": True},
        "package_production_confirmed_keys": ["purchase_hpp.view"],
    }
    before = effective_permissions("crm", user["permissions_override"], {"crm": role_doc["permissions"]})
    assert {"purchase_hpp.view", "purchase_orders.view"} <= set(before)
    baseline = {key for key in before if key not in PACKAGE_PERMISSIONS}
    role_plan = plan_role_document(role_doc)
    user_plan = plan_user_document(user)
    after = effective_permissions(
        "crm",
        user_plan["next_override"],
        {"crm": role_plan["next_permissions"]},
    )
    after_filtered = filter_package_permissions(
        after,
        {"role": "crm", "permissions_override": user_plan["next_override"]},
        "production",
        "mongodb://prod:27017",
        role_normalized=True,
        user_normalized=True,
        role_permission_keys=role_plan["next_permissions"],
        user_override=user_plan["next_override"],
    )
    assert set(after_filtered) == baseline
    assert "paint.view" in after_filtered
    assert "po.view" in after_filtered
    assert "purchase_orders.view" not in role_plan["next_permissions"]
    holding = plan_role_document({
        "role": "holding_team",
        "permissions": ["purchase_hpp.view", "po.view"],
    })
    holding_after = filter_package_permissions(
        holding["next_permissions"],
        {"role": "holding_team"},
        "production",
        "mongodb://prod:27017",
    )
    assert "purchase_hpp.view" in holding_after


def test_production_apply_requires_the_named_database_and_ignores_app_env():
    with pytest.raises(SystemExit, match="requires --database"):
        require_explicit_database(True, "", "eternal_dummy_cutover")
    with pytest.raises(SystemExit, match="does not match"):
        require_explicit_database(True, "other-db", "eternal_dummy_cutover")
    require_explicit_database(True, "eternal_dummy_cutover", "eternal_dummy_cutover")
    require_explicit_database(False, "", "eternal_dummy_cutover")


def test_apply_guard_exits_before_a_production_connection():
    env = os.environ.copy()
    env["APP_ENV"] = "production"
    env["DB_NAME"] = "eternal_dummy_cutover"
    env["MONGO_URL"] = "mongodb://203.0.113.10:27017/?replicaSet=do_not_connect"
    script = BACKEND / "scripts" / "dry_run_manual_grant_cutover.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--apply", "--database", "other-db"],
        cwd=str(BACKEND),
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "REFUSED" in combined
    assert "203.0.113.10" not in combined
    assert "do_not_connect" not in combined


def _loopback_mongo() -> str:
    url = os.environ.get("MONGO_URL") or "mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs"
    host = urlparse(url).hostname
    if host not in LOOPBACK:
        pytest.skip("cutover write test refuses a non-loopback Mongo host")
    return url


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_isolated_cutover_apply_reapply_conflict_and_rollback():
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(_loopback_mongo(), serverSelectionTimeoutMS=8000)
    db = client[CUTOVER_DB]
    await client.drop_database(CUTOVER_DB)
    try:
        crm = {
            "role": "crm",
            "permissions": ["po.view", "purchase_orders.view", "paint.view"],
            "package_production_confirmed_keys": ["purchase_orders.view"],
            "branch_ids": ["ygy"],
        }
        holding = {
            "role": "holding_team",
            "permissions": ["purchase_hpp.view", "po.view"],
            "package_production_confirmed_keys": [],
        }
        user = {
            "email": "clerk@example.test",
            "id": "clerk-1",
            "role": "crm",
            "permissions_override": {"purchase_hpp.view": True, "paint.view": True},
            "package_production_confirmed_keys": ["purchase_hpp.view"],
            "branch_ids": ["ygy"],
            "password_hash": "must-not-leak",
        }
        await db.role_permissions.insert_many([crm, holding])
        await db.users.insert_one(user)
        _roles, _users, plans = await load_targets(db)
        summary = summarize_plans(plans)
        assert summary["normalize"] == 3
        assert summary["opt_in_kept"] >= 2
        stored_before = (await db.role_permissions.find_one({"role": "crm"}))["permissions"]
        assert stored_before == ["po.view", "purchase_orders.view", "paint.view"]
        assert "purchase_orders.view" in stored_before

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        await db.role_permissions.update_one({"role": "crm"}, {"$set": {"permissions": stored_before + ["po.edit"]}})
        with pytest.raises(CutoverConflict) as conflict:
            await apply_cutover(db, plans, stamp=stamp)
        assert conflict.value.wrote is False
        assert await db.manual_grant_cutover_backups.count_documents({}) == 0
        assert "po.edit" in (await db.role_permissions.find_one({"role": "crm"}))["permissions"]

        await db.role_permissions.update_one(
            {"role": "crm"},
            {"$set": {"permissions": ["po.view", "purchase_orders.view", "paint.view"]}},
        )
        _roles, _users, plans = await load_targets(db)
        result = await apply_cutover(db, plans, stamp=stamp)
        assert result["applied"] == 3
        crm_after = await db.role_permissions.find_one({"role": "crm"}, {"_id": 0})
        assert "purchase_orders.view" not in crm_after["permissions"]
        assert "paint.view" in crm_after["permissions"]
        assert "po.view" in crm_after["permissions"]
        assert crm_after["manual_grant_normalized"] is True
        assert crm_after["branch_ids"] == ["ygy"]
        holding_after = await db.role_permissions.find_one({"role": "holding_team"}, {"_id": 0})
        assert "purchase_hpp.view" in holding_after["permissions"]
        user_after = await db.users.find_one({"email": "clerk@example.test"}, {"_id": 0, "password_hash": 0})
        assert "purchase_hpp.view" not in user_after["permissions_override"]
        assert user_after["permissions_override"]["paint.view"] is True
        assert user_after["branch_ids"] == ["ygy"]
        assert "password_hash" not in user_after

        await db.role_permissions.update_one(
            {"role": "crm"},
            {"$set": {"permissions": crm_after["permissions"] + ["purchase_hpp.view"]}},
        )
        _roles, _users, again = await load_targets(db)
        second = await apply_cutover(db, again, stamp=stamp + "b")
        assert second["applied"] == 0
        kept = (await db.role_permissions.find_one({"role": "crm"}))["permissions"]
        assert "purchase_hpp.view" in kept

        with pytest.raises(CutoverConflict) as blocked:
            await rollback_cutover(db, apply=True)
        assert blocked.value.wrote is False
        assert "purchase_hpp.view" in (await db.role_permissions.find_one({"role": "crm"}))["permissions"]

        await db.role_permissions.update_one(
            {"role": "crm"},
            {"$set": {"permissions": [key for key in kept if key != "purchase_hpp.view"]}},
        )
        preview = await rollback_cutover(db, apply=False)
        assert preview["mode"] == "dry-run"
        assert preview["restored"] == 3
        assert "purchase_orders.view" not in (await db.role_permissions.find_one({"role": "crm"}))["permissions"]
        restored = await rollback_cutover(db, apply=True)
        assert restored["restored"] == 3
        rolled = await db.role_permissions.find_one({"role": "crm"}, {"_id": 0})
        assert "purchase_orders.view" in rolled["permissions"]
        assert rolled.get("manual_grant_normalized") is not True
        assert "paint.view" in rolled["permissions"]
        user_rolled = await db.users.find_one({"email": "clerk@example.test"})
        assert user_rolled["permissions_override"]["purchase_hpp.view"] is True
        assert user_rolled["password_hash"] == "must-not-leak"
        assert user_rolled["branch_ids"] == ["ygy"]
    finally:
        await client.drop_database(CUTOVER_DB)
        client.close()


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
