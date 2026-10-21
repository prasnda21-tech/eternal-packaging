/** Local-only table layout + column filter prefs for Master Sales settings. */

export const SALES_MASTER_COL_STORAGE_KEY = "sales-master-cols-v1";

export const SALES_MASTER_DEFAULT_COL_ORDER = [
  "name",
  "branches",
  "user",
  "status",
  "sort_order",
  "actions",
];

export const SALES_MASTER_COL_LABELS = {
  name: "Nama Sales",
  branches: "Cabang",
  user: "Akun Eternal",
  status: "Status",
  sort_order: "Urutan",
  actions: "Aksi",
};

/** Baseline widths sized so default desktop fills the settings content well. */
export const SALES_MASTER_DEFAULT_COL_WIDTHS = {
  name: 248,
  branches: 210,
  user: 190,
  status: 118,
  sort_order: 128,
  actions: 190,
};

export const SALES_MASTER_MIN_COL_WIDTHS = {
  name: 160,
  branches: 140,
  user: 140,
  status: 108,
  sort_order: 112,
  actions: 160,
}

/** Per-user key when a stable session user id is available; else legacy shared key. */
export function salesMasterColStorageKey(userId) {
  const id = String(userId || "").trim();
  if (!id) return SALES_MASTER_COL_STORAGE_KEY;
  return `${SALES_MASTER_COL_STORAGE_KEY}:${id}`;
}

export const SALES_MASTER_UNASSIGNED_BRANCH = "__unassigned__";
export const SALES_MASTER_UNLINKED_USER = "__none__";

export const SALES_MASTER_DEFAULT_COL_FILTERS = {
  name: "",
  branches: [],
  user: "",
  status: "",
  sort_min: "",
  sort_max: "",
};

export function clampSalesMasterColWidth(key, width) {
  const min = SALES_MASTER_MIN_COL_WIDTHS[key] ?? 40;
  const fallback = SALES_MASTER_DEFAULT_COL_WIDTHS[key] ?? min;
  const n = Number(width);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.round(n));
}

export function normalizeSalesMasterColOrder(raw) {
  const seen = new Set();
  const next = [];
  const candidates = Array.isArray(raw) ? raw : [];
  candidates.forEach((key) => {
    if (SALES_MASTER_DEFAULT_COL_ORDER.includes(key) && !seen.has(key)) {
      seen.add(key);
      next.push(key);
    }
  });
  SALES_MASTER_DEFAULT_COL_ORDER.forEach((key) => {
    if (!seen.has(key)) next.push(key);
  });
  return next;
}

export function normalizeSalesMasterColWidths(raw) {
  const next = { ...SALES_MASTER_DEFAULT_COL_WIDTHS };
  if (!raw || typeof raw !== "object") return next;
  Object.keys(SALES_MASTER_DEFAULT_COL_WIDTHS).forEach((key) => {
    if (raw[key] != null) next[key] = clampSalesMasterColWidth(key, raw[key]);
  });
  return next;
}

export function normalizeSalesMasterColFilters(raw) {
  const next = {
    name: "",
    branches: [],
    user: "",
    status: "",
    sort_min: "",
    sort_max: "",
  };
  if (!raw || typeof raw !== "object") return next;
  next.name = String(raw.name || "");
  next.branches = Array.isArray(raw.branches)
    ? raw.branches.map((x) => String(x)).filter(Boolean)
    : [];
  next.user = String(raw.user || "");
  const st = String(raw.status || "");
  next.status = st === "active" || st === "inactive" ? st : "";
  next.sort_min = raw.sort_min === "" || raw.sort_min == null ? "" : String(raw.sort_min);
  next.sort_max = raw.sort_max === "" || raw.sort_max == null ? "" : String(raw.sort_max);
  return next;
}

