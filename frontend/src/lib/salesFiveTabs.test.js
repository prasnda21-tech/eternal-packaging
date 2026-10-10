import {
  SALES_LIST_DEFAULT_PAGE_SIZE,
  normalizeSalesListTab,
  parseSalesListPageSize,
  salesFiveTabCountsFromResponse,
  salesFiveTabListMetaFromResponse,
  salesFiveTabListParams,
  salesFiveTabRangeLabel,
  salesListTabLabel,
} from "./salesFiveTabs";

describe("salesFiveTabs", () => {
  test("normalizes UI and legacy tab tokens to backend list_tab", () => {
    expect(normalizeSalesListTab("SEMUA")).toBe("all");
    expect(normalizeSalesListTab("aktif")).toBe("active");
    expect(normalizeSalesListTab("histori")).toBe("completed");
    expect(normalizeSalesListTab("batal")).toBe("cancelled");
    expect(normalizeSalesListTab("pending")).toBe("pending");
    expect(normalizeSalesListTab("nope")).toBe("active");
  });

  test("list params send exclusive list_tab with default 50/page", () => {
    expect(salesFiveTabListParams({ listTab: "Selesai", q: " PT " })).toEqual({
      list_tab: "completed",
      page: 1,
      page_size: SALES_LIST_DEFAULT_PAGE_SIZE,
      q: "PT",
    });
    expect(salesFiveTabListParams({
      listTab: "pending",
      page: 3,
      pageSize: 50,
      extra: { date_from: "2026-01-01", financial_status: "" },
    })).toEqual({
      list_tab: "pending",
      page: 3,
      page_size: 50,
      date_from: "2026-01-01",
    });
  });

  test("counts prefer exclusive five_tab_active_total over legacy active_total", () => {
    expect(salesFiveTabCountsFromResponse({
      all_total: 10,
      active_total: 9,
      five_tab_active_total: 4,
      completed_total: 3,
      pending_total: 2,
      cancelled_total: 1,
    })).toEqual({
      all: 10,
      active: 4,
      completed: 3,
      pending: 2,
      cancelled: 1,
    });
  });

  test("list meta keeps exact backend totals and capped flag", () => {
    const meta = salesFiveTabListMetaFromResponse({
      total: 12,
      tab_total: 12,
      list_tab: "active",
      capped: true,
      all_total: 40,
      five_tab_active_total: 12,
      completed_total: 10,
      pending_total: 8,
      cancelled_total: 10,
      page: 1,
      page_size: 50,
    });
    expect(meta.total).toBe(12);
    expect(meta.tabTotal).toBe(12);
    expect(meta.capped).toBe(true);
    expect(meta.counts.active).toBe(12);
    expect(salesListTabLabel(meta.listTab)).toBe("Aktif");
    expect(salesFiveTabRangeLabel({
      total: 12,
      page: 1,
      pageSize: 50,
      tabLabel: "Aktif",
      noun: "faktur",
      capped: true,
    })).toMatch(/1–12 dari 12 faktur pada tab Aktif/);
  });

  test("page size parser defaults to 50", () => {
    expect(parseSalesListPageSize(undefined)).toBe(50);
    expect(parseSalesListPageSize("20")).toBe(20);
    expect(parseSalesListPageSize("7")).toBe(50);
  });
});
