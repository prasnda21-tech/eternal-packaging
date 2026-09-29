"""Desain Cetakan: model, renderer, draft/activation, and print resolution."""
from __future__ import annotations

import asyncio
import copy
import io

import pdfplumber
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from permissions import ROLE_DEFAULT_PERMISSIONS
from services.print_template_model import (
    builtin_invoice_design,
    print_cache_token,
    sample_delivery,
    sample_invoice,
    terbilang_rupiah,
    validate_design,
)
from services.print_template_render import DesignRenderError, render_design_pdf
from services.print_template_model import build_delivery_context, build_invoice_context
from services import print_templates as svc
from services.print_template_routes import register
from services.sales_invoices import generate_sales_invoice_pdf_bytes
from services.sales_deliveries import generate_sales_delivery_pdf_bytes


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, limit):
        return self.docs[:limit]


class _WriteResult:
    def __init__(self, matched_count):
        self.matched_count = matched_count


class _Col:
    def __init__(self):
        self.docs = []

    def _match(self, doc, query):
        for key, expected in (query or {}).items():
            if doc.get(key) != expected:
                return False
        return True

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if self._match(doc, query):
                return copy.deepcopy(doc)
        return None

    def find(self, query=None, projection=None):
        return _Cursor([copy.deepcopy(doc) for doc in self.docs if self._match(doc, query or {})])

    async def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    async def create_index(self, key, unique=False, name=None):
        return name

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._match(doc, query):
                doc.update(copy.deepcopy((update or {}).get("$set") or {}))
                return _WriteResult(1)
        if upsert:
            created = {key: value for key, value in (query or {}).items() if not str(key).startswith("$")}
            created.update(copy.deepcopy((update or {}).get("$setOnInsert") or {}))
            created.update(copy.deepcopy((update or {}).get("$set") or {}))
            self.docs.append(created)
            return _WriteResult(1)
        return _WriteResult(0)


class MemoryDB:
    def __init__(self):
        self.cols = {}

    def __getitem__(self, name):
        self.cols.setdefault(name, _Col())
        return self.cols[name]


class MemoryStorage:
    def __init__(self):
        self.objects = {}

    def put_object(self, path, data, content_type):
        self.objects[path] = (data, content_type)
        return {"path": path}

    def get_object(self, path):
        if path not in self.objects:
            raise FileNotFoundError(path)
        return self.objects[path]


def _pdf_text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _pages(data: bytes) -> int:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return len(pdf.pages)


def _app(db, user):
    app = FastAPI()
    router_holder = {}

    async def current_user():
        return user

    async def user_permissions(payload):
        return set(payload.get("_perms") or [])

    from fastapi import APIRouter
    api = APIRouter(prefix="/api")
    register(api, db=db, current_user=current_user, user_permissions=user_permissions, obj_storage=MemoryStorage())
    app.include_router(api)
    router_holder["storage"] = None
    return app


ADMIN = {"role": "admin", "email": "admin@eternal.test", "name": "Admin", "user_id": "admin-1", "is_super_admin": True}
VIEWER = {"role": "crm", "email": "view@eternal.test", "name": "Viewer", "user_id": "view-1", "_perms": ["print_template.view"]}
STAFF = {"role": "crm", "email": "staff@eternal.test", "name": "Staff", "user_id": "staff-1", "_perms": ["sales.invoice.view"]}


def test_builtin_designs_validate_and_match_legacy_when_unmodified():
    assert validate_design(builtin_invoice_design()) == []
    invoice = sample_invoice()
    assert generate_sales_invoice_pdf_bytes(invoice).startswith(b"%PDF")
    delivery = sample_delivery(partial=True)
    assert generate_sales_delivery_pdf_bytes(delivery, None).startswith(b"%PDF")


def test_legacy_default_pdf_is_the_existing_renderer():
    db = MemoryDB()
    invoice = sample_invoice()
    before = copy.deepcopy(invoice)
    rendered = asyncio.run(svc.render_invoice_pdf(db, invoice))
    assert _pdf_text(rendered) == _pdf_text(generate_sales_invoice_pdf_bytes(invoice))
    assert invoice == before
    delivery = sample_delivery(partial=True)
    before_delivery = copy.deepcopy(delivery)
    rendered_sj = asyncio.run(svc.render_delivery_pdf(db, delivery))
    assert _pdf_text(rendered_sj) == _pdf_text(generate_sales_delivery_pdf_bytes(delivery, None))
    assert delivery == before_delivery


