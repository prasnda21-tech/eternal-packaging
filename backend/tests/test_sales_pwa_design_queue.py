"""Design-approval queue follows the open gate, not the workflow stage label."""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pymongo import monitoring

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from permissions import ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.sales_pwa import design_approval_need

AYUB = "sales-queue-ayub"
EUIS = "sales-queue-euis"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    app.dependency_overrides.pop(current_user, None)
    yield
    app.dependency_overrides.pop(current_user, None)


def _sales(uid):
    return {
        "id": uid,
        "user_id": uid,
        "email": f"{uid}@eternal.test",
        "name": uid,
        "role": "sales_eternal",
        "permissions": list(ROLE_DEFAULT_PERMISSIONS["sales_eternal"]),
    }


def _admin():
    return {
        "id": "admin-queue",
        "user_id": "admin-queue",
        "email": "admin.queue@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
        "permissions": list(ROLE_DEFAULT_PERMISSIONS["admin"]),
    }


def _pdf(file_id, name, uploaded_at):
    return {
        "id": file_id,
        "name": name,
        "content_type": "application/pdf",
        "uploaded_at": uploaded_at,
        "data_url": "data:application/pdf;base64,JVBERi0=",
    }


def _order(po, sales_id, day, **extra):
    now = datetime.now(timezone.utc).isoformat()
    customer = extra.pop("customer", "Pelanggan")
    doc = {
        "id": str(uuid.uuid4()),
        "po_number": po,
        "customer": customer,
        "customer_name": extra.pop("customer_name", customer),
        "sales_user_id": sales_id,
        "order_date": day,
        "cancelled": False,
        "hold": False,
        "current_stage": 2,
        "design_status": "Waiting",
        "line_items": [{"name": "Cup", "quantity": 10, "unit": "pcs"}],
        "design_files": [],
        "created_at": now,
        "updated_at": now,
    }
    doc.update(extra)
    return doc


def test_need_uses_the_open_gate_and_designer_label():
    waiting_file = _order("PO-WAIT", AYUB, "2026-09-02", design_files=[_pdf("f1", "aktif.pdf", "2026-09-02T00:00:00+00:00")])
    assert design_approval_need(waiting_file)["code"] == "waiting_customer"
    assert design_approval_need(waiting_file)["design_name"] == "aktif.pdf"

    bare = _order("PO-BARE", AYUB, "2026-08-01")
    assert design_approval_need(bare) == {"code": "waiting_design", "label": "Menunggu desain"}

    revised = _order(
        "PO-REV",
        AYUB,
        "2026-09-03",
        design_files=[_pdf("f-rev", "revisi.pdf", "2026-09-01T00:00:00+00:00")],
        customer_approval={
            "approval_id": "appr-rev",
            "status": "rejected",
            "design_version": "f-rev",
            "responded_at": "2026-09-04T00:00:00+00:00",
        },
    )
    assert design_approval_need(revised)["code"] == "needs_revision"
    assert design_approval_need(revised)["label"] == "Perlu revisi"
    assert design_approval_need(revised)["design_name"] == "revisi.pdf"

    reuploaded = _order(
        "PO-REUP",
        AYUB,
        "2026-09-03",
        design_files=[_pdf("f-new", "baru.pdf", "2026-09-05T00:00:00+00:00")],
        customer_approval={
            "approval_id": "appr-old",
            "status": "rejected",
            "design_version": "f-new",
            "responded_at": "2026-09-04T00:00:00+00:00",
        },
    )
    assert design_approval_need(reuploaded)["code"] == "waiting_customer"

    assert design_approval_need(_order("PO-OK", AYUB, "2026-09-01", design_status="Approved")) is None
    assert design_approval_need(_order("PO-BY", AYUB, "2026-09-01", design_bypassed=True)) is None
    assert design_approval_need(_order(
        "PO-CUST",
        AYUB,
        "2026-09-01",
        customer_approval={"approval_id": "appr", "status": "approved", "design_version": "f"},
    )) is None
    assert design_approval_need(_order("PO-CAN", AYUB, "2026-09-01", cancelled=True)) is None
    delivered = _order("PO-DONE", AYUB, "2026-08-01", qty_delivered=10, delivery_status="Terkirim")
    assert design_approval_need(delivered) is None
    held = _order("PO-HOLD", AYUB, "2026-07-01", hold=True)
    assert design_approval_need(held)["code"] == "waiting_design"


