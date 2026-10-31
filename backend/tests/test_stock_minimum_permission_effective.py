"""Effective permission matrix for stock_minimum.manage (isolated, no DB)."""
from permissions import (
    DIRECTOR_FULL_ACCESS_ROLE,
    DIRECTOR_MUST_OPT_IN_PERMISSIONS,
    OPT_IN_ONLY_PERMISSIONS,
    ROLE_DEFAULT_PERMISSIONS,
    director_full_permission_set,
    effective_permissions,
    merge_director_must_opt_in,
)

PERM = "stock_minimum.manage"
UPDATE = "current_stock.update"


def test_not_implied_by_stock_update_and_not_in_admin_defaults():
    assert PERM in OPT_IN_ONLY_PERMISSIONS
    assert PERM not in DIRECTOR_MUST_OPT_IN_PERMISSIONS
    assert PERM not in set(ROLE_DEFAULT_PERMISSIONS.get("admin", []))
    # current_stock.update holders via admin defaults still lack manage.
    admin = set(ROLE_DEFAULT_PERMISSIONS.get("admin", []))
    assert UPDATE in admin
    assert PERM not in admin


def test_director_full_access_includes_manage():
    full = director_full_permission_set()
    assert PERM in full
    merged = merge_director_must_opt_in(
        full,
        DIRECTOR_FULL_ACCESS_ROLE,
        {},
        None,
    )
    assert PERM in merged
    # Explicit deny still honored for director must-opt-in keys only;
    # stock_minimum is NOT must-opt-in, so deny via override works through
    # merge only for must-opt-in — document effective: director has it.
    assert PERM not in DIRECTOR_MUST_OPT_IN_PERMISSIONS


def test_stock_update_only_user_lacks_manage():
    eff = effective_permissions("operator", {UPDATE: True}, None)
    assert UPDATE in eff
    assert PERM not in eff


def test_minimum_only_user_has_manage_not_update():
    eff = effective_permissions("operator", {PERM: True}, None)
    assert PERM in eff
    assert UPDATE not in eff
    assert PERM in effective_permissions("helper", {PERM: True}, None)


def test_user_without_access_denied():
    assert PERM not in effective_permissions("crm", {}, None)
    assert PERM not in effective_permissions("operator", {}, None)
    assert PERM not in effective_permissions("helper", {UPDATE: True}, None)
