"""Design Cetakan Phase 4B — sparse branch override on top of global settings."""
from __future__ import annotations

import copy
import io
import os
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_NAME", "eternal_packaging_design_cetakan_branch_test")

from services import storage as obj_storage  # noqa: E402
from services.order_pdf import generate_sales_order_pdf_bytes  # noqa: E402
from services.print_design_settings import (  # noqa: E402
    BRANCH_KEY_PREFIX,
    SETTINGS_KEY,
    SLOT_APPROVED,
    SLOT_COMPANY_LOGO,
    SLOT_PREPARED,
    branch_settings_key,
    get_branch_design_view,
    get_print_design_for_pdf,
    get_print_design_internal,
    list_design_cetakan_branches,
    load_slot_bytes,
    remove_branch_print_design_asset,
    reset_branch_design_settings,
    save_branch_design_settings,
    save_print_design_settings,
    upload_branch_print_design_asset,
    upload_print_design_asset,
)
from services.sales_deliveries import generate_sales_delivery_pdf_bytes  # noqa: E402
from services.sales_invoices import DOCUMENT_PROFORMA, generate_sales_invoice_pdf_bytes  # noqa: E402

USER = {"id": "u1", "email": "owner@eternalpackaging.com", "name": "Owner", "role": "admin"}


def _png(color=(200, 20, 20)) -> bytes:
    from PIL import Image as PILImage

    im = PILImage.new("RGB", (32, 16), color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _match(doc, query):
    for key, expected in (query or {}).items():
        if isinstance(expected, dict):
            if "$ne" in expected and doc.get(key) == expected["$ne"]:
                return False
            elif "$ne" not in expected and doc.get(key) != expected:
                return False
        elif doc.get(key) != expected:
            return False
    return True


class MockCollection:
    def __init__(self, initial=None):
        self._docs = copy.deepcopy(initial or [])

    async def find_one(self, query=None, projection=None):
        for doc in self._docs:
            if _match(doc, query):
                res = copy.deepcopy(doc)
                if projection and projection.get("_id") == 0 and "_id" in res:
                    res.pop("_id", None)
                return res
        return None

    async def update_one(self, query, update, upsert=False):
        set_vals = update.get("$set", {})
        for idx, doc in enumerate(self._docs):
            if _match(doc, query):
                self._docs[idx].update(copy.deepcopy(set_vals))
                return
        if upsert:
            new_doc = {k: v for k, v in (query or {}).items() if not str(k).startswith("$")}
            new_doc.update(copy.deepcopy(set_vals))
            self._docs.append(new_doc)

    async def insert_one(self, doc):
        self._docs.append(copy.deepcopy(doc))

    async def delete_one(self, query):
        for idx, doc in enumerate(self._docs):
            if _match(doc, query):
                self._docs.pop(idx)
                return

    async def distinct(self, field):
        return [doc.get(field) for doc in self._docs]


class MockDb:
    def __init__(self, orders=None, app_settings=None):
        self.orders = MockCollection(orders)
        self.app_settings = MockCollection(app_settings)
        self.files = MockCollection()


@pytest.fixture(autouse=True)
def _isolated_local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "objstore"))
    obj_storage._reset_provider_for_tests()
    yield
    obj_storage._reset_provider_for_tests()


def _orders():
    return [
        {"id": "o1", "branch_id": "50", "outlet": "Gudang A"},
        {"id": "o2", "branch_id": "50", "outlet": "Gudang B"},
        {"id": "o3", "branch_id": "77"},
        {"id": "o4", "branch_id": "Yogyakarta"},
        {"id": "o5", "branch_id": ""},
        {"id": "o6", "branch_id": None},
        {"id": "o7", "outlet": "50"},
        {"id": "o8", "branch_id": "50a"},
    ]


