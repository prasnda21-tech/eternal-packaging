/**
 * Module-scoped in-memory list caches for Smart Workspace SWR.
 * All instances use createInMemoryListCache — no second engine.
 * Display-only. Never authorize qty/stock/financial writes from cache.
 */

import {
  DEFAULT_LIST_CACHE_MAX,
  buildListCacheKey,
  createInMemoryListCache,
} from "./createInMemoryListCache";

export const SALES_LIST_SWR_TTL_MS = 30_000;
export const PRODUCTION_DISPLAY_SWR_TTL_MS = 15_000;
export const PURCHASING_LIST_SWR_TTL_MS = 30_000;
export const CASH_BANK_LIST_SWR_TTL_MS = 20_000;
export const INVENTORY_MASTER_SWR_TTL_MS = 30_000;
export const LEDGER_LIST_SWR_TTL_MS = 20_000;

function makeDomainCache({ endpoint, ttlMs, maxEntries = DEFAULT_LIST_CACHE_MAX }) {
  const cache = createInMemoryListCache({ maxEntries, ttlMs });
  return {
    endpoint,
    ttlMs,
    cache,
    buildKey(parts = {}) {
      return buildListCacheKey({ endpoint, ...parts });
    },
    get(key) {
      return cache.get(key);
    },
    set(key, data, now) {
      if (data == null || (typeof data === "object" && data.error)) return null;
      return cache.set(key, data, now);
    },
    isFresh(entry, now) {
      return cache.isFresh(entry, now);
    },
    invalidate() {
      cache.invalidate();
    },
    clear() {
      cache.clear();
    },
    size() {
      return cache.size();
    },
    shouldRevalidateOnFocus(now) {
      return cache.shouldRevalidateOnFocus(now);
    },
    __resetForTests() {
      cache.__resetForTests();
    },
  };
}

export const salesReceiptListCache = makeDomainCache({
  endpoint: "sales.receipts.list",
  ttlMs: SALES_LIST_SWR_TTL_MS,
});

export const salesDeliveryListCache = makeDomainCache({
  endpoint: "sales.deliveries.list",
  ttlMs: SALES_LIST_SWR_TTL_MS,
});

/** Display-only production/task board rows — never for qty/stage submit validation. */
export const productionDisplayListCache = makeDomainCache({
  endpoint: "production.display.list",
  ttlMs: PRODUCTION_DISPLAY_SWR_TTL_MS,
});

export const purchasingListCache = makeDomainCache({
  endpoint: "purchasing.documents.list",
  ttlMs: PURCHASING_LIST_SWR_TTL_MS,
});

export const cashBankListCache = makeDomainCache({
  endpoint: "cashbank.lists",
  ttlMs: CASH_BANK_LIST_SWR_TTL_MS,
});

export const inventoryMasterListCache = makeDomainCache({
  endpoint: "inventory.master.list",
  ttlMs: INVENTORY_MASTER_SWR_TTL_MS,
});

export const ledgerListCache = makeDomainCache({
  endpoint: "ledger.lists",
  ttlMs: LEDGER_LIST_SWR_TTL_MS,
});

const ALL_SMART_LIST_CACHES = [
  salesReceiptListCache,
  salesDeliveryListCache,
  productionDisplayListCache,
  purchasingListCache,
  cashBankListCache,
  inventoryMasterListCache,
  ledgerListCache,
];

/** Logout / identity change — drop every Smart Workspace payload cache. */
export function clearAllSmartListCaches() {
  ALL_SMART_LIST_CACHES.forEach((c) => c.clear());
}

export function __resetAllSmartListCachesForTests() {
  ALL_SMART_LIST_CACHES.forEach((c) => c.__resetForTests());
}
