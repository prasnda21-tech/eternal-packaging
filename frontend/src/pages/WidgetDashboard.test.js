import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import WidgetDashboard from "./WidgetDashboard";

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

jest.mock("react-router-dom", () => ({
  useNavigate: () => (global.__widgetNavigate || jest.fn()),
  useLocation: () => global.__widgetLocation || { pathname: "/dashboard/widget", search: "" },
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { role: global.__widgetRole || "" },
    hasPerm: (key) => (global.__widgetPerms || []).includes(key),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesDashboard: jest.fn(),
    dashboardToday: jest.fn(),
    deliverySchedule: jest.fn(),
    productionDashboard: jest.fn(),
    purchasePeriodSummary: jest.fn(),
    accountsPayableSummary: jest.fn(),
    financialDashboard: jest.fn(),
    widgetProfitLoss: jest.fn(),
    balanceSheet: jest.fn(),
    accountsReceivableOverdueCustomers: jest.fn(),
    widgetReconciliations: jest.fn(),
  },
}));

import api from "../lib/api";
import { TABS, WIDGET_IDS, canOpenWidgetDashboard, widgetAllowed } from "../components/widgetDashboard/widgetDashboardCatalog";

function Harness() {
  const [, setTick] = useState(0);
  global.__rerenderWidget = () => setTick((value) => value + 1);
  return <WidgetDashboard />;
}

function render() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(<Harness />); });
  return {
    host,
    unmount() {
      act(() => { root.unmount(); });
      host.remove();
    },
  };
}

beforeEach(() => {
  global.__widgetRole = "sales";
  global.__widgetPerms = ["dashboard.sales.view"];
  global.__widgetLocation = { pathname: "/dashboard/widget", search: "?tab=penjualan&panel=ringkasan&preset=custom&start=2026-10-01&end=2026-10-04" };
  global.__widgetNavigate = jest.fn();
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  api.salesDashboard.mockReset();
  api.dashboardToday.mockReset();
  api.deliverySchedule.mockReset();
  api.productionDashboard.mockReset();
  api.financialDashboard.mockReset();
  api.widgetProfitLoss.mockReset();
  api.purchasePeriodSummary.mockReset();
});

test("failed sales response does not show a sample amount", async () => {
  api.salesDashboard.mockRejectedValue({ response: { status: 500, data: { detail: "Mongo tidak ada" } } });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  const card = view.host.querySelector("[data-testid='widget-omzet']");
  expect(card.getAttribute("data-status")).toBe("error");
  expect(card.textContent).toContain("Gagal memuat");
  expect(card.textContent).not.toContain("48");
  expect(view.host.textContent).not.toContain("DATA CONTOH");
  view.unmount();
});

test("a stale period response does not replace the later one", async () => {
  let first;
  let second;
  const firstGate = new Promise((resolve) => { first = resolve; });
  const secondGate = new Promise((resolve) => { second = resolve; });
  api.salesDashboard
    .mockImplementationOnce(() => firstGate)
    .mockImplementationOnce(() => secondGate);
  const view = render();
  await act(async () => { await flush(); });
  global.__widgetLocation = { pathname: "/dashboard/widget", search: "?tab=penjualan&panel=ringkasan&preset=custom&start=2026-10-02&end=2026-10-02" };
  await act(async () => { global.__rerenderWidget(); await flush(); });
  await act(async () => {
    second({
      omzet: { status: "complete", amount: 1000, invoice_count: 1 },
      settlement: { status: "empty" },
      trend: { status: "empty", points: [] },
      sales: { status: "empty", groups: [] },
      products: { status: "empty", items: [], all_items: [] },
      receivables: { status: "zero", aging: [], unbucketed_outstanding: 0 },
    });
    await flush();
  });
  const amount = view.host.querySelector("[data-testid='omzet-amount']");
  expect(amount.querySelector("span").getAttribute("title")).toBe("Rp 1.000");
  await act(async () => {
    first({
      omzet: { status: "complete", amount: 999999, invoice_count: 9 },
      settlement: { status: "empty" },
      trend: { status: "empty", points: [] },
      sales: { status: "empty", groups: [] },
      products: { status: "empty", items: [], all_items: [] },
      receivables: { status: "zero", aging: [], unbucketed_outstanding: 0 },
    });
    await flush();
  });
  expect(view.host.querySelector("[data-testid='omzet-amount'] span").getAttribute("title")).toBe("Rp 1.000");
  view.unmount();
});

