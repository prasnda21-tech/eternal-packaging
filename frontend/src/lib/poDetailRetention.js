/**
 * Short-lived in-memory PO detail retention.
 *
 * Keeps history-tab and PWA back/forward navigation responsive without
 * persisting operational payloads to browser storage. Every consumer still
 * revalidates against the server immediately after showing the last-good copy.
 */
import { storageUserKey } from "./poListReturn";

const MAX_ENTRIES_PER_USER = 12;
const snapshots = new Map();

export const PO_DETAIL_VIEW_PERMISSIONS = [
  "po.view",
  "work_panel.view",
  "schedule.view",
  "screen.update",
  "helper.prepare",
  "operator.update",
  "packing.update",
  "delivery_note.view",
  "delivery_note.manage",
  "loading.update",
  "delivery.update",
  "design.view",
  "payment.view",
];

export function canRetainPoDetail(hasPerm) {
  return typeof hasPerm === "function"
    && PO_DETAIL_VIEW_PERMISSIONS.some((permission) => hasPerm(permission));
}

export function poDetailRetentionKey(user, orderId) {
  const userKey = storageUserKey(user);
  const id = String(orderId || "").trim();
  if (!id || !userKey || userKey === "anon") return "";
  return `${userKey}::${id}`;
}

function pruneOtherUsers(userKey) {
  const prefix = `${userKey}::`;
  for (const key of [...snapshots.keys()]) {
    if (!key.startsWith(prefix)) snapshots.delete(key);
  }
}

function trimUserEntries(userKey) {
  const prefix = `${userKey}::`;
  const keys = [...snapshots.keys()].filter((key) => key.startsWith(prefix));
  while (keys.length > MAX_ENTRIES_PER_USER) {
    snapshots.delete(keys.shift());
  }
}

export function readPoDetailRetention({ user, orderId, hasPerm } = {}) {
  if (!canRetainPoDetail(hasPerm)) return null;
  const key = poDetailRetentionKey(user, orderId);
  if (!key) return null;
  const userKey = storageUserKey(user);
  pruneOtherUsers(userKey);
  const order = snapshots.get(key);
  return order ? { ...order } : null;
}

export function writePoDetailRetention({ user, orderId, hasPerm, order } = {}) {
  if (!canRetainPoDetail(hasPerm) || !order || typeof order !== "object") return;
  const key = poDetailRetentionKey(user, orderId || order.id);
  if (!key) return;
  const userKey = storageUserKey(user);
  pruneOtherUsers(userKey);
  snapshots.delete(key);
  snapshots.set(key, { ...order });
  trimUserEntries(userKey);
}

export function clearPoDetailRetention() {
  snapshots.clear();
}
