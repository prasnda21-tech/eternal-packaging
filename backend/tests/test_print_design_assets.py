"""Design Cetakan Phase 3 — custom logo + Faktur/Proforma signature assets."""
from __future__ import annotations

import copy
import io
import os
import sys
from pathlib import Path
import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_NAME", "eternal_packaging_design_cetakan_assets_test")
os.environ.setdefault("STORAGE_PROVIDER", "local")

from services import storage as obj_storage  # noqa: E402
from services.order_pdf import generate_sales_order_pdf_bytes  # noqa: E402
from services.print_design_settings import (  # noqa: E402
    ASSET_SLOTS,
    FILE_KIND,
    MAX_LOGO_BYTES,
    MAX_SIGNATURE_BYTES,
    SLOT_APPROVED,
    SLOT_COMPANY_LOGO,
    SLOT_PREPARED,
    get_print_design_for_pdf,
    get_print_design_internal,
    get_print_design_settings,
    load_slot_bytes,
    remove_print_design_asset,
    resolve_print_design,
    save_print_design_settings,
    upload_print_design_asset,
    validate_design_image,
)
from services.sales_deliveries import generate_sales_delivery_pdf_bytes  # noqa: E402
from services.sales_invoices import (  # noqa: E402
    DOCUMENT_PROFORMA,
    PROFORMA_WARNING,
    generate_sales_invoice_pdf_bytes,
)

def _make_png(size=(32, 16), color=(200, 30, 30, 255), *, rgba=True) -> bytes:
    from PIL import Image as PILImage

    mode = "RGBA" if rgba else "RGB"
    im = PILImage.new(mode, size, color if rgba else color[:3])
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg(size=(32, 16), color=(30, 30, 200)) -> bytes:
    from PIL import Image as PILImage

    im = PILImage.new("RGB", size, color)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _make_webp(size=(32, 16), color=(30, 160, 60)) -> bytes:
    from PIL import Image as PILImage

    im = PILImage.new("RGB", size, color)
    buf = io.BytesIO()
    im.save(buf, format="WEBP")
    return buf.getvalue()


PNG_1X1 = _make_png()
JPEG_1X1 = _make_jpeg()
WEBP_1X1 = _make_webp()

SVG_BYTES = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
USER = {"id": "u1", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}


class MockCollection:
    def __init__(self, initial=None):
        self._docs = copy.deepcopy(initial or [])

    async def find_one(self, query=None, projection=None):
        query = query or {}
        for doc in self._docs:
            if _match(doc, query):
                res = copy.deepcopy(doc)
                if projection and projection.get("_id") == 0 and "_id" in res:
                    del res["_id"]
                return res
        return None

    async def update_one(self, query, update, upsert=False):
        set_vals = update.get("$set", {})
        for idx, doc in enumerate(self._docs):
            if _match(doc, query):
                self._docs[idx].update(copy.deepcopy(set_vals))
                return
        if upsert:
            new_doc = copy.deepcopy(query)
            # strip operators from query keys that aren't equality
            new_doc = {k: v for k, v in new_doc.items() if not str(k).startswith("$") and not isinstance(v, dict)}
            new_doc.update(copy.deepcopy(set_vals))
            self._docs.append(new_doc)

    async def insert_one(self, doc):
        self._docs.append(copy.deepcopy(doc))


def _match(doc, query):
    for k, v in (query or {}).items():
        if isinstance(v, dict):
            if "$ne" in v:
                if doc.get(k) == v["$ne"]:
                    return False
            else:
                if doc.get(k) != v:
                    return False
        elif doc.get(k) != v:
            return False
    return True


class MockDb:
    def __init__(self, app_settings=None, files=None):
        self.app_settings = MockCollection(app_settings)
        self.files = MockCollection(files)


def _pdf_ok(pdf_bytes: bytes) -> bool:
    return bool(pdf_bytes) and pdf_bytes.startswith(b"%PDF")


