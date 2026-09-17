import fs from "fs";
import path from "path";
import {
  defaultSjOperationalDate,
  emptySjLine,
  parseOperationalDate,
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

  test("generated and manual SJ files appear together with source", () => {
    const hist = sjHistory({
      surat_jalan_files: [
        { id: "gen-1", name: "Surat Jalan SJ-1.pdf", source: "SYSTEM_GENERATED", sj_number: "SJ-1", shipment_id: "sh-1", lines: [{ line_idx: 0, qty: 10 }] },
        { id: "man-1", name: "signed-scan.pdf", lines: [] },
      ],
    });
    expect(hist).toHaveLength(2);
    expect(hist[0].source).toBe("SYSTEM_GENERATED");
    expect(hist[0].sj_number).toBe("SJ-1");
    expect(hist[1].name).toBe("signed-scan.pdf");
    expect(hist[0].id).not.toBe(hist[1].id);
  });

  test("ghost sj_files without a live file do not stay in Detail PO history", () => {
    const hist = sjHistory({
      sj_files: [
        { id: "gone", file_id: null, has_file: false, name: "Surat Jalan SJ-GONE" },
      ],
      surat_jalan_files: [{ id: "keep", name: "keep.pdf" }],
      shipments: [{ id: "gone", number: "SJ-GONE", file_id: null }],
    });
    expect(hist.map((f) => f.id || f.file_id)).toEqual(["keep"]);
  });

  test("history keeps superseded SJ status for audit trail", () => {
    const hist = sjHistory({
      surat_jalan_files: [
        { id: "sj-3", name: "SJ3.pdf", status: "superseded", cancelled: true, shipment_id: "sj-3", sj_number: "SJ/1_3" },
      ],
    });
    expect(hist[0].status).toBe("superseded");
    expect(hist[0].cancelled).toBe(true);
  });

  test("empty row helper", () => {
    expect(emptySjLine()).toEqual({ line_idx: "", qty: "" });
  });

  test("operational date validation and default", () => {
    expect(parseOperationalDate("2026-08-30")).toBe("2026-08-30");
    expect(parseOperationalDate("2026-02-31")).toBe("");
    expect(parseOperationalDate("")).toBe("");
    expect(parseOperationalDate("not-a-date")).toBe("");
    expect(defaultSjOperationalDate({ delivery_note_created_at: "2026-08-30T12:00:00+07:00" })).toBe("2026-08-30");
    expect(defaultSjOperationalDate({ scheduled_delivery_date: "2026-08-31" })).toBe("2026-08-31");
    expect(defaultSjOperationalDate({}, new Date("2026-08-28T10:00:00+07:00"))).toBe("");
  });
});
