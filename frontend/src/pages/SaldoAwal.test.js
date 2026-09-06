import fs from "fs";
import path from "path";

describe("Saldo Awal frontend contracts", () => {
  test("App route and page wiring", () => {
    const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
    expect(app).toMatch(/\/buku-besar\/saldo-awal/);
    expect(app).toMatch(/SaldoAwal/);
    expect(app).toMatch(/opening_balance\.view/);
  });

  test("api client exposes opening balance endpoints", () => {
    const src = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
    expect(src).toMatch(/openingBalances/);
    expect(src).toMatch(/createOpeningBalance/);
    expect(src).toMatch(/postOpeningBalance/);
    expect(src).toMatch(/reverseOpeningBalance/);
  });

  test("page has list/form/totals/status/actions and BANK-only absent", () => {
    const src = fs.readFileSync(path.join(__dirname, "SaldoAwal.jsx"), "utf8");
    expect(src).toMatch(/saldo-awal-page/);
    expect(src).toMatch(/saldo-awal-table/);
    expect(src).toMatch(/saldo-awal-form/);
    expect(src).toMatch(/saldo-awal-add-line/);
    expect(src).toMatch(/saldo-awal-totals/);
    expect(src).toMatch(/saldo-awal-save-draft/);
    expect(src).toMatch(/saldo-awal-post/);
    expect(src).toMatch(/!totals\.balanced/);
    expect(src).toMatch(/statusFilter/);
    expect(src).toMatch(/Tanggal Cut-off/);
    expect(src).toMatch(/opening_balance\.manage/);
    expect(src).toMatch(/opening_balance\.post/);
    expect(src).toMatch(/opening_balance\.reverse/);
    expect(src).toMatch(/ledgerAccounts/);
    expect(src).not.toMatch(/account_type === ["']BANK["']/);
    expect(src).not.toMatch(/type === ["']BANK["']/);
  });

  test("AppShell Buku Besar flyout still present", () => {
    const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
    expect(shell).toMatch(/top-nav-buku-besar/);
    expect(shell).toMatch(/ledgerOpen && ledgerItems/);
  });
});
