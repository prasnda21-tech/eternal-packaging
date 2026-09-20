import fs from "fs";
import path from "path";

describe("Purchase Accounting Settings UI", () => {
  const pageSrc = fs.readFileSync(path.join(__dirname, "PurchaseAccountingSettings.jsx"), "utf8");
  const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
  const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

  test("settings card maps exact COA selectors", () => {
    expect(pageSrc).toMatch(/purchase-accounting-settings/);
    expect(pageSrc).toMatch(/purchase-accounting-payable/);
    expect(pageSrc).toMatch(/purchase-accounting-clearing/);
    expect(pageSrc).toMatch(/Dr Clearing \/ Cr Hutang Usaha/);
    expect(pageSrc).toMatch(/account_type === "APAY"/);
    expect(pageSrc).toMatch(/account_type === "OCLY"/);
    expect(pageSrc).toMatch(/account_type === "OASS"/);
    expect(pageSrc).not.toMatch(/Posted/);
  });

  test("Settings nav and API are wired", () => {
    expect(settingsSrc).toMatch(/settings-tab-purchase-accounting/);
    expect(settingsSrc).toMatch(/PurchaseAccountingSettings/);
    expect(apiSrc).toMatch(/purchaseAccountingSettings:/);
    expect(apiSrc).toMatch(/updatePurchaseAccountingSettings:/);
  });
});
