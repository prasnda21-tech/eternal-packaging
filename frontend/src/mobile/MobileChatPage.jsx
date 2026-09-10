import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import {
  formatChatTime,
  previewText,
  roomInitials,
  roomTypeFilter,
} from "../lib/chatFormat";
import { RoomListItem } from "../components/chat/ChatShared";
import "../pages/chat.css";

function normalizeRoom(room) {
  return {
    ...room,
    room_id: room.room_id || room.id,
    name: room.name || room.title || "Chat",
    initials: roomInitials(room.name || room.title),
    preview: room.last_message_preview || previewText(room.last_message),
    last_activity_label: formatChatTime(room.last_activity_at || room.updated_at),
    room_type: room.room_type || room.type,
  };
}

export default function MobileChatPage() {
  const navigate = useNavigate();
  const [rooms, setRooms] = useState([]);
  const [filter, setFilter] = useState("all");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await api.chatListRooms();
      setRooms((res?.rooms || res?.items || []).map(normalizeRoom));
    } catch (err) {
      setError(err?.response?.data?.detail || "Gagal memuat chat");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = rooms.filter((r) => roomTypeFilter(filter, r));

  return (
    <div className="mop-chat-page" data-testid="mobile-chat-page">
      <header className="mop-topbar" style={{ padding: "10px 12px" }}>
        <strong>Chat</strong>
      </header>
      <div className="chat-filters">
        {[
          ["all", "Semua"],
          ["personal", "Personal"],
          ["group", "Grup"],
          ["work", "Pekerjaan"],
        ].map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={filter === id ? "is-active" : ""}
            onClick={() => setFilter(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {error ? <div className="chat-error">{error}</div> : null}
      <div className="chat-room-list">
        {filtered.map((room) => (
          <RoomListItem
            key={room.room_id}
            room={room}
            active={false}
            onSelect={() => navigate(`/m/chat/${encodeURIComponent(room.room_id)}`)}
          />
        ))}
      </div>
    </div>
  );
}
