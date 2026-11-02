import {
  buildCombinedAllocationRows,
  buildSourceAllocationsPayload,
  lineAllocKey,
  summarizeMultiPoDeliveryQty,
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
    expect(err).toMatch(/customer_id/i);
  });

  test("summarizeMultiPoDeliveryQty sums PO totals without collapsing same product names", () => {
    const keyA = lineAllocKey("a", 0);
    const keyB = lineAllocKey("b", 0);
    const summary = summarizeMultiPoDeliveryQty(
      {
        a: { total_ordered: 3000, total_shipped: 500, total_remaining: 2500 },
        b: { total_ordered: 2000, total_shipped: 0, total_remaining: 2000 },
      },
      { [keyA]: 1000, [keyB]: 500 },
    );
    expect(summary.ready).toBe(true);
    expect(summary.po_count).toBe(2);
    expect(summary.total_ordered).toBe(5000);
    expect(summary.total_shipped).toBe(500);
    expect(summary.total_remaining_before).toBe(4500);
    expect(summary.total_allocated).toBe(1500);
    expect(summary.total_remaining_after).toBe(3000);
  });

  test("summarizeMultiPoDeliveryQty ignores allocation keys outside selected POs", () => {
    const summary = summarizeMultiPoDeliveryQty(
      { a: { total_ordered: 100, total_shipped: 0, total_remaining: 100 } },
      { [lineAllocKey("a", 0)]: 40, [lineAllocKey("other", 0)]: 999 },
    );
    expect(summary.total_allocated).toBe(40);
    expect(summary.total_remaining_after).toBe(60);
  });

  test("validateMultiSourceSelection rejects missing customer_id even when names match", () => {
    const err = validateMultiSourceSelection({
      a: { customer_id: "", address: "Jl Satu", customer: "PT Sama" },
      b: { customer_id: "c1", address: "Jl Satu", customer: "PT Sama" },
    });
    expect(err).toMatch(/customer_id/i);
  });
});
