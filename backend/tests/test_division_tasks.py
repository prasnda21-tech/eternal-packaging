"""Division task list: own assignment, line identity, qty semantics."""
import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.division_tasks import (
    LIST_INLINE_PRESENT,
    TASK_PROJECTION,
    build_delivery_tasks,
    build_designer_tasks,
    build_division_tasks,
    build_helper_tasks,
    build_kepala_produksi_tasks,
    build_loading_tasks,
    build_operator_tasks,
    build_packing_tasks,
    build_screener_tasks,
    build_surat_jalan_tasks,
    can_view_all_division,
    division_tasks_read_pipeline,
    po_needs_loading_task,
    project_order_for_task_list,
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


def test_operator_split_exposes_exact_work_date_and_name():
    o = _print_po(operator_splits=[
        {
            "id": "sBudi",
            "line_idx": 0,
            "operator_email": "budi@x.com",
            "operator_name": "Budi",
            "quantity": 4000,
            "qty_completed": 1500,
            "work_date": "2026-09-08",
        },
        {
            "id": "sAndi",
            "line_idx": 0,
            "operator_email": "andi@x.com",
            "operator_name": "Andi",
            "quantity": 1000,
            "qty_completed": 0,
            "work_date": "2026-09-10",
        },
    ])
    rows = build_operator_tasks(o, {"email": "budi@x.com"}, mine_only=True)
    assert len(rows) == 1
    assert rows[0]["work_date"] == "2026-09-08"
    assert rows[0]["assigned_user_name"] == "Budi"
    assert rows[0]["operator_names"] == ["Budi"]
    assert rows[0]["order_number"] == "PO-001"
    assert rows[0]["line_idx"] == 0
    assert rows[0]["line_id"] == "L1"


def test_line_level_operators_stay_on_same_line_identity():
    o = _print_po(
        operator_splits=[
            {"id": "a", "line_idx": 0, "operator_name": "Budi", "quantity": 5000, "work_date": "2026-09-08"},
            {"id": "b", "line_idx": 1, "operator_name": "Sari", "quantity": 3000, "work_date": "2026-09-10"},
        ],
        helper_prep={"items": [
            {"line_idx": 0, "qty_prepared": 1000},
            {"line_idx": 1, "qty_prepared": 500},
        ]},
    )
    rows = build_helper_tasks(o)
    by = {r["line_idx"]: r for r in rows}
    assert by[0]["item"] == by[1]["item"] == "Cup A"
    assert by[0]["line_id"] == "L1"
    assert by[1]["line_id"] == "L2"
    assert by[0]["operator_names"] == ["Budi"]
    assert by[1]["operator_names"] == ["Sari"]
    assert by[0]["work_date"] == "2026-09-08"
    assert by[1]["work_date"] == "2026-09-10"
    assert "Sari" not in by[0]["operator_names"]
    assert "Budi" not in by[1]["operator_names"]


def test_po_level_uses_order_work_date_and_deadline_snapshot_fields():
    o = _print_po(
        work_date="2026-09-08",
        delivery_deadline_effective="2026-09-09T03:00:00+00:00",
        delivery_deadline_source="automatic",
        delivery_deadline_status="overdue",
    )
    rows = build_screener_tasks(o)
    assert len(rows) == 1
    assert rows[0]["line_idx"] is None
    assert rows[0]["work_date"] == "2026-09-08"
    assert rows[0]["delivery_deadline"] == "2026-09-09T03:00:00+00:00"
    assert rows[0]["delivery_deadline_source"] == "automatic"
    assert rows[0]["delivery_deadline_status"] == "overdue"
    assert set(rows[0]["operator_names"]) == {"Budi", "Andi"}


def test_task_row_copies_schedule_urgent_flag():
    o = _print_po(priority="urgent", delivery_deadline_status="h1")
    row = build_screener_tasks(o)[0]
    assert row["priority"] == "urgent"
    assert row["urgent"] is True
    assert row["delivery_deadline_status"] == "h1"
    helper = build_helper_tasks(o)[0]
    assert helper["urgent"] is True
    op = build_operator_tasks(o, {"email": "budi@x.com"}, mine_only=True)[0]
    assert op["urgent"] is True
    assert op["task_id"] != helper["task_id"]


def test_normal_priority_is_not_urgent():
    o = _print_po(priority="normal", delivery_deadline_status="normal")
    row = build_screener_tasks(o)[0]
    assert row["urgent"] is False
    assert row["priority"] == "normal"


def test_delivery_deadline_is_not_invented():
    o = _print_po()
    row = build_screener_tasks(o)[0]
    assert row["delivery_deadline"] is None
    assert row["work_date"] is None


def test_existing_task_fields_remain_after_enrichment():
    o = _print_po()
    row = build_operator_tasks(o, {"email": "budi@x.com"}, mine_only=True)[0]
    for key in (
        "task_id", "order_id", "order_number", "customer_name", "line_id", "line_idx",
        "item", "assigned_user_name", "assigned_user_email", "target_qty", "completed_qty",
        "status", "status_label", "required_input",
    ):
        assert key in row


def test_enrichment_does_not_change_task_eligibility_count():
    o = _print_po()
    before = len(build_operator_tasks(o, {"email": "budi@x.com"}, mine_only=True))
    o["operator_splits"][0]["work_date"] = "2026-09-08"
    o["delivery_deadline_effective"] = "2026-09-09T00:00:00+00:00"
    after = len(build_operator_tasks(o, {"email": "budi@x.com"}, mine_only=True))
    assert before == after == 1


def test_projection_includes_schedule_read_fields_not_blobs():
    assert "work_date" in TASK_PROJECTION
    assert "delivery_deadline_manual" in TASK_PROJECTION
    assert "buffer_stock" in TASK_PROJECTION
    assert "design_approved_at" in TASK_PROJECTION
    assert "priority" in TASK_PROJECTION
    assert "line_items" in TASK_PROJECTION
    joined = " ".join(str(k) for k in TASK_PROJECTION)
    assert "data_url" not in joined
    assert "signature" not in joined


def test_line_tasks_copy_exact_line_screen_size():
    o = _print_po()
    o["line_items"][0]["screen_size"] = "large"
    o["line_items"][1]["screen_size"] = "small"
    o["operator_splits"] = [
        {"id": "a", "line_idx": 0, "quantity": 5000, "qty_completed": 0, "operator_email": "budi@x.com", "operator_name": "Budi"},
        {"id": "b", "line_idx": 1, "quantity": 3000, "qty_completed": 0, "operator_email": "andi@x.com", "operator_name": "Andi"},
    ]
    o["helper_prep"] = {
        "qty_prepared": 1000,
        "items": [{"line_idx": 0, "qty_prepared": 500}, {"line_idx": 1, "qty_prepared": 500}],
    }
    helpers = {r["line_idx"]: r for r in build_helper_tasks(o)}
    assert helpers[0]["item"] == helpers[1]["item"] == "Cup A"
    assert helpers[0]["screen_size"] == "large"
    assert helpers[1]["screen_size"] == "small"
    ops = {r["line_idx"]: r for r in build_operator_tasks(o, {"email": "budi@x.com"}, mine_only=False)}
    assert ops[0]["screen_size"] == "large"
    assert ops[1]["screen_size"] == "small"


def test_po_level_task_does_not_guess_screen_size():
    o = _print_po()
    o["line_items"][0]["screen_size"] = "large"
    o["line_items"][1]["screen_size"] = "small"
    row = build_screener_tasks(o)[0]
    assert row["line_idx"] is None
    assert row["screen_size"] is None
    designer = build_designer_tasks(o)
    if designer:
        assert designer[0]["screen_size"] is None


def test_operator_task_keeps_each_allocation_screen():
    o = _print_po()
    o["line_items"][0]["screen_size"] = "large"
    o["operator_splits"] = [
        {
            "id": "deni", "line_idx": 0, "quantity": 4000, "qty_completed": 0,
            "operator_email": "deni@x.com", "operator_name": "Deni", "screen_size": "large",
        },
        {
            "id": "djoko", "line_idx": 0, "quantity": 1000, "qty_completed": 0,
            "operator_email": "djoko@x.com", "operator_name": "Djoko", "screen_size": "small",
        },
    ]
    ops = {
        r["assigned_user_email"]: r
        for r in build_operator_tasks(o, {"email": "deni@x.com"}, mine_only=False)
    }
    assert ops["deni@x.com"]["screen_size"] == "large"
    assert ops["djoko@x.com"]["screen_size"] == "small"


def test_operator_task_keeps_screen_when_line_mirror_empty():
    o = _print_po()
    o["line_items"][0].pop("screen_size", None)
    o["operator_splits"] = [
        {
            "id": "deni", "line_idx": 0, "quantity": 4000, "qty_completed": 0,
            "operator_email": "deni@x.com", "operator_name": "Deni", "screen_size": "large",
        },
        {
            "id": "djoko", "line_idx": 0, "quantity": 1000, "qty_completed": 0,
            "operator_email": "djoko@x.com", "operator_name": "Djoko", "screen_size": "small",
        },
    ]
    ops = {
        r["assigned_user_email"]: r
        for r in build_operator_tasks(o, {"email": "deni@x.com"}, mine_only=False)
    }
    assert ops["deni@x.com"]["screen_size"] == "large"
    assert ops["deni@x.com"]["target_qty"] == 4000
    assert ops["djoko@x.com"]["screen_size"] == "small"
    assert ops["djoko@x.com"]["target_qty"] == 1000
    helpers = build_helper_tasks(o)
    assert helpers[0]["line_idx"] == 0
    assert helpers[0]["screen_size"] is None


def test_missing_line_screen_size_is_null():
    o = _print_po()
    helper = build_helper_tasks(o)[0]
    assert helper["line_idx"] == 0
    assert helper["screen_size"] is None


def test_division_tasks_endpoint_is_read_only():
    src = Path(__file__).resolve().parents[1].joinpath("server.py").read_text(encoding="utf-8")
    start = src.index("async def work_division_tasks")
    end = src.index("\n# ---- Screener info")
    body = src[start:end]
    assert "_attach_deadline_snapshot" in body
    assert "_get_delivery_deadline_settings" in body
    assert "update_one" not in body
    assert "insert_one" not in body
    assert "replace_one" not in body
    assert "aggregate(" in body
    assert ".find(" not in body


def _import_server():
    from dotenv import load_dotenv

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
    os.environ.setdefault("DB_NAME", "test_database")
    import server

    return server


def _apply_task_projection(order: dict) -> dict:
    """In-memory mirror of TASK_PROJECTION top-level includes."""
    out = {}
    for key, include in TASK_PROJECTION.items():
        if key == "_id" or include != 1 or "." in str(key):
            continue
        if key in order:
            out[key] = order[key]
    return out


def _division_task_after_endpoint_snapshot(order: dict):
    """Same path as GET /work/division-tasks: lean projection → attach snapshot → rows."""
    server = _import_server()
    projected = _apply_task_projection(order)
    server._attach_deadline_snapshot(projected, 3)
    rows = build_screener_tasks(projected)
    return projected, rows


def test_buffer_stock_overdue_date_is_not_overdue_on_division_task():
    normal = _print_po(
        id="div-normal",
        current_stage=6,
        delivery_status="Belum Terkirim",
        buffer_stock=False,
        delivery_deadline_manual="2020-01-15T00:00:00+07:00",
    )
    buffer = _print_po(
        id="div-buffer",
        current_stage=6,
        delivery_status="Belum Terkirim",
        buffer_stock=True,
        delivery_deadline_manual="2020-01-15T00:00:00+07:00",
    )
    missing = _print_po(
        id="div-missing",
        current_stage=6,
        delivery_status="Belum Terkirim",
        delivery_deadline_manual="2020-01-15T00:00:00+07:00",
    )
    delivered = _print_po(
        id="div-done",
        current_stage=14,
        delivery_status="Terkirim",
        buffer_stock=True,
        delivery_deadline_manual="2020-01-15T00:00:00+07:00",
    )

    _, normal_rows = _division_task_after_endpoint_snapshot(normal)
    _, buffer_rows = _division_task_after_endpoint_snapshot(buffer)
    _, missing_rows = _division_task_after_endpoint_snapshot(missing)
    _, delivered_rows = _division_task_after_endpoint_snapshot(delivered)

    assert len(normal_rows) >= 1
    assert len(buffer_rows) >= 1
    assert normal_rows[0]["delivery_deadline_status"] == "overdue"
    assert buffer_rows[0]["delivery_deadline_status"] != "overdue"
    assert buffer_rows[0]["delivery_deadline_status"] == "normal"
    assert buffer_rows[0]["order_id"] == "div-buffer"
    assert missing_rows[0]["delivery_deadline_status"] == "overdue"
    assert delivered_rows[0]["delivery_deadline_status"] == "terkirim"


def _legacy_division_tasks_read_pipeline(query: dict) -> list:
    """Pipeline before the stage-1 reshape. Comparison fixture only."""
    return [
        {"$match": query},
        {"$limit": 1000},
        {"$project": TASK_PROJECTION},
        {"$set": {
            "surat_jalan_files": {
                "$map": {
                    "input": {"$ifNull": ["$surat_jalan_files", []]},
                    "as": "f",
                    "in": {
                        "$mergeObjects": [
                            "$$f",
                            {LIST_INLINE_PRESENT: {
                                "$and": [
                                    {"$eq": [{"$type": "$$f.data_url"}, "string"]},
                                    {"$eq": [{"$substrCP": ["$$f.data_url", 0, 5]}, "data:"]},
                                ]
                            }},
                        ]
                    },
                }
            }
        }},
        {"$unset": [
            "surat_jalan_files.data_url",
            "surat_jalan_files.url",
            "delivery_proofs.data_url",
            "delivery_proofs.url",
            "operator_splits.progress_entries.data_url",
            "operator_splits.progress_entries.photo",
            "operator_splits.progress_entries.photos",
        ]},
    ]


def _json_size(value) -> int:
    return len(json.dumps(value, default=str).encode("utf-8"))


def _sorted_docs(docs):
    return sorted(docs, key=lambda doc: doc.get("id") or "")


def _inline_hits(value, path="$"):
    hits = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}"
            if key in ("data_url", "photo", "photos"):
                hits.append(here)
            if key == "url" and isinstance(item, str) and item.startswith("data:"):
                hits.append(here)
            hits.extend(_inline_hits(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_inline_hits(item, f"{path}[{index}]"))
    elif isinstance(value, str) and value.startswith("data:"):
        hits.append(path)
    return hits


def _task_rows(docs, division, user, *, mine_only):
    return build_division_tasks(docs, division, user, mine_only=mine_only)


def test_pipeline_keeps_match_limit_and_skips_sort():
    query = {"cancelled": {"$ne": True}, "operator_splits.operator_email": "op@x.com"}
    pipeline = division_tasks_read_pipeline(query)
    assert pipeline[0] == {"$match": query}
    assert pipeline[1] == {"$limit": 1000}
    assert all("$sort" not in stage for stage in pipeline)


def test_task_list_pipeline_drops_inline_bytes_and_keeps_tasks():
    from pymongo import MongoClient

    marker = "INLINEBLOB9"
    inline = "data:application/octet-stream;base64," + (marker * 20000)
    https_sj = "https://files.example/sj/storage-ref"
    https_progress = "https://files.example/progress/op-note"
    object_url = {"href": "/api/files/sj-storage"}
    user = {"id": "u-op", "email": "op@x.com", "role": "operator"}
    other = {"id": "u-other", "email": "other@x.com", "role": "operator"}
    split_blob = {
        "qty": 100,
        "data_url": inline,
        "photo": inline,
        "photos": [inline],
        "url": inline,
    }

    def _split(sid, email, progress):
        row = {
            "id": sid,
            "line_idx": 0,
            "line_id": "L0",
            "operator_email": email,
            "operator_user_id": email,
            "operator_name": email.split("@")[0],
            "quantity": 1000,
            "qty_completed": 100,
            "work_date": "2026-10-04",
            "screen_size": "large",
        }
        if progress is not None:
            row["progress_entries"] = progress
        return row

    def _base(oid, po, email, progress):
        return {
            "id": oid,
            "po_number": po,
            "customer": "Cust Payload",
            "customer_name": "Cust Payload",
            "cancelled": False,
            "hold": False,
            "current_stage": 11,
            "delivery_status": "Belum Terkirim",
            "work_date": "2026-10-04",
            "line_items": [{
                "id": "L0",
                "name": "Cup Merah",
                "quantity": 1000,
                "production_process": "print",
            }],
            "operator_splits": [_split(f"s-{oid}", email, progress)],
            "screener_info": {"screen_qty": 1000, "entries": [{"qty": 1000, "cancelled": False}]},
            "helper_prep": {"qty_prepared": 1000, "items": [{"line_idx": 0, "qty_prepared": 1000}]},
            "packing_info": {"qty_packed": 200, "lines": [{"line_idx": 0, "packed_qty": 200}]},
            "design_files": [{
                "id": "design-1",
                "name": "cup.pdf",
                "uploaded_at": "2026-10-01T00:00:00Z",
                "data_url": inline,
            }],
            "sample_files": [{"id": "sample-1", "name": "sample.png", "data_url": inline}],
        }

    legacy = _base("legacy", "PO-LEGACY", "op@x.com", [dict(split_blob), {"qty": 1, "url": https_progress}])
    legacy["surat_jalan_files"] = [{
        "id": "sj-legacy",
        "data_url": inline,
        "url": "https://files.example/sj/legacy-ref",
        "shipment_id": "sh-legacy",
        "lines": [{"line_idx": 0, "qty": 10}],
    }]
    legacy["shipments"] = [{"id": "sh-legacy", "lines": [{"line_idx": 0, "qty": 10}]}]
    legacy["delivery_proofs"] = [{
        "id": "proof-legacy",
        "storage_path": "proofs/legacy.jpg",
        "data_url": inline,
        "url": "https://files.example/proof/legacy",
    }]

    stored = _base("stored", "PO-STORED", "op@x.com", [{"qty": 40, "url": https_progress}])
    stored["surat_jalan_files"] = [{
        "id": "sj-stored",
        "storage_path": "eternal-packaging/sj/stored.pdf",
        "name": "stored.pdf",
        "uploaded_at": "2026-10-02T00:00:00Z",
        "url": object_url,
        "status": "active",
        "shipment_id": "sh-stored",
        "lines": [{"line_idx": 0, "qty": 4}],
        "data_url": "not-inline-" + (marker * 1000),
    }]
    stored["delivery_proofs"] = [{
        "id": "proof-stored",
        "url": https_sj,
        "data_url": inline,
    }]

    blank = _base("blank", "PO-BLANK", "op@x.com", [])
    blank["surat_jalan_files"] = [{"id": "sj-blank", "url": ""}]
    blank["delivery_proofs"] = []

    missing = _base("missing", "PO-MISSING", "op@x.com", None)
    missing.pop("sample_files")

    other_order = _base("other", "PO-OTHER", "other@x.com", [{"qty": 5, "photo": inline, "url": inline}])
    other_order["surat_jalan_files"] = [{"id": "sj-other", "storage_path": "eternal-packaging/sj/other.pdf"}]

    cancelled = _base("cancelled", "PO-BATAL", "op@x.com", [{"photo": inline}])
    cancelled["cancelled"] = True
    cancelled["surat_jalan_files"] = [{"id": "sj-batal", "data_url": inline}]

    orders = [legacy, stored, blank, missing, other_order, cancelled]
    division_query = {"cancelled": {"$ne": True}}
    operator_query = {"cancelled": {"$ne": True}, "operator_splits.operator_email": "op@x.com"}
    client = MongoClient(os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017"))
    db_name = "pwa_task_list_payload_stage1"
    db = client[db_name]
    try:
        client.drop_database(db_name)
        db.orders.insert_many(copy.deepcopy(orders))
        before = _sorted_docs(list(db.orders.find({}, {"_id": 0})))

        old_division = _sorted_docs(list(db.orders.aggregate(_legacy_division_tasks_read_pipeline(division_query))))
        new_division = _sorted_docs(list(db.orders.aggregate(division_tasks_read_pipeline(division_query))))
        old_operator = _sorted_docs(list(db.orders.aggregate(_legacy_division_tasks_read_pipeline(operator_query))))
        new_operator = _sorted_docs(list(db.orders.aggregate(division_tasks_read_pipeline(operator_query))))
        included = _sorted_docs(list(db.orders.find(division_query, TASK_PROJECTION)))
        after = _sorted_docs(list(db.orders.find({}, {"_id": 0})))
    finally:
        client.drop_database(db_name)
        client.close()

    assert after == before
    assert all(LIST_INLINE_PRESENT not in json.dumps(doc) for doc in before)
    assert marker in json.dumps(before)

    assert [doc["id"] for doc in old_division] == ["blank", "legacy", "missing", "other", "stored"]
    assert [doc["id"] for doc in new_division] == [doc["id"] for doc in old_division]
    assert [doc["id"] for doc in old_operator] == ["blank", "legacy", "missing", "stored"]
    assert [doc["id"] for doc in new_operator] == [doc["id"] for doc in old_operator]
    assert "cancelled" not in {doc["id"] for doc in new_division}
    assert "other" not in {doc["id"] for doc in new_operator}

    divisions = ("screener", "helper", "packing", "loading", "terkirim")
    for division in divisions:
        assert _task_rows(old_division, division, user, mine_only=False) == _task_rows(
            new_division, division, user, mine_only=False
        )
    assert _task_rows(old_operator, "operator", user, mine_only=True) == _task_rows(
        new_operator, "operator", user, mine_only=True
    )
    assert _task_rows(old_division, "operator", other, mine_only=True) == _task_rows(
        new_division, "operator", other, mine_only=True
    )

    by_new = {doc["id"]: doc for doc in new_division}
    by_old = {doc["id"]: doc for doc in old_division}
    legacy_file = by_new["legacy"]["surat_jalan_files"][0]
    assert legacy_file[LIST_INLINE_PRESENT] is True
    assert legacy_file["id"] == "sj-legacy"
    assert legacy_file["shipment_id"] == "sh-legacy"
    assert legacy_file["lines"] == [{"line_idx": 0, "qty": 10}]
    assert legacy_file["url"] == "https://files.example/sj/legacy-ref"
    assert "url" not in by_old["legacy"]["surat_jalan_files"][0]
    assert by_old["legacy"]["surat_jalan_files"][0][LIST_INLINE_PRESENT] is True
    progress = by_new["legacy"]["operator_splits"][0]["progress_entries"]
    assert progress[0]["qty"] == 100
    assert "url" not in progress[0]
    assert progress[1]["url"] == https_progress
    assert by_old["legacy"]["operator_splits"][0]["progress_entries"][0]["url"].startswith("data:")
    assert by_new["legacy"]["delivery_proofs"][0]["url"] == "https://files.example/proof/legacy"
    assert by_new["legacy"]["delivery_proofs"][0]["storage_path"] == "proofs/legacy.jpg"
    assert "url" not in by_old["legacy"]["delivery_proofs"][0]

    stored_file = by_new["stored"]["surat_jalan_files"][0]
    assert stored_file[LIST_INLINE_PRESENT] is False
    assert stored_file["storage_path"] == "eternal-packaging/sj/stored.pdf"
    assert stored_file["name"] == "stored.pdf"
    assert stored_file["uploaded_at"] == "2026-10-02T00:00:00Z"
    assert stored_file["url"] == object_url
    assert "url" not in by_old["stored"]["surat_jalan_files"][0]
    assert by_new["stored"]["delivery_proofs"][0]["url"] == https_sj
    assert by_new["stored"]["delivery_proofs"][0]["id"] == "proof-stored"

    blank_file = by_new["blank"]["surat_jalan_files"][0]
    assert blank_file[LIST_INLINE_PRESENT] is False
    assert blank_file["url"] == ""
    assert by_new["blank"]["delivery_proofs"] == []
    assert by_new["missing"]["surat_jalan_files"] == []
    assert "delivery_proofs" not in by_new["missing"]
    assert "progress_entries" not in by_new["missing"]["operator_splits"][0]
    assert by_old["blank"]["surat_jalan_files"][0][LIST_INLINE_PRESENT] is False
    assert by_old["missing"]["surat_jalan_files"] == []

    assert _inline_hits(new_division) == []
    assert _inline_hits(new_operator) == []
    assert marker not in json.dumps(new_division)
    assert marker in json.dumps(old_division)
    legacy_tasks = build_loading_tasks(by_new["legacy"])
    assert legacy_tasks
    assert legacy_tasks == build_loading_tasks(by_old["legacy"])
    assert po_needs_loading_task(by_new["legacy"]) == po_needs_loading_task(by_old["legacy"])
    assert build_loading_tasks(by_new["stored"]) == build_loading_tasks(by_old["stored"])
    assert build_loading_tasks(by_new["blank"]) == build_loading_tasks(by_old["blank"])
    assert build_loading_tasks(by_new["missing"]) == []
    assert build_screener_tasks(by_new["legacy"]) == build_screener_tasks(by_old["legacy"])
    assert build_helper_tasks(by_new["stored"]) == build_helper_tasks(by_old["stored"])
    assert build_packing_tasks(by_new["blank"]) == build_packing_tasks(by_old["blank"])
    assert build_delivery_tasks(by_new["legacy"]) == build_delivery_tasks(by_old["legacy"])
    assert build_operator_tasks(by_new["legacy"], user, mine_only=True) == build_operator_tasks(
        by_old["legacy"], user, mine_only=True
    )

    mirrored = _sorted_docs(project_order_for_task_list(doc) for doc in before if not doc.get("cancelled"))
    assert mirrored == new_division

    old_tasks = {division: _task_rows(old_division, division, user, mine_only=False) for division in divisions}
    new_tasks = {division: _task_rows(new_division, division, user, mine_only=False) for division in divisions}
    old_tasks["operator"] = _task_rows(old_operator, "operator", user, mine_only=True)
    new_tasks["operator"] = _task_rows(new_operator, "operator", user, mine_only=True)
    legacy_aggregate_json_bytes = _json_size(old_division)
    new_aggregate_json_bytes = _json_size(new_division)
    legacy_task_response_json_bytes = _json_size(old_tasks)
    new_task_response_json_bytes = _json_size(new_tasks)
    assert new_aggregate_json_bytes < legacy_aggregate_json_bytes
    assert new_task_response_json_bytes == legacy_task_response_json_bytes
    assert _json_size(included) > legacy_aggregate_json_bytes
