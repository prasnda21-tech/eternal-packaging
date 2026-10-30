"""Effective Purchasing + Inventory + HPP permissions.

LOCAL / preview / development / test (explicit APP_ENV only):
  Existing role permissions apply (admin keeps full package access).

All other runtimes (including APP_ENV unset/unknown, and production):
  - holding_team and direktur_utama: granted package keys stay effective.
    Global branch scope does not add module permissions.
  - other roles, before one-time normalization: a package key is effective
    only when it is already in ``package_production_confirmed_keys``.
    Stored grants that were never effective stay ineffective on deploy.
  - after ``manual_grant_normalized`` (editor save or the one-time cutover):
    a package key is effective when it is in the saved role grant or an
    explicit user override True. No separate approval list is required.

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
# One-time marker. Unnormalized documents keep the old confirmation filter.
# It is not an approval dialog and it does not grant keys by itself.
MANUAL_GRANT_NORMALIZED_FIELD = "manual_grant_normalized"

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


def legacy_effective_package_keys(
    granted,
    confirmed,
    *,
    unrestricted: bool = False,
    already_normalized: bool = False,
) -> Set[str]:
    """Package keys that were effective before a manual save or cutover."""
    granted_pkg = PACKAGE_PERMISSIONS.intersection(granted or [])
    if unrestricted or already_normalized:
        return set(granted_pkg)
    return granted_pkg & normalize_package_confirmed_keys(confirmed)


def reconcile_manual_package_grant(
    stored_permissions,
    next_permissions,
    previously_confirmed,
    *,
    already_normalized: bool = False,
    unrestricted: bool = False,
) -> tuple[list, list]:
    """Return ``(cleaned_permissions, effective_package_keys)``.

    A normalized grant keeps every package key in the saved list.
    An unnormalized grant keeps keys that were already effective, activates
    keys newly added by this save, and drops stored package keys that were
    never effective so a deploy or an unrelated save cannot turn them on.
    """
    stored = set(stored_permissions or [])
    nxt = set(next_permissions or [])
    if already_normalized or unrestricted:
        effective = PACKAGE_PERMISSIONS.intersection(nxt)
    else:
        previously = legacy_effective_package_keys(
            stored, previously_confirmed, unrestricted=False, already_normalized=False,
        )
        newly = PACKAGE_PERMISSIONS.intersection(nxt - stored)
        effective = (previously & nxt) | newly
    cleaned = (nxt - PACKAGE_PERMISSIONS) | effective
    return sorted(cleaned), sorted(effective)


def reconcile_manual_user_override(
    old_override,
    new_override,
    previously_confirmed,
    *,
    already_normalized: bool = False,
    unrestricted: bool = False,
) -> tuple[dict, list]:
    """Drop override-True package keys that were stored but never effective."""
    old = old_override or {}
    new = dict(new_override or {})
    old_true = {key for key, allowed in old.items() if allowed is True}
    new_true = {key for key, allowed in new.items() if allowed is True}
    if already_normalized or unrestricted:
        effective = new_true & PACKAGE_PERMISSIONS
    else:
        previously = legacy_effective_package_keys(
            old_true, previously_confirmed, unrestricted=False, already_normalized=False,
        )
        newly = (new_true - old_true) & PACKAGE_PERMISSIONS
        effective = (previously & new_true) | newly
    for key in list(new):
        if key in PACKAGE_PERMISSIONS and new.get(key) is True and key not in effective:
            del new[key]
    return new, sorted(effective)


def filter_package_permissions(
    perms: Iterable[str],
    user: Optional[Dict[str, Any]],
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
    *,
    role_confirmed_keys: Optional[Iterable[str]] = None,
    user_confirmed_keys: Optional[Iterable[str]] = None,
    role_normalized: bool = False,
    user_normalized: bool = False,
    role_permission_keys: Optional[Iterable[str]] = None,
    user_override: Optional[Dict[str, Any]] = None,
) -> Set[str]:
    """Keep package keys that are already effective under the active rule.

    Holding and Direktur keep the package keys already in their grant.
    Local/test runtimes are unchanged. Every other production account:

    - unnormalized: a package key survives only when it is in the old
      confirmation stamp (role stamp or user stamp);
    - normalized role: only package keys present in the stored role grant;
    - normalized user: only package keys with an explicit override True.

    A normalized flag does not bless a super-admin wildcard. Package keys
    that are neither a stored role grant nor an explicit override stay off.
    """
    out = set(perms or [])
    if is_local_runtime(app_env, mongo_url) or can_access_purchasing_inventory_package(
        user, app_env, mongo_url
    ):
        return out
    if role_normalized:
        role_keep = PACKAGE_PERMISSIONS.intersection(role_permission_keys or [])
    else:
        role_keep = normalize_package_confirmed_keys(role_confirmed_keys)
    if user_normalized:
        override = user_override if isinstance(user_override, dict) else ((user or {}).get("permissions_override") or {})
        user_keep = {
            key for key, allowed in override.items()
            if allowed is True and key in PACKAGE_PERMISSIONS
        }
    else:
        user_keep = normalize_package_confirmed_keys(user_confirmed_keys)
    keep = role_keep | user_keep
    return {key for key in out if key not in PACKAGE_PERMISSIONS or key in keep}


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
