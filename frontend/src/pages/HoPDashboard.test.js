import fs from "fs";
import path from "path";

describe("Dashboard presentation", () => {
  const src = fs.readFileSync(path.join(__dirname, "../pages/HoPDashboard.jsx"), "utf8");

  test("cards navigate to Status PO views except Pengiriman Hari Ini", () => {
    expect(src).toMatch(/\/laporan\/status-po\?view=/);
    expect(src).toMatch(/view=\$\{view\}|work_today/);
    expect(src).toMatch(/\/produksi\/pengiriman-hari-ini/);
    expect(src).not.toMatch(/status-po\?view=\$\{view\}.*delivery_today|openStatusPo\("delivery_today"\)/);
  });

  test("no inline expanded list under dashboard", () => {
    expect(src).not.toMatch(/hop-filter-panel/);
    expect(src).not.toMatch(/hop-filter-list/);
  });

  test("progress per tahap and beban operator removed from UI", () => {
    expect(src).not.toMatch(/stage-breakdown-panel/);
    expect(src).not.toMatch(/operator-load-panel/);
    expect(src).not.toMatch(/Progres per Tahap/);
    expect(src).not.toMatch(/Beban Operator/);
  });

  test("pengerjaan and pengiriman are kpi cards", () => {
    expect(src).toMatch(/kpi-work-today/);
    expect(src).toMatch(/kpi-delivery-today/);
    expect(src).toMatch(/Pengerjaan Hari Ini/);
    expect(src).toMatch(/Pengiriman Hari Ini/);
  });
});
