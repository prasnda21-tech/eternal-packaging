import { hrPayrollMenuItems, hrPayrollNavVisible, NAV_KEYS, NAV_MENU_FALLBACK } from "./navAccess";

const has = (...keys) => (key) => keys.includes(key);

describe("HR & Payroll nav", () => {
  test("catalog contains parent and children including reconciliation", () => {
    const labels = NAV_MENU_FALLBACK
      .filter((item) => item.key === NAV_KEYS.hrPayroll || item.parent === NAV_KEYS.hrPayroll)
      .map((item) => item.label);
    expect(labels).toEqual([
      "HR & Payroll", "Daftar Karyawan", "Data Kepegawaian", "Penggajian",
      "Slip Gaji", "Komponen Gaji", "Riwayat Payroll", "Rekonsiliasi Payroll",
    ]);
  });

  test("functional employee permission exposes employee pages", () => {
    const visible = hrPayrollNavVisible(has("hr.employee.view"));
    expect(visible.parent).toBe(true);
    expect(visible.employees).toBe(true);
    expect(visible.employment).toBe(true);
    expect(hrPayrollMenuItems(has("hr.employee.view")).map((item) => item.label))
      .toEqual(["Daftar Karyawan", "Data Kepegawaian"]);
  });

  test("payroll permissions expose matching pages", () => {
    expect(hrPayrollNavVisible(has("payroll.calculate")).payroll).toBe(true);
    expect(hrPayrollNavVisible(has("payroll.payslip.view_own")).payslips).toBe(true);
    expect(hrPayrollNavVisible(has("payroll.settings")).components).toBe(true);
    expect(hrPayrollNavVisible(has("payroll.payslip.view_all")).history).toBe(true);
    expect(hrPayrollNavVisible(has("payroll.view", "payroll_payment.view")).reconciliation).toBe(true);
    expect(hrPayrollNavVisible(has("payroll.view")).reconciliation).toBe(false);
  });

  test("hidden without grants", () => {
    expect(hrPayrollNavVisible(has()).parent).toBe(false);
    expect(hrPayrollMenuItems(has())).toEqual([]);
  });
});
