import {
  STOCK_SAAT_INI_PAGE_CACHE_MAX,
  STOCK_SAAT_INI_PAGE_CACHE_TTL_MS,
  __resetStockSaatIniPageCacheForTests,
  buildStockSaatIniPageCacheKey,
  clearStockSaatIniPageCache,
  getStockSaatIniPageCache,
  invalidateStockSaatIniPageCache,
  setStockSaatIniPageCache,
} from "./stockSaatIniPageCache";

describe("stockSaatIniPageCache", () => {
  beforeEach(() => {
    __resetStockSaatIniPageCacheForTests();
  });

  test("keys isolate user, branch, warehouse, search, page, and page size", () => {
    const base = {
      userId: "u1",
      branchId: "b1",
      warehouseId: "",
      q: "",
      page: 1,
      pageSize: 50,
    };
    expect(buildStockSaatIniPageCacheKey({ ...base, userId: "u2" }))
      .not.toBe(buildStockSaatIniPageCacheKey(base));
    expect(buildStockSaatIniPageCacheKey({ ...base, branchId: "b2" }))
      .not.toBe(buildStockSaatIniPageCacheKey(base));
    expect(buildStockSaatIniPageCacheKey({ ...base, warehouseId: "wh-1" }))
      .not.toBe(buildStockSaatIniPageCacheKey(base));
    expect(buildStockSaatIniPageCacheKey({ ...base, q: "box" }))
      .not.toBe(buildStockSaatIniPageCacheKey(base));
    expect(buildStockSaatIniPageCacheKey({ ...base, page: 2 }))
      .not.toBe(buildStockSaatIniPageCacheKey(base));
    expect(buildStockSaatIniPageCacheKey({ ...base, pageSize: 25 }))
      .not.toBe(buildStockSaatIniPageCacheKey(base));
  });

  test("LRU caps entries and TTL expires", () => {
    for (let i = 0; i < STOCK_SAAT_INI_PAGE_CACHE_MAX + 2; i += 1) {
      const key = buildStockSaatIniPageCacheKey({ userId: "u1", page: i + 1 });
      setStockSaatIniPageCache(key, {
        items: [{ master_item_id: `m-${i}` }],
        total: 1,
        page: i + 1,
        page_count: 1,
      });
    }
    expect(getStockSaatIniPageCache(buildStockSaatIniPageCacheKey({ userId: "u1", page: 1 }))).toBeNull();
    expect(getStockSaatIniPageCache(
      buildStockSaatIniPageCacheKey({ userId: "u1", page: STOCK_SAAT_INI_PAGE_CACHE_MAX + 2 }),
    )?.data.items[0].master_item_id).toBe(`m-${STOCK_SAAT_INI_PAGE_CACHE_MAX + 1}`);

    const key = buildStockSaatIniPageCacheKey({ userId: "u1", page: 99 });
    const entry = setStockSaatIniPageCache(key, { items: [], total: 0, page: 99, page_count: 1 });
    expect(entry).toBeTruthy();
    // Force-expire by advancing past TTL via invalidate path: clear then re-set with old clock.
    // Freshness is checked at get() time against Date.now(); stub clock for TTL.
    const realNow = Date.now;
    Date.now = () => entry.fetchedAt + STOCK_SAAT_INI_PAGE_CACHE_TTL_MS + 1;
    try {
      expect(getStockSaatIniPageCache(key)).toBeNull();
    } finally {
      Date.now = realNow;
    }
  });

  test("invalidate and clear drop entries; rejects non-page payloads", () => {
    const key = buildStockSaatIniPageCacheKey({ userId: "u1", branchId: "b1" });
    setStockSaatIniPageCache(key, { items: [{ master_item_id: "a" }], total: 1 });
    invalidateStockSaatIniPageCache();
    expect(getStockSaatIniPageCache(key)).toBeNull();

    setStockSaatIniPageCache(key, { items: [{ master_item_id: "a" }], total: 1 });
    clearStockSaatIniPageCache();
    expect(getStockSaatIniPageCache(key)).toBeNull();

    expect(setStockSaatIniPageCache(key, { error: "nope" })).toBeNull();
    expect(setStockSaatIniPageCache(key, null)).toBeNull();
    expect(getStockSaatIniPageCache(key)).toBeNull();
  });
});
