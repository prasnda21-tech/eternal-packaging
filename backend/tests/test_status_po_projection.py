"""GET /dashboard/status-po lean projection: same visible values, no file bytes."""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")

from services.status_po import (  # noqa: E402
    build_status_po,
    effective_workflow_status,
    filter_orders_for_view,
    item_line_names,
    status_po_row,
)


def _status_po_fn_body() -> str:
    start = SERVER.index("async def status_po_dashboard(")
    end = SERVER.index("class ProductionExportPayload")
    return SERVER[start:end]


def _status_po_projection_src() -> str:
    start = SERVER.index("STATUS_PO_PROJECTION = {")
    end = SERVER.index("@api_router.get(\"/dashboard/status-po\")")
    return SERVER[start:end]


def _parse_status_po_projection() -> dict:
    src = _status_po_projection_src()
    start = src.index("{")
    end = src.rindex("}") + 1
    return ast.literal_eval(src[start:end])


def _project_included(value, paths):
    """Apply dotted inclusion paths from the parsed STATUS_PO_PROJECTION."""
    if any(not parts for parts in paths):
        return value
    if isinstance(value, list):
        return [
            _project_included(item, paths) if isinstance(item, dict) else item
            for item in value
        ]
    if not isinstance(value, dict):
        return value
    grouped = {}
    for parts in paths:
        grouped.setdefault(parts[0], []).append(parts[1:])
    out = {}
    for head, rests in grouped.items():
        if head not in value:
            continue
        out[head] = _project_included(value[head], rests)
    return out


def apply_parsed_status_po_projection(order: dict) -> dict:
    """Shape a document with the inclusion list parsed from server.py."""
    projection = _parse_status_po_projection()
    paths = [key.split(".") for key, flag in projection.items() if flag and key != "_id"]
    return _project_included(order or {}, paths)


def _pick(src: dict, keys: tuple) -> dict:
    return {k: src[k] for k in keys if k in src}


