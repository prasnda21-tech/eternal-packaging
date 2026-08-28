"""Accurate Sales Order HTTP reliability: retry, token refresh, partial failure.

All upstream Accurate calls are mocked. Never hits production Accurate.
"""
import asyncio
import logging
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.accurate import (
    ACCURATE_MAX_TRANSIENT_ATTEMPTS,
    ACCURATE_SO_FETCH_CONCURRENCY,
    AccurateClient,
    AccurateErrorCategory,
    AccurateRequestError,
    _MOCK_SALES_ORDERS,
    _classify_s_false,
    _normalize_accurate_so,
    coerce_accurate_quantity,
    sanitize_accurate_error,
    validate_normalized_so,
)


def _await(coro):
    return asyncio.run(coro)


def _so_payload(number, *, customer="Kopi Rumah", qty=10, item_id=101, extra_d=None):
    d = {
        "id": abs(hash(number)) % 100000,
        "number": number,
        "customer": {"name": customer},
        "detailItem": [
            {
                "quantity": qty,
                "itemUnitName": "pcs",
                "item": {"id": item_id, "no": "PC-12", "name": "Paper cup 12oz"},
            }
        ],
    }
    if extra_d:
        d.update(extra_d)
    return {"s": True, "d": d}


class _FakeResp:
    def __init__(self, status_code, payload=None, headers=None, json_error=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.is_error = status_code >= 400
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("invalid json")
        return self._payload


class _FakeHTTP:
    """httpx.AsyncClient stand-in. Script GET/POST by URL suffix + params."""

    created = 0
    gets = []
    posts = []
    # list of actions consumed FIFO per key (url suffix)
    # action: exception instance | _FakeResp | callable(url, params) -> _FakeResp|exc
    script = {}

    def __init__(self, *args, **kwargs):
        type(self).created += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _pop(self, key):
        script = type(self).script
        if key not in script:
            return None
        q = script[key]
        return q.pop(0) if q else None

    def _resolve(self, url, params, method):
        suffix = url.split("/accurate/api/")[-1] if "/accurate/api/" in url else url.rsplit("/", 1)[-1]
        so = (params or {}).get("number") or (params or {}).get("id")
        for key in (f"{method}:{suffix}:{so}", f"{method}:{suffix}", suffix, method):
            act = self._pop(key)
            if act is not None:
                return act
        return None

    async def get(self, url, headers=None, params=None):
        rec = {
            "url": url,
            "params": dict(params or {}),
            "header_names": sorted((headers or {}).keys()),
        }
        type(self).gets.append(rec)
        act = self._resolve(url, params, "GET")
        if callable(act):
            act = act(url, params)
        if isinstance(act, BaseException):
            raise act
        if act is None:
            number = (params or {}).get("number") or (params or {}).get("id") or "SO-UNKNOWN"
            return _FakeResp(200, _so_payload(str(number)))
        return act

    async def post(self, url, headers=None, data=None, **kwargs):
        # Record keys only — never token values.
        type(self).posts.append({
            "url": url,
            "data_keys": sorted((data or {}).keys()),
            "header_names": sorted((headers or {}).keys()),
        })
        act = self._resolve(url, None, "POST")
        if callable(act):
            act = act(url, None)
        if isinstance(act, BaseException):
            raise act
        if act is None:
            return _FakeResp(200, {"access_token": "new-access", "refresh_token": "new-refresh"})
        return act


def _live_so_client(monkeypatch, *, oauth=False):
    _FakeHTTP.created = 0
    _FakeHTTP.gets = []
    _FakeHTTP.posts = []
    _FakeHTTP.script = {}
    import services.accurate as acc
    monkeypatch.setattr(acc.httpx, "AsyncClient", _FakeHTTP)

    async def _nosleep(_s):
        return None

    monkeypatch.setattr(acc.asyncio, "sleep", _nosleep)
    c = AccurateClient()
    if oauth:
        c.api_token = ""
        c.api_signature_secret = ""
        c.client_id = "unit-client-id"
        c.client_secret = "unit-client-secret"
        c.access_token = "old-access"
        c.refresh_token = "unit-refresh"
        c._cached_access_token = "old-access"
    else:
        c.api_token = "unit-test-token"
        c.api_signature_secret = "unit-test-secret"
        c.client_secret = ""
        c.access_token = ""
        c.refresh_token = ""
        c._cached_access_token = ""
    c.db_id = "1"
    c.open_db_calls = []

    async def _open(token):
        c.open_db_calls.append("x")
        return "https://odin.accurate.id", "sess-1"

    c._open_db = _open
    assert c.is_live
    return c


# ---------- helpers / validation ----------
def test_coerce_quantity_does_not_crash():
    assert coerce_accurate_quantity(10) == 10
    assert coerce_accurate_quantity("12.9") == 12
    assert coerce_accurate_quantity("1,000") == 1000
    assert coerce_accurate_quantity("abc") == 0
    assert coerce_accurate_quantity(None) == 0
    assert coerce_accurate_quantity(True) == 0


def test_normalize_skips_non_dict_lines_and_bad_qty():
    so = _normalize_accurate_so({
        "id": 1,
        "number": "SO-N",
        "customer": {"name": "A"},
        "detailItem": [
            "garbage",
            {"quantity": "nope", "item": {"id": 1, "name": "X"}},
            {"quantity": 5, "item": {"id": 2, "no": "Y", "name": "Ok"}},
        ],
        "shipTo": "not-an-object",
        "branch": "string-branch",
    })
    assert len(so["line_items"]) == 2
    assert so["line_items"][0]["quantity"] == 0
    assert so["line_items"][1]["quantity"] == 5
    assert so["outlet"] is None


def test_validate_missing_customer_and_items():
    with pytest.raises(AccurateRequestError) as ei:
        validate_normalized_so({"po_number": "SO-1", "customer": None, "line_items": []})
    assert ei.value.category == AccurateErrorCategory.INVALID_RESPONSE
    assert ei.value.retryable is False


def test_mock_fetch_all_succeeds():
    c = AccurateClient()
    if c.is_live:
        pytest.skip("live credentials present — unit tests must not call Accurate")
    numbers = [so["po_number"] for so in _MOCK_SALES_ORDERS]
    rows = _await(c.fetch_sales_order_results(numbers))
    assert all(r["success"] for r in rows)
    assert len(rows) == len(numbers)


# ---------- HTTP retry / classification ----------
def test_all_so_succeed_reuses_session(monkeypatch):
    c = _live_so_client(monkeypatch)
    numbers = ["SO-A", "SO-B", "SO-C", "SO-D"]
    rows = _await(c.fetch_sales_order_results(numbers))
    assert [r["success"] for r in rows] == [True] * 4
    assert all(r["data"]["customer"] == "Kopi Rumah" for r in rows)
    assert len(c.open_db_calls) == 1
    assert _FakeHTTP.created == 1
    assert len(_FakeHTTP.gets) == 4
    assert ACCURATE_SO_FETCH_CONCURRENCY == 3


def test_timeout_then_success_on_retry(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-T": [
            httpx.ReadTimeout("read timed out"),
            _FakeResp(200, _so_payload("SO-T")),
        ]
    }
    rows = _await(c.fetch_sales_order_results(["SO-T"]))
    assert rows[0]["success"] is True
    assert rows[0]["data"]["po_number"] == "SO-T"
    assert len(_FakeHTTP.gets) == 2
    assert "TIMEOUT" in caplog.text or "ReadTimeout" in caplog.text or "error_category=TIMEOUT" in caplog.text
    assert "unit-test-token" not in caplog.text
    assert "Authorization" not in caplog.text


