import fs from "fs";
import path from "path";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");

const sliceFn = (name, nextName) => {
  const start = settingsSrc.indexOf(`function ${name}`);
  const end = nextName ? settingsSrc.indexOf(`function ${nextName}`) : settingsSrc.length;
  return settingsSrc.slice(start, end);
};

describe("Settings — single-source sidebar access UI", () => {
  test("card grid still includes Akses Menu Sidebar and Role & Hak Akses", () => {
    expect(settingsSrc).toMatch(/settings-cat-grid|SettingsCategoryNav/);
    expect(settingsSrc).toMatch(/id:\s*"roles"/);
    expect(settingsSrc).toMatch(/id:\s*"sidebar-access"/);
    expect(settingsSrc).toMatch(/label:\s*"Akses Menu Sidebar"/);
    expect(settingsSrc).toMatch(/label:\s*"Role & Hak Akses"/);
    expect(settingsSrc).toMatch(/settings-tab-sidebar-access/);
    expect(settingsSrc).toMatch(/settings-tab-roles/);
    expect(settingsSrc).toMatch(/"sidebar-access":\s*canViewSidebarAccess/);
    expect(settingsSrc).toMatch(/roles:\s*canManagePerms/);
  });

  test("Role & Hak Akses does not render the sidebar/menu matrix", () => {
    const rolesTab = sliceFn("RolesTab", "AuditTab");
    expect(rolesTab).toMatch(/data-testid="roles-tab"/);
    expect(rolesTab).toMatch(/RolePermissionWorkspace/);
    expect(rolesTab).toMatch(/roleBusinessSavePayload/);
    expect(rolesTab).not.toMatch(/NavAccessMatrix/);
    expect(rolesTab).not.toMatch(/nav-access-matrix/);
    expect(rolesTab).not.toMatch(/nav-access-save/);
    const workspaceSrc = fs.readFileSync(path.join(__dirname, "../components/RolePermissionWorkspace.jsx"), "utf8");
    expect(workspaceSrc).toMatch(/role-perms-\$\{selected\}/);
    expect(workspaceSrc).toMatch(/businessPermissionModules/);
  });

  test("Akses Menu Sidebar uses the read-only shared resolver", () => {
    const sidebarTab = sliceFn("SidebarAccessTab", "OperatorWageTab");
    expect(sidebarTab).toMatch(/settings-sidebar-access/);
    expect(sidebarTab).toMatch(/roleSidebarPreview/);
    expect(sidebarTab).not.toMatch(/NavAccessMatrix|adminUpdateRolePermissions|nav-access-save/);
    const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
    expect(shell).toMatch(/resolveDesktopSidebar\(hasPerm, user\)/);
    const resolver = fs.readFileSync(path.join(__dirname, "../lib/desktopSidebarModel.js"), "utf8");
    expect(resolver).toMatch(/role.sidebar_permissions/);
    expect(resolver).toMatch(/resolveDesktopSidebar/);
  });

  test("employee override UI hides sidebar/menu visibility module only", () => {
    const emp = sliceFn("EmployeeForm", "EmployeesTab");
    expect(emp).toMatch(/businessPermissionModules/);
    expect(emp).toMatch(/Hak Akses Khusus \(Override\)/);
    expect(emp).not.toMatch(/NavAccessMatrix/);
    expect(emp).toMatch(/permissions_override/);
  });
});
