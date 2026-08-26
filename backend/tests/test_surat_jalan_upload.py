"""Surat Jalan file upload card + remaining allocation helper."""
from quantity_rules import remaining_delivery_note_updates


def test_remaining_delivery_note_updates_used_for_upload_scope():
    order = {
        "line_items": [{"name": "A", "quantity": 1000, "production_process": "print"}],
        "packing_info": {"lines": [{"line_idx": 0, "packed_qty": 1000}], "qty_packed": 1000},
        "shipments": [],
    }
    updates = remaining_delivery_note_updates(order)
    assert updates
    assert sum(int(u.get("qty") or 0) for u in updates) == 1000


def test_surat_jalan_panel_source_is_upload_only():
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "SuratJalanPanel.jsx"
    text = src.read_text(encoding="utf-8")
    assert "Upload File Surat Jalan" in text
    assert "surat-jalan-number-input" not in text
    assert "previewAccurateSuratJalan" not in text
