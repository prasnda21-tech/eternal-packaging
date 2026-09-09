import { normalizeWorkspacePath } from "./workspaceTabs";

/** Dynamic global workspace tab for a PO detail route. Identity is order_id. */
export const PO_DETAIL_TAB_PREFIX = "po-detail:";

export function poDetailOrderIdFromPath(pathname) {
  const path = normalizeWorkspacePath(pathname);
  const match = /^\/po\/([^/]+)$/.exec(path);
  if (!match) return null;
  try {
    const orderId = decodeURIComponent(match[1]).trim();
    return orderId || null;
  } catch {
    return null;
  }
}

export function compactPoWorkspaceLabel(poNumber, orderId) {
  const raw = String(poNumber || "").trim();
  if (raw) {
    const parts = raw.split("/").map((part) => part.trim()).filter(Boolean);
    const last = parts[parts.length - 1] || "";
    if (parts.length >= 2 && /^PO$/i.test(parts[0]) && /^\d+$/.test(last)) {
      return `PO ${last}`;
    }
    return raw;
  }
  const id = String(orderId || "").trim();
  return id ? `PO ${id}` : "PO";
}

export function poDetailWorkspaceTab(pathname, meta = {}) {
  const orderId = poDetailOrderIdFromPath(pathname);
  if (!orderId) return null;
  return {
    id: `${PO_DETAIL_TAB_PREFIX}${orderId}`,
    path: `/po/${orderId}`,
    label: compactPoWorkspaceLabel(meta.po_number, orderId),
    dynamic: true,
  };
}

export function hydratePoDetailStoredTab(entry) {
  if (typeof entry === "string" && entry.startsWith(PO_DETAIL_TAB_PREFIX)) {
    const orderId = entry.slice(PO_DETAIL_TAB_PREFIX.length).trim();
    return orderId ? poDetailWorkspaceTab(`/po/${orderId}`) : null;
  }
  if (!entry || typeof entry !== "object" || typeof entry.id !== "string") return null;
  if (!entry.id.startsWith(PO_DETAIL_TAB_PREFIX)) return null;
  const fromPath = poDetailWorkspaceTab(entry.path || `/po/${entry.id.slice(PO_DETAIL_TAB_PREFIX.length)}`);
  if (!fromPath) return null;
  const label = String(entry.label || "").trim();
  return label ? { ...fromPath, label } : fromPath;
}
