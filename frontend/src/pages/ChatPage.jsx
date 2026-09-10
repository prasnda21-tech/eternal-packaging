import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  CHAT_ACCEPT,
  canEditMessage,
  formatChatTime,
  memberDisplayName,
  newClientMessageId,
  previewText,
  roomDisplayTitle,
  roomInitials,
  roomTypeFilter,
} from "../lib/chatFormat";
import { createChatRealtime } from "../lib/chatRealtime";
import {
  ChatComposer,
  MemberListItem,
  MessageBubble,
  NewChatModal,
  RoomListItem,
  WorkPickerModal,
  useMentionQuery,
} from "../components/chat/ChatShared";
import "./chat.css";

const FILTERS = [
  { id: "all", label: "Semua" },
  { id: "personal", label: "Personal" },
  { id: "group", label: "Grup" },
  { id: "work", label: "Pekerjaan" },
];

function normalizeRoom(room, { members = [], meId } = {}) {
  const id = room.room_id || room.id;
  const title = roomDisplayTitle(room, { members, meId });
  return {
    ...room,
    room_id: id,
    name: title,
    display_name: title,
    initials: roomInitials(title),
    preview: room.last_message_preview || previewText(room.last_message),
    last_activity_label: formatChatTime(room.last_activity_at || room.updated_at),
    room_type: room.room_type || room.type,
  };
}

function normalizeMessage(msg) {
  return {
    ...msg,
    message_id: msg.message_id || msg.id,
    created_at_label: formatChatTime(msg.created_at),
  };
}

