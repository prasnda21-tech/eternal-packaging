/**
 * Desktop Global Menu Search — pure helpers.
 * Catalog = permission-filtered buildDesktopSidebar → sidebarLeaves only.
 */

import { sidebarLeaves } from "./sidebarPresentation";

export const MENU_SEARCH_MAX_RESULTS = 20;

/** Rail id → display group (same copy as AppShell RAIL_LABELS). */
export const MENU_SEARCH_GROUP_LABELS = Object.freeze({
  dashboard: "Dashboard",
  spk: "SPK",
  chat: "Chat",
  sales: "Penjualan",
  produksi: "Produksi",
  pengiriman: "Pengiriman",
  purchasing: "Pembelian",
  inventory: "Persediaan",
  keuangan: "Keuangan",
  employeeAdmin: "Administrasi Karyawan",
  hrPayroll: "HR & Payroll",
  reports: "Laporan",
  settings: "Pengaturan",
});

export function normalizeMenuSearchQuery(raw) {
  return String(raw ?? "").trim().toLowerCase();
}

/** True when Ctrl/Cmd+K must not open the palette (editing ordinary text). */
export function isMenuSearchShortcutBlockedTarget(target) {
  if (!target || typeof target !== "object") return false;
  const el = /** @type {HTMLElement} */ (target);
  const tag = String(el.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  const role = String(el.getAttribute?.("role") || "").toLowerCase();
  if (role === "textbox" || role === "searchbox" || role === "combobox") return true;
  return false;
}

/**
 * Flatten authorized desktop rails into searchable leaves.
 * Drops parents / items without a navigable `to`.
 */
export function menuSearchItemsFromRails(rails, groupLabels = MENU_SEARCH_GROUP_LABELS) {
  return sidebarLeaves(rails)
    .filter((leaf) => leaf && String(leaf.to || "").trim() && String(leaf.label || "").trim())
    .map((leaf) => {
      const group = leaf.group || "";
      return {
        key: leaf.key,
        to: leaf.to,
        label: leaf.label,
        group,
        section: leaf.section || "",
        groupLabel: groupLabels[group] || group || "Menu",
      };
    });
}

function haystack(item) {
  return [
    item.label,
    item.groupLabel,
    item.section,
  ].filter(Boolean).join("\n").toLowerCase();
}

/**
 * Case-insensitive substring filter on label + group + section.
 * Empty query returns the first `max` items (browse).
 */
export function filterMenuSearchItems(items, query, max = MENU_SEARCH_MAX_RESULTS) {
  const list = Array.isArray(items) ? items : [];
  const limit = Math.max(0, Number(max) || MENU_SEARCH_MAX_RESULTS);
  const q = normalizeMenuSearchQuery(query);
  if (!q) return list.slice(0, limit);
  const matched = [];
  for (const item of list) {
    if (!item?.to) continue;
    if (haystack(item).includes(q)) {
      matched.push(item);
      if (matched.length >= limit) break;
    }
  }
  return matched;
}

export function clampMenuSearchHighlight(index, length) {
  const n = Number(length) || 0;
  if (n <= 0) return 0;
  const i = Number(index);
  if (!Number.isFinite(i)) return 0;
  if (i < 0) return n - 1;
  if (i >= n) return 0;
  return Math.floor(i);
}

/** True when key event is Ctrl+K or Meta+K (palette open shortcut). */
export function isMenuSearchOpenShortcut(event) {
  if (!event || event.defaultPrevented) return false;
  if (event.altKey || event.shiftKey) return false;
  const key = String(event.key || "").toLowerCase();
  if (key !== "k") return false;
  return Boolean(event.ctrlKey || event.metaKey);
}