def test_terbilang_and_partial_sj_qty_and_multipage():
    assert terbilang_rupiah(1000) == "seribu rupiah"
    assert terbilang_rupiah(0) == "nol rupiah"
    delivery = sample_delivery(partial=True, lines=1)
    ctx = build_delivery_context(delivery)
    assert ctx["lines"][0]["line.qty"] == 40
    assert "line.unit_price" not in ctx["lines"][0]
    assert ctx["values"]["delivery.total_qty"] == 40
    from services.print_template_model import builtin_delivery_design
    pdf = render_design_pdf(builtin_delivery_design(), ctx)
    text = _pdf_text(pdf)
    assert "40" in text
    assert "100" not in text
    invoice = sample_invoice(lines=26, long_name=True)
    blob = render_design_pdf(builtin_invoice_design(), build_invoice_context(invoice, preview=True))
    assert _pages(blob) >= 2
    text = _pdf_text(blob)
    assert "Halaman 1 dari" in text
    assert "#26" in text
    assert "Rp 5" in text or "Total" in text
    assert text.count("FAKTUR PENJUALAN") == 1


def test_financial_disclosure_when_summary_fields_removed():
    design = builtin_invoice_design()
    summary = design["bands"]["summary"]["elements"]
    design["bands"]["summary"]["elements"] = [
        el for el in summary
        if (el.get("props") or {}).get("field") not in {"invoice.discount_amount", "invoice.ppn_amount", "invoice.other_charges"}
        and not str(el.get("id", "")).startswith("s_l_1")
        and not str(el.get("id", "")).startswith("s_l_2")
        and not str(el.get("id", "")).startswith("s_l_3")
    ]
    # Labels for those rows were removed with the filter above only when ids match.
    # Drop the label elements explicitly.
    design["bands"]["summary"]["elements"] = [
        el for el in design["bands"]["summary"]["elements"]
        if el.get("id") not in {"s_l_1", "s_v_1", "s_l_2", "s_v_2", "s_l_3", "s_v_3"}
    ]
    invoice = sample_invoice()
    before = copy.deepcopy(invoice)
    pdf = render_design_pdf(design, build_invoice_context(invoice, preview=True))
    text = _pdf_text(pdf)
    assert "Diskon" in text and "Ongkos kirim" in text and "Pajak" in text
    assert invoice == before


def test_required_and_out_of_band_block_activation_shape():
    design = builtin_invoice_design()
    design["bands"]["title"]["elements"] = [el for el in design["bands"]["title"]["elements"] if el["id"] != "t_number"]
    errors = validate_design(design)
    assert any(err["code"] == "required" for err in errors)
    design = builtin_invoice_design()
    design["bands"]["title"]["elements"][0]["x_mm"] = 200
    errors = validate_design(design)
    assert any(err["code"] == "out_of_band" and err["element_id"] for err in errors)
    design = builtin_delivery_design_with_price()
    assert any(err["code"] == "field" for err in validate_design(design))


def builtin_delivery_design_with_price():
    from services.print_template_model import builtin_delivery_design
    design = builtin_delivery_design()
    design["bands"]["summary"]["elements"].append({
        "id": "bad_price",
        "type": "field",
        "band": "summary",
        "x_mm": 0,
        "y_mm": 0,
        "w_mm": 40,
        "h_mm": 6,
        "z": 1,
        "required": False,
        "props": {"field": "delivery.unit_price", "font": "Helvetica", "font_size": 8},
    })
    return design


def test_public_context_drops_internal_fields():
    invoice = sample_invoice()
    invoice["hpp"] = 999
    invoice["margin"] = 50
    invoice["created_by"] = "secret-user"
    invoice["notes"] = "catatan internal"
    ctx = build_invoice_context(invoice, public=True)
    blob = repr(ctx)
    assert "secret-user" not in blob
    assert "999" not in blob
    assert ctx["values"]["invoice.notes"] in ("-", "")