test("penjualan is the primary widget route and widget is a replace alias", () => {
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const shell = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const hook = fs.readFileSync(path.join(__dirname, "../hooks/useGlobalWorkspaceTabs.js"), "utf8");
  expect(TABS).toHaveLength(3);
  expect(TABS.flatMap((tab) => tab.panels)).toHaveLength(5);
  expect(WIDGET_IDS).toHaveLength(20);
  expect(app).toContain('path="/dashboard/penjualan" element={<WidgetDashboardGate><WidgetDashboard /></WidgetDashboardGate>}');
  expect(app).toContain('path="/dashboard/widget" element={<WidgetDashboardAlias />}');
  expect(app).toContain("<Navigate to={widgetDashboardAliasTarget(location)} replace />");
  expect(app).toContain('path="/dashboard" element={<PermGate anyOf={[NAV_KEYS.dashboard]}><HoPDashboard /></PermGate>}');
  expect(app).not.toContain("<SalesFinanceDashboard />");
  expect(app).toContain("WidgetDashboardGate");
  expect(app).toContain("canOpenWidgetDashboard");
  expect(shell).toContain("dashboardMenuItems(hasPerm, user)");
  expect(shell).not.toContain('to: "/dashboard/widget"');
  expect(hook).toContain("user?.role");
});

test("delivery schedule access follows the endpoint contract", () => {
  const has = (keys) => (key) => keys.includes(key);
  expect(widgetAllowed("shipLate", { role: "admin" }, has([]))).toBe(true);
  expect(widgetAllowed("shipNext", { role: "admin" }, has([]))).toBe(true);
  expect(widgetAllowed("shipToday", { role: "admin" }, has([]))).toBe(false);
  expect(widgetAllowed("production", { role: "admin" }, has([]))).toBe(false);
  expect(widgetAllowed("omzet", { role: "admin" }, has([]))).toBe(false);
  expect(widgetAllowed("cash", { role: "admin" }, has([]))).toBe(false);
  expect(canOpenWidgetDashboard({ role: "admin" }, has([]))).toBe(true);
  ["delivery.schedule.view", "schedule.view", "nav.delivery_schedule"].forEach((key) => {
    expect(widgetAllowed("shipLate", { role: "sales" }, has([key]))).toBe(true);
    expect(widgetAllowed("shipNext", { role: "sales" }, has([key]))).toBe(true);
    expect(canOpenWidgetDashboard({ role: "sales" }, has([key]))).toBe(true);
  });
  expect(widgetAllowed("shipLate", { role: "sales" }, has(["po.view"]))).toBe(false);
  expect(widgetAllowed("omzet", { role: "sales" }, has(["delivery.schedule.view"]))).toBe(false);
  expect(widgetAllowed("cash", { role: "sales" }, has(["delivery.schedule.view"]))).toBe(false);
  expect(widgetAllowed("pl", { role: "sales" }, has(["delivery.schedule.view"]))).toBe(false);
  expect(widgetAllowed("shipToday", { role: "sales" }, has(["po.view"]))).toBe(true);
  expect(widgetAllowed("production", { role: "sales" }, has(["po.view"]))).toBe(false);
  expect(widgetAllowed("production", { role: "sales" }, has(["schedule.view"]))).toBe(true);
  expect(widgetAllowed("production", { role: "admin" }, has(["schedule.view"]))).toBe(true);
  expect(canOpenWidgetDashboard({ role: "sales" }, has([]))).toBe(false);
});

