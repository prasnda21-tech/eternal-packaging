import { FILTER_DEFAULTS, applyProductionFilters } from "./productionScheduleFilters";
import { scheduledPrintQtyProgress, totalQtyScheduledCell } from "./productionScheduleQty";
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
    expect(keys[0]).toBe(productionScheduleDisplayRowKey("po-1", "email:budi@x.com"));
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

  test("same operator across two splits → 1 row and retains split/line identities", () => {
    const row = {
      id: "po-same",
      po_number: "PO-S",
      operators: [
        { split_id: "s1", operator_email: "nanda@x.com", operator_user_id: "u-nanda", operator_name: "Nanda Operator", line_idx: 0, line_id: "lid-a", quantity: 100 },
        { split_id: "s2", operator_email: "nanda@x.com", operator_user_id: "u-nanda", operator_name: "Nanda Operator", line_idx: 2, line_id: "lid-b", quantity: 50 },
      ],
    };
    const display = buildProductionScheduleDisplayRows([row]);
    expect(display).toHaveLength(1);
    expect(display[0].displayOperator.operator_name).toBe("Nanda Operator");
    expect(display[0].displayOperator.quantity).toBe(150);
    expect(display[0].displayOperator.operator_split_ids).toEqual(["s1", "s2"]);
    expect(display[0].displayOperator.line_ids).toEqual(["lid-a", "lid-b"]);
    expect(display[0].displayOperator.line_indices).toEqual([0, 2]);
    expect(display[0].displayOperator.splits).toHaveLength(2);
    expect(display[0].displayRowKey).toBe(productionScheduleDisplayRowKey("po-same", productionScheduleOperatorIdentity(row.operators[0])));
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

  test("Qty Cetak and Total Qty stay PO-level on every expanded row", () => {
    const display = buildProductionScheduleDisplayRows([poThree]);
    const canonicalCetak = scheduledPrintQtyProgress(poThree).text;
    const canonicalQty = totalQtyScheduledCell(poThree);
    display.forEach((row) => {
      expect(scheduledPrintQtyProgress(row).text).toBe(canonicalCetak);
      expect(totalQtyScheduledCell(row).groups).toHaveLength(canonicalQty.groups.length);
    });
  });

  test("search/filter runs on canonical rows before expansion", () => {
    const rows = [poThree, { id: "po-2", po_number: "PO-002", customer: "Other", operators: [{ split_id: "z", operator_name: "Dedi" }] }];
    const filtered = applyProductionFilters(rows, { ...FILTER_DEFAULTS, q: "PO-001" });
    expect(filtered).toHaveLength(1);
    const display = buildProductionScheduleDisplayRows(filtered);
    expect(display).toHaveLength(3);
    expect(display.every((row) => row.po_number === "PO-001")).toBe(true);
  });

  test("mixed split work dates do not invent earliest/latest; PO-level date is used", () => {
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
    expect(display).toHaveLength(1);
    expect(display[0].displayWorkDate).toBe("2026-09-10");
  });
});
