import { formatPhysicalOrderQty, physicalOrderQty } from "./orderPhysicalQty";

describe("orderPhysicalQty", () => {
  test("IDER shape: packing_required_total 1000 wins over total_quantity 3000", () => {
    const order = {
      total_quantity: 3000,
      packing_required_total: 1000,
      item_count: 3,
    };
    expect(physicalOrderQty(order)).toBe(1000);
    expect(formatPhysicalOrderQty(order)).toBe("1.000 pcs");
  });

  test("allows zero physical (all process-only SERVICE)", () => {
    expect(physicalOrderQty({ packing_required_total: 0, total_quantity: 2000 })).toBe(0);
    expect(formatPhysicalOrderQty({ packing_required_total: 0 })).toBe("0 pcs");
  });

  test("physical-only PO unchanged", () => {
    expect(physicalOrderQty({ packing_required_total: 1000, total_quantity: 1000 })).toBe(1000);
  });

  test("null packing_required_total key present but empty → unavailable", () => {
    expect(physicalOrderQty({ packing_required_total: null, total_quantity: 500 })).toBeNull();
  });

  test("missing packing_required_total → null (no total_quantity fallback)", () => {
    expect(physicalOrderQty({ total_quantity: 3000 })).toBeNull();
    expect(formatPhysicalOrderQty({ total_quantity: 3000 })).toBeNull();
  });

  test("invalid packing_required_total → null", () => {
    expect(physicalOrderQty({ packing_required_total: "x", total_quantity: 10 })).toBeNull();
    expect(physicalOrderQty({ packing_required_total: -1, total_quantity: 10 })).toBeNull();
  });
});
