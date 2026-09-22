"""SPK Antar Divisi — assignment, return, draft, idempotency, and module isolation."""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from test_spk import _client_for, _payload, _prepare, _user
from services.spk import EVENTS, ORDERS

MODULE_DOCS = {
    "orders": {
        "id": "marker-po",
        "module": "PO",
        "po_number": "MARKER-PO",
        "current_stage": "design",
        "status": "open",
        "design_status": "approved",
        "design_files": [{"id": "design-1", "name": "art.png", "status": "approved"}],
        "schedule_status": "planned",
        "schedule_date": "2026-09-20",
        "production_status": "queued",
        "qc_status": "pending",
        "packing_status": "waiting",
        "surat_jalan_number": "SJ-MARKER",
        "surat_jalan_status": "draft",
        "delivery_status": "planned",
    },
    "purchase_orders": {"id": "marker-purchase-order", "module": "PO", "status": "draft", "number": "PO-MARKER"},
    "purchase_payments": {"id": "marker-payment", "module": "Payment", "status": "unpaid", "amount": "150000"},
    "delivery_schedules": {"id": "marker-schedule", "module": "Penjadwalan", "status": "planned", "date": "2026-09-20"},
    "production_jobs": {"id": "marker-production", "module": "Produksi", "status": "queued"},
    "qc_inspections": {"id": "marker-qc", "module": "QC", "status": "pending"},
    "item_stock_manual": {"id": "marker-stock", "module": "Stok", "qty": 7, "status": "ready"},
    "packing_batches": {"id": "marker-packing", "module": "Packing", "status": "waiting"},
    "sales_deliveries": {"id": "marker-surat-jalan", "module": "Surat Jalan", "status": "draft", "number": "SJ-MARKER"},
    "delivery_batches": {"id": "marker-delivery", "module": "Pengiriman", "status": "planned"},
    "sales_invoices": {"id": "marker-invoice", "module": "Payment", "status": "open"},
    "sales_receipts": {"id": "marker-receipt", "module": "Payment", "status": "posted"},
}


def _simple(pic_id, **extra):
    body = _payload(pic_id, **extra)
    body["milestones"] = [{
        "title": "Tugas umum",
        "type": "QUANTITY",
        "weight": "100",
        "target_value": "10",
        "unit": "unit",
        "requires_evidence": False,
    }]
    body["idempotency_key"] = extra.get("idempotency_key") or f"ops-{uuid.uuid4().hex}"
    return body


async def _counts(db):
    return {name: await db[name].count_documents({}) for name in MODULE_DOCS}


async def _snapshots(db):
    out = {}
    for name, doc in MODULE_DOCS.items():
        stored = await db[name].find_one({"id": doc["id"]}, {"_id": 0})
        out[name] = stored
    return out


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    from services import spk as spk_service
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    spk_service._today_override = None
    spk_service._progress_crash_after = None
    yield
    spk_service._today_override = None
    spk_service._progress_crash_after = None
    server.client.close()


@pytest.mark.anyio
async def test_only_assigned_pic_can_change_progress_including_director():
    director = _user("direktur_utama", name="Dimas", override={
        "spk.view_all": True, "spk.create": True, "spk.verify": True, "spk.cancel": True, "spk.comment": True,
    })
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True, "spk.comment": True})
    other = _user("operator", name="Operator Lain", override={"spk.view_own": True, "spk.update_progress": True})
    head = _user("head_production", name="Kepala", override={
        "spk.view_division": True, "spk.verify": True, "spk.comment": True, "spk.update_progress": True,
    })
    await _prepare([director, pic, other, head])
    async with _client_for(director) as client:
        own = await client.post("/api/spk", json=_simple(director["id"], publish=True, target_division="direksi", title="Tugas direktur"))
        assert own.status_code == 200, own.text
        owned = own.json()["order"]
        other_order = await client.post("/api/spk", json=_simple(pic["id"], publish=True, target_division="kepala_produksi", title="Tugas PIC"))
        assert other_order.status_code == 200, other_order.text
        assigned = other_order.json()["order"]
    async with _client_for(director) as client:
        ack = await client.post(f"/api/spk/{owned['id']}/acknowledge", json={"doc_version": owned["doc_version"]})
        assert ack.status_code == 200, ack.text
        started = await client.post(f"/api/spk/{owned['id']}/start", json={"doc_version": ack.json()["order"]["doc_version"]})
        assert started.status_code == 200, started.text
        current = started.json()["order"]
        milestone = current["milestones"][0]["id"]
        moved = await client.post(f"/api/spk/{owned['id']}/progress", json={
            "doc_version": current["doc_version"], "milestone_id": milestone, "new_actual": 4, "idempotency_key": "dir-progress",
        })
        assert moved.status_code == 200, moved.text
        stolen = await client.post(f"/api/spk/{assigned['id']}/progress", json={
            "doc_version": assigned["doc_version"], "milestone_id": assigned["milestones"][0]["id"], "new_actual": 1, "idempotency_key": "dir-steal",
        })
        assert stolen.status_code == 403
    async with _client_for(pic) as client:
        ack = await client.post(f"/api/spk/{assigned['id']}/acknowledge", json={"doc_version": assigned["doc_version"]})
        started = await client.post(f"/api/spk/{assigned['id']}/start", json={"doc_version": ack.json()["order"]["doc_version"]})
        assert started.status_code == 200, started.text
        current = started.json()["order"]
    async with _client_for(head) as client:
        seen = await client.get(f"/api/spk/{assigned['id']}")
        assert seen.status_code == 200
        blocked = await client.post(f"/api/spk/{assigned['id']}/progress", json={
            "doc_version": current["doc_version"],
            "milestone_id": current["milestones"][0]["id"],
            "new_actual": 9,
            "idempotency_key": "head-overwrite",
        })
        assert blocked.status_code == 403
        returned = await client.post(f"/api/spk/{assigned['id']}/return", json={"reason": "Bukan tugas saya", "doc_version": current["doc_version"]})
        assert returned.status_code == 403
    async with _client_for(other) as client:
        hidden = await client.post(f"/api/spk/{assigned['id']}/progress", json={
            "doc_version": current["doc_version"], "milestone_id": current["milestones"][0]["id"], "new_actual": 2, "idempotency_key": "other",
        })
        assert hidden.status_code == 404
    import server
    stored = await server.db[ORDERS].find_one({"id": assigned["id"]}, {"_id": 0})
    assert stored["milestones"][0]["actual_value"] == "0.00"


