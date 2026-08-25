import { formatIdQty, printQtyProgress } from "./productionScheduleQty";

describe("Jadwal Produksi Total Qty print progress", () => {
  test("TEST 1: 5.000 / 10.000 not PO 15.000", () => {
    const p = printQtyProgress({
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 5000,
    });
    expect(p.text).toBe("5.000 / 10.000");
    expect(p.text).not.toBe("5.000 / 15.000");
    expect(p.text).not.toBe(formatIdQty(15000));
    expect(p.over).toBe(false);
  });

  test("TEST 2: zero actual still shows 0 / target", () => {
    const p = printQtyProgress({
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 0,
    });
    expect(p.text).toBe("0 / 10.000");
  });

  test("TEST 3: complete print 10.000 / 10.000", () => {
    const p = printQtyProgress({
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 10000,
    });
    expect(p.text).toBe("10.000 / 10.000");
  });

  test("TEST 4: multi-line aggregation uses backend totals only", () => {
    const p = printQtyProgress({
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 5000,
    });
    expect(p.text).toBe("5.000 / 10.000");
  });

  test("TEST 5: repeated values do not double-count", () => {
    const row = { print_qty_total: 10000, print_completed_qty: 5000, total_quantity: 15000 };
    expect(printQtyProgress(row).text).toBe("5.000 / 10.000");
    expect(printQtyProgress(row).text).toBe("5.000 / 10.000");
  });

  test("no printable items shows em dash", () => {
    const p = printQtyProgress({
      total_quantity: 5000,
      print_qty_total: 0,
      print_completed_qty: 0,
    });
    expect(p.text).toBe("—");
  });

  test("actual above target is not clamped", () => {
    const p = printQtyProgress({
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 12000,
    });
    expect(p.text).toBe("12.000 / 10.000");
    expect(p.over).toBe(true);
  });

  test("does not fall back to PO total as denominator", () => {
    const p = printQtyProgress({
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 0,
    });
    expect(p.target).toBe(10000);
    expect(p.poTotal).toBe(15000);
  });
});
