"""Injectable UTC clock for ownership evaluation tests."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

_frozen: Optional[datetime] = None
_factory: Optional[Callable[[], datetime]] = None


def now_utc() -> datetime:
    if _factory is not None:
        value = _factory()
    elif _frozen is not None:
        value = _frozen
    else:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_now() -> str:
    return now_utc().isoformat()


def set_frozen(value: Optional[datetime]) -> None:
    global _frozen, _factory
    _factory = None
    if value is None:
        _frozen = None
        return
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    _frozen = value.astimezone(timezone.utc)


def set_factory(fn: Optional[Callable[[], datetime]]) -> None:
    global _factory, _frozen
    _frozen = None
    _factory = fn


def reset() -> None:
    set_frozen(None)
    set_factory(None)


def parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