export default function ChatPage() {
  const { roomId: routeRoomId } = useParams();
  const navigate = useNavigate();
  const { user, hasPerm } = useAuth();
  const meId = user?.id || user?.user_id;
  const canManageGroup = hasPerm("chat.group.manage");
  const canModerate = hasPerm("chat.moderate");

  const [filter, setFilter] = useState("all");
  const [rooms, setRooms] = useState([]);
  const [activeRoomId, setActiveRoomId] = useState(routeRoomId || null);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [pendingFiles, setPendingFiles] = useState([]);
  const [workTags, setWorkTags] = useState([]);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [wsStatus, setWsStatus] = useState("connecting");
  const [toast, setToast] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [showWorkPicker, setShowWorkPicker] = useState(false);
  const [workPickerMode, setWorkPickerMode] = useState("tag"); // tag | room
  const [infoOpen, setInfoOpen] = useState(true);
  const [lightbox, setLightbox] = useState(null);
  const [members, setMembers] = useState([]);
  const [roomDetail, setRoomDetail] = useState(null);
  const fileInputRef = useRef(null);
  const messagesRef = useRef(null);
  const stickToBottomRef = useRef(true);
  const seenIds = useRef(new Set());
  const activeRoomIdRef = useRef(activeRoomId);
  const roomsRef = useRef([]);
  const realtimeRef = useRef(null);
  activeRoomIdRef.current = activeRoomId;

  const mentionQ = useMentionQuery(draft);
  const mentionSuggestions = useMemo(() => {
    if (mentionQ == null) return [];
    const q = mentionQ.toLowerCase();
    return (members || [])
      .filter((m) => {
        const label = memberDisplayName(m).toLowerCase();
        const role = String(m.role || m.user_role || "").toLowerCase();
        return label.includes(q) || role.includes(q);
      })
      .slice(0, 8)
      .map((m) => ({ id: m.user_id || m.id, label: memberDisplayName(m), type: "user" }));
  }, [mentionQ, members]);

  const loadRooms = useCallback(async () => {
    const res = await api.chatListRooms();
    const items = (res?.rooms || res?.items || []).map((r) => normalizeRoom(r, { meId }));
    setRooms(items);
    return items;
  }, [meId]);

  const loadMessages = useCallback(async (rid) => {
    if (!rid) return;
    const res = await api.chatListMessages(rid, { limit: 40 });
    const items = (res?.messages || res?.items || []).map(normalizeMessage);
    seenIds.current = new Set(items.map((m) => m.message_id));
    setMessages(items);
    const last = items[items.length - 1];
    if (last?.sequence != null) {
      try {
        await api.chatMarkRead(rid, last.sequence);
        setRooms((cur) => cur.map((r) => (r.room_id === rid ? { ...r, unread_count: 0 } : r)));
      } catch { /* ignore */ }
    }
  }, []);

  const loadRoomMeta = useCallback(async (rid) => {
    if (!rid) return;
    const res = await api.chatGetRoom(rid);
    const room = res?.room || res || {};
    const nextMembers = res?.members || room?.members || [];
    setMembers(nextMembers);
    setRoomDetail(normalizeRoom(room, { members: nextMembers, meId }));
  }, [meId]);

  useEffect(() => {
    loadRooms().catch(() => setToast("Gagal memuat inbox chat"));
  }, [loadRooms]);

  useEffect(() => {
    if (routeRoomId) setActiveRoomId(routeRoomId);
  }, [routeRoomId]);

  useEffect(() => {
    if (!activeRoomId) {
      setMessages([]);
      setRoomDetail(null);
      return;
    }
    loadMessages(activeRoomId).catch(() => setToast("Gagal memuat pesan"));
    loadRoomMeta(activeRoomId).catch(() => {});
  }, [activeRoomId, loadMessages, loadRoomMeta]);

  useEffect(() => {
    const rt = createChatRealtime({
      onStatus: setWsStatus,
      onReconnect: () => {
        loadRooms().catch(() => {});
        if (activeRoomIdRef.current) loadMessages(activeRoomIdRef.current).catch(() => {});
        const ids = roomsRef.current.map((r) => r.room_id || r.id).filter(Boolean);
        if (activeRoomIdRef.current) ids.push(activeRoomIdRef.current);
        rt.subscribe(ids);
      },
      onEvent: (ev) => {
        const type = ev?.type || ev?.event;
        const payload = ev?.payload || ev?.data || ev;
        if (type === "message.created") {
          const msg = normalizeMessage(payload.message || payload);
          const rid = msg.room_id || payload.room_id;
          const mid = msg.message_id || msg.id;
          if (seenIds.current.has(mid)) return;
          if (rid === activeRoomIdRef.current) {
            seenIds.current.add(mid);
            setMessages((cur) => [...cur, msg]);
            if (msg.sequence != null) api.chatMarkRead(rid, msg.sequence).catch(() => {});
          } else if (msg.sender_user_id !== meId) {
            setToast(`Pesan baru di ${payload.room_name || "chat"}`);
            loadRooms().catch(() => {});
          }
        }
        if (type === "message.edited" || type === "message.deleted") {
          const msg = normalizeMessage(payload.message || payload);
          const mid = msg.message_id || msg.id;
          setMessages((cur) => cur.map((m) => ((m.message_id || m.id) === mid ? msg : m)));
        }
        if (type === "room.updated" || type === "membership.updated" || type === "read.updated") {
          loadRooms().catch(() => {});
        }
      },
    });
    realtimeRef.current = rt;
    rt.start();
    return () => {
      realtimeRef.current = null;
      rt.stop();
    };
  }, [loadRooms, loadMessages, meId]);

  useEffect(() => {
    roomsRef.current = rooms;
    const ids = rooms.map((r) => r.room_id || r.id).filter(Boolean);
    if (activeRoomId) ids.push(activeRoomId);
    realtimeRef.current?.subscribe(ids);
  }, [rooms, activeRoomId]);

  useEffect(() => {
    stickToBottomRef.current = true;
  }, [activeRoomId]);

  useEffect(() => {
    const el = messagesRef.current;
    if (!el || !stickToBottomRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, activeRoomId]);

  const onMessagesScroll = () => {
    const el = messagesRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distance < 80;
  };

  const filteredRooms = useMemo(
    () => rooms.filter((r) => roomTypeFilter(filter, r)),
    [rooms, filter],
  );

  const conversationTitle = roomDetail?.name || "Percakapan";

  const selectRoom = (room) => {
    const id = room.room_id || room.id;
    setActiveRoomId(id);
    navigate(`/chat/${encodeURIComponent(id)}`, { replace: false });
  };

  const attachmentUrl = (att) => api.chatAttachmentDownloadUrl(att.attachment_id || att.id);

  const send = async () => {
    if (!activeRoomId || sending) return;
    const content = draft;
    if (!content.trim() && !pendingFiles.length && !workTags.length) return;
    setSending(true);
    setSendError("");
    const clientMessageId = newClientMessageId();
    try {
      const attachmentIds = [];
      for (const file of pendingFiles) {
        const up = await api.chatUploadAttachment(activeRoomId, file);
        attachmentIds.push(up.attachment_id || up.id);
      }
      const msg = await api.chatSendMessage(activeRoomId, {
        content,
        client_message_id: clientMessageId,
        work_refs: workTags,
        attachment_ids: attachmentIds,
      });
      const normalized = normalizeMessage(msg?.message || msg);
      if (!seenIds.current.has(normalized.message_id)) {
        seenIds.current.add(normalized.message_id);
        setMessages((cur) => [...cur, normalized]);
      }
      setDraft("");
      setPendingFiles([]);
      setWorkTags([]);
      loadRooms().catch(() => {});
    } catch (err) {
      setSendError(err?.response?.data?.detail || "Gagal mengirim. Draft tetap ada.");
    } finally {
      setSending(false);
    }
  };

  const onEdit = async (message) => {
    if (message?.type === "preview") {
      setLightbox(message.attachment);
      return;
    }
    if (!canEditMessage(message)) {
      setToast("Batas edit 15 menit terlewati");
      return;
    }
    const next = window.prompt("Edit pesan", message.content || "");
    if (next == null) return;
    try {
      const res = await api.chatEditMessage(message.message_id, { content: next });
      const msg = normalizeMessage(res?.message || res);
      setMessages((cur) => cur.map((m) => (m.message_id === msg.message_id ? msg : m)));
    } catch (err) {
      setToast(err?.response?.data?.detail || "Gagal mengedit");
    }
  };

  const onDelete = async (message) => {
    if (!window.confirm("Hapus pesan ini?")) return;
    try {
      const res = await api.chatDeleteMessage(message.message_id);
      const msg = normalizeMessage(res?.message || { ...message, deleted: true, deleted_at: new Date().toISOString() });
      setMessages((cur) => cur.map((m) => (m.message_id === message.message_id ? msg : m)));
    } catch (err) {
      setToast(err?.response?.data?.detail || "Gagal menghapus");
    }
  };

  return (
    <div className={`chat-page${infoOpen && activeRoomId ? " has-info" : ""}`} data-testid="chat-page">
      <aside className="chat-inbox" data-testid="chat-inbox">
        <div className="chat-inbox-head" data-testid="chat-inbox-head">
          <h1>Chat</h1>
          <button
            type="button"
            className="chat-btn chat-btn-primary"
            data-testid="chat-new-btn"
            onClick={() => setShowNew(true)}
          >
            + Baru
          </button>
        </div>
        <div className="chat-filters">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className={filter === f.id ? "is-active" : ""}
              data-testid={`chat-filter-${f.id}`}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="chat-room-list">
          {filteredRooms.map((room) => (
            <RoomListItem
              key={room.room_id}
              room={room}
              active={room.room_id === activeRoomId}
              onSelect={selectRoom}
            />
          ))}
        </div>
      </aside>

      <section className="chat-conversation" data-testid="chat-conversation">
        {activeRoomId ? (
          <>
            <header className="chat-conv-head">
              <div>
                <strong data-testid="chat-conversation-title">{conversationTitle}</strong>
                <span className="chat-ws-status" data-testid="chat-ws-status">{wsStatus}</span>
              </div>
              <button
                type="button"
                className="chat-btn chat-btn-secondary"
                data-testid="chat-info-btn"
                onClick={() => setInfoOpen((v) => !v)}
              >
                Info
              </button>
            </header>
            <div
              className="chat-messages"
              data-testid="chat-messages"
              ref={messagesRef}
              onScroll={onMessagesScroll}
            >
              {messages.map((m) => (
                <MessageBubble
                  key={m.message_id}
                  message={m}
                  self={m.sender_user_id === meId}
                  attachmentUrl={attachmentUrl}
                  onEdit={onEdit}
                  onDelete={onDelete}
                  canModerate={canModerate}
                />
              ))}
            </div>
            {workTags.length ? (
              <div className="chat-pending-tags" data-testid="chat-pending-work-tags">
                {workTags.map((t, i) => (
                  <span key={`${t.order_id}-${i}`}>PO {t.order_id}</span>
                ))}
              </div>
            ) : null}
            {pendingFiles.length ? (
              <div className="chat-pending-files">{pendingFiles.map((f) => f.name).join(", ")}</div>
            ) : null}
            {sendError ? <div className="chat-error" data-testid="chat-send-error">{sendError}</div> : null}
            <ChatComposer
              draft={draft}
              setDraft={setDraft}
              onSend={send}
              sending={sending}
              onPickFiles={(files) => setPendingFiles((cur) => [...cur, ...files].slice(0, 5))}
              onTagWork={() => { setWorkPickerMode("tag"); setShowWorkPicker(true); }}
              mentionSuggestions={mentionSuggestions}
              onPickMention={(m) => {
                setDraft((d) => d.replace(/(?:^|\s)@[^\s@]*$/, (x) => `${x.startsWith(" ") || x.startsWith("\n") ? x[0] : ""}@${m.label} `));
              }}
              fileInputRef={fileInputRef}
              accept={CHAT_ACCEPT}
            />
          </>
        ) : (
          <div className="chat-empty">Pilih percakapan atau buat chat baru.</div>
        )}
      </section>

      {infoOpen && activeRoomId ? (
        <aside className="chat-info" data-testid="chat-info-drawer">
          <h2>Info ruang</h2>
          <p data-testid="chat-info-title">{conversationTitle}</p>
          <p className="muted">{roomDetail?.room_type}</p>
          {roomDetail?.work_context ? (
            <div className="chat-info-work">
              <strong>Konteks pekerjaan</strong>
              <pre>{JSON.stringify(roomDetail.work_context, null, 2)}</pre>
            </div>
          ) : null}
          <h3>Anggota</h3>
          <ul className="chat-member-list" data-testid="chat-member-list">
            {members.map((m) => (
              <MemberListItem key={m.user_id || m.id} member={m} />
            ))}
          </ul>
        </aside>
      ) : null}

      <NewChatModal
        open={showNew}
        onClose={() => setShowNew(false)}
        canManageGroup={canManageGroup}
        searchUsers={async (q) => {
          const res = await api.chatSearchUsers(q);
          return { items: res?.users || res?.items || [] };
        }}
        onCreateDm={async (peerId) => {
          const res = await api.chatCreateDm(peerId);
          const room = normalizeRoom(res?.room || res);
          setShowNew(false);
          await loadRooms();
          selectRoom(room);
        }}
        onCreateGroup={async (name, memberIds) => {
          const res = await api.chatCreateCustomGroup({ name, member_ids: memberIds });
          const room = normalizeRoom(res?.room || res);
          setShowNew(false);
          await loadRooms();
          selectRoom(room);
        }}
        onCreateWork={() => {
          setShowNew(false);
          setWorkPickerMode("room");
          setShowWorkPicker(true);
        }}
      />

      <WorkPickerModal
        open={showWorkPicker}
        onClose={() => setShowWorkPicker(false)}
        searchOrders={async (q) => {
          const res = await api.chatSearchWorkOrders(q);
          return { items: res?.orders || res?.items || [] };
        }}
        loadLines={async (oid) => {
          const res = await api.chatWorkOrderLines(oid);
          return { items: res?.lines || res?.items || [] };
        }}
        loadTasks={async (oid, lineIdx) => {
          const res = await api.chatWorkOrderTasks(oid, lineIdx);
          return { items: res?.tasks || res?.items || [] };
        }}
        onConfirm={async (ctx) => {
          if (workPickerMode === "tag") {
            setWorkTags((cur) => [...cur, ctx]);
            setShowWorkPicker(false);
            return;
          }
          const res = await api.chatCreateWorkRoom(ctx);
          const room = normalizeRoom(res?.room || res);
          setShowWorkPicker(false);
          await loadRooms();
          selectRoom(room);
        }}
      />

      {lightbox ? (
        <div className="lightbox" data-testid="chat-lightbox" onClick={() => setLightbox(null)}>
          <img src={attachmentUrl(lightbox)} alt={lightbox.original_filename || "gambar"} />
        </div>
      ) : null}

      {toast ? <div className="toast" data-testid="chat-toast">{toast}</div> : null}
    </div>
  );
}
