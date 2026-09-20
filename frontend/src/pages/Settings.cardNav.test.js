import fs from "fs";
import path from "path";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const cssSrc = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");

describe("Settings category card navigation", () => {
  test("uses card grid instead of long horizontal category tabs", () => {
    expect(settingsSrc).toMatch(/settings-cat-grid|SettingsCategoryNav/);
    expect(settingsSrc).toMatch(/settings-category-nav/);
    expect(settingsSrc).toMatch(/settings-cat-card/);
    // Root category nav must not be a nowrap horizontal .settings-tabs row
    expect(settingsSrc).not.toMatch(/<div className="settings-tabs" role="tablist">\s*\{canViewUsers/);
    expect(cssSrc).toMatch(/\.settings-cat-grid\{[^}]*display:\s*grid/);
    expect(cssSrc).toMatch(/grid-template-columns:\s*repeat\(4,/);
  });

  test("preserves stable section keys and testids", () => {
    const keys = [
      "employees",
      "roles",
      "sidebar-access",
      "operator-wage",
      "hpp",
      "sales-accounting",
      "purchase-accounting",
      "payroll-accounting",
      "customer-pricing",
      "customer-approval",
      "notifications",
      "delivery-deadline",
      "operator-target",
      "work-panel",
      "employee-admin",
      "hr-payroll",
      "sales-activity",
      "route-optimizer",
      "accurate-creators",
      "sales-user-mapping",
      "audit",
    ];
    keys.forEach((id) => {
      expect(settingsSrc).toMatch(new RegExp(`id:\\s*"${id}"`));
      expect(settingsSrc).toMatch(new RegExp(`settings-tab-${id === "sidebar-access" ? "sidebar-access" : id}`));
      expect(settingsSrc).toMatch(new RegExp(`tab === "${id}"`));
    });
  });

  test("active card styling and AKTIF badge exist", () => {
    expect(settingsSrc).toMatch(/is-active/);
    expect(settingsSrc).toMatch(/AKTIF/);
    expect(settingsSrc).toMatch(/aria-selected=\{isActive\}/);
    expect(cssSrc).toMatch(/\.settings-cat-card\.is-active/);
    expect(cssSrc).toMatch(/settings-cat-card-badge/);
  });

  test("permission gates still hide unauthorized categories", () => {
    expect(settingsSrc).toMatch(/navVisibility/);
    expect(settingsSrc).toMatch(/SETTINGS_NAV_ITEMS\.filter/);
    expect(settingsSrc).toMatch(/employees:\s*canViewUsers/);
    expect(settingsSrc).toMatch(/roles:\s*canManagePerms/);
    expect(settingsSrc).toMatch(/"hr-payroll":\s*canViewHrPayroll/);
    expect(settingsSrc).toMatch(/"sales-activity":\s*canViewSalesActivity/);
    expect(settingsSrc).toMatch(/"route-optimizer":\s*canViewRouteOptimizer/);
    expect(settingsSrc).toMatch(/hpp:\s*canViewHpp/);
    expect(settingsSrc).toMatch(/"sales-accounting":\s*canViewSalesAccounting/);
    expect(settingsSrc).toMatch(/"purchase-accounting":\s*canViewPurchaseAccounting/);
    expect(settingsSrc).toMatch(/"payroll-accounting":\s*canViewPayrollAccounting/);
    expect(settingsSrc).toMatch(/"customer-pricing":\s*canViewCustomerPricing/);
  });

  test("only active panel content is conditionally rendered", () => {
    expect(settingsSrc).toMatch(/settings-active-panel/);
    expect(settingsSrc).toMatch(/tab === "employees" && canViewUsers/);
    expect(settingsSrc).toMatch(/tab === "hpp" && canViewHpp/);
    expect(settingsSrc).toMatch(/tab === "hr-payroll" && canViewHrPayroll/);
    // Must not render all panels unconditionally
    expect(settingsSrc).not.toMatch(/<(EmployeesTab|HppSettings|HrPayrollSettings)[^/]*\/>\s*<(EmployeesTab|HppSettings)/);
  });

  test("does not introduce emoji icons in category cards", () => {
    const navBlock = settingsSrc.slice(
      settingsSrc.indexOf("SETTINGS_NAV_ITEMS"),
      settingsSrc.indexOf("function SettingsCategoryNav")
    );
    expect(navBlock).not.toMatch(/[\u{1F300}-\u{1FAFF}]/u);
  });
});
