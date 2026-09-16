import { operatorPrintableItems } from "./productionScheduleQty";
import { buildProductionScheduleDisplayRows } from "./productionScheduleDisplayRows";
import {
  fauzanPlastikPe28ProofRow,
  isolatedOperatorsProofRow,
  singleItemLineProofRow,
  unassignedProofRow,
} from "./productionScheduleItemLinePreviewDummy";

describe("production schedule item-line preview proof", () => {
  test("Fauzan Plastik PE 28 2.000 and 2.600 become two rows without aggregating 4.600", () => {
    const display = buildProductionScheduleDisplayRows([fauzanPlastikPe28ProofRow()]);
    expect(display).toHaveLength(2);
    expect(display.every((row) => row.po_number === "PO-FAUZAN-PE")).toBe(true);
    expect(display.every((row) => row.customer === "Customer PE")).toBe(true);
    expect(display.map((row) => row.displayOperator.operator_name)).toEqual(["Fauzan", "Fauzan"]);
    expect(display.map((row) => row.displayOperator.quantity)).toEqual([2000, 2600]);
    expect(display.map((row) => operatorPrintableItems(row.displayOperator.splits).map((item) => ({
      name: item.name,
      quantity: item.quantity,
    })))).toEqual([
      [{ name: "Plastik PE 28", quantity: 2000 }],
      [{ name: "Plastik PE 28", quantity: 2600 }],
    ]);
    expect(display[0].displayRowKey).not.toBe(display[1].displayRowKey);
    expect(display.some((row) => Number(row.displayOperator.quantity) === 4600)).toBe(false);
  });

  test("one item line still produces exactly one row", () => {
    const display = buildProductionScheduleDisplayRows([singleItemLineProofRow()]);
    expect(display).toHaveLength(1);
    expect(display[0].displayOperator.operator_name).toBe("Budi");
    expect(display[0].displayOperator.quantity).toBe(5000);
  });

  test("multiple operators stay isolated", () => {
    const display = buildProductionScheduleDisplayRows([isolatedOperatorsProofRow()]);
    expect(display).toHaveLength(2);
    expect(display.map((row) => row.displayOperator.operator_name)).toEqual(["Andi", "Budi"]);
    expect(display.map((row) => row.displayOperator.quantity)).toEqual([7000, 1000]);
    expect(display[0].displayOperatorKey).not.toBe(display[1].displayOperatorKey);
  });

  test("Belum ditugaskan unassigned row remains a single empty operator row", () => {
    const display = buildProductionScheduleDisplayRows([unassignedProofRow()]);
    expect(display).toHaveLength(1);
    expect(display[0].displayOperator).toBeNull();
    expect(display[0].displayOperatorKey).toBe("unassigned");
  });
});
