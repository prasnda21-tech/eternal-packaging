import fs from "fs";
import path from "path";

describe("Settings EmployeeForm — Email Notifikasi", () => {
  const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");

  function sliceFn(name, nextName) {
    const start = settingsSrc.indexOf(`function ${name}`);
    const end = nextName
      ? settingsSrc.indexOf(`function ${nextName}`, start + 1)
      : settingsSrc.length;
    return settingsSrc.slice(start, end > 0 ? end : undefined);
  }

  const empForm = sliceFn("EmployeeForm", "EmployeesTab");

  test("Email login remains labeled and read-only on edit", () => {
    expect(empForm).toMatch(/label="Email login"/);
    expect(empForm).toMatch(/data-testid="emp-email"/);
    expect(empForm).toMatch(/disabled=\{isEdit \|\| busy\}/);
  });

  test("Email Notifikasi field is visible, editable, and compact with helper copy", () => {
    expect(empForm).toMatch(/label="Email Notifikasi"/);
    expect(empForm).toMatch(/data-testid="emp-notification-email"/);
    expect(empForm).toMatch(/Digunakan untuk menerima pemberitahuan operasional\. Tidak mengubah email login\./);
    expect(empForm).toMatch(/data-testid="emp-notification-email-hint"/);
    expect(empForm).toMatch(/Kosong = tidak ada notifikasi email yang dikirim ke pengguna ini\./);
    expect(empForm).toMatch(/data-testid="emp-notification-email-empty-hint"/);
    // Editable when not busy (no isEdit lock)
    expect(empForm).toMatch(
      /data-testid="emp-notification-email"[\s\S]*?disabled=\{busy\}|disabled=\{busy\}[\s\S]*?data-testid="emp-notification-email"/,
    );
  });

  test("form state includes notification_email and functional setState (focus-safe typing)", () => {
    expect(empForm).toMatch(/notification_email:\s*initial\?\.notification_email \|\| ""/);
    expect(empForm).toMatch(
      /setForm\(\(f\)\s*=>\s*\(\{\s*\.\.\.f,\s*notification_email:\s*e\.target\.value\s*\}\)\)/,
    );
  });

  test("submit payload sends notification_email (empty or normalized)", () => {
    expect(empForm).toMatch(/payload\.notification_email\s*=\s*notifyRaw/);
    expect(empForm).toMatch(/payload\.notification_email\s*=\s*""/);
    expect(empForm).toMatch(/Format email notifikasi tidak valid/);
  });

  test("create still sends login email; edit does not overwrite login email in payload", () => {
    expect(empForm).toMatch(/if\s*\(!isEdit\)\s*\{[\s\S]*payload\.email\s*=\s*form\.email\.trim\(\)\.toLowerCase\(\)/);
    // Outside create block, email is not assigned from form for edit path
    const withoutCreate = empForm.replace(
      /if\s*\(!isEdit\)\s*\{[\s\S]*?\n\s*\}/,
      "",
    );
    expect(withoutCreate).not.toMatch(/payload\.email\s*=/);
  });

  test("no remount key that would reset input mid-typing on notification field alone", () => {
    // EmployeeForm itself is not keyed by notification_email
    expect(settingsSrc).not.toMatch(/key=\{[^}]*notification_email/);
    expect(empForm).not.toMatch(/key=\{form\.notification_email/);
  });
});
