import {
  EMPTY_HISTORY_FILTERS,
  HISTORY_PAGE_SIZE_OPTIONS,
  defaultHistoryFilters,
  formatHistoryDay,
  formatHistoryQty,
  historyActiveDatePreset,
  historyDatePresetRange,
  historyFiltersActive,
  historyRequestParams,
  isHistoryMonthPreset,
} from "./salesPwaDeliveryHistory";

describe("salesPwaDeliveryHistory helpers", () => {
  test("Bulan Ini uses first and last day of the WIB calendar month", () => {
    expect(historyDatePresetRange("month", "2026-11-15")).toEqual({
      date_from: "2026-11-01",
      date_to: "2026-11-30",
    });
    expect(historyDatePresetRange("month", "2026-02-03")).toEqual({
      date_from: "2026-02-01",
      date_to: "2026-02-28",
    });
    expect(historyDatePresetRange("month", "2024-02-29")).toEqual({
      date_from: "2024-02-01",
      date_to: "2024-02-29",
    });
    expect(historyDatePresetRange("month", "2026-12-31")).toEqual({
      date_from: "2026-12-01",
      date_to: "2026-12-31",
    });
    expect(historyDatePresetRange("month", "2027-01-01")).toEqual({
      date_from: "2027-01-01",
      date_to: "2027-01-31",
    });
  });

  test("other presets stay relative to today WIB", () => {
    expect(historyDatePresetRange("today", "2026-11-15")).toEqual({
      date_from: "2026-11-15",
      date_to: "2026-11-15",
    });
    expect(historyDatePresetRange("7d", "2026-11-15")).toEqual({
      date_from: "2026-11-09",
      date_to: "2026-11-15",
    });
    expect(historyDatePresetRange("30d", "2026-11-15")).toEqual({
      date_from: "2026-10-17",
      date_to: "2026-11-15",
    });
  });

  test("default filters are Bulan Ini with no text limits", () => {
    const def = defaultHistoryFilters("2026-11-02");
    expect(def).toEqual({
      ...EMPTY_HISTORY_FILTERS,
      date_from: "2026-11-01",
      date_to: "2026-11-30",
    });
    expect(isHistoryMonthPreset(def, "2026-11-02")).toBe(true);
    expect(historyActiveDatePreset(def, "2026-11-02")).toBe("month");
    expect(historyFiltersActive(def, "2026-11-02")).toBe(false);
    expect(historyFiltersActive({ ...def, item_q: "pet" }, "2026-11-02")).toBe(true);
    expect(historyFiltersActive({ ...def, date_from: "2026-11-01", date_to: "2026-11-01" }, "2026-11-02")).toBe(true);
  });

  test("request params keep numeric qty and trim text", () => {
    expect(historyRequestParams({
      ...EMPTY_HISTORY_FILTERS,
      customer_q: "  kopi  ",
      item_q: "pet 16",
      qty_min: "100",
      qty_max: "2000",
      date_from: "2026-11-01",
      date_to: "2026-11-30",
    }, { skip: 25, limit: 25 })).toEqual({
      skip: 25,
      limit: 25,
      customer_q: "kopi",
      item_q: "pet 16",
      qty_min: 100,
      qty_max: 2000,
      date_from: "2026-11-01",
      date_to: "2026-11-30",
    });
  });

  test("formatters", () => {
    expect(formatHistoryDay("2026-11-02T10:00:00+07:00")).toBe("02/11/2026");
    expect(formatHistoryQty(2000, "pcs")).toBe("2.000 pcs");
    expect(formatHistoryQty(2000, null)).toBe("2.000");
  });

  test("page size options are 25/50/100 with default request limit 25", () => {
    expect(HISTORY_PAGE_SIZE_OPTIONS).toEqual([25, 50, 100]);
    expect(historyRequestParams({}, { skip: 0, limit: 25 })).toEqual({ skip: 0, limit: 25 });
  });
});
