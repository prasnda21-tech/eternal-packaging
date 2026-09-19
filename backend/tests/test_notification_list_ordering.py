"""Targeted: GET /notifications list is unread-first BEFORE limit."""
from __future__ import annotations

import copy
import inspect
from typing import Any, Dict, List

import pytest

from services import notifications as notif
from services.notification_http import register_notification_routes


def _match(doc: dict, query: dict) -> bool:
    for k, v in (query or {}).items():
        if k.startswith("$"):
            continue
        actual = doc.get(k)
        if isinstance(v, dict):
            if "$in" in v:
                if actual not in v["$in"]:
                    return False
            elif "$ne" in v:
                if actual == v["$ne"]:
                    return False
            else:
                if actual != v:
                    return False
        elif actual != v:
            return False
    return True


class FakeCursor:
    def __init__(self, rows: List[dict]):
        self._rows = rows

    def sort(self, key, direction=-1):
        field = key[0][0] if isinstance(key, list) else key
        self._rows = sorted(self._rows, key=lambda r: r.get(field) or "", reverse=(direction == -1))
        return self

    async def to_list(self, n):
        return copy.deepcopy(self._rows[:n])


class FakeCollection:
    def __init__(self, initial=None):
        self.docs: List[dict] = copy.deepcopy(initial or [])

    def find(self, query=None, projection=None):
        rows = []
        for d in self.docs:
            if _match(d, query or {}):
                out = copy.deepcopy(d)
                if projection and projection.get("_id") == 0:
                    out.pop("_id", None)
                rows.append(out)
        return FakeCursor(rows)


class FakeDb:
    def __init__(self, notifications=None):
        self.notifications = FakeCollection(notifications)

    def __getitem__(self, name):
        return getattr(self, name)


def _row(nid: str, *, created_at: str, read_at: Any, user_id: str = "u-1") -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "id": nid,
        "user_id": user_id,
        "title": nid,
        "body": nid,
        "created_at": created_at,
    }
    if read_at is not Ellipsis:
        doc["read_at"] = read_at
    return doc


def _ids(rows: List[dict]) -> List[str]:
    return [r["id"] for r in rows]


@pytest.mark.anyio
async def test_edge_50_newer_read_plus_1_old_unread_old_unread_survives_limit_50():
    """50 newest READ + 1 older UNREAD → unread is kept and first; oldest read is clipped."""
    docs = []
    for i in range(50):
        # n00 newest read … n49 oldest of the 50 reads
        docs.append(
            _row(
                f"r{i:02d}",
                created_at=f"2026-09-11T12:{59 - i:02d}:00Z",
                read_at=f"2026-09-11T13:{i:02d}:00Z",
            )
        )
    docs.append(_row("old-unread", created_at="2026-09-11T10:00:00Z", read_at=None))
    db = FakeDb(docs)

    rows = await notif.list_notifications_for_user(db, "u-1", limit=50)
    assert len(rows) == 50
    assert rows[0]["id"] == "old-unread"
    assert rows[0]["is_read"] is False
    assert rows[0]["read_at"] is None
    ids = _ids(rows)
    assert "old-unread" in ids
    assert "r49" not in ids
    assert ids[1:] == [f"r{i:02d}" for i in range(49)]
    assert all(r["is_read"] for r in rows[1:])


@pytest.mark.anyio
async def test_a_two_unread_two_read_unread_first():
    docs = [
        _row("C", created_at="2026-09-11T12:30:00Z", read_at="x"),
        _row("A", created_at="2026-09-11T12:00:00Z", read_at=None),
        _row("B", created_at="2026-09-11T11:00:00Z", read_at=None),
        _row("D", created_at="2026-09-11T10:00:00Z", read_at="y"),
    ]
    rows = await notif.list_notifications_for_user(FakeDb(docs), "u-1", limit=50)
    assert _ids(rows) == ["A", "B", "C", "D"]


@pytest.mark.anyio
async def test_b_unread_created_at_desc():
    docs = [
        _row("old", created_at="2026-09-11T11:00:00Z", read_at=None),
        _row("new", created_at="2026-09-11T12:00:00Z", read_at=None),
    ]
    rows = await notif.list_notifications_for_user(FakeDb(docs), "u-1", limit=50)
    assert _ids(rows) == ["new", "old"]


@pytest.mark.anyio
async def test_c_read_created_at_desc():
    docs = [
        _row("old", created_at="2026-09-11T10:00:00Z", read_at="x"),
        _row("new", created_at="2026-09-11T12:30:00Z", read_at="y"),
    ]
    rows = await notif.list_notifications_for_user(FakeDb(docs), "u-1", limit=50)
    assert _ids(rows) == ["new", "old"]


@pytest.mark.anyio
async def test_d_missing_read_at_is_unread():
    docs = [
        _row("read-newer", created_at="2026-09-11T12:30:00Z", read_at="z"),
        _row("missing", created_at="2026-09-11T11:00:00Z", read_at=Ellipsis),
        _row("empty", created_at="2026-09-11T10:30:00Z", read_at=""),
    ]
    rows = await notif.list_notifications_for_user(FakeDb(docs), "u-1", limit=50)
    assert _ids(rows) == ["missing", "empty", "read-newer"]
    assert rows[0]["is_read"] is False
    assert rows[1]["is_read"] is False
    assert rows[2]["is_read"] is True


def test_e_default_and_http_limit_remain_50():
    src = inspect.getsource(notif.list_notifications_for_user)
    assert "limit: int = 50" in src
    http_src = inspect.getsource(register_notification_routes)
    assert "limit: int = Query(50, ge=1, le=100)" in http_src
    assert "list_notifications_for_user(db, uid, limit=limit)" in http_src
