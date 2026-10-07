"""Production lock for recovered Purchasing + Inventory + HPP package.

LOCAL / preview / development:
  Existing role permissions apply (admin keeps full package access).

PRODUCTION (non-local Mongo host, or APP_ENV=production):
  Only role holding_team may hold package permission keys.
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
LOCAL_MONGO_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Capability + nav keys restored by PR495 recovery. Filtered out for
# non-HOLDING_TEAM users when runtime is production.
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
)


def mongo_hostname(mongo_url: Optional[str]) -> Optional[str]:
    try:
        return urlparse(str(mongo_url or "")).hostname
    except ValueError:
        return None


def is_local_runtime(app_env: Optional[str], mongo_url: Optional[str] = None) -> bool:
    """True for Cloud/local preview. False for production hosts."""
    env = str(app_env or "").strip().lower()
    host = mongo_hostname(mongo_url)
    if env == "production":
        return False
    if host and host not in LOCAL_MONGO_HOSTS:
        return False
    if env in LOCAL_ENVS:
        return True
    # APP_ENV unset + local Mongo → treat as local preview (current Cloud DEV).
    if not env and (host in LOCAL_MONGO_HOSTS or host is None):
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
