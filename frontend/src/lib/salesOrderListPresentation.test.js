import {
  canShowAktifSummary,
  formatQuantityByUnit,
  listDeliveryProgress,
  listProgressText,
  orderCreatorLabel,
  orderInvoiceStatusLabel,
  orderInvoiceStatusTone,
  orderQuantityLabel,
  quantityByUnitFromLines,
  salesOrderListRequest,
  salesOrderListTabFromQuery,
  salesOrderStatusFromQuery,
  salesOrderTabDefaultStatus,
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
      tab: "active",
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
      tab: "active",
      status: "aktif",
      total: 10,
      capped: true,
    })).toBe(false);
    expect(canShowAktifSummary({
      tab: "completed",
      status: "selesai",
      total: 4,
      capped: false,
    })).toBe(false);
    expect(canShowAktifSummary({
      tab: "active",
      status: "aktif",
      total: null,
      capped: false,
    })).toBe(false);
  });

  test("list request keeps paging; list_tab path is exclusive five-tab", () => {
    expect(salesOrderListRequest({
      q: " ABC ",
      status: "aktif",
      dateFrom: "2026-09-01",
      dateTo: "2026-09-30",
      orderDateFrom: "2026-09-05",
      orderDateTo: "2026-09-20",
      shipDateFrom: "2026-09-10",
      shipDateTo: "2026-09-12",
      customerQ: " outlet ",
      sales: "Andi",
      createdBy: "__unknown__",
      deliveryStatuses: ["SEBAGIAN", "SELESAI"],
      processLabels: ["HOLD"],
      invoiceStatuses: ["sudah", "sebagian"],
      qtyMin: "10",
      qtyMax: "50",
      qtyUnit: "pcs",
      page: 2,
      pageSize: 10,
    })).toEqual({
      q: "ABC",
      status: "aktif",
      date_from: "2026-09-01",
      date_to: "2026-09-30",
      order_date_from: "2026-09-05",
      order_date_to: "2026-09-20",
      ship_date_from: "2026-09-10",
      ship_date_to: "2026-09-12",
      customer_q: "outlet",
      sales: "Andi",
      created_by: "__unknown__",
      delivery_status: "SEBAGIAN,SELESAI",
      process_label: "HOLD",
      invoice_status: "sudah,sebagian",
      qty_min: "10",
      qty_max: "50",
      qty_unit: "pcs",
      page: 2,
      page_size: 10,
    });
    expect(salesOrderListRequest({
      listTab: "pending",
      q: " PT ",
      page: 1,
      pageSize: 50,
      dateFrom: "2026-09-01",
    })).toEqual({
      list_tab: "pending",
      page: 1,
      page_size: 50,
      q: "PT",
      date_from: "2026-09-01",
    });
    expect(salesOrderListRequest({ status: "all", page: 1, pageSize: 10 }).status).toBeUndefined();
  });
});

describe("Five-tab query state", () => {
  test("default route stays active; legacy tokens map into five tabs", () => {
    expect(salesOrderListTabFromQuery({})).toBe("active");
    expect(salesOrderStatusFromQuery({})).toBe("aktif");
    expect(salesOrderTabDefaultStatus("active")).toBe("aktif");
    expect(salesOrderTabDefaultStatus("histori")).toBe("selesai");
    expect(salesOrderTabDefaultStatus("all")).toBe("gabungan");
    expect(salesOrderListTabFromQuery({ tab: "semua" })).toBe("all");
    expect(salesOrderStatusFromQuery({ tab: "semua" })).toBe("gabungan");
    expect(salesOrderListTabFromQuery({ status: "gabungan" })).toBe("all");
    expect(salesOrderListTabFromQuery({ tab: "histori" })).toBe("completed");
    expect(salesOrderListTabFromQuery({ tab: "pending" })).toBe("pending");
    expect(salesOrderListTabFromQuery({ tab: "batal" })).toBe("cancelled");
  });

  test("explicit existing status chooses the tab, including over tab=semua", () => {
    expect(salesOrderListTabFromQuery({ tab: "semua", status: "selesai" })).toBe("completed");
    expect(salesOrderStatusFromQuery({ tab: "semua", status: "selesai" })).toBe("selesai");
    expect(salesOrderListTabFromQuery({ tab: "semua", status: "cancelled" })).toBe("cancelled");
    expect(salesOrderListTabFromQuery({ tab: "semua", status: "histori" })).toBe("completed");
    expect(salesOrderListTabFromQuery({ tab: "semua", status: "aktif" })).toBe("active");
    expect(salesOrderListTabFromQuery({ tab: "semua", status: "hold" })).toBe("active");
    expect(salesOrderStatusFromQuery({ tab: "semua", status: "hold" })).toBe("hold");
    expect(salesOrderListTabFromQuery({ tab: "semua", status: "all" })).toBe("active");
    expect(salesOrderListTabFromQuery({ tab: "histori", status: "aktif" })).toBe("active");
    expect(salesOrderListTabFromQuery({ status: "hold" })).toBe("active");
  });
});

describe("creator and invoice status presentation", () => {
  test("creator uses snapshot and falls back to Tidak diketahui", () => {
    expect(orderCreatorLabel({ created_by_user: "Budi" })).toBe("Budi");
    expect(orderCreatorLabel({ created_by_user: "budi@example.com" })).toBe("budi@example.com");
    expect(orderCreatorLabel({ created_by_user: "" })).toBe("Tidak diketahui");
    expect(orderCreatorLabel({})).toBe("Tidak diketahui");
    expect(orderCreatorLabel({ sales: "Vira", created_by_user: "" })).toBe("Tidak diketahui");
  });

  test("invoice status labels and tones", () => {
    expect(orderInvoiceStatusLabel({ invoice_status: "belum" })).toBe("Belum");
    expect(orderInvoiceStatusLabel({ invoice_status: "sebagian" })).toBe("Sebagian");
    expect(orderInvoiceStatusLabel({ invoice_status: "sudah" })).toBe("Sudah");
    expect(orderInvoiceStatusLabel({ invoice_status: "perlu_pemeriksaan" })).toBe("Perlu Pemeriksaan");
    expect(orderInvoiceStatusLabel({})).toBe(null);
    expect(orderInvoiceStatusTone({ invoice_status: "sudah" })).toBe("done");
    expect(orderInvoiceStatusTone({ invoice_status: "sebagian" })).toBe("partial");
    expect(orderInvoiceStatusTone({ invoice_status: "perlu_pemeriksaan" })).toBe("review");
  });
});
