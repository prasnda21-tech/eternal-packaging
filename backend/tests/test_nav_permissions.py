"""Unit tests for sidebar nav permission derivation (no live API required)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import (
    ALL_PERMISSIONS,
    NAV_ALWAYS_VISIBLE,
    NAV_CONFIGURED,
    NAV_MENU_ITEMS,
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
        "nav.sales.sales_order",
        "nav.sales.delivery_order",
        "nav.sales.sales_invoice",
        "nav.sales.sales_receipt",
        "nav.sales.import_po",
        "nav.sales.sales_activity",
        "nav.sales.product_requests",
        "product_request.create",
        "product_request.read_own_draft",
        "product_request.read_all",
        "sales.activity.view",
        "sales.activity.create",
        "sales.outlet.create",
        "sales.ownership.manage",
        "sales.pool.manage",
        "sales.activity.import",
        "sales.activity.activate_migration",
        "settings.sales_activity.view",
        "settings.sales_activity.manage",
        "nav.production_schedule",
        "nav.production",
        "nav.delivery_schedule",
        "nav.delivery_reschedule",
        "nav.purchasing",
        "nav.inventory",
        "nav.reports",
        "nav.reports.analytics_output",
        "nav.reports.shipment_status",
        "nav.reports.status_po",
        "nav.reports.gaji_operator",
        "nav.reports.order_prediction",
        "nav.settings",
        "nav.tasks",
        "nav.tasks.designer",
        "nav.tasks.kepala_produksi",
        "nav.tasks.screener",
        "nav.tasks.helper",
        "nav.tasks.operator",
        "nav.tasks.packing",
        "nav.tasks.surat_jalan",
        "nav.tasks.loading",
        "nav.tasks.delivery",
        "nav.route_optimizer",
        "nav.ledger",
        "nav.ledger.accounts",
        "nav.ledger.accounts_receivable",
        "accounts_receivable.view",
        "nav.ledger.accounts_payable",
        "accounts_payable.view",
        "ledger.accounts.view",
        "ledger.accounts.manage",
        "accounting.period.view",
        "accounting.period.close",
        "accounting.period.reopen",
        "nav.ledger.accounting_periods",
        NAV_CONFIGURED,
        "nav.inventory.master_items",
        "nav.inventory.current_stock",
        "nav.purchasing.receipts",
        "nav.purchasing.requests",
        "nav.purchasing.orders",
        "nav.purchasing.invoices",
        "nav.purchasing.payments",
        "purchase_requests.view",
        "purchase_orders.view",
        "purchase_invoices.view",
        "purchase_payments.view",
        "settings.hpp.view",
        "settings.hpp.manage",
        "delivery.reschedule",
        "delivery.reschedule.set_date",
        "delivery.schedule.view",
        "delivery.location.manage",
        "delivery.route.optimize",
        "delivery.route.edit",
        "delivery.route.confirm",
        "delivery.vehicle.manage",
        "settings.depot.view",
        "settings.depot.manage",
        "nav.driver_route",
        "delivery_note.view_file",
        "work_panel.view",
        "settings.work_panel.view",
        "settings.work_panel.manage",
        "reports.gaji_operator.view",
        "reports.order_prediction.view",
        "settings.operator_wage.view",
        "settings.operator_wage.manage",
        "employee_admin.view_own",
        "employee_admin.submit",
        "employee_admin.approve",
        "employee_admin.view_all",
        "employee_admin.settings",
        "employee_admin.document_view",
        "nav.employee_administration",
        "nav.employee_administration.mine",
        "nav.employee_administration.create",
        "nav.employee_administration.approve",
        "nav.employee_administration.archive",
        "hr.employee.view",
        "hr.employee.manage",
        "payroll.view",
        "payroll.calculate",
        "payroll.review",
        "payroll.approve",
        "payroll.finalize",
        "payroll.reopen",
        "payroll.settings",
        "payroll.payslip.view_own",
        "payroll.payslip.view_all",
        "payroll.adjustment",
        "nav.hr_payroll",
        "nav.hr_payroll.employees",
        "nav.hr_payroll.employment",
        "nav.hr_payroll.payroll",
        "nav.hr_payroll.payslips",
        "nav.hr_payroll.components",
        "nav.hr_payroll.history",
    ):
        assert key in ALL_PERMISSIONS, key


def test_sales_receipt_nav_is_registered_like_other_sales_menus():
    assert "nav.sales.sales_receipt" in ALL_PERMISSIONS
    assert "nav.sales.sales_receipt" in NAV_PERMISSION_KEYS
    labels = {k: lbl for k, lbl, _p in NAV_MENU_ITEMS}
    assert labels["nav.sales.sales_receipt"] == "Penerimaan Penjualan"
    for key in (
        "nav.sales",
        "nav.sales.sales_order",
        "nav.sales.delivery_order",
        "nav.sales.sales_invoice",
        "nav.sales.import_po",
    ):
        assert key in ALL_PERMISSIONS, key
        assert key in NAV_PERMISSION_KEYS, key
    assert "nav.sales.sales_receipt" not in NAV_ALWAYS_VISIBLE
    assert "not.a.real.permission" not in ALL_PERMISSIONS


def test_non_admin_roles_do_not_auto_gain_sales_receipt_nav():
    for role in ("helper", "operator", "screener", "designer", "driver", "crm", "head_production", "head_operations"):
        defaults = set(ROLE_DEFAULT_PERMISSIONS[role])
        derived = with_nav_defaults(defaults)
        assert "nav.sales.sales_receipt" not in defaults, role
        assert "nav.sales.sales_receipt" not in derived, role


def test_non_admin_roles_do_not_auto_gain_sales_activity_permissions():
    new_keys = {
        "sales.activity.view",
        "sales.activity.create",
        "sales.outlet.create",
        "sales.ownership.manage",
        "sales.pool.manage",
        "sales.activity.import",
        "sales.activity.activate_migration",
        "settings.sales_activity.view",
        "settings.sales_activity.manage",
        "nav.sales.sales_activity",
    }
    for role in ("helper", "operator", "screener", "designer", "driver", "crm", "head_production", "head_operations"):
        defaults = set(ROLE_DEFAULT_PERMISSIONS[role])
        derived = with_nav_defaults(defaults)
        for key in new_keys:
            assert key not in defaults, (role, key)
            assert key not in derived, (role, key)


def test_non_admin_roles_do_not_auto_gain_route_optimizer_permissions():
    new_keys = {
        "route_optimizer.view",
        "route_optimizer.create",
        "route_optimizer.edit_own",
        "route_optimizer.optimize",
        "route_optimizer.manage",
        "settings.route_optimizer.view",
        "settings.route_optimizer.manage",
        "nav.route_optimizer",
    }
    for role in ("helper", "operator", "screener", "designer", "driver", "crm", "head_production", "head_operations"):
        defaults = set(ROLE_DEFAULT_PERMISSIONS[role])
        derived = with_nav_defaults(defaults)
        for key in new_keys:
            assert key not in defaults, (role, key)
            assert key not in derived, (role, key)


def test_admin_still_has_all_including_nav():
    admin = set(ROLE_DEFAULT_PERMISSIONS["admin"])
    for key in NAV_PERMISSION_KEYS:
        assert key in admin, key
    assert NAV_CONFIGURED in admin


def test_helper_keeps_schedule_and_always_visible_menus():
    helper = set(ROLE_DEFAULT_PERMISSIONS["helper"])
    assert "nav.production_schedule" in helper
    assert "nav.production" in helper
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
    assert "nav.sales.sales_receipt" not in helper
    assert "nav.tasks" in helper
    assert "nav.tasks.helper" in helper
    assert "nav.tasks.packing" in helper
    assert "nav.tasks.operator" not in helper
    assert "nav.tasks.designer" not in helper
    assert "nav.tasks.kepala_produksi" not in helper


def test_head_production_gets_dashboard_nav():
    hp = set(ROLE_DEFAULT_PERMISSIONS["head_production"])
    assert "nav.dashboard" in hp
    assert "nav.production_schedule" in hp
    assert "nav.production" in hp
    assert "nav.delivery_schedule" in hp
    assert "nav.delivery_reschedule" in hp
    assert "current_stock.view" in hp
    assert "purchase_receipts.view" in hp
    assert "purchase_receipts.sync" in hp
    assert "nav.purchasing.receipts" in hp
    assert "nav.inventory.current_stock" in hp
    assert "nav.tasks.kepala_produksi" in hp
    assert "nav.tasks.designer" not in hp
    assert "design.upload" not in hp
    assert "schedule.edit" in hp


def test_inventory_valuation_is_admin_and_holding_only_by_default():
    for role in ("admin", "holding_team"):
        permissions = set(ROLE_DEFAULT_PERMISSIONS[role])
        assert "inventory_valuation.view" in permissions
        assert "inventory_valuation.export" in permissions
        assert "inventory_valuation.manage_opening" in permissions
        assert "nav.inventory.inventory_value" in permissions
    for role in (
        "head_production",
        "head_operations",
        "operator",
        "helper",
        "screener",
        "designer",
        "driver",
        "crm",
        "direktur_utama",
        "sales_eternal",
    ):
        permissions = set(ROLE_DEFAULT_PERMISSIONS[role])
        assert "inventory_valuation.view" not in permissions, role
        assert "inventory_valuation.export" not in permissions, role
        assert "inventory_valuation.manage_opening" not in permissions, role
        assert "nav.inventory.inventory_value" not in permissions, role


def test_designer_gets_designer_task_nav_not_scheduling():
    designer = set(ROLE_DEFAULT_PERMISSIONS["designer"])
    assert "design.upload" in designer
    assert "nav.tasks" in designer
    assert "nav.tasks.designer" in designer
    assert "nav.tasks.kepala_produksi" not in designer
    assert "schedule.edit" not in designer


def test_legacy_stored_role_without_nav_keys_derives_defaults():
    stored = {"po.view", "schedule.view", "helper.prepare"}
    derived = with_nav_defaults(stored)
    assert "nav.production_schedule" in derived
    assert "nav.production" in derived
    assert "nav.sales" in derived
    assert "nav.dashboard" not in derived
    assert "helper.prepare" in derived
    assert "nav.tasks" in derived
    assert "nav.tasks.helper" in derived
    assert "nav.tasks.designer" not in derived
    assert "nav.tasks.kepala_produksi" not in derived


def test_derived_nav_from_design_upload_and_schedule_edit():
    designer_nav = derived_nav_permissions({"design.upload", "po.view"})
    assert "nav.tasks.designer" in designer_nav
    assert "nav.tasks.kepala_produksi" not in designer_nav
    sched = derived_nav_permissions({"schedule.edit", "po.view"})
    assert "nav.tasks.kepala_produksi" in sched
    assert "nav.tasks.designer" not in sched


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


def test_driver_has_work_panel_view():
    driver = set(ROLE_DEFAULT_PERMISSIONS["driver"])
    assert "work_panel.view" in driver
    assert "loading.update" in driver
    assert "delivery_note.view_file" in driver
    assert "delivery_note.manage" not in driver
    assert "delivery.update" in driver
    assert "delivery.reschedule" in driver
    assert "delivery.reschedule.set_date" not in driver
    assert "nav.driver_route" not in driver
    assert "delivery.route.optimize" not in driver
    assert "delivery.route.edit" not in driver
    assert "delivery.route.confirm" not in driver


def test_screener_has_work_panel_view():
    screener = set(ROLE_DEFAULT_PERMISSIONS["screener"])
    assert "work_panel.view" in screener
    assert "screen.update" in screener


def test_loading_nav_derived_from_loading_update():
    derived = derived_nav_permissions({"loading.update"})
    assert "nav.tasks.loading" in derived
    assert "nav.tasks" in derived
    assert "delivery.schedule.create" not in ALL_PERMISSIONS
    assert "delivery.schedule.publish" not in ALL_PERMISSIONS
