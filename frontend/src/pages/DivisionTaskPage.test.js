import fs from "fs";
import path from "path";
import { formatCurrentStock, formatIdQty, DIVISION_TASK_PAGES } from "../lib/divisionTasks";

describe("Division task UI", () => {
  const page = fs.readFileSync(path.join(__dirname, "DivisionTaskPage.jsx"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const lib = fs.readFileSync(path.join(__dirname, "../lib/divisionTasks.js"), "utf8");
  const packing = fs.readFileSync(path.join(__dirname, "../components/PackingPanel.jsx"), "utf8");
  const sj = fs.readFileSync(path.join(__dirname, "../components/SuratJalanPanel.jsx"), "utf8");

  test("sidebar Tugas Produksi and all submenus", () => {
    expect(shell).toMatch(/Tugas Produksi/);
    expect(shell).not.toMatch(/Tugas per Divisi/);
    expect(nav).toMatch(/top-nav-tasks-\$\{id\}/);
    expect(nav).toMatch(/TASK_NAV_ORDER/);
    expect(shell).toMatch(/TASK_NAV_ORDER/);
  });

  test("routes exist per division", () => {
    expect(app).toMatch(/\/tasks\/designer/);
    expect(app).toMatch(/\/tasks\/kepala-produksi/);
    expect(app).toMatch(/\/tasks\/screener/);
    expect(app).toMatch(/\/tasks\/helper/);
    expect(app).toMatch(/\/tasks\/operator/);
    expect(app).toMatch(/\/tasks\/packing/);
    expect(app).toMatch(/\/tasks\/surat-jalan/);
    expect(app).toMatch(/\/tasks\/loading/);
    expect(app).toMatch(/\/tasks\/delivery/);
  });

  test("slug comes from pathname not missing :slug param", () => {
    expect(page).toMatch(/useLocation/);
    expect(page).toMatch(/pathname/);
    expect(page).toMatch(/DIVISION_TASK_PAGES\[slug\]/);
  });

  test("Yang Harus Diinput column and no Packing Sebagian", () => {
    expect(page).toMatch(/Yang Harus Diinput/);
    expect(page).toMatch(/inputColLabel/);
    expect(lib).toMatch(/Yang Harus Dilakukan/);
    expect(lib).toMatch(/TUGAS DESIGNER/);
    expect(lib).toMatch(/TUGAS KEPALA PRODUKSI/);
    expect(lib).toMatch(/Sudah Dijadwalkan/);
    expect(page).toMatch(/required_input/);
    expect(page).not.toMatch(/Packing Sebagian/);
    expect(packing).not.toMatch(/Packing Sebagian/);
    expect(packing).not.toMatch(/complete-packing-button/);
  });

  test("shows Production Schedule H-1 / URGENT badges on task rows", () => {
    expect(page).toMatch(/ProductionSchedulePriorityBadges/);
  });

  test("Lihat PO gated by can_view_po", () => {
    expect(page).toMatch(/canViewPo/);
    expect(page).toMatch(/Lihat PO/);
  });

  test("Surat Jalan remains upload-only", () => {
    expect(sj).not.toMatch(/sj-accurate-import/);
    expect(page).toMatch(/Upload File Surat Jalan|sj_status/);
  });

  test("STOCK SAAT INI header and column order after ITEM before QTY ITEM CETAK", () => {
    expect(page).toMatch(/Stock Saat Ini/);
    expect(lib).toMatch(/showCurrentStock:\s*true/);
    const itemIdx = page.indexOf("<th>Item</th>");
    const stockIdx = page.indexOf("Stock Saat Ini");
    const qtyIdx = page.indexOf('{cfg.targetLabel || "Target"}');
    expect(itemIdx).toBeGreaterThan(-1);
    expect(stockIdx).toBeGreaterThan(itemIdx);
    expect(qtyIdx).toBeGreaterThan(stockIdx);
    expect(DIVISION_TASK_PAGES["kepala-produksi"].showCurrentStock).toBe(true);
    expect(DIVISION_TASK_PAGES["kepala-produksi"].targetLabel).toBe("Qty Item Cetak");
  });

  test("current stock formatting: positive, zero, unresolved dash", () => {
    expect(formatCurrentStock(3250)).toBe("3.250");
    expect(formatCurrentStock(0)).toBe("0");
    expect(formatCurrentStock(null)).toBe("—");
    expect(formatCurrentStock(undefined)).toBe("—");
    expect(formatCurrentStock("")).toBe("—");
    // null must not silently become 0 via Number(null)
    expect(formatCurrentStock(null)).not.toBe("0");
    expect(formatIdQty(2000)).toBe("2.000");
  });

  test("existing filters and scope buttons still rendered", () => {
    expect(page).toMatch(/division-task-status-filters/);
    expect(page).toMatch(/division-task-scope-mine/);
    expect(page).toMatch(/division-task-scope-all/);
    expect(page).toMatch(/Tugas Saya/);
    expect(page).toMatch(/Semua Tugas Divisi/);
  });

  test("row still keyed by task_id and exposes line via work href", () => {
    expect(page).toMatch(/key=\{t\.task_id\}/);
    expect(page).toMatch(/formatCurrentStock\(t\.current_stock\)/);
    expect(lib).toMatch(/line_idx/);
  });
});