def apply_status_po_projection(order: dict) -> dict:
    """In-memory mirror of STATUS_PO_PROJECTION (tests/parity)."""
    src = order or {}
    shaped = _pick(src, (
        "id", "po_number", "customer", "order_date", "deadline",
        "delivery_deadline_manual", "scheduled_delivery_date", "buffer_stock",
        "design_approved_at", "design_bypassed_at",
        "payment_approved_at", "payment_bypassed_at",
        "delivered_at", "qty_delivered", "qty_packed", "delivery_status",
        "current_stage", "total_quantity", "quantity", "packing_required_total",
        "requires_printing",
        "cancelled", "work_date", "hold", "priority", "status_label",
        "design_status", "design_bypassed", "payment_status", "payment_bypassed",
        "sample_approved", "packing_status", "packing_status_semantic",
        "surat_jalan_created", "surat_jalan_status_semantic",
        "delivery_note_number", "delivery_note_created_at", "delivery_note_created_by",
        "loading_completed", "loading_completed_at", "loading_qty_status",
        "terkirim_confirmed", "print_status", "print_completed_qty",
    ))
    shaped["delivery_proofs"] = [
        _pick(p, ("uploaded_at", "created_at"))
        for p in (src.get("delivery_proofs") or [])
        if isinstance(p, dict)
    ]
    shaped["line_items"] = [
        _pick(li, ("name", "item_name", "product_name", "quantity", "production_process"))
        for li in (src.get("line_items") or [])
        if isinstance(li, dict)
    ]
    shaped["operator_splits"] = [
        _pick(s, ("qty_completed", "line_idx", "quantity", "work_date", "operator_name"))
        for s in (src.get("operator_splits") or [])
        if isinstance(s, dict)
    ]
    shaped["shipments"] = []
    for sh in (src.get("shipments") or []):
        if not isinstance(sh, dict):
            continue
        row = _pick(sh, (
            "id", "created_at", "number", "delivery_status", "delivered_at",
            "sales_delivery_id", "status", "sj_status", "cancelled", "is_cancelled",
            "deleted", "is_deleted", "superseded",
            "superseded_by_shipment_id", "qty_delivered", "total_qty",
        ))
        row["lines"] = [ln for ln in (sh.get("lines") or []) if isinstance(ln, dict)]
        shaped["shipments"].append(row)
    shaped["design_files"] = [_pick(f, ("id",)) for f in (src.get("design_files") or []) if isinstance(f, dict)]
    shaped["payment_files"] = [_pick(f, ("id",)) for f in (src.get("payment_files") or []) if isinstance(f, dict)]
    shaped["sample_files"] = [_pick(f, ("id",)) for f in (src.get("sample_files") or []) if isinstance(f, dict)]
    snap = src.get("customer_approval")
    if isinstance(snap, dict) and "status" in snap:
        shaped["customer_approval"] = {"status": snap.get("status")}
    si = src.get("screener_info")
    if isinstance(si, dict) and "screen_qty" in si:
        shaped["screener_info"] = {"screen_qty": si.get("screen_qty")}
    hp = src.get("helper_prep")
    if isinstance(hp, dict) and "qty_prepared" in hp:
        shaped["helper_prep"] = {"qty_prepared": hp.get("qty_prepared")}
    pi = src.get("packing_info")
    if isinstance(pi, dict):
        packed = {}
        if "qty_packed" in pi:
            packed["qty_packed"] = pi.get("qty_packed")
        packed["lines"] = [
            _pick(ln, ("line_id", "line_idx", "packed_qty"))
            for ln in (pi.get("lines") or [])
            if isinstance(ln, dict)
        ]
        shaped["packing_info"] = packed
    li = src.get("loading_info")
    if isinstance(li, dict):
        shaped["loading_info"] = {
            "events": [
                _pick(ev, ("at", "created_at", "qty", "line_idx"))
                for ev in (li.get("events") or [])
                if isinstance(ev, dict)
            ],
            "lines": [
                _pick(ln, ("line_idx", "loaded_qty"))
                for ln in (li.get("lines") or [])
                if isinstance(ln, dict)
            ],
        }
    shaped["loading_confirmations"] = [
        {
            **_pick(c, ("id", "surat_jalan_id", "shipment_id", "cancelled", "deleted")),
            "lines": [
                _pick(ln, ("line_idx", "qty", "quantity"))
                for ln in (c.get("lines") or [])
                if isinstance(ln, dict)
            ],
        }
        for c in (src.get("loading_confirmations") or [])
        if isinstance(c, dict)
    ]
    shaped["legacy_loading_allocations"] = [
        {
            **_pick(row, ("status", "shipment_id", "surat_jalan_id")),
            "lines": [
                _pick(ln, ("line_idx", "qty", "quantity"))
                for ln in (row.get("lines") or [])
                if isinstance(ln, dict)
            ],
        }
        for row in (src.get("legacy_loading_allocations") or [])
        if isinstance(row, dict)
    ]
    shaped["surat_jalan_files"] = [
        {
            **_pick(f, ("id", "shipment_id", "sj_number", "number", "sales_delivery_id", "status")),
            "lines": [
                _pick(ln, ("line_idx", "qty", "quantity"))
                for ln in (f.get("lines") or [])
                if isinstance(ln, dict)
            ],
        }
        for f in (src.get("surat_jalan_files") or [])
        if isinstance(f, dict)
    ]
    shaped["delivery_confirmations"] = [
        _pick(rec, ("shipment_id", "qty", "confirmed_at"))
        for rec in (src.get("delivery_confirmations") or [])
        if isinstance(rec, dict)
    ]
    return shaped


def _fat_blob(tag: str) -> str:
    return f"data:image/png;base64,{tag}" + ("A" * 8000)


