import { deliveredPercentLabel, effectivePoStatus, formatPoAgeLabel, formatStatusPoDate, formatStatusPoUpdated, itemLineSummary, matchesStatusPoFilters, parseStatusPoDateInput, STATUS_PO_COLUMNS, statusPoFilterOptions } from "./statusPo";

describe("Status PO", () => {
  test("columns exact", () => {
    expect(STATUS_PO_COLUMNS).toEqual([
      "No PO",
      "Tanggal PO",
      "Pelanggan",
      "Item / Produk",
      "Status Saat Ini",
      "Umur",
      "Progress (15 Tahap)",
      "%",
      "Aksi",
    ]);
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

  test("A2. Combined search matches customer or item using existing po filter", () => {
    expect(matchesStatusPoFilters(a, { po: "ABC Pack" })).toBe(true);
    expect(matchesStatusPoFilters(b, { po: "ABC Pack" })).toBe(false);
    expect(matchesStatusPoFilters(
      { ...a, item_lines: ["Plastik PE"] },
      { po: "plastik" },
    )).toBe(true);
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

describe("Status PO display helpers", () => {
  test("age is calendar days from order_date without new thresholds", () => {
    expect(formatPoAgeLabel("2026-09-10", new Date("2026-09-17T05:00:00+07:00"))).toBe("7 hari");
    expect(formatPoAgeLabel("2026-09-17", new Date("2026-09-17T05:00:00+07:00"))).toBe("0 hari");
    expect(formatPoAgeLabel("", new Date("2026-09-17T05:00:00+07:00"))).toBe("—");
  });

  test("updated label uses real timestamp parts", () => {
    expect(formatStatusPoUpdated(new Date("2026-09-17T02:12:00+07:00"))).toBe("17 Sep 2026 · 02:12");
  });

  test("order date displays Indonesia dd/mm/yyyy", () => {
    expect(formatStatusPoDate("2026-08-10")).toBe("10/08/2026");
    expect(formatStatusPoDate("2026-09-17T06:00:00+00:00")).toBe("17/09/2026");
    expect(parseStatusPoDateInput("10/08/2026")).toBe("2026-08-10");
    expect(parseStatusPoDateInput("2026-08-10")).toBe("2026-08-10");
    expect(parseStatusPoDateInput("")).toBe("");
    expect(parseStatusPoDateInput("13/13/2026")).toBe(null);
  });

  test("item summary keeps real names and +N lainnya", () => {
    expect(itemLineSummary([])).toEqual({ text: "-", title: undefined });
    expect(itemLineSummary(["Cup 16oz"])).toEqual({ text: "Cup 16oz", title: "Cup 16oz" });
    expect(itemLineSummary(["Cup 16oz", "Lid 98 mm", "Add On Warna"])).toEqual({
      text: "Cup 16oz +2 lainnya",
      title: "Cup 16oz · Lid 98 mm · Add On Warna",
    });
  });
});