def _approval_db_cells():
    """Customer-approval routes close over the import-time Motor database."""
    cells = []
    seen = set()
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        code = getattr(endpoint, "__code__", None)
        closure = getattr(endpoint, "__closure__", None)
        path = getattr(route, "path", "")
        if not code or not closure or "customer-approval" not in path or "db" not in code.co_freevars:
            continue
        cell = closure[code.co_freevars.index("db")]
        if id(cell) in seen:
            continue
        seen.add(id(cell))
        cells.append(cell)
    return cells


class _WriteSpy(monitoring.CommandListener):
    def __init__(self):
        self.writes = []

    def started(self, event):
        if event.command_name in {"insert", "update", "delete", "findAndModify"}:
            command = event.command if isinstance(event.command, dict) else {}
            collection = str(command.get(event.command_name) or "")
            self.writes.append(f"{event.command_name}:{collection}")

    def succeeded(self, event):
        return None

    def failed(self, event):
        return None



@pytest.mark.anyio
async def test_queue_ignores_month_and_matches_the_list_without_writes():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient

    live = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.client = live
    server.db = live[os.environ["DB_NAME"]]
    db = server.db
    approval_cells = _approval_db_cells()
    assert approval_cells, "customer-approval routes did not close over db"
    previous_dbs = [cell.cell_contents for cell in approval_cells]
    for cell in approval_cells:
        cell.cell_contents = db
    client_db = None
    ids = []
    try:
        suffix = uuid.uuid4().hex[:8]
        ayub = f"sales-queue-{suffix}"
        euis = f"sales-queue-other-{suffix}"
        future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        old_id = f"file-old-{suffix}"
        new_id = f"file-new-{suffix}"
        current = _order(
            f"PO-CUR-{suffix}",
            ayub,
            "2026-10-02",
            customer="Pelanggan Oktober",
            customer_name="Pelanggan Oktober",
            design_files=[_pdf(old_id, "lama.pdf", "2026-10-01T00:00:00+00:00")],
        )
        previous = _order(
            f"PO-PREV-{suffix}",
            ayub,
            "2026-09-12",
            customer="Pelanggan September",
            customer_name="Pelanggan September",
            design_files=[_pdf(f"file-prev-{suffix}", "sept.pdf", "2026-09-12T00:00:00+00:00")],
            customer_approval={
                "approval_id": f"appr-prev-{suffix}",
                "status": "active",
                "design_version": f"file-prev-{suffix}",
                "expires_at": future,
            },
        )
        missing_link = _order(
            f"PO-NOLINK-{suffix}",
            ayub,
            "2026-10-01",
            customer="Pelanggan Tanpa Link",
            customer_name="Pelanggan Tanpa Link",
            design_files=[_pdf(f"file-nolink-{suffix}", "tanpa.pdf", "2026-10-01T00:00:00+00:00")],
            customer_approval={
                "approval_id": f"appr-nolink-{suffix}",
                "status": "active",
                "design_version": f"file-nolink-{suffix}",
                "expires_at": future,
            },
        )
        revision = _order(
            f"PO-REV-{suffix}",
            ayub,
            "2026-08-20",
            customer="Pelanggan Revisi",
            customer_name="Pelanggan Revisi",
            hold=True,
            design_files=[_pdf(f"file-rev-{suffix}", "revisi.pdf", "2026-08-01T00:00:00+00:00")],
            customer_approval={
                "approval_id": f"appr-rev-{suffix}",
                "status": "rejected",
                "design_version": f"file-rev-{suffix}",
                "responded_at": "2026-08-21T00:00:00+00:00",
            },
        )
        bare = _order(
            f"PO-BARE-{suffix}",
            ayub,
            "2026-10-02",
            customer="Pelanggan Kosong",
            customer_name="Pelanggan Kosong",
        )
        approved = _order(
            f"PO-OK-{suffix}",
            ayub,
            "2026-10-02",
            customer="Pelanggan Selesai",
            customer_name="Pelanggan Selesai",
            design_status="Approved",
            design_files=[_pdf(f"file-ok-{suffix}", "selesai.pdf", "2026-10-01T00:00:00+00:00")],
        )
        cancelled = _order(
            f"PO-CAN-{suffix}",
            ayub,
            "2026-09-01",
            customer="Pelanggan Batal",
            customer_name="Pelanggan Batal",
            cancelled=True,
        )
        delivered = _order(
            f"PO-DONE-{suffix}",
            ayub,
            "2026-07-01",
            customer="Pelanggan Terkirim",
            customer_name="Pelanggan Terkirim",
            qty_delivered=10,
            delivery_status="Terkirim",
        )
        foreign = _order(
            f"PO-OTHER-{suffix}",
            euis,
            "2026-09-12",
            customer="Pelanggan Euis",
            customer_name="Pelanggan Euis",
        )
        docs = [current, previous, missing_link, revision, bare, approved, cancelled, delivered, foreign]
        ids = [doc["id"] for doc in docs]
        await db.orders.delete_many({"sales_user_id": {"$in": [ayub, euis]}})
        await db.orders.delete_many({"id": {"$in": ids}})
        await db.customer_approvals.delete_many({"order_id": {"$in": ids}})
        await db.orders.insert_many(docs)
        await db.customer_approvals.insert_one({
            "id": f"appr-prev-{suffix}",
            "order_id": previous["id"],
            "status": "active",
            "design_version": f"file-prev-{suffix}",
            "generated_at": "2026-09-12T00:00:00+00:00",
            "expires_at": future,
            "token_hash": f"hash-prev-{suffix}",
        })

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            app.dependency_overrides[current_user] = lambda: _admin()
            generated = await client.post(f"/api/orders/{current['id']}/customer-approval/generate", json={})
            assert generated.status_code == 200, generated.text
            plain = generated.json()["token"]
            stored = await db.orders.find_one({"id": current["id"]}, {"tracking_token": 1, "customer_approval": 1})
            tracking = stored["tracking_token"]
            assert tracking and plain != tracking
            await db.orders.update_one(
                {"id": current["id"]},
                {"$set": {"design_files": [
                    _pdf(old_id, "lama.pdf", "2026-10-01T00:00:00+00:00"),
                    _pdf(new_id, "baru.pdf", "2026-10-02T00:00:00+00:00"),
                ]}},
            )
            again = await client.post(f"/api/orders/{current['id']}/customer-approval/generate", json={})
            assert again.status_code == 200, again.text
            assert again.json()["approval"]["design_version"] == new_id
            assert (await db.orders.find_one({"id": current["id"]}, {"tracking_token": 1}))["tracking_token"] == tracking

            opened = await client.get(f"/api/public/customer-approval/{tracking}")
            assert opened.status_code == 200, opened.text
            view = opened.json()["view"]
            assert view["po_number"] == current["po_number"]
            assert view["status"] == "active"
            assert view["design_file"]["name"] == "baru.pdf"
            assert view["design_file"]["id"] == new_id
            stale = await client.get(f"/api/public/customer-approval/{plain}")
            assert stale.status_code == 200, stale.text
            assert stale.json()["view"]["status"] != "active"
            assert stale.json()["view"]["design_file"]["name"] == "lama.pdf"
            assert stale.json()["view"]["design_file"]["id"] == old_id

            before = await db.orders.find_one({"id": current["id"]}, {"_id": 0})
            spy = _WriteSpy()
            client_db = AsyncIOMotorClient(os.environ["MONGO_URL"], event_listeners=[spy])
            server.client = client_db
            server.db = client_db[os.environ["DB_NAME"]]
            db = server.db
            spy.writes.clear()
            app.dependency_overrides[current_user] = lambda: _sales(ayub)
            actions = await client.get("/api/sales-pwa/field-actions", params={"month": "2026-10"})
            assert actions.status_code == 200, actions.text
            listed = await client.get("/api/sales-pwa/orders", params={"design_queue": "1", "status": "semua", "limit": 4})
            assert listed.status_code == 200, listed.text
            rest = await client.get("/api/sales-pwa/orders", params={"design_queue": "1", "status": "semua", "skip": 4, "limit": 4})
            assert rest.status_code == 200, rest.text
            detail = await client.get(f"/api/sales-pwa/orders/{missing_link['id']}")
            assert detail.status_code == 200, detail.text
            stage = await client.get("/api/sales-pwa/orders", params={"stage": "Approval Design", "status": "semua", "limit": 20})
            assert stage.status_code == 200, stage.text
        assert spy.writes == []
        after = await db.orders.find_one({"id": current["id"]}, {"_id": 0})
        assert after["tracking_token"] == before["tracking_token"]
        assert after.get("customer_approval") == before.get("customer_approval")

        body = actions.json()
        queue = body["design_queue"]
        assert queue["total"] == 5
        preview_ids = [row["id"] for row in queue["orders"]]
        assert len(preview_ids) == 4
        assert previous["id"] in preview_ids
        assert current["id"] in preview_ids
        assert approved["id"] not in preview_ids
        assert cancelled["id"] not in preview_ids
        assert delivered["id"] not in preview_ids
        assert foreign["id"] not in preview_ids
        stage_bucket = {row["id"] for row in body["design"]["orders"]}
        assert approved["id"] in stage_bucket
        assert current["id"] not in stage_bucket
        assert foreign["po_number"] not in actions.text
        assert plain not in actions.text
        assert f"hash-prev-{suffix}" not in actions.text

        by_id = {row["id"]: row for row in queue["orders"]}
        current_row = by_id[current["id"]]
        assert current_row["design_need"]["code"] == "waiting_customer"
        assert current_row["design_need"]["design_name"] == "baru.pdf"
        versions = {row.get("design_name"): row for row in current_row["design_approvals"]}
        assert versions["baru.pdf"]["path"] == f"/customer/design-approval/{tracking}"
        assert "path" not in versions["lama.pdf"]
        assert versions["lama.pdf"]["status"] == "revoked"
        previous_row = by_id[previous["id"]]
        assert previous_row["design_need"]["label"] == "Menunggu approval customer"
        assert "path" not in previous_row["design_approvals"][0]
        assert by_id[missing_link["id"]]["design_need"]["code"] == "waiting_customer"
        assert "path" not in by_id[missing_link["id"]]["design_approvals"][0]
        assert by_id[bare["id"]]["design_need"]["code"] == "waiting_design"
        assert by_id[bare["id"]]["design_approvals"] == []
        stage_ids = {row["id"] for row in stage.json()["orders"]}
        assert current["id"] not in stage_ids
        assert missing_link["id"] not in stage_ids

        page_ids = [row["id"] for row in listed.json()["orders"]]
        rest_ids = [row["id"] for row in rest.json()["orders"]]
        assert listed.json()["total"] == queue["total"] == 5
        assert page_ids == preview_ids
        assert len(page_ids) == 4
        assert rest_ids == [revision["id"]]
        assert rest.json()["orders"][0]["design_need"]["code"] == "needs_revision"
        assert set(page_ids + rest_ids) == {current["id"], previous["id"], missing_link["id"], revision["id"], bare["id"]}
        assert not set(page_ids) & set(rest_ids)
        assert detail.json()["order"]["design_need"]["code"] == "waiting_customer"
        assert "path" not in detail.json()["order"]["design_approvals"][0]

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            app.dependency_overrides[current_user] = lambda: _sales(euis)
            hidden = await client.get("/api/sales-pwa/field-actions", params={"month": "2026-09"})
            assert hidden.status_code == 200, hidden.text
            assert current["po_number"] not in hidden.text
            assert tracking not in hidden.text
            denied = await client.get(f"/api/sales-pwa/orders/{current['id']}")
            assert denied.status_code == 404
            assert tracking not in denied.text
            foreign_list = await client.get("/api/sales-pwa/orders", params={"design_queue": "1", "limit": 20})
            assert current["po_number"] not in [row["po_number"] for row in foreign_list.json()["orders"]]
    finally:
        if ids:
            await db.customer_approvals.delete_many({"order_id": {"$in": ids}})
            await db.orders.delete_many({"id": {"$in": ids}})
        for cell, previous in zip(approval_cells, previous_dbs):
            cell.cell_contents = previous
        if client_db is not None:
            client_db.close()
        live.close()
