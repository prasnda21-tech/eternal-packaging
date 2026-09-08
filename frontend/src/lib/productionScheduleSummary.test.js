import { buildProductionScheduleSummary } from "./productionScheduleSummary";

describe("production schedule Ringkasan metrics", () => {
  test("multi-operator PO counts once for PO and Total Qty", () => {
    const rows = [{
      id: "po-1",
      po_number: "PO-1",
      total_quantity: 20000,
      print_qty_total: 20000,
      operators: [
        { split_id: "a", operator_user_id: "u-a", line_printable: true, qty_completed: 1000 },
        { split_id: "b", operator_user_id: "u-b", line_printable: true, qty_completed: 2000 },
      ],
    }];
    const summary = buildProductionScheduleSummary(rows);
    expect(summary.jumlahPo).toBe(1);
    expect(summary.totalQtyPesanan).toBe(20000);
    expect(summary.qtySudahDicetak).toBe(3000);
    expect(summary.sisaQtyCetak).toBe(17000);
    expect(summary.poBelumSelesaiCetak).toBe(1);
    expect(summary.poSelesaiCetak).toBe(0);
    expect(summary.operatorCount).toBe(2);
  });

  test("remaining is max(total - printed, 0)", () => {
    const summary = buildProductionScheduleSummary([{
      id: "po-1",
      total_quantity: 20000,
      print_qty_total: 20000,
      operators: [{ split_id: "a", operator_user_id: "u-a", line_printable: true, qty_completed: 3000 }],
    }]);
    expect(summary.sisaQtyCetak).toBe(17000);
  });

  test("does not double-count duplicate split identities", () => {
    const summary = buildProductionScheduleSummary([{
      id: "po-1",
      total_quantity: 20000,
      print_qty_total: 20000,
      operators: [
        { split_id: "s1", operator_user_id: "u-a", line_printable: true, qty_completed: 3000 },
        { split_id: "s1", operator_user_id: "u-a", line_printable: true, qty_completed: 3000 },
      ],
    }]);
    expect(summary.qtySudahDicetak).toBe(3000);
  });

  test("non-cetak completed qty is excluded", () => {
    const summary = buildProductionScheduleSummary([{
      id: "po-1",
      total_quantity: 20000,
      print_qty_total: 20000,
      operators: [
        { split_id: "s1", operator_user_id: "u-a", line_printable: true, qty_completed: 3000 },
        { split_id: "s2", operator_user_id: "u-b", line_printable: false, qty_completed: 8000 },
      ],
    }]);
    expect(summary.qtySudahDicetak).toBe(3000);
    expect(summary.operatorCount).toBe(1);
  });

  test("PO is cetak-complete when printable completed reaches print_qty_total", () => {
    const summary = buildProductionScheduleSummary([{
      id: "po-1",
      total_quantity: 20000,
      print_qty_total: 20000,
      operators: [{ split_id: "s1", operator_user_id: "u-a", line_printable: true, qty_completed: 20000 }],
    }]);
    expect(summary.poSelesaiCetak).toBe(1);
    expect(summary.poBelumSelesaiCetak).toBe(0);
  });
});
