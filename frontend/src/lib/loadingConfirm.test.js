import { emptyLoadingLine, validateLoadingDraftLines, loadingPoLines, sjFilesForLoading } from "./loadingConfirm";
import { parseSjQty } from "./suratJalan";

describe("Loading confirm helpers", () => {
  const order = {
    line_items: [
      { name: "Item A", item_code: "A1", quantity: 1000 },
      { name: "Item A", item_code: "A1", quantity: 500 },
    ],
    shipment_lines: [
      { line_idx: 0, loaded: 0, available_loading: 1000, qty_po: 1000 },
      { line_idx: 1, loaded: 0, available_loading: 500, qty_po: 500 },
    ],
  };

  test("does not merge same product name across PO lines", () => {
    const lines = loadingPoLines(order);
    expect(lines).toHaveLength(2);
    expect(lines[0].line_idx).toBe(0);
    expect(lines[1].line_idx).toBe(1);
  });

  test("empty packing qty still leaves Loading available when PO qty remains", () => {
    const lines = loadingPoLines({
      line_items: [{ name: "A", quantity: 1000 }],
      packing_info: { qty_packed: 0, lines: [{ line_idx: 0, packed_qty: 0 }] },
      shipment_lines: [{ line_idx: 0, packed: 0, loaded: 0, available_loading: 0, qty_po: 1000 }],
    });
    expect(lines[0].available).toBe(1000);
  });

  test("cancelled Surat Jalan files are not loading-eligible", () => {
    const files = sjFilesForLoading({
      surat_jalan_files: [
        { id: "gone", name: "old.pdf", status: "cancelled" },
        { id: "ok", name: "sj.pdf", uploaded_at: "2026-08-28T10:00:00+07:00" },
      ],
    });
    expect(files.map((f) => f.id)).toEqual(["ok"]);
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