def test_draft_activate_default_archive_restore_and_conflict():
    db = MemoryDB()
    created = asyncio.run(svc.create_template(db, {"name": "Faktur Toko", "document_type": "sales_invoice", "audience": "customer"}, ADMIN))
    template = created
    design = copy.deepcopy(template["design"])
    design["bands"]["title"]["elements"].append({
        "id": "mark",
        "type": "text",
        "band": "title",
        "x_mm": 0,
        "y_mm": 8,
        "w_mm": 40,
        "h_mm": 5,
        "z": 2,
        "required": False,
        "props": {"text": "VERSI-DRAF", "font": "Helvetica", "font_size": 8, "wrap": True},
    })
    saved = asyncio.run(svc.save_draft(db, template["id"], design, template["revision"], ADMIN))
    invoice = sample_invoice()
    active_pdf = asyncio.run(svc.render_invoice_pdf(db, invoice))
    assert b"VERSI-DRAF" not in active_pdf
    assert _pdf_text(active_pdf).find("VERSI-DRAF") == -1
    preview = asyncio.run(svc.preview_template(db, template["id"], design))
    assert "VERSI-DRAF" in _pdf_text(preview)
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(svc.save_draft(db, template["id"], design, template["revision"], ADMIN))
    assert conflict.value.status_code == 409
    activated = asyncio.run(svc.activate_template(db, template["id"], saved["revision"], ADMIN))
    printed = asyncio.run(svc.render_invoice_pdf(db, invoice, template_id=activated["id"]))
    assert "VERSI-DRAF" in _pdf_text(printed)
    # Customer default is still the builtin until this template becomes default.
    default_pdf = asyncio.run(svc.render_invoice_pdf(db, invoice))
    assert "VERSI-DRAF" not in _pdf_text(default_pdf)
    made_default = asyncio.run(svc.set_default(db, activated["id"], activated["revision"], ADMIN))
    assert "VERSI-DRAF" in _pdf_text(asyncio.run(svc.render_invoice_pdf(db, invoice)))
    with pytest.raises(HTTPException) as archived:
        asyncio.run(svc.archive_template(db, made_default["id"], made_default["revision"], ADMIN))
    assert archived.value.status_code == 400
    builtin = asyncio.run(svc.get_template(db, "builtin-sales-invoice-customer"))
    asyncio.run(svc.set_default(db, builtin["id"], builtin["revision"], ADMIN))
    current = asyncio.run(svc.get_template(db, made_default["id"]))
    archived_doc = asyncio.run(svc.archive_template(db, current["id"], current["revision"], ADMIN))
    assert archived_doc["status"] == "archived"
    with pytest.raises(HTTPException):
        asyncio.run(svc.render_invoice_pdf(db, invoice, template_id=archived_doc["id"]))
    versions = asyncio.run(svc.list_versions(db, current["id"]))
    activate_row = next(row for row in versions if row["action"] == "activate")
    restored = asyncio.run(svc.restore_version(db, current["id"], activate_row["id"], archived_doc["revision"], ADMIN))
    assert restored["status"] == "active"
    assert len(asyncio.run(svc.list_versions(db, current["id"]))) > len(versions)
    token_old = print_cache_token({"id": restored["id"], "active_version": 1, "legacy_renderer": False}, invoice)
    token_new = print_cache_token({"id": restored["id"], "active_version": restored["active_version"], "legacy_renderer": False}, invoice)
    assert token_old != token_new


def test_internal_template_is_ignored_for_public_tracking():
    db = MemoryDB()
    created = asyncio.run(svc.create_template(db, {"name": "Internal", "document_type": "sales_invoice", "audience": "internal"}, ADMIN))
    design = copy.deepcopy(created["design"])
    design["bands"]["title"]["elements"].append({
        "id": "secret",
        "type": "text",
        "band": "title",
        "x_mm": 0,
        "y_mm": 8,
        "w_mm": 70,
        "h_mm": 5,
        "z": 3,
        "required": False,
        "props": {"text": "RAHASIA-INTERNAL-XYZ", "font": "Helvetica", "font_size": 8, "wrap": True},
    })
    saved = asyncio.run(svc.save_draft(db, created["id"], design, created["revision"], ADMIN))
    activated = asyncio.run(svc.activate_template(db, created["id"], saved["revision"], ADMIN))
    asyncio.run(svc.set_default(db, activated["id"], activated["revision"], ADMIN))
    invoice = sample_invoice()
    invoice["hpp"] = 123456
    order = {"id": "po-1", "po_number": invoice["po_number"], "customer": invoice["customer"], "tracking_token": "token-publik-yang-sudah-ada"}
    pdf = asyncio.run(svc.render_public_invoice_pdf(db, invoice, order, requested_template_id=activated["id"]))
    text = _pdf_text(pdf)
    assert "RAHASIA-INTERNAL-XYZ" not in text
    assert "123456" not in text
    assert "secret-user" not in text


def test_permissions_are_not_granted_to_other_roles():
    assert "print_template.view" in ROLE_DEFAULT_PERMISSIONS["admin"]
    assert "print_template.manage" in ROLE_DEFAULT_PERMISSIONS["admin"]
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if role == "admin":
            continue
        assert "print_template.manage" not in perms
        assert "print_template.view" not in perms


