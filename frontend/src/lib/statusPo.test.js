import { deliveredPercentLabel, effectivePoStatus, matchesStatusPoFilters, STATUS_PO_COLUMNS, statusPoFilterOptions } from "./statusPo";

describe("Status PO", () => {
  test("columns exact", () => {
    expect(STATUS_PO_COLUMNS).toEqual(["No PO", "Tanggal", "Pelanggan", "Item Line", "Status Saat Ini", "Progress", "Aksi"]);
  });

  test("partial delivery 50% remains 50%", () => {
    expect(deliveredPercentLabel(50)).toBe("50%");
    expect(deliveredPercentLabel(50.4)).toBe("50%");
  });

  test("status uses backend derived field when present", () => {
    expect(effectivePoStatus({ status: "Parsial Terkirim", current_stage: 14 })).toBe("Parsial Terkirim");
  });

  test("does not treat stage index alone as fully delivered", () => {
    const row = {
      current_stage: 14,
      terkirim_confirmed: false,
      delivery_status: "Sebagian Terkirim",
      qty_delivered: 5000,
      packing_required_total: 10000,
      status: "Parsial Terkirim",
    };
    expect(effectivePoStatus(row)).toBe("Parsial Terkirim");
  });
});

describe("Status PO header filters", () => {
  const a = { po_number: "PO-001", customer: "ABC Pack", status: "Operator", order_date: "2026-08-10" };
  const b = { po_number: "PO-002", customer: "XYZ", status: "Packing", order_date: "2026-08-20" };
  const c = { po_number: "PO-003", customer: "ABC Store", status: "Operator", order_date: "2026-07-15" };

  test("A. No PO partial match only", () => {
    expect(matchesStatusPoFilters(a, { po: "PO-001" })).toBe(true);
    expect(matchesStatusPoFilters(b, { po: "PO-001" })).toBe(false);
    expect(matchesStatusPoFilters(a, { po: "001" })).toBe(true);
  });

  test("B. Customer partial match only", () => {
    expect(matchesStatusPoFilters(a, { customer: "ABC" })).toBe(true);
    expect(matchesStatusPoFilters(c, { customer: "ABC" })).toBe(true);
    expect(matchesStatusPoFilters(b, { customer: "ABC" })).toBe(false);
  });

  test("C. Status exact from actual Status PO labels", () => {
    expect(matchesStatusPoFilters(a, { status: "Operator" })).toBe(true);
    expect(matchesStatusPoFilters(b, { status: "Operator" })).toBe(false);
    expect(statusPoFilterOptions([a, b])).toEqual(["Operator", "Packing"]);
  });

  test("D. Date range on order_date", () => {
    expect(matchesStatusPoFilters(a, { dateFrom: "2026-08-01", dateTo: "2026-08-31" })).toBe(true);
    expect(matchesStatusPoFilters(b, { dateFrom: "2026-08-01", dateTo: "2026-08-31" })).toBe(true);
    expect(matchesStatusPoFilters(c, { dateFrom: "2026-08-01", dateTo: "2026-08-31" })).toBe(false);
  });

  test("E. Combined AND customer + status + date", () => {
    const f = { customer: "ABC", status: "Operator", dateFrom: "2026-08-01", dateTo: "2026-08-31" };
    expect(matchesStatusPoFilters(a, f)).toBe(true);
    expect(matchesStatusPoFilters(b, f)).toBe(false);
    expect(matchesStatusPoFilters(c, f)).toBe(false);
  });

  test("F. Empty filters match all", () => {
    expect(matchesStatusPoFilters(a, {})).toBe(true);
    expect(matchesStatusPoFilters(a, { po: "", customer: "", status: "", dateFrom: "", dateTo: "" })).toBe(true);
  });

  test("I. Empty result is false not throw", () => {
    expect(matchesStatusPoFilters(a, { po: "NOPE" })).toBe(false);
    expect(matchesStatusPoFilters({}, { po: "x" })).toBe(false);
  });
});
