"""Accurate Penerimaan Barang → Pembelian Barang. Mock / fake HTTP only."""
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.accurate import (
    ACCURATE_RECEIVE_ITEM_DETAIL_PATH,
    ACCURATE_RECEIVE_ITEM_ENDPOINT_AVAILABLE,
    ACCURATE_RECEIVE_ITEM_LIST_PATH,
    AccurateClient,
    _ITEM_DETAIL_CONCURRENCY,
    _MOCK_RECEIVE_ITEMS,
    normalize_receive_item,
    receive_item_is_valid,
)
from services.current_stock import PURCHASE_SOURCE_ACCURATE, build_stock_rows, compute_stock_current
from services.purchase_receipts import (
    COLLECTION,
    SYNC_META_COLLECTION,
    aggregate_received_qty,
    public_receipt,
    purchase_source_from_meta,
    sync_purchase_receipts,
    upsert_receipts,
)


def _await(coro):
    return asyncio.run(coro)


def _norm(rid, number, lines, status="APPROVED"):
    return normalize_receive_item({
        "id": rid,
        "number": number,
        "transDate": "25/08/2026",
        "approvalStatus": status,
        "detailItem": [
            {"id": i + 1, "quantity": qty, "item": {"id": aid}, "itemUnitName": "pcs"}
            for i, (aid, qty) in enumerate(lines)
        ],
    })


class _Cursor:
    def __init__(self, docs):
        self._docs = [dict(d) for d in docs]

    async def to_list(self, n):
        return [dict(d) for d in self._docs[: int(n)]]


class MemCol:
    def __init__(self):
        self.docs = []

    def _match(self, doc, query):
        if not query:
            return True
        for k, v in query.items():
            if doc.get(k) != v:
                return False
        return True

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if self._match(d, query):
                out = dict(d)
                if proj and proj.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    def find(self, query, proj=None):
        matched = []
        for d in self.docs:
            if self._match(d, query):
                out = dict(d)
                if proj and proj.get("_id") == 0:
                    out.pop("_id", None)
                matched.append(out)
        return _Cursor(matched)

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if self._match(d, query):
                d.update(update.get("$set") or {})
                return
        if not upsert:
            return
        new = dict(query)
        new.update(update.get("$setOnInsert") or {})
        new.update(update.get("$set") or {})
        self.docs.append(new)

    async def count_documents(self, query):
        return sum(1 for d in self.docs if self._match(d, query))


class FakeDB:
    def __init__(self):
        self._cols = {
            COLLECTION: MemCol(),
            SYNC_META_COLLECTION: MemCol(),
            "master_items": MemCol(),
        }

    def __getitem__(self, name):
        if name not in self._cols:
            self._cols[name] = MemCol()
        return self._cols[name]

    @property
    def master_items(self):
        return self["master_items"]


class FakeClient:
    def __init__(self, receipts=None, failed=None, complete=True, mode="mock", exc=None):
        self.auth_mode = mode
        self._receipts = receipts
        self._failed = failed or []
        self._complete = complete
        self._exc = exc
        self.calls = 0

    async def fetch_all_receive_items(self):
        self.calls += 1
        if self._exc:
            raise self._exc
        return {
            "receipts": list(self._receipts or []),
            "failed": list(self._failed),
            "complete": self._complete,
            "mode": self.auth_mode,
            "detail_required": True,
        }


def test_endpoint_constants_official():
    assert ACCURATE_RECEIVE_ITEM_ENDPOINT_AVAILABLE is True
    assert ACCURATE_RECEIVE_ITEM_LIST_PATH == "/accurate/api/receive-item/list.do"
    assert ACCURATE_RECEIVE_ITEM_DETAIL_PATH == "/accurate/api/receive-item/detail.do"


def test_normalize_quantity_not_unit_price_or_item_no():
    n = normalize_receive_item({
        "id": 1,
        "number": "R1",
        "transDate": "25/08/2026",
        "approvalStatus": "APPROVED",
        "detailItem": [{
            "id": 9,
            "quantity": 2000,
            "unitPrice": 99999,
            "itemNo": "WRONG-AS-ID",
            "item": {"id": 101, "no": "PC-12"},
        }],
    })
    assert n["accurate_receipt_id"] == "1"
    assert n["lines"][0]["accurate_item_id"] == "101"
    assert n["lines"][0]["received_qty"] == 2000
    assert n["lines"][0]["accurate_line_id"] == "9"


