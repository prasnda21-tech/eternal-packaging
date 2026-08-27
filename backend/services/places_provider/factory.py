"""Select PlacesProvider. Production never silently falls back to mock."""
from __future__ import annotations

import os

from services.places_provider.base import PlacesNotConfigured, PlacesProvider
from services.places_provider.google import GooglePlacesProvider, is_google_places_configured
from services.places_provider.mock import LocalMockPlacesProvider


def is_production_runtime() -> bool:
    env = (
        os.environ.get("APP_ENV")
        or os.environ.get("ENVIRONMENT")
        or os.environ.get("ENV")
        or ""
    ).strip().lower()
    return env in ("production", "prod")


def requested_provider() -> str:
    return (os.environ.get("PLACES_PROVIDER") or "").strip().lower()


def get_places_provider() -> PlacesProvider:
    """Resolve provider.

    Local preview / tests: mock unless PLACES_PROVIDER=google and a key exists.
    Production: Google required. Mock only if PLACES_PROVIDER=mock is explicit.
    """
    explicit = requested_provider()
    google_ok = is_google_places_configured()

    if explicit == "google":
        if not google_ok:
            raise PlacesNotConfigured(
                "PLACES_PROVIDER=google tetapi GOOGLE_PLACES_API_KEY belum ada."
            )
        return GooglePlacesProvider()

    if explicit == "mock":
        return LocalMockPlacesProvider()

    if google_ok:
        return GooglePlacesProvider()

    if is_production_runtime():
        raise PlacesNotConfigured(
            "Google Places wajib di production. Tidak ada silent mock fallback."
        )
    return LocalMockPlacesProvider()
