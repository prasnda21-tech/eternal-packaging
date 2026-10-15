import {
  SALES_INVOICE_LIST_CACHE_MAX,
  SALES_INVOICE_LIST_CACHE_TTL_MS,
  __resetSalesInvoiceListCacheForTests,
  buildSalesInvoiceListCacheKey,
  clearSalesInvoiceListCache,
  getSalesInvoiceListCache,
  getSalesInvoiceListScroll,
  invalidateSalesInvoiceListCache,
  isSalesInvoiceListCacheFresh,
  salesInvoiceListCacheSize,
  setSalesInvoiceListCache,
  setSalesInvoiceListScroll,
  shouldRevalidateSalesInvoiceListOnFocus,
} from "./salesInvoiceListCache";

describe("salesInvoiceListCache", () => {
  beforeEach(() => {
    __resetSalesInvoiceListCacheForTests();
  });

  test("builds canonical keys that ignore whitespace and default blanks", () => {
    const a = buildSalesInvoiceListCacheKey({
      userId: "u1",
      listTab: "active",
      q: "  kopi  ",
      columnFilters: { datePreset: "all", financialStatus: "", accountingStatus: "", paymentStatus: "" },
      page: 1,
      pageSize: 50,
    });
    const b = buildSalesInvoiceListCacheKey({
      userId: "u1",
      listTab: "active",
      q: "kopi",
      columnFilters: {},
      page: 1,
      pageSize: 50,
    });
    expect(a).toBe(b);
  });

  test("different tabs or pages produce different keys", () => {
    const active = buildSalesInvoiceListCacheKey({ userId: "u1", listTab: "active", page: 1 });
    const completed = buildSalesInvoiceListCacheKey({ userId: "u1", listTab: "completed", page: 1 });
    const page2 = buildSalesInvoiceListCacheKey({ userId: "u1", listTab: "active", page: 2 });
    expect(active).not.toBe(completed);
    expect(active).not.toBe(page2);
  });

  test("stores and returns payload; LRU caps at max entries", () => {
    for (let i = 0; i < SALES_INVOICE_LIST_CACHE_MAX + 3; i += 1) {
      const key = buildSalesInvoiceListCacheKey({ userId: "u1", listTab: "active", page: i + 1 });
      setSalesInvoiceListCache(key, {
        invoices: [{ id: `inv-${i}` }],
        listMeta: { total: 1, tabTotal: 1, capped: false, counts: {} },
      });
    }
    expect(salesInvoiceListCacheSize()).toBe(SALES_INVOICE_LIST_CACHE_MAX);
    const oldest = buildSalesInvoiceListCacheKey({ userId: "u1", listTab: "active", page: 1 });
    expect(getSalesInvoiceListCache(oldest)).toBeNull();
    const newest = buildSalesInvoiceListCacheKey({
      userId: "u1",
      listTab: "active",
      page: SALES_INVOICE_LIST_CACHE_MAX + 3,
    });
    expect(getSalesInvoiceListCache(newest)?.invoices[0].id).toBe(`inv-${SALES_INVOICE_LIST_CACHE_MAX + 2}`);
  });

  test("fresh TTL expires", () => {
    const key = buildSalesInvoiceListCacheKey({ userId: "u1", listTab: "active" });
    const entry = setSalesInvoiceListCache(key, { invoices: [], listMeta: {} }, 1_000);
    expect(isSalesInvoiceListCacheFresh(entry, 1_000 + SALES_INVOICE_LIST_CACHE_TTL_MS - 1)).toBe(true);
    expect(isSalesInvoiceListCacheFresh(entry, 1_000 + SALES_INVOICE_LIST_CACHE_TTL_MS + 1)).toBe(false);
  });

  test("clear on logout isolates even same user id", () => {
    const key = buildSalesInvoiceListCacheKey({ userId: "u1", listTab: "active" });
    setSalesInvoiceListCache(key, { invoices: [{ id: "a" }], listMeta: {} });
    expect(getSalesInvoiceListCache(key)?.invoices[0].id).toBe("a");
    clearSalesInvoiceListCache();
    expect(getSalesInvoiceListCache(key)).toBeNull();
    setSalesInvoiceListCache(key, { invoices: [{ id: "b" }], listMeta: {} });
    expect(getSalesInvoiceListCache(key)?.invoices[0].id).toBe("b");
  });

  test("invalidate drops entries without leaving stale financial rows", () => {
    const key = buildSalesInvoiceListCacheKey({ userId: "u1", listTab: "active" });
    setSalesInvoiceListCache(key, { invoices: [{ id: "a" }], listMeta: {} });
    invalidateSalesInvoiceListCache();
    expect(getSalesInvoiceListCache(key)).toBeNull();
    expect(salesInvoiceListCacheSize()).toBe(0);
  });

  test("scroll hints are UI-only and survive invalidate but not clear", () => {
    const key = buildSalesInvoiceListCacheKey({ userId: "u1", listTab: "active" });
    setSalesInvoiceListScroll(key, 240);
    expect(getSalesInvoiceListScroll(key)).toBe(240);
    invalidateSalesInvoiceListCache();
    expect(getSalesInvoiceListScroll(key)).toBe(240);
    clearSalesInvoiceListCache();
    expect(getSalesInvoiceListScroll(key)).toBe(0);
  });

  test("scroll offsets are keyed by query so a long list offset does not leak", () => {
    const pending = buildSalesInvoiceListCacheKey({
      userId: "u1",
      listTab: "pending",
      q: "INV-SCROLL",
      page: 1,
      pageSize: 50,
    });
    const other = buildSalesInvoiceListCacheKey({
      userId: "u1",
      listTab: "pending",
      q: "OTHER",
      page: 1,
      pageSize: 50,
    });
    // Simulate a tall dummy invoice list that was scrolled past the fold.
    setSalesInvoiceListScroll(pending, 480);
    expect(getSalesInvoiceListScroll(pending)).toBe(480);
    expect(getSalesInvoiceListScroll(other)).toBe(0);
    setSalesInvoiceListScroll(other, 12);
    expect(getSalesInvoiceListScroll(pending)).toBe(480);
    expect(getSalesInvoiceListScroll(other)).toBe(12);
  });

  test("focus revalidate is throttled", () => {
    expect(shouldRevalidateSalesInvoiceListOnFocus(1000)).toBe(true);
    expect(shouldRevalidateSalesInvoiceListOnFocus(1500)).toBe(false);
    expect(shouldRevalidateSalesInvoiceListOnFocus(1000 + 10_000)).toBe(true);
  });
});
