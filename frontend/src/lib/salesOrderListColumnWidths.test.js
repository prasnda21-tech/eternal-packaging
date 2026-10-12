import {
  SALES_ORDER_COL_STORAGE_KEY,
  SALES_ORDER_DEFAULT_COL_WIDTHS,
  SALES_ORDER_MIN_COL_WIDTHS,
  clearSalesOrderColWidths,
  clampSalesOrderColWidth,
  normalizeSalesOrderColWidths,
  readSalesOrderColWidths,
  salesOrderTableMinWidth,
  writeSalesOrderColWidths,
} from "./salesOrderListColumnWidths";

function memoryStorage(seed = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  };
}

describe("salesOrderListColumnWidths", () => {
  test("clamp enforces per-column minimums", () => {
    expect(clampSalesOrderColWidth("actions", 40)).toBe(SALES_ORDER_MIN_COL_WIDTHS.actions);
    expect(clampSalesOrderColWidth("invoice", 50)).toBe(SALES_ORDER_MIN_COL_WIDTHS.invoice);
    expect(clampSalesOrderColWidth("customer", 240)).toBe(240);
    expect(clampSalesOrderColWidth("qty", "x")).toBe(SALES_ORDER_DEFAULT_COL_WIDTHS.qty);
  });

  test("invalid localStorage falls back to defaults", () => {
    const storage = memoryStorage({ [SALES_ORDER_COL_STORAGE_KEY]: "{not-json" });
    expect(readSalesOrderColWidths(storage)).toEqual(SALES_ORDER_DEFAULT_COL_WIDTHS);
  });

  test("partial stored widths merge and clamp", () => {
    const storage = memoryStorage({
      [SALES_ORDER_COL_STORAGE_KEY]: JSON.stringify({ customer: 300, actions: 10, junk: 9 }),
    });
    const widths = readSalesOrderColWidths(storage);
    expect(widths.customer).toBe(300);
    expect(widths.actions).toBe(SALES_ORDER_MIN_COL_WIDTHS.actions);
    expect(widths.sales).toBe(SALES_ORDER_DEFAULT_COL_WIDTHS.sales);
    expect(widths.junk).toBeUndefined();
  });

  test("write then read round-trips normalized widths", () => {
    const storage = memoryStorage();
    writeSalesOrderColWidths({ customer: 250, actions: 20 }, storage);
    const widths = readSalesOrderColWidths(storage);
    expect(widths.customer).toBe(250);
    expect(widths.actions).toBe(SALES_ORDER_MIN_COL_WIDTHS.actions);
  });

  test("clear removes stored preference", () => {
    const storage = memoryStorage({
      [SALES_ORDER_COL_STORAGE_KEY]: JSON.stringify({ customer: 300 }),
    });
    clearSalesOrderColWidths(storage);
    expect(storage.getItem(SALES_ORDER_COL_STORAGE_KEY)).toBe(null);
    expect(readSalesOrderColWidths(storage)).toEqual(SALES_ORDER_DEFAULT_COL_WIDTHS);
  });

  test("normalize rejects non-objects", () => {
    expect(normalizeSalesOrderColWidths(null)).toEqual(SALES_ORDER_DEFAULT_COL_WIDTHS);
    expect(normalizeSalesOrderColWidths("x")).toEqual(SALES_ORDER_DEFAULT_COL_WIDTHS);
  });

  test("table min width sums visible columns", () => {
    const withInvoice = salesOrderTableMinWidth(SALES_ORDER_DEFAULT_COL_WIDTHS, {
      includeCheck: true,
      includeInvoice: true,
    });
    const withoutInvoice = salesOrderTableMinWidth(SALES_ORDER_DEFAULT_COL_WIDTHS, {
      includeCheck: true,
      includeInvoice: false,
    });
    expect(withInvoice - withoutInvoice).toBe(SALES_ORDER_DEFAULT_COL_WIDTHS.invoice);
  });
});
