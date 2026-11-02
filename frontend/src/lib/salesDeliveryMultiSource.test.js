import {
  buildCombinedAllocationRows,
  buildSourceAllocationsPayload,
  lineAllocKey,
  validateMultiSourceSelection,
} from "./salesDeliveryMultiSource";

describe("salesDeliveryMultiSource", () => {
  test("builds combined rows and payload without merging prices", () => {
    const rows = buildCombinedAllocationRows({
      a: {
        po_id: "a",
        po_number: "PO-A",
        customer: "PT",
        lines: [{ line_idx: 0, line_id: "la", name: "X", remaining_qty: 3000, ordered_qty: 3000, unit: "pcs" }],
      },
      b: {
        po_id: "b",
        po_number: "PO-B",
        customer: "PT",
        lines: [{ line_idx: 0, line_id: "lb", name: "X", remaining_qty: 2000, ordered_qty: 2000, unit: "pcs" }],
      },
    });
    expect(rows).toHaveLength(2);
    expect(rows[0].key).toBe(lineAllocKey("a", 0));
    const payload = buildSourceAllocationsPayload(rows, {
      [lineAllocKey("a", 0)]: 3000,
      [lineAllocKey("b", 0)]: 2000,
    });
    expect(payload).toEqual([
      { source_po_id: "a", source_line_id: "la", source_line_idx: 0, allocated_qty: 3000, notes: "" },
      { source_po_id: "b", source_line_id: "lb", source_line_idx: 0, allocated_qty: 2000, notes: "" },
    ]);
  });

  test("validateMultiSourceSelection rejects mismatched customer_id", () => {
    const err = validateMultiSourceSelection({
      a: { customer_id: "c1", address: "Jl Satu" },
      b: { customer_id: "c2", address: "Jl Satu" },
    });
    expect(err).toMatch(/Customer/);
  });
});