def _order_packing(**kw):
    base = {
        "id": "po-pack",
        "po_number": "PO-PACK",
        "customer": "Pack Cust",
        "order_date": "2026-08-12",
        "current_stage": 11,
        "cancelled": False,
        "hold": False,
        "line_items": [{"quantity": 100, "production_process": "print"}],
        "operator_splits": [
            {"qty_completed": 100, "line_idx": 0, "quantity": 100, "work_date": "2026-08-12"}
        ],
        "qty_delivered": 0,
        "total_quantity": 100,
        "packing_status": "Sudah",
        "packing_info": {
            "qty_packed": 100,
            "photo_data_url": _fat_blob("pack"),
            "notes": "heavy unused packing note",
            "lines": [{"line_id": "L1", "line_idx": 0, "packed_qty": 100, "photo": _fat_blob("pline")}],
        },
        "design_files": [{"id": "d1", "data_url": _fat_blob("d"), "name": "a.png"}],
        "payment_files": [{"id": "p1", "data_url": _fat_blob("p"), "name": "b.png"}],
        "sample_files": [{"id": "s1", "data_url": _fat_blob("s"), "name": "c.png"}],
        "loading_info": {
            "vehicle_plate": "B 1 XX",
            "events": [{"at": "2026-08-12T03:00:00+00:00", "photo": _fat_blob("load")}],
        },
        "design_status": "Approved",
        "payment_status": "Approved",
    }
    base.update(kw)
    return base


def _order_active(**kw):
    base = {
        "id": "po-op",
        "po_number": "PO-OP",
        "customer": "Active Cust",
        "order_date": "2026-08-10",
        "current_stage": 8,
        "cancelled": False,
        "line_items": [{"quantity": 10000, "production_process": "print"}],
        "operator_splits": [
            {"qty_completed": 2500, "line_idx": 0, "quantity": 10000, "operator_name": "Op"}
        ],
        "qty_delivered": 0,
        "total_quantity": 10000,
        "helper_prep": {"qty_prepared": 10000},
        "screener_info": {"screen_qty": 4},
        "sample_approved": True,
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_files": [{"id": "d1", "data_url": _fat_blob("d")}],
        "payment_files": [{"id": "p1", "data_url": _fat_blob("p")}],
        "sample_files": [{"id": "s1", "data_url": _fat_blob("s")}],
    }
    base.update(kw)
    return base


def _order_terkirim(**kw):
    base = {
        "id": "po-done",
        "po_number": "PO-DONE",
        "customer": "Done Cust",
        "order_date": "2026-07-01",
        "current_stage": 14,
        "cancelled": False,
        "line_items": [{"quantity": 200, "production_process": "print"}],
        "operator_splits": [{"qty_completed": 200, "line_idx": 0, "quantity": 200}],
        "qty_delivered": 200,
        "total_quantity": 200,
        "delivery_status": "Terkirim",
        "terkirim_confirmed": True,
        "packing_info": {"qty_packed": 200, "lines": [{"line_idx": 0, "packed_qty": 200}]},
        "design_files": [{"id": "d1", "data_url": _fat_blob("d")}],
        "payment_files": [{"id": "p1", "data_url": _fat_blob("p")}],
    }
    base.update(kw)
    return base


def _order_legacy_missing(**kw):
    base = {
        "id": "po-legacy",
        "po_number": "PO-LEG",
        "customer": "Legacy",
        "order_date": "2026-06-01",
        "current_stage": 1,
        "cancelled": False,
        "quantity": 50,
        "design_files": [{"id": "d1"}],
    }
    base.update(kw)
    return base


VISIBLE_KEYS = ("id", "po_number", "order_date", "customer", "status", "percent_delivered")


def _visible(row: dict) -> dict:
    return {k: row.get(k) for k in VISIBLE_KEYS}


def test_status_po_handler_stays_read_only_and_uses_projection():
    body = _status_po_fn_body()
    assert "STATUS_PO_PROJECTION" in body
    assert "find(q, STATUS_PO_PROJECTION)" in body
    assert "build_status_po" in body
    assert "require_perm(\"schedule.view\")" in body
    for banned in ("update_one", "insert_one", "delete_one", "replace_one", "update_many", "delete_many"):
        assert banned not in body


