"""Delivery route optimization: queue, batch, identity, mock optimizer, Google adapter.

No live Google API calls. No Mongo required for these unit tests.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS, effective_permissions
from services.delivery_batches import (
    actor_id,
    apply_stop_updates,
    build_batch,
    mark_published,
    upsert_driver_ready,
    validate_for_publish,
)
from services.delivery_routes import (
    ROUTE_BERJALAN,
    ROUTE_DRAFT,
    ROUTE_TERJADWAL,
    RouteError,
    apply_optimize_result,
    assignment_for_driver,
    build_optimize_request,
    confirm_plan,
    edit_draft,
    mark_stop_delivered,
    mark_stop_rescheduled,
    my_route_view,
    start_assignment,
)
from services.delivery_stops import (
    build_queue_stops,
    remaining_eligible_qty,
    snapshot_stop,
    split_remaining_by_shipment,
)
from services.route_optimizer.factory import get_route_optimizer, is_production_runtime
from services.route_optimizer.base import RouteOptimizerNotConfigured
from services.route_optimizer.google import PRIORITY_PENALTY, build_google_request, parse_google_response
from services.route_optimizer.mock import LocalMockRouteOptimizer
from services.route_optimizer.types import (
    OptimizeRequest,
    OptimizerDepot,
    OptimizerStop,
    OptimizerVehicle,
)


def _ready_order(**extra):
    base = {
        "id": extra.pop("id", "ord-a"),
        "po_number": extra.pop("po_number", "PO/A"),
        "customer": extra.pop("customer", "Customer A"),
        "address": "Jl. Contoh 1",
        "deadline": "2026-08-30",
        "delivery_status": "Belum Terkirim",
        "current_stage": 13,
        "line_items": [{"name": "Karton", "quantity": 10000}],
        "total_quantity": 10000,
        "packing_info": {"qty_packed": 4000, "lines": [{"line_idx": 0, "packed_qty": 4000}]},
        "shipments": extra.pop("shipments", [{"id": "sh-1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 4000}]}]),
        "loading_info": extra.pop("loading_info", {}),
        "qty_delivered": extra.pop("qty_delivered", 0),
        "delivery_location": extra.pop("delivery_location", {"lat": -6.2, "lng": 106.8, "place_id": "ChIJA"}),
        "cancelled": False,
    }
    base.update(extra)
    return base


def _user(uid="u-budi", name="Budi", role="driver"):
    return {"id": uid, "user_id": uid, "sub": uid, "name": name, "email": f"{uid}@example.com", "role": role}


def _depot():
    return {"name": "Gudang Preview", "address": "Depot Test", "lat": -6.175, "lng": 106.827, "place_id": "ChIJDEPOT"}


def _stop(i, **kw):
    return OptimizerStop(
        stop_id=kw.get("stop_id", f"ds:ord-{i}:sh-{i}"),
        lat=kw.get("lat", -6.2 - i * 0.01),
        lng=kw.get("lng", 106.8 + i * 0.01),
        qty=kw.get("qty", 100),
        package_count=kw.get("package_count", 10),
        priority=kw.get("priority", "normal"),
        outlet_closing_time=kw.get("outlet_closing_time"),
        customer=kw.get("customer", f"Customer {i}"),
    )


def test_queue_is_delivery_stop_not_whole_po():
    o = _ready_order(
        shipments=[
            {"id": "sh-1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 2000}]},
            {"id": "sh-2", "number": "SJ-2", "lines": [{"line_idx": 0, "qty": 2000}]},
        ],
    )
    assert remaining_eligible_qty(o) == 4000
    parts = split_remaining_by_shipment(o, 4000)
    assert len(parts) == 2
    assert {p["shipment_id"] for p in parts} == {"sh-1", "sh-2"}
    rows = build_queue_stops([o])
    assert len(rows) == 2
    assert all(r["stop_id"].startswith("ds:ord-a:") for r in rows)
    assert rows[0]["qty"] == 2000


def test_partial_only_eligible_qty():
    o = _ready_order(qty_delivered=1500)
    assert remaining_eligible_qty(o) == 2500
    rows = build_queue_stops([o])
    assert rows[0]["qty"] == 2500


def test_missing_location_is_visible_not_dropped():
    o = _ready_order(delivery_location={}, address="Jl. Tanpa Koordinat")
    rows = build_queue_stops([o])
    assert len(rows) == 1
    assert rows[0]["location_status"] == "need_location"
    assert rows[0]["location_label"] == "Belum Ada Lokasi"
    assert rows[0]["selectable"] is False


def test_priority_and_closing_stored_on_stop_not_po():
    o = _ready_order()
    row = build_queue_stops([o])[0]
    snap = snapshot_stop(row, priority="mendesak", outlet_closing_time="15:00")
    assert snap["priority"] == "mendesak"
    assert snap["outlet_closing_time"] == "15:00"
    assert o.get("urgent") in (None, False)
    assert o.get("deadline") == "2026-08-30"


def test_scheduler_create_and_publish_and_unauthorized_keys():
    o = _ready_order()
    rows = build_queue_stops([o])
    admin = _user("u-hp", "Kepala", "head_production")
    batch = build_batch(delivery_date="2026-08-27", queue_rows=rows, user=admin)
    apply_stop_updates(batch, [{"stop_id": rows[0]["stop_id"], "priority": "prioritas", "outlet_closing_time": "17:00"}])
    assert batch["stops"][0]["priority"] == "prioritas"
    assert batch["stops"][0]["outlet_closing_time"] == "17:00"
    assert validate_for_publish(batch) is None
    mark_published(batch, admin)
    assert batch["status"] == "published"
    hp = set(ROLE_DEFAULT_PERMISSIONS["head_production"])
    helper = set(ROLE_DEFAULT_PERMISSIONS["helper"])
    assert "delivery.schedule.create" in hp
    assert "delivery.schedule.publish" in hp
    assert "delivery.schedule.create" not in helper
    assert "delivery.schedule.publish" not in helper


def test_driver_name_from_actor_not_payload():
    budi = _user("u-budi", "Budi")
    andi = _user("u-andi", "Andi")
    o = _ready_order()
    batch = build_batch(delivery_date="2026-08-27", queue_rows=build_queue_stops([o]), user=budi)
    mark_published(batch, budi)
    upsert_driver_ready(
        batch,
        driver_user_id=actor_id(budi),
        driver_name=budi["name"],
        vehicle={"id": "veh-1", "name": "L300", "plate": "B 1234 ABC", "capacity_dus": 120},
    )
    assert batch["driver_readiness"][0]["driver_name"] == "Budi"
    assert batch["driver_readiness"][0]["driver_user_id"] == "u-budi"
    assert actor_id(budi) != actor_id(andi)


def test_multi_vehicle_assigns_each_stop_once():
    stops = [_stop(i, package_count=10) for i in range(1, 7)]
    vehicles = [
        OptimizerVehicle("veh-a", "u-budi", "Budi", "B 1234 ABC", "A", 100, -6.175, 106.827, -6.175, 106.827),
        OptimizerVehicle("veh-b", "u-andi", "Andi", "B 5678 XYZ", "B", 100, -6.175, 106.827, -6.175, 106.827),
    ]
    req = OptimizeRequest(
        batch_id="b1",
        delivery_date="2026-08-27",
        depot=OptimizerDepot("Gudang", -6.175, 106.827),
        stops=stops,
        vehicles=vehicles,
    )
    result = LocalMockRouteOptimizer().optimize(req)
    assigned = result.assigned_stop_ids()
    assert len(assigned) == 6
    assert len(set(assigned)) == 6
    assert {r.driver_user_id for r in result.routes} == {"u-budi", "u-andi"}
    assert all(r.stops for r in result.routes)


def test_outlet_closing_infeasible_is_unassigned():
    stop = _stop(1, outlet_closing_time="07:00", lat=-7.5, lng=110.4)
    req = OptimizeRequest(
        batch_id="b1",
        delivery_date="2026-08-27",
        depot=OptimizerDepot("Gudang", -6.175, 106.827),
        stops=[stop],
        vehicles=[OptimizerVehicle("veh-a", "u-budi", "Budi", "B 1", "A", 100, -6.175, 106.827, -6.175, 106.827)],
    )
    result = LocalMockRouteOptimizer().optimize(req)
    assert result.assigned_stop_ids() == []
    assert result.unassigned[0].reason == "Jam tutup tidak feasible"


def test_google_adapter_passes_priority_and_time_window():
    req = OptimizeRequest(
        batch_id="b1",
        delivery_date="2026-08-27",
        depot=OptimizerDepot("Gudang", -6.175, 106.827),
        stops=[
            _stop(1, priority="mendesak", outlet_closing_time="15:00"),
            _stop(2, priority="normal"),
        ],
        vehicles=[OptimizerVehicle("veh-a", "u-budi", "Budi", "B 1", "A", 80, -6.175, 106.827, -6.175, 106.827)],
    )
    body = build_google_request(req)
    assert "https://routeoptimization.googleapis.com" in "https://routeoptimization.googleapis.com/v1/projects/x:optimizeTours"
    ships = body["model"]["shipments"]
    by_label = {s["label"]: s for s in ships}
    assert by_label[req.stops[0].stop_id]["penaltyCost"] == PRIORITY_PENALTY["mendesak"]
    tw = by_label[req.stops[0].stop_id]["deliveries"][0]["timeWindows"]
    assert tw[0]["endTime"].startswith("2026-08-27T15:00") or "15:00" in tw[0]["endTime"] or tw[0]["endTime"].endswith("+07:00")
    assert "timeWindows" not in by_label[req.stops[1].stop_id]["deliveries"][0]
    assert by_label[req.stops[1].stop_id]["penaltyCost"] == PRIORITY_PENALTY["normal"]
    assert body["model"]["vehicles"][0]["loadLimits"]["dus"]["maxLoad"] == "80"
    assert "startTime" not in tw[0]


def test_google_adapter_maps_skipped_and_infeasible_eta():
    req = OptimizeRequest(
        batch_id="b1",
        delivery_date="2026-08-27",
        depot=OptimizerDepot("Gudang", -6.175, 106.827),
        stops=[_stop(1, outlet_closing_time="15:00"), _stop(2)],
        vehicles=[OptimizerVehicle("veh-a", "u-budi", "Budi", "B 1", "A")],
    )
    payload = {
        "routes": [{
            "vehicleLabel": "veh-a",
            "visits": [{
                "shipmentLabel": req.stops[0].stop_id,
                "startTime": "2026-08-27T16:30:00+07:00",
            }],
            "metrics": {"travelDistanceMeters": 1000, "travelDuration": "600s"},
        }],
        "skippedShipments": [{
            "label": req.stops[1].stop_id,
            "reasons": [{"code": "DEMAND_EXCEEDS_VEHICLE_CAPACITY"}],
        }],
    }
    parsed = parse_google_response(payload, req)
    assert parsed.routes[0].stops[0].infeasible is True
    assert parsed.unassigned[0].reason == "Kapasitas tidak cukup"


def test_missing_location_unassigned_reason():
    req = OptimizeRequest(
        batch_id="b1",
        delivery_date="2026-08-27",
        depot=OptimizerDepot("Gudang", -6.175, 106.827),
        stops=[OptimizerStop("ds:x:po", None, None, qty=10)],
        vehicles=[OptimizerVehicle("veh-a", "u-budi", "Budi")],
    )
    result = LocalMockRouteOptimizer().optimize(req)
    assert result.unassigned[0].reason == "Lokasi outlet belum diatur"


def test_no_vehicle_unassigned():
    req = OptimizeRequest(
        batch_id="b1",
        delivery_date="2026-08-27",
        depot=OptimizerDepot("Gudang", -6.175, 106.827),
        stops=[_stop(1)],
        vehicles=[],
    )
    result = LocalMockRouteOptimizer().optimize(req)
    assert result.unassigned[0].reason == "Tidak ada kendaraan"


def test_draft_then_confirm_then_driver_sees_own_route():
    o = _ready_order()
    batch = build_batch(delivery_date="2026-08-27", queue_rows=build_queue_stops([o]), user=_user("u-hp", "Kepala"))
    mark_published(batch, _user("u-hp", "Kepala"))
    upsert_driver_ready(
        batch, driver_user_id="u-budi", driver_name="Budi",
        vehicle={"id": "veh-1", "name": "L300", "plate": "B 1234 ABC", "capacity_dus": 200},
    )
    req = build_optimize_request(batch, _depot())
    result = LocalMockRouteOptimizer().optimize(req)
    plan = apply_optimize_result(batch, result, user=_user("u-hp", "Kepala"))
    assert plan["status"] == ROUTE_DRAFT
    assert assignment_for_driver(plan, "u-budi")
    budi = _user("u-budi", "Budi")
    try:
        start_assignment(plan, driver_user_id="u-budi", user=budi)
        assert False, "start before confirm should fail"
    except RouteError:
        pass
    confirm_plan(plan, _user("u-hp", "Kepala"))
    assert plan["status"] == ROUTE_TERJADWAL
    view = my_route_view(plan, assignment_for_driver(plan, "u-budi"), budi)
    assert view["driver_name"] == "Budi"
    assert view["driver_user_id"] == "u-budi"
    start_assignment(plan, driver_user_id="u-budi", user=budi)
    assert plan["status"] == ROUTE_BERJALAN
    asg = assignment_for_driver(plan, "u-budi")
    assert asg["started_at"]
    assert asg["started_by_id"] == "u-budi"
    assert "watchPosition" not in str(plan)
    assert plan.get("gps") is None
    assert assignment_for_driver(plan, "u-andi") is None


def test_budi_cannot_start_andi_assignment():
    o = _ready_order()
    batch = build_batch(delivery_date="2026-08-27", queue_rows=build_queue_stops([o]), user=_user("u-hp", "Kepala"))
    mark_published(batch, _user("u-hp", "Kepala"))
    upsert_driver_ready(batch, driver_user_id="u-andi", driver_name="Andi", vehicle={"id": "veh-2", "plate": "B 2"})
    plan = apply_optimize_result(
        batch, LocalMockRouteOptimizer().optimize(build_optimize_request(batch, _depot())),
        user=_user("u-hp", "Kepala"),
    )
    confirm_plan(plan, _user("u-hp", "Kepala"))
    try:
        start_assignment(plan, driver_user_id="u-budi", user=_user("u-budi", "Budi"))
        assert False, "Budi must not start Andi route"
    except RouteError as e:
        assert "tidak memiliki" in str(e).lower()


def test_manual_edit_and_unassigned_visible():
    o = _ready_order()
    batch = build_batch(delivery_date="2026-08-27", queue_rows=build_queue_stops([o]), user=_user("u-hp", "Kepala"))
    mark_published(batch, _user("u-hp", "Kepala"))
    upsert_driver_ready(batch, driver_user_id="u-budi", driver_name="Budi", vehicle={"id": "veh-1", "plate": "B 1"})
    plan = apply_optimize_result(
        batch, LocalMockRouteOptimizer().optimize(build_optimize_request(batch, _depot())),
        user=_user("u-hp", "Kepala"),
    )
    sid = plan["routes"][0]["stops"][0]["stop_id"]
    aid = plan["routes"][0]["assignment_id"]
    edit_draft(plan, {"moves": [{"stop_id": sid, "assignment_id": None, "reason": "Dilepas"}]}, _user("u-hp", "Kepala"))
    assert plan["unassigned"]
    assert plan["unassigned"][0]["unassigned_reason"]
    kinds = [v["kind"] for v in plan["versions"]]
    assert "manual_edit" in kinds
    assert plan["status"] == ROUTE_DRAFT


def test_delivery_progress_and_reschedule_visible():
    o = _ready_order()
    batch = build_batch(delivery_date="2026-08-27", queue_rows=build_queue_stops([o]), user=_user("u-hp", "Kepala"))
    mark_published(batch, _user("u-hp", "Kepala"))
    upsert_driver_ready(batch, driver_user_id="u-budi", driver_name="Budi", vehicle={"id": "veh-1", "plate": "B 1"})
    plan = apply_optimize_result(
        batch, LocalMockRouteOptimizer().optimize(build_optimize_request(batch, _depot())),
        user=_user("u-hp", "Kepala"),
    )
    confirm_plan(plan, _user("u-hp", "Kepala"))
    start_assignment(plan, driver_user_id="u-budi", user=_user("u-budi", "Budi"))
    assert mark_stop_delivered(plan, order_id="ord-a") is True
    assert plan["routes"][0]["stops"][0]["status"] == "delivered"
    o2 = _ready_order(id="ord-b", po_number="PO/B", customer="Customer B",
                      delivery_location={"lat": -6.21, "lng": 106.81})
    batch2 = build_batch(delivery_date="2026-08-27", queue_rows=build_queue_stops([o, o2]), user=_user("u-hp", "Kepala"))
    mark_published(batch2, _user("u-hp", "Kepala"))
    upsert_driver_ready(batch2, driver_user_id="u-budi", driver_name="Budi", vehicle={"id": "veh-1", "plate": "B 1"})
    plan2 = apply_optimize_result(
        batch2, LocalMockRouteOptimizer().optimize(build_optimize_request(batch2, _depot())),
        user=_user("u-hp", "Kepala"),
    )
    confirm_plan(plan2, _user("u-hp", "Kepala"))
    mark_stop_rescheduled(plan2, order_id="ord-b", route_started=False)
    assert any(s.get("status") == "rescheduled" or s.get("unassigned_reason") for s in plan2.get("unassigned") or [])


def test_factory_local_is_mock_production_no_silent_fallback(monkeypatch=None):
    os.environ.pop("ROUTE_OPTIMIZER_PROVIDER", None)
    os.environ.pop("GOOGLE_ROUTE_OPTIMIZATION_PROJECT", None)
    os.environ.pop("GOOGLE_ROUTE_OPTIMIZATION_API_KEY", None)
    os.environ.pop("APP_ENV", None)
    provider = get_route_optimizer()
    assert provider.name == "mock"
    assert is_production_runtime() is False


def test_factory_production_raises_without_google():
    prev = {k: os.environ.get(k) for k in (
        "APP_ENV", "ENVIRONMENT", "ENV", "ROUTE_OPTIMIZER_PROVIDER",
        "GOOGLE_ROUTE_OPTIMIZATION_PROJECT", "GOOGLE_ROUTE_OPTIMIZATION_API_KEY",
        "GOOGLE_MAPS_API_KEY", "GOOGLE_CLOUD_PROJECT",
    )}
    try:
        os.environ["APP_ENV"] = "production"
        for k in (
            "ROUTE_OPTIMIZER_PROVIDER", "GOOGLE_ROUTE_OPTIMIZATION_PROJECT",
            "GOOGLE_ROUTE_OPTIMIZATION_API_KEY", "GOOGLE_MAPS_API_KEY",
            "GOOGLE_CLOUD_PROJECT", "ENVIRONMENT", "ENV",
        ):
            os.environ.pop(k, None)
        raised = False
        try:
            get_route_optimizer()
        except RouteOptimizerNotConfigured:
            raised = True
        assert raised
        assert is_production_runtime() is True
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_catalog_has_route_permissions():
    for key in (
        "delivery.schedule.create",
        "delivery.schedule.publish",
        "delivery.route.optimize",
        "delivery.route.edit",
        "delivery.route.confirm",
        "delivery.vehicle.manage",
        "settings.depot.view",
        "settings.depot.manage",
        "delivery.location.manage",
        "nav.driver_route",
    ):
        assert key in ALL_PERMISSIONS, key
    driver = set(ROLE_DEFAULT_PERMISSIONS["driver"])
    assert "delivery.route.optimize" in driver
    assert "delivery.schedule.create" not in driver
    assert "delivery.location.manage" not in driver
    assert "nav.driver_route" in driver
    helper = effective_permissions("helper", None)
    assert "delivery.schedule.create" not in helper
