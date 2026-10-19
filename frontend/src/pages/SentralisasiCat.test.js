import fs from "fs";
import path from "path";
import { NAV_KEYS, paintNavVisible, productionMenuItems } from "../lib/navAccess";

const pageSrc = fs.readFileSync(path.join(__dirname, "SentralisasiCat.jsx"), "utf8");
const cssSrc = fs.readFileSync(path.join(__dirname, "SentralisasiCat.css"), "utf8");
const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

function has(...keys) {
  const set = new Set(keys);
  return (k) => set.has(k);
}

describe("Sentralisasi Cat UX 9.0 contracts", () => {
  test("registers four tabs and local green stylesheet", () => {
    expect(pageSrc).toMatch(/id:\s*"ringkasan"/);
    expect(pageSrc).toMatch(/id:\s*"transaksi"/);
    expect(pageSrc).toMatch(/id:\s*"stok"/);
    expect(pageSrc).toMatch(/id:\s*"laporan"/);
    expect(pageSrc).toMatch(/import \"\.\/SentralisasiCat\.css\"/);
    expect(cssSrc).toMatch(/--paint-green:\s*#166534/);
    expect(cssSrc).toMatch(/width:\s*100%/);
    expect(cssSrc).toMatch(/background:\s*var\(--paint-green-strong\)/);
  });

  test("branch select reset only on modal open transition", () => {
    expect(pageSrc).toMatch(/wasOpen\.current/);
    expect(pageSrc).toMatch(/if \(open && !wasOpen\.current\)/);
    expect(pageSrc).toMatch(/api\.paintBranches\(\)/);
    expect(pageSrc).not.toMatch(/api\.branches\(\)/);
    expect(pageSrc).not.toMatch(/}, \[open, defaults\?\.branchId, hasPerm\]\)/);
  });

  test("employee selected meta is separated from name", () => {
    expect(pageSrc).toMatch(/className="paint-emp-meta"/);
    expect(pageSrc).toMatch(/function employeeMeta/);
    expect(pageSrc).toMatch(/\[row\?\.role_label \|\| row\?\.role, row\?\.division\]\.filter\(Boolean\)\.join\(" · "\)/);
  });

  test("uses Indonesian date input and WIB timestamps", () => {
    expect(pageSrc).toMatch(/placeholder="dd\/mm\/yyyy"/);
    expect(pageSrc).toMatch(/parseStatusPoDateInput/);
    expect(pageSrc).toMatch(/Asia\/Jakarta/);
    expect(pageSrc).not.toMatch(/type="date"/);
  });

  test("branch labels are human-readable", () => {
    expect(pageSrc).toMatch(/function branchLabel/);
    expect(pageSrc).toMatch(/branchLabel\(branches, row\.branch_id\)/);
  });

  test("report filters reuse GET contracts", () => {
    expect(pageSrc).toMatch(/employee_user_id:\s*reportEmployee/);
    expect(pageSrc).toMatch(/paint_id:\s*reportPaintId/);
    expect(pageSrc).toMatch(/data-testid=\{`paint-preset-\$\{id\}`\}/);
    expect(pageSrc).toMatch(/\["today", "Hari Ini"\]/);
    expect(pageSrc).toMatch(/paintUsageReport/);
  });

  test("toolbar uses combined period and reset", () => {
    expect(pageSrc).toMatch(/function PeriodRangeControl/);
    expect(pageSrc).toMatch(/paint-period-trigger/);
    expect(pageSrc).toMatch(/paint-filter-reset/);
    expect(pageSrc).toMatch(/const resetFilters = useCallback/);
    expect(pageSrc).toMatch(/paint-report-more-filters/);
  });

  test("primary actions use Eternal green class", () => {
    expect(pageSrc).toMatch(/paint-btn-primary/);
    expect(cssSrc).toMatch(/paint-btn-primary/);
  });

  test("route and menu still registered", () => {
    expect(appSrc).toMatch(/path="\/produksi\/sentralisasi-cat"/);
    expect(paintNavVisible(has("paint.report.view"))).toBe(true);
    const items = productionMenuItems(has(NAV_KEYS.paintSentralisasi));
    expect(items.some((i) => i.to === "/produksi/sentralisasi-cat")).toBe(true);
  });

  test("typing focus pattern retained for employee search", () => {
    expect(pageSrc).toMatch(/setQInput\(e\.target\.value\)/);
    expect(pageSrc).toMatch(/setTimeout\(\(\) => setQ\(qInput\.trim\(\)\), 250\)/);
    expect(pageSrc).not.toMatch(/key=\{qInput\}/);
  });

  test("compact layout contracts", () => {
    expect(cssSrc).toMatch(/--paint-row-h:\s*31px/);
    expect(cssSrc).toMatch(/min-height:\s*48px/);
    expect(pageSrc).toMatch(/paint-report-more-filters/);
    expect(pageSrc).toMatch(/paint-ellipsis/);
    expect(pageSrc).toMatch(/\+ Transaksi/);
  });

  test("search inputs use Eternal green focus colors", () => {
    expect(cssSrc).toMatch(/\.paint-search\s*\{[\s\S]*?border:\s*1px solid #D5E3DB/i);
    expect(cssSrc).toMatch(/\.paint-search:focus-within\s*\{[\s\S]*?border-color:\s*#166534/i);
    expect(cssSrc).toMatch(/box-shadow:\s*0 0 0 3px rgba\(22,\s*101,\s*52,\s*0\.12\)/);
    expect(cssSrc).toMatch(/\.paint-search-wrap:focus-within/);
    expect(cssSrc).toMatch(/color:\s*#94A3B8/);
  });

  test("advanced table wired for all four paint tables", () => {
    const advSrc = fs.readFileSync(path.join(__dirname, "../components/paint/PaintAdvancedTable.jsx"), "utf8");
    expect(pageSrc).toMatch(/PaintAdvancedTable/);
    expect(pageSrc).toMatch(/tableId="paint-recent"/);
    expect(pageSrc).toMatch(/tableId="paint-tx"/);
    expect(pageSrc).toMatch(/tableId="paint-stock"/);
    expect(pageSrc).toMatch(/tableId="paint-report"/);
    expect(pageSrc).toMatch(/encodeHeaderFilters/);
    expect(advSrc).toMatch(/paint-reset-cols-\$\{tableId\}/);
    expect(advSrc).toMatch(/normalizeReportColumns|mergePaintLayout/);
  });
});