@pytest.mark.anyio
async def test_progress_rules_notes_over_target_and_recorded_correction():
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True, "spk.verify": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True, "spk.comment": True})
    await _prepare([director, pic])
    async with _client_for(director) as client:
        created = await client.post("/api/spk", json=_simple(pic["id"], publish=True, title="Aturan nilai"))
        assert created.status_code == 200, created.text
        order = created.json()["order"]
    async with _client_for(pic) as client:
        ack = await client.post(f"/api/spk/{order['id']}/acknowledge", json={"doc_version": order["doc_version"]})
        started = await client.post(f"/api/spk/{order['id']}/start", json={"doc_version": ack.json()["order"]["doc_version"]})
        assert started.status_code == 200, started.text
        order = started.json()["order"]
    milestone = order["milestones"][0]["id"]
    async with _client_for(pic) as client:
        negative = await client.post(f"/api/spk/{order['id']}/progress", json={
            "doc_version": order["doc_version"], "milestone_id": milestone, "new_actual": -1, "idempotency_key": "neg",
        })
        assert negative.status_code == 400
        zero = await client.post(f"/api/spk/{order['id']}/progress", json={
            "doc_version": order["doc_version"], "milestone_id": milestone, "new_actual": 0, "idempotency_key": "zero",
        })
        assert zero.status_code == 400
        assert "0" in zero.text
        noted = await client.post(f"/api/spk/{order['id']}/notes", json={
            "doc_version": order["doc_version"], "note": "Masih menyiapkan bahan", "idempotency_key": "note-1",
        })
        assert noted.status_code == 200, noted.text
        assert noted.json()["order"]["milestones"][0]["actual_value"] == "0.00"
        current = noted.json()["order"]
        above = await client.post(f"/api/spk/{order['id']}/progress", json={
            "doc_version": current["doc_version"], "milestone_id": milestone, "new_actual": 12, "idempotency_key": "above",
        })
        assert above.status_code == 200, above.text
        row = above.json()["order"]["milestones"][0]
        assert row["actual_value"] == "12.00"
        assert row["progress_percent"] == "100.00"
        assert row["exceeds_target"] is True
        assert above.json()["order"]["progress_percent"] == "100.00"
        down = await client.post(f"/api/spk/{order['id']}/progress", json={
            "doc_version": above.json()["order"]["doc_version"], "milestone_id": milestone, "new_actual": 4, "idempotency_key": "down",
        })
        assert down.status_code == 400
        bare = await client.post(f"/api/spk/{order['id']}/correct", json={
            "doc_version": above.json()["order"]["doc_version"], "milestone_id": milestone, "new_actual": 4,
        })
        assert bare.status_code == 400
        fixed = await client.post(f"/api/spk/{order['id']}/correct", json={
            "doc_version": above.json()["order"]["doc_version"],
            "milestone_id": milestone,
            "new_actual": 4,
            "reason": "Salah input awal",
        })
        assert fixed.status_code == 200, fixed.text
        assert fixed.json()["order"]["milestones"][0]["actual_value"] == "4.00"
    import server
    events = await server.db[EVENTS].find({"spk_id": order["id"]}).sort("created_at", 1).to_list(20)
    kinds = [row["event_type"] for row in events]
    assert "NOTE" in kinds
    assert "PROGRESS_UPDATE" in kinds
    assert "CORRECTION" in kinds
    correction = next(row for row in events if row["event_type"] == "CORRECTION")
    assert correction["previous_actual"] == "12.00"
    assert correction["new_actual"] == "4.00"
    assert correction["reason"] == "Salah input awal"
    assert any(row["event_type"] == "PROGRESS_UPDATE" and row["new_actual"] == "12.00" for row in events)