def _pdf_text(pdf_bytes: bytes) -> str:
    import pdfplumber
    from io import BytesIO

    pages = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _sample_docs():
    order = {
        "po_number": "PO/2026/01/01/000001",
        "customer": "PT Contoh",
        "address": "Yogyakarta",
        "order_date": "2026-01-01",
        "line_items": [{"item_code": "ITM-1", "name": "Tas", "quantity": 10, "unit": "pcs", "line_idx": 0}],
        "total_quantity": 10,
        "operator_splits": [],
    }
    invoice = {
        "invoice_number": "INV/2026/01/0001",
        "invoice_date": "2026-01-02",
        "due_date": "2026-01-16",
        "customer": "PT Contoh",
        "address": "Yogyakarta",
        "po_number": order["po_number"],
        "sales": "Sales A",
        "subtotal": 11000,
        "discount_amount": 0,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "paid_total": 0,
        "grand_total": 11000,
        "lines": [{
            "item_code": "ITM-1",
            "item_name": "Tas",
            "quantity": 10,
            "unit_price": 1100,
            "line_subtotal": 11000,
        }],
        "other_charges": [],
    }
    delivery = {
        "sj_number": "SJ/01/01/26/001",
        "customer": "PT Contoh",
        "address": "Yogyakarta",
        "po_number": order["po_number"],
        "delivery_date": "2026-01-03",
        "lines": [{"item_code": "ITM-1", "item_name": "Tas", "qty": 5, "unit": "pcs"}],
    }
    return order, invoice, delivery


@pytest.fixture(autouse=True)
def _isolated_local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "objstore"))
    obj_storage._reset_provider_for_tests()
    yield
    obj_storage._reset_provider_for_tests()


def test_asset_slots_exact():
    assert ASSET_SLOTS == frozenset({
        SLOT_COMPANY_LOGO,
        SLOT_PREPARED,
        SLOT_APPROVED,
    })


def test_validate_png_jpeg_webp_accept():
    assert validate_design_image(PNG_1X1, "image/png", max_bytes=MAX_LOGO_BYTES) == "image/png"
    assert validate_design_image(JPEG_1X1, "image/jpeg", max_bytes=MAX_LOGO_BYTES) == "image/jpeg"
    assert validate_design_image(WEBP_1X1, "image/webp", max_bytes=MAX_LOGO_BYTES) == "image/webp"


def test_validate_rejects_svg_malformed_oversized():
    with pytest.raises(HTTPException) as exc:
        validate_design_image(SVG_BYTES, "image/svg+xml", max_bytes=MAX_LOGO_BYTES)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException):
        validate_design_image(b"not-an-image-bytes-xxxxxxxxxxxxxxxx", "image/png", max_bytes=MAX_LOGO_BYTES)

    with pytest.raises(HTTPException) as oversized_logo:
        validate_design_image(b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_LOGO_BYTES + 10), "image/png", max_bytes=MAX_LOGO_BYTES)
    assert oversized_logo.value.status_code == 400

    with pytest.raises(HTTPException) as oversized_sig:
        validate_design_image(b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_SIGNATURE_BYTES + 10), "image/png", max_bytes=MAX_SIGNATURE_BYTES)
    assert oversized_sig.value.status_code == 400


@pytest.mark.anyio
async def test_upload_logo_and_signatures_save_refs():
    db = MockDb()
    for slot, body in (
        (SLOT_COMPANY_LOGO, PNG_1X1),
        (SLOT_PREPARED, PNG_1X1),
        (SLOT_APPROVED, JPEG_1X1),
    ):
        public = await upload_print_design_asset(
            db,
            slot=slot,
            body=body,
            content_type="image/png" if body.startswith(b"\x89PNG") else "image/jpeg",
            filename=f"client-{slot}.png",
            user=USER,
        )
        if slot == SLOT_COMPANY_LOGO:
            assert public["company_logo"]["has_custom"] is True
            assert "storage_path" not in public["company_logo"]
        else:
            assert public["invoice"][slot]["has_custom"] is True
            assert "storage_path" not in public["invoice"][slot]

    internal = await get_print_design_internal(db)
    logo_ref = internal["company_logo"]
    assert logo_ref["file_id"]
    assert logo_ref["storage_path"].startswith(f"{obj_storage.APP_NAME}/print-design/{SLOT_COMPANY_LOGO}/")
    assert ".." not in logo_ref["storage_path"]
    assert logo_ref["storage_path"].endswith(".png")
    # client filename sanitized, not trusted as path
    assert logo_ref["storage_path"].find("client-") == -1

    files = db.files._docs
    assert len(files) == 3
    assert all(f.get("kind") == FILE_KIND for f in files)


