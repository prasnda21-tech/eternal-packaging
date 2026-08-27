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

  test("Loading is not an active stage chip", () => {
    const loading = resolveStageState(13, {
      surat_jalan_created: true,
      loading_qty_status: "partial",
      loading_completed: false,
      current_stage: 12,
    }, 12);
    expect(loading.label).toMatch(/riwayat/i);
    expect(loading.state).toBe("done");
  });

  test("Terkirim waits for proof after Surat Jalan, not Loading", () => {
    const terkirim = resolveStageState(14, {
      surat_jalan_created: true,
      terkirim_confirmed: false,
      delivery_status: "Belum Terkirim",
      current_stage: 12,
    }, 12);
    expect(terkirim.label).toMatch(/bukti/i);
    expect(terkirim.state).toBe("current");
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
