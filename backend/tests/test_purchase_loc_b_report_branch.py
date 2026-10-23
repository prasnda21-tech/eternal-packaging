"""PUR-LOC-01 LOC-B: purchase report branch mapping for master branch_id + filter."""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_pur_loc_b_report_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.purchase_reports import (
    BRANCH_JOGJA,
    BRANCH_SEMARANG,
    PERM_EXPORT,
    PERM_VIEW,
    _branch_for_invoice,
)
from services.sales_reports import BRANCH_SEMUA

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    names = (
        "purchase_invoices", "purchase_orders", "purchase_requests",
        "purchase_goods_receipts", "users", "purchase_report_memorized", "app_settings",
    )
    for name in names:
        await server.db[name].delete_many({})
    yield
    for name in names:
        await server.db[name].delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _user(**extra):
    uid = extra.pop("id", None) or f"u-{uuid.uuid4().hex[:8]}"
    doc = {
        "id": uid,
        "email": extra.pop("email", None) or f"{uid}@example.com",
        "name": "viewer",
        "role": "designer",
        "status": "active",
        "user_id": uid,
        "permissions_override": {PERM_VIEW: True, PERM_EXPORT: True},
    }
    doc.update(extra)
    return doc


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _invoice(**fields):
    doc = {
        "id": str(uuid.uuid4()),
        "invoice_number": fields.pop("invoice_number", f"FPB-{uuid.uuid4().hex[:6]}"),
        "invoice_date": fields.pop("invoice_date", "2026-03-10"),
        "purchase_order_id": fields.pop("purchase_order_id", "po-1"),
        "po_number": fields.pop("po_number", "PB-20260310-001"),
        "supplier": fields.pop("supplier", "Supplier A"),
        "subtotal": fields.pop("subtotal", 1000),
        "grand_total": fields.pop("grand_total", 1000),
        "cancelled": False,
        "precedes_goods_receipt": False,
        "invoice_source": "purchase_order",
        "payment_status": "UNPAID",
        "accounting_status": "not_posted",
    }
    doc.update(fields)
    await server.db.purchase_invoices.insert_one(doc)
    return doc


async def _show(client, **extra):
    body = {
        "report_id": "daftar-faktur-pembelian",
        "date_from": "01/03/2026",
        "date_to": "31/03/2026",
        "branch": "semua",
        "page": 1,
        "page_size": 50,
    }
    body.update(extra)
    return await client.post("/api/laporan/pembelian/tampilkan", json=body)


async def test_resolver_master_id_plus_code_maps_jogja():
    """1. Invoice baru: branch_id master YGY + branch_code YGY → jogja."""
    inv = {"branch_id": "branch-ygy", "branch_code": "YGY", "branch_name": "Yogyakarta"}
    assert _branch_for_invoice(inv, None, None) == BRANCH_JOGJA


async def test_resolver_unmapped_id_plus_name_yogyakarta():
    """2. branch_id tidak dikenali + branch_name Yogyakarta → jogja."""
    inv = {"branch_id": "branch-ygy", "branch_name": "Yogyakarta"}
    assert _branch_for_invoice(inv, None, None) == BRANCH_JOGJA


async def test_resolver_unmapped_invoice_falls_back_to_po():
    """3. branch_id tidak dikenali + PO cabang valid → fallback PO."""
    inv = {"branch_id": "branch-ygy"}
    po = {"branch_id": "branch-ygy", "branch_code": "YGY", "branch_name": "Yogyakarta"}
    assert _branch_for_invoice(inv, po, None) == BRANCH_JOGJA


async def test_resolver_legacy_empty_location_stays_empty():
    """4. Invoice legacy tanpa lokasi → empty (perilaku lama)."""
    assert _branch_for_invoice({}, None, None) == ""
    assert _branch_for_invoice({"branch_id": ""}, {"supplier": "X"}, None) == ""


async def test_resolver_other_branch_not_jogja():
    """5. Cabang lain → tidak salah masuk filter Jogja."""
    inv = {"branch_id": "branch-smg", "branch_code": "SMG", "branch_name": "Semarang"}
    assert _branch_for_invoice(inv, None, None) == BRANCH_SEMARANG
    assert _branch_for_invoice(inv, None, None) != BRANCH_JOGJA


async def test_resolver_unknown_master_id_alone_not_invented_as_ygy():
    """6. Lokasi tidak diketahui → tidak otomatis dianggap YGY/jogja."""
    inv = {"branch_id": "branch-ygy"}
    assert _branch_for_invoice(inv, None, None) == ""
    inv2 = {"branch_id": "branch-unknown-xyz"}
    assert _branch_for_invoice(inv2, None, None) == ""