def test_timeout_exhausted(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-X": [
            httpx.ReadTimeout("read timed out")
        ] * ACCURATE_MAX_TRANSIENT_ATTEMPTS
    }
    rows = _await(c.fetch_sales_order_results(["SO-X"]))
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == AccurateErrorCategory.TIMEOUT
    assert rows[0]["retryable"] is True
    assert "lambat" in (rows[0]["user_message"] or "").lower()
    assert len(_FakeHTTP.gets) == ACCURATE_MAX_TRANSIENT_ATTEMPTS
    assert "unit-test-token" not in caplog.text


def test_429_then_success_uses_retry_after(monkeypatch):
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-R": [
            _FakeResp(429, {"s": False}, headers={"Retry-After": "0"}),
            _FakeResp(200, _so_payload("SO-R")),
        ]
    }
    rows = _await(c.fetch_sales_order_results(["SO-R"]))
    assert rows[0]["success"] is True
    assert len(_FakeHTTP.gets) == 2


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_retries_then_fails(monkeypatch, status):
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-5": [
            _FakeResp(status, {"s": False})
        ] * ACCURATE_MAX_TRANSIENT_ATTEMPTS
    }
    rows = _await(c.fetch_sales_order_results(["SO-5"]))
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == AccurateErrorCategory.ACCURATE_SERVER_ERROR
    assert rows[0]["retryable"] is True
    assert len(_FakeHTTP.gets) == ACCURATE_MAX_TRANSIENT_ATTEMPTS


