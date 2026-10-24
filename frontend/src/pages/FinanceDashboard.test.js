import { act } from "react";
import { createRoot } from "react-dom/client";
import FinanceDashboard from "./FinanceDashboard";
import api from "../lib/api";
import { layoutOverlaps } from "../components/financeDashboard/financeWidgetLayout";

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { role: "finance" },
    hasPerm: (key) => (global.__financePerms || []).includes(key),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    financeDashboardLayout: jest.fn(),
    saveFinanceDashboardLayout: jest.fn(),
    resetFinanceDashboardLayout: jest.fn(),
    salesDashboard: jest.fn(),
    financialDashboard: jest.fn(),
    purchasePeriodSummary: jest.fn(),
    widgetProfitLoss: jest.fn(),
    widgetReconciliations: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

if (typeof PointerEvent === "undefined") {
  global.PointerEvent = class PointerEvent extends MouseEvent {};
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function cashWidget(extra = {}) {
  return { widget_id: "cash", x: 0, y: 0, w: 4, h: 3, settings: { period_mode: "global" }, ...extra };
}

function expenseWidget() {
  return { widget_id: "expense", x: 4, y: 0, w: 4, h: 3, settings: { period_mode: "global", visual: "list" } };
}

function layout(widgets, version = 1) {
  return { version, source: version ? "saved" : "default", widgets, ignored: [], withheld: [] };
}

async function renderPage() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<FinanceDashboard />); });
  await act(async () => { await flush(); await flush(); });
  return {
    host,
    unmount() {
      act(() => root.unmount());
      host.remove();
    },
  };
}

beforeEach(() => {
  global.__financePerms = ["dashboard.finance.view", "dashboard.sales.view"];
  api.financeDashboardLayout.mockReset();
  api.saveFinanceDashboardLayout.mockReset();
  api.resetFinanceDashboardLayout.mockReset();
  api.salesDashboard.mockReset();
  api.financialDashboard.mockReset();
  api.purchasePeriodSummary.mockReset();
  api.widgetProfitLoss.mockReset();
  api.widgetReconciliations.mockReset();
  api.financeDashboardLayout.mockResolvedValue(layout([cashWidget(), expenseWidget()]));
  api.saveFinanceDashboardLayout.mockImplementation(async (body) => ({ ...body, version: body.version + 1, source: "saved" }));
  api.financialDashboard.mockResolvedValue({
    cash_movement: { status: "complete", cash_in: 1500, cash_out: 400, net_change: 1100 },
    charts: { expense_composition: { status: "empty", items: [], detail: "Belum ada beban." } },
  });
  api.salesDashboard.mockResolvedValue({
    omzet: { status: "zero", amount: 0, invoice_count: 0 },
    products: { status: "empty", items: [], detail: "Belum ada faktur" },
    sales: { status: "incomplete", groups: [], detail: "Pemetaan belum lengkap" },
    trend: { status: "complete", grain_label: "Harian", points: [{ label: "1 Okt", omzet: 100 }] },
  });
});

test("default saved layout renders and does not save until the user edits", async () => {
  const view = await renderPage();
  expect(view.host.querySelector("[data-testid='finance-dashboard-page']").textContent).toContain("Dashboard Keuangan");
  expect(view.host.querySelector("[data-testid='finance-widget-cash']").textContent).toContain("Arus Kas");
  expect(view.host.querySelector("[data-testid='finance-widget-expense']").textContent).toContain("Belum ada data");
  expect(view.host.querySelector("[data-testid='finance-widget-expense']").textContent).not.toContain("Harga pokok tidak dimasukkan");
  expect(view.host.querySelector("[data-testid='finance-widget-expense']").textContent).not.toContain("Rp 0");
  expect(view.host.querySelector("[data-testid='finance-period-start']").value).toMatch(/^\d{2}\/\d{2}\/\d{4}$/);
  expect(view.host.querySelector("[data-testid='finance-period-end']").value).toMatch(/^\d{2}\/\d{2}\/\d{4}$/);
  await act(async () => { view.host.querySelector("[data-testid='finance-menu-cash']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='finance-info-cash']").click(); });
  expect(view.host.querySelector("[data-testid='finance-info-panel-cash']").textContent).toContain("Transfer antar rekening");
  expect(api.financialDashboard).toHaveBeenCalledTimes(1);
  expect(api.salesDashboard).not.toHaveBeenCalled();
  await act(async () => { await wait(700); });
  expect(api.saveFinanceDashboardLayout).not.toHaveBeenCalled();
  view.unmount();
});

