import fs from "fs";
import path from "path";

describe("Form Pekerjaan Divisi many-to-many UI", () => {
  const settings = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
  const matrix = fs.readFileSync(path.join(__dirname, "../components/FormAccessMatrix.jsx"), "utf8");

  test("work panel tab hosts form-access matrix", () => {
    expect(settings).toMatch(/FormAccessMatrix/);
    expect(settings).toMatch(/canManagePerms/);
    expect(settings).toMatch(/canListRoles/);
    expect(matrix).toMatch(/form-access-matrix/);
    expect(matrix).toMatch(/adminListRoles/);
    expect(matrix).toMatch(/adminUpdateRolePermissions/);
  });

  test("matrix is driven by existing RBAC keys not hardcoded role outcomes", () => {
    expect(matrix).toMatch(/FORM_ACCESS_DEFS/);
    expect(matrix).toMatch(/applyFormToggle/);
    expect(matrix).not.toMatch(/role\s*===\s*["']helper["']\s*\?\s*true/);
  });
});
