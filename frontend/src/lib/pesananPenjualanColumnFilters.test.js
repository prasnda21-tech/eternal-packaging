import {
  EMPTY_SALES_ORDER_COLUMN_FILTERS,
  applySalesOrderColumnFilters,
  salesOrderColumnFilterActive,
  salesOrderColumnFiltersAreActive,
  salesOrderDeliveryStatus,
  salesOrderIsoDate,
  salesOrderRemainingQty,
  salesOrderStageLabel,
} from "./pesananPenjualanColumnFilters";

const sampleOrders = [
  {
    id: "1",
    po_number: "SO-1001",
    order_date: "2026-04-01",
    customer: "PT Maju Jaya",
    outlet: "Jakarta Pusat",
    sales: "Andi",
    total_quantity: 100,
    delivery_summary: { total_remaining: 40, status: "SEBAGIAN" },
    current_stage: 5,
    status_label: "Produksi",
  },
  {
    id: "2",
    po_number: "SO-2002",
    order_date: "2026-04-10",
    customer: "CV Sejahtera",
    outlet: "Bandung",
    sales_user: "Budi",
    total_quantity: 20,
    cancelled: true,
    current_stage: 2,
  },
  {
    id: "3",
    po_number: "SO-3003",
    order_date: "2026-04-15T00:00:00Z",
    customer: "PT Maju Sentosa",
    outlet: "Jakarta Selatan",
    sales: "Andi",
    total_quantity: 50,
    hold: true,
    delivery_summary: { total_remaining: 50, status: "BELUM DIKIRIM" },
    current_stage: 3,
    status_label: "HOLD",
  },
];

describe("Pesanan Penjualan column filters", () => {
  test("normalizes ISO dates and remaining qty / labels", () => {
    expect(salesOrderIsoDate("2026-04-15T08:00:00Z")).toBe("2026-04-15");
    expect(salesOrderRemainingQty(sampleOrders[0])).toBe(40);
    expect(salesOrderRemainingQty(sampleOrders[1])).toBe(20);
    expect(salesOrderDeliveryStatus(sampleOrders[1])).toBe("DIBATALKAN");
    expect(salesOrderStageLabel(sampleOrders[1])).toBe("Cancelled");
    expect(salesOrderStageLabel(sampleOrders[2])).toBe("HOLD");
  });

  test("text filters are case-insensitive contains matches", () => {
    const filtered = applySalesOrderColumnFilters(sampleOrders, {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      customer: "maju",
    });
    expect(filtered.map((o) => o.id)).toEqual(["1", "3"]);
  });

  test("date range and numeric min/max compose with text", () => {
    const filtered = applySalesOrderColumnFilters(sampleOrders, {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      dateFrom: "2026-04-10",
      dateTo: "2026-04-15",
      qtyMin: "20",
      qtyMax: "50",
      sales: "andi",
    });
    expect(filtered.map((o) => o.id)).toEqual(["3"]);
  });

  test("delivery checklist and stage text filter independently", () => {
    const byDelivery = applySalesOrderColumnFilters(sampleOrders, {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      deliveryStatuses: ["SEBAGIAN"],
    });
    expect(byDelivery.map((o) => o.id)).toEqual(["1"]);

    const byStage = applySalesOrderColumnFilters(sampleOrders, {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      stageText: "cancel",
    });
    expect(byStage.map((o) => o.id)).toEqual(["2"]);
  });

  test("active helpers detect per-column and combined filters", () => {
    const filters = { ...EMPTY_SALES_ORDER_COLUMN_FILTERS, po: "SO-1" };
    expect(salesOrderColumnFilterActive("po", filters)).toBe(true);
    expect(salesOrderColumnFilterActive("customer", filters)).toBe(false);
    expect(salesOrderColumnFiltersAreActive(filters, "all")).toBe(true);
    expect(salesOrderColumnFiltersAreActive(EMPTY_SALES_ORDER_COLUMN_FILTERS, "hold")).toBe(true);
    expect(salesOrderColumnFiltersAreActive(EMPTY_SALES_ORDER_COLUMN_FILTERS, "all")).toBe(false);
  });
});
