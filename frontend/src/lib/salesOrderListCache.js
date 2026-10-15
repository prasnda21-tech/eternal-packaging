/**
 * Pesanan Penjualan list cache — shared factory instance.
 * Does not replace salesInvoiceListCache (Faktur remains separate).
 */

import {
  DEFAULT_LIST_CACHE_MAX,
  DEFAULT_LIST_CACHE_TTL_MS,
  DEFAULT_LIST_CACHE_FOCUS_REVALIDATE_MS,
  buildListCacheKey,
  createInMemoryListCache,
} from "./createInMemoryListCache";

export const SALES_ORDER_LIST_CACHE_MAX = DEFAULT_LIST_CACHE_MAX;
export const SALES_ORDER_LIST_CACHE_TTL_MS = DEFAULT_LIST_CACHE_TTL_MS;
export const SALES_ORDER_LIST_FOCUS_REVALIDATE_MS = DEFAULT_LIST_CACHE_FOCUS_REVALIDATE_MS;
export const SALES_ORDER_LIST_CACHE_ENDPOINT = "sales.orders.list";

const cache = createInMemoryListCache({
  maxEntries: SALES_ORDER_LIST_CACHE_MAX,
  ttlMs: SALES_ORDER_LIST_CACHE_TTL_MS,
  focusRevalidateMs: SALES_ORDER_LIST_FOCUS_REVALIDATE_MS,
});

function normalizeColumnFilters(columnFilters = {}) {
  return {
    customerQ: columnFilters.customerQ || "",
    orderDateFrom: columnFilters.orderDateFrom || "",
    orderDateTo: columnFilters.orderDateTo || "",
    shipDateFrom: columnFilters.shipDateFrom || "",
    shipDateTo: columnFilters.shipDateTo || "",
    qtyMin: columnFilters.qtyMin || "",
    qtyMax: columnFilters.qtyMax || "",
    qtyUnit: columnFilters.qtyUnit || "",
    deliveryStatuses: Array.isArray(columnFilters.deliveryStatuses) ? columnFilters.deliveryStatuses : [],
    processLabels: Array.isArray(columnFilters.processLabels) ? columnFilters.processLabels : [],
    sales: columnFilters.sales || "",
    createdBy: columnFilters.createdBy || "",
    invoiceStatuses: Array.isArray(columnFilters.invoiceStatuses) ? columnFilters.invoiceStatuses : [],
  };
}

/**
 * Explicit key: identity + branch + permission scope + endpoint + normalized query/page.
 */
export function buildSalesOrderListCacheKey({
  userId = "",
  branchId = "",
  canViewInvoice = false,
  listTab = "active",
  period = "all",
  q = "",
  columnFilters = {},
  page = 1,
  pageSize = 50,
} = {}) {
  return buildListCacheKey({
    endpoint: SALES_ORDER_LIST_CACHE_ENDPOINT,
    u: userId,
    b: branchId,
    canViewInvoice: Boolean(canViewInvoice),
    tab: listTab || "active",
    period: period || "all",
    q,
    filters: normalizeColumnFilters(columnFilters),
    page: Math.max(1, Number(page) || 1),
    pageSize: Math.max(1, Number(pageSize) || 50),
  });
}

export function getSalesOrderListCache(key) {
  const entry = cache.get(key);
  if (!entry) return null;
  return {
    generation: entry.generation,
    fetchedAt: entry.fetchedAt,
    orders: Array.isArray(entry.data?.orders) ? entry.data.orders : [],
    listMeta: entry.data?.listMeta || {
      total: 0,
      capped: false,
      tabTotal: 0,
      counts: { all: 0, active: 0, completed: 0, pending: 0, cancelled: 0 },
    },
    res: entry.data?.res || null,
  };
}

export function isSalesOrderListCacheFresh(entry, now = Date.now()) {
  return cache.isFresh(entry, now);
}

export function setSalesOrderListCache(key, payload, now = Date.now()) {
  if (!payload || payload.error) return null;
  const data = {
    orders: Array.isArray(payload.orders) ? payload.orders : [],
    listMeta: payload.listMeta || {
      total: 0,
      capped: false,
      tabTotal: 0,
      counts: { all: 0, active: 0, completed: 0, pending: 0, cancelled: 0 },
    },
    res: payload.res || null,
  };
  const entry = cache.set(key, data, now);
  if (!entry) return null;
  return getSalesOrderListCache(key);
}

export function clearSalesOrderListCache() {
  cache.clear();
}

export function invalidateSalesOrderListCache() {
  cache.invalidate();
}

export function salesOrderListCacheSize() {
  return cache.size();
}

export function shouldRevalidateSalesOrderListOnFocus(now = Date.now()) {
  return cache.shouldRevalidateOnFocus(now);
}

export function __resetSalesOrderListCacheForTests() {
  cache.__resetForTests();
}
