from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_internal_chat_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ["STORAGE_PROVIDER"] = "local"

import server
from permissions import ALL_PERMISSIONS
from server import app, current_user
from services import storage as obj_storage
from services.chat_constants import COLL_ATTACHMENTS, COLL_MEMBERS, COLL_MESSAGES, COLL_ROOMS
from services.chat_http import authenticate_ws
from services.chat_indexes import ensure_indexes
from services.chat_membership import bootstrap_system_groups, sync_user_system_membership
from services.chat_realtime import manager

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    obj_storage._reset_provider_for_tests()


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    db = server.db
    await ensure_indexes(db)
    for coll in (COLL_ROOMS, COLL_MEMBERS, COLL_MESSAGES, COLL_ATTACHMENTS, "users", "roles", "role_permissions", "orders", "files"):
        if coll == "files":
            await db[coll].delete_many({"kind": "chat_attachment"})
        else:
            await db[coll].delete_many({})
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()
    for coll in (COLL_ROOMS, COLL_MEMBERS, COLL_MESSAGES, COLL_ATTACHMENTS, "users", "roles", "role_permissions", "orders", "files"):
        if coll == "files":
            await db[coll].delete_many({"kind": "chat_attachment"})
        else:
            await db[coll].delete_many({})


def _user(role="admin", perms=None, **kw):
    uid = kw.get("id") or f"u-{role}-{uuid.uuid4().hex[:8]}"
    override = kw.get("permissions_override")
    if override is None:
        override = {} if perms is None else {p: True for p in perms}
    return {
        "id": uid,
        "user_id": uid,
        "email": kw.get("email") or f"{uid}@example.com",
        "name": kw.get("name") or role.title(),
        "role": role,
        "status": kw.get("status") or "active",
        "permissions_override": override,
    }


async def _insert_user(user):
    doc = dict(user)
    doc["password_hash"] = "x"
    await server.db.users.insert_one(doc)
    return user


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _order(**kw):
    oid = kw.get("id") or f"po-{uuid.uuid4().hex[:8]}"
    return {
        "id": oid,
        "po_number": kw.get("po_number") or "PO-CHAT-001",
        "customer": "Acme",
        "customer_name": "Acme Customer",
        "line_items": kw.get("line_items") or [
            {"id": "L1", "name": "Cup 16oz", "quantity": 10, "production_process": "print", "screen_size": "large"},
            {"id": "L2", "name": "Cup 16oz", "quantity": 7, "production_process": "print", "screen_size": "small"},
        ],
        "operator_splits": kw.get("operator_splits") or [],
        "current_stage": 5,
        "total_quantity": 17,
        "priority": kw.get("priority", "urgent"),
        "deadline": "2026-09-12T00:00:00+00:00",
        "delivery_deadline_effective": "2026-09-11T00:00:00+00:00",
        "delivery_deadline_status": "h1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cancelled": False,
    }


@pytest.mark.anyio
async def test_auth_and_user_picker_requires_chat_use_and_excludes_self():
    me = await _insert_user(_user("operator", permissions_override={"chat.use": False, "nav.chat": False}))
    peer = await _insert_user(_user("helper", perms=["chat.use"], name="Peer Helper"))
    async with await client_for(me) as c:
        denied = await c.get("/api/chat/users")
        assert denied.status_code == 403
    me["permissions_override"] = {"chat.use": True}
    async with await client_for(me) as c:
        ok = await c.get("/api/chat/users", params={"q": "Peer"})
        assert ok.status_code == 200
        ids = [u["id"] for u in ok.json()["users"]]
        assert peer["id"] in ids
        assert me["id"] not in ids


@pytest.mark.anyio
async def test_dm_reuse_concurrent_and_reject_self_inactive_peer():
    a = await _insert_user(_user("operator", perms=["chat.use"]))
    b = await _insert_user(_user("helper", perms=["chat.use"]))
    inactive = await _insert_user(_user("driver", perms=["chat.use"], status="inactive"))
    async with await client_for(a) as c:
        async def make_dm():
            r = await c.post("/api/chat/rooms/dm", json={"peer_user_id": b["id"]})
            assert r.status_code == 201, r.text
            return r.json()["id"]
        ids = await asyncio.gather(make_dm(), make_dm())
        assert ids[0] == ids[1]
        assert await server.db[COLL_ROOMS].count_documents({"type": "dm"}) == 1
        assert (await c.post("/api/chat/rooms/dm", json={"peer_user_id": a["id"]})).status_code == 400
        assert (await c.post("/api/chat/rooms/dm", json={"peer_user_id": inactive["id"]})).status_code == 404


