import {
  OWNER_PERM,
  canAccessOwnerDashboard,
  moneyOrState,
  ownerRangeFromSearch,
  shouldApplyDashboardResponse,
} from "./ownerAnalytics";
import { dashboardMenuItems } from "./navAccess";

describe("owner analytics access", () => {
  const has = (perms) => (key) => perms.includes(key);

  test("direktur and holding with perm allowed", () => {
    expect(canAccessOwnerDashboard({ role: "direktur_utama", permissions: [OWNER_PERM] }, has([OWNER_PERM]))).toBe(true);
    expect(canAccessOwnerDashboard({ role: "holding_team", permissions: [OWNER_PERM] }, has([OWNER_PERM]))).toBe(true);
  });

  test("admin denied even with owner perm", () => {
    expect(canAccessOwnerDashboard({ role: "admin", permissions: [OWNER_PERM] }, has([OWNER_PERM]))).toBe(false);
  });

  test("crm/operator denied", () => {
    expect(canAccessOwnerDashboard({ role: "crm", permissions: [OWNER_PERM] }, has([OWNER_PERM]))).toBe(false);
    expect(canAccessOwnerDashboard({ role: "operator", permissions: [OWNER_PERM] }, has([OWNER_PERM]))).toBe(false);
  });

  test("missing permission denied for director", () => {
    expect(canAccessOwnerDashboard({ role: "direktur_utama", permissions: [] }, has([]))).toBe(false);
  });

  test("owner-only director gets the owner fallback and admin does not", () => {
    const director = dashboardMenuItems(has([OWNER_PERM, "nav.dashboard.owner"]), { role: "direktur_utama", permissions: [OWNER_PERM] });
    expect(director.map((item) => item.to)).toEqual(["/dashboard/owner"]);
    expect(canAccessOwnerDashboard({ role: "direktur_utama", permissions: [OWNER_PERM] }, has([OWNER_PERM]))).toBe(true);
    const admin = dashboardMenuItems(has([OWNER_PERM, "nav.dashboard.owner"]), { role: "admin", permissions: [OWNER_PERM] });
    expect(admin.some((item) => item.to === "/dashboard/owner")).toBe(false);
    expect(admin.some((item) => item.to === "/dashboard/keuangan")).toBe(false);
  });
});

describe("owner analytics formatting", () => {
  test("moneyOrState never shows Rp 0 for empty/null", () => {
    expect(moneyOrState({ status: "empty", amount: null }).text).not.toMatch(/^Rp\s*0/);
    expect(moneyOrState({ status: "not_ready" }).text).toBe("Data Belum Siap");
    expect(moneyOrState({ status: "error" }).kind).toBe("error");
  });

  test("date presets", () => {
    const month = ownerRangeFromSearch("?preset=month", new Date("2026-03-15T03:00:00Z"));
    expect(month.preset).toBe("month");
    expect(month.start.endsWith("-01")).toBe(true);
    expect(shouldApplyDashboardResponse(2, 2)).toBe(true);
    expect(shouldApplyDashboardResponse(1, 2)).toBe(false);
  });
});
