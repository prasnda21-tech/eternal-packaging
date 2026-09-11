"""Focused regression: Customer Design Approval → Designer/CRM notifications."""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List

import pytest
from pymongo.errors import DuplicateKeyError

from services import notifications as notif


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
        if isinstance(key, list):
            field, direction = key[0]
        else:
            field = key
        self._rows = sorted(self._rows, key=lambda r: r.get(field) or "", reverse=(direction == -1))
        return self

    async def to_list(self, n):
        return copy.deepcopy(self._rows[:n])


class FakeCollection:
    def __init__(self, initial=None):
        self.docs: List[dict] = copy.deepcopy(initial or [])
        self.fail_insert = False

    async def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _match(d, query or {}):
                out = copy.deepcopy(d)
                if projection and projection.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    def find(self, query=None, projection=None):
        rows = []
        for d in self.docs:
            if _match(d, query or {}):
                out = copy.deepcopy(d)
                if projection and projection.get("_id") == 0:
                    out.pop("_id", None)
                rows.append(out)
        return FakeCursor(rows)

    async def insert_one(self, doc):
        if self.fail_insert:
            raise RuntimeError("simulated insert failure")
        for existing in self.docs:
            if doc.get("dedupe_key") and existing.get("dedupe_key") == doc["dedupe_key"]:
                raise DuplicateKeyError("E11000 dedupe_key")
            if doc.get("id") and existing.get("id") == doc["id"]:
                raise DuplicateKeyError("E11000 id")
        self.docs.append(copy.deepcopy(doc))
        return True

    async def update_one(self, query, update, upsert=False):
        set_vals = update.get("$set", {})
        for i, d in enumerate(self.docs):
            if _match(d, query or {}):
                self.docs[i] = {**d, **copy.deepcopy(set_vals)}
                return True
        if upsert:
            self.docs.append({**copy.deepcopy(query or {}), **copy.deepcopy(set_vals)})
        return True


class FakeDb:
    def __init__(self, users=None, settings=None, notifications=None):
        self.users = FakeCollection(users)
        self.app_settings = FakeCollection(settings)
        self.notifications = FakeCollection(notifications)

    def __getitem__(self, name):
        return getattr(self, name)


def _users():
    return [
        {"id": "u-designer-1", "role": "designer", "status": "active"},
        {"id": "u-designer-2", "role": "designer", "status": "active"},
        {"id": "u-crm-1", "role": "crm", "status": "active"},
        {"id": "u-crm-inactive", "role": "crm", "status": "inactive"},
        {"id": "u-ops", "role": "operator", "status": "active"},
    ]


def _order():
    return {
        "id": "ord-notif-1",
        "po_number": "PO-NOTIF-1",
        "customer": "PT Contoh",
        "current_stage": 2,
        "qty": 1000,
    }


def _approval(**extra):
    base = {
        "id": "ca-notif-1",
        "order_id": "ord-notif-1",
        "po_number": "PO-NOTIF-1",
        "reject_reason": None,
    }
    base.update(extra)
    return base


@pytest.mark.anyio
async def test_settings_default_active_designer_crm_no_write():
    db = FakeDb()
    s = await notif.get_notification_settings(db)
    assert s["enabled"] is True
    assert s["notify_designer"] is True
    assert s["notify_crm"] is True
    assert db.app_settings.docs == []


@pytest.mark.anyio
async def test_settings_persist_reload():
    db = FakeDb()
    await notif.update_notification_settings(
        db,
        {"enabled": True, "notify_designer": False, "notify_crm": True},
        user={"id": "admin-1", "email": "a@x.com", "role": "admin", "name": "A"},
    )
    s = await notif.get_notification_settings(db)
    assert s["notify_designer"] is False
    assert s["notify_crm"] is True


@pytest.mark.anyio
async def test_approve_default_notifies_designer_and_crm():
    db = FakeDb(users=_users())
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(), decision="approved",
    )
    recipients = {c["user_id"] for c in created}
    assert recipients == {"u-designer-1", "u-designer-2", "u-crm-1"}
    assert all(c["event_type"] == notif.EVENT_APPROVED for c in created)
    assert all(c["deep_link"] == "/m/orders/ord-notif-1" for c in created)
    assert all("PO-NOTIF-1" in c["body"] for c in created)