@pytest.mark.anyio
async def test_system_group_sync_tracks_single_role_and_inactive_membership():
    user = await _insert_user(_user("helper", perms=["chat.use"]))
    await server.db.roles.insert_many([{"role": "helper", "label": "Helper"}, {"role": "operator", "label": "Operator"}])
    await bootstrap_system_groups(server.db)
    helper_members = await server.db[COLL_MEMBERS].find({"user_id": user["id"], "active": True}, {"_id": 0}).to_list(10)
    assert len(helper_members) == 1
    helper_room = await server.db[COLL_ROOMS].find_one({"id": helper_members[0]["room_id"]}, {"_id": 0})
    assert helper_room["system_key"] == "role:helper"
    user["role"] = "operator"
    await server.db.users.update_one({"id": user["id"]}, {"$set": {"role": "operator"}})
    await sync_user_system_membership(server.db, user)
    active = await server.db[COLL_MEMBERS].find({"user_id": user["id"], "active": True}, {"_id": 0}).to_list(10)
    assert len(active) == 1
    operator_room = await server.db[COLL_ROOMS].find_one({"id": active[0]["room_id"]}, {"_id": 0})
    assert operator_room["system_key"] == "role:operator"
    user["status"] = "inactive"
    await sync_user_system_membership(server.db, user)
    assert await server.db[COLL_MEMBERS].count_documents({"user_id": user["id"], "active": True}) == 0


@pytest.mark.anyio
async def test_custom_group_permission_member_changes_and_archive():
    admin = await _insert_user(_user("admin"))
    plain = await _insert_user(_user("operator", perms=["chat.use"]))
    peer = await _insert_user(_user("helper", perms=["chat.use"]))
    async with await client_for(plain) as c:
        denied = await c.post("/api/chat/rooms/custom", json={"name": "Ops", "member_ids": [peer["id"]]})
        assert denied.status_code == 403
    async with await client_for(admin) as c:
        created = await c.post("/api/chat/rooms/custom", json={"name": "Ops", "member_ids": [peer["id"]]})
        assert created.status_code == 201, created.text
        room_id = created.json()["id"]
        patched = await c.patch(f"/api/chat/rooms/{room_id}", json={"name": "Ops Baru"})
        assert patched.json()["name"] == "Ops Baru"
        archived = await c.post(f"/api/chat/rooms/{room_id}/archive")
        assert archived.json()["archived"] is True


@pytest.mark.anyio
async def test_work_room_identity_same_name_lines_and_spoofed_snapshot_ignored():
    admin = await _insert_user(_user("admin"))
    order = _order()
    await server.db.orders.insert_one(order)
    async with await client_for(admin) as c:
        r0 = await c.post("/api/chat/rooms/work", json={"order_id": order["id"], "line_idx": 0})
        r1 = await c.post("/api/chat/rooms/work", json={"order_id": order["id"], "line_idx": 1})
        assert r0.status_code == r1.status_code == 201
        assert r0.json()["id"] != r1.json()["id"]
        assert r0.json()["work_context"]["item_name"] == r1.json()["work_context"]["item_name"] == "Cup 16oz"
        assert r0.json()["work_context"]["line_idx"] == 0
        assert r1.json()["work_context"]["line_idx"] == 1
        msg = await c.post(f"/api/chat/rooms/{r0.json()['id']}/messages", json={
            "content": "cek",
            "work_refs": [{"order_id": order["id"], "line_idx": 0, "po_number": "SPOOF", "customer_name": "Mallory"}],
        })
        assert msg.status_code == 201, msg.text
        snap = msg.json()["work_refs"][0]
        assert snap["po_number"] == order["po_number"]
        assert snap["customer_name"] == "Acme Customer"


