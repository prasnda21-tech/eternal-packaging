import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import api from "../lib/api";
import SalesFinanceDashboard from "./SalesFinanceDashboard";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={typeof to === "string" ? to : "#"} {...props}>{children}</a>,
  Navigate: ({ to }) => <div data-testid="access-denied" data-to={to} />,
  useNavigate: () => (to) => { global.__dashboardHash = to.hash; },
  useLocation: () => ({ hash: global.__dashboardHash || "", pathname: "/dashboard/penjualan" }),
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: global.__dashboardUser || null,
    hasPerm: (key) => (global.__dashboardPerms || []).includes(key),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesDashboard: jest.fn(),
    financialDashboard: jest.fn(),
  },
}));

jest.mock("recharts", () => {
  const React = require("react");
  const Pass = ({ children }) => <div>{children}</div>;
  return {
    ResponsiveContainer: Pass,
    LineChart: Pass,
    AreaChart: Pass,
    BarChart: Pass,
    PieChart: Pass,
    Pie: () => null,
    Cell: () => null,
    Line: () => null,
    Area: () => null,
    Bar: () => null,
    XAxis: () => null,
    YAxis: () => null,
    CartesianGrid: () => null,
    Legend: () => null,
    Tooltip: () => null,
  };
});

global.IS_REACT_ACT_ENVIRONMENT = true;

const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

function salesPayload() {
  return {
    loaded_at: "2026-09-26T10:00:00Z",
    omzet: { status: "complete", amount: 4000, grand_total: 4400, basis_note: "Setelah diskon barang, sebelum PPN dan biaya lainnya.", comparison: { available: false } },
    trend: { status: "empty", points: [], detail: "Belum ada faktur pada periode ini" },
    settlement: { status: "complete", settled_count: 1, settled_grand_total: 4400, open_count: 0, open_outstanding: 0, points: [] },
    sales: { status: "empty", groups: [], detail: "Belum ada faktur pada periode ini" },
    customers: { status: "empty", detail: "Belum ada faktur pada periode ini", percents_available: false },
    products: { status: "empty", items: [], detail: "Belum ada faktur pada periode ini" },
    receivables: { status: "zero", outstanding: 0, open_invoice_count: 0, customer_count: 0, aging: [] },
    orders: { status: "empty", total: 0, buckets: [], detail: "Belum ada pesanan" },
  };
}

function financePayload() {
  return {
    loaded_at: "2026-09-26T10:00:00Z",
    end_date: "2026-09-26",
    performance: { status: "complete", revenue: 9000, gross_profit: 1000, gross_margin: 0.1, net_profit: 500, net_margin: 0.05 },
    position: {
      cash_and_bank: { status: "complete", amount: 2000 },
      accounts_receivable: { status: "complete", amount: 800 },
      accounts_payable: { status: "zero", amount: 0 },
      inventory_book: { status: "unavailable", amount: null },
      grni: { status: "unavailable", amount: null },
    },
    cash_movement: { status: "zero", cash_in: 0, cash_out: 0, net_change: 0, accounts: [] },
    charts: {
      trend: { status: "zero", points: [] },
      expense_composition: { status: "empty", items: [] },
      aging: { receivable: { status: "zero" }, payable: { status: "zero" } },
    },
    completeness: { status: "complete" },
    controls: { items: [] },
    receivables: { status: "zero", items: [], item_total: 0, total_outstanding: 0, gl_balance: 0 },
    payables: { status: "zero", items: [], item_total: 0, total_outstanding: 0, gl_balance: 0 },
  };
}

describe("merged dashboard route", () => {
  test("keeps the finance bookmark as a redirect and opens the combined page for either permission", () => {
    expect(appSrc).toContain('path="/dashboard/keuangan"');
    expect(appSrc).toContain('to="/dashboard/penjualan#keuangan"');
    expect(appSrc).toContain('anyOf={["dashboard.finance.view", NAV_KEYS.dashboardFinance]}><Navigate to="/dashboard/penjualan#keuangan"');
    expect(appSrc).toContain('path="/dashboard/penjualan"');
    expect(appSrc).toContain('"dashboard.sales.view", NAV_KEYS.dashboardSales, "dashboard.finance.view", NAV_KEYS.dashboardFinance');
    expect(appSrc).not.toContain("<FinancialDashboard />");
    expect(appSrc).not.toContain("<SalesDashboard />");
  });
});

