/** Pending Konfirmasi Terkirim ids for this browser tab.

Storage shape:
{
  version: 2,
  entries: {
    [JSON.stringify([userId, orderId, shipmentId, payloadToken])]: {
      actionId: string,
      unknown: boolean,
    },
  },
}

A legacy map of order|shipment → token is ignored. It is not a user-scoped
operation and must not be replayed.
*/

export const CONFIRM_ACTION_STORAGE = "pwa-delivery-confirm-action";
const STORAGE_VERSION = 2;
const memory = new Map();

export function resetConfirmActionStateForTests() {
  memory.clear();
}

export function confirmPayloadToken(recipientName, shipment) {
  const name = String(recipientName || "").trim().replace(/\s+/g, " ");
  const lines = Array.isArray(shipment?.lines) ? shipment.lines : [];
  const parts = lines.map((line) => {
    if (!line || typeof line !== "object") return ":";
    const idx = line.line_idx ?? "";
    const qty = line.qty ?? line.quantity ?? "";
    return `${idx}:${qty}`;
  });
  return `${name}|${parts.join(",")}`;
}

export function confirmActionKey(userId, orderId, shipmentId, payloadToken) {
  return JSON.stringify([
    String(userId || ""),
    String(orderId || ""),
    String(shipmentId || ""),
    String(payloadToken || ""),
  ]);
}

function emptyStore() {
  return { version: STORAGE_VERSION, entries: {} };
}

function readStore() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(CONFIRM_ACTION_STORAGE) || "");
    if (
      !parsed
      || parsed.version !== STORAGE_VERSION
      || !parsed.entries
      || typeof parsed.entries !== "object"
      || Array.isArray(parsed.entries)
    ) {
      return emptyStore();
    }
    return { version: STORAGE_VERSION, entries: { ...parsed.entries } };
  } catch {
    return emptyStore();
  }
}

function writeStore(store) {
  try {
    sessionStorage.setItem(CONFIRM_ACTION_STORAGE, JSON.stringify({
      version: STORAGE_VERSION,
      entries: store.entries,
    }));
  } catch {
    /* private mode keeps the in-memory id for this page load */
  }
}

function scopeOf(key) {
  try {
    const parsed = JSON.parse(key);
    if (!Array.isArray(parsed) || parsed.length < 4) return null;
    return {
      userId: String(parsed[0] || ""),
      orderId: String(parsed[1] || ""),
      shipmentId: String(parsed[2] || ""),
    };
  } catch {
    return null;
  }
}

function remember(key, entry) {
  const next = { actionId: String(entry.actionId), unknown: entry.unknown === true };
  memory.set(key, next);
  const store = readStore();
  store.entries[key] = next;
  writeStore(store);
  return next;
}

export function readConfirmAction(userId, orderId, shipmentId, payloadToken) {
  const key = confirmActionKey(userId, orderId, shipmentId, payloadToken);
  if (memory.has(key)) return { key, ...memory.get(key) };
  const stored = readStore().entries[key];
  if (!stored || !stored.actionId) return null;
  const entry = { actionId: String(stored.actionId), unknown: stored.unknown === true };
  memory.set(key, entry);
  return { key, ...entry };
}

export function storeConfirmAction(userId, orderId, shipmentId, payloadToken, actionId) {
  const key = confirmActionKey(userId, orderId, shipmentId, payloadToken);
  const existing = readConfirmAction(userId, orderId, shipmentId, payloadToken);
  if (existing) return existing;
  const entry = remember(key, { actionId, unknown: false });
  return { key, ...entry };
}

export function markConfirmActionUnknown(key) {
  const current = memory.get(key);
  if (!current) return null;
  return { key, ...remember(key, { ...current, unknown: true }) };
}

export function settleConfirmAction(key) {
  const current = memory.get(key);
  if (!current) return null;
  return { key, ...remember(key, { ...current, unknown: false }) };
}

export function clearConfirmAction(key) {
  if (!key) return;
  memory.delete(key);
  const store = readStore();
  if (!store.entries[key]) return;
  delete store.entries[key];
  writeStore(store);
}

export function unknownConfirmActionKeys(userId, orderId, shipmentId, exceptKey = "") {
  const store = readStore();
  const keys = new Set([...memory.keys(), ...Object.keys(store.entries)]);
  const wantUser = String(userId || "");
  const wantOrder = String(orderId || "");
  const wantShipment = String(shipmentId || "");
  const found = [];
  for (const key of keys) {
    if (key === exceptKey) continue;
    const scope = scopeOf(key);
    if (!scope) continue;
    if (scope.userId !== wantUser || scope.orderId !== wantOrder || scope.shipmentId !== wantShipment) continue;
    const entry = memory.get(key) || store.entries[key];
    if (entry && entry.unknown === true && entry.actionId) found.push(key);
  }
  return found;
}
