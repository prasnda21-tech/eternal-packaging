import fs from "fs";
import path from "path";

describe("Sales Accounting Settings UI", () => {
  const pageSrc = fs.readFileSync(path.join(__dirname, "SalesAccountingSettings.jsx"), "utf8");
  const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
  const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

  test("settings card maps exact COA selectors and net-to-revenue rule", () => {
    expect(pageSrc).toMatch(/sales-accounting-settings/);
    expect(pageSrc).toMatch(/sales-accounting-receivable/);
    expect(pageSrc).toMatch(/sales-accounting-revenue/);
    expect(pageSrc).toMatch(/sales-accounting-output-tax/);
    expect(pageSrc).toMatch(/Net ke Pendapatan/);
    expect(pageSrc).toMatch(/account_type === "AREC"/);
    expect(pageSrc).toMatch(/account_type === "REVE"/);
    expect(pageSrc).toMatch(/report_group === "liability"/);
    expect(pageSrc).not.toMatch(/Posted/);
  });

  test("Settings nav and API are wired", () => {
    expect(settingsSrc).toMatch(/settings-tab-sales-accounting/);
    expect(settingsSrc).toMatch(/SalesAccountingSettings/);
    expect(apiSrc).toMatch(/salesAccountingSettings:/);
    expect(apiSrc).toMatch(/updateSalesAccountingSettings:/);
  });
});
