import { balanceAsOfLabel, comparisonDisplay, displayAmount, displayCount, formatRatio, otherResultLines, plBars, positionLabel } from "./salesDashboard";
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

  test("production and finance stay on their own entries when sales is absent", () => {
    expect(dashboardMenuItems(has(NAV_KEYS.dashboard, "dashboard.finance.view")).map((item) => item.to)).toEqual([
      "/dashboard",
      "/dashboard/penjualan",
    ]);
    expect(dashboardMenuItems(has(NAV_KEYS.dashboard, "dashboard.finance.view")).map((item) => item.label)).toEqual([
      "Dashboard Produksi",
      "Dashboard Penjualan",
    ]);
  });

  test("sales and finance share one menu entry", () => {
    expect(dashboardMenuItems(has("dashboard.sales.view", "dashboard.finance.view")).map((item) => item.to)).toEqual([
      "/dashboard/penjualan",
    ]);
  });

  test("operations-only access opens the same penjualan menu", () => {
    const items = dashboardMenuItems(has("po.view"), { role: "ops" });
    expect(dashboardNavVisible(has("po.view"), { role: "ops" })).toEqual({
      parent: true,
      production: false,
      sales: false,
      finance: false,
    });
    expect(items.map((item) => item.label)).toEqual(["Dashboard Penjualan"]);
    expect(items.map((item) => item.to)).toEqual(["/dashboard/penjualan"]);
  });

  test("finance-only access keeps one penjualan menu item", () => {
    const items = dashboardMenuItems(has("dashboard.finance.view"), { role: "finance" });
    expect(items.map((item) => item.to)).toEqual(["/dashboard/penjualan"]);
    expect(items.map((item) => item.label)).toEqual(["Dashboard Penjualan"]);
  });

  test("admin without stored keys sees penjualan because O2 and O3 are allowed", () => {
    const items = dashboardMenuItems(() => false, { role: "admin" });
    expect(items.map((item) => ({ label: item.label, to: item.to }))).toEqual([
      { label: "Dashboard Penjualan", to: "/dashboard/penjualan" },
    ]);
  });

  test("a user with no widget permission does not see a dashboard menu", () => {
    expect(dashboardNavVisible(() => false, { role: "sales" }).parent).toBe(false);
    expect(dashboardMenuItems(() => false, { role: "sales" })).toEqual([]);
    expect(dashboardMenuItems(has(NAV_KEYS.dashboardSales), { role: "sales" })).toEqual([]);
  });

  test("production stays its own menu and does not add a second penjualan entry", () => {
    const items = dashboardMenuItems(has(NAV_KEYS.dashboard, "dashboard.sales.view", "po.view"), { role: "admin" });
    expect(items.map((item) => item.to)).toEqual(["/dashboard", "/dashboard/penjualan"]);
    expect(items.filter((item) => item.label === "Dashboard Penjualan")).toHaveLength(1);
    expect(items.some((item) => item.to === "/dashboard/widget")).toBe(false);
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

  test("labels a live receivable position separately from a period-end balance", () => {
    expect(positionLabel("")).toBe("Posisi saat ini");
    expect(positionLabel("2026-10-03")).toBe("Posisi saat ini · 2026-10-03");
    expect(positionLabel("bukan-tanggal")).toBe("Posisi saat ini");
    expect(balanceAsOfLabel("2026-09-26")).toBe("Per 2026-09-26");
    expect(balanceAsOfLabel("")).toBe("Per tanggal akhir periode");
  });

  test("keeps signed profit bars and only lists nonzero other results", () => {
    expect(plBars(null)).toBeNull();
    expect(plBars({ status: "empty", revenue: 0, cogs: 0, operating_expense: 0 })).toBeNull();
    expect(plBars({ status: "complete", revenue: 10, cogs: null, operating_expense: 1 })).toBeNull();
    expect(plBars({ status: "complete", revenue: 1000, cogs: 400, operating_expense: -50 })).toEqual([
      { key: "revenue", label: "Pendapatan", amount: 1000 },
      { key: "cogs", label: "HPP", amount: 400 },
      { key: "operating_expense", label: "Beban operasional", amount: -50 },
    ]);
    expect(otherResultLines({ status: "complete", other_income: 0, other_expense: 0 })).toEqual([]);
    expect(otherResultLines({ status: "error", other_income: 25, other_expense: -10 })).toEqual([]);
    expect(otherResultLines({ status: "complete", other_income: 25, other_expense: -10 })).toEqual([
      { key: "other_income", label: "Pendapatan lain", amount: 25 },
      { key: "other_expense", label: "Beban lain", amount: -10 },
    ]);
  });
});