test("admin without delivery keys still loads the schedule widgets", async () => {
  global.__widgetRole = "admin";
  global.__widgetPerms = [];
  global.__widgetLocation = { pathname: "/dashboard/widget", search: "?tab=operasi&panel=jadwal" };
  api.deliverySchedule.mockResolvedValue({ rows: [] });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(view.host.querySelector('[role="tab"][aria-selected="true"]').textContent).toBe("Operasi");
  expect(api.deliverySchedule).toHaveBeenCalledTimes(1);
  expect(api.dashboardToday).not.toHaveBeenCalled();
  expect(api.productionDashboard).not.toHaveBeenCalled();
  expect(api.salesDashboard).not.toHaveBeenCalled();
  expect(api.financialDashboard).not.toHaveBeenCalled();
  expect(api.widgetProfitLoss).not.toHaveBeenCalled();
  expect(view.host.querySelector("[data-testid='widget-shipLate']").getAttribute("data-status")).toBe("empty");
  expect(view.host.querySelector("[data-testid='widget-shipNext']").getAttribute("data-status")).toBe("empty");
  expect(view.host.querySelector("[data-testid='widget-shipToday']").getAttribute("data-status")).toBe("forbidden");
  expect(view.host.querySelector("[data-testid='widget-production']").getAttribute("data-status")).toBe("forbidden");
  view.unmount();
});

test("each delivery permission loads schedule without sales or finance", async () => {
  for (const key of ["delivery.schedule.view", "schedule.view", "nav.delivery_schedule"]) {
    api.deliverySchedule.mockClear();
    api.productionDashboard.mockClear();
    api.salesDashboard.mockClear();
    api.financialDashboard.mockClear();
    api.dashboardToday.mockClear();
    global.__widgetRole = "sales";
    global.__widgetPerms = [key];
    global.__widgetLocation = { pathname: "/dashboard/widget", search: "?tab=operasi&panel=jadwal" };
    api.deliverySchedule.mockResolvedValue({ rows: [] });
    api.productionDashboard.mockResolvedValue({ rows: [] });
    const view = render();
    await act(async () => { await flush(); await flush(); });
    expect(api.deliverySchedule).toHaveBeenCalledTimes(1);
    expect(api.salesDashboard).not.toHaveBeenCalled();
    expect(api.financialDashboard).not.toHaveBeenCalled();
    expect(api.dashboardToday).not.toHaveBeenCalled();
    if (key === "schedule.view") expect(api.productionDashboard).toHaveBeenCalled();
    else expect(api.productionDashboard).not.toHaveBeenCalled();
    view.unmount();
  }
});

test("a user without delivery access does not call the schedule endpoint", async () => {
  global.__widgetRole = "sales";
  global.__widgetPerms = ["po.view"];
  global.__widgetLocation = { pathname: "/dashboard/widget", search: "?tab=operasi&panel=jadwal" };
  api.dashboardToday.mockResolvedValue({ shipments_today: { total: 0, preview: [], date: "2026-10-04" } });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(api.deliverySchedule).not.toHaveBeenCalled();
  expect(api.dashboardToday).toHaveBeenCalledTimes(1);
  expect(api.productionDashboard).not.toHaveBeenCalled();
  expect(view.host.querySelector("[data-testid='forbidden-shipLate']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='forbidden-shipNext']")).toBeTruthy();
  view.unmount();
});

test("schedule permission alternatives load delivery and sales share is not invented", async () => {
  global.__widgetPerms = ["nav.delivery_schedule"];
  global.__widgetLocation = { pathname: "/dashboard/widget", search: "?tab=operasi&panel=jadwal" };
  api.deliverySchedule.mockResolvedValue({ rows: [] });
  api.dashboardToday.mockResolvedValue({ shipments_today: { total: 0, preview: [] } });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(api.deliverySchedule).toHaveBeenCalled();
  expect(api.dashboardToday).not.toHaveBeenCalled();
  expect(view.host.querySelector("[data-testid='forbidden-shipToday']").textContent).toContain("po.view");
  view.unmount();
});

