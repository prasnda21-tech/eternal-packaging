import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { workContextHref, workContextLabel } from "../../lib/chatWorkLinks";
import {
  extractClipboardImageFiles,
  formatBytes,
  isImageMime,
  memberDisplayName,
  memberRoleLabel,
  replyQuotePreview,
  roomInitials,
} from "../../lib/chatFormat";
import PoIdentityBadges from "../PoIdentityBadges";

export function WorkContextCard({ workRef, mobile = false }) {
  const snap = workRef?.snapshot || workRef || {};
  const href = workContextHref(workRef, { mobile });
  const title = workContextLabel(workRef);
  return (
    <div className="chat-work-card" data-testid="chat-work-card">
      <div className="chat-work-card-title">
        {title}{" "}
        <PoIdentityBadges order={snap} testIdPrefix="chat-work-card" />
      </div>
      <div className="chat-work-card-meta">
        {snap.customer_name ? <div>Customer: {snap.customer_name}</div> : null}
        {snap.item_name ? <div>Item: {snap.item_name}</div> : null}
        {snap.qty != null && snap.qty !== "" ? <div>Qty: {String(snap.qty)}</div> : null}
        {(snap.stage_label || snap.task_label) ? (
          <div>Stage: {snap.stage_label || snap.task_label}</div>
        ) : null}
        {(snap.assignee || snap.assignee_name) ? <div>Assignee: {snap.assignee || snap.assignee_name}</div> : null}
        {snap.h_minus_1 || snap.is_h1 ? <div className="chat-work-flag">H-1</div> : null}
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

export function ReplyQuote({ reply, onJump }) {
  const quote = replyQuotePreview(reply);
  const mid = reply?.message_id || reply?.id;
  return (
    <button
      type="button"
      className={`chat-reply-quote${quote.unavailable ? " is-unavailable" : ""}`}
      data-testid="chat-reply-quote"
      data-reply-to={mid || ""}
      onClick={() => {
        if (mid && onJump) onJump(mid);
      }}
    >
      <span className="chat-reply-quote-sender">{quote.sender || "Pesan"}</span>
      <span className="chat-reply-quote-text">{quote.text}</span>
    </button>
  );
}

export function MessageBubble({
  message,
  self,
  attachmentUrl,
  mobile,
  onEdit,
  onDelete,
  onReply,
  onJumpToReply,
  canModerate,
}) {
  const deleted = !!(message.deleted || message.deleted_at);
  const edited = !!(message.edited_at && !deleted);
  const replyMeta = message.reply_to || (message.reply_to_message_id
    ? { message_id: message.reply_to_message_id, unavailable: true }
    : null);
  return (
    <div
      className={`chat-bubble${self ? " is-self" : ""}${deleted ? " is-deleted" : ""}`}
      data-testid="chat-message"
      data-message-id={message.message_id || message.id}
    >
      {!self ? (
        <div className="chat-bubble-author">
          {memberDisplayName({ display_name: message.sender_name, user_id: message.sender_user_id })}
        </div>
      ) : null}
      {deleted ? (
        <div className="chat-bubble-deleted" data-testid="chat-message-deleted">Pesan dihapus</div>
      ) : (
        <>
          {replyMeta ? <ReplyQuote reply={replyMeta} onJump={onJumpToReply} /> : null}
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
        {!deleted ? (
          <span className="chat-bubble-actions">
            <button
              type="button"
              className="chat-btn chat-btn-ghost chat-reply-btn"
              data-testid="chat-reply-btn"
              onClick={() => onReply?.(message)}
            >
              Balas
            </button>
            {self ? (
              <>
                <button type="button" className="chat-btn chat-btn-ghost" data-testid="chat-edit-btn" onClick={() => onEdit?.(message)}>Edit</button>
                <button type="button" className="chat-btn chat-btn-danger" data-testid="chat-delete-btn" onClick={() => onDelete?.(message)}>Hapus</button>
              </>
            ) : null}
            {!self && canModerate ? (
              <button type="button" className="chat-btn chat-btn-danger" data-testid="chat-moderate-delete" onClick={() => onDelete?.(message)}>
                Moderasi hapus
              </button>
            ) : null}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function RoomListItem({ room, active, onSelect }) {
  const unread = Number(room.unread_count || 0);
  const title = room.display_name || room.name;
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
          <strong>{title}</strong>
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

export function MemberListItem({ member }) {
  const name = memberDisplayName(member);
  const role = memberRoleLabel(member);
  return (
    <li className="chat-member-row" data-testid="chat-member-row" data-user-id={member.user_id || member.id}>
      <span className="chat-member-avatar" aria-hidden>{roomInitials(name)}</span>
      <span>
        <div className="chat-member-name" data-testid="chat-member-name">{name}</div>
        {role ? <div className="chat-member-meta" data-testid="chat-member-role">{role}</div> : null}
      </span>
    </li>
  );
}

export function PendingFilePreview({ files, onRemove }) {
  if (!files?.length) return null;
  return (
    <div className="chat-pending-files" data-testid="chat-pending-files">
      {files.map((file, idx) => {
        const isImg = isImageMime(file.type);
        const canThumb = isImg && typeof URL !== "undefined" && typeof URL.createObjectURL === "function";
        const url = canThumb ? URL.createObjectURL(file) : null;
        return (
          <div key={`${file.name}-${file.size}-${idx}`} className="chat-pending-file" data-testid="chat-pending-file">
            {url ? (
              <img
                src={url}
                alt=""
                className="chat-pending-thumb"
                onLoad={(e) => {
                  try { URL.revokeObjectURL(e.currentTarget.src); } catch { /* ignore */ }
                }}
              />
            ) : (
              <span className="chat-pending-file-icon" aria-hidden>FILE</span>
            )}
            <span className="chat-pending-file-meta">
              <strong>{file.name || "lampiran"}</strong>
              <em>{file.type || "file"} · {formatBytes(file.size)}</em>
            </span>
            <button
              type="button"
              className="chat-btn chat-btn-ghost"
              data-testid="chat-pending-remove"
              aria-label="Hapus lampiran"
              onClick={() => onRemove?.(idx)}
            >
              ×
            </button>
          </div>
        );
      })}
    </div>
  );
}

export function ReplyComposerBar({ replyTo, onCancel }) {
  if (!replyTo) return null;
  const quote = replyQuotePreview(replyTo.reply_to || replyTo);
  return (
    <div className="chat-reply-bar" data-testid="chat-reply-bar">
      <div className="chat-reply-bar-body">
        <strong>Balas ke {quote.sender || "pesan"}</strong>
        <span>{quote.text}</span>
      </div>
      <button
        type="button"
        className="chat-btn chat-btn-ghost"
        data-testid="chat-reply-cancel"
        aria-label="Batalkan balasan"
        onClick={onCancel}
      >
        ×
      </button>
    </div>
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
  replyTo,
  onCancelReply,
  pendingFiles,
  onRemovePendingFile,
}) {
  const taRef = useRef(null);
  const pasteGuardRef = useRef("");

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const onPaste = (e) => {
    const files = extractClipboardImageFiles(e.clipboardData);
    if (!files.length) return;
    const stamp = `${files.map((f) => `${f.size}:${f.type}:${f.lastModified}`).join("|")}`;
    if (pasteGuardRef.current === stamp) return;
    pasteGuardRef.current = stamp;
    // Keep default text paste; only attach image draft(s).
    onPickFiles?.(files);
    window.setTimeout(() => {
      if (pasteGuardRef.current === stamp) pasteGuardRef.current = "";
    }, 400);
  };

  return (
    <div className="chat-composer" data-testid="chat-composer">
      <ReplyComposerBar replyTo={replyTo} onCancel={onCancelReply} />
      <PendingFilePreview files={pendingFiles} onRemove={onRemovePendingFile} />
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
        <button type="button" className="chat-btn chat-btn-secondary" data-testid="chat-tag-work-btn" onClick={onTagWork}>
          Tag Pekerjaan
        </button>
        <button type="button" className="chat-btn chat-btn-secondary" data-testid="chat-attach-btn" onClick={() => fileInputRef.current?.click()}>
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
        onPaste={onPaste}
        rows={2}
        placeholder="Tulis pesan… (Enter kirim, Shift+Enter baris baru)"
        data-testid="chat-composer-input"
      />
      <button
        type="button"
        className="chat-btn chat-btn-primary chat-send-btn"
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
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

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
    setLoading(false);
    setSearched(false);
    setReloadKey(0);
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setLoading(true);
    setError("");
    const t = setTimeout(async () => {
      try {
        const res = await searchOrders(q);
        if (cancelled) return;
        const list = Array.isArray(res?.items)
          ? res.items
          : Array.isArray(res?.orders)
            ? res.orders
            : Array.isArray(res)
              ? res
              : [];
        setOrders(list);
        setSearched(true);
      } catch (err) {
        if (cancelled) return;
        setOrders([]);
        setSearched(true);
        setError(err?.response?.data?.detail || "Gagal mencari PO");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [q, open, searchOrders, reloadKey]);

  if (!open) return null;

  const poLabel = order ? (order.po_number || order.order_number || order.id) : "";
  const customerLabel = order ? (order.customer_name || order.customer || "") : "";
  const lineStep = !!(order && !line && !task);

  const emptyHint = (() => {
    if (loading) return null;
    if (error) return null;
    if (!searched) return null;
    if (orders.length) return null;
    if (String(q || "").trim()) {
      return "Tidak ada PO/pekerjaan yang cocok.";
    }
    return "Belum ada PO/pekerjaan yang dapat diakses. Ketik nomor PO untuk mencari.";
  })();

  return (
    <div className="modal" data-testid="chat-work-picker">
      <div className="modal-inner chat-work-picker-inner">
        <div className="modal-head">
          <strong>Tag / Chat Pekerjaan</strong>
          <button type="button" className="chat-btn chat-btn-ghost" data-testid="chat-work-close" onClick={onClose}>
            Tutup
          </button>
        </div>

        <div className="chat-work-picker-body">
          {!order ? (
            <>
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Cari PO…"
                data-testid="chat-work-po-search"
              />
              {loading ? (
                <div className="chat-picker-status" data-testid="chat-work-loading">Memuat PO…</div>
              ) : null}
              {emptyHint ? (
                <div className="chat-picker-status" data-testid="chat-work-empty">{emptyHint}</div>
              ) : null}
              <ul className="chat-picker-list" data-testid="chat-work-po-list">
                {orders.map((o) => (
                  <li key={o.id || o.order_id}>
                    <button
                      type="button"
                      className="chat-picker-row"
                      onClick={async () => {
                        setOrder(o);
                        const lr = await loadLines(o.id || o.order_id);
                        setLines(lr?.items || lr || []);
                      }}
                    >
                      <strong>
                        {o.po_number || o.order_number}{" "}
                        <PoIdentityBadges order={o} testIdPrefix={`chat-work-po-${o.po_number || o.id}`} />
                      </strong>
                      <span>{o.customer_name || o.customer || ""}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : lineStep ? (
            <>
              <div className="chat-work-picker-context" data-testid="chat-work-context">
                PO {poLabel}{customerLabel ? ` · ${customerLabel}` : ""} — pilih line (opsional) atau lanjut PO saja.
              </div>
              <ul className="chat-picker-list" data-testid="chat-work-line-list">
                {lines.map((li) => (
                  <li key={`${li.line_id || "idx"}-${li.line_idx}`}>
                    <button
                      type="button"
                      className="chat-picker-row"
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
            </>
          ) : (
            <>
              <div className="chat-work-picker-context">
                PO {poLabel}
                {line && !line.__po_task ? ` · ${line.name || line.item_name || `line ${line.line_idx}`}` : ""}
                {" — pilih task/stage (opsional)."}
              </div>
              <ul className="chat-picker-list" data-testid="chat-work-task-list">
                {tasks.map((tk) => {
                  const selected = task?.task_id === tk.task_id;
                  return (
                    <li key={tk.task_id}>
                      <button
                        type="button"
                        className={`chat-picker-row${selected ? " is-selected" : ""}`}
                        data-testid="chat-work-task-option"
                        aria-pressed={selected}
                        onClick={() => setTask(tk)}
                      >
                        <strong>{tk.task_label || tk.division || tk.task_id}</strong>
                        <span>{tk.division || tk.task_id}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          {error ? (
            <div className="chat-error" data-testid="chat-work-error">
              {error}
              <button
                type="button"
                className="chat-btn chat-btn-secondary"
                data-testid="chat-work-retry"
                onClick={() => {
                  setSearched(false);
                  setError("");
                  setReloadKey((k) => k + 1);
                }}
              >
                Coba lagi
              </button>
            </div>
          ) : null}
        </div>

        {order ? (
          <div className="chat-work-picker-foot" data-testid="chat-work-picker-actions">
            {lineStep ? (
              <>
                <button
                  type="button"
                  className="chat-btn chat-btn-primary"
                  data-testid="chat-work-po-only"
                  onClick={() => onConfirm({
                    order_id: order.id || order.order_id,
                    po_number: order.po_number || order.order_number,
                    customer_name: order.customer_name || order.customer,
                  })}
                >
                  Gunakan PO saja
                </button>
                <button
                  type="button"
                  className="chat-btn chat-btn-secondary"
                  data-testid="chat-work-pick-task-po"
                  onClick={async () => {
                    const tr = await loadTasks(order.id || order.order_id);
                    setTasks(tr?.items || tr || []);
                    setLine({ __po_task: true });
                  }}
                >
                  Pilih task PO
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="chat-btn chat-btn-primary"
                  data-testid="chat-work-confirm"
                  onClick={() => onConfirm({
                    order_id: order.id || order.order_id,
                    line_id: line?.line_id || undefined,
                    line_idx: line?.__po_task ? undefined : line?.line_idx,
                    task_id: task?.task_id || undefined,
                    po_number: order.po_number || order.order_number,
                    customer_name: order.customer_name || order.customer,
                    item_name: line?.__po_task ? undefined : (line?.name || line?.item_name),
                  })}
                >
                  Pakai konteks ini
                </button>
                <button type="button" className="chat-btn chat-btn-ghost" onClick={onClose}>
                  Batal
                </button>
              </>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function EditMembersModal({
  open,
  onClose,
  members,
  searchUsers,
  onSave,
}) {
  const [q, setQ] = useState("");
  const [users, setUsers] = useState([]);
  const [selected, setSelected] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setQ("");
    setUsers([]);
    setError("");
    setSaving(false);
    setSelected(
      (members || [])
        .map((m) => String(m.user_id || m.id || "").trim())
        .filter(Boolean),
    );
  }, [open, members]);

  useEffect(() => {
    if (!open) return undefined;
    const t = setTimeout(async () => {
      try {
        const res = await searchUsers(q);
        setUsers(res?.items || res || []);
      } catch (err) {
        setError(err?.response?.data?.detail || "Gagal mencari user");
      }
    }, 200);
    return () => clearTimeout(t);
  }, [q, open, searchUsers]);

  if (!open) return null;

  const memberIds = new Set(
    (members || []).map((m) => String(m.user_id || m.id || "").trim()).filter(Boolean),
  );
  const directory = [...(members || [])];
  for (const u of users) {
    const id = String(u.id || u.user_id || "").trim();
    if (!id || directory.some((m) => String(m.user_id || m.id) === id)) continue;
    directory.push({
      user_id: id,
      display_name: u.name || u.display_name || u.email,
      name: u.name,
      email: u.email,
      role: u.role,
    });
  }

  const toggle = (id) => {
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  };

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const next = new Set(selected);
      const addIds = [...next].filter((id) => !memberIds.has(id));
      const removeIds = [...memberIds].filter((id) => !next.has(id));
      await onSave({ addIds, removeIds, selectedIds: [...next] });
      onClose();
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || "Gagal menyimpan anggota");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal" data-testid="chat-edit-members-modal">
      <div className="modal-inner chat-work-picker-inner">
        <div className="modal-head">
          <strong>Edit Anggota</strong>
          <button type="button" className="chat-btn chat-btn-ghost" data-testid="chat-edit-members-close" onClick={onClose}>
            Tutup
          </button>
        </div>
        <div className="chat-work-picker-body">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Cari user aktif…"
            data-testid="chat-edit-members-search"
          />
          <ul className="chat-picker-list" data-testid="chat-edit-members-list">
            {directory.map((u) => {
              const id = String(u.user_id || u.id || "").trim();
              const checked = selected.includes(id);
              const label = memberDisplayName(u);
              return (
                <li key={id}>
                  <button
                    type="button"
                    className={`chat-picker-row${checked ? " is-selected" : ""}`}
                    data-testid="chat-edit-member-option"
                    data-user-id={id}
                    aria-pressed={checked}
                    onClick={() => toggle(id)}
                  >
                    <strong data-testid="chat-edit-member-name">{label}</strong>
                    <span>{memberRoleLabel(u) || u.role || ""}</span>
                    <span>{checked ? "✓" : ""}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          {error ? <div className="chat-error" data-testid="chat-edit-members-error">{error}</div> : null}
        </div>
        <div className="chat-work-picker-foot">
          <button
            type="button"
            className="chat-btn chat-btn-primary"
            data-testid="chat-edit-members-save"
            disabled={saving}
            onClick={save}
          >
            {saving ? "Menyimpan…" : "Simpan Anggota"}
          </button>
          <button type="button" className="chat-btn chat-btn-ghost" onClick={onClose}>
            Batal
          </button>
        </div>
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
          <button type="button" className="chat-btn chat-btn-ghost" onClick={onClose}>Tutup</button>
        </div>
        <div className="chat-new-modes">
          <button type="button" className={`chat-btn${mode === "dm" ? " is-active chat-btn-primary" : " chat-btn-secondary"}`} onClick={() => setMode("dm")} data-testid="chat-new-dm">Personal</button>
          {canManageGroup ? (
            <button type="button" className={`chat-btn${mode === "group" ? " is-active chat-btn-primary" : " chat-btn-secondary"}`} onClick={() => setMode("group")} data-testid="chat-new-group">Grup Custom</button>
          ) : null}
          <button type="button" className={`chat-btn${mode === "work" ? " is-active chat-btn-primary" : " chat-btn-secondary"}`} onClick={() => setMode("work")} data-testid="chat-new-work">Chat Pekerjaan</button>
        </div>
        {mode === "work" ? (
          <button type="button" className="chat-btn chat-btn-primary" onClick={onCreateWork}>Pilih konteks pekerjaan…</button>
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
                className="chat-btn chat-btn-primary"
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
