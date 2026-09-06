import fs from "fs";
import path from "path";

describe("Neraca frontend contracts", () => {
  const src = fs.readFileSync(path.join(__dirname, "Neraca.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const gl = fs.readFileSync(path.join(__dirname, "BukuBesar.jsx"), "utf8");

  test("route, RBAC, and api client are wired", () => {
    expect(app).toMatch(/\/buku-besar\/neraca/);
    expect(app).toMatch(/Neraca/);
    expect(app).toMatch(/balance_sheet\.view/);
    expect(app).toMatch(/NAV_KEYS\.ledgerBalanceSheet/);
    expect(api).toMatch(/balanceSheet/);
    expect(api).toMatch(/\/ledger\/balance-sheet/);
  });

  test("as-of date filter and search are present", () => {
    expect(src).toMatch(/neraca-page/);
    expect(src).toMatch(/neraca-as-of/);
    expect(src).toMatch(/neraca-search/);
    expect(src).toMatch(/KbSearchField/);
    expect(src).toMatch(/neraca-date-required/);
    expect(src).toMatch(/if \(!asOfDate\)/);
    expect(src).toMatch(/as_of_date: asOfDate/);
    expect(src).toMatch(/q: q \|\| undefined/);
    expect(src).toMatch(/api\.balanceSheet/);
    expect(src).toMatch(/Tanggal Neraca/);
    expect(src).toMatch(/Cari akun\.\.\./);
  });

  test("asset, liability, and equity sections with official totals", () => {
    expect(src).toMatch(/ASET/);
    expect(src).toMatch(/KEWAJIBAN/);
    expect(src).toMatch(/EKUITAS/);
    expect(src).toMatch(/Total Aset/);
    expect(src).toMatch(/Total Kewajiban/);
    expect(src).toMatch(/Total Ekuitas/);
    expect(src).toMatch(/TOTAL KEWAJIBAN & EKUITAS/);
    expect(src).toMatch(/neraca-group-/);
    expect(src).toMatch(/neraca-row-\$\{row\.account_id\}/);
    expect(src).toMatch(/neraca-group-total-/);
    expect(src).toMatch(/summary\.total_assets/);
    expect(src).toMatch(/summary\.total_liabilities/);
    expect(src).toMatch(/summary\.total_equity/);
    expect(src).toMatch(/summary\.total_liabilities_and_equity/);
    expect(src).toMatch(/Kode/);
    expect(src).toMatch(/Nama Akun/);
    expect(src).toMatch(/Debit/);
    expect(src).toMatch(/Kredit/);
    expect(src).toMatch(/Jumlah/);
    expect(src).toMatch(/className="num/);
    expect(src).toMatch(/kb-money/);
  });

  test("DEPR negative presentation and signed values", () => {
    expect(src).not.toMatch(/Math\.abs/);
    expect(src).not.toMatch(/\.abs\(/);
    expect(src).toMatch(/Number\(n\) < 0/);
    expect(src).toMatch(/kb-pl-neg/);
    expect(src).toMatch(/row\.amount/);
  });

  test("Laba/Rugi Berjalan is a derived line without COA drill-down", () => {
    expect(src).toMatch(/Laba\/Rugi Berjalan/);
    expect(src).toMatch(/neraca-current-earnings/);
    expect(src).toMatch(/kb-bs-derived/);
    expect(src).toMatch(/currentEarnings\.amount/);
    expect(src).not.toMatch(/current_earnings\.account_id/);
    expect(src).not.toMatch(/openLedger\(currentEarnings/);
  });

  test("balanced status and imbalance difference stay visible", () => {
    expect(src).toMatch(/NERACA SEIMBANG/);
    expect(src).toMatch(/SELISIH NERACA/);
    expect(src).toMatch(/summary\.is_balanced/);
    expect(src).toMatch(/summary\.balance_difference/);
    expect(src).toMatch(/neraca-status/);
    expect(src).toMatch(/neraca-imbalance/);
  });

  test("search filters rows only; totals stay from API summary", () => {
    expect(src).toMatch(/group\.accounts/);
    expect(src).toMatch(/summary\.total_assets/);
    expect(src).toMatch(/summary\.total_liabilities/);
    expect(src).toMatch(/summary\.total_equity/);
    expect(src).toMatch(/summary\.total_liabilities_and_equity/);
    expect(src).not.toMatch(/reduce\(/);
  });

  test("Buku Besar drill-down uses exact account_id and as-of end_date", () => {
    expect(src).toMatch(/\/buku-besar\/buku-besar/);
    expect(src).toMatch(/account_id/);
    expect(src).toMatch(/end_date/);
    expect(src).toMatch(/Lihat Buku Besar/);
    expect(src).toMatch(/drilldown/);
    expect(src).not.toMatch(/start_date/);
    expect(gl).toMatch(/useSearchParams/);
    expect(gl).toMatch(/account_id/);
    expect(gl).toMatch(/end_date/);
  });

  test("nav and flyout include Neraca child", () => {
    expect(nav).toMatch(/ledgerBalanceSheet/);
    expect(nav).toMatch(/nav\.ledger\.balance_sheet/);
    expect(nav).toMatch(/\/buku-besar\/neraca/);
    expect(nav).toMatch(/balance_sheet\.view/);
    expect(shell).toMatch(/top-nav-buku-besar/);
    expect(shell).toMatch(/NAV_KEYS\.ledgerBalanceSheet/);
    expect(shell).toMatch(/useGlobalWorkspaceTabs/);
    expect(shell).toMatch(/<WorkspaceTabs/);
  });

  test("mobile-safe compact filter CSS", () => {
    expect(css).toMatch(/kb-bs-filters/);
    expect(css).toMatch(/kb-bs-asof/);
    expect(css).toMatch(/kb-tb-search/);
    expect(css).toMatch(/kb-pl-section/);
    expect(css).toMatch(/kb-bs-derived/);
    expect(css).toMatch(/kb-bs-status/);
    expect(css).toMatch(/overflow-x:\s*auto/);
    expect(css).toMatch(/@media \(max-width:560px\)/);
  });
});