@pytest.mark.anyio
async def test_draft_roundtrip_keeps_identity_and_blocks_strangers():
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True})
    other = _user("direktur_utama", uid="director-2", name="Direktur Lain", override={"spk.view_all": True, "spk.create": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, other, pic])
    async with _client_for(director) as client:
        draft = await client.post("/api/spk", json=_simple(pic["id"], publish=False, title="UAT-SPK-ANTAR-DIVISI draft"))
        assert draft.status_code == 200, draft.text
        order = draft.json()["order"]
        assert order["workflow_status"] == "DRAFT"
        edited = await client.patch(f"/api/spk/{order['id']}", json={
            "doc_version": order["doc_version"],
            "title": "UAT-SPK-ANTAR-DIVISI diperbaiki",
            "instruction": order["instruction"],
            "assigned_user_ids": [pic["id"]],
            "milestones": order["milestones"],
        })
        assert edited.status_code == 200, edited.text
    async with _client_for(other) as client:
        denied = await client.patch(f"/api/spk/{order['id']}", json={"doc_version": edited.json()["order"]["doc_version"], "title": "Diambil orang lain"})
        assert denied.status_code == 403
    async with _client_for(director) as client:
        published = await client.post(f"/api/spk/{order['id']}/publish", json={
            "doc_version": edited.json()["order"]["doc_version"],
            "idempotency_key": "publish-draft-1",
        })
        assert published.status_code == 200, published.text
        live = published.json()["order"]
        assert live["id"] == order["id"]
        assert live["spk_number"] == order["spk_number"]
        assert live["workflow_status"] == "PUBLISHED"
        removed = await client.delete(f"/api/spk/{order['id']}")
        assert removed.status_code == 400


@pytest.mark.anyio
async def test_return_republish_keeps_number_and_moves_pic_access():
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True, "spk.cancel": True})
    first = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    second = _user("operator", name="Rina", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, first, second])
    async with _client_for(director) as client:
        created = await client.post("/api/spk", json=_simple(first["id"], publish=True, title="UAT-SPK-ANTAR-DIVISI kembali"))
        order = created.json()["order"]
        number = order["spk_number"]
    async with _client_for(second) as client:
        denied = await client.post(f"/api/spk/{order['id']}/return", json={"reason": "Bukan PIC", "doc_version": order["doc_version"]})
        assert denied.status_code in (403, 404)
    async with _client_for(first) as client:
        short = await client.post(f"/api/spk/{order['id']}/return", json={"reason": "no", "doc_version": order["doc_version"]})
        assert short.status_code == 400
        returned = await client.post(f"/api/spk/{order['id']}/return", json={"reason": "Instruksi belum jelas", "doc_version": order["doc_version"]})
        assert returned.status_code == 200, returned.text
        assert returned.json()["order"]["workflow_status"] == "RETURNED"
    async with _client_for(director) as client:
        edited = await client.patch(f"/api/spk/{order['id']}", json={
            "doc_version": returned.json()["order"]["doc_version"],
            "title": order["title"],
            "instruction": "Instruksi diperjelas untuk PIC baru",
            "assigned_user_ids": [second["id"]],
            "target_division": "operator",
            "milestones": order["milestones"],
        })
        assert edited.status_code == 200, edited.text
        again = await client.post(f"/api/spk/{order['id']}/publish", json={
            "doc_version": edited.json()["order"]["doc_version"],
            "idempotency_key": "republish-1",
        })
        assert again.status_code == 200, again.text
        live = again.json()["order"]
        assert live["spk_number"] == number
        assert live["id"] == order["id"]
        assert live["workflow_status"] == "PUBLISHED"
        assert second["id"] in live["assigned_user_ids"]
        assert first["id"] not in live["assigned_user_ids"]
    async with _client_for(first) as client:
        lost = await client.post(f"/api/spk/{order['id']}/acknowledge", json={"doc_version": live["doc_version"]})
        assert lost.status_code in (403, 404)
    async with _client_for(second) as client:
        ack = await client.post(f"/api/spk/{order['id']}/acknowledge", json={"doc_version": live["doc_version"]})
        assert ack.status_code == 200, ack.text
        started = await client.post(f"/api/spk/{order['id']}/start", json={"doc_version": ack.json()["order"]["doc_version"]})
        assert started.status_code == 200, started.text
        blocked = await client.post(f"/api/spk/{order['id']}/return", json={
            "reason": "Sudah terlambat dikembalikan",
            "doc_version": started.json()["order"]["doc_version"],
        })
        assert blocked.status_code == 400
    import server
    notes = await server.db.notifications.find({"order_id": order["id"], "user_id": second["id"]}).to_list(10)
    assert any(row["event_type"] == "spk_republished" for row in notes)
    creator_notes = await server.db.notifications.find({"order_id": order["id"], "user_id": director["id"]}).to_list(10)
    assert any(row["event_type"] == "spk_returned" for row in creator_notes)