@pytest.mark.anyio
async def test_invalid_slot_rejected():
    db = MockDb()
    with pytest.raises(HTTPException) as exc:
        await upload_print_design_asset(
            db, slot="po_operator_signature", body=PNG_1X1, content_type="image/png", filename="x.png", user=USER
        )
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_replace_updates_active_ref_and_remove_clears():
    db = MockDb()
    first = await upload_print_design_asset(
        db, slot=SLOT_COMPANY_LOGO, body=PNG_1X1, content_type="image/png", filename="a.png", user=USER
    )
    assert first["company_logo"]["has_custom"] is True
    internal1 = await get_print_design_internal(db)
    path1 = internal1["company_logo"]["storage_path"]

    second = await upload_print_design_asset(
        db, slot=SLOT_COMPANY_LOGO, body=JPEG_1X1, content_type="image/jpeg", filename="b.jpg", user=USER
    )
    assert second["company_logo"]["has_custom"] is True
    internal2 = await get_print_design_internal(db)
    path2 = internal2["company_logo"]["storage_path"]
    assert path2 != path1
    assert path2.endswith(".jpg")

    cleared = await remove_print_design_asset(db, slot=SLOT_COMPANY_LOGO, user=USER)
    assert cleared["company_logo"]["has_custom"] is False
    internal3 = await get_print_design_internal(db)
    assert internal3["company_logo"] is None


@pytest.mark.anyio
async def test_text_put_preserves_asset_refs():
    db = MockDb()
    await upload_print_design_asset(
        db, slot=SLOT_PREPARED, body=PNG_1X1, content_type="image/png", filename="prep.png", user=USER
    )
    await upload_print_design_asset(
        db, slot=SLOT_COMPANY_LOGO, body=PNG_1X1, content_type="image/png", filename="logo.png", user=USER
    )
    saved = await save_print_design_settings(
        db,
        {"company_name": "ETERNAL ASSET DEMO", "accent_color": "#112233"},
        user=USER,
    )
    assert saved["company_name"] == "ETERNAL ASSET DEMO"
    assert saved["company_logo"]["has_custom"] is True
    assert saved["invoice"]["prepared_signature_image"]["has_custom"] is True
    # Client cannot inject asset refs via PUT
    poisoned = await save_print_design_settings(
        db,
        {
            "company_name": "X",
            "company_logo": {"file_id": "evil", "storage_path": "evil/path.png"},
            "invoice": {
                "prepared_signature_image": {"file_id": "evil2", "storage_path": "evil/2.png"},
            },
        },
        user=USER,
    )
    assert poisoned["company_logo"]["has_custom"] is True
    assert poisoned["invoice"]["prepared_signature_image"]["has_custom"] is True
    internal = await get_print_design_internal(db)
    assert "evil" not in (internal["company_logo"] or {}).get("storage_path", "")


@pytest.mark.anyio
async def test_preview_load_and_missing_storage_fallback_pdf():
    db = MockDb()
    await upload_print_design_asset(
        db, slot=SLOT_COMPANY_LOGO, body=PNG_1X1, content_type="image/png", filename="logo.png", user=USER
    )
    data, media, name = await load_slot_bytes(db, SLOT_COMPANY_LOGO)
    assert data.startswith(b"\x89PNG")
    assert media == "image/png"
    assert name

    # Break storage object → PDF still generates via repo logo fallback
    internal = await get_print_design_internal(db)
    path = internal["company_logo"]["storage_path"]
    try:
        obj_storage.delete_object(path)
    except Exception:
        pass
    design = await get_print_design_for_pdf(db)
    assert design["_pdf_assets"]["company_logo"] is None
    order, invoice, delivery = _sample_docs()
    assert _pdf_ok(generate_sales_order_pdf_bytes(order, design=design))
    assert _pdf_ok(generate_sales_invoice_pdf_bytes(invoice, design=design))
    assert _pdf_ok(generate_sales_delivery_pdf_bytes(delivery, design=design))