def test_401_oauth_refresh_then_retry_success(monkeypatch):
    c = _live_so_client(monkeypatch, oauth=True)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-U": [
            _FakeResp(401, {"s": False, "d": ["token expired"]}),
            _FakeResp(200, _so_payload("SO-U")),
        ],
        "POST:token": [
            _FakeResp(200, {"access_token": "rotated-access", "refresh_token": "rotated-refresh"}),
        ],
    }
    rows = _await(c.fetch_sales_order_results(["SO-U"]))
    assert rows[0]["success"] is True
    assert c._cached_access_token == "rotated-access"
    assert c.refresh_token == "rotated-refresh"
    assert len(_FakeHTTP.posts) == 1
    assert "refresh_token" in _FakeHTTP.posts[0]["data_keys"]
    # token values must not be recorded
    dump = str(_FakeHTTP.posts)
    assert "rotated-access" not in dump
    assert "unit-refresh" not in dump


def test_refresh_token_failed(monkeypatch):
    c = _live_so_client(monkeypatch, oauth=True)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-U": [
            _FakeResp(401, {"s": False}),
        ],
        "POST:token": [
            _FakeResp(400, {"error": "invalid_grant"}),
        ],
    }
    rows = _await(c.fetch_sales_order_results(["SO-U"]))
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] in (
        AccurateErrorCategory.UNAUTHORIZED,
        AccurateErrorCategory.TOKEN_EXPIRED,
    )
    assert rows[0]["retryable"] is False
    assert "sesi" in (rows[0]["user_message"] or "").lower()


def test_concurrent_401_refreshes_once(monkeypatch):
    c = _live_so_client(monkeypatch, oauth=True)
    numbers = ["SO-1", "SO-2", "SO-3", "SO-4"]

    def _get_act(url, params):
        # First wave: 401 until refresh has happened
        if c._token_version == 0:
            return _FakeResp(401, {"s": False})
        number = (params or {}).get("number") or (params or {}).get("id")
        return _FakeResp(200, _so_payload(str(number)))

    _FakeHTTP.script = {
        "GET:sales-order/detail.do": [_get_act] * 20,
        "POST:token": [
            _FakeResp(200, {"access_token": "once-token"}),
        ],
    }
    rows = _await(c.fetch_sales_order_results(numbers))
    assert all(r["success"] for r in rows)
    assert len(_FakeHTTP.posts) == 1
    assert c._token_version == 1


def test_partial_failure_does_not_fail_batch(monkeypatch):
    c = _live_so_client(monkeypatch)

    def _act(url, params):
        number = str((params or {}).get("number") or "")
        if number == "SO-BAD":
            return httpx.ConnectError("connection reset")
        return _FakeResp(200, _so_payload(number))

    _FakeHTTP.script = {
        "GET:sales-order/detail.do": [_act] * 20,
    }
    rows = _await(c.fetch_sales_order_results(["SO-OK1", "SO-BAD", "SO-OK2"]))
    by = {r["so_number"]: r for r in rows}
    assert by["SO-OK1"]["success"] is True
    assert by["SO-OK2"]["success"] is True
    assert by["SO-BAD"]["success"] is False
    assert by["SO-BAD"]["error_code"] in (
        AccurateErrorCategory.CONNECTION_ERROR,
        AccurateErrorCategory.TIMEOUT,
    )
    assert by["SO-OK1"]["data"]["customer"]


def test_invalid_response_missing_fields(monkeypatch):
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-I": [
            _FakeResp(200, {"s": True, "d": {"id": 1, "number": "SO-I", "detailItem": []}}),
        ]
    }
    rows = _await(c.fetch_sales_order_results(["SO-I"]))
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == AccurateErrorCategory.INVALID_RESPONSE
    assert "tidak lengkap" in (rows[0]["user_message"] or "").lower()
    assert rows[0]["retryable"] is False


