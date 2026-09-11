/** Pure helpers for chat UI rendering (XSS-safe — never HTML). */

export function newClientMessageId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `cm-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function formatChatTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) {
    return d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleString("id-ID", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function roomTypeFilter(filter, room) {
  const t = room?.room_type || room?.type;
  if (!filter || filter === "all" || filter === "Semua") return true;
  if (filter === "personal" || filter === "Personal") return t === "dm";
  if (filter === "group" || filter === "Grup") return t === "system_group" || t === "custom_group";
  if (filter === "work" || filter === "Pekerjaan") return t === "work";
  return true;
}

export function roomInitials(name) {
  const parts = String(name || "?").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
}

export function previewText(message) {
  if (!message) return "";
  if (message.deleted || message.deleted_at) return "Pesan dihapus";
  const text = String(message.content || "").trim();
  if (text) return text.length > 80 ? `${text.slice(0, 80)}…` : text;
  if ((message.attachments || message.attachment_ids || []).length) return "Lampiran";
  if ((message.work_refs || []).length) return "Tag pekerjaan";
  return "";
}

export function isImageMime(ct) {
  return String(ct || "").toLowerCase().startsWith("image/");
}

export function formatBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / (1024 * 1024)).toFixed(1)} MB`;
}

export function canEditMessage(message, now = Date.now()) {
  if (!message?.created_at || message.deleted || message.deleted_at) return false;
  const created = new Date(message.created_at).getTime();
  if (Number.isNaN(created)) return false;
  return now - created <= 15 * 60 * 1000;
}

/** Human-readable member label. Never prefer raw user_id/UUID. */
export function memberDisplayName(member) {
  const candidates = [
    member?.display_name,
    member?.name,
    member?.user_name,
    member?.email,
    member?.user_email,
  ];
  for (const c of candidates) {
    const s = String(c || "").trim();
    if (!s) continue;
    if (looksLikeInternalId(s)) continue;
    return s;
  }
  return "Pengguna";
}

export function memberRoleLabel(member) {
  return String(member?.role || member?.user_role || "").trim();
}

export function looksLikeInternalId(value) {
  const s = String(value || "").trim();
  if (!s) return false;
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(s)) return true;
  if (/^u-[a-z0-9-]+$/i.test(s)) return true;
  return false;
}

/** Presentation title for room list/header. DM generic titles resolve via peer member. */
export function roomDisplayTitle(room, { members = [], meId } = {}) {
  const type = room?.room_type || room?.type;
  const raw = String(room?.name || room?.title || "").trim();
  if (type === "dm") {
    const generic = !raw || raw === "Chat" || raw === "DM" || looksLikeInternalId(raw);
    if (generic) {
      const peer = (members || []).find((m) => String(m.user_id || m.id) !== String(meId));
      const peerName = memberDisplayName(peer);
      if (peerName && peerName !== "Pengguna") return peerName;
    }
  }
  return raw || "Chat";
}

export const CHAT_ACCEPT =
  "image/jpeg,image/png,image/webp,application/pdf,.doc,.docx,.xls,.xlsx";

/** Image MIME types accepted for chat paste/upload (subset of CHAT_ACCEPT). */
export const CHAT_IMAGE_MIMES = new Set(["image/jpeg", "image/png", "image/webp"]);

export function isAcceptedChatImageMime(ct) {
  return CHAT_IMAGE_MIMES.has(String(ct || "").toLowerCase());
}

/**
 * Extract image File objects from a ClipboardEvent's clipboardData.
 * Does not touch text items — callers should not preventDefault when only
 * attaching images alongside normal text paste.
 */
export function extractClipboardImageFiles(clipboardData) {
  const items = clipboardData?.items;
  if (!items || !items.length) return [];
  const files = [];
  const seen = new Set();
  for (let i = 0; i < items.length; i += 1) {
    const item = items[i];
    if (!item || item.kind !== "file") continue;
    const type = String(item.type || "").toLowerCase();
    if (!isAcceptedChatImageMime(type)) continue;
    const file = item.getAsFile?.();
    if (!file) continue;
    const key = `${file.type}|${file.size}|${file.lastModified}|${file.name || ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const ext = type === "image/jpeg" ? "jpg" : type === "image/webp" ? "webp" : "png";
    const rawName = String(file.name || "").trim();
    const name = rawName && rawName !== "image.png" && rawName !== "image.jpg"
      ? rawName
      : `clipboard-${Date.now()}-${files.length + 1}.${ext}`;
    files.push(rawName === name ? file : new File([file], name, { type: file.type, lastModified: file.lastModified || Date.now() }));
  }
  return files;
}

/** Compact quote line for reply composer / bubble. */
export function replyQuotePreview(messageOrReply) {
  if (!messageOrReply) return { sender: "", text: "Pesan tidak tersedia", unavailable: true };
  if (messageOrReply.unavailable || messageOrReply.deleted || messageOrReply.deleted_at) {
    return {
      sender: memberDisplayName({ display_name: messageOrReply.sender_name, name: messageOrReply.sender_name }),
      text: "Pesan tidak tersedia",
      unavailable: true,
    };
  }
  const sender = memberDisplayName({
    display_name: messageOrReply.sender_name,
    name: messageOrReply.sender_name,
  });
  if (messageOrReply.content_preview != null) {
    return { sender, text: messageOrReply.content_preview || (messageOrReply.has_image ? "[Gambar]" : ""), unavailable: false };
  }
  const text = previewText(messageOrReply);
  const fallback = text || (messageOrReply.has_image ? "[Gambar]" : "");
  return { sender, text: fallback || "—", unavailable: false };
}
