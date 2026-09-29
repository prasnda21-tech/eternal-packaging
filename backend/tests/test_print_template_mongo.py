"""Desain Cetakan against an isolated Mongo database. Never uses eternal_packaging."""
from __future__ import annotations

import asyncio
import io
import os
import sys
import uuid
from pathlib import Path

import httpx
import pdfplumber
import pytest
from fastapi import HTTPException

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
from services import print_templates as svc
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
        "print_templates", "print_template_versions", "print_template_assets", "print_template_defaults",
        "audit_logs", "chart_of_accounts", "app_settings",
    ):
        await server.db[name].delete_many({})
    await ensure_coa(server.db)
    svc._before_revision_write = None
    svc._before_builtin_insert = None
    svc._before_pointer_write = None
    svc._before_pointer_revert = None
    svc._before_archive_rollback = None
    svc._indexed_databases.clear()
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-print",
        "email": "admin-print@example.com",
        "name": "Admin",
        "role": "admin",
    }
    yield
    svc._before_revision_write = None
    svc._before_builtin_insert = None
    svc._before_pointer_write = None
    svc._before_pointer_revert = None
    svc._before_archive_rollback = None
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


async def _hold_two_writers():
    state = {"count": 0, "release": asyncio.Event()}

    async def hold(_template_id):
        state["count"] += 1
        if state["count"] >= 2:
            state["release"].set()
        await state["release"].wait()

    return hold


def _title_text(design: dict) -> str:
    return design["bands"]["title"]["elements"][0]["props"]["text"]


async def test_two_saves_that_passed_the_read_check_do_not_both_commit():
    created = await svc.create_template(server.db, {
        "name": "Balapan",
        "document_type": "sales_invoice",
        "audience": "customer",
    }, {"id": "admin-print", "email": "admin-print@example.com", "name": "Admin", "role": "admin"})
    revision = created["revision"]
    first = created["design"]
    left = dict(first)
    right = dict(first)
    left = __import__("copy").deepcopy(first)
    right = __import__("copy").deepcopy(first)
    left["bands"]["title"]["elements"][0]["props"]["text"] = "PEMENANG-KIRI"
    right["bands"]["title"]["elements"][0]["props"]["text"] = "PEMENANG-KANAN"
    svc._before_revision_write = await _hold_two_writers()
    try:
        results = await asyncio.gather(
            svc.save_draft(server.db, created["id"], left, revision, {"name": "Admin", "role": "admin"}),
            svc.save_draft(server.db, created["id"], right, revision, {"name": "Admin", "role": "admin"}),
            return_exceptions=True,
        )
    finally:
        svc._before_revision_write = None
    wins = [row for row in results if not isinstance(row, Exception)]
    losses = [row for row in results if isinstance(row, HTTPException)]
    assert len(wins) == 1 and len(losses) == 1
    assert losses[0].status_code == 409
    stored = await server.db.print_templates.find_one({"id": created["id"]}, {"_id": 0})
    assert _title_text(stored["design"]) == _title_text(wins[0]["design"])
    assert _title_text(stored["design"]) in {"PEMENANG-KIRI", "PEMENANG-KANAN"}
    assert await server.db.print_template_versions.count_documents({
        "template_id": created["id"],
        "action": "save_draft",
    }) == 1


