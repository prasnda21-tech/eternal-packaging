import fs from "fs";
import path from "path";
import {
  emptySjLine,
  parseSjQty,
  poLineLabel,
  sjHistory,
  sjPoLines,
  validateSjDraftLines,
} from "./suratJalan";

describe("Surat Jalan item/qty helpers", () => {
  const order = {
    line_items: [
      { name: "Item A", item_code: "A1", quantity: 1000 },
      { name: "Item A", item_code: "A1", quantity: 500 },
    ],
    shipment_lines: [
      { line_idx: 0, shipped: 0, available_sj_file: 1000, qty_po: 1000 },
      { line_idx: 1, shipped: 0, available_sj_file: 500, qty_po: 500 },
    ],
  };

  test("does not merge same product name across PO lines", () => {
    const lines = sjPoLines(order);
    expect(lines).toHaveLength(2);
    expect(lines[0].line_idx).toBe(0);
    expect(lines[1].line_idx).toBe(1);
    expect(poLineLabel(order.line_items[0], 0)).toContain("Item A");
  });

  test("rejects zero and invalid qty", () => {
    expect(parseSjQty("0")).toBeNull();
    expect(parseSjQty("-1")).toBeNull();
    expect(parseSjQty("")).toBeNull();
    expect(parseSjQty("100")).toBe(100);
    expect(validateSjDraftLines([{ line_idx: 0, qty: "0" }], sjPoLines(order)).ok).toBe(false);
    expect(validateSjDraftLines([{ line_idx: 0, qty: "100" }], sjPoLines(order)).ok).toBe(true);
  });

  test("legacy history shows incomplete without inventing qty", () => {
    const hist = sjHistory({ surat_jalan_files: [{ id: "x", name: "old.pdf" }] });
    expect(hist[0].legacy_incomplete).toBe(true);
    expect(hist[0].has_item_qty).toBe(false);
  });

  test("empty row helper", () => {
    expect(emptySjLine()).toEqual({ line_idx: "", qty: "" });
  });
});
