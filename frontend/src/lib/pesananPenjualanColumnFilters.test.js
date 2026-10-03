import { STAGES } from "./constants";
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

  test("badge priority uses canonical stage names", () => {
    expect(STAGES[0]).toBe("Order Masuk");
    expect(STAGES[5]).toBe("Penjadwalan");
    expect(STAGES[13]).toBe("Loading");
    expect(STAGES[14]).toBe("Pengiriman");

    expect(salesOrderStageLabel({
      cancelled: true,
      hold: true,
      current_stage: 5,
      status_label: "Penjadwalan",
    })).toBe("Cancelled");

    expect(salesOrderStageLabel({
      hold: true,
      current_stage: 5,
      status_label: "Penjadwalan",
    })).toBe("HOLD");

    expect(salesOrderStageLabel({ current_stage: 5 })).toBe("Penjadwalan");
    expect(salesOrderStageLabel({
      current_stage: 5,
      status_label: "Produksi",
    })).toBe("Penjadwalan");
    expect(salesOrderStageLabel(sampleOrders[0])).toBe("Penjadwalan");

    expect(salesOrderStageLabel({ current_stage: 0 })).toBe("Order Masuk");
    expect(salesOrderStageLabel({
      current_stage: 0,
      status_label: "",
    })).toBe("Order Masuk");
    expect(salesOrderStageLabel({
      current_stage: 0,
      status_label: "Draft",
    })).toBe("Order Masuk");

    expect(salesOrderStageLabel({
      current_stage: 13,
      status_label: "Pengiriman",
      delivery_status: "Sebagian Terkirim",
      qty_delivered: 4,
      total_quantity: 10,
    })).toBe("Loading");
    expect(salesOrderStageLabel({
      current_stage: 14,
      status_label: "Selesai",
      delivery_status: "Terkirim",
      qty_delivered: 10,
      total_quantity: 10,
    })).toBe("Pengiriman");
  });

  test("invalid stage does not become Order Masuk and falls back to a filled label", () => {
    const invalidStages = [
      null,
      undefined,
      "",
      " ",
      "   ",
      "\t",
      "\n",
      "0",
      "5",
      "  0  ",
      "abc",
      Number.NaN,
      5.5,
      -1,
      15,
      99,
      true,
      false,
    ];
    invalidStages.forEach((current_stage) => {
      expect(salesOrderStageLabel({ current_stage, status_label: "Label Lama" })).toBe("Label Lama");
      expect(salesOrderStageLabel({ current_stage })).toBe("");
      expect(salesOrderStageLabel({ current_stage, status_label: "" })).toBe("");
      expect(salesOrderStageLabel({ current_stage, status_label: null })).toBe("");
    });
    expect(salesOrderStageLabel({})).toBe("");
  });

  test("stage text filter matches the badge label on every tab payload", () => {
    const order = {
      id: "stage-5",
      po_number: "SO-STAGE",
      current_stage: 5,
      status_label: "Produksi",
    };
    const aktif = salesOrderStageLabel(order);
    const histori = salesOrderStageLabel(order);
    const semua = salesOrderStageLabel(order);
    expect(aktif).toBe("Penjadwalan");
    expect(histori).toBe(aktif);
    expect(semua).toBe(aktif);

    const rows = [
      order,
      {
        id: "partial",
        po_number: "SO-PART",
        current_stage: 13,
        status_label: "Pengiriman",
        delivery_status: "Sebagian Terkirim",
      },
      {
        id: "full",
        po_number: "SO-FULL",
        current_stage: 14,
        status_label: "Selesai",
      },
      {
        id: "legacy",
        po_number: "SO-OLD",
        current_stage: " ",
        status_label: "Label Lama",
      },
    ];
    const idsFor = (stageText) => applySalesOrderColumnFilters(rows, {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      stageText,
    }).map((row) => row.id);

    expect(idsFor("Penjadwalan")).toEqual(["stage-5"]);
    expect(idsFor("produksi")).toEqual([]);
    expect(idsFor("Loading")).toEqual(["partial"]);
    expect(idsFor("Pengiriman")).toEqual(["full"]);
    expect(idsFor("Selesai")).toEqual([]);
    expect(idsFor("Label Lama")).toEqual(["legacy"]);
    expect(idsFor(aktif)).toEqual(["stage-5"]);
  });
});
