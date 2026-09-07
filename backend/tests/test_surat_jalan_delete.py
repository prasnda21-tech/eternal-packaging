"""Hapus File Surat Jalan — permission + exact-file delete, no cascade."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS, effective_permissions
from quantity_rules import (
    find_sj_file,
    qty_packed,
    surat_jalan_created,
    surat_jalan_status_semantic,
    validate_loading_confirmation,
)
from services.surat_jalan_files import (
    PERM_DELETE_FILE,
    PERM_MANAGE,
    PERM_VIEW_FILE,
    history_entries,
    remove_file_from_order,
)


def test_delete_permission_is_new_granular_key():
    assert PERM_DELETE_FILE == "delivery_note.delete_file"
    assert PERM_DELETE_FILE in ALL_PERMISSIONS
    assert PERM_DELETE_FILE != PERM_MANAGE
    labels = {k: lbl for _m, k, lbl in PERMISSION_CATALOG}
    assert labels[PERM_DELETE_FILE] == "Hapus File Surat Jalan"


def test_upload_yes_delete_no_for_manage_only():
    crm = set(ROLE_DEFAULT_PERMISSIONS["crm"])
    assert PERM_MANAGE in crm
    assert PERM_DELETE_FILE not in crm
    helper_upload = effective_permissions("helper", {PERM_MANAGE: True, PERM_DELETE_FILE: False})
    assert PERM_MANAGE in helper_upload
    assert PERM_DELETE_FILE not in helper_upload


def test_delete_yes_independent_of_manage():
    granted = effective_permissions("operator", {PERM_DELETE_FILE: True, PERM_MANAGE: False})
    assert PERM_DELETE_FILE in granted
    assert PERM_MANAGE not in granted


def test_super_admin_mechanism_is_all_permissions_not_role_name():
    admin = set(ROLE_DEFAULT_PERMISSIONS["admin"])
    assert admin == set(ALL_PERMISSIONS)
    assert PERM_DELETE_FILE in admin
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    block_start = src.index("async def delete_surat_jalan_file(")
    nxt = src.find("\nasync def ", block_start + 10)
    block = src[block_start:nxt]
    assert "require_perm(_SJ_DELETE_FILE)" in block or 'require_perm("delivery_note.delete_file")' in block
    assert 'require("admin"' not in block
    assert "is_super_admin" not in block
    assert "Aksi ini tidak tersedia untuk role Anda" not in block


def test_remove_exact_file_does_not_cascade():
    order = {
        "surat_jalan_files": [
            {"id": "a", "name": "one.pdf", "uploaded_at": "t1", "uploaded_by": "u1"},
            {"id": "b", "name": "two.pdf", "uploaded_at": "t2", "uploaded_by": "u2"},
        ],
        "loading_confirmations": [{"id": "c1", "surat_jalan_id": "a", "lines": [{"line_idx": 0, "qty": 10}]}],
        "delivery_history": [{"item": "A"}],
        "delivery_note_number": "SJ-1",
        "delivery_note_created_at": "t1",
        "delivery_note_created_by": "u1",
    }
    removed = remove_file_from_order(order, "a")
    assert removed["id"] == "a"
    assert [f["id"] for f in order["surat_jalan_files"]] == ["b"]
    assert order["loading_confirmations"][0]["id"] == "c1"
    assert order["delivery_history"][0]["item"] == "A"
    assert surat_jalan_created(order) is True


def test_last_file_delete_clears_sj_status():
    order = {
        "surat_jalan_files": [
            {"id": "only", "name": "sj.pdf", "storage_path": "p", "uploaded_at": "t", "uploaded_by": "u"},
        ],
        "shipments": [],
        "delivery_note_number": "SJ-1",
        "delivery_note_created_at": "t",
        "delivery_note_created_by": "u",
        "loading_confirmations": [{"id": "c1", "surat_jalan_id": "only", "lines": [{"line_idx": 0, "qty": 10}]}],
        "line_items": [{"name": "A", "quantity": 10}],
        "packing_info": {"qty_packed": 10, "lines": [{"line_idx": 0, "packed_qty": 10}]},
    }
    assert surat_jalan_created(order)
    assert remove_file_from_order(order, "missing") is None
    removed = remove_file_from_order(order, "only")
    assert removed["id"] == "only"
    assert order["surat_jalan_files"] == []
    assert order["delivery_note_number"] is None
    assert order["loading_confirmations"]
    assert surat_jalan_created(order) is False
    assert surat_jalan_status_semantic(order) != "completed"


def test_endpoint_is_authenticated_permission_404():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "@api_router.delete(\"/orders/{order_id}/surat-jalan/files/{file_id}\")" in src
    assert "File Surat Jalan tidak ditemukan" in src
    frontend = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "SuratJalanPanel.jsx"
    text = frontend.read_text(encoding="utf-8")
    assert "Hapus File" in text
    assert "Hapus file Surat Jalan ini?" in text
    assert "SJ_DELETE_FILE_PERM" in text


def test_delete_exact_file_does_not_resurface_via_shipment_or_history():
    order = {
        "id": "po-a",
        "line_items": [
            {"name": "Cup", "quantity": 100},
            {"name": "Cup", "quantity": 50},
        ],
        "packing_info": {"qty_packed": 80, "lines": [{"line_idx": 0, "packed_qty": 80}, {"line_idx": 1, "packed_qty": 0}]},
        "surat_jalan_files": [
            {"id": "sj-keep", "name": "keep.pdf", "storage_path": "p-keep", "uploaded_at": "t1", "lines": [{"line_idx": 0, "qty": 10}]},
            {"id": "sj-gone", "name": "gone.pdf", "storage_path": "p-gone", "shipment_id": "sh-gone", "sj_number": "SJ-GONE", "uploaded_at": "t2", "lines": [{"line_idx": 1, "qty": 5}]},
        ],
        "shipments": [
            {"id": "sh-keep", "number": "SJ-KEEP", "file_id": "sj-keep", "lines": [{"line_idx": 0, "qty": 10}]},
            {"id": "sh-gone", "number": "SJ-GONE", "file_id": "sj-gone", "sales_delivery_id": "sj-gone", "lines": [{"line_idx": 1, "qty": 5}]},
        ],
        "loading_confirmations": [{"id": "c1", "surat_jalan_id": "sj-keep", "lines": [{"line_idx": 0, "qty": 10}]}],
    }
    other = {
        "id": "po-b",
        "surat_jalan_files": [{"id": "other-sj", "name": "other.pdf", "storage_path": "p-o"}],
        "shipments": [{"id": "other-sj", "file_id": "other-sj"}],
    }
    packed_before = qty_packed(order)
    loading_before = list(order["loading_confirmations"])
    removed = remove_file_from_order(order, "sj-gone")
    assert removed["id"] == "sj-gone"
    assert [f["id"] for f in order["surat_jalan_files"]] == ["sj-keep"]
    assert find_sj_file(order, "sj-gone") is None
    assert find_sj_file(order, "sj-keep")["id"] == "sj-keep"
    assert all(e.get("file_id") != "sj-gone" and e.get("id") != "sj-gone" for e in history_entries(order))
    assert any((e.get("file_id") or e.get("id")) == "sj-keep" for e in history_entries(order))
    gone_ship = next(sh for sh in order["shipments"] if sh["id"] == "sh-gone")
    assert gone_ship.get("file_id") in (None, "")
    assert gone_ship["lines"][0]["qty"] == 5
    assert qty_packed(order) == packed_before
    assert order["loading_confirmations"] == loading_before
    err = validate_loading_confirmation(
        {**order, "packing_info": {"qty_packed": 80, "lines": [{"line_idx": 0, "packed_qty": 80}, {"line_idx": 1, "packed_qty": 50}]}},
        "sj-gone",
        [{"line_idx": 1, "qty": 1}],
    )
    assert err
    assert "tidak ditemukan" in err.lower()
    assert other["surat_jalan_files"][0]["id"] == "other-sj"
    assert other["shipments"][0]["file_id"] == "other-sj"


def test_delete_does_not_mix_same_name_lines():
    order = {
        "line_items": [{"name": "Same", "quantity": 10}, {"name": "Same", "quantity": 20}],
        "surat_jalan_files": [
            {"id": "a", "name": "a.pdf", "storage_path": "pa", "lines": [{"line_idx": 0, "qty": 10}]},
            {"id": "b", "name": "b.pdf", "storage_path": "pb", "lines": [{"line_idx": 1, "qty": 20}]},
        ],
    }
    remove_file_from_order(order, "a")
    kept = order["surat_jalan_files"][0]
    assert kept["id"] == "b"
    assert kept["lines"][0]["line_idx"] == 1
    assert kept["lines"][0]["qty"] == 20
