import fs from "fs";
import path from "path";
import { resolveStageState } from "./stageState";
import { resolveApprovalDesignChip } from "./designEvidence";

describe("SJ / Loading / Terkirim chips", () => {
  test("SJ uses shipment evidence, not a done-from-stage index", () => {
    const sj = resolveStageState(12, {
      surat_jalan_created: true,
      surat_jalan_status_semantic: "partial",
      current_stage: 12,
    }, 12);
    expect(sj.label).toMatch(/parsial/i);
    expect(sj.state).toBe("current");
  });

  test("deleted/cancelled SJ only: Surat Jalan is not falsely complete", () => {
    const sj = resolveStageState(12, {
      surat_jalan_created: false,
      surat_jalan_status_semantic: "pending",
      packing_info: { qty_packed: 3000, lines: [{ line_idx: 0, packed_qty: 3000 }] },
      current_stage: 12,
    }, 12);
    expect(sj.state).not.toBe("done");
    expect(sj.label).toMatch(/menunggu/i);
    expect(sj.label).not.toMatch(/selesai/i);

    const loading = resolveStageState(13, {
      surat_jalan_created: false,
      loading_qty_status: "pending",
      current_stage: 12,
    }, 12);
    expect(loading.state).toBe("pending");
    expect(loading.label).toMatch(/surat jalan/i);

    const terkirim = resolveStageState(14, {
      surat_jalan_created: false,
      eligible_delivery_qty: 0,
      terkirim_confirmed: false,
      delivery_status: "Belum Terkirim",
      current_stage: 12,
    }, 12);
    expect(terkirim.state).toBe("pending");
    expect(terkirim.state).not.toBe("current");
    expect(terkirim.state).not.toBe("done");
  });

  test("shipment ghost without live file does not keep SJ stage active", () => {
    const sj = resolveStageState(12, {
      surat_jalan_created: false,
      surat_jalan_status_semantic: "pending",
      shipments: [{ id: "ghost", sales_delivery_id: "ghost", lines: [{ line_idx: 0, qty: 3000 }] }],
      surat_jalan_files: [],
      sj_files: [],
      packing_info: { qty_packed: 3000, lines: [{ line_idx: 0, packed_qty: 3000 }] },
      current_stage: 12,
    }, 12);
    expect(sj.state).not.toBe("done");
    expect(sj.label).toMatch(/menunggu/i);

    const loading = resolveStageState(13, {
      surat_jalan_created: false,
      surat_jalan_files: [],
      current_stage: 12,
    }, 12);
    expect(loading.state).toBe("pending");
  });

  test("historical deleted SJ does not drive stage; new active SJ does", () => {
    const historyOnly = resolveStageState(12, {
      surat_jalan_created: false,
      surat_jalan_status_semantic: "pending",
      sj_files: [{ id: "old-deleted", name: "SJ-OLD.pdf", is_deleted: true }],
      current_stage: 12,
    }, 12);
    expect(historyOnly.state).not.toBe("done");
    expect(historyOnly.label).toMatch(/menunggu/i);

    const newActive = resolveStageState(12, {
      surat_jalan_created: true,
      surat_jalan_status_semantic: "partial",
      current_stage: 12,
    }, 12);
    expect(newActive.state).toBe("current");
    expect(newActive.label).toMatch(/parsial|siap loading/i);
  });

  test("SJ + full Loading is Selesai on Alur Pekerjaan", () => {
    const sj = resolveStageState(12, {
      surat_jalan_created: true,
      surat_jalan_status_semantic: "completed",
      loading_qty_status: "completed",
      current_stage: 13,
    }, 13);
    expect(sj.label).toBe("Selesai");
    expect(sj.state).toBe("done");
  });

  test("SJ stays partial when Loading is short", () => {
    const sj = resolveStageState(12, {
      surat_jalan_created: true,
      surat_jalan_status_semantic: "partial",
      loading_qty_status: "partial",
      current_stage: 13,
    }, 13);
    expect(sj.label).toMatch(/parsial/i);
    expect(sj.state).not.toBe("done");
  });

  test("Loading is current after Surat Jalan file", () => {
    const loading = resolveStageState(13, {
      surat_jalan_created: true,
      current_stage: 12,
    }, 12);
    expect(loading.label).toMatch(/verifikasi|kesesuaian|sj/i);
    expect(loading.state).toBe("current");
  });

  test("legacy Loading history stays read-only", () => {
    const loading = resolveStageState(13, {
      loading_qty_status: "partial",
      loading_completed: false,
      loading_info: { events: [{ qty: 10 }] },
      current_stage: 13,
    }, 13);
    expect(loading.label).toMatch(/riwayat/i);
    expect(loading.state).toBe("done");
  });

  test("Terkirim waits for Loading confirmation before proof", () => {
    const terkirim = resolveStageState(14, {
      surat_jalan_created: true,
      terkirim_confirmed: false,
      delivery_status: "Belum Terkirim",
      current_stage: 12,
    }, 12);
    expect(terkirim.label).toMatch(/loading/i);
    expect(terkirim.state).toBe("pending");
  });

  test("early delivery process / SJ existence does not make Pengiriman current without Loading", () => {
    const loading = resolveStageState(13, {
      surat_jalan_created: true,
      loading_qty_status: "pending",
      terkirim_confirmed: false,
      delivery_status: "Belum Terkirim",
      eligible_delivery_qty: 0,
      qty_delivered: 0,
      current_stage: 12,
    }, 12);
    expect(loading.state).toBe("current");
    expect(loading.label).toMatch(/verifikasi|kesesuaian|sj/i);

    const terkirim = resolveStageState(14, {
      surat_jalan_created: true,
      terkirim_confirmed: false,
      delivery_status: "Belum Terkirim",
      eligible_delivery_qty: 0,
      qty_delivered: 0,
      current_stage: 12,
    }, 12);
    expect(terkirim.state).toBe("pending");
    expect(terkirim.label).toMatch(/loading/i);
    expect(terkirim.state).not.toBe("current");
    expect(terkirim.state).not.toBe("done");
  });

  test("partial loaded batch can open Pengiriman when eligible reflects Loading only", () => {
    const terkirim = resolveStageState(14, {
      surat_jalan_created: true,
      terkirim_confirmed: false,
      delivery_status: "Belum Terkirim",
      eligible_delivery_qty: 2000,
      qty_delivered: 0,
      current_stage: 13,
    }, 13);
    expect(terkirim.state).toBe("current");
    expect(terkirim.label).toMatch(/bukti/i);
  });

  test("partial delivery does not mark full Terkirim", () => {
    const terkirim = resolveStageState(14, {
      delivery_status: "Sebagian Terkirim",
      qty_delivered: 4000,
      terkirim_confirmed: false,
      current_stage: 13,
    }, 13);
    expect(terkirim.label).toMatch(/parsial/i);
    expect(terkirim.state).not.toBe("done");
  });

  test("Pengiriman complete follows persisted delivery, not leftover bukti kirim cache", () => {
    const waiting = resolveStageState(14, {
      delivery_proofs: [{ id: "p1" }, { id: "p2" }],
      terkirim_confirmed: false,
      delivery_status: "Belum Terkirim",
      qty_delivered: 0,
      eligible_delivery_qty: 100,
      current_stage: 13,
    }, 13);
    expect(waiting.state).not.toBe("done");
    expect(waiting.label).toMatch(/bukti/i);

    const afterProofDelete = resolveStageState(14, {
      delivery_proofs: [],
      terkirim_confirmed: false,
      delivery_status: "Belum Terkirim",
      qty_delivered: 0,
      eligible_delivery_qty: 100,
      current_stage: 13,
    }, 13);
    expect(afterProofDelete.state).not.toBe("done");
    expect(afterProofDelete.label).toMatch(/bukti/i);

    const confirmed = resolveStageState(14, {
      delivery_proofs: [],
      terkirim_confirmed: true,
      delivery_status: "Terkirim",
      qty_delivered: 1000,
      current_stage: 14,
    }, 14);
    expect(confirmed.state).toBe("done");
  });
});

