"""GET /api/dashboard/action-items lean projection preserves Action Center semantics."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")


def _action_items_fn_body() -> str:
    start = SERVER.index("async def dashboard_action_items(")
    end = SERVER.index("@api_router.get(\"/work/division-tasks\")")
    return SERVER[start:end]


def _projection_fn_body() -> str:
    start = SERVER.index("def _orders_action_center_projection")
    end = SERVER.index("def _action_center_file_presence")
    return SERVER[start:end]


def _import_server():
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    if "MONGO_URL" not in os.environ:
        os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
        os.environ.setdefault("DB_NAME", "test_database")
    import server
    return server


def _prepare(server, order: dict, lead_days: int = 3) -> dict:
    o = copy.deepcopy(order)
    server._apply_hold_snapshot(o)
    server._attach_deadline_snapshot(o, lead_days)
    o["requires_printing"] = server._requires_printing(o)
    o["terkirim_confirmed"] = server._terkirim_confirmed(o)
    return o


def _predicate_map(server):
    return [
        (key, pred)
        for key, _label, _perm, pred in server._ACTION_DEFS
        if key != "cetak_qty"
    ]


def _outcomes(server, order: dict, *, email: str = "op@x.com", lead_days: int = 3) -> dict:
    prepared = _prepare(server, order, lead_days)
    out = {
        key: bool(pred(prepared))
        for key, pred in _predicate_map(server)
    }
    splits = server._op_task_predicate(prepared, email)
    out["operator_work"] = bool(splits)
    out["operator_split_emails"] = [
        (s.get("operator_email"), s.get("quantity"), s.get("qty_completed"))
        for s in splits
    ]
    out["hold"] = bool(prepared.get("hold"))
    out["deadline_status"] = prepared.get("delivery_deadline_status")
    out["deadline_effective"] = prepared.get("delivery_deadline_effective")
    out["deadline_priority"] = server._deadline_priority(prepared)
    out["slim"] = server._slim_action(prepared)
    return out


def _assert_parity(server, full: dict, **kwargs):
    shaped = server._shape_order_for_action_center_compute(full)
    full_out = _outcomes(server, full, **kwargs)
    shaped_out = _outcomes(server, shaped, **kwargs)
    assert shaped_out == full_out
    return shaped, full_out, shaped_out


def _base_print_po(**overrides):
    doc = {
        "id": "po-ac-1",
        "po_number": "PO-AC-1",
        "outlet": "Outlet A",
        "customer": "Cust A",
        "work_date": "2026-09-13",
        "cancelled": False,
        "current_stage": 8,
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_bypassed": False,
        "payment_bypassed": False,
        "design_approved_at": "2026-09-01T00:00:00+00:00",
        "payment_approved_at": "2026-09-01T00:00:00+00:00",
        "design_files": [{
            "name": "art.pdf",
            "uploaded_at": "2026-09-01T00:00:00+00:00",
            "data_url": "data:application/pdf;base64," + ("A" * 4000),
        }],
        "payment_files": [{
            "name": "pay.pdf",
            "data_url": "data:application/pdf;base64," + ("B" * 4000),
        }],
        "sample_files": [{"name": "sample.jpg", "data_url": "data:image/jpeg;base64," + ("C" * 2000)}],
        "sample_approved": True,
        "customer_approval": {"status": "", "note": "n", "token": "secret"},
        "operator_splits": [{
            "operator_email": "op@x.com",
            "operator_name": "Op Satu",
            "quantity": 100,
            "qty_completed": 40,
            "stage": 8,
            "work_date": "2026-09-13",
            "progress_entries": [{"photo": "data:image/jpeg;base64," + ("P" * 1000)}],
        }],
        "screener_info": {"screen_qty": 10, "notes": "heavy"},
        "helper_prep": {"qty_prepared": 10, "notes": "heavy"},
        "line_items": [
            {
                "line_id": "L-A",
                "line_idx": 0,
                "name": "Cup",
                "production_process": "print",
                "quantity": 50,
                "unit_price": 999,
                "artwork_url": "http://x/art.png",
            },
            {
                "line_id": "L-B",
                "line_idx": 1,
                "name": "Cup",
                "production_process": "print",
                "quantity": 50,
                "notes": "same name must stay distinct",
            },
        ],
        "total_quantity": 100,
        "timeline": [{"note": "should not ship", "by": "admin"}],
        "notes": "internal",
        "delivery_proofs": [{"id": "p1", "data_url": "data:image/jpeg;base64,XXXX"}],
        "packing_info": {"qty_packed": 80, "lines": [
            {"line_id": "L-A", "line_idx": 0, "packed_qty": 40, "photos": ["blob"]},
            {"line_id": "L-B", "line_idx": 1, "packed_qty": 40},
        ]},
        "surat_jalan_files": [{
            "id": "sj1",
            "name": "SJ.pdf",
            "uploaded_at": "2026-09-12T00:00:00+00:00",
            "data_url": "data:application/pdf;base64," + ("D" * 3000),
            "lines": [{"line_idx": 0, "qty": 40}, {"line_idx": 1, "qty": 40}],
        }],
        "shipments": [{
            "id": "sh1",
            "number": "SJ-1",
            "file_id": "sj1",
            "sales_delivery_id": "",
            "lines": [{"line_idx": 0, "qty": 40}, {"line_idx": 1, "qty": 40}],
        }],
        "loading_confirmations": [{
            "id": "lc1",
            "surat_jalan_id": "sj1",
            "cancelled": False,
            "deleted": False,
            "lines": [{"line_idx": 0, "qty": 40}, {"line_idx": 1, "qty": 40}],
        }],
        "loading_info": {"lines": [], "events": []},
        "loading_checklist": {"items_loaded": False, "seal_ok": False},
        "qty_delivered": 0,
        "qty_packed": 80,
        "delivery_status": "Belum Terkirim",
        "delivery_note": None,
        "delivery_deadline_manual": None,
        "scheduled_delivery_date": None,
        "delivery_reschedules": [],
        "hpp": {"blob": "H" * 200},
        "accurate_payload": {"raw": "X"},
        "crm_history": [{"note": "x"}],
    }
    doc.update(overrides)
    return doc


def test_handler_uses_dedicated_aggregate_not_full_find():
    body = _action_items_fn_body()
    assert "aggregate" in body
    assert "_orders_action_center_projection" in body
    assert "_ACTION_CENTER_LIMIT" in body
    assert 'find(query, {"_id": 0})' not in body
    assert ".to_list(_ACTION_CENTER_LIMIT)" in body
    assert '{"$limit": _ACTION_CENTER_LIMIT}' in body
    assert '"$match": query' in body
    assert '"$sort"' not in body
    assert '"$group"' not in body
    assert '"$lookup"' not in body
    assert "_compute_pending_queues" not in body
    assert "_apply_hold_snapshot" in body
    assert "_slim_action" in body
    assert "_ACTION_DEFS" in body
    assert "_op_task_predicate" in body
    assert "update_one" not in body
    assert "insert_one" not in body
    assert "delete_one" not in body
    assert "replace_one" not in body
    assert "bulk_write" not in body


def test_limit_remains_2000_and_projection_is_not_phase2b():
    assert "_ACTION_CENTER_LIMIT = 2000" in SERVER
    proj = _projection_fn_body()
    assert "_orders_dashboard_stage_projection" not in proj
    assert "_orders_dashboard_list_projection" not in proj
    assert '"customer": 1' in proj
    assert "operator_email" in SERVER[SERVER.index("def _orders_action_center_operator_splits_expr"): SERVER.index("def _orders_action_center_sj_files_expr")]
    assert '"name": "$$li.name"' in SERVER
    assert "Dedicated to GET /dashboard/action-items" in proj


def test_projection_source_excludes_heavy_top_level_fields():
    start = SERVER.index("def _orders_action_center_projection")
    end = SERVER.index("def _action_center_file_presence")
    body = SERVER[start:end]
    for banned in (
        "timeline",
        "notes",
        "delivery_proofs",
        "hpp",
        "accurate",
        "inventory",
        "crm_history",
        "progress_entries",
        "artwork",
    ):
        assert banned not in body


def test_response_slim_keys_unchanged():
    server = _import_server()
    keys = set(server._slim_action(_base_print_po()).keys())
    assert keys == {
        "id", "po_number", "outlet", "customer", "item", "work_date",
        "current_stage", "total_quantity", "delivery_deadline_effective",
        "delivery_deadline_status", "delivery_deadline_source",
        "requires_printing", "design_status",
    }


def test_heavy_blobs_excluded_from_shaped_document():
    server = _import_server()
    full = _base_print_po()
    shaped = server._shape_order_for_action_center_compute(full)
    blob = json.dumps(shaped)
    assert "timeline" not in shaped
    assert "notes" not in shaped
    assert "delivery_proofs" not in shaped
    assert "hpp" not in shaped
    assert "accurate_payload" not in shaped
    assert "crm_history" not in shaped
    assert "progress_entries" not in json.dumps(shaped.get("operator_splits"))
    assert "data:application" not in blob
    assert "data:image" not in blob
    assert "AAAA" not in blob
    assert "BBBB" not in blob
    assert "CCCC" not in blob
    assert "DDDD" not in blob
    assert shaped["design_files"] == [{}]
    assert shaped["payment_files"] == [{}]
    assert shaped["sample_files"] == [{}]
    assert "data_url" not in shaped["surat_jalan_files"][0]
    assert shaped["line_items"][0]["name"] == "Cup"
    assert "unit_price" not in shaped["line_items"][0]
    assert shaped["operator_splits"][0]["operator_email"] == "op@x.com"


def test_design_upload_parity():
    server = _import_server()
    full = _base_print_po(
        design_files=[],
        design_status="Waiting",
        current_stage=1,
        operator_splits=[],
        sample_files=[],
        sample_approved=False,
        packing_info={},
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
    )
    _assert_parity(server, full)
    assert _outcomes(server, full)["design_upload"] is True


def test_design_approve_and_bypass_parity():
    server = _import_server()
    waiting = _base_print_po(
        design_status="Waiting",
        design_bypassed=False,
        current_stage=2,
        operator_splits=[],
        sample_files=[],
        sample_approved=False,
        packing_info={},
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
    )
    _assert_parity(server, waiting)
    assert _outcomes(server, waiting)["design_approve"] is True

    bypassed = copy.deepcopy(waiting)
    bypassed["design_bypassed"] = True
    _assert_parity(server, bypassed)
    assert _outcomes(server, bypassed)["design_approve"] is False


def test_customer_approval_does_not_clear_design_approve():
    server = _import_server()
    full = _base_print_po(
        design_status="Waiting",
        design_bypassed=False,
        customer_approval={"status": "approved", "token": "secret"},
        current_stage=2,
        operator_splits=[],
        sample_files=[],
        sample_approved=False,
        packing_info={},
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
    )
    shaped, full_out, _ = _assert_parity(server, full)
    assert full_out["design_approve"] is True
    assert server._design_ok(full) is True
    assert server._design_ok(shaped) is True
    assert full_out["penjadwalan"] is True


def test_payment_approve_and_bypass_parity():
    server = _import_server()
    waiting = _base_print_po(
        payment_status="Waiting",
        payment_bypassed=False,
        current_stage=4,
        operator_splits=[],
        sample_files=[],
        sample_approved=False,
        packing_info={},
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
    )
    _assert_parity(server, waiting)
    assert _outcomes(server, waiting)["payment_approve"] is True
    bypassed = copy.deepcopy(waiting)
    bypassed["payment_bypassed"] = True
    bypassed["payment_files"] = []
    _assert_parity(server, bypassed)
    assert _outcomes(server, bypassed)["payment_approve"] is False


def test_penjadwalan_screener_helper_sample_packing_parity():
    server = _import_server()
    need_sched = _base_print_po(
        current_stage=5,
        operator_splits=[],
        sample_files=[],
        sample_approved=False,
        screener_info={},
        helper_prep={},
        packing_info={},
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
    )
    _assert_parity(server, need_sched)
    assert _outcomes(server, need_sched)["penjadwalan"] is True

    parallel = _base_print_po(
        current_stage=6,
        sample_files=[],
        sample_approved=False,
        screener_info={},
        helper_prep={},
        packing_info={},
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
        operator_splits=[{
            "operator_email": "op@x.com",
            "operator_name": "Op",
            "quantity": 100,
            "qty_completed": 0,
            "stage": 8,
            "work_date": "2026-09-13",
        }],
    )
    out = _outcomes(server, parallel)
    assert out["screener"] is True
    assert out["helper"] is True
    _assert_parity(server, parallel)

    sample_up = copy.deepcopy(parallel)
    sample_up["screener_info"] = {"screen_qty": 10}
    sample_up["helper_prep"] = {"qty_prepared": 100}
    _assert_parity(server, sample_up)
    assert _outcomes(server, sample_up)["sample_upload"] is True
    assert _outcomes(server, sample_up)["helper"] is False

    sample_ap = copy.deepcopy(sample_up)
    sample_ap["sample_files"] = [{"data_url": "data:image/jpeg;base64," + ("C" * 500)}]
    sample_ap["sample_approved"] = False
    _assert_parity(server, sample_ap)
    assert _outcomes(server, sample_ap)["sample_approve"] is True
    assert _outcomes(server, sample_ap)["sample_upload"] is False

    packing = _base_print_po(current_stage=11, surat_jalan_files=[], shipments=[], loading_confirmations=[])
    _assert_parity(server, packing)
    assert _outcomes(server, packing)["packing"] is True


def test_surat_jalan_loading_terkirim_and_packed_zero():
    server = _import_server()
    from services.division_tasks import po_needs_loading_task

    need_sj = _base_print_po(
        current_stage=11,
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
        packing_info={"qty_packed": 80, "lines": [
            {"line_idx": 0, "packed_qty": 40},
            {"line_idx": 1, "packed_qty": 40},
        ]},
    )
    _assert_parity(server, need_sj)
    assert _outcomes(server, need_sj)["surat_jalan"] is True

    packed0 = _base_print_po(
        current_stage=12,
        packing_info={"qty_packed": 0, "lines": [
            {"line_idx": 0, "packed_qty": 0},
            {"line_idx": 1, "packed_qty": 0},
        ]},
        qty_packed=0,
        loading_confirmations=[],
        loading_info={"lines": [], "events": []},
        shipments=[{
            "id": "sh1",
            "file_id": "sj1",
            "sales_delivery_id": "sd-early",
            "lines": [{"line_idx": 0, "qty": 40}, {"line_idx": 1, "qty": 40}],
        }],
    )
    shaped, full_out, _ = _assert_parity(server, packed0)
    assert full_out["loading"] is False
    assert po_needs_loading_task(packed0) is False
    assert po_needs_loading_task(shaped) is False

    ready_load = _base_print_po(
        current_stage=12,
        loading_confirmations=[],
        loading_info={"lines": [], "events": []},
        shipments=[{
            "id": "sh1", "file_id": "sj1", "sales_delivery_id": "sd-early",
            "lines": [{"line_idx": 0, "qty": 40}, {"line_idx": 1, "qty": 40}],
        }],
    )
    _assert_parity(server, ready_load)
    assert _outcomes(server, ready_load)["loading"] is True

    terkirim_ready = _base_print_po(current_stage=13)
    _assert_parity(server, terkirim_ready)
    assert _outcomes(server, terkirim_ready)["terkirim"] is True

    delivered = _base_print_po(
        current_stage=14,
        delivery_status="Terkirim",
        qty_delivered=100,
    )
    _assert_parity(server, delivered)
    assert _outcomes(server, delivered)["terkirim"] is False


def test_data_url_only_sj_keeps_has_file_semantics():
    server = _import_server()
    from quantity_rules import has_surat_jalan_file

    data_url_only = _base_print_po(
        current_stage=12,
        surat_jalan_files=[{
            "id": "sj-embed",
            "data_url": "data:application/pdf;base64," + ("D" * 3000),
        }],
        packing_info={"qty_packed": 80, "lines": [
            {"line_idx": 0, "packed_qty": 40},
            {"line_idx": 1, "packed_qty": 40},
        ]},
        loading_confirmations=[],
        loading_info={"lines": [], "events": []},
        shipments=[],
    )
    shaped, full_out, shaped_out = _assert_parity(server, data_url_only)
    assert has_surat_jalan_file(data_url_only) is True
    assert has_surat_jalan_file(shaped) is True
    assert shaped["surat_jalan_files"][0]["storage_path"] == "1"
    assert "data_url" not in shaped["surat_jalan_files"][0]
    assert "DDDD" not in json.dumps(shaped)
    assert full_out["loading"] is True
    assert shaped_out["loading"] is True
    assert full_out["surat_jalan"] is False


def test_data_url_only_design_evidence():
    server = _import_server()
    full = _base_print_po(
        design_status="Waiting",
        design_files=[{"data_url": "data:application/pdf;base64," + ("A" * 2000)}],
        current_stage=2,
        operator_splits=[],
        sample_files=[],
        sample_approved=False,
        packing_info={},
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
    )
    shaped, full_out, _ = _assert_parity(server, full)
    assert full_out["design_approve"] is True
    assert full_out["design_upload"] is False
    assert shaped["design_files"] == [{}]
    assert "data_url" not in json.dumps(shaped["design_files"])


def test_hold_remains_eligible_cancelled_not_in_match():
    server = _import_server()
    hold = _base_print_po(
        current_stage=2,
        design_files=[],
        payment_files=[],
        design_status="Waiting",
        payment_status="Waiting",
        operator_splits=[],
        sample_files=[],
        sample_approved=False,
        packing_info={},
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
    )
    _assert_parity(server, hold)
    out = _outcomes(server, hold)
    assert out["hold"] is True
    assert out["design_upload"] is True

    body = _action_items_fn_body()
    assert '{"cancelled": {"$ne": True}}' in body
    cancelled = copy.deepcopy(hold)
    cancelled["cancelled"] = True
    shaped = server._shape_order_for_action_center_compute(cancelled)
    assert shaped["cancelled"] is True


def test_same_name_lines_keep_independent_idx_qty():
    server = _import_server()
    from services.division_tasks import po_needs_loading_task

    full = _base_print_po(
        current_stage=12,
        packing_info={"qty_packed": 50, "lines": [
            {"line_idx": 0, "packed_qty": 10},
            {"line_idx": 1, "packed_qty": 40},
        ]},
        loading_confirmations=[{
            "surat_jalan_id": "sj1",
            "cancelled": False,
            "lines": [{"line_idx": 0, "qty": 10}, {"line_idx": 1, "qty": 0}],
        }],
        loading_info={"lines": [], "events": []},
        shipments=[{"id": "sh1", "file_id": "sj1", "sales_delivery_id": "", "lines": []}],
    )
    shaped, _, _ = _assert_parity(server, full)
    assert [li["name"] for li in shaped["line_items"]] == ["Cup", "Cup"]
    assert shaped["packing_info"]["lines"][0]["packed_qty"] == 10
    assert shaped["packing_info"]["lines"][1]["packed_qty"] == 40
    assert po_needs_loading_task(full) == po_needs_loading_task(shaped)


def test_cancelled_deleted_loading_confirmations_ignored():
    server = _import_server()
    full = _base_print_po(
        current_stage=12,
        loading_confirmations=[
            {
                "surat_jalan_id": "sj1",
                "cancelled": True,
                "lines": [{"line_idx": 0, "qty": 40}, {"line_idx": 1, "qty": 40}],
            },
            {
                "surat_jalan_id": "sj1",
                "deleted": True,
                "lines": [{"line_idx": 0, "qty": 40}, {"line_idx": 1, "qty": 40}],
            },
        ],
        loading_info={"lines": [], "events": []},
        shipments=[{"id": "sh1", "file_id": "sj1", "sales_delivery_id": "sd1",
                    "lines": [{"line_idx": 0, "qty": 40}, {"line_idx": 1, "qty": 40}]}],
    )
    _assert_parity(server, full)
    assert _outcomes(server, full)["loading"] is True
    assert _outcomes(server, full)["terkirim"] is False


def test_legacy_missing_fields_and_deadline_parity():
    server = _import_server()
    legacy = {
        "id": "legacy-1",
        "po_number": "PO-LEG",
        "outlet": "O",
        "customer": "C",
        "cancelled": False,
        "current_stage": 0,
    }
    _assert_parity(server, legacy)
    dated = _base_print_po(
        delivery_deadline_manual="2026-09-14",
        current_stage=5,
        operator_splits=[],
        sample_files=[],
        sample_approved=False,
        packing_info={},
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
    )
    shaped, full_out, shaped_out = _assert_parity(server, dated)
    assert full_out["deadline_status"] == shaped_out["deadline_status"]
    assert full_out["deadline_effective"] == shaped_out["deadline_effective"]
    assert full_out["deadline_priority"] == shaped_out["deadline_priority"]
    assert shaped.get("delivery_deadline_manual") == "2026-09-14"


def test_operator_work_email_scoping():
    server = _import_server()
    full = _base_print_po(
        current_stage=10,
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
        packing_info={},
        operator_splits=[
            {
                "operator_email": "op@x.com",
                "operator_name": "Mine",
                "quantity": 50,
                "qty_completed": 10,
                "stage": 8,
                "work_date": "2026-09-13",
            },
            {
                "operator_email": "other@x.com",
                "operator_name": "Other",
                "quantity": 50,
                "qty_completed": 0,
                "stage": 8,
                "work_date": "2026-09-13",
            },
        ],
    )
    mine = _outcomes(server, full, email="op@x.com")
    other = _outcomes(server, full, email="other@x.com")
    none = _outcomes(server, full, email="nobody@x.com")
    _assert_parity(server, full, email="op@x.com")
    _assert_parity(server, full, email="other@x.com")
    assert mine["operator_work"] is True
    assert other["operator_work"] is True
    assert none["operator_work"] is False
    assert mine["operator_split_emails"] == [("op@x.com", 50, 10)]
    assert other["operator_split_emails"] == [("other@x.com", 50, 0)]
    body = _action_items_fn_body()
    assert 'query["operator_splits.operator_email"] = user_email' in body


def test_action_defs_and_predicates_not_rewritten():
    body = SERVER[SERVER.index("_ACTION_DEFS = ["): SERVER.index("def _slim_action")]
    assert '("design_upload", "Upload Design", "design.upload", _po_needs_design_upload)' in body
    assert '("design_approve", "Approval Design", "design.approve", _po_needs_design_approve)' in body
    assert '("helper", "Persiapan Helper", "helper.prepare", _po_needs_helper)' in body
    assert '("loading", "Konfirmasi Loading", "loading.update", _po_needs_loading)' in body
    slim = SERVER[SERVER.index("def _slim_action"): SERVER.index("def _deadline_priority")]
    assert '"design_status": o.get("design_status"),' in slim
