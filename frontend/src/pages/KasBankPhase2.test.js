import fs from "fs";
import path from "path";

describe("Kas & Bank Phase 2 frontend contracts", () => {
  test("App routes include Mutasi + Rekonsiliasi", () => {
    const src = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
    expect(src).toMatch(/\/kas-bank\/mutasi/);
    expect(src).toMatch(/\/kas-bank\/rekonsiliasi/);
    expect(src).toMatch(/MutasiKasBank/);
    expect(src).toMatch(/RekonsiliasiKasBank/);
    expect(src).toMatch(/cash_bank\.reconcile/);
  });

  test("Mutasi page has filters, summary, accounting table", () => {
    const src = fs.readFileSync(path.join(__dirname, "MutasiKasBank.jsx"), "utf8");
    expect(src).toMatch(/kas-bank-mutasi-page/);
    expect(src).toMatch(/kas-bank-mutasi-toolbar/);
    expect(src).toMatch(/kb-mutasi-filters/);
    expect(src).toMatch(/KbSearchField/);
    expect(src).toMatch(/kas-bank-mutasi-summary/);
    expect(src).toMatch(/Saldo Awal/);
    expect(src).toMatch(/Total Debit/);
    expect(src).toMatch(/Total Kredit/);
    expect(src).toMatch(/Saldo Akhir/);
    expect(src).toMatch(/Saldo Berjalan/);
    expect(src).toMatch(/className="num/);
    expect(src).toMatch(/kb-money/);
    expect(src).toMatch(/source_path/);
    expect(src).toMatch(/cashBankMutasi/);
  });

  test("Rekonsiliasi page draft/finalize match UI", () => {
    const src = fs.readFileSync(path.join(__dirname, "RekonsiliasiKasBank.jsx"), "utf8");
    expect(src).toMatch(/kas-bank-rekonsiliasi-page/);
    expect(src).toMatch(/cash_bank\.reconcile/);
    expect(src).toMatch(/kb-recon-toolbar/);
    expect(src).toMatch(/KbSearchField/);
    expect(src).toMatch(/Saldo Buku Eternal/);
    expect(src).toMatch(/Saldo Rekening Koran/);
    expect(src).toMatch(/Selisih/);
    expect(src).toMatch(/finalizeCashBankReconciliation/);
    expect(src).toMatch(/journal_line_ids/);
    expect(src).toMatch(/kb-row-matched/);
    expect(src).toMatch(/Finalisasi/);
    expect(src).toMatch(/Simpan Draft/);
    expect(src).toMatch(/Hapus Draft/);
    expect(src).toMatch(/kb-workspace-panel/);
    expect(src).toMatch(/KbSection|KbField/);
  });

  test("api client exposes mutasi + reconciliation endpoints", () => {
    const src = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
    expect(src).toMatch(/cashBankMutasi/);
    expect(src).toMatch(/cashBankReconciliations/);
    expect(src).toMatch(/finalizeCashBankReconciliation/);
    expect(src).toMatch(/setCashBankReconciliationMatches/);
  });

  test("CSS summary strip + shared search field + compact filters", () => {
    const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
    expect(css).toMatch(/kb-summary-strip/);
    expect(css).toMatch(/kb-summary-card/);
    expect(css).toMatch(/kb-row-matched/);
    expect(css).toMatch(/kb-search-field/);
    expect(css).toMatch(/kb-mutasi-filters/);
    expect(css).toMatch(/kb-recon-toolbar/);
  });

  test("KbSearchField keeps Lucide icon inside input", () => {
    const src = fs.readFileSync(path.join(__dirname, "../components/CashBankFormPrimitives.jsx"), "utf8");
    expect(src).toMatch(/export function KbSearchField/);
    expect(src).toMatch(/kb-search-field/);
    expect(src).toMatch(/kb-search-field-icon/);
    expect(src).toMatch(/from "lucide-react"/);
    expect(src).toMatch(/Search/);
  });
});
