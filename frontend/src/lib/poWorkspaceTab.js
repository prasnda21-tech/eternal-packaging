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

/** Visible label priority: customer (2) > compact po_number (1) > PO order_id (0). */
export function poDetailTabLabelPriority(meta, orderId) {
  const info = meta && typeof meta === "object" ? meta : {};
  if (String(info.customer || "").trim()) return 2;
  if (String(info.po_number || "").trim()) return 1;
  return String(orderId || "").trim() ? 0 : 0;
}

export function inferPoDetailStoredLabelPriority(label, orderId) {
  const text = String(label || "").trim();
  if (!text) return 0;
  const bare = compactPoWorkspaceLabel("", orderId);
  if (text === bare || text === "PO") return 0;
  // Compact / full PO serials stay below customer names.
  if (/^PO(\s|$)/i.test(text)) return 1;
  return 2;
}

export function poDetailWorkspaceLabel(meta, orderId) {
  const info = meta && typeof meta === "object" ? meta : {};
  const customer = String(info.customer || "").trim();
  if (customer) return customer;
  return compactPoWorkspaceLabel(info.po_number, orderId);
}

export function poDetailWorkspaceTitle(meta, orderId) {
  const info = meta && typeof meta === "object" ? meta : {};
  const customer = String(info.customer || "").trim();
  const poNumber = String(info.po_number || "").trim();
  if (customer && poNumber) return `${customer} — ${poNumber}`;
  if (customer) return customer;
  return compactPoWorkspaceLabel(poNumber, orderId);
}

export function poDetailWorkspaceTab(pathname, meta) {
  const orderId = poDetailOrderIdFromPath(pathname);
  if (!orderId) return null;
  // React Router sets location.state to null (not undefined) on tab/direct
  // navigation. Default-param `meta = {}` does not apply to null and would
  // throw, white-screening AppShell when reopening a PO workspace tab.
  const info = meta && typeof meta === "object" ? meta : {};
  return {
    id: `${PO_DETAIL_TAB_PREFIX}${orderId}`,
    path: `/po/${orderId}`,
    label: poDetailWorkspaceLabel(info, orderId),
    title: poDetailWorkspaceTitle(info, orderId),
    labelPriority: poDetailTabLabelPriority(info, orderId),
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
  const orderId = entry.id.slice(PO_DETAIL_TAB_PREFIX.length).trim();
  const fromPath = poDetailWorkspaceTab(entry.path || `/po/${orderId}`);
  if (!fromPath) return null;
  const label = String(entry.label || "").trim();
  const title = String(entry.title || "").trim();
  const storedPriority = Number.isFinite(entry.labelPriority) ? entry.labelPriority : null;
  const labelPriority = storedPriority != null
    ? storedPriority
    : (label ? inferPoDetailStoredLabelPriority(label, orderId) : fromPath.labelPriority);
  return {
    ...fromPath,
    ...(label ? { label } : {}),
    ...(title ? { title } : {}),
    labelPriority,
  };
}

/** Upgrade only when incoming meta yields a richer (or equal non-fallback) label. */
export function shouldUpgradePoDetailTabLabel(existing, entry, meta) {
  if (!entry?.dynamic || !entry.label || entry.label === existing?.label) return false;
  const info = meta && typeof meta === "object" ? meta : null;
  if (!info) return false;
  const hasCustomer = Boolean(String(info.customer || "").trim());
  const hasPoNumber = Boolean(String(info.po_number || "").trim());
  if (!hasCustomer && !hasPoNumber) return false;
  const newPri = entry.labelPriority ?? 0;
  const oldPri = existing?.labelPriority
    ?? inferPoDetailStoredLabelPriority(existing?.label, existing?.id?.slice(PO_DETAIL_TAB_PREFIX.length));
  return newPri > oldPri || (newPri === oldPri && newPri > 0);
}