@pytest.mark.anyio
async def test_reject_default_includes_canonical_reason():
    db = FakeDb(users=_users())
    created = await notif.emit_customer_design_approval_notifications(
        db,
        order=_order(),
        approval=_approval(reject_reason="Warna tidak sesuai"),
        decision="rejected",
    )
    assert {c["user_id"] for c in created} == {"u-designer-1", "u-designer-2", "u-crm-1"}
    assert all(c["event_type"] == notif.EVENT_REJECTED for c in created)
    assert all("Warna tidak sesuai" in c["body"] for c in created)


@pytest.mark.anyio
async def test_disabled_setting_no_notifications():
    db = FakeDb(
        users=_users(),
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": False,
            "notify_designer": True,
            "notify_crm": True,
        }],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(), decision="approved",
    )
    assert created == []
    assert db.notifications.docs == []


@pytest.mark.anyio
async def test_designer_off_crm_only():
    db = FakeDb(
        users=_users(),
        settings=[{"key": notif.SETTINGS_KEY, "enabled": True, "notify_designer": False, "notify_crm": True}],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(), decision="approved",
    )
    assert {c["user_id"] for c in created} == {"u-crm-1"}


@pytest.mark.anyio
async def test_crm_off_designer_only():
    db = FakeDb(
        users=_users(),
        settings=[{"key": notif.SETTINGS_KEY, "enabled": True, "notify_designer": True, "notify_crm": False}],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(), decision="rejected",
    )
    assert {c["user_id"] for c in created} == {"u-designer-1", "u-designer-2"}


@pytest.mark.anyio
async def test_same_user_id_across_roles_deduped():
    users = [
        {"id": "u-both", "role": "designer", "status": "active"},
        {"id": "u-both", "role": "crm", "status": "active"},
    ]
    db = FakeDb(users=users)
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-both"), decision="approved",
    )
    assert len(created) == 1
    assert created[0]["user_id"] == "u-both"


@pytest.mark.anyio
async def test_duplicate_approve_idempotent():
    db = FakeDb(users=_users()[:1])
    first = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-dup"), decision="approved",
    )
    second = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-dup"), decision="approved",
    )
    assert len(first) == 1 and len(second) == 1
    assert first[0]["id"] == second[0]["id"]
    assert len(db.notifications.docs) == 1


@pytest.mark.anyio
async def test_duplicate_reject_idempotent():
    db = FakeDb(users=_users()[:1])
    await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-dup-r", reject_reason="x"), decision="rejected",
    )
    await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-dup-r", reject_reason="x"), decision="rejected",
    )
    assert len(db.notifications.docs) == 1


@pytest.mark.anyio
async def test_notification_write_failure_swallowed():
    db = FakeDb(users=_users()[:1])
    db.notifications.fail_insert = True
    created = await notif.emit_customer_design_approval_notifications(
        db,
        order=_order(),
        approval=_approval(id="ca-fail"),
        decision="approved",
        logger=logging.getLogger("test.notif"),
    )
    assert created == []


@pytest.mark.anyio
async def test_list_scoped_and_get_readonly():
    db = FakeDb(users=_users())
    await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-list"), decision="approved",
    )
    mine = await notif.list_notifications_for_user(db, "u-designer-1")
    assert mine and all(n["is_read"] is False for n in mine)
    assert all(d.get("read_at") is None for d in db.notifications.docs if d["user_id"] == "u-designer-1")
    other_ids = {n["id"] for n in await notif.list_notifications_for_user(db, "u-crm-1")}
    assert {n["id"] for n in mine}.isdisjoint(other_ids)


@pytest.mark.anyio
async def test_mark_read_own_only():
    db = FakeDb(users=_users()[:1])
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-read"), decision="approved",
    )
    nid = created[0]["id"]
    ok = await notif.mark_notification_read(db, "u-designer-1", nid)
    assert ok["is_read"] is True
    assert await notif.mark_notification_read(db, "u-crm-1", nid) is None


@pytest.mark.anyio
async def test_emit_does_not_mutate_order_workflow_fields():
    db = FakeDb(users=_users()[:1])
    order = _order()
    before = copy.deepcopy(order)
    await notif.emit_customer_design_approval_notifications(
        db, order=order, approval=_approval(), decision="approved",
    )
    assert order == before


def test_customer_approval_hook_imports_emit_after_persist():
    import inspect
    import customer_approval as ca

    src = inspect.getsource(ca)
    assert "emit_customer_design_approval_notifications" in src
    assert "await persist_order(order)" in src
    assert src.index("await persist_order(order)") < src.index("emit_customer_design_approval_notifications")
