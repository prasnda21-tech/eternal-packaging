import {
  SALES_ORDER_LIST_CACHE_MAX,
  SALES_ORDER_LIST_CACHE_TTL_MS,
  __resetSalesOrderListCacheForTests,
  buildSalesOrderListCacheKey,
  clearSalesOrderListCache,
  getSalesOrderListCache,
  invalidateSalesOrderListCache,
  isSalesOrderListCacheFresh,
  salesOrderListCacheSize,
  setSalesOrderListCache,
} from "./salesOrderListCache";

describe("salesOrderListCache", () => {
  beforeEach(() => {
    __resetSalesOrderListCacheForTests();
  });

  test("keys isolate user, branch, canViewInvoice, tab, period, and page", () => {
    const base = {
      userId: "u1",
      branchId: "b1",
      canViewInvoice: false,
      listTab: "active",
      period: "all",
      q: "",
      page: 1,
      pageSize: 50,
    };
    expect(buildSalesOrderListCacheKey({ ...base, userId: "u2" }))
      .not.toBe(buildSalesOrderListCacheKey(base));
    expect(buildSalesOrderListCacheKey({ ...base, branchId: "b2" }))
      .not.toBe(buildSalesOrderListCacheKey(base));
    expect(buildSalesOrderListCacheKey({ ...base, canViewInvoice: true }))
      .not.toBe(buildSalesOrderListCacheKey(base));
    expect(buildSalesOrderListCacheKey({ ...base, listTab: "completed" }))
      .not.toBe(buildSalesOrderListCacheKey(base));
    expect(buildSalesOrderListCacheKey({ ...base, period: "this_month" }))
      .not.toBe(buildSalesOrderListCacheKey(base));
    expect(buildSalesOrderListCacheKey({ ...base, page: 2 }))
      .not.toBe(buildSalesOrderListCacheKey(base));
  });

  test("LRU caps entries and TTL expires", () => {
    for (let i = 0; i < SALES_ORDER_LIST_CACHE_MAX + 2; i += 1) {
      const key = buildSalesOrderListCacheKey({ userId: "u1", page: i + 1 });
      setSalesOrderListCache(key, {
        orders: [{ id: `o-${i}` }],
        listMeta: { total: 1, capped: false, tabTotal: 1, counts: {} },
      });
    }
    expect(salesOrderListCacheSize()).toBe(SALES_ORDER_LIST_CACHE_MAX);
    expect(getSalesOrderListCache(buildSalesOrderListCacheKey({ userId: "u1", page: 1 }))).toBeNull();

    const key = buildSalesOrderListCacheKey({ userId: "u1", listTab: "active" });
    const entry = setSalesOrderListCache(key, { orders: [], listMeta: {} }, 1_000);
    expect(isSalesOrderListCacheFresh(entry, 1_000 + SALES_ORDER_LIST_CACHE_TTL_MS - 1)).toBe(true);
    expect(isSalesOrderListCacheFresh(entry, 1_000 + SALES_ORDER_LIST_CACHE_TTL_MS + 1)).toBe(false);
  });

  test("invalidate and logout clear isolate identities", () => {
    const key = buildSalesOrderListCacheKey({ userId: "u1", listTab: "active" });
    setSalesOrderListCache(key, { orders: [{ id: "a" }], listMeta: {} });
    invalidateSalesOrderListCache();
    expect(getSalesOrderListCache(key)).toBeNull();

    setSalesOrderListCache(key, { orders: [{ id: "a" }], listMeta: {} });
    clearSalesOrderListCache();
    expect(getSalesOrderListCache(key)).toBeNull();
    setSalesOrderListCache(key, { orders: [{ id: "b" }], listMeta: {} });
    expect(getSalesOrderListCache(key)?.orders[0].id).toBe("b");
  });

  test("rejects error payloads", () => {
    const key = buildSalesOrderListCacheKey({ userId: "u1" });
    expect(setSalesOrderListCache(key, { error: "nope", orders: [{ id: "x" }] })).toBeNull();
    expect(getSalesOrderListCache(key)).toBeNull();
  });
});
