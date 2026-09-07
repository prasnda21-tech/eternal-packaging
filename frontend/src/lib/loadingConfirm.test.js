import { emptyLoadingLine, validateLoadingDraftLines, loadingPoLines, sjFilesForLoading } from "./loadingConfirm";
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

  test("generated SJ file is listed once and keeps exact shipment identity", () => {
    const files = sjFilesForLoading({
      surat_jalan_files: [
        {
          id: "sh-1",
          shipment_id: "sh-1",
          sj_number: "SJ-100",
          name: "Surat Jalan SJ-100.pdf",
          source: "SYSTEM_GENERATED",
          lines: [{ line_idx: 0, qty: 200 }],
        },
        {
          id: "sh-2",
          shipment_id: "sh-2",
          sj_number: "SJ-100-2",
          name: "Surat Jalan SJ-100-2.pdf",
          source: "SYSTEM_GENERATED",
          lines: [{ line_idx: 0, qty: 150 }],
        },
      ],
      shipments: [
        { id: "sh-1", number: "SJ-100", file_id: "sh-1", lines: [{ line_idx: 0, qty: 200 }] },
        { id: "sh-2", number: "SJ-100-2", file_id: "sh-2", lines: [{ line_idx: 0, qty: 150 }] },
      ],
    });
    expect(files).toHaveLength(2);
    expect(files[0].id).toBe("sh-1");
    expect(files[0].sj_number).toBe("SJ-100");
    expect(files[0].has_file).toBe(true);
    expect(files[1].id).toBe("sh-2");
    expect(files[1].sj_number).toBe("SJ-100-2");
  });

  test("deleted Surat Jalan file is not offered from leftover shipment", () => {
    const files = sjFilesForLoading({
      surat_jalan_files: [{ id: "keep", name: "keep.pdf" }],
      shipments: [
        { id: "deleted-sj", number: "SJ-GONE", file_id: "deleted-sj", lines: [{ line_idx: 0, qty: 10 }] },
        { id: "keep", number: "SJ-KEEP", file_id: "keep" },
      ],
    });
    expect(files.map((f) => f.id)).toEqual(["keep"]);
  });

  test("manual and generated files coexist without merging shipments", () => {
    const files = sjFilesForLoading({
      surat_jalan_files: [
        { id: "sh-1", shipment_id: "sh-1", sj_number: "SJ-1", source: "SYSTEM_GENERATED", name: "Surat Jalan SJ-1.pdf" },
        { id: "manual-1", name: "signed-scan.pdf" },
      ],
      shipments: [{ id: "sh-1", number: "SJ-1", file_id: "sh-1" }],
    });
    expect(files.map((f) => f.id)).toEqual(["sh-1", "manual-1"]);
    expect(files[0].source).toBe("SYSTEM_GENERATED");
    expect(files[1].name).toBe("signed-scan.pdf");
  });

  test("loading qty still respects packing even when generated SJ qty is higher", () => {
    const lines = loadingPoLines({
      line_items: [{ name: "Paper Cup 12oz", quantity: 10000 }],
      packing_info: { lines: [{ line_idx: 0, packed_qty: 6000 }] },
      shipments: [{ id: "sh-1", number: "SJ-1", lines: [{ line_idx: 0, qty: 8000 }] }],
      surat_jalan_files: [{
        id: "sh-1",
        shipment_id: "sh-1",
        sj_number: "SJ-1",
        source: "SYSTEM_GENERATED",
        lines: [{ line_idx: 0, qty: 8000 }],
      }],
    }, "sh-1");
    expect(lines[0].qty_sj).toBe(8000);
    expect(lines[0].available).toBe(6000);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "6000" }], lines).ok).toBe(true);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "8000" }], lines).ok).toBe(false);
  });
});