@pytest.mark.anyio
async def test_idempotent_create_and_publish_do_not_duplicate():
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True})
    await _prepare([director, pic])
    payload = _simple(pic["id"], publish=True, title="UAT-SPK-ANTAR-DIVISI idem", idempotency_key="same-key")
    other = _simple(pic["id"], publish=True, title="UAT-SPK-ANTAR-DIVISI lain", idempotency_key="other-key")

    async def _post(body):
        async with _client_for(director) as client:
            return await client.post("/api/spk", json=body)

    first, second = await asyncio.gather(_post(payload), _post(dict(payload)))
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["order"]["id"] == second.json()["order"]["id"]
    assert first.json()["order"]["spk_number"] == second.json()["order"]["spk_number"]
    import server
    assert await server.db[ORDERS].count_documents({"title": payload["title"]}) == 1
    assert await server.db[EVENTS].count_documents({"spk_id": first.json()["order"]["id"], "event_type": "PUBLISH"}) == 1
    async with _client_for(director) as client:
        retry = await client.post("/api/spk", json=payload)
        assert retry.status_code == 200
        assert retry.json()["order"]["id"] == first.json()["order"]["id"]
        clash = await client.post("/api/spk", json={**other, "idempotency_key": "same-key", "title": "Payload berbeda"})
        assert clash.status_code == 409
        fresh = await client.post("/api/spk", json=other)
        assert fresh.status_code == 200
        assert fresh.json()["order"]["spk_number"] != first.json()["order"]["spk_number"]
        draft = await client.post("/api/spk", json=_simple(pic["id"], publish=False, title="Draft idem", idempotency_key="draft-key"))
        draft_id = draft.json()["order"]["id"]
        version = draft.json()["order"]["doc_version"]

        async def _publish():
            async with _client_for(director) as inner:
                return await inner.post(f"/api/spk/{draft_id}/publish", json={"doc_version": version, "idempotency_key": "pub-key"})

        pub_a, pub_b = await asyncio.gather(_publish(), _publish())
        assert {pub_a.status_code, pub_b.status_code} == {200}
        assert pub_a.json()["order"]["id"] == pub_b.json()["order"]["id"] == draft_id
    assert await server.db[EVENTS].count_documents({"spk_id": draft_id, "event_type": "PUBLISH"}) == 1
    assert await server.db.notifications.count_documents({"order_id": draft_id, "event_type": "spk_assigned"}) == 1
    stored_key = await server.db.spk_idempotency.find_one({"idempotency_key": "same-key"}, {"_id": 0})
    assert stored_key["created_at"]
    assert stored_key["status"] == "done"
    from datetime import datetime, timedelta, timezone
    retain = stored_key["retain_until"]
    assert isinstance(retain, datetime)
    if retain.tzinfo is None:
        retain = retain.replace(tzinfo=timezone.utc)
    remaining = retain - datetime.now(timezone.utc)
    assert timedelta(days=89) < remaining < timedelta(days=91)
    indexes = await server.db.spk_idempotency.index_information()
    assert indexes["spk_idempotency_retain_until"]["expireAfterSeconds"] == 0


