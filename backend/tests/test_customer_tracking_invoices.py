"""Public live-tracking invoices. Isolated test database only."""
from __future__ import annotations

import io
import os
import sys
import uuid
import zlib
from pathlib import Path

import httpx
import pdfplumber
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["MONGO_URL"] = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ["DB_NAME"] = "eternal_packaging_tracking_invoices_test"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

assert os.environ["DB_NAME"] != "eternal_packaging"

import server
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.customer_tracking import PUBLIC_NOT_FOUND, new_tracking_token, public_payload
from services.customer_tracking_invoices import customer_invoice_pdf_source
from services.sales_invoice_posting import ACCOUNTING_POSTED
from services.sales_invoices import (
    cancel_sales_invoice,
    create_sales_invoice,
    finalize_sales_invoice,
    generate_sales_invoice_pdf_bytes,
    get_sales_invoice_detail,
)
from services.sales_receipt_posting import _finalize_reversal_operational_state
from services.sales_receipts import create_sales_receipt

USER = {"id": "u-track-inv", "name": "Staff Uji", "email": "uji@example.com", "role": "admin"}
FORBIDDEN = (
    "hpp", "margin", "journal", "created_by", "accounting", "notes", "cash_bank",
    "idempotency", "tracking_token", "internal note", "sales_user",
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        monkeypatch.setattr(server, "client", client)
        monkeypatch.setattr(
            server, "db", client["eternal_packaging_tracking_invoices_test"]
        )
        assert server.db.name == "eternal_packaging_tracking_invoices_test"
        yield
    finally:
        client.close()


def _token():
    return new_tracking_token()


async def _order(customer="PT Sumber Rejeki", qty=100, unit_price=None, **extra):
    po_id = str(uuid.uuid4())
    doc = {
        "id": po_id,
        "po_number": f"PO/TRK/{uuid.uuid4().hex[:8].upper()}",
        "customer": customer,
        "outlet": "Outlet Uji",
        "address": "Jl. Internal 9",
        "sales": "Sales Internal",
        "notes": "catatan internal pesanan",
        "line_items": [{
            "line_idx": 0,
            "line_id": "line-0",
            "name": "Cup 8oz",
            "quantity": qty,
            "unit": "pcs",
            **({"unit_price": unit_price} if unit_price is not None else {}),
        }],
        "total_quantity": qty,
        "current_stage": 5,
        "payment_status": "Waiting",
        "tracking_token": extra.pop("tracking_token", _token()),
        "tracking_token_created_at": extra.pop("tracking_token_created_at", "2020-01-01T00:00:00+00:00"),
        "cancelled": False,
    }
    doc.update(extra)
    await server.db.orders.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def _invoice(order, qty, unit_price, *, term=14, date="2026-09-01", notes="catatan internal faktur"):
    # The invoice service snapshots the PO price. The figure belongs on the order first.
    await server.db.orders.update_one(
        {
            "id": order["id"],
            "line_items.0.unit_price": {"$exists": False},
        },
        {"$set": {"line_items.0.unit_price": unit_price}},
    )
    return await create_sales_invoice(server.db, {
        "po_id": order["id"],
        "invoice_date": date,
        "payment_term_days": term,
        "notes": notes,
        "lines": [{"line_idx": 0, "quantity": qty, "unit_price": unit_price}],
    }, USER)


async def _finalize(inv):
    return await finalize_sales_invoice(server.db, inv["id"], USER)


async def _pay(inv, amount):
    return await create_sales_receipt(server.db, {
        "invoice_id": inv["id"],
        "amount": amount,
        "receipt_date": "2026-09-02",
        "notes": "catatan internal penerimaan",
    }, USER)


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test")


def _pdf_text(data: bytes) -> str:
    """ReportLab streams are ASCII85 + Flate."""
    import base64

    parts = []
    cursor = 0
    marker = b"stream\n"
    while True:
        idx = data.find(marker, cursor)
        if idx < 0:
            break
        raw_start = idx + len(marker)
        end = data.find(b"endstream", raw_start)
        if end < 0:
            break
        raw = data[raw_start:end].strip()
        if raw.endswith(b"~>"):
            raw = raw[:-2]
        cursor = end + len(b"endstream")
        try:
            decoded = zlib.decompress(base64.a85decode(raw, adobe=False))
        except (ValueError, zlib.error):
            continue
        parts.append(decoded.decode("latin-1", errors="ignore"))
    return "\n".join(parts)


def _assert_public(body):
    blob = str(body).lower()
    for word in FORBIDDEN:
        assert word not in blob
    assert "Jl. Internal" not in str(body)
    assert "Sales Internal" not in str(body)


async def _counts():
    return {
        "invoices": await server.db.sales_invoices.count_documents({}),
        "receipts": await server.db.sales_receipts.count_documents({}),
        "journals": await server.db.journal_entries.count_documents({}),
        "orders": await server.db.orders.count_documents({}),
    }


@pytest.mark.anyio
async def test_a_order_without_invoice_is_empty_and_read_only():
    order = await _order()
    before = await _counts()
    async with _client() as client:
        res = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices")
    assert res.status_code == 200
    assert res.json() == {"invoices": []}
    assert res.headers["cache-control"] == "private, no-store"
    tracking = public_payload(order, order["tracking_token"])
    assert "invoices" not in tracking
    assert "grand_total" not in tracking
    assert await _counts() == before


@pytest.mark.anyio
async def test_b_draft_and_posted_flag_do_not_publish():
    order = await _order()
    draft = await _invoice(order, 10, 1000)
    await server.db.sales_invoices.update_one(
        {"id": draft["id"]},
        {"$set": {"accounting_posted": True, "accounting_status": ACCOUNTING_POSTED}},
    )
    async with _client() as client:
        listed = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices")
        detail = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices/{draft['id']}")
        pdf = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices/{draft['id']}/pdf")
    assert listed.json()["invoices"] == []
    assert detail.status_code == 404
    assert pdf.status_code == 404
    assert detail.json()["detail"] == PUBLIC_NOT_FOUND


@pytest.mark.anyio
async def test_c_finalized_unpaid_without_journal_is_visible():
    order = await _order()
    inv = await _finalize(await _invoice(order, 40, 2500))
    internal = await get_sales_invoice_detail(server.db, inv["id"])
    async with _client() as client:
        res = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices")
    row = res.json()["invoices"][0]
    _assert_public(row)
    assert row["invoice_number"] == internal["invoice_number"]
    assert row["grand_total"] == internal["grand_total"]
    assert row["outstanding_amount"] == internal["outstanding_amount"]
    assert row["paid_total"] == 0
    assert row["payment_label"] == "Belum lunas"
    assert row["financial_status"] == "finalized"
    assert row["due_date"] == internal["due_date"]
    assert row["pdf_url"].startswith(f"/api/public/tracking/{order['tracking_token']}/invoices/{inv['id']}/pdf")
    assert internal.get("accounting_posted") is not True


@pytest.mark.anyio
async def test_d_partial_then_full_payment_matches_internal():
    order = await _order(qty=100, unit_price=1000)
    inv = await _finalize(await _invoice(order, 100, 99999))
    await _pay(inv, 40000)
    async with _client() as client:
        partial = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices")
    internal_partial = await get_sales_invoice_detail(server.db, inv["id"])
    row = partial.json()["invoices"][0]
    assert row["payment_label"] == "Sebagian"
    assert row["paid_total"] == internal_partial["paid_total"] == 40000
    assert row["outstanding_amount"] == internal_partial["outstanding_amount"] == 60000
    assert row["grand_total"] == internal_partial["grand_total"] == 100000

    await _pay(inv, 60000)
    async with _client() as client:
        full = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices/{inv['id']}")
    internal_full = await get_sales_invoice_detail(server.db, inv["id"])
    body = full.json()
    assert body["payment_label"] == "Lunas"
    assert body["outstanding_amount"] == internal_full["outstanding_amount"] == 0
    assert body["paid_total"] == internal_full["paid_total"] == 100000
    assert body["lines"][0]["unit_price"] == 1000
    _assert_public(body)


@pytest.mark.anyio
async def test_e_partial_invoices_stay_separate_documents():
    order = await _order(qty=100, unit_price=2500)
    first = await _finalize(await _invoice(order, 40, 2500, date="2026-09-01"))
    second = await _finalize(await _invoice(order, 60, 2500, date="2026-09-10", term=7))
    async with _client() as client:
        res = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices")
    rows = res.json()["invoices"]
    assert [r["id"] for r in rows] == [first["id"], second["id"]]
    assert rows[0]["grand_total"] == 100000
    assert rows[1]["grand_total"] == 150000
    assert "combined_total" not in res.json()
    assert sum(r["grand_total"] for r in rows) == 250000


@pytest.mark.anyio
async def test_f_cancelled_invoice_and_cancelled_receipt_follow_lifecycle():
    cancelled_order = await _order(customer="PT Batal")
    cancelled = await _finalize(await _invoice(cancelled_order, 10, 5000))
    await cancel_sales_invoice(server.db, cancelled["id"], USER)
    async with _client() as client:
        listed = await client.get(f"/api/public/tracking/{cancelled_order['tracking_token']}/invoices")
    internal = await get_sales_invoice_detail(server.db, cancelled["id"])
    row = listed.json()["invoices"][0]
    assert row["payment_label"] == "Batal"
    assert row["financial_status"] == "cancelled"
    assert row["outstanding_amount"] == internal["outstanding_amount"] == 0
    assert row["paid_total"] == internal["paid_total"] == 0
    assert row["grand_total"] == internal["grand_total"] == 50000

    paid_order = await _order(customer="PT Reversal")
    inv = await _finalize(await _invoice(paid_order, 10, 8000))
    receipt = await _pay(inv, 80000)
    await _finalize_reversal_operational_state(server.db, receipt, USER)
    async with _client() as client:
        after = await client.get(f"/api/public/tracking/{paid_order['tracking_token']}/invoices/{inv['id']}")
    internal_after = await get_sales_invoice_detail(server.db, inv["id"])
    body = after.json()
    assert body["payment_label"] == "Belum lunas"
    assert body["outstanding_amount"] == internal_after["outstanding_amount"] == 80000
    assert body["paid_total"] == internal_after["paid_total"] == 0


@pytest.mark.anyio
async def test_g_token_of_order_a_cannot_read_order_b():
    order_a = await _order(customer="PT Alpha")
    order_b = await _order(customer="PT Beta")
    inv_b = await _finalize(await _invoice(order_b, 5, 1500))
    async with _client() as client:
        listed = await client.get(f"/api/public/tracking/{order_a['tracking_token']}/invoices")
        detail = await client.get(f"/api/public/tracking/{order_a['tracking_token']}/invoices/{inv_b['id']}")
        pdf = await client.get(f"/api/public/tracking/{order_a['tracking_token']}/invoices/{inv_b['id']}/pdf")
        own = await client.get(f"/api/public/tracking/{order_b['tracking_token']}/invoices/{inv_b['id']}")
    assert listed.json()["invoices"] == []
    assert detail.status_code == pdf.status_code == 404
    assert own.status_code == 200
    assert own.json()["id"] == inv_b["id"]
    assert own.json()["invoice_number"] == inv_b["invoice_number"]


@pytest.mark.anyio
async def test_h_invalid_token_rejected_and_tracking_has_no_expiry():
    order = await _order()
    inv = await _finalize(await _invoice(order, 2, 1000))
    async with _client() as client:
        for token in ("short", "   ", "tidak-ada-token-xxxx", order["tracking_token"] + "x"):
            listed = await client.get(f"/api/public/tracking/{token}/invoices")
            detail = await client.get(f"/api/public/tracking/{token}/invoices/{inv['id']}")
            pdf = await client.get(f"/api/public/tracking/{token}/invoices/{inv['id']}/pdf")
            assert listed.status_code == detail.status_code == pdf.status_code == 404
        old = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices")
        cancelled = await _order(customer="PT Tutup", cancelled=True)
        gone = await client.get(f"/api/public/tracking/{cancelled['tracking_token']}/invoices")
    assert old.status_code == 200
    assert old.json()["invoices"][0]["id"] == inv["id"]
    assert gone.status_code == 404


@pytest.mark.anyio
async def test_i_multi_order_invoice_is_withheld():
    order_a = await _order(customer="PT Gabungan A")
    order_b = await _order(customer="PT Gabungan B")
    inv = await _finalize(await _invoice(order_a, 8, 1000))
    await server.db.sales_invoices.update_one(
        {"id": inv["id"]},
        {"$set": {
            "po_ids": [order_a["id"], order_b["id"]],
            "lines.0.po_id": order_b["id"],
            "customer": "PT Orang Lain",
        }},
    )
    async with _client() as client:
        listed = await client.get(f"/api/public/tracking/{order_a['tracking_token']}/invoices")
        detail = await client.get(f"/api/public/tracking/{order_a['tracking_token']}/invoices/{inv['id']}")
        pdf = await client.get(f"/api/public/tracking/{order_a['tracking_token']}/invoices/{inv['id']}/pdf")
        other = await client.get(f"/api/public/tracking/{order_b['tracking_token']}/invoices/{inv['id']}")
    assert listed.json()["invoices"] == []
    assert detail.status_code == pdf.status_code == other.status_code == 404


@pytest.mark.anyio
async def test_j_snapshot_price_ignores_later_order_price():
    order = await _order()
    inv = await _finalize(await _invoice(order, 3, 4321))
    await server.db.orders.update_one(
        {"id": order["id"]},
        {"$set": {"line_items.0.unit_price": 1, "line_items.0.name": "Nama Baru"}},
    )
    async with _client() as client:
        detail = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices/{inv['id']}")
    internal = await get_sales_invoice_detail(server.db, inv["id"])
    body = detail.json()
    assert body["lines"][0]["unit_price"] == internal["lines"][0]["unit_price"] == 4321
    assert body["lines"][0]["item_name"] == "Cup 8oz"
    assert body["grand_total"] == internal["grand_total"]


@pytest.mark.anyio
async def test_k_reads_do_not_write_business_documents():
    order = await _order()
    inv = await _finalize(await _invoice(order, 4, 2000))
    await _pay(inv, 1000)
    before_docs = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0, "updated_at": 1, "paid_total": 1, "financial_status": 1})
    before_order = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0, "current_stage": 1, "payment_status": 1, "tracking_token": 1})
    before = await _counts()
    async with _client() as client:
        listed = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices")
        detail = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices/{inv['id']}")
        pdf = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices/{inv['id']}/pdf")
        tracking = await client.get(f"/api/public/tracking/{order['tracking_token']}")
    assert listed.status_code == detail.status_code == pdf.status_code == tracking.status_code == 200
    assert pdf.content[:4] == b"%PDF"
    assert "catatan internal" not in _pdf_text(pdf.content).lower()
    assert pdf.headers["cache-control"] == "private, no-store"
    assert "invoices" not in tracking.json()
    assert await _counts() == before
    after_docs = await server.db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0, "updated_at": 1, "paid_total": 1, "financial_status": 1})
    after_order = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0, "current_stage": 1, "payment_status": 1, "tracking_token": 1})
    assert after_docs == before_docs
    assert after_order == before_order


