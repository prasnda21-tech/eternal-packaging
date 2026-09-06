import fs from "fs";
import path from "path";
import { settingsTabFromPath } from "../lib/navAccess";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

describe("Settings child routes", () => {
  test("Settings syncs the existing section from path without changing panel contracts", () => {
    expect(settingsSrc).toMatch(/settingsTabFromPath/);
    expect(settingsSrc).toMatch(/selectTab/);
    expect(settingsSrc).toMatch(/navigate\(`\/settings\/\$\{id\}`\)/);
    expect(settingsSrc).toMatch(/tab === "sales-accounting" && canViewSalesAccounting/);
    expect(settingsSrc).toMatch(/tab === "payroll-accounting" && canViewPayrollAccounting/);
    expect(settingsSrc).toMatch(/<SalesAccountingSettings/);
    expect(settingsSrc).toMatch(/<PayrollAccountingSettings/);
  });

  test("App exposes existing Settings page on child paths", () => {
    expect(appSrc).toMatch(/path="\/settings\/:tab"/);
    expect(appSrc).toMatch(/path="\/settings"/);
    expect(appSrc.match(/<Settings \/>/g).length).toBe(2);
  });

  test("legacy query still resolves to the same section id", () => {
    expect(settingsTabFromPath("/settings", "?tab=sales-accounting")).toBe("sales-accounting");
    expect(settingsTabFromPath("/settings/sales-accounting", "?foo=1")).toBe("sales-accounting");
    expect(settingsTabFromPath("/settings/not-a-real-tab")).toBe(null);
  });

  test("child routes hide the in-page category grid", () => {
    expect(settingsSrc).toMatch(/showCategoryNav = location\.pathname === "\/settings" && !urlTab/);
    expect(settingsSrc).toMatch(/\{showCategoryNav && \(/);
  });
});
