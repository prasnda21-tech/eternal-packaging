/** Pure helpers for the web floating notification bell (presentation only). */

export const POLL_INTERVAL_MS = 15000;
export const CLICK_DRAG_THRESHOLD_PX = 6;
export const BELL_SIZE_PX = 52;
export const VIEWPORT_PAD_PX = 12;
export const POS_STORAGE_PREFIX = "ep.webNotifBell.pos";

export function storageKeyForUser(userId) {
  const id = String(userId || "").trim();
  return id ? `${POS_STORAGE_PREFIX}.${id}` : `${POS_STORAGE_PREFIX}.anon`;
}

export function countUnread(notifications) {
  if (!Array.isArray(notifications)) return 0;
  return notifications.reduce((n, item) => {
    if (!item) return n;
    // Canonical client/unread source: read_at (API also exposes is_read = bool(read_at)).
    const unread = item.read_at == null || item.read_at === "";
    return unread ? n + 1 : n;
  }, 0);
}

export function defaultBellPosition(viewport = {}) {
  const vw = Number(viewport.width) || (typeof window !== "undefined" ? window.innerWidth : 1280);
  const vh = Number(viewport.height) || (typeof window !== "undefined" ? window.innerHeight : 800);
  return {
    x: Math.max(VIEWPORT_PAD_PX, vw - BELL_SIZE_PX - 24),
    y: Math.max(VIEWPORT_PAD_PX, vh - BELL_SIZE_PX - 24),
  };
}

export function clampBellPosition(pos, viewport = {}) {
  const vw = Number(viewport.width) || (typeof window !== "undefined" ? window.innerWidth : 1280);
  const vh = Number(viewport.height) || (typeof window !== "undefined" ? window.innerHeight : 800);
  const x = Number(pos?.x);
  const y = Number(pos?.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return defaultBellPosition({ width: vw, height: vh });
  const maxX = Math.max(VIEWPORT_PAD_PX, vw - BELL_SIZE_PX - VIEWPORT_PAD_PX);
  const maxY = Math.max(VIEWPORT_PAD_PX, vh - BELL_SIZE_PX - VIEWPORT_PAD_PX);
  return {
    x: Math.min(Math.max(VIEWPORT_PAD_PX, x), maxX),
    y: Math.min(Math.max(VIEWPORT_PAD_PX, y), maxY),
  };
}

export function readStoredBellPosition(storage, userId, viewport) {
  try {
    const raw = storage?.getItem?.(storageKeyForUser(userId));
    if (!raw) return defaultBellPosition(viewport);
    const parsed = JSON.parse(raw);
    return clampBellPosition(parsed, viewport);
  } catch (_) {
    return defaultBellPosition(viewport);
  }
}

export function writeStoredBellPosition(storage, userId, pos, viewport) {
  try {
    const clamped = clampBellPosition(pos, viewport);
    storage?.setItem?.(storageKeyForUser(userId), JSON.stringify(clamped));
    return clamped;
  } catch (_) {
    return clampBellPosition(pos, viewport);
  }
}

export function movementExceededThreshold(start, current, threshold = CLICK_DRAG_THRESHOLD_PX) {
  if (!start || !current) return false;
  const dx = Number(current.x) - Number(start.x);
  const dy = Number(current.y) - Number(start.y);
  return Math.hypot(dx, dy) >= threshold;
}

export function panelPlacement(bellPos, panelSize = {}, viewport = {}) {
  const vw = Number(viewport.width) || 1280;
  const vh = Number(viewport.height) || 800;
  const panelW = Number(panelSize.width) || 320;
  const panelH = Number(panelSize.height) || 380;
  const gap = 10;
  const bell = clampBellPosition(bellPos, { width: vw, height: vh });

  let left = bell.x + BELL_SIZE_PX - panelW;
  if (left < VIEWPORT_PAD_PX) left = bell.x;
  if (left + panelW > vw - VIEWPORT_PAD_PX) left = Math.max(VIEWPORT_PAD_PX, vw - panelW - VIEWPORT_PAD_PX);

  let top = bell.y - panelH - gap;
  if (top < VIEWPORT_PAD_PX) top = bell.y + BELL_SIZE_PX + gap;
  if (top + panelH > vh - VIEWPORT_PAD_PX) top = Math.max(VIEWPORT_PAD_PX, vh - panelH - VIEWPORT_PAD_PX);

  return { left, top };
}

export function decisionLabel(decision) {
  if (decision === "approved") return "Disetujui";
  if (decision === "rejected") return "Ditolak";
  return decision || "";
}