test("add, keyboard move, resize, and remove stay free of overlap and save", async () => {
  const view = await renderPage();
  await act(async () => { view.host.querySelector("[data-testid='finance-edit-layout']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='finance-add-widget']").click(); });
  const pending = view.host.querySelector("[data-testid='finance-catalog-assets']");
  expect(pending.textContent).toContain("Belum tersedia");
  expect(pending.querySelector("button")).toBeNull();
  const addSales = view.host.querySelector("[data-testid='finance-catalog-sales'] button");
  expect(addSales.disabled).toBe(false);
  expect(addSales.getAttribute("aria-label")).toBe("Tambahkan Penjualan");
  await act(async () => { addSales.click(); });
  expect(view.host.querySelector("[data-testid='finance-widget-sales']")).toBeTruthy();

  const grid = view.host.querySelector("[data-testid='finance-grid']");
  Object.defineProperty(grid, "clientWidth", { configurable: true, value: 1200 });
  const handle = view.host.querySelector("[data-testid='finance-drag-cash']");
  await act(async () => {
    handle.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0, clientX: 0, clientY: 0 }));
    window.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 200, clientY: 0 }));
    window.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, clientX: 200, clientY: 0 }));
  });
  expect(view.host.querySelector("[data-testid='finance-grid-hint']")).toBeNull();

  await act(async () => { view.host.querySelector("[data-testid='finance-menu-cash']").click(); });
  await act(async () => {
    view.host.querySelector("[aria-label='Geser Arus Kas ke kanan']").click();
  });
  const resize = view.host.querySelector("[data-testid='finance-resize-cash']");
  await act(async () => {
    resize.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0, clientX: 0, clientY: 0 }));
    window.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 100, clientY: 78 }));
    window.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
  });
  await act(async () => { view.host.querySelector("[data-testid='finance-menu-expense']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='finance-remove-expense']").click(); });
  await act(async () => { await wait(700); });
  expect(api.saveFinanceDashboardLayout).toHaveBeenCalled();
  const saved = api.saveFinanceDashboardLayout.mock.calls.at(-1)[0];
  expect(saved.widgets.map((item) => item.widget_id)).toEqual(expect.arrayContaining(["cash", "sales"]));
  expect(saved.widgets.some((item) => item.widget_id === "expense")).toBe(false);
  expect(layoutOverlaps(saved.widgets)).toBe(false);
  expect(saved.widgets.find((item) => item.widget_id === "cash").x).toBeGreaterThan(0);
  view.unmount();
});

test("a conflict keeps the local layout and stops another silent write", async () => {
  api.saveFinanceDashboardLayout.mockRejectedValue({ response: { status: 409, data: { detail: { code: "LAYOUT_CONFLICT" } } } });
  const view = await renderPage();
  await act(async () => { view.host.querySelector("[data-testid='finance-edit-layout']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='finance-menu-cash']").click(); });
  await act(async () => { view.host.querySelector("[aria-label='Geser Arus Kas ke kanan']").click(); });
  await act(async () => { await wait(700); });
  expect(view.host.querySelector("[data-testid='finance-save-state']").textContent).toBe("Konflik");
  expect(view.host.querySelector("[data-testid='finance-widget-cash']")).toBeTruthy();
  const calls = api.saveFinanceDashboardLayout.mock.calls.length;
  await act(async () => { view.host.querySelector("[aria-label='Geser Arus Kas ke kanan']").click(); });
  await act(async () => { await wait(700); });
  expect(api.saveFinanceDashboardLayout).toHaveBeenCalledTimes(calls);
  view.unmount();
});

