"""Driver/Terkirim upload + confirm must be permission-based, not role-name."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS, effective_permissions
from quantity_rules import MIN_DELIVERY_PROOFS, can_confirm_delivery, confirm_delivery_block_reason


def _server_src() -> str:
    return (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")


def _fn_block(src: str, name: str) -> str:
    marker = f"async def {name}("
    start = src.index(marker)
    nxt = src.find("\nasync def ", start + len(marker))
    nxt_class = src.find("\nclass ", start + len(marker))
    ends = [i for i in (nxt, nxt_class) if i != -1]
    end = min(ends) if ends else len(src)
    return src[start:end]


def test_delivery_proof_endpoints_use_delivery_update_not_role_names():
    src = _server_src()
    upload = _fn_block(src, "upload_delivery_proof")
    delete = _fn_block(src, "delete_delivery_proof")
    confirm = _fn_block(src, "confirm_delivered")
    for block in (upload, delete):
        assert 'require_perm("delivery.update")' in block
        assert 'require("admin", "head_production", "driver")' not in block
        assert "Aksi ini tidak tersedia untuk role Anda" not in block
    assert 'await _require_perm(user, "delivery.update")' in confirm
    assert 'require("admin"' not in confirm
    assert "Aksi ini tidak tersedia untuk role Anda" not in confirm


def test_super_admin_effective_set_includes_delivery_update():
    assert "delivery.update" in ALL_PERMISSIONS
    admin = set(ROLE_DEFAULT_PERMISSIONS["admin"])
    assert "delivery.update" in admin
    assert admin == set(ALL_PERMISSIONS)


def test_driver_and_other_roles_can_share_delivery_update():
    driver = set(ROLE_DEFAULT_PERMISSIONS["driver"])
    assert "delivery.update" in driver
    hop = set(ROLE_DEFAULT_PERMISSIONS["head_operations"])
    assert "delivery.update" in hop
    crm = effective_permissions("crm", {"delivery.update": True})
    assert "delivery.update" in crm
    operator = set(ROLE_DEFAULT_PERMISSIONS["operator"])
    assert "delivery.update" not in operator
    helper = set(ROLE_DEFAULT_PERMISSIONS["helper"])
    assert "delivery.update" not in helper


def test_delete_keeps_driver_own_photo_ownership_rule():
    delete = _fn_block(_server_src(), "delete_delivery_proof")
    assert 'user.get("role") == "driver"' in delete
    assert "Driver hanya dapat menghapus bukti yang di-upload sendiri" in delete


def test_confirm_still_needs_two_photos_not_role():
    assert MIN_DELIVERY_PROOFS == 2
    order = {
        "line_items": [{"name": "A", "quantity": 1000, "production_process": "print"}],
        "total_quantity": 1000,
        "packing_info": {"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        "shipments": [{"id": "s1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
        "delivery_proofs": [{"id": "p1"}],
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
    }
    assert can_confirm_delivery(order) is False
    assert confirm_delivery_block_reason(order)
    order["delivery_proofs"].append({"id": "p2"})
    assert can_confirm_delivery(order) is True
    assert confirm_delivery_block_reason(order) is None
