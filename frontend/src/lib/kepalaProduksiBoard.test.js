import {
  KP_KPI_ORDER,
  formatDeliveryDate,
  formatKpiCardValue,
  paginateRows,
  rowMatchesExtraFilters,
  uniquePoCount,
} from "./kepalaProduksiBoard";

describe("kepalaProduksiBoard helpers", () => {
  test("six KPI keys stable", () => {
    expect(KP_KPI_ORDER).toEqual([
      "kirim-hari-ini",
      "terlambat",
      "urgent",
      "hold",
      "perlu-produksi",
      "sisa-produksi",
    ]);
  });

  test("filters AND across priority and hold", () => {
    const row = {
      explicit_urgent: true,
      delivery_deadline_status: "overdue",
      authoritative_hold: false,
      effective_delivery_date: "2026-10-13",
    };
    expect(rowMatchesExtraFilters(row, { priority: "urgent", hold: "no" })).toBe(true);
    expect(rowMatchesExtraFilters(row, { priority: "urgent", hold: "yes" })).toBe(false);
    expect(rowMatchesExtraFilters(row, { priority: "normal" })).toBe(false);
  });

  test("paginate after filter", () => {
    const rows = Array.from({ length: 55 }, (_, i) => ({ id: i }));
    const p1 = paginateRows(rows, 1, 50);
    const p2 = paginateRows(rows, 2, 50);
    expect(p1.rows).toHaveLength(50);
    expect(p2.rows).toHaveLength(5);
    expect(p1.total).toBe(55);
  });

  test("delivery date null safe", () => {
    expect(formatDeliveryDate({})).toBe("—");
    expect(formatDeliveryDate({ effective_delivery_date: "2026-10-13" })).toBe("2026-10-13");
  });

  test("unique PO count ignores multi-line", () => {
    expect(uniquePoCount([
      { order_id: "1" },
      { order_id: "1" },
      { order_id: "2" },
    ])).toBe(2);
  });

  test("multi-unit KPI display not a false single total", () => {
    const card = { metric: "qty_by_unit", value: null, by_unit: { pcs: 10, box: 2 }, display: "10 pcs, 2 box" };
    expect(formatKpiCardValue(card)).toBe("10 pcs, 2 box");
  });
});
