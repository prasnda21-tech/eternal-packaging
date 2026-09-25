import { formatMargin, formatMetric, jakartaDate, periodRange, shouldApplyDashboardResponse } from "./financialDashboard";
import { dashboardMenuItems, dashboardNavVisible, NAV_KEYS } from "./navAccess";

const has = (...keys) => (key) => keys.includes(key);

describe("dashboard submenu access", () => {
  test("hides the parent when neither dashboard is granted", () => {
    const visible = dashboardNavVisible(has("nav.sales"));
    expect(visible.parent).toBe(false);
    expect(dashboardMenuItems(has("nav.sales"))).toEqual([]);
  });

  test("production access keeps the existing route and does not add finance", () => {
    const visible = dashboardNavVisible(has(NAV_KEYS.dashboard));
    expect(visible).toEqual({ parent: true, production: true, finance: false });
    expect(dashboardMenuItems(has(NAV_KEYS.dashboard))).toEqual([
      expect.objectContaining({ label: "Dashboard Produksi", to: "/dashboard", end: true }),
    ]);
  });

  test("finance access does not reveal production", () => {
    const visible = dashboardNavVisible(has("dashboard.finance.view"));
    expect(visible.production).toBe(false);
    expect(visible.finance).toBe(true);
    expect(dashboardMenuItems(has("dashboard.finance.view")).map((item) => item.to)).toEqual(["/dashboard/keuangan"]);
  });
});

describe("financial dashboard presentation", () => {
  test("uses the Asia/Jakarta calendar day", () => {
    expect(jakartaDate(new Date("2026-09-30T16:30:00Z"))).toBe("2026-09-30");
    expect(jakartaDate(new Date("2026-09-30T17:30:00Z"))).toBe("2026-10-01");
    expect(periodRange("today", {}, new Date("2026-09-30T17:30:00Z"))).toEqual({
      start: "2026-10-01",
      end: "2026-10-01",
    });
    expect(periodRange("month", {}, new Date("2026-09-30T17:30:00Z"))).toEqual({
      start: "2026-10-01",
      end: "2026-10-01",
    });
  });

  test("keeps empty, zero, unavailable, and error distinct", () => {
    expect(formatMetric("empty", null).text).toBe("Tidak ada transaksi");
    expect(formatMetric("zero", 0).text).toMatch(/^Rp /);
    expect(formatMetric("unavailable", null).text).toBe("Belum tersedia");
    expect(formatMetric("error", 0).text).toBe("Gagal muat");
    expect(formatMargin(null)).toBe("—");
    expect(formatMargin(0.3)).toBe("30,0%");
  });

  test("ignores a response that is older than the latest filter", () => {
    expect(shouldApplyDashboardResponse(1, 2)).toBe(false);
    expect(shouldApplyDashboardResponse(2, 2)).toBe(true);
  });
});
