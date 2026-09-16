import { FILTER_DEFAULTS, applyProductionFilters } from "./productionScheduleFilters";
import { operatorQtyCetakProgress, poTotalQtyDisplay } from "./productionScheduleQty";
import {
  buildProductionScheduleDisplayRows,
  productionScheduleAssignmentKey,
  productionScheduleDisplayRowKey,
  productionScheduleOperatorIdentity,
} from "./productionScheduleDisplayRows";

const poThree = {
  id: "po-1",
  po_number: "PO-001",
  customer: "Customer A",
  outlet: "Tebet",
  deadline: "2026-09-10",
  work_date: "2026-09-01",
  current_stage: 8,
  total_quantity: 3000,
  print_qty_total: 3000,
  operator_assigned_qty: 3000,
  operators: [
    { split_id: "s-c", operator_email: "cici@x.com", operator_name: "Cici", quantity: 800, line_idx: 1, line_id: "lid-b", line_name: "Lid", line_printable: true, work_date: "2026-09-03" },
    { split_id: "s-a", operator_email: "budi@x.com", operator_name: "Budi", quantity: 1000, line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz", line_printable: true, work_date: "2026-09-01" },
    { split_id: "s-b", operator_email: "andi@x.com", operator_name: "Andi", quantity: 1200, line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz", line_printable: true, work_date: "2026-09-02" },
  ],
};

describe("buildProductionScheduleDisplayRows", () => {
  test("one PO + one operator + one split → 1 row", () => {
    const rows = [{
      id: "po-1",
      po_number: "PO-1",
      operators: [{ split_id: "s1", operator_email: "budi@x.com", operator_name: "Budi", quantity: 500, line_id: "lid-a", line_idx: 0 }],
    }];
    const display = buildProductionScheduleDisplayRows(rows);
    expect(display).toHaveLength(1);
    expect(display[0].displayOperator.operator_name).toBe("Budi");
    expect(display[0].id).toBe("po-1");
  });

  test("one PO + three different operators → 3 rows", () => {
    const display = buildProductionScheduleDisplayRows([poThree]);
    expect(display).toHaveLength(3);
    expect(display.map((row) => row.displayOperator.operator_name)).toEqual(["Budi", "Andi", "Cici"]);
    display.forEach((row) => {
      expect(row.displayOperator).toBeTruthy();
      expect(row.po_number).toBe("PO-001");
      expect(row.id).toBe("po-1");
      expect(row.customer).toBe("Customer A");
      expect(row.deadline).toBe("2026-09-10");
    });
    const keys = display.map((row) => row.displayRowKey);
    expect(new Set(keys).size).toBe(3);
    expect(keys[0]).toBe(productionScheduleDisplayRowKey("po-1", "email:budi@x.com", "s-a"));
  });

  test("canonical PO count stays 1 while display rows are 3", () => {
    const canonical = [poThree];
    expect(canonical).toHaveLength(1);
    expect(applyProductionFilters(canonical, FILTER_DEFAULTS)).toHaveLength(1);
    expect(buildProductionScheduleDisplayRows(canonical)).toHaveLength(3);
  });

  test("no operator keeps one row", () => {
    const display = buildProductionScheduleDisplayRows([{ id: "po-empty", po_number: "PO-X", operators: [] }]);
    expect(display).toHaveLength(1);
    expect(display[0].displayOperator).toBeNull();
    expect(display[0].displayOperatorKey).toBe("unassigned");
  });

  test("same-name product lines do not merge when operators differ", () => {
    const row = {
      id: "po-lines",
      po_number: "PO-L",
      operators: [
        { split_id: "s1", operator_email: "budi@x.com", operator_name: "Budi", line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz", quantity: 1000 },
        { split_id: "s2", operator_email: "sari@x.com", operator_name: "Sari", line_idx: 2, line_id: "lid-b", line_name: "Cup 16oz", quantity: 500 },
      ],
    };
    const display = buildProductionScheduleDisplayRows([row]);
    expect(display).toHaveLength(2);
    expect(display[0].displayOperator.line_id).toBe("lid-a");
    expect(display[1].displayOperator.line_id).toBe("lid-b");
    expect(display[0].displayOperator.line_idx).toBe(0);
    expect(display[1].displayOperator.line_idx).toBe(2);
    expect(productionScheduleAssignmentKey(row.operators[0], 0)).not.toBe(productionScheduleAssignmentKey(row.operators[1], 1));
  });

  test("same operator across two item lines → 2 rows with repeated name and line qty", () => {
    const row = {
      id: "po-same",
      po_number: "PO-S",
      operators: [
        { split_id: "s1", operator_email: "nanda@x.com", operator_user_id: "u-nanda", operator_name: "Nanda Operator", line_idx: 0, line_id: "lid-a", line_name: "Plastik PE 28", quantity: 2000 },
        { split_id: "s2", operator_email: "nanda@x.com", operator_user_id: "u-nanda", operator_name: "Nanda Operator", line_idx: 2, line_id: "lid-b", line_name: "Plastik PE 28", quantity: 2600 },
      ],
    };
    const display = buildProductionScheduleDisplayRows([row]);
    expect(display).toHaveLength(2);
    expect(display.map((r) => r.displayOperator.operator_name)).toEqual(["Nanda Operator", "Nanda Operator"]);
    expect(display.map((r) => r.displayOperator.quantity)).toEqual([2000, 2600]);
    expect(display.map((r) => r.displayOperator.line_id)).toEqual(["lid-a", "lid-b"]);
    expect(display.map((r) => r.displayOperator.splits)).toEqual([
      [row.operators[0]],
      [row.operators[1]],
    ]);
    expect(display[0].displayRowKey).not.toBe(display[1].displayRowKey);
    expect(display[0].po_number).toBe("PO-S");
    expect(display[1].po_number).toBe("PO-S");
  });

  test("same display name but different stable operator IDs → 2 rows", () => {
    const row = {
      id: "po-dup-name",
      po_number: "PO-D",
      operators: [
        { split_id: "s1", operator_email: "nanda1@x.com", operator_user_id: "u-1", operator_name: "Nanda Operator", quantity: 10 },
        { split_id: "s2", operator_email: "nanda2@x.com", operator_user_id: "u-2", operator_name: "Nanda Operator", quantity: 20 },
      ],
    };
    const display = buildProductionScheduleDisplayRows([row]);
    expect(display).toHaveLength(2);
    expect(display[0].displayOperatorKey).not.toBe(display[1].displayOperatorKey);
    expect(productionScheduleOperatorIdentity(row.operators[0])).toBe("uid:u-1");
    expect(productionScheduleOperatorIdentity(row.operators[1])).toBe("uid:u-2");
  });

  test("Total Qty stays PO-level; Qty Cetak is operator completed / PO total", () => {
    const display = buildProductionScheduleDisplayRows([{
      ...poThree,
      operators: poThree.operators.map((op, i) => ({ ...op, qty_completed: i === 0 ? 800 : i === 1 ? 1000 : 1200 })),
    }]);
    display.forEach((row) => {
      expect(poTotalQtyDisplay(row).text).toBe("3.000");
    });
    expect(operatorQtyCetakProgress(display[0], display[0].displayOperator.splits).text).toBe("1.000 / 3.000");
    expect(operatorQtyCetakProgress(display[1], display[1].displayOperator.splits).text).toBe("1.200 / 3.000");
    expect(operatorQtyCetakProgress(display[2], display[2].displayOperator.splits).text).toBe("800 / 3.000");
  });

  test("search/filter runs on canonical rows before expansion", () => {
    const rows = [poThree, { id: "po-2", po_number: "PO-002", customer: "Other", operators: [{ split_id: "z", operator_name: "Dedi" }] }];
    const filtered = applyProductionFilters(rows, { ...FILTER_DEFAULTS, q: "PO-001" });
    expect(filtered).toHaveLength(1);
    const display = buildProductionScheduleDisplayRows(filtered);
    expect(display).toHaveLength(3);
    expect(display.every((row) => row.po_number === "PO-001")).toBe(true);
  });

  test("identical item names keep separate qty and do not aggregate", () => {
    const row = {
      id: "po-pe",
      po_number: "PO-PE",
      customer: "Customer A",
      operators: [
        { split_id: "s1", operator_user_id: "u-isma", operator_name: "Isma Nur Alamsyah", line_name: "Plastik PE 28", line_id: "lid-a", line_idx: 0, line_printable: true, quantity: 2000 },
        { split_id: "s2", operator_user_id: "u-isma", operator_name: "Isma Nur Alamsyah", line_name: "Plastik PE 28", line_id: "lid-b", line_idx: 1, line_printable: true, quantity: 2600 },
      ],
    };
    const display = buildProductionScheduleDisplayRows([row]);
    expect(display).toHaveLength(2);
    expect(display[0].displayOperator.quantity + display[1].displayOperator.quantity).toBe(4600);
    expect(display[0].displayOperator.quantity).toBe(2000);
    expect(display[1].displayOperator.quantity).toBe(2600);
    expect(display.every((r) => r.customer === "Customer A")).toBe(true);
  });

  test("mixed split work dates stay on their own assignment rows", () => {
    const row = {
      id: "po-dates",
      po_number: "PO-W",
      work_date: "2026-09-10",
      operators: [
        { split_id: "s1", operator_email: "nanda@x.com", operator_name: "Nanda", work_date: "2026-09-01", quantity: 10 },
        { split_id: "s2", operator_email: "nanda@x.com", operator_name: "Nanda", work_date: "2026-09-08", quantity: 10 },
      ],
    };
    const display = buildProductionScheduleDisplayRows([row]);
    expect(display).toHaveLength(2);
    expect(display.map((r) => r.displayWorkDate)).toEqual(["2026-09-01", "2026-09-08"]);
    expect(display.map((r) => r.work_date)).toEqual(["2026-09-01", "2026-09-08"]);
  });

  test("non-cetak splits are excluded from operator rows and items", () => {
    const row = {
      id: "po-mix",
      po_number: "PO-M",
      print_qty_total: 1000,
      operators: [
        { split_id: "s-print", operator_email: "budi@x.com", operator_name: "Budi", line_name: "Cup", line_id: "lid-a", line_idx: 0, line_printable: true, quantity: 1000, qty_completed: 300 },
        { split_id: "s-np", operator_email: "sari@x.com", operator_name: "Sari", line_name: "Lid Non Print", line_id: "lid-b", line_idx: 1, line_printable: false, quantity: 500, qty_completed: 500 },
      ],
    };
    const display = buildProductionScheduleDisplayRows([row]);
    expect(display).toHaveLength(1);
    expect(display[0].displayOperator.operator_name).toBe("Budi");
    expect(display[0].displayOperator.splits.every((op) => op.line_printable !== false)).toBe(true);
  });

  test("same-name items keep distinct line identities on separate rows", () => {
    const row = {
      id: "po-same-name",
      operators: [
        { split_id: "s1", operator_user_id: "u-1", operator_name: "Budi", line_name: "Cup 16oz", line_id: "lid-a", line_idx: 0, line_printable: true, qty_completed: 10 },
        { split_id: "s2", operator_user_id: "u-1", operator_name: "Budi", line_name: "Cup 16oz", line_id: "lid-b", line_idx: 2, line_printable: true, qty_completed: 20 },
      ],
    };
    const display = buildProductionScheduleDisplayRows([row]);
    expect(display).toHaveLength(2);
    expect(display.map((r) => r.displayOperator.line_id)).toEqual(["lid-a", "lid-b"]);
    expect(display.map((r) => r.displayOperator.split_id)).toEqual(["s1", "s2"]);
  });
});
