import { emptyLoadingLine, validateLoadingDraftLines, loadingPoLines } from "./loadingConfirm";
import { parseSjQty } from "./suratJalan";

describe("Loading confirm helpers", () => {
  const order = {
    line_items: [
      { name: "Item A", item_code: "A1", quantity: 1000 },
      { name: "Item A", item_code: "A1", quantity: 500 },
    ],
    packing_info: {
      lines: [
        { line_idx: 0, packed_qty: 400 },
        { line_idx: 1, packed_qty: 100 },
      ],
    },
    shipment_lines: [
      { line_idx: 0, loaded: 0, packed: 400, available_loading: 400, qty_po: 1000 },
      { line_idx: 1, loaded: 0, packed: 100, available_loading: 100, qty_po: 500 },
    ],
  };

  test("does not merge same product name across PO lines", () => {
    const lines = loadingPoLines(order);
    expect(lines).toHaveLength(2);
    expect(lines[0].line_idx).toBe(0);
    expect(lines[1].line_idx).toBe(1);
  });

  test("available qty follows packed physical qty not remaining PO", () => {
    const lines = loadingPoLines(order);
    expect(lines[0].available).toBe(400);
    expect(lines[0].qty_po).toBe(1000);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "400" }], lines).ok).toBe(true);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "401" }], lines).ok).toBe(false);
  });

  test("blocks loading when packed physical qty is empty", () => {
    const empty = loadingPoLines({
      line_items: [{ name: "Item A", quantity: 1000 }],
      packing_info: { lines: [{ line_idx: 0, packed_qty: 0 }] },
    });
    expect(empty[0].available).toBe(0);
    const check = validateLoadingDraftLines([{ line_idx: 0, qty: "10" }], empty);
    expect(check.ok).toBe(false);
    expect(check.error).toMatch(/qty Packing/i);
  });

  test("remaining after partial loading is packed minus loaded", () => {
    const lines = loadingPoLines({
      line_items: [{ name: "Item A", quantity: 1000 }],
      packing_info: { lines: [{ line_idx: 0, packed_qty: 500 }] },
      shipment_lines: [{ line_idx: 0, packed: 500, loaded: 300, available_loading: 200, qty_po: 1000 }],
    });
    expect(lines[0].available).toBe(200);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "200" }], lines).ok).toBe(true);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "201" }], lines).ok).toBe(false);
  });

  test("per-line cap does not share remaining across items", () => {
    const lines = loadingPoLines(order);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "400" }, { line_idx: 1, qty: "100" }], lines).ok).toBe(true);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "400" }, { line_idx: 1, qty: "101" }], lines).ok).toBe(false);
  });

  test("rejects zero qty and requires item", () => {
    expect(parseSjQty("0")).toBeNull();
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "0" }], loadingPoLines(order)).ok).toBe(false);
    expect(validateLoadingDraftLines([{ line_idx: "", qty: "10" }], loadingPoLines(order)).ok).toBe(false);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "100" }], loadingPoLines(order)).ok).toBe(true);
  });

  test("empty row helper", () => {
    expect(emptyLoadingLine()).toEqual({ line_idx: "", qty: "" });
  });
});
