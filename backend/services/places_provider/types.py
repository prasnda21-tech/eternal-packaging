"""Domain types for place search. Independent of Google Places JSON."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PlaceSuggestion:
    place_id: str
    label: str
    formatted_address: Optional[str] = None


@dataclass
class PlaceDetails:
    place_id: str
    formatted_address: str
    lat: float
    lng: float
    name: Optional[str] = None


@dataclass
class PlaceSearchResult:
    provider: str
    suggestions: List[PlaceSuggestion]
