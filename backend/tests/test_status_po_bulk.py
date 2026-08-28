"""Bulk cancel/delete response shape and Status PO bulk wiring (no live API)."""
from pathlib import Path


def test_bulk_endpoints_return_succeeded_failed_without_new_delete_path():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    cancel = src[src.find("async def bulk_cancel"):src.find("async def bulk_delete")]
    delete = src[src.find("async def bulk_delete"):src.find("# ---- Helper qty")]
    assert 'await _require_perm(user, "po.cancel")' in cancel
    assert 'await _require_perm(user, "po.delete")' in delete
    assert '"succeeded": succeeded' in cancel
    assert '"failed": failed' in cancel
    assert '"succeeded": succeeded' in delete
    assert '"failed": failed' in delete
    assert "PO tidak ditemukan" in cancel
    assert "PO tidak ditemukan" in delete
    assert "dict.fromkeys(payload.ids)" in cancel
    assert "dict.fromkeys(payload.ids)" in delete
    assert 'confirm or "").strip().upper() != "HAPUS"' in delete
    assert "_audit_po" in delete
    assert 'action="po.bulk_delete"' in delete
    assert 'action="po.bulk_cancel"' in cancel


def test_status_po_page_uses_existing_bulk_apis():
    src = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "pages"
        / "StatusPO.jsx"
    ).read_text(encoding="utf-8")
    assert "api.bulkCancel" in src
    assert "api.bulkDelete" in src
    assert "inFlight.current" in src
    assert "po.cancel" in src
    assert "po.delete" in src
