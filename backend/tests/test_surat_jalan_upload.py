"""Surat Jalan file-only upload, view_file permission, Loading confirm gate."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS, effective_permissions
from quantity_rules import (
    apply_loading_confirmation,
    eligible_delivery_qty,
    validate_loading_confirmation,
    validate_delivery_qty,
)
from services.delivery_stops import remaining_eligible_qty
from services.surat_jalan_files import (
    PERM_VIEW_FILE,
    sniffer_content_type,
    strip_sj_bytes_from_order,
)
from services.division_tasks import build_division_tasks, DIVISION_DEFS


def test_surat_jalan_panel_source_is_upload_only():
    src = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "SuratJalanPanel.jsx"
    text = src.read_text(encoding="utf-8")
    assert "Pilih File" in text or "Upload Surat Jalan" in text
    assert "surat-jalan-number-input" not in text
    assert "sj-qty-input" not in text
    assert "sj-item-select" not in text
    assert "Menunggu Packing" not in text


def test_catalog_has_view_file_permission():
    assert PERM_VIEW_FILE in ALL_PERMISSIONS
    assert PERM_VIEW_FILE == "delivery_note.view_file"
    assert "delivery_note.manage" in ALL_PERMISSIONS
    assert PERM_VIEW_FILE != "delivery_note.manage"
    assert "loading.update" in ALL_PERMISSIONS
    assert "delivery.schedule.create" not in ALL_PERMISSIONS


def test_rbac_upload_view_loading_are_independent():
    a = effective_permissions("helper", {"delivery_note.manage": True, PERM_VIEW_FILE: False, "loading.update": False})
    assert "delivery_note.manage" in a
    assert PERM_VIEW_FILE not in a
    assert "loading.update" not in a
    b = effective_permissions("helper", {"delivery_note.manage": False, PERM_VIEW_FILE: True, "loading.update": True})
    assert "delivery_note.manage" not in b
    assert PERM_VIEW_FILE in b
    assert "loading.update" in b
    c = effective_permissions("helper", {"delivery_note.manage": True, PERM_VIEW_FILE: True, "loading.update": True})
    assert "delivery_note.manage" in c and PERM_VIEW_FILE in c and "loading.update" in c
    d = set(ROLE_DEFAULT_PERMISSIONS["helper"])
    assert "delivery_note.manage" not in d and PERM_VIEW_FILE not in d
    driver = set(ROLE_DEFAULT_PERMISSIONS["driver"])
    assert "delivery.update" in driver
    assert PERM_VIEW_FILE in driver
    assert "delivery_note.manage" not in driver
    assert "loading.update" in driver


def test_file_endpoint_requires_view_file_perm():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "/orders/{order_id}/surat-jalan/files/{file_id}" in src
    assert "delivery_note.view_file" in src
    assert 'rec.get("kind") == "surat_jalan"' in src
    assert "application/pdf" in src
    assert "loading-confirm" in src


def test_create_schedule_api_disabled():
    src = (Path(__file__).resolve().parents[1] / "services" / "delivery_route_api.py").read_text(encoding="utf-8")
    assert "CREATE_SCHEDULE_INACTIVE" in src
    assert 'raise HTTPException(410, CREATE_SCHEDULE_INACTIVE)' in src


def _po(**extra):
    o = {
        "id": extra.pop("id", "ord-sj"),
        "po_number": "PO/SJ",
        "customer": "Cust",
        "cancelled": False,
        "current_stage": 8,
        "delivery_status": "Belum Terkirim",
        "line_items": [{"name": "Item A", "item_code": "A1", "quantity": 1000}],
        "total_quantity": 1000,
        "packing_info": extra.pop("packing_info", {}),
        "shipments": extra.pop("shipments", []),
        "surat_jalan_files": extra.pop("surat_jalan_files", []),
        "loading_confirmations": extra.pop("loading_confirmations", []),
        "qty_delivered": extra.pop("qty_delivered", 0),
    }
    o.update(extra)
    return o


def test_upload_not_blocked_by_packing_in_attachment_handler():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    att = src[src.find("async def add_attachment"):src.find("async def add_note")]
    assert "Item dan Qty Surat Jalan wajib diisi" not in att
    assert "tahap Packing belum selesai" not in att
    assert "loaded_qty" not in att
    assert "File-only" in att or "file-only" in att.lower() or "Item/Qty is confirmed later" in att
    assert "Tanggal pengiriman wajib" in att
    assert 'order["delivery_note_created_at"] = op_iso' in att
    assert "uploaded_at" in att


def test_sj_file_activates_loading_task():
    o = _po(surat_jalan_files=[{"id": "f1", "name": "sj.pdf", "uploaded_at": "2026-08-27T10:00:00+07:00"}])
    rows = build_division_tasks([o], "loading", {"email": "a@x.com"}, mine_only=False)
    assert len(rows) == 1
    assert rows[0]["status"] in ("siap", "belum")
    assert "loading" in DIVISION_DEFS


def test_loading_requires_linked_sj_and_positive_qty():
    o = _po(surat_jalan_files=[{"id": "f1", "name": "sj.pdf"}])
    assert validate_loading_confirmation(o, "missing", [{"line_idx": 0, "qty": 10}])
    assert validate_loading_confirmation(o, "f1", [])
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 0}])
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 100}]) is None


def test_loading_cumulative_and_over_confirm():
    o = _po(surat_jalan_files=[{"id": "f1", "name": "a.pdf"}, {"id": "f2", "name": "b.pdf"}])
    apply_loading_confirmation(o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 400}], actor={"email": "a@x.com", "name": "A"}, confirmation_id="c1", confirmed_at="t1")
    apply_loading_confirmation(o, surat_jalan_id="f2", lines=[{"line_idx": 0, "qty": 300}], actor={"email": "a@x.com", "name": "A"}, confirmation_id="c2", confirmed_at="t2")
    assert eligible_delivery_qty(o) == 700
    assert remaining_eligible_qty(o) == 700
    err = validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 301}])
    assert err
    assert validate_loading_confirmation(o, "f1", [{"line_idx": 0, "qty": 300}]) is None
    assert len(o["loading_confirmations"]) == 2


def test_delivery_bounded_by_confirmed_loading():
    o = _po(
        packing_info={"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        surat_jalan_files=[{"id": "f1", "name": "sj.pdf"}],
    )
    apply_loading_confirmation(o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 400}], actor={"email": "d@x.com"}, confirmation_id="c1")
    assert eligible_delivery_qty(o) == 400
    assert validate_delivery_qty(400, o) is None
    assert validate_delivery_qty(401, o) is not None


def test_pdf_sniffer_forces_application_pdf():
    assert sniffer_content_type(b"%PDF-1.4 rest", "sj.bin", "application/octet-stream") == "application/pdf"
    assert sniffer_content_type(b"xxxx", "sj.pdf", "") == "application/pdf"


def test_strip_does_not_expose_data_url():
    o = _po(surat_jalan_files=[{
        "id": "f1", "name": "sj.pdf",
        "data_url": "data:application/pdf;base64,AAA",
        "storage_path": "secret/path",
        "url": "https://cdn.example/sj.pdf",
        "uploaded_by_name": "A",
    }])
    pub = strip_sj_bytes_from_order(o)
    dumped = str(pub.get("surat_jalan_files"))
    assert "data:application/pdf" not in dumped
    assert "secret/path" not in dumped
    assert "cdn.example" not in dumped
    assert o["surat_jalan_files"][0]["data_url"].startswith("data:")


def test_no_route_optimization_reintroduced_in_sj_files():
    src = Path(__file__).resolve().parents[1] / "services" / "surat_jalan_files.py"
    text = src.read_text(encoding="utf-8")
    assert "optimize" not in text.lower()
    assert "rute saya" not in text.lower()
