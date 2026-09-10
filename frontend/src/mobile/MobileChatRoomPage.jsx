import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  CHAT_ACCEPT,
  canEditMessage,
  formatChatTime,
  newClientMessageId,
  roomDisplayTitle,
} from "../lib/chatFormat";
import { createChatRealtime } from "../lib/chatRealtime";
import {
  ChatComposer,
  MessageBubble,
  WorkPickerModal,
} from "../components/chat/ChatShared";
import "../pages/chat.css";

function normalizeMessage(msg) {
  return {
    ...msg,
    message_id: msg.message_id || msg.id,
    created_at_label: formatChatTime(msg.created_at),
  };
}

export default function MobileChatRoomPage() {
  const { roomId } = useParams();
  const navigate = useNavigate();
  const { user, hasPerm } = useAuth();
  const meId = user?.id || user?.user_id;
  const canModerate = hasPerm("chat.moderate");

  const [room, setRoom] = useState(null);
  const [members, setMembers] = useState([]);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [pendingFiles, setPendingFiles] = useState([]);
  const [workTags, setWorkTags] = useState([]);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [wsStatus, setWsStatus] = useState("connecting");
  const [showWorkPicker, setShowWorkPicker] = useState(false);
  const fileInputRef = useRef(null);
  const seenIds = useRef(new Set());

  const searchWorkOrders = useCallback(async (q) => {
    const res = await api.chatSearchWorkOrders(q);
    return { items: res?.orders || [] };
  }, []);

  const loadWorkLines = useCallback(async (oid) => {
    const res = await api.chatWorkOrderLines(oid);
    return { items: res?.lines || [] };
  }, []);

  const loadWorkTasks = useCallback(async (oid, lineIdx) => {
    const res = await api.chatWorkOrderTasks(oid, lineIdx);
    return { items: res?.tasks || [] };
  }, []);

  const load = useCallback(async () => {
    const [meta, hist] = await Promise.all([
      api.chatGetRoom(roomId),
      api.chatListMessages(roomId, { limit: 40 }),
    ]);
    const metaRoom = meta?.room || meta;
    setRoom(metaRoom);
    setMembers(meta?.members || metaRoom?.members || []);
    const items = (hist?.messages || hist?.items || []).map(normalizeMessage);
    seenIds.current = new Set(items.map((m) => m.message_id));
    setMessages(items);
    const last = items[items.length - 1];
    if (last?.sequence != null) {
      await api.chatMarkRead(roomId, last.sequence).catch(() => {});
    }
  }, [roomId]);

  useEffect(() => {
    load().catch(() => setSendError("Gagal memuat percakapan"));
  }, [load]);

  useEffect(() => {
    const rt = createChatRealtime({
      onStatus: setWsStatus,
      onReconnect: () => load().catch(() => {}),
      onEvent: (ev) => {
        const type = ev?.type || ev?.event;
        const payload = ev?.payload || ev?.data || ev;
        if (type === "message.created") {
          const msg = normalizeMessage(payload.message || payload);
          if (msg.room_id !== roomId && payload.room_id !== roomId) return;
          if (seenIds.current.has(msg.message_id)) return;
          seenIds.current.add(msg.message_id);
          setMessages((cur) => [...cur, msg]);
        }
        if (type === "message.edited" || type === "message.deleted") {
          const msg = normalizeMessage(payload.message || payload);
          setMessages((cur) => cur.map((m) => (m.message_id === msg.message_id ? msg : m)));
        }
      },
    });
    rt.start();
    rt.subscribe([roomId]);
    return () => rt.stop();
  }, [roomId, load]);

  const attachmentUrl = (att) => api.chatAttachmentDownloadUrl(att.attachment_id || att.id);

  const send = async () => {
    if (sending) return;
    const content = draft;
    if (!content.trim() && !pendingFiles.length && !workTags.length) return;
    setSending(true);
    setSendError("");
    const clientMessageId = newClientMessageId();
    try {
      const attachmentIds = [];
      for (const file of pendingFiles) {
        const up = await api.chatUploadAttachment(roomId, file);
        attachmentIds.push(up.attachment_id || up.id);
      }
      const msg = await api.chatSendMessage(roomId, {
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
    } catch (err) {
      setSendError(err?.response?.data?.detail || "Gagal mengirim. Draft tetap ada.");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="mop-chat-page" data-testid="mobile-chat-room-page">
      <header className="mop-topbar" style={{ display: "flex", gap: 8, alignItems: "center", padding: "10px 12px" }}>
        <button type="button" onClick={() => navigate("/m/chat")} data-testid="mobile-chat-back">←</button>
        <div>
          <strong data-testid="mobile-chat-title">
            {roomDisplayTitle(room, { members, meId })}
          </strong>
          <div className="chat-ws-status">{wsStatus}</div>
        </div>
      </header>
      <div className="chat-messages" data-testid="mobile-chat-messages">
        {messages.map((m) => (
          <MessageBubble
            key={m.message_id}
            message={m}
            self={m.sender_user_id === meId}
            attachmentUrl={attachmentUrl}
            mobile
            canModerate={canModerate}
            onEdit={async (message) => {
              if (!canEditMessage(message)) return;
              const next = window.prompt("Edit pesan", message.content || "");
              if (next == null) return;
              const res = await api.chatEditMessage(message.message_id, { content: next });
              const msg = normalizeMessage(res?.message || res);
              setMessages((cur) => cur.map((x) => (x.message_id === msg.message_id ? msg : x)));
            }}
            onDelete={async (message) => {
              const res = await api.chatDeleteMessage(message.message_id);
              const msg = normalizeMessage(res?.message || { ...message, deleted: true });
              setMessages((cur) => cur.map((x) => (x.message_id === message.message_id ? msg : x)));
            }}
          />
        ))}
      </div>
      {workTags.length ? (
        <div className="chat-pending-tags" data-testid="chat-pending-work-tags">
          {workTags.map((t, i) => (
            <span key={`${t.order_id}-${i}`}>{t.po_number || `PO ${t.order_id}`}</span>
          ))}
        </div>
      ) : null}
      {sendError ? <div className="chat-error">{sendError}</div> : null}
      <ChatComposer
        draft={draft}
        setDraft={setDraft}
        onSend={send}
        sending={sending}
        onPickFiles={(files) => setPendingFiles((cur) => [...cur, ...files].slice(0, 5))}
        onTagWork={() => setShowWorkPicker(true)}
        fileInputRef={fileInputRef}
        accept={CHAT_ACCEPT}
      />
      <WorkPickerModal
        open={showWorkPicker}
        onClose={() => setShowWorkPicker(false)}
        searchOrders={searchWorkOrders}
        loadLines={loadWorkLines}
        loadTasks={loadWorkTasks}
        onConfirm={(ctx) => {
          setWorkTags((cur) => [...cur, ctx]);
          setShowWorkPicker(false);
        }}
      />
    </div>
  );
}
