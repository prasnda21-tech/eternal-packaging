"""GET /api/reminders lean Mongo projection — query-shape and semantic locks.

Perbaikan #6: replace near-full find() with aggregate $limit + dedicated
projection. Reminder semantics, role filters, sort, and response contract
stay in Python and must match the previous full-document path.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

_env_path = BACKEND_DIR / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test_database")

import server  # noqa: E402

# ---------------------------------------------------------------------------
# Query-shape / source locks
# ---------------------------------------------------------------------------

_SERVER_SRC = inspect.getsource(server)
_REMINDERS_SRC = inspect.getsource(server.reminders)

_REMINDERS_REQUIRED_SCALARS = (
    "id",
    "po_number",
    "outlet",
    "customer",
    "deadline",
    "priority",
    "buffer_stock",
    "current_stage",
    "status_label",
)

_HEAVY_FIELDS = (
    "design_files",
    "payment_files",
    "sample_files",
    "surat_jalan_files",
    "delivery_proofs",
    "delivery_signatures",
    "timeline",
    "notes",
    "crm_history",
    "comments",
    "packing_photos",
    "packing_payload",
    "loading_payload",
    "artwork",
    "hpp",
    "inventory",
    "accurate",
    "shipments",
    "line_items",
)

_RESPONSE_ROW_KEYS = (
    "id",
    "po_number",
    "outlet",
    "customer",
    "deadline",
    "priority",
    "buffer_stock",
    "current_stage",
    "status_label",
    "owner_role",
    "bucket",
    "hours_left",
)


def _pipeline_from_reminders_source() -> list:
    """Parse aggregate([...]) stages in reminders() without executing them."""
    tree = ast.parse(_REMINDERS_SRC)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "aggregate":
            continue
        if not node.args:
            continue
        pipeline_node = node.args[0]
        if not isinstance(pipeline_node, ast.List):
            continue
        stages = []
        for elt in pipeline_node.elts:
            if not isinstance(elt, ast.Dict) or not elt.keys:
                raise AssertionError("unexpected aggregate stage")
            key_node = elt.keys[0]
            val_node = elt.values[0]
            if not isinstance(key_node, ast.Constant):
                raise AssertionError("aggregate stage key is not a constant")
            name = key_node.value
            if isinstance(val_node, ast.Name) and val_node.id == "_REMINDERS_LIMIT":
                stages.append({name: 1000})
            elif isinstance(val_node, ast.Name) and name == "$match":
                stages.append({name: val_node.id})
            elif (
                isinstance(val_node, ast.Call)
                and isinstance(val_node.func, ast.Name)
                and val_node.func.id == "_orders_reminders_projection"
            ):
                stages.append({name: "_orders_reminders_projection()"})
            else:
                raise AssertionError(f"unexpected aggregate value for {name}: {ast.dump(val_node)}")
        return stages
    raise AssertionError("reminders() has no aggregate([...]) pipeline")


def test_reminders_uses_aggregate_not_near_full_find():
    assert "db.orders.aggregate(" in _REMINDERS_SRC
    assert "_orders_reminders_projection()" in _REMINDERS_SRC
    assert "_REMINDERS_LIMIT" in _REMINDERS_SRC
    assert 'db.orders.find({}, {"_id": 0, "design_files": 0, "sample_files": 0})' not in _REMINDERS_SRC
    assert ".find(" not in _REMINDERS_SRC


def test_reminders_limit_is_exactly_1000():
    assert server._REMINDERS_LIMIT == 1000
    pipeline = _pipeline_from_reminders_source()
    assert pipeline[0] == {"$match": "scope"}
    assert pipeline[1] == {"$limit": 1000}
    assert "order_access_scope" in _REMINDERS_SRC
    assert ".to_list(_REMINDERS_LIMIT)" in _REMINDERS_SRC or ".to_list(1000)" in _REMINDERS_SRC


def test_reminders_pipeline_scopes_before_limit_and_has_no_sort():
    pipeline = _pipeline_from_reminders_source()
    stages = [next(iter(stage)) for stage in pipeline]
    assert "$sort" not in stages
    assert stages == ["$match", "$limit", "$project"]


def test_reminders_scope_precedes_limit_without_changing_deadline_rules():
    """Branch scope is the only Mongo match. Stage and deadline stay in Python."""
    pipeline = _pipeline_from_reminders_source()
    assert list(pipeline[0].keys()) == ["$match"]
    assert pipeline[0]["$match"] == "scope"
    assert pipeline[1]["$limit"] == 1000
    assert list(pipeline[2].keys()) == ["$project"]
    pipeline_src = _REMINDERS_SRC[_REMINDERS_SRC.index("aggregate(") : _REMINDERS_SRC.index(".to_list")]
    assert "$sort" not in pipeline_src
    assert "cancelled" not in pipeline_src
    assert "current_stage" not in pipeline_src  # stage filter stays in Python loop


def test_reminders_projection_is_strict_inclusion():
    proj = server._orders_reminders_projection()
    assert proj["_id"] == 0
    for field in _REMINDERS_REQUIRED_SCALARS:
        assert proj[field] == 1, field
    assert "operator_splits" in proj
    include_keys = [k for k, v in proj.items() if v == 1]
    assert set(include_keys) == set(_REMINDERS_REQUIRED_SCALARS)
    for field in _HEAVY_FIELDS:
        assert field not in proj, field


def test_reminders_projection_is_not_dashboard_or_action_center_reuse():
    reminders_proj = server._orders_reminders_projection()
    dashboard_proj = server._orders_dashboard_summary_projection()
    action_proj = server._orders_action_center_projection()
    assert reminders_proj != dashboard_proj
    assert reminders_proj != action_proj
    assert "design_files" not in reminders_proj
    assert "payment_files" not in reminders_proj
    assert "qty_total" not in reminders_proj
    assert "qty_completed" not in reminders_proj
    assert "workflow_ready" not in reminders_proj
    assert "tanggal_pengiriman" not in reminders_proj
    assert "delivery_deadline_status" not in reminders_proj


def test_reminders_does_not_use_hop_deadline_snapshot_helpers():
    assert "_attach_deadline_snapshot" not in _REMINDERS_SRC
    assert "delivery_deadline_status" not in _REMINDERS_SRC
    assert "delivery_deadline_effective" not in _REMINDERS_SRC
    assert "_compute_pending_queues" not in _REMINDERS_SRC
    assert "_deadline_status(" in _REMINDERS_SRC
    assert "STAGE_OWNER_ROLE" in _REMINDERS_SRC
    # Buffer Stock never enters customer-overdue reminder bucket.
    assert 'buffer_stock' in _REMINDERS_SRC
    assert 'ds["bucket"] == "overdue"' in _REMINDERS_SRC


def test_reminders_python_still_skips_done_stage_and_ok_bucket():
    assert 'o.get("current_stage", 0) >= 14' in _REMINDERS_SRC
    assert 'ds["bucket"] == "ok"' in _REMINDERS_SRC


def test_reminders_sort_order_source_lock():
    assert '"overdue": 0' in _REMINDERS_SRC
    assert '"warn_6h": 1' in _REMINDERS_SRC
    assert '"warn_h1": 2' in _REMINDERS_SRC
    assert "items.sort(key=lambda x:" in _REMINDERS_SRC
    assert "x[\"hours_left\"]" in _REMINDERS_SRC
    assert "$sort" not in _REMINDERS_SRC


def test_reminders_operator_splits_only_email():
    expr = server._orders_reminders_operator_splits_expr()
    dumped = json.dumps(expr)
    assert "operator_email" in dumped
    for extra in (
        "operator_name",
        "quantity",
        "qty_completed",
        "stage",
        "work_date",
        "progress_entries",
        "photos",
    ):
        assert extra not in dumped
    shaped = server._shape_order_for_reminders_compute(
        {
            "id": "x",
            "operator_splits": [
                {
                    "operator_email": "op@x.com",
                    "operator_name": "should-drop",
                    "quantity": 99,
                    "photos": ["a"],
                }
            ],
        }
    )
    assert shaped["operator_splits"] == [{"operator_email": "op@x.com"}]


def test_reminders_heavy_fields_dropped_by_shaper():
    raw = {field: [{"blob": "x" * 50}] for field in _HEAVY_FIELDS}
    raw.update({"id": "po-1", "po_number": "EP-1", "deadline": "2026-01-01T00:00:00+00:00", "current_stage": 1})
    shaped = server._shape_order_for_reminders_compute(raw)
    for field in _HEAVY_FIELDS:
        assert field not in shaped, field


# ---------------------------------------------------------------------------
# Semantic fixtures
# ---------------------------------------------------------------------------

def _full_order(**overrides):
    base = {
        "id": "po-full",
        "po_number": "EP-FULL",
        "outlet": "Outlet A",
        "customer": "Customer A",
        "deadline": "2026-01-15T12:00:00+00:00",
        "priority": "high",
        "current_stage": 1,
        "status_label": "Design",
        "status": "in_progress",
        "operator_splits": [
            {
                "operator_email": "op@eternal.com",
                "operator_name": "Op",
                "quantity": 100,
                "qty_completed": 10,
                "stage": 8,
                "work_date": "2026-01-10",
                "progress_entries": [{"note": "x"}],
                "photos": ["p1"],
            }
        ],
        "design_files": [{"url": "https://cdn/design.pdf", "blob": "D" * 200}],
        "payment_files": [{"url": "https://cdn/pay.pdf"}],
        "sample_files": [{"url": "https://cdn/sample.pdf"}],
        "surat_jalan_files": [{"url": "https://cdn/sj.pdf"}],
        "delivery_proofs": [{"url": "proof"}],
        "timeline": [{"event": "created"}],
        "notes": "internal notes " * 20,
        "crm_history": [{"msg": "hi"}],
        "comments": [{"body": "c"}],
        "line_items": [{"sku": "BOX", "qty": 500}],
        "shipments": [{"id": "sh1"}],
        "artwork": {"layers": [1, 2, 3]},
        "hpp": {"cogs": 999},
        "inventory": {"stock": 1},
        "accurate": {"synced": True},
        "qty_total": 500,
        "qty_completed": 10,
        "tanggal_pengiriman": "2026-02-01",
        "delivery_deadline_status": "overdue",
        "workflow_ready": True,
    }
    base.update(overrides)
    return base


def _projected(order: dict) -> dict:
    return server._shape_order_for_reminders_compute(order)


class _FakeOrders:
    def __init__(self, docs):
        self.docs = docs
        self.aggregate_calls = []
        self.find_calls = []
        self.count_calls = []
        self.write_calls = []

    async def count_documents(self, query, *args, **kwargs):
        self.count_calls.append(query)
        return len(self.docs)

    def aggregate(self, pipeline):
        self.aggregate_calls.append(pipeline)
        result = MagicMock()
        result.to_list = AsyncMock(return_value=list(self.docs))
        return result

    def find(self, *args, **kwargs):
        self.find_calls.append((args, kwargs))
        result = MagicMock()
        result.to_list = AsyncMock(return_value=list(self.docs))
        return result

    def insert_one(self, *a, **k):
        self.write_calls.append("insert_one")
        raise AssertionError("GET /api/reminders must not write")

    def update_one(self, *a, **k):
        self.write_calls.append("update_one")
        raise AssertionError("GET /api/reminders must not write")

    def replace_one(self, *a, **k):
        self.write_calls.append("replace_one")
        raise AssertionError("GET /api/reminders must not write")

    def delete_one(self, *a, **k):
        self.write_calls.append("delete_one")
        raise AssertionError("GET /api/reminders must not write")

    def bulk_write(self, *a, **k):
        self.write_calls.append("bulk_write")
        raise AssertionError("GET /api/reminders must not write")


def _user(role: str, email: str = "user@eternal.com") -> dict:
    return {"id": f"u-{role}", "role": role, "email": email, "name": role}


async def _run_reminders_async(docs, role="admin", email="admin@eternal.com"):
    fake = _FakeOrders(docs)
    original = server.db
    server.db = SimpleNamespace(orders=fake)
    try:
        return await server.reminders(user=_user(role, email)), fake
    finally:
        server.db = original


def _run_reminders(docs, role="admin", email="admin@eternal.com"):
    return asyncio.run(_run_reminders_async(docs, role=role, email=email))


def _now_iso(delta_hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=delta_hours)).isoformat()


# ---------------------------------------------------------------------------
# Semantic parity: full vs projected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "label,overrides",
    [
        ("overdue", {"id": "po-ov", "deadline": _now_iso(-10), "current_stage": 1, "status_label": "Design"}),
        ("warn_6h", {"id": "po-w6", "deadline": _now_iso(3), "current_stage": 1, "status_label": "Design"}),
        ("warn_h1", {"id": "po-h1", "deadline": _now_iso(20), "current_stage": 1, "status_label": "Design"}),
        ("ok", {"id": "po-ok", "deadline": _now_iso(80), "current_stage": 1, "status_label": "Design"}),
        ("done", {"id": "po-done", "deadline": _now_iso(-5), "current_stage": 14, "status_label": "Selesai"}),
        ("priority_urgent", {"id": "po-pr", "deadline": _now_iso(-2), "priority": "urgent", "current_stage": 1}),
        ("missing_deadline", {"id": "po-nd", "deadline": None, "current_stage": 1, "status_label": "Design"}),
        ("legacy_unparseable_deadline", {"id": "po-leg", "deadline": "15/01/2026 12:00", "current_stage": 1}),
        ("missing_optional", {"id": "po-mo", "outlet": None, "customer": None, "status_label": None, "priority": None, "deadline": _now_iso(-1), "current_stage": 2}),
        ("cancelled_open", {"id": "po-can", "status": "cancelled", "deadline": _now_iso(-4), "current_stage": 3, "status_label": "Payment"}),
    ],
)
def test_reminders_full_vs_projected_parity(label, overrides):
    full = _full_order(**overrides)
    projected = _projected(full)
    full_out, _ = _run_reminders([full], role="admin")
    proj_out, _ = _run_reminders([projected], role="admin")
    assert full_out["reminders"] == proj_out["reminders"], label
    assert full_out["counts"] == proj_out["counts"], label
    assert full_out["role_scope"] == proj_out["role_scope"], label


def test_reminders_ok_and_done_are_excluded_identically():
    ok = _full_order(id="ok1", deadline=_now_iso(90), current_stage=5)
    done = _full_order(id="done1", deadline=_now_iso(-20), current_stage=14)
    done15 = _full_order(id="done2", deadline=_now_iso(-20), current_stage=15)
    full_out, _ = _run_reminders([ok, done, done15])
    proj_out, _ = _run_reminders([_projected(ok), _projected(done), _projected(done15)])
    assert full_out["reminders"] == []
    assert proj_out["reminders"] == []
    assert full_out["counts"] == {"overdue": 0, "warn_6h": 0, "warn_h1": 0}


def test_reminders_cancelled_open_stage_parity_current_behavior():
    """Do not assert business correctness — only full vs projected parity.

    Current reminders do not filter cancelled in Mongo or Python.
    """
    cancelled = _full_order(
        id="can-open",
        status="cancelled",
        deadline=_now_iso(-8),
        current_stage=4,
        status_label="Approval Payment",
    )
    full_out, fake = _run_reminders([cancelled])
    proj_out, _ = _run_reminders([_projected(cancelled)])
    assert full_out["reminders"] == proj_out["reminders"]
    pipeline = fake.aggregate_calls[0]
    assert pipeline[0] == {"$match": {}}
    assert "cancelled" not in json.dumps(pipeline[0])
    # If current code emits the cancelled overdue PO, projected path must too.
    if full_out["reminders"]:
        assert full_out["reminders"][0]["id"] == "can-open"
        assert full_out["reminders"][0]["bucket"] == "overdue"


def test_reminders_legacy_deadline_uses_deadline_status_not_tanggal():
    """Current _deadline_status uses fromisoformat only; unparseable → ok → omitted.

    tanggal_pengiriman / delivery_deadline_status must not become reminder authority.
    """
    unparseable = _full_order(
        id="leg-dl",
        deadline="01/01/2020 08:00",
        tanggal_pengiriman=_now_iso(200),
        delivery_deadline_status="overdue",
        current_stage=1,
    )
    iso_overdue = _full_order(
        id="iso-dl",
        deadline=_now_iso(-8),
        tanggal_pengiriman=_now_iso(200),
        delivery_deadline_status="ok",
        current_stage=1,
    )
    out, _ = _run_reminders([_projected(unparseable), _projected(iso_overdue)])
    ids = [r["id"] for r in out["reminders"]]
    assert "leg-dl" not in ids
    assert ids == ["iso-dl"]
    assert out["reminders"][0]["bucket"] == "overdue"
    assert server._deadline_status("01/01/2020 08:00", 1)["bucket"] == "ok"


def test_reminders_sort_parity_urgency_then_hours_left():
    orders = [
        _full_order(id="h1-late", deadline=_now_iso(22), current_stage=1),
        _full_order(id="ov-soon", deadline=_now_iso(-1), current_stage=1),
        _full_order(id="w6", deadline=_now_iso(2), current_stage=1),
        _full_order(id="ov-old", deadline=_now_iso(-40), current_stage=1),
        _full_order(id="h1-soon", deadline=_now_iso(10), current_stage=1),
    ]
    full_out, _ = _run_reminders(orders)
    proj_out, _ = _run_reminders([_projected(o) for o in orders])
    full_ids = [r["id"] for r in full_out["reminders"]]
    proj_ids = [r["id"] for r in proj_out["reminders"]]
    assert full_ids == proj_ids
    assert full_ids[0:2] == ["ov-old", "ov-soon"] or full_ids[0] == "ov-old"
    buckets = [r["bucket"] for r in full_out["reminders"]]
    assert buckets == sorted(buckets, key=lambda b: ["overdue", "warn_6h", "warn_h1"].index(b))


def test_reminders_response_contract_keys():
    order = _full_order(id="contract", deadline=_now_iso(-3), current_stage=1)
    out, _ = _run_reminders([_projected(order)])
    assert set(out.keys()) == {
        "reminders", "counts", "role_scope", "generated_at",
        "read_limit", "matched_count", "completeness",
    }
    assert out["read_limit"] == 1000
    assert out["matched_count"] == 1
    assert out["completeness"] == "complete"
    assert isinstance(out["reminders"], list)
    assert out["reminders"]
    row = out["reminders"][0]
    assert set(row.keys()) == set(_RESPONSE_ROW_KEYS)
    assert out["counts"].keys() == {"overdue", "warn_6h", "warn_h1"}
    assert out["role_scope"] == "all"


def test_reminders_zero_write_and_aggregate_shape():
    order = _projected(_full_order(id="zw", deadline=_now_iso(-3), current_stage=1))
    out, fake = _run_reminders([order])
    assert fake.write_calls == []
    assert fake.find_calls == []
    assert len(fake.aggregate_calls) == 1
    pipeline = fake.aggregate_calls[0]
    assert pipeline[0] == {"$match": {}}
    assert pipeline[1] == {"$limit": 1000}
    assert "$sort" not in pipeline[1]
    assert pipeline[2]["$project"] == server._orders_reminders_projection()
    assert fake.count_calls == [{}]
    assert out["reminders"][0]["id"] == "zw"


# ---------------------------------------------------------------------------
# Role parity
# ---------------------------------------------------------------------------

_ROLE_CASES = (
    "admin",
    "head_production",
    "crm",
    "designer",
    "helper",
    "screener",
    "operator",
    "driver",
)


@pytest.mark.parametrize("role", _ROLE_CASES)
def test_reminders_role_visibility_full_vs_projected(role):
    # Design stage (1) owner is designer; helper stage 7; screener 7;
    # operator 8; driver 13 (pengiriman).
    orders = [
        _full_order(id="st1", current_stage=1, status_label="Design", deadline=_now_iso(-5)),
        _full_order(id="st6", current_stage=6, status_label="Screener", deadline=_now_iso(-5)),
        _full_order(id="st7", current_stage=7, status_label="Helper", deadline=_now_iso(-5)),
        _full_order(
            id="st8-match",
            current_stage=8,
            status_label="Operator",
            deadline=_now_iso(-5),
            operator_splits=[{"operator_email": "op@eternal.com", "operator_name": "Op"}],
        ),
        _full_order(
            id="st8-other",
            current_stage=8,
            status_label="Operator",
            deadline=_now_iso(-5),
            operator_splits=[{"operator_email": "other@eternal.com"}],
        ),
        _full_order(id="st13", current_stage=13, status_label="Pengiriman", deadline=_now_iso(-5)),
    ]
    email = "op@eternal.com" if role == "operator" else f"{role}@eternal.com"
    full_out, _ = _run_reminders(orders, role=role, email=email)
    proj_out, _ = _run_reminders([_projected(o) for o in orders], role=role, email=email)
    assert [r["id"] for r in full_out["reminders"]] == [r["id"] for r in proj_out["reminders"]]
    expected_scope = "all" if role in ("admin", "head_production", "crm") else "own"
    assert full_out["role_scope"] == proj_out["role_scope"] == expected_scope

    ids = {r["id"] for r in full_out["reminders"]}
    if role in ("admin", "head_production", "crm"):
        assert ids == {"st1", "st6", "st7", "st8-match", "st8-other", "st13"}
    elif role == "designer":
        assert ids == {"st1"}
        assert all(server.STAGE_OWNER_ROLE[r["current_stage"]] == "designer" for r in full_out["reminders"])
    elif role == "helper":
        assert ids == {"st7"}
    elif role == "screener":
        assert ids == {"st6"}
    elif role == "operator":
        assert ids == {"st8-match"}
        assert "st8-other" not in ids
    elif role == "driver":
        assert ids == {"st13"}


def test_reminders_operator_multiple_splits_one_matching_email():
    order = _full_order(
        id="multi-split",
        current_stage=8,
        deadline=_now_iso(-6),
        operator_splits=[
            {"operator_email": "other@eternal.com", "operator_name": "A", "quantity": 10},
            {"operator_email": "me@eternal.com", "photos": ["x"]},
            {"operator_email": "third@eternal.com"},
        ],
    )
    full_out, _ = _run_reminders([order], role="operator", email="me@eternal.com")
    proj_out, _ = _run_reminders([_projected(order)], role="operator", email="me@eternal.com")
    assert [r["id"] for r in full_out["reminders"]] == ["multi-split"]
    assert [r["id"] for r in proj_out["reminders"]] == ["multi-split"]

    miss_full, _ = _run_reminders([order], role="operator", email="nobody@eternal.com")
    miss_proj, _ = _run_reminders([_projected(order)], role="operator", email="nobody@eternal.com")
    assert miss_full["reminders"] == []
    assert miss_proj["reminders"] == []


def test_reminders_stage_owner_role_unchanged_for_non_global():
    assert server.STAGE_OWNER_ROLE[1] == "designer"
    assert server.STAGE_OWNER_ROLE[6] == "screener"
    assert server.STAGE_OWNER_ROLE[7] == "helper"
    assert server.STAGE_OWNER_ROLE[8] == "operator"
    order = _full_order(id="st1", current_stage=1, deadline=_now_iso(-2))
    designer_out, _ = _run_reminders([_projected(order)], role="designer")
    helper_out, _ = _run_reminders([_projected(order)], role="helper")
    assert designer_out["reminders"][0]["owner_role"] == "designer"
    assert helper_out["reminders"] == []


def test_deadline_status_unchanged_on_full_and_projected():
    deadline = _now_iso(-12)
    full = _full_order(deadline=deadline, current_stage=3)
    proj = _projected(full)
    assert server._deadline_status(full.get("deadline"), full.get("current_stage", 1)) == server._deadline_status(
        proj.get("deadline"), proj.get("current_stage", 1)
    )
    ds = server._deadline_status(deadline, 3)
    assert ds["bucket"] == "overdue"
    assert ds["hours_left"] < 0


def test_synthetic_projected_order_is_smaller_than_full_source():
    full = _full_order()
    projected = _projected(full)
    full_bytes = len(json.dumps(full, default=str).encode("utf-8"))
    proj_bytes = len(json.dumps(projected, default=str).encode("utf-8"))
    assert proj_bytes < full_bytes
    ratio = proj_bytes / full_bytes
    # SYNTHETIC ONLY — recorded for the preview report, not production savings.
    print(f"SYNTHETIC ONLY full={full_bytes} projected={proj_bytes} ratio={ratio:.4f}")
    assert ratio < 0.5
