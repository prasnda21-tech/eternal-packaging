import fs from "fs";
import path from "path";

describe("Jurnal Umum frontend contracts", () => {
  test("App route and page wiring", () => {
    const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
    expect(app).toMatch(/\/buku-besar\/jurnal-umum/);
    expect(app).toMatch(/JurnalUmum/);
    expect(app).toMatch(/general_journal\.view/);
  });

  test("api client exposes general journal endpoints", () => {
    const src = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
    expect(src).toMatch(/generalJournals/);
    expect(src).toMatch(/createGeneralJournal/);
    expect(src).toMatch(/postGeneralJournal/);
    expect(src).toMatch(/reverseGeneralJournal/);
  });

  test("page has list/form/totals/status/actions and BANK-only absent", () => {
    const src = fs.readFileSync(path.join(__dirname, "JurnalUmum.jsx"), "utf8");
    expect(src).toMatch(/jurnal-umum-page/);
    expect(src).toMatch(/jurnal-umum-table/);
    expect(src).toMatch(/jurnal-umum-form/);
    expect(src).toMatch(/jurnal-umum-add-line/);
    expect(src).toMatch(/jurnal-umum-totals/);
    expect(src).toMatch(/jurnal-umum-save-draft/);
    expect(src).toMatch(/jurnal-umum-post/);
    expect(src).toMatch(/!totals\.balanced/);
    expect(src).toMatch(/statusFilter/);
    expect(src).toMatch(/general_journal\.manage/);
    expect(src).toMatch(/general_journal\.post/);
    expect(src).toMatch(/general_journal\.reverse/);
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
