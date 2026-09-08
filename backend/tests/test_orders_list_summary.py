"""Additive GET /orders?view=summary must stay lean and zero-write."""
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


def test_summary_is_additive_opt_in_query():
    body = _list_orders_fn_body()
    assert 'view: Optional[str] = None' in body
    assert 'view == "summary"' in body or '== "summary"' in body
    assert 'return await _list_orders_summary(st)' in body


def test_default_list_orders_path_unchanged():
    body = _list_orders_fn_body()
    assert 'db.orders.find(q, {"_id": 0})' in body
    assert "_compute_pending_queues" in body
    assert "stage_counts" in body
    assert "_attach_deadline_snapshot" in body


def test_summary_path_does_not_load_full_documents_or_blobs():
    body = _summary_fn_body()
    assert "aggregate" in body
    assert "_orders_list_summary_projection" in body
    assert 'find(q, {"_id": 0})' not in body
    assert "sample_files" not in body or "sample_files" in _ORDERS_SUMMARY_STRIP_FROM_SOURCE()
    assert "_compute_pending_queues" not in body
    assert "all_active" not in body


def _ORDERS_SUMMARY_STRIP_FROM_SOURCE():
    start = SERVER.index("_ORDERS_SUMMARY_STRIP_KEYS")
    end = SERVER.index("def _orders_list_summary_projection")
    return SERVER[start:end]


def test_summary_projection_has_list_fields_not_file_bytes():
    start = SERVER.index("def _orders_list_summary_projection")
    end = SERVER.index("def _finalize_orders_summary_row")
    body = SERVER[start:end]
    for key in ("id", "po_number", "customer", "outlet", "deadline", "work_date", "current_stage", "line_items"):
        assert key in body
    assert "design_files" in body  # existence expr only
    assert "data_url" not in body
    assert "sample_files" not in body
    assert "delivery_proofs" not in body
    assert "timeline" not in body
    assert "_mongo_array_nonempty_expr" in body


def test_summary_keeps_aktif_hold_semantics():
    body = _summary_fn_body()
    assert 'st == "aktif"' in body
    assert "not o.get(\"hold\")" in body
    assert "_apply_hold_snapshot" in SERVER[SERVER.index("def _finalize_orders_summary_row"): SERVER.index("def _orders_list_match")]
    match_src = SERVER[SERVER.index("def _orders_list_match"): SERVER.index("async def _list_orders_summary")]
    assert 'current_stage' not in match_src


def test_summary_and_default_list_orders_are_read_only():
    for body in (_list_orders_fn_body(), _summary_fn_body()):
        assert "update_one" not in body
        assert "update_many" not in body
        assert "replace_one" not in body
        assert "insert_one" not in body
        assert "delete_one" not in body
        assert "delete_many" not in body
        assert "bulk_write" not in body


def test_finalize_summary_row_hold_and_strip():
    import sys
    sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv
    env_path = ROOT / ".env"
    alt = Path(r"C:\Projects\eternal-packaging\backend\.env")
    if env_path.exists():
        load_dotenv(env_path)
    elif alt.exists():
        load_dotenv(alt)
    from server import _finalize_orders_summary_row

    held = _finalize_orders_summary_row({
        "id": "a",
        "po_number": "PO-1",
        "customer": "Cust A",
        "current_stage": 2,
        "_has_design_files": False,
        "_has_payment_files": False,
        "line_items": [{"name": "Cup", "quantity": 10}],
    })
    assert held["hold"] is True
    assert held["customer_name"] == "Cust A"
    assert "design_files" not in held
    assert "payment_files" not in held
    assert "_has_design_files" not in held

    ready = _finalize_orders_summary_row({
        "id": "b",
        "po_number": "PO-2",
        "customer_name": "Cust B",
        "current_stage": 11,
        "_has_design_files": True,
        "_has_payment_files": True,
        "line_items": [{"name": "Bag", "quantity": 5}],
    })
    assert ready["hold"] is False
    assert ready["customer_name"] == "Cust B"
    assert "design_files" not in ready
