import fs from "fs";
import path from "path";
import { resolveStageState } from "./stageState";

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
    expect(loading.label).toMatch(/konfirmasi/i);
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

  test("Loading and SJ panels have no checkbox", () => {
    expect(loadingPanel).not.toMatch(/type="checkbox"/);
    expect(sjPanel).not.toMatch(/type="checkbox"/);
    expect(loadingPanel).not.toMatch(/Lanjut Loading/);
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
