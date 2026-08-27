"""Select RouteOptimizerProvider. Production never silently falls back to mock."""
from __future__ import annotations

import os

from services.route_optimizer.base import RouteOptimizerNotConfigured, RouteOptimizerProvider
from services.route_optimizer.google import GoogleRouteOptimizer, is_google_configured
from services.route_optimizer.mock import LocalMockRouteOptimizer


def is_production_runtime() -> bool:
    env = (
        os.environ.get("APP_ENV")
        or os.environ.get("ENVIRONMENT")
        or os.environ.get("ENV")
        or ""
    ).strip().lower()
    return env in ("production", "prod")


def requested_provider() -> str:
    return (os.environ.get("ROUTE_OPTIMIZER_PROVIDER") or "").strip().lower()


def get_route_optimizer() -> RouteOptimizerProvider:
    """Resolve provider.

    - Automated tests / local preview: mock unless Google is explicitly configured
      and ROUTE_OPTIMIZER_PROVIDER=google.
    - Production: Google required. Mock only if ROUTE_OPTIMIZER_PROVIDER=mock
      is set on purpose — never a silent fallback.
    """
    explicit = requested_provider()
    google_ok = is_google_configured()

    if explicit == "google":
        if not google_ok:
            raise RouteOptimizerNotConfigured(
                "ROUTE_OPTIMIZER_PROVIDER=google tetapi kredensial Google belum ada."
            )
        return GoogleRouteOptimizer()

    if explicit == "mock":
        return LocalMockRouteOptimizer()

    if google_ok:
        return GoogleRouteOptimizer()

    if is_production_runtime():
        raise RouteOptimizerNotConfigured(
            "Google Route Optimization wajib di production. "
            "Tidak ada silent mock fallback."
        )
    return LocalMockRouteOptimizer()