describe("PODetail / SuratJalan source: no generic next or checkboxes", () => {
  const poDetail = fs.readFileSync(path.join(__dirname, "../pages/PODetail.jsx"), "utf8");
  const suratJalan = fs.readFileSync(path.join(__dirname, "../pages/SuratJalan.jsx"), "utf8");
  const loadingPanel = fs.readFileSync(path.join(__dirname, "../components/LoadingPanel.jsx"), "utf8");
  const sjPanel = fs.readFileSync(path.join(__dirname, "../components/SuratJalanPanel.jsx"), "utf8");

  test("PODetail has no Lanjut Loading / Lanjut Terkirim / Tandai Terkirim", () => {
    expect(poDetail).not.toMatch(/Lanjut →/);
    expect(poDetail).not.toMatch(/mark-delivered-button/);
    expect(poDetail).not.toMatch(/advance-button/);
    expect(poDetail).not.toMatch(/Tandai Terkirim/);
  });

  test("Surat Jalan page has no completion checkbox or Tandai Terkirim", () => {
    expect(suratJalan).not.toMatch(/sj-items-loaded/);
    expect(suratJalan).not.toMatch(/sj-seal-ok/);
    expect(suratJalan).not.toMatch(/sj-mark-delivered/);
    expect(suratJalan).not.toMatch(/Tandai Terkirim/);
    expect(suratJalan).not.toMatch(/type="checkbox"/);
  });

  test("Loading discrepancy uses line checkboxes; no stage-completion checkbox", () => {
    // Exception path marks mismatched SJ lines with checkboxes; matching path stays one-click.
    expect(loadingPanel).toMatch(/type="checkbox"/);
    expect(loadingPanel).toMatch(/Ada Selisih Qty|loading-disc-mark/);
    expect(loadingPanel).not.toMatch(/Lanjut Loading/);
    expect(sjPanel).not.toMatch(/type="checkbox"/);
    expect(sjPanel).not.toMatch(/Lanjut Loading/);
  });
});

