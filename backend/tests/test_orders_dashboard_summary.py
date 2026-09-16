"""GET /api/dashboard/summary lean projection preserves HoP semantics."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")

_KPI_LIST_PAIRS = (
    ("total_active", "active"),
    ("total_urgent", "urgent"),
    ("due_today", "due_today"),
    ("overdue", "overdue"),
    ("deadline_h1", "deadline_h1"),
    ("deadline_h6", "deadline_h6"),
    ("waiting_design_approval", "waiting_design_approval"),
    ("waiting_payment_approval", "waiting_payment_approval"),
    ("hold", "hold"),
    ("completed", "completed"),
)

_RESPONSE_KEYS = (
    "kpi",
    "active",
    "urgent",
    "due_today",
    "overdue",
    "deadline_h1",
    "deadline_h6",
    "waiting_design_approval",
    "waiting_payment_approval",
    "hold",
    "completed",
    "stage_breakdown",
    "operator_load",
    "work_today",
    "shipments_today",
    "generated_at",
)


def _summary_fn_body() -> str:
    start = SERVER.index("async def dashboard_summary(")
    end = SERVER.index('@api_router.get("/dashboard/today")')
    return SERVER[start:end]


def _projection_fn_body() -> str:
    start = SERVER.index("def _orders_dashboard_summary_projection")
    end = SERVER.index("def _dashboard_summary_file_presence")
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


def _blob(ch: str, n: int = 4000) -> str:
    return f"data:application/octet-stream;base64,{ch * n}"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _base_print_po(**overrides):
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    today = "2026-09-13"
    doc = {
        "id": "po-hop-1",
        "po_number": "PO-HOP-1",
        "outlet": "Outlet A",
        "customer": "Cust A",
        "priority": "normal",
        "deadline": _iso(now.replace(hour=15)),
        "status_label": "Operator",
        "work_date": today,
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
            "data_url": _blob("A"),
        }],
        "payment_files": [{
            "name": "pay.pdf",
            "data_url": _blob("B"),
        }],
        "sample_files": [{"name": "sample.jpg", "data_url": _blob("C", 2000)}],
        "customer_approval": {"status": "", "note": "n", "token": "secret"},
        "operator_splits": [{
            "operator_email": "op@x.com",
            "operator_name": "Op Satu",
            "quantity": 100,
            "qty_completed": 40,
            "stage": 8,
            "work_date": today,
            "progress_entries": [{"photo": _blob("P", 1000)}],
        }],
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
        "quantity": 100,
        "timeline": [{"note": "should not ship", "by": "admin"}],
        "notes": "internal",
        "crm_history": [{"note": "x"}],
        "comments": [{"body": "c"}],
        "hpp": {"blob": "H" * 200},
        "accurate_payload": {"raw": "X"},
        "inventory": {"sku": "x"},
        "delivery_proofs": [{
            "id": "p1",
            "photo": _blob("Q"),
            "signature": _blob("S", 800),
            "data_url": _blob("D"),
            "uploaded_at": _iso(now.replace(hour=8)),
        }],
        "packing_info": {"qty_packed": 80, "lines": [
            {"line_id": "L-A", "line_idx": 0, "packed_qty": 40, "photos": ["blob"]},
            {"line_id": "L-B", "line_idx": 1, "packed_qty": 40},
        ]},
        "qty_packed": 80,
        "surat_jalan_files": [{
            "id": "sj1",
            "name": "SJ.pdf",
            "data_url": _blob("E", 3000),
        }],
        "shipments": [{
            "id": "sh1",
            "number": "SJ-1",
            "created_at": _iso(now.replace(hour=7)),
            "pdf_base64": _blob("F", 5000),
            "lines": [
                {"line_idx": 0, "qty": 40, "quantity": 40, "name": "Cup"},
                {"line_idx": 1, "qty": 40, "quantity": 40, "name": "Cup"},
            ],
        }],
        "loading_confirmations": [{
            "cancelled": False,
            "deleted": False,
            "surat_jalan_id": "sj1",
            "photo": _blob("L", 500),
            "lines": [
                {"line_idx": 0, "qty": 40, "quantity": 40},
                {"line_idx": 1, "qty": 40, "quantity": 40},
            ],
        }],
        "loading_info": {
            "lines": [{"line_idx": 0, "loaded_qty": 0}, {"line_idx": 1, "loaded_qty": 0}],
            "events": [{
                "line_idx": 0,
                "qty": 0,
                "at": _iso(now.replace(hour=9)),
                "created_at": _iso(now.replace(hour=9)),
                "photo": _blob("M", 500),
            }],
        },
        "loading_checklist": {"items_loaded": False, "seal_ok": False, "notes": "n"},
        "delivery_status": "",
        "qty_delivered": 0,
        "delivered_at": None,
        "delivery_note_number": "SJ-1",
        "delivery_note_created_at": _iso(now.replace(hour=7)),
        "loading_completed_at": None,
        "delivery_deadline_manual": "2026-09-16",
        "scheduled_delivery_date": None,
        "delivery_reschedules": [{"status": "scheduled", "new_date": "2026-09-16", "note": "x"}],
    }
    doc.update(overrides)
    return doc


def _hop_payload(server, raw_orders, *, lead_days=3, now=None):
    """Test-only harness: existing production helpers, same loops as dashboard_summary."""
    orders = [copy.deepcopy(o) for o in raw_orders if not o.get("cancelled")]
    for o in orders:
        server._apply_hold_snapshot(o)
        server._attach_deadline_snapshot(o, lead_days)

    now = now or datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    from quantity_rules import is_fully_delivered
    from services.dashboard_today import (
        due_today_orders,
        partition_orders_by_delivery,
        wib_today,
    )
    today = wib_today(now)
    active_orders, completed_orders = partition_orders_by_delivery(
        orders, is_fully_delivered
    )
    urgent_orders = [o for o in active_orders if o.get("priority") == "urgent"]

    due_today = due_today_orders(active_orders, today)
    overdue = []
    deadline_h1, deadline_h6 = [], []
    waiting_design, waiting_payment = [], []
    hold_list = []
    for o in active_orders:
        st = o.get("delivery_deadline_status")
        if st == "overdue":
            overdue.append(o)
        elif st == "h6":
            deadline_h6.append(o)
        elif st == "h1":
            deadline_h1.append(o)
        if o.get("design_status") != "Approved" and not o.get("design_bypassed"):
            waiting_design.append(o)
        if o.get("payment_status") != "Approved" and not o.get("payment_bypassed"):
            waiting_payment.append(o)
        if o.get("hold"):
            hold_list.append(o)

    stage_breakdown = []
    for idx, label in enumerate(server.STAGES):
        cnt = sum(1 for o in active_orders if o.get("current_stage", 0) == idx)
        stage_breakdown.append({"stage": idx, "label": label, "count": cnt})

    op_map = {}
    for o in active_orders:
        for s in o.get("operator_splits", []):
            email = s.get("operator_email")
            if not email:
                continue
            rec = op_map.setdefault(email, {
                "email": email,
                "name": s.get("operator_name", email),
                "qty_scheduled": 0,
                "qty_completed": 0,
                "active_po_count": 0,
                "pos": [],
            })
            rec["qty_scheduled"] += int(s.get("quantity", 0))
            rec["qty_completed"] += int(s.get("qty_completed", 0))
            rec["active_po_count"] += 1
            rec["pos"].append({
                "po_id": o.get("id"),
                "po_number": o.get("po_number"),
                "outlet": o.get("outlet"),
                "quantity": int(s.get("quantity", 0)),
                "qty_completed": int(s.get("qty_completed", 0)),
                "stage": s.get("stage"),
                "stage_label": server.STAGES[min(s.get("stage", 8), len(server.STAGES) - 1)],
                "deadline": o.get("deadline"),
                "priority": o.get("priority"),
            })
    operator_load = []
    for rec in op_map.values():
        rec["qty_remaining"] = max(0, rec["qty_scheduled"] - rec["qty_completed"])
        operator_load.append(rec)
    operator_load.sort(key=lambda r: -r["qty_remaining"])

    def slim(o):
        return {
            "id": o.get("id"),
            "po_number": o.get("po_number"),
            "outlet": o.get("outlet"),
            "customer": o.get("customer"),
            "deadline": o.get("deadline"),
            "priority": o.get("priority"),
            "current_stage": o.get("current_stage"),
            "status_label": o.get("status_label"),
            "design_status": o.get("design_status"),
            "payment_status": o.get("payment_status"),
            "design_bypassed": bool(o.get("design_bypassed")),
            "payment_bypassed": bool(o.get("payment_bypassed")),
            "hold": bool(o.get("hold")),
            "work_date": o.get("work_date"),
            "total_quantity": o.get("total_quantity"),
            "delivery_deadline_effective": o.get("delivery_deadline_effective"),
            "delivery_deadline_status": o.get("delivery_deadline_status"),
            "delivery_deadline_source": o.get("delivery_deadline_source"),
        }

    from services.dashboard_today import build_shipments_today, build_work_today

    return {
        "kpi": {
            "total_active": len(active_orders),
            "total_urgent": len(urgent_orders),
            "due_today": len(due_today),
            "overdue": len(overdue),
            "deadline_h1": len(deadline_h1),
            "deadline_h6": len(deadline_h6),
            "waiting_design_approval": len(waiting_design),
            "waiting_payment_approval": len(waiting_payment),
            "hold": len(hold_list),
            "completed": len(completed_orders),
        },
        "active": [slim(o) for o in active_orders],
        "urgent": [slim(o) for o in urgent_orders],
        "due_today": [slim(o) for o in due_today],
        "overdue": [slim(o) for o in overdue],
        "deadline_h1": [slim(o) for o in deadline_h1],
        "deadline_h6": [slim(o) for o in deadline_h6],
        "waiting_design_approval": [slim(o) for o in waiting_design],
        "waiting_payment_approval": [slim(o) for o in waiting_payment],
        "hold": [slim(o) for o in hold_list],
        "completed": [slim(o) for o in completed_orders],
        "stage_breakdown": stage_breakdown,
        "operator_load": operator_load,
        "work_today": build_work_today(orders, today),
        # Endpoint loads schedule candidates through a separate SCHEDULE_PROJECTION query.
        "shipments_today": build_shipments_today([], today),
    }


def _drop_generated(payload: dict) -> dict:
    out = copy.deepcopy(payload)
    out.pop("generated_at", None)
    return out


def _assert_hop_parity(server, *full_orders, now=None, lead_days=3):
    full = [copy.deepcopy(o) for o in full_orders]
    shaped = [server._shape_order_for_dashboard_summary_compute(o) for o in full]
    full_out = _drop_generated(_hop_payload(server, full, lead_days=lead_days, now=now))
    shaped_out = _drop_generated(_hop_payload(server, shaped, lead_days=lead_days, now=now))
    assert shaped_out == full_out
    return shaped, full_out, shaped_out


class _Agg:
    def __init__(self, docs):
        self.docs = docs

    def to_list(self, n):
        async def _inner():
            return copy.deepcopy(self.docs)
        return _inner()


# ---------------------------------------------------------------------------
# Query / isolation
# ---------------------------------------------------------------------------

def test_handler_uses_dedicated_aggregate_not_full_find():
    body = _summary_fn_body()
    assert "aggregate" in body
    assert "_orders_dashboard_summary_projection" in body
    assert 'find({}, {"_id": 0}).to_list(1000)' not in body
    assert 'to_list(None)' in body
    assert '"$project": _orders_dashboard_summary_projection()' in body
    assert '{"$match": {"cancelled": {"$ne": True}}}' in body
    assert '"$limit"' not in body
    assert '"$sort"' not in body
    assert "cancelled" in body
    assert 'if not o.get("cancelled")' in body
    assert "_apply_hold_snapshot" in body
    assert "_attach_deadline_snapshot" in body
    assert "build_work_today" in body
    assert "build_shipments_today" in body
    assert "update_one" not in body
    assert "insert_one" not in body
    assert "delete_one" not in body
    assert "replace_one" not in body
    assert "bulk_write" not in body
    assert "create_index" not in body


def test_cancelled_is_filtered_before_projection_without_truncation():
    body = _summary_fn_body()
    assert '{"cancelled": {"$ne": True}}' in body
    assert '"$limit"' not in body
    assert 'orders = [o for o in raw_orders if not o.get("cancelled")]' in body


def test_projection_is_not_phase2b_or_action_center():
    proj = _projection_fn_body()
    assert "_orders_dashboard_stage_projection" not in proj
    assert "_orders_dashboard_list_projection" not in proj
    assert "_orders_action_center_projection" not in proj
    assert "Dedicated; not Phase 2B or Action Center" in proj
    assert "_DASHBOARD_SUMMARY_LIMIT" not in SERVER


def test_projection_source_excludes_heavy_top_level_fields():
    body = _projection_fn_body()
    for banned in (
        "timeline",
        "notes",
        "hpp",
        "accurate",
        "inventory",
        "crm_history",
        "progress_entries",
        "artwork",
        "data_url",
        "sample_files",
        "surat_jalan_files",
        "pdf_base64",
        "signature",
    ):
        assert banned not in body
    assert "delivery_proofs" in body
    proofs = SERVER[SERVER.index("def _orders_dashboard_summary_delivery_proofs_expr"): SERVER.index("def _orders_dashboard_summary_loading_info_expr")]
    assert "uploaded_at" in proofs
    assert "data_url" not in proofs


def test_orders_and_action_center_paths_unchanged():
    list_start = SERVER.index("async def list_orders(")
    list_end = SERVER.index('@api_router.get("/orders/{order_id}")')
    list_body = SERVER[list_start:list_end]
    assert 'db.orders.find(q, {"_id": 0})' in list_body
    assert "view_norm == \"dashboard\"" in list_body
    assert "view_norm == \"summary\"" in list_body
    ac = SERVER[SERVER.index("async def dashboard_action_items("): SERVER.index('@api_router.get("/work/division-tasks")')]
    assert "_orders_action_center_projection" in ac
    assert "_ACTION_CENTER_LIMIT" in ac
    rem = SERVER[SERVER.index("async def reminders("): SERVER.index("async def reminders(") + 2500]
    assert "_orders_dashboard_summary_projection" not in rem
    assert "_orders_reminders_projection" in rem
    assert "db.orders.aggregate(" in rem
    assert 'db.orders.find({}, {"_id": 0, "design_files": 0, "sample_files": 0})' not in rem


def test_rbac_dependency_unchanged():
    body = _summary_fn_body()
    assert 'user=Depends(require_perm("dashboard.production.view"))' in SERVER[
        SERVER.index('@api_router.get("/dashboard/summary")'): SERVER.index("async def dashboard_summary(") + 200
    ]
    assert "require_perm" in body or "dashboard.production.view" in SERVER


def test_require_perm_denies_without_dashboard_production_view():
    server = _import_server()
    checker = server.require_perm("dashboard.production.view")

    async def _run():
        with patch.object(server, "user_permissions", AsyncMock(return_value=set())):
            with pytest.raises(HTTPException) as ei:
                await checker({"email": "screener@x.com", "role": "screener"})
            assert ei.value.status_code == 403
        with patch.object(server, "user_permissions", AsyncMock(return_value={"dashboard.production.view"})):
            user = await checker({"email": "hop@x.com", "role": "head_production"})
            assert user["email"] == "hop@x.com"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Heavy blobs / file sentinels
# ---------------------------------------------------------------------------

def test_heavy_blobs_excluded_from_shaped_document():
    server = _import_server()
    full = _base_print_po()
    shaped = server._shape_order_for_dashboard_summary_compute(full)
    blob = json.dumps(shaped)
    assert "data:application" not in blob
    assert "AAAA" not in blob
    assert "timeline" not in shaped
    assert "notes" not in shaped
    assert "hpp" not in shaped
    assert "accurate_payload" not in shaped
    assert "inventory" not in shaped
    assert "crm_history" not in shaped
    assert "sample_files" not in shaped
    assert "surat_jalan_files" not in shaped
    assert "progress_entries" not in json.dumps(shaped.get("operator_splits"))
    proofs = json.dumps(shaped.get("delivery_proofs"))
    assert "photo" not in proofs
    assert "signature" not in proofs
    assert "data_url" not in proofs
    assert shaped["design_files"] == [{}]
    assert shaped["payment_files"] == [{}]
    assert "pdf_base64" not in json.dumps(shaped.get("shipments"))


def test_design_and_payment_file_truthiness_cases():
    server = _import_server()
    cases = [
        ("missing", None),
        ("empty", []),
        ("data_url_only", [{"name": "x.pdf", "data_url": _blob("Z")}]),
        ("storage", [{"name": "x.pdf", "storage_path": "design/po/x.pdf"}]),
        ("null_field", "NULL"),
    ]
    for label, files in cases:
        full = _base_print_po(current_stage=2, design_status="Waiting", payment_status="Waiting")
        if files == "NULL":
            full["design_files"] = None
            full["payment_files"] = None
        elif files is None:
            full.pop("design_files", None)
            full.pop("payment_files", None)
        else:
            full["design_files"] = copy.deepcopy(files)
            full["payment_files"] = copy.deepcopy(files)
        shaped = server._shape_order_for_dashboard_summary_compute(full)
        assert bool(shaped.get("design_files")) == bool(full.get("design_files")), label
        assert bool(shaped.get("payment_files")) == bool(full.get("payment_files")), label
        server._apply_hold_snapshot(full)
        server._apply_hold_snapshot(shaped)
        assert full.get("hold") == shaped.get("hold"), label


def test_customer_approval_hold_false_waiting_design_true():
    server = _import_server()
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    full = _base_print_po(
        current_stage=2,
        design_status="Waiting",
        design_bypassed=False,
        payment_status="Approved",
        customer_approval={"status": "approved", "token": "secret"},
        design_files=[{"storage_path": "d.pdf"}],
        payment_files=[{"storage_path": "p.pdf"}],
        operator_splits=[],
        shipments=[],
        packing_info={"qty_packed": 0, "lines": [{"line_idx": 0, "packed_qty": 0}, {"line_idx": 1, "packed_qty": 0}]},
        loading_confirmations=[],
        loading_info={"lines": [], "events": []},
        delivery_proofs=[],
        delivery_note_created_at=None,
        delivery_note_number=None,
    )
    shaped, full_out, _ = _assert_hop_parity(server, full, now=now)
    assert shaped[0]["customer_approval"] == {"status": "approved"}
    assert full_out["kpi"]["hold"] == 0
    assert full_out["kpi"]["waiting_design_approval"] == 1
    assert full_out["waiting_design_approval"][0]["id"] == "po-hop-1"


def test_cancelled_excluded_identically():
    server = _import_server()
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    live = _base_print_po(id="live")
    cancelled = _base_print_po(id="canc", po_number="PO-C", cancelled=True, priority="urgent")
    _, full_out, shaped_out = _assert_hop_parity(server, live, cancelled, now=now)
    ids = {o["id"] for o in full_out["active"]}
    assert ids == {"live"}
    assert all(o["id"] != "canc" for lst in (
        full_out["active"], full_out["urgent"], full_out["hold"], full_out["completed"],
        full_out["waiting_design_approval"],
    ) for o in lst)
    assert shaped_out["kpi"] == full_out["kpi"]


def test_active_vs_completed_and_urgent():
    server = _import_server()
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    active = _base_print_po(id="a", current_stage=8, priority="normal")
    urgent = _base_print_po(id="u", po_number="PO-U", current_stage=3, priority="urgent")
    done = _base_print_po(
        id="d", po_number="PO-D", current_stage=14, priority="urgent",
        qty_delivered=100, delivery_status="Terkirim",
    )
    _, out, _ = _assert_hop_parity(server, active, urgent, done, now=now)
    assert out["kpi"]["total_active"] == 2
    assert out["kpi"]["completed"] == 1
    assert out["kpi"]["total_urgent"] == 1
    assert {o["id"] for o in out["urgent"]} == {"u"}
    assert {o["id"] for o in out["completed"]} == {"d"}
    assert {o["id"] for o in out["active"]} == {"a", "u"}


def test_due_today_uses_tanggal_pengiriman_not_legacy_deadline():
    server = _import_server()
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    due = _base_print_po(
        id="due",
        deadline=_iso(now.replace(hour=18)),
        delivery_deadline_manual="2026-09-20",
        scheduled_delivery_date="2026-09-20",
    )
    not_due = _base_print_po(
        id="later",
        po_number="PO-L",
        deadline=_iso(now + timedelta(days=5)),
        delivery_deadline_manual="2026-09-13",
        scheduled_delivery_date="2026-09-13",
    )
    _, out, _ = _assert_hop_parity(server, due, not_due, now=now)
    assert {o["id"] for o in out["due_today"]} == {"later"}
    assert out["kpi"]["due_today"] == 1
    due_row = out["due_today"][0]
    assert due_row["deadline"] == not_due["deadline"]
    assert due_row["delivery_deadline_effective"] == "2026-09-13"


def test_deadline_h1_h6_overdue_use_snapshot_status():
    server = _import_server()
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    overdue = _base_print_po(
        id="ov", po_number="PO-OV", current_stage=5,
        delivery_deadline_manual="2026-09-01",
        design_status="Approved", payment_status="Approved",
        operator_splits=[], shipments=[], delivery_proofs=[],
        delivery_note_created_at=None, packing_info={"qty_packed": 0, "lines": []},
        loading_confirmations=[], loading_info={"lines": [], "events": []},
    )
    h6 = _base_print_po(
        id="h6", po_number="PO-H6", current_stage=5,
        delivery_deadline_manual=_iso(now + timedelta(hours=3))[:10],
        scheduled_delivery_date=None,
        operator_splits=[], shipments=[], delivery_proofs=[],
        delivery_note_created_at=None, packing_info={"qty_packed": 0, "lines": []},
        loading_confirmations=[], loading_info={"lines": [], "events": []},
    )
    # Force effective ISO within 6h via snapshot: use a datetime-shaped manual date
    # by attaching after shaping is not needed; use known calendar + _deadline_status_for.
    full_overdue = copy.deepcopy(overdue)
    server._attach_deadline_snapshot(full_overdue, 3)
    assert full_overdue["delivery_deadline_status"] == "overdue"

    _, out, _ = _assert_hop_parity(server, overdue, now=now)
    assert out["kpi"]["overdue"] == 1
    assert out["overdue"][0]["id"] == "ov"
    assert out["overdue"][0]["delivery_deadline_status"] == "overdue"

    near = _base_print_po(
        id="near", po_number="PO-NEAR", current_stage=5,
        delivery_deadline_manual=None,
        scheduled_delivery_date=None,
        design_approved_at=_iso(now - timedelta(hours=2)),
        payment_approved_at=_iso(now - timedelta(hours=2)),
        operator_splits=[], shipments=[], delivery_proofs=[],
        delivery_note_created_at=None,
    )
    # Automatic tanggal = approval + 3 days → not h1. Set manual ISO via scheduled.
    # Use delivery_deadline_manual as YYYY-MM-DD today so status is overdue or h1 depending on WIB midnight.
    today_key = now.astimezone(timezone(timedelta(hours=7))).date().isoformat()
    h_doc = _base_print_po(
        id="hx", po_number="PO-HX", current_stage=5,
        delivery_deadline_manual=today_key,
        operator_splits=[], shipments=[], delivery_proofs=[],
        delivery_note_created_at=None,
    )
    probe = copy.deepcopy(h_doc)
    server._attach_deadline_snapshot(probe, 3)
    st = probe["delivery_deadline_status"]
    assert st in ("overdue", "h1", "h6")
    _, out2, _ = _assert_hop_parity(server, h_doc, now=now)
    assert out2["kpi"][ {"overdue": "overdue", "h1": "deadline_h1", "h6": "deadline_h6"}[st] ] == 1


def test_work_today_from_order_and_operator_splits():
    server = _import_server()
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    top = _base_print_po(id="top", work_date="2026-09-13", operator_splits=[])
    split_only = _base_print_po(
        id="spl", po_number="PO-SPL", work_date="2026-09-01",
        operator_splits=[{
            "operator_email": "op2@x.com",
            "operator_name": "Op Dua",
            "quantity": 10,
            "qty_completed": 0,
            "stage": 8,
            "work_date": "2026-09-13",
        }],
        shipments=[], delivery_proofs=[], delivery_note_created_at=None,
        packing_info={"qty_packed": 0, "lines": []}, loading_confirmations=[],
        loading_info={"lines": [], "events": []},
    )
    other_day = _base_print_po(
        id="old", po_number="PO-OLD", work_date="2026-09-10",
        operator_splits=[{"operator_email": "op@x.com", "operator_name": "Op", "quantity": 1, "qty_completed": 0, "stage": 8, "work_date": "2026-09-10"}],
        shipments=[], delivery_proofs=[], delivery_note_created_at=None,
    )
    _, out, _ = _assert_hop_parity(server, top, split_only, other_day, now=now)
    ids = {r["id"] for r in out["work_today"]["preview"]}
    assert ids == {"top", "spl"}
    assert out["work_today"]["total"] == 2


def test_operator_load_and_stage_breakdown_parity():
    server = _import_server()
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    a = _base_print_po(id="a", current_stage=8)
    b = _base_print_po(
        id="b", po_number="PO-B", current_stage=11,
        operator_splits=[{
            "operator_email": "op@x.com",
            "operator_name": "Op Satu",
            "quantity": 20,
            "qty_completed": 5,
            "stage": 10,
            "work_date": "2026-09-13",
        }],
    )
    _, out, _ = _assert_hop_parity(server, a, b, now=now)
    assert len(out["operator_load"]) == 1
    rec = out["operator_load"][0]
    assert rec["email"] == "op@x.com"
    assert rec["qty_scheduled"] == 120
    assert rec["qty_completed"] == 45
    assert rec["active_po_count"] == 2
    stages = {row["stage"]: row["count"] for row in out["stage_breakdown"]}
    assert stages[8] == 1
    assert stages[11] == 1


def test_shipments_today_does_not_count_proof_activity_as_schedule():
    server = _import_server()
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    proof_only = _base_print_po(
        id="pr", po_number="PO-PR",
        shipments=[],
        delivery_note_created_at=None,
        delivery_note_number=None,
        packing_info={"qty_packed": 0, "lines": [{"line_idx": 0, "packed_qty": 0}, {"line_idx": 1, "packed_qty": 0}]},
        loading_confirmations=[],
        loading_info={"lines": [], "events": []},
        delivery_proofs=[{"uploaded_at": _iso(now.replace(hour=10)), "data_url": _blob("Z")}],
    )
    _, out, _ = _assert_hop_parity(server, proof_only, now=now)
    assert out["shipments_today"]["total"] == 0


def test_packed_zero_does_not_invent_loading():
    server = _import_server()
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    from quantity_rules import qty_loaded, qty_packed
    from services.dashboard_today import delivery_status_label

    full = _base_print_po(
        current_stage=12,
        packing_info={"qty_packed": 0, "lines": [
            {"line_idx": 0, "packed_qty": 0},
            {"line_idx": 1, "packed_qty": 0},
        ]},
        qty_packed=0,
        loading_confirmations=[],
        loading_info={"lines": [{"line_idx": 0, "loaded_qty": 0}], "events": []},
        loading_checklist={"items_loaded": False, "seal_ok": False},
        shipments=[{"created_at": _iso(now.replace(hour=7)), "number": "SJ-0",
                    "lines": [{"line_idx": 0, "qty": 0}, {"line_idx": 1, "qty": 0}]}],
        delivery_note_created_at=None,
        delivery_proofs=[],
    )
    shaped = server._shape_order_for_dashboard_summary_compute(full)
    assert qty_packed(full) == 0
    assert qty_packed(shaped) == 0
    assert qty_loaded(full) == 0
    assert qty_loaded(shaped) == 0
    assert "Loading" not in delivery_status_label(full)
    assert delivery_status_label(full) == delivery_status_label(shaped)
    _assert_hop_parity(server, full, now=now)


def test_packing_qty_fallback_parity():
    server = _import_server()
    from quantity_rules import qty_packed
    legacy = _base_print_po(packing_info=None)
    legacy.pop("packing_info", None)
    legacy["qty_packed"] = 17
    shaped = server._shape_order_for_dashboard_summary_compute(legacy)
    assert "packing_info" not in shaped
    assert qty_packed(legacy) == qty_packed(shaped) == 17
    nested = _base_print_po(packing_info={"qty_packed": 21}, qty_packed=0)
    nested["packing_info"].pop("lines", None)
    shaped2 = server._shape_order_for_dashboard_summary_compute(nested)
    assert qty_packed(nested) == qty_packed(shaped2) == 21


def test_loading_quantity_parity():
    server = _import_server()
    from quantity_rules import qty_loaded
    full = _base_print_po(
        loading_confirmations=[{
            "cancelled": False,
            "lines": [{"line_idx": 0, "qty": 7}, {"line_idx": 1, "qty": 3}],
        }],
        loading_info={"lines": [{"line_idx": 0, "loaded_qty": 1}, {"line_idx": 1, "loaded_qty": 2}], "events": []},
    )
    shaped = server._shape_order_for_dashboard_summary_compute(full)
    assert qty_loaded(full) == qty_loaded(shaped)
    assert qty_loaded(full) == 13


def test_same_name_two_lines_keep_positions_and_line_idx():
    server = _import_server()
    full = _base_print_po()
    shaped = server._shape_order_for_dashboard_summary_compute(full)
    assert [li["name"] for li in shaped["line_items"]] == ["Cup", "Cup"]
    assert [li["quantity"] for li in shaped["line_items"]] == [50, 50]
    assert [ln["line_idx"] for ln in shaped["shipments"][0]["lines"]] == [0, 1]
    assert [ln["line_idx"] for ln in shaped["packing_info"]["lines"]] == [0, 1]
    names = [li["name"] for li in shaped["line_items"]]
    assert names.count("Cup") == 2
    assert len(shaped["line_items"]) == 2


def test_legacy_missing_fields_do_not_crash():
    server = _import_server()
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    legacy = {"id": "legacy-1", "po_number": "PO-LEG", "outlet": "O", "customer": "C", "cancelled": False, "current_stage": 0}
    _assert_hop_parity(server, legacy, now=now)


def test_kpi_list_invariant_and_response_contract():
    server = _import_server()
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    _, out, _ = _assert_hop_parity(server, _base_print_po(), now=now)
    for k in _RESPONSE_KEYS:
        if k == "generated_at":
            continue
        assert k in out
    for kpi_k, list_k in _KPI_LIST_PAIRS:
        assert out["kpi"][kpi_k] == len(out[list_k])


def test_endpoint_zero_write_and_contract():
    server = _import_server()
    now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    shaped = [server._shape_order_for_dashboard_summary_compute(_base_print_po())]

    class _FakeAgg:
        def __init__(self, docs):
            self.docs = docs

        async def to_list(self, n):
            assert n is None
            return copy.deepcopy(self.docs)

    fake_orders = MagicMock()
    fake_orders.aggregate.return_value = _FakeAgg(shaped)
    fake_orders.update_one = MagicMock()
    fake_orders.insert_one = MagicMock()
    fake_orders.delete_one = MagicMock()
    fake_orders.replace_one = MagicMock()
    fake_orders.bulk_write = MagicMock()
    fake_orders.find.return_value = _FakeAgg([])

    async def _run():
        with patch.object(server.db, "orders", fake_orders):
            with patch.object(server, "_get_delivery_deadline_settings", AsyncMock(return_value={"lead_time_days": 3})):
                return await server.dashboard_summary(user={"role": "admin"})

    result = asyncio.run(_run())
    fake_orders.find.assert_called_once()
    fake_orders.update_one.assert_not_called()
    fake_orders.insert_one.assert_not_called()
    fake_orders.delete_one.assert_not_called()
    fake_orders.replace_one.assert_not_called()
    fake_orders.bulk_write.assert_not_called()
    fake_orders.aggregate.assert_called_once()
    pipeline = fake_orders.aggregate.call_args[0][0]
    assert pipeline[0] == {"$match": {"cancelled": {"$ne": True}}}
    assert "$project" in pipeline[1]
    for k in _RESPONSE_KEYS:
        assert k in result
    assert "generated_at" in result
    for kpi_k, list_k in _KPI_LIST_PAIRS:
        assert result["kpi"][kpi_k] == len(result[list_k])


def test_helpers_unchanged_in_endpoint():
    body = _summary_fn_body()
    assert "def slim(o):" in body
    assert "partition_orders_by_delivery" in body
    assert "due_today_orders" in body
    assert "wib_today(now)" in body
    assert "stage_breakdown" in body
    assert "operator_load" in body
    assert "waiting_design.append" in body
    assert 'o.get("design_status") != "Approved"' in body
    assert 'o.get("priority") == "urgent"' in body
    assert "_qr_is_fully_delivered" in body
    assert 'o.get("deadline")' not in body
    assert "delivery_deadline_status" in body


def test_synthetic_size_comparison_labeled():
    """SYNTHETIC ONLY — fixture JSON bytes, not production savings."""
    server = _import_server()
    full = _base_print_po()
    shaped = server._shape_order_for_dashboard_summary_compute(full)
    full_b = len(json.dumps(full, default=str).encode("utf-8"))
    proj_b = len(json.dumps(shaped, default=str).encode("utf-8"))
    ratio = proj_b / full_b if full_b else 0
    print(f"SYNTHETIC ONLY full={full_b} projected={proj_b} ratio={ratio:.4f}")
    assert proj_b < full_b
    assert "data:application" not in json.dumps(shaped)
    # stash for the report via attribute
    test_synthetic_size_comparison_labeled.stats = {
        "full_bytes": full_b,
        "projected_bytes": proj_b,
        "ratio": ratio,
    }
