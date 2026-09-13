import { resolveStageState } from "./stageState";
import { hasRealDesignUpload, resolveDesignChip, resolveApprovalDesignChip } from "./designEvidence";

function dashboardShape(full) {
  const designFiles = (full.design_files || []).map((f) => {
    const url = (f.url || "").trim() || (f.data_url ? "embedded" : "");
    return { name: f.name || "", url, uploaded_at: f.uploaded_at || "" };
  });
  return {
    ...full,
    design_files: designFiles,
    payment_files: (full.payment_files || []).length ? [{}] : [],
    sample_files: (full.sample_files || []).length ? [{}] : [],
    surat_jalan_files: (full.surat_jalan_files || []).map((f) => ({
      id: f.id,
      file_id: f.file_id,
      name: f.name || "",
      uploaded_at: f.uploaded_at,
      is_deleted: f.is_deleted,
      has_file: f.has_file,
      storage_path: (f.storage_path || (f.data_url || "").startsWith("data:")) ? "1" : "",
    })),
    line_items: undefined,
    timeline: undefined,
    shipments: undefined,
    delivery_proofs: undefined,
    notes: undefined,
  };
}

function rail(order) {
  return [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14].map((idx) => {
    const { state, label } = resolveStageState(idx, order, order.current_stage);
    return `${idx}:${state}:${label}`;
  });
}

describe("StageBar parity on dashboard-view shape", () => {
  test("design upload via data_url remains real evidence after sentinel", () => {
    const full = {
      current_stage: 2,
      design_files: [{ data_url: "data:application/pdf;base64,XXXX" }],
      design_status: "Waiting",
    };
    const lean = dashboardShape(full);
    expect(hasRealDesignUpload(full)).toBe(true);
    expect(hasRealDesignUpload(lean)).toBe(true);
    expect(resolveDesignChip(lean).state).toBe("done");
    expect(resolveApprovalDesignChip(lean).state).toBe("current");
    expect(lean.design_files[0].data_url).toBeUndefined();
  });

  test("design bypass / approved / empty stay identical", () => {
    const bypass = { current_stage: 2, design_bypassed: true, design_files: [] };
    const approved = { current_stage: 3, design_status: "Approved", design_files: [{ name: "a.pdf", uploaded_at: "2026-01-01" }] };
    const empty = { current_stage: 1, design_files: [] };
    expect(resolveDesignChip(dashboardShape(bypass))).toEqual(resolveDesignChip(bypass));
    expect(resolveApprovalDesignChip(dashboardShape(approved))).toEqual(resolveApprovalDesignChip(approved));
    expect(resolveDesignChip(dashboardShape(empty))).toEqual(resolveDesignChip(empty));
  });

  test("payment evidence and bypass", () => {
    const proof = { current_stage: 4, payment_files: [{ data_url: "data:application/pdf;base64,YY" }], payment_status: "Waiting" };
    const bypass = { current_stage: 4, payment_bypassed: true, payment_files: [] };
    const approved = { current_stage: 5, payment_status: "Approved", payment_files: [{}] };
    expect(rail(dashboardShape(proof))).toEqual(rail(proof));
    expect(rail(dashboardShape(bypass))).toEqual(rail(bypass));
    expect(rail(dashboardShape(approved))).toEqual(rail(approved));
  });

  test("screener helper operator sample print packing", () => {
    const mid = {
      current_stage: 8,
      design_status: "Approved",
      payment_status: "Approved",
      design_files: [{ name: "a.pdf", uploaded_at: "2026-01-01" }],
      payment_files: [{ name: "p.pdf" }],
      operator_splits: [{ operator_name: "Op", quantity: 100, qty_completed: 20 }],
      screener_info: { screen_qty: 10 },
      helper_prep: { qty_prepared: 10 },
      sample_files: [{ name: "s.jpg" }],
      sample_approved: true,
      print_status: "BELUM SELESAI CETAK",
      packing_info: { qty_packed: 0, lines: [{ line_idx: 0, packed_qty: 0 }] },
    };
    expect(rail(dashboardShape(mid))).toEqual(rail(mid));
  });

  test("SJ / Loading / Terkirim evidence", () => {
    const sj = {
      current_stage: 12,
      packing_info: { qty_packed: 100, lines: [{ line_idx: 0, packed_qty: 100 }] },
      surat_jalan_created: true,
      surat_jalan_status_semantic: "partial",
      surat_jalan_files: [{ id: "sj1", name: "SJ.pdf", data_url: "data:application/pdf;base64,ZZ" }],
      loading_qty_status: "pending",
      terkirim_confirmed: false,
      eligible_delivery_qty: 0,
    };
    const loaded = {
      ...sj,
      current_stage: 13,
      loading_completed: true,
      loading_qty_status: "completed",
      loading_confirmations: [{ id: "c1" }],
      eligible_delivery_qty: 100,
    };
    const delivered = {
      ...loaded,
      current_stage: 14,
      terkirim_confirmed: true,
      delivery_status: "Terkirim",
      qty_delivered: 100,
    };
    expect(rail(dashboardShape(sj))).toEqual(rail(sj));
    expect(rail(dashboardShape(loaded))).toEqual(rail(loaded));
    expect(rail(dashboardShape(delivered))).toEqual(rail(delivered));
  });

  test("packed=0 does not look load-complete on StageBar", () => {
    const order = {
      current_stage: 12,
      packing_info: { qty_packed: 0, lines: [{ line_idx: 0, packed_qty: 0 }] },
      surat_jalan_created: true,
      loading_qty_status: "pending",
      loading_completed: false,
      eligible_delivery_qty: 0,
    };
    const loading = resolveStageState(13, dashboardShape(order), 12);
    expect(loading.state).not.toBe("done");
  });
});