async def test_resolver_legacy_label_branch_id_still_works():
    """Legacy report docs with jogja/semarang branch_id keep working."""
    assert _branch_for_invoice({"branch_id": "jogja"}, None, None) == BRANCH_JOGJA
    assert _branch_for_invoice({"branch_id": "semarang"}, None, None) == BRANCH_SEMARANG


async def test_report_filter_jogja_and_other_branch_and_totals():
    """API: cases 1,5,7,8 — Jogja filter, other branch excluded, semua keeps all, totals stable."""
    await server.db.purchase_orders.insert_many([
        {
            "id": "po-ygy",
            "po_number": "PB-YGY",
            "supplier": "S-YGY",
            "branch_id": "branch-ygy",
            "branch_code": "YGY",
            "branch_name": "Yogyakarta",
        },
        {
            "id": "po-smg",
            "po_number": "PB-SMG",
            "supplier": "S-SMG",
            "branch_id": "branch-smg",
            "branch_code": "SMG",
            "branch_name": "Semarang",
        },
        {"id": "po-legacy", "po_number": "PB-LEG", "supplier": "S-LEG"},
    ])
    await _invoice(
        invoice_number="FPB-YGY-NEW",
        purchase_order_id="po-ygy",
        po_number="PB-YGY",
        supplier="S-YGY",
        branch_id="branch-ygy",
        branch_code="YGY",
        branch_name="Yogyakarta",
        grand_total=1500,
        subtotal=1500,
    )
    await _invoice(
        invoice_number="FPB-YGY-NAME",
        purchase_order_id="po-ygy",
        po_number="PB-YGY",
        supplier="S-YGY",
        branch_id="branch-ygy",
        branch_name="Yogyakarta",
        grand_total=500,
        subtotal=500,
    )
    await _invoice(
        invoice_number="FPB-YGY-POFB",
        purchase_order_id="po-ygy",
        po_number="PB-YGY",
        supplier="S-YGY",
        branch_id="branch-ygy",
        grand_total=250,
        subtotal=250,
    )
    await _invoice(
        invoice_number="FPB-SMG",
        purchase_order_id="po-smg",
        po_number="PB-SMG",
        supplier="S-SMG",
        branch_id="branch-smg",
        branch_code="SMG",
        branch_name="Semarang",
        grand_total=900,
        subtotal=900,
    )
    await _invoice(
        invoice_number="FPB-LEGACY-EMPTY",
        purchase_order_id="po-legacy",
        po_number="PB-LEG",
        supplier="S-LEG",
        grand_total=100,
        subtotal=100,
    )
    await _invoice(
        invoice_number="FPB-UNKNOWN-ID",
        purchase_order_id="po-legacy",
        po_number="PB-LEG",
        supplier="S-LEG",
        branch_id="branch-unknown-xyz",
        grand_total=75,
        subtotal=75,
    )

    async with await client_for(_user()) as client:
        jogja = await _show(client, branch="jogja")
        smg = await _show(client, branch="semarang")
        semua = await _show(client, branch="semua")

    assert jogja.status_code == 200, jogja.text
    j_nums = [r["values"]["nomor"] for r in jogja.json()["rows"]]
    assert "FPB-YGY-NEW" in j_nums
    assert "FPB-YGY-NAME" in j_nums
    assert "FPB-YGY-POFB" in j_nums
    assert "FPB-SMG" not in j_nums
    assert "FPB-LEGACY-EMPTY" not in j_nums
    assert "FPB-UNKNOWN-ID" not in j_nums
    assert Decimal(jogja.json()["totals"]["total"]) == Decimal("2250.00")

    assert smg.status_code == 200, smg.text
    s_nums = [r["values"]["nomor"] for r in smg.json()["rows"]]
    assert s_nums == ["FPB-SMG"]
    assert Decimal(smg.json()["totals"]["total"]) == Decimal("900.00")

    assert semua.status_code == 200, semua.text
    all_nums = [r["values"]["nomor"] for r in semua.json()["rows"]]
    assert set(all_nums) == {
        "FPB-YGY-NEW", "FPB-YGY-NAME", "FPB-YGY-POFB",
        "FPB-SMG", "FPB-LEGACY-EMPTY", "FPB-UNKNOWN-ID",
    }
    assert Decimal(semua.json()["totals"]["total"]) == Decimal("3325.00")
    assert BRANCH_SEMUA == "semua"
