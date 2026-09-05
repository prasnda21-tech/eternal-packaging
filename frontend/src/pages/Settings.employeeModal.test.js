import fs from "fs";
import path from "path";

describe("Settings Employee Modal viewport-safe structure and CSS", () => {
  const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
  const cssSrc = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");

  test("Settings Employee modal uses viewport-safe shell and wrapper", () => {
    // Both Tambah and Edit share modal-viewport-safe-wrap and modal-viewport-safe
    expect(settingsSrc).toMatch(/className="modal modal-viewport-safe-wrap"\s+data-testid="employee-modal"/);
    expect(settingsSrc).toMatch(/className=\{\s*"modal-inner modal-viewport-safe "\s*\+\s*\(modal\.type === "perms" \? "modal-wide" : ""\)\s*\}/);
  });

  test("EmployeeForm renders internal scrolling body and fixed footer", () => {
    // Body scroll container
    expect(settingsSrc).toMatch(/<form className="emp-form emp-modal-form"/);
    expect(settingsSrc).toMatch(/<div className="emp-modal-body" data-testid="employee-modal-body">/);
    expect(settingsSrc).toMatch(/data-testid="employee-modal-footer"/);
    expect(settingsSrc).toMatch(/className="emp-modal-foot"/);
  });

  test("Modal header is non-scrolling, stays visible with title and close button", () => {
    expect(settingsSrc).toMatch(/<header className="modal-head">/);
    expect(settingsSrc).toMatch(/Tambah Karyawan/);
    expect(settingsSrc).toMatch(/Edit \$\{modal\.user\.name\}/);
    expect(settingsSrc).toMatch(/aria-label="Tutup"/);
    expect(cssSrc).toMatch(/\.modal-inner\.modal-viewport-safe \.modal-head\{flex-shrink:0\}/);
  });

  test("Modal footer stays visible with action buttons and reset password on edit", () => {
    // Action buttons inside footer
    expect(settingsSrc).toMatch(/data-testid="employee-form-cancel"/);
    expect(settingsSrc).toMatch(/data-testid="employee-form-submit"/);
    expect(settingsSrc).toMatch(/data-testid="emp-reset-password"/);
    expect(settingsSrc).toMatch(/isEdit && showData && onResetPassword/);
    expect(cssSrc).toMatch(/\.emp-modal-foot\{[^}]*flex-shrink:0/);
    expect(cssSrc).toMatch(/\.emp-modal-foot\{[^}]*display:flex/);
    expect(cssSrc).toMatch(/\.emp-modal-foot\{[^}]*justify-content:space-between/);
    expect(cssSrc).toMatch(/\.emp-modal-foot\{[^}]*border-top:1px solid var\(--line\)/);
  });

  test("CSS rules guarantee viewport-safe height, flex layout and prevent double-scroll", () => {
    // Viewport-safe max-height
    expect(cssSrc).toMatch(/\.modal-inner\.modal-viewport-safe\{[^}]*max-height:min\(calc\(100vh - 32px\),860px\)/);
    expect(cssSrc).toMatch(/\.modal-inner\.modal-viewport-safe\{[^}]*display:flex/);
    expect(cssSrc).toMatch(/\.modal-inner\.modal-viewport-safe\{[^}]*flex-direction:column/);
    expect(cssSrc).toMatch(/\.modal-inner\.modal-viewport-safe\{[^}]*overflow:hidden/);

    // Form container doesn't create extra scrollbar
    expect(cssSrc).toMatch(/\.modal-inner > form\.emp-modal-form/);
    expect(cssSrc).toMatch(/\.modal-viewport-safe > form\.emp-modal-form\{[^}]*overflow:hidden/);

    // Body scrolls internally and children don't shrink/cut off
    expect(cssSrc).toMatch(/\.emp-modal-body\{[^}]*overflow-y:auto/);
    expect(cssSrc).toMatch(/\.emp-modal-body\{[^}]*min-height:0/);
    expect(cssSrc).toMatch(/\.emp-modal-body\{[^}]*flex:1 1 auto/);
    expect(cssSrc).toMatch(/\.emp-modal-body > \*\{flex-shrink:0\}/);
  });
});
