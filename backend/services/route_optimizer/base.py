"""RouteOptimizerProvider abstraction. Business logic must not depend on Google schema."""
from __future__ import annotations

from abc import ABC, abstractmethod

from services.route_optimizer.types import OptimizeRequest, OptimizeResult


class RouteOptimizerError(RuntimeError):
    pass


class RouteOptimizerNotConfigured(RouteOptimizerError):
    pass


class RouteOptimizerProvider(ABC):
    name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        """True when this provider can serve a live optimize call."""

    @abstractmethod
    def optimize(self, request: OptimizeRequest) -> OptimizeResult:
        """Run batch optimization. Must not be called on page render."""
