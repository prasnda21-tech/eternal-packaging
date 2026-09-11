import fs from "fs";
import path from "path";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const mobileSrc = fs.readFileSync(path.join(__dirname, "../mobile/MobileNotificationsPage.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
const navSrc = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");

describe("Customer Design Approval notification settings + inbox wiring", () => {
  test("Settings exposes Notifikasi tab with Approval Design Customer controls", () => {
    expect(settingsSrc).toMatch(/id:\s*"notifications"/);
    expect(settingsSrc).toMatch(/settings-tab-notifications/);
    expect(settingsSrc).toMatch(/function NotificationsSettingsTab/);
    expect(settingsSrc).toMatch(/data-testid="notifications-settings-tab"/);
    expect(settingsSrc).toMatch(/data-testid="notif-toggle-enabled"/);
    expect(settingsSrc).toMatch(/data-testid="notif-toggle-designer"/);
    expect(settingsSrc).toMatch(/data-testid="notif-toggle-crm"/);
    expect(settingsSrc).toMatch(/notify_designer/);
    expect(settingsSrc).toMatch(/notify_crm/);
    expect(settingsSrc).toMatch(/customerDesignApprovalNotificationSettings/);
    expect(settingsSrc).toMatch(/updateCustomerDesignApprovalNotificationSettings/);
    expect(settingsSrc).toMatch(/notifications:\s*canViewCaSettings/);
    expect(settingsSrc).toMatch(/tab === "notifications"/);
  });

  test("navAccess registers notifications under existing Settings authorization", () => {
    expect(navSrc).toMatch(/"notifications"/);
    expect(navSrc).toMatch(/"notifications":\s*"Notifikasi"/);
    expect(navSrc).toMatch(/"notifications":\s*Boolean\(parentGranted && customerApproval\)/);
  });

  test("api client exposes notification list/read/settings endpoints", () => {
    expect(apiSrc).toMatch(/listNotifications/);
    expect(apiSrc).toMatch(/markNotificationRead/);
    expect(apiSrc).toMatch(/\/settings\/notifications\/customer-design-approval/);
  });

  test("mobile notifications page renders inbox semantics and deep-link navigation", () => {
    expect(mobileSrc).toMatch(/listNotifications/);
    expect(mobileSrc).toMatch(/markNotificationRead/);
    expect(mobileSrc).toMatch(/data-testid="mobile-notifications-page"/);
    expect(mobileSrc).toMatch(/mobile-notifications-empty/);
    expect(mobileSrc).toMatch(/is_read/);
    expect(mobileSrc).toMatch(/deep_link/);
    expect(mobileSrc).toMatch(/\/m\/orders\//);
    expect(mobileSrc).toMatch(/navigate\(/);
    expect(mobileSrc).not.toMatch(/serviceWorker|pushManager|Notification\(/);
  });
});
