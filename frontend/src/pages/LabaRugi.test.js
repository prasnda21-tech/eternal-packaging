import fs from "fs";
import path from "path";

describe("Laba/Rugi frontend contracts", () => {
  const src = fs.readFileSync(path.join(__dirname, "LabaRugi.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const gl = fs.readFileSync(path.join(__dirname, "BukuBesar.jsx"), "utf8");

  test("route, RBAC, and api client are wired", () => {
    expect(app).toMatch(/\/buku-besar\/laba-rugi/);
    expect(app).toMatch(/LabaRugi/);
    expect(app).toMatch(/profit_loss\.view/);
    expect(app).toMatch(/NAV_KEYS\.ledgerProfitLoss/);
    expect(api).toMatch(/profitLoss/);
    expect(api).toMatch(/\/ledger\/profit-loss/);
  });

  test("date filters and search are present", () => {
    expect(src).toMatch(/laba-rugi-page/);
    expect(src).toMatch(/laba-rugi-start/);
    expect(src).toMatch(/laba-rugi-end/);
    expect(src).toMatch(/laba-rugi-search/);
    expect(src).toMatch(/KbSearchField/);
    expect(src).toMatch(/laba-rugi-dates-required/);
    expect(src).toMatch(/if \(!startDate \|\| !endDate\)/);
    expect(src).toMatch(/start_date: startDate/);
    expect(src).toMatch(/end_date: endDate/);
    expect(src).toMatch(/q: q \|\| undefined/);
    expect(src).toMatch(/api\.profitLoss/);
  });

  test("group sections, account rows, and official totals", () => {
    expect(src).toMatch(/PENDAPATAN/);
    expect(src).toMatch(/BEBAN POKOK PENJUALAN/);
    expect(src).toMatch(/Total Pendapatan/);
    expect(src).toMatch(/Total HPP/);
    expect(src).toMatch(/LABA KOTOR/);
    expect(src).toMatch(/BEBAN/);
    expect(src).toMatch(/Total Beban/);
    expect(src).toMatch(/LABA OPERASIONAL/);
    expect(src).toMatch(/PENDAPATAN LAIN-LAIN/);
    expect(src).toMatch(/Total Pendapatan Lain/);
    expect(src).toMatch(/BEBAN LAIN-LAIN/);
    expect(src).toMatch(/Total Beban Lain/);
    expect(src).toMatch(/LABA BERSIH/);
    expect(src).toMatch(/laba-rugi-group-/);
    expect(src).toMatch(/laba-rugi-row-\$\{row\.account_id\}/);
    expect(src).toMatch(/laba-rugi-group-total-/);
    expect(src).toMatch(/laba-rugi-\$\{key\}/);
    expect(src).toMatch(/gross-profit/);
    expect(src).toMatch(/operating-profit/);
    expect(src).toMatch(/net-profit/);
    expect(src).toMatch(/totals\.gross_profit/);
    expect(src).toMatch(/totals\.operating_profit/);
    expect(src).toMatch(/totals\.net_profit/);
    expect(src).toMatch(/Kode/);
    expect(src).toMatch(/Nama Akun/);
    expect(src).toMatch(/Debit/);
    expect(src).toMatch(/Kredit/);
    expect(src).toMatch(/Jumlah/);
    expect(src).toMatch(/className="num/);
    expect(src).toMatch(/kb-money/);
  });

  test("negative values stay signed", () => {
    expect(src).not.toMatch(/Math\.abs/);
    expect(src).not.toMatch(/\.abs\(/);
    expect(src).toMatch(/Number\(n\) < 0/);
    expect(src).toMatch(/kb-pl-neg/);
  });

  test("search filters rows only; totals stay from API totals", () => {
    expect(src).toMatch(/group\.items/);
    expect(src).toMatch(/group\.total/);
    expect(src).toMatch(/totals\.gross_profit/);
    expect(src).toMatch(/totals\.operating_profit/);
    expect(src).toMatch(/totals\.net_profit/);
    expect(src).not.toMatch(/reduce\(/);
  });

  test("Buku Besar drill-down uses exact account and dates", () => {
    expect(src).toMatch(/\/buku-besar\/buku-besar/);
    expect(src).toMatch(/account_id/);
    expect(src).toMatch(/start_date/);
    expect(src).toMatch(/end_date/);
    expect(src).toMatch(/Lihat Buku Besar/);
    expect(src).toMatch(/drilldown/);
    expect(gl).toMatch(/useSearchParams/);
    expect(gl).toMatch(/account_id/);
    expect(gl).toMatch(/start_date/);
    expect(gl).toMatch(/end_date/);
  });

  test("nav and flyout include Laba/Rugi child", () => {
    expect(nav).toMatch(/ledgerProfitLoss/);
    expect(nav).toMatch(/nav\.ledger\.profit_loss/);
    expect(nav).toMatch(/\/buku-besar\/laba-rugi/);
    expect(nav).toMatch(/profit_loss\.view/);
    expect(shell).toMatch(/top-nav-buku-besar/);
    expect(shell).toMatch(/NAV_KEYS\.ledgerProfitLoss/);
    expect(shell).toMatch(/CashBankWorkspaceTabs/);
  });

  test("mobile-safe compact filter CSS", () => {
    expect(css).toMatch(/kb-tb-filters/);
    expect(css).toMatch(/kb-tb-start/);
    expect(css).toMatch(/kb-tb-end/);
    expect(css).toMatch(/kb-tb-search/);
    expect(css).toMatch(/kb-pl-section/);
    expect(css).toMatch(/kb-pl-subtotal/);
    expect(css).toMatch(/kb-pl-net/);
    expect(css).toMatch(/overflow-x:\s*hidden/);
    expect(css).toMatch(/@media \(max-width:560px\)/);
  });
});