describe("Parallel Screener & Helper + Strict Downstream Gating", () => {
  test("Penjadwalan Selesai: Screener & Helper are both ready in parallel, Operator is LOCKED", () => {
    const order = {
      current_stage: 6,
      design_bypassed: true,
      payment_bypassed: true,
      operator_splits: [{ id: "s1", quantity: 1000 }],
      screener_info: null,
      helper_prep: null,
      sample_files: [],
      sample_approved: false,
    };

    // Stage 5: Penjadwalan -> Selesai
    expect(resolveStageState(5, order, 6).state).toBe("done");

    // Stage 6: Screener -> Ready / Menunggu input
    const screener = resolveStageState(6, order, 6);
    expect(screener.state).toBe("current");
    expect(screener.label).toBe("Menunggu input");

    // Stage 7: Persiapan Helper -> Ready / Menunggu input (PARALEL!)
    const helper = resolveStageState(7, order, 6);
    expect(helper.state).toBe("current");
    expect(helper.label).toBe("Menunggu input");

    // Stage 8: Operator -> LOCKED / Menunggu
    const op = resolveStageState(8, order, 6);
    expect(op.state).toBe("pending");
    expect(op.label).toBe("Menunggu");
  });

  test("Helper done without Screener: Operator unlocked", () => {
    const order = {
      current_stage: 6,
      design_bypassed: true,
      payment_bypassed: true,
      operator_splits: [{ id: "s1", quantity: 1000 }],
      screener_info: null,
      helper_prep: { qty_prepared: 1000 },
      sample_files: [],
      sample_approved: false,
    };

    expect(resolveStageState(6, order, 6).state).toBe("current");
    expect(resolveStageState(7, order, 6).state).toBe("done");
    expect(resolveStageState(8, order, 6).state).toBe("current");
  });
});

describe("Customer Approval Design StageFlow", () => {
  const uploaded = {
    id: "df-1",
    name: "art.pdf",
    data_url: "data:application/pdf;base64,eA==",
    uploaded_at: "2026-09-11T01:00:00+00:00",
  };

  test("CA approved advances Approval Design; Payment stays current and not auto-complete", () => {
    const order = {
      current_stage: 2,
      design_status: "Waiting",
      design_bypassed: false,
      design_files: [uploaded],
      payment_status: "Waiting",
      payment_files: [],
      customer_approval: { status: "approved" },
    };
    expect(resolveApprovalDesignChip(order, 2).state).toBe("done");
    const payment = resolveStageState(3, order, 2);
    expect(payment.state).toBe("current");
    expect(payment.label).toBe("Belum dikerjakan");
    expect(resolveStageState(4, order, 2).state).not.toBe("done");
  });
});
