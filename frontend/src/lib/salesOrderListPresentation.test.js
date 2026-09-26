import {
  canShowAktifSummary,
  formatQuantityByUnit,
  listDeliveryProgress,
  listProgressText,
  orderQuantityLabel,
  quantityByUnitFromLines,
  salesOrderListRequest,
} from "./salesOrderListPresentation";

describe("sales order quantity presentation", () => {
  test("same unit is totaled and different units stay separate", () => {
    const parts = quantityByUnitFromLines([
      { quantity: 4, unit: "pcs" },
      { quantity: 6, unit: "pcs" },
      { quantity: 2, unit: "dus" },
    ]);
    expect(parts).toEqual([
      { unit: "pcs", quantity: 10 },
      { unit: "dus", quantity: 2 },
    ]);
    expect(formatQuantityByUnit(parts)).toBe("10 pcs · 2 dus");
    expect(formatQuantityByUnit(parts)).not.toMatch(/12/);
  });

  test("a missing breakdown is not labeled as PCS", () => {
    expect(orderQuantityLabel({ total_quantity: 1500 })).toBe("1.500");
    expect(orderQuantityLabel({ total_quantity: 1500 })).not.toMatch(/pcs/i);
  });

  test("mixed-unit progress does not add the units into one number", () => {
    const text = listProgressText({
      quantity_by_unit: [
        { unit: "pcs", quantity: 10 },
        { unit: "dus", quantity: 2 },
      ],
      delivery_summary: { status: "SEBAGIAN", total_ordered: 12, total_shipped: 4, total_remaining: 8 },
    });
    expect(text).toBe("SEBAGIAN");
    expect(text).not.toMatch(/Siap kirim|Kendala/);
  });

  test("one unit labels shipped over ordered and keeps a zero bar empty", () => {
    expect(listProgressText({
      quantity_by_unit: [{ unit: "pcs", quantity: 100 }],
      delivery_summary: { status: "SEBAGIAN", total_ordered: 100, total_shipped: 40 },
    })).toBe("40 terkirim / 100 pesanan pcs");
    const empty = listDeliveryProgress({
      quantity_by_unit: [{ unit: "pcs", quantity: 80 }],
      delivery_summary: { status: "BELUM DIKIRIM", total_ordered: 80, total_shipped: 0, total_remaining: 80 },
    });
    expect(empty.ratio).toBe(0);
    expect(empty.label).toBe("0 terkirim / 80 pesanan pcs");
    expect(empty.status).toBe("BELUM DIKIRIM");
  });
});

describe("aktif summary source", () => {
  test("shows only a server total for the unfiltered aktif classification", () => {
    expect(canShowAktifSummary({
      tab: "aktif",
      status: "aktif",
      q: "",
      period: "all",
      columnFiltersActive: false,
      total: 24,
      capped: false,
    })).toBe(true);
    expect(canShowAktifSummary({
      tab: "aktif",
      status: "aktif",
      total: 10,
      capped: false,
      q: "kopi",
    })).toBe(false);
    expect(canShowAktifSummary({
      tab: "aktif",
      status: "aktif",
      total: 10,
      capped: true,
    })).toBe(false);
    expect(canShowAktifSummary({
      tab: "histori",
      status: "histori",
      total: 4,
      capped: false,
    })).toBe(false);
    expect(canShowAktifSummary({
      tab: "aktif",
      status: "aktif",
      total: null,
      capped: false,
    })).toBe(false);
  });

  test("list request keeps paging and the existing status value", () => {
    expect(salesOrderListRequest({
      q: " ABC ",
      status: "aktif",
      dateFrom: "2026-09-01",
      dateTo: "2026-09-30",
      page: 2,
      pageSize: 10,
    })).toEqual({
      q: "ABC",
      status: "aktif",
      date_from: "2026-09-01",
      date_to: "2026-09-30",
      page: 2,
      page_size: 10,
    });
  });
});
