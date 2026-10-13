import {
  EMPTY_SALES_ORDER_COLUMN_FILTERS,
  SALES_ORDER_DELIVERY_STATUS_OPTIONS,
  salesOrderColumnFilterActive,
  salesOrderColumnFilterValidation,
  salesOrderColumnFiltersAreActive,
  salesOrderDeliveryStatus,
  salesOrderStageLabel,
} from "./pesananPenjualanColumnFilters";

describe("pesananPenjualanColumnFilters selectable model", () => {
  test("empty filters inactive", () => {
    expect(salesOrderColumnFiltersAreActive(EMPTY_SALES_ORDER_COLUMN_FILTERS)).toBe(false);
  });

  test("date / delivery / sales / process / invoice active flags", () => {
    expect(salesOrderColumnFilterActive("shipDate", {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      shipDateFrom: "2026-10-01",
    })).toBe(true);
    expect(salesOrderColumnFilterActive("delivery", {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      deliveryStatuses: ["SEBAGIAN"],
    })).toBe(true);
    expect(salesOrderColumnFilterActive("sales", {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      sales: "Andi",
    })).toBe(true);
    expect(salesOrderColumnFilterActive("process", {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      processLabels: ["HOLD"],
    })).toBe(true);
    expect(salesOrderColumnFilterActive("invoice", {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      invoiceStatuses: ["sudah"],
    })).toBe(true);
  });

  test("date and qty validation", () => {
    expect(salesOrderColumnFilterValidation({
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      orderDateFrom: "2026-10-12",
      orderDateTo: "2026-10-01",
    })).toMatch(/Tanggal PO/);
    expect(salesOrderColumnFilterValidation({
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      qtyMin: "20",
      qtyMax: "5",
    })).toMatch(/Qty/);
    expect(salesOrderColumnFilterValidation(EMPTY_SALES_ORDER_COLUMN_FILTERS)).toBe("");
  });

  test("delivery status options and process label priority", () => {
    expect(SALES_ORDER_DELIVERY_STATUS_OPTIONS.map((o) => o.value)).toEqual([
      "BELUM DIKIRIM",
      "SEBAGIAN",
      "SELESAI",
      "DIBATALKAN",
    ]);
    expect(salesOrderDeliveryStatus({ cancelled: true })).toBe("DIBATALKAN");
    expect(salesOrderStageLabel({ cancelled: true, hold: true, current_stage: 1 })).toBe("Cancelled");
    expect(salesOrderStageLabel({ hold: true, current_stage: 1 })).toBe("HOLD");
    expect(salesOrderStageLabel({ current_stage: 5 })).toBe("Penjadwalan");
  });
});
