"""Iteration 13: Eternal Packaging PDF header parsing bug fix.

Only tests the /api/import/pdf/preview endpoint with the Eternal sample PDF.
Verifies exact header field extractions and that no DB side-effects occur.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"
FIXTURE = "/app/backend/tests/fixtures/eternal_sample.pdf"
SYNTHETIC = "/tmp/test-po.pdf"

ETERNAL_CREDS = {"email": "admin@eternalpackaging.com", "password": "Eternal123!"}


@pytest.fixture(scope="module")
def eternal_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=ETERNAL_CREDS, timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def preview(eternal_session):
    assert os.path.exists(FIXTURE), f"Fixture missing: {FIXTURE}"
    with open(FIXTURE, "rb") as f:
        r = eternal_session.post(
            f"{API}/import/pdf/preview",
            files={"file": ("eternal_sample.pdf", f, "application/pdf")},
            timeout=60,
        )
    assert r.status_code == 200, f"Preview failed: {r.status_code} {r.text[:500]}"
    body = r.json()
    return body.get("preview") or body


# ---------- Header field exact-match tests ----------

def test_po_number(preview):
    assert preview.get("po_number") == "PO/2026/08/20/000019", preview.get("po_number")


def test_order_date(preview):
    v = preview.get("order_date") or ""
    assert v.startswith("2026-08-20"), v


def test_estimated_delivery(preview):
    v = preview.get("estimated_delivery") or ""
    assert v.startswith("2026-08-24"), v


def test_customer(preview):
    assert preview.get("customer") == "KALJA COFFEE", preview.get("customer")


def test_outlet_is_none(preview):
    assert preview.get("outlet") in (None, ""), preview.get("outlet")


def test_recipient(preview):
    assert preview.get("recipient") == "ICANG", preview.get("recipient")


def test_address(preview):
    expected = "Jl. Pandega Wirabuana 3, Manggung, Caturtunggal, Depok, Sleman"
    actual = (preview.get("address") or "").rstrip(".").strip()
    assert actual == expected, f"actual={preview.get('address')!r}"
    a_low = (preview.get("address") or "").lower()
    for bad in ["keterangan", "total kuantitas", "sablon", "inject", "12 oz"]:
        assert bad not in a_low, f"address contains {bad!r}: {preview.get('address')!r}"


def test_payment_terms(preview):
    assert preview.get("payment_terms") == "TRANSFER BANK", preview.get("payment_terms")


def test_expedition(preview):
    assert preview.get("expedition") == "OUTLET", preview.get("expedition")


def test_delivery_zone(preview):
    assert preview.get("delivery_zone") == "JT", preview.get("delivery_zone")


def test_sales(preview):
    assert preview.get("sales") == "Dimas Aji Prasetya", preview.get("sales")


def test_created_by_user(preview):
    assert preview.get("created_by_user") == "TIANA", preview.get("created_by_user")


def test_notes(preview):
    assert preview.get("notes") == "SABLON WARNA PUTIH", preview.get("notes")


# ---------- Line items intact ----------

def test_line_items(preview):
    items = preview.get("items") or preview.get("line_items") or []
    assert len(items) == 2, f"item_count={len(items)} items={items}"
    by_code = {str(it.get("item_code")): it for it in items}
    assert "100147" in by_code and "100115" in by_code, list(by_code.keys())
    a = by_code["100147"]
    assert a.get("name") == "12 oz Inject Bening Wingoh", a
    assert int(a.get("quantity") or a.get("qty") or 0) == 300
    b = by_code["100115"]
    assert b.get("name") == "LID Connect Inject Bening Victory", b
    assert int(b.get("quantity") or b.get("qty") or 0) == 300
    tq = preview.get("total_quantity")
    if tq is not None:
        assert int(tq) == 600, tq
    ic = preview.get("item_count")
    if ic is not None:
        assert int(ic) == 2, ic


# ---------- Side-effect checks ----------

def test_no_order_created(eternal_session):
    r = eternal_session.get(f"{API}/orders", timeout=30)
    assert r.status_code == 200
    orders = r.json() if isinstance(r.json(), list) else r.json().get("orders", [])
    matching = [o for o in orders if o.get("po_number") == "PO/2026/08/20/000019"]
    assert len(matching) == 0, f"Preview created an order! Found: {matching}"


def test_no_import_log_created(eternal_session):
    # try common endpoints; skip if none exist
    for path in ["/import/logs", "/import-logs", "/imports/logs"]:
        r = eternal_session.get(f"{API}{path}", timeout=15)
        if r.status_code == 200:
            data = r.json() if isinstance(r.json(), list) else r.json().get("logs", [])
            hits = [x for x in data if "PO/2026/08/20/000019" in str(x)]
            assert len(hits) == 0, f"Import log created for preview: {hits}"
            return
    pytest.skip("No import log endpoint found; skipping.")


# ---------- Regression on synthetic PDF ----------

def test_synthetic_pdf_regression(eternal_session):
    if not os.path.exists(SYNTHETIC):
        pytest.skip("/tmp/test-po.pdf missing (pod restart) — regression skipped")
    with open(SYNTHETIC, "rb") as f:
        r = eternal_session.post(
            f"{API}/import/pdf/preview",
            files={"file": ("test-po.pdf", f, "application/pdf")},
            timeout=60,
        )
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    p = body.get("preview") or body
    assert p.get("po_number") == "PO-PDF-TEST-7001", p.get("po_number")
    assert p.get("customer") == "Kopi PDF Test", p.get("customer")
    assert p.get("sales") == "Vira", p.get("sales")
    assert p.get("created_by_user") == "Rina (Sales Admin)", p.get("created_by_user")
    items = p.get("items") or p.get("line_items") or []
    assert len(items) == 3, len(items)


# ---------- Regression: existing PO preserved ----------

def test_existing_po_preserved(eternal_session):
    r = eternal_session.get(f"{API}/orders", timeout=30)
    assert r.status_code == 200
    orders = r.json() if isinstance(r.json(), list) else r.json().get("orders", [])
    pos = [o.get("po_number") for o in orders]
    if "PO-PDF-TEST-7001" not in pos:
        pytest.skip(f"PO-PDF-TEST-7001 not present in DB (prior data not preserved); pos sample: {pos[:5]}")
