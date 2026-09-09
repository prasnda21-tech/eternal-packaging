"""Additive GET /dashboard/production?view=summary stays lean and canonical."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")

_VISIBLE_ROW_KEYS = (
    "id",
    "customer",
    "deadline",
    "delivery_deadline_effective",
    "delivery_deadline_status",
    "delivery_deadline_source",
    "work_date",
    "operator_work_dates",
    "priority",
    "urgent",
    "buffer_stock",
    "po_number",
    "outlet",
    "total_quantity",
    "print_qty_total",
    "print_completed_qty",
    "operator_assigned_qty",
    "operators",
    "design_status",
    "design_bypassed",
    "payment_status",
    "payment_bypassed",
    "hold",
    "hold_reasons",
    "screen_qty",
    "helper_required",
    "helper_prepared",
    "helper_shortage",
    "helper_shortage_status",
    "packing_status",
    "packing_qty",
    "delivery_note_number",
    "driver_name",
    "status_final",
    "cancelled",
    "current_stage",
    "status_label",
)


def _dashboard_fn_body() -> str:
    start = SERVER.index("async def production_dashboard(")
    end = SERVER.index("@api_router.get(\"/dashboard/shipment-status\")")
    return SERVER[start:end]


def test_summary_is_additive_opt_in_query():
    body = _dashboard_fn_body()
    assert "view: Optional[str] = None" in body
    assert '== "summary"' in body
    assert "_PRODUCTION_DASHBOARD_SUMMARY_PROJECTION" in body
    assert 'find(q, {"_id": 0})' in body


def test_default_production_dashboard_path_unchanged():
    body = _dashboard_fn_body()
    assert 'find(q, {"_id": 0})' in body
    assert "_apply_hold_snapshot" in body
    assert "_attach_deadline_snapshot" in body
    assert "_production_row" in body
    assert "include_cancelled" in body
    assert "include_completed" in body


def test_summary_projection_excludes_file_bytes():
    start = SERVER.index("_PRODUCTION_DASHBOARD_SUMMARY_PROJECTION")
    end = SERVER.index("@api_router.get(\"/dashboard/production\")")
    body = SERVER[start:end]
    for key in ("id", "po_number", "customer", "deadline", "line_items", "operator_splits"):
        assert key in body
    assert "design_files.id" in body
    assert "payment_files.id" in body
    assert "data_url" not in body
    assert "sample_files" not in body
    assert "timeline" not in body


def test_summary_and_default_are_read_only():
    body = _dashboard_fn_body()
    assert "update_one" not in body
    assert "update_many" not in body
    assert "replace_one" not in body
    assert "insert_one" not in body
    assert "delete_one" not in body
    assert "delete_many" not in body
    assert "bulk_write" not in body
    assert "find_one(" not in body.replace("_get_delivery_deadline_settings", "")


def test_summary_is_single_orders_find_not_n_plus_one():
    body = _dashboard_fn_body()
    assert body.count("db.orders.find") == 2  # summary branch + default branch
    assert "db.orders.find_one" not in body


def test_production_row_hold_matches_with_id_only_files():
    import sys
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv
    env_path = ROOT / ".env"
    alt = Path(r"C:\Projects\eternal-packaging\backend\.env")
    if env_path.exists():
        load_dotenv(env_path)
    elif alt.exists():
        load_dotenv(alt)
    from server import _apply_hold_snapshot, _production_row

    full = {
        "id": "po-hold",
        "po_number": "PO-H",
        "customer": "Cust",
        "current_stage": 2,
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_files": [{"id": "d1", "data_url": "data:application/pdf;base64,AAAA"}],
        "payment_files": [{"id": "p1", "data_url": "data:image/png;base64,BBBB"}],
        "line_items": [{"id": "l1", "name": "Cup", "quantity": 10, "production_process": "print"}],
    }
    slim = {
        "id": "po-hold",
        "po_number": "PO-H",
        "customer": "Cust",
        "current_stage": 2,
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_files": [{"id": "d1"}],
        "payment_files": [{"id": "p1"}],
        "line_items": [{"id": "l1", "name": "Cup", "quantity": 10, "production_process": "print"}],
    }
    _apply_hold_snapshot(full)
    _apply_hold_snapshot(slim)
    full_row = _production_row(full)
    slim_row = _production_row(slim)
    assert full_row["hold"] is False
    assert slim_row["hold"] is False
    assert full_row["hold_reasons"] == slim_row["hold_reasons"]


def test_production_row_keeps_same_name_line_identity():
    import sys
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv
    env_path = ROOT / ".env"
    alt = Path(r"C:\Projects\eternal-packaging\backend\.env")
    if env_path.exists():
        load_dotenv(env_path)
    elif alt.exists():
        load_dotenv(alt)
    from server import _apply_hold_snapshot, _production_row

    order = {
        "id": "po-lines",
        "po_number": "PO-L",
        "customer": "Cust",
        "total_quantity": 1600,
        "current_stage": 8,
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_files": [{"id": "d1"}],
        "payment_files": [{"id": "p1"}],
        "line_items": [
            {"id": "lid-a", "name": "Cup 16oz", "quantity": 1000, "production_process": "print", "screen_size": "large"},
            {"id": "lid-x", "name": "Lid", "quantity": 100, "production_process": "non_print"},
            {"id": "lid-b", "name": "Cup 16oz", "quantity": 500, "production_process": "print", "screen_size": "small"},
        ],
        "operator_splits": [
            {"id": "s1", "line_idx": 0, "operator_name": "Budi", "quantity": 1000, "qty_completed": 0, "stage": 8},
            {"id": "s2", "line_idx": 2, "operator_name": "Sari", "quantity": 500, "qty_completed": 0, "stage": 8},
        ],
    }
    _apply_hold_snapshot(order)
    row = _production_row(order)
    assert [op["line_idx"] for op in row["operators"]] == [0, 2]
    assert [op["line_id"] for op in row["operators"]] == ["lid-a", "lid-b"]
    assert [op["line_name"] for op in row["operators"]] == ["Cup 16oz", "Cup 16oz"]
    assert [op["split_id"] for op in row["operators"]] == ["s1", "s2"]
    assert [op["screen_size"] for op in row["operators"]] == ["large", "small"]
    assert row["print_qty_total"] == 1500
    assert row["total_quantity"] == 1600


def _row_visible(row: dict) -> dict:
    out = {k: row.get(k) for k in _VISIBLE_ROW_KEYS}
    ops = []
    for op in row.get("operators") or []:
        ops.append({
            "operator_name": op.get("operator_name"),
            "quantity": op.get("quantity"),
            "qty_completed": op.get("qty_completed"),
            "line_idx": op.get("line_idx"),
            "line_id": op.get("line_id"),
            "line_name": op.get("line_name"),
            "line_printable": op.get("line_printable"),
        })
    out["operators"] = ops
    return out


def test_live_summary_matches_default_visible_columns():
    """Same filters: same count and 20-column canonical values. No writes."""
    import copy
    import os
    import sys

    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv
    env_path = ROOT / ".env"
    alt = Path(r"C:\Projects\eternal-packaging\backend\.env")
    if env_path.exists():
        load_dotenv(env_path)
    elif alt.exists():
        load_dotenv(alt)
    if not os.environ.get("MONGO_URL"):
        return
    from pymongo import MongoClient
    from server import (
        _PRODUCTION_DASHBOARD_LIMIT,
        _PRODUCTION_DASHBOARD_SUMMARY_PROJECTION,
        _apply_hold_snapshot,
        _attach_deadline_snapshot,
        _production_row,
    )

    coll = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]].orders
    q = {"cancelled": {"$ne": True}}
    full = list(coll.find(q, {"_id": 0}).sort("deadline", 1).limit(_PRODUCTION_DASHBOARD_LIMIT))
    slim = list(coll.find(q, _PRODUCTION_DASHBOARD_SUMMARY_PROJECTION).sort("deadline", 1).limit(_PRODUCTION_DASHBOARD_LIMIT))
    assert len(full) == len(slim)
    assert len(full) > 0

    blob_hits = 0
    for doc in slim:
        dumped = str(doc)
        assert "data:image" not in dumped
        assert "data:application" not in dumped
        if "sample_files" in doc:
            blob_hits += 1
    assert blob_hits == 0

    # Deadline lead time is a settings lookup; use the same constant as live default.
    from server import _DELIVERY_DEADLINE_DEFAULT
    lead = int(_DELIVERY_DEADLINE_DEFAULT["lead_time_days"])
    settings = coll.database.app_settings.find_one({"key": "delivery_deadline"}, {"_id": 0})
    if settings:
        lead = int(settings.get("lead_time_days", lead))

    def build(docs, include_completed=True):
        rows = []
        for raw in docs:
            o = copy.deepcopy(raw)
            _apply_hold_snapshot(o)
            _attach_deadline_snapshot(o, lead)
            if not include_completed and o.get("current_stage", 0) >= 14:
                continue
            rows.append(_production_row(o))
        return rows

    default_rows = build(full, True)
    summary_rows = build(slim, True)
    assert len(default_rows) == len(summary_rows)
    assert any((r.get("current_stage") or 0) >= 14 for r in default_rows)

    for a, b in zip(default_rows, summary_rows):
        assert _row_visible(a) == _row_visible(b)

    without_completed = build(slim, False)
    assert all((r.get("current_stage") or 0) < 14 for r in without_completed)
    assert len(without_completed) < len(summary_rows) or all(
        (r.get("current_stage") or 0) < 14 for r in summary_rows
    )

    cancelled_q = {}
    all_docs = list(coll.find(cancelled_q, {"id": 1, "cancelled": 1}).limit(_PRODUCTION_DASHBOARD_LIMIT))
    cancelled_n = sum(1 for d in all_docs if d.get("cancelled") is True)
    if cancelled_n:
        slim_with_cancelled = list(
            coll.find(cancelled_q, _PRODUCTION_DASHBOARD_SUMMARY_PROJECTION).sort("deadline", 1).limit(_PRODUCTION_DASHBOARD_LIMIT)
        )
        assert len(slim_with_cancelled) >= len(slim)
        assert any(d.get("cancelled") is True for d in slim_with_cancelled)
        assert all(d.get("cancelled") is not True for d in slim)
