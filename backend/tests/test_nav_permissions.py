"""Unit tests for sidebar nav permission derivation (no live API required)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import (
    ALL_PERMISSIONS,
    NAV_CONFIGURED,
    NAV_PERMISSION_KEYS,
    ROLE_DEFAULT_PERMISSIONS,
    derived_nav_permissions,
    effective_permissions,
    with_nav_defaults,
)


def test_catalog_includes_nav_keys():
    for key in (
        "nav.dashboard",
        "nav.sales",
        "nav.sales.import_po",
        "nav.production_schedule",
        "nav.purchasing",
        "nav.inventory",
        "nav.reports",
        "nav.reports.analytics_output",
        "nav.settings",
        NAV_CONFIGURED,
        "nav.inventory.master_items",
        "nav.inventory.current_stock",
        "nav.purchasing.receipts",
    ):
        assert key in ALL_PERMISSIONS, key


def test_admin_still_has_all_including_nav():
    admin = set(ROLE_DEFAULT_PERMISSIONS["admin"])
    for key in NAV_PERMISSION_KEYS:
        assert key in admin, key
    assert NAV_CONFIGURED in admin


def test_helper_keeps_schedule_and_always_visible_menus():
    helper = set(ROLE_DEFAULT_PERMISSIONS["helper"])
    assert "nav.production_schedule" in helper
    assert "nav.sales" in helper
    assert "nav.settings" in helper
    assert "nav.inventory" in helper
    assert "nav.inventory.master_items" in helper
    assert "nav.inventory.current_stock" not in helper
    assert "nav.dashboard" not in helper  # helper has no dashboard.production.view
    assert "master_items.view" not in helper
    assert "master_items.sync" not in helper
    assert "current_stock.view" not in helper
    assert "purchase_receipts.view" not in helper
    assert "purchase_receipts.sync" not in helper
    assert "nav.purchasing.receipts" not in helper


def test_head_production_gets_dashboard_nav():
    hp = set(ROLE_DEFAULT_PERMISSIONS["head_production"])
    assert "nav.dashboard" in hp
    assert "nav.production_schedule" in hp
    assert "current_stock.view" in hp
    assert "purchase_receipts.view" in hp
    assert "purchase_receipts.sync" in hp
    assert "nav.purchasing.receipts" in hp
    assert "nav.inventory.current_stock" in hp


def test_legacy_stored_role_without_nav_keys_derives_defaults():
    stored = {"po.view", "schedule.view", "helper.prepare"}
    derived = with_nav_defaults(stored)
    assert "nav.production_schedule" in derived
    assert "nav.sales" in derived
    assert "nav.dashboard" not in derived
    assert "helper.prepare" in derived


def test_legacy_without_schedule_does_not_gain_jadwal():
    stored = {"po.view"}
    derived = with_nav_defaults(stored)
    assert "nav.production_schedule" not in derived
    assert "nav.sales" in derived


def test_explicit_nav_config_is_not_rederived():
    stored = {"po.view", "schedule.view", "nav.dashboard"}
    derived = with_nav_defaults(stored)
    assert "nav.dashboard" in derived
    assert "nav.sales" not in derived  # missing nav keys are deny once configured
    assert "nav.production_schedule" not in derived


def test_sentinel_allows_deny_all_nav():
    stored = {"po.view", "schedule.view", NAV_CONFIGURED}
    derived = with_nav_defaults(stored)
    assert "nav.sales" not in derived
    assert "nav.production_schedule" not in derived


def test_user_override_can_deny_derived_nav():
    eff = effective_permissions("helper", {"nav.settings": False}, None)
    assert "nav.settings" not in eff
    assert "nav.production_schedule" in eff


def test_user_override_can_grant_nav():
    eff = effective_permissions("helper", {"nav.dashboard": True}, None)
    assert "nav.dashboard" in eff


def test_customized_stored_list_uses_override_then_derives():
    role_override = {"helper": ["po.view", "helper.prepare"]}
    eff = effective_permissions("helper", None, role_override)
    assert "helper.prepare" in eff
    assert "nav.sales" in eff
    assert "nav.production_schedule" not in eff


def test_derived_nav_from_dashboard_perm():
    nav = derived_nav_permissions({"dashboard.production.view", "po.view"})
    assert "nav.dashboard" in nav
    assert "nav.production_schedule" not in nav
