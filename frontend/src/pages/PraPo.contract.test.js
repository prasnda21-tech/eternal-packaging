import fs from "fs";
import path from "path";

const pageSrc = fs.readFileSync(path.join(__dirname, "PraPo.jsx"), "utf8");
const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const navSrc = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

describe("Pra PO + unified notifications contract", () => {
  test("single Settings Notifikasi tab remains", () => {
    const matches = settingsSrc.match(/id:\s*"notifications"/g) || [];
    expect(matches.length).toBe(1);
    expect(settingsSrc).toMatch(/function NotificationsSettingsTab/);
    expect(settingsSrc).toMatch(/customerDesignApprovalNotificationSettings/);
    expect(settingsSrc).toMatch(/crmProgressNotificationSettings/);
    expect(settingsSrc).toMatch(/notifications-crm-progress-settings/);
    expect(settingsSrc).toMatch(/CRM creator saja/);
  });

  test("Pra PO menu and routes exist with role gates", () => {
    expect(navSrc).toMatch(/praPo:\s*"nav\.sales\.pra_po"/);
    expect(navSrc).toMatch(/label:\s*"Pra PO"/);
    expect(appSrc).toMatch(/path="\/penjualan\/pra-po"/);
    expect(appSrc).toMatch(/pra_po\.create/);
    expect(appSrc).toMatch(/pra_po\.verify/);
  });

  test("Pra PO page supports return / revise / resubmit / verify", () => {
    expect(pageSrc).toMatch(/pra-po-return/);
    expect(pageSrc).toMatch(/pra-po-submit/);
    expect(pageSrc).toMatch(/pra-po-verify/);
    expect(pageSrc).toMatch(/pra-po-return-reason/);
    expect(pageSrc).toMatch(/Ajukan Ulang/);
    expect(pageSrc).toMatch(/Alasan pengembalian/);
  });
});
