/** Permission-driven mobile operational workspaces. Route/query never grants access. */

import { canAccessRoutePage } from "./independentRoute";
import { NAV_KEYS } from "./navAccess";
import { MOBILE_DELIVERY_SCHEDULE_PATH, MOBILE_DELIVERY_SCHEDULE_VIEW_PERMS } from "./mobileDeliverySchedule";

export const MOBILE_WORKSPACES = [
  { id: "screener", label: "Screener", permission: "screen.update", division: "screener" },
  { id: "prep", label: "Persiapan Cup", permission: "helper.prepare", division: "helper" },
  { id: "print", label: "Cetak", permission: "operator.update", division: "operator" },
  { id: "sample", label: "Upload Sampel Cetak", permission: "operator.sample_upload", division: "operator" },
  { id: "packing", label: "Packing", permission: "packing.update", division: "packing" },
  { id: "loading", label: "Loading", permission: "loading.update", division: "loading" },
  { id: "delivery", label: "Pengiriman", permission: "delivery.update", division: "terkirim" },
];

/** Nav visibility for Optimasi Rute — existing RBAC helper, never role-name hardcoding. */
export function canSeeMobileOptimasiRute(hasPerm) {
  return canAccessRoutePage(hasPerm);
}

export const MOBILE_NAV_ITEMS = [
  { id: "tasks", label: "Tugas Saya", to: "/m/tasks" },
  { id: "orders", label: "PO", to: "/m/orders" },
  {
    id: "schedule",
    label: "Jadwal Produksi",
    lines: ["Jadwal", "Produksi"],
    to: "/m/schedule",
    anyOf: ["schedule.view", NAV_KEYS.schedule],
  },
  {
    id: "deliverySchedule",
    label: "Jadwal Pengiriman",
    lines: ["Jadwal", "Pengiriman"],
    to: MOBILE_DELIVERY_SCHEDULE_PATH,
    anyOf: [...MOBILE_DELIVERY_SCHEDULE_VIEW_PERMS],
  },
  {
    id: "optimasiRute",
    label: "Optimasi Rute",
    lines: ["Optimasi", "Rute"],
    to: "/optimasi-rute",
    // Predicate uses canAccessRoutePage (nav.route_optimizer + route_optimizer.*).
    visibleWhen: canSeeMobileOptimasiRute,
  },
  { id: "chat", label: "Chat", to: "/m/chat", anyOf: [NAV_KEYS.chat, "chat.use"] },
  { id: "notifications", label: "Notifikasi", to: "/m/notifications" },
  { id: "profile", label: "Profil", to: "/m/profile" },
];

export function permittedMobileWorkspaces(hasPerm) {
  if (typeof hasPerm !== "function") return [];
  return MOBILE_WORKSPACES.filter((ws) => hasPerm(ws.permission));
}

export function visibleMobileNavItems(hasPerm) {
  return MOBILE_NAV_ITEMS.filter((item) => {
    if (typeof item.visibleWhen === "function") {
      return item.visibleWhen(hasPerm);
    }
    if (!item.anyOf?.length) return true;
    if (typeof hasPerm !== "function") return false;
    return item.anyOf.some((perm) => hasPerm(perm));
  });
}

export function selectMobileWorkspace(hasPerm, requestedId) {
  const permitted = permittedMobileWorkspaces(hasPerm);
  if (!permitted.length) {
    return { permitted, selected: null, unauthorized: Boolean(requestedId) };
  }
  const match = permitted.find((ws) => ws.id === requestedId);
  if (match) return { permitted, selected: match, unauthorized: false };
  return { permitted, selected: permitted[0], unauthorized: Boolean(requestedId) };
}

export function mobileTaskHref(orderId, workspaceId) {
  if (!orderId) return "/m/orders";
  const base = `/m/orders/${encodeURIComponent(orderId)}`;
  if (!workspaceId) return base;
  return `${base}?ws=${encodeURIComponent(workspaceId)}`;
}

/**
 * Task contexts selectable on Jadwal Pengiriman (presentation only).
 * Same packing → loading → delivery order as preferredMobileWorkspaceForTable("delivery").
 * Prefer workspace for schedule row click — permission-based, never role-name.
 */
export const DELIVERY_SCHEDULE_TASK_IDS = ["packing", "loading", "delivery"];

export function permittedDeliveryScheduleTaskContexts(hasPerm) {
  const allowed = new Set(DELIVERY_SCHEDULE_TASK_IDS);
  return permittedMobileWorkspaces(hasPerm).filter((ws) => allowed.has(ws.id));
}

/**
 * Resolve Jadwal Pengiriman task-context from ?ws=.
 * Unauthorized/missing query never grants access — falls back without mutating auth.
 * Default prefers Pengiriman (schedule page identity) when eligible; else first permitted.
 */
export function selectDeliveryScheduleTaskContext(hasPerm, requestedId) {
  const permitted = permittedDeliveryScheduleTaskContexts(hasPerm);
  if (!permitted.length) {
    return { permitted, selected: null, unauthorized: Boolean(requestedId) };
  }
  const match = permitted.find((ws) => ws.id === requestedId);
  if (match) return { permitted, selected: match, unauthorized: false };
  const fallback = permitted.find((ws) => ws.id === "delivery") || permitted[0];
  return { permitted, selected: fallback, unauthorized: Boolean(requestedId) };
}

/** Packing, Loading, and Pengiriman all land on the canonical Jadwal Pengiriman table. */
export function deliveryScheduleShowsTable(workspaceId) {
  return workspaceId == null
    || workspaceId === "delivery"
    || workspaceId === "packing"
    || workspaceId === "loading";
}

export function mobileLoadingScheduleHref() {
  return `${MOBILE_DELIVERY_SCHEDULE_PATH}?ws=loading`;
}

