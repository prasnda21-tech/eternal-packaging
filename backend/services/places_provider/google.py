"""Google Places API (New) adapter.

Official endpoints (do not invent):
  POST https://places.googleapis.com/v1/places:autocomplete
    https://developers.google.com/maps/documentation/places/web-service/place-autocomplete
  GET  https://places.googleapis.com/v1/places/{placeId}
    https://developers.google.com/maps/documentation/places/web-service/place-details

Automated tests must parse these helpers with fixtures — never call the live API.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from services.places_provider.base import PlacesNotConfigured, PlacesProvider, PlacesProviderError
from services.places_provider.types import PlaceDetails, PlaceSearchResult, PlaceSuggestion

GOOGLE_PLACES_HOST = "https://places.googleapis.com"
GOOGLE_AUTOCOMPLETE_RPC = "/v1/places:autocomplete"
PLACE_DETAILS_FIELD_MASK = "id,displayName,formattedAddress,location"
AUTOCOMPLETE_FIELD_MASK = "suggestions.placePrediction.placeId,suggestions.placePrediction.text.text"


def places_api_key() -> str:
    return (
        os.environ.get("GOOGLE_PLACES_API_KEY")
        or os.environ.get("GOOGLE_MAPS_API_KEY")
        or ""
    ).strip()


def is_google_places_configured() -> bool:
    return bool(places_api_key())


def build_autocomplete_request(query: str) -> Dict[str, Any]:
    """Official Autocomplete (New) JSON body. regionCode id biases Indonesia."""
    return {
        "input": (query or "").strip(),
        "languageCode": "id",
        "regionCode": "id",
        "includedRegionCodes": ["id"],
    }


def parse_autocomplete_response(payload: Any) -> List[PlaceSuggestion]:
    src = payload if isinstance(payload, dict) else {}
    out: List[PlaceSuggestion] = []
    for row in src.get("suggestions") or []:
        pred = (row or {}).get("placePrediction") or {}
        pid = str(pred.get("placeId") or "").strip()
        text_obj = pred.get("text") if isinstance(pred.get("text"), dict) else {}
        label = str(text_obj.get("text") or pred.get("text") or "").strip()
        if pid:
            out.append(PlaceSuggestion(place_id=pid, label=label or pid, formatted_address=label or None))
    return out


def parse_place_details_response(payload: Any) -> Optional[PlaceDetails]:
    src = payload if isinstance(payload, dict) else {}
    pid = str(src.get("id") or "").strip()
    loc = src.get("location") if isinstance(src.get("location"), dict) else {}
    try:
        lat = float(loc["latitude"]) if loc.get("latitude") is not None else None
        lng = float(loc["longitude"]) if loc.get("longitude") is not None else None
    except (TypeError, ValueError):
        lat, lng = None, None
    address = str(src.get("formattedAddress") or "").strip()
    display = src.get("displayName") if isinstance(src.get("displayName"), dict) else {}
    name = str(display.get("text") or "").strip() or None
    if not pid or lat is None or lng is None:
        return None
    return PlaceDetails(
        place_id=pid,
        formatted_address=address or name or pid,
        lat=lat,
        lng=lng,
        name=name,
    )


class GooglePlacesProvider(PlacesProvider):
    name = "google"

    def is_configured(self) -> bool:
        return is_google_places_configured()

    def _headers(self, field_mask: str) -> dict:
        key = places_api_key()
        if not key:
            raise PlacesNotConfigured("GOOGLE_PLACES_API_KEY belum dikonfigurasi.")
        return {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": field_mask,
        }

    def search(self, query: str) -> PlaceSearchResult:
        q = (query or "").strip()
        if len(q) < 2:
            return PlaceSearchResult(provider=self.name, suggestions=[])
        try:
            with httpx.Client(timeout=12.0) as client:
                r = client.post(
                    f"{GOOGLE_PLACES_HOST}{GOOGLE_AUTOCOMPLETE_RPC}",
                    headers=self._headers(AUTOCOMPLETE_FIELD_MASK),
                    json=build_autocomplete_request(q),
                )
        except httpx.HTTPError as e:
            raise PlacesProviderError(f"Google Places autocomplete gagal: {e}") from e
        if r.status_code >= 400:
            raise PlacesProviderError(f"Google Places autocomplete HTTP {r.status_code}")
        try:
            payload = r.json()
        except ValueError as e:
            raise PlacesProviderError("Google Places autocomplete merespon non-JSON") from e
        return PlaceSearchResult(provider=self.name, suggestions=parse_autocomplete_response(payload))

    def details(self, place_id: str) -> Optional[PlaceDetails]:
        pid = str(place_id or "").strip()
        if not pid:
            return None
        url = f"{GOOGLE_PLACES_HOST}/v1/places/{quote(pid, safe='')}"
        try:
            with httpx.Client(timeout=12.0) as client:
                r = client.get(url, headers=self._headers(PLACE_DETAILS_FIELD_MASK))
        except httpx.HTTPError as e:
            raise PlacesProviderError(f"Google Places details gagal: {e}") from e
        if r.status_code >= 400:
            raise PlacesProviderError(f"Google Places details HTTP {r.status_code}")
        try:
            payload = r.json()
        except ValueError as e:
            raise PlacesProviderError("Google Places details merespon non-JSON") from e
        return parse_place_details_response(payload)
