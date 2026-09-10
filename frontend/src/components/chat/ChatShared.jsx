import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { workContextHref, workContextLabel } from "../../lib/chatWorkLinks";
import { formatBytes, isImageMime } from "../../lib/chatFormat";

export function WorkContextCard({ workRef, mobile = false }) {
  const snap = workRef?.snapshot || {};
  const href = workContextHref(workRef, { mobile });
  const title = workContextLabel(workRef);
  return (
    <div className="chat-work-card" data-testid="chat-work-card">
      <div className="chat-work-card-title">{title}</div>
      <div className="chat-work-card-meta">
        {snap.customer_name ? <div>Customer: {snap.customer_name}</div> : null}
        {snap.item_name ? <div>Item: {snap.item_name}</div> : null}
        {snap.qty != null && snap.qty !== "" ? <div>Qty: {String(snap.qty)}</div> : null}
        {(snap.stage_label || snap.task_label) ? (
          <div>Stage: {snap.stage_label || snap.task_label}</div>
        ) : null}
        {snap.assignee_name ? <div>Assignee: {snap.assignee_name}</div> : null}
        {snap.is_h1 ? <div className="chat-work-flag">H-1</div> : null}
        {snap.is_urgent || snap.urgent ? <div className="chat-work-flag urgent">URGENT</div> : null}
      </div>
      {href ? (
        <Link className="chat-work-link" to={href} data-testid="chat-work-open-link">
          Lihat Pekerjaan
        </Link>
      ) : null}
    </div>
  );
}

export function AttachmentCard({ att, onOpen, downloadUrl }) {
  const name = att?.original_filename || att?.name || "file";
  const ct = att?.content_type || att?.mime_type || "";
  const size = formatBytes(att?.byte_size || att?.size);
  if (isImageMime(ct)) {
    return (
      <button
        type="button"
        className="chat-attach-image"
        data-testid="chat-attach-image"
        onClick={() => onOpen?.(att)}
      >
        <img src={downloadUrl} alt={name} loading="lazy" />
      </button>
    );
  }
  return (
    <a
      className="chat-attach-file"
      data-testid="chat-attach-file"
      href={downloadUrl}
      target="_blank"
      rel="noreferrer"
    >
      <strong>{name}</strong>
      <span>{ct || "dokumen"} · {size}</span>
    </a>
  );
}

