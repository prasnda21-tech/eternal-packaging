"""PWA Sales design-approval links stay on the owning sales order."""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from permissions import ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.sales_pwa import design_approval_rows, effective_customer_approval_status

AYUB_ID = "sales-approval-ayub"
EUIS_ID = "sales-approval-euis"


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


def _sales(uid, email, name):
    return {
        "id": uid,
        "user_id": uid,
        "email": email,
        "name": name,
        "role": "sales_eternal",
        "permissions": list(ROLE_DEFAULT_PERMISSIONS["sales_eternal"]),
    }


def _admin():
    return {
        "id": "admin-approval",
        "user_id": "admin-approval",
        "email": "admin.approval@eternalpackaging.com",
        "name": "Admin",
        "role": "admin",
        "permissions": list(ROLE_DEFAULT_PERMISSIONS["admin"]),
    }


def _pdf(file_id, name):
    return {
        "id": file_id,
        "name": name,
        "content_type": "application/pdf",
        "data_url": "data:application/pdf;base64,JVBERi0=",
    }


def _order(po, sales_id, customer, **extra):
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "po_number": po,
        "customer": customer,
        "customer_name": customer,
        "sales": "Sholahudin Al - Ayubi",
        "sales_user_id": sales_id,
        "order_date": "2026-09-18",
        "cancelled": False,
        "current_stage": 2,
        "line_items": extra.pop("line_items", [{"name": "Cup", "color": "Merah", "quantity": 10, "unit": "pcs"}]),
        "design_files": extra.pop("design_files", []),
        "created_at": now,
        "updated_at": now,
    }
    doc.update(extra)
    return doc


