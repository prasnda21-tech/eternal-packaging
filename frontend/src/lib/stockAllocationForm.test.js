import {
  allocatedTotal,
  allocationVariance,
  allocationWarningMessage,
  isInventoryLineForAllocation,
  payloadAllocationsFromLine,
  summarizeFormAllocationWarnings,
} from "./stockAllocationForm";

describe("stockAllocationForm", () => {
  test("SERVICE lines skip allocation", () => {
    expect(isInventoryLineForAllocation({ item_type: "SERVICE", master_item_id: "m1" })).toBe(false);
    expect(isInventoryLineForAllocation({ item_type: "INVENTORY", master_item_id: "m1" })).toBe(true);
    expect(isInventoryLineForAllocation({ master_item_id: "m1" })).toBe(true);
  });

  test("empty / under / over are warnings only", () => {
    expect(allocationWarningMessage({ master_item_id: "m1", quantity: 10, stock_allocations: [] }))
      .toBe("Alokasi gudang belum lengkap");
    expect(allocationWarningMessage({
      master_item_id: "m1",
      quantity: 10,
      stock_allocations: [{ warehouse_id: "w1", qty: 4 }],
    })).toBe("Total alokasi kurang dari qty pesanan");
    expect(allocationWarningMessage({
      master_item_id: "m1",
      quantity: 10,
      stock_allocations: [{ warehouse_id: "w1", qty: 6 }, { warehouse_id: "w2", qty: 6 }],
    })).toBe("Total alokasi melebihi qty pesanan");
    expect(allocationWarningMessage({
      master_item_id: "m1",
      quantity: 10,
      stock_allocations: [{ warehouse_id: "w1", qty: 4 }, { warehouse_id: "w2", qty: 6 }],
    })).toBe("");
  });

  test("payload strips invalid rows; totals are additive", () => {
    const line = {
      master_item_id: "m1",
      stock_allocations: [
        { warehouse_id: "w1", qty: 3 },
        { warehouse_id: "", qty: 2 },
        { warehouse_id: "w2", qty: "NaN" },
        { warehouse_id: "w3", qty: 7 },
      ],
    };
    expect(payloadAllocationsFromLine(line)).toEqual([
      { warehouse_id: "w1", qty: 3 },
      { warehouse_id: "w3", qty: 7 },
    ]);
    expect(allocatedTotal(line.stock_allocations)).toBe(10);
    expect(allocationVariance(10, [{ warehouse_id: "w1", qty: 4 }, { warehouse_id: "w2", qty: 6 }])).toBe(0);
  });

  test("summarizeFormAllocationWarnings lists soft messages", () => {
    const warnings = summarizeFormAllocationWarnings([
      { master_item_id: "a", quantity: 5, stock_allocations: [] },
      { item_type: "SERVICE", quantity: 1 },
    ]);
    expect(warnings).toHaveLength(1);
    expect(warnings[0].message).toMatch(/belum lengkap/i);
  });
});
