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
    assert s["additional_recipient_user_ids"] == []
    assert db.app_settings.docs == []


@pytest.mark.anyio
async def test_settings_persist_reload():
    db = FakeDb(users=_users())
    await notif.update_notification_settings(
        db,
        {
            "enabled": True,
            "notify_designer": False,
            "notify_crm": True,
            "additional_recipient_user_ids": ["u-ops", "u-ops", "u-missing"],
        },
        user={"id": "admin-1", "email": "a@x.com", "role": "admin", "name": "A"},
    )
    s = await notif.get_notification_settings(db)
    assert s["notify_designer"] is False
    assert s["notify_crm"] is True
    assert s["additional_recipient_user_ids"] == ["u-ops"]


@pytest.mark.anyio
async def test_settings_backward_compat_missing_additional_field():
    db = FakeDb(settings=[{
        "key": notif.SETTINGS_KEY,
        "enabled": True,
        "notify_designer": True,
        "notify_crm": True,
    }])
    s = await notif.get_notification_settings(db)
    assert s["additional_recipient_user_ids"] == []
    assert db.app_settings.docs[0].get("additional_recipient_user_ids") is None


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
            "additional_recipient_user_ids": ["u-ops"],
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
async def test_explicit_non_role_user_receives():
    db = FakeDb(
        users=_users(),
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": True,
            "notify_designer": True,
            "notify_crm": True,
            "additional_recipient_user_ids": ["u-ops"],
        }],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-explicit"), decision="approved",
    )
    assert "u-ops" in {c["user_id"] for c in created}
    assert {c["user_id"] for c in created} == {"u-designer-1", "u-designer-2", "u-crm-1", "u-ops"}


@pytest.mark.anyio
async def test_role_plus_explicit_designer_deduped():
    db = FakeDb(
        users=_users(),
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": True,
            "notify_designer": True,
            "notify_crm": False,
            "additional_recipient_user_ids": ["u-designer-1"],
        }],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-dup-des"), decision="approved",
    )
    recipients = [c["user_id"] for c in created]
    assert recipients.count("u-designer-1") == 1
    assert set(recipients) == {"u-designer-1", "u-designer-2"}


@pytest.mark.anyio
async def test_role_plus_explicit_crm_deduped():
    db = FakeDb(
        users=_users(),
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": True,
            "notify_designer": False,
            "notify_crm": True,
            "additional_recipient_user_ids": ["u-crm-1"],
        }],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-dup-crm"), decision="approved",
    )
    recipients = [c["user_id"] for c in created]
    assert recipients.count("u-crm-1") == 1
    assert set(recipients) == {"u-crm-1"}


@pytest.mark.anyio
async def test_multiple_explicit_members_each_once():
    users = _users() + [
        {"id": "u-a", "role": "helper", "status": "active"},
        {"id": "u-b", "role": "helper", "status": "active"},
        {"id": "u-c", "role": "helper", "status": "active"},
    ]
    db = FakeDb(
        users=users,
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": True,
            "notify_designer": False,
            "notify_crm": False,
            "additional_recipient_user_ids": ["u-a", "u-b", "u-c"],
        }],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-multi"), decision="approved",
    )
    assert sorted(c["user_id"] for c in created) == ["u-a", "u-b", "u-c"]


@pytest.mark.anyio
async def test_inactive_explicit_member_skipped():
    db = FakeDb(
        users=_users(),
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": True,
            "notify_designer": False,
            "notify_crm": False,
            "additional_recipient_user_ids": ["u-crm-inactive"],
        }],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-inactive"), decision="approved",
    )
    assert created == []


@pytest.mark.anyio
async def test_designer_off_but_explicit_designer_receives():
    db = FakeDb(
        users=_users(),
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": True,
            "notify_designer": False,
            "notify_crm": False,
            "additional_recipient_user_ids": ["u-designer-1"],
        }],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-des-explicit"), decision="approved",
    )
    assert {c["user_id"] for c in created} == {"u-designer-1"}


@pytest.mark.anyio
async def test_crm_off_but_explicit_crm_receives():
    db = FakeDb(
        users=_users(),
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": True,
            "notify_designer": False,
            "notify_crm": False,
            "additional_recipient_user_ids": ["u-crm-1"],
        }],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-crm-explicit"), decision="rejected",
    )
    assert {c["user_id"] for c in created} == {"u-crm-1"}


@pytest.mark.anyio
async def test_both_roles_off_explicit_only():
    db = FakeDb(
        users=_users(),
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": True,
            "notify_designer": False,
            "notify_crm": False,
            "additional_recipient_user_ids": ["u-ops"],
        }],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-roles-off"), decision="approved",
    )
    assert {c["user_id"] for c in created} == {"u-ops"}


@pytest.mark.anyio
async def test_duplicate_ids_in_config_one_recipient():
    db = FakeDb(
        users=_users(),
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": True,
            "notify_designer": False,
            "notify_crm": False,
            "additional_recipient_user_ids": ["u-ops", "u-ops", "u-ops"],
        }],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-dup-cfg"), decision="approved",
    )
    assert [c["user_id"] for c in created] == ["u-ops"]


