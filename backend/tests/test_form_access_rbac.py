"""Form access is existing RBAC — no second auth system, no permission rename."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS, effective_permissions
from services.division_tasks import po_needs_loading_task


def _catalog_keys():
    return {k for _mod, k, _lbl in PERMISSION_CATALOG}


def test_existing_submit_keys_unchanged():
    keys = _catalog_keys()
    assert "helper.prepare" in keys
    assert "packing.update" in keys
    assert "loading.update" in keys
    labels = {k: lbl for _mod, k, lbl in PERMISSION_CATALOG}
    assert labels["design.approve"] == "Approve desain"


def test_helper_defaults_cup_and_packing_not_loading():
    helper = set(ROLE_DEFAULT_PERMISSIONS["helper"])
    assert "helper.prepare" in helper
    assert "packing.update" in helper
    assert "loading.update" not in helper


def test_loading_optional_via_override_not_role_name():
    other = effective_permissions("operator", {"loading.update": True})
    assert "loading.update" in other
    assert "operator.update" in other
    helper = effective_permissions("helper", {"loading.update": False})
    assert "loading.update" not in helper


def test_packing_can_be_granted_without_helper_role():
    perms = effective_permissions("crm", {"packing.update": True})
    assert "packing.update" in perms
    assert "helper.prepare" not in perms


def test_crm_default_does_not_auto_grant_design_approve():
    crm = set(ROLE_DEFAULT_PERMISSIONS["crm"])
    assert "design.approve" not in crm
    granted = effective_permissions("crm", {"design.approve": True})
    assert "design.approve" in granted


def test_submit_endpoints_still_require_existing_perms():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert 'await _require_perm(user, "helper.prepare")' in src
    assert 'await _require_perm(user, "packing.update")' in src
    assert 'await _require_perm(user, "loading.update")' in src
    assert 'await _require_perm(user, "design.approve")' in src


def test_tugas_saya_action_defs_reuse_existing_keys():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert '("helper", "Persiapan Helper", "helper.prepare"' in src
    assert '("packing", "Packing", "packing.update"' in src
    assert '("loading", "Konfirmasi Loading", "loading.update"' in src
    assert '("design_approve", "Approval Design", "design.approve"' in src


def test_po_needs_loading_when_sj_and_remaining():
    o = {
        "delivery_status": "Belum Terkirim",
        "surat_jalan_created": True,
        "surat_jalan_files": [{"id": "f1", "name": "sj.pdf"}],
        "line_items": [{"name": "Karton", "quantity": 1000, "production_process": "print"}],
        "packing_info": {"qty_packed": 400, "lines": [{"line_idx": 0, "packed_qty": 400}]},
        "loading_info": {},
        "loading_confirmations": [],
    }
    assert po_needs_loading_task(o) is True
    o["loading_info"] = {"lines": [{"line_idx": 0, "loaded_qty": 400}]}
    assert po_needs_loading_task(o) is False
