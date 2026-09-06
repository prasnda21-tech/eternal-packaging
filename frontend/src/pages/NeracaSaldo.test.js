import fs from "fs";
import path from "path";

describe("Neraca Saldo frontend contracts", () => {
  const src = fs.readFileSync(path.join(__dirname, "NeracaSaldo.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const gl = fs.readFileSync(path.join(__dirname, "BukuBesar.jsx"), "utf8");

  test("route, RBAC, and api client are wired", () => {
    expect(app).toMatch(/\/buku-besar\/neraca-saldo/);
    expect(app).toMatch(/NeracaSaldo/);
    expect(app).toMatch(/trial_balance\.view/);
    expect(app).toMatch(/NAV_KEYS\.ledgerTrialBalance/);
    expect(api).toMatch(/trialBalance/);
    expect(api).toMatch(/\/ledger\/trial-balance/);
  });

  test("date filters and search are present", () => {
    expect(src).toMatch(/neraca-saldo-page/);
    expect(src).toMatch(/neraca-saldo-start/);
    expect(src).toMatch(/neraca-saldo-end/);
    expect(src).toMatch(/neraca-saldo-search/);
    expect(src).toMatch(/KbSearchField/);
    expect(src).toMatch(/neraca-saldo-dates-required/);
    expect(src).toMatch(/if \(!startDate \|\| !endDate\)/);
    expect(src).toMatch(/start_date: startDate/);
    expect(src).toMatch(/end_date: endDate/);
    expect(src).toMatch(/q: q \|\| undefined/);
    expect(src).toMatch(/api\.trialBalance/);
  });

  test("summary equality display and table alignment", () => {
    expect(src).toMatch(/neraca-saldo-summary/);
    expect(src).toMatch(/Saldo Awal/);
    expect(src).toMatch(/Mutasi/);
    expect(src).toMatch(/Saldo Akhir/);
    expect(src).toMatch(/Selisih/);
    expect(src).toMatch(/opening_diff/);
    expect(src).toMatch(/period_diff/);
    expect(src).toMatch(/ending_diff/);
    expect(src).toMatch(/Saldo Awal Debit/);
    expect(src).toMatch(/Saldo Awal Kredit/);
    expect(src).toMatch(/Mutasi Debit/);
    expect(src).toMatch(/Mutasi Kredit/);
    expect(src).toMatch(/Saldo Akhir Debit/);
    expect(src).toMatch(/Saldo Akhir Kredit/);
    expect(src).toMatch(/className="num/);
    expect(src).toMatch(/kb-money/);
    expect(src).toMatch(/neraca-saldo-empty/);
    expect(src).toMatch(/Tidak ada akun untuk ditampilkan/);
  });

  test("Buku Besar linkage uses existing route and exact params", () => {
    expect(src).toMatch(/\/buku-besar\/buku-besar/);
    expect(src).toMatch(/account_id/);
    expect(src).toMatch(/start_date/);
    expect(src).toMatch(/end_date/);
    expect(src).toMatch(/Lihat Buku Besar/);
    expect(gl).toMatch(/useSearchParams/);
    expect(gl).toMatch(/account_id/);
    expect(gl).toMatch(/start_date/);
    expect(gl).toMatch(/end_date/);
  });

  test("nav and flyout include Neraca Saldo child", () => {
    expect(nav).toMatch(/ledgerTrialBalance/);
    expect(nav).toMatch(/nav\.ledger\.trial_balance/);
    expect(nav).toMatch(/\/buku-besar\/neraca-saldo/);
    expect(nav).toMatch(/trial_balance\.view/);
    expect(shell).toMatch(/top-nav-buku-besar/);
    expect(shell).toMatch(/NAV_KEYS\.ledgerTrialBalance/);
    expect(shell).toMatch(/useGlobalWorkspaceTabs/);
    expect(shell).toMatch(/<WorkspaceTabs/);
  });

  test("mobile-safe compact filter CSS", () => {
    expect(css).toMatch(/kb-tb-filters/);
    expect(css).toMatch(/kb-tb-start/);
    expect(css).toMatch(/kb-tb-end/);
    expect(css).toMatch(/kb-tb-search/);
    expect(css).toMatch(/kb-tb-summary/);
    expect(css).toMatch(/overflow-x:\s*hidden/);
    expect(css).toMatch(/@media \(max-width:560px\)/);
  });
});
