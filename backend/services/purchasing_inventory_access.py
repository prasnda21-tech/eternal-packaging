"""Production lock for recovered Purchasing + Inventory + HPP package.

LOCAL / preview / development / test (explicit APP_ENV only):
  Existing role permissions apply (admin keeps full package access).

All other runtimes (including APP_ENV unset/unknown, and production):
  Only role holding_team may hold package permission keys.
  Mongo on 127.0.0.1/localhost alone NEVER implies local.
  Sidebar, routes, and APIs all derive from filtered effective permissions —
  no separate soft-hide.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Set
from urllib.parse import urlparse

from fastapi import HTTPException

HOLDING_TEAM_ROLE = "holding_team"
PACKAGE_DENIED_MSG = "Modul Pembelian/Persediaan/HPP hanya untuk HOLDING_TEAM di production."

LOCAL_ENVS = frozenset({"local", "development", "preview", "test"})
# Documented loopback hosts only — never used to grant local package access.
LOCAL_MONGO_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Capability + nav keys restored by PR495 recovery. Filtered out for
# non-HOLDING_TEAM users when runtime is not an explicit LOCAL_ENVS.
PACKAGE_PERMISSIONS: frozenset[str] = frozenset({
    # Purchasing capabilities
    "purchase_requests.view",
    "purchase_requests.create",
    "purchase_orders.view",
    "purchase_orders.create",
    "purchase_invoices.view",
    "purchase_invoices.create",
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

# Builtin defaults granted to holding_team so production Holding can operate
# the recovered package without relying on Mongo role document edits yet.
HOLDING_TEAM_PACKAGE_GRANTS: tuple[str, ...] = (
    "purchase_requests.view",
    "purchase_requests.create",
    "purchase_orders.view",
    "purchase_orders.create",
    "purchase_invoices.view",
    "purchase_invoices.create",
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


def can_access_purchasing_inventory_package(
    user: Optional[Dict[str, Any]],
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> bool:
    if is_local_runtime(app_env, mongo_url):
        return True
    role = str((user or {}).get("role") or "").strip().lower()
    return role == HOLDING_TEAM_ROLE


def filter_package_permissions(
    perms: Iterable[str],
    user: Optional[Dict[str, Any]],
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> Set[str]:
    """Drop package keys for non-HOLDING_TEAM users outside local runtime."""
    out = set(perms or [])
    if can_access_purchasing_inventory_package(user, app_env, mongo_url):
        return out
    return out - PACKAGE_PERMISSIONS


def require_purchasing_inventory_package_access(
    user: Optional[Dict[str, Any]],
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> None:
    if not can_access_purchasing_inventory_package(user, app_env, mongo_url):
        raise HTTPException(403, PACKAGE_DENIED_MSG)


def touches_package_permissions(perms: Iterable[str]) -> bool:
    return bool(PACKAGE_PERMISSIONS.intersection(perms or []))