@pytest.mark.anyio
async def test_pdf_uses_customer_label_and_blocks_other_order_number():
    order = await _order()
    inv = await _finalize(await _invoice(order, 1, 1500))
    async with _client() as client:
        pdf = await client.get(
            f"/api/public/tracking/{order['tracking_token']}/invoices/{inv['id']}/pdf?disposition=inline"
        )
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert "inline" in pdf.headers["content-disposition"]
    text = _pdf_text(pdf.content)
    assert inv["invoice_number"] in text
    assert "Belum lunas" in text
    assert "catatan internal" not in text.lower()


def _pdf_pages(data: bytes):
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = []
        for page in pdf.pages:
            pages.append({
                "text": page.extract_text() or "",
                "words": page.extract_words() or [],
            })
    return pages


def _phrase_band(words, phrase):
    tokens = phrase.split()
    for index, word in enumerate(words):
        window = words[index:index + len(tokens)]
        if [item["text"] for item in window] != tokens:
            continue
        tops = [item["top"] for item in window]
        bottoms = [item["bottom"] for item in window]
        if max(tops) - min(tops) > 3:
            continue
        return {"top": min(tops), "bottom": max(bottoms)}
    raise AssertionError(f"PDF is missing a single line for {phrase}")


def _bands_separated(first, second):
    return first["bottom"] <= second["top"] + 0.5 or second["bottom"] <= first["top"] + 0.5


