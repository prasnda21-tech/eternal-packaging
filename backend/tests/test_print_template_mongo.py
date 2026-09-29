"""Desain Cetakan against an isolated Mongo database. Never uses eternal_packaging."""
from __future__ import annotations

import io
import os
import sys
import uuid
from pathlib import Path

import httpx
import pdfplumber
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27018")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
_DB_NAME = f"print_design_isolated_{os.environ.get('PYTEST_XDIST_WORKER') or 'main'}"
assert _DB_NAME != "eternal_packaging"

import server
from server import app, current_user
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.sales_accounting_settings import SETTINGS_KEY
from services.sales_invoices import generate_sales_invoice_pdf_bytes

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[_DB_NAME]
    assert server.db.name == _DB_NAME
    for name in (
        "orders", "sales_invoices", "sales_receipts", "sales_deliveries",
        "print_templates", "print_template_versions", "print_template_assets",         "audit_logs", "chart_of_accounts", "app_settings",
    ):
        await server.db[name].delete_many({})
    await ensure_coa(server.db)
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-print",
        "email": "admin-print@example.com",
        "name": "Admin",
        "role": "admin",
    }
    yield
    app.dependency_overrides.pop(current_user, None)


def _text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _order():
    order_id = str(uuid.uuid4())
    doc = {
        "id": order_id,
        "po_number": f"PRINT-{uuid.uuid4().hex[:6].upper()}",
        "customer": "PT Uji Cetakan",
        "outlet": "Outlet Uji",
        "recipient": "Sari",
        "address": "Jl. Uji Cetakan",
        "delivery_zone": "Jakarta",
        "sales": "Vira",
        "current_stage": 3,
        "status_label": "Payment",
        "line_items": [{
            "line_idx": 0,
            "line_id": "l0",
            "name": "Cup Uji Cetakan",
            "quantity": 100,
            "unit": "pcs",
        }],
        "total_quantity": 100,
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
    }
    await server.db.orders.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def _configure_accounts():
    user = {"id": "t", "name": "t", "role": "admin"}
    receivable = await create_account(server.db, {"code": f"11{uuid.uuid4().hex[:6]}", "name": "Piutang Uji", "account_type": "AREC"}, user=user)
    revenue = await create_account(server.db, {"code": f"41{uuid.uuid4().hex[:6]}", "name": "Pendapatan Uji", "account_type": "REVE"}, user=user)
    tax = await create_account(server.db, {"code": f"21{uuid.uuid4().hex[:6]}", "name": "PPN Uji", "account_type": "OCLY"}, user=user)
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {
            "key": SETTINGS_KEY,
            "receivable_account_id": receivable["id"],
            "revenue_account_id": revenue["id"],
            "output_tax_account_id": tax["id"],
        }},
        upsert=True,
    )


