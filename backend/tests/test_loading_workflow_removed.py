"""Loading is no longer an active workflow.

Historical loading records stay readable. Delivery uses packed/SJ eligible qty.
No live API / Mongo required.
"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantity_rules import (
    apply_delivery_from_proof_evidence,
    eligible_delivery_qty,
    qty_loaded,
    qty_loaded_for_line,
    validate_delivery_qty,
)
from services.customer_tracking import customer_workflow, public_payload
from services.delivery_stops import remaining_eligible_qty, build_queue_stops
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
    packed = extra.pop("packed_line", extra.pop("packed", 1000))
    shipped = extra.pop("shipped", packed)
    o = {
        "id": extra.pop("id", "ord-new"),
        "po_number": extra.pop("po_number", "PO/NEW"),
        "customer": extra.pop("customer", "Cust"),
        "cancelled": False,
        "current_stage": 12,
        "delivery_status": "Belum Terkirim",
        "line_items": [{"name": "Karton", "quantity": extra.get("total_quantity", 1000), "production_process": "print"}],
        "total_quantity": extra.pop("total_quantity", 1000),
        "packing_info": {
            "qty_packed": packed,
            "lines": [{"line_idx": 0, "packed_qty": packed}],
        },
        "shipments": extra.pop("shipments", [{
            "id": "sj-1",
            "number": "SJ-1",
            "lines": [{"line_idx": 0, "qty": shipped}],
        }]),
        "delivery_note_number": "SJ-1",
        "delivery_note_created_at": "2026-08-27T10:00:00+07:00",
        "delivery_note_created_by": "admin@x.com",
        "surat_jalan_created": True,
        "qty_delivered": extra.pop("qty_delivered", 0),
        "delivery_proofs": extra.pop("delivery_proofs", []),
        "loading_info": extra.pop("loading_info", {}),
    }
    o.update(extra)
    return o


def _legacy_loading_po():
    return _new_po(
        id="ord-legacy",
        packed_line=1000,
        loading_info={
            "lines": [{"line_idx": 0, "loaded_qty": 400}],
            "events": [{"line_idx": 0, "qty": 400, "loaded_qty_after": 400}],
        },
        loading_checklist={"items_loaded": True, "seal_ok": True, "driver_name": "Budi"},
    )


def test_a_new_shipment_loading_task_absent():
    assert "loading" not in DIVISION_DEFS
    assert resolve_division("loading") is None
    ids = [r["id"] for r in default_divisions()]
    assert "loading" not in ids
    rows = build_division_tasks([_new_po()], "loading", {"email": "a@x.com"}, mine_only=True)
    assert rows == []


def test_b_delivery_eligibility_does_not_require_loaded_qty():
    o = _new_po(packed_line=1000)
    o["loading_info"] = {}
    assert qty_loaded(o) == 0
    assert eligible_delivery_qty(o) == 1000
    assert remaining_eligible_qty(o) == 1000
    ev = apply_delivery_from_proof_evidence(o)
    assert ev is not None
    assert ev["qty_delivered"] == 1000


def test_c_surat_jalan_to_driver_without_loading():
    o = _new_po(packed_line=1000)
    assert remaining_eligible_qty(o) > 0
    rows = build_queue_stops([o])
    assert len(rows) >= 1
    assert rows[0]["qty"] == 1000
    delivery_rows = build_division_tasks([o], "terkirim", {"email": "driver@x.com"}, mine_only=False)
    assert len(delivery_rows) == 1
    sj_rows = build_division_tasks([o], "surat_jalan", {"email": "a@x.com"}, mine_only=False)
    assert len(sj_rows) == 1


def test_d_partial_delivery_preserved():
    o = _new_po(
        packed_line=1000,
        qty_delivered=0,
        shipments=[
            {"id": "sj-1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 400}]},
            {"id": "sj-2", "number": "SJ-2", "lines": [{"line_idx": 0, "qty": 300}]},
        ],
    )
    assert eligible_delivery_qty(o) == 700
    ev1 = apply_delivery_from_proof_evidence(o)
    assert ev1["qty_delivered"] == 700
    o["qty_delivered"] = 400
    assert remaining_eligible_qty(o) == 300
    ev2 = apply_delivery_from_proof_evidence(o)
    assert ev2["qty_delivered"] == 700
    o["qty_delivered"] = 700
    ev3 = apply_delivery_from_proof_evidence(o)
    assert ev3["qty_delivered"] == 700
    assert remaining_eligible_qty(o) == 0


def test_e_historical_loading_records_unchanged_readable():
    original = _legacy_loading_po()
    snapshot = copy.deepcopy(original["loading_info"])
    assert qty_loaded(original) == 400
    assert qty_loaded_for_line(original, 0) == 400
    assert original["loading_checklist"]["driver_name"] == "Budi"
    assert eligible_delivery_qty(original) == 1000
    assert original["loading_info"] == snapshot


def test_f_route_ready_stop_not_blocked_by_loading():
    o = _new_po(
        packed_line=4000,
        total_quantity=10000,
        shipments=[{"id": "sh-1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 4000}]}],
        loading_info={},
        delivery_location={"lat": -6.2, "lng": 106.8, "place_id": "ChIJA"},
        address="Jl. Contoh 1",
        deadline="2026-08-30",
    )
    o["line_items"][0]["quantity"] = 10000
    assert remaining_eligible_qty(o) == 4000
    rows = build_queue_stops([o])
    assert len(rows) == 1
    assert rows[0]["qty"] == 4000


def test_g_driver_authorization_preserved():
    driver = set(ROLE_DEFAULT_PERMISSIONS["driver"])
    assert "delivery.update" in driver
    assert "loading.update" in driver


def test_h_no_delivery_over_eligible_qty():
    o = _new_po(
        packed_line=1000,
        shipments=[{"id": "sj-1", "number": "SJ-1", "lines": [{"line_idx": 0, "qty": 400}]}],
    )
    assert eligible_delivery_qty(o) == 400
    assert validate_delivery_qty(400, o) is None
    assert validate_delivery_qty(401, o) is not None
    ev = apply_delivery_from_proof_evidence(o)
    assert ev["qty_delivered"] == 400


def test_nav_loading_removed_from_user_facing_catalog():
    keys = [k for k, _l, _p in NAV_MENU_ITEMS]
    assert "nav.tasks.loading" not in keys
    assert "nav.tasks.loading" not in ALL_PERMISSIONS
    derived = derived_nav_permissions({"loading.update"})
    assert "nav.tasks.loading" not in derived
    labels = [l for _k, l, _p in NAV_MENU_ITEMS]
    assert "Loading" not in labels


def test_public_tracking_has_no_loading_milestone():
    names = [r["stage"] for r in customer_workflow(_new_po())]
    assert "Loading" not in names
    assert "Surat Jalan" in names
    assert "Terkirim" in names
    payload = public_payload(_new_po(customer="PT Contoh"))
    assert "loading_info" not in payload
    assert "loaded_qty" not in str(payload)
