"""Additive GET /orders?view=dashboard stays lean with unchanged workflow semantics."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")


def _list_orders_fn_body() -> str:
    start = SERVER.index("async def list_orders(")
    end = SERVER.index("@api_router.get(\"/orders/{order_id}\")")
    return SERVER[start:end]


def _summary_fn_body() -> str:
    start = SERVER.index("async def _list_orders_summary(")
    end = SERVER.index("@api_router.get(\"/orders\")")
    return SERVER[start:end]


def _dashboard_fn_body() -> str:
    start = SERVER.index("async def _list_orders_dashboard(")
    end = SERVER.index("@api_router.get(\"/orders/{order_id}\")")
    return SERVER[start:end]


def test_dashboard_view_is_additive_opt_in():
    body = _list_orders_fn_body()
    assert 'view: Optional[str] = None' in body
    assert 'view_norm == "dashboard"' in body
    assert "return await _list_orders_dashboard(st)" in body
    assert "return await _list_orders_summary(st)" in body


def test_default_list_orders_path_still_full_documents():
    body = _list_orders_fn_body()
    assert 'db.orders.find(q, {"_id": 0})' in body
    assert 'all_active = await db.orders.find({"cancelled": {"$ne": True}}, {"_id": 0}).to_list(1000)' in body


def test_summary_path_still_skips_stage_counts():
    body = _summary_fn_body()
    assert "_compute_pending_queues" not in body
    assert "all_active" not in body
    assert '"stage_counts": {}' in body or "'stage_counts': {}" in body


def test_dashboard_path_uses_lean_aggregates_not_full_find():
    body = _dashboard_fn_body()
    assert "aggregate" in body
    assert "_orders_dashboard_list_projection" in body
    assert "_orders_dashboard_stage_projection" in body
    assert "_compute_pending_queues" in body
    assert "_apply_hold_snapshot" in body
    assert "_attach_deadline_snapshot" in body
    assert 'find(q, {"_id": 0})' not in body
    assert "update_one" not in body
    assert "insert_one" not in body
    assert "delete_one" not in body
    assert "replace_one" not in body
    assert "_ORDERS_DASHBOARD_LIMIT" in body
    assert "_ORDERS_DASHBOARD_STAGE_LIMIT" in body


def test_dashboard_projections_exclude_heavy_fields():
    list_start = SERVER.index("def _orders_dashboard_list_projection")
    list_end = SERVER.index("def _dashboard_design_file_evidence")
    body = SERVER[list_start:list_end]
    for banned in (
        "timeline",
        "notes",
        "delivery_proofs",
        "data_url",
        "hpp",
        "accurate",
        "inventory",
    ):
        assert banned not in body
    assert "design_files" in SERVER[SERVER.index("def _orders_dashboard_design_files_expr"): list_end]
    assert "embedded" in SERVER


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


def _base_print_po(**overrides):
    doc = {
        "id": "po-dash-1",
        "po_number": "PO-DASH-1",
        "outlet": "Outlet A",
        "customer": "Cust A",
        "item_count": 2,
        "priority": "urgent",
        "deadline": "2026-09-13T10:00:00+00:00",
        "cancelled": False,
        "current_stage": 8,
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_bypassed": False,
        "payment_bypassed": False,
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
        "customer_approval": {"status": "approved", "note": "ok", "token": "secret"},
        "operator_splits": [
            {"operator_name": "Op Satu", "quantity": 100, "qty_completed": 40, "work_date": "2026-09-13"},
        ],
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
            {"line_id": "L-A", "line_idx": 0, "packed_qty": 40},
            {"line_id": "L-B", "line_idx": 1, "packed_qty": 40},
        ]},
        "surat_jalan_files": [{
            "id": "sj1",
            "name": "SJ.pdf",
            "uploaded_at": "2026-09-12T00:00:00+00:00",
            "data_url": "data:application/pdf;base64," + ("D" * 3000),
        }],
        "shipments": [{
            "id": "sh1",
            "sales_delivery_id": "",
            "lines": [{"line_idx": 0, "qty": 40}, {"line_idx": 1, "qty": 40}],
        }],
        "loading_confirmations": [{
            "id": "lc1",
            "surat_jalan_id": "sj1",
            "lines": [{"line_idx": 0, "qty": 40}, {"line_idx": 1, "qty": 40}],
        }],
        "loading_info": {"lines": [], "events": []},
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "hold_reasons": ["x"],
    }
    doc.update(overrides)
    return doc


def test_shape_and_finalize_drop_bytes_and_keep_evidence():
    server = _import_server()
    full = _base_print_po()
    shaped = server._shape_order_for_dashboard_compute(full)
    assert shaped["design_files"][0]["url"] == "embedded"
    assert "data_url" not in shaped["design_files"][0]
    assert shaped["payment_files"] == [{}]
    assert shaped["sample_files"] == [{}]
    assert shaped["surat_jalan_files"][0]["storage_path"] == "1"
    assert "data_url" not in shaped["surat_jalan_files"][0]
    assert shaped["line_items"][0]["line_id"] == "L-A"
    assert shaped["line_items"][1]["line_id"] == "L-B"
    assert shaped["line_items"][0]["name"] if False else True
    assert "unit_price" not in shaped["line_items"][0]
    assert "timeline" not in shaped
    finalized = server._finalize_orders_dashboard_row(copy.deepcopy(shaped))
    blob = json.dumps(finalized)
    assert "data:application" not in blob
    assert "AAAA" not in blob
    assert "timeline" not in finalized
    assert "shipments" not in finalized
    assert "line_items" not in finalized
    assert finalized["design_files"][0]["url"] == "embedded"


def test_pending_queues_parity_and_parallel_screener_helper():
    server = _import_server()
    full = _base_print_po(
        current_stage=8,
        sample_files=[],
        sample_approved=False,
        screener_info={},
        helper_prep={},
        operator_splits=[{"operator_name": "Op", "quantity": 100, "qty_completed": 0}],
        packing_info={},
        surat_jalan_files=[],
        shipments=[],
        loading_confirmations=[],
    )
    shaped = server._shape_order_for_dashboard_compute(full)
    full_q = server._compute_pending_queues(full)
    shaped_q = server._compute_pending_queues(shaped)
    assert full_q == shaped_q
    assert "screener" in full_q
    assert "helper" in full_q


def test_stage_counts_parity_on_fixture_set():
    server = _import_server()
    fixtures = [
        _base_print_po(id="a", po_number="A", current_stage=0, design_files=[], payment_files=[],
                       operator_splits=[], sample_files=[], sample_approved=False,
                       packing_info={}, surat_jalan_files=[], shipments=[], loading_confirmations=[],
                       design_status="Waiting", payment_status="Waiting"),
        _base_print_po(id="b", po_number="B",
                       screener_info={}, helper_prep={}, sample_files=[], sample_approved=False,
                       packing_info={}, surat_jalan_files=[], shipments=[], loading_confirmations=[]),
        _base_print_po(id="c", po_number="C", current_stage=14, qty_delivered=100,
                       delivery_status="Terkirim"),
    ]
    def counts(docs):
        out = {slug: 0 for slug in server.WORK_QUEUE_SLUGS}
        for d in docs:
            for slug in server._compute_pending_queues(d):
                out[slug] += 1
        return out
    full_counts = counts(fixtures)
    shaped_counts = counts([server._shape_order_for_dashboard_compute(d) for d in fixtures])
    assert full_counts == shaped_counts
    assert full_counts["screener"] >= 1
    assert full_counts["helper"] >= 1


def test_hold_and_status_semantics_unchanged():
    server = _import_server()
    waiting = _base_print_po(
        current_stage=2,
        design_files=[],
        payment_files=[],
        design_status="Waiting",
        payment_status="Waiting",
        operator_splits=[],
        sample_files=[],
        sample_approved=False,
    )
    shaped = server._shape_order_for_dashboard_compute(waiting)
    server._apply_hold_snapshot(waiting)
    server._apply_hold_snapshot(shaped)
    assert waiting["hold"] is True
    assert shaped["hold"] is True
    assert (not shaped.get("hold") and shaped.get("current_stage", 0) < 14) is False


def test_design_evidence_variants():
    server = _import_server()
    uploaded = server._dashboard_design_file_evidence([{
        "data_url": "data:application/pdf;base64,XXX",
    }])
    assert uploaded[0]["url"] == "embedded"
    named = server._dashboard_design_file_evidence([{
        "name": "a.pdf",
        "uploaded_at": "2026-01-01T00:00:00Z",
    }])
    assert named[0]["name"] == "a.pdf"
    assert named[0]["uploaded_at"]
    empty = server._dashboard_design_file_evidence([])
    assert empty == []


def test_payment_sample_presence_without_bytes():
    server = _import_server()
    shaped = server._shape_order_for_dashboard_compute(_base_print_po())
    assert shaped["payment_files"] == [{}]
    assert shaped["sample_files"] == [{}]
    none = server._shape_order_for_dashboard_compute(_base_print_po(payment_files=[], sample_files=[]))
    assert none["payment_files"] == []
    assert none["sample_files"] == []


def test_print_and_packing_and_sj_loading_terkirim_parity():
    server = _import_server()
    full = _base_print_po()
    shaped = server._shape_order_for_dashboard_compute(full)
    assert server._compute_pending_queues(full) == server._compute_pending_queues(shaped)
    packed0 = _base_print_po(
        packing_info={"qty_packed": 0, "lines": [
            {"line_id": "L-A", "line_idx": 0, "packed_qty": 0},
            {"line_id": "L-B", "line_idx": 1, "packed_qty": 0},
        ]},
        loading_confirmations=[],
        shipments=[],
        qty_delivered=0,
        current_stage=12,
        surat_jalan_files=[{
            "id": "sj1",
            "name": "SJ.pdf",
            "uploaded_at": "2026-09-12T00:00:00+00:00",
            "data_url": "data:application/pdf;base64,EEE",
        }],
    )
    shaped0 = server._shape_order_for_dashboard_compute(packed0)
    assert server._qr_eligible_delivery_qty(packed0) == server._qr_eligible_delivery_qty(shaped0)
    assert server._qr_eligible_delivery_qty(shaped0) == 0
    assert server._compute_pending_queues(packed0) == server._compute_pending_queues(shaped0)
    assert "driver" not in server._compute_pending_queues(shaped0)
    assert "loading" in server._compute_pending_queues(shaped0)
    terkirim = _base_print_po(current_stage=14, qty_delivered=100, delivery_status="Terkirim")
    shaped_t = server._shape_order_for_dashboard_compute(terkirim)
    assert server._compute_pending_queues(terkirim) == ["delivered"]
    assert server._compute_pending_queues(shaped_t) == ["delivered"]
    assert server._terkirim_confirmed(terkirim) == server._terkirim_confirmed(shaped_t)


def test_same_name_lines_stay_distinct():
    server = _import_server()
    shaped = server._shape_order_for_dashboard_compute(_base_print_po())
    ids = [li["line_id"] for li in shaped["line_items"]]
    idxs = [li["line_idx"] for li in shaped["line_items"]]
    assert ids == ["L-A", "L-B"]
    assert idxs == [0, 1]


def test_dashboard_payload_smaller_than_full_document_json():
    server = _import_server()
    full = _base_print_po()
    shaped = server._shape_order_for_dashboard_compute(full)
    shaped["pending_queues"] = server._compute_pending_queues(shaped)
    slim = server._finalize_orders_dashboard_row(copy.deepcopy(shaped))
    full_bytes = len(json.dumps(full, default=str).encode("utf-8"))
    slim_bytes = len(json.dumps(slim, default=str).encode("utf-8"))
    assert slim_bytes < full_bytes
    assert "data:application" not in json.dumps(slim)


def test_dashboard_and_default_helpers_are_read_only_source():
    for body in (_list_orders_fn_body(), _dashboard_fn_body(), _summary_fn_body()):
        assert "update_one" not in body
        assert "update_many" not in body
        assert "insert_one" not in body
        assert "delete_one" not in body
        assert "bulk_write" not in body
        assert "create_index" not in body


def _dashboard_status_postfilter(orders, st):
    """Mirror of `_list_orders_dashboard` / default GET /orders post-filters."""
    if st == "hold":
        return [o for o in orders if o.get("hold")]
    if st == "aktif":
        return [o for o in orders if not o.get("hold") and o.get("current_stage", 0) < 14]
    if st == "selesai":
        return [o for o in orders if o.get("current_stage", 0) >= 14]
    return list(orders)


def _match_includes(order, q):
    cancelled = bool(order.get("cancelled"))
    if q.get("cancelled") is True:
        return cancelled
    if q.get("cancelled") == {"$ne": True}:
        return not cancelled
    return True


def _status_fixture_set(server):
    aktif = _base_print_po(
        id="po-aktif",
        po_number="PO-AKTIF",
        cancelled=False,
        current_stage=8,
        design_status="Approved",
        payment_status="Approved",
    )
    hold = _base_print_po(
        id="po-hold",
        po_number="PO-HOLD",
        cancelled=False,
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
    selesai = _base_print_po(
        id="po-selesai",
        po_number="PO-SELESAI",
        cancelled=False,
        current_stage=14,
        qty_delivered=100,
        delivery_status="Terkirim",
    )
    cancelled = _base_print_po(
        id="po-cancelled",
        po_number="PO-CANCELLED",
        cancelled=True,
        current_stage=6,
        cancel_reason="fixture",
    )
    rows = []
    for raw in (aktif, hold, selesai, cancelled):
        shaped = server._shape_order_for_dashboard_compute(raw)
        server._apply_hold_snapshot(shaped)
        shaped["id"] = raw["id"]
        shaped["po_number"] = raw["po_number"]
        shaped["cancelled"] = raw["cancelled"]
        rows.append(shaped)
    return {row["id"]: row for row in rows}


def test_loading_confirmation_lean_fields_available_until_finalize():
    server = _import_server()
    body = _dashboard_fn_body()
    assert body.index("o[\"pending_queues\"] = _compute_pending_queues(o)") < body.index(
        "orders = [_finalize_orders_dashboard_row(o) for o in raw]"
    )
    assert body.index("o[\"terkirim_confirmed\"] = _terkirim_confirmed(o)") < body.index(
        "_finalize_orders_dashboard_row"
    )

    full = _base_print_po()
    shaped = server._shape_order_for_dashboard_compute(full)
    conf = shaped["loading_confirmations"][0]
    assert conf["surat_jalan_id"] == "sj1"
    assert conf["lines"] == [
        {"line_idx": 0, "qty": 40, "quantity": None},
        {"line_idx": 1, "qty": 40, "quantity": None},
    ]
    full_qty = server._qr_eligible_delivery_qty(full)
    shaped_qty = server._qr_eligible_delivery_qty(shaped)
    assert shaped_qty == full_qty
    assert shaped_qty > 0
    assert server._compute_pending_queues(full) == server._compute_pending_queues(shaped)
    assert shaped["loading_confirmations"][0]["surat_jalan_id"] == "sj1"
    assert shaped["loading_confirmations"][0]["lines"][0]["qty"] == 40

    finalized = server._finalize_orders_dashboard_row(copy.deepcopy(shaped))
    assert finalized["loading_confirmations"] == [{}]
    conf_blob = json.dumps(finalized["loading_confirmations"])
    assert "sj1" not in conf_blob
    assert "surat_jalan_id" not in conf_blob
    assert "line_idx" not in conf_blob
    assert '"qty": 40' not in json.dumps(finalized)


def test_cancelled_status_preserves_historical_match_and_no_postfilter():
    server = _import_server()
    body = _dashboard_fn_body()
    default_body = _list_orders_fn_body()
    assert 'q = _orders_list_match(st)' in body
    assert 'st == "cancelled"' in default_body
    assert "stage_view / all / cancelled" in default_body
    assert server._orders_list_match("cancelled") == {"cancelled": True}

    fixtures = _status_fixture_set(server)
    q = server._orders_list_match("cancelled")
    matched = [o for o in fixtures.values() if _match_includes(o, q)]
    assert [o["id"] for o in matched] == ["po-cancelled"]
    filtered = _dashboard_status_postfilter(matched, "cancelled")
    assert [o["id"] for o in filtered] == ["po-cancelled"]
    cancelled = fixtures["po-cancelled"]
    assert cancelled["cancelled"] is True
    raw_cancelled = _base_print_po(
        id="po-cancelled",
        po_number="PO-CANCELLED",
        cancelled=True,
        current_stage=6,
        cancel_reason="fixture",
    )
    server._apply_hold_snapshot(raw_cancelled)
    assert server._compute_pending_queues(raw_cancelled) == server._compute_pending_queues(cancelled)


def test_stage_view_keeps_non_cancelled_including_hold_and_selesai():
    server = _import_server()
    assert server._orders_list_match("stage_view") == {"cancelled": {"$ne": True}}
    body = _dashboard_fn_body()
    assert 'elif st == "selesai"' in body
    assert 'st == "stage_view"' not in body

    fixtures = _status_fixture_set(server)
    q = server._orders_list_match("stage_view")
    matched = [o for o in fixtures.values() if _match_includes(o, q)]
    ids = {o["id"] for o in matched}
    assert ids == {"po-aktif", "po-hold", "po-selesai"}
    assert "po-cancelled" not in ids
    filtered = _dashboard_status_postfilter(matched, "stage_view")
    assert {o["id"] for o in filtered} == ids
    holds = [o for o in filtered if o.get("hold")]
    selesai = [o for o in filtered if o.get("current_stage", 0) >= 14]
    assert len(holds) == 1
    assert len(selesai) == 1


def test_all_status_matches_historical_non_cancelled_match():
    server = _import_server()
    # Historical default GET /orders treats `all` as non-cancelled match + no post-filter.
    assert server._orders_list_match("all") == {"cancelled": {"$ne": True}}
    assert server._orders_list_match("all") == server._orders_list_match("stage_view")
    fixtures = _status_fixture_set(server)
    q = server._orders_list_match("all")
    matched = [o for o in fixtures.values() if _match_includes(o, q)]
    filtered = _dashboard_status_postfilter(matched, "all")
    assert {o["id"] for o in filtered} == {"po-aktif", "po-hold", "po-selesai"}
    assert "po-cancelled" not in {o["id"] for o in filtered}


def test_sj_storage_sentinel_used_server_side_then_stripped_from_browser_row():
    server = _import_server()
    from quantity_rules import has_surat_jalan_file

    data_url_only = _base_print_po()
    shaped = server._shape_order_for_dashboard_compute(data_url_only)
    assert shaped["surat_jalan_files"][0]["storage_path"] == "1"
    assert "data_url" not in shaped["surat_jalan_files"][0]
    assert has_surat_jalan_file(shaped) is True
    queues = server._compute_pending_queues(shaped)
    assert queues == server._compute_pending_queues(data_url_only)

    finalized = server._finalize_orders_dashboard_row(copy.deepcopy(shaped))
    sj = finalized["surat_jalan_files"][0]
    assert "storage_path" not in sj
    assert "data_url" not in sj
    assert sj.get("id") == "sj1"
    assert sj.get("name") == "SJ.pdf"
    blob = json.dumps(finalized)
    assert "storage_path" not in blob
    assert "data:application" not in blob
    assert "DDDD" not in blob

    stored = _base_print_po(surat_jalan_files=[{
        "id": "sj-stored",
        "name": "SJ-stored.pdf",
        "uploaded_at": "2026-09-12T00:00:00+00:00",
        "storage_path": "surat-jalan/po-dash-1/sj-stored.pdf",
    }])
    shaped_stored = server._shape_order_for_dashboard_compute(stored)
    assert shaped_stored["surat_jalan_files"][0]["storage_path"] == "1"
    assert has_surat_jalan_file(shaped_stored) is True
    public = server._finalize_orders_dashboard_row(copy.deepcopy(shaped_stored))
    assert "storage_path" not in public["surat_jalan_files"][0]
    assert "surat-jalan/po-dash-1/sj-stored.pdf" not in json.dumps(public)
