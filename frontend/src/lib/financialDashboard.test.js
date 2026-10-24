import {
  agingChartStatus,
  chartPlotsSeries,
  formatAxisMoney,
  groupDetailNotes,
  formatMargin,
  formatMetric,
  jakartaDate,
  periodRange,
  shouldApplyDashboardResponse,
  trendShowsDot,
} from "./financialDashboard";
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
    expect(visible).toEqual({ parent: true, production: true, owner: false, finance: false, sales: false });
    expect(dashboardMenuItems(has(NAV_KEYS.dashboard))).toEqual([
      expect.objectContaining({ label: "Dashboard Produksi", to: "/dashboard", end: true }),
    ]);
  });

  test("finance access does not reveal production", () => {
    const visible = dashboardNavVisible(has("dashboard.finance.view"));
    expect(visible.production).toBe(false);
    expect(visible.finance).toBe(true);
    expect(dashboardMenuItems(has("dashboard.finance.view")).map((item) => item.to)).toEqual(["/dashboard/keuangan"]);
    expect(dashboardMenuItems(has("dashboard.finance.view")).map((item) => item.label)).toEqual(["Dashboard Keuangan"]);
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

  test("compacts axis amounts and keeps a single real point visible", () => {
    expect(formatAxisMoney(30000)).toBe("30 rb");
    expect(formatAxisMoney(144000)).toBe("144 rb");
    expect(formatAxisMoney(1500000)).toBe("1,5 jt");
    expect(formatAxisMoney(-35000)).toBe("-35 rb");
    expect(formatAxisMoney(0)).toBe("0");
    expect(formatAxisMoney(null)).toBe("");
    expect(chartPlotsSeries("empty")).toBe(false);
    expect(chartPlotsSeries("error")).toBe(false);
    expect(chartPlotsSeries("zero")).toBe(true);
    expect(trendShowsDot(1)).toBe(true);
    expect(trendShowsDot(12)).toBe(false);
    expect(agingChartStatus("empty", "empty")).toBe("empty");
    expect(agingChartStatus("zero", "zero")).toBe("zero");
    expect(agingChartStatus("error", "error")).toBe("error");
    expect(agingChartStatus("complete", "error")).toBe("complete");
  });

  test("merges identical detail notes and keeps the affected metrics", () => {
    expect(groupDetailNotes([
      { metric: "Pendapatan", text: "Ada reversal yang belum selesai." },
      { metric: "Laba bersih", text: "Ada reversal yang belum selesai." },
      { metric: "Persediaan", text: "Cakupan opening belum lengkap." },
      { metric: "", text: "   " },
    ])).toEqual([
      { text: "Ada reversal yang belum selesai.", metrics: ["Pendapatan", "Laba bersih"] },
      { text: "Cakupan opening belum lengkap.", metrics: ["Persediaan"] },
    ]);
  });
});
