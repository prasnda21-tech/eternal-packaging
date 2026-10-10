/**
 * Shared five-tab list contracts for Pesanan / Faktur / Penerimaan Penjualan.
 * Tab classification and counts come from the backend list_tab response.
 */

export const SALES_LIST_TABS = Object.freeze([
  { id: "all", label: "Semua", testIdSuffix: "semua" },
  { id: "active", label: "Aktif", testIdSuffix: "aktif" },
  { id: "completed", label: "Selesai", testIdSuffix: "selesai" },
  { id: "pending", label: "Pending", testIdSuffix: "pending" },
  { id: "cancelled", label: "Batal", testIdSuffix: "batal" },
]);

export const SALES_LIST_TAB_IDS = Object.freeze(SALES_LIST_TABS.map((t) => t.id));

export const SALES_LIST_DEFAULT_PAGE_SIZE = 50;
export const SALES_LIST_PAGE_SIZES = Object.freeze([10, 20, 50, 100]);

export function normalizeSalesListTab(raw, { defaultTab = "active" } = {}) {
  const value = String(raw || "").trim().toLowerCase();
  if (SALES_LIST_TAB_IDS.includes(value)) return value;
  if (value === "semua" || value === "gabungan") return "all";
  if (value === "aktif") return "active";
  if (value === "selesai" || value === "histori") return "completed";
  if (value === "batal" || value === "dibatalkan") return "cancelled";
  return SALES_LIST_TAB_IDS.includes(defaultTab) ? defaultTab : "active";
}

export function salesListTabLabel(tab) {
  const id = normalizeSalesListTab(tab);
  return SALES_LIST_TABS.find((row) => row.id === id)?.label || "Aktif";
}

/** Build list query params. Always sends exclusive list_tab (never FE-classifies). */
export function salesFiveTabListParams({
  listTab,
  page = 1,
  pageSize = SALES_LIST_DEFAULT_PAGE_SIZE,
  q,
  extra = {},
} = {}) {
  const params = {
    list_tab: normalizeSalesListTab(listTab),
    page: Math.max(1, Number(page) || 1),
    page_size: Math.max(1, Number(pageSize) || SALES_LIST_DEFAULT_PAGE_SIZE),
  };
  const query = String(q || "").trim();
  if (query) params.q = query;
  Object.entries(extra || {}).forEach(([key, value]) => {
    if (value == null || value === "") return;
    if (Array.isArray(value) && value.length === 0) return;
    params[key] = value;
  });
  return params;
}

/**
 * Exact backend tab counts. Prefer exclusive five-tab fields when present.
 * Invoice/receipt `active_total` is legacy non-Batal — use five_tab_active_total for Aktif.
 */
export function salesFiveTabCountsFromResponse(res = {}) {
  const num = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };
  const activeExclusive = res.five_tab_active_total != null
    ? num(res.five_tab_active_total)
    : num(res.active_total);
  return {
    all: num(res.all_total),
    active: activeExclusive,
    completed: num(res.completed_total),
    pending: num(res.pending_total),
    cancelled: num(res.cancelled_total),
  };
}

export function salesFiveTabListMetaFromResponse(res = {}, { rowCount = 0 } = {}) {
  const counts = salesFiveTabCountsFromResponse(res);
  const listTab = normalizeSalesListTab(res.list_tab, { defaultTab: "active" });
  const total = res.total == null ? rowCount : Number(res.total) || 0;
  const tabTotal = res.tab_total == null
    ? (listTab === "all" ? counts.all : counts[listTab] || total)
    : Number(res.tab_total) || 0;
  return {
    total,
    tabTotal,
    capped: Boolean(res.capped),
    listTab,
    page: Number(res.page) || 1,
    pageSize: Number(res.page_size) || SALES_LIST_DEFAULT_PAGE_SIZE,
    counts,
  };
}

export function salesFiveTabRangeLabel({
  total,
  page,
  pageSize,
  tabLabel,
  noun = "dokumen",
  filtered = false,
  capped = false,
} = {}) {
  const safeTotal = Number(total) || 0;
  const size = Number(pageSize) || SALES_LIST_DEFAULT_PAGE_SIZE;
  const current = Number(page) || 1;
  const fmt = (value) => Number(value || 0).toLocaleString("id-ID");
  const start = safeTotal === 0 ? 0 : (current - 1) * size + 1;
  const end = Math.min(current * size, safeTotal);
  const base = safeTotal <= 0
    ? `Menampilkan 0 ${noun} pada tab ${tabLabel}`
    : `Menampilkan ${fmt(start)}–${fmt(end)} dari ${fmt(safeTotal)} ${noun} pada tab ${tabLabel}`;
  const parts = [base];
  if (filtered) parts.push("filter aktif");
  if (capped) parts.push("hasil dibatasi server");
  return parts.join(" · ");
}

export function parseSalesListPageSize(raw, { fallback = SALES_LIST_DEFAULT_PAGE_SIZE } = {}) {
  const value = Number(raw);
  if (SALES_LIST_PAGE_SIZES.includes(value)) return value;
  return fallback;
}
