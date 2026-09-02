"""Route Optimizer Places search — reuses official Google Places adapter.

Does not use the delivery Places mock fallback. If Google is not configured,
search returns an empty candidate list with a clear message.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException

from services.places_provider.base import PlacesNotConfigured, PlacesProviderError
from services.places_provider.google import GooglePlacesProvider, is_google_places_configured


def places_status() -> dict:
    configured = is_google_places_configured()
    return {
        "configured": configured,
        "provider": "google" if configured else "none",
    }


def google_maps_url(*, place_id: str | None = None, lat=None, lng=None, name: str = "") -> str:
    pid = str(place_id or "").strip()
    if pid:
        query = quote((name or "").strip() or f"{lat},{lng}")
        return f"https://www.google.com/maps/search/?api=1&query={query}&query_place_id={quote(pid)}"
    if lat is not None and lng is not None:
        return f"https://www.google.com/maps?q={lat},{lng}"
    return ""


def search_places(q: str) -> dict:
    status = places_status()
    if not status["configured"]:
        return {
            **status,
            "suggestions": [],
            "message": "Google Maps belum dikonfigurasi.",
        }
    query = (q or "").strip()
    if len(query) < 2:
        return {**status, "suggestions": []}
    try:
        result = GooglePlacesProvider().search(query)
    except PlacesNotConfigured:
        return {
            "configured": False,
            "provider": "none",
            "suggestions": [],
            "message": "Google Maps belum dikonfigurasi.",
        }
    except PlacesProviderError:
        raise HTTPException(502, "Pencarian Google Maps gagal. Coba lagi atau pakai titik peta / koordinat.")
    return {
        **status,
        "suggestions": [
            {
                "place_id": s.place_id,
                "label": s.label,
                "formatted_address": s.formatted_address,
            }
            for s in result.suggestions
        ],
    }


def place_details(place_id: str) -> dict:
    status = places_status()
    if not status["configured"]:
        raise HTTPException(503, "Google Maps belum dikonfigurasi.")
    pid = str(place_id or "").strip()
    if not pid:
        raise HTTPException(400, "place_id wajib.")
    try:
        det = GooglePlacesProvider().details(pid)
    except PlacesNotConfigured:
        raise HTTPException(503, "Google Maps belum dikonfigurasi.")
    except PlacesProviderError:
        raise HTTPException(502, "Detail tempat Google Maps gagal dimuat.")
    if not det:
        raise HTTPException(404, "Tempat tidak ditemukan.")
    return {
        **status,
        "place": {
            "place_id": det.place_id,
            "google_place_id": det.place_id,
            "name": det.name,
            "formatted_address": det.formatted_address,
            "lat": det.lat,
            "lng": det.lng,
            "google_maps_url": google_maps_url(
                place_id=det.place_id,
                lat=det.lat,
                lng=det.lng,
                name=det.name or "",
            ),
        },
    }