describe("merged dashboard permissions", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.salesDashboard.mockReset();
    api.financialDashboard.mockReset();
    api.salesDashboard.mockResolvedValue(salesPayload());
    api.financialDashboard.mockResolvedValue(financePayload());
    global.__dashboardHash = "";
    global.__dashboardPerms = [];
    global.__dashboardUser = null;
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const flush = async () => {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  };

  test("sales only loads the sales endpoint and hides finance", async () => {
    global.__dashboardPerms = ["dashboard.sales.view"];
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    expect(container.querySelector('[data-testid="section-penjualan"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="section-keuangan"]')).toBeNull();
    expect(container.querySelector('[data-testid="sales-finance-distinction"]')).toBeNull();
    expect(api.salesDashboard).toHaveBeenCalled();
    expect(api.financialDashboard).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="sales-omzet-value"]').textContent).toMatch(/4\.000|4,000/);
  });

  test("finance only loads the finance endpoint and hides sales", async () => {
    global.__dashboardPerms = ["dashboard.finance.view"];
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    expect(container.querySelector('[data-testid="section-keuangan"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="section-penjualan"]')).toBeNull();
    expect(api.financialDashboard).toHaveBeenCalled();
    expect(api.salesDashboard).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="finance-revenue-value"]').textContent).toMatch(/9\.000|9,000/);
    expect(container.querySelector('[data-testid="finance-chart-trend"] .fd-plot').classList.contains("is-series")).toBe(true);
    expect(container.querySelector('[data-testid="finance-chart-expense"] .fd-plot').classList.contains("is-message")).toBe(true);
  });

  test("both permissions keep independent sections when one source fails", async () => {
    global.__dashboardPerms = ["dashboard.sales.view", "nav.dashboard.finance"];
    api.salesDashboard.mockRejectedValue({ response: { data: { detail: "penjualan gagal" } } });
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    expect(container.querySelector('[data-testid="sales-finance-distinction"]').textContent).toMatch(/tidak dijumlahkan/);
    expect(container.querySelector('[data-testid="sales-error"]').textContent).toMatch(/penjualan gagal/);
    expect(container.querySelector('[data-testid="section-keuangan"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="finance-revenue-value"]').textContent).toMatch(/9\.000|9,000/);
    expect(container.querySelector('[data-testid="finance-error"]')).toBeNull();
  });

  test("neither permission uses the existing access redirect", async () => {
    global.__dashboardPerms = ["nav.sales"];
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    expect(container.querySelector('[data-testid="access-denied"]').getAttribute("data-to")).toBe("/");
    expect(api.salesDashboard).not.toHaveBeenCalled();
    expect(api.financialDashboard).not.toHaveBeenCalled();
  });

  test("finance bookmark hash targets the finance section", async () => {
    global.__dashboardPerms = ["dashboard.finance.view"];
    global.__dashboardHash = "#keuangan";
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    expect(container.querySelector("#dashboard-panel-keuangan").hidden).toBe(false);
    expect(container.querySelector("#dashboard-tab-keuangan").getAttribute("aria-selected")).toBe("true");
  });
  test("tab changes preserve the period, endpoint results and keyboard focus", async () => {
    global.__dashboardPerms = ["dashboard.sales.view", "dashboard.finance.view"];
    await act(async () => root.render(<SalesFinanceDashboard />));
    await flush();
    await act(async () => container.querySelector('[data-testid="sales-preset-year"]').click());
    await flush();
    const calls = api.salesDashboard.mock.calls.length;
    const salesTab = container.querySelector('#dashboard-tab-penjualan');
    salesTab.focus();
    await act(async () => salesTab.dispatchEvent(new KeyboardEvent('keydown', {key:'ArrowRight',bubbles:true})));
    await act(async () => root.render(<SalesFinanceDashboard />));
    expect(global.__dashboardHash).toBe('#keuangan');
    expect(container.querySelector('[data-testid="section-penjualan"]').hidden).toBe(true);
    expect(container.querySelector('[data-testid="section-keuangan"]').hidden).toBe(false);
    expect(document.activeElement.id).toBe('dashboard-tab-keuangan');
    await act(async () => salesTab.click());
    await act(async () => root.render(<SalesFinanceDashboard />));
    expect(container.querySelector('[data-testid="sales-dashboard-page"]').dataset.preset).toBe('year');
    expect(api.salesDashboard.mock.calls.length).toBe(calls);
  });
  test("unauthorized finance bookmark falls back to sales", async () => {
    global.__dashboardPerms = ["dashboard.sales.view"];
    global.__dashboardHash = '#keuangan';
    await act(async () => root.render(<SalesFinanceDashboard />));
    await flush();
    expect(container.querySelector('[data-testid="section-penjualan"]').hidden).toBe(false);
    expect(container.querySelector('#dashboard-tab-keuangan')).toBeNull();
    expect(api.financialDashboard).not.toHaveBeenCalled();
  });

  test("sends one date range to both sources and keeps finance free of the sales preset", async () => {
    global.__dashboardPerms = ["dashboard.sales.view", "dashboard.finance.view"];
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    const salesCall = api.salesDashboard.mock.calls.at(-1)[0];
    const financeCall = api.financialDashboard.mock.calls.at(-1)[0];
    expect(salesCall.start_date).toBe(financeCall.start_date);
    expect(salesCall.end_date).toBe(financeCall.end_date);
    expect(financeCall.preset).toBeUndefined();
    expect(Object.keys(financeCall).sort()).toEqual(["end_date", "start_date"]);
    await act(async () => container.querySelector('[data-testid="sales-preset-year"]').click());
    await flush();
    const nextSales = api.salesDashboard.mock.calls.at(-1)[0];
    const nextFinance = api.financialDashboard.mock.calls.at(-1)[0];
    expect(nextSales.start_date).toBe(nextFinance.start_date);
    expect(nextSales.end_date).toBe(nextFinance.end_date);
    expect(nextSales.preset).toBe("year");
    expect(nextFinance.preset).toBeUndefined();
    expect(nextSales.start_date.endsWith("-01-01")).toBe(true);
  });

  test("sales permission omits finance figures and finance permission omits sales figures", async () => {
    global.__dashboardPerms = ["dashboard.sales.view"];
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    expect(container.querySelector('[data-testid="kpi-laba"]')).toBeNull();
    expect(container.querySelector('[data-testid="kpi-kas"]')).toBeNull();
    expect(container.querySelector('[data-testid="panel-laba-rugi"]')).toBeNull();
    expect(container.textContent).not.toMatch(/9[.,]000|2[.,]000/);

    global.__dashboardPerms = ["dashboard.finance.view"];
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    expect(container.querySelector('[data-testid="sales-omzet"]')).toBeNull();
    expect(container.querySelector('[data-testid="kpi-piutang"]')).toBeNull();
    expect(container.querySelector('[data-testid="finance-revenue-value"]').textContent).toMatch(/9[.,]000/);
  });

  test("labels personal sales beside company finance without mixing a margin", async () => {
    global.__dashboardPerms = ["dashboard.sales.view", "dashboard.finance.view"];
    global.__dashboardUser = { role: "sales_eternal" };
    api.salesDashboard.mockResolvedValue({
      ...salesPayload(),
      as_of_date: "2026-10-03",
      omzet: { status: "complete", amount: 4000, comparison: { available: false } },
    });
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    expect(container.querySelector('[data-testid="scope-penjualan"]').textContent).toBe("Penjualan saya");
    expect(container.querySelector('[data-testid="scope-keuangan"]').textContent).toBe("Keuangan perusahaan");
    expect(container.querySelector('[data-testid="sales-piutang-scope"]').textContent).toMatch(/^Penjualan saya · Posisi saat ini/);
    expect(container.querySelector('[data-testid="sales-kas-scope"]').textContent).toMatch(/^Keuangan perusahaan · Per /);
    expect(container.querySelector('[data-testid="kpi-laba-value"]').textContent).not.toMatch(/%/);
    expect(container.querySelector('[data-testid="panel-laba-rugi"]').textContent).not.toMatch(/%/);
  });

  test("current receivables and cash as of the period end use different labels", async () => {
    global.__dashboardPerms = ["dashboard.sales.view", "dashboard.finance.view"];
    api.salesDashboard.mockResolvedValue({ ...salesPayload(), as_of_date: "2026-10-03" });
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    const piutang = container.querySelector('[data-testid="sales-piutang-scope"]').textContent;
    const kas = container.querySelector('[data-testid="sales-kas-scope"]').textContent;
    expect(piutang).toBe("Posisi saat ini · 2026-10-03");
    expect(kas).toBe("Per 2026-09-26");
    expect(piutang).not.toBe(kas);
    expect(container.querySelector('[data-testid="sales-receivables-scope"]').textContent).toBe("Posisi saat ini · 2026-10-03");
  });

  test("keeps a failed reload from presenting the previous payload as current", async () => {
    global.__dashboardPerms = ["dashboard.sales.view", "dashboard.finance.view"];
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    expect(container.querySelector('[data-testid="sales-omzet-value"]').textContent).toMatch(/4[.,]000/);
    expect(container.querySelector('[data-testid="finance-revenue-value"]').textContent).toMatch(/9[.,]000/);
    api.salesDashboard.mockRejectedValue({ response: { data: { detail: "penjualan gagal ulang" } } });
    api.financialDashboard.mockRejectedValue({ response: { data: { detail: "keuangan gagal ulang" } } });
    await act(async () => container.querySelector('[data-testid="sales-reload"]').click());
    await flush();
    expect(container.querySelector('[data-testid="sales-error"]').textContent).toMatch(/penjualan gagal ulang/);
    expect(container.querySelector('[data-testid="sales-omzet-value"]').textContent).not.toMatch(/4[.,]000/);
    expect(container.querySelector('[data-testid="finance-error"]').textContent).toMatch(/keuangan gagal ulang/);
    expect(container.querySelector('[data-testid="finance-revenue-value"]').textContent).not.toMatch(/9[.,]000/);
  });

  test("drops a stale response after a faster period change", async () => {
    global.__dashboardPerms = ["dashboard.sales.view", "dashboard.finance.view"];
    let firstSales;
    let secondSales;
    let salesCalls = 0;
    api.salesDashboard.mockImplementation(() => new Promise((resolve) => {
      salesCalls += 1;
      if (salesCalls === 1) firstSales = resolve;
      else secondSales = resolve;
    }));
    api.financialDashboard.mockResolvedValue(financePayload());
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await act(async () => container.querySelector('[data-testid="sales-preset-today"]').click());
    await act(async () => {
      secondSales({
        ...salesPayload(),
        omzet: { status: "complete", amount: 9000, comparison: { available: false } },
      });
    });
    await flush();
    await act(async () => { firstSales(salesPayload()); });
    await flush();
    expect(container.querySelector('[data-testid="sales-omzet-value"]').textContent).toMatch(/9[.,]000/);
    expect(container.querySelector('[data-testid="sales-omzet-value"]').textContent).not.toMatch(/4[.,]000/);
    expect(api.salesDashboard.mock.calls.at(-1)[0].preset).toBe("today");
  });

  test("explains other results and keeps negative profit-loss amounts signed", async () => {
    global.__dashboardPerms = ["dashboard.sales.view", "dashboard.finance.view"];
    api.financialDashboard.mockResolvedValue({
      ...financePayload(),
      performance: {
        status: "complete",
        revenue: 1000,
        cogs: 400,
        operating_expense: -50,
        other_income: 25,
        other_expense: -10,
        net_profit: 585,
      },
    });
    api.salesDashboard.mockResolvedValue({
      ...salesPayload(),
      products: {
        status: "incomplete",
        items: [{ key: "master:1", label: "Karton", amount: 4000 }],
        detail: "Satu baris tanpa identitas barang tidak masuk peringkat.",
      },
      receivables: {
        status: "incomplete",
        outstanding: 1500,
        aging: [{ key: "NOT_DUE", label: "Belum jatuh tempo", amount: 1000 }],
        unbucketed_outstanding: 500,
      },
      trend: {
        status: "complete",
        grain_label: "Harian",
        points: [
          { key: "2026-09-01", label: "1 Sep", omzet: 1000, invoice_count: 1 },
          { key: "2026-09-02", label: "2 Sep", omzet: 0, invoice_count: 0 },
        ],
      },
    });
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    expect(container.querySelector('[data-testid="pl-chart"]').getAttribute("data-amounts")).toBe("1000|400|-50");
    expect(container.querySelector('[data-testid="pl-other_income"]').textContent).toMatch(/Pendapatan lain/);
    expect(container.querySelector('[data-testid="pl-other_income"]').textContent).toMatch(/25/);
    expect(container.querySelector('[data-testid="pl-other_expense"]').textContent).toMatch(/Beban lain/);
    expect(container.querySelector('[data-testid="pl-other_expense"]').textContent).toMatch(/-10|−10/);
    expect(container.querySelector('[data-testid="kpi-laba-value"]').textContent).toMatch(/585/);
    expect(container.querySelector('[data-testid="sales-products"]').textContent).toMatch(/Nilai sebelum diskon faktur/);
    expect(container.querySelector('[data-testid="sales-products"]').textContent).toMatch(/Satu baris tanpa identitas/);
    expect(container.querySelector('[data-testid="sales-ar-unbucketed"]').textContent).toMatch(/Tanggal tidak tersedia/);
    expect(container.querySelector('[data-testid="sales-ar-unbucketed"]').textContent).toMatch(/500/);
    expect(container.querySelector('[data-testid="sales-trend"]').textContent).toMatch(/harian/i);
    expect(container.querySelector('[data-testid="sales-trend"]').textContent).not.toMatch(/Januari|Desember/);
  });

  test("null, empty, and error stay distinct from a recorded zero", async () => {
    global.__dashboardPerms = ["dashboard.sales.view", "dashboard.finance.view"];
    api.salesDashboard.mockResolvedValue({
      ...salesPayload(),
      omzet: { status: "complete", amount: null, comparison: { available: false } },
      receivables: { status: "empty", outstanding: null, aging: [], detail: "Belum ada saldo piutang" },
    });
    api.financialDashboard.mockResolvedValue({
      ...financePayload(),
      performance: { status: "complete", revenue: 1000, cogs: 0, operating_expense: 0, other_income: 0, other_expense: 0, net_profit: null },
      position: { ...financePayload().position, cash_and_bank: { status: "unavailable", amount: null } },
    });
    await act(async () => { root.render(<SalesFinanceDashboard />); });
    await flush();
    expect(container.querySelector('[data-testid="sales-omzet-value"]').textContent).toBe("—");
    expect(container.querySelector('[data-testid="kpi-piutang-value"]').textContent).toBe("Belum ada saldo piutang");
    expect(container.querySelector('[data-testid="kpi-laba-value"]').textContent).toBe("—");
    expect(container.querySelector('[data-testid="kpi-kas-value"]').textContent).toBe("Belum tersedia");
    expect(container.querySelector('[data-testid="pl-other"]')).toBeNull();

    api.salesDashboard.mockResolvedValue({
      ...salesPayload(),
      omzet: { status: "zero", amount: 0, invoice_count: 0, comparison: { available: false } },
    });
    api.financialDashboard.mockResolvedValue({
      ...financePayload(),
      performance: { status: "zero", revenue: 0, cogs: 0, operating_expense: 0, net_profit: 0 },
      position: { ...financePayload().position, cash_and_bank: { status: "zero", amount: 0 } },
    });
    await act(async () => container.querySelector('[data-testid="sales-reload"]').click());
    await flush();
    expect(container.querySelector('[data-testid="sales-omzet-value"]').textContent).toMatch(/^Rp\s/);
    expect(container.querySelector('[data-testid="kpi-laba-value"]').textContent).toMatch(/^Rp\s/);
    expect(container.querySelector('[data-testid="kpi-kas-value"]').textContent).toMatch(/^Rp\s/);
  });

});

