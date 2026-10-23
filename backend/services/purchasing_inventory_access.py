"""Production lock for recovered Purchasing + Inventory + HPP package.

LOCAL / preview / development / test (explicit APP_ENV only):
  Existing role permissions apply (admin keeps full package access).

All other runtimes (including APP_ENV unset/unknown, and production):
  - holding_team: package keys pass (unchanged)
  - direktur_utama: package keys pass (Owner full-access policy)
  - other roles: package keys are never effective, even when checked or
    stamped via ``package_production_confirmed_keys`` in Settings.
    Persisted grants remain stored; effective set strips them.

Mongo on 127.0.0.1/localhost alone NEVER implies local.
Sidebar, routes, and APIs all derive from filtered effective permissions —
  no separate soft-hide.

r2_go_live / r2_authority_active is a separate operational mutation gate
and is NOT used here as an RBAC feature flag.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Set
from urllib.parse import urlparse

from fastapi import HTTPException

HOLDING_TEAM_ROLE = "holding_team"
DIRECTOR_FULL_ACCESS_ROLE = "direktur_utama"
PACKAGE_DENIED_MSG = "Modul Pembelian/Persediaan/HPP hanya untuk HOLDING_TEAM di production."

LOCAL_ENVS = frozenset({"local", "development", "preview", "test"})
# Documented loopback hosts only — never used to grant local package access.
LOCAL_MONGO_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Capability + nav keys restored by PR495 recovery. Filtered out for
# non-HOLDING_TEAM / non-Direktur users when runtime is not LOCAL_ENVS,
# unless the key was explicitly confirmed for production package access.
PACKAGE_PERMISSIONS: frozenset[str] = frozenset({
    # Purchasing capabilities
    "purchase_requests.view",
    "purchase_requests.create",
    "purchase_orders.view",
    "purchase_orders.create",
    "purchase_orders.edit_branch",
    "purchase_invoices.view",
    "purchase_invoices.create",
    "purchase_invoices.edit",
    "purchase_payments.view",
    "purchase_payments.create",
    "purchase_receipts.view",
    "purchase_receipts.sync",
    "purchase_receipts.export",
    "purchase_receipts.receive",
    "purchase_hpp.view",
    # Inventory / R2 stock
    "master_items.view",
    "master_items.manage",
    "org.branches.view",
    "org.branches.manage",
    "org.warehouses.view",
    "org.warehouses.manage",
    "current_stock.view",
    "current_stock.update",
    "current_stock.import",
    "current_stock.export",
    "inventory_valuation.view",
    "inventory_valuation.export",
    "inventory_valuation.manage_opening",
    # Nav keys
    "nav.purchasing",
    "nav.purchasing.requests",
    "nav.purchasing.orders",
    "nav.purchasing.advances",
    "nav.purchasing.invoices",
    "nav.purchasing.payments",
    "nav.purchasing.receipts",
    "nav.purchasing.returns",
    "nav.purchasing.hpp",
    "nav.inventory",
    "nav.inventory.master_items",
    "nav.inventory.branches_warehouses",
    "nav.inventory.current_stock",
    "nav.inventory.inventory_value",
})

# Field on role_permissions / users docs — exact package keys reviewed & confirmed.
PACKAGE_CONFIRMED_KEYS_FIELD = "package_production_confirmed_keys"
PACKAGE_CONFIRMED_AT_FIELD = "package_production_confirmed_at"
PACKAGE_CONFIRMED_BY_FIELD = "package_production_confirmed_by"

# Builtin defaults granted to holding_team so production Holding can operate
# the recovered package without relying on Mongo role document edits yet.
HOLDING_TEAM_PACKAGE_GRANTS: tuple[str, ...] = (
    "purchase_requests.view",
    "purchase_requests.create",
    "purchase_orders.view",
    "purchase_orders.create",
    "purchase_orders.edit_branch",
    "purchase_invoices.view",
    "purchase_invoices.create",
    "purchase_invoices.edit",
    "purchase_payments.view",
    "purchase_payments.create",
    "purchase_receipts.view",
    "purchase_receipts.sync",
    "purchase_receipts.export",
    "purchase_receipts.receive",
    "purchase_hpp.view",
    "master_items.view",
    "master_items.manage",
    "org.branches.view",
    "org.branches.manage",
    "org.warehouses.view",
    "org.warehouses.manage",
    "current_stock.view",
    "current_stock.update",
    "current_stock.import",
    "current_stock.export",
    "inventory_valuation.view",
    "inventory_valuation.export",
    "inventory_valuation.manage_opening",
    # Nav keys required when role_permissions override replaces builtins
    "nav.purchasing",
    "nav.purchasing.requests",
    "nav.purchasing.orders",
    "nav.purchasing.advances",
    "nav.purchasing.invoices",
    "nav.purchasing.payments",
    "nav.purchasing.receipts",
    "nav.purchasing.returns",
    "nav.purchasing.hpp",
    "nav.inventory",
    "nav.inventory.master_items",
    "nav.inventory.branches_warehouses",
    "nav.inventory.current_stock",
    "nav.inventory.inventory_value",
)


def mongo_hostname(mongo_url: Optional[str]) -> Optional[str]:
    try:
        return urlparse(str(mongo_url or "")).hostname
    except ValueError:
        return None


def is_local_runtime(app_env: Optional[str], mongo_url: Optional[str] = None) -> bool:
    """True only for explicitly configured local/preview/dev/test runtimes.

    Fail-closed:
    - APP_ENV must be one of LOCAL_ENVS (local, development, preview, test).
    - Unset / unknown APP_ENV is NOT local (denies admin package exemption).
    - Loopback Mongo alone never implies local (production VPS often uses 127.0.0.1).
    - mongo_url is retained for callers/tests but does not grant local status.
    """
    env = str(app_env or "").strip().lower()
    if env in LOCAL_ENVS:
        return True
    return False


def normalize_package_confirmed_keys(raw) -> Set[str]:
    """Sanitize a confirmed-keys list to known PACKAGE_PERMISSIONS only."""
    out: Set[str] = set()
    if not isinstance(raw, (list, tuple, set)):
        return out
    for item in raw:
        key = str(item or "").strip()
        if key in PACKAGE_PERMISSIONS:
            out.add(key)
    return out


def shrink_package_confirmed_keys(confirmed, next_permissions) -> list:
    """On ordinary save: never grow; drop confirmed keys no longer granted."""
    confirmed_set = normalize_package_confirmed_keys(confirmed)
    next_set = set(next_permissions or [])
    return sorted(confirmed_set & next_set & PACKAGE_PERMISSIONS)


def apply_explicit_package_confirmation(confirmed, next_permissions, confirm_keys) -> list:
    """Replace confirmed set with the explicitly reviewed list ∩ next grants.

    Keys outside confirm_keys are NOT carried forward — confirmation covers
    only the reviewed subset. New package keys never inherit an old stamp.
    """
    next_set = set(next_permissions or [])
    reviewed = normalize_package_confirmed_keys(confirm_keys)
    return sorted(reviewed & next_set & PACKAGE_PERMISSIONS)


def can_access_purchasing_inventory_package(
    user: Optional[Dict[str, Any]],
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> bool:
    """Unrestricted package holder (local / Holding / Direktur)."""
    if is_local_runtime(app_env, mongo_url):
        return True
    role = str((user or {}).get("role") or "").strip().lower()
    return role in {HOLDING_TEAM_ROLE, DIRECTOR_FULL_ACCESS_ROLE}


def filter_package_permissions(
    perms: Iterable[str],
    user: Optional[Dict[str, Any]],
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
    *,
    role_confirmed_keys: Optional[Iterable[str]] = None,
    user_confirmed_keys: Optional[Iterable[str]] = None,
) -> Set[str]:
    """Drop package keys for non-Holding/non-Direktur outside local.

    Owner policy: Pembelian/Persediaan/HPP is Holding/Direktur only in
    production. Checkbox / confirmation stamps on other roles are configuration
    records only — they never become effective access. ``role_confirmed_keys``
    and ``user_confirmed_keys`` are retained for API compatibility but ignored
    for effective filtering.
    """
    out = set(perms or [])
    if can_access_purchasing_inventory_package(user, app_env, mongo_url):
        return out
    # Silence unused-arg warnings while preserving call-site compatibility.
    _ = role_confirmed_keys, user_confirmed_keys
    return {key for key in out if key not in PACKAGE_PERMISSIONS}


def split_package_permission_args(perms: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split an any-of/all-of list into (non_package, package) preserving order."""
    non_pkg: list[str] = []
    pkg: list[str] = []
    for raw in perms or ():
        key = str(raw or "").strip()
        if not key:
            continue
        if key in PACKAGE_PERMISSIONS:
            pkg.append(key)
        else:
            non_pkg.append(key)
    return tuple(non_pkg), tuple(pkg)


def any_of_satisfied_without_package(perms: Iterable[str], effective: Iterable[str]) -> bool:
    """True when a non-package alternative in ``perms`` is present in ``effective``."""
    eff = set(effective or [])
    non_pkg, _pkg = split_package_permission_args(perms)
    return any(key in eff for key in non_pkg)


def require_purchasing_inventory_package_access(
    user: Optional[Dict[str, Any]],
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> None:
    if not can_access_purchasing_inventory_package(user, app_env, mongo_url):
        raise HTTPException(403, PACKAGE_DENIED_MSG)


def touches_package_permissions(perms: Iterable[str]) -> bool:
    return bool(PACKAGE_PERMISSIONS.intersection(perms or []))
