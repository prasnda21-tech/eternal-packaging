import fs from "fs";
import path from "path";

describe("Administrasi Karyawan UI", () => {
  const page = fs.readFileSync(path.join(__dirname, "EmployeeAdmin.jsx"), "utf8");
  const detail = fs.readFileSync(path.join(__dirname, "EmployeeAdminDetail.jsx"), "utf8");
  const settings = fs.readFileSync(path.join(__dirname, "../components/EmployeeAdminSettings.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const settingsPage = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");

  test("sidebar Administrasi Karyawan flyout", () => {
    expect(shell).toMatch(/Administrasi Karyawan/);
    expect(shell).toMatch(/top-nav-employee-admin/);
    expect(shell).toMatch(/sidebar-flyout-employee-admin/);
    expect(shell).toMatch(/employeeAdminNavVisible\(hasPerm\)/);
  });

  test("routes for mine create approve archive", () => {
    expect(app).toMatch(/\/administrasi-karyawan\/pengajuan-saya/);
    expect(app).toMatch(/\/administrasi-karyawan\/buat/);
    expect(app).toMatch(/\/administrasi-karyawan\/persetujuan/);
    expect(app).toMatch(/\/administrasi-karyawan\/arsip/);
    expect(page).toMatch(/ea-mine-page/);
    expect(page).toMatch(/ea-create-page/);
    expect(page).toMatch(/ea-approve-page/);
    expect(page).toMatch(/ea-archive-page/);
  });

  test("dynamic form and ineligible empty state", () => {
    expect(page).toMatch(/ea-dynamic-form/);
    expect(page).toMatch(/employeeAdminEligibleTypes/);
    expect(page).toMatch(/ea-no-eligible/);
    expect(page).toMatch(/ea-type-select/);
  });

  test("approval inbox actions and timeline", () => {
    expect(detail).toMatch(/ea-approval-timeline/);
    expect(detail).toMatch(/ea-approve/);
    expect(detail).toMatch(/ea-reject/);
    expect(detail).toMatch(/ea-decide-note/);
    expect(detail).toMatch(/employeeAdminDecide/);
  });

  test("settings tabs and builders live under Pengaturan", () => {
    expect(settingsPage).toMatch(/settings-tab-employee-admin/);
    expect(settingsPage).not.toMatch(/to="\/pengaturan-karyawan"/);
    expect(settings).toMatch(/ea-form-builder/);
    expect(settings).toMatch(/ea-eligibility-builder/);
    expect(settings).toMatch(/ea-approval-builder/);
    expect(settings).toMatch(/ea-document-settings/);
    expect(settings).toMatch(/ea-tab-type/);
    expect(settings).toMatch(/Cocok Salah Satu/);
  });
});
