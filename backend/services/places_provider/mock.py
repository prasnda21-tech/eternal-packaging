"""Deterministic local Places fixture. Never hits Google. Not a real Eternal address."""
from __future__ import annotations

from typing import List, Optional

from services.places_provider.base import PlacesProvider
from services.places_provider.types import PlaceDetails, PlaceSearchResult, PlaceSuggestion

# Preview-only fixtures. Dummy development addresses — not Eternal production data.
MOCK_PLACES: List[PlaceDetails] = [
    PlaceDetails(
        place_id="mock-place-jusin-test",
        name="JUS.IN Test Outlet",
        formatted_address="Jl. Preview Dummy No.1, Kelapa Gading (lokasi uji, bukan alamat produksi)",
        lat=-6.15801,
        lng=106.91002,
    ),
    PlaceDetails(
        place_id="mock-place-preview-utara",
        name="Preview Outlet Utara",
        formatted_address="Jl. Preview Dummy No.2, Jakarta Utara (lokasi uji)",
        lat=-6.14001,
        lng=106.85002,
    ),
    PlaceDetails(
        place_id="mock-place-preview-bekasi",
        name="Preview Outlet Bekasi",
        formatted_address="Jl. Preview Dummy No.3, Bekasi (lokasi uji)",
        lat=-6.23801,
        lng=106.99202,
    ),
    PlaceDetails(
        place_id="mock-place-preview-boulevard",
        name="Preview Outlet Boulevard",
        formatted_address="Jl. Boulevard Raya Preview No.10, Kelapa Gading (lokasi uji)",
        lat=-6.15702,
        lng=106.90903,
    ),
]


class LocalMockPlacesProvider(PlacesProvider):
    name = "mock"

    def is_configured(self) -> bool:
        return True

    def search(self, query: str) -> PlaceSearchResult:
        q = (query or "").strip().lower()
        hits = MOCK_PLACES
        if q:
            hits = [
                p for p in MOCK_PLACES
                if q in p.name.lower() or q in p.formatted_address.lower() or q in p.place_id.lower()
            ]
        return PlaceSearchResult(
            provider=self.name,
            suggestions=[
                PlaceSuggestion(
                    place_id=p.place_id,
                    label=f"{p.name} — {p.formatted_address}",
                    formatted_address=p.formatted_address,
                )
                for p in hits
            ],
        )

    def details(self, place_id: str) -> Optional[PlaceDetails]:
        pid = str(place_id or "").strip()
        for p in MOCK_PLACES:
            if p.place_id == pid:
                return p
        return None