test("a failed save keeps the local card", async () => {
  api.saveFinanceDashboardLayout.mockRejectedValue({ response: { status: 500, data: { detail: "down" } } });
  const view = await renderPage();
  await act(async () => { view.host.querySelector("[data-testid='finance-edit-layout']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='finance-menu-expense']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='finance-remove-expense']").click(); });
  await act(async () => { await wait(700); });
  expect(view.host.querySelector("[data-testid='finance-save-state']").textContent).toBe("Gagal");
  expect(view.host.querySelector("[data-testid='finance-widget-expense']")).toBeNull();
  expect(view.host.querySelector("[data-testid='finance-widget-cash']")).toBeTruthy();
  view.unmount();
});

test("a forbidden widget is not fetched and a permitted sales card shares one request", async () => {
  global.__financePerms = ["dashboard.sales.view"];
  api.financeDashboardLayout.mockResolvedValue(layout([
    cashWidget(),
    { widget_id: "sales", x: 4, y: 0, w: 4, h: 3, settings: { period_mode: "global" } },
    { widget_id: "trend", x: 0, y: 3, w: 8, h: 4, settings: { period_mode: "global", visual: "bars" } },
  ], 2));
  const view = await renderPage();
  const cash = view.host.querySelector("[data-testid='finance-widget-cash']");
  expect(cash.getAttribute("data-status")).toBe("forbidden");
  expect(cash.textContent).toContain("Tidak ada izin");
  await act(async () => { view.host.querySelector("[data-testid='finance-add-widget']").click(); });
  const cashCatalog = view.host.querySelector("[data-testid='finance-catalog-cash']");
  expect(cashCatalog.textContent).toContain("Tidak memiliki akses");
  expect(cashCatalog.querySelector("button")).toBeNull();
  expect(view.host.querySelector("[data-testid='finance-catalog-sales']").textContent).toContain("Terpasang");
  expect(view.host.querySelector("[data-testid='finance-catalog-sales'] button")).toBeNull();
  expect(api.financialDashboard).not.toHaveBeenCalled();
  expect(api.salesDashboard).toHaveBeenCalledTimes(1);
  expect(view.host.querySelector("[data-testid='finance-widget-trend']").textContent).toContain("1 Okt");
  const before = api.salesDashboard.mock.calls.length;
  await act(async () => { view.host.querySelectorAll(".kb-period .kb-btn")[0].click(); });
  await act(async () => { await flush(); await flush(); });
  expect(api.salesDashboard.mock.calls.length).toBeGreaterThan(before);
  view.unmount();
});

test("reset restores the server default and a missing layout does not write", async () => {
  api.resetFinanceDashboardLayout.mockResolvedValue(layout([cashWidget()], 0));
  const view = await renderPage();
  await act(async () => { view.host.querySelector("[data-testid='finance-more']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='finance-reset-layout']").click(); });
  await act(async () => { view.host.querySelector("[data-testid='finance-reset-confirm']").click(); await flush(); });
  expect(api.resetFinanceDashboardLayout).toHaveBeenCalledTimes(1);
  expect(view.host.querySelector("[data-testid='finance-widget-expense']")).toBeNull();
  view.unmount();

  api.financeDashboardLayout.mockRejectedValue(new Error("offline"));
  api.saveFinanceDashboardLayout.mockClear();
  const fallback = await renderPage();
  expect(fallback.host.querySelector("[data-testid='finance-notice']").textContent).toContain("gagal dimuat");
  await act(async () => { await wait(700); });
  expect(api.saveFinanceDashboardLayout).not.toHaveBeenCalled();
  fallback.unmount();
});

test("incomplete and error cash are not shown as Rp 0", async () => {
  api.financialDashboard.mockResolvedValue({
    cash_movement: { status: "incomplete", cash_in: 0, cash_out: null, net_change: null, detail: "Jurnal belum lengkap" },
    charts: { expense_composition: { status: "error", items: [], detail: "Gagal" } },
  });
  const view = await renderPage();
  const cash = view.host.querySelector("[data-testid='finance-widget-cash']").textContent;
  expect(cash).toContain("Belum lengkap");
  expect(cash).not.toContain("Rp 0");
  const expense = view.host.querySelector("[data-testid='finance-widget-expense']").textContent;
  expect(expense).toContain("Gagal memuat");
  expect(expense).not.toContain("Rp 0");
  view.unmount();
});