def _pdf_text(pdf_bytes: bytes) -> str:
    import pdfplumber

    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _docs(branch_id):
    order = {
        "po_number": "PO/1",
        "customer": "PT Contoh",
        "address": "Jl",
        "order_date": "2026-01-01",
        "line_items": [{"item_code": "ITM-1", "name": "Tas", "quantity": 1, "unit": "pcs"}],
        "branch_id": branch_id,
    }
    invoice = {
        "invoice_number": "INV/1",
        "invoice_date": "2026-01-02",
        "due_date": "2026-01-16",
        "customer": "PT Contoh",
        "address": "Jl",
        "po_number": "PO/1",
        "sales": "Sales",
        "subtotal": 1000,
        "discount_amount": 0,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "paid_total": 0,
        "grand_total": 1000,
        "lines": [{"item_code": "ITM-1", "item_name": "Tas", "quantity": 1, "unit_price": 1000, "line_subtotal": 1000}],
        "other_charges": [],
        "branch_id": branch_id,
    }
    delivery = {
        "sj_number": "SJ/1",
        "customer": "PT Contoh",
        "address": "Jl",
        "po_number": "PO/1",
        "delivery_date": "2026-01-03",
        "lines": [{"item_code": "ITM-1", "item_name": "Tas", "qty": 1, "unit": "pcs"}],
        "branch_id": branch_id,
    }
    return order, invoice, delivery


@pytest.mark.anyio
async def test_catalog_distinct_digit_ids_and_name_fallback():
    db = MockDb(_orders())

    async def lookup(bid):
        if bid == "50":
            return "Yogyakarta"
        if bid == "77":
            raise RuntimeError("accurate down")
        return None

    rows = await list_design_cetakan_branches(db, lookup)
    assert [row["branch_id"] for row in rows] == ["50", "77"]
    assert rows[0]["name"] == "Yogyakarta"
    assert rows[1]["name"] == "Cabang 77"
    assert all("outlet" not in row for row in rows)

    async def blank(_bid):
        return "  "

    rows_blank = await list_design_cetakan_branches(db, blank)
    assert rows_blank[0]["name"] == "Cabang 50"


@pytest.mark.anyio
async def test_branch_validation_known_unknown_and_nondigit():
    db = MockDb(_orders())
    assert branch_settings_key("50") == f"{BRANCH_KEY_PREFIX}50"
    view = await get_branch_design_view(db, "50")
    assert view["branch_id"] == "50"
    assert view["inherited"]["company_name"] == {"inherited": True, "overridden": False}

    with pytest.raises(HTTPException) as unknown:
        await save_branch_design_settings(db, "99", {"company_name": "Nope"}, user=USER)
    assert unknown.value.status_code == 400

    with pytest.raises(HTTPException) as nondigit:
        await save_branch_design_settings(db, "50a", {"company_name": "Nope"}, user=USER)
    assert nondigit.value.status_code == 400

    with pytest.raises(HTTPException):
        branch_settings_key("../design_cetakan")