@pytest.mark.anyio
async def test_nonexistent_explicit_id_no_notification():
    db = FakeDb(
        users=_users(),
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": True,
            "notify_designer": False,
            "notify_crm": False,
            "additional_recipient_user_ids": ["u-does-not-exist"],
        }],
    )
    created = await notif.emit_customer_design_approval_notifications(
        db, order=_order(), approval=_approval(id="ca-ghost"), decision="approved",
    )
    assert created == []


@pytest.mark.anyio
async def test_update_rejects_user_objects_as_ids():
    db = FakeDb(users=_users())
    with pytest.raises(ValueError, match="canonical user id"):
        await notif.update_notification_settings(
            db,
            {
                "enabled": True,
                "notify_designer": True,
                "notify_crm": True,
                "additional_recipient_user_ids": [{"id": "u-ops", "name": "Ops"}],
            },
        )


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
    mine = (await notif.list_notifications_for_user(db, "u-designer-1"))["notifications"]
    assert mine and all(n["is_read"] is False for n in mine)
    assert all(d.get("read_at") is None for d in db.notifications.docs if d["user_id"] == "u-designer-1")
    other_ids = {
        n["id"]
        for n in (await notif.list_notifications_for_user(db, "u-crm-1"))["notifications"]
    }
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
    assert "emit_customer_design_decision_notifications" in src
    assert "await persist_order(order)" in src
    assert src.index("await persist_order(order)") < src.index("emit_customer_design_decision_notifications")


@pytest.mark.anyio
async def test_cda_decision_dedupes_creator_in_crm_role_blast():
    """CRM creator in notify_crm role list gets one progress row, not role-blast + progress."""
    db = FakeDb(users=_users())
    order = {**_order(), "created_by_user_id": "u-crm-1"}
    result = await notif.emit_customer_design_decision_notifications(
        db, order=order, approval=_approval(id="ca-dedupe-role"), decision="approved",
    )
    assert result["creator_excluded_from_role_blast"] is True
    assert result["progress_notification"]["user_id"] == "u-crm-1"
    assert result["progress_notification"]["event_type"] == notif.EVENT_CRM_CUSTOMER_DESIGN_APPROVED
    role_uids = [d["user_id"] for d in result["role_notifications"]]
    assert "u-crm-1" not in role_uids
    assert "u-designer-1" in role_uids and "u-designer-2" in role_uids
    creator_rows = [d for d in db.notifications.docs if d["user_id"] == "u-crm-1"]
    assert len(creator_rows) == 1
    assert creator_rows[0]["event_type"] == notif.EVENT_CRM_CUSTOMER_DESIGN_APPROVED


@pytest.mark.anyio
async def test_cda_decision_dedupes_creator_as_additional_recipient():
    db = FakeDb(
        users=_users(),
        settings=[{
            "key": notif.SETTINGS_KEY,
            "enabled": True,
            "notify_designer": True,
            "notify_crm": False,
            "additional_recipient_user_ids": ["u-crm-1"],
        }],
    )
    order = {**_order(), "created_by_user_id": "u-crm-1"}
    result = await notif.emit_customer_design_decision_notifications(
        db, order=order, approval=_approval(id="ca-dedupe-add"), decision="approved",
    )
    assert result["creator_excluded_from_role_blast"] is True
    role_uids = {d["user_id"] for d in result["role_notifications"]}
    assert "u-crm-1" not in role_uids
    assert "u-designer-1" in role_uids
    creator_rows = [d for d in db.notifications.docs if d["user_id"] == "u-crm-1"]
    assert len(creator_rows) == 1
    assert creator_rows[0]["event_type"] == notif.EVENT_CRM_CUSTOMER_DESIGN_APPROVED


@pytest.mark.anyio
async def test_cda_decision_other_crm_still_receives_role_blast():
    users = _users() + [{"id": "u-crm-2", "role": "crm", "status": "active"}]
    db = FakeDb(users=users)
    order = {**_order(), "created_by_user_id": "u-crm-1"}
    result = await notif.emit_customer_design_decision_notifications(
        db, order=order, approval=_approval(id="ca-other-crm"), decision="approved",
    )
    role_uids = {d["user_id"] for d in result["role_notifications"]}
    assert "u-crm-2" in role_uids
    assert "u-crm-1" not in role_uids
    assert "u-designer-1" in role_uids


@pytest.mark.anyio
async def test_cda_decision_rejected_unchanged_no_progress_exclude():
    db = FakeDb(users=_users())
    order = {**_order(), "created_by_user_id": "u-crm-1"}
    result = await notif.emit_customer_design_decision_notifications(
        db, order=order, approval=_approval(id="ca-rej"), decision="rejected",
    )
    assert result["creator_excluded_from_role_blast"] is False
    assert result["progress_notification"] is None
    role_uids = {d["user_id"] for d in result["role_notifications"]}
    assert "u-crm-1" in role_uids
    assert "u-designer-1" in role_uids