async def _charge_account(name):
    await ensure_coa(server.db)
    return await create_account(
        server.db,
        {"code": f"79{uuid.uuid4().hex[:6]}", "name": name, "account_type": "EXPS"},
        user=USER,
    )


@pytest.mark.anyio
async def test_public_pdf_forwards_sales_charges_and_stored_total():
    """Passthrough of stored charges. Packing here is not a tax-policy decision."""
    assert server.db.name == "eternal_packaging_tracking_invoices_test"
    assert server.db.name != "eternal_packaging"
    await server.db.command("ping")

    async def priced_order():
        created = await _order(customer="PT Uji Biaya", sales="Sari Penjualan", qty=100)
        await server.db.orders.update_one(
            {"id": created["id"]},
            {"$set": {"line_items.0.unit_price": 7000, "line_items.0.name": "Dus Uji"}},
        )
        return created

    plain_order = await priced_order()
    both_order = await priced_order()
    ongkir_order = await priced_order()
    shipping = await _charge_account("Biaya Kirim Uji")
    packing = await _charge_account("Packing Pelanggan Uji")
    plain = await _finalize(await create_sales_invoice(server.db, {
        "po_id": plain_order["id"],
        "invoice_date": "2026-09-03",
        "notes": "catatan internal faktur lama",
        "lines": [{"line_idx": 0, "quantity": 10, "unit_price": 1}],
    }, USER))
    both = await _finalize(await create_sales_invoice(server.db, {
        "po_id": both_order["id"],
        "invoice_date": "2026-09-04",
        "notes": "catatan internal faktur",
        "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 1}],
        "other_charges": [
            {"account_id": shipping["id"], "description": "Ongkos kirim", "amount": 15000},
            {"account_id": packing["id"], "description": "Packing pelanggan", "amount": 5000},
        ],
    }, USER))
    ongkir = await _finalize(await create_sales_invoice(server.db, {
        "po_id": ongkir_order["id"],
        "invoice_date": "2026-09-05",
        "discount_amount": 50000,
        "ppn_percent": 11,
        "notes": "catatan internal ongkir",
        "lines": [{"line_idx": 0, "quantity": 100, "unit_price": 1}],
        "other_charges": [
            {"account_id": shipping["id"], "description": "Ongkir", "amount": 20000},
        ],
    }, USER))
    assert plain["other_charges"] == []
    assert plain["grand_total"] == 70000
    assert both["subtotal"] == 700000
    assert both["other_charges_total"] == 20000
    assert both["grand_total"] == 720000
    assert ongkir["after_discount"] == 650000
    assert ongkir["ppn_amount"] == 71500
    assert ongkir["grand_total"] == 741500

    other = await _order()
    before = await _counts()
    before_both = await server.db.sales_invoices.find_one({"id": both["id"]}, {"_id": 0, "updated_at": 1, "grand_total": 1})
    async with _client() as client:
        denied = await client.get(f"/api/public/tracking/{other['tracking_token']}/invoices/{both['id']}/pdf")
        old_pdf = await client.get(f"/api/public/tracking/{plain_order['tracking_token']}/invoices/{plain['id']}/pdf")
        public_both = await client.get(f"/api/public/tracking/{both_order['tracking_token']}/invoices/{both['id']}/pdf")
        public_ongkir = await client.get(f"/api/public/tracking/{ongkir_order['tracking_token']}/invoices/{ongkir['id']}/pdf")
    assert denied.status_code == 404
    assert old_pdf.status_code == public_both.status_code == public_ongkir.status_code == 200
    assert public_both.headers["cache-control"] == "private, no-store"
    assert await _counts() == before
    after_both = await server.db.sales_invoices.find_one({"id": both["id"]}, {"_id": 0, "updated_at": 1, "grand_total": 1})
    assert after_both == before_both

    stored_both = await get_sales_invoice_detail(server.db, both["id"])
    stored_ongkir = await get_sales_invoice_detail(server.db, ongkir["id"])
    allow_both = customer_invoice_pdf_source(stored_both, both_order)
    assert allow_both["notes"] == ""
    assert allow_both["sales"] == "Sari Penjualan"
    assert allow_both["other_charges"] == [
        {"description": "Ongkos kirim", "amount": 15000},
        {"description": "Packing pelanggan", "amount": 5000},
    ]
    assert allow_both["grand_total"] == stored_both["grand_total"] == 720000
    blob = str(allow_both)
    for secret in (shipping["id"], shipping["code"], shipping["name"], packing["code"], packing["name"], "catatan internal"):
        assert secret not in blob

    internal_both = _pdf_pages(generate_sales_invoice_pdf_bytes(stored_both))
    public_pages = _pdf_pages(public_both.content)
    public_text = "\n".join(page["text"] for page in public_pages)
    internal_text = "\n".join(page["text"] for page in internal_both)
    for needle in ("Sari Penjualan", "Ongkos kirim", "Packing pelanggan", "Rp 15.000", "Rp 5.000", "Rp 720.000"):
        assert needle in public_text
        assert needle in internal_text
    for secret in (
        shipping["code"], packing["code"], shipping["name"], packing["name"],
        "catatan internal", stored_both.get("accounting_journal_id") or "journal-entry",
    ):
        assert secret not in public_text
    old_text = "\n".join(page["text"] for page in _pdf_pages(old_pdf.content))
    assert "Rp 70.000" in old_text
    assert "Ongkos kirim" not in old_text
    assert "catatan internal" not in old_text.lower()

    words = public_pages[0]["words"]
    kirim = _phrase_band(words, "Ongkos kirim")
    packing_band = _phrase_band(words, "Packing pelanggan")
    assert _bands_separated(kirim, packing_band)
    assert abs(kirim["top"] - packing_band["top"]) >= 8

    ongkir_pages = _pdf_pages(public_ongkir.content)
    ongkir_text = "\n".join(page["text"] for page in ongkir_pages)
    internal_ongkir = "\n".join(page["text"] for page in _pdf_pages(generate_sales_invoice_pdf_bytes(stored_ongkir)))
    for needle in ("Sari Penjualan", "Ongkir", "Rp 50.000", "Rp 71.500", "Rp 20.000", "Rp 741.500"):
        assert needle in ongkir_text
        assert needle in internal_ongkir
    assert "catatan internal ongkir" not in ongkir_text
    assert shipping["code"] not in ongkir_text
    ongkir_band = _phrase_band(ongkir_pages[0]["words"], "Ongkir")
    ppn_band = _phrase_band(ongkir_pages[0]["words"], "PPN (11.0%)")
    total_band = _phrase_band(ongkir_pages[0]["words"], "GRAND TOTAL")
    assert _bands_separated(ppn_band, ongkir_band)
    assert _bands_separated(ongkir_band, total_band)


def test_invoice_routes_do_not_write():
    src = Path(__file__).resolve().parents[1] / "services" / "customer_tracking_invoices.py"
    text = src.read_text(encoding="utf-8")
    for word in ("update_one", "insert_one", "delete_one", "delete_many", "replace_one", "customer_pricing"):
        assert word not in text
    routes = Path(__file__).resolve().parents[1] / "services" / "customer_tracking.py"
    block = routes.read_text(encoding="utf-8").split("async def public_tracking_invoices")[1].split("async def tracking_link")[0]
    for word in ("update_one", "insert_one", "delete_one", "ensure_tracking_token"):
        assert word not in block
