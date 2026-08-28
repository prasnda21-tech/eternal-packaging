import { canSubmitLoadingIncrement, loadingStatusText, shipmentLinesForOrder } from "./shipmentLines";

describe("shipmentLinesForOrder", () => {
  test("uses backend shipment_lines when present", () => {
    const rows = shipmentLinesForOrder({
      shipment_lines: [{ line_idx: 0, name: "A", packed: 4000, shipped: 4000, available_sj: 0 }],
    });
    expect(rows[0].packed).toBe(4000);
    expect(rows[0].available_sj).toBe(0);
  });

  test("derives available SJ from packed minus shipped", () => {
    const rows = shipmentLinesForOrder({
      line_items: [{ name: "A", quantity: 10000 }],
      packing_info: { lines: [{ line_idx: 0, packed_qty: 7000 }] },
      packing_lines: [{ line_idx: 0, name: "A", packed: 7000, qty_po: 10000 }],
      shipments: [{ lines: [{ line_idx: 0, qty: 4000 }] }],
    });
    expect(rows[0].available_sj).toBe(3000);
    expect(rows[0].shipped).toBe(4000);
    expect(rows[0].loading_cap).toBe(10000);
    expect(rows[0].available_loading).toBe(10000);
  });

  test("loading fallback uses PO qty when packing confirmed qty is empty", () => {
    const rows = shipmentLinesForOrder({
      line_items: [{ name: "A", quantity: 500 }],
      packing_info: { qty_packed: 0 },
    });
    expect(rows[0].packed).toBe(0);
    expect(rows[0].loading_cap).toBe(500);
    expect(rows[0].available_loading).toBe(500);
  });

  test("loading status shows actual qty, not a checkbox label", () => {
    expect(loadingStatusText(0)).toBe("Belum Loading");
    expect(loadingStatusText(2500)).toBe("2.500");
    expect(loadingStatusText(4000)).toBe("4.000");
  });

  test("loading increment submit requires qty > 0 and <= available", () => {
    expect(canSubmitLoadingIncrement("", 4000)).toBe(false);
    expect(canSubmitLoadingIncrement("0", 4000)).toBe(false);
    expect(canSubmitLoadingIncrement("2500", 4000)).toBe(true);
    expect(canSubmitLoadingIncrement("4001", 4000)).toBe(false);
    expect(canSubmitLoadingIncrement("1", 0)).toBe(false);
  });
});
