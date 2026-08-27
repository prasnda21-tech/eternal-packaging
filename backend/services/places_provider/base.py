"""PlacesProvider abstraction. Frontend never receives the Google API key."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from services.places_provider.types import PlaceDetails, PlaceSearchResult


class PlacesProviderError(RuntimeError):
    pass


class PlacesNotConfigured(PlacesProviderError):
    pass


class PlacesProvider(ABC):
    name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        ...

    @abstractmethod
    def search(self, query: str) -> PlaceSearchResult:
        ...

    @abstractmethod
    def details(self, place_id: str) -> Optional[PlaceDetails]:
        ...
