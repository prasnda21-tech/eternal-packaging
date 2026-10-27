import {
  STOCK_SAAT_INI_PAGE_CACHE_MAX,
  STOCK_SAAT_INI_PAGE_CACHE_MAX_STALE_MS,
  STOCK_SAAT_INI_PAGE_CACHE_TTL_MS,
  __resetStockSaatIniPageCacheForTests,
  buildStockSaatIniPageCacheKey,
  clearStockSaatIniPageCache,
  getStockSaatIniLastView,
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

  test("LRU caps entries and fresh TTL expires unless allowStale", () => {
    for (let i = 0; i < STOCK_SAAT_INI_PAGE_CACHE_MAX + 2; i += 1) {
      const key = buildStockSaatIniPageCacheKey({ userId: "u1", page: i + 1 });
      setStockSaatIniPageCache(key, {
        items: [{ master_item_id: `m-${i}` }],
        total: 1,
        page: i + 1,
        page_count: 1,
      }, { userId: "u1", page: i + 1 });
    }
    expect(getStockSaatIniPageCache(buildStockSaatIniPageCacheKey({ userId: "u1", page: 1 }))).toBeNull();
    expect(getStockSaatIniPageCache(
      buildStockSaatIniPageCacheKey({ userId: "u1", page: STOCK_SAAT_INI_PAGE_CACHE_MAX + 2 }),
    )?.data.items[0].master_item_id).toBe(`m-${STOCK_SAAT_INI_PAGE_CACHE_MAX + 1}`);

    const key = buildStockSaatIniPageCacheKey({ userId: "u1", page: 99 });
    const entry = setStockSaatIniPageCache(key, { items: [], total: 0, page: 99, page_count: 1 }, {
      userId: "u1",
      page: 99,
    });
    expect(entry).toBeTruthy();
    const realNow = Date.now;
    Date.now = () => entry.fetchedAt + STOCK_SAAT_INI_PAGE_CACHE_TTL_MS + 1;
    try {
      expect(getStockSaatIniPageCache(key)).toBeNull();
      expect(getStockSaatIniPageCache(key, { allowStale: true })?.stale).toBe(true);
      expect(getStockSaatIniPageCache(key, { allowStale: true })?.data).toEqual({
        items: [], total: 0, page: 99, page_count: 1,
      });
    } finally {
      Date.now = realNow;
    }
  });

  test("last view restores same-user scope and rejects other users / max stale", () => {
    const key = buildStockSaatIniPageCacheKey({
      userId: "u1", branchId: "b1", warehouseId: "wh-a", q: "box", page: 2, pageSize: 50,
    });
    const payload = {
      items: [{ master_item_id: "m1" }],
      total: 1,
      page: 2,
      page_count: 3,
      stock_read_mode: "warehouse_standalone",
      warehouse_stock_standalone: true,
    };
    const entry = setStockSaatIniPageCache(key, payload, {
      userId: "u1",
      branchId: "b1",
      warehouseId: "wh-a",
      q: "box",
      page: 2,
      pageSize: 50,
      scrollY: 240,
    });
    const view = getStockSaatIniLastView("u1");
    expect(view?.data.items[0].master_item_id).toBe("m1");
    expect(view?.scope).toEqual({
      branchId: "b1", warehouseId: "wh-a", q: "box", page: 2, pageSize: 50,
    });
    expect(view?.scrollY).toBe(240);
    expect(getStockSaatIniLastView("u2")).toBeNull();
    expect(getStockSaatIniLastView("anon")).toBeNull();

    const realNow = Date.now;
    Date.now = () => entry.fetchedAt + STOCK_SAAT_INI_PAGE_CACHE_MAX_STALE_MS + 1;
    try {
      expect(getStockSaatIniLastView("u1")).toBeNull();
      expect(getStockSaatIniPageCache(key, { allowStale: true })).toBeNull();
    } finally {
      Date.now = realNow;
    }
  });

  test("invalidate and clear drop entries and last view; rejects non-page payloads", () => {
    const key = buildStockSaatIniPageCacheKey({ userId: "u1", branchId: "b1" });
    setStockSaatIniPageCache(key, { items: [{ master_item_id: "a" }], total: 1 }, { userId: "u1", branchId: "b1" });
    expect(getStockSaatIniLastView("u1")).toBeTruthy();
    invalidateStockSaatIniPageCache();
    expect(getStockSaatIniPageCache(key)).toBeNull();
    expect(getStockSaatIniLastView("u1")).toBeNull();

    setStockSaatIniPageCache(key, { items: [{ master_item_id: "a" }], total: 1 }, { userId: "u1", branchId: "b1" });
    clearStockSaatIniPageCache();
    expect(getStockSaatIniPageCache(key)).toBeNull();
    expect(getStockSaatIniLastView("u1")).toBeNull();

    expect(setStockSaatIniPageCache(key, { error: "nope" })).toBeNull();
    expect(setStockSaatIniPageCache(key, null)).toBeNull();
    expect(getStockSaatIniPageCache(key)).toBeNull();
  });
});