def test_line_id_fallback_deterministic():
    n = normalize_receive_item({
        "id": 77,
        "number": "R77",
        "detailItem": [{"quantity": 4, "item": {"id": 101}}],
    })
    assert n["lines"][0]["accurate_line_id"] == "77:0"


def test_rejected_is_invalid():
    raw = {"id": 2, "approvalStatus": "REJECTED", "detailItem": [{"quantity": 8, "item": {"id": 101}}]}
    assert receive_item_is_valid(raw) is False
    n = normalize_receive_item(raw)
    assert n["valid"] is False


def test_single_receipt_aggregate():
    recs = [_norm(100, "R1", [(101, 2000)])]
    bucket, _ = aggregate_received_qty(recs)
    assert bucket["a:101"] == 2000


def test_idempotent_upsert():
    incoming = [_norm(100, "R1", [(101, 2000)])]
    first = upsert_receipts([], incoming, complete=True)
    second = upsert_receipts(first["docs"], incoming, complete=True)
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["unchanged"] == 1
    bucket, _ = aggregate_received_qty(second["docs"])
    assert bucket["a:101"] == 2000


def test_edited_receipt_overwrites():
    a = [_norm(100, "R1", [(101, 2000)])]
    b = [_norm(100, "R1", [(101, 2500)])]
    first = upsert_receipts([], a, complete=True)
    second = upsert_receipts(first["docs"], b, complete=True)
    assert second["updated"] == 1
    bucket, _ = aggregate_received_qty(second["docs"])
    assert bucket["a:101"] == 2500
    assert bucket["a:101"] != 4500


def test_multiple_receipts_sum():
    incoming = [_norm(100, "R1", [(101, 2000)]), _norm(101, "R2", [(101, 1500)])]
    out = upsert_receipts([], incoming, complete=True)
    bucket, _ = aggregate_received_qty(out["docs"])
    assert bucket["a:101"] == 3500


def test_partial_not_po():
    incoming = [_norm(1, "R1", [(101, 4000)])]
    bucket, _ = aggregate_received_qty(incoming)
    assert bucket["a:101"] == 4000
    assert bucket["a:101"] != 10000


def test_same_name_different_item_id():
    incoming = [_norm(1, "R1", [(101, 2000), (202, 5000)])]
    bucket, _ = aggregate_received_qty(incoming)
    assert bucket["a:101"] == 2000
    assert bucket["a:202"] == 5000


def test_invalid_receipt_excluded():
    incoming = [
        _norm(1, "R1", [(101, 2000)], status="APPROVED"),
        _norm(2, "R2", [(101, 9000)], status="REJECTED"),
    ]
    bucket, _ = aggregate_received_qty(incoming)
    assert bucket["a:101"] == 2000


def test_failed_sync_preserves_existing():
    db = FakeDB()
    good = [_norm(100, "R1", [(101, 2000)]), _norm(101, "R2", [(101, 1500)])]
    r1 = _await(sync_purchase_receipts(db, FakeClient(good)))
    assert r1["ok"] is True
    assert r1["purchase_source"] == "ACCURATE"
    fail = _await(sync_purchase_receipts(db, FakeClient(exc=TimeoutError("page 2"))))
    assert fail["ok"] is False
    assert fail["preserved"] is True
    bucket, _ = aggregate_received_qty(db[COLLECTION].docs)
    assert bucket["a:101"] == 3500
    assert purchase_source_from_meta(db[SYNC_META_COLLECTION].docs[0]) == "ACCURATE"


def test_sync_idempotent_and_formula():
    db = FakeDB()
    recs = [_norm(100, "R1", [(101, 2000)]), _norm(101, "R2", [(101, 1500)])]
    a = _await(sync_purchase_receipts(db, FakeClient(recs)))
    b = _await(sync_purchase_receipts(db, FakeClient(recs)))
    assert a["created"] == 2
    assert b["created"] == 0
    assert b["unchanged"] == 2
    assert b["updated"] == 0
    assert len(db[COLLECTION].docs) == 2
    rows = build_stock_rows(
        [{"id": "m1", "accurate_item_id": "101", "item_name": "Cup", "item_code": "PC-12", "unit": "pcs"}],
        [{"cancelled": False, "line_items": [{"accurate_item_id": "101", "quantity": 3000, "production_process": "print"}],
          "operator_splits": [{"line_idx": 0, "qty_completed": 4000}]}],
        [{"accurate_item_id": "101", "manual_current_qty": 10000, "manual_purchase_qty": 1000}],
        db[COLLECTION].docs,
        purchase_source=PURCHASE_SOURCE_ACCURATE,
    )
    assert rows[0]["purchase_qty"] == 3500
    assert rows[0]["stock_current"] == 6500
    assert compute_stock_current(3000, 10000, 3500, 4000) == 6500


