import fs from "fs";
import path from "path";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const mobileSrc = fs.readFileSync(path.join(__dirname, "../mobile/MobileNotificationsPage.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
const navSrc = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
const bellSrc = fs.readFileSync(path.join(__dirname, "../components/FloatingNotificationBell.jsx"), "utf8");

function extractNotificationsTab(src) {
  const start = src.indexOf("function NotificationsSettingsTab");
  expect(start).toBeGreaterThan(-1);
  const end = src.indexOf("\nfunction DeliveryDeadlineSettingsTab", start);
  expect(end).toBeGreaterThan(start);
  return src.slice(start, end);
}

describe("Customer Design Approval notification settings + inbox wiring", () => {
  const tabSrc = extractNotificationsTab(settingsSrc);

  test("CASE A — existing role toggles still render Aktif / Designer / CRM", () => {
    expect(tabSrc).toMatch(/data-testid="notif-toggle-enabled"/);
    expect(tabSrc).toMatch(/data-testid="notif-toggle-designer"/);
    expect(tabSrc).toMatch(/data-testid="notif-toggle-crm"/);
    expect(tabSrc).toMatch(/>Aktif</);
    expect(tabSrc).toMatch(/>Designer</);
    expect(tabSrc).toMatch(/>CRM</);
    expect(tabSrc).toMatch(/notify_designer/);
    expect(tabSrc).toMatch(/notify_crm/);
  });

  test("CASE B — member picker renders Anggota Penerima Tambahan", () => {
    expect(tabSrc).toMatch(/Anggota Penerima Tambahan/);
    expect(tabSrc).toMatch(/data-testid="notif-additional-recipients"/);
    expect(tabSrc).toMatch(/data-testid="notif-member-picker"/);
    expect(tabSrc).toMatch(/data-testid="notif-member-search"/);
    expect(tabSrc).toMatch(/Cari anggota\.\.\./);
  });

  test("CASE C — search active users via existing adminListUsers", () => {
    expect(tabSrc).toMatch(/adminListUsers\(/);
    expect(tabSrc).toMatch(/notif-member-search-results/);
    expect(tabSrc).toMatch(/status === "inactive"/);
  });

  test("CASE D/E — select user once; duplicate guarded", () => {
    expect(tabSrc).toMatch(/addMember/);
    expect(tabSrc).toMatch(/if \(cur\.includes\(id\)\) return s/);
    expect(tabSrc).toMatch(/additional_recipient_user_ids: \[\.\.\.cur, id\]/);
  });

  test("CASE F — remove selected user", () => {
    expect(tabSrc).toMatch(/removeMember/);
    expect(tabSrc).toMatch(/notif-member-remove-/);
    expect(tabSrc).toMatch(/\.filter\(\(x\) => x !== uid\)/);
  });

  test("CASE G — Save sends canonical user IDs not display names/objects", () => {
    expect(tabSrc).toMatch(/additional_recipient_user_ids: selectedIds\.map\(\(id\) => String\(id\)\)/);
    expect(tabSrc).not.toMatch(/additional_recipient_user_ids: selectedIds\.map\(\(id\) => \(\{/);
    expect(tabSrc).not.toMatch(/name:\s*u\.name/);
  });

  test("CASE H — reload setting restores selected members", () => {
    expect(tabSrc).toMatch(/customerDesignApprovalNotificationSettings/);
    expect(tabSrc).toMatch(/additional_recipient_user_ids: Array\.isArray\(s\.additional_recipient_user_ids\)/);
    expect(tabSrc).toMatch(/Array\.isArray\(next\.additional_recipient_user_ids\)/);
  });

  test("CASE I — old settings response without member field → empty selection", () => {
    expect(tabSrc).toMatch(/\? s\.additional_recipient_user_ids\s*\n\s*: \[\]/);
  });

  test("CASE J — Designer/CRM toggles coexist with explicit members", () => {
    expect(tabSrc).toMatch(/notif-toggle-designer/);
    expect(tabSrc).toMatch(/notif-toggle-crm/);
    expect(tabSrc).toMatch(/notif-additional-recipients/);
    expect(tabSrc).toMatch(/notif-member-selected/);
  });

  test("CASE K — API/user search error does not crash Settings", () => {
    expect(tabSrc).toMatch(/notif-member-search-error/);
    expect(tabSrc).toMatch(/setDirectoryErr/);
    expect(tabSrc).toMatch(/setDirectory\(\[\]\)/);
    expect(tabSrc).toMatch(/\.catch\(\(e\) => \{/);
  });

  test("CASE K2 — directory fetch failure does not erase persisted member IDs", () => {
    const catchStart = tabSrc.indexOf("api.adminListUsers()");
    expect(catchStart).toBeGreaterThan(-1);
    const catchBlock = tabSrc.slice(catchStart, catchStart + 500);
    expect(catchBlock).toMatch(/\.catch\(\(e\) => \{/);
    expect(catchBlock).toMatch(/setDirectory\(\[\]\)/);
    expect(catchBlock).toMatch(/setDirectoryErr/);
    // Must not wipe selected/persisted explicit recipient IDs on directory 403/error
    expect(catchBlock).not.toMatch(/additional_recipient_user_ids:\s*\[\]/);
    expect(catchBlock).not.toMatch(/setState\(\{[^}]*additional_recipient_user_ids:\s*\[\]/);
    expect(tabSrc).toMatch(/additional_recipient_user_ids: selectedIds\.map\(\(id\) => String\(id\)\)/);
  });

  test("CASE L — unauthorized Settings access regression remains safe", () => {
    expect(settingsSrc).toMatch(/notifications:\s*canViewCaSettings/);
    expect(settingsSrc).toMatch(/tab === "notifications" && canViewCaSettings/);
    expect(tabSrc).toMatch(/customer_approval\.settings/);
    expect(tabSrc).toMatch(/!canEdit/);
  });

  test("Settings exposes Notifikasi tab with Approval Design Customer controls", () => {
    expect(settingsSrc).toMatch(/id:\s*"notifications"/);
    expect(settingsSrc).toMatch(/settings-tab-notifications/);
    expect(settingsSrc).toMatch(/function NotificationsSettingsTab/);
    expect(settingsSrc).toMatch(/data-testid="notifications-settings-tab"/);
    expect(settingsSrc).toMatch(/customerDesignApprovalNotificationSettings/);
    expect(settingsSrc).toMatch(/updateCustomerDesignApprovalNotificationSettings/);
    expect(settingsSrc).toMatch(/crmProgressNotificationSettings/);
    expect(settingsSrc).toMatch(/notifications-crm-progress-settings/);
    const tabMatches = settingsSrc.match(/id:\s*"notifications"/g) || [];
    expect(tabMatches.length).toBe(1);
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
    expect(apiSrc).toMatch(/adminListUsers/);
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

  test("floating bell wiring remains present", () => {
    expect(bellSrc).toMatch(/listNotifications/);
    expect(bellSrc).toMatch(/FloatingNotificationBell/);
  });
});
