import {
  CRM_DAILY_COL_STORAGE_KEY,
  CRM_DAILY_DEFAULT_COL_ORDER,
  CRM_DAILY_DEFAULT_COL_WIDTHS,
  clearCrmDailyColPrefs,
  clampCrmDailyColWidth,
  moveCrmDailyColumn,
  normalizeCrmDailyColOrder,
  readCrmDailyColPrefs,
  shiftCrmDailyColumn,
  writeCrmDailyColPrefs,
} from "./crmDailyReportColumns";

describe("crmDailyReportColumns", () => {
  let storage;

  beforeEach(() => {
    const map = new Map();
    storage = {
      getItem: (k) => (map.has(k) ? map.get(k) : null),
      setItem: (k, v) => map.set(k, String(v)),
      removeItem: (k) => map.delete(k),
    };
  });

  test("default order matches Excel", () => {
    expect(CRM_DAILY_DEFAULT_COL_ORDER).toEqual([
      "no", "tanggal", "crm", "customer", "wa", "po", "sales",
      "status_order", "status_customer", "no_order_reason", "complaint", "issue", "actions",
    ]);
  });

  test("move and shift preserve all columns", () => {
    const moved = moveCrmDailyColumn(CRM_DAILY_DEFAULT_COL_ORDER, "sales", "customer");
    expect(moved.indexOf("sales")).toBeLessThan(moved.indexOf("customer"));
    expect(moved).toHaveLength(CRM_DAILY_DEFAULT_COL_ORDER.length);
    expect(new Set(moved)).toEqual(new Set(CRM_DAILY_DEFAULT_COL_ORDER));

    const shifted = shiftCrmDailyColumn(moved, "sales", -1);
    expect(shifted.indexOf("sales")).toBe(moved.indexOf("sales") - 1);
  });

  test("normalize fills missing keys and drops unknowns", () => {
    expect(normalizeCrmDailyColOrder(["sales", "bogus", "customer"])).toEqual([
      "sales",
      "customer",
      ...CRM_DAILY_DEFAULT_COL_ORDER.filter((k) => k !== "sales" && k !== "customer"),
    ]);
  });

  test("clamp enforces minimum width", () => {
    expect(clampCrmDailyColWidth("customer", 10)).toBeGreaterThanOrEqual(160);
    expect(clampCrmDailyColWidth("status_order", 40)).toBeGreaterThanOrEqual(120);
    expect(clampCrmDailyColWidth("tanggal", 50)).toBeGreaterThanOrEqual(100);
  });

  test("legacy narrow prefs are clamped without resetting order", () => {
    storage.setItem(CRM_DAILY_COL_STORAGE_KEY, JSON.stringify({
      order: ["no", "sales", "tanggal", "crm", "customer", "wa", "po", "status_order", "status_customer", "no_order_reason", "complaint", "issue", "actions"],
      widths: { tanggal: 40, sales: 30, status_order: 50, customer: 80 },
    }));
    const loaded = readCrmDailyColPrefs(storage);
    expect(loaded.order[1]).toBe("sales");
    expect(loaded.widths.tanggal).toBeGreaterThanOrEqual(100);
    expect(loaded.widths.sales).toBeGreaterThanOrEqual(96);
    expect(loaded.widths.status_order).toBeGreaterThanOrEqual(120);
    expect(loaded.widths.customer).toBeGreaterThanOrEqual(160);
  });

  test("prefs persist and reset", () => {
    const order = moveCrmDailyColumn(CRM_DAILY_DEFAULT_COL_ORDER, "sales", "tanggal");
    const widths = { ...CRM_DAILY_DEFAULT_COL_WIDTHS, sales: 180 };
    writeCrmDailyColPrefs({ order, widths }, storage);
    expect(storage.getItem(CRM_DAILY_COL_STORAGE_KEY)).toBeTruthy();

    const loaded = readCrmDailyColPrefs(storage);
    expect(loaded.order.indexOf("sales")).toBe(1);
    expect(loaded.order[0]).toBe("no");
    expect(loaded.widths.sales).toBe(180);

    clearCrmDailyColPrefs(storage);
    const reset = readCrmDailyColPrefs(storage);
    expect(reset.order).toEqual(CRM_DAILY_DEFAULT_COL_ORDER);
    expect(reset.widths.sales).toBe(CRM_DAILY_DEFAULT_COL_WIDTHS.sales);
  });
});