@pytest.mark.anyio
async def test_workflow_and_cancel_do_not_touch_other_modules():
    director = _user("direktur_utama", name="Dimas", override={
        "spk.view_all": True, "spk.create": True, "spk.verify": True, "spk.cancel": True,
    })
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, pic])
    import server
    db = server.db
    for name, doc in MODULE_DOCS.items():
        await db[name].delete_many({"id": doc["id"]})
        await db[name].insert_one(dict(doc))
    before = await _counts(db)
    baseline = await _snapshots(db)
    async with _client_for(director) as client:
        created = await client.post("/api/spk", json=_simple(pic["id"], publish=True, title="UAT-SPK-ANTAR-DIVISI alur"))
        order = created.json()["order"]
        number = order["spk_number"]
    async with _client_for(pic) as client:
        returned = await client.post(f"/api/spk/{order['id']}/return", json={
            "reason": "Perlu perbaikan instruksi",
            "doc_version": order["doc_version"],
        })
        assert returned.status_code == 200, returned.text
    async with _client_for(director) as client:
        again = await client.post(f"/api/spk/{order['id']}/publish", json={
            "doc_version": returned.json()["order"]["doc_version"],
            "idempotency_key": "isolation-republish",
        })
        assert again.status_code == 200, again.text
        order = again.json()["order"]
        assert order["id"] == created.json()["order"]["id"]
        assert order["spk_number"] == number
    async with _client_for(pic) as client:
        ack = await client.post(f"/api/spk/{order['id']}/acknowledge", json={"doc_version": order["doc_version"]})
        started = await client.post(f"/api/spk/{order['id']}/start", json={"doc_version": ack.json()["order"]["doc_version"]})
        current = started.json()["order"]
        progressed = await client.post(f"/api/spk/{order['id']}/progress", json={
            "doc_version": current["doc_version"],
            "milestone_id": current["milestones"][0]["id"],
            "new_actual": 10,
            "idempotency_key": "finish-flow",
        })
        assert progressed.status_code == 200, progressed.text
        requested = await client.post(f"/api/spk/{order['id']}/verification-request", json={"doc_version": progressed.json()["order"]["doc_version"]})
        assert requested.status_code == 200, requested.text
    async with _client_for(director) as client:
        revised = await client.post(f"/api/spk/{order['id']}/verify", json={
            "decision": "revision", "reason": "Lengkapi catatan", "doc_version": requested.json()["order"]["doc_version"],
        })
        assert revised.status_code == 200, revised.text
    async with _client_for(pic) as client:
        detail = (await client.get(f"/api/spk/{order['id']}")).json()["order"]
        requested = await client.post(f"/api/spk/{order['id']}/verification-request", json={"doc_version": detail["doc_version"]})
    async with _client_for(director) as client:
        approved = await client.post(f"/api/spk/{order['id']}/verify", json={
            "decision": "approve", "doc_version": requested.json()["order"]["doc_version"],
        })
        assert approved.status_code == 200
        assert approved.json()["order"]["workflow_status"] == "COMPLETED"
        cancel_target = await client.post("/api/spk", json=_simple(pic["id"], publish=True, title="UAT-SPK-ANTAR-DIVISI batal"))
        cancelled = await client.post(f"/api/spk/{cancel_target.json()['order']['id']}/cancel", json={
            "reason": "Tidak jadi dikerjakan",
            "doc_version": cancel_target.json()["order"]["doc_version"],
        })
        assert cancelled.status_code == 200
        assert cancelled.json()["order"]["workflow_status"] == "CANCELLED"
    assert await _counts(db) == before
    assert await _snapshots(db) == baseline
    for name, doc in MODULE_DOCS.items():
        await db[name].delete_one({"id": doc["id"]})


@pytest.mark.anyio
async def test_saved_role_without_spk_keys_is_not_granted():
    user = _user("crm", name="Tanpa Izin", override={})
    await _prepare([user])
    import server
    db = server.db
    await db.role_permissions.delete_many({"role": "crm"})
    await db.role_permissions.insert_one({"role": "crm", "label": "CRM", "permissions": ["po.view"]})
    try:
        async with _client_for(user) as client:
            denied = await client.get("/api/spk")
            assert denied.status_code == 403
        report_user = _user("admin", name="Admin", override={})
        async with _client_for(report_user) as client:
            report = await client.get("/api/spk/permission-report")
            assert report.status_code == 200, report.text
            roles = {row["role"] for row in report.json()["roles_without_spk"]}
            assert "crm" in roles
        stored = await db.role_permissions.find_one({"role": "crm"}, {"_id": 0})
        assert stored["permissions"] == ["po.view"]
    finally:
        await db.role_permissions.delete_many({"role": "crm"})


def _blocked(status_code):
    assert status_code in (403, 404)
    return status_code


