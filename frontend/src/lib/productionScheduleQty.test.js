import { formatIdQty, formatOperatorAssignedQty, OPERATOR_UNASSIGNED_LABEL, OPERATOR_WORK_QTY_UNAVAILABLE, printQtyProgress, scheduledPrintQtyProgress, totalQtyScheduledCell, operatorQtyCetakProgress, operatorPrintableItems, poTotalQtyDisplay, desktopPrintQtyDisplay, operatorWorkQtyCell, scheduledPrintQtyCell, operatorScheduleProgressCell } from "./productionScheduleQty";

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

  test("Total Qty keeps actual print progress when scheduling differs", () => {
    const row = {
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 2000,
      operator_assigned_qty: 5000,
      operators: [{ quantity: 5000, qty_completed: 2000 }],
    };
    expect(printQtyProgress(row).text).toBe("2.000 / 10.000");
  });
});

describe("Jadwal Produksi Total Qty = SUM active schedule per printable line", () => {
  test("TEST A: A 4.000 + B 1.000 = 5.000 once, not per operator", () => {
    const cell = totalQtyScheduledCell({
      total_quantity: 15000,
      print_qty_total: 5000,
      operators: [
        { operator_name: "A", operator_email: "a@x.com", quantity: 4000, line_idx: 0, line_printable: true, accurate_item_id: "PC-16" },
        { operator_name: "B", operator_email: "b@x.com", quantity: 1000, line_idx: 0, line_printable: true, accurate_item_id: "PC-16" },
      ],
    });
    expect(cell.groups).toHaveLength(1);
    expect(cell.groups[0].total).toBe(5000);
    expect(cell.text).toBe("5.000");
    expect(cell.text).not.toBe("4.000\n1.000");
    expect(cell.text).not.toBe("15.000");
  });

  test("TEST B: cancel without reassign uses active qty 3.000 + 1.000 = 4.000", () => {
    const cell = totalQtyScheduledCell({
      operators: [
        { operator_name: "A", quantity: 3000, line_idx: 0, line_printable: true, accurate_item_id: "PC-16" },
        { operator_name: "B", quantity: 1000, line_idx: 0, line_printable: true, accurate_item_id: "PC-16" },
      ],
    });
    expect(cell.text).toBe("4.000");
  });

  test("TEST C: reassign keeps line total 3.000 + 2.000 = 5.000", () => {
    const cell = totalQtyScheduledCell({
      operators: [
        { operator_name: "A", quantity: 3000, line_idx: 0, line_printable: true, accurate_item_id: "PC-16" },
        { operator_name: "B", quantity: 2000, line_idx: 0, line_printable: true, accurate_item_id: "PC-16" },
      ],
    });
    expect(cell.text).toBe("5.000");
  });

  test("non-print line is excluded", () => {
    const cell = totalQtyScheduledCell({
      total_quantity: 15000,
      operators: [
        { operator_name: "A", quantity: 6000, line_idx: 0, line_printable: true, accurate_item_id: "PRINT" },
        { operator_name: "A", quantity: 5000, line_idx: 1, line_printable: false, accurate_item_id: "NOPRINT" },
      ],
    });
    expect(cell.groups.map((g) => g.total)).toEqual([6000]);
    expect(cell.text).toBe("6.000");
    expect(cell.text).not.toContain("5.000");
  });

  test("two printable lines each appear once, not summed as PO total", () => {
    const cell = totalQtyScheduledCell({
      total_quantity: 15000,
      operators: [
        { operator_name: "A", quantity: 4000, line_idx: 0, line_printable: true, accurate_item_id: "A" },
        { operator_name: "B", quantity: 1000, line_idx: 0, line_printable: true, accurate_item_id: "A" },
        { operator_name: "C", quantity: 2000, line_idx: 1, line_printable: true, accurate_item_id: "B" },
      ],
    });
    expect(cell.groups.map((g) => g.total)).toEqual([5000, 2000]);
    expect(cell.text).toBe("5.000\n2.000");
    expect(cell.text).not.toBe("7.000");
    expect(cell.text).not.toBe("15.000");
  });

  test("CASE 2: three operators on one line still one total 5.000", () => {
    const cell = totalQtyScheduledCell({
      operators: [
        { operator_name: "A", quantity: 2000, line_idx: 0, line_id: "line-a", line_printable: true },
        { operator_name: "B", quantity: 2000, line_idx: 0, line_id: "line-a", line_printable: true },
        { operator_name: "C", quantity: 1000, line_idx: 0, line_id: "line-a", line_printable: true },
      ],
    });
    expect(cell.groups).toHaveLength(1);
    expect(cell.groups[0].operators).toHaveLength(3);
    expect(cell.text).toBe("5.000");
    expect(cell.text.split("\n")).toEqual(["5.000"]);
  });

  test("cancelled qty uses remaining assignment, not original_quantity", () => {
    const cell = totalQtyScheduledCell({
      operators: [
        { operator_name: "A", quantity: 3000, original_quantity: 4000, qty_cancelled: 1000, line_idx: 0, line_id: "L1", line_printable: true },
        { operator_name: "B", quantity: 1000, original_quantity: 1000, line_idx: 0, line_id: "L1", line_printable: true },
      ],
    });
    expect(cell.text).toBe("4.000");
    expect(cell.text).not.toBe("5.000");
  });

  test("CASE 5: same item / SKU on two PO lines stays 5.000 and 3.000", () => {
    const cell = totalQtyScheduledCell({
      operators: [
        { operator_name: "A", quantity: 4000, line_idx: 0, line_id: "L1", accurate_item_id: "CUP", line_printable: true },
        { operator_name: "B", quantity: 1000, line_idx: 0, line_id: "L1", accurate_item_id: "CUP", line_printable: true },
        { operator_name: "C", quantity: 3000, line_idx: 1, line_id: "L2", accurate_item_id: "CUP", line_printable: true },
      ],
    });
    expect(cell.groups.map((g) => g.total)).toEqual([5000, 3000]);
    expect(cell.text).toBe("5.000\n3.000");
    expect(cell.text).not.toBe("8.000");
  });
});

