"""Loading restored as SJ-linked item/qty confirmation. Historical loading preserved."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    apply_delivery_from_proof_evidence,
    apply_loading_confirmation,
    eligible_delivery_qty,
    qty_loaded,
    qty_loaded_for_line,
    validate_delivery_qty,
    validate_loading_confirmation,
)
from services.customer_tracking import customer_workflow, public_payload
from services.delivery_stops import remaining_eligible_qty
from services.division_tasks import (
    DIVISION_DEFS,
    build_division_tasks,
    resolve_division,
)
from services.work_panel_settings import default_divisions
from permissions import (
    ALL_PERMISSIONS,
    NAV_MENU_ITEMS,
    ROLE_DEFAULT_PERMISSIONS,
    derived_nav_permissions,
)


def _new_po(**extra):
    o = {
        "id": extra.pop("id", "ord-new"),
        "po_number": extra.pop("po_number", "PO/NEW"),
        "customer": extra.pop("customer", "Cust"),
        "cancelled": False,
        "current_stage": 12,
        "delivery_status": "Belum Terkirim",
        "line_items": [{"name": "Karton", "quantity": extra.get("total_quantity", 1000), "production_process": "print"}],
        "total_quantity": extra.pop("total_quantity", 1000),
        "packing_info": extra.pop("packing_info", {
            "qty_packed": extra.pop("packed", 0),
            "lines": [{"line_idx": 0, "packed_qty": extra.pop("packed_line", 0)}],
        }),
        "shipments": extra.pop("shipments", []),
        "surat_jalan_files": extra.pop("surat_jalan_files", [{
            "id": "f-sj-1",
            "name": "sj.pdf",
            "uploaded_at": "2026-08-27T10:00:00+07:00",
            "uploaded_by_name": "Admin",
        }]),
        "delivery_note_number": "SJ-1",
        "delivery_note_created_at": "2026-08-27T10:00:00+07:00",
        "delivery_note_created_by": "admin@x.com",
        "surat_jalan_created": True,
        "qty_delivered": extra.pop("qty_delivered", 0),
        "delivery_proofs": extra.pop("delivery_proofs", []),
        "loading_info": extra.pop("loading_info", {}),
        "loading_confirmations": extra.pop("loading_confirmations", []),
    }
    o.update(extra)
    return o


def _legacy_loading_po():
    return _new_po(
        id="ord-legacy",
        packing_info={"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        shipments=[{"id": "sj-1", "number": "SJ-1", "file_id": "f-sj-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
        surat_jalan_files=[{
            "id": "f-sj-1", "name": "sj.pdf", "uploaded_at": "2026-08-27T10:00:00+07:00",
            "uploaded_by_name": "Admin", "lines": [{"line_idx": 0, "qty": 1000}], "shipment_id": "sj-1",
        }],
        loading_info={
            "lines": [{"line_idx": 0, "loaded_qty": 400}],
            "events": [{"line_idx": 0, "qty": 400, "loaded_qty_after": 400}],
        },
        loading_checklist={"items_loaded": True, "seal_ok": True, "driver_name": "Budi"},
    )


def test_a_loading_task_present_after_sj():
    assert "loading" in DIVISION_DEFS
    assert resolve_division("loading") == "loading"
    ids = [r["id"] for r in default_divisions()]
    assert "loading" in ids
    rows = build_division_tasks([_new_po()], "loading", {"email": "a@x.com"}, mine_only=True)
    assert len(rows) == 1


def test_b_delivery_requires_confirmed_loading_not_raw_po():
    o = _new_po()
    o["loading_info"] = {}
    assert eligible_delivery_qty(o) == 0
    apply_loading_confirmation(
        o, surat_jalan_id="f-sj-1", lines=[{"line_idx": 0, "qty": 400}],
        actor={"email": "d@x.com", "name": "Driver"}, confirmation_id="c1",
    )
    assert eligible_delivery_qty(o) == 400
    assert remaining_eligible_qty(o) == 400
    ev = apply_delivery_from_proof_evidence(o)
    assert ev is not None
    assert ev["qty_delivered"] == 400


def test_c_driver_without_create_schedule():
    o = _new_po()
    apply_loading_confirmation(
        o, surat_jalan_id="f-sj-1", lines=[{"line_idx": 0, "qty": 400}],
        actor={"email": "d@x.com"}, confirmation_id="c1",
    )
    delivery_rows = build_division_tasks([o], "terkirim", {"email": "driver@x.com"}, mine_only=False)
    assert len(delivery_rows) == 1
    src = (Path(__file__).resolve().parents[1] / "services" / "delivery_route_api.py").read_text(encoding="utf-8")
    assert "CREATE_SCHEDULE_INACTIVE" in src


def test_d_partial_cumulative_loading_and_delivery():
    o = _new_po(surat_jalan_files=[
        {"id": "f1", "name": "a.pdf"},
        {"id": "f2", "name": "b.pdf"},
    ])
    apply_loading_confirmation(o, surat_jalan_id="f1", lines=[{"line_idx": 0, "qty": 400}], confirmation_id="c1")
    apply_loading_confirmation(o, surat_jalan_id="f2", lines=[{"line_idx": 0, "qty": 300}], confirmation_id="c2")
    assert eligible_delivery_qty(o) == 700
    ev1 = apply_delivery_from_proof_evidence(o)
    assert ev1["qty_delivered"] == 700
    o["qty_delivered"] = 400
    assert remaining_eligible_qty(o) == 300
    assert len(o["loading_confirmations"]) == 2


def test_e_historical_loading_records_unchanged_readable():
    original = _legacy_loading_po()
    snapshot = copy.deepcopy(original["loading_info"])
    assert qty_loaded(original) == 400
    assert qty_loaded_for_line(original, 0) == 400
    assert original["loading_checklist"]["driver_name"] == "Budi"
    assert original["loading_info"] == snapshot


def test_g_driver_authorization_preserved():
    driver = set(ROLE_DEFAULT_PERMISSIONS["driver"])
    assert "delivery.update" in driver
    assert "loading.update" in driver
    assert "delivery_note.view_file" in driver


def test_h_no_delivery_over_confirmed_loading():
    o = _new_po()
    apply_loading_confirmation(o, surat_jalan_id="f-sj-1", lines=[{"line_idx": 0, "qty": 400}], confirmation_id="c1")
    assert eligible_delivery_qty(o) == 400
    assert validate_delivery_qty(400, o) is None
    assert validate_delivery_qty(401, o) is not None
    ev = apply_delivery_from_proof_evidence(o)
    assert ev["qty_delivered"] == 400


def test_over_confirm_rejected():
    o = _new_po()
    apply_loading_confirmation(o, surat_jalan_id="f-sj-1", lines=[{"line_idx": 0, "qty": 400}], confirmation_id="c1")
    err = validate_loading_confirmation(o, "f-sj-1", [{"line_idx": 0, "qty": 700}])
    assert err


def test_nav_loading_in_user_facing_catalog():
    keys = [k for k, _l, _p in NAV_MENU_ITEMS]
    assert "nav.tasks.loading" in keys
    assert "nav.tasks.loading" in ALL_PERMISSIONS
    derived = derived_nav_permissions({"loading.update"})
    assert "nav.tasks.loading" in derived


def test_public_tracking_has_no_loading_milestone():
    names = [r["stage"] for r in customer_workflow(_new_po())]
    assert "Loading" not in names
    assert "Surat Jalan" in names
    assert "Terkirim" in names
    payload = public_payload(_new_po(customer="PT Contoh"))
    assert "loading_info" not in payload
    assert "loaded_qty" not in str(payload)


def test_historical_sj_qty_still_deliverable():
    o = _new_po(
        shipments=[{"id": "sj-1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 1000}]}],
        packing_info={"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
    )
    assert eligible_delivery_qty(o) == 1000