export function MessageBubble({
  message,
  self,
  attachmentUrl,
  mobile,
  onEdit,
  onDelete,
  canModerate,
}) {
  const deleted = !!(message.deleted || message.deleted_at);
  const edited = !!(message.edited_at && !deleted);
  return (
    <div
      className={`chat-bubble${self ? " is-self" : ""}${deleted ? " is-deleted" : ""}`}
      data-testid="chat-message"
      data-message-id={message.message_id || message.id}
    >
      {!self ? (
        <div className="chat-bubble-author">{message.sender_name || message.sender_user_id}</div>
      ) : null}
      {deleted ? (
        <div className="chat-bubble-deleted" data-testid="chat-message-deleted">Pesan dihapus</div>
      ) : (
        <>
          {message.content ? <div className="chat-bubble-text">{message.content}</div> : null}
          {(message.work_refs || []).map((ref, idx) => (
            <WorkContextCard key={`${ref.order_id}-${idx}`} workRef={ref} mobile={mobile} />
          ))}
          {(message.attachments || []).map((att) => (
            <AttachmentCard
              key={att.attachment_id || att.id}
              att={att}
              downloadUrl={attachmentUrl(att)}
              onOpen={(a) => onEdit?.({ type: "preview", attachment: a })}
            />
          ))}
        </>
      )}
      <div className="chat-bubble-foot">
        <span>{message.created_at_label || ""}</span>
        {edited ? <span data-testid="chat-edited-label">Diedit</span> : null}
        {self && !deleted ? (
          <span className="chat-bubble-actions">
            <button type="button" data-testid="chat-edit-btn" onClick={() => onEdit?.(message)}>Edit</button>
            <button type="button" data-testid="chat-delete-btn" onClick={() => onDelete?.(message)}>Hapus</button>
          </span>
        ) : null}
        {!self && canModerate && !deleted ? (
          <button type="button" data-testid="chat-moderate-delete" onClick={() => onDelete?.(message)}>
            Moderasi hapus
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function RoomListItem({ room, active, onSelect }) {
  const unread = Number(room.unread_count || 0);
  return (
    <button
      type="button"
      className={`chat-room-row${active ? " is-active" : ""}`}
      data-testid="chat-room-row"
      data-room-id={room.room_id || room.id}
      onClick={() => onSelect(room)}
    >
      <span className="chat-room-avatar" aria-hidden>{room.initials || "?"}</span>
      <span className="chat-room-body">
        <span className="chat-room-top">
          <strong>{room.name}</strong>
          <time>{room.last_activity_label || ""}</time>
        </span>
        <span className="chat-room-preview">{room.preview || "—"}</span>
        <span className="chat-room-tags">
          {room.room_type === "work" || room.type === "work" ? <em>Pekerjaan</em> : null}
          {room.room_type === "system_group" || room.type === "system_group" ? <em>Sistem</em> : null}
          {room.room_type === "custom_group" || room.type === "custom_group" ? <em>Grup</em> : null}
          {room.archived ? <em>Arsip</em> : null}
        </span>
      </span>
      {unread > 0 ? (
        <span className="chat-unread-badge" data-testid="chat-room-unread">{unread > 99 ? "99+" : unread}</span>
      ) : null}
    </button>
  );
}

export function ChatComposer({
  draft,
  setDraft,
  onSend,
  onPickFiles,
  onTagWork,
  sending,
  mentionSuggestions,
  onPickMention,
  fileInputRef,
  accept,
}) {
  const taRef = useRef(null);

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  return (
    <div className="chat-composer" data-testid="chat-composer">
      {mentionSuggestions?.length ? (
        <div className="chat-mention-list" data-testid="chat-mention-list">
          {mentionSuggestions.map((m) => (
            <button key={m.id} type="button" onClick={() => onPickMention(m)}>
              @{m.label || m.name}
            </button>
          ))}
        </div>
      ) : null}
      <div className="chat-composer-tools">
        <button type="button" data-testid="chat-tag-work-btn" onClick={onTagWork}>Tag Pekerjaan</button>
        <button type="button" data-testid="chat-attach-btn" onClick={() => fileInputRef.current?.click()}>
          Lampiran
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={accept}
          hidden
          data-testid="chat-file-input"
          onChange={(e) => {
            onPickFiles(Array.from(e.target.files || []));
            e.target.value = "";
          }}
        />
      </div>
      <textarea
        ref={taRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        rows={3}
        placeholder="Tulis pesan… (Enter kirim, Shift+Enter baris baru)"
        data-testid="chat-composer-input"
      />
      <button
        type="button"
        className="chat-send-btn"
        data-testid="chat-send-btn"
        disabled={sending}
        onClick={onSend}
      >
        {sending ? "Mengirim…" : "Kirim"}
      </button>
    </div>
  );
}

export function WorkPickerModal({
  open,
  onClose,
  onConfirm,
  searchOrders,
  loadLines,
  loadTasks,
}) {
  const [q, setQ] = useState("");
  const [orders, setOrders] = useState([]);
  const [order, setOrder] = useState(null);
  const [lines, setLines] = useState([]);
  const [line, setLine] = useState(null);
  const [tasks, setTasks] = useState([]);
  const [task, setTask] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setQ("");
    setOrders([]);
    setOrder(null);
    setLines([]);
    setLine(null);
    setTasks([]);
    setTask(null);
    setError("");
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const t = setTimeout(async () => {
      try {
        const res = await searchOrders(q);
        setOrders(res?.items || res || []);
      } catch (err) {
        setError(err?.response?.data?.detail || "Gagal mencari PO");
      }
    }, 250);
    return () => clearTimeout(t);
  }, [q, open, searchOrders]);

  if (!open) return null;

  return (
    <div className="modal" data-testid="chat-work-picker">
      <div className="modal-inner chat-work-picker-inner">
        <div className="modal-head">
          <strong>Tag / Chat Pekerjaan</strong>
          <button type="button" onClick={onClose}>Tutup</button>
        </div>
        {!order ? (
          <>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Cari PO…"
              data-testid="chat-work-po-search"
            />
            <ul className="chat-picker-list">
              {orders.map((o) => (
                <li key={o.id || o.order_id}>
                  <button
                    type="button"
                    onClick={async () => {
                      setOrder(o);
                      const lr = await loadLines(o.id || o.order_id);
                      setLines(lr?.items || lr || []);
                    }}
                  >
                    <strong>{o.po_number || o.order_number}</strong>
                    <span>{o.customer_name || o.customer || ""}</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : !line && !task ? (
          <>
            <p>PO {order.po_number || order.order_number} — pilih line (opsional) atau lanjut PO saja.</p>
            <ul className="chat-picker-list">
              {lines.map((li) => (
                <li key={`${li.line_id || "idx"}-${li.line_idx}`}>
                  <button
                    type="button"
                    data-testid="chat-work-line-option"
                    onClick={async () => {
                      setLine(li);
                      const tr = await loadTasks(order.id || order.order_id, li.line_idx);
                      setTasks(tr?.items || tr || []);
                    }}
                  >
                    <strong>{li.name || li.item_name}</strong>
                    <span>Qty {li.qty ?? li.quantity} · line_idx {li.line_idx}</span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="chat-picker-actions">
              <button
                type="button"
                data-testid="chat-work-po-only"
                onClick={() => onConfirm({
                  order_id: order.id || order.order_id,
                })}
              >
                Gunakan PO saja
              </button>
              <button
                type="button"
                onClick={async () => {
                  const tr = await loadTasks(order.id || order.order_id);
                  setTasks(tr?.items || tr || []);
                  setLine({ __po_task: true });
                }}
              >
                Pilih task PO
              </button>
            </div>
          </>
        ) : (
          <>
            <p>Pilih task/stage (opsional).</p>
            <ul className="chat-picker-list">
              {tasks.map((tk) => (
                <li key={tk.task_id}>
                  <button
                    type="button"
                    data-testid="chat-work-task-option"
                    onClick={() => setTask(tk)}
                  >
                    <strong>{tk.task_label || tk.division || tk.task_id}</strong>
                    <span>{tk.task_id}</span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="chat-picker-actions">
              <button
                type="button"
                data-testid="chat-work-confirm"
                onClick={() => onConfirm({
                  order_id: order.id || order.order_id,
                  line_id: line?.line_id || undefined,
                  line_idx: line?.__po_task ? undefined : line?.line_idx,
                  task_id: task?.task_id || undefined,
                })}
              >
                Pakai konteks ini
              </button>
              <button type="button" onClick={onClose}>Batal</button>
            </div>
          </>
        )}
        {error ? <div className="chat-error">{error}</div> : null}
      </div>
    </div>
  );
}

export function NewChatModal({
  open,
  onClose,
  canManageGroup,
  searchUsers,
  onCreateDm,
  onCreateGroup,
  onCreateWork,
}) {
  const [mode, setMode] = useState("dm");
  const [q, setQ] = useState("");
  const [users, setUsers] = useState([]);
  const [selected, setSelected] = useState([]);
  const [groupName, setGroupName] = useState("");

  useEffect(() => {
    if (!open) return;
    setMode("dm");
    setQ("");
    setUsers([]);
    setSelected([]);
    setGroupName("");
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const t = setTimeout(async () => {
      const res = await searchUsers(q);
      setUsers(res?.items || res || []);
    }, 200);
    return () => clearTimeout(t);
  }, [q, open, searchUsers]);

  if (!open) return null;

  return (
    <div className="modal" data-testid="chat-new-modal">
      <div className="modal-inner">
        <div className="modal-head">
          <strong>Chat Baru</strong>
          <button type="button" onClick={onClose}>Tutup</button>
        </div>
        <div className="chat-new-modes">
          <button type="button" className={mode === "dm" ? "is-active" : ""} onClick={() => setMode("dm")} data-testid="chat-new-dm">Personal</button>
          {canManageGroup ? (
            <button type="button" className={mode === "group" ? "is-active" : ""} onClick={() => setMode("group")} data-testid="chat-new-group">Grup Custom</button>
          ) : null}
          <button type="button" className={mode === "work" ? "is-active" : ""} onClick={() => setMode("work")} data-testid="chat-new-work">Chat Pekerjaan</button>
        </div>
        {mode === "work" ? (
          <button type="button" className="btn" onClick={onCreateWork}>Pilih konteks pekerjaan…</button>
        ) : (
          <>
            {mode === "group" ? (
              <input
                value={groupName}
                onChange={(e) => setGroupName(e.target.value)}
                placeholder="Nama grup"
                data-testid="chat-group-name"
              />
            ) : null}
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Cari user aktif…"
              data-testid="chat-user-search"
            />
            <ul className="chat-picker-list">
              {users.map((u) => {
                const id = u.id || u.user_id;
                const checked = selected.includes(id);
                return (
                  <li key={id}>
                    <button
                      type="button"
                      onClick={() => {
                        if (mode === "dm") {
                          onCreateDm(id);
                          return;
                        }
                        setSelected((cur) => (checked ? cur.filter((x) => x !== id) : [...cur, id]));
                      }}
                    >
                      <strong>{u.name || u.email}</strong>
                      <span>{u.role}</span>
                      {mode === "group" ? <span>{checked ? "✓" : ""}</span> : null}
                    </button>
                  </li>
                );
              })}
            </ul>
            {mode === "group" ? (
              <button
                type="button"
                className="btn"
                data-testid="chat-create-group-btn"
                onClick={() => onCreateGroup(groupName, selected)}
              >
                Buat Grup
              </button>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

export function useMentionQuery(draft) {
  return useMemo(() => {
    const m = String(draft || "").match(/(?:^|\s)@([^\s@]*)$/);
    if (!m) return null;
    return m[1] || "";
  }, [draft]);
}