@pytest.mark.anyio
async def test_custom_logo_and_signatures_render_pdfs():
    db = MockDb()
    await upload_print_design_asset(
        db, slot=SLOT_COMPANY_LOGO, body=PNG_1X1, content_type="image/png", filename="logo.png", user=USER
    )
    await upload_print_design_asset(
        db, slot=SLOT_PREPARED, body=PNG_1X1, content_type="image/png", filename="prep.png", user=USER
    )
    await upload_print_design_asset(
        db, slot=SLOT_APPROVED, body=PNG_1X1, content_type="image/png", filename="appr.png", user=USER
    )
    design = await get_print_design_for_pdf(db)
    assert design["_pdf_assets"]["company_logo"]
    assert design["_pdf_assets"]["prepared_signature_image"]
    assert design["_pdf_assets"]["approved_signature_image"]

    order, invoice, delivery = _sample_docs()
    po = generate_sales_order_pdf_bytes(order, design=design)
    inv = generate_sales_invoice_pdf_bytes(invoice, design=design)
    pro = generate_sales_invoice_pdf_bytes(invoice, document_type=DOCUMENT_PROFORMA, design=design)
    sj = generate_sales_delivery_pdf_bytes(delivery, design=design)
    assert all(_pdf_ok(x) for x in (po, inv, pro, sj))

    # Image XObjects present when custom assets render
    assert b"/XObject" in inv or b"/Image" in inv
    pro_text = _pdf_text(pro)
    assert "PROFORMA INVOICE" in pro_text
    assert PROFORMA_WARNING in pro_text
    assert "Uang dimuka" not in pro_text

    sj_text = _pdf_text(sj)
    assert "SURAT JALAN" in sj_text
    assert "Gudang" in sj_text
    # No static design signature slots for SJ in Phase 3 — blank/text labels remain
    assert "Kurir" in sj_text


def test_no_custom_asset_preserves_existing_behavior():
    order, invoice, delivery = _sample_docs()
    design = resolve_print_design(None)
    assert design.get("company_logo") is None
    assert design["invoice"]["prepared_signature_image"] is None
    assert design["invoice"]["approved_signature_image"] is None
    assert _pdf_ok(generate_sales_order_pdf_bytes(order, design=design))
    inv_text = _pdf_text(generate_sales_invoice_pdf_bytes(invoice, design=design))
    assert "Disiapkan Oleh" in inv_text
    assert "Disetujui Oleh" in inv_text
    assert "Faktur Penjualan" in inv_text


@pytest.mark.anyio
async def test_public_settings_hide_storage_paths():
    db = MockDb()
    await upload_print_design_asset(
        db, slot=SLOT_COMPANY_LOGO, body=PNG_1X1, content_type="image/png", filename="logo.png", user=USER
    )
    public = await get_print_design_settings(db)
    dumped = str(public)
    assert "storage_path" not in dumped
    assert "eternal-packaging/print-design" not in dumped
    assert public["company_logo"]["has_custom"] is True


def test_malformed_asset_ref_sanitized():
    resolved = resolve_print_design({
        "company_logo": {"file_id": "x", "storage_path": "../etc/passwd"},
        "invoice": {
            "prepared_signature_image": {"file_id": "", "storage_path": "x"},
            "approved_signature_image": "not-a-dict",
        },
    })
    assert resolved["company_logo"] is None
    assert resolved["invoice"]["prepared_signature_image"] is None
    assert resolved["invoice"]["approved_signature_image"] is None


def test_http_asset_routes_permission_wiring():
    """View for preview GET; manage for upload/remove. No new permissions."""
    server_src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert '@api_router.get("/settings/design-cetakan/assets/{slot}")' in server_src
    assert '@api_router.post("/settings/design-cetakan/assets/{slot}")' in server_src
    assert '@api_router.delete("/settings/design-cetakan/assets/{slot}")' in server_src
    # Preview allows view
    preview_idx = server_src.index("async def design_cetakan_asset_preview")
    preview_block = server_src[preview_idx:preview_idx + 500]
    assert "settings.design_cetakan.view" in preview_block
    assert "settings.design_cetakan.manage" in preview_block
    # Upload/remove require manage (not view-only)
    upload_idx = server_src.index("async def design_cetakan_asset_upload")
    upload_block = server_src[upload_idx:upload_idx + 400]
    assert "settings.design_cetakan.manage" in upload_block
    assert "settings.design_cetakan.view" not in upload_block
    remove_idx = server_src.index("async def design_cetakan_asset_remove")
    remove_block = server_src[remove_idx:remove_idx + 350]
    assert "settings.design_cetakan.manage" in remove_block
    assert "settings.design_cetakan.view" not in remove_block


def test_pdf_endpoints_prefetch_assets():
    server_src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "get_print_design_for_pdf" in server_src
    deliveries_src = (ROOT / "services" / "sales_deliveries.py").read_text(encoding="utf-8")
    assert "get_print_design_for_pdf" in deliveries_src