@pytest.mark.anyio
async def test_sparse_override_inherits_empty_and_stays_sparse():
    db = MockDb(_orders())
    await save_print_design_settings(
        db,
        {
            "company_name": "Eternal Global",
            "address_lines": ["Alamat Global"],
            "accent_color": "#112233",
            "invoice": {
                "bank_accounts": [{"bank_label": "BRI", "account_number": "1", "account_name": "Global"}],
                "prepared_label": "Disiapkan Global",
                "prepared_footer": "Admin Global",
                "approved_label": "Disetujui Global",
                "approved_footer": "Pengelola Global",
            },
        },
        user=USER,
    )
    no_override = await get_print_design_for_pdf(db, branch_id="50")
    assert no_override["company_name"] == "Eternal Global"
    assert no_override["address_lines"] == ["Alamat Global"]

    await save_branch_design_settings(db, "50", {"company_name": "Eternal Jogja"}, user=USER)
    partial = await get_print_design_for_pdf(db, branch_id="50")
    assert partial["company_name"] == "Eternal Jogja"
    assert partial["address_lines"] == ["Alamat Global"]
    assert partial["accent_color"] == "#112233"

    await save_branch_design_settings(
        db,
        "50",
        {"company_name": "Eternal Jogja", "address_lines": ["Jl Jogja 1"]},
        user=USER,
    )
    addressed = await get_print_design_for_pdf(db, branch_id="50")
    assert addressed["address_lines"] == ["Jl Jogja 1"]

    await save_branch_design_settings(
        db,
        "50",
        {
            "company_name": "Eternal Jogja",
            "address_lines": ["Jl Jogja 1"],
            "invoice": {"bank_accounts": [{"bank_label": "BCA", "account_number": "9", "account_name": "Jogja"}]},
        },
        user=USER,
    )
    banked = await get_print_design_for_pdf(db, branch_id="50")
    assert banked["invoice"]["bank_accounts"][0]["bank_label"] == "BCA"
    assert banked["invoice"]["prepared_label"] == "Disiapkan Global"

    dirty = {
        "key": branch_settings_key("77"),
        "branch_id": "77",
        "company_name": "",
        "address_lines": [],
        "company_logo": None,
        "accent_color": "#ffffff",
        "invoice": {"bank_accounts": [], "prepared_label": None, "prepared_footer": "  "},
    }
    await db.app_settings.insert_one(dirty)
    inherited = await get_print_design_for_pdf(db, branch_id="77")
    assert inherited["company_name"] == "Eternal Global"
    assert inherited["address_lines"] == ["Alamat Global"]
    assert inherited["accent_color"] == "#112233"
    assert inherited["invoice"]["bank_accounts"][0]["account_name"] == "Global"
    status = (await get_branch_design_view(db, "77"))["inherited"]
    assert status["company_name"]["overridden"] is False
    assert status["bank_accounts"]["inherited"] is True

    await save_branch_design_settings(
        db,
        "50",
        {
            "company_name": "Eternal Jogja",
            "address_lines": ["", None],
            "accent_color": "#abcdef",
            "po": {"footer_name_label": "Tidak"},
            "invoice": {
                "bank_accounts": [],
                "estimasi_title": "Jangan",
                "prepared_label": "",
            },
            "surat_jalan": {"date_label": "Nope"},
            "company_logo": {"file_id": "x", "storage_path": "../secret"},
        },
        user=USER,
    )
    stored = await db.app_settings.find_one({"key": branch_settings_key("50")}, {"_id": 0})
    assert stored["company_name"] == "Eternal Jogja"
    assert "address_lines" not in stored
    assert "accent_color" not in stored
    assert "po" not in stored
    assert "surat_jalan" not in stored
    assert "company_logo" not in stored
    assert "bank_accounts" not in (stored.get("invoice") or {})
    assert "storage_path" not in str(stored)
    view = await get_branch_design_view(db, "50")
    assert view["inherited"]["company_name"]["overridden"] is True
    assert view["inherited"]["address_lines"]["inherited"] is True
    assert view["override"]["company_name"] == "Eternal Jogja"
    assert "storage_path" not in str(view["override"])
    assert "storage_path" not in str(view["effective"])


@pytest.mark.anyio
async def test_branch_saves_are_isolated_from_global_and_each_other():
    db = MockDb(_orders())
    await save_print_design_settings(db, {"company_name": "Eternal Global"}, user=USER)
    await save_branch_design_settings(db, "50", {"company_name": "Eternal Jogja"}, user=USER)
    await save_branch_design_settings(db, "77", {"company_name": "Eternal Semarang"}, user=USER)
    await save_branch_design_settings(db, "50", {"company_name": "Eternal Jogja 2"}, user=USER)

    global_doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    branch_b = await db.app_settings.find_one({"key": branch_settings_key("77")}, {"_id": 0})
    assert global_doc["company_name"] == "Eternal Global"
    assert branch_b["company_name"] == "Eternal Semarang"

    await save_print_design_settings(db, {"company_name": "Eternal Global 2"}, user=USER)
    branch_a = await db.app_settings.find_one({"key": branch_settings_key("50")}, {"_id": 0})
    branch_b = await db.app_settings.find_one({"key": branch_settings_key("77")}, {"_id": 0})
    assert branch_a["company_name"] == "Eternal Jogja 2"
    assert branch_b["company_name"] == "Eternal Semarang"
    assert (await get_print_design_internal(db))["company_name"] == "Eternal Global 2"


