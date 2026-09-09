"""Internal Accurate creator resolution. No live Accurate. No hardcoded parser map."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.accurate import (
    _normalize_accurate_so,
    clear_accurate_name_cache,
)
from services.accurate_creator import (
    aggregate_creator_scan,
    apply_creator_to_so,
    clear_creator_map_cache,
    load_verified_creator_map,
    loader_call_count,
    normalize_scan_limit,
    parse_verified_map,
    reset_loader_call_count,
    resolve_accurate_creator,
    sanitize_settings,
    set_mapping_loader,
    upsert_verified_mapping,
    usable_display_name,
)
from tests.test_accurate_http import _FakeHTTP, _FakeResp, _await, _live_so_client, _so_payload
from tests.test_accurate_so_mapping import CAKE_IN_LOVE_SO_DETAIL

VERIFIED_NABILA = {
    "1248273": {
        "accurate_created_by_id": "1248273",
        "display_name": "nabila salsabila",
        "verified": True,
    }
}


def _reset():
    clear_creator_map_cache()
    reset_loader_call_count()
    set_mapping_loader(None)
    clear_accurate_name_cache()


def setup_function():
    _reset()


def teardown_function():
    _reset()


def test_createdBy_1248273_with_verified_mapping():
    so = _normalize_accurate_so(CAKE_IN_LOVE_SO_DETAIL, creator_mappings=VERIFIED_NABILA)
    assert so["created_by_user"] == "nabila salsabila"
    assert so["_created_by_id"] == "1248273"
    assert so["sales"] == "Dimas Aji Prasetya"
    assert so["created_by_user"] != so["sales"]
    assert so["recipient"] == "CAKE IN LOVE"
    assert so["expedition"] == "OUTLET"
    assert so["delivery_zone"] == "JS"
    assert so["line_items"][0]["color"] is None
    assert so["estimated_ship_date"] == "2026-09-05"


def test_unknown_createdBy_stays_null():
    raw = {
        "id": 1,
        "number": "SO-1",
        "createdBy": 999,
        "printUserName": "Dimas Aji Prasetya",
        "userPrinted": 488125,
        "salesman": {"name": "Dimas Aji Prasetya"},
        "customer": {"name": "A"},
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw, creator_mappings=VERIFIED_NABILA)
    assert so["created_by_user"] is None
    assert so["sales"] == "Dimas Aji Prasetya"


def test_printUserName_and_salesman_not_used():
    name = resolve_accurate_creator(
        1248273,
        live_name=None,
        mappings=VERIFIED_NABILA,
    )
    assert name == "nabila salsabila"
    raw = dict(CAKE_IN_LOVE_SO_DETAIL)
    so = _normalize_accurate_so(raw, creator_mappings=VERIFIED_NABILA)
    assert so["created_by_user"] == "nabila salsabila"
    assert so["created_by_user"] != so["sales"]
    assert so["created_by_user"] != raw["printUserName"]
    assert so["created_by_user"] != str(raw["userPrinted"])


def test_unverified_mapping_not_used():
    mappings = {
        "1248273": {
            "accurate_created_by_id": "1248273",
            "display_name": "nabila salsabila",
            "verified": False,
        }
    }
    so = _normalize_accurate_so(CAKE_IN_LOVE_SO_DETAIL, creator_mappings=mappings)
    assert so["created_by_user"] is None
    assert parse_verified_map({"mappings": [mappings["1248273"]]}) == {}


def test_numeric_id_never_becomes_name():
    assert usable_display_name("1248273") is None
    assert usable_display_name(1248273) is None
    assert resolve_accurate_creator(
        1248273,
        mappings={"1248273": {"display_name": "1248273", "verified": True}},
    ) is None


def test_live_name_wins_over_mapping():
    raw = {
        "id": 1,
        "number": "SO-1",
        "createdBy": 1248273,
        "userName": "nama dari accurate",
        "customer": {"name": "A"},
        "detailItem": [{"quantity": 1, "item": {"id": 1, "name": "Item"}}],
    }
    so = _normalize_accurate_so(raw, creator_mappings=VERIFIED_NABILA)
    assert so["created_by_user"] == "nama dari accurate"


def test_sanitize_drops_blank_and_numeric_names():
    doc = sanitize_settings({
        "mappings": [
            {"accurate_created_by_id": "1248273", "display_name": "nabila salsabila", "verified": True},
            {"accurate_created_by_id": "x", "display_name": "bad"},
            {"accurate_created_by_id": "1", "display_name": "1"},
            {"accurate_created_by_id": "1248273", "display_name": "dup"},
        ]
    })
    assert [m["accurate_created_by_id"] for m in doc["mappings"]] == ["1248273"]
    assert doc["mappings"][0]["display_name"] == "nabila salsabila"


def test_batch_same_createdBy_loads_mapping_once(monkeypatch):
    async def loader():
        return {"mappings": [
            {"accurate_created_by_id": "1248273", "display_name": "nabila salsabila", "verified": True}
        ]}

    set_mapping_loader(loader)
    c = _live_so_client(monkeypatch)
    extra = {
        "createdBy": 1248273,
        "printUserName": "Dimas Aji Prasetya",
        "userPrinted": 488125,
        "salesman": {"name": "Bukan Creator"},
        "customer": {"name": "CAKE IN LOVE", "shipAddress": {"picName": "CAKE IN LOVE"}},
        "shipment": {"id": "150", "name": "OUTLET"},
        "charField3": "JS",
        "shipDate": "05/09/2026",
        "description": "SABLON WARNA HITAM",
        "toAddress": "59G2+JPW Yogyakarta",
    }
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-A": [_FakeResp(200, _so_payload("SO-A", extra_d=extra))],
        "GET:sales-order/detail.do:SO-B": [_FakeResp(200, _so_payload("SO-B", extra_d=extra))],
        "GET:sales-order/detail.do:SO-C": [_FakeResp(200, _so_payload("SO-C", extra_d=extra))],
    }
    rows = _await(c.fetch_sales_order_results(["SO-A", "SO-B", "SO-C"]))
    assert [r["success"] for r in rows] == [True, True, True]
    assert all(r["data"]["created_by_user"] == "nabila salsabila" for r in rows)
    assert all(r["data"]["sales"] == "Bukan Creator" for r in rows)
    assert all(r["data"]["recipient"] == "CAKE IN LOVE" for r in rows)
    assert all(r["data"]["delivery_zone"] == "JS" for r in rows)
    assert all(r["data"]["expedition"] == "OUTLET" for r in rows)
    assert loader_call_count() == 1
    assert [g for g in _FakeHTTP.gets if "/user/" in g["url"]] == []


def test_cache_hit_does_not_reload(monkeypatch):
    async def loader():
        return {"mappings": [
            {"accurate_created_by_id": "1248273", "display_name": "nabila salsabila", "verified": True}
        ]}

    set_mapping_loader(loader)
    _await(load_verified_creator_map())
    assert loader_call_count() == 1
    _await(load_verified_creator_map())
    assert loader_call_count() == 1


def test_apply_does_not_fail_preview_without_mapping():
    so = _normalize_accurate_so(CAKE_IN_LOVE_SO_DETAIL)
    apply_creator_to_so(so, {})
    assert so["created_by_user"] is None
    assert so["customer"] == "CAKE IN LOVE"


def test_aggregate_dedupes_createdBy_and_splits_status():
    rows = [
        {"accurate_created_by_id": "1248273", "po_number": "SO-1", "trans_date": "2026-08-28"},
        {"accurate_created_by_id": 1248273, "po_number": "SO-2", "trans_date": "2026-08-27"},
        {"accurate_created_by_id": "1248273", "po_number": "SO-3", "trans_date": "2026-08-26"},
        {"accurate_created_by_id": "1248273", "po_number": "SO-4", "trans_date": "2026-08-20"},
        {"accurate_created_by_id": "999001", "po_number": "SO-5", "trans_date": "2026-08-25"},
        {"accurate_created_by_id": "999001", "po_number": "SO-6", "trans_date": "2026-08-24"},
    ]
    out = aggregate_creator_scan(rows, VERIFIED_NABILA, scanned=6, failed=0, limit=100)
    assert out["wrote_mappings"] is False
    assert len(out["verified"]) == 1
    verified = out["verified"][0]
    assert verified["accurate_created_by_id"] == "1248273"
    assert verified["so_count"] == 4
    assert verified["sample_po_numbers"] == ["SO-1", "SO-2", "SO-3"]
    assert verified["last_so_date"] == "2026-08-28"
    assert verified["display_name"] == "nabila salsabila"
    assert verified["status"] == "verified"
    assert len(out["unknown"]) == 1
    unknown = out["unknown"][0]
    assert unknown["accurate_created_by_id"] == "999001"
    assert unknown["so_count"] == 2
    assert unknown["display_name"] is None
    assert unknown["verified"] is False
    assert unknown["status"] == "unknown"


def test_aggregate_does_not_use_printUserName_or_salesman_fields():
    rows = [{
        "accurate_created_by_id": "999001",
        "po_number": "SO-1",
        "trans_date": "2026-08-28",
        "printUserName": "Dimas Aji Prasetya",
        "salesman": "Dimas Aji Prasetya",
        "userPrinted": 488125,
    }]
    out = aggregate_creator_scan(rows, {}, scanned=1, failed=1, limit=100)
    assert out["unknown"][0]["display_name"] is None
    assert out["unknown"][0]["accurate_created_by_id"] != "488125"


def test_upsert_does_not_duplicate_existing_id():
    doc = {"mappings": [
        {"accurate_created_by_id": "1248273", "display_name": "nabila salsabila", "verified": True},
    ]}
    once = upsert_verified_mapping(doc, "1248273", "nabila salsabila")
    twice = upsert_verified_mapping(once, 1248273, "nabila salsabila")
    assert [m["accurate_created_by_id"] for m in twice["mappings"]] == ["1248273"]
    extra = upsert_verified_mapping(twice, "999001", "user baru")
    assert [m["accurate_created_by_id"] for m in extra["mappings"]] == ["1248273", "999001"]
    assert all(m["verified"] is True for m in extra["mappings"])


def test_normalize_scan_limit_bounded():
    assert normalize_scan_limit(None) == 100
    assert normalize_scan_limit(100) == 100
    assert normalize_scan_limit(250) == 250
    assert normalize_scan_limit(500) == 500
    assert normalize_scan_limit(9999) == 100
    assert normalize_scan_limit("nope") == 100
