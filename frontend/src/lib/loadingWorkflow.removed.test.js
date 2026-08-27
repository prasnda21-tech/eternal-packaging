import fs from "fs";
import path from "path";
import { DIVISION_TASK_PAGES } from "./divisionTasks";
import { NAV_MENU_FALLBACK, TASK_NAV_LABELS, tasksNavVisible } from "./navAccess";
import { WORK_DIVISION_IDS, fallbackDivision, permittedDivisionIds } from "./workPanel";

describe("Loading removed from active workflow", () => {
  test("sidebar and tugas produksi have no Loading item", () => {
    expect(NAV_MENU_FALLBACK.some((m) => m.label === "Loading")).toBe(false);
    expect(NAV_MENU_FALLBACK.some((m) => String(m.key).includes("loading"))).toBe(false);
    expect(TASK_NAV_LABELS.loading).toBeUndefined();
    expect(TASK_NAV_LABELS.surat_jalan).toBe("Surat Jalan");
    expect(TASK_NAV_LABELS.terkirim).toBe("Driver / Terkirim");
    expect(TASK_NAV_LABELS.packing).toBe("Packing");
  });

  test("division task pages have no Loading workspace", () => {
    expect(DIVISION_TASK_PAGES.loading).toBeUndefined();
    expect(WORK_DIVISION_IDS).not.toContain("loading");
    expect(WORK_DIVISION_IDS).toEqual(expect.arrayContaining(["packing", "surat_jalan", "terkirim"]));
    expect(fallbackDivision("loading").id).not.toBe("loading");
  });

  test("qty loading input is gone from LoadingPanel", () => {
    const panel = fs.readFileSync(path.join(__dirname, "../components/LoadingPanel.jsx"), "utf8");
    expect(panel).toMatch(/Riwayat Loading/);
    expect(panel).not.toMatch(/loading-qty-input/);
    expect(panel).not.toMatch(/updateLoading/);
    expect(panel).not.toMatch(/Input Qty Loading/);
    expect(panel).not.toMatch(/canManage/);
  });

  test("PO detail has no active Loading action", () => {
    const po = fs.readFileSync(path.join(__dirname, "../pages/PODetail.jsx"), "utf8");
    expect(po).not.toMatch(/canLoadingUpdate/);
    expect(po).not.toMatch(/canManage=\{canLoadingUpdate\}/);
    expect(po).toMatch(/<LoadingPanel order=\{order\}/);
  });

  test("loading.update does not show a tugas produksi child", () => {
    const v = tasksNavVisible((k) => k === "loading.update");
    expect(v.loading).toBeUndefined();
    expect(v.parent).toBe(false);
  });

  test("permitted divisions never include loading", () => {
    const ids = permittedDivisionIds((k) => true);
    expect(ids).not.toContain("loading");
  });
});
