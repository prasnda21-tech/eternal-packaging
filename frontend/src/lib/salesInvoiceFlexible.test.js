import {
  INVOICE_SOURCE_PO,
  INVOICE_SOURCE_SJ,
  buildFlexibleInvoicePayload,
  buildMultiPoInvoicePreviewLines,
  buildSjInvoicePreviewLines,
  invoiceLineTestId,
  invoiceLineUiKey,
  isInvoiceableSalesDelivery,
  isPoInvoiceMergeCompatible,
  normalizeInvoiceSourceMode,
  resolveDetailLineCapacity,
  summarizeInvoiceSources,
  validateMultiPoInvoiceQtyLines,
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

  test("T23 invoice line UI keys stay unique across colliding source line_idx", () => {
    const a = {
      invoice_line_id: "inv-line-a",
      line_idx: 0,
      source_po_id: "po-a",
      source_sj_id: "sj-a",
      line_id: "po-a_line_0",
    };
    const b = {
      line_idx: 0,
      source_po_id: "po-b",
      source_sj_id: "sj-b",
      source_po_line_id: "po-b_line_0",
      line_id: "po-b_line_0",
    };
    expect(invoiceLineUiKey(a, 0)).toBe("inv-line-a");
    expect(invoiceLineUiKey(b, 1)).toContain("po-b");
    expect(invoiceLineUiKey(b, 1)).not.toBe(invoiceLineUiKey({ ...b, source_po_id: "po-a" }, 0));
    expect(invoiceLineTestId("detail-qty-input", a, 0)).toBe("detail-qty-input-inv-line-a");
    expect(invoiceLineTestId("detail-qty-input", b, 1)).not.toBe(
      invoiceLineTestId("detail-qty-input", { ...b, source_po_id: "po-a", source_sj_id: "sj-a" }, 0),
    );
  });

  test("T24 multi-PO preview keeps same product / different price as separate rows", () => {
    const lines = buildMultiPoInvoicePreviewLines([
      {
        po_id: "po-a",
        po_number: "PO/A",
        customer_id: "c1",
        lines: [
          {
            line_idx: 0,
            line_id: "po-a_line_0",
            item_name: "Cup PET 16 oz",
            ordered_qty: 3000,
            remaining_invoice_qty: 3000,
            unit_price: 450,
          },
        ],
      },
      {
        po_id: "po-b",
        po_number: "PO/B",
        customer_id: "c1",
        lines: [
          {
            line_idx: 0,
            line_id: "po-b_line_0",
            item_name: "Cup PET 16 oz",
            ordered_qty: 2000,
            remaining_invoice_qty: 2000,
            unit_price: 425,
          },
        ],
      },
    ]);
    expect(lines).toHaveLength(2);
    expect(lines[0].rowKey).not.toBe(lines[1].rowKey);
    expect(lines[0].unit_price).toBe(450);
    expect(lines[1].unit_price).toBe(425);
    const ok = validateMultiPoInvoiceQtyLines(lines.map((l) => ({ ...l, quantity: l.remaining_invoice_qty })));
    expect(ok.ok).toBe(true);
    const bad = validateMultiPoInvoiceQtyLines([{ ...lines[0], quantity: 9999 }]);
    expect(bad.ok).toBe(false);
  });

  test("T25 merge compatibility + detail capacity ignores foreign colliding line_idx", () => {
    expect(
      isPoInvoiceMergeCompatible(
        { customer_id: "c1", currency: "IDR", operational_branch_id: "br-ygy" },
        { customer_id: "c1", currency: "IDR", operational_branch_id: "br-ygy" },
      ),
    ).toBe(true);
    expect(
      isPoInvoiceMergeCompatible(
        { customer_id: "c1", currency: "IDR", operational_branch_id: "br-ygy" },
        { customer_id: "c2", currency: "IDR", operational_branch_id: "br-ygy" },
      ),
    ).toBe(false);

    const lines = [
      {
        line_idx: 0,
        source_po_id: "po-a",
        source_sj_id: "sj-a",
        ordered_qty: 800,
        sj_line_qty: 800,
        quantity: 800,
        remaining_invoice_qty_before: 800,
        max_qty: 800,
        po_qty: 800,
      },
      {
        line_idx: 0,
        source_po_id: "po-b",
        source_sj_id: "sj-b",
        ordered_qty: 1000,
        sj_line_qty: 1000,
        quantity: 1000,
        remaining_invoice_qty_before: 1000,
        // Wrong primary-PO attach would claim max 800 — UI must ignore it.
        max_qty: 800,
        po_qty: 800,
      },
    ];
    const capB = resolveDetailLineCapacity(lines[1], {
      allLines: lines,
      invoice: { po_id: "po-a" },
    });
    expect(capB.sourceScoped).toBe(true);
    expect(capB.maxQty).toBe(1000);
    expect(capB.maxQty).toBeGreaterThanOrEqual(1000);
  });
});
