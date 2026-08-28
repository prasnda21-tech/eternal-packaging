"""Accurate creator auto-discovery scan. No live Accurate. No user-by-id calls."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.accurate_creator import (
    set_mapping_loader,
    upsert_verified_mapping,
)
from tests.test_accurate_http import _FakeHTTP, _FakeResp, _await, _live_so_client, _so_payload
from tests.test_accurate_creator import _reset


def setup_function():
    _reset()


def teardown_function():
    _reset()


STORE = {
    "mappings": [
        {"accurate_created_by_id": "1248273", "display_name": "nabila salsabila", "verified": True}
    ]
}


def _detail_extra(created_by):
    return {
        "createdBy": created_by,
        "printUserName": "Dimas Aji Prasetya",
        "userPrinted": 488125,
        "salesman": {"name": "Bukan Creator"},
        "transDate": "28/08/2026",
    }


def _list_body(count, *, page=1, page_count=1):
    start = (page - 1) * 100
    rows = [
        {"id": i, "number": f"SO-{i:03d}", "transDate": "28/08/2026"}
        for i in range(start + 1, start + count + 1)
    ]
    return {"s": True, "d": rows, "sp": {"rowCount": count, "pageCount": page_count, "page": page}}


def _detail_fn(fail_ids=None):
    fail_ids = set(fail_ids or [])

    def _fn(_url, params):
        oid = int(params["id"])
        if oid in fail_ids:
            return _FakeResp(400, {"s": False, "d": ["bad request"]})
        created = 1248273 if oid <= 70 else 999001
        return _FakeResp(200, _so_payload(f"SO-{oid:03d}", extra_d=_detail_extra(created)))

    return _fn


def _script_scan(n=100, fail_ids=None, page_count=1):
    slots = max(n * 3, 20)
    pages = []
    remaining = n
    page = 1
    while remaining > 0:
        chunk = min(100, remaining)
        pages.append(_FakeResp(200, _list_body(chunk, page=page, page_count=page_count)))
        remaining -= chunk
        page += 1
    return {
        "GET:sales-order/list.do": pages,
        "GET:sales-order/detail.do": [_detail_fn(fail_ids) for _ in range(slots)],
    }


def test_scan_100_dedupes_and_does_not_write_mapping(monkeypatch):
    store = copy.deepcopy(STORE)

    async def loader():
        return store

    set_mapping_loader(loader)
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = _script_scan(100)
    before = copy.deepcopy(store)
    result = _await(c.scan_sales_order_creators(100))
    assert result["wrote_mappings"] is False
    assert store == before
    assert result["scanned"] == 100
    assert result["failed"] == 0
    assert result["list_requests"] == 1
    assert result["detail_requests"] == 100
    assert _FakeHTTP.created == 1
    assert len(c.open_db_calls) == 1
    list_gets = [g for g in _FakeHTTP.gets if "sales-order/list.do" in g["url"]]
    detail_gets = [g for g in _FakeHTTP.gets if "sales-order/detail.do" in g["url"]]
    user_gets = [g for g in _FakeHTTP.gets if "/user/" in g["url"] or "userinfo" in g["url"]]
    assert len(list_gets) == 1
    assert list_gets[0]["params"].get("sp.pageSize") == 100
    assert len(detail_gets) == 100
    assert user_gets == []
    assert len(result["verified"]) == 1
    assert result["verified"][0]["accurate_created_by_id"] == "1248273"
    assert result["verified"][0]["so_count"] == 70
    assert result["verified"][0]["display_name"] == "nabila salsabila"
    assert result["verified"][0]["status"] == "verified"
    assert len(result["unknown"]) == 1
    unknown = result["unknown"][0]
    assert unknown["accurate_created_by_id"] == "999001"
    assert unknown["so_count"] == 30
    assert unknown["display_name"] is None
    assert unknown["verified"] is False
    assert unknown["status"] == "unknown"
    assert "Dimas Aji Prasetya" not in str(unknown)
    assert unknown["accurate_created_by_id"] != "488125"


def test_scan_partial_detail_failure_still_returns(monkeypatch):
    async def loader():
        return copy.deepcopy(STORE)

    set_mapping_loader(loader)
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = _script_scan(8, fail_ids={3})
    result = _await(c.scan_sales_order_creators(100))
    assert result["scanned"] == 7
    assert result["failed"] == 1
    assert result["wrote_mappings"] is False
    counts = {r["accurate_created_by_id"]: r["so_count"] for r in result["verified"] + result["unknown"]}
    assert counts["1248273"] == 7
    assert result["unknown"] == []


def test_scan_unknown_not_auto_verified(monkeypatch):
    async def loader():
        return {"mappings": []}

    set_mapping_loader(loader)
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = _script_scan(5)
    result = _await(c.scan_sales_order_creators(100))
    assert result["verified"] == []
    assert result["unknown"][0]["verified"] is False
    assert result["unknown"][0]["display_name"] is None


def test_verify_after_scan_does_not_duplicate_mapping():
    doc = copy.deepcopy(STORE)
    once = upsert_verified_mapping(doc, "999001", "user baru")
    twice = upsert_verified_mapping(once, "999001", "user baru")
    ids = [m["accurate_created_by_id"] for m in twice["mappings"]]
    assert ids == ["1248273", "999001"]
    assert ids.count("999001") == 1
    assert ids.count("1248273") == 1
