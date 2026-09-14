import { emptyLoadingLine, validateLoadingDraftLines, loadingPoLines, sjFilesForLoading, resolveLoadingPoLineIdx, loadingVerificationStatus } from "./loadingConfirm";
import { loadingAvailability } from "./mobileWorkspaces";
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
      line_items: [{ name: "Item A", quantity: 1000, production_process: "print" }],
      packing_info: { lines: [{ line_idx: 0, packed_qty: 0 }] },
    });
    expect(empty[0].available).toBe(0);
    const check = validateLoadingDraftLines([{ line_idx: 0, qty: "10" }], empty);
    expect(check.ok).toBe(false);
    expect(check.error).toMatch(/qty Packing/i);
  });

  test("non-cetak loading uses PO qty without packed_qty", () => {
    const lines = loadingPoLines({
      line_items: [{ name: "Box", quantity: 400, production_process: "non_print" }],
      packing_info: {},
      surat_jalan_files: [{ id: "f1", name: "sj.pdf", lines: [{ line_idx: 0, qty: 400 }] }],
    }, "f1");
    expect(lines[0].packed).toBe(0);
    expect(lines[0].available).toBe(400);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "400" }], lines).ok).toBe(true);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "401" }], lines).ok).toBe(false);
  });

  test("mixed order: cetak needs packed, non-cetak uses PO qty", () => {
    const lines = loadingPoLines({
      line_items: [
        { name: "Cetak", quantity: 500, production_process: "print" },
        { name: "Non", quantity: 300, production_process: "non_print" },
      ],
      packing_info: { lines: [{ line_idx: 0, packed_qty: 200 }] },
      surat_jalan_files: [{
        id: "f1",
        name: "sj.pdf",
        lines: [
          { line_idx: 0, qty: 500 },
          { line_idx: 1, qty: 300 },
        ],
      }],
    }, "f1");
    expect(lines[0].available).toBe(200);
    expect(lines[1].available).toBe(300);
    expect(lines[1].packed).toBe(0);
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

  test("selected SJ total 2000 still exposes 1000 available on the packed line", () => {
    const lines = loadingPoLines({
      line_items: [
        { name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LA" },
        { name: "Other Cup", quantity: 1000, production_process: "non_print", line_id: "LB" },
      ],
      packing_info: { qty_packed: 1000, lines: [{ line_idx: 0, line_id: "LA", packed_qty: 1000 }] },
      surat_jalan_files: [{
        id: "sj1",
        sj_number: "SJ/2026/09/08/000006",
        total_qty: 2000,
        lines: [
          { line_idx: 0, line_id: "LA", qty: 1000 },
          { line_idx: 1, line_id: "LB", qty: 1000 },
        ],
      }],
    }, "sj1");
    expect(lines[0].qty_sj + lines[1].qty_sj).toBe(2000);
    expect(lines[0].available).toBe(1000);
    expect(lines[0].loaded).toBe(0);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "1000" }], lines).ok).toBe(true);
  });

  test("packed 0 on the exact SJ line still blocks Loading", () => {
    const lines = loadingPoLines({
      line_items: [{ name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LA" }],
      packing_info: { lines: [{ line_idx: 0, packed_qty: 0 }] },
      surat_jalan_files: [{ id: "sj1", lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }] }],
    }, "sj1");
    expect(lines[0].available).toBe(0);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "1000" }], lines).ok).toBe(false);
  });

  test("same-name lines do not share packing", () => {
    const lines = loadingPoLines({
      line_items: [
        { name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LA" },
        { name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LB" },
      ],
      packing_info: { lines: [{ line_idx: 0, line_id: "LA", packed_qty: 1000 }, { line_idx: 1, line_id: "LB", packed_qty: 0 }] },
      surat_jalan_files: [{
        id: "sj1",
        lines: [
          { line_idx: 0, line_id: "LA", qty: 1000 },
          { line_idx: 1, line_id: "LB", qty: 1000 },
        ],
      }],
    }, "sj1");
    expect(lines[0].available).toBe(1000);
    expect(lines[1].available).toBe(0);
    expect(validateLoadingDraftLines([{ line_idx: 1, qty: "1000" }], lines).ok).toBe(false);
  });

  test("already loaded subtracts once from packed remaining", () => {
    const lines = loadingPoLines({
      line_items: [{ name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LA" }],
      packing_info: { lines: [{ line_idx: 0, packed_qty: 1000 }] },
      surat_jalan_files: [{ id: "sj1", lines: [{ line_idx: 0, qty: 1000 }] }],
      loading_confirmations: [{ id: "c1", surat_jalan_id: "sj1", lines: [{ line_idx: 0, qty: 400 }] }],
    }, "sj1");
    expect(lines[0].available).toBe(600);
    expect(lines[0].loaded).toBe(400);
  });

  test("line_id without line_idx resolves the packed PO line", () => {
    expect(resolveLoadingPoLineIdx({
      line_items: [
        { name: "14 oz Inject Datar Frosted Klir", line_id: "LA" },
        { name: "Other", line_id: "LB" },
      ],
    }, { line_id: "LA", qty: 1000 })).toBe(0);
    const lines = loadingPoLines({
      line_items: [
        { name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LA" },
        { name: "Other", quantity: 1000, production_process: "non_print", line_id: "LB" },
      ],
      packing_info: { lines: [{ line_id: "LA", packed_qty: 1000 }] },
      surat_jalan_files: [{
        id: "sj1",
        total_qty: 2000,
        lines: [
          { line_id: "LA", qty: 1000 },
          { line_id: "LB", qty: 1000 },
        ],
      }],
    }, "sj1");
    expect(lines[0].qty_sj).toBe(1000);
    expect(lines[1].qty_sj).toBe(1000);
    expect(lines[0].available).toBe(1000);
  });

  test("stale line_idx loses to unique line_id", () => {
    expect(resolveLoadingPoLineIdx({
      line_items: [
        { name: "14 oz Inject Datar Frosted Klir", line_id: "LA" },
        { name: "Other", line_id: "LB" },
      ],
    }, { line_idx: 1, line_id: "LA" })).toBe(0);
    const lines = loadingPoLines({
      line_items: [
        { name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LA" },
        { name: "Other", quantity: 1000, production_process: "non_print", line_id: "LB" },
      ],
      packing_info: { lines: [{ line_idx: 0, line_id: "LA", packed_qty: 1000 }] },
      surat_jalan_files: [{
        id: "sj1",
        lines: [
          { line_idx: 1, line_id: "LA", qty: 1000 },
          { line_idx: 0, line_id: "LB", qty: 1000 },
        ],
      }],
    }, "sj1");
    expect(lines[0].qty_sj).toBe(1000);
    expect(lines[0].available).toBe(1000);
    expect(lines[1].qty_sj).toBe(1000);
  });

  test("missing line_idx is not treated as PO line 0", () => {
    expect(resolveLoadingPoLineIdx({
      line_items: [{ name: "A", line_id: "LA" }, { name: "B", line_id: "LB" }],
    }, { qty: 1000 })).toBeNull();
  });

  test("unique accurate_item_id resolves without using item name", () => {
    expect(resolveLoadingPoLineIdx({
      line_items: [
        { name: "14 oz Inject Datar Frosted Klir", line_id: "LA", accurate_item_id: "frosted" },
        { name: "14 oz Inject Datar Frosted Klir", line_id: "LB", accurate_item_id: "other" },
      ],
    }, { accurate_item_id: "frosted", qty: 1000 })).toBe(0);
    expect(resolveLoadingPoLineIdx({
      line_items: [
        { name: "14 oz Inject Datar Frosted Klir", accurate_item_id: "same" },
        { name: "14 oz Inject Datar Frosted Klir", accurate_item_id: "same" },
      ],
    }, { accurate_item_id: "same", qty: 1000 })).toBeNull();
  });

  test("legacy packing stale line_idx loses to unique line_id", () => {
    const lines = loadingPoLines({
      line_items: [
        { name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LA" },
        { name: "Other", quantity: 1000, production_process: "non_print", line_id: "LB" },
      ],
      packing_info: { lines: [{ line_idx: 1, line_id: "LA", packed_qty: 1000 }] },
      surat_jalan_files: [{
        id: "sj1",
        lines: [
          { line_idx: 0, line_id: "LA", qty: 1000 },
          { line_idx: 1, line_id: "LB", qty: 1000 },
        ],
      }],
    }, "sj1");
    expect(lines[0].packed).toBe(1000);
    expect(lines[0].available).toBe(1000);
    expect(lines[1].packed).toBe(0);
    expect(loadingVerificationStatus({
      line_items: lines.map((ln, i) => ({
        name: ln.name,
        quantity: ln.qty_po,
        production_process: ln.production_process,
        line_id: i === 0 ? "LA" : "LB",
      })),
      packing_info: { lines: [{ line_idx: 1, line_id: "LA", packed_qty: 1000 }] },
      surat_jalan_files: [{
        id: "sj1",
        lines: [
          { line_idx: 0, line_id: "LA", qty: 1000 },
          { line_idx: 1, line_id: "LB", qty: 1000 },
        ],
      }],
    }, "sj1").code).not.toBe("packing_insufficient");
  });

  test("production symptom: selected shipment line_id not file stale line_idx", () => {
    const order = {
      line_items: [
        { name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LA" },
        { name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LB" },
      ],
      packing_info: { lines: [{ line_idx: 0, line_id: "LA", packed_qty: 1000 }, { line_idx: 1, line_id: "LB", packed_qty: 0 }] },
      shipments: [{
        id: "sj1",
        number: "SJ/2026/09/08/000006",
        lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }],
      }],
      surat_jalan_files: [{
        id: "sj1",
        sj_number: "SJ/2026/09/08/000006",
        lines: [{ line_idx: 1, qty: 1000, name: "14 oz Inject Datar Frosted Klir" }],
      }],
    };
    const lines = loadingPoLines(order, "sj1");
    expect(lines[0].available).toBe(1000);
    expect(lines[1].available).toBe(0);
    expect(lines.reduce((s, r) => s + r.available, 0)).toBe(1000);
    expect(loadingVerificationStatus(order, "sj1").code).not.toBe("packing_insufficient");
  });

  test("already-loaded 400 of packed 1000 leaves remaining 600", () => {
    const lines = loadingPoLines({
      line_items: [{ name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LA" }],
      packing_info: { lines: [{ line_idx: 0, line_id: "LA", packed_qty: 1000 }] },
      surat_jalan_files: [{ id: "sj1", lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }] }],
      loading_confirmations: [{ id: "c1", surat_jalan_id: "sj1", lines: [{ line_idx: 0, qty: 400 }] }],
    }, "sj1");
    expect(lines[0].available).toBe(600);
  });

  test("legacy loading_info reduces frontend remaining like backend shortfall", () => {
    const lines = loadingPoLines({
      line_items: [{ name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LA" }],
      packing_info: { lines: [{ line_idx: 0, line_id: "LA", packed_qty: 1000 }] },
      surat_jalan_files: [{ id: "sj1", lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }] }],
      loading_info: { lines: [{ line_idx: 0, loaded_qty: 1000 }] },
    }, "sj1");
    expect(lines[0].packed).toBe(1000);
    expect(lines[0].available).toBe(0);
  });

  test("generic enrich without SJ identity is not assumed to be selected SJ", () => {
    const frosted = "14 oz Inject Datar Frosted Klir";
    const order = {
      line_items: [
        { name: frosted, quantity: 1000, production_process: "print", line_id: "LA" },
        { name: frosted, quantity: 1000, production_process: "print", line_id: "LB" },
      ],
      packing_info: {
        lines: [
          { line_idx: 0, line_id: "LA", packed_qty: 1000 },
          { line_idx: 1, line_id: "LB", packed_qty: 0 },
        ],
      },
      shipments: [
        { id: "ship-a", file_id: "file-a", lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }] },
        { id: "ship-b", file_id: "file-b", lines: [{ line_idx: 1, line_id: "LB", qty: 1000 }] },
      ],
      surat_jalan_files: [
        { id: "file-a", shipment_id: "ship-a", lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }] },
        { id: "file-b", shipment_id: "ship-b", lines: [{ line_idx: 1, line_id: "LB", qty: 1000 }] },
      ],
      loading_verification_status: {
        code: "packing_insufficient",
        label: "Packing Belum Mencukupi",
        reason: "Packing belum mencukupi untuk 14 oz Inject Datar Frosted Klir: diperlukan 1.000, tersedia 0.",
      },
    };
    const verA = loadingVerificationStatus(order, "file-a");
    expect(verA.code).toBe("ready_to_verify");
    expect(verA.reason).toBeFalsy();
  });

  test("stale GET enrich for latest SJ B is not reused for selected SJ A", () => {
    const frosted = "14 oz Inject Datar Frosted Klir";
    const order = {
      line_items: [
        { name: frosted, quantity: 1000, production_process: "print", line_id: "LA" },
        { name: frosted, quantity: 1000, production_process: "print", line_id: "LB" },
      ],
      packing_info: {
        lines: [
          { line_idx: 0, line_id: "LA", packed_qty: 1000 },
          { line_idx: 1, line_id: "LB", packed_qty: 0 },
        ],
      },
      shipments: [
        {
          id: "ship-a",
          number: "SJ/2026/09/08/000006",
          file_id: "file-a",
          lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }],
        },
        {
          id: "ship-b",
          number: "SJ/2026/09/08/000007",
          file_id: "file-b",
          lines: [{ line_idx: 1, line_id: "LB", qty: 1000 }],
        },
      ],
      surat_jalan_files: [
        {
          id: "file-a",
          shipment_id: "ship-a",
          sj_number: "SJ/2026/09/08/000006",
          lines: [{ line_idx: 1, qty: 1000, name: frosted }],
        },
        {
          id: "file-b",
          shipment_id: "ship-b",
          sj_number: "SJ/2026/09/08/000007",
          lines: [{ line_idx: 1, qty: 1000, name: frosted }],
        },
      ],
      loading_verification_status: {
        code: "packing_insufficient",
        label: "Packing Belum Mencukupi",
        surat_jalan_id: "ship-b",
        reason: "Packing belum mencukupi untuk 14 oz Inject Datar Frosted Klir: diperlukan 1.000, tersedia 0.",
      },
    };
    const linesA = loadingPoLines(order, "file-a");
    expect(linesA.reduce((s, r) => s + r.qty_sj, 0)).toBe(1000);
    expect(linesA.reduce((s, r) => s + r.available, 0)).toBe(1000);
    const verA = loadingVerificationStatus(order, "file-a");
    expect(verA.code).toBe("ready_to_verify");
    expect(verA.reason).toBeFalsy();
    const verB = loadingVerificationStatus(order, "file-b");
    expect(verB.code).toBe("packing_insufficient");
    expect(String(verB.reason || "")).toMatch(/tersedia 0/);
    expect(loadingPoLines(order, "file-b").reduce((s, r) => s + r.available, 0)).toBe(0);
  });

  test("selected A packed 0 stays blocked even if latest B is packed", () => {
    const frosted = "14 oz Inject Datar Frosted Klir";
    const order = {
      line_items: [
        { name: frosted, quantity: 1000, production_process: "print", line_id: "LA" },
        { name: frosted, quantity: 1000, production_process: "print", line_id: "LB" },
      ],
      packing_info: {
        lines: [
          { line_idx: 0, line_id: "LA", packed_qty: 0 },
          { line_idx: 1, line_id: "LB", packed_qty: 1000 },
        ],
      },
      shipments: [
        { id: "ship-a", file_id: "file-a", lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }] },
        { id: "ship-b", file_id: "file-b", lines: [{ line_idx: 1, line_id: "LB", qty: 1000 }] },
      ],
      surat_jalan_files: [
        { id: "file-a", shipment_id: "ship-a", lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }] },
        { id: "file-b", shipment_id: "ship-b", lines: [{ line_idx: 1, line_id: "LB", qty: 1000 }] },
      ],
    };
    expect(loadingVerificationStatus(order, "file-a").code).toBe("packing_insufficient");
    expect(loadingVerificationStatus(order, "file-b").code).toBe("ready_to_verify");
  });

  test("file-id selection prefers linked shipment lines over stale file lines", () => {
    const frosted = "14 oz Inject Datar Frosted Klir";
    const order = {
      line_items: [
        { name: frosted, quantity: 1000, production_process: "print", line_id: "LA" },
        { name: frosted, quantity: 1000, production_process: "print", line_id: "LB" },
      ],
      packing_info: {
        lines: [
          { line_idx: 0, line_id: "LA", packed_qty: 1000 },
          { line_idx: 1, line_id: "LB", packed_qty: 0 },
        ],
      },
      shipments: [{
        id: "ship-a",
        number: "SJ/2026/09/08/000006",
        file_id: "file-uuid-a",
        lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }],
      }],
      surat_jalan_files: [{
        id: "file-uuid-a",
        shipment_id: "ship-a",
        sj_number: "SJ/2026/09/08/000006",
        lines: [
          { line_idx: 0, qty: 1000, name: frosted },
          { line_idx: 1, qty: 1000, name: frosted },
        ],
      }],
    };
    const lines = loadingPoLines(order, "file-uuid-a");
    expect(lines.reduce((s, r) => s + r.qty_sj, 0)).toBe(1000);
    expect(lines[0].available).toBe(1000);
    expect(loadingVerificationStatus(order, "file-uuid-a").code).toBe("ready_to_verify");
  });

  test("frontend and backend agreement fixture: current format ready", () => {
    const order = {
      line_items: [
        { name: "14 oz Inject Datar Frosted Klir", quantity: 1000, production_process: "print", line_id: "LA" },
        { name: "Other Cup", quantity: 1000, production_process: "non_print", line_id: "LB" },
      ],
      packing_info: { lines: [{ line_idx: 0, line_id: "LA", packed_qty: 1000 }] },
      surat_jalan_files: [{
        id: "sj1",
        lines: [
          { line_idx: 0, line_id: "LA", qty: 1000 },
          { line_idx: 1, line_id: "LB", qty: 1000 },
        ],
      }],
    };
    const lines = loadingPoLines(order, "sj1");
    expect(lines[0].available).toBe(1000);
    expect(loadingVerificationStatus(order, "sj1").code).toBe("ready_to_verify");
  });

  test("CASE D non-print partial historical Loading uses min(physical remainder, SJ remainder)", () => {
    const lines = loadingPoLines({
      line_items: [{ name: "Box", quantity: 1000, production_process: "non_print", line_id: "NP1" }],
      packing_info: {},
      surat_jalan_files: [{ id: "sj1", lines: [{ line_idx: 0, line_id: "NP1", qty: 1000 }] }],
      loading_confirmations: [{ id: "c1", surat_jalan_id: "sj1", lines: [{ line_idx: 0, qty: 400 }] }],
    }, "sj1");
    expect(lines[0].packed).toBe(0);
    expect(lines[0].loaded).toBe(400);
    expect(lines[0].physical_available).toBe(600);
    expect(lines[0].sj_available).toBe(600);
    expect(lines[0].available).toBe(600);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "600" }], lines).ok).toBe(true);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "601" }], lines).ok).toBe(false);
  });

  test("CASE D selected-SJ remainder can be tighter than physical remainder", () => {
    const lines = loadingPoLines({
      line_items: [{ name: "Box", quantity: 1000, production_process: "non_print", line_id: "NP1" }],
      packing_info: {},
      surat_jalan_files: [{ id: "sj1", lines: [{ line_idx: 0, line_id: "NP1", qty: 600 }] }],
      loading_confirmations: [{ id: "c1", surat_jalan_id: "sj1", lines: [{ line_idx: 0, qty: 0 }] }],
      loading_info: { lines: [{ line_idx: 0, loaded_qty: 400 }] },
    }, "sj1");
    expect(lines[0].physical_available).toBe(600);
    expect(lines[0].sj_available).toBe(600);
    expect(lines[0].available).toBe(600);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "601" }], lines).ok).toBe(false);
  });

  test("CASE E non-print multi SJ: selected B ignores A loaded but A still reduces physical", () => {
    const order = {
      line_items: [{ name: "Box", quantity: 1000, production_process: "non_print", line_id: "NP1" }],
      packing_info: {},
      shipments: [
        { id: "sj-a", file_id: "sj-a", lines: [{ line_idx: 0, line_id: "NP1", qty: 400 }] },
        { id: "sj-b", file_id: "sj-b", lines: [{ line_idx: 0, line_id: "NP1", qty: 600 }] },
      ],
      surat_jalan_files: [
        { id: "sj-a", shipment_id: "sj-a", lines: [{ line_idx: 0, line_id: "NP1", qty: 400 }] },
        { id: "sj-b", shipment_id: "sj-b", lines: [{ line_idx: 0, line_id: "NP1", qty: 600 }] },
      ],
      loading_confirmations: [
        { id: "c-a", surat_jalan_id: "sj-a", lines: [{ line_idx: 0, qty: 400 }] },
      ],
    };
    const a = loadingPoLines(order, "sj-a");
    const b = loadingPoLines(order, "sj-b");
    expect(a[0].loaded).toBe(400);
    expect(a[0].available).toBe(0);
    expect(b[0].loaded).toBe(0);
    expect(b[0].physical_available).toBe(600);
    expect(b[0].sj_available).toBe(600);
    expect(b[0].available).toBe(600);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "600" }], b).ok).toBe(true);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "601" }], b).ok).toBe(false);
  });

  test("CASE F same-name non-print lines do not share Loading availability", () => {
    const lines = loadingPoLines({
      line_items: [
        { name: "Box Polos", quantity: 400, production_process: "non_print", line_id: "NP-A" },
        { name: "Box Polos", quantity: 600, production_process: "non_print", line_id: "NP-B" },
      ],
      packing_info: {},
      surat_jalan_files: [{
        id: "sj1",
        lines: [
          { line_idx: 0, line_id: "NP-A", qty: 400 },
          { line_idx: 1, line_id: "NP-B", qty: 600 },
        ],
      }],
      loading_confirmations: [{ id: "c1", surat_jalan_id: "sj1", lines: [{ line_idx: 0, qty: 400 }] }],
    }, "sj1");
    expect(lines[0].available).toBe(0);
    expect(lines[1].available).toBe(600);
    expect(validateLoadingDraftLines([{ line_idx: 1, qty: "600" }], lines).ok).toBe(true);
    expect(validateLoadingDraftLines([{ line_idx: 0, qty: "1" }], lines).ok).toBe(false);
  });

  test("CASE G cancelled Loading does not consume non-print availability", () => {
    const lines = loadingPoLines({
      line_items: [{ name: "Box", quantity: 1000, production_process: "non_print", line_id: "NP1" }],
      packing_info: {},
      surat_jalan_files: [{ id: "sj1", lines: [{ line_idx: 0, line_id: "NP1", qty: 1000 }] }],
      loading_confirmations: [
        { id: "c1", surat_jalan_id: "sj1", cancelled: true, lines: [{ line_idx: 0, qty: 700 }] },
        { id: "c2", surat_jalan_id: "sj1", deleted: true, lines: [{ line_idx: 0, qty: 200 }] },
      ],
    }, "sj1");
    expect(lines[0].loaded).toBe(0);
    expect(lines[0].available).toBe(1000);
  });

  test("PWA gate agrees with loadingPoLines: non-print packed 0 is loadable", () => {
    const order = {
      line_items: [{ name: "Box", quantity: 1000, production_process: "non_print", line_id: "NP1" }],
      packing_info: {},
      surat_jalan_files: [{ id: "sj1", lines: [{ line_idx: 0, line_id: "NP1", qty: 1000 }] }],
    };
    const poLines = loadingPoLines(order, "sj1");
    const avail = loadingAvailability(order, poLines, [{ id: "sj1" }]);
    expect(poLines[0].packed).toBe(0);
    expect(poLines[0].available).toBe(1000);
    expect(avail.blocked).toBe(false);
    expect(avail.reason).not.toMatch(/qty Packing/i);
  });

  test("PWA gate agrees with loadingPoLines: printable packed 0 stays blocked", () => {
    const order = {
      line_items: [{ name: "Cup", quantity: 1000, production_process: "print", line_id: "P1" }],
      packing_info: { lines: [{ line_idx: 0, line_id: "P1", packed_qty: 0 }] },
      surat_jalan_files: [{ id: "sj1", lines: [{ line_idx: 0, line_id: "P1", qty: 1000 }] }],
    };
    const poLines = loadingPoLines(order, "sj1");
    const avail = loadingAvailability(order, poLines, [{ id: "sj1" }]);
    expect(poLines[0].available).toBe(0);
    expect(avail.blocked).toBe(true);
    expect(avail.reason).toMatch(/qty Packing/i);
  });
});
