import fs from "fs";
import path from "path";

describe("Division task UI", () => {
  const page = fs.readFileSync(path.join(__dirname, "DivisionTaskPage.jsx"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const packing = fs.readFileSync(path.join(__dirname, "../components/PackingPanel.jsx"), "utf8");
  const sj = fs.readFileSync(path.join(__dirname, "../components/SuratJalanPanel.jsx"), "utf8");

  test("sidebar Tugas per Divisi and all submenus", () => {
    expect(shell).toMatch(/Tugas per Divisi/);
    expect(shell).toMatch(/top-nav-tasks-\$\{id\}/);
    expect(shell).toMatch(/"screener", "helper", "operator", "packing", "surat_jalan", "loading", "terkirim"/);
  });

  test("routes exist per division", () => {
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
    expect(page).toMatch(/required_input/);
    expect(page).not.toMatch(/Packing Sebagian/);
    expect(packing).not.toMatch(/Packing Sebagian/);
    expect(packing).not.toMatch(/complete-packing-button/);
  });

  test("Lihat PO gated by can_view_po", () => {
    expect(page).toMatch(/canViewPo/);
    expect(page).toMatch(/Lihat PO/);
  });

  test("Surat Jalan remains upload-only", () => {
    expect(sj).not.toMatch(/sj-accurate-import/);
    expect(page).toMatch(/Upload File Surat Jalan|sj_status/);
  });
});
