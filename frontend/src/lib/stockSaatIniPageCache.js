/**
 * Short-lived in-memory Stock Saat Ini page cache (remount / back-nav).
 * Not persisted — never localStorage/sessionStorage.
 * Key must include user identity + branch + warehouse + search + page.
 *
 * Remount strategy:
 * - Fresh TTL: paint immediately, soft-revalidate.
 * - Stale (within MAX_STALE): still paint (SWR), soft-revalidate.
 * - Last-view snapshot: restore scope + rows before filtersReady to avoid blank chrome.
 */

import {
  DEFAULT_LIST_CACHE_TTL_MS,
  buildListCacheKey,
  createInMemoryListCache,
} from "./createInMemoryListCache";

export const STOCK_SAAT_INI_PAGE_CACHE_TTL_MS = DEFAULT_LIST_CACHE_TTL_MS;
/** Soft-stale window for tab-return paint (not authoritative for mutations). */
export const STOCK_SAAT_INI_PAGE_CACHE_MAX_STALE_MS = 5 * 60_000;
export const STOCK_SAAT_INI_PAGE_CACHE_MAX = 8;
export const STOCK_SAAT_INI_PAGE_CACHE_ENDPOINT = "inventory.physical_stock.catalog";

const cache = createInMemoryListCache({
  maxEntries: STOCK_SAAT_INI_PAGE_CACHE_MAX,
  ttlMs: STOCK_SAAT_INI_PAGE_CACHE_TTL_MS,
  focusRevalidateMs: 10_000,
});

/** @type {null | {
 *   userId: string,
 *   key: string,
 *   generation: number,
 *   fetchedAt: number,
 *   data: object,
 *   scope: { branchId: string, warehouseId: string, q: string, page: number, pageSize: number },
 *   scrollY: number,
 * }} */
let lastView = null;

function pagePayloadOk(data) {
  return Boolean(data && typeof data === "object" && Array.isArray(data.items));
}

function withinStaleWindow(fetchedAt, now = Date.now()) {
  return now - fetchedAt < STOCK_SAAT_INI_PAGE_CACHE_MAX_STALE_MS;
}

export function buildStockSaatIniPageCacheKey({
  userId = "",
  branchId = "",
  warehouseId = "",
  q = "",
  page = 1,
  pageSize = 50,
} = {}) {
  return buildListCacheKey({
    endpoint: STOCK_SAAT_INI_PAGE_CACHE_ENDPOINT,
    u: userId,
    b: branchId,
    w: warehouseId,
    q,
    page: Math.max(1, Number(page) || 1),
    pageSize: Math.max(1, Number(pageSize) || 50),
  });
}

/**
 * @param {string} key
 * @param {{ allowStale?: boolean, now?: number }} [opts]
 */
export function getStockSaatIniPageCache(key, opts = {}) {
  const allowStale = Boolean(opts.allowStale);
  const now = opts.now == null ? Date.now() : opts.now;
  const entry = cache.get(key);
  if (!entry || !pagePayloadOk(entry.data)) return null;
  const fresh = cache.isFresh(entry, now);
  if (!fresh) {
    if (!allowStale || !withinStaleWindow(entry.fetchedAt, now)) return null;
  }
  return {
    generation: entry.generation,
    fetchedAt: entry.fetchedAt,
    stale: !fresh,
    data: entry.data,
  };
}

/**
 * Instant remount hydrate for the same user (scope + rows).
 * Never returns another user's snapshot.
 */
export function getStockSaatIniLastView(userId, now = Date.now()) {
  const uid = String(userId || "").trim();
  if (!uid || uid === "anon" || !lastView) return null;
  if (lastView.userId !== uid) return null;
  if (!pagePayloadOk(lastView.data)) return null;
  if (!withinStaleWindow(lastView.fetchedAt, now)) return null;
  // Drop if logout/clear bumped generation, or mutation invalidate wiped entries.
  const live = cache.get(lastView.key);
  if (!live || live.generation !== lastView.generation) return null;
  if (!pagePayloadOk(live.data)) return null;
  const fresh = cache.isFresh(live, now);
  return {
    key: lastView.key,
    generation: lastView.generation,
    fetchedAt: lastView.fetchedAt,
    stale: !fresh,
    data: live.data,
    scope: { ...lastView.scope },
    scrollY: Number(lastView.scrollY) || 0,
  };
}

/**
 * @param {string} key
 * @param {object} data
 * @param {{ userId?: string, branchId?: string, warehouseId?: string, q?: string, page?: number, pageSize?: number, scrollY?: number }} [meta]
 */
export function setStockSaatIniPageCache(key, data, meta = {}) {
  if (!pagePayloadOk(data)) return null;
  const entry = cache.set(key, data);
  if (!entry) return null;
  const userId = String(meta.userId || "").trim();
  if (userId && userId !== "anon") {
    lastView = {
      userId,
      key,
      generation: entry.generation,
      fetchedAt: entry.fetchedAt,
      data,
      scope: {
        branchId: String(meta.branchId || ""),
        warehouseId: String(meta.warehouseId || ""),
        q: String(meta.q || ""),
        page: Math.max(1, Number(meta.page) || 1),
        pageSize: Math.max(1, Number(meta.pageSize) || 50),
      },
      scrollY: Math.max(0, Number(meta.scrollY) || 0),
    };
  }
  return entry;
}

export function clearStockSaatIniPageCache() {
  lastView = null;
  cache.clear();
}

/** Drop entries after local stock mutations (adjust / opening) without logout. */
export function invalidateStockSaatIniPageCache() {
  lastView = null;
  cache.invalidate();
}

export function stockSaatIniPageCacheShouldRevalidateOnFocus(now = Date.now()) {
  return cache.shouldRevalidateOnFocus(now);
}

/** @private test helper */
export function __resetStockSaatIniPageCacheForTests() {
  lastView = null;
  cache.__resetForTests();
}
