"""Design Cetakan Phase 5A — saved-config PDF preview with dummy documents."""
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

os.environ.setdefault("DB_NAME", "eternal_packaging_design_cetakan_preview_test")

from services import storage as obj_storage  # noqa: E402
from services.print_design_preview import render_print_design_preview  # noqa: E402
from services.print_design_settings import (  # noqa: E402
    SLOT_COMPANY_LOGO,
    save_branch_design_settings,
    save_print_design_settings,
    upload_branch_print_design_asset,
    upload_print_design_asset,
)
from services.sales_invoices import PROFORMA_WARNING  # noqa: E402

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
        self.writes = 0

    async def find_one(self, query=None, projection=None):
        for doc in self._docs:
            if _match(doc, query):
                res = copy.deepcopy(doc)
                if projection and projection.get("_id") == 0 and "_id" in res:
                    res.pop("_id", None)
                return res
        return None

    async def update_one(self, query, update, upsert=False):
        self.writes += 1
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
        self.writes += 1
        self._docs.append(copy.deepcopy(doc))

    async def delete_one(self, query):
        self.writes += 1
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
        self.invoices = MockCollection()
        self.sales_invoices = MockCollection()
        self.sales_deliveries = MockCollection()


@pytest.fixture(autouse=True)
def _isolated_local_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "objstore"))
    obj_storage._reset_provider_for_tests()
    yield
    obj_storage._reset_provider_for_tests()


def _orders():
    return [
        {"id": "o1", "branch_id": "50"},
        {"id": "o2", "branch_id": "77"},
    ]


def _pdf_text(pdf_bytes: bytes) -> str:
    import pdfplumber

    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _writes(db: MockDb) -> int:
    return (
        db.orders.writes
        + db.app_settings.writes
        + db.files.writes
        + db.invoices.writes
        + db.sales_invoices.writes
        + db.sales_deliveries.writes
    )


@pytest.mark.anyio
async def test_all_preview_types_return_pdf_and_do_not_write():
    db = MockDb(_orders())
    before = _writes(db)
    for kind in ("po", "invoice", "proforma", "delivery"):
        pdf = await render_print_design_preview(db, kind)
        assert pdf.startswith(b"%PDF")
    proforma = await render_print_design_preview(db, "proforma")
    assert "PROFORMA INVOICE" in _pdf_text(proforma)
    assert PROFORMA_WARNING in _pdf_text(proforma)
    assert _writes(db) == before
    with pytest.raises(HTTPException) as unknown:
        await render_print_design_preview(db, "receipt")
    assert unknown.value.status_code == 400


@pytest.mark.anyio
async def test_global_and_branch_saved_config_applied():
    db = MockDb(_orders())
    await save_print_design_settings(db, {"company_name": "Eternal Global"}, user=USER)
    await save_branch_design_settings(db, "50", {"company_name": "Eternal Jogja"}, user=USER)
    writes_after_setup = _writes(db)

    session_user = {"branch_id": "77", "outlet": "50"}
    global_inv = await render_print_design_preview(db, "invoice")
    branch_50 = await render_print_design_preview(db, "invoice", branch_id="50")
    branch_77 = await render_print_design_preview(db, "invoice", branch_id="77")
    assert session_user["branch_id"] == "77"
    assert "Eternal Global" in _pdf_text(global_inv)
    assert "Eternal Jogja" in _pdf_text(branch_50)
    assert "Eternal Global" in _pdf_text(branch_77)
    assert "Eternal Jogja" not in _pdf_text(branch_77)
    assert _writes(db) == writes_after_setup

    with pytest.raises(HTTPException) as unknown:
        await render_print_design_preview(db, "invoice", branch_id="99")
    assert unknown.value.status_code == 400
    with pytest.raises(HTTPException) as nondigit:
        await render_print_design_preview(db, "invoice", branch_id="50a")
    assert nondigit.value.status_code == 400
    assert _writes(db) == writes_after_setup


@pytest.mark.anyio
async def test_preview_uses_selected_branch_not_session_and_resolves_assets(monkeypatch):
    db = MockDb(_orders())
    await save_print_design_settings(db, {"company_name": "Eternal Global"}, user=USER)
    branch_png = _png((10, 80, 200))
    global_png = _png((10, 20, 30))
    await upload_print_design_asset(
        db, slot=SLOT_COMPANY_LOGO, body=global_png, content_type="image/png", filename="g.png", user=USER
    )
    await upload_branch_print_design_asset(
        db, branch_id="50", slot=SLOT_COMPANY_LOGO, body=branch_png, content_type="image/png", filename="b.png", user=USER
    )
    captured = []
    from services import print_design_settings as pds

    original = pds.get_print_design_for_pdf

    async def wrapped(db_arg, branch_id=None):
        captured.append(branch_id)
        return await original(db_arg, branch_id=branch_id)

    monkeypatch.setattr("services.print_design_preview.get_print_design_for_pdf", wrapped)

    user = {"branch_id": "77"}
    pdf = await render_print_design_preview(db, "po", branch_id="50")
    assert pdf.startswith(b"%PDF")
    assert captured == ["50"]
    assert user["branch_id"] == "77"

    design = await original(db, branch_id="50")
    assert design["_pdf_assets"]["company_logo"] == branch_png
    design_77 = await original(db, branch_id="77")
    assert design_77["_pdf_assets"]["company_logo"] == global_png


def test_preview_route_is_view_only_and_does_not_store():
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    preview = (ROOT / "services" / "print_design_preview.py").read_text(encoding="utf-8")
    assert '@api_router.get("/settings/design-cetakan/preview/{document_type}")' in server
    idx = server.index("async def design_cetakan_preview")
    nxt = server.find("\nasync def ", idx + 10)
    block = server[idx:nxt]
    helper = server[server.index("def _design_cetakan_view_perms"):server.index("def _design_cetakan_view_perms") + 220]
    assert "_design_cetakan_view_perms()" in block
    assert "settings.design_cetakan.view" in helper
    assert "Cache-Control" in block and "private, no-store" in block
    assert "application/pdf" in block
    assert "inline; filename=" in block
    assert "put_object" not in preview
    assert "ensure_generated_sj" not in preview
    assert "create_sales" not in preview
    assert "insert_one" not in preview
    assert "update_one" not in preview
    assert "DOCUMENT_PROFORMA" in preview
    assert "generate_sales_order_pdf_bytes" in preview
    assert "generate_sales_invoice_pdf_bytes" in preview
    assert "generate_sales_delivery_pdf_bytes" in preview
    body = preview.split("async def render_print_design_preview")[1][:900]
    assert "user" not in body
