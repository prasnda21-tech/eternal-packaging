import fs from "fs";
import path from "path";
import { DIVISION_TASK_PAGES } from "./divisionTasks";
import { NAV_MENU_FALLBACK, TASK_NAV_LABELS, tasksNavVisible } from "./navAccess";
import { WORK_DIVISION_IDS, fallbackDivision, permittedDivisionIds } from "./workPanel";

describe("Loading restored as SJ qty confirmation", () => {
  test("sidebar Tugas Produksi includes Loading", () => {
    expect(NAV_MENU_FALLBACK.some((m) => m.label === "Loading")).toBe(true);
    expect(TASK_NAV_LABELS.loading).toBe("Loading");
    expect(TASK_NAV_LABELS.terkirim).toBe("Driver / Terkirim");
  });

  test("division task pages include Loading workspace", () => {
    expect(DIVISION_TASK_PAGES.loading).toBeTruthy();
    expect(WORK_DIVISION_IDS).toContain("loading");
    expect(fallbackDivision("loading").id).toBe("loading");
  });

  test("LoadingPanel has manual item/qty and confirm, no SJ item/qty on that card purpose", () => {
    const panel = fs.readFileSync(path.join(__dirname, "../components/LoadingPanel.jsx"), "utf8");
    expect(panel).toMatch(/KONFIRMASI ITEM & QTY/);
    expect(panel).toMatch(/loading-qty-input/);
    expect(panel).toMatch(/loading-physical-gate/);
    expect(panel).toMatch(/confirmLoading/);
    expect(panel).toMatch(/KONFIRMASI LOADING/);
    expect(panel).toMatch(/surat_jalan_id/);
    expect(panel).toMatch(/Lihat Surat Jalan|loading-sj-lihat/);
    expect(panel).toMatch(/loading-sj-number/);
    expect(panel).toMatch(/loading-sj-file-row/);
  });

  test("PO detail has active Loading action", () => {
    const po = fs.readFileSync(path.join(__dirname, "../pages/PODetail.jsx"), "utf8");
    expect(po).toMatch(/canLoadingUpdate/);
    expect(po).toMatch(/canManage=\{canLoadingUpdate\}/);
  });

  test("loading.update shows a tugas produksi child", () => {
    const v = tasksNavVisible((k) => k === "loading.update");
    expect(v.loading).toBe(true);
    expect(v.parent).toBe(true);
  });

  test("permitted divisions include loading", () => {
    const ids = permittedDivisionIds((k) => true);
    expect(ids).toContain("loading");
  });

  test("no Buat Jadwal Pengiriman in active schedule page", () => {
    const schedule = fs.readFileSync(path.join(__dirname, "../pages/DeliverySchedulePage.jsx"), "utf8");
    expect(schedule).not.toMatch(/Buat Jadwal Pengiriman/);
    expect(schedule).not.toMatch(/ds-create-schedule/);
    const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
    expect(app).toMatch(/path="\/produksi\/jadwal-pengiriman\/buat"/);
    expect(app).toMatch(/Navigate to="\/produksi\/jadwal-pengiriman"/);
    expect(app).not.toMatch(/CreateDeliverySchedulePage/);
  });
});
