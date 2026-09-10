"""In-process WebSocket fan-out for a single uvicorn worker."""
from __future__ import annotations

import asyncio
from typing import Dict, Set

from starlette.websockets import WebSocket


EVENT_MESSAGE_CREATED = "message.created"
EVENT_MESSAGE_EDITED = "message.edited"
EVENT_MESSAGE_DELETED = "message.deleted"
EVENT_ROOM_UPDATED = "room.updated"
EVENT_READ_UPDATED = "read.updated"
EVENT_MEMBERSHIP_UPDATED = "membership.updated"


class ConnectionManager:
    def __init__(self) -> None:
        self._user_sockets: Dict[str, Set[WebSocket]] = {}
        self._socket_users: Dict[WebSocket, str] = {}
        self._subscriptions: Dict[WebSocket, Set[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, user_id: str) -> None:
        await websocket.accept()
        uid = str(user_id)
        async with self._lock:
            self._user_sockets.setdefault(uid, set()).add(websocket)
            self._socket_users[websocket] = uid
            self._subscriptions.setdefault(websocket, set())

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            uid = self._socket_users.pop(websocket, None)
            self._subscriptions.pop(websocket, None)
            if uid and uid in self._user_sockets:
                self._user_sockets[uid].discard(websocket)
                if not self._user_sockets[uid]:
                    self._user_sockets.pop(uid, None)

    async def subscribe(self, websocket: WebSocket, room_ids: list[str]) -> None:
        async with self._lock:
            if websocket in self._subscriptions:
                self._subscriptions[websocket].update(str(r) for r in room_ids if r)

    async def unsubscribe(self, websocket: WebSocket, room_ids: list[str]) -> None:
        async with self._lock:
            subs = self._subscriptions.get(websocket)
            if subs is not None:
                subs.difference_update(str(r) for r in room_ids if r)

    async def unsubscribe_user_from_room(self, user_id: str, room_id: str) -> None:
        """Drop in-process room subscriptions for a user after membership removal."""
        rid = str(room_id)
        async with self._lock:
            for ws in list(self._user_sockets.get(str(user_id), set())):
                subs = self._subscriptions.get(ws)
                if subs is not None:
                    subs.discard(rid)

    async def broadcast_to_room(self, room_id: str, event: dict) -> None:
        payload = dict(event or {})
        payload["room_id"] = str(room_id)
        targets: list[WebSocket] = []
        async with self._lock:
            for ws, rooms in self._subscriptions.items():
                if str(room_id) in rooms:
                    targets.append(ws)
        await self._send_many(targets, payload)

    async def broadcast_to_user(self, user_id: str, event: dict) -> None:
        async with self._lock:
            targets = list(self._user_sockets.get(str(user_id), set()))
        await self._send_many(targets, dict(event or {}))

    async def _send_many(self, sockets: list[WebSocket], event: dict) -> None:
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


manager = ConnectionManager()


async def broadcast_to_room(room_id: str, event: dict) -> None:
    await manager.broadcast_to_room(room_id, event)


async def broadcast_to_user(user_id: str, event: dict) -> None:
    await manager.broadcast_to_user(user_id, event)