@pytest.mark.anyio
async def test_normal_return_start_and_progress_are_assigned_pic_only():
    director = _user("direktur_utama", name="Dimas", override={
        "spk.view_all": True, "spk.create": True, "spk.verify": True, "spk.comment": True, "spk.cancel": True,
    })
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True, "spk.comment": True})
    admin = _user("admin", name="Admin Operasional")
    head = _user("head_production", name="Kepala", override={
        "spk.view_division": True, "spk.verify": True, "spk.comment": True, "spk.update_progress": True,
    })
    other = _user("operator", name="Operator Lain", override={"spk.view_own": True, "spk.update_progress": True})
    stranger = _user("holding_team", name="Tanpa Akses", override={
        "spk.view_own": False, "spk.view_division": False, "spk.view_all": False, "spk.manage": False, "spk.update_progress": False,
    })
    corrector = _user("crm", uid="corrector-1", name="Korektor", override={
        "spk.view_all": True, "spk.correct": True,
    })
    await _prepare([director, pic, admin, head, other, stranger, corrector])
    async with _client_for(director) as client:
        owned = await client.post("/api/spk", json=_simple(pic["id"], publish=True, title="SPK milik PIC"))
        assert owned.status_code == 200, owned.text
        pic_order = owned.json()["order"]
        admin_owned = await client.post("/api/spk", json=_simple(admin["id"], publish=True, target_division="direksi", title="SPK milik admin"))
        assert admin_owned.status_code == 200, admin_owned.text
        admin_order = admin_owned.json()["order"]
    async with _client_for(pic) as client:
        seen = await client.get(f"/api/spk/{pic_order['id']}")
        assert seen.json()["order"]["capabilities"]["return_spk"] is True
        short = await client.post(f"/api/spk/{pic_order['id']}/return", json={"reason": "no", "doc_version": pic_order["doc_version"]})
        assert short.status_code == 400
        returned = await client.post(f"/api/spk/{pic_order['id']}/return", json={
            "reason": "Instruksi belum jelas",
            "doc_version": pic_order["doc_version"],
        })
        assert returned.status_code == 200, returned.text
        assert returned.json()["order"]["workflow_status"] == "RETURNED"
    async with _client_for(admin) as client:
        seen = await client.get(f"/api/spk/{admin_order['id']}")
        assert seen.json()["order"]["capabilities"]["return_spk"] is True
        returned = await client.post(f"/api/spk/{admin_order['id']}/return", json={
            "reason": "Saya PIC dan mengembalikan",
            "doc_version": admin_order["doc_version"],
        })
        assert returned.status_code == 200, returned.text
    async with _client_for(director) as client:
        fresh = await client.post("/api/spk", json=_simple(pic["id"], publish=True, title="SPK yang tidak boleh dikembalikan orang lain"))
        blocked = fresh.json()["order"]
    async with _client_for(admin) as client:
        caps = (await client.get(f"/api/spk/{blocked['id']}")).json()["order"]["capabilities"]
        assert caps["return_spk"] is False
        assert caps["acknowledge"] is False
        assert caps["start"] is False
        assert caps["update_progress"] is False
        for path in ("return", "acknowledge", "start"):
            body = {"doc_version": blocked["doc_version"], "reason": "Bukan PIC yang ditugaskan"}
            denied = await client.post(f"/api/spk/{blocked['id']}/{path}", json=body)
            _blocked(denied.status_code)
        denied = await client.post(f"/api/spk/{blocked['id']}/progress", json={
            "doc_version": blocked["doc_version"],
            "milestone_id": blocked["milestones"][0]["id"],
            "new_actual": 1,
            "idempotency_key": "admin-progress",
        })
        _blocked(denied.status_code)
    async with _client_for(pic) as client:
        ack = await client.post(f"/api/spk/{blocked['id']}/acknowledge", json={"doc_version": blocked["doc_version"]})
        started = await client.post(f"/api/spk/{blocked['id']}/start", json={"doc_version": ack.json()["order"]["doc_version"]})
        assert started.status_code == 200, started.text
        working = started.json()["order"]
        moved = await client.post(f"/api/spk/{blocked['id']}/progress", json={
            "doc_version": working["doc_version"],
            "milestone_id": working["milestones"][0]["id"],
            "new_actual": 4,
            "idempotency_key": "pic-progress",
        })
        assert moved.status_code == 200, moved.text
        working = moved.json()["order"]
    for actor in (admin, head, director):
        async with _client_for(actor) as client:
            denied = await client.post(f"/api/spk/{blocked['id']}/progress", json={
                "doc_version": working["doc_version"],
                "milestone_id": working["milestones"][0]["id"],
                "new_actual": 6,
                "idempotency_key": f"deny-{actor['id']}",
            })
            _blocked(denied.status_code)
            denied_return = await client.post(f"/api/spk/{blocked['id']}/return", json={
                "reason": "Bukan PIC yang ditugaskan",
                "doc_version": working["doc_version"],
            })
            _blocked(denied_return.status_code)
            if actor is not admin:
                denied_correct = await client.post(f"/api/spk/{blocked['id']}/correct", json={
                    "doc_version": working["doc_version"],
                    "milestone_id": working["milestones"][0]["id"],
                    "new_actual": 2,
                    "reason": "Bukan izin koreksi",
                })
                _blocked(denied_correct.status_code)
    async with _client_for(other) as client:
        _blocked((await client.post(f"/api/spk/{blocked['id']}/return", json={
            "reason": "Bukan PIC yang ditugaskan",
            "doc_version": working["doc_version"],
        })).status_code)
        _blocked((await client.post(f"/api/spk/{blocked['id']}/progress", json={
            "doc_version": working["doc_version"],
            "milestone_id": working["milestones"][0]["id"],
            "new_actual": 5,
            "idempotency_key": "other-progress",
        })).status_code)
    async with _client_for(stranger) as client:
        _blocked((await client.post(f"/api/spk/{blocked['id']}/return", json={
            "reason": "Tidak punya izin",
            "doc_version": working["doc_version"],
        })).status_code)
    async with _client_for(admin) as client:
        fixed = await client.post(f"/api/spk/{blocked['id']}/correct", json={
            "doc_version": working["doc_version"],
            "milestone_id": working["milestones"][0]["id"],
            "new_actual": 2,
            "reason": "Koreksi administratif tercatat",
        })
        assert fixed.status_code == 200, fixed.text
        assert fixed.json()["order"]["milestones"][0]["actual_value"] == "2.00"
        working = fixed.json()["order"]
    async with _client_for(corrector) as client:
        fixed = await client.post(f"/api/spk/{blocked['id']}/correct", json={
            "doc_version": working["doc_version"],
            "milestone_id": working["milestones"][0]["id"],
            "new_actual": 1,
            "reason": "Izin koreksi khusus",
        })
        assert fixed.status_code == 200, fixed.text
    import server
    events = await server.db[EVENTS].find({"spk_id": pic_order["id"], "event_type": "RETURN"}).to_list(5)
    assert len(events) == 1
    assert events[0]["actor"]["id"] == pic["id"]
    assert events[0]["workflow_status_before"] == "PUBLISHED"
    assert events[0]["workflow_status_after"] == "RETURNED"
    assert events[0]["reason"] == "Instruksi belum jelas"
    corrections = await server.db[EVENTS].find({"spk_id": blocked["id"], "event_type": "CORRECTION"}).sort("created_at", 1).to_list(5)
    assert [row["actor"]["id"] for row in corrections] == [admin["id"], corrector["id"]]
    assert corrections[0]["previous_actual"] == "4.00"
    assert corrections[0]["new_actual"] == "2.00"
    assert corrections[0]["reason"] == "Koreksi administratif tercatat"
    stored = await server.db[ORDERS].find_one({"id": blocked["id"]}, {"_id": 0})
    assert stored["milestones"][0]["actual_value"] == "1.00"


