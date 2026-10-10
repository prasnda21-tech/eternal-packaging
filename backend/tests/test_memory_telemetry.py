"""Phase C — request memory telemetry: isolated local tests (no production)."""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.memory_telemetry as mt


def _await(coro):
    return asyncio.run(coro)


def _cfg(
    *,
    enabled: bool = True,
    sample_rate: float = 1.0,
    max_records: int = 64,
    max_seconds: float = 3600.0,
    rss_sample_interval_s: float = 0.5,
    output_dir: str = "/tmp/mt-test",
    flush_interval_s: float = 60.0,
    max_disk_bytes: int = 10 * 1024 * 1024,
    max_aggregate_keys: int = 256,
    deadline_unix: Optional[float] = None,
    started_at_monotonic: Optional[float] = None,
) -> mt.MemoryTelemetryConfig:
    now = time.monotonic() if started_at_monotonic is None else started_at_monotonic
    return mt.MemoryTelemetryConfig(
        enabled=enabled,
        sample_rate=sample_rate,
        max_records=max_records,
        max_seconds=max_seconds,
        rss_sample_interval_s=rss_sample_interval_s,
        started_at_monotonic=now,
        process_marker="test",
        pid=os.getpid(),
        output_dir=output_dir,
        flush_interval_s=flush_interval_s,
        max_disk_bytes=max_disk_bytes,
        max_aggregate_keys=max_aggregate_keys,
        deadline_unix=deadline_unix if deadline_unix is not None else time.time() + max_seconds,
    )


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Each test gets a fresh temp diagnostic dir; default OFF."""
    tel_dir = tmp_path / "eternal-memory-telemetry"
    for key in (
        "ETERNAL_MEMORY_TELEMETRY_ENABLED",
        "ETERNAL_MEMORY_TELEMETRY_SAMPLE_RATE",
        "ETERNAL_MEMORY_TELEMETRY_MAX_RECORDS",
        "ETERNAL_MEMORY_TELEMETRY_MAX_SECONDS",
        "ETERNAL_MEMORY_TELEMETRY_RSS_INTERVAL_S",
        "ETERNAL_MEMORY_TELEMETRY_FLUSH_INTERVAL_S",
        "ETERNAL_MEMORY_TELEMETRY_MAX_DISK_BYTES",
        "ETERNAL_MEMORY_TELEMETRY_MAX_AGG_KEYS",
        "ETERNAL_MEMORY_TELEMETRY_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_DIR", str(tel_dir))
    mt.reset_state(mt.load_config())
    yield tel_dir
    _await(mt.stop_flush_worker())
    mt.reset_state(mt.load_config())


def _enable(
    monkeypatch,
    tel_dir: Path,
    *,
    sample_rate: str = "1.0",
    max_records: str = "64",
    max_seconds: str = "3600",
    rss_interval: str = "0.5",
    max_disk_bytes: Optional[str] = None,
    max_agg_keys: Optional[str] = None,
) -> mt.MemoryTelemetryConfig:
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_ENABLED", "1")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_SAMPLE_RATE", sample_rate)
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_RECORDS", max_records)
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_SECONDS", max_seconds)
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_RSS_INTERVAL_S", rss_interval)
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_DIR", str(tel_dir))
    if max_disk_bytes is not None:
        monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_DISK_BYTES", max_disk_bytes)
    if max_agg_keys is not None:
        monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_AGG_KEYS", max_agg_keys)
    # Fresh activation window for this temp dir.
    deadline = Path(tel_dir) / mt._DEADLINE_FILENAME
    if deadline.exists():
        deadline.unlink()
    return mt.reset_state(mt.load_config())


def _build_app(*, install: bool = True) -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    @app.post("/echo")
    async def echo(payload: Dict[str, Any]):
        return {"echo": payload}

    @app.get("/slow")
    async def slow():
        await asyncio.sleep(0.05)
        return {"slow": True}

    @app.get("/items/{item_id}")
    async def item(item_id: str):
        return {"item_id": item_id}

    @app.get("/error")
    async def error():
        raise HTTPException(status_code=418, detail="teapot")

    @app.get("/stream")
    async def stream():
        async def gen():
            for i in range(20):
                yield f"chunk-{i}\n".encode("utf-8")
                await asyncio.sleep(0)

        return StreamingResponse(gen(), media_type="text/plain")

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    if install:
        mt.install_memory_telemetry(app)
    return app


async def _client(app: FastAPI):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _flush_files(tel_dir: Path) -> List[Path]:
    if not tel_dir.exists():
        return []
    return sorted(tel_dir.glob("flush_*.json"))


# ---------------------------------------------------------------------------
# Required regressions (preserved)
# ---------------------------------------------------------------------------


def test_01_disabled_by_default(monkeypatch, _isolate_env):
    tel_dir = _isolate_env
    cfg = mt.load_config()
    assert cfg.enabled is False
    assert mt.is_active() is False
    app = _build_app(install=True)

    async def run():
        async with await _client(app) as client:
            r = await client.get("/ping")
            assert r.status_code == 200
            assert r.json() == {"ok": True}

    _await(run())
    assert mt.get_records() == []
    assert _flush_files(tel_dir) == []
    mt.flush_aggregates_to_disk(force=True)
    assert _flush_files(tel_dir) == []


def test_02_enabled_samples_according_to_policy(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env, sample_rate="1.0")
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            for _ in range(5):
                assert (await client.get("/ping")).status_code == 200

    _await(run())
    records = mt.get_records()
    assert len(records) == 5
    assert all(r["route_template"] == "/ping" for r in records)
    assert all(r["method"] == "GET" for r in records)
    aggs = mt.get_aggregates()
    assert len(aggs) == 1
    assert aggs["GET /ping"]["count"] == 5


def test_03_request_output_identical_on_off(monkeypatch, _isolate_env):
    async def hit(enabled: bool):
        if enabled:
            _enable(monkeypatch, _isolate_env)
        else:
            monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_ENABLED", "0")
            mt.reset_state(mt.load_config())
        app = _build_app()
        async with await _client(app) as client:
            r = await client.post("/echo", json={"a": 1, "secret": "x"})
            return r.status_code, r.headers.get("content-type"), r.json()

    assert _await(hit(False)) == _await(hit(True))


def test_04_error_responses_identical(monkeypatch, _isolate_env):
    async def hit(enabled: bool):
        if enabled:
            _enable(monkeypatch, _isolate_env)
        else:
            monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_ENABLED", "0")
            mt.reset_state(mt.load_config())
        app = _build_app()
        async with await _client(app) as client:
            r = await client.get("/error")
            return r.status_code, r.json()

    assert _await(hit(False)) == _await(hit(True)) == (418, {"detail": "teapot"})


def test_05_cancellation_propagates(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = FastAPI()

    @app.get("/hang")
    async def hang():
        await asyncio.sleep(60)
        return {"done": True}

    mt.install_memory_telemetry(app)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with pytest.raises(asyncio.CancelledError):
                task = asyncio.create_task(client.get("/hang"))
                await asyncio.sleep(0.02)
                task.cancel()
                await task

    _await(run())
    assert mt.get_in_flight() == 0


def test_06_streaming_behavior_unchanged(monkeypatch, _isolate_env):
    async def body(enabled: bool) -> bytes:
        if enabled:
            _enable(monkeypatch, _isolate_env)
        else:
            monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_ENABLED", "0")
            mt.reset_state(mt.load_config())
        app = _build_app()
        async with await _client(app) as client:
            r = await client.get("/stream")
            assert r.status_code == 200
            return r.content

    assert _await(body(False)) == _await(body(True))
    assert b"chunk-0\n" in _await(body(True))


def test_07_websockets_bypass_safely(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    seen = {"ws": 0}

    async def inner(scope, receive, send):
        if scope["type"] == "websocket":
            seen["ws"] += 1
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1000})
            return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = mt.MemoryTelemetryMiddleware(inner)
    mt.clear_records()

    async def run():
        scope = {"type": "websocket", "path": "/ws", "headers": [], "query_string": b""}

        async def receive():
            return {"type": "websocket.disconnect", "code": 1000}

        sent = []

        async def send(message):
            sent.append(message)

        await mw(scope, receive, send)
        assert seen["ws"] == 1

    _await(run())
    assert mt.get_records() == []
    assert mt.get_in_flight() == 0


def test_08_no_raw_route_parameters_logged(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            assert (await client.get("/items/CUST-999-SECRET")).status_code == 200

    _await(run())
    blob = json.dumps(mt.get_records() + list(mt.get_aggregates().values()))
    assert "CUST-999-SECRET" not in blob
    assert mt.get_records()[0]["route_template"] == "/items/{item_id}"


def test_09_no_query_strings_logged(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            assert (await client.get("/ping?phone=08123456789&invoice=INV-1")).status_code == 200

    _await(run())
    blob = json.dumps(mt.get_records() + list(mt.get_aggregates().values()))
    assert "08123456789" not in blob
    assert "INV-1" not in blob


def test_10_no_request_response_bodies_logged(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()
    secret = "BODY_SECRET_TOKEN_XYZ"

    async def run():
        async with await _client(app) as client:
            r = await client.post("/echo", json={"token": secret, "note": "private"})
            assert secret in r.text

    _await(run())
    blob = json.dumps(mt.get_records() + list(mt.get_aggregates().values()))
    assert secret not in blob
    assert "private" not in blob


def test_11_no_auth_headers_or_cookies_logged(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            assert (
                await client.get(
                    "/ping",
                    headers={"Authorization": "Bearer SUPERSECRET", "Cookie": "session=abc123"},
                )
            ).status_code == 200

    _await(run())
    blob = json.dumps(mt.get_records() + list(mt.get_aggregates().values()))
    assert "SUPERSECRET" not in blob
    assert "session=abc123" not in blob


def test_12_concurrent_in_flight_count_correct(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = FastAPI()
    peak = {"n": 0}

    @app.get("/hold")
    async def hold():
        with mt._in_flight_lock:
            peak["n"] = max(peak["n"], mt._in_flight)
        await asyncio.sleep(0.08)
        return {"ok": True}

    mt.install_memory_telemetry(app)

    async def run():
        async with await _client(app) as client:
            await asyncio.gather(*[client.get("/hold") for _ in range(8)])

    _await(run())
    assert peak["n"] >= 2
    assert mt.get_in_flight() == 0


def test_13_rss_delta_not_exclusive_attribution(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            await client.get("/ping")

    _await(run())
    rec = mt.get_records()[0]
    assert rec["rss_delta_note"] == "correlation_only_not_exclusive"
    path = mt.flush_aggregates_to_disk(force=True)
    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["rss_delta_note"] == "correlation_only_not_exclusive"
    assert all(g.get("rss_delta_note") == "correlation_only_not_exclusive" for g in data["groups"])


def test_14_buffer_bounded_under_heavy_traffic(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env, sample_rate="1.0", max_records="5", max_agg_keys="2")
    app = FastAPI()

    @app.get("/a")
    async def a():
        return {"a": 1}

    @app.get("/b")
    async def b():
        return {"b": 1}

    @app.get("/c")
    async def c():
        return {"c": 1}

    mt.install_memory_telemetry(app)

    async def run():
        async with await _client(app) as client:
            for _ in range(20):
                await client.get("/a")
                await client.get("/b")
                await client.get("/c")

    _await(run())
    assert len(mt.get_records()) <= 5
    assert mt.get_dropped_count() >= 1
    assert len(mt.get_aggregates()) <= 2
    assert mt.get_dropped_aggregate_count() >= 1


def test_15_telemetry_logging_failure_cannot_fail_erp(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()

    def boom(*_a, **_k):
        raise RuntimeError("log explode")

    monkeypatch.setattr(mt, "_safe_log_record", boom)
    monkeypatch.setattr(mt, "_append_record", boom)
    monkeypatch.setattr(mt, "_aggregate_record", boom)

    async def run():
        async with await _client(app) as client:
            r = await client.get("/ping")
            assert r.status_code == 200

    _await(run())


def test_16_activation_expires_automatically(monkeypatch, _isolate_env):
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_ENABLED", "1")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_SAMPLE_RATE", "1.0")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_SECONDS", "1")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_RECORDS", "32")
    start = time.monotonic()
    cfg = mt.reset_state(mt.load_config(now_monotonic=start))
    assert cfg.enabled is True
    assert mt.is_active(now_monotonic=start) is True
    assert mt.is_active(now_monotonic=start + 1.01) is False

    app = FastAPI()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    mt.install_memory_telemetry(app)
    mt.reset_state(
        _cfg(
            enabled=True,
            max_seconds=0.01,
            output_dir=str(_isolate_env),
            started_at_monotonic=time.monotonic() - 10,
            deadline_unix=time.time() - 1,
        )
    )
    assert mt.is_active() is False
    mt.clear_records()

    async def run():
        async with await _client(app) as client:
            assert (await client.get("/ping")).status_code == 200

    _await(run())
    assert mt.get_records() == []
    assert mt.flush_aggregates_to_disk() is None


def test_17_invalid_configuration_fails_safely_to_off(monkeypatch, _isolate_env):
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_ENABLED", "1")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_SAMPLE_RATE", "not-a-float")
    cfg = mt.load_config()
    assert cfg.enabled is False

    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_SAMPLE_RATE", "0.5")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_RECORDS", "0")
    assert mt.load_config().enabled is False

    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_RECORDS", "10")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_SECONDS", "999999")
    assert mt.load_config().enabled is False

    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_SECONDS", "60")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_FLUSH_INTERVAL_S", "1")
    # Flush interval < 60 must fail safe OFF
    assert mt.load_config().enabled is False


def test_18_process_restart_resets_counters(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env, max_records="10")
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            for _ in range(3):
                await client.get("/ping")

    _await(run())
    assert len(mt.get_records()) == 3
    mt.reset_state(mt.load_config())
    assert mt.get_records() == []
    assert mt.get_in_flight() == 0
    assert mt.get_dropped_count() == 0
    assert mt.get_aggregates() == {}


def test_19_sensitive_endpoint_templates_remain_sanitized(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = FastAPI()

    @app.get("/api/customers/{customer_id}/orders/{order_id}")
    async def sensitive(customer_id: str, order_id: str):
        return {"customer_id": customer_id, "order_id": order_id}

    mt.install_memory_telemetry(app)

    async def run():
        async with await _client(app) as client:
            assert (await client.get("/api/customers/CUSTPHONE0812/orders/PO-999")).status_code == 200

    _await(run())
    path = mt.flush_aggregates_to_disk(force=True)
    blob = path.read_text(encoding="utf-8") if path else ""
    assert "CUSTPHONE0812" not in blob
    assert "PO-999" not in blob
    assert "/api/customers/{customer_id}/orders/{order_id}" in blob


def test_20_no_business_endpoint_contract_changes(_isolate_env):
    app = FastAPI()

    @app.get("/biz")
    async def biz():
        return {"biz": 1}

    paths_before = {getattr(r, "path", None) for r in app.router.routes}
    mt.reset_state(mt.load_config())
    mt.install_memory_telemetry(app)
    paths_after = {getattr(r, "path", None) for r in app.router.routes}
    assert paths_before == paths_after


def test_sample_rate_zero_records_none(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env, sample_rate="0.0")
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            for _ in range(10):
                await client.get("/ping")

    _await(run())
    assert mt.get_records() == []
    assert mt.get_aggregates() == {}


def test_route_template_unmatched_label():
    assert mt.route_template_from_scope({"type": "http", "method": "GET", "path": "/nope"}) == "UNMATCHED"


def test_max_activation_capped_at_24h(monkeypatch, _isolate_env):
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_ENABLED", "1")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_SAMPLE_RATE", "1")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_RECORDS", "10")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_SECONDS", str(24 * 60 * 60))
    deadline = _isolate_env / mt._DEADLINE_FILENAME
    if deadline.exists():
        deadline.unlink()
    cfg = mt.load_config()
    assert cfg.enabled is True
    assert cfg.max_seconds == 24 * 60 * 60


# ---------------------------------------------------------------------------
# Durable output hardening
# ---------------------------------------------------------------------------


def test_durable_output_created_with_0600(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            await client.get("/ping")
            await client.post("/echo", json={"x": 1})

    _await(run())
    path = mt.flush_aggregates_to_disk(force=True)
    assert path is not None and path.is_file()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "groups" in data
    assert data["rss_delta_note"] == "correlation_only_not_exclusive"


def test_durable_output_no_sensitive_data(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            await client.get(
                "/items/SECRET-CUSTOMER?phone=0812&invoice=INV-9",
                headers={"Authorization": "Bearer TOK", "Cookie": "a=b"},
            )
            await client.post("/echo", json={"customer": "Alice", "phone": "0812"})

    _await(run())
    path = mt.flush_aggregates_to_disk(force=True)
    blob = path.read_text(encoding="utf-8")
    for needle in ("SECRET-CUSTOMER", "0812", "INV-9", "Bearer TOK", "Alice", "a=b"):
        assert needle not in blob


def test_disk_cap_and_rotation(monkeypatch, _isolate_env):
    # Tiny budget forces rotation / fail-safe stop.
    _enable(monkeypatch, _isolate_env, max_disk_bytes="2048")
    mt.reset_state(
        _cfg(
            output_dir=str(_isolate_env),
            max_disk_bytes=2048,
            max_aggregate_keys=50,
            flush_interval_s=60.0,
        )
    )
    # Seed many large aggregate groups and flush repeatedly.
    written = []
    for i in range(30):
        with mt._agg_lock:
            mt._aggregates[f"GET /r{i}"] = {
                "method": "GET",
                "route_template": f"/r{i}",
                "count": 100,
                "duration_ms_sum": 1234.5,
                "duration_ms_max": 50.0,
                "in_flight_max": 3,
                "rss_kib_last": 99999,
                "rss_delta_kib_sum": 1,
                "rss_delta_note": mt._RSS_DELTA_NOTE,
                "pad": "x" * 200,
            }
        path = mt.flush_aggregates_to_disk(force=True)
        if path is not None:
            written.append(path)
        else:
            break
    total = sum(p.stat().st_size for p in _isolate_env.glob("flush_*.json"))
    assert total <= 2048
    # Either rotation kept us under budget or fail-safe stopped writes.
    assert written or mt.disk_writes_stopped()


def test_restart_preserves_existing_output_files(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            await client.get("/ping")

    _await(run())
    path = mt.flush_aggregates_to_disk(force=True)
    assert path is not None
    content = path.read_text(encoding="utf-8")
    # Simulate process restart: counters reset, files remain.
    mt.reset_state(mt.load_config())
    assert mt.get_records() == []
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == content


def test_write_failure_fail_open(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            r = await client.get("/ping")
            assert r.status_code == 200

    _await(run())

    real_open = __import__("builtins").open

    def boom_open(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", boom_open)
    assert mt.flush_aggregates_to_disk(force=True) is None
    assert mt.get_write_failure_count() >= 1
    monkeypatch.setattr("builtins.open", real_open)

    # ERP still works after telemetry write failures.
    async def run2():
        async with await _client(app) as client:
            assert (await client.get("/ping")).status_code == 200

    _await(run2())


def test_write_failures_stop_after_limit(monkeypatch, _isolate_env):
    mt.reset_state(
        _cfg(output_dir=str(_isolate_env), max_disk_bytes=10 * 1024 * 1024)
    )
    with mt._agg_lock:
        mt._aggregates["GET /ping"] = {
            "method": "GET",
            "route_template": "/ping",
            "count": 1,
            "duration_ms_sum": 1.0,
            "duration_ms_max": 1.0,
            "in_flight_max": 1,
            "rss_kib_last": 1,
            "rss_delta_kib_sum": 0,
            "rss_delta_note": mt._RSS_DELTA_NOTE,
        }

    def boom_open(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr("builtins.open", boom_open)
    for _ in range(mt._MAX_CONSECUTIVE_WRITE_FAILURES):
        # Re-seed aggregates each attempt (flush clears on snapshot).
        with mt._agg_lock:
            mt._aggregates["GET /ping"] = {
                "method": "GET",
                "route_template": "/ping",
                "count": 1,
                "duration_ms_sum": 1.0,
                "duration_ms_max": 1.0,
                "in_flight_max": 1,
                "rss_kib_last": 1,
                "rss_delta_kib_sum": 0,
                "rss_delta_note": mt._RSS_DELTA_NOTE,
            }
        mt.flush_aggregates_to_disk(force=True)
    assert mt.disk_writes_stopped() is True
    status = _isolate_env / mt._STATUS_FILENAME
    # status write also uses open — may be missing; failure counter is authoritative.
    assert mt.get_consecutive_write_failures() >= mt._MAX_CONSECUTIVE_WRITE_FAILURES


def test_no_request_path_blocking_disk_writes(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()
    calls = {"n": 0}
    real_flush = mt.flush_aggregates_to_disk

    def guarded_flush(**kwargs):
        if getattr(mt._request_path_disk_guard, "in_request", False):
            calls["n"] += 1
        return real_flush(**kwargs)

    monkeypatch.setattr(mt, "flush_aggregates_to_disk", guarded_flush)

    async def run():
        async with await _client(app) as client:
            for _ in range(10):
                await client.get("/ping")

    _await(run())
    assert calls["n"] == 0
    # Explicit off-path flush still works.
    assert guarded_flush(force=True) is not None or mt.get_aggregates() == {}


def test_expiry_stops_writing(monkeypatch, _isolate_env):
    mt.reset_state(
        _cfg(
            output_dir=str(_isolate_env),
            max_seconds=0.01,
            started_at_monotonic=time.monotonic() - 10,
            deadline_unix=time.time() - 1,
        )
    )
    with mt._agg_lock:
        mt._aggregates["GET /ping"] = {
            "method": "GET",
            "route_template": "/ping",
            "count": 1,
            "duration_ms_sum": 1.0,
            "duration_ms_max": 1.0,
            "in_flight_max": 1,
            "rss_kib_last": 1,
            "rss_delta_kib_sum": 0,
            "rss_delta_note": mt._RSS_DELTA_NOTE,
        }
    assert mt.is_active() is False
    assert mt.flush_aggregates_to_disk() is None


def test_shutdown_flush_and_cleanup(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env)
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            await client.get("/ping")
        assert mt.get_aggregates()
        mt.start_flush_worker()
        await mt.stop_flush_worker()
        assert mt.get_aggregates() == {}
        assert _flush_files(_isolate_env)

    _await(run())


def test_persistent_deadline_survives_restart(monkeypatch, _isolate_env):
    _enable(monkeypatch, _isolate_env, max_seconds="3600")
    deadline_path = _isolate_env / mt._DEADLINE_FILENAME
    assert deadline_path.is_file()
    data = json.loads(deadline_path.read_text(encoding="utf-8"))
    assert data["deadline_unix"] > time.time()
    # Restart with same dir: remaining window, not a fresh 3600 if almost done.
    # Force near-expiry deadline.
    almost = time.time() + 30
    deadline_path.write_text(json.dumps({"deadline_unix": almost, "max_seconds": 3600}))
    os.chmod(deadline_path, 0o600)
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_ENABLED", "1")
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_MAX_SECONDS", "3600")
    cfg = mt.load_config()
    assert cfg.enabled is True
    assert cfg.max_seconds <= 30.5
    assert cfg.deadline_unix == almost


def test_off_produces_no_output(monkeypatch, _isolate_env):
    monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_ENABLED", "0")
    mt.reset_state(mt.load_config())
    app = _build_app()

    async def run():
        async with await _client(app) as client:
            await client.get("/ping")

    _await(run())
    assert mt.flush_aggregates_to_disk(force=True) is None
    assert _flush_files(_isolate_env) == []


# ---------------------------------------------------------------------------
# Synthetic overhead benchmark (dummy endpoints only)
# ---------------------------------------------------------------------------


def test_benchmark_off_vs_on(monkeypatch, _isolate_env):
    async def hammer(enabled: bool, n: int = 80) -> Dict[str, Any]:
        if enabled:
            _enable(monkeypatch, _isolate_env, sample_rate="1.0", max_records="128", rss_interval="1.0")
        else:
            monkeypatch.setenv("ETERNAL_MEMORY_TELEMETRY_ENABLED", "0")
            mt.reset_state(mt.load_config())
        app = _build_app()
        rss0 = mt.read_rss_kib()
        t0 = time.perf_counter()
        async with await _client(app) as client:
            for _ in range(n // 4):
                await client.get("/ping")
            for _ in range(n // 4):
                await client.post("/echo", json={"k": "v", "n": 1})
            for _ in range(max(2, n // 20)):
                await client.get("/slow")
            for _ in range(max(2, n // 20)):
                await client.get("/stream")
            await asyncio.gather(*[client.get("/ping") for _ in range(16)])
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        rss1 = mt.read_rss_kib()
        # Durable flush only OFF the request path (benchmark end).
        flush_path = mt.flush_aggregates_to_disk(force=True) if enabled else None
        records = mt.get_records()
        disk_bytes = sum(p.stat().st_size for p in _flush_files(_isolate_env))
        return {
            "enabled": enabled,
            "elapsed_ms": elapsed_ms,
            "rss0_kib": rss0,
            "rss1_kib": rss1,
            "rss_delta_kib": (None if rss0 is None or rss1 is None else rss1 - rss0),
            "record_count": len(records),
            "aggregate_keys": len(mt.get_aggregates()) if not enabled else 0,
            "max_records_cap": (mt.get_config().max_records if mt.get_config() else None),
            "flush_file": str(flush_path) if flush_path else None,
            "disk_bytes": disk_bytes,
            "dropped": mt.get_dropped_count(),
        }

    off = _await(hammer(False))
    on = _await(hammer(True))

    out_dir = Path("/opt/cursor/artifacts")
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"off": off, "on": on}
    if off["elapsed_ms"] > 0:
        report["latency_ratio_on_over_off"] = on["elapsed_ms"] / off["elapsed_ms"]
    (out_dir / "phase_c_memory_telemetry_benchmark.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    assert off["record_count"] == 0
    assert off["disk_bytes"] == 0
    assert on["record_count"] <= on["max_records_cap"]
    assert on["disk_bytes"] > 0
    assert report.get("latency_ratio_on_over_off", 0) < 5.0
