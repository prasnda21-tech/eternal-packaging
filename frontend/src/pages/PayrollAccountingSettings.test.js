import fs from "fs";
import path from "path";

describe("Payroll Accounting Settings UI", () => {
  const pageSrc = fs.readFileSync(path.join(__dirname, "PayrollAccountingSettings.jsx"), "utf8");
  const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
  const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const payrollSrc = fs.readFileSync(path.join(__dirname, "HrPayroll.jsx"), "utf8");

  test("settings card maps exact COA and treatments without posting", () => {
    expect(pageSrc).toMatch(/payroll-accounting-settings/);
    expect(pageSrc).toMatch(/payroll-accounting-payable/);
    expect(pageSrc).toMatch(/payroll-accounting-mapping-table/);
    expect(pageSrc).toMatch(/Beban \/ Cost/);
    expect(pageSrc).toMatch(/Pengurang Beban/);
    expect(pageSrc).toMatch(/Kewajiban/);
    expect(pageSrc).toMatch(/Pengurang Aset/);
    expect(pageSrc).toMatch(/source_key/);
    expect(pageSrc).not.toMatch(/Posted Accounting/);
    expect(pageSrc).not.toMatch(/post-accounting/);
  });

  test("Settings nav, API, and payroll preview are wired", () => {
    expect(settingsSrc).toMatch(/settings-tab-payroll-accounting/);
    expect(settingsSrc).toMatch(/PayrollAccountingSettings/);
    expect(apiSrc).toMatch(/payrollAccountingSettings:/);
    expect(apiSrc).toMatch(/updatePayrollAccountingSettings:/);
    expect(apiSrc).toMatch(/hrPayrollAccountingReadiness:/);
    expect(payrollSrc).toMatch(/hr-payroll-accounting-ready/);
    expect(payrollSrc).toMatch(/PREVIEW — BELUM MEMBUAT JURNAL/);
    expect(payrollSrc).toMatch(/<th>Sumber<\/th>/);
    expect(payrollSrc).toMatch(/<th>Treatment<\/th>/);
    expect(payrollSrc).toMatch(/previewAccountLabel/);
    expect(payrollSrc).toMatch(/acc\.code/);
    expect(payrollSrc).toMatch(/acc\.name/);
    expect(payrollSrc).toMatch(/Utang Gaji \(net\)/);
    expect(payrollSrc).toMatch(/Beban \/ Cost/);
    expect(payrollSrc).toMatch(/Pengurang Beban/);
    expect(payrollSrc).toMatch(/Kewajiban/);
    expect(payrollSrc).toMatch(/Pengurang Aset/);
    expect(payrollSrc).not.toMatch(/Posted Accounting/);
  });
});
