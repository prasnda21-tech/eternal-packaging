import fs from "fs";
import path from "path";
import { formatCurrentStock, formatIdQty, DIVISION_TASK_PAGES } from "../lib/divisionTasks";
import { KP_KPI_ORDER, kpDetailPath, formatKpiCardValue, uniquePoCount } from "../lib/kepalaProduksiBoard";

describe("Division task UI", () => {
  const page = fs.readFileSync(path.join(__dirname, "DivisionTaskPage.jsx"), "utf8");
  const detail = fs.readFileSync(path.join(__dirname, "KepalaProduksiKpiDetailPage.jsx"), "utf8");
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

  test("routes exist per division including KPI detail", () => {
    expect(app).toMatch(/\/tasks\/designer/);
    expect(app).toMatch(/\/tasks\/kepala-produksi/);
    expect(app).toMatch(/\/tasks\/kepala-produksi\/prioritas\/:kpi/);
    expect(app).toMatch(/KepalaProduksiKpiDetailPage/);
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

  test("shows Production Schedule H-1 / URGENT badges on non-KP task rows", () => {
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

  test("KP board keeps Stock Saat Ini and production + schedule qty columns", () => {
    expect(page).toMatch(/Stock Saat Ini/);
    expect(page).toMatch(/Total Order/);
    expect(page).toMatch(/Sudah Diproduksi/);
    expect(page).toMatch(/Belum Diproduksi/);
    expect(page).toMatch(/Sudah Dijadwalkan/);
    expect(page).toMatch(/Belum Dijadwalkan/);
    expect(page).toMatch(/Tanggal Kirim/);
    expect(lib).toMatch(/showCurrentStock:\s*true/);
    expect(DIVISION_TASK_PAGES["kepala-produksi"].showCurrentStock).toBe(true);
  });

  test("tab label Belum Dijadwalkan keeps status key belum for KP only", () => {
    expect(page).toMatch(/Belum Dijadwalkan/);
    expect(page).toMatch(/key:\s*"belum"/);
    expect(page).toMatch(/statusTabsFor/);
    expect(page).toMatch(/taskMatchesStatus/);
  });

  test("six clickable KPI cards navigate to detail pages", () => {
    expect(page).toMatch(/kp-kpi-strip/);
    expect(page).toMatch(/kp-queue-tabs/);
    expect(page).toMatch(/kp-empty-actionable/);
    expect(page).toMatch(/kp-empty-hold-link/);
    expect(page).toMatch(/hold_readonly_count/);
    expect(page).toMatch(/kpDetailPath/);
    expect(KP_KPI_ORDER).toHaveLength(6);
    expect(kpDetailPath("terlambat")).toBe("/tasks/kepala-produksi/prioritas/terlambat");
    expect(detail).toMatch(/kp-kpi-detail-back/);
    expect(detail).toMatch(/Kembali/);
    expect(detail).toMatch(/detail_rows/);
  });

  test("desktop KPI strip stays one row of six until below 1200px", () => {
    const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
    expect(css).toMatch(/\.kp-kpi-strip\{[^}]*grid-template-columns:repeat\(6,/s);
    expect(css).toMatch(/@media \(max-width:1200px\)/);
    expect(css).not.toMatch(/@media \(max-width:1440px\)\{\s*\.kp-kpi-strip\{grid-template-columns:repeat\(3/);
  });

  test("KP table headers force dark background with white text (no white-on-white)", () => {
    const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
    expect(css).toMatch(/\.kp-priority-table(?:\.prod-table)? thead th\{[^}]*color:#fff/s);
    expect(css).toMatch(/\.kp-priority-table(?:\.prod-table)? thead th\{[^}]*background:var\(--ep-dark/s);
    expect(page).toMatch(/<th>Prioritas<\/th>/);
    expect(page).toMatch(/<th>Tanggal Kirim<\/th>/);
    expect(page).toMatch(/<th[^>]*>Total Order<\/th>/);
    expect(page).toMatch(/Sudah Diproduksi/);
    expect(page).toMatch(/Belum Dijadwalkan/);
    expect(detail).toMatch(/<th>Prioritas<\/th>/);
    expect(detail).toMatch(/Stock Saat Ini/);
    expect(detail).toMatch(/Sudah Diproduksi/);
  });

  test("HOLD blocks Jadwalkan on KP board and detail", () => {
    expect(page).toMatch(/schedule_eligible/);
    expect(page).toMatch(/authoritative_hold/);
    expect(detail).toMatch(/kp-kpi-jadwal-blocked/);
    expect(detail).toMatch(/HOLD/);
  });

  test("current stock formatting: positive, zero, unresolved dash", () => {
    expect(formatCurrentStock(3250)).toBe("3.250");
    expect(formatCurrentStock(0)).toBe("0");
    expect(formatCurrentStock(null)).toBe("—");
    expect(formatCurrentStock(undefined)).toBe("—");
    expect(formatCurrentStock("")).toBe("—");
    expect(formatCurrentStock(null)).not.toBe("0");
    expect(formatIdQty(2000)).toBe("2.000");
  });

  test("KPI card value and unique PO count helpers", () => {
    expect(formatKpiCardValue({ metric: "po_count", count: 12 })).toBe("12");
    expect(formatKpiCardValue({ metric: "qty_by_unit", by_unit: { pcs: 4000, box: 5 }, display: "4.000 pcs, 5 box" })).toBe("4.000 pcs, 5 box");
    expect(uniquePoCount([{ order_id: "a" }, { order_id: "a" }, { order_id: "b" }])).toBe(2);
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

  test("detail page has pagination and AppShell route (not modal)", () => {
    expect(detail).toMatch(/kp-kpi-pagination/);
    expect(detail).toMatch(/paginateRows/);
    expect(detail).not.toMatch(/Modal|Drawer|Popover/);
    expect(app).toMatch(/prioritas\/:kpi/);
  });
});
