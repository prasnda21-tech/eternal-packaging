import { computePackingLines, packingLinesForOrder } from "./packingCard";

describe("packingCard non-cetak skip Packing", () => {
  test("non-cetak lines are excluded from packing list", () => {
    const order = {
      line_items: [
        { name: "Cup", quantity: 500, production_process: "print" },
        { name: "Sedotan", quantity: 300, production_process: "non_print" },
      ],
      operator_splits: [{ line_idx: 0, quantity: 500, qty_completed: 200 }],
      packing_info: { lines: [] },
    };
    const rows = packingLinesForOrder(order);
    expect(rows).toHaveLength(1);
    expect(rows[0].line_idx).toBe(0);
    expect(rows[0].is_print).toBe(true);
    expect(rows.every((r) => r.production_process !== "non_print")).toBe(true);
  });

  test("full non-cetak PO yields empty packing lines", () => {
    const order = {
      line_items: [{ name: "Box", quantity: 100, production_process: "non_print" }],
    };
    expect(computePackingLines(order)).toEqual([]);
    expect(packingLinesForOrder(order)).toEqual([]);
  });

  test("filters legacy packing_lines that include non_print", () => {
    const order = {
      packing_lines: [
        { line_idx: 0, name: "Cetak", is_print: true, production_process: "print", available: 10, packed: 0 },
        { line_idx: 1, name: "Non", is_print: false, production_process: "non_print", available: 20, packed: 0 },
      ],
    };
    const rows = packingLinesForOrder(order);
    expect(rows).toHaveLength(1);
    expect(rows[0].line_idx).toBe(0);
  });
});