@pytest.mark.anyio
async def test_asset_inheritance_remove_and_reset_keep_global():
    db = MockDb(_orders())
    global_png = _png((10, 20, 30))
    branch_png = _png((200, 10, 10))
    prep_global = _png((1, 2, 3))
    prep_branch = _png((4, 5, 6))
    appr_global = _png((7, 8, 9))
    appr_branch = _png((9, 8, 7))
    await upload_print_design_asset(
        db, slot=SLOT_COMPANY_LOGO, body=global_png, content_type="image/png", filename="g.png", user=USER
    )
    await upload_print_design_asset(
        db, slot=SLOT_PREPARED, body=prep_global, content_type="image/png", filename="pg.png", user=USER
    )
    await upload_print_design_asset(
        db, slot=SLOT_APPROVED, body=appr_global, content_type="image/png", filename="ag.png", user=USER
    )
    await upload_branch_print_design_asset(
        db, branch_id="50", slot=SLOT_COMPANY_LOGO, body=branch_png, content_type="image/png", filename="b.png", user=USER
    )
    await upload_branch_print_design_asset(
        db, branch_id="50", slot=SLOT_PREPARED, body=prep_branch, content_type="image/png", filename="pb.png", user=USER
    )
    await upload_branch_print_design_asset(
        db, branch_id="50", slot=SLOT_APPROVED, body=appr_branch, content_type="image/png", filename="ab.png", user=USER
    )

    layered = await get_print_design_for_pdf(db, branch_id="50")
    assert layered["_pdf_assets"]["company_logo"] == branch_png
    assert layered["_pdf_assets"]["prepared_signature_image"] == prep_branch
    assert layered["_pdf_assets"]["approved_signature_image"] == appr_branch
    assert (await get_branch_design_view(db, "50"))["inherited"]["company_logo"]["overridden"] is True

    global_only = await get_print_design_for_pdf(db, branch_id="77")
    assert global_only["_pdf_assets"]["company_logo"] == global_png
    assert global_only["_pdf_assets"]["prepared_signature_image"] == prep_global
    assert global_only["_pdf_assets"]["approved_signature_image"] == appr_global

    branch_logo_path = (await db.app_settings.find_one({"key": branch_settings_key("50")}))["company_logo"]["storage_path"]
    obj_storage.delete_object(branch_logo_path)
    fallback = await get_print_design_for_pdf(db, branch_id="50")
    assert fallback["_pdf_assets"]["company_logo"] == global_png

    await remove_branch_print_design_asset(db, branch_id="50", slot=SLOT_COMPANY_LOGO, user=USER)
    after_remove = await get_print_design_for_pdf(db, branch_id="50")
    assert after_remove["_pdf_assets"]["company_logo"] == global_png
    global_bytes, _media, _name = await load_slot_bytes(db, SLOT_COMPANY_LOGO)
    assert global_bytes == global_png
    removed_view = await get_branch_design_view(db, "50")
    assert removed_view["inherited"]["company_logo"]["inherited"] is True
    assert removed_view["inherited"]["prepared_signature_image"]["overridden"] is True

    await reset_branch_design_settings(db, "50", user=USER)
    assert await db.app_settings.find_one({"key": branch_settings_key("50")}) is None
    reset_view = await get_branch_design_view(db, "50")
    assert reset_view["override"] == {}
    assert all(item["inherited"] for item in reset_view["inherited"].values())
    effective = await get_print_design_for_pdf(db, branch_id="50")
    assert effective["_pdf_assets"]["company_logo"] == global_png
    assert effective["_pdf_assets"]["prepared_signature_image"] == prep_global
    assert effective["_pdf_assets"]["approved_signature_image"] == appr_global
    assert (await load_slot_bytes(db, SLOT_PREPARED))[0] == prep_global
    assert (await load_slot_bytes(db, SLOT_APPROVED))[0] == appr_global
    global_doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    assert global_doc["company_logo"]["storage_path"]
    assert global_doc["invoice"]["prepared_signature_image"]["storage_path"]