@pytest.mark.anyio
async def test_republish_same_key_is_one_transition():
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, pic])
    async with _client_for(director) as client:
        created = await client.post("/api/spk", json=_simple(pic["id"], publish=True, title="Terbit ulang idem"))
        order = created.json()["order"]
    async with _client_for(pic) as client:
        returned = await client.post(f"/api/spk/{order['id']}/return", json={
            "reason": "Perlu PIC lain nanti",
            "doc_version": order["doc_version"],
        })
        assert returned.status_code == 200, returned.text
        version = returned.json()["order"]["doc_version"]
    async with _client_for(director) as client:
        async def _publish():
            async with _client_for(director) as inner:
                return await inner.post(f"/api/spk/{order['id']}/publish", json={
                    "doc_version": version,
                    "idempotency_key": "republish-same",
                })

        first, second = await asyncio.gather(_publish(), _publish())
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["order"]["id"] == second.json()["order"]["id"] == order["id"]
        assert first.json()["order"]["spk_number"] == order["spk_number"]
    import server
    assert await server.db[EVENTS].count_documents({"spk_id": order["id"], "event_type": "REPUBLISH"}) == 1
    assert await server.db.notifications.count_documents({"order_id": order["id"], "event_type": "spk_republished"}) == 1


@pytest.mark.anyio
async def test_pagination_filters_active_month_and_legacy_documents():
    from datetime import date
    from services import spk as spk_service
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    other = _user("operator", name="Rina", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, pic, other])
    spk_service._today_override = date(2026, 9, 22)
    try:
        async with _client_for(director) as client:
            made = []
            for index, title in enumerate(("Halaman satu", "Halaman dua", "FILTER-KHUSUS")):
                body = _simple(
                    pic["id"],
                    publish=True,
                    title=title,
                    priority="HIGH" if index == 2 else "LOW",
                    target_division="penjualan",
                    deadline="2026-09-07",
                )
                created = await client.post("/api/spk", json=body)
                assert created.status_code == 200, created.text
                made.append(created.json()["order"])
            hidden = await client.post("/api/spk", json=_simple(other["id"], publish=True, title="Milik operator", target_division="operator"))
            assert hidden.status_code == 200, hidden.text
        import server
        legacy_id = "legacy-spk-202401"
        await server.db[ORDERS].insert_one({
            "id": legacy_id,
            "spk_number": "SPK-202401-0007",
            "title": "SPK selesai lama",
            "workflow_status": "COMPLETED",
            "priority": "NORMAL",
            "target_division": "penjualan",
            "created_by_id": director["id"],
            "assigned_user_ids": [pic["id"]],
            "assigned_pics": [{"id": pic["id"], "name": "Fahdo"}],
            "milestones": [],
            "doc_version": 4,
            "completed_at": "2026-08-02T00:00:00+00:00",
            "progress_percent": "100.00",
        })
        await server.db[ORDERS].insert_one({
            "id": "done-this-month",
            "spk_number": "SPK-202609-0099",
            "title": "Selesai September",
            "workflow_status": "COMPLETED",
            "priority": "NORMAL",
            "target_division": "penjualan",
            "created_by_id": director["id"],
            "assigned_user_ids": [pic["id"]],
            "assigned_pics": [{"id": pic["id"], "name": "Fahdo"}],
            "milestones": [],
            "doc_version": 2,
            "completed_at": "2026-09-15T00:00:00+00:00",
        })
        async with _client_for(director) as client:
            opened = await client.get(f"/api/spk/{legacy_id}")
            assert opened.status_code == 200, opened.text
            legacy = opened.json()["order"]
            assert legacy["spk_number"] == "SPK-202401-0007"
            assert legacy["workflow_status"] == "COMPLETED"
            first = await client.get("/api/spk", params={"tab": "all", "q": "Halaman", "page": 1, "page_size": 1})
            second = await client.get("/api/spk", params={"tab": "all", "q": "Halaman", "page": 2, "page_size": 1})
            assert first.status_code == 200 and second.status_code == 200
            page_one = first.json()
            page_two = second.json()
            assert page_one["total"] == 2
            assert page_one["page"] == 1 and page_two["page"] == 2
            assert page_one["page_size"] == 1
            assert page_one["total_pages"] == 2
            assert page_one["orders"][0]["id"] != page_two["orders"][0]["id"]
            active = await client.get("/api/spk", params={
                "tab": "all",
                "status": "PUBLISHED,ACKNOWLEDGED,IN_PROGRESS,REVISION_REQUIRED",
                "page_size": 20,
            })
            assert active.status_code == 200, active.text
            assert active.json()["summary"]["active"] == active.json()["total"]
            assert active.json()["summary"]["completed_this_month"] == 1
            month = await client.get("/api/spk", params={"tab": "all", "completed_month": "1", "page_size": 20})
            assert month.status_code == 200, month.text
            month_ids = {row["id"] for row in month.json()["orders"]}
            assert month_ids == {"done-this-month"}
            assert "legacy-spk-202401" not in month_ids
            combined = await client.get("/api/spk", params={
                "tab": "all",
                "status": "PUBLISHED",
                "priority": "HIGH",
                "division": "penjualan",
                "pic": pic["id"],
                "deadline_state": "OVERDUE",
                "q": "FILTER-KHUSUS",
                "page": 1,
                "page_size": 20,
            })
            assert combined.status_code == 200, combined.text
            assert combined.json()["total"] == 1
            assert combined.json()["orders"][0]["id"] == made[2]["id"]
        async with _client_for(other) as client:
            visible = await client.get("/api/spk", params={"tab": "mine", "page": 1, "page_size": 1})
            assert visible.status_code == 200, visible.text
            assert visible.json()["total"] == 1
            assert visible.json()["orders"][0]["title"] == "Milik operator"
            leaked = await client.get(f"/api/spk/{made[0]['id']}")
            assert leaked.status_code in (403, 404)
        stored = await server.db[ORDERS].find_one({"id": legacy_id}, {"_id": 0})
        assert stored["spk_number"] == "SPK-202401-0007"
        assert stored["workflow_status"] == "COMPLETED"
        assert stored["doc_version"] == 4
    finally:
        spk_service._today_override = None


