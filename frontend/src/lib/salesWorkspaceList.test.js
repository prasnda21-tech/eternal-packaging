import {
  paginateRows,
  invoiceListMatchesFilter,
  receiptListMatchesFilter,
  deliveryDateInRange,
  formatDeliveryStatusLabel,
  SALES_DEFAULT_PAGE_SIZE,
} from "./salesWorkspaceList";

describe("sales workspace client pagination", () => {
  const rows = Array.from({ length: 40 }, (_, i) => ({ id: i + 1 }));

  test("defaults to 15 rows and slices the active page", () => {
    const first = paginateRows(rows, 1, SALES_DEFAULT_PAGE_SIZE);
    expect(first.rows).toHaveLength(15);
    expect(first.rows[0].id).toBe(1);
    expect(first.pageCount).toBe(3);
    const second = paginateRows(rows, 2, 15);
    expect(second.rows[0].id).toBe(16);
  });

  test("search/filter reset is represented by page 1 of a new list", () => {
    const filtered = rows.filter((r) => r.id > 30);
    const page = paginateRows(filtered, 4, 15);
    expect(page.page).toBe(1);
    expect(page.total).toBe(10);
  });
});

describe("sales workspace client filters", () => {
  test("invoice chips use existing payment fields", () => {
    expect(invoiceListMatchesFilter({ payment_status: "LUNAS", outstanding_amount: 0 }, "paid")).toBe(true);
    expect(invoiceListMatchesFilter({ payment_status: "DP", outstanding_amount: 10, paid_total: 5 }, "partial")).toBe(true);
    expect(invoiceListMatchesFilter({ payment_status: "BELUM DIBAYAR", outstanding_amount: 10 }, "unpaid")).toBe(true);
    expect(invoiceListMatchesFilter({ outstanding_amount: 10, due_date: "2000-01-01" }, "overdue")).toBe(true);
  });

  test("receipt chips and delivery date range stay frontend-only", () => {
    expect(receiptListMatchesFilter({ payment_state: "LUNAS" }, "LUNAS")).toBe(true);
    expect(receiptListMatchesFilter({ payment_state: "DP" }, "DP")).toBe(true);
    expect(deliveryDateInRange({ delivery_date: "2026-02-01" }, "2026-01-01", "2026-02-28")).toBe(true);
    expect(deliveryDateInRange({ delivery_date: "2025-12-01" }, "2026-01-01", "")).toBe(false);
  });

  test("delivery status keeps a text label", () => {
    expect(formatDeliveryStatusLabel("SEBAGIAN DIKIRIM")).toBe("Sebagian");
    expect(formatDeliveryStatusLabel("SELESAI", true)).toBe("Dibatalkan");
  });
});
