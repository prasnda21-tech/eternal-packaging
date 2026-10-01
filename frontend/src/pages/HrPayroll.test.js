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
    ["hr-calc-btn", "hr-transition-review", "hr-transition-approval", "hr-transition-final", "hr-reopen-btn", "hr-breakdown", "hr-kasbon-row", "hr-payroll-accounting-ready", "hr-payroll-accounting-preview"].forEach((id) => expect(page).toMatch(new RegExp(id)));
    expect(page).toMatch(/hasPerm\("payroll\.reopen"\)/);
    expect(page).toMatch(/hr-payroll-finance-steps/);
    expect(page).toMatch(/hr-pay-step-line/);
    expect(page).toMatch(/hr-payroll-finance-action/);
    expect(page).toMatch(/Status jurnal/);
    expect(page).toMatch(/hr-payroll-reverse-summary/);
    expect(page).toMatch(/payroll_accounting\.post/);
    expect(page).toMatch(/hr-payroll-post-accounting/);
    expect(page).toMatch(/Post Accounting Payroll\?/);
    expect(page).toMatch(/hr-payroll-open-journal/);
    expect(page).toMatch(/Journal:/);
    expect(page).toMatch(/hrPayrollPeriodJournal/);
    expect(page).toMatch(/accounting_ready === true/);
    expect(page).toMatch(/Accounting: \{selected\.accounting_journal_id \? "Posted"/);
    expect(page).toMatch(/payroll_payment\.create/);
    expect(page).toMatch(/payroll_payment\.approve/);
    expect(page).toMatch(/payroll_payment\.post/);
    expect(page).toMatch(/payroll_payment\.reverse/);
    expect(page).toMatch(/payroll_payment\.cancel/);
    expect(page).toMatch(/hr-payroll-payment-history/);
    expect(page).toMatch(/hr-payroll-payment-journal-number/);
    expect(page).toMatch(/journal_number/);
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

  test("buat profil HR flow for virtual directory users", () => {
    expect(page).toMatch(/const canCreateHrProfile = \(row\) => Boolean\(row\?\.has_hr_profile === false && row\?\.user_id\)/);
    expect(page).toMatch(/data-testid="hr-create-profile-action"/);
    expect(page).toMatch(/Buat Profil HR/);
    expect(page).toMatch(/canCreateHrProfile\(row\) && \(\s*<button className="ghost sm" data-testid="hr-create-profile-action"/);
    expect(page).toMatch(/isCreateProfileFromUser/);
    expect(page).toMatch(/Buat Profil HR ·/);
    expect(page).toMatch(/hrPayrollCreateEmployee\(payload\)/);
    expect(page).toMatch(/user_id:\s*form\.user_id\s*\|\|\s*initial\?\.user_id/);
    expect(page).not.toMatch(/hrPayrollCreateEmployee\(\s*null/);
    expect(page).not.toMatch(/hrPayrollCreateEmployee\(\s*undefined/);
  });

  test("buat profil HR flow adheres strictly to requirements 1-12", () => {
    // 1. has_hr_profile=false shows "Buat Profil HR"
    expect(page).toMatch(/canCreateHrProfile\(row\) && \(\s*<button className="ghost sm" data-testid="hr-create-profile-action"/);
    expect(page).toMatch(/Buat Profil HR/);

    // 2. has_hr_profile=false does NOT show Edit
    expect(page).toMatch(/const canEditHr = \(row\) => Boolean\(row\?\.has_hr_profile !== false && row\?\.id\)/);

    // 3. has_hr_profile=true shows Edit
    expect(page).toMatch(/canEditHr\(row\) && <button className="ghost sm" onClick=\{\(\) => setEditing\(row\)\}>Edit<\/button>/);

    // 4. has_hr_profile=true does NOT show Buat Profil HR
    expect(page).toMatch(/const canCreateHrProfile = \(row\) => Boolean\(row\?\.has_hr_profile === false && row\?\.user_id\)/);

    // 5. clicking Buat Profil HR opens form
    expect(page).toMatch(/onClick=\{\(\) => setEditing\(row\)\}/);
    expect(page).toMatch(/isCreateProfileFromUser/);

    // 6. canonical user ID is retained for submit
    expect(page).toMatch(/user_id:\s*form\.user_id\s*\|\|\s*initial\?\.user_id\s*\|\|\s*null/);
    expect(page).toMatch(/user_id:\s*initial\?\.user_id/);

    // 7. known user fields prefilled where available
    expect(page).toMatch(/name:\s*initial\?\.name\s*\|\|\s*""/);
    expect(page).toMatch(/email:\s*initial\?\.email\s*\|\|\s*""/);
    expect(page).toMatch(/role:\s*initial\?\.role\s*\|\|\s*""/);
    expect(page).toMatch(/phone:\s*initial\?\.phone\s*\|\|\s*""/);

    // 8. cancel creates nothing / calls no create API
    expect(page).toMatch(/<button type="button" className="ghost" onClick=\{onClose\}>Batal<\/button>/);

    // 9. successful create refreshes list
    expect(page).toMatch(/await onSaved\(\);/);
    expect(page).toMatch(/onClose\(\);/);

    // 10. duplicate conflict shows safe message and refreshes list
    expect(page).toMatch(/ex\?\.response\?\.status === 409/);
    expect(page).toMatch(/onSaved\(\)\.catch/);

    // 11. no request contains /employees/null or /employees/undefined
    expect(page).not.toMatch(/\/employees\/null/);
    expect(page).not.toMatch(/\/employees\/undefined/);
  });

  test("payroll accounting preview shows treatment, exact COA, and payable line", () => {
    expect(page).toMatch(/PREVIEW — BELUM MEMBUAT JURNAL/);
    expect(page).toMatch(/<th>Sumber<\/th><th>Treatment<\/th><th>Akun<\/th>/);
    expect(page).toMatch(/previewTreatmentLabel\(row\.treatment\)/);
    expect(page).toMatch(/previewAccountLabel\(row\)/);
    expect(page).toMatch(/acc\.code/);
    expect(page).toMatch(/acc\.name/);
    expect(page).toMatch(/Utang Gaji \(net\)/);
    expect(page).toMatch(/hr-payroll-accounting-preview-payable/);
    expect(page).toMatch(/source_keys \|\| \[\]/);
    expect(page).toMatch(/Selisih/);
    expect(page).not.toMatch(/Posted Accounting/);
  });
});
