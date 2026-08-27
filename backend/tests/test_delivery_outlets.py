"""Outlet location records, reuse, historical snapshot, Places mock/adapter.

No live Google API calls.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from services.delivery_outlets import (
    apply_location_to_stops,
    build_outlet_doc,
    index_outlets,
    plan_is_historical,
    public_outlet,
    resolved_location,
)
from services.delivery_routes import (
    apply_optimize_result,
    confirm_plan,
    my_route_view,
)
from services.delivery_batches import build_batch, mark_published, upsert_driver_ready
from services.delivery_stops import MISSING_LOCATION_REASON, build_queue_stops
from services.delivery_routes import build_optimize_request
from services.places_provider.factory import get_places_provider
from services.places_provider.google import (
    GOOGLE_AUTOCOMPLETE_RPC,
    GOOGLE_PLACES_HOST,
    build_autocomplete_request,
    parse_autocomplete_response,
    parse_place_details_response,
)
from services.places_provider.mock import LocalMockPlacesProvider
from services.route_optimizer.mock import LocalMockRouteOptimizer


def _ready_order(**extra):
    base = {
        "id": extra.pop("id", "ord-a"),
        "po_number": extra.pop("po_number", "PO/A"),
        "customer": extra.pop("customer", "Customer ABC"),
        "outlet": extra.pop("outlet", "Outlet Jakarta"),
        "address": extra.pop("address", "Alamat teks saja"),
        "deadline": "2026-08-30",
        "delivery_status": "Belum Terkirim",
        "line_items": [{"name": "Karton", "quantity": 1000}],
        "total_quantity": 1000,
        "packing_info": {"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        "shipments": extra.pop("shipments", [{"id": "sh-1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 1000}]}]),
        "loading_info": {"lines": [{"line_idx": 0, "loaded_qty": 1000}]},
        "qty_delivered": 0,
        "delivery_location": extra.pop("delivery_location", {}),
        "cancelled": False,
    }
    base.update(extra)
    return base


def _user(uid="u-hp", name="Kepala", role="head_production"):
    return {"id": uid, "user_id": uid, "name": name, "role": role}


def test_case_a_missing_location_shows_belum_ada_lokasi():
    rows = build_queue_stops([_ready_order()])
    assert len(rows) == 1
    assert rows[0]["location_status"] == "need_location"
    assert rows[0]["location_label"] == "Belum Ada Lokasi"
    assert rows[0]["exclude_reason"] == MISSING_LOCATION_REASON
    assert rows[0]["selectable"] is False


def test_case_b_place_fields_stored_on_outlet():
    loc = {
        "place_id": "ChIJ-fixture",
        "formatted_address": "Jl. Asia Afrika No.8, Jakarta",
        "lat": -6.22514,
        "lng": 106.79939,
    }
    outlet = build_outlet_doc(
        customer_name="Customer ABC",
        customer_id="cust-abc",
        name="Outlet Jakarta",
        location=loc,
        user=_user(),
    )
    pub = public_outlet(outlet)
    assert pub["id"]
    assert pub["google_place_id"] == "ChIJ-fixture"
    assert pub["place_id"] == "ChIJ-fixture"
    assert pub["lat"] == -6.22514
    assert pub["lng"] == 106.79939
    assert pub["formatted_address"].startswith("Jl. Asia Afrika")
    assert pub["customer_id"] == "cust-abc"


def test_case_c_same_outlet_reused_on_next_order():
    outlet = build_outlet_doc(
        customer_name="Customer ABC",
        name="Outlet Jakarta",
        location={"lat": -6.22, "lng": 106.79, "place_id": "ChIJ-jkt", "formatted_address": "Jakarta Selatan"},
        user=_user(),
    )
    idx = index_outlets([outlet])
    nxt = _ready_order(id="ord-b", po_number="PO/B", delivery_location={})
    loc = resolved_location(nxt, idx)
    assert loc["location_status"] == "ok"
    assert loc["lat"] == -6.22
    assert loc["place_id"] == "ChIJ-jkt"
    rows = build_queue_stops([nxt], outlets=[outlet])
    assert rows[0]["location_label"] == "Lokasi Siap"
    assert rows[0]["selectable"] is True


def test_multiple_outlets_do_not_collapse_to_one_global():
    jkt = build_outlet_doc(
        customer_name="Customer ABC", name="Outlet Jakarta",
        location={"lat": -6.22, "lng": 106.79, "place_id": "ChIJ-jkt", "formatted_address": "Jakarta"},
        user=_user(),
    )
    bks = build_outlet_doc(
        customer_name="Customer ABC", name="Outlet Bekasi",
        location={"lat": -6.23, "lng": 106.99, "place_id": "ChIJ-bks", "formatted_address": "Bekasi"},
        user=_user(),
    )
    idx = index_outlets([jkt, bks])
    po_jkt = resolved_location(_ready_order(outlet="Outlet Jakarta", delivery_location={}), idx)
    po_bks = resolved_location(_ready_order(id="ord-c", outlet="Outlet Bekasi", delivery_location={}), idx)
    assert po_jkt["place_id"] == "ChIJ-jkt"
    assert po_bks["place_id"] == "ChIJ-bks"
    ambiguous = resolved_location(_ready_order(id="ord-d", outlet="", delivery_location={}), idx)
    assert ambiguous["location_status"] == "need_location"


def test_case_d_optimizer_does_not_silently_drop_missing_location():
    o = _ready_order()
    batch = build_batch(delivery_date="2026-08-27", queue_rows=build_queue_stops([o]), user=_user())
    assert batch["stops"][0]["location_status"] == "need_location"
    mark_published(batch, _user())
    upsert_driver_ready(batch, driver_user_id="u-budi", driver_name="Budi", vehicle={"id": "v1", "plate": "B 1"})
    req = build_optimize_request(batch, {"name": "Gudang Preview", "lat": -6.175, "lng": 106.827})
    result = LocalMockRouteOptimizer().optimize(req)
    assert result.unassigned
    assert result.unassigned[0].stop_id == batch["stops"][0]["stop_id"]
    assert result.unassigned[0].reason == MISSING_LOCATION_REASON
    plan = apply_optimize_result(batch, result, user=_user())
    assigned_ids = {s["stop_id"] for r in plan.get("routes") or [] for s in r.get("stops") or []}
    assert batch["stops"][0]["stop_id"] not in assigned_ids
    assert plan["unassigned"][0]["unassigned_reason"] == MISSING_LOCATION_REASON


def test_case_f_confirmed_route_keeps_snapshot_after_master_edit():
    o = _ready_order(delivery_location={"lat": -6.22, "lng": 106.79, "place_id": "ChIJ-old", "formatted_address": "Lama"})
    batch = build_batch(delivery_date="2026-08-27", queue_rows=build_queue_stops([o]), user=_user())
    mark_published(batch, _user())
    upsert_driver_ready(batch, driver_user_id="u-budi", driver_name="Budi", vehicle={"id": "v1", "plate": "B 1"})
    plan = apply_optimize_result(
        batch, LocalMockRouteOptimizer().optimize(build_optimize_request(batch, {"name": "Gudang Preview", "lat": -6.175, "lng": 106.827})),
        user=_user(),
    )
    confirm_plan(plan, _user())
    assert plan_is_historical(plan) is True
    snap = plan["routes"][0]["stops"][0].get("destination_snapshot") or {}
    snap_lat = snap.get("lat") or plan["routes"][0]["stops"][0]["lat"]
    snap_pid = snap.get("place_id") or plan["routes"][0]["stops"][0]["place_id"]
    assert snap_lat == -6.22
    assert snap_pid == "ChIJ-old"
    outlet = build_outlet_doc(
        customer_name="Customer ABC", name="Outlet Jakarta",
        location={"lat": -6.99, "lng": 107.01, "place_id": "ChIJ-new", "formatted_address": "Baru"},
        user=_user(),
    )
    apply_location_to_stops(plan["routes"][0]["stops"], order_id="ord-a", outlet=outlet)
    view = my_route_view(plan, plan["routes"][0], {"name": "Budi", "id": "u-budi"})
    assert view["stops"][0]["lat"] == snap_lat
    assert view["stops"][0]["place_id"] == snap_pid
    assert view["stops"][0]["lat"] != -6.99
    assert view["stops"][0]["place_id"] != "ChIJ-new"


def test_driver_cannot_manage_location_by_default():
    assert "delivery.location.manage" in ALL_PERMISSIONS
    assert "delivery.location.manage" in ROLE_DEFAULT_PERMISSIONS["head_production"]
    assert "delivery.location.manage" not in ROLE_DEFAULT_PERMISSIONS["driver"]
    assert "delivery.location.manage" not in ROLE_DEFAULT_PERMISSIONS["helper"]
    assert "delivery.schedule.create" not in ROLE_DEFAULT_PERMISSIONS["driver"]


def test_places_mock_search_and_details_no_google():
    p = LocalMockPlacesProvider()
    res = p.search("JUS.IN Test")
    assert res.provider == "mock"
    assert res.suggestions
    assert res.suggestions[0].place_id == "mock-place-jusin-test"
    assert "JUS.IN Test Outlet" in res.suggestions[0].label
    det = p.details(res.suggestions[0].place_id)
    assert det.name == "JUS.IN Test Outlet"
    assert det.lat == -6.15801
    assert det.lng == 106.91002
    assert det.place_id == "mock-place-jusin-test"
    assert "lokasi uji" in det.formatted_address.lower()
    bekasi = p.search("bekasi")
    assert bekasi.suggestions
    assert all(s.place_id.startswith("mock-place-") for s in bekasi.suggestions)


def test_google_places_adapter_parses_official_schema_without_network():
    body = build_autocomplete_request("senayan")
    assert body["input"] == "senayan"
    assert GOOGLE_PLACES_HOST == "https://places.googleapis.com"
    assert GOOGLE_AUTOCOMPLETE_RPC == "/v1/places:autocomplete"
    suggestions = parse_autocomplete_response({
        "suggestions": [{
            "placePrediction": {
                "place": "places/ChIJ5YQQf1GHhYARPKG7WLIaOko",
                "placeId": "ChIJ5YQQf1GHhYARPKG7WLIaOko",
                "text": {"text": "Plaza Senayan, Jakarta, Indonesia"},
            }
        }]
    })
    assert suggestions[0].place_id == "ChIJ5YQQf1GHhYARPKG7WLIaOko"
    det = parse_place_details_response({
        "id": "ChIJ5YQQf1GHhYARPKG7WLIaOko",
        "formattedAddress": "Jl. Asia Afrika No.8, Jakarta",
        "location": {"latitude": -6.22514, "longitude": 106.79939},
        "displayName": {"text": "Plaza Senayan"},
    })
    assert det.lat == -6.22514
    assert det.lng == 106.79939
    assert det.place_id.startswith("ChIJ")


def test_places_factory_local_is_mock():
    prev = {k: os.environ.get(k) for k in ("PLACES_PROVIDER", "GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY", "APP_ENV")}
    try:
        os.environ["PLACES_PROVIDER"] = "mock"
        os.environ.pop("GOOGLE_PLACES_API_KEY", None)
        os.environ.pop("GOOGLE_MAPS_API_KEY", None)
        os.environ.pop("APP_ENV", None)
        assert get_places_provider().name == "mock"
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_places_factory_production_no_silent_mock():
    from services.places_provider.base import PlacesNotConfigured
    prev = {k: os.environ.get(k) for k in ("PLACES_PROVIDER", "GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY", "APP_ENV")}
    try:
        os.environ["APP_ENV"] = "production"
        os.environ.pop("PLACES_PROVIDER", None)
        os.environ.pop("GOOGLE_PLACES_API_KEY", None)
        os.environ.pop("GOOGLE_MAPS_API_KEY", None)
        try:
            get_places_provider()
            assert False, "production without Places key must not silent-mock"
        except PlacesNotConfigured as e:
            assert "silent mock" in str(e).lower() or "wajib" in str(e).lower()
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