export function preferredMobileWorkspaceForTable(hasPerm, table) {
  const permitted = permittedMobileWorkspaces(hasPerm);
  if (!permitted.length) return null;
  const order = table === "delivery"
    ? DELIVERY_SCHEDULE_TASK_IDS
    : ["screener", "prep", "print", "sample"];
  for (const id of order) {
    const match = permitted.find((ws) => ws.id === id);
    if (match) return match;
  }
  return permitted[0];
}

export function mobileScheduleWorkHref(orderId, hasPerm, table, workspaceId, row = null) {
  const href = workspaceId
    ? mobileTaskHref(orderId, workspaceId)
    : mobileTaskHref(orderId, preferredMobileWorkspaceForTable(hasPerm, table)?.id);
  if ((workspaceId === "loading" || workspaceId === "delivery") && row?.shipment_id) {
    const join = href.includes("?") ? "&" : "?";
    return `${href}${join}shipment=${encodeURIComponent(row.shipment_id)}`;
  }
  return href;
}

/** Pick the SJ already identified on the schedule row; never invent or match by item name. */
export function matchLoadingSjFile(sjFiles, shipmentId) {
  const list = Array.isArray(sjFiles) ? sjFiles : [];
  if (!shipmentId) return list[0] || null;
  const wanted = String(shipmentId);
  return list.find((f) => (
    String(f?.id || "") === wanted
    || String(f?.file_id || "") === wanted
    || String(f?.shipment_id || "") === wanted
  )) || list[0] || null;
}

export function activeMobileNav(pathname) {
  const path = String(pathname || "");
  if (path.startsWith("/m/orders")) return "orders";
  if (path.startsWith(MOBILE_DELIVERY_SCHEDULE_PATH) || path.startsWith("/m/delivery-schedule")) {
    return "deliverySchedule";
  }
  if (path.startsWith("/optimasi-rute")) return "optimasiRute";
  if (path.startsWith("/m/schedule")) return "schedule";
  if (path.startsWith("/m/chat")) return "chat";
  if (path.startsWith("/m/notifications")) return "notifications";
  if (path.startsWith("/m/profile")) return "profile";
  if (path === "/m" || path.startsWith("/m/tasks")) return "tasks";
  return null;
}

export function mobileWorkStatus(done, target) {
  const d = Number(done || 0);
  const t = Number(target || 0);
  if (d <= 0) return { key: "idle", label: "Belum Mulai" };
  if (t > 0 && d >= t) return { key: "done", label: "Selesai" };
  return { key: "process", label: "Proses" };
}

export { taskSummaryFromList } from "./mobileTaskSummary";

export function poLineSummaries(order) {
  return (order?.line_items || []).map((li, idx) => ({
    line_idx: idx,
    name: li?.name || `Line ${idx + 1}`,
    color: li?.color || "",
    qty: Number(li?.quantity || 0),
  }));
}

export function poCardPresentation(order) {
  const lines = poLineSummaries(order);
  if (lines.length > 1) {
    return {
      product: `${lines[0].name} · +${lines.length - 1} line`,
      qty: null,
      lines,
    };
  }
  if (lines.length === 1) {
    return { product: lines[0].name, qty: lines[0].qty, lines };
  }
  const fallbackName = order?.item || order?.product_name || "—";
  const fallbackQty = order?.item != null || order?.product_name != null
    ? Number(order?.total_quantity || 0) || null
    : null;
  return { product: fallbackName, qty: fallbackQty, lines };
}

export function packingMetrics(order, packingLines) {
  const rows = Array.isArray(packingLines) ? packingLines : [];
  return {
    target: rows.reduce((s, r) => s + Number(r.qty_po || r.target || 0), 0),
    printed: rows.reduce((s, r) => s + Number(r.print_completed || 0), 0)
      || Number(order?.print_completed_qty || 0),
    packed: rows.reduce((s, r) => s + Number(r.packed || 0), 0),
    remaining: rows.reduce((s, r) => s + Number(r.remaining || 0), 0),
    lines: rows.map((row) => ({
      line_idx: row.line_idx,
      name: row.name,
      color: row.color || "",
      qty_po: Number(row.qty_po || 0),
      print_completed: Number(row.print_completed || 0),
      packed: Number(row.packed || 0),
      remaining: Number(row.remaining || 0),
      available: Number(row.available || 0),
    })),
  };
}

export function loadingAvailability(order, poLines, sjFiles) {
  const files = Array.isArray(sjFiles) ? sjFiles : [];
  const lines = Array.isArray(poLines) ? poLines : [];
  const packed = lines.reduce((s, l) => s + Number(l.packed || 0), 0);
  const loaded = lines.reduce((s, l) => s + Number(l.loaded || 0), 0);
  const remaining = lines.reduce((s, l) => s + Number(l.available ?? l.physical_available ?? 0), 0);
  const sjReady = files.length > 0 || !!order?.surat_jalan_created;
  if (!sjReady) {
    return {
      packed,
      loaded,
      remaining,
      blocked: true,
      reason: "Surat Jalan belum tersedia.",
    };
  }
  if (packed <= 0) {
    return {
      packed,
      loaded,
      remaining,
      blocked: true,
      reason: "Loading tidak dapat dilakukan karena belum ada qty Packing.",
    };
  }
  return { packed, loaded, remaining, blocked: false, reason: "" };
}

export function mapsSearchUrl(address) {
  const text = String(address || "").trim();
  if (!text) return null;
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(text)}`;
}

export function deliveryAddressFromOrder(order) {
  return String(
    order?.address
    || order?.customer_address
    || order?.delivery_address
    || order?.formatted_address
    || "",
  ).trim();
}