test("an empty profit period keeps the year-to-date control", async () => {
  global.__widgetPerms = ["profit_loss.view"];
  global.__widgetLocation = { pathname: "/dashboard/widget", search: "?tab=keuangan&panel=ringkasan&preset=custom&start=2026-09-01&end=2026-09-30" };
  api.widgetProfitLoss.mockImplementation(async (params) => {
    if (params.start_date === "2026-01-01") {
      return { status: "complete", revenue: 30000, cogs: 1000, operating_expense: 8000, other_income: 0, other_expense: 2000, net_profit: 19000 };
    }
    return { status: "empty", detail: "Belum ada data." };
  });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  const toggle = view.host.querySelector("[data-testid='pl-ytd']");
  expect(toggle).toBeTruthy();
  expect(view.host.querySelector("[data-testid='widget-pl']").textContent).toContain("Belum ada data");
  await act(async () => { toggle.click(); await flush(); });
  expect(view.host.querySelector("[data-testid='widget-pl']").textContent).toContain("30");
  view.unmount();
});

test("incomplete sales contribution is not shown as 100 percent", async () => {
  api.salesDashboard.mockResolvedValue({
    omzet: { status: "complete", amount: 7300, invoice_count: 3 },
    settlement: { status: "complete", settled_grand_total: 0, settled_count: 0, open_grand_total: 7700, open_outstanding: 6700, open_count: 3 },
    trend: { status: "complete", points: [] },
    sales: { status: "incomplete", total_omzet: 7300, groups: [{ label: "Belum ditetapkan", omzet: 7300, invoice_count: 3, share: null }], detail: "Belum lengkap." },
    products: { status: "complete", items: [], all_items: [{ key: "a", label: "Utama", amount: 4500, share: 78.9 }, { key: "b", label: "Kecil", amount: 1200, share: 21.1 }] },
    receivables: { status: "zero", aging: [], unbucketed_outstanding: 0 },
  });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  const sales = view.host.querySelector("[data-testid='widget-sales']").textContent;
  expect(sales).toContain("Belum ditetapkan");
  expect(sales).not.toContain("100%");
  expect(sales).toContain("—");
  const products = view.host.querySelector("[data-testid='widget-products']").textContent;
  expect(products).toContain("78,9%");
  const settlement = view.host.querySelector("[data-testid='widget-settlement']").textContent;
  expect(settlement).toContain("7,7");
  expect(settlement).not.toContain("6,7");
  view.unmount();
});

function selectedSection() {
  return document.querySelector('[aria-label="Bagian dashboard"] [role="tab"][aria-selected="true"]')?.textContent;
}

test("menu entry for operations skips the forbidden sales panel and does not fetch it", async () => {
  global.__widgetRole = "ops";
  global.__widgetPerms = ["po.view"];
  global.__widgetLocation = { pathname: "/dashboard/penjualan", search: "", hash: "" };
  api.dashboardToday.mockResolvedValue({ shipments_today: { total: 0, preview: [], date: "2026-10-05" } });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(selectedSection()).toBe("Operasi");
  expect(api.dashboardToday).toHaveBeenCalledTimes(1);
  expect(api.salesDashboard).not.toHaveBeenCalled();
  expect(api.deliverySchedule).not.toHaveBeenCalled();
  expect(api.financialDashboard).not.toHaveBeenCalled();
  expect(view.host.querySelector("[data-testid='widget-shipToday']").getAttribute("data-status")).not.toBe("forbidden");
  expect(view.host.querySelector("[data-testid='forbidden-shipLate']").textContent).not.toMatch(/Rp\s*0/);
  view.unmount();
});