def test_projection_keeps_required_fields_and_excludes_file_bytes():
    src = _status_po_projection_src()
    for key in (
        "id",
        "po_number",
        "customer",
        "order_date",
        "design_files.id",
        "payment_files.id",
        "sample_files.id",
        "packing_info.qty_packed",
        "packing_info.lines.packed_qty",
        "customer_approval.status",
        "operator_splits.quantity",
        "line_items.name",
        "requires_printing",
        "delivery_confirmations.confirmed_at",
        "shipments.delivered_at",
        "shipments.is_cancelled",
        "shipments.deleted",
        "shipments.is_deleted",
        "delivered_at",
    ):
        assert key in src
    assert '"design_files": 1' not in src
    assert '"payment_files": 1' not in src
    assert '"sample_files": 1' not in src
    assert '"packing_info": 1' not in src
    assert '"loading_info": 1' not in src
    for banned in ("data_url", "attachments", "timeline", "notes", "hpp", "accurate"):
        assert banned not in src


@pytest.mark.parametrize(
    "order",
    [
        _order_active(),
        _order_packing(),
        _order_terkirim(),
        _order_legacy_missing(),
        _order_active(
            id="po-screener",
            po_number="PO-SCR",
            current_stage=6,
            sample_approved=False,
            sample_files=[],
            operator_splits=[],
            helper_prep={},
        ),
    ],
)
def test_lean_projection_same_visible_status_po_values(order):
    full = status_po_row(order)
    lean = status_po_row(apply_status_po_projection(order))
    assert _visible(lean) == _visible(full)
    assert lean["percent_delivered"] == full["percent_delivered"]
    assert effective_workflow_status(apply_status_po_projection(order)) == effective_workflow_status(order)


def test_projection_keeps_line_item_names_for_status_po_rows():
    order = _order_packing(line_items=[{
        "name": "Cup 16oz",
        "item_name": "Cup 16oz",
        "product_name": "Cup 16oz Printed",
        "quantity": 100,
        "production_process": "print",
    }])
    lean = apply_status_po_projection(order)
    assert item_line_names(order) == ["Cup 16oz"]
    assert item_line_names(lean) == ["Cup 16oz"]
    assert status_po_row(lean)["item_lines"] == ["Cup 16oz"]


def test_lean_build_same_record_count_and_filters():
    orders = [_order_active(), _order_packing(), _order_terkirim(), _order_legacy_missing()]
    lean_orders = [apply_status_po_projection(o) for o in orders]
    full = build_status_po(orders)
    lean = build_status_po(lean_orders)
    assert lean["total"] == full["total"] == 4
    assert [r["po_number"] for r in lean["rows"]] == [r["po_number"] for r in full["rows"]]
    assert [_visible(r) for r in lean["rows"]] == [_visible(r) for r in full["rows"]]

    filtered_full = build_status_po(orders, filters={"customer": "Pack"})
    filtered_lean = build_status_po(lean_orders, filters={"customer": "Pack"})
    assert [r["id"] for r in filtered_full["rows"]] == [r["id"] for r in filtered_lean["rows"]] == ["po-pack"]


def test_view_filters_unchanged_on_lean_docs():
    orders = [_order_active(current_stage=8), _order_terkirim()]
    lean = [apply_status_po_projection(o) for o in orders]
    assert [o["id"] for o in filter_orders_for_view(orders, "active")] == ["po-op"]
    assert [o["id"] for o in filter_orders_for_view(lean, "active")] == ["po-op"]
    assert [o["id"] for o in filter_orders_for_view(orders, "completed")] == ["po-done"]
    assert [o["id"] for o in filter_orders_for_view(lean, "completed")] == ["po-done"]


