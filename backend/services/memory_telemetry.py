"""Optional low-overhead FastAPI/ASGI request memory telemetry (Phase C).

Diagnostic instrumentation only — not a memory-leak fix.

Default: DISABLED. Activation requires explicit env opt-in and a process restart.
Environment variables are read once at install / config load time (no hot-reload).

Durable output (when enabled):
- Request path only updates in-memory samples + bounded aggregates (no disk I/O).
- A single FastAPI startup/shutdown-managed flush task writes aggregates at most
  once every 60 seconds (off the event loop via asyncio.to_thread).
- Files land in a dedicated diagnostic directory (never frontend webroot), mode 0600.
- Total directory size capped at 10 MiB with oldest-file rotation; fail-safe stop
  after repeated write failures.
- Wall-clock activation deadline persisted beside output so restart does not
  silently extend the diagnostic window. Restart does NOT invent a new window
  unless the deadline file is absent/cleared after expiry.

Kill switch: set ETERNAL_MEMORY_TELEMETRY_ENABLED=0 (or unset) and restart.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Mapping, MutableMapping, Optional, Tuple

logger = logging.getLogger("eternal.memory_telemetry")

# Absolute ceiling: 24 hours.
_MAX_ACTIVATION_SECONDS = 24 * 60 * 60
_DEFAULT_SAMPLE_RATE = 0.05
_DEFAULT_MAX_RECORDS = 256
_DEFAULT_MAX_SECONDS = 3600
_DEFAULT_RSS_INTERVAL_S = 5.0
_DEFAULT_FLUSH_INTERVAL_S = 60.0
_DEFAULT_MAX_DISK_BYTES = 10 * 1024 * 1024
_DEFAULT_MAX_AGG_KEYS = 256
_DEFAULT_OUTPUT_DIR = "/var/tmp/eternal-memory-telemetry"
_MAX_CONSECUTIVE_WRITE_FAILURES = 5
_UNMATCHED = "UNMATCHED"
_RSS_DELTA_NOTE = "correlation_only_not_exclusive"
_DEADLINE_FILENAME = "activation_deadline.json"
_STATUS_FILENAME = "telemetry_status.json"


@dataclass(frozen=True)
class MemoryTelemetryConfig:
    enabled: bool
    sample_rate: float
    max_records: int
    max_seconds: float
    rss_sample_interval_s: float
    started_at_monotonic: float
    process_marker: str
    pid: int
    output_dir: str
    flush_interval_s: float
    max_disk_bytes: int
    max_aggregate_keys: int
    deadline_unix: Optional[float]


# Process-scoped state (in-memory counters reset on restart; disk files persist).
_config: Optional[MemoryTelemetryConfig] = None
_buffer: Optional[Deque[Dict[str, Any]]] = None
_buffer_lock = threading.Lock()
_in_flight = 0
_in_flight_lock = threading.Lock()
_dropped_records = 0
_dropped_aggregates = 0
_last_rss_kib: Optional[int] = None
_last_rss_mono: float = 0.0
_rss_lock = threading.Lock()
_aggregates: Dict[str, Dict[str, Any]] = {}
_agg_lock = threading.Lock()
_write_failures = 0
_consecutive_write_failures = 0
_flush_stopped_due_to_errors = False
_disk_writes_disabled = False
_flush_task: Optional[asyncio.Task] = None
_flush_lock = threading.Lock()
_last_flush_mono: float = 0.0
_request_path_disk_guard = threading.local()


def _env_flag(name: str, default: str = "0") -> bool:
    raw = (os.environ.get(name, default) or default).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _parse_float(name: str, default: float, *, lo: float, hi: float) -> Optional[float]:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value < lo or value > hi or value != value:  # NaN check
        return None
    return value


def _parse_int(name: str, default: int, *, lo: int, hi: int) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip(), 10)
    except (TypeError, ValueError):
        return None
    if value < lo or value > hi:
        return None
    return value


def _disabled_config(
    *,
    now: float,
    process_marker: str,
    pid: int,
    output_dir: str = _DEFAULT_OUTPUT_DIR,
) -> MemoryTelemetryConfig:
    return MemoryTelemetryConfig(
        enabled=False,
        sample_rate=0.0,
        max_records=_DEFAULT_MAX_RECORDS,
        max_seconds=0.0,
        rss_sample_interval_s=_DEFAULT_RSS_INTERVAL_S,
        started_at_monotonic=now,
        process_marker=process_marker,
        pid=pid,
        output_dir=output_dir,
        flush_interval_s=_DEFAULT_FLUSH_INTERVAL_S,
        max_disk_bytes=_DEFAULT_MAX_DISK_BYTES,
        max_aggregate_keys=_DEFAULT_MAX_AGG_KEYS,
        deadline_unix=None,
    )


def _ensure_output_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
        return True
    except Exception:
        return False


def _read_deadline(path: Path) -> Optional[float]:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        value = float(data.get("deadline_unix"))
        if value != value:  # NaN
            return None
        return value
    except Exception:
        return None


def _write_deadline(path: Path, deadline_unix: float, max_seconds: float) -> bool:
    try:
        payload = {
            "deadline_unix": float(deadline_unix),
            "max_seconds": float(max_seconds),
            "note": "wall_clock_activation_deadline_not_business_data",
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        os.chmod(path, 0o600)
        return True
    except Exception:
        return False


def _resolve_deadline_and_remaining(
    *,
    enabled: bool,
    max_seconds: float,
    output_dir: str,
) -> Tuple[Optional[float], float, bool]:
    """Return (deadline_unix, remaining_seconds, expired_persistent).

    Restart does not extend activation: an existing deadline file is authoritative.
    """
    if not enabled:
        return None, 0.0, False
    dir_path = Path(output_dir)
    if not _ensure_output_dir(dir_path):
        # Cannot persist deadline safely → fail closed for durable mode but keep
        # in-memory diagnostics with configured max_seconds (no disk).
        return None, max_seconds, False
    deadline_path = dir_path / _DEADLINE_FILENAME
    existing = _read_deadline(deadline_path)
    now = time.time()
    if existing is not None:
        if existing <= now:
            return existing, 0.0, True
        remaining = min(existing - now, max_seconds, float(_MAX_ACTIVATION_SECONDS))
        return existing, remaining, False
    deadline = now + max_seconds
    _write_deadline(deadline_path, deadline, max_seconds)
    return deadline, max_seconds, False


def load_config(*, now_monotonic: Optional[float] = None) -> MemoryTelemetryConfig:
    """Load config from environment. Invalid values fail safely to disabled."""
    now = time.monotonic() if now_monotonic is None else now_monotonic
    pid = os.getpid()
    process_marker = f"pid={pid};start_mono={now:.3f}"

    output_dir_raw = (os.environ.get("ETERNAL_MEMORY_TELEMETRY_DIR") or "").strip()
    output_dir = output_dir_raw or _DEFAULT_OUTPUT_DIR
    # Refuse obvious frontend/webroot paths.
    lowered = output_dir.lower().replace("\\", "/")
    if any(
        part in lowered
        for part in ("/frontend/", "/static/", "/public/", "/build/", "/dist/")
    ):
        logger.warning("memory_telemetry refuses webroot-like output dir; OFF")
        return _disabled_config(now=now, process_marker=process_marker, pid=pid)

    enabled_requested = _env_flag("ETERNAL_MEMORY_TELEMETRY_ENABLED", "0")
    sample_rate = _parse_float(
        "ETERNAL_MEMORY_TELEMETRY_SAMPLE_RATE",
        _DEFAULT_SAMPLE_RATE,
        lo=0.0,
        hi=1.0,
    )
    max_records = _parse_int(
        "ETERNAL_MEMORY_TELEMETRY_MAX_RECORDS",
        _DEFAULT_MAX_RECORDS,
        lo=1,
        hi=10_000,
    )
    max_seconds = _parse_float(
        "ETERNAL_MEMORY_TELEMETRY_MAX_SECONDS",
        float(_DEFAULT_MAX_SECONDS),
        lo=1.0,
        hi=float(_MAX_ACTIVATION_SECONDS),
    )
    rss_interval = _parse_float(
        "ETERNAL_MEMORY_TELEMETRY_RSS_INTERVAL_S",
        _DEFAULT_RSS_INTERVAL_S,
        lo=0.5,
        hi=300.0,
    )
    flush_interval = _parse_float(
        "ETERNAL_MEMORY_TELEMETRY_FLUSH_INTERVAL_S",
        _DEFAULT_FLUSH_INTERVAL_S,
        lo=60.0,
        hi=3600.0,
    )
    max_disk = _parse_int(
        "ETERNAL_MEMORY_TELEMETRY_MAX_DISK_BYTES",
        _DEFAULT_MAX_DISK_BYTES,
        lo=1024,
        hi=_DEFAULT_MAX_DISK_BYTES,
    )
    max_agg_keys = _parse_int(
        "ETERNAL_MEMORY_TELEMETRY_MAX_AGG_KEYS",
        _DEFAULT_MAX_AGG_KEYS,
        lo=1,
        hi=2000,
    )

    if (
        sample_rate is None
        or max_records is None
        or max_seconds is None
        or rss_interval is None
        or flush_interval is None
        or max_disk is None
        or max_agg_keys is None
    ):
        logger.warning(
            "memory_telemetry invalid config; failing safely to OFF",
            extra={"event": "memory_telemetry_config_invalid"},
        )
        return _disabled_config(
            now=now, process_marker=process_marker, pid=pid, output_dir=output_dir
        )

    max_seconds = min(float(max_seconds), float(_MAX_ACTIVATION_SECONDS))
    deadline_unix, remaining, expired = _resolve_deadline_and_remaining(
        enabled=bool(enabled_requested),
        max_seconds=max_seconds,
        output_dir=output_dir,
    )
    if expired:
        # Env still "enabled" but persistent deadline elapsed — do not sample/write.
        logger.warning(
            "memory_telemetry persistent deadline expired; inactive until deadline file cleared",
        )
        return MemoryTelemetryConfig(
            enabled=True,
            sample_rate=float(sample_rate),
            max_records=int(max_records),
            max_seconds=0.0,
            rss_sample_interval_s=float(rss_interval),
            started_at_monotonic=now,
            process_marker=process_marker,
            pid=pid,
            output_dir=output_dir,
            flush_interval_s=float(flush_interval),
            max_disk_bytes=int(max_disk),
            max_aggregate_keys=int(max_agg_keys),
            deadline_unix=deadline_unix,
        )

    return MemoryTelemetryConfig(
        enabled=bool(enabled_requested),
        sample_rate=float(sample_rate),
        max_records=int(max_records),
        max_seconds=float(remaining if enabled_requested else 0.0),
        rss_sample_interval_s=float(rss_interval),
        started_at_monotonic=now,
        process_marker=process_marker,
        pid=pid,
        output_dir=output_dir,
        flush_interval_s=float(flush_interval),
        max_disk_bytes=int(max_disk),
        max_aggregate_keys=int(max_agg_keys),
        deadline_unix=deadline_unix if enabled_requested else None,
    )


def reset_state(config: Optional[MemoryTelemetryConfig] = None) -> MemoryTelemetryConfig:
    """Replace process state. Used by install() and isolated tests."""
    global _config, _buffer, _in_flight, _dropped_records, _dropped_aggregates
    global _last_rss_kib, _last_rss_mono, _aggregates
    global _write_failures, _consecutive_write_failures, _flush_stopped_due_to_errors
    global _disk_writes_disabled, _last_flush_mono
    cfg = config if config is not None else load_config()
    with _buffer_lock:
        _config = cfg
        _buffer = deque(maxlen=cfg.max_records) if cfg.enabled else deque(maxlen=1)
        _dropped_records = 0
    with _agg_lock:
        _aggregates = {}
        _dropped_aggregates = 0
    with _in_flight_lock:
        _in_flight = 0
    with _rss_lock:
        _last_rss_kib = None
        _last_rss_mono = 0.0
    _write_failures = 0
    _consecutive_write_failures = 0
    _flush_stopped_due_to_errors = False
    _disk_writes_disabled = False
    _last_flush_mono = 0.0
    return cfg


def get_config() -> Optional[MemoryTelemetryConfig]:
    return _config


def is_active(*, now_monotonic: Optional[float] = None, now_unix: Optional[float] = None) -> bool:
    cfg = _config
    if cfg is None or not cfg.enabled:
        return False
    if cfg.max_seconds <= 0:
        return False
    wall = time.time() if now_unix is None else now_unix
    if cfg.deadline_unix is not None and wall >= cfg.deadline_unix:
        return False
    now = time.monotonic() if now_monotonic is None else now_monotonic
    if (now - cfg.started_at_monotonic) >= cfg.max_seconds:
        return False
    return True


def get_in_flight() -> int:
    with _in_flight_lock:
        return _in_flight


def get_dropped_count() -> int:
    with _buffer_lock:
        return int(_dropped_records)


def get_dropped_aggregate_count() -> int:
    with _agg_lock:
        return int(_dropped_aggregates)


def get_write_failure_count() -> int:
    return int(_write_failures)


def get_consecutive_write_failures() -> int:
    return int(_consecutive_write_failures)


def disk_writes_stopped() -> bool:
    return bool(_flush_stopped_due_to_errors or _disk_writes_disabled)


def get_records() -> List[Dict[str, Any]]:
    with _buffer_lock:
        if _buffer is None:
            return []
        return list(_buffer)


def get_aggregates() -> Dict[str, Dict[str, Any]]:
    with _agg_lock:
        return {k: dict(v) for k, v in _aggregates.items()}


def clear_records() -> None:
    with _buffer_lock:
        if _buffer is not None:
            _buffer.clear()
        global _dropped_records
        _dropped_records = 0
    with _agg_lock:
        _aggregates.clear()
        global _dropped_aggregates
        _dropped_aggregates = 0


def read_rss_kib() -> Optional[int]:
    """Best-effort current RSS in KiB via /proc (Linux). Never raises."""
    try:
        with open("/proc/self/status", "r", encoding="ascii", errors="ignore") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except Exception:
        return None
    return None


def sample_rss_kib(*, force: bool = False, now_monotonic: Optional[float] = None) -> Optional[int]:
    """Rate-limited RSS sample. Cached value reused between intervals."""
    cfg = _config
    if cfg is None:
        return None
    now = time.monotonic() if now_monotonic is None else now_monotonic
    with _rss_lock:
        global _last_rss_kib, _last_rss_mono
        if (
            not force
            and _last_rss_kib is not None
            and (now - _last_rss_mono) < cfg.rss_sample_interval_s
        ):
            return _last_rss_kib
        value = read_rss_kib()
        if value is not None:
            _last_rss_kib = value
            _last_rss_mono = now
        return _last_rss_kib


def route_template_from_scope(scope: Mapping[str, Any]) -> str:
    """Return normalized FastAPI/Starlette route template; never raw path params."""
    route = scope.get("route")
    path_template: Optional[str] = None
    if route is not None:
        path_template = getattr(route, "path", None)
        if not isinstance(path_template, str) or not path_template:
            path_template = getattr(route, "path_format", None)
    if isinstance(path_template, str) and path_template:
        safe = path_template.split("?", 1)[0].strip()
        return safe if safe else _UNMATCHED
    return _UNMATCHED


def _should_sample(cfg: MemoryTelemetryConfig) -> bool:
    if cfg.sample_rate <= 0.0:
        return False
    if cfg.sample_rate >= 1.0:
        return True
    return random.random() < cfg.sample_rate


def _append_record(record: Dict[str, Any]) -> None:
    global _dropped_records
    with _buffer_lock:
        if _buffer is None:
            return
        if len(_buffer) >= (_buffer.maxlen or 0):
            _dropped_records += 1
        _buffer.append(record)


def _aggregate_record(record: Dict[str, Any]) -> None:
    """Update bounded low-cardinality aggregates (memory only)."""
    global _dropped_aggregates
    cfg = _config
    if cfg is None:
        return
    method = str(record.get("method") or "UNKNOWN")
    route = str(record.get("route_template") or _UNMATCHED)
    key = f"{method} {route}"
    with _agg_lock:
        bucket = _aggregates.get(key)
        if bucket is None:
            if len(_aggregates) >= cfg.max_aggregate_keys:
                _dropped_aggregates += 1
                return
            bucket = {
                "method": method,
                "route_template": route,
                "count": 0,
                "duration_ms_sum": 0.0,
                "duration_ms_max": 0.0,
                "in_flight_max": 0,
                "rss_kib_last": None,
                "rss_delta_kib_sum": 0,
                "rss_delta_note": _RSS_DELTA_NOTE,
            }
            _aggregates[key] = bucket
        bucket["count"] += 1
        dur = float(record.get("duration_ms") or 0.0)
        bucket["duration_ms_sum"] += dur
        if dur > bucket["duration_ms_max"]:
            bucket["duration_ms_max"] = dur
        inflight = int(record.get("in_flight") or 0)
        if inflight > bucket["in_flight_max"]:
            bucket["in_flight_max"] = inflight
        if record.get("rss_kib") is not None:
            bucket["rss_kib_last"] = record.get("rss_kib")
        if record.get("rss_delta_kib") is not None:
            bucket["rss_delta_kib_sum"] += int(record["rss_delta_kib"])


def _safe_log_record(record: Dict[str, Any]) -> None:
    try:
        logger.info(
            "memory_telemetry sample method=%s route=%s duration_ms=%.2f "
            "in_flight=%s rss_kib=%s rss_delta_kib=%s note=%s marker=%s",
            record.get("method"),
            record.get("route_template"),
            record.get("duration_ms"),
            record.get("in_flight"),
            record.get("rss_kib"),
            record.get("rss_delta_kib"),
            record.get("rss_delta_note"),
            record.get("process_marker"),
        )
    except Exception:
        pass


def _dir_total_bytes(dir_path: Path) -> int:
    total = 0
    try:
        for p in dir_path.iterdir():
            if p.is_file():
                try:
                    total += p.stat().st_size
                except Exception:
                    pass
    except Exception:
        return total
    return total


def _rotate_to_budget(dir_path: Path, max_bytes: int, incoming_size: int) -> bool:
    """Delete oldest flush_*.json until incoming fits. Returns False if impossible."""
    try:
        files = sorted(
            [p for p in dir_path.glob("flush_*.json") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
        )
    except Exception:
        return False
    total = _dir_total_bytes(dir_path)
    while files and total + incoming_size > max_bytes:
        victim = files.pop(0)
        try:
            size = victim.stat().st_size
            victim.unlink(missing_ok=True)
            total = max(0, total - size)
        except Exception:
            return False
    return total + incoming_size <= max_bytes


def _write_status_file(dir_path: Path, payload: Dict[str, Any]) -> None:
    try:
        path = dir_path / _STATUS_FILENAME
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        os.chmod(path, 0o600)
    except Exception:
        pass


def _record_write_failure(exc: BaseException) -> None:
    global _write_failures, _consecutive_write_failures, _flush_stopped_due_to_errors
    _write_failures += 1
    _consecutive_write_failures += 1
    logger.warning(
        "memory_telemetry flush write failed count=%s consecutive=%s err=%s",
        _write_failures,
        _consecutive_write_failures,
        type(exc).__name__,
    )
    cfg = _config
    if cfg is not None:
        _write_status_file(
            Path(cfg.output_dir),
            {
                "ok": False,
                "write_failures": _write_failures,
                "consecutive_write_failures": _consecutive_write_failures,
                "stopped": _consecutive_write_failures >= _MAX_CONSECUTIVE_WRITE_FAILURES,
                "ts_unix": int(time.time()),
            },
        )
    if _consecutive_write_failures >= _MAX_CONSECUTIVE_WRITE_FAILURES:
        _flush_stopped_due_to_errors = True


def flush_aggregates_to_disk(*, force: bool = False) -> Optional[Path]:
    """Flush current aggregates to disk. Never called from request path.

    Returns written path or None. Fail-open: never raises to callers that ignore it.
    """
    global _last_flush_mono, _consecutive_write_failures, _disk_writes_disabled
    cfg = _config
    if cfg is None or not cfg.enabled:
        return None
    if _flush_stopped_due_to_errors or _disk_writes_disabled:
        return None
    # Stop writing after activation expiry (unless forced final empty skip).
    if not is_active() and not force:
        return None
    if getattr(_request_path_disk_guard, "in_request", False):
        # Hard guard: never write from HTTP middleware path.
        return None

    now_mono = time.monotonic()
    with _flush_lock:
        if not force and _last_flush_mono and (now_mono - _last_flush_mono) < cfg.flush_interval_s:
            return None
        with _agg_lock:
            if not _aggregates:
                _last_flush_mono = now_mono
                return None
            snapshot = [dict(v) for v in _aggregates.values()]
            _aggregates.clear()

        dir_path = Path(cfg.output_dir)
        if not _ensure_output_dir(dir_path):
            _record_write_failure(OSError("mkdir_failed"))
            # restore snapshot best-effort
            with _agg_lock:
                for item in snapshot:
                    key = f"{item['method']} {item['route_template']}"
                    if key not in _aggregates and len(_aggregates) < cfg.max_aggregate_keys:
                        _aggregates[key] = item
            return None

        payload = {
            "ts_unix": int(time.time()),
            "pid": cfg.pid,
            "process_marker": cfg.process_marker,
            "rss_delta_note": _RSS_DELTA_NOTE,
            "group_count": len(snapshot),
            "groups": snapshot,
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if not _rotate_to_budget(dir_path, cfg.max_disk_bytes, len(raw)):
            _disk_writes_disabled = True
            _record_write_failure(OSError("disk_cap_exhausted"))
            _write_status_file(
                dir_path,
                {
                    "ok": False,
                    "reason": "disk_cap_exhausted",
                    "max_disk_bytes": cfg.max_disk_bytes,
                    "write_failures": _write_failures,
                    "ts_unix": int(time.time()),
                },
            )
            return None

        name = f"flush_{int(time.time())}_{cfg.pid}.json"
        target = dir_path / name
        try:
            tmp = target.with_suffix(".tmp")
            with open(tmp, "wb") as fh:
                fh.write(raw)
            os.chmod(tmp, 0o600)
            tmp.replace(target)
            os.chmod(target, 0o600)
            _last_flush_mono = now_mono
            _consecutive_write_failures = 0
            _write_status_file(
                dir_path,
                {
                    "ok": True,
                    "last_flush_file": target.name,
                    "write_failures": _write_failures,
                    "ts_unix": int(time.time()),
                },
            )
            return target
        except Exception as exc:
            # Restore snapshot so a transient write failure does not drop metrics.
            with _agg_lock:
                for item in snapshot:
                    key = f"{item['method']} {item['route_template']}"
                    if key not in _aggregates and len(_aggregates) < cfg.max_aggregate_keys:
                        _aggregates[key] = item
            _record_write_failure(exc)
            return None


async def _flush_loop() -> None:
    cfg = _config
    interval = float(cfg.flush_interval_s) if cfg is not None else _DEFAULT_FLUSH_INTERVAL_S
    try:
        while True:
            await asyncio.sleep(interval)
            if _flush_stopped_due_to_errors or _disk_writes_disabled:
                break
            if not is_active():
                # Activation expired: one final best-effort flush, then stop.
                try:
                    await asyncio.to_thread(flush_aggregates_to_disk, force=True)
                except Exception:
                    pass
                break
            try:
                await asyncio.to_thread(flush_aggregates_to_disk)
            except Exception:
                pass
    except asyncio.CancelledError:
        try:
            await asyncio.to_thread(flush_aggregates_to_disk, force=True)
        except Exception:
            pass
        raise


def start_flush_worker() -> None:
    """Start bounded periodic flush task (idempotent)."""
    global _flush_task
    cfg = _config
    if cfg is None or not cfg.enabled or not is_active():
        return
    if _flush_task is not None and not _flush_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _flush_task = loop.create_task(_flush_loop(), name="eternal-memory-telemetry-flush")


async def stop_flush_worker() -> None:
    """Cancel flush task and perform a final off-loop flush."""
    global _flush_task
    task = _flush_task
    _flush_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    # Always attempt a final flush after cancel (idempotent if empty).
    try:
        await asyncio.to_thread(flush_aggregates_to_disk, force=True)
    except Exception:
        pass


class MemoryTelemetryMiddleware:
    """Minimal pure-ASGI middleware. Does not subclass BaseHTTPMiddleware."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        if not is_active():
            await self.app(scope, receive, send)
            return

        cfg = _config
        if cfg is None:
            await self.app(scope, receive, send)
            return

        sampled = False
        started = 0.0
        rss_before: Optional[int] = None
        in_flight_snapshot = 0
        status_holder: Dict[str, Any] = {"status": None}
        _request_path_disk_guard.in_request = True

        try:
            with _in_flight_lock:
                global _in_flight
                _in_flight += 1
                in_flight_snapshot = _in_flight
            sampled = _should_sample(cfg)
            if sampled:
                started = time.perf_counter()
                rss_before = sample_rss_kib()
        except Exception:
            try:
                await self.app(scope, receive, send)
            finally:
                try:
                    with _in_flight_lock:
                        if _in_flight > 0:
                            _in_flight -= 1
                except Exception:
                    pass
                _request_path_disk_guard.in_request = False
            return

        async def send_wrapper(message: Mapping[str, Any]) -> None:
            try:
                if message.get("type") == "http.response.start":
                    status_holder["status"] = message.get("status")
            except Exception:
                pass
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper if sampled else send)
        except BaseException:
            try:
                with _in_flight_lock:
                    if _in_flight > 0:
                        _in_flight -= 1
            except Exception:
                pass
            _request_path_disk_guard.in_request = False
            raise
        else:
            try:
                with _in_flight_lock:
                    if _in_flight > 0:
                        _in_flight -= 1
            except Exception:
                pass

            if not sampled:
                _request_path_disk_guard.in_request = False
                return

            try:
                duration_ms = (time.perf_counter() - started) * 1000.0
                rss_after = sample_rss_kib()
                rss_delta = None
                if rss_before is not None and rss_after is not None:
                    rss_delta = int(rss_after) - int(rss_before)
                method = str(scope.get("method") or "UNKNOWN")
                route_template = route_template_from_scope(scope)
                record = {
                    "ts_unix": int(time.time()),
                    "method": method,
                    "route_template": route_template,
                    "duration_ms": round(duration_ms, 3),
                    "in_flight": int(in_flight_snapshot),
                    "status": status_holder.get("status"),
                    "rss_kib": rss_after,
                    "rss_delta_kib": rss_delta,
                    "rss_delta_note": _RSS_DELTA_NOTE,
                    "process_marker": cfg.process_marker,
                    "pid": cfg.pid,
                }
                _append_record(record)
                _aggregate_record(record)
                _safe_log_record(record)
            except Exception:
                pass
            finally:
                _request_path_disk_guard.in_request = False


