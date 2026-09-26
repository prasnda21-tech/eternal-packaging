import { comparisonDisplay, displayAmount, displayCount, formatRatio } from "./salesDashboard";
import { dashboardMenuItems, dashboardNavVisible, NAV_KEYS } from "./navAccess";

const has = (...keys) => (key) => keys.includes(key);

describe("sales dashboard access", () => {
  test("sales permission opens only Dashboard Penjualan", () => {
    const visible = dashboardNavVisible(has("dashboard.sales.view"));
    expect(visible).toEqual({ parent: true, production: false, sales: true, finance: false });
    expect(dashboardMenuItems(has("dashboard.sales.view"))).toEqual([
      expect.objectContaining({
        label: "Dashboard Penjualan",
        to: "/dashboard/penjualan",
        end: true,
        testid: "top-nav-dashboard-penjualan",
      }),
    ]);
  });

  test("production and finance stay unchanged when sales is absent", () => {
    expect(dashboardMenuItems(has(NAV_KEYS.dashboard, "dashboard.finance.view")).map((item) => item.to)).toEqual([
      "/dashboard",
      "/dashboard/keuangan",
    ]);
  });
});

describe("sales dashboard presentation", () => {
  test("hides a comparison arrow unless the percent is real", () => {
    expect(comparisonDisplay(null)).toEqual({ text: "—", arrow: null });
    expect(comparisonDisplay({ available: false, percent: 0.12, direction: "up" })).toEqual({ text: "—", arrow: null });
    expect(comparisonDisplay({ available: true, status: "error", percent: 0.12, direction: "up" })).toEqual({ text: "—", arrow: null });
    expect(comparisonDisplay({ available: true, percent: null, direction: null })).toEqual({ text: "—", arrow: null });
    expect(comparisonDisplay({ available: true, percent: 0, direction: "flat" })).toEqual({ text: "0%", arrow: null });
    expect(comparisonDisplay({ available: true, percent: 0.125, direction: "up" })).toEqual({ text: "12,5%", arrow: "up" });
    expect(comparisonDisplay({ available: true, percent: -0.5, direction: "down" })).toEqual({ text: "-50%", arrow: "down" });
  });

  test("does not turn a failed or empty amount into zero", () => {
    expect(displayAmount("error", 0, "rusak")).toBe("Gagal muat");
    expect(displayAmount("empty", null, "Belum ada faktur pada periode ini")).toBe("Belum ada faktur pada periode ini");
    expect(displayAmount("unavailable", null, "")).toBe("Belum tersedia");
    expect(displayAmount("complete", null)).toBe("—");
    expect(displayAmount("zero", 0)).toMatch(/^Rp\s/);
    expect(displayCount("error", 0)).toBe("Gagal muat");
    expect(displayCount("complete", null)).toBe("—");
    expect(displayCount("zero", 0)).toBe("0");
    expect(formatRatio(null)).toBe("—");
    expect(formatRatio(0.7)).toBe("70%");
  });
});
