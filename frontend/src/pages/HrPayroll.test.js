import fs from "fs";
import path from "path";

describe("HR & Payroll UI source", () => {
  const page = fs.readFileSync(path.join(__dirname, "HrPayroll.jsx"), "utf8");
  const settings = fs.readFileSync(path.join(__dirname, "../components/HrPayrollSettings.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");

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

  test("daftar karyawan shows canonical settings fields and keeps nav stable", () => {
    expect(page).toMatch(/role_label \|\| row\.role/);
    expect(page).toMatch(/hr-employees-table/);
    expect(page).toMatch(/hr-employee-row/);
    expect(page).toMatch(/data-user-id/);
    expect(page).toMatch(/data-has-hr-profile/);
    expect(page).toMatch(/directory_key/);
    expect(page).toMatch(/canEditHr/);
    expect(page).toMatch(/has_hr_profile === false/);
    expect(page).toMatch(/linkedFromSettings/);
    expect(page).toMatch(/hr-employee-settings-authority/);
    expect(page).toMatch(/Settings → Karyawan/);
    expect(nav).toMatch(/nav\.hr_payroll\.employees/);
    expect(nav).toMatch(/Daftar Karyawan/);
    expect(shell).toMatch(/sidebar-flyout-hr-payroll/);
    expect(page).not.toMatch(/nav\.hr_payroll\.employees.*checkbox/);
  });

  test("virtual settings rows do not mutate HR with fake employee id", () => {
    expect(page).toMatch(/const canEditHr = \(row\) => Boolean\(row\?\.has_hr_profile !== false && row\?\.id\)/);
    expect(page).toMatch(/const rowKey = \(row\) => row\?\.directory_key \|\| row\?\.id \|\| row\?\.user_id/);
    expect(page).toMatch(/isVirtualDirectory/);
    expect(page).toMatch(/Profil HR belum ada/);
    expect(page).toMatch(/hr-virtual-directory-note/);
    expect(page).toMatch(/hr-directory-no-profile/);
    expect(page).toMatch(/Belum ada profil HR/);
    expect(page).toMatch(/canEditHr\(row\) && <button className="ghost sm" onClick=\{\(\) => setEditing\(row\)\}>Edit<\/button>/);
    expect(page).toMatch(/data-employee-id=\{row\.id \|\| ""\}/);
    expect(page).toMatch(/hrPayrollUpdateEmployee\(initial\.id/);
    expect(page).not.toMatch(/hrPayrollUpdateEmployee\(initial\.user_id/);
    expect(page).not.toMatch(/hrPayrollDeleteEmployee/);
    expect(page).not.toMatch(/hrPayrollEmployee\(/);
    expect(page).not.toMatch(/\/employees\/null/);
    expect(page).not.toMatch(/hrPayrollUpdateAttendance\([^,]+,\s*null/);
  });

  test("payroll attendance only uses persistent HR employee ids", () => {
    expect(page).toMatch(/hr_profile_only:\s*true/);
    expect(page).toMatch(/emp\?\.id && emp\?\.has_hr_profile !== false/);
    expect(page).toMatch(/hrEmployees\.map/);
    expect(page).toMatch(/if \(!employeeId\) return;/);
    expect(page).toMatch(/hr-attendance-employee-row/);
  });

  test("buat akun eternal toggle and fields", () => {
    expect(page).toMatch(/Buat Akun Eternal/);
    expect(page).toMatch(/hr-create-eternal-toggle/);
    expect(page).toMatch(/startWithAccountCreate/);
    expect(page).toMatch(/hr-eternal-account-fields/);
    expect(page).toMatch(/hr-acc-password/);
    expect(page).toMatch(/Wajib diisi \(min\. 6\)/);
    expect(page).toMatch(/hrPayrollCreateEternalAccount/);
    expect(page).toMatch(/payload\.create_account\s*=/);
    expect(page).toMatch(/canCreateEternalAccount/);
    expect(page).toMatch(/hasPerm\("users\.manage"\) && hasPerm\("hr\.employee\.manage"\)/);
    expect(page).toMatch(/users\.manage/);
    expect(page).toMatch(/hr-create-account-action/);
    expect(page).toMatch(/Role \/ Hak Akses/);
    expect(page).toMatch(/bukan jabatan HR/);
    expect(page).not.toMatch(/eternal123/i);
    expect(page).not.toMatch(/TRIAL_DEFAULT_PASSWORD/);
  });

  test("employee modal is viewport-safe with sticky header/footer and scrollable body", () => {
    const css = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
    expect(page).toMatch(/hr-employee-modal/);
    expect(page).toMatch(/data-testid="hr-employee-form-body"/);
    expect(page).toMatch(/data-testid="hr-employee-form-footer"/);
    expect(page).toMatch(/hr-employee-form-body/);
    expect(page).toMatch(/hr-employee-form-footer/);
    expect(page).toMatch(/document\.documentElement/);
    expect(page).toMatch(/body\.style\.position\s*=\s*"fixed"/);
    expect(page).toMatch(/window\.scrollTo\(0,\s*scrollY\)/);
    expect(page).toMatch(/previous\.htmlOverflow/);
    expect(page).toMatch(/previous\.bodyOverflow/);
    expect(page).toMatch(/hr-create-eternal-toggle/);
    expect(page).toMatch(/data-testid="hr-employee-save"/);
    expect(css).toMatch(/\.modal-inner\.hr-employee-modal\s*\{[^}]*max-height\s*:\s*calc\(100vh\s*-\s*32px\)/s);
    expect(css).toMatch(/\.hr-employee-form-body\s*\{[^}]*overflow-y\s*:\s*auto/s);
    expect(css).toMatch(/\.hr-employee-form-footer\s*\{[^}]*flex-shrink\s*:\s*0/s);
    expect(css).toMatch(/\.hr-employee-form-footer\s*\{[^}]*background\s*:\s*var\(--surface\)/s);
  });
});
