/**
 * Shared in-memory bounded list-cache factory for Smart Workspace list pages.
 * Not persisted — payloads must not land in localStorage/sessionStorage.
 * No automatic background polling. Caller owns fetch/revalidate scheduling.
 */

export const DEFAULT_LIST_CACHE_MAX = 10;
export const DEFAULT_LIST_CACHE_TTL_MS = 30_000;
export const DEFAULT_LIST_CACHE_FOCUS_REVALIDATE_MS = 10_000;

export function canonicalListCacheValue(value) {
  if (value == null) return "";
  if (Array.isArray(value)) {
    return value.map((item) => canonicalListCacheValue(item)).filter(Boolean).sort().join(",");
  }
  if (typeof value === "boolean") return value ? "1" : "0";
  return String(value).trim();
}

/**
 * Build a stable JSON cache key from an explicit parts object.
 * Callers must include identity, branch, permission-relevant scope, endpoint, and query/page.
 */
export function buildListCacheKey(parts = {}) {
  const normalized = {};
  const keys = Object.keys(parts).sort();
  for (const key of keys) {
    const value = parts[key];
    if (value != null && typeof value === "object" && !Array.isArray(value)) {
      const nested = {};
      for (const nestedKey of Object.keys(value).sort()) {
        nested[nestedKey] = canonicalListCacheValue(value[nestedKey]);
      }
      normalized[key] = nested;
    } else {
      normalized[key] = canonicalListCacheValue(value);
    }
  }
  return JSON.stringify(normalized);
}

/**
 * @param {{ maxEntries?: number, ttlMs?: number, focusRevalidateMs?: number }} [options]
 */
export function createInMemoryListCache({
  maxEntries = DEFAULT_LIST_CACHE_MAX,
  ttlMs = DEFAULT_LIST_CACHE_TTL_MS,
  focusRevalidateMs = DEFAULT_LIST_CACHE_FOCUS_REVALIDATE_MS,
} = {}) {
  let generation = 0;
  /** @type {Map<string, { generation: number, fetchedAt: number, data: any }>} */
  const entries = new Map();
  let lastFocusRevalidateAt = Number.NEGATIVE_INFINITY;

  function touch(key, entry) {
    entries.delete(key);
    entries.set(key, entry);
  }

  function get(key) {
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

  function isFresh(entry, now = Date.now()) {
    if (!entry) return false;
    if (entry.generation !== generation) return false;
    return now - entry.fetchedAt < ttlMs;
  }

  /**
   * Store a successful list payload. Error responses must not be passed here.
   * @returns {object|null}
   */
  function set(key, data, now = Date.now()) {
    if (!key || data == null) return null;
    // Never treat an explicit error-shaped payload as valid list data.
    if (typeof data === "object" && data.error) return null;
    const entry = {
      generation,
      fetchedAt: now,
      data,
    };
    touch(key, entry);
    while (entries.size > maxEntries) {
      const oldest = entries.keys().next().value;
      entries.delete(oldest);
    }
    return entry;
  }

  /** Drop all entries (logout / identity change). Bumps generation. */
  function clear() {
    generation += 1;
    entries.clear();
    lastFocusRevalidateAt = Number.NEGATIVE_INFINITY;
  }

  /** Drop entries without bumping generation (mutation invalidation). */
  function invalidate() {
    entries.clear();
  }

  function size() {
    return entries.size;
  }

  function shouldRevalidateOnFocus(now = Date.now()) {
    if (now - lastFocusRevalidateAt < focusRevalidateMs) return false;
    lastFocusRevalidateAt = now;
    return true;
  }

  /** Test helper — reset module state between tests. */
  function __resetForTests() {
    generation = 0;
    entries.clear();
    lastFocusRevalidateAt = Number.NEGATIVE_INFINITY;
  }

  return {
    maxEntries,
    ttlMs,
    focusRevalidateMs,
    get,
    set,
    isFresh,
    clear,
    invalidate,
    size,
    shouldRevalidateOnFocus,
    __resetForTests,
  };
}