def test_mock_client_r1_r2_r3():
    c = AccurateClient()
    assert c.auth_mode == "mock"
    payload = _await(c.fetch_all_receive_items())
    assert payload["mode"] == "mock"
    assert payload["complete"] is True
    assert payload["detail_required"] is True
    by = {r["receipt_number"]: r for r in payload["receipts"]}
    assert by["R1"]["lines"][0]["received_qty"] == 2000
    assert by["R2"]["lines"][0]["received_qty"] == 1500
    assert by["R3"]["lines"][0]["accurate_item_id"] == "202"
    assert by["R3"]["lines"][0]["received_qty"] == 4000
    bucket, _ = aggregate_received_qty(payload["receipts"])
    assert bucket["a:101"] == 3500
    assert bucket["a:202"] == 4000
    assert len(_MOCK_RECEIVE_ITEMS) == 3


def test_public_receipt_uses_master_display_not_name_identity():
    rec = _norm(100, "R1", [(101, 2000)])
    pub = public_receipt(rec, {"101": {"id": "m1", "item_code": "PC-12", "item_name": "Paper cup 12oz", "unit": "pcs"}})
    assert pub["receipt_number"] == "R1"
    assert pub["line_count"] == 1
    assert pub["total_qty"] == 2000
    assert pub["source"] == "accurate"
    assert pub["lines"][0]["accurate_item_id"] == "101"
    assert pub["lines"][0]["item_code"] == "PC-12"
    assert pub["lines"][0]["master_item_id"] == "m1"


class _FakeHTTPResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    @property
    def is_error(self):
        return self.status_code >= 400

    def json(self):
        return self._payload


class _CapturingAsyncClient:
    captured = []
    created = 0
    inflight = 0
    max_inflight = 0
    list_pages = {}
    details = {}
    fail_page = None
    fail_detail_id = None

    def __init__(self, *args, **kwargs):
        _CapturingAsyncClient.created += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None, params=None):
        rec = {
            "url": url,
            "params": dict(params or {}),
            "header_names": sorted((headers or {}).keys()),
            "timestamp": (headers or {}).get("X-Api-Timestamp"),
        }
        _CapturingAsyncClient.captured.append(rec)
        assert "fields" not in rec["params"]
        assert "Authorization" in (headers or {})
        if url.endswith("/receive-item/list.do"):
            page = int(rec["params"].get("sp.page") or 1)
            if _CapturingAsyncClient.fail_page == page:
                req = httpx.Request("GET", url)
                raise httpx.ReadTimeout("page timeout", request=req)
            payload = _CapturingAsyncClient.list_pages.get(page) or {
                "s": True, "d": [], "sp": {"page": page, "pageCount": 1, "rowCount": 0},
            }
            return _FakeHTTPResponse(200, payload)
        if url.endswith("/receive-item/detail.do"):
            _CapturingAsyncClient.inflight += 1
            try:
                _CapturingAsyncClient.max_inflight = max(
                    _CapturingAsyncClient.max_inflight, _CapturingAsyncClient.inflight,
                )
                await asyncio.sleep(0.01)
                oid = str((params or {}).get("id"))
                if _CapturingAsyncClient.fail_detail_id and oid == str(_CapturingAsyncClient.fail_detail_id):
                    return _FakeHTTPResponse(500, {"s": False})
                raw = _CapturingAsyncClient.details.get(oid)
                if not raw:
                    return _FakeHTTPResponse(404, {"s": False})
                return _FakeHTTPResponse(200, {"s": True, "d": raw})
            finally:
                _CapturingAsyncClient.inflight -= 1
        raise AssertionError(f"unexpected Accurate URL {url}")


