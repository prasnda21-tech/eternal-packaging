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

export const CHAT_ACCEPT =
  "image/jpeg,image/png,image/webp,application/pdf,.doc,.docx,.xls,.xlsx";