def test_not_found_is_not_retried(monkeypatch):
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-N": [_FakeResp(404, {"s": False})]
    }
    rows = _await(c.fetch_sales_order_results(["SO-N"]))
    assert rows[0]["error_code"] == AccurateErrorCategory.NOT_FOUND
    assert rows[0]["retryable"] is False
    assert len(_FakeHTTP.gets) == 1


def test_400_validation_is_not_retried(monkeypatch):
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-V": [_FakeResp(400, {"s": False, "d": ["bad request"]})]
    }
    rows = _await(c.fetch_sales_order_results(["SO-V"]))
    assert rows[0]["success"] is False
    assert rows[0]["retryable"] is False
    assert len(_FakeHTTP.gets) == 1


def test_user_message_never_contains_token(monkeypatch):
    c = _live_so_client(monkeypatch)
    leaked = sanitize_accurate_error(c, "boom unit-test-token unit-test-secret")
    assert leaked == "Accurate error"
    rows = _await(c.fetch_sales_order_results(["SO-A"]))
    blob = str(rows)
    assert "unit-test-token" not in blob
    assert "unit-test-secret" not in blob


def test_logs_have_correlation_and_no_secrets(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    c = _live_so_client(monkeypatch)
    _await(c.fetch_sales_order_results(["SO-L"], correlation_id="abc123def456"))
    assert "correlation_id=abc123def456" in caplog.text
    assert "endpoint=sales-order/detail.do" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "unit-test-token" not in caplog.text
    assert "Authorization" not in caplog.text


def test_http_403_is_forbidden_not_token_refresh(monkeypatch):
    c = _live_so_client(monkeypatch, oauth=True)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-F": [_FakeResp(403, {"s": False, "d": ["forbidden"]})],
    }
    rows = _await(c.fetch_sales_order_results(["SO-F"]))
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == AccurateErrorCategory.UNAUTHORIZED
    assert rows[0]["retryable"] is False
    assert "ditolak" in (rows[0]["user_message"] or "").lower()
    assert len(_FakeHTTP.gets) == 1
    assert _FakeHTTP.posts == []


def test_s_false_not_authorized_is_not_token_expired():
    cat = _classify_s_false({"s": False, "d": ["You are not authorized to view this sales order"]})
    assert cat == AccurateErrorCategory.UNAUTHORIZED
    cat2 = _classify_s_false({"s": False, "d": ["token expired"]})
    assert cat2 == AccurateErrorCategory.TOKEN_EXPIRED


def test_stale_session_invalidates_cache_and_retries_once(monkeypatch):
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-S": [
            _FakeResp(200, {"s": False, "d": ["Session expired"]}),
            _FakeResp(200, _so_payload("SO-S")),
        ],
    }
    rows = _await(c.fetch_sales_order_results(["SO-S"]))
    assert rows[0]["success"] is True
    assert len(c.open_db_calls) == 2
    assert len(_FakeHTTP.gets) == 2


def test_oauth_refresh_retries_original_detail_once(monkeypatch):
    c = _live_so_client(monkeypatch, oauth=True)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-U": [
            _FakeResp(401, {"s": False}),
            _FakeResp(502, {"s": False}),
            _FakeResp(200, _so_payload("SO-U")),
        ],
        "POST:token": [_FakeResp(200, {"access_token": "rotated-access"})],
    }
    rows = _await(c.fetch_sales_order_results(["SO-U"]))
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == AccurateErrorCategory.ACCURATE_SERVER_ERROR
    assert len(_FakeHTTP.gets) == 2
    assert len(_FakeHTTP.posts) == 1


def test_duplicate_protection_still_in_server_source():
    src = Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8")
    assert 'create_index("po_number", unique=True)' in src
    assert "DuplicateKeyError" in src
    assert "_find_existing_import" in src
    assert "except DuplicateKeyError" in src


def test_single_fetch_raises_typed_error_after_timeout(monkeypatch):
    c = _live_so_client(monkeypatch)
    _FakeHTTP.script = {
        "GET:sales-order/detail.do:SO-X": [
            httpx.ReadTimeout("read timed out")
        ] * ACCURATE_MAX_TRANSIENT_ATTEMPTS
    }
    with pytest.raises(AccurateRequestError) as ei:
        _await(c.fetch_single_sales_order("SO-X"))
    assert ei.value.category == AccurateErrorCategory.TIMEOUT
    assert "lambat" in str(ei.value).lower()
