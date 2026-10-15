/**
 * In-memory bounded cache for Faktur Penjualan list queries.
 * Not persisted — financial rows must not land in localStorage/sessionStorage.
 */

export const SALES_INVOICE_LIST_CACHE_MAX = 10;
export const SALES_INVOICE_LIST_CACHE_TTL_MS = 30_000;
export const SALES_INVOICE_LIST_FOCUS_REVALIDATE_MS = 10_000;

let generation = 0;
/** @type {Map<string, { generation: number, fetchedAt: number, invoices: any[], listMeta: object, res: object }>} */
const entries = new Map();
/** Scroll offsets for list table panes — UI only, not financial payloads. */
const scrollByKey = new Map();
let lastFocusRevalidateAt = Number.NEGATIVE_INFINITY;

function canonicalFilterValue(value) {
  if (value == null) return "";
  return String(value).trim();
}

/**
 * Stable cache key for one list query. Same inputs → same key.
 */
export function buildSalesInvoiceListCacheKey({
  userId = "",
  branchId = "",
  listTab = "active",
  q = "",
  columnFilters = {},
  page = 1,
  pageSize = 50,
} = {}) {
  const filters = columnFilters || {};
  return JSON.stringify({
    u: canonicalFilterValue(userId),
    b: canonicalFilterValue(branchId),
    tab: canonicalFilterValue(listTab) || "active",
    q: canonicalFilterValue(q),
    datePreset: canonicalFilterValue(filters.datePreset) || "all",
    financialStatus: canonicalFilterValue(filters.financialStatus),
    accountingStatus: canonicalFilterValue(filters.accountingStatus),
    paymentStatus: canonicalFilterValue(filters.paymentStatus),
    page: Math.max(1, Number(page) || 1),
    pageSize: Math.max(1, Number(pageSize) || 50),
  });
}

function touch(key, entry) {
  entries.delete(key);
  entries.set(key, entry);
}

export function getSalesInvoiceListCache(key) {
  if (!key) return null;
  const entry = entries.get(key);
  if (!entry) return null;
  if (entry.generation !== generation) {
    entries.delete(key);
    return null;
  }
  touch(key, entry);
  return entry;
}

export function isSalesInvoiceListCacheFresh(entry, now = Date.now()) {
  if (!entry) return false;
  if (entry.generation !== generation) return false;
  return now - entry.fetchedAt < SALES_INVOICE_LIST_CACHE_TTL_MS;
}

export function setSalesInvoiceListCache(key, payload, now = Date.now()) {
  if (!key || !payload) return null;
  const entry = {
    generation,
    fetchedAt: now,
    invoices: Array.isArray(payload.invoices) ? payload.invoices : [],
    listMeta: payload.listMeta || {
      total: 0,
      tabTotal: 0,
      capped: false,
      counts: { all: 0, active: 0, completed: 0, pending: 0, cancelled: 0 },
    },
    res: payload.res || null,
  };
  touch(key, entry);
  while (entries.size > SALES_INVOICE_LIST_CACHE_MAX) {
    const oldest = entries.keys().next().value;
    entries.delete(oldest);
    scrollByKey.delete(oldest);
  }
  return entry;
}

/** Drop all cached list payloads (logout / session change). */
export function clearSalesInvoiceListCache() {
  generation += 1;
  entries.clear();
  scrollByKey.clear();
  lastFocusRevalidateAt = Number.NEGATIVE_INFINITY;
}

/** Invalidate every entry without changing generation semantics for callers. */
export function invalidateSalesInvoiceListCache() {
  entries.clear();
  // Keep scroll hints; they are not financial. Clear on clearSalesInvoiceListCache.
}

export function salesInvoiceListCacheSize() {
  return entries.size;
}

export function getSalesInvoiceListScroll(key) {
  if (!key) return 0;
  const value = scrollByKey.get(key);
  return Number.isFinite(value) ? value : 0;
}

export function setSalesInvoiceListScroll(key, scrollTop) {
  if (!key) return;
  const next = Number(scrollTop);
  if (!Number.isFinite(next) || next < 0) {
    scrollByKey.delete(key);
    return;
  }
  scrollByKey.set(key, next);
  while (scrollByKey.size > SALES_INVOICE_LIST_CACHE_MAX * 2) {
    const oldest = scrollByKey.keys().next().value;
    scrollByKey.delete(oldest);
  }
}

export function shouldRevalidateSalesInvoiceListOnFocus(now = Date.now()) {
  if (now - lastFocusRevalidateAt < SALES_INVOICE_LIST_FOCUS_REVALIDATE_MS) return false;
  lastFocusRevalidateAt = now;
  return true;
}

/** Test helper — reset module state between tests. */
export function __resetSalesInvoiceListCacheForTests() {
  generation = 0;
  entries.clear();
  scrollByKey.clear();
  lastFocusRevalidateAt = Number.NEGATIVE_INFINITY;
}
