import fs from "fs";
import path from "path";

describe("Periode Akuntansi page", () => {
  const src = fs.readFileSync(path.join(__dirname, "PeriodeAkuntansi.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

  test("page lists month status and close/reopen actions", () => {
    expect(src).toMatch(/Periode Akuntansi/);
    expect(src).toMatch(/periode-akuntansi-table/);
    expect(src).toMatch(/accounting\.period\.close/);
    expect(src).toMatch(/accounting\.period\.reopen/);
    expect(src).toMatch(/periode-close-/);
    expect(src).toMatch(/periode-reopen-/);
    expect(src).toMatch(/CLOSED/);
    expect(src).toMatch(/OPEN/);
  });

  test("close and reopen use reason modals", () => {
    expect(src).toMatch(/Tutup Periode \$\{dialog\.period_key\}/);
    expect(src).toMatch(/Buka Kembali Periode \$\{dialog\.period_key\}/);
    expect(src).toMatch(/Setelah periode ditutup, jurnal baru dan reversal pada bulan ini akan diblokir sampai periode dibuka kembali\./);
    expect(src).toMatch(/Membuka kembali periode memungkinkan posting dan reversal pada bulan tersebut\./);
    expect(src).toMatch(/Alasan penutupan/);
    expect(src).toMatch(/Alasan membuka kembali/);
    expect(src).toMatch(/trimmedReason\.length >= 3/);
    expect(src).toMatch(/reason:\s*trimmedReason/);
    expect(src).not.toMatch(/closeAccountingPeriod\([^,]+,\s*\{\s*\}/);
    expect(src).not.toMatch(/reopenAccountingPeriod\([^,]+,\s*\{\s*\}/);
  });

  test("API and route are wired", () => {
    expect(api).toMatch(/accountingPeriods/);
    expect(api).toMatch(/closeAccountingPeriod/);
    expect(api).toMatch(/reopenAccountingPeriod/);
    expect(app).toMatch(/\/buku-besar\/periode-akuntansi/);
    expect(app).toMatch(/PeriodeAkuntansi/);
  });
});
