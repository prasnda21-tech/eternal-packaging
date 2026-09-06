import fs from "fs";
import path from "path";

describe("Buku Besar frontend contracts", () => {
  const src = fs.readFileSync(path.join(__dirname, "BukuBesar.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");

  test("route, RBAC, and api client are wired", () => {
    expect(app).toMatch(/\/buku-besar\/buku-besar/);
    expect(app).toMatch(/BukuBesar/);
    expect(app).toMatch(/general_ledger\.view/);
    expect(app).toMatch(/NAV_KEYS\.ledgerGeneralLedger/);
    expect(api).toMatch(/generalLedger/);
    expect(api).toMatch(/\/ledger\/general-ledger/);
  });

  test("account is required and filters are present", () => {
    expect(src).toMatch(/buku-besar-page/);
    expect(src).toMatch(/buku-besar-account/);
    expect(src).toMatch(/buku-besar-source/);
    expect(src).toMatch(/buku-besar-start/);
    expect(src).toMatch(/buku-besar-end/);
    expect(src).toMatch(/buku-besar-search/);
    expect(src).toMatch(/KbSearchField/);
    expect(src).toMatch(/buku-besar-account-required/);
    expect(src).toMatch(/Pilih Akun Perkiraan/);
    expect(src).toMatch(/if \(!accountId \|\| !startDate \|\| !endDate\)/);
    expect(src).not.toMatch(/account_type === ["']BANK["']/);
    expect(src).not.toMatch(/type === ["']BANK["']/);
    expect(src).toMatch(/ledgerAccounts/);
    expect(src).toMatch(/account_type/);
  });

  test("summary, table alignment, running balance, empty state", () => {
    expect(src).toMatch(/buku-besar-summary/);
    expect(src).toMatch(/Saldo Awal/);
    expect(src).toMatch(/Total Debit/);
    expect(src).toMatch(/Total Kredit/);
    expect(src).toMatch(/Saldo Akhir/);
    expect(src).toMatch(/Saldo Berjalan/);
    expect(src).toMatch(/className="num/);
    expect(src).toMatch(/kb-money/);
    expect(src).toMatch(/running_balance/);
    expect(src).toMatch(/buku-besar-empty/);
    expect(src).toMatch(/Tidak ada mutasi pada periode ini/);
    expect(src).toMatch(/source_path/);
    expect(src).toMatch(/source_id/);
  });

  test("search and source filters use API params without local accounting rewrite", () => {
    expect(src).toMatch(/source_type: sourceType/);
    expect(src).toMatch(/q: q \|\| undefined/);
    expect(src).toMatch(/api\.generalLedger/);
  });

  test("nav and flyout include Buku Besar child", () => {
    expect(nav).toMatch(/ledgerGeneralLedger/);
    expect(nav).toMatch(/nav\.ledger\.general_ledger/);
    expect(nav).toMatch(/\/buku-besar\/buku-besar/);
    expect(nav).toMatch(/general_ledger\.view/);
    expect(shell).toMatch(/top-nav-buku-besar/);
    expect(shell).toMatch(/ledgerOpen && ledgerItems/);
    expect(shell).toMatch(/NAV_KEYS\.ledgerGeneralLedger/);
    expect(shell).toMatch(/CashBankWorkspaceTabs/);
  });

  test("mobile-safe compact filter CSS", () => {
    expect(css).toMatch(/kb-gl-filters/);
    expect(css).toMatch(/kb-gl-account/);
    expect(css).toMatch(/kb-gl-source/);
    expect(css).toMatch(/kb-gl-start/);
    expect(css).toMatch(/kb-gl-end/);
    expect(css).toMatch(/kb-gl-search/);
    expect(css).toMatch(/overflow-x:\s*hidden/);
    expect(css).toMatch(/@media \(max-width:560px\)/);
    expect(css).toMatch(/kb-summary-strip/);
  });
});
