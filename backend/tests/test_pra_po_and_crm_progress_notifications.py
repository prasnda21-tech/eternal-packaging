"""Focused: Pra PO lifecycle + CRM creator-only progress notifications."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

from services import notifications as notif
from services import pra_po


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

    async def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _match(d, query or {}):
                out = copy.deepcopy(d)
                if projection and projection.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    async def insert_one(self, doc):
        from pymongo.errors import DuplicateKeyError
        for existing in self.docs:
            if doc.get("id") and existing.get("id") == doc.get("id"):
                raise DuplicateKeyError("id")
            if doc.get("dedupe_key") and existing.get("dedupe_key") == doc.get("dedupe_key"):
                raise DuplicateKeyError("dedupe_key")
            # Pra PO business number uniqueness only (orders use po_number separately).
            if doc.get("number") and existing.get("number") == doc.get("number") and "status" in doc:
                raise DuplicateKeyError("number")
            if (
                doc.get("po_number")
                and existing.get("po_number") == doc.get("po_number")
                and doc.get("import_source") == "pra_po"
            ):
                raise DuplicateKeyError("po_number")
            # Sparse unique orders.pra_po_id (mirrors ensure_indexes). History rows
            # also carry pra_po_id but are not unique on that field.
            if (
                doc.get("pra_po_id")
                and doc.get("import_source") == "pra_po"
                and existing.get("pra_po_id") == doc.get("pra_po_id")
                and existing.get("import_source") == "pra_po"
            ):
                raise DuplicateKeyError("pra_po_id")
        self.docs.append(copy.deepcopy(doc))
        return None

    async def replace_one(self, query, doc):
        for i, d in enumerate(self.docs):
            if _match(d, query or {}):
                self.docs[i] = copy.deepcopy(doc)
                return
        self.docs.append(copy.deepcopy(doc))

    async def update_one(self, query, update, upsert=False):
        for i, d in enumerate(self.docs):
            if _match(d, query or {}):
                if "$set" in update:
                    d.update(update["$set"])
                return
        if upsert:
            row = dict(query or {})
            row.update((update or {}).get("$set") or {})
            self.docs.append(row)

    async def find_one_and_update(self, query, update, return_document=None, projection=None):
        for i, d in enumerate(self.docs):
            if _match(d, query or {}):
                if "$set" in update:
                    d.update(update["$set"])
                out = copy.deepcopy(d)
                if projection and projection.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    async def delete_one(self, query):
        self.docs = [d for d in self.docs if not _match(d, query or {})]

    async def count_documents(self, query=None):
        return sum(1 for d in self.docs if _match(d, query or {}))

    def aggregate(self, pipeline):
        # Minimal unread aggregation support used by unread_counts_for_user.
        match = pipeline[0].get("$match") if pipeline else {}
        rows = [copy.deepcopy(d) for d in self.docs if _match(d, match or {})]
        return FakeCursor(rows)

    async def create_index(self, *args, **kwargs):
        return None


def _match(doc: dict, query: dict) -> bool:
    if not query:
        return True
    if "$and" in query:
        return all(_match(doc, part) for part in query["$and"])
    if "$or" in query:
        return any(_match(doc, part) for part in query["$or"])
    for k, v in query.items():
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
            elif "$exists" in v:
                exists = k in doc
                if bool(v["$exists"]) != exists:
                    return False
            elif "$lt" in v:
                if not (actual is not None and actual < v["$lt"]):
                    return False
            else:
                if actual != v:
                    return False
        elif actual != v:
            return False
    return True


class FakeDb:
    def __init__(self):
        self.pra_pos = FakeCollection()
        self.pra_po_history = FakeCollection()
        self.notifications = FakeCollection()
        self.app_settings = FakeCollection()
        self.users = FakeCollection([
            {"id": "crm-a", "role": "crm", "status": "active", "name": "CRM A"},
            {"id": "crm-b", "role": "crm", "status": "active", "name": "CRM B"},
            {"id": "admin-1", "role": "admin", "status": "active", "name": "Admin"},
        ])
        self.orders = FakeCollection()
        self.sales_invoices = FakeCollection()

    def __getitem__(self, name):
        return getattr(self, name)


CRM_A = {"id": "crm-a", "role": "crm", "name": "CRM A", "email": "a@test.local"}
CRM_B = {"id": "crm-b", "role": "crm", "name": "CRM B", "email": "b@test.local"}
ADMIN = {"id": "admin-1", "role": "admin", "name": "Admin", "email": "admin@test.local"}

CRM_PERMS = {
    pra_po.PERM_CREATE, pra_po.PERM_EDIT_OWN, pra_po.PERM_VIEW_OWN,
    pra_po.PERM_SUBMIT, pra_po.PERM_AUDIT,
}
ADMIN_PERMS = {
    pra_po.PERM_VIEW_ALL, pra_po.PERM_VERIFY, pra_po.PERM_RETURN, pra_po.PERM_AUDIT,
}


@pytest.mark.anyio
async def test_crm_a_does_not_receive_crm_b_progress():
    db = FakeDb()
    order_b = {"id": "ord-b", "po_number": "PO/B", "created_by_user_id": "crm-b", "customer": "Cust B"}
    await notif.emit_sales_order_verified_to_creator(db, order=order_b)
    rows = db.notifications.docs
    assert len(rows) == 1
    assert rows[0]["user_id"] == "crm-b"
    assert rows[0]["event_type"] == notif.EVENT_SALES_ORDER_VERIFIED
    assert all(r["user_id"] != "crm-a" for r in rows)


@pytest.mark.anyio
async def test_pra_po_draft_not_in_orders_collection():
    db = FakeDb()
    doc = await pra_po.create_draft(
        db,
        {"customer": "Toko A", "line_items": [{"name": "Box", "quantity": 10, "master_item_id": "m1"}]},
        CRM_A,
    )
    assert doc["status"] == pra_po.STATUS_DRAFT
    assert doc["created_by_user_id"] == "crm-a"
    assert await db.orders.count_documents({}) == 0
    assert await db.pra_pos.count_documents({}) == 1


@pytest.mark.anyio
async def test_return_requires_reason_and_crm_cannot_verify():
    db = FakeDb()
    created = await pra_po.create_draft(
        db,
        {"customer": "Toko A", "line_items": [{"name": "Box", "quantity": 2, "master_item_id": "m1"}]},
        CRM_A,
    )
    submitted = await pra_po.submit_pra_po(db, created["id"], CRM_A, CRM_PERMS)
    assert submitted["status"] == pra_po.STATUS_SUBMITTED

    with pytest.raises(HTTPException) as no_reason:
        await pra_po.return_pra_po(db, created["id"], {"reason": "  "}, ADMIN, ADMIN_PERMS)
    assert no_reason.value.status_code == 400

    with pytest.raises(HTTPException) as crm_verify:
        await pra_po.verify_pra_po(db, created["id"], CRM_A, CRM_PERMS)
    assert crm_verify.value.status_code == 403

    returned = await pra_po.return_pra_po(
        db, created["id"], {"reason": "Harga belum lengkap"}, ADMIN, ADMIN_PERMS,
    )
    assert returned["status"] == pra_po.STATUS_RETURNED
    assert returned["return_reason"] == "Harga belum lengkap"
    # Action-required notify to CRM A only
    assert any(
        r["user_id"] == "crm-a" and r["event_type"] == notif.EVENT_PRA_PO_RETURNED
        for r in db.notifications.docs
    )


@pytest.mark.anyio
async def test_verify_retry_does_not_duplicate_sales_order(monkeypatch):
    db = FakeDb()
    created = await pra_po.create_draft(
        db,
        {"customer": "Toko A", "line_items": [{"name": "Box", "quantity": 2, "master_item_id": "m1"}]},
        CRM_A,
    )
    await pra_po.submit_pra_po(db, created["id"], CRM_A, CRM_PERMS)

    calls = {"n": 0}

    async def fake_materialize(db_, pra, user):
        calls["n"] += 1
        order = {
            "id": f"ord-{calls['n']}",
            "po_number": f"PO/TEST/{calls['n']}",
            "created_by_user_id": pra["created_by_user_id"],
            "customer": pra["customer"],
            "pra_po_id": pra["id"],
        }
        await db_.orders.insert_one(dict(order))
        return order

    monkeypatch.setattr(pra_po, "_materialize_sales_order", fake_materialize)

    first = await pra_po.verify_pra_po(db, created["id"], ADMIN, ADMIN_PERMS)
    assert first["idempotent"] is False
    assert first["order"]["id"] == "ord-1"
    second = await pra_po.verify_pra_po(db, created["id"], ADMIN, ADMIN_PERMS)
    assert second["idempotent"] is True
    assert second["order"]["id"] == "ord-1"
    assert await db.orders.count_documents({}) == 1
    assert calls["n"] == 1


@pytest.mark.anyio
async def test_invoice_and_payment_and_sj_progress_events_are_distinct():
    db = FakeDb()
    order = {
        "id": "ord-1",
        "po_number": "PO/1",
        "created_by_user_id": "crm-a",
        "customer": "Cust",
        "payment_approved_at": "2026-10-01T00:00:00Z",
        "payment_bypassed_at": "2026-10-02T00:00:00Z",
    }
    inv = {"id": "inv-1", "invoice_number": "INV-1"}
    await notif.emit_sales_invoice_created_to_creator(db, order=order, invoice=inv)
    await notif.emit_payment_approved_to_creator(db, order=order)
    await notif.emit_payment_bypassed_to_creator(db, order=order)
    shipment = {"id": "sj-1", "number": "SJ-1"}
    await notif.emit_crm_surat_jalan_created_to_creator(db, order=order, shipment=shipment)
    # second SJ
    await notif.emit_crm_surat_jalan_created_to_creator(
        db, order=order, shipment={"id": "sj-2", "number": "SJ-2"},
    )
    types = {r["event_type"] for r in db.notifications.docs}
    assert notif.EVENT_SALES_INVOICE_CREATED in types
    assert notif.EVENT_PAYMENT_APPROVED in types
    assert notif.EVENT_PAYMENT_BYPASSED in types
    assert notif.EVENT_CRM_SURAT_JALAN_CREATED in types
    sj_rows = [r for r in db.notifications.docs if r["event_type"] == notif.EVENT_CRM_SURAT_JALAN_CREATED]
    assert {r["approval_id"] for r in sj_rows} == {"sj-1", "sj-2"}
    assert all(r["user_id"] == "crm-a" for r in db.notifications.docs)


@pytest.mark.anyio
async def test_cda_settings_round_trip_preserved():
    db = FakeDb()
    saved = await notif.update_notification_settings(
        db,
        {
            "enabled": True,
            "notify_designer": False,
            "notify_crm": True,
            "additional_recipient_user_ids": ["crm-a"],
        },
        user=ADMIN,
    )
    assert saved["notify_designer"] is False
    assert saved["notify_crm"] is True
    assert saved["additional_recipient_user_ids"] == ["crm-a"]
    loaded = await notif.get_notification_settings(db)
    assert loaded["notify_designer"] is False
    assert loaded["additional_recipient_user_ids"] == ["crm-a"]


@pytest.mark.anyio
async def test_unread_counts_by_module():
    db = FakeDb()
    await notif.create_notification(
        db, user_id="crm-a", event_type=notif.EVENT_SALES_ORDER_VERIFIED,
        approval_id="o1", order_id="o1", po_number="PO1", decision="verified",
        title="t", body="b", deep_link="/po/o1",
    )
    await notif.create_notification(
        db, user_id="crm-a", event_type=notif.EVENT_PRA_PO_RETURNED,
        approval_id="p1", order_id="p1", po_number="PRA1", decision="returned",
        title="t", body="b", deep_link="/penjualan/pra-po/p1",
    )
    counts = await notif.unread_counts_for_user(db, "crm-a")
    assert counts["total"] == 2
    assert counts["by_module"].get(notif.MODULE_SALES_ORDER) == 1
    assert counts["by_module"].get(notif.MODULE_PRA_PO) == 1


@pytest.mark.anyio
async def test_concurrent_verify_creates_single_sales_order(monkeypatch):
    import asyncio

    db = FakeDb()
    created = await pra_po.create_draft(
        db,
        {"customer": "Toko A", "line_items": [{"name": "Box", "quantity": 2, "master_item_id": "m1"}]},
        CRM_A,
    )
    await pra_po.submit_pra_po(db, created["id"], CRM_A, CRM_PERMS)

    calls = {"n": 0}
    gate = asyncio.Event()
    entered = {"n": 0}

    async def fake_materialize(db_, pra, user):
        calls["n"] += 1
        entered["n"] += 1
        if entered["n"] == 1:
            gate.set()
        await asyncio.sleep(0.01)
        order = {
            "id": f"ord-{calls['n']}",
            "po_number": f"PO/CONC/{calls['n']}",
            "created_by_user_id": pra["created_by_user_id"],
            "customer": pra["customer"],
            "pra_po_id": pra["id"],
        }
        await db_.orders.insert_one(dict(order))
        return order

    monkeypatch.setattr(pra_po, "_materialize_sales_order", fake_materialize)

    results = await asyncio.gather(
        pra_po.verify_pra_po(db, created["id"], ADMIN, ADMIN_PERMS),
        pra_po.verify_pra_po(db, created["id"], ADMIN, ADMIN_PERMS),
        return_exceptions=True,
    )
    oks = [r for r in results if isinstance(r, dict)]
    assert len(oks) >= 1
    assert await db.orders.count_documents({}) == 1
    pra = await db.pra_pos.find_one({"id": created["id"]})
    assert pra["status"] == pra_po.STATUS_VERIFIED
    assert pra["sales_order_id"] == oks[0]["order"]["id"]
    assert all(r["order"]["id"] == pra["sales_order_id"] for r in oks)


@pytest.mark.anyio
async def test_verify_recovers_when_so_exists_but_pra_not_finalized(monkeypatch):
    db = FakeDb()
    created = await pra_po.create_draft(
        db,
        {"customer": "Toko A", "line_items": [{"name": "Box", "quantity": 2, "master_item_id": "m1"}]},
        CRM_A,
    )
    await pra_po.submit_pra_po(db, created["id"], CRM_A, CRM_PERMS)
    # Simulate partial success: SO inserted, Pra PO still submitted / verifying.
    orphan_so = {
        "id": "ord-partial",
        "po_number": "PO/PARTIAL/1",
        "pra_po_id": created["id"],
        "created_by_user_id": "crm-a",
        "customer": "Toko A",
        "import_source": "pra_po",
    }
    await db.orders.insert_one(dict(orphan_so))
    await db.pra_pos.update_one(
        {"id": created["id"]},
        {"$set": {"status": pra_po.STATUS_VERIFYING, "sales_order_id": None}},
    )

    async def boom(*_a, **_k):
        raise AssertionError("must not rematerialize")

    monkeypatch.setattr(pra_po, "_materialize_sales_order", boom)
    recovered = await pra_po.verify_pra_po(db, created["id"], ADMIN, ADMIN_PERMS)
    assert recovered["idempotent"] is True
    assert recovered["order"]["id"] == "ord-partial"
    pra = await db.pra_pos.find_one({"id": created["id"]})
    assert pra["status"] == pra_po.STATUS_VERIFIED
    assert pra["sales_order_id"] == "ord-partial"
    assert await db.orders.count_documents({}) == 1


@pytest.mark.anyio
async def test_invalid_master_item_does_not_verify_or_create_so(monkeypatch):
    db = FakeDb()
    created = await pra_po.create_draft(
        db,
        {"customer": "Toko A", "line_items": [{"name": "Box", "quantity": 2, "master_item_id": "bad-id"}]},
        CRM_A,
    )
    await pra_po.submit_pra_po(db, created["id"], CRM_A, CRM_PERMS)

    async def bad_master(*_a, **_k):
        raise HTTPException(
            400,
            "Line item #1: Produk target tidak ditemukan di Barang/Jasa.",
        )

    monkeypatch.setattr(pra_po, "_materialize_sales_order", bad_master)
    with pytest.raises(HTTPException) as exc:
        await pra_po.verify_pra_po(db, created["id"], ADMIN, ADMIN_PERMS)
    assert exc.value.status_code == 400
    assert "tidak ditemukan" in str(exc.value.detail).lower() or "Master" in str(exc.value.detail)
    pra = await db.pra_pos.find_one({"id": created["id"]})
    assert pra["status"] == pra_po.STATUS_SUBMITTED
    assert pra.get("sales_order_id") in (None, "")
    assert await db.orders.count_documents({}) == 0


@pytest.mark.anyio
async def test_crm_direct_sales_order_create_guard():
    from services.sales_order_create_guard import reject_crm_direct_sales_order_create

    with pytest.raises(HTTPException) as exc:
        reject_crm_direct_sales_order_create({"id": "crm-a", "role": "CRM"})
    assert exc.value.status_code == 403
    assert "Pra PO" in str(exc.value.detail)

    # Non-CRM roles pass through (permission checks happen elsewhere).
    reject_crm_direct_sales_order_create({"id": "admin-1", "role": "admin"})
    reject_crm_direct_sales_order_create({"id": "ops", "role": "operator"})


@pytest.mark.anyio
async def test_crm_progress_settings_do_not_wipe_cda_recipients():
    db = FakeDb()
    await notif.update_notification_settings(
        db,
        {
            "enabled": True,
            "notify_designer": True,
            "notify_crm": True,
            "additional_recipient_user_ids": ["crm-a"],
        },
        user=ADMIN,
    )
    events = {k: True for k in notif.CRM_PROGRESS_EVENT_DEFAULTS}
    events[notif.EVENT_CRM_CUSTOMER_DESIGN_APPROVED] = False
    await notif.update_crm_progress_settings(db, {"events": events}, user=ADMIN)
    cda = await notif.get_notification_settings(db)
    assert cda["additional_recipient_user_ids"] == ["crm-a"]
    assert cda["notify_designer"] is True
    progress = await notif.get_crm_progress_settings(db)
    assert progress["events"][notif.EVENT_CRM_CUSTOMER_DESIGN_APPROVED] is False
