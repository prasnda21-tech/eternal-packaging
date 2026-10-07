import fs from "fs";
import path from "path";

describe("Payroll Rekonsiliasi UI", () => {
  const page = fs.readFileSync(path.join(__dirname, "PayrollReconciliation.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "PayrollReconciliation.css"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");

  test("route and API wiring", () => {
    expect(app).toMatch(/\/hr-payroll\/rekonsiliasi/);
    expect(app).toMatch(/PayrollReconciliation/);
    expect(app).toMatch(/alsoAnyOf=\{\["payroll_payment\.view"\]\}/);
    expect(api).toMatch(/hrPayrollReconciliation:/);
    expect(api).toMatch(/hrPayrollReconciliationDetail:/);
    expect(nav).toMatch(/hrPayrollReconciliation/);
    expect(nav).toMatch(/\/hr-payroll\/rekonsiliasi/);
    expect(nav).toMatch(/Rekonsiliasi Payroll/);
  });

  test("table columns and filters", () => {
    expect(page).toMatch(/payroll-reconciliation-page/);
    expect(page).toMatch(/pr-table/);
    expect(page).toMatch(/Periode/);
    expect(page).toMatch(/Jenis/);
    expect(page).toMatch(/Total Bersih/);
    expect(page).toMatch(/Payroll/);
    expect(page).toMatch(/Jurnal/);
    expect(page).toMatch(/Payment/);
    expect(page).toMatch(/Bank/);
    expect(page).toMatch(/Rekonsiliasi/);
    expect(page).toMatch(/pr-filter-status/);
    expect(page).toMatch(/pr-filter-scope/);
  });

  test("status badges cover required labels", () => {
    [
      "Belum Final",
      "Final, Jurnal Belum Ada",
      "Belum Dibayar",
      "Menunggu Persetujuan",
      "Siap Diposting",
      "Sesuai",
      "Selesai Tanpa Pembayaran",
      "Nominal Tidak Sesuai",
      "Jurnal Bank Bermasalah",
    ].forEach((label) => expect(page).toMatch(new RegExp(label)));
    expect(page).toMatch(/pr-status-badge/);
    expect(page).toMatch(/pr-badge-ok/);
    expect(page).toMatch(/pr-badge-bad/);
  });

  test("detail drawer sections and rules", () => {
    expect(page).toMatch(/pr-detail-drawer/);
    expect(page).toMatch(/pr-section-payroll/);
    expect(page).toMatch(/pr-section-accrual/);
    expect(page).toMatch(/pr-section-payment/);
    expect(page).toMatch(/pr-section-bank/);
    expect(page).toMatch(/pr-section-rules/);
    expect(page).toMatch(/Payroll net = Utang Gaji|rule\.label/);
    expect(page).toMatch(/pr-open-detail/);
  });

  test("read-only actions and permission-gated deep links", () => {
    expect(page).toMatch(/pr-link-payroll/);
    expect(page).toMatch(/pr-link-payment/);
    expect(page).toMatch(/pr-link-mutasi/);
    expect(page).toMatch(/pr-link-gl/);
    expect(page).toMatch(/canMutasi/);
    expect(page).toMatch(/canGl/);
    expect(page).toMatch(/cash_bank\.view/);
    expect(page).toMatch(/general_ledger\.view/);
    expect(page).not.toMatch(/hrPayrollCreatePayment/);
    expect(page).not.toMatch(/hrPayrollApprovePayment/);
    expect(page).not.toMatch(/hrPayrollPostPayment/);
    expect(page).not.toMatch(/hrPayrollReversePayment/);
    expect(page).not.toMatch(/hrPayrollPostAccounting/);
    expect(page).toMatch(/pr-readonly-note/);
    expect(page).toMatch(/Tidak ada aksi Create \/ Approve \/ Post \/ Reverse/);
  });

  test("compact layout css avoids forced overflow", () => {
    expect(css).toMatch(/\.pr-page/);
    expect(css).toMatch(/overflow-x:\s*auto/);
    expect(css).toMatch(/max-width:\s*100%/);
    expect(css).toMatch(/min-width:\s*0/);
    expect(css).not.toMatch(/position:\s*fixed[^;]*;\s*width:\s*200vw/);
  });
});