def test_projection_keeps_age_end_from_last_valid_confirmation():
    order = _order_terkirim(
        delivered_at="2026-09-20T10:00:00+07:00",
        scheduled_delivery_date="2026-09-25",
        deadline="2026-09-30T00:00:00+07:00",
        delivery_proofs=[{
            "uploaded_at": "2026-09-21T00:00:00+07:00",
            "created_at": "2026-09-21T00:00:00+07:00",
            "data_url": _fat_blob("proof"),
        }],
        shipments=[
            {
                "id": "sj-live",
                "created_at": "2026-09-02T01:00:00+07:00",
                "delivered_at": "2026-09-05T10:00:00+07:00",
                "delivery_status": "Terkirim",
                "lines": [{"line_idx": 0, "qty": 200}],
                "photo": _fat_blob("sj"),
            },
            {
                "id": "sj-old",
                "superseded": True,
                "status": "superseded",
                "delivered_at": "2026-09-20T10:00:00+07:00",
                "delivery_status": "Terkirim",
                "lines": [{"line_idx": 0, "qty": 200}],
            },
        ],
        delivery_confirmations=[
            {
                "shipment_id": "sj-old",
                "qty": 200,
                "confirmed_at": "2026-09-20T10:00:00+07:00",
                "note": "ignore me",
            },
            {
                "shipment_id": "sj-live",
                "qty": 200,
                "confirmed_at": "2026-09-05T10:00:00+07:00",
            },
        ],
    )
    lean = apply_status_po_projection(order)
    assert "confirmed_at" in lean["delivery_confirmations"][0]
    assert "data_url" not in json.dumps(lean)
    full = status_po_row(order)
    projected = status_po_row(lean)
    assert full["age_closed"] is True
    assert full["age_end_at"] == "2026-09-05T10:00:00+07:00"
    assert projected["age_closed"] == full["age_closed"]
    assert projected["age_end_at"] == full["age_end_at"]
    assert projected["status"] == full["status"]
    assert projected["percent_delivered"] == full["percent_delivered"]


@pytest.mark.parametrize("flag", ["is_cancelled", "deleted", "is_deleted"])
def test_parsed_projection_ignores_later_confirmation_on_inactive_shipment(flag):
    """Age end uses STATUS_PO_PROJECTION as parsed from server.py."""
    active_at = "2026-09-05T10:00:00+07:00"
    inactive_at = "2026-09-20T10:00:00+07:00"
    order = _order_terkirim(
        delivered_at=inactive_at,
        shipments=[
            {
                "id": "sj-live",
                "delivered_at": active_at,
                "delivery_status": "Terkirim",
                "lines": [{"line_idx": 0, "qty": 200}],
            },
            {
                "id": "sj-dead",
                flag: True,
                "delivered_at": inactive_at,
                "delivery_status": "Terkirim",
                "lines": [{"line_idx": 0, "qty": 200}],
                "secret_blob": _fat_blob("dead"),
            },
        ],
        delivery_confirmations=[
            {"shipment_id": "sj-live", "qty": 200, "confirmed_at": active_at},
            {
                "shipment_id": "sj-dead",
                "qty": 200,
                "confirmed_at": inactive_at,
                "note": "inactive confirmation",
            },
        ],
    )
    parsed = _parse_status_po_projection()
    assert parsed[f"shipments.{flag}"] == 1
    lean = apply_parsed_status_po_projection(order)
    dead = next(sh for sh in lean["shipments"] if sh.get("id") == "sj-dead")
    assert dead[flag] is True
    assert "secret_blob" not in dead
    assert lean["delivery_confirmations"][1]["confirmed_at"] == inactive_at
    assert "note" not in lean["delivery_confirmations"][1]
    row = status_po_row(lean)
    assert row["age_closed"] is True
    assert row["age_end_at"] == active_at
    mirrored = apply_status_po_projection(order)
    assert mirrored["shipments"][1][flag] is True
    assert status_po_row(mirrored)["age_end_at"] == active_at


def test_payload_size_reduction_local_harness_only():
    """LOCAL / TEST HARNESS ONLY — synthetic blobs, not production milliseconds."""
    order = _order_packing()
    lean = apply_status_po_projection(order)
    before = len(json.dumps(order, default=str))
    after = len(json.dumps(lean, default=str))
    assert after < before
    reduction = 100.0 * (before - after) / before
    assert reduction > 80
    dumped = json.dumps(lean)
    assert "data:image/png" not in dumped
    assert _fat_blob("d") not in dumped
    assert lean.get("design_files") == [{"id": "d1"}]
    assert lean.get("payment_files") == [{"id": "p1"}]
    assert lean.get("sample_files") == [{"id": "s1"}]
    assert "photo_data_url" not in (lean.get("packing_info") or {})
    Path("/tmp/status-po-payload-harness.json").write_text(
        json.dumps({"before": before, "after": after, "reduction_pct": reduction, "label": "LOCAL / TEST HARNESS ONLY"}),
        encoding="utf-8",
    )