export function readSalesMasterColPrefs(
  storage = typeof window !== "undefined" ? window.localStorage : null,
  userId,
) {
  const fallback = {
    order: [...SALES_MASTER_DEFAULT_COL_ORDER],
    widths: { ...SALES_MASTER_DEFAULT_COL_WIDTHS },
    filters: { ...SALES_MASTER_DEFAULT_COL_FILTERS, branches: [] },
  };
  if (!storage) return fallback;
  try {
    const key = salesMasterColStorageKey(userId);
    let raw = storage.getItem(key);
    // One-time legacy fallback when user-scoped key is empty.
    if (!raw && userId) raw = storage.getItem(SALES_MASTER_COL_STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    return {
      order: normalizeSalesMasterColOrder(parsed?.order),
      widths: normalizeSalesMasterColWidths(parsed?.widths),
      filters: normalizeSalesMasterColFilters(parsed?.filters),
    };
  } catch {
    return fallback;
  }
}

export function writeSalesMasterColPrefs(
  prefs,
  storage = typeof window !== "undefined" ? window.localStorage : null,
  userId,
) {
  if (!storage) return;
  try {
    storage.setItem(salesMasterColStorageKey(userId), JSON.stringify({
      order: normalizeSalesMasterColOrder(prefs?.order),
      widths: normalizeSalesMasterColWidths(prefs?.widths),
      filters: normalizeSalesMasterColFilters(prefs?.filters),
    }));
  } catch {
    /* ignore quota / private mode */
  }
}

export function clearSalesMasterColPrefs(
  storage = typeof window !== "undefined" ? window.localStorage : null,
  userId,
) {
  if (!storage) return;
  try {
    storage.removeItem(salesMasterColStorageKey(userId));
    if (userId) storage.removeItem(SALES_MASTER_COL_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function moveSalesMasterColumn(order, fromKey, toKey) {
  const next = normalizeSalesMasterColOrder(order);
  const from = next.indexOf(fromKey);
  const to = next.indexOf(toKey);
  if (from < 0 || to < 0 || from === to) return next;
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

export function shiftSalesMasterColumn(order, key, direction) {
  const next = normalizeSalesMasterColOrder(order);
  const idx = next.indexOf(key);
  if (idx < 0) return next;
  const target = direction < 0 ? idx - 1 : idx + 1;
  if (target < 0 || target >= next.length) return next;
  const copy = [...next];
  const tmp = copy[idx];
  copy[idx] = copy[target];
  copy[target] = tmp;
  return copy;
}

export function salesMasterTableMinWidth(widths, order = SALES_MASTER_DEFAULT_COL_ORDER) {
  const w = widths || SALES_MASTER_DEFAULT_COL_WIDTHS;
  return normalizeSalesMasterColOrder(order).reduce(
    (sum, key) => sum + (w[key] || SALES_MASTER_DEFAULT_COL_WIDTHS[key] || 0),
    0,
  );
}

export function isSalesMasterColFilterActive(key, filters) {
  const f = normalizeSalesMasterColFilters(filters);
  switch (key) {
    case "name":
      return Boolean(f.name.trim());
    case "branches":
      return f.branches.length > 0;
    case "user":
      return Boolean(f.user);
    case "status":
      return Boolean(f.status);
    case "sort_order":
      return f.sort_min !== "" || f.sort_max !== "";
    default:
      return false;
  }
}

export function countActiveSalesMasterFilters(filters) {
  return SALES_MASTER_DEFAULT_COL_ORDER.filter(
    (key) => key !== "actions" && isSalesMasterColFilterActive(key, filters),
  ).length;
}

/** Client-side AND filter over the already-loaded catalog (max ~2000 from API). */
export function filterSalesMasterRows(items, { query = "", filters } = {}) {
  const list = Array.isArray(items) ? items : [];
  const f = normalizeSalesMasterColFilters(filters);
  const globalQ = String(query || "").trim().toLowerCase();
  const nameQ = f.name.trim().toLowerCase();
  const min = f.sort_min === "" ? null : Number(f.sort_min);
  const max = f.sort_max === "" ? null : Number(f.sort_max);

  return list.filter((row) => {
    const salesName = String(row.sales_name || "").toLowerCase();
    if (globalQ && !salesName.includes(globalQ)) return false;
    if (nameQ && !salesName.includes(nameQ)) return false;

    if (f.branches.length) {
      const ids = Array.isArray(row.branch_ids) ? row.branch_ids : [];
      const hit = f.branches.some((bid) => {
        if (bid === SALES_MASTER_UNASSIGNED_BRANCH) return ids.length === 0;
        return ids.includes(bid);
      });
      if (!hit) return false;
    }

    if (f.user === SALES_MASTER_UNLINKED_USER) {
      if (row.user_id) return false;
    } else if (f.user) {
      if (row.user_id !== f.user) return false;
    }

    if (f.status === "active" && !row.active) return false;
    if (f.status === "inactive" && row.active) return false;

    const sortN = Number(row.sort_order) || 0;
    if (min != null && Number.isFinite(min) && sortN < min) return false;
    if (max != null && Number.isFinite(max) && sortN > max) return false;

    return true;
  });
}