test("an explicit forbidden tab does not fetch and does not fall back to a zero", async () => {
  global.__widgetRole = "ops";
  global.__widgetPerms = ["po.view"];
  global.__widgetLocation = { pathname: "/dashboard/penjualan", search: "?tab=penjualan", hash: "" };
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(selectedSection()).toBe("Penjualan");
  expect(api.salesDashboard).not.toHaveBeenCalled();
  expect(api.dashboardToday).not.toHaveBeenCalled();
  expect(view.host.querySelector("[data-testid='widget-omzet']").getAttribute("data-status")).toBe("forbidden");
  expect(view.host.querySelector("[data-testid='widget-omzet']").textContent).not.toMatch(/Rp\s*0/);
  view.unmount();
});

test("legacy keuangan hash opens finance and refresh keeps it", async () => {
  global.__widgetRole = "finance";
  global.__widgetPerms = ["dashboard.finance.view"];
  global.__widgetLocation = { pathname: "/dashboard/penjualan", search: "", hash: "#keuangan" };
  api.financialDashboard.mockResolvedValue({ cash_movement: { status: "empty" }, expense_composition: { status: "empty" } });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(selectedSection()).toBe("Keuangan");
  expect(api.financialDashboard).toHaveBeenCalled();
  expect(api.salesDashboard).not.toHaveBeenCalled();
  await act(async () => { global.__rerenderWidget(); await flush(); });
  expect(selectedSection()).toBe("Keuangan");
  view.unmount();
});

test("legacy penjualan hash for an admin opens O2 and O3 only", async () => {
  global.__widgetRole = "admin";
  global.__widgetPerms = [];
  global.__widgetLocation = { pathname: "/dashboard/penjualan", search: "", hash: "#penjualan" };
  api.deliverySchedule.mockResolvedValue({ rows: [] });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(selectedSection()).toBe("Operasi");
  expect(api.deliverySchedule).toHaveBeenCalledTimes(1);
  expect(api.dashboardToday).not.toHaveBeenCalled();
  expect(api.productionDashboard).not.toHaveBeenCalled();
  expect(api.salesDashboard).not.toHaveBeenCalled();
  expect(view.host.querySelector("[data-testid='widget-production']").getAttribute("data-status")).toBe("forbidden");
  view.unmount();
});

test("tab changes push history and back returns to the legacy hash", async () => {
  global.__widgetRole = "sales";
  global.__widgetPerms = ["dashboard.sales.view", "dashboard.finance.view"];
  global.__widgetLocation = { pathname: "/dashboard/penjualan", search: "", hash: "#keuangan" };
  api.financialDashboard.mockResolvedValue({ cash_movement: { status: "empty" }, expense_composition: { status: "empty" } });
  api.salesDashboard.mockResolvedValue({
    omzet: { status: "empty" },
    settlement: { status: "empty" },
    trend: { status: "empty", points: [] },
    sales: { status: "empty", groups: [] },
    products: { status: "empty", items: [], all_items: [] },
    receivables: { status: "empty", aging: [] },
  });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(selectedSection()).toBe("Keuangan");
  const penjualan = view.host.querySelector('[aria-label="Bagian dashboard"] [role="tab"]');
  await act(async () => { penjualan.click(); });
  expect(global.__widgetNavigate).toHaveBeenCalledWith({
    pathname: "/dashboard/penjualan",
    search: "?tab=penjualan&panel=ringkasan&widget=omzet",
    hash: "#penjualan",
  }, { replace: false });
  global.__widgetLocation = { pathname: "/dashboard/penjualan", search: "", hash: "#keuangan" };
  await act(async () => { global.__rerenderWidget(); await flush(); });
  expect(selectedSection()).toBe("Keuangan");
  view.unmount();
});

test("a user without widget permission is not treated as able to open the dashboard", () => {
  expect(canOpenWidgetDashboard({ role: "sales" }, () => false)).toBe(false);
  expect(canOpenWidgetDashboard({ role: "finance" }, () => false)).toBe(false);
  expect(canOpenWidgetDashboard({ role: "ops" }, () => false)).toBe(false);
  expect(canOpenWidgetDashboard({ role: "admin" }, () => false)).toBe(true);
});
