"""Local isolated checks for the operational PWA performance patches.

Measurements are localhost Mongo only. They are not production or phone timings.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pymongo import monitoring

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

from services.division_tasks import (  # noqa: E402
    TASK_PROJECTION,
    build_division_tasks,
    division_tasks_read_pipeline,
    project_order_for_task_list,
)
from services.operational_order_view import operational_public_order  # noqa: E402
from quantity_rules import validate_packing_lines, packing_lines_stored  # noqa: E402

pytestmark = pytest.mark.anyio
DB_NAME = "pwa_ops_perf_isolated"
WIB = timezone(timedelta(hours=7))
BLOB = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.4\n" + (b"A" * 4000)).decode("ascii")
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
PNG_URL = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _today():
    return datetime.now(WIB).date().isoformat()


def _shift(days: int) -> str:
    return (datetime.now(WIB).date() + timedelta(days=days)).isoformat()


def _line(idx, name, qty, color, process="print"):
    return {
        "id": f"L{idx}",
        "name": name,
        "quantity": qty,
        "color": color,
        "production_process": process,
        "screen_size": "large" if idx == 0 else "small",
    }


def _split(sid, idx, email, name, qty, done, work_date, screen="large"):
    return {
        "id": sid,
        "line_idx": idx,
        "line_id": f"L{idx}",
        "operator_email": email,
        "operator_user_id": email,
        "operator_name": name,
        "quantity": qty,
        "qty_completed": done,
        "work_date": work_date,
        "screen_size": screen,
        "progress_entries": [{
            "qty": done,
            "photo": "data:image/png;base64," + ("Q" * 2000),
            "data_url": "data:image/png;base64," + ("R" * 2000),
        }],
    }


def _orders():
    today = _today()
    yesterday = _shift(-2)
    tomorrow = _shift(3)
    common = {
        "customer": "Cust Perf",
        "customer_name": "Cust Perf",
        "address": "Jl. Melati 9",
        "customer_address": "Jl. Melati 9",
        "cancelled": False,
        "hold": False,
        "current_stage": 11,
        "delivery_status": "Belum Terkirim",
        "buffer_stock": False,
        "sample_approved": True,
        "sample_files": [{"id": "sample-1", "name": "sample.png", "data_url": PNG_URL}],
        "design_files": [
            {"id": "design-id", "name": "by-id.pdf", "content_type": "application/pdf", "data_url": BLOB},
            {"name": "legacy-inline.pdf", "content_type": "application/pdf", "data_url": BLOB},
        ],
        "payment_files": [],
        "design_status": "Approved",
        "payment_status": "Approved",
        "screener_info": {"screen_qty": 1000, "entries": [{"qty": 1000, "cancelled": False}]},
        "helper_prep": {"qty_prepared": 1000, "items": [{"line_idx": 0, "qty_prepared": 1000}]},
        "packing_info": {"qty_packed": 800, "lines": [{"line_idx": 0, "packed_qty": 800}]},
        "shipments": [{
            "id": "sh-partial",
            "number": "SJ-PARTIAL",
            "lines": [{"line_idx": 0, "qty": 500}],
        }],
        "loading_confirmations": [{
            "id": "ld-1",
            "surat_jalan_id": "sj-legacy",
            "shipment_id": "sh-partial",
            "lines": [{"line_idx": 0, "qty": 400}],
        }],
        "delivery_proofs": [{"id": "proof-1"}, {"id": "proof-2"}],
        "qty_delivered": 200,
        "total_quantity": 1000,
    }
    rows = []
    specs = [
        ("today-done", "PO-TODAY", today, "op@x.com", "Op", 1000, 1000, False),
        ("prior-open", "PO-PRIOR", yesterday, "op@x.com", "Op", 1000, 200, False),
        ("cancelled", "PO-BATAL", today, "op@x.com", "Op", 1000, 0, True),
        ("future", "PO-FUTURE", tomorrow, "op@x.com", "Op", 1000, 0, False),
        ("other-op", "PO-OTHER", today, "other@x.com", "Other", 1000, 100, False),
    ]
    for oid, po, work_date, email, name, qty, done, cancelled in specs:
        order = copy.deepcopy(common)
        order.update({
            "id": oid,
            "po_number": po,
            "cancelled": cancelled,
            "work_date": work_date,
            "line_items": [_line(0, "Cup Merah", qty, "merah")],
            "operator_splits": [_split("s-" + oid, 0, email, name, qty, done, work_date)],
            "surat_jalan_files": [{
                "id": "sj-" + oid,
                "name": "",
                "data_url": BLOB,
            }],
        })
        if oid == "prior-open":
            order["screener_info"] = {"screen_qty": 200, "entries": [{"qty": 200, "cancelled": False}]}
            order["helper_prep"] = {"qty_prepared": 200, "items": [{"line_idx": 0, "qty_prepared": 200}]}
        rows.append(order)
    multi = copy.deepcopy(common)
    multi.update({
        "id": "multi",
        "po_number": "PO-MULTI",
        "work_date": today,
        "total_quantity": 1500,
        "line_items": [
            _line(0, "Cup Merah", 1000, "merah"),
            _line(1, "Cup Biru", 500, "biru"),
        ],
        "operator_splits": [
            _split("s-multi-a", 0, "op@x.com", "Op", 1000, 400, today, "large"),
            _split("s-multi-b", 1, "op@x.com", "Op", 500, 100, today, "small"),
        ],
        "helper_prep": {
            "qty_prepared": 600,
            "items": [
                {"line_idx": 0, "qty_prepared": 400},
                {"line_idx": 1, "qty_prepared": 200},
            ],
        },
        "surat_jalan_files": [{"id": "sj-multi", "data_url": BLOB}],
    })
    partial = copy.deepcopy(common)
    partial.update({
        "id": "partial",
        "po_number": "PO-PARTIAL",
        "work_date": today,
        "qty_delivered": 200,
        "delivery_status": "Sebagian Terkirim",
        "line_items": [_line(0, "Cup Kirim", 1000, "hijau")],
        "operator_splits": [_split("s-partial", 0, "op@x.com", "Op", 1000, 1000, today)],
        "packing_info": {"qty_packed": 1000, "lines": [{"line_idx": 0, "packed_qty": 1000}]},
        "surat_jalan_files": [{
            "id": "sj-legacy",
            "data_url": BLOB,
        }],
    })
    rows.extend([multi, partial])
    return rows


def _task_identity(rows):
    return [
        (
            row.get("task_id"),
            row.get("order_id"),
            row.get("order_number"),
            row.get("target_qty"),
            row.get("completed_qty"),
            row.get("remaining_qty"),
            row.get("work_date"),
            row.get("status"),
            row.get("status_label"),
            row.get("item"),
            row.get("screen_size"),
            row.get("assigned_user_email"),
            row.get("photo_count"),
            row.get("sj_file_count"),
            row.get("shipment_id"),
        )
        for row in rows
    ]


def _compare_division(orders, division, user):
    full = build_division_tasks(orders, division, user, mine_only=True)
    projected = build_division_tasks(
        [project_order_for_task_list(order) for order in orders],
        division,
        user,
        mine_only=True,
    )
    assert [row.get("task_id") for row in projected] == [row.get("task_id") for row in full]
    assert _task_identity(projected) == _task_identity(full)
    for left, right in zip(full, projected):
        assert left == right
    blob = json.dumps(projected)
    assert "data:application/pdf" not in blob
    assert "_list_inline_present" not in blob
    assert "base64" not in blob
    return full


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _CommandProbe(monitoring.CommandListener):
    def __init__(self):
        self.events = []

    def started(self, event):
        self.events.append(event)

    def succeeded(self, event):
        return None

    def failed(self, event):
        return None


def _unregister(listener):
    listeners = monitoring._LISTENERS.command_listeners
    if listener in listeners:
        listeners.remove(listener)


def _listening_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    probe = _CommandProbe()
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], event_listeners=[probe])
    return client, client[DB_NAME], probe


def test_projected_tasks_match_full_documents_for_four_roles():
    orders = _orders()
    users = {
        "screener": {"id": "u-screen", "email": "screen@x.com", "role": "screener"},
        "helper": {"id": "u-helper", "email": "helper@x.com", "role": "helper"},
        "operator": {"id": "u-op", "email": "op@x.com", "role": "operator"},
        "terkirim": {"id": "u-driver", "email": "driver@x.com", "role": "driver"},
    }
    screener = _compare_division(orders, "screener", users["screener"])
    helper = _compare_division(orders, "helper", users["helper"])
    operator = _compare_division(orders, "operator", users["operator"])
    delivery = _compare_division(orders, "terkirim", users["terkirim"])
    numbers = {row["order_number"] for row in screener}
    assert "PO-TODAY" in numbers
    assert "PO-PRIOR" in numbers
    assert "PO-FUTURE" in numbers
    assert "PO-OTHER" in numbers
    assert "PO-MULTI" in numbers
    assert "PO-PARTIAL" in numbers
    assert "PO-BATAL" not in numbers
    assert any(row["work_date"] == _shift(-2) and row["status"] != "selesai" for row in helper)
    assert any(row["work_date"] == _today() and row["status"] == "selesai" for row in operator)
    assert all(row["assigned_user_email"] == "op@x.com" for row in operator)
    multi_rows = [row for row in helper if row["order_number"] == "PO-MULTI"]
    assert [row["item"] for row in multi_rows] == ["Cup Merah", "Cup Biru"]
    assert [row["screen_size"] for row in multi_rows] == ["large", "small"]
    assert any(row["order_number"] == "PO-PARTIAL" for row in delivery)
    legacy = project_order_for_task_list(next(o for o in orders if o["id"] == "partial"))
    sj = legacy["surat_jalan_files"][0]
    assert sj["_list_inline_present"] is True
    assert "data_url" not in sj
    assert sj.get("storage_path") != "1"
    assert not str(sj.get("storage_path") or "").startswith("data:")


def test_packing_monotonic_guard_still_rejects_hidden_drop():
    order = {
        "line_items": [
            {"name": "A", "quantity": 2000, "production_process": "print"},
            {"name": "B", "quantity": 2000, "production_process": "print"},
        ],
        "total_quantity": 4000,
        "operator_splits": [
            {"line_idx": 0, "quantity": 2000, "qty_completed": 2000},
            {"line_idx": 1, "quantity": 2000, "qty_completed": 2000},
        ],
        "helper_prep": {"qty_prepared": 4000, "items": [
            {"line_idx": 0, "qty_prepared": 2000},
            {"line_idx": 1, "qty_prepared": 2000},
        ]},
        "packing_info": {
            "qty_packed": 2000,
            "lines": [{"line_idx": 0, "packed_qty": 2000}, {"line_idx": 1, "packed_qty": 0}],
        },
        "current_stage": 13,
        "qty_delivered": 4000,
    }
    snapshot = copy.deepcopy(order)
    err = validate_packing_lines(
        [{"line_idx": 0, "packed_qty": 1600}, {"line_idx": 1, "packed_qty": 500}],
        order,
    )
    assert err
    assert "tidak boleh lebih kecil" in err
    assert order == snapshot
    assert packing_lines_stored(order) is not None


def test_list_inline_present_is_not_attachment_evidence():
    import inspect

    from quantity_rules import (
        has_surat_jalan_file,
        surat_jalan_created,
        surat_jalan_status_semantic,
        validate_packing_lines as packing_guard,
    )
    from services.division_tasks import _list_has_surat_jalan_file, build_loading_tasks

    sentinel = {"surat_jalan_files": [{"id": "client-sj", "_list_inline_present": True}]}
    assert has_surat_jalan_file(sentinel) is False
    assert surat_jalan_created(sentinel) is False
    assert surat_jalan_status_semantic(sentinel) == "pending"
    assert "_list_inline_present" not in inspect.getsource(packing_guard)
    real = {"surat_jalan_files": [{"id": "sj-real", "name": "sj.pdf"}]}
    assert has_surat_jalan_file(real) is True
    deleted = {"surat_jalan_files": [{
        "id": "sj-gone",
        "_list_inline_present": True,
        "is_deleted": True,
    }]}
    assert _list_has_surat_jalan_file(deleted) is False
    projected = {
        "id": "po-list",
        "po_number": "PO-LIST",
        "customer": "Cust",
        "line_items": [{"name": "Cup", "quantity": 10, "production_process": "print"}],
        "surat_jalan_files": [{"id": "sj-legacy", "_list_inline_present": True}],
    }
    assert has_surat_jalan_file(projected) is False
    assert _list_has_surat_jalan_file(projected) is True
    tasks = build_loading_tasks(projected)
    assert len(tasks) == 1
    assert tasks[0]["order_number"] == "PO-LIST"


def test_operational_copy_keeps_legacy_inline_and_drops_only_opened_bytes():
    stored = {
        "id": "po-open",
        "address": "Jl. Melati 9",
        "qty_delivered": 3,
        "line_items": [{"id": "L0", "name": "Cup", "quantity": 10}],
        "design_files": [
            {"id": "d-id", "name": "by-id.pdf", "content_type": "application/pdf", "data_url": BLOB},
            {"name": "legacy.pdf", "content_type": "application/pdf", "data_url": BLOB},
            {"id": "d-store", "name": "stored.pdf", "storage_path": "eternal-packaging/design/stored.pdf", "data_url": BLOB},
        ],
        "sample_files": [{"name": "only-inline.png", "data_url": PNG_URL}],
        "surat_jalan_files": [
            {"id": "sj-1", "name": "sj.pdf", "data_url": BLOB, "lines": [{"line_idx": 0, "qty": 4}]},
            {"name": "legacy-sj.pdf", "data_url": BLOB},
        ],
        "delivery_proofs": [{"id": "p1", "storage_path": "eternal-packaging/proof/p1.jpg", "data_url": PNG_URL}],
        "line_product_replace": [{"line_idx": 0}],
        "sales_delivery_summary": {"rows": [1]},
    }
    original = copy.deepcopy(stored)
    public = operational_public_order(stored)
    assert stored == original
    assert "line_product_replace" not in public
    assert "sales_delivery_summary" not in public
    by_name = {item.get("name"): item for item in public["design_files"]}
    assert "data_url" not in by_name["by-id.pdf"]
    assert by_name["legacy.pdf"]["data_url"].startswith("data:")
    assert "data_url" not in by_name["stored.pdf"]
    assert by_name["stored.pdf"]["storage_path"] == "eternal-packaging/design/stored.pdf"
    assert public["sample_files"][0]["data_url"].startswith("data:")
    sj_by_name = {item.get("name"): item for item in public["surat_jalan_files"]}
    assert "data_url" not in sj_by_name["sj.pdf"]
    assert sj_by_name["legacy-sj.pdf"]["data_url"].startswith("data:")
    assert "data_url" not in public["delivery_proofs"][0]
    assert public["address"] == "Jl. Melati 9"
    assert public["qty_delivered"] == 3
    assert public["line_items"][0]["quantity"] == 10


async def _bind_db():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[DB_NAME]
    await client.drop_database(DB_NAME)
    server.client = client
    server.db = db
    return server, client, db


async def test_mongo_projection_matches_identity_and_does_not_write_sentinel():
    server, client, db = await _bind_db()
    orders = _orders()
    await db.orders.insert_many(copy.deepcopy(orders))
    query = {"cancelled": {"$ne": True}}
    user = {"id": "u-op", "email": "op@x.com", "role": "operator"}
    old_client, old_db, old_probe = _listening_db()
    try:
        started = time.perf_counter()
        old_docs = await old_db.orders.find(query, TASK_PROJECTION).to_list(1000)
        old_built = {}
        for division in ("screener", "helper", "operator", "terkirim"):
            old_built[division] = build_division_tasks(copy.deepcopy(old_docs), division, user, mine_only=True)
        old_ready_s = time.perf_counter() - started
    finally:
        old_client.close()
    new_client, new_db, new_probe = _listening_db()
    try:
        started = time.perf_counter()
        new_docs = await new_db.orders.aggregate(division_tasks_read_pipeline(query)).to_list(1000)
        new_built = {}
        for division in ("screener", "helper", "operator", "terkirim"):
            new_built[division] = build_division_tasks(copy.deepcopy(new_docs), division, user, mine_only=True)
        new_ready_s = time.perf_counter() - started
    finally:
        new_client.close()
    for division in ("screener", "helper", "operator", "terkirim"):
        old_rows = old_built[division]
        new_rows = new_built[division]
        assert [row["task_id"] for row in new_rows] == [row["task_id"] for row in old_rows]
        assert _task_identity(new_rows) == _task_identity(old_rows)
    old_query_s = old_ready_s
    new_query_s = new_ready_s
    stored = await db.orders.find_one({"id": "partial"}, {"_id": 0, "surat_jalan_files": 1})
    assert stored["surat_jalan_files"][0]["data_url"].startswith("data:")
    assert "_list_inline_present" not in stored["surat_jalan_files"][0]
    projected = next(doc for doc in new_docs if doc["id"] == "partial")
    assert projected["surat_jalan_files"][0]["_list_inline_present"] is True
    assert "data_url" not in projected["surat_jalan_files"][0]
    old_bytes = len(json.dumps(old_docs, default=str).encode("utf-8"))
    new_bytes = len(json.dumps(new_docs, default=str).encode("utf-8"))
    old_task_bytes = len(json.dumps(old_built, default=str).encode("utf-8"))
    new_task_bytes = len(json.dumps(new_built, default=str).encode("utf-8"))
    assert new_bytes < old_bytes

    def _names(probe):
        return [event.command_name for event in probe.events]

    artifact = {
        "scope": "localhost isolated mongo, not production, not a phone",
        "comparison": "same fixture; old path is find(TASK_PROJECTION) reconstructed in-process; new path is the aggregate pipeline",
        "old_list_ready_seconds": round(old_ready_s, 4),
        "new_list_ready_seconds": round(new_ready_s, 4),
        "old_query_seconds": round(old_query_s, 4),
        "new_query_seconds": round(new_query_s, 4),
        "old_projected_json_bytes": old_bytes,
        "new_projected_json_bytes": new_bytes,
        "old_task_json_bytes": old_task_bytes,
        "new_task_json_bytes": new_task_bytes,
        "old_commands": _names(old_probe),
        "new_commands": _names(new_probe),
        "document_count": len(new_docs),
        "list_cap_1000_unchanged": True,
    }
    out = Path("/opt/cursor/artifacts")
    out.mkdir(parents=True, exist_ok=True)
    (out / "pwa_ops_perf_measure.json").write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    client.close()


async def test_unread_summary_skips_message_queries_and_matches_sequence_math():
    _server, client, db = await _bind_db()
    from services.chat_constants import COLL_MEMBERS, COLL_MESSAGES, COLL_ROOMS
    from services.chat_rooms import list_my_rooms, unread_summary
    uid = "u-chat"
    await db[COLL_ROOMS].insert_many([
        {"id": "room-a", "last_sequence": 5, "last_activity_at": "2026-10-03T01:00:00Z", "type": "work"},
        {"id": "room-empty", "last_activity_at": "2026-10-03T02:00:00Z", "type": "work"},
        {"id": "room-ahead", "last_sequence": 2, "last_activity_at": "2026-10-03T03:00:00Z", "type": "work"},
        {"id": "room-secret", "last_sequence": 9, "last_activity_at": "2026-10-03T04:00:00Z", "type": "work"},
        {"id": "room-left", "last_sequence": 4, "last_activity_at": "2026-10-03T05:00:00Z", "type": "work"},
    ])
    await db[COLL_MEMBERS].insert_many([
        {"room_id": "room-a", "user_id": uid, "active": True, "last_read_sequence": 2},
        {"room_id": "room-empty", "user_id": uid, "active": True},
        {"room_id": "room-ahead", "user_id": uid, "active": True, "last_read_sequence": 8},
        {"room_id": "room-left", "user_id": uid, "active": False, "last_read_sequence": 0},
    ])
    await db[COLL_MESSAGES].insert_one({
        "room_id": "room-a", "sequence": 5, "content": "halo", "sender_user_id": "other",
    })
    listen_client, listen_db, probe = _listening_db()
    try:
        summary = await unread_summary(listen_db, {"id": uid})
    finally:
        listen_client.close()
    message_finds = [
        event for event in probe.events
        if event.command_name == "find" and event.command.get("find") == COLL_MESSAGES
    ]
    assert probe.events
    assert message_finds == []
    assert summary == {
        "total_unread": 3,
        "rooms": [{"room_id": "room-a", "unread": 3}],
    }
    listed_ids = {item["room_id"] for item in summary["rooms"]}
    assert "room-secret" not in listed_ids
    assert "room-left" not in listed_ids
    assert "room-ahead" not in listed_ids
    assert "room-empty" not in listed_ids
    preview_client, preview_db, preview_probe = _listening_db()
    try:
        rooms = await list_my_rooms(preview_db, {"id": uid})
    finally:
        preview_client.close()
    preview_message_finds = [
        event for event in preview_probe.events
        if event.command_name == "find" and event.command.get("find") == COLL_MESSAGES
    ]
    assert preview_message_finds, [
        (event.command_name, dict(event.command)) for event in preview_probe.events
    ]
    by_room = {room["id"]: room for room in rooms}
    assert set(by_room) == {"room-a", "room-empty", "room-ahead"}
    assert by_room["room-a"]["last_message"]["content"] == "halo"
    assert by_room["room-a"]["unread_count"] == 3
    assert by_room["room-empty"]["unread_count"] == 0
    assert by_room["room-ahead"]["unread_count"] == 0
    assert "room-secret" not in by_room
    assert "room-left" not in by_room
    client.close()


async def test_operational_get_desktop_get_and_attachment_open_paths():
    server, client, db = await _bind_db()
    from services import storage as obj_storage
    app = server.app
    stored_path = obj_storage.put_object(
        "eternal-packaging/design/perf-stored.pdf",
        PDF_BYTES,
        "application/pdf",
    )["path"]
    proof_path = obj_storage.put_object(
        "eternal-packaging/proof/perf-proof.png",
        PNG_BYTES,
        "image/png",
    )["path"]
    order = _orders()[-1]
    order["id"] = "po-open"
    order["design_files"] = [
        {"id": "design-id", "name": "by-id.pdf", "content_type": "application/pdf", "data_url": "data:application/pdf;base64," + base64.b64encode(PDF_BYTES).decode("ascii")},
        {"name": "legacy.pdf", "content_type": "application/pdf", "data_url": "data:application/pdf;base64," + base64.b64encode(PDF_BYTES).decode("ascii")},
        {"id": "design-store", "name": "stored.pdf", "content_type": "application/pdf", "storage_path": stored_path, "data_url": BLOB},
    ]
    order["delivery_proofs"] = [{"id": "proof-1", "storage_path": proof_path, "data_url": PNG_URL}, {"id": "proof-2"}]
    order["surat_jalan_files"] = [{
        "id": "sj-open",
        "name": "sj.pdf",
        "content_type": "application/pdf",
        "data_url": "data:application/pdf;base64," + base64.b64encode(PDF_BYTES).decode("ascii"),
        "lines": [{"line_idx": 0, "qty": 4}],
    }]
    order["address"] = "Jl. Melati 9"
    await db.orders.insert_one(copy.deepcopy(order))
    await db.files.insert_one({
        "id": "file-store",
        "storage_path": stored_path,
        "content_type": "application/pdf",
        "kind": "design",
        "order_id": "po-open",
        "is_deleted": False,
    })
    await db.files.insert_one({
        "id": "proof-1",
        "storage_path": proof_path,
        "content_type": "image/png",
        "kind": "delivery_proof",
        "order_id": "po-open",
        "is_deleted": False,
    })
    app.dependency_overrides[server.current_user] = lambda: {
        "id": "admin-1", "user_id": "admin-1", "email": "admin@x.com", "name": "Admin", "role": "admin",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        desktop = await http.get("/api/orders/po-open")
        operational = await http.get("/api/orders/po-open", params={"view": "operational"})
        assert desktop.status_code == 200
        assert operational.status_code == 200
        desk = desktop.json()["order"]
        op = operational.json()["order"]
        assert "sales_delivery_summary" in desk
        assert "line_product_replace" in desk
        assert "sales_delivery_summary" not in op
        assert "line_product_replace" not in op
        assert op["address"] == "Jl. Melati 9"
        assert op["line_items"][0]["quantity"] == order["line_items"][0]["quantity"]
        legacy = next(item for item in op["design_files"] if item.get("name") == "legacy.pdf")
        assert legacy["data_url"].startswith("data:application/pdf")
        identified = next(item for item in op["design_files"] if item.get("id") == "design-id")
        assert "data_url" not in identified
        stored_file = next(item for item in op["design_files"] if item.get("id") == "design-store")
        assert "data_url" not in stored_file
        assert stored_file["storage_path"] == stored_path
        opened = await http.get("/api/orders/po-open/attachments/design/design-id")
        assert opened.status_code == 200
        assert opened.content.startswith(b"%PDF")
        stored_open = await http.get(f"/api/files/{stored_path}")
        assert stored_open.status_code == 200
        assert stored_open.content.startswith(b"%PDF")
        sj = await http.get("/api/orders/po-open/surat-jalan/files/sj-open")
        assert sj.status_code == 200
        assert sj.content.startswith(b"%PDF")
        proof = await http.get(f"/api/files/{proof_path}")
        assert proof.status_code == 200
        assert proof.content.startswith(b"\x89PNG")
        mongo_order = await db.orders.find_one({"id": "po-open"}, {"_id": 0, "design_files": 1})
        assert mongo_order["design_files"][0]["data_url"].startswith("data:")
    app.dependency_overrides.pop(server.current_user, None)
    client.close()


async def test_operational_save_keeps_document_and_list_sees_new_qty():
    server, client, db = await _bind_db()
    app = server.app
    order = next(item for item in _orders() if item["id"] == "prior-open")
    order["screener_info"] = {"screen_qty": 0, "entries": []}
    await db.orders.insert_one(copy.deepcopy(order))
    user = {
        "id": "u-screen", "user_id": "u-screen", "email": "screen@x.com",
        "name": "Screen", "role": "screener",
    }
    app.dependency_overrides[server.current_user] = lambda: user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        before = await http.get("/api/work/division-tasks", params={"division": "screener", "scope": "mine"})
        assert before.status_code == 200
        before_row = next(row for row in before.json()["tasks"] if row["order_id"] == "prior-open")
        saved = await http.post(
            "/api/orders/prior-open/screener-info",
            json={"qty": 25, "notes": "", "action_id": str(uuid.uuid4())},
            params={"view": "operational"},
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()["order"]
        assert "data:application/pdf;base64," + ("A" * 20) not in json.dumps(body)
        stored = await db.orders.find_one({"id": "prior-open"}, {"_id": 0})
        assert stored["design_files"][0]["data_url"].startswith("data:")
        assert stored["screener_info"]["screen_qty"] == 25
        after = await http.get("/api/work/division-tasks", params={"division": "screener", "scope": "mine"})
        after_row = next(row for row in after.json()["tasks"] if row["order_id"] == "prior-open")
        assert after_row["completed_qty"] == before_row["completed_qty"] + 25
        assert after_row["task_id"] == before_row["task_id"]
    app.dependency_overrides.pop(server.current_user, None)
    await client.drop_database(DB_NAME)
    client.close()


def _smoke_order():
    today = _today()
    order = {
        "id": "smoke-1",
        "po_number": "PO-SMOKE",
        "customer": "Cust Smoke",
        "customer_name": "Cust Smoke",
        "address": "Jl. Melati 9",
        "cancelled": False,
        "hold": False,
        "current_stage": 11,
        "delivery_status": "Belum Terkirim",
        "work_date": today,
        "design_status": "Approved",
        "payment_status": "Approved",
        "sample_approved": True,
        "sample_files": [{"id": "sample-smoke", "name": "sample.png", "data_url": PNG_URL}],
        "design_files": [
            {
                "id": "design-smoke",
                "name": "by-id.pdf",
                "content_type": "application/pdf",
                "data_url": "data:application/pdf;base64," + base64.b64encode(PDF_BYTES).decode("ascii"),
            },
            {
                "name": "legacy.pdf",
                "content_type": "application/pdf",
                "data_url": "data:application/pdf;base64," + base64.b64encode(PDF_BYTES).decode("ascii"),
            },
        ],
        "line_items": [_line(0, "Cup Smoke", 100, "merah")],
        "total_quantity": 100,
        "operator_splits": [_split("s-smoke", 0, "op@x.com", "Op", 100, 10, today)],
        "screener_info": {"screen_qty": 10, "entries": []},
        "helper_prep": {"qty_prepared": 10, "items": [{"line_idx": 0, "qty_prepared": 10}]},
        "packing_info": {"qty_packed": 10, "lines": [{"line_idx": 0, "packed_qty": 10}]},
        "shipments": [{
            "id": "sh-smoke",
            "number": "SJ-SMOKE",
            "delivery_date": today,
            "lines": [{"line_idx": 0, "qty": 10, "name": "Cup Smoke"}],
        }],
        "loading_confirmations": [{
            "id": "ld-smoke",
            "shipment_id": "sh-smoke",
            "surat_jalan_id": "sh-smoke",
            "lines": [{"line_idx": 0, "qty": 10}],
        }],
        "surat_jalan_files": [{
            "id": "sj-smoke",
            "name": "sj.pdf",
            "content_type": "application/pdf",
            "data_url": "data:application/pdf;base64," + base64.b64encode(PDF_BYTES).decode("ascii"),
            "lines": [{"line_idx": 0, "qty": 10}],
        }],
        "delivery_proofs": [],
    }
    return order


async def test_four_role_smoke_open_save_return_and_open_attachment():
    server, client, db = await _bind_db()
    app = server.app
    await db.orders.insert_one(_smoke_order())
    holder = {}
    app.dependency_overrides[server.current_user] = lambda: holder["user"]
    roles = {
        "screener": {"id": "u-screen", "user_id": "u-screen", "email": "screen@x.com", "name": "Screen", "role": "screener"},
        "helper": {"id": "u-helper", "user_id": "u-helper", "email": "helper@x.com", "name": "Helper", "role": "helper"},
        "operator": {"id": "u-op", "user_id": "u-op", "email": "op@x.com", "name": "Op", "role": "operator"},
        "driver": {"id": "u-driver", "user_id": "u-driver", "email": "driver@x.com", "name": "Driver", "role": "driver"},
    }
    http_times = {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        for role, user in roles.items():
            holder["user"] = user
            division = "terkirim" if role == "driver" else role
            started = time.perf_counter()
            listed = await http.get("/api/work/division-tasks", params={"division": division, "scope": "mine"})
            http_times[role] = round(time.perf_counter() - started, 4)
            assert listed.status_code == 200, listed.text
            row = next(item for item in listed.json()["tasks"] if item["order_id"] == "smoke-1")
            assert row["order_number"] == "PO-SMOKE"
            detail = await http.get("/api/orders/smoke-1", params={"view": "operational"})
            assert detail.status_code == 200, detail.text
            body = detail.json()["order"]
            assert body["address"] == "Jl. Melati 9"
            assert body["line_items"][0]["quantity"] == 100
            assert "sales_delivery_summary" not in body
            legacy = next(item for item in body["design_files"] if item.get("name") == "legacy.pdf")
            assert legacy["data_url"].startswith("data:application/pdf")

        holder["user"] = roles["operator"]
        opened = await http.get("/api/orders/smoke-1/attachments/design/design-smoke")
        assert opened.status_code == 200
        assert opened.content.startswith(b"%PDF")
        holder["user"] = roles["driver"]
        sj = await http.get("/api/orders/smoke-1/surat-jalan/files/sj-smoke")
        assert sj.status_code == 200, sj.text
        assert sj.content.startswith(b"%PDF")
        schedule = await http.get("/api/delivery/schedule")
        assert schedule.status_code == 200, schedule.text
        sched = next(row for row in schedule.json()["rows"] if row["id"] == "smoke-1")
        assert sched["sj_label"] == "SJ-SMOKE"
        assert sched["qty_sj"] == 10
        assert sched["qty_loaded"] == 10
        assert sched["qty_delivered_sj"] == 0

        holder["user"] = roles["screener"]
        before = await http.get("/api/work/division-tasks", params={"division": "screener", "scope": "mine"})
        before_qty = next(row for row in before.json()["tasks"] if row["order_id"] == "smoke-1")["completed_qty"]
        saved = await http.post(
            "/api/orders/smoke-1/screener-info",
            json={"qty": 5, "notes": "", "action_id": str(uuid.uuid4())},
            params={"view": "operational"},
        )
        assert saved.status_code == 200, saved.text
        saved_order = saved.json()["order"]
        saved_by_name = {item.get("name"): item for item in saved_order["design_files"]}
        assert "data_url" not in saved_by_name["by-id.pdf"]
        assert saved_by_name["legacy.pdf"]["data_url"].startswith("data:application/pdf")
        after = await http.get("/api/work/division-tasks", params={"division": "screener", "scope": "mine"})
        after_qty = next(row for row in after.json()["tasks"] if row["order_id"] == "smoke-1")["completed_qty"]
        assert after_qty == before_qty + 5

        holder["user"] = roles["helper"]
        before = await http.get("/api/work/division-tasks", params={"division": "helper", "scope": "mine"})
        before_qty = next(row for row in before.json()["tasks"] if row["order_id"] == "smoke-1")["completed_qty"]
        saved = await http.post(
            "/api/orders/smoke-1/helper-prep/items",
            json={"items": [{"line_idx": 0, "qty": 5, "action_id": str(uuid.uuid4())}], "notes": ""},
            params={"view": "operational"},
        )
        assert saved.status_code == 200, saved.text
        after = await http.get("/api/work/division-tasks", params={"division": "helper", "scope": "mine"})
        after_qty = next(row for row in after.json()["tasks"] if row["order_id"] == "smoke-1")["completed_qty"]
        assert after_qty == before_qty + 5

        holder["user"] = roles["operator"]
        before = await http.get("/api/work/division-tasks", params={"division": "operator", "scope": "mine"})
        before_qty = next(row for row in before.json()["tasks"] if row["order_id"] == "smoke-1")["completed_qty"]
        saved = await http.post(
            "/api/orders/smoke-1/operator-progress",
            json={"operator_id": "s-smoke", "qty": 5, "action_id": str(uuid.uuid4())},
            params={"view": "operational"},
        )
        assert saved.status_code == 200, saved.text
        after = await http.get("/api/work/division-tasks", params={"division": "operator", "scope": "mine"})
        after_qty = next(row for row in after.json()["tasks"] if row["order_id"] == "smoke-1")["completed_qty"]
        assert after_qty == before_qty + 5

        holder["user"] = roles["driver"]
        before = await http.get("/api/work/division-tasks", params={"division": "terkirim", "scope": "mine"})
        before_photos = next(row for row in before.json()["tasks"] if row["order_id"] == "smoke-1")["photo_count"]
        saved = await http.post(
            "/api/orders/smoke-1/delivery-proof",
            files={"file": ("bukti.png", PNG_BYTES, "image/png")},
            data={"shipment_id": "sh-smoke", "note": "smoke"},
            params={"view": "operational"},
        )
        assert saved.status_code == 200, saved.text
        proof = saved.json()["proof"]
        assert proof.get("storage_path")
        assert "data_url" not in proof
        opened_proof = await http.get("/api/files/" + proof["storage_path"])
        assert opened_proof.status_code == 200
        assert opened_proof.content.startswith(b"\x89PNG")
        after = await http.get("/api/work/division-tasks", params={"division": "terkirim", "scope": "mine"})
        after_photos = next(row for row in after.json()["tasks"] if row["order_id"] == "smoke-1")["photo_count"]
        assert after_photos == before_photos + 1
        stored = await db.orders.find_one({"id": "smoke-1"}, {"_id": 0, "design_files": 1, "address": 1})
        assert stored["address"] == "Jl. Melati 9"
        assert stored["design_files"][1]["data_url"].startswith("data:")

    measure_path = Path("/opt/cursor/artifacts/pwa_ops_perf_measure.json")
    if measure_path.exists():
        artifact = json.loads(measure_path.read_text(encoding="utf-8"))
    else:
        artifact = {}
    artifact["http_list_ready_seconds_localhost"] = http_times
    artifact["phone_production_measured"] = False
    measure_path.parent.mkdir(parents=True, exist_ok=True)
    measure_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    app.dependency_overrides.pop(server.current_user, None)
    await client.drop_database(DB_NAME)
    client.close()


@pytest.mark.anyio
async def test_schedule_read_sees_confirmed_sj_immediately():
    """Confirm one of two SJ, then read the schedule the way GET does.

    The handler finds orders on the primary client. There is no schedule cache
    and no secondary read preference on the local Motor client.
    """
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo.read_preferences import ReadPreference
    from httpx import ASGITransport, AsyncClient
    from services.delivery_schedule import SCHEDULE_PROJECTION, schedule_rows

    db_name = "pwa_kirim_schedule_read"
    mongo_client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    assert mongo_client.read_preference == ReadPreference.PRIMARY
    database = mongo_client[db_name]
    await mongo_client.drop_database(db_name)
    previous_db = server.db
    previous_client = server.client
    server.client = mongo_client
    server.db = database
    server.app.dependency_overrides[server.current_user] = lambda: {
        "id": "user-admin",
        "name": "Admin Uji",
        "email": "admin@test.local",
        "role": "admin",
        "permissions_override": {},
    }
    order = {
        "id": "po-two-sj",
        "po_number": "PO-TWO",
        "customer_name": "Renea",
        "cancelled": False,
        "current_stage": 13,
        "updated_at": "2026-10-03T00:00:00+00:00",
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "line_items": [
            {"name": "Cup", "quantity": 800, "line_id": "L0", "production_process": "print"},
        ],
        "shipments": [
            {
                "id": "sj-1",
                "number": "SJ-1",
                "delivery_date": "2026-10-03",
                "lines": [{"line_idx": 0, "line_id": "L0", "qty": 400, "name": "Cup"}],
            },
            {
                "id": "sj-2",
                "number": "SJ-2",
                "delivery_date": "2026-10-04",
                "lines": [{"line_idx": 0, "line_id": "L0", "qty": 400, "name": "Cup"}],
            },
        ],
        "loading_confirmations": [
            {"id": "ld-1", "shipment_id": "sj-1", "surat_jalan_id": "sj-1", "lines": [{"line_idx": 0, "qty": 400}]},
            {"id": "ld-2", "shipment_id": "sj-2", "surat_jalan_id": "sj-2", "lines": [{"line_idx": 0, "qty": 400}]},
        ],
        "delivery_proofs": [
            {"id": "p1", "shipment_id": "sj-1"},
            {"id": "p2", "shipment_id": "sj-1"},
            {"id": "p3", "shipment_id": "sj-2"},
            {"id": "p4", "shipment_id": "sj-2"},
        ],
        "delivery_recipient_signature": {
            "id": "sig-sj-1",
            "shipment_id": "sj-1",
            "recipient_name": "Penerima Uji",
            "signature_storage_path": "sig/sj-1-recipient.png",
        },
        "delivery_driver_signature": {
            "id": "dsig-sj-1",
            "shipment_id": "sj-1",
            "driver_name": "Driver Uji",
            "signature_storage_path": "sig/sj-1-driver.png",
            "driver_signature_storage_path": "sig/sj-1-driver.png",
        },
        "delivery_confirmations": [],
        "delivery_confirm_action_ids": [],
    }
    try:
        await database.orders.insert_one(dict(order))
        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            saved = await http.post("/api/orders/po-two-sj/confirm-delivered", json={
                "action_id": "act-sj-1",
                "shipment_id": "sj-1",
                "recipient_name": "Penerima Uji",
            })
            assert saved.status_code == 200, saved.text
            retry = await http.post("/api/orders/po-two-sj/confirm-delivered", json={
                "action_id": "act-sj-1",
                "shipment_id": "sj-1",
                "recipient_name": "Penerima Uji",
            })
            assert retry.status_code == 200, retry.text
            schedule = await http.get("/api/delivery/schedule")
            assert schedule.status_code == 200, schedule.text
        docs = await database.orders.find(
            {"cancelled": {"$ne": True}},
            SCHEDULE_PROJECTION,
        ).to_list(None)
        built = []
        for doc in docs:
            built.extend(schedule_rows(doc))
        by_sj = {row["shipment_id"]: row for row in built if row.get("id") == "po-two-sj"}
        http_rows = {
            row["shipment_id"]: row
            for row in schedule.json()["rows"]
            if row.get("id") == "po-two-sj"
        }
        assert set(by_sj) == {"sj-1", "sj-2"}
        assert by_sj["sj-1"]["qty_delivered_sj"] == 400
        assert by_sj["sj-2"]["qty_delivered_sj"] == 0
        assert http_rows["sj-1"]["qty_delivered_sj"] == 400
        assert http_rows["sj-2"]["qty_delivered_sj"] == 0
        stored = await database.orders.find_one(
            {"id": "po-two-sj"},
            {"_id": 0, "shipments": 1, "qty_delivered": 1, "delivery_confirmations": 1},
        )
        assert stored["qty_delivered"] == 400
        assert len(stored["delivery_confirmations"]) == 1
        states = {sh["id"]: sh.get("delivery_status") for sh in stored["shipments"]}
        assert states["sj-1"] == "Terkirim"
        assert states.get("sj-2") != "Terkirim"
    finally:
        server.app.dependency_overrides.pop(server.current_user, None)
        server.db = previous_db
        server.client = previous_client
        await mongo_client.drop_database(db_name)
        mongo_client.close()