async def test_activate_archive_and_restore_also_reject_the_losing_write():
    actor = {"id": "admin-print", "email": "admin-print@example.com", "name": "Admin", "role": "admin"}
    created = await svc.create_template(server.db, {
        "name": "Tulis",
        "document_type": "sales_delivery",
        "audience": "customer",
    }, actor)
    saved = await svc.save_draft(server.db, created["id"], created["design"], created["revision"], actor)
    svc._before_revision_write = await _hold_two_writers()
    try:
        activated = await asyncio.gather(
            svc.activate_template(server.db, created["id"], saved["revision"], actor),
            svc.activate_template(server.db, created["id"], saved["revision"], actor),
            return_exceptions=True,
        )
    finally:
        svc._before_revision_write = None
    assert sum(isinstance(row, HTTPException) and row.status_code == 409 for row in activated) == 1
    assert await server.db.print_template_versions.count_documents({
        "template_id": created["id"],
        "action": "activate",
    }) == 1
    current = await svc.get_template(server.db, created["id"])
    version = (await svc.list_versions(server.db, created["id"]))
    saved_version = next(row for row in version if row["action"] == "activate")
    svc._before_revision_write = await _hold_two_writers()
    try:
        restored = await asyncio.gather(
            svc.restore_version(server.db, created["id"], saved_version["id"], current["revision"], actor),
            svc.restore_version(server.db, created["id"], saved_version["id"], current["revision"], actor),
            return_exceptions=True,
        )
    finally:
        svc._before_revision_write = None
    assert sum(isinstance(row, HTTPException) and row.status_code == 409 for row in restored) == 1
    assert await server.db.print_template_versions.count_documents({
        "template_id": created["id"],
        "action": "restore",
    }) == 1
    current = await svc.get_template(server.db, created["id"])
    svc._before_revision_write = await _hold_two_writers()
    try:
        archived = await asyncio.gather(
            svc.archive_template(server.db, created["id"], current["revision"], actor),
            svc.archive_template(server.db, created["id"], current["revision"], actor),
            return_exceptions=True,
        )
    finally:
        svc._before_revision_write = None
    assert sum(isinstance(row, HTTPException) and row.status_code == 409 for row in archived) == 1
    assert await server.db.print_template_versions.count_documents({
        "template_id": created["id"],
        "action": "archive",
    }) == 1
    stored = await server.db.print_templates.find_one({"id": created["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == "archived"


async def test_set_default_losing_write_does_not_record_history():
    actor = {"id": "admin-print", "email": "admin-print@example.com", "name": "Admin", "role": "admin"}
    created = await svc.create_template(server.db, {
        "name": "Default balapan",
        "document_type": "sales_invoice",
        "audience": "internal",
    }, actor)
    activated = await svc.activate_template(server.db, created["id"], created["revision"], actor)
    svc._before_revision_write = await _hold_two_writers()
    try:
        results = await asyncio.gather(
            svc.set_default(server.db, created["id"], activated["revision"], actor),
            svc.set_default(server.db, created["id"], activated["revision"], actor),
            return_exceptions=True,
        )
    finally:
        svc._before_revision_write = None
    assert sum(isinstance(row, HTTPException) and row.status_code == 409 for row in results) == 1
    assert await server.db.print_template_versions.count_documents({
        "template_id": created["id"],
        "action": "set_default",
    }) == 1
    pointer = await server.db.print_template_defaults.find_one({"id": "default:sales_invoice:internal"}, {"_id": 0})
    assert pointer["template_id"] == created["id"]


async def test_default_pointer_stays_singular_under_races():
    actor = {"id": "admin-print", "email": "admin-print@example.com", "name": "Admin", "role": "admin"}

    async def active_template(name: str):
        created = await svc.create_template(server.db, {
            "name": name,
            "document_type": "sales_invoice",
            "audience": "customer",
        }, actor)
        return await svc.activate_template(server.db, created["id"], created["revision"], actor)

    left = await active_template("Kiri")
    right = await active_template("Kanan")
    raced = await asyncio.gather(
        svc.set_default(server.db, left["id"], left["revision"], actor),
        svc.set_default(server.db, right["id"], right["revision"], actor),
        return_exceptions=True,
    )
    wins = [row for row in raced if not isinstance(row, Exception)]
    losses = [row for row in raced if isinstance(row, HTTPException)]
    assert len(wins) + len(losses) == 2
    assert wins
    assert all(row.status_code == 409 for row in losses)
    pointer = await server.db.print_template_defaults.find_one({"id": "default:sales_invoice:customer"}, {"_id": 0})
    assert pointer["template_id"] in {row["id"] for row in wins}
    resolved = await svc.resolve_template(server.db, "sales_invoice", "customer")
    assert resolved["id"] == pointer["template_id"]
    listed = await svc.list_templates(server.db, document_type="sales_invoice")
    defaults = [row["id"] for row in listed if row["audience"] == "customer" and row["is_default"]]
    assert defaults == [pointer["template_id"]]
    options = await svc.active_print_options(server.db, "sales_invoice")
    menu_defaults = [row["id"] for row in options if row["audience"] == "customer" and row["is_default"]]
    assert menu_defaults == [pointer["template_id"]]

    async def fail_pointer(_db, _doc, expected_generation=None):
        raise RuntimeError("pointer write failed")

    previous = pointer["template_id"]
    other = right if previous == left["id"] else left
    fresh = await svc.get_template(server.db, other["id"])
    before_failures = await server.db.print_template_versions.count_documents({
        "template_id": fresh["id"],
        "action": "set_default",
    })
    original_writer = svc._write_pointer
    svc._write_pointer = fail_pointer
    try:
        with pytest.raises(HTTPException) as failure:
            await svc.set_default(server.db, fresh["id"], fresh["revision"], actor)
        assert failure.value.status_code == 500
    finally:
        svc._write_pointer = original_writer
    still = await server.db.print_template_defaults.find_one({"id": "default:sales_invoice:customer"}, {"_id": 0})
    assert still["template_id"] == previous
    assert (await svc.resolve_template(server.db, "sales_invoice", "customer"))["id"] == previous
    assert await server.db.print_template_versions.count_documents({
        "template_id": fresh["id"],
        "action": "set_default",
    }) == before_failures


async def test_archive_racing_a_default_switch_keeps_a_usable_default():
    actor = {"id": "admin-print", "email": "admin-print@example.com", "name": "Admin", "role": "admin"}
    created = await svc.create_template(server.db, {
        "name": "Hampir default",
        "document_type": "sales_invoice",
        "audience": "customer",
    }, actor)
    activated = await svc.activate_template(server.db, created["id"], created["revision"], actor)

    async def switch_during_archive(template_id):
        svc._before_revision_write = None
        await svc.set_default(server.db, template_id, activated["revision"], actor)

    svc._before_revision_write = switch_during_archive
    try:
        with pytest.raises(HTTPException) as raced:
            await svc.archive_template(server.db, activated["id"], activated["revision"], actor)
        assert raced.value.status_code == 409
    finally:
        svc._before_revision_write = None
    stored = await server.db.print_templates.find_one({"id": activated["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == "active"
    resolved = await svc.resolve_template(server.db, "sales_invoice", "customer")
    assert resolved["id"] == activated["id"]
    assert resolved["status"] == "active"
    assert await server.db.print_template_versions.count_documents({
        "template_id": activated["id"],
        "action": "archive",
    }) == 0
    fresh = await svc.get_template(server.db, activated["id"])
    with pytest.raises(HTTPException) as retry:
        await svc.archive_template(server.db, activated["id"], fresh["revision"], actor)
    assert retry.value.status_code == 400
    assert (await svc.resolve_template(server.db, "sales_invoice", "customer"))["id"] == activated["id"]


def _image_count(data: bytes) -> int:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return sum(len(page.images or []) for page in pdf.pages)


def _png() -> bytes:
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (220, 20, 20)).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_uploaded_logo_is_embedded_and_a_missing_asset_is_rejected():
    png = _png()
    async with _client() as client:
        uploaded = await client.post(
            "/api/print-templates/assets",
            files={"file": ("logo.png", png, "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        asset_id = uploaded.json()["asset"]["id"]
        created = await client.post("/api/print-templates", json={
            "name": "Dengan logo",
            "document_type": "sales_invoice",
            "audience": "customer",
        })
        template = created.json()["template"]
        design = template["design"]
        design["bands"]["title"]["elements"].append({
            "id": "logo",
            "type": "image",
            "band": "title",
            "x_mm": 0,
            "y_mm": 8,
            "w_mm": 12,
            "h_mm": 8,
            "z": 3,
            "required": False,
            "props": {"role": "logo", "asset_id": asset_id},
        })
        saved = await client.put(
            f"/api/print-templates/{template['id']}/draft",
            json={"design": design, "expected_revision": template["revision"]},
        )
        assert saved.status_code == 200, saved.text
        preview = await client.post(f"/api/print-templates/{template['id']}/preview", json={"design": design})
        assert preview.status_code == 200, preview.text
        assert b"/Subtype /Image" in preview.content
        assert _image_count(preview.content) >= 1
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
        order = await _order()
        await _configure_accounts()
        issued = await client.post("/api/sales-invoices/issue", json={
            "po_id": order["id"],
            "invoice_date": "2026-09-04",
            "payment_term_days": 0,
            "discount_amount": 0,
            "ppn_percent": 0,
            "notes": "",
            "lines": [{"line_idx": 0, "line_id": "l0", "quantity": 1, "unit_price": 1000}],
            "other_charges": [],
        })
        assert issued.status_code == 200, issued.text
        printed = await client.get(f"/api/sales-invoices/{issued.json()['invoice']['id']}/pdf")
        assert printed.status_code == 200, printed.text
        assert b"/Subtype /Image" in printed.content
        assert _image_count(printed.content) >= 1
        from services.customer_tracking import ensure_tracking_token
        token = await ensure_tracking_token(server.db, order)
        public = await client.get(f"/api/public/tracking/{token}/invoices/{issued.json()['invoice']['id']}/pdf")
        assert public.status_code == 200, public.text
        assert b"/Subtype /Image" in public.content
        assert _image_count(public.content) >= 1

        linked = await client.post(f"/api/print-templates/{template['id']}/preview", json={"design": {
            **design,
            "bands": {
                **design["bands"],
                "title": {
                    **design["bands"]["title"],
                    "elements": [
                        *design["bands"]["title"]["elements"][:-1],
                        {
                            **design["bands"]["title"]["elements"][-1],
                            "props": {"role": "logo", "asset_id": "", "url": "https://example.invalid/logo.png"},
                        },
                    ],
                },
            },
        }})
        assert linked.status_code == 400
        assert "URL" in linked.text or "tidak diizinkan" in linked.text

        missing = design
        missing["bands"]["title"]["elements"][-1]["props"]["asset_id"] = "aset-hilang"
        rejected = await client.post(f"/api/print-templates/{template['id']}/preview", json={"design": missing})
        assert rejected.status_code == 400
        assert "tidak ditemukan" in rejected.text
        blocked = await client.put(
            f"/api/print-templates/{template['id']}/draft",
            json={"design": missing, "expected_revision": made_default.json()["template"]["revision"]},
        )
        assert blocked.status_code == 200
        not_active = await client.post(
            f"/api/print-templates/{template['id']}/activate",
            json={"expected_revision": blocked.json()["template"]["revision"]},
        )
        assert not_active.status_code == 400
        assert "tidak ditemukan" in not_active.text
        stored = await server.db.print_templates.find_one({"id": template["id"]}, {"_id": 0, "status": 1, "active_design": 1})
        assert stored["status"] == "active"
        assert stored["active_design"]["bands"]["title"]["elements"][-1]["props"]["asset_id"] == asset_id
        still_printed = await client.get(f"/api/sales-invoices/{issued.json()['invoice']['id']}/pdf")
        assert still_printed.status_code == 200
        assert _image_count(still_printed.content) >= 1


def _hold_same_id(expected_id: str):
    state = {"count": 0, "release": asyncio.Event()}

    async def hold(template_id, *_args):
        if template_id != expected_id:
            return
        state["count"] += 1
        if state["count"] >= 2:
            state["release"].set()
        await state["release"].wait()

    return hold


async def test_two_first_requests_keep_one_builtin_and_one_default():
    svc._before_builtin_insert = _hold_same_id("builtin-sales-invoice-customer")
    try:
        await asyncio.gather(
            svc.list_templates(server.db),
            svc.resolve_template(server.db, "sales_invoice", "customer"),
        )
    finally:
        svc._before_builtin_insert = None
    for template_id in svc.BUILTIN_IDS.values():
        assert await server.db.print_templates.count_documents({"id": template_id}) == 1
        stored = await server.db.print_templates.find_one({"id": template_id})
        assert stored["_id"] == template_id
    for document_type, audience in svc.BUILTIN_IDS:
        pointer_id = f"default:{document_type}:{audience}"
        assert await server.db.print_template_defaults.count_documents({"id": pointer_id}) == 1
        pointer = await server.db.print_template_defaults.find_one({"id": pointer_id})
        assert pointer["_id"] == pointer_id
        assert pointer["template_id"] == svc.BUILTIN_IDS[(document_type, audience)]
    template_indexes = await server.db.print_templates.index_information()
    default_indexes = await server.db.print_template_defaults.index_information()
    assert template_indexes["print_template_id_unique"]["unique"] is True
    assert default_indexes["print_template_default_id_unique"]["unique"] is True
    resolved = await svc.resolve_template(server.db, "sales_invoice", "customer")
    assert resolved["id"] == "builtin-sales-invoice-customer"
    assert resolved["status"] == "active"


async def test_pointer_after_archive_does_not_keep_an_archived_default():
    actor = {"id": "admin-print", "email": "admin-print@example.com", "name": "Admin", "role": "admin"}
    created = await svc.create_template(server.db, {
        "name": "Nanti arsip",
        "document_type": "sales_invoice",
        "audience": "customer",
    }, actor)
    activated = await svc.activate_template(server.db, created["id"], created["revision"], actor)

    async def archive_before_pointer(template_id):
        svc._before_pointer_write = None
        fresh = await svc.get_template(server.db, template_id)
        await svc.archive_template(server.db, template_id, fresh["revision"], actor)

    svc._before_pointer_write = archive_before_pointer
    try:
        with pytest.raises(HTTPException) as lost:
            await svc.set_default(server.db, activated["id"], activated["revision"], actor)
        assert lost.value.status_code == 409
    finally:
        svc._before_pointer_write = None
    stored = await server.db.print_templates.find_one({"id": activated["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == "archived"
    pointer = await server.db.print_template_defaults.find_one({"id": "default:sales_invoice:customer"})
    assert pointer["template_id"] != activated["id"]
    assert await server.db.print_template_versions.count_documents({
        "template_id": activated["id"], "action": "set_default",
    }) == 0
    assert await server.db.print_template_versions.count_documents({
        "template_id": activated["id"], "action": "archive",
    }) == 1
    resolved = await svc.resolve_template(server.db, "sales_invoice", "customer")
    assert resolved["status"] == "active"
    assert resolved["id"] != activated["id"]
    fresh = await svc.get_template(server.db, activated["id"])
    with pytest.raises(HTTPException) as retry:
        await svc.set_default(server.db, activated["id"], fresh["revision"], actor)
    assert retry.value.status_code == 400
    again = await server.db.print_template_defaults.find_one({"id": "default:sales_invoice:customer"})
    assert again["template_id"] == pointer["template_id"]


async def test_default_winner_rejects_archive_and_retry():
    actor = {"id": "admin-print", "email": "admin-print@example.com", "name": "Admin", "role": "admin"}
    created = await svc.create_template(server.db, {
        "name": "Pemenang default",
        "document_type": "sales_delivery",
        "audience": "customer",
    }, actor)
    activated = await svc.activate_template(server.db, created["id"], created["revision"], actor)
    chosen = await svc.set_default(server.db, activated["id"], activated["revision"], actor)
    fresh = await svc.get_template(server.db, chosen["id"])
    with pytest.raises(HTTPException) as blocked:
        await svc.archive_template(server.db, chosen["id"], fresh["revision"], actor)
    assert blocked.value.status_code == 400
    again = await svc.get_template(server.db, chosen["id"])
    with pytest.raises(HTTPException) as retry:
        await svc.archive_template(server.db, chosen["id"], again["revision"], actor)
    assert retry.value.status_code == 400
    stored = await server.db.print_templates.find_one({"id": chosen["id"]}, {"_id": 0, "status": 1})
    assert stored["status"] == "active"
    pointer = await server.db.print_template_defaults.find_one({"id": "default:sales_delivery:customer"})
    assert pointer["template_id"] == chosen["id"]
    assert await server.db.print_template_versions.count_documents({
        "template_id": chosen["id"], "action": "archive",
    }) == 0
    resolved = await svc.resolve_template(server.db, "sales_delivery", "customer")
    assert resolved["id"] == chosen["id"]
    assert resolved["status"] == "active"


async def test_archive_rollback_requires_revision_ownership():
    actor = {"id": "admin-print", "email": "admin-print@example.com", "name": "Admin", "role": "admin"}
    created = await svc.create_template(server.db, {
        "name": "Pagar revisi",
        "document_type": "sales_invoice",
        "audience": "internal",
    }, actor)
    activated = await svc.activate_template(server.db, created["id"], created["revision"], actor)
    owned = activated["revision"] + 1

    async def point_during_archive(template_id):
        svc._before_revision_write = None
        await svc._write_pointer(server.db, {
            "id": template_id,
            "document_type": "sales_invoice",
            "audience": "internal",
        })

    async def steal_revision(template_id, revision):
        assert revision == owned
        result = await server.db.print_templates.update_one(
            {"id": template_id, "revision": revision, "status": "archived"},
            {"$set": {"revision": revision + 7}},
        )
        assert result.matched_count == 1

    svc._before_revision_write = point_during_archive
    svc._before_archive_rollback = steal_revision
    try:
        with pytest.raises(HTTPException) as failure:
            await svc.archive_template(server.db, activated["id"], activated["revision"], actor)
        assert failure.value.status_code == 409
    finally:
        svc._before_revision_write = None
        svc._before_archive_rollback = None
    stored = await server.db.print_templates.find_one({"id": activated["id"]}, {"_id": 0, "status": 1, "revision": 1})
    assert stored["status"] == "archived"
    assert stored["revision"] == owned + 7
    pointer = await server.db.print_template_defaults.find_one({"id": "default:sales_invoice:internal"})
    assert pointer["template_id"] != activated["id"]
    assert await server.db.print_template_versions.count_documents({
        "template_id": activated["id"], "action": "archive",
    }) == 0
    resolved = await svc.resolve_template(server.db, "sales_invoice", "internal")
    assert resolved["status"] == "active"
    assert resolved["id"] != activated["id"]


async def _active_named(actor, name, audience="customer"):
    created = await svc.create_template(server.db, {
        "name": name,
        "document_type": "sales_invoice",
        "audience": audience,
    }, actor)
    return await svc.activate_template(server.db, created["id"], created["revision"], actor)


async def test_stale_pointer_snapshot_does_not_replace_a_later_default():
    actor = {"id": "admin-print", "email": "admin-print@example.com", "name": "Admin", "role": "admin"}
    initial = await _active_named(actor, "X")
    initial = await svc.set_default(server.db, initial["id"], initial["revision"], actor)
    waiting = await _active_named(actor, "A")
    other = await _active_named(actor, "Y")

    async def archive_then_choose_other(template_id):
        svc._before_pointer_write = None
        fresh = await svc.get_template(server.db, template_id)
        await svc.archive_template(server.db, template_id, fresh["revision"], actor)
        current = await svc.get_template(server.db, other["id"])
        await svc.set_default(server.db, other["id"], current["revision"], actor)

    svc._before_pointer_write = archive_then_choose_other
    try:
        with pytest.raises(HTTPException) as lost:
            await svc.set_default(server.db, waiting["id"], waiting["revision"], actor)
        assert lost.value.status_code == 409
    finally:
        svc._before_pointer_write = None
    pointer = await server.db.print_template_defaults.find_one({"id": "default:sales_invoice:customer"})
    assert pointer["template_id"] == other["id"]
    assert pointer["template_id"] != initial["id"]
    assert await server.db.print_template_versions.count_documents({
        "template_id": other["id"], "action": "set_default",
    }) == 1
    assert await server.db.print_template_versions.count_documents({
        "template_id": waiting["id"], "action": "set_default",
    }) == 0
    assert await server.db.print_template_versions.count_documents({
        "template_id": initial["id"], "action": "set_default",
    }) == 1
    fresh = await svc.get_template(server.db, waiting["id"])
    with pytest.raises(HTTPException) as retry:
        await svc.set_default(server.db, waiting["id"], fresh["revision"], actor)
    assert retry.value.status_code == 400
    again = await server.db.print_template_defaults.find_one({"id": "default:sales_invoice:customer"})
    assert again["template_id"] == other["id"]
    assert await server.db.print_template_versions.count_documents({
        "template_id": other["id"], "action": "set_default",
    }) == 1
    resolved = await svc.resolve_template(server.db, "sales_invoice", "customer")
    assert resolved["id"] == other["id"]
    assert resolved["status"] == "active"


async def test_pointer_revert_does_not_restore_a_stale_snapshot():
    actor = {"id": "admin-print", "email": "admin-print@example.com", "name": "Admin", "role": "admin"}
    initial = await _active_named(actor, "X-lama")
    await svc.set_default(server.db, initial["id"], initial["revision"], actor)
    waiting = await _active_named(actor, "A-tertulis")
    other = await _active_named(actor, "Y-baru")

    async def archive_before_write(template_id):
        svc._before_pointer_write = None
        fresh = await svc.get_template(server.db, template_id)
        await svc.archive_template(server.db, template_id, fresh["revision"], actor)

    async def choose_other_before_revert(_template_id, _generation):
        svc._before_pointer_revert = None
        current = await svc.get_template(server.db, other["id"])
        await svc.set_default(server.db, other["id"], current["revision"], actor)

    svc._before_pointer_write = archive_before_write
    svc._before_pointer_revert = choose_other_before_revert
    try:
        with pytest.raises(HTTPException) as lost:
            await svc.set_default(server.db, waiting["id"], waiting["revision"], actor)
        assert lost.value.status_code == 409
    finally:
        svc._before_pointer_write = None
        svc._before_pointer_revert = None
    pointer = await server.db.print_template_defaults.find_one({"id": "default:sales_invoice:customer"})
    assert pointer["template_id"] == other["id"]
    assert await server.db.print_template_versions.count_documents({
        "template_id": other["id"], "action": "set_default",
    }) == 1
    assert await server.db.print_template_versions.count_documents({
        "template_id": waiting["id"], "action": "set_default",
    }) == 0
    assert await server.db.print_template_versions.count_documents({
        "template_id": initial["id"], "action": "set_default",
    }) == 1