def _live_receipt_client(monkeypatch):
    import services.accurate as acc
    _CapturingAsyncClient.captured = []
    _CapturingAsyncClient.created = 0
    _CapturingAsyncClient.inflight = 0
    _CapturingAsyncClient.max_inflight = 0
    _CapturingAsyncClient.fail_page = None
    _CapturingAsyncClient.fail_detail_id = None
    monkeypatch.setattr(acc.httpx, "AsyncClient", _CapturingAsyncClient)

    async def _nosleep(_s):
        return None

    monkeypatch.setattr(acc.asyncio, "sleep", _nosleep)
    c = AccurateClient()
    c.api_token = "unit-test-token"
    c.api_signature_secret = "unit-test-secret"
    c.db_id = "1"
    c.client_secret = ""
    c.access_token = ""
    c.refresh_token = ""
    c.open_db_calls = []
    c.token_calls = []

    async def _tok():
        c.token_calls.append(1)
        return "unit-test-token"

    async def _open(token):
        c.open_db_calls.append(token)
        return "https://odin.accurate.id", ""

    c._obtain_token = _tok
    c._open_db = _open
    assert c.is_live
    return c


def test_live_receive_item_session_pagination_concurrency(monkeypatch):
    pages = {
        1: {"s": True, "d": [{"id": i} for i in range(1, 5)],
            "sp": {"page": 1, "pageSize": 4, "pageCount": 2, "rowCount": 8}},
        2: {"s": True, "d": [{"id": i} for i in range(5, 9)],
            "sp": {"page": 2, "pageSize": 4, "pageCount": 2, "rowCount": 8}},
    }
    details = {}
    for i in range(1, 9):
        details[str(i)] = {
            "id": i, "number": f"R{i}", "transDate": "25/08/2026",
            "approvalStatus": "APPROVED",
            "detailItem": [{"id": i * 10, "quantity": 100 * i, "item": {"id": 101}}],
        }
    _CapturingAsyncClient.list_pages = pages
    _CapturingAsyncClient.details = details
    c = _live_receipt_client(monkeypatch)
    payload = _await(c.fetch_all_receive_items())
    assert payload["mode"] == "live"
    assert payload["complete"] is True
    assert payload["detail_required"] is True
    assert len(payload["receipts"]) == 8
    urls = [r["url"] for r in _CapturingAsyncClient.captured]
    assert urls[0].endswith("/receive-item/list.do")
    assert sum(1 for u in urls if u.endswith("/receive-item/list.do")) == 2
    assert sum(1 for u in urls if u.endswith("/receive-item/detail.do")) == 8
    assert all("fields" not in r["params"] for r in _CapturingAsyncClient.captured)
    assert len(c.token_calls) == 1
    assert len(c.open_db_calls) == 1
    assert _CapturingAsyncClient.created == 1
    assert _CapturingAsyncClient.max_inflight <= _ITEM_DETAIL_CONCURRENCY
    assert _CapturingAsyncClient.max_inflight >= 1
    stamps = [r["timestamp"] for r in _CapturingAsyncClient.captured if r["timestamp"]]
    assert stamps
    assert "unit-test-token" not in str(_CapturingAsyncClient.captured)
    assert "unit-test-secret" not in str(_CapturingAsyncClient.captured)


def test_live_list_page_timeout_does_not_return_partial_as_complete(monkeypatch):
    _CapturingAsyncClient.list_pages = {
        1: {"s": True, "d": [{"id": 1}], "sp": {"page": 1, "pageCount": 2, "rowCount": 2}},
    }
    _CapturingAsyncClient.details = {
        "1": {"id": 1, "number": "R1", "detailItem": [{"quantity": 2000, "item": {"id": 101}}]},
    }
    c = _live_receipt_client(monkeypatch)
    _CapturingAsyncClient.fail_page = 2
    try:
        _await(c.fetch_all_receive_items())
        assert False, "expected timeout"
    except RuntimeError:
        pass
    db = FakeDB()
    db[COLLECTION].docs.append({
        "accurate_receipt_id": "keep", "valid": True,
        "lines": [{"accurate_item_id": "101", "received_qty": 3500}],
    })
    out = _await(sync_purchase_receipts(db, FakeClient(exc=RuntimeError("timeout"))))
    assert out["preserved"] is True
    assert db[COLLECTION].docs[0]["lines"][0]["received_qty"] == 3500
