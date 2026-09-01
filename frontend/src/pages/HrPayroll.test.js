import fs from "fs";
import path from "path";

describe("HR & Payroll UI source", () => {
  const page = fs.readFileSync(path.join(__dirname, "HrPayroll.jsx"), "utf8");
  const settings = fs.readFileSync(path.join(__dirname, "../components/HrPayrollSettings.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");

  test("contains all six route modes", () => {
    ["hr-employees-page", "hr-employment-page", "hr-payroll-page", "hr-payslips-page", "hr-components-page", "hr-history-page"].forEach((id) => expect(page).toMatch(new RegExp(id)));
    expect(app).toMatch(/\/hr-payroll\/daftar-karyawan/);
    expect(app).toMatch(/\/hr-payroll\/data-kepegawaian/);
    expect(app).toMatch(/\/hr-payroll\/penggajian/);
    expect(app).toMatch(/\/hr-payroll\/slip-gaji/);
    expect(app).toMatch(/\/hr-payroll\/komponen-gaji/);
    expect(app).toMatch(/\/hr-payroll\/riwayat/);
  });

  test("payroll workflow controls and breakdown are present", () => {
    ["hr-calc-btn", "hr-transition-review", "hr-transition-approval", "hr-transition-final", "hr-reopen-btn", "hr-breakdown", "hr-kasbon-row"].forEach((id) => expect(page).toMatch(new RegExp(id)));
    expect(page).toMatch(/hasPerm\("payroll\.reopen"\)/);
    expect(page).toMatch(/canTransition\(selected\.status, target\)/);
  });

  test("settings and sidebar integrations are present", () => {
    expect(settings).toMatch(/settings-hr-payroll/);
    expect(settings).toMatch(/Workflow info/);
    expect(settings).toMatch(/hrPayrollSettings/);
    expect(settings).toMatch(/hrPayrollComponents/);
    expect(shell).toMatch(/sidebar-flyout-hr-payroll/);
  });
});
