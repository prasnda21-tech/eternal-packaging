import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import SalesPwaDashboardPage from "./SalesPwaDashboardPage";

jest.mock("react-router-dom", () => ({ Link: ({ children, to }) => <a href={to}>{children}</a> }), { virtual: true });
jest.mock("../lib/api", () => ({
  __esModule: true,
  default: { salesPwaDashboard: jest.fn() },
}));
global.IS_REACT_ACT_ENVIRONMENT = true;

test("shows invoice-authoritative revenue and receivables", async () => {
  api.salesPwaDashboard.mockResolvedValue({
    metrics: {
      revenue: 12500000,
      receivables: 3200000,
      invoice_count: 3,
      receivable_invoice_count: 1,
      active_po: 8,
      buffer_po: 2,
      visit_month: 6,
      visit_overdue: 1,
    },
    upcoming_visits: [{ id: "v1", outlet: "Kopi Baru", date: "2026-09-20", overdue: false }],
  });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaDashboardPage />); await Promise.resolve(); });
  expect(node.textContent).toContain("Omzet faktur bulan ini");
  expect(node.textContent).toContain("Piutang pelanggan");
  expect(node.textContent).toContain("Rp12.500.000");
  expect(node.textContent).toContain("sales_user_id");
  act(() => root.unmount());
});

test("hides the previous month figures until the selected month returns", async () => {
  let resolveNext;
  api.salesPwaDashboard.mockReset();
  api.salesPwaDashboard
    .mockResolvedValueOnce({
      metrics: { revenue: 12500000, receivables: 0, invoice_count: 1, receivable_invoice_count: 0, active_po: 1, buffer_po: 0, visit_month: 0, visit_overdue: 0 },
      upcoming_visits: [],
    })
    .mockImplementationOnce(() => new Promise((resolve) => { resolveNext = resolve; }));
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaDashboardPage />); await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
  expect(node.textContent).toContain("Rp12.500.000");

  const input = node.querySelector('input[type="month"]');
  const nextMonth = input.value === "2026-10" ? "2026-11" : "2026-10";
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(input, nextMonth);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(node.textContent).not.toContain("Rp12.500.000");
  expect(node.textContent).toContain("Memuat data penjualan...");

  await act(async () => {
    resolveNext({
      month: nextMonth,
      metrics: { revenue: 5000, receivables: 0, invoice_count: 1, receivable_invoice_count: 0, active_po: 1, buffer_po: 0, visit_month: 0, visit_overdue: 0 },
      upcoming_visits: [],
    });
  });
  expect(node.textContent).toContain("Rp5.000");
  expect(node.textContent).not.toContain("Rp12.500.000");
  act(() => root.unmount());
});
