"""Unit tests for division work-panel settings merge (no live DB)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.work_panel_settings import default_divisions, merge_settings, sanitize_put


def test_defaults_include_operator_active():
    rows = default_divisions()
    ids = [r["id"] for r in rows]
    assert ids == ["screener", "helper", "operator", "packing", "surat_jalan", "terkirim"]
    op = next(r for r in rows if r["id"] == "operator")
    assert op["active"] is True
    assert op["implemented"] is True
    assert op["input_label"] == "Qty Cetak"
    assert op["target_source"] == "print_line_qty"
    assert op["entry_mode"] == "append_only"
    assert op["confirm_required"] is True


def test_missing_record_uses_safe_defaults():
    merged = merge_settings(None)
    op = next(r for r in merged["divisions"] if r["id"] == "operator")
    assert op["active"] is True
    assert op["input_label"] == "Qty Cetak"


def test_label_change_does_not_unlock_overwrite():
    merged = merge_settings({
        "divisions": [{
            "id": "operator",
            "input_label": "Qty Produksi",
            "entry_mode": "overwrite",
            "confirm_required": False,
            "target_source": "invented_field",
            "submit_perm": "admin",
        }],
    })
    op = next(r for r in merged["divisions"] if r["id"] == "operator")
    assert op["input_label"] == "Qty Produksi"
    assert op["entry_mode"] == "append_only"
    assert op["confirm_required"] is True
    assert op["target_source"] == "print_line_qty"
    assert op["submit_perm"] == "operator.update"


def test_allowed_target_source_is_kept():
    merged = sanitize_put({
        "divisions": [{"id": "operator", "target_source": "split_allocation", "active": True}],
    })
    op = next(r for r in merged["divisions"] if r["id"] == "operator")
    assert op["target_source"] == "split_allocation"


def test_other_divisions_default_implemented_and_active():
    merged = sanitize_put({
        "divisions": [{"id": "packing", "active": True}],
    })
    pk = next(r for r in merged["divisions"] if r["id"] == "packing")
    assert pk["active"] is True
    assert pk["implemented"] is True
    assert pk["input_kind"] == "qty"
    sj = next(r for r in merged["divisions"] if r["id"] == "surat_jalan")
    assert sj["input_kind"] == "evidence"
    assert sj["confirm_required"] is True
    assert sj["entry_mode"] == "append_only"


def test_stub_settings_upgrade_activates_new_divisions():
    merged = merge_settings({
        "divisions": [{"id": "screener", "active": False, "implemented": False}],
    })
    sc = next(r for r in merged["divisions"] if r["id"] == "screener")
    assert sc["implemented"] is True
    assert sc["active"] is True


def test_put_can_deactivate_after_implementation():
    merged = sanitize_put({
        "divisions": [{"id": "screener", "active": False}],
    })
    sc = next(r for r in merged["divisions"] if r["id"] == "screener")
    assert sc["implemented"] is True
    assert sc["active"] is False


def test_helper_default_label():
    hp = next(r for r in default_divisions() if r["id"] == "helper")
    assert hp["input_label"] == "Qty Prepared"
    assert hp["active"] is True
    assert hp["implemented"] is True
