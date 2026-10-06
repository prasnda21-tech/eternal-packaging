import {
  EMPTY_SALES_ORDER_COLUMN_FILTERS,
  SALES_ORDER_DELIVERY_STATUS_OPTIONS,
  salesOrderColumnFilterActive,
  salesOrderColumnFiltersAreActive,
  salesOrderDeliveryStatus,
} from "./pesananPenjualanColumnFilters";

describe("pesananPenjualanColumnFilters selectable model", () => {
  test("empty filters inactive", () => {
    expect(salesOrderColumnFiltersAreActive(EMPTY_SALES_ORDER_COLUMN_FILTERS, "all")).toBe(false);
  });

  test("tanggal kirim / delivery / sales active flags", () => {
    expect(salesOrderColumnFilterActive("tanggalKirim", {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      tanggalKirimPreset: "today",
    })).toBe(true);
    expect(salesOrderColumnFilterActive("delivery", {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      deliveryStatuses: ["SEBAGIAN"],
    })).toBe(true);
    expect(salesOrderColumnFilterActive("sales", {
      ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
      sales: "Andi",
    })).toBe(true);
  });

  test("delivery status options are normalized business values", () => {
    expect(SALES_ORDER_DELIVERY_STATUS_OPTIONS).toEqual([
      { value: "BELUM DIKIRIM", label: "Belum dikirim" },
      { value: "SEBAGIAN", label: "Sebagian" },
      { value: "SELESAI", label: "Selesai" },
      { value: "DIBATALKAN", label: "Dibatalkan" },
    ]);
    expect(salesOrderDeliveryStatus({ cancelled: true })).toBe("DIBATALKAN");
  });
});
