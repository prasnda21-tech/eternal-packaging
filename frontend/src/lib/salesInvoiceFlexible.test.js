import {
  INVOICE_SOURCE_PO,
  INVOICE_SOURCE_SJ,
  buildFlexibleInvoicePayload,
  buildSjInvoicePreviewLines,
  isInvoiceableSalesDelivery,
  normalizeInvoiceSourceMode,
  summarizeInvoiceSources,
  validateSjInvoiceQtyLines,
} from "./salesInvoiceFlexible";

describe("salesInvoiceFlexible", () => {
  test("T17 normalizes source mode / PO legacy payload", () => {
    expect(normalizeInvoiceSourceMode("sj")).toBe(INVOICE_SOURCE_SJ);
    expect(normalizeInvoiceSourceMode("")).toBe(INVOICE_SOURCE_PO);
    const payload = buildFlexibleInvoicePayload({
      sourceMode: "po",
      sourcePoIds: ["po-a", "po-b"],
      lines: [{ source_po_id: "po-a", line_idx: 0, quantity: 1 }],
      invoiceDate: "2026-01-01",
    });
    expect(payload.source_mode).toBe("po");
    expect(payload.source_po_ids).toEqual(["po-a", "po-b"]);
  });

  test("T18 SJ multi-select payload + preview lines", () => {
    const payload = buildFlexibleInvoicePayload({
      sourceMode: "sj",
      sourceSjIds: ["sj-1", "sj-2"],
      lines: [
        { source_sj_id: "sj-1", source_po_id: "po-a", line_idx: 0, quantity: 2 },
        { source_sj_id: "sj-2", source_po_id: "po-a", line_idx: 0, quantity: 1 },
      ],
      invoiceDate: "2026-01-01",
    });
    expect(payload.source_mode).toBe("sj");
    expect(payload.source_sj_ids).toEqual(["sj-1", "sj-2"]);
    expect(payload.source_po_ids).toBeUndefined();

    const built = buildSjInvoicePreviewLines([
      {
        id: "sj-1",
        sj_number: "SJ-1",
        po_id: "po-a",
        po_number: "PO-A",
        source_allocations: [
          {
            source_po_id: "po-a",
            source_po_number: "PO-A",
            source_line_id: "po-a_line_0",
            source_line_idx: 0,
            name: "Item",
            allocated_qty: 10,
            unit_price: 1000,
          },
        ],
      },
      {
        id: "sj-2",
        sj_number: "SJ-2",
        po_number: "PO-A",
        source_allocations: [
          {
            source_po_id: "po-a",
            source_line_id: "po-a_line_0",
            source_line_idx: 0,
            name: "Item",
            allocated_qty: 5,
            unit_price: 1000,
          },
        ],
      },
    ]);
    expect(built.lines).toHaveLength(2);
    expect(built.relations).toHaveLength(2);
  });

  test("T19 qty validation", () => {
    const ok = validateSjInvoiceQtyLines([
      { item_name: "A", quantity: 3, sj_line_qty: 5, remaining_invoice_qty: 5 },
    ]);
    expect(ok.ok).toBe(true);
    const bad = validateSjInvoiceQtyLines([
      { item_name: "A", quantity: 9, sj_line_qty: 5, remaining_invoice_qty: 5 },
    ]);
    expect(bad.ok).toBe(false);
  });

  test("T20 pending SJ excluded", () => {
    expect(isInvoiceableSalesDelivery({ id: "a", allocation_commit_pending: true })).toBe(false);
    expect(isInvoiceableSalesDelivery({ id: "b", is_cancelled: true })).toBe(false);
    expect(isInvoiceableSalesDelivery({ id: "c", sj_number: "SJ-1" })).toBe(true);
  });

  test("T21 detail source mapping", () => {
    const summary = summarizeInvoiceSources({
      source_mode: "sj",
      is_multi_source: true,
      source_po_ids: ["a", "b"],
      source_sj_ids: ["s1", "s2"],
    });
    expect(summary.sourceMode).toBe(INVOICE_SOURCE_SJ);
    expect(summary.sourceSjIds).toEqual(["s1", "s2"]);
    expect(summary.isMultiSource).toBe(true);
  });

  test("T22 rejects mixed PO+SJ ids", () => {
    expect(() =>
      buildFlexibleInvoicePayload({
        sourceMode: "po",
        sourcePoIds: ["po-a"],
        sourceSjIds: ["sj-1"],
        lines: [],
      }),
    ).toThrow(/mencampur/);
  });
});
