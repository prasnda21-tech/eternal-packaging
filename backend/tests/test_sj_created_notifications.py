"""Surat Jalan created → Helper / Driver / Kepala Produksi notifications."""
from __future__ import annotations

import copy
import inspect

import pytest
from pymongo.errors import DuplicateKeyError

from services import notifications as notif
from services.sales_deliveries import create_sales_delivery


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
        return False


class FakeDb:
    def __init__(self, users=None, notifications=None):
        self.users = FakeCollection(users)
        self.notifications = FakeCollection(notifications)

    def __getitem__(self, name):
        return getattr(self, name)


def _users():
    return [
        {"id": "u-helper-1", "role": "helper", "status": "active", "branch_id": "br-a"},
        {"id": "u-helper-inactive", "role": "helper", "status": "inactive", "branch_id": "br-a"},
        {"id": "u-driver-1", "role": "driver", "status": "active", "branch_id": "br-a"},
        {"id": "u-driver-other", "role": "driver", "status": "active", "branch_id": "br-b"},
        {"id": "u-kp-1", "role": "head_production", "status": "active", "branch_id": "br-a"},
        {"id": "u-crm-1", "role": "crm", "status": "active", "branch_id": "br-a"},
        {"id": "u-designer-1", "role": "designer", "status": "active"},
    ]


def _order(**extra):
    base = {
        "id": "ord-sj-1",
        "po_number": "PO-SJ-1",
        "customer_name": "PT Kirim",
        "branch_id": "br-a",
        "qty_packed": 5000,
        "current_stage": 11,
    }
    base.update(extra)
    return base


def _shipment(**extra):
    base = {
        "id": "sh-sj-1",
        "number": "SJ-001",
        "sales_delivery_id": "sh-sj-1",
    }
    base.update(extra)
    return base


@pytest.mark.anyio
async def test_valid_sj_notifies_helper_driver_and_kepala_produksi():
    db = FakeDb(users=_users())
    created = await notif.emit_surat_jalan_created_notifications(
        db, order=_order(), shipment=_shipment()
    )
    roles = sorted({c["decision"] for c in created})
    recipients = {c["user_id"] for c in created}
    assert roles == ["driver", "head_production", "helper"]
    assert recipients == {"u-helper-1", "u-driver-1", "u-kp-1"}
    assert "u-crm-1" not in recipients
    assert "u-designer-1" not in recipients
    assert "u-helper-inactive" not in recipients
    assert "u-driver-other" not in recipients
    assert all(c["event_type"] == notif.EVENT_SURAT_JALAN_CREATED for c in created)
    assert all("/m/orders/ord-sj-1" == c["deep_link"] for c in created)


@pytest.mark.anyio
async def test_missing_sj_id_or_number_does_not_notify():
    db = FakeDb(users=_users())
    assert await notif.emit_surat_jalan_created_notifications(
        db, order=_order(), shipment={"id": "", "number": "SJ-001"}
    ) == []
    assert await notif.emit_surat_jalan_created_notifications(
        db, order=_order(), shipment={"id": "sh-x", "number": ""}
    ) == []
    assert await notif.emit_surat_jalan_created_notifications(
        db, order=_order(), shipment={"id": None, "sj_number": "SJ-OLD"}
    ) == []
    assert db.notifications.docs == []


@pytest.mark.anyio
async def test_packing_or_production_without_sj_does_not_notify():
    db = FakeDb(users=_users())
    created = await notif.emit_surat_jalan_created_notifications(
        db,
        order=_order(qty_packed=9000, current_stage=11, print_qty_total=9000),
        shipment={"id": None, "number": ""},
    )
    assert created == []
    assert db.notifications.docs == []


@pytest.mark.anyio
async def test_assigned_driver_is_preferred_over_all_drivers():
    db = FakeDb(users=_users() + [
        {"id": "u-assigned-driver", "role": "driver", "status": "active", "branch_id": "br-a"},
    ])
    created = await notif.emit_surat_jalan_created_notifications(
        db,
        order=_order(loading_checklist={"driver_user_id": "u-assigned-driver"}),
        shipment=_shipment(),
    )
    driver_ids = {c["user_id"] for c in created if c["decision"] == "driver"}
    assert driver_ids == {"u-assigned-driver"}
    assert "u-driver-1" not in driver_ids


@pytest.mark.anyio
async def test_emit_is_idempotent_per_sj_and_user():
    db = FakeDb(users=_users())
    first = await notif.emit_surat_jalan_created_notifications(db, order=_order(), shipment=_shipment())
    second = await notif.emit_surat_jalan_created_notifications(db, order=_order(), shipment=_shipment())
    assert len(first) == 3
    assert len(second) == 3
    assert len(db.notifications.docs) == 3


@pytest.mark.anyio
async def test_write_failure_is_swallowed():
    db = FakeDb(users=_users())
    db.notifications.fail_insert = True
    created = await notif.emit_surat_jalan_created_notifications(db, order=_order(), shipment=_shipment())
    assert created == []


def test_create_sales_delivery_emits_after_shipment_persist():
    src = inspect.getsource(create_sales_delivery)
    assert "emit_surat_jalan_created_notifications" in src
    assert src.index('"shipments": shipment_entry') < src.index("emit_surat_jalan_created_notifications")
    assert src.index("emit_surat_jalan_created_notifications") < src.index("return delivery_doc")


def test_notification_service_has_no_schedule_table_status_helpers():
    src = inspect.getsource(notif)
    assert "idle_sj_notification_status" not in src
    assert "sj_created_notification_status_map" not in src
    assert "attach_sj_created_notification_status" not in src
    assert "sj_notifications" not in src


@pytest.mark.anyio
async def test_multiple_item_lines_do_not_duplicate_recipient_notifications():
    db = FakeDb(users=_users())
    shipment = _shipment(lines=[
        {"line_idx": 0, "name": "Cup", "qty": 1000},
        {"line_idx": 1, "name": "Lid", "qty": 1000},
        {"line_idx": 2, "name": "Gelas", "qty": 1000},
    ])
    created = await notif.emit_surat_jalan_created_notifications(
        db, order=_order(), shipment=shipment
    )
    helper_ids = [c["user_id"] for c in created if c["decision"] == "helper"]
    assert helper_ids == ["u-helper-1"]
    assert len(db.notifications.docs) == 3


@pytest.mark.anyio
async def test_two_distinct_sj_on_same_po_create_independent_events():
    db = FakeDb(users=_users())
    first = await notif.emit_surat_jalan_created_notifications(
        db, order=_order(), shipment=_shipment(id="sh-a", number="SJ-A", sales_delivery_id="sh-a")
    )
    second = await notif.emit_surat_jalan_created_notifications(
        db, order=_order(), shipment=_shipment(id="sh-b", number="SJ-B", sales_delivery_id="sh-b")
    )
    assert len(first) == 3
    assert len(second) == 3
    assert {c["approval_id"] for c in first} == {"sh-a"}
    assert {c["approval_id"] for c in second} == {"sh-b"}
    helper_keys = [
        d["dedupe_key"] for d in db.notifications.docs if d["decision"] == "helper"
    ]
    assert sorted(helper_keys) == [
        "surat_jalan_created:sh-a:u-helper-1",
        "surat_jalan_created:sh-b:u-helper-1",
    ]