@pytest.mark.anyio
async def test_message_idempotency_sequence_edit_soft_delete_unread_and_mentions():
    a = await _insert_user(_user("admin"))
    b = await _insert_user(_user("helper", perms=["chat.use"]))
    async with await client_for(a) as c:
        room = (await c.post("/api/chat/rooms/dm", json={"peer_user_id": b["id"]})).json()
        first = await c.post(f"/api/chat/rooms/{room['id']}/messages", json={"content": "halo", "client_message_id": "c1"})
        again = await c.post(f"/api/chat/rooms/{room['id']}/messages", json={"content": "beda", "client_message_id": "c1"})
        assert first.json()["id"] == again.json()["id"]
        assert first.json()["sequence"] == 1
        edited = await c.patch(f"/api/chat/messages/{first.json()['id']}", json={"content": "halo edit"})
        assert edited.status_code == 200
        assert edited.json()["edited_at"]
        await server.db[COLL_MESSAGES].update_one({"id": first.json()["id"]}, {"$set": {"created_at": (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()}})
        late = await c.patch(f"/api/chat/messages/{first.json()['id']}", json={"content": "late"})
        assert late.status_code == 400
        deleted = await c.delete(f"/api/chat/messages/{first.json()['id']}")
        assert deleted.json()["deleted"] is True
        listed = await c.get(f"/api/chat/rooms/{room['id']}/messages")
        assert listed.json()["messages"][0]["content"] is None
    async with await client_for(b) as c2:
        rooms = await c2.get("/api/chat/rooms")
        assert rooms.json()["rooms"][0]["unread_count"] == 1
        bad_mention = await c2.post(f"/api/chat/rooms/{room['id']}/messages", json={"content": "x", "mentions": [{"type": "user", "id": "not-member"}]})
        assert bad_mention.status_code == 400
        read = await c2.post(f"/api/chat/rooms/{room['id']}/read", json={"sequence": 1})
        assert read.json()["last_read_sequence"] == 1


@pytest.mark.anyio
async def test_attachment_rejects_exe_and_private_download_requires_membership():
    a = await _insert_user(_user("admin"))
    b = await _insert_user(_user("helper", perms=["chat.use"]))
    c_user = await _insert_user(_user("driver", perms=["chat.use"]))
    async with await client_for(a) as c:
        room = (await c.post("/api/chat/rooms/dm", json={"peer_user_id": b["id"]})).json()
        exe = await c.post(f"/api/chat/rooms/{room['id']}/attachments", files={"file": ("bad.exe", b"MZ...", "application/octet-stream")})
        assert exe.status_code == 400
        pdf = await c.post(f"/api/chat/rooms/{room['id']}/attachments", files={"file": ("ok.pdf", b"%PDF-1.4\n%", "application/pdf")})
        assert pdf.status_code == 201, pdf.text
        aid = pdf.json()["attachment_id"]
        got = await c.get(f"/api/chat/attachments/{aid}/download")
        assert got.status_code == 200
        assert got.content.startswith(b"%PDF")
    async with await client_for(c_user) as outsider:
        denied = await outsider.get(f"/api/chat/attachments/{aid}/download")
        assert denied.status_code == 403


@pytest.mark.anyio
async def test_websocket_auth_and_subscribe_ack():
    user = await _insert_user(_user("admin", email="ws-admin@example.com"))
    peer = await _insert_user(_user("helper", perms=["chat.use"]))
    async with await client_for(user) as c:
        room = (await c.post("/api/chat/rooms/dm", json={"peer_user_id": peer["id"]})).json()
    token = server.token_for(user)

    class FakeWebSocket:
        def __init__(self, auth=""):
            self.cookies = {}
            self.headers = {}
            self.query_params = {"auth": auth} if auth else {}
            self.closed = None
            self.accepted = False
            self.sent = []

        async def accept(self):
            self.accepted = True

        async def close(self, code=1000):
            self.closed = code

        async def send_json(self, payload):
            self.sent.append(payload)

    ws = FakeWebSocket(token)
    authed = await authenticate_ws(ws, server.db, user_permissions=server.user_permissions)
    assert authed["id"] == user["id"]
    await manager.connect(ws, authed["id"])
    await manager.subscribe(ws, [room["id"]])
    await manager.broadcast_to_room(room["id"], {"type": "message.created"})
    assert ws.accepted is True
    assert ws.sent[-1]["type"] == "message.created"
    await manager.disconnect(ws)

    bad = FakeWebSocket()
    try:
        await authenticate_ws(bad, server.db, user_permissions=server.user_permissions)
        raise AssertionError("unauthenticated websocket should fail")
    except Exception:
        assert bad.closed == 4401


@pytest.mark.anyio
async def test_room_detail_enriches_member_display_names_and_dm_title():
    a = await _insert_user(_user("admin", name="Admin Eternal", perms=["chat.use"]))
    b = await _insert_user(_user("helper", name="Peer Helper", perms=["chat.use"]))
    outsider = await _insert_user(_user("driver", perms=["chat.use"], name="Outsider"))
    async with await client_for(a) as c:
        created = await c.post("/api/chat/rooms/dm", json={"peer_user_id": b["id"]})
        assert created.status_code == 201, created.text
        room_id = created.json()["id"]
        # Strip denormalized names to prove detail re-enriches from users bulk lookup
        await server.db[COLL_MEMBERS].update_many(
            {"room_id": room_id},
            {"$unset": {"user_name": "", "user_email": "", "user_role": "", "name": ""}},
        )
        await server.db[COLL_ROOMS].update_one({"id": room_id}, {"$set": {"name": None}})
        detail = await c.get(f"/api/chat/rooms/{room_id}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["name"] == "Peer Helper"
        assert body["id"] == room_id
        names = {m["user_id"]: m["display_name"] for m in body["members"]}
        assert names[a["id"]] == "Admin Eternal"
        assert names[b["id"]] == "Peer Helper"
        assert all(m["user_id"] in (a["id"], b["id"]) for m in body["members"])
        assert all(m["display_name"] != m["user_id"] for m in body["members"])
    async with await client_for(outsider) as c:
        denied = await c.get(f"/api/chat/rooms/{room_id}")
        assert denied.status_code == 403


@pytest.mark.anyio
async def test_work_tag_search_respects_role_override_and_excludes_inaccessible():
    viewer = await _insert_user(_user("custom_role", permissions_override={"chat.use": True}))
    await server.db.role_permissions.insert_one({
        "role": "custom_role",
        "permissions": ["chat.use", "nav.chat", "po.view"],
    })
    visible = _order(po_number="PO-VISIBLE-1")
    other = _order(po_number="PO-OTHER-9")
    await server.db.orders.insert_many([visible, other])
    async with await client_for(viewer) as c:
        listed = await c.get("/api/chat/work/orders")
        assert listed.status_code == 200, listed.text
        numbers = {o["po_number"] for o in listed.json()["orders"]}
        assert "PO-VISIBLE-1" in numbers
        searched = await c.get("/api/chat/work/orders", params={"q": "VISIBLE"})
        assert searched.status_code == 200
        assert any(o["order_id"] == visible["id"] for o in searched.json()["orders"])
        assert all(o["order_id"] for o in searched.json()["orders"])

    locked = await _insert_user(_user("locked_role", permissions_override={"chat.use": True, "po.view": False}))
    await server.db.role_permissions.insert_one({
        "role": "locked_role",
        "permissions": ["chat.use", "nav.chat"],
    })
    async with await client_for(locked) as c:
        empty = await c.get("/api/chat/work/orders", params={"q": "VISIBLE"})
        assert empty.status_code == 200
        assert empty.json()["orders"] == []


@pytest.mark.anyio
async def test_work_tag_message_persists_canonical_ids_and_same_name_lines():
    admin = await _insert_user(_user("admin"))
    order = _order()
    await server.db.orders.insert_one(order)
    peer = await _insert_user(_user("helper", perms=["chat.use"]))
    async with await client_for(admin) as c:
        room = (await c.post("/api/chat/rooms/dm", json={"peer_user_id": peer["id"]})).json()
        msg0 = await c.post(f"/api/chat/rooms/{room['id']}/messages", json={
            "content": "line0",
            "work_refs": [{"order_id": order["id"], "line_id": "L1", "line_idx": 0}],
        })
        msg1 = await c.post(f"/api/chat/rooms/{room['id']}/messages", json={
            "content": "line1",
            "work_refs": [{"order_id": order["id"], "line_id": "L2", "line_idx": 1}],
        })
        assert msg0.status_code == msg1.status_code == 201
        snap0 = msg0.json()["work_refs"][0]
        snap1 = msg1.json()["work_refs"][0]
        assert snap0["order_id"] == order["id"]
        assert snap0["line_id"] == "L1"
        assert snap0["line_idx"] == 0
        assert snap1["line_id"] == "L2"
        assert snap1["line_idx"] == 1
        assert snap0["item_name"] == snap1["item_name"] == "Cup 16oz"
        listed = await c.get(f"/api/chat/rooms/{room['id']}/messages")
        refs = [m["work_refs"][0] for m in listed.json()["messages"] if m.get("work_refs")]
        assert {r["line_id"] for r in refs} == {"L1", "L2"}


@pytest.mark.anyio
async def test_custom_group_member_add_remove_auth_and_history_preserved():
    admin = await _insert_user(_user("admin", name="Manager"))
    plain = await _insert_user(_user("operator", perms=["chat.use"], name="Plain"))
    peer = await _insert_user(_user("helper", perms=["chat.use"], name="Peer"))
    extra = await _insert_user(_user("driver", perms=["chat.use"], name="Extra"))
    await server.db.roles.insert_many([
        {"role": "helper", "label": "Helper"},
        {"role": "operator", "label": "Operator"},
    ])
    await bootstrap_system_groups(server.db)
    async with await client_for(admin) as c:
        created = await c.post("/api/chat/rooms/custom", json={"name": "Ops", "member_ids": [peer["id"]]})
        assert created.status_code == 201, created.text
        room_id = created.json()["id"]
        msg = await c.post(f"/api/chat/rooms/{room_id}/messages", json={"content": "keep me", "client_message_id": "hist-1"})
        assert msg.status_code == 201
        msg_id = msg.json()["id"]

        added = await c.post(f"/api/chat/rooms/{room_id}/members", json={"user_ids": [extra["id"], peer["id"]]})
        assert added.status_code == 200, added.text
        member_ids = [m["user_id"] for m in added.json()["members"]]
        assert member_ids.count(extra["id"]) == 1
        assert peer["id"] in member_ids
        assert all(m.get("display_name") for m in added.json()["members"])

        removed = await c.request("DELETE", f"/api/chat/rooms/{room_id}/members", json={"user_ids": [extra["id"]]})
        assert removed.status_code == 200
        assert extra["id"] not in [m["user_id"] for m in removed.json()["members"]]
        again = await c.request("DELETE", f"/api/chat/rooms/{room_id}/members", json={"user_ids": [extra["id"]]})
        assert again.status_code == 200

        hist = await c.get(f"/api/chat/rooms/{room_id}/messages")
        assert any(m["id"] == msg_id and m["content"] == "keep me" for m in hist.json()["messages"])

    async with await client_for(extra) as c:
        denied_list = await c.get(f"/api/chat/rooms/{room_id}/messages")
        assert denied_list.status_code == 403
        denied_send = await c.post(f"/api/chat/rooms/{room_id}/messages", json={"content": "nope"})
        assert denied_send.status_code == 403

    async with await client_for(plain) as c:
        denied = await c.post(f"/api/chat/rooms/{room_id}/members", json={"user_ids": [extra["id"]]})
        assert denied.status_code == 403

    # system group manual membership rejected
    helper_room = await server.db[COLL_ROOMS].find_one({"type": "system_group", "system_key": "role:helper"}, {"_id": 0})
    assert helper_room
    async with await client_for(admin) as c:
        bad = await c.post(f"/api/chat/rooms/{helper_room['id']}/members", json={"user_ids": [extra["id"]]})
        assert bad.status_code == 400
        bad_del = await c.request("DELETE", f"/api/chat/rooms/{helper_room['id']}/members", json={"user_ids": [peer["id"]]})
        assert bad_del.status_code == 400


@pytest.mark.anyio
async def test_removed_member_unsubscribed_from_room_events():
    admin = await _insert_user(_user("admin"))
    peer = await _insert_user(_user("helper", perms=["chat.use"]))
    async with await client_for(admin) as c:
        created = await c.post("/api/chat/rooms/custom", json={"name": "Ops", "member_ids": [peer["id"]]})
        room_id = created.json()["id"]

    class FakeWebSocket:
        def __init__(self):
            self.sent = []

        async def accept(self):
            return None

        async def send_json(self, payload):
            self.sent.append(payload)

    ws = FakeWebSocket()
    await manager.connect(ws, peer["id"])
    await manager.subscribe(ws, [room_id])
    await manager.unsubscribe_user_from_room(peer["id"], room_id)
    await manager.broadcast_to_room(room_id, {"type": "message.created", "probe": True})
    assert not any(e.get("probe") for e in ws.sent)
    await manager.disconnect(ws)


@pytest.mark.anyio
async def test_reply_same_room_accepted_and_enriches_preview():
    a = await _insert_user(_user("admin"))
    b = await _insert_user(_user("helper", perms=["chat.use"]))
    async with await client_for(a) as c:
        room = (await c.post("/api/chat/rooms/dm", json={"peer_user_id": b["id"]})).json()
        first = await c.post(f"/api/chat/rooms/{room['id']}/messages", json={"content": "Tolong cek qty packing"})
        assert first.status_code == 201, first.text
        mid = first.json()["id"]
        reply = await c.post(
            f"/api/chat/rooms/{room['id']}/messages",
            json={"content": "Baik, saya cek.", "reply_to_message_id": mid},
        )
        assert reply.status_code == 201, reply.text
        body = reply.json()
        assert body["reply_to_message_id"] == mid
        assert body["reply_to"]["unavailable"] is False
        assert body["reply_to"]["content_preview"] == "Tolong cek qty packing"
        assert "Tolong" in (body["reply_to"].get("content_preview") or "")
        stored = await server.db[COLL_MESSAGES].find_one({"id": body["id"]}, {"_id": 0})
        assert stored["reply_to_message_id"] == mid
        assert "reply_to" not in stored  # only id persisted; preview is read-model
        listed = await c.get(f"/api/chat/rooms/{room['id']}/messages")
        msgs = listed.json()["messages"]
        legacy = next(m for m in msgs if m["id"] == mid)
        assert legacy.get("reply_to_message_id") in (None, "")
        answered = next(m for m in msgs if m["id"] == body["id"])
        assert answered["reply_to"]["message_id"] == mid


@pytest.mark.anyio
async def test_reply_cross_conversation_and_invalid_id_rejected():
    a = await _insert_user(_user("admin"))
    b = await _insert_user(_user("helper", perms=["chat.use"]))
    c_user = await _insert_user(_user("driver", perms=["chat.use"]))
    async with await client_for(a) as c:
        room_ab = (await c.post("/api/chat/rooms/dm", json={"peer_user_id": b["id"]})).json()
        room_ac = (await c.post("/api/chat/rooms/dm", json={"peer_user_id": c_user["id"]})).json()
        msg_ab = await c.post(f"/api/chat/rooms/{room_ab['id']}/messages", json={"content": "secret-ab"})
        mid = msg_ab.json()["id"]
        cross = await c.post(
            f"/api/chat/rooms/{room_ac['id']}/messages",
            json={"content": "cross", "reply_to_message_id": mid},
        )
        assert cross.status_code == 400
        assert "percakapan lain" in (cross.json().get("detail") or "").lower() or cross.status_code == 400
        invalid = await c.post(
            f"/api/chat/rooms/{room_ab['id']}/messages",
            json={"content": "x", "reply_to_message_id": "no-such-message"},
        )
        assert invalid.status_code == 400


@pytest.mark.anyio
async def test_reply_to_deleted_message_shows_unavailable_preview():
    a = await _insert_user(_user("admin"))
    b = await _insert_user(_user("helper", perms=["chat.use"]))
    async with await client_for(a) as c:
        room = (await c.post("/api/chat/rooms/dm", json={"peer_user_id": b["id"]})).json()
        first = await c.post(f"/api/chat/rooms/{room['id']}/messages", json={"content": "akan dihapus"})
        mid = first.json()["id"]
        await c.delete(f"/api/chat/messages/{mid}")
        reply = await c.post(
            f"/api/chat/rooms/{room['id']}/messages",
            json={"content": "masih reply", "reply_to_message_id": mid},
        )
        assert reply.status_code == 201, reply.text
        assert reply.json()["reply_to"]["unavailable"] is True
        assert reply.json()["reply_to"].get("content_preview") in (None, "")


@pytest.mark.anyio
async def test_image_attachment_metadata_not_base64_in_message():
    a = await _insert_user(_user("admin"))
    b = await _insert_user(_user("helper", perms=["chat.use"]))
    # Minimal valid PNG header + IHDR chunk-ish bytes accepted by magic checks
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    async with await client_for(a) as c:
        room = (await c.post("/api/chat/rooms/dm", json={"peer_user_id": b["id"]})).json()
        up = await c.post(
            f"/api/chat/rooms/{room['id']}/attachments",
            files={"file": ("shot.png", png, "image/png")},
        )
        assert up.status_code == 201, up.text
        aid = up.json()["attachment_id"]
        msg = await c.post(
            f"/api/chat/rooms/{room['id']}/messages",
            json={"content": "lihat", "attachment_ids": [aid]},
        )
        assert msg.status_code == 201, msg.text
        body = msg.json()
        raw = await server.db[COLL_MESSAGES].find_one({"id": body["id"]}, {"_id": 0})
        blob = str(raw)
        assert "data:image" not in blob
        assert "base64," not in blob
        assert raw["attachments"][0]["attachment_id"] == aid
        assert raw["attachments"][0].get("original_filename") == "shot.png"