def install_memory_telemetry(app: Any) -> MemoryTelemetryConfig:
    """Register middleware + startup/shutdown flush worker.

    Restart required for env changes (no hot-reload).
    Kill switch: ETERNAL_MEMORY_TELEMETRY_ENABLED=0 and restart.
    Persistent deadline file limits total activation across restarts (wall clock).
    """
    cfg = reset_state(load_config())
    app.add_middleware(MemoryTelemetryMiddleware)

    @app.on_event("startup")
    async def _eternal_memory_telemetry_startup() -> None:
        start_flush_worker()

    @app.on_event("shutdown")
    async def _eternal_memory_telemetry_shutdown() -> None:
        await stop_flush_worker()

    if cfg.enabled and is_active():
        logger.info(
            "memory_telemetry ENABLED sample_rate=%s max_records=%s max_seconds=%s "
            "flush_interval_s=%s output_dir=%s marker=%s",
            cfg.sample_rate,
            cfg.max_records,
            cfg.max_seconds,
            cfg.flush_interval_s,
            cfg.output_dir,
            cfg.process_marker,
        )
    elif cfg.enabled:
        logger.info(
            "memory_telemetry env ENABLED but inactive (expired deadline or zero window)"
        )
    else:
        logger.info("memory_telemetry DISABLED (default or fail-safe)")
    return cfg
