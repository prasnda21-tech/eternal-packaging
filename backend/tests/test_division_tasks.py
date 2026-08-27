"""Division task list: own assignment, line identity, qty semantics."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.division_tasks import (
    TASK_PROJECTION,
    build_delivery_tasks,
    build_designer_tasks,
    build_division_tasks,
    build_helper_tasks,
    build_kepala_produksi_tasks,
    build_operator_tasks,
    build_packing_tasks,
    build_surat_jalan_tasks,
    can_view_all_division,
    resolve_division,
)


def _print_po(**kwargs):
    o = {
        "id": "po1",
        "po_number": "PO-001",
        "customer": "Cust A",
        "cancelled": False,
        "line_items": [
            {"id": "L1", "name": "Cup A", "quantity": 5000, "production_process": "print"},
            {"id": "L2", "name": "Cup A", "quantity": 3000, "production_process": "print"},
            {"id": "L3", "name": "Non", "quantity": 2000, "production_process": "non_print"},
        ],
        "operator_splits": [
            {"id": "sBudi", "line_idx": 0, "operator_email": "budi@x.com", "operator_name": "Budi", "quantity": 4000, "qty_completed": 1500},
            {"id": "sAndi", "line_idx": 0, "operator_email": "andi@x.com", "operator_name": "Andi", "quantity": 1000, "qty_completed": 0},
        ],
        "helper_prep": {"qty_prepared": 2000, "items": [{"line_idx": 0, "qty_prepared": 2000}]},
        "screener_info": {"screen_qty": 0},
        "packing_info": {},
        "delivery_proofs": [],
    }
    o.update(kwargs)
    return o


def test_resolve_slug():
    assert resolve_division("surat-jalan") == "surat_jalan"
    assert resolve_division("delivery") == "terkirim"
    assert resolve_division("operator") == "operator"
    assert resolve_division("designer") == "designer"
    assert resolve_division("kepala-produksi") == "kepala_produksi"
    assert resolve_division("kepala_produksi") == "kepala_produksi"


def test_view_all_uses_existing_perms_not_role_string():
    assert can_view_all_division(["operator.update"]) is False
    assert can_view_all_division(["schedule.edit"]) is True
    assert can_view_all_division(["dashboard.production.view"]) is True


def test_operator_mine_excludes_other_assignment():
    o = _print_po()
    budi = {"email": "budi@x.com", "id": "u-budi"}
    rows = build_operator_tasks(o, budi, mine_only=True)
    assert len(rows) == 1
    assert rows[0]["assigned_user_email"] == "budi@x.com"
    assert rows[0]["target_qty"] == 4000
    assert rows[0]["completed_qty"] == 1500
    assert rows[0]["remaining_qty"] == 2500
    assert rows[0]["required_input"] == "Qty Cetak"
    andi_ids = [r["assigned_user_email"] for r in rows]
    assert "andi@x.com" not in andi_ids


def test_operator_does_not_use_line_aggregate_5000():
    o = _print_po()
    rows = build_operator_tasks(o, {"email": "budi@x.com"}, mine_only=True)
    assert rows[0]["target_qty"] != 5000
    assert rows[0]["target_qty"] == 4000


def test_operator_view_all_shows_both():
    o = _print_po()
    rows = build_operator_tasks(o, {"email": "budi@x.com"}, mine_only=False)
    assert sorted(r["target_qty"] for r in rows) == [1000, 4000]


def test_helper_target_is_active_schedule_not_po_total():
    o = _print_po()
    rows = build_helper_tasks(o)
    line0 = next(r for r in rows if r["line_idx"] == 0)
    assert line0["target_qty"] == 5000  # 4000+1000 active schedule
    assert line0["completed_qty"] == 2000
    assert line0["remaining_qty"] == 3000
    assert line0["required_input"] == "Qty Prepared"
    assert not any(r["line_idx"] == 2 for r in rows)


def test_helper_same_item_different_line_not_merged():
    o = _print_po(operator_splits=[
        {"id": "a", "line_idx": 0, "quantity": 5000, "qty_completed": 0, "operator_email": "budi@x.com"},
        {"id": "b", "line_idx": 1, "quantity": 3000, "qty_completed": 0, "operator_email": "andi@x.com"},
    ], helper_prep={"items": [
        {"line_idx": 0, "qty_prepared": 5000},
        {"line_idx": 1, "qty_prepared": 1000},
    ]})
    rows = build_helper_tasks(o)
    by = {r["line_id"]: r["target_qty"] for r in rows}
    assert by["L1"] == 5000
    assert by["L2"] == 3000
    assert 8000 not in by.values()


def test_packing_remaining_and_no_sebagian_button_field():
    o = _print_po(
        operator_splits=[{"id": "s", "line_idx": 0, "quantity": 5000, "qty_completed": 5000}],
        packing_info={"lines": [{"line_idx": 0, "packed_qty": 2000}], "qty_packed": 2000},
        line_items=[{"id": "L1", "name": "Cup A", "quantity": 5000, "production_process": "print"}],
    )
    rows = build_packing_tasks(o)
    assert rows
    r = rows[0]
    assert r["completed_qty"] == 2000
    assert r["remaining_qty"] == 3000 or r["target_qty"] - r["completed_qty"] == r["remaining_qty"]
    assert r["required_input"] == "Qty Packing"
    joined = " ".join(r.get("available_actions") or [])
    assert "Packing Sebagian" not in joined
    assert "sebagian" not in (r.get("action_label") or "").lower()


def test_surat_jalan_upload_only_copy():
    o = _print_po(
        packing_info={"lines": [{"line_idx": 0, "packed_qty": 1000}], "qty_packed": 1000},
        surat_jalan_files=[],
    )
    rows = build_surat_jalan_tasks(o)
    assert rows[0]["required_input"] == "Upload File Surat Jalan"
    assert rows[0]["required_input_type"] == "upload_sj"


def test_driver_one_photo_requires_upload():
    o = _print_po(
        packing_info={"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        surat_jalan_created=True,
        delivery_proofs=[{"id": "p1"}],
    )
    rows = build_delivery_tasks(o)
    assert rows[0]["required_input"] == "Upload Foto"
    assert rows[0]["status"] == "sebagian"
    assert "confirm_terkirim" not in rows[0]["available_actions"]


def test_driver_two_photos_requires_confirm_not_auto_delivered():
    o = _print_po(
        packing_info={"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        surat_jalan_created=True,
        delivery_proofs=[{"id": "p1"}, {"id": "p2"}],
        delivery_status="Belum",
        delivered_at=None,
    )
    rows = build_delivery_tasks(o)
    assert rows[0]["required_input"] == "Konfirmasi Terkirim"
    assert rows[0]["status"] == "siap"
    assert "confirm_terkirim" in rows[0]["available_actions"]
    assert rows[0]["status"] != "selesai"


def test_screener_required_input():
    o = _print_po()
    rows = build_division_tasks([o], "screener", {"email": "s@x.com"}, mine_only=True)
    assert rows
    assert rows[0]["required_input"] == "Qty Screener"
    assert rows[0]["target_qty"] == 8000


def test_loading_division_present_after_sj():
    o = _print_po(
        packing_info={"qty_packed": 5000, "lines": [{"line_idx": 0, "packed_qty": 5000}]},
        loading_info={"qty_loaded": 2000, "lines": [{"line_idx": 0, "loaded_qty": 2000}]},
        surat_jalan_created=True,
        surat_jalan_files=[{"id": "f-sj-1", "name": "sj.pdf"}],
        line_items=[{"id": "L1", "name": "Cup A", "quantity": 5000, "production_process": "print"}],
        operator_splits=[{"id": "s", "line_idx": 0, "quantity": 5000, "qty_completed": 5000}],
    )
    assert resolve_division("loading") == "loading"
    rows = build_division_tasks([o], "loading", {"email": "a@x.com"}, mine_only=False)
    assert len(rows) == 1


def test_cancelled_orders_excluded():
    o = _print_po(cancelled=True)
    rows = build_division_tasks([o], "operator", {"email": "budi@x.com"}, mine_only=True)
    assert rows == []


def _ready_print_po(**kwargs):
    return _print_po(
        design_status="Approved",
        payment_status="Approved",
        **kwargs,
    )


def test_designer_needs_upload_design():
    o = _print_po(design_files=[], design_status="Waiting")
    rows = build_designer_tasks(o)
    assert len(rows) == 1
    assert rows[0]["required_input"] == "Upload Design"
    assert rows[0]["action_label"] == "Kerjakan"
    assert rows[0]["status"] == "belum"
    assert rows[0]["design_status_label"] == "Belum ada design"
    assert rows[0]["target_qty"] is None
    assert rows[0]["assigned_user_id"] is None


def test_designer_upload_done_waiting_approval():
    o = _print_po(
        design_files=[{"id": "d1", "name": "a.png", "uploaded_at": "2026-08-26T08:00:00+00:00"}],
        design_status="Waiting",
    )
    rows = build_designer_tasks(o)
    assert rows[0]["status"] == "selesai"
    assert rows[0]["required_input"] == "Upload Design"
    assert rows[0]["design_status_label"] == "Menunggu approval"


def test_designer_customer_revisi_needs_reupload():
    o = _print_po(
        design_files=[{"id": "d1", "name": "a.png", "uploaded_at": "2026-08-26T08:00:00+00:00"}],
        design_status="Waiting",
        customer_approval={"status": "rejected", "responded_at": "2026-08-26T09:00:00+00:00"},
    )
    rows = build_designer_tasks(o)
    assert rows[0]["status"] == "belum"
    assert rows[0]["required_input"] == "Upload Design"
    assert rows[0]["design_status_label"] == "Revisi"


def test_designer_reupload_after_revisi_clears_need():
    o = _print_po(
        design_files=[
            {"id": "d1", "name": "a.png", "uploaded_at": "2026-08-26T08:00:00+00:00"},
            {"id": "d2", "name": "b.png", "uploaded_at": "2026-08-26T10:00:00+00:00"},
        ],
        design_status="Waiting",
        customer_approval={"status": "rejected", "responded_at": "2026-08-26T09:00:00+00:00"},
    )
    rows = build_designer_tasks(o)
    assert rows[0]["status"] == "selesai"
    assert rows[0]["design_status_label"] == "Menunggu approval"


def test_designer_bypass_without_file_is_not_a_task():
    o = _print_po(design_files=[], design_bypassed=True)
    assert build_designer_tasks(o) == []


def test_designer_no_fake_assignment():
    o = _print_po(design_files=[])
    rows = build_designer_tasks(o)
    assert rows[0]["assigned_user_email"] is None
    assert rows[0]["assigned_user_id"] is None


def test_projection_omits_design_file_bytes():
    assert "design_files.id" in TASK_PROJECTION
    assert "design_files" not in TASK_PROJECTION
    joined = " ".join(str(k) for k in TASK_PROJECTION)
    assert "data_url" not in joined


def test_kepala_produksi_case_a_no_schedule():
    o = _ready_print_po(
        operator_splits=[],
        line_items=[{"id": "L1", "name": "Cup A", "quantity": 5000, "production_process": "print"}],
    )
    rows = build_kepala_produksi_tasks(o)
    assert len(rows) == 1
    assert rows[0]["target_qty"] == 5000
    assert rows[0]["completed_qty"] == 0
    assert rows[0]["remaining_qty"] == 5000
    assert rows[0]["required_input"] == "Penjadwalan"
    assert rows[0]["action_label"] == "Jadwalkan"
    assert rows[0]["status"] == "belum"


def test_kepala_produksi_case_b_partial_one_operator():
    o = _ready_print_po(
        operator_splits=[{"id": "a", "line_idx": 0, "quantity": 4000, "operator_email": "a@x.com"}],
        line_items=[{"id": "L1", "name": "Cup A", "quantity": 5000, "production_process": "print"}],
    )
    rows = build_kepala_produksi_tasks(o)
    assert len(rows) == 1
    assert rows[0]["completed_qty"] == 4000
    assert rows[0]["remaining_qty"] == 1000
    assert rows[0]["required_input"] == "Penjadwalan"


def test_kepala_produksi_case_c_multi_operator_one_line_task():
    o = _ready_print_po(
        operator_splits=[
            {"id": "a", "line_idx": 0, "quantity": 4000, "operator_email": "a@x.com"},
            {"id": "b", "line_idx": 0, "quantity": 1000, "operator_email": "b@x.com"},
        ],
        line_items=[{"id": "L1", "name": "Cup A", "quantity": 5000, "production_process": "print"}],
    )
    rows = build_kepala_produksi_tasks(o)
    assert len(rows) == 1
    assert rows[0]["target_qty"] == 5000
    assert rows[0]["completed_qty"] == 5000
    assert rows[0]["remaining_qty"] == 0
    assert rows[0]["status"] == "selesai"
    assert rows[0]["status_label"] == "Selesai Dijadwalkan"


def test_kepala_produksi_case_d_cancel_reopens_remainder():
    o = _ready_print_po(
        operator_splits=[
            {"id": "a", "line_idx": 0, "quantity": 3000, "qty_cancelled": 1000, "operator_email": "a@x.com"},
            {"id": "b", "line_idx": 0, "quantity": 1000, "operator_email": "b@x.com"},
        ],
        line_items=[{"id": "L1", "name": "Cup A", "quantity": 5000, "production_process": "print"}],
    )
    rows = build_kepala_produksi_tasks(o)
    assert len(rows) == 1
    assert rows[0]["completed_qty"] == 4000
    assert rows[0]["remaining_qty"] == 1000
    assert rows[0]["required_input"] == "Penjadwalan"
    assert rows[0]["status"] == "sebagian"


def test_kepala_produksi_case_e_reassign_fills_line():
    o = _ready_print_po(
        operator_splits=[
            {"id": "a", "line_idx": 0, "quantity": 3000, "operator_email": "a@x.com"},
            {"id": "b", "line_idx": 0, "quantity": 2000, "operator_email": "b@x.com"},
        ],
        line_items=[{"id": "L1", "name": "Cup A", "quantity": 5000, "production_process": "print"}],
    )
    rows = build_kepala_produksi_tasks(o)
    assert len(rows) == 1
    assert rows[0]["completed_qty"] == 5000
    assert rows[0]["remaining_qty"] == 0


def test_kepala_produksi_non_print_excluded():
    o = _ready_print_po()
    rows = build_kepala_produksi_tasks(o)
    assert all(r["line_idx"] != 2 for r in rows)
    assert not any("Non" == r["item"] for r in rows)


def test_kepala_produksi_full_non_print_po_excluded():
    o = _ready_print_po(
        line_items=[{"id": "L3", "name": "Non", "quantity": 2000, "production_process": "non_print"}],
        operator_splits=[],
    )
    assert build_kepala_produksi_tasks(o) == []


def test_kepala_produksi_waits_for_design_payment():
    o = _print_po(
        design_status="Waiting",
        payment_status="Waiting",
        operator_splits=[],
        line_items=[{"id": "L1", "name": "Cup A", "quantity": 5000, "production_process": "print"}],
    )
    assert build_kepala_produksi_tasks(o) == []


def test_kepala_produksi_same_name_lines_not_merged():
    o = _ready_print_po()
    rows = build_kepala_produksi_tasks(o)
    assert len(rows) == 2
    by = {r["line_id"]: r for r in rows}
    assert set(by) == {"L1", "L2"}
    assert by["L1"]["completed_qty"] == 5000
    assert by["L1"]["remaining_qty"] == 0
    assert by["L2"]["completed_qty"] == 0
    assert by["L2"]["remaining_qty"] == 3000
    assert by["L2"]["required_input"] == "Penjadwalan"
    o = _ready_print_po(
        helper_prep={"qty_prepared": 2000, "items": [{"line_idx": 0, "qty_prepared": 2000}]},
        operator_splits=[{"id": "a", "line_idx": 0, "quantity": 4000}],
        line_items=[{"id": "L1", "name": "Cup A", "quantity": 5000, "production_process": "print"}],
    )
    rows = build_kepala_produksi_tasks(o)
    assert rows[0]["completed_qty"] == 4000
    assert rows[0]["remaining_qty"] == 1000
    assert rows[0]["completed_qty"] != 2000
