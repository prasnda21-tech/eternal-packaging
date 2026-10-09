import {
  mapOwnerPurchasing,
} from "./ownerAnalytics";

describe("mapOwnerPurchasing", () => {
  test("maps total trend top5 distribution and caps suppliers at five", () => {
    const mapped = mapOwnerPurchasing({
      status: "complete",
      scope_label: "Periode",
      completeness: "complete",
      note: "Nilai faktur aktif sebelum retur.",
      total: {
        status: "complete",
        label: "Total Pembelian Faktur",
        amount: 1000,
        invoice_count: 3,
        basis: "grand_total",
        detail: "basis note",
      },
      trend: {
        status: "complete",
        grain: "day",
        points: [
          { period: "2026-03-01", amount: 400, invoice_count: 1 },
          { period: "2026-03-02", amount: 600, invoice_count: 2 },
        ],
      },
      top_suppliers: {
        status: "complete",
        rows: [
          { key: "name:a", label: "A", amount: 500, share: 50 },
          { key: "name:b", label: "B", amount: 300, share: 30 },
          { key: "name:c", label: "C", amount: 100, share: 10 },
          { key: "name:d", label: "D", amount: 50, share: 5 },
          { key: "name:e", label: "E", amount: 40, share: 4 },
          { key: "name:f", label: "F", amount: 10, share: 1 },
        ],
      },
      distribution: {
        status: "complete",
        show_share: true,
        groups: [
          { key: "name:a", label: "A", amount: 500, share: 50 },
          { key: "other:", label: "Lainnya (1 supplier)", amount: 500, share: 50 },
        ],
      },
    });
    expect(mapped.total.amount).toBe(1000);
    expect(mapped.total.invoice_count).toBe(3);
    expect(mapped.trend.grain).toBe("Harian");
    expect(mapped.trend.points).toHaveLength(2);
    expect(mapped.trend.points[0].label).toMatch(/1 Mar|01 Mar/);
    expect(mapped.top_suppliers.rows).toHaveLength(5);
    expect(mapped.top_suppliers.rows[0].barPct).toBe(100);
    expect(mapped.distribution.show_share).toBe(true);
    expect(mapped.distribution.groups[1].label).toMatch(/Lainnya/);
    expect(mapped.note).toMatch(/retur/i);
  });

  test("error and empty do not invent amounts; incomplete suppresses share use", () => {
    const err = mapOwnerPurchasing({ status: "error", detail: "boom", total: { status: "error" } });
    expect(err.status).toBe("error");
    expect(err.total.amount).toBeNull();
    expect(err.trend.points).toEqual([]);

    const empty = mapOwnerPurchasing({
      status: "empty",
      total: { status: "empty", amount: null, invoice_count: 0 },
      trend: { status: "empty", grain: "day", points: [] },
      top_suppliers: { status: "empty", rows: [] },
      distribution: { status: "empty", groups: [], show_share: false },
    });
    expect(empty.total.amount).toBeNull();
    expect(empty.distribution.show_share).toBe(false);

    const incomplete = mapOwnerPurchasing({
      status: "incomplete",
      completeness: "incomplete",
      total: { status: "incomplete", amount: 50, invoice_count: 1 },
      trend: { status: "complete", grain: "month", points: [{ period: "2026-03", amount: 50, invoice_count: 1 }] },
      top_suppliers: {
        status: "incomplete",
        rows: [{ key: "unknown:", label: "Supplier tidak diketahui", amount: 50, share: null }],
      },
      distribution: {
        status: "incomplete",
        show_share: false,
        groups: [{ key: "unknown:", label: "Supplier tidak diketahui", amount: 50, share: null }],
      },
    });
    expect(incomplete.completeness).toBe("incomplete");
    expect(incomplete.distribution.show_share).toBe(false);
    expect(incomplete.top_suppliers.rows[0].share).toBeNull();
  });
});
