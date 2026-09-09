import {
  accuratePreviewInner,
  accuratePreviewLineItems,
  wrapAccurateImportPreviewResponse,
} from "./accurateImportPreview";

const BACKEND = {
  duplicate: false,
  existing_po_id: null,
  existing_po_number: null,
  mode: "mock",
  preview: {
    accurate_id: "91290",
    po_number: "PO/2026/09/09/000018",
    customer: "WELCOME TO PLEASURE",
    notes: "SABLON WARNA PUTIH, HIJAU, PINK\nBUFFER STOCK",
    item_count: 2,
    total_quantity: 4000,
    line_items: [
      {
        item_code: "100003",
        name: "12 oz PET Datar 11 gr SJP",
        quantity: 2000,
        unit: "pcs",
        color: "PUTIH, HIJAU, PINK",
        accurate_line_id: "99990",
      },
      {
        item_code: "100123",
        name: "LID PET Strawless Starindo",
        quantity: 2000,
        unit: "pcs",
        color: "BENING",
        accurate_line_id: "99991",
      },
    ],
  },
};

describe("wrapAccurateImportPreviewResponse", () => {
  test("nested preview.line_items and hoisted line_items both have 2 rows", () => {
    const state = wrapAccurateImportPreviewResponse(BACKEND);
    expect(state.source).toBe("accurate");
    expect(state.preview.item_count).toBe(2);
    expect(state.preview.line_items).toHaveLength(2);
    expect(state.item_count).toBe(2);
    expect(state.line_items).toHaveLength(2);
    expect(accuratePreviewLineItems(state)).toHaveLength(2);
    expect(accuratePreviewInner(state).po_number).toBe("PO/2026/09/09/000018");
  });

  test("preserves 100003 / 100123 structured Warna only", () => {
    const state = wrapAccurateImportPreviewResponse(BACKEND);
    const lines = accuratePreviewLineItems(state);
    expect(lines[0].item_code).toBe("100003");
    expect(lines[0].color).toBe("PUTIH, HIJAU, PINK");
    expect(lines[0].quantity).toBe(2000);
    expect(lines[1].item_code).toBe("100123");
    expect(lines[1].color).toBe("BENING");
    expect(lines[1].quantity).toBe(2000);
    expect(state.preview.notes).toMatch(/SABLON WARNA/);
    expect(lines[0].color).not.toMatch(/SABLON/);
    expect(lines[1].color).not.toMatch(/BUFFER/);
  });

  test("empty nested line_items stays empty (no header fallback)", () => {
    const d = {
      preview: {
        po_number: "PO-X",
        customer: "A",
        notes: "SABLON WARNA HITAM",
        item_count: 0,
        line_items: [],
      },
    };
    const state = wrapAccurateImportPreviewResponse(d);
    expect(accuratePreviewLineItems(state)).toEqual([]);
    expect(state.item_count).toBe(0);
  });
});
