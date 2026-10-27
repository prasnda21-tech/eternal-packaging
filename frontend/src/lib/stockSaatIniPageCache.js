/**
 * Short-lived in-memory Stock Saat Ini page cache (remount / back-nav).
 * Not persisted — never localStorage/sessionStorage.
 * Key must include user identity + branch + warehouse + search + page.
 */

import {
  DEFAULT_LIST_CACHE_TTL_MS,
  buildListCacheKey,
  createInMemoryListCache,
} from "./createInMemoryListCache";

export const STOCK_SAAT_INI_PAGE_CACHE_TTL_MS = DEFAULT_LIST_CACHE_TTL_MS;
export const STOCK_SAAT_INI_PAGE_CACHE_MAX = 8;
export const STOCK_SAAT_INI_PAGE_CACHE_ENDPOINT = "inventory.physical_stock.catalog";

const cache = createInMemoryListCache({
  maxEntries: STOCK_SAAT_INI_PAGE_CACHE_MAX,
  ttlMs: STOCK_SAAT_INI_PAGE_CACHE_TTL_MS,
  focusRevalidateMs: 10_000,
});

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

export function getStockSaatIniPageCache(key) {
  const entry = cache.get(key);
  if (!entry || !cache.isFresh(entry)) return null;
  const data = entry.data;
  if (!data || typeof data !== "object" || !Array.isArray(data.items)) return null;
  return {
    generation: entry.generation,
    fetchedAt: entry.fetchedAt,
    data,
  };
}

export function setStockSaatIniPageCache(key, data) {
  if (!data || typeof data !== "object" || !Array.isArray(data.items)) return null;
  return cache.set(key, data);
}

export function clearStockSaatIniPageCache() {
  cache.clear();
}

/** Drop entries after local stock mutations (adjust / opening) without logout. */
export function invalidateStockSaatIniPageCache() {
  cache.invalidate();
}

export function stockSaatIniPageCacheShouldRevalidateOnFocus(now = Date.now()) {
  return cache.shouldRevalidateOnFocus(now);
}

/** @private test helper */
export function __resetStockSaatIniPageCacheForTests() {
  cache.__resetForTests();
}