async def test_invoice_receipt_delivery_and_template_flow():
    order = await _order()
    await _configure_accounts()
    async with _client() as client:
        created = await client.post("/api/sales-invoices/issue", json={
            "po_id": order["id"],
            "invoice_date": "2026-09-04",
            "payment_term_days": 0,
            "discount_amount": 5000,
            "ppn_percent": 11,
            "notes": "catatan internal tidak untuk publik",
            "lines": [{"line_idx": 0, "line_id": "l0", "quantity": 10, "unit_price": 1500}],
            "other_charges": [],
        })
        assert created.status_code == 200, created.text
        invoice = created.json()["invoice"]
        assert invoice.get("issue_state") == "issued"

        before = await client.get(f"/api/sales-invoices/{invoice['id']}/pdf")
        assert before.status_code == 200
        assert before.content.startswith(b"%PDF")
        detail = (await client.get(f"/api/sales-invoices/{invoice['id']}")).json()["invoice"]
        assert _text(before.content) == _text(generate_sales_invoice_pdf_bytes(detail))

        receipt = await client.post("/api/sales-receipts", json={"invoice_id": invoice["id"], "amount": 1000})
        assert receipt.status_code == 200, receipt.text
        receipt_id = receipt.json()["receipt"]["id"]
        deleted_receipt = await client.delete(f"/api/sales-receipts/{receipt_id}")
        assert deleted_receipt.status_code == 200, deleted_receipt.text
        assert await server.db.sales_receipts.count_documents({"id": receipt_id}) == 0

        delivery = await client.post("/api/sales-deliveries", json={
            "po_id": order["id"],
            "line_items": [{"line_idx": 0, "line_id": "l0", "qty": 40}],
        })
        assert delivery.status_code == 200, delivery.text
        delivery_id = delivery.json()["delivery"]["id"]
        assert delivery.json()["delivery"]["total_qty"] == 40
        sj = await client.get(f"/api/sales-deliveries/{delivery_id}/pdf")
        assert sj.status_code == 200
        sj_text = _text(sj.content)
        assert "40" in sj_text
        assert "100" not in sj_text

        denied = await client.post("/api/print-templates", json={
            "name": "Ditolak",
            "document_type": "sales_invoice",
            "audience": "customer",
        })
        # admin is allowed; switch after capturing the happy path below
        assert denied.status_code == 200, denied.text
        template = denied.json()["template"]
        design = template["design"]
        design["bands"]["title"]["elements"].append({
            "id": "mark",
            "type": "text",
            "band": "title",
            "x_mm": 0,
            "y_mm": 8,
            "w_mm": 50,
            "h_mm": 5,
            "z": 4,
            "required": False,
            "props": {"text": "VERSI-DRAF-UJI", "font": "Helvetica", "font_size": 8, "wrap": True},
        })
        saved = await client.put(
            f"/api/print-templates/{template['id']}/draft",
            json={"design": design, "expected_revision": template["revision"]},
        )
        assert saved.status_code == 200, saved.text
        still_default = await client.get(f"/api/sales-invoices/{invoice['id']}/pdf")
        assert "VERSI-DRAF-UJI" not in _text(still_default.content)
        conflict = await client.put(
            f"/api/print-templates/{template['id']}/draft",
            json={"design": design, "expected_revision": template["revision"]},
        )
        assert conflict.status_code == 409

        activated = await client.post(
            f"/api/print-templates/{template['id']}/activate",
            json={"expected_revision": saved.json()["template"]["revision"]},
        )
        assert activated.status_code == 200, activated.text
        made_default = await client.post(
            f"/api/print-templates/{template['id']}/default",
            json={"expected_revision": activated.json()["template"]["revision"]},
        )
        assert made_default.status_code == 200, made_default.text
        custom = await client.get(f"/api/sales-invoices/{invoice['id']}/pdf")
        assert "VERSI-DRAF-UJI" in _text(custom.content)
        later = dict(design)
        later["bands"]["title"]["elements"][-1]["props"]["text"] = "VERSI-DRAF-KEDUA"
        second = await client.put(
            f"/api/print-templates/{template['id']}/draft",
            json={"design": later, "expected_revision": made_default.json()["template"]["revision"]},
        )
        assert second.status_code == 200, second.text
        unchanged = await client.get(f"/api/sales-invoices/{invoice['id']}/pdf")
        unchanged_text = _text(unchanged.content)
        assert "VERSI-DRAF-UJI" in unchanged_text
        assert "VERSI-DRAF-KEDUA" not in unchanged_text

        internal = await client.post("/api/print-templates", json={
            "name": "Internal",
            "document_type": "sales_invoice",
            "audience": "internal",
        })
        assert internal.status_code == 200, internal.text
        internal_template = internal.json()["template"]
        internal_design = internal_template["design"]
        internal_design["bands"]["title"]["elements"].append({
            "id": "secret",
            "type": "text",
            "band": "title",
            "x_mm": 0,
            "y_mm": 8,
            "w_mm": 70,
            "h_mm": 5,
            "z": 5,
            "required": False,
            "props": {"text": "RAHASIA-INTERNAL-XYZ", "font": "Helvetica", "font_size": 8, "wrap": True},
        })
        internal_saved = await client.put(
            f"/api/print-templates/{internal_template['id']}/draft",
            json={"design": internal_design, "expected_revision": internal_template["revision"]},
        )
        internal_on = await client.post(
            f"/api/print-templates/{internal_template['id']}/activate",
            json={"expected_revision": internal_saved.json()["template"]["revision"]},
        )
        assert internal_on.status_code == 200, internal_on.text
        await client.post(
            f"/api/print-templates/{internal_template['id']}/default",
            json={"expected_revision": internal_on.json()["template"]["revision"]},
        )
        from services.customer_tracking import ensure_tracking_token
        token = await ensure_tracking_token(server.db, order)
        public = await client.get(
            f"/api/public/tracking/{token}/invoices/{invoice['id']}/pdf",
            params={"template_id": internal_template["id"]},
        )
        assert public.status_code == 200, public.text
        public_text = _text(public.content)
        assert "RAHASIA-INTERNAL-XYZ" not in public_text
        assert "catatan internal tidak untuk publik" not in public_text

        removed = await client.delete(f"/api/sales-invoices/{invoice['id']}")
        assert removed.status_code == 200, removed.text
        hidden = await client.get(f"/api/public/tracking/{token}/invoices/{invoice['id']}/pdf")
        assert hidden.status_code == 404


async def test_manage_permission_is_enforced():
    app.dependency_overrides[current_user] = lambda: {
        "id": "crm-1",
        "email": "crm-print@example.com",
        "name": "CRM",
        "role": "crm",
        "permissions_override": {},
    }
    async with _client() as client:
        denied = await client.post("/api/print-templates", json={
            "name": "Tidak",
            "document_type": "sales_invoice",
            "audience": "customer",
        })
        assert denied.status_code == 403
        listed = await client.get("/api/print-templates")
        assert listed.status_code == 403