@pytest.mark.anyio
async def test_attachment_download_is_scoped_and_validated():
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True, "spk.comment": True})
    stranger = _user("operator", name="Orang Lain", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, pic, stranger])
    async with _client_for(director) as client:
        first = await client.post("/api/spk", json=_simple(pic["id"], publish=True, title="Lampiran satu"))
        second = await client.post("/api/spk", json=_simple(pic["id"], publish=True, title="Lampiran dua"))
        order = first.json()["order"]
        other = second.json()["order"]
    async with _client_for(pic) as client:
        ack = await client.post(f"/api/spk/{order['id']}/acknowledge", json={"doc_version": order["doc_version"]})
        started = await client.post(f"/api/spk/{order['id']}/start", json={"doc_version": ack.json()["order"]["doc_version"]})
        current = started.json()["order"]
        bad_type = await client.post("/api/spk/uploads", files={"file": ("catatan.txt", b"hello", "text/plain")})
        assert bad_type.status_code == 400
        too_big = await client.post(
            "/api/spk/uploads",
            files={"file": ("besar.pdf", b"%PDF" + (b"0" * (8 * 1024 * 1024)), "application/pdf")},
        )
        assert too_big.status_code == 400
        uploaded = await client.post(
            "/api/spk/uploads",
            files={"file": ("bukti.pdf", b"%PDF-1.4 bukti", "application/pdf")},
        )
        assert uploaded.status_code == 200, uploaded.text
        file_id = uploaded.json()["file"]["id"]
        noted = await client.post(f"/api/spk/{order['id']}/notes", json={
            "doc_version": current["doc_version"],
            "evidence_only": True,
            "milestone_id": current["milestones"][0]["id"],
            "file_ids": [file_id],
        })
        assert noted.status_code == 200, noted.text
        assert noted.json()["order"]["milestones"][0]["actual_value"] == "0.00"
        downloaded = await client.get(f"/api/spk/{order['id']}/files/{file_id}")
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content.startswith(b"%PDF")
        crossed = await client.get(f"/api/spk/{other['id']}/files/{file_id}")
        assert crossed.status_code == 404
        traversal = await client.get(f"/api/spk/{order['id']}/files/..%2F..%2Fetc%2Fpasswd")
        assert traversal.status_code == 404
    async with _client_for(stranger) as client:
        denied = await client.get(f"/api/spk/{order['id']}/files/{file_id}")
        assert denied.status_code in (403, 404)
    async with _client_for(director) as client:
        denied = await client.post(f"/api/spk/{order['id']}/notes", json={
            "doc_version": noted.json()["order"]["doc_version"],
            "evidence_only": True,
            "milestone_id": current["milestones"][0]["id"],
            "file_ids": [file_id],
        })
        assert denied.status_code == 403