def test_expired_snapshot_has_no_path_and_keeps_the_bound_design_name():
    order = {
        "id": "ord-1",
        "tracking_token": "tracking-should-not-be-used",
        "customer_approval": {
            "approval_id": "appr-old",
            "status": "active",
            "design_version": "file-b",
            "expires_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        },
        "design_files": [
            {"id": "file-a", "name": "salah.pdf"},
            {"id": "file-b", "name": "benar.pdf"},
        ],
        "line_items": [
            {"name": "Gelas", "color": "Biru"},
            {"name": "Gelas", "color": "Hijau"},
        ],
    }
    assert effective_customer_approval_status(order["customer_approval"]) == "expired"
    rows = design_approval_rows(order)
    assert len(rows) == 1
    assert rows[0]["design_name"] == "benar.pdf"
    assert rows[0]["status"] == "expired"
    assert "path" not in rows[0]
    assert rows[0]["items"] == [
        {"idx": 0, "label": "Gelas · Biru"},
        {"idx": 1, "label": "Gelas · Hijau"},
    ]
    blob = str(rows)
    assert "salah.pdf" not in blob
    assert "tracking-should-not-be-used" not in blob


def test_two_design_versions_do_not_share_one_path():
    order = {
        "tracking_token": "track-current",
        "customer_approval": {
            "approval_id": "appr-new",
            "status": "active",
            "design_version": "file-new",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
        },
        "design_files": [
            {"id": "file-old", "name": "lama.pdf"},
            {"id": "file-new", "name": "baru.pdf"},
        ],
        "line_items": [{"name": "Cup", "color": "Merah"}],
    }
    records = [
        {
            "id": "appr-old",
            "order_id": "ord",
            "status": "revoked",
            "design_version": "file-old",
            "generated_at": "2026-09-01T00:00:00+00:00",
            "token_hash": "secret-hash-old",
        },
        {
            "id": "appr-new",
            "order_id": "ord",
            "status": "active",
            "design_version": "file-new",
            "generated_at": "2026-09-02T00:00:00+00:00",
            "expires_at": order["customer_approval"]["expires_at"],
            "token_hash": "secret-hash-new",
        },
    ]
    rows = design_approval_rows(order, records)
    by_name = {row["design_name"]: row for row in rows}
    assert set(by_name) == {"baru.pdf", "lama.pdf"}
    assert by_name["baru.pdf"]["path"] == "/customer/design-approval/track-current"
    assert by_name["baru.pdf"]["status"] == "active"
    assert "path" not in by_name["lama.pdf"]
    assert by_name["lama.pdf"]["status"] == "revoked"
    assert "items" not in by_name["baru.pdf"]
    assert "token_hash" not in str(rows)
    assert "secret-hash" not in str(rows)


@pytest.mark.anyio
async def test_generated_link_appears_for_the_owner_and_opens_the_bound_design():
    import server
    db = server.db
    suffix = uuid.uuid4().hex[:8]
    old_id = f"file-old-{suffix}"
    new_id = f"file-new-{suffix}"
    single_id = f"file-one-{suffix}"
    multi = _order(
        f"PO-MULTI-{suffix}",
        AYUB_ID,
        "Pelanggan Multi",
        design_files=[_pdf(old_id, "desain-lama.pdf")],
        line_items=[
            {"name": "Gelas", "color": "Biru", "quantity": 10, "unit": "pcs"},
            {"name": "Gelas", "color": "Hijau", "quantity": 5, "unit": "pcs"},
        ],
    )
    single = _order(
        f"PO-ONE-{suffix}",
        AYUB_ID,
        "Pelanggan Satu",
        design_files=[_pdf(single_id, "desain-satu.pdf")],
        line_items=[
            {"name": "Gelas", "color": "Biru", "quantity": 10, "unit": "pcs"},
            {"name": "Gelas", "color": "Hijau", "quantity": 4, "unit": "pcs"},
        ],
    )
    bare = _order(f"PO-BARE-{suffix}", AYUB_ID, "Pelanggan Kosong", design_files=[])
    other = _order(f"PO-OTHER-{suffix}", EUIS_ID, "Pelanggan Euis", sales="Euis Risnawati")
    past = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    expired = _order(
        f"PO-EXP-{suffix}",
        AYUB_ID,
        "Pelanggan Kedaluwarsa",
        design_files=[_pdf(f"file-exp-{suffix}", "kedaluwarsa.pdf")],
        customer_approval={
            "approval_id": f"appr-exp-{suffix}",
            "status": "active",
            "design_version": f"file-exp-{suffix}",
            "expires_at": past,
        },
    )
    ids = [multi["id"], single["id"], bare["id"], other["id"], expired["id"]]
    await db.orders.delete_many({"id": {"$in": ids}})
    await db.customer_approvals.delete_many({"order_id": {"$in": ids}})
    await db.orders.insert_many([multi, single, bare, other, expired])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        app.dependency_overrides[current_user] = lambda: _admin()
        first = await client.post(f"/api/orders/{multi['id']}/customer-approval/generate", json={})
        assert first.status_code == 200, first.text
        first_token = first.json()["token"]
        await db.orders.update_one(
            {"id": multi["id"]},
            {"$set": {"design_files": [_pdf(old_id, "desain-lama.pdf"), _pdf(new_id, "desain-baru.pdf")]}},
        )
        second = await client.post(f"/api/orders/{multi['id']}/customer-approval/generate", json={})
        assert second.status_code == 200, second.text
        second_token = second.json()["token"]
        assert second.json()["approval"]["design_version"] == new_id
        one = await client.post(f"/api/orders/{single['id']}/customer-approval/generate", json={})
        assert one.status_code == 200, one.text
        one_token = one.json()["token"]

        app.dependency_overrides[current_user] = lambda: _sales(AYUB_ID, "ayub.approval@eternal.com", "Ayub")
        listed = await client.get("/api/sales-pwa/orders", params={"q": suffix, "limit": 20})
        assert listed.status_code == 200, listed.text
        body = listed.json()
        by_po = {row["po_number"]: row for row in body["orders"]}
        assert set(by_po) >= {multi["po_number"], single["po_number"], bare["po_number"], expired["po_number"]}
        assert other["po_number"] not in by_po

        multi_row = by_po[multi["po_number"]]
        approvals = {row.get("design_name"): row for row in multi_row["design_approvals"]}
        assert set(approvals) == {"desain-baru.pdf", "desain-lama.pdf"}
        current = approvals["desain-baru.pdf"]
        previous = approvals["desain-lama.pdf"]
        assert current["status"] == "active"
        assert current["status_label"] == "Aktif · Menunggu customer"
        assert current["path"].startswith("/customer/design-approval/")
        assert second_token not in current["path"]
        assert first_token not in current["path"]
        assert "path" not in previous
        assert previous["status"] == "revoked"
        assert "items" not in current
        blob = listed.text
        assert "token_hash" not in blob
        assert second_token not in blob
        assert first_token not in blob
        assert one_token not in blob

        opened = await client.get(f"/api/public/customer-approval/{current['path'].rsplit('/', 1)[-1]}")
        assert opened.status_code == 200, opened.text
        assert opened.json()["view"]["design_file"]["name"] == "desain-baru.pdf"
        assert opened.json()["view"]["po_number"] == multi["po_number"]

        single_rows = by_po[single["po_number"]]["design_approvals"]
        assert len(single_rows) == 1
        assert single_rows[0]["design_name"] == "desain-satu.pdf"
        assert single_rows[0]["items"] == [
            {"idx": 0, "label": "Gelas · Biru"},
            {"idx": 1, "label": "Gelas · Hijau"},
        ]
        single_open = await client.get(f"/api/public/customer-approval/{single_rows[0]['path'].rsplit('/', 1)[-1]}")
        assert single_open.status_code == 200, single_open.text
        assert single_open.json()["view"]["design_file"]["name"] == "desain-satu.pdf"
        assert one_token not in single_rows[0]["path"]

        assert by_po[bare["po_number"]]["design_approvals"] == []
        expired_row = by_po[expired["po_number"]]["design_approvals"][0]
        assert expired_row["status"] == "expired"
        assert expired_row["design_name"] == "kedaluwarsa.pdf"
        assert "path" not in expired_row

        again = await client.get("/api/sales-pwa/orders", params={"q": suffix, "limit": 20})
        again_multi = next(row for row in again.json()["orders"] if row["po_number"] == multi["po_number"])
        again_current = next(row for row in again_multi["design_approvals"] if row.get("design_name") == "desain-baru.pdf")
        assert again_current["path"] == current["path"]

        detail = await client.get(f"/api/sales-pwa/orders/{multi['id']}")
        assert detail.status_code == 200, detail.text
        detail_current = next(row for row in detail.json()["order"]["design_approvals"] if row.get("design_name") == "desain-baru.pdf")
        assert detail_current["path"] == current["path"]

        app.dependency_overrides[current_user] = lambda: _sales(EUIS_ID, "euis.approval@eternal.com", "Euis")
        foreign = await client.get("/api/sales-pwa/orders", params={"q": suffix, "limit": 20})
        assert foreign.status_code == 200, foreign.text
        foreign_pos = [row["po_number"] for row in foreign.json()["orders"]]
        assert multi["po_number"] not in foreign_pos
        assert single["po_number"] not in foreign_pos
        assert current["path"] not in foreign.text
        denied = await client.get(f"/api/sales-pwa/orders/{multi['id']}")
        assert denied.status_code == 404
        assert current["path"] not in denied.text
        assert "desain-baru.pdf" not in denied.text

    stored = await db.orders.find_one({"id": multi["id"]}, {"tracking_token": 1})
    assert str(stored.get("tracking_token") or "")
    expired_stored = await db.orders.find_one({"id": expired["id"]}, {"tracking_token": 1})
    assert not str((expired_stored or {}).get("tracking_token") or "").strip()
    await db.customer_approvals.delete_many({"order_id": {"$in": ids}})
    await db.orders.delete_many({"id": {"$in": ids}})
