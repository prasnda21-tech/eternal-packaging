import {
  discrepancyDiff,
  validateDiscrepancySelection,
  loadingVerificationStatus,
  canManageLoadingActions,
  canViewLoading,
  LOADING_CONFIRM_MATCH_PERM,
  LOADING_REPORT_DISCREPANCY_PERM,
  LOADING_VIEW_PERM,
} from "./loadingConfirm";

describe("Loading SJ verification helpers", () => {
  test("discrepancyDiff classifies kurang and lebih", () => {
    const short = discrepancyDiff(4000, 3000);
    expect(short.ok).toBe(true);
    expect(String(short.classification || short.label || "")).toMatch(/kurang/i);
    const over = discrepancyDiff(4000, 4500);
    expect(over.ok).toBe(true);
    expect(String(over.classification || over.label || "")).toMatch(/lebih/i);
    expect(discrepancyDiff(4000, 4000).ok).toBe(false);
    expect(discrepancyDiff(4000, -1).ok).toBe(false);
    expect(discrepancyDiff(4000, "").ok).toBe(false);
  });

  test("validateDiscrepancySelection requires genuine mismatches only on marked lines", () => {
    const lines = [
      { line_idx: 0, name: "Cup", qty_sj: 5000 },
      { line_idx: 1, name: "LID", qty_sj: 4000 },
    ];
    expect(validateDiscrepancySelection({}, lines).ok).toBe(false);
    expect(validateDiscrepancySelection({ 1: { marked: true, actual_qty: "4000" } }, lines).ok).toBe(false);
    const ok = validateDiscrepancySelection({ 1: { marked: true, actual_qty: "3000" } }, lines);
    expect(ok.ok).toBe(true);
    expect(ok.mismatches).toEqual([{ line_idx: 1, actual_qty: 3000 }]);
  });

  test("permission helpers keep view separate from mutate", () => {
    const has = (k) => k === LOADING_VIEW_PERM;
    expect(canViewLoading(has)).toBe(true);
    expect(canManageLoadingActions(has)).toBe(false);
    const mut = (k) => k === LOADING_CONFIRM_MATCH_PERM || k === LOADING_REPORT_DISCREPANCY_PERM;
    expect(canManageLoadingActions(mut)).toBe(true);
  });

  test("loadingVerificationStatus surfaces held mismatch", () => {
    const order = {
      loading_discrepancies: [{ id: "d1", status: "open", surat_jalan_id: "sj1", lines: [] }],
      surat_jalan_files: [{ id: "sj1", lines: [{ line_idx: 0, qty: 1 }] }],
    };
    const st = loadingVerificationStatus(order, "sj1");
    expect(String(st.label || "")).toMatch(/Tertahan|Sesuai SJ|Tidak Sesuai/i);
  });

  test("dropdown A to B does not keep stale A verification", () => {
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
        code: "ready_to_verify",
        label: "Siap Diverifikasi",
        surat_jalan_id: "file-a",
      },
    };
    expect(loadingVerificationStatus(order, "file-a").code).toBe("ready_to_verify");
    const after = loadingVerificationStatus(order, "file-b");
    expect(after.code).toBe("packing_insufficient");
    expect(after.surat_jalan_id).toBe("file-b");
  });

  test("ORANJE replaced SJ confirmation still blocks as prior Loading", () => {
    const cup = "14 oz Inject Datar Frosted Klir";
    const lid = "LID Connect Inject Frosted/Dop Victory";
    const sj = "e076bb1d-c360-4746-b789-49a0e42edfd2";
    const old = "dc9c9875-9d8b-47c0-ad6f-37af8d15a1a2";
    const order = {
      line_items: [
        { name: cup, quantity: 1000, production_process: "print", accurate_item_id: "8350" },
        { name: lid, quantity: 1000, production_process: "non_print", accurate_item_id: "3950" },
      ],
      packing_info: {
        qty_packed: 1000,
        lines: [{ line_idx: 0, packed_qty: 1000 }, { line_idx: 1, packed_qty: 0 }],
      },
      shipments: [{
        id: sj,
        number: "SJ/2026/09/08/000006",
        lines: [
          { line_idx: 0, line_id: "89db8fba-4cc0-4e74-bb49-ad67fa18ba0d_line_0", qty: 1000 },
          { line_idx: 1, line_id: "89db8fba-4cc0-4e74-bb49-ad67fa18ba0d_line_1", qty: 1000 },
        ],
      }],
      surat_jalan_files: [{
        id: sj,
        shipment_id: sj,
        sj_number: "SJ/2026/09/08/000006",
        total_qty: 2000,
        lines: [
          { line_idx: 0, line_id: "89db8fba-4cc0-4e74-bb49-ad67fa18ba0d_line_0", qty: 1000 },
          { line_idx: 1, line_id: "89db8fba-4cc0-4e74-bb49-ad67fa18ba0d_line_1", qty: 1000 },
        ],
      }],
      loading_confirmations: [{
        id: "old-c0",
        surat_jalan_id: old,
        lines: [{ line_idx: 0, qty: 1000 }],
      }],
    };
    const st = loadingVerificationStatus(order, sj);
    expect(st.code).toBe("prior_loading_consumed");
    expect(st.surat_jalan_id).toBe(sj);
    expect(String(st.reason || "")).toMatch(/sudah pernah dikonfirmasi Loading/);
    expect(String(st.reason || "")).toContain(cup);
    expect(String(st.reason || "")).not.toMatch(/Packing belum mencukupi/);
  });
});
