import {
  DEFAULT_LIST_CACHE_MAX,
  DEFAULT_LIST_CACHE_TTL_MS,
  buildListCacheKey,
  createInMemoryListCache,
} from "./createInMemoryListCache";

describe("createInMemoryListCache", () => {
  let cache;

  beforeEach(() => {
    cache = createInMemoryListCache({
      maxEntries: 3,
      ttlMs: 1_000,
      focusRevalidateMs: 500,
    });
  });

  test("buildListCacheKey is stable for equivalent identity and query parts", () => {
    const a = buildListCacheKey({
      endpoint: "sales.orders.list",
      u: "u1",
      b: "b1",
      canViewInvoice: true,
      q: "  kopi  ",
      page: 1,
      filters: { sales: "A", deliveryStatuses: ["x", "y"] },
    });
    const b = buildListCacheKey({
      endpoint: "sales.orders.list",
      u: "u1",
      b: "b1",
      canViewInvoice: "1",
      q: "kopi",
      page: "1",
      filters: { deliveryStatuses: ["y", "x"], sales: "A" },
    });
    expect(a).toBe(b);
  });

  test("identity, branch, permission scope, and page isolate keys", () => {
    const base = {
      endpoint: "sales.orders.list",
      u: "u1",
      b: "b1",
      canViewInvoice: false,
      tab: "active",
      page: 1,
    };
    expect(buildListCacheKey({ ...base, u: "u2" })).not.toBe(buildListCacheKey(base));
    expect(buildListCacheKey({ ...base, b: "b2" })).not.toBe(buildListCacheKey(base));
    expect(buildListCacheKey({ ...base, canViewInvoice: true })).not.toBe(buildListCacheKey(base));
    expect(buildListCacheKey({ ...base, page: 2 })).not.toBe(buildListCacheKey(base));
    expect(buildListCacheKey({ ...base, endpoint: "sales.invoices.list" })).not.toBe(
      buildListCacheKey(base),
    );
  });

  test("stores and returns data; LRU evicts oldest", () => {
    cache.set("k1", { rows: [1] });
    cache.set("k2", { rows: [2] });
    cache.set("k3", { rows: [3] });
    cache.set("k4", { rows: [4] });
    expect(cache.size()).toBe(3);
    expect(cache.get("k1")).toBeNull();
    expect(cache.get("k4")?.data.rows[0]).toBe(4);
    // Touch k2 so k3 is next eviction victim
    cache.get("k2");
    cache.set("k5", { rows: [5] });
    expect(cache.get("k3")).toBeNull();
    expect(cache.get("k2")?.data.rows[0]).toBe(2);
  });

  test("TTL freshness expires", () => {
    const entry = cache.set("k1", { rows: [] }, 1_000);
    expect(cache.isFresh(entry, 1_000 + 999)).toBe(true);
    expect(cache.isFresh(entry, 1_000 + 1_001)).toBe(false);
  });

  test("does not cache error-shaped payloads", () => {
    expect(cache.set("k1", { error: "boom" })).toBeNull();
    expect(cache.get("k1")).toBeNull();
    expect(cache.size()).toBe(0);
  });

  test("invalidate drops entries; clear isolates even same key after logout", () => {
    cache.set("k1", { rows: [{ id: "a" }] });
    expect(cache.get("k1")?.data.rows[0].id).toBe("a");
    cache.invalidate();
    expect(cache.get("k1")).toBeNull();
    expect(cache.size()).toBe(0);

    cache.set("k1", { rows: [{ id: "a" }] });
    cache.clear();
    expect(cache.get("k1")).toBeNull();
    cache.set("k1", { rows: [{ id: "b" }] });
    expect(cache.get("k1")?.data.rows[0].id).toBe("b");
  });

  test("two factory instances do not share mutable state", () => {
    const other = createInMemoryListCache({ maxEntries: 2, ttlMs: 1_000 });
    cache.set("shared-key", { rows: ["a"] });
    other.set("shared-key", { rows: ["b"] });
    expect(cache.get("shared-key")?.data.rows[0]).toBe("a");
    expect(other.get("shared-key")?.data.rows[0]).toBe("b");
    cache.clear();
    expect(cache.get("shared-key")).toBeNull();
    expect(other.get("shared-key")?.data.rows[0]).toBe("b");
  });

  test("focus revalidate is throttled", () => {
    expect(cache.shouldRevalidateOnFocus(1000)).toBe(true);
    expect(cache.shouldRevalidateOnFocus(1200)).toBe(false);
    expect(cache.shouldRevalidateOnFocus(1000 + 500)).toBe(true);
  });

  test("default TTL is 30 seconds", () => {
    expect(DEFAULT_LIST_CACHE_TTL_MS).toBe(30_000);
    expect(DEFAULT_LIST_CACHE_MAX).toBe(10);
  });
});
