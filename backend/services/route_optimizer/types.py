"""Domain types for route optimization. Independent of Google request/response schema."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class OptimizerStop:
    stop_id: str
    lat: Optional[float]
    lng: Optional[float]
    place_id: Optional[str] = None
    qty: int = 0
    package_count: Optional[int] = None
    priority: str = "normal"
    outlet_closing_time: Optional[str] = None
    deadline: Optional[str] = None
    customer: str = ""
    address: str = ""

    def has_location(self) -> bool:
        return self.lat is not None and self.lng is not None


@dataclass
class OptimizerVehicle:
    vehicle_id: str
    driver_user_id: str
    driver_name: str = ""
    plate: str = ""
    name: str = ""
    capacity_dus: Optional[int] = None
    start_lat: Optional[float] = None
    start_lng: Optional[float] = None
    end_lat: Optional[float] = None
    end_lng: Optional[float] = None


@dataclass
class OptimizerDepot:
    name: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    place_id: Optional[str] = None
    address: str = ""

    def has_location(self) -> bool:
        return self.lat is not None and self.lng is not None


@dataclass
class OptimizeRequest:
    """Provider-agnostic batch optimization input."""
    batch_id: str
    delivery_date: str
    depot: OptimizerDepot
    stops: List[OptimizerStop]
    vehicles: List[OptimizerVehicle]
    timezone: str = "Asia/Jakarta"


@dataclass
class AssignedStop:
    stop_id: str
    sequence: int
    eta: Optional[str] = None
    infeasible: bool = False
    infeasible_reason: Optional[str] = None


@dataclass
class VehicleRoute:
    vehicle_id: str
    driver_user_id: str
    driver_name: str = ""
    plate: str = ""
    vehicle_name: str = ""
    stops: List[AssignedStop] = field(default_factory=list)
    distance_meters: int = 0
    duration_seconds: int = 0
    total_qty: int = 0
    total_dus: Optional[int] = None
    geometry: List[Dict[str, float]] = field(default_factory=list)
    polyline: Optional[str] = None


@dataclass
class UnassignedStop:
    stop_id: str
    reason: str
    code: str = "unassigned"


@dataclass
class OptimizeResult:
    routes: List[VehicleRoute]
    unassigned: List[UnassignedStop]
    provider: str
    warnings: List[str] = field(default_factory=list)
    raw_meta: Dict[str, Any] = field(default_factory=dict)

    def assigned_stop_ids(self) -> List[str]:
        ids = []
        for r in self.routes:
            for s in r.stops:
                ids.append(s.stop_id)
        return ids