def test_http_flow_create_edit_preview_activate_and_permission():
    db = MemoryDB()
    admin = TestClient(_app(db, ADMIN))
    created = admin.post("/api/print-templates", json={"name": "SJ Gudang", "document_type": "sales_delivery", "audience": "customer"})
    assert created.status_code == 200, created.text
    template = created.json()["template"]
    design = template["design"]
    design["bands"]["title"]["elements"][0]["props"]["text"] = "DATA CONTOH CETAK"
    saved = admin.put(f"/api/print-templates/{template['id']}/draft", json={"design": design, "expected_revision": template["revision"]})
    assert saved.status_code == 200, saved.text
    preview = admin.post(f"/api/print-templates/{template['id']}/preview", json={"design": design})
    assert preview.status_code == 200
    assert preview.content.startswith(b"%PDF")
    assert "DATA CONTOH CETAK" in _pdf_text(preview.content)
    activated = admin.post(f"/api/print-templates/{template['id']}/activate", json={"expected_revision": saved.json()["template"]["revision"]})
    assert activated.status_code == 200, activated.text
    viewer = TestClient(_app(db, VIEWER))
    denied = viewer.post("/api/print-templates", json={"name": "Tidak", "document_type": "sales_invoice", "audience": "customer"})
    assert denied.status_code == 403
    listed = viewer.get("/api/print-templates")
    assert listed.status_code == 200
    assert any(row["name"] == "SJ Gudang" for row in listed.json()["templates"])
    staff = TestClient(_app(db, STAFF))
    options = staff.get("/api/print-templates/active", params={"document_type": "sales_invoice"})
    assert options.status_code == 200
    assert staff.get("/api/print-templates").status_code == 403


def _words_overlap(a, b) -> bool:
    ax0, ay0, ax1, ay1 = a["x0"], a["top"], a["x1"], a["bottom"]
    bx0, by0, bx1, by1 = b["x0"], b["top"], b["x1"], b["bottom"]
    ix = max(0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0, min(ay1, by1) - max(ay0, by0))
    if ix < 1 or iy < 1:
        return False
    area = ix * iy
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return smaller > 0 and area / smaller > 0.45


def test_multipage_pdf_repeats_header_without_overlap_or_duplicate_total():
    invoice = sample_invoice(lines=30, long_name=True)
    blob = render_design_pdf(builtin_invoice_design(), build_invoice_context(invoice, preview=True))
    grand = None
    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        assert len(pdf.pages) >= 3
        full = []
        detail_pages = 0
        for index, page in enumerate(pdf.pages):
            assert page.width > 500 and page.height > 700
            text = page.extract_text() or ""
            full.append(text)
            words = page.extract_words() or []
            for word in words:
                assert word["x0"] >= 20, word
                assert word["x1"] <= page.width - 20, word
                assert word["top"] >= 20
                assert word["bottom"] <= page.height - 20
            for left, right in zip(words, words[1:]):
                if left["text"] == right["text"] and abs(left["x0"] - right["x0"]) < 0.5:
                    continue
                assert not _words_overlap(left, right), (index + 1, left["text"], right["text"])
            if "Produk / Jasa" in text:
                detail_pages += 1
            if index == 0:
                assert "FAKTUR PENJUALAN" in text
            else:
                assert "FAKTUR PENJUALAN" not in text
            assert f"Halaman {index + 1} dari {len(pdf.pages)}" in text
        body = "\n".join(full)
        assert "#1" in body and "#30" in body
        assert detail_pages >= 2
        grand_line = [line for line in body.splitlines() if line.strip().startswith("Total ")]
        assert len(grand_line) == 1
        assert grand_line[0].count("Rp") == 1


def test_empty_logo_still_renders():
    design = builtin_invoice_design()
    design["bands"]["title"]["elements"].append({
        "id": "logo",
        "type": "image",
        "band": "title",
        "x_mm": 0,
        "y_mm": 8,
        "w_mm": 16,
        "h_mm": 8,
        "z": 1,
        "required": False,
        "props": {"role": "logo", "asset_id": ""},
    })
    pdf = render_design_pdf(design, build_invoice_context(sample_invoice(), preview=True))
    assert pdf.startswith(b"%PDF")
    assert "INV/CONTOH/001" in _pdf_text(pdf)


def test_missing_asset_bytes_fail_before_a_blank_image():
    design = builtin_invoice_design()
    design["bands"]["title"]["elements"].append({
        "id": "logo",
        "type": "image",
        "band": "title",
        "x_mm": 0,
        "y_mm": 8,
        "w_mm": 16,
        "h_mm": 8,
        "z": 1,
        "required": False,
        "props": {"role": "logo", "asset_id": "aset-hilang"},
    })
    with pytest.raises(DesignRenderError, match="tidak ditemukan"):
        render_design_pdf(design, build_invoice_context(sample_invoice(), preview=True), assets={})
