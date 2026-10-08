"""Accurate in-memory cache eviction: expired TTL only. Mocks only."""
from __future__ import annotations

import asyncio
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.accurate as acc
from services.accurate import (
    ACCURATE_NAME_CACHE_TTL_S,
    AccurateClient,
    AccurateErrorCategory,
    AccurateRequestError,
    _LIST_CACHE_TTL,
    _NAME_CACHE,
    _NAME_INFLIGHT,
    _SO_LIST_CACHE,
    _env_positive_int,
    clear_accurate_name_cache,
    clear_accurate_so_list_cache,
    maybe_sweep_accurate_caches,
    reset_accurate_cache_sweep_clock,
)


def _await(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_accurate_name_cache()
    clear_accurate_so_list_cache()
    reset_accurate_cache_sweep_clock()
    yield
    clear_accurate_name_cache()
    clear_accurate_so_list_cache()
    reset_accurate_cache_sweep_clock()


def _seed_so(key: str, *, age_s: float, payload=None):
    _SO_LIST_CACHE[key] = {
        "data": payload or {
            "rows": [{"number": key}],
            "total": 1,
            "page": 1,
            "pageSize": 20,
            "pageCount": 1,
            "mode": "mock",
        },
        "at": time.time() - age_s,
    }


def _seed_name(key: str, name: str, *, expires_in_s: float):
    _NAME_CACHE[key] = (time.monotonic() + expires_in_s, name)


def test_expired_so_list_entries_evicted_valid_retained():
    _seed_so("list:mock:1:20:old", age_s=_LIST_CACHE_TTL + 5)
    _seed_so("list:mock:1:20:live", age_s=1)
    stats = maybe_sweep_accurate_caches(force=True)
    assert stats["skipped"] == 0
    assert stats["so_expired"] == 1
    assert "list:mock:1:20:old" not in _SO_LIST_CACHE
    assert "list:mock:1:20:live" in _SO_LIST_CACHE


def test_expired_name_entries_evicted_valid_retained():
    _seed_name("branch:1", "Old", expires_in_s=-1)
    _seed_name("branch:2", "Live", expires_in_s=120)
    stats = maybe_sweep_accurate_caches(force=True)
    assert stats["name_expired"] == 1
    assert "branch:1" not in _NAME_CACHE
    assert _NAME_CACHE["branch:2"][1] == "Live"


def test_expired_entries_not_returned_as_hits():
    client = AccurateClient()
    _seed_name("branch:x", "Stale", expires_in_s=-10)
    calls = {"n": 0}

    async def fetcher():
        calls["n"] += 1
        return "Fresh", True

    out = _await(client._lookup_cached_name("branch:x", fetcher))
    assert out == "Fresh"
    assert calls["n"] == 1
    assert _NAME_CACHE["branch:x"][1] == "Fresh"


def test_name_cache_hit_avoids_duplicate_fetch():
    client = AccurateClient()
    _seed_name("branch:hit", "Cached", expires_in_s=300)
    calls = {"n": 0}

    async def fetcher():
        calls["n"] += 1
        return "Nope", True

    assert _await(client._lookup_cached_name("branch:hit", fetcher)) == "Cached"
    assert _await(client._lookup_cached_name("branch:hit", fetcher)) == "Cached"
    assert calls["n"] == 0


def test_so_list_cache_hit_miss_and_response_equivalence():
    client = AccurateClient()
    client.api_token = ""
    client.access_token = ""
    client.refresh_token = ""
    client.client_id = ""
    assert not client.is_live

    first = _await(client.list_recent_sales_orders(limit=5, page=1))
    assert first["cached"] is False
    second = _await(client.list_recent_sales_orders(limit=5, page=1))
    assert second["cached"] is True
    assert second["rows"] == first["rows"]
    assert second["total"] == first["total"]

    key = next(iter(_SO_LIST_CACHE))
    _SO_LIST_CACHE[key]["at"] = time.time() - (_LIST_CACHE_TTL + 1)
    reset_accurate_cache_sweep_clock()
    third = _await(client.list_recent_sales_orders(limit=5, page=1))
    assert third["cached"] is False
    assert third["rows"] == first["rows"]


def test_so_stale_on_error_preserved_after_expired_sweep(monkeypatch):
    """Local captured entry still returned as stale even if sweep deleted dict key."""
    client = AccurateClient()
    client.api_token = "t"
    client.api_signature_secret = "s"
    client.db_id = "1"
    client.access_token = ""
    client.refresh_token = ""
    client.client_id = ""
    assert client.is_live

    payload = {
        "rows": [{"number": "SO-STALE"}],
        "total": 1,
        "page": 1,
        "pageSize": 20,
        "pageCount": 1,
        "host": "https://odin.accurate.id",
        "mode": "live",
    }
    cache_key = f"list:{client.auth_mode}:1:20:"
    _SO_LIST_CACHE[cache_key] = {
        "data": payload,
        "at": time.time() - (_LIST_CACHE_TTL + 5),  # expired — not a fresh hit
    }

    async def boom_session():
        raise AccurateRequestError(
            AccurateErrorCategory.CONNECTION_ERROR,
            technical_message="session down",
        )

    monkeypatch.setattr(client, "_ensure_db_session", boom_session)
    reset_accurate_cache_sweep_clock()
    out = _await(client.list_recent_sales_orders(limit=20, page=1))
    assert out.get("stale") is True
    assert out.get("cached") is True
    assert out["rows"] == payload["rows"]
    # Sweep removed the expired dict entry; stale payload came from local capture.
    assert cache_key not in _SO_LIST_CACHE


def test_concurrent_name_fetch_single_flight():
    client = AccurateClient()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = {"n": 0}

    async def fetcher():
        calls["n"] += 1
        started.set()
        await release.wait()
        return "Once", True

    async def run():
        t1 = asyncio.create_task(client._lookup_cached_name("branch:sf", fetcher))
        await started.wait()
        t2 = asyncio.create_task(client._lookup_cached_name("branch:sf", fetcher))
        await asyncio.sleep(0)
        assert "branch:sf" in _NAME_INFLIGHT
        # Sweep during inflight must not drop the key mid-flight.
        _seed_name("branch:sf", "temp", expires_in_s=-1)
        maybe_sweep_accurate_caches(force=True)
        assert "branch:sf" in _NAME_INFLIGHT
        release.set()
        a, b = await asyncio.gather(t1, t2)
        assert a == b == "Once"
        assert calls["n"] == 1
        assert "branch:sf" not in _NAME_INFLIGHT

    _await(run())


def test_concurrent_lookup_with_sweep():
    client = AccurateClient()
    for i in range(100):
        _seed_name(f"noise:{i}", "N", expires_in_s=-1)

    async def fetcher():
        await asyncio.sleep(0.01)
        return "OK", True

    async def sweep_loop():
        for _ in range(5):
            maybe_sweep_accurate_caches(force=True)
            await asyncio.sleep(0)

    async def run():
        look_tasks = [
            asyncio.create_task(client._lookup_cached_name(f"live:{i % 5}", fetcher))
            for i in range(20)
        ]
        sweep_task = asyncio.create_task(sweep_loop())
        results = await asyncio.gather(*look_tasks)
        await sweep_task
        assert all(r == "OK" for r in results)
        assert all(k.startswith("live:") for k in _NAME_CACHE)
        assert not any(k.startswith("noise:") for k in _NAME_CACHE)

    _await(run())


def test_max_size_overflow_keeps_live_and_inflight(monkeypatch):
    monkeypatch.setattr(acc, "ACCURATE_NAME_CACHE_MAX_ENTRIES", 2)
    loop = asyncio.new_event_loop()
    fut = loop.create_future()
    _NAME_INFLIGHT["branch:inflight"] = fut
    _seed_name("branch:inflight", "Keep", expires_in_s=300)
    _seed_name("branch:live1", "A", expires_in_s=300)
    _seed_name("branch:live2", "B", expires_in_s=300)
    _seed_name("branch:expired", "Gone", expires_in_s=-1)
    stats = maybe_sweep_accurate_caches(force=True)
    assert stats["name_expired"] == 1
    assert "branch:expired" not in _NAME_CACHE
    assert "branch:inflight" in _NAME_CACHE
    assert "branch:live1" in _NAME_CACHE
    assert "branch:live2" in _NAME_CACHE
    # Over max but live-only — no trim of live entries.
    assert len(_NAME_CACHE) == 3
    _NAME_INFLIGHT.clear()
    loop.close()


def test_invalid_cache_size_configuration(monkeypatch):
    monkeypatch.setenv("ACCURATE_NAME_CACHE_MAX_ENTRIES", "not-a-number")
    assert _env_positive_int("ACCURATE_NAME_CACHE_MAX_ENTRIES", 5000) == 5000
    monkeypatch.setenv("ACCURATE_SO_LIST_CACHE_MAX_ENTRIES", "0")
    assert _env_positive_int("ACCURATE_SO_LIST_CACHE_MAX_ENTRIES", 256) == 256
    monkeypatch.setenv("ACCURATE_SO_LIST_CACHE_MAX_ENTRIES", "-9")
    assert _env_positive_int("ACCURATE_SO_LIST_CACHE_MAX_ENTRIES", 256) == 256
    monkeypatch.setenv("ACCURATE_NAME_CACHE_MAX_ENTRIES", "12")
    assert _env_positive_int("ACCURATE_NAME_CACHE_MAX_ENTRIES", 5000) == 12


def test_sweep_does_not_mutate_unsafely_during_iteration():
    for i in range(200):
        _seed_name(f"branch:{i}", f"N{i}", expires_in_s=-1 if i % 2 == 0 else 60)
        _seed_so(f"list:k:{i}", age_s=_LIST_CACHE_TTL + 1 if i % 3 == 0 else 1)
    before_name = len(_NAME_CACHE)
    before_so = len(_SO_LIST_CACHE)
    stats = maybe_sweep_accurate_caches(force=True)
    assert stats["skipped"] == 0
    assert len(_NAME_CACHE) < before_name
    assert len(_SO_LIST_CACHE) < before_so
    now = time.monotonic()
    assert all(exp > now for exp, _ in _NAME_CACHE.values())
    now_wall = time.time()
    assert all((now_wall - e["at"]) < _LIST_CACHE_TTL for e in _SO_LIST_CACHE.values())


def test_large_synthetic_cache_shrinks_after_opportunistic_sweep():
    for i in range(5000):
        _seed_name(f"branch:big:{i}", f"N{i}", expires_in_s=-5)
    for i in range(400):
        _seed_so(f"list:big:{i}", age_s=_LIST_CACHE_TTL + 10)
    t0 = time.perf_counter()
    stats = maybe_sweep_accurate_caches(force=True)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert stats["name_expired"] == 5000
    assert stats["so_expired"] == 400
    assert len(_NAME_CACHE) == 0
    assert len(_SO_LIST_CACHE) == 0
    assert elapsed_ms < 2000


def test_sweep_performs_no_network(monkeypatch):
    calls = {"n": 0}

    class Boom:
        def __init__(self, *a, **k):
            calls["n"] += 1
            raise AssertionError("sweep must not open HTTP")

    monkeypatch.setattr(acc.httpx, "AsyncClient", Boom)
    for i in range(50):
        _seed_name(f"b:{i}", "x", expires_in_s=-1)
        _seed_so(f"s:{i}", age_s=_LIST_CACHE_TTL + 1)
    maybe_sweep_accurate_caches(force=True)
    assert calls["n"] == 0


def test_sweep_throttled_unless_over_threshold_or_forced(monkeypatch):
    monkeypatch.setattr(acc, "_CACHE_SWEEP_MIN_INTERVAL_S", 60.0)
    monkeypatch.setattr(acc, "ACCURATE_NAME_CACHE_MAX_ENTRIES", 10)
    _seed_name("a", "A", expires_in_s=-1)
    reset_accurate_cache_sweep_clock()
    first = maybe_sweep_accurate_caches(force=False)
    assert first["skipped"] == 0
    _seed_name("live", "Live", expires_in_s=300)
    skipped = maybe_sweep_accurate_caches(force=False)
    assert skipped["skipped"] == 1
    assert _NAME_CACHE["live"][1] == "Live"
    for i in range(12):
        _seed_name(f"over:{i}", "X", expires_in_s=-1)
    assert len(_NAME_CACHE) > 10
    early = maybe_sweep_accurate_caches(force=False)
    assert early["skipped"] == 0
    assert early["name_expired"] >= 12
    assert _NAME_CACHE["live"][1] == "Live"
    assert len(_NAME_CACHE) == 1


def test_synthetic_memory_shrink_after_sweep():
    tracemalloc.start()
    for i in range(3000):
        _seed_name(f"mem:{i}", "X" * 64, expires_in_s=-1)
        _seed_so(
            f"memso:{i}",
            age_s=_LIST_CACHE_TTL + 1,
            payload={"rows": [{"n": i, "blob": "Y" * 32}], "total": 1},
        )
    before_count = (len(_NAME_CACHE), len(_SO_LIST_CACHE))
    t0 = time.perf_counter()
    stats = maybe_sweep_accurate_caches(force=True)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    tracemalloc.stop()
    assert before_count == (3000, 3000)
    assert stats["name_size"] == 0 and stats["so_size"] == 0
    assert elapsed_ms < 2000


def test_ttl_constants_unchanged():
    assert _LIST_CACHE_TTL == 60
    assert ACCURATE_NAME_CACHE_TTL_S == 600.0


def test_fetch_after_eviction_stores_fresh_entry():
    client = AccurateClient()
    _seed_name("branch:refill", "Old", expires_in_s=-1)
    maybe_sweep_accurate_caches(force=True)
    assert "branch:refill" not in _NAME_CACHE

    async def fetcher():
        return "NewName", True

    assert _await(client._lookup_cached_name("branch:refill", fetcher)) == "NewName"
    exp, name = _NAME_CACHE["branch:refill"]
    assert name == "NewName"
    assert exp > time.monotonic()