@pytest.mark.anyio
async def test_pdf_uses_document_branch_not_session_and_falls_back_when_missing():
    db = MockDb(_orders())
    await save_print_design_settings(db, {"company_name": "Eternal Global"}, user=USER)
    await save_branch_design_settings(db, "50", {"company_name": "Eternal Jogja"}, user=USER)

    session_user = {"branch_id": "77", "outlet": "50"}
    design_50 = await get_print_design_for_pdf(db, branch_id="50")
    design_77 = await get_print_design_for_pdf(db, branch_id="77")
    design_missing = await get_print_design_for_pdf(db, branch_id=None)
    design_invalid = await get_print_design_for_pdf(db, branch_id="cabang-jogja")
    assert session_user["branch_id"] == "77"
    assert design_50["company_name"] == "Eternal Jogja"
    assert design_77["company_name"] == "Eternal Global"
    assert design_missing["company_name"] == "Eternal Global"
    assert design_invalid["company_name"] == "Eternal Global"

    order, invoice, delivery = _docs("50")
    po_50 = generate_sales_order_pdf_bytes(order, design=design_50)
    assert po_50.startswith(b"%PDF")
    assert design_50["company_name"] == "Eternal Jogja"
    assert "Eternal Jogja" in _pdf_text(generate_sales_invoice_pdf_bytes(invoice, design=design_50))
    assert "Eternal Jogja" in _pdf_text(
        generate_sales_invoice_pdf_bytes(invoice, document_type=DOCUMENT_PROFORMA, design=design_50)
    )
    assert "Eternal Jogja" in _pdf_text(generate_sales_delivery_pdf_bytes(delivery, design=design_50))

    _order77, invoice77, delivery77 = _docs("77")
    assert "Eternal Global" in _pdf_text(generate_sales_invoice_pdf_bytes(invoice77, design=design_77))
    assert "Eternal Jogja" not in _pdf_text(generate_sales_invoice_pdf_bytes(invoice77, design=design_77))
    assert "Eternal Global" in _pdf_text(generate_sales_delivery_pdf_bytes(delivery77, design=design_77))

    bare_order, bare_invoice, bare_delivery = _docs(None)
    bare_order.pop("branch_id")
    bare_invoice.pop("branch_id")
    bare_delivery.pop("branch_id")
    bare = await get_print_design_for_pdf(db, branch_id=bare_order.get("branch_id"))
    assert bare["company_name"] == "Eternal Global"

    await reset_branch_design_settings(db, "50", user=USER)
    reset_design = await get_print_design_for_pdf(db, branch_id="50")
    assert reset_design["company_name"] == "Eternal Global"
    assert "Eternal Global" in _pdf_text(generate_sales_invoice_pdf_bytes(invoice, design=reset_design))


def test_pdf_and_permission_call_sites_use_document_branch():
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    deliveries = (ROOT / "services" / "sales_deliveries.py").read_text(encoding="utf-8")
    assert 'get_print_design_for_pdf(db, branch_id=order.get("branch_id"))' in server
    assert 'get_print_design_for_pdf(db, branch_id=(detail or {}).get("branch_id"))' in server
    assert 'get_print_design_for_pdf(db, branch_id=(invoice or {}).get("branch_id"))' in server
    assert 'get_print_design_for_pdf(db, branch_id=delivery_doc.get("branch_id"))' in server
    assert deliveries.count('get_print_design_for_pdf(db, branch_id=(delivery or {}).get("branch_id"))') == 2
    assert "user.get(\"branch" not in deliveries
    assert 'lookup_branch_display_name' in server

    def _block(name):
        idx = server.index(f"async def {name}")
        nxt = server.find("\nasync def ", idx + 10)
        return server[idx:nxt if nxt != -1 else idx + 1200]

    helper = server[server.index("def _design_cetakan_view_perms"):server.index("def _design_cetakan_view_perms") + 250]
    assert "settings.design_cetakan.view" in helper
    assert "settings.design_cetakan.manage" in helper
    for name in (
        "design_cetakan_branches_get",
        "design_cetakan_branch_get",
        "design_cetakan_branch_asset_preview",
    ):
        assert "_design_cetakan_view_perms()" in _block(name)
    for name in (
        "design_cetakan_branch_put",
        "design_cetakan_branch_reset",
        "design_cetakan_branch_asset_upload",
        "design_cetakan_branch_asset_remove",
    ):
        block = _block(name)
        assert "settings.design_cetakan.manage" in block
        assert "_design_cetakan_view_perms" not in block
        assert "settings.design_cetakan.view" not in block
    assert "settings.design_cetakan.branch_update" in server
    assert "settings.design_cetakan.branch_reset" in server
    assert "settings.design_cetakan.branch_asset_upload" in server
    assert '"branch_id": view.get("branch_id")' in server