describe("Jadwal Produksi Qty Cetak scheduled progress", () => {
  test("5.000 / 10.000 not PO 15.000", () => {
    const p = scheduledPrintQtyProgress({
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 2000,
      operators: [{ quantity: 5000, qty_completed: 2000 }],
    });
    expect(p.text).toBe("5.000 / 10.000");
    expect(p.text).not.toBe("5.000 / 15.000");
    expect(p.text).not.toBe("2.000 / 10.000");
  });

  test("uses operator_assigned_qty when present", () => {
    const p = scheduledPrintQtyProgress({
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 2000,
      operator_assigned_qty: 5000,
      operators: [{ quantity: 5000, qty_completed: 2000 }, { quantity: 5000, qty_completed: 0 }],
    });
    expect(p.text).toBe("5.000 / 10.000");
  });

  test("ignores actual operator print as numerator", () => {
    const p = scheduledPrintQtyProgress({
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 2000,
      operators: [{ quantity: 5000, qty_completed: 2000 }],
    });
    expect(p.scheduled).toBe(5000);
    expect(p.target).toBe(10000);
  });

  test("partial multi-line assignments sum current splits only", () => {
    const p = scheduledPrintQtyProgress({
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 0,
      operators: [
        { quantity: 4000, qty_completed: 0, line_idx: 0 },
        { quantity: 1000, qty_completed: 0, line_idx: 1 },
      ],
    });
    expect(p.text).toBe("5.000 / 10.000");
  });

  test("edit assignment replaces value, does not double-count", () => {
    const row = {
      total_quantity: 15000,
      print_qty_total: 10000,
      print_completed_qty: 0,
      operators: [{ quantity: 6000, qty_completed: 0 }],
    };
    expect(scheduledPrintQtyProgress(row).text).toBe("6.000 / 10.000");
    expect(scheduledPrintQtyProgress(row).text).toBe("6.000 / 10.000");
  });

  test("no printable items shows em dash", () => {
    const p = scheduledPrintQtyProgress({
      total_quantity: 5000,
      print_qty_total: 0,
      print_completed_qty: 0,
      operators: [],
    });
    expect(p.text).toBe("—");
  });
});

describe("operator Qty Cetak display", () => {
  test("formats completed / PO total", () => {
    expect(operatorQtyCetakProgress(
      { total_quantity: 20000 },
      [{ split_id: "s1", line_printable: true, qty_completed: 3000 }],
    ).text).toBe("3.000 / 20.000");
  });

  test("zero progress still shows PO total", () => {
    expect(operatorQtyCetakProgress(
      { total_quantity: 20000 },
      [{ split_id: "s1", line_printable: true, qty_completed: 0 }],
    ).text).toBe("0 / 20.000");
  });

  test("sums completed qty across printable splits of one operator", () => {
    expect(operatorQtyCetakProgress(
      { total_quantity: 20000 },
      [
        { split_id: "s1", line_printable: true, qty_completed: 1000 },
        { split_id: "s2", line_printable: true, qty_completed: 2000 },
      ],
    ).text).toBe("3.000 / 20.000");
  });

  test("does not treat assignment quantity as completed", () => {
    expect(operatorQtyCetakProgress(
      { total_quantity: 20000 },
      [{ split_id: "s1", line_printable: true, quantity: 5000, qty_completed: 3000 }],
    ).text).toBe("3.000 / 20.000");
  });

  test("excludes non-cetak splits from completed qty and items", () => {
    const splits = [
      { split_id: "s1", line_printable: true, line_name: "Cup", qty_completed: 3000 },
      { split_id: "s2", line_printable: false, line_name: "Lid", qty_completed: 4000 },
    ];
    expect(operatorQtyCetakProgress({ total_quantity: 20000 }, splits).text).toBe("3.000 / 20.000");
    expect(operatorPrintableItems(splits).map((item) => item.name)).toEqual(["Cup"]);
  });

  test("same-name items stay distinct by line identity", () => {
    const items = operatorPrintableItems([
      { split_id: "s1", line_printable: true, line_name: "Cup 16oz", line_id: "lid-a" },
      { split_id: "s2", line_printable: true, line_name: "Cup 16oz", line_id: "lid-b" },
    ]);
    expect(items).toHaveLength(2);
    expect(items[0].line_id).toBe("lid-a");
    expect(items[1].line_id).toBe("lid-b");
  });

  test("Total Qty is PO total without item names", () => {
    const cell = poTotalQtyDisplay({ total_quantity: 20000 });
    expect(cell.text).toBe("20.000");
    expect(cell.text).not.toMatch(/Cup|Item/);
  });

  test("desktop cells use split quantity and reported qty_completed, not PO aggregates", () => {
    const row = {
      total_quantity: 25000,
      print_qty_total: 20000,
      operator_assigned_qty: 8000,
      print_completed_qty: 100,
      displayOperator: {
        splits: [{ quantity: 10000, qty_completed: 3000, qty_reported: true, line_printable: true }],
      },
    };
    expect(scheduledPrintQtyCell(row).text).toBe("10.000");
    expect(operatorWorkQtyCell(row).text).toBe("3.000");
    expect(operatorScheduleProgressCell(row).text).toBe("30%");
    expect(desktopPrintQtyDisplay(row).text).toBe("20.000");
    expect(desktopPrintQtyDisplay(row).text).not.toBe("25.000");
    expect(scheduledPrintQtyCell(row).text).not.toBe("20.000");
    expect(scheduledPrintQtyCell(row).text).not.toBe("8.000");
    expect(operatorWorkQtyCell(row).text).not.toBe("100");
  });

  test("unreported qty is a dash and a reported zero stays zero", () => {
    const missing = {
      displayOperator: { splits: [{ quantity: 10000, qty_completed: null }] },
    };
    expect(operatorWorkQtyCell(missing).text).toBe("-");
    expect(operatorWorkQtyCell(missing).title).toBe(OPERATOR_WORK_QTY_UNAVAILABLE);
    expect(operatorScheduleProgressCell(missing).text).toBe("-");
    const absent = {
      displayOperator: { splits: [{ quantity: 500, qty_completed: 0 }] },
    };
    expect(operatorWorkQtyCell(absent).text).toBe("-");
    expect(operatorScheduleProgressCell(absent).text).toBe("-");
    const zero = {
      displayOperator: { splits: [{ quantity: 500, qty_completed: 0, qty_reported: true }] },
    };
    expect(operatorWorkQtyCell(zero).text).toBe("0");
    expect(operatorScheduleProgressCell(zero).text).toBe("0%");
    const partial = {
      displayOperator: { splits: [{ quantity: 500, qty_completed: 200, qty_reported: true }] },
    };
    expect(operatorWorkQtyCell(partial).text).toBe("200");
    expect(operatorScheduleProgressCell(partial).text).toBe("40%");
    expect(desktopPrintQtyDisplay({ total_quantity: 20000, print_qty_total: 10000 }).text).toBe("10.000");
  });

  test("progress is hidden when scheduled qty is missing or zero", () => {
    expect(scheduledPrintQtyCell({
      displayOperator: { splits: [{ qty_completed: 10 }] },
    }).text).toBe("-");
    expect(operatorScheduleProgressCell({
      displayOperator: { splits: [{ quantity: 0, qty_completed: 0, qty_reported: true }] },
    }).text).toBe("-");
    expect(operatorScheduleProgressCell({
      total_quantity: 5000,
      print_qty_total: 5000,
      displayOperator: null,
    }).text).toBe("-");
  });

  test("operator assigned qty is formatted separately from PO total", () => {
    expect(formatOperatorAssignedQty(5000)).toBe("5.000 pcs");
    expect(formatOperatorAssignedQty(7000)).toBe("7.000 pcs");
    expect(formatOperatorAssignedQty(null)).toBe("");
    expect(OPERATOR_UNASSIGNED_LABEL).toBe("Belum ditugaskan");
  });
});
