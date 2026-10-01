import { readFileSync } from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import SalesPwaDashboardPage from "./SalesPwaDashboardPage";

jest.mock("react-router-dom", () => ({
  Link: ({ children, to, state, ...props }) => (
    <a href={typeof to === "string" ? to : ""} data-state={state ? JSON.stringify(state) : undefined} {...props}>{children}</a>
  ),
}), { virtual: true });
jest.mock("../lib/api", () => ({
  __esModule: true,
  default: { salesPwaDashboard: jest.fn(), salesPwaOrders: jest.fn(), salesPwaVisits: jest.fn() },
}));
global.IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
  api.salesPwaOrders.mockReset();
  api.salesPwaOrders.mockResolvedValue({ orders: [], counts: { selesai: 0 }, total: 0 });
  api.salesPwaVisits.mockReset();
  api.salesPwaVisits.mockResolvedValue({ visits: [] });
});

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
  expect(node.textContent).toContain("Omzet bulan ini");
  expect(node.textContent).toContain("Piutang");
  expect(node.textContent).toContain("PO aktif");
  expect(node.textContent).toContain("8 PO");
  expect(node.textContent).toContain("2 buffer stock");
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
  expect(node.querySelector('[data-testid="sales-dashboard-revenue"]').getAttribute("href")).toBe(`/sales/invoices?month=${nextMonth}`);
  expect(node.querySelector('[data-testid="sales-dashboard-receivables"]').getAttribute("href")).toBe("/sales/receivables");
  act(() => root.unmount());
});

test("shows owned action cards and the existing selesai count without an approval token", async () => {
  api.salesPwaDashboard.mockResolvedValue({
    metrics: { revenue: 1000, receivables: 0, invoice_count: 1, receivable_invoice_count: 0, active_po: 2, buffer_po: 1, visit_month: 1, visit_overdue: 0 },
    upcoming_visits: [{ id: "v1", outlet: "Tala Bumi Coffee", date: "2026-09-30", at: "2026-09-30T10:00:00", overdue: false }],
  });
  api.salesPwaOrders.mockImplementation((params) => {
    if (params.stage === "Approval Design") {
      return Promise.resolve({ orders: [{ id: "po-design", customer: "Tala Bumi Coffee", po_number: "PO/2026/09/23/000023", status: "Approval Design" }] });
    }
    if (params.stage === "Approval Payment") {
      return Promise.resolve({ orders: [{ id: "po-pay", customer: "CV Maju Jaya", po_number: "PO/2026/09/21/000017", status: "Approval Payment" }] });
    }
    return Promise.resolve({ orders: [], counts: { selesai: 12 }, total: 12 });
  });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaDashboardPage />); await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
  expect(node.textContent).toContain("PO selesai");
  expect(node.querySelector('[data-testid="sales-dashboard-done"]').textContent).toBe("12 PO");
  expect(node.textContent).toContain("bulan ini");
  expect(node.textContent).toContain("Butuh aksi");
  expect(node.textContent).toContain("Tala Bumi Coffee");
  const design = node.querySelector('[data-testid="sales-action-design-po-design"] a');
  const payment = node.querySelector('[data-testid="sales-action-payment-po-pay"] a');
  expect(design.getAttribute("href")).toBe("/sales/po/po-design");
  expect(design.textContent).toBe("Lihat PO Approval Design");
  expect(payment.getAttribute("href")).toBe("/sales/po/po-pay");
  expect(payment.textContent).toBe("Lihat PO Approval Payment");
  expect(node.querySelector('[data-testid="sales-actions-count"]').textContent).toBe("2 PO perlu tindakan");
  expect(node.querySelector('[data-testid="sales-actions-empty"]')).toBeNull();
  expect(node.textContent).not.toContain("Buka Approval Design");
  expect(node.textContent).not.toContain("Menunggu:");
  expect(node.innerHTML).not.toContain("customer-approval");
  expect(node.innerHTML).not.toContain("token");
  act(() => root.unmount());
});

test("shows the action empty state without an approval link when nothing needs action", async () => {
  api.salesPwaDashboard.mockResolvedValue({
    metrics: { revenue: 0, receivables: 0, invoice_count: 0, receivable_invoice_count: 0, active_po: 1, buffer_po: 0, visit_month: 0, visit_overdue: 0 },
    upcoming_visits: [],
  });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaDashboardPage />); await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
  const empty = node.querySelector('[data-testid="sales-actions-empty"]');
  expect(empty.textContent).toContain("Tidak ada PO yang membutuhkan tindakan");
  expect(empty.textContent).toContain("Semua PO berjalan normal");
  expect(node.textContent).toContain("Agenda hari ini");
  expect(node.textContent).toContain("Belum ada agenda");
  expect(node.textContent).not.toContain("Agenda hari ini (");
  expect(node.querySelector('[data-testid="sales-actions-count"]')).toBeNull();
  expect(node.querySelector('[data-testid="sales-dashboard-actions"] a')).toBeNull();
  expect(node.innerHTML).not.toContain("customer-approval");
  expect(api.salesPwaOrders.mock.calls.every((call) => call[0].stage === "Approval Design" || call[0].stage === "Approval Payment" || call[0].status === "selesai")).toBe(true);
  act(() => root.unmount());
});

test("uses the existing list totals for the action count without another request", async () => {
  api.salesPwaDashboard.mockResolvedValue({
    metrics: { revenue: 0, receivables: 0, invoice_count: 0, receivable_invoice_count: 0, active_po: 27, buffer_po: 5, visit_month: 0, visit_overdue: 0 },
    upcoming_visits: [],
  });
  api.salesPwaOrders.mockImplementation((params) => {
    if (params.stage === "Approval Design") {
      return Promise.resolve({
        orders: [{ id: "po-design", customer: "Tala Bumi Coffee", po_number: "PO/1", status: "Approval Design" }],
        total: 5,
      });
    }
    if (params.stage === "Approval Payment") {
      return Promise.resolve({
        orders: [{ id: "po-pay", customer: "CV Maju Jaya", po_number: "PO/2", status: "Approval Payment" }],
        total: 3,
      });
    }
    return Promise.resolve({ orders: [], counts: { selesai: 0 }, total: 12 });
  });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaDashboardPage />); await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
  expect(api.salesPwaOrders).toHaveBeenCalledTimes(3);
  expect(node.querySelector('[data-testid="sales-actions-count"]').textContent).toBe("8 PO perlu tindakan");
  expect(node.textContent).toContain("5 buffer stock");
  expect(node.textContent).not.toContain("Partial");
  expect(node.querySelector('[data-testid="sales-actions-empty"]')).toBeNull();
  act(() => root.unmount());
});

test("explains running receivables and links only the cards that already have a destination", async () => {
  api.salesPwaDashboard.mockResolvedValue({
    metrics: { revenue: 0, receivables: 4945000, invoice_count: 0, receivable_invoice_count: 4, active_po: 3, buffer_po: 1, visit_month: 2, visit_overdue: 1 },
    upcoming_visits: [],
  });
  api.salesPwaVisits.mockResolvedValue({
    visits: [
      { id: "old", outlet: "Lama", activity_at: "2026-09-01T08:00:00", result: "Lama" },
      { id: "new", outlet: "ABC", activity_at: "2026-10-01T09:00:00", result: "Follow up desain" },
    ],
  });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaDashboardPage />); await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });

  const main = node.querySelector('[data-testid="sales-pwa-dashboard"]').innerHTML;
  const order = ["sales-dashboard-actions", "sales-dashboard-agenda", "sales-dashboard-followup", "sales-dashboard-last-visit", "sales-dashboard-performance"];
  let cursor = -1;
  order.forEach((id) => {
    const at = main.indexOf(id);
    expect(at).toBeGreaterThan(cursor);
    cursor = at;
  });

  const month = node.querySelector('input[type="month"]').value;
  const receivables = node.querySelector('[data-testid="sales-dashboard-receivables"]');
  expect(receivables.tagName).toBe("A");
  expect(receivables.getAttribute("href")).toBe("/sales/receivables");
  expect(receivables.className).toContain("spwa-metric-link");
  expect(receivables.textContent).toContain("Piutang berjalan");
  expect(receivables.textContent).toContain("Rp4.945.000");
  expect(receivables.textContent).toContain("4 invoice belum lunas");
  expect(receivables.textContent).toContain("Semua periode");
  expect(receivables.textContent).toContain("Saldo invoice yang masih belum lunas, termasuk periode sebelumnya.");
  expect(receivables.getAttribute("title")).toContain("periode sebelumnya");
  const revenue = node.querySelector('[data-testid="sales-dashboard-revenue"]');
  expect(revenue.tagName).toBe("A");
  expect(revenue.getAttribute("href")).toBe(`/sales/invoices?month=${month}`);
  expect(revenue.textContent).toContain("Rp0");
  expect(revenue.textContent).toContain("0 faktur final");
  expect(node.innerHTML).not.toContain("/buku-besar/piutang-usaha");
  expect(node.innerHTML).not.toContain("/penjualan/faktur-penjualan");

  const active = node.querySelector('[data-testid="sales-dashboard-active"]');
  const done = node.querySelector('[data-testid="sales-dashboard-done-card"]');
  const visits = node.querySelector('[data-testid="sales-dashboard-visits"]');
  expect(active.getAttribute("href")).toBe("/sales/po");
  expect(JSON.parse(active.getAttribute("data-state"))).toEqual({ salesPwaPoContext: { active: true } });
  expect(done.getAttribute("href")).toBe("/sales/po");
  expect(JSON.parse(done.getAttribute("data-state"))).toEqual({ salesPwaPoContext: { status: "selesai", periode: month } });
  expect(visits.getAttribute("href")).toBe("/sales/visit");
  expect(visits.textContent).toContain("Visit bulan ini");
  expect(visits.textContent).toContain("2");

  const last = node.querySelector('[data-testid="sales-last-visit"]');
  expect(last.textContent).toContain("ABC");
  expect(last.textContent).toContain("01 Oktober 2026");
  expect(last.textContent).toContain("Follow up desain");
  expect(node.querySelector('[data-testid="sales-last-visit-empty"]')).toBeNull();
  expect(api.salesPwaVisits).toHaveBeenCalledWith({ month });
  act(() => root.unmount());
});

test("shows an empty visit history without breaking the dashboard", async () => {
  api.salesPwaDashboard.mockResolvedValue({
    metrics: { revenue: 0, receivables: 0, invoice_count: 0, receivable_invoice_count: 0, active_po: 0, buffer_po: 0, visit_month: 0, visit_overdue: 0 },
    upcoming_visits: [],
  });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaDashboardPage />); await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
  expect(node.querySelector('[data-testid="sales-last-visit-empty"]').textContent).toBe("Tidak ada riwayat visit");
  expect(node.querySelector('[data-testid="sales-dashboard-done"]').textContent).toBe("0 PO");
  expect(node.querySelector('[data-testid="sales-dashboard-receivables"]').textContent).toContain("0 invoice belum lunas");
  act(() => root.unmount());
});

test("links omzet and receivables with the existing pressable card treatment", async () => {
  api.salesPwaDashboard.mockResolvedValue({
    metrics: { revenue: 12500000, receivables: 3200000, invoice_count: 3, receivable_invoice_count: 1, active_po: 1, buffer_po: 0, visit_month: 1, visit_overdue: 0 },
    upcoming_visits: [],
  });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaDashboardPage />); await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
  const month = node.querySelector('input[type="month"]').value;
  const revenue = node.querySelector('[data-testid="sales-dashboard-revenue"]');
  const receivables = node.querySelector('[data-testid="sales-dashboard-receivables"]');
  expect(revenue.tagName).toBe("A");
  expect(revenue.className).toContain("spwa-metric-link");
  expect(revenue.getAttribute("href")).toBe(`/sales/invoices?month=${month}`);
  expect(revenue.textContent).toContain("Rp12.500.000");
  expect(revenue.textContent).toContain("3 faktur final");
  expect(receivables.tagName).toBe("A");
  expect(receivables.className).toContain("spwa-metric-link");
  expect(receivables.getAttribute("href")).toBe("/sales/receivables");
  expect(receivables.textContent).toContain("Rp3.200.000");
  expect(receivables.textContent).toContain("1 invoice belum lunas");
  expect(node.querySelector('[data-testid="sales-dashboard-active"]').className).toContain("spwa-metric-link");
  expect(node.querySelector('[data-testid="sales-dashboard-active"]').getAttribute("href")).toBe("/sales/po");
  expect(node.querySelector('[data-testid="sales-dashboard-done-card"]').className).toContain("spwa-metric-link");
  expect(node.querySelector('[data-testid="sales-dashboard-done-card"]').getAttribute("href")).toBe("/sales/po");
  expect(node.querySelector('[data-testid="sales-dashboard-visits"]').getAttribute("href")).toBe("/sales/visit");
  const css = readFileSync(path.join(__dirname, "salesPwa.css"), "utf8");
  expect(css).toMatch(/a\.spwa-metric-link\s*\{[^}]*cursor:\s*pointer/);
  expect(css).toMatch(/a\.spwa-metric-link:hover\s*\{[^}]*border-color:\s*#8bd6bd/);
  expect(css).toMatch(/a\.spwa-metric-link:active\s*\{[^}]*border-color:\s*#08785d/);
  act(() => root.unmount());
});

test("keeps today's agenda after classifying more than eight earlier reminders", async () => {
  const now = new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const overdue = Array.from({ length: 9 }, (_, index) => ({
    id: `old-${index}`,
    outlet: `Lama ${index}`,
    date: `2020-01-${String(index + 1).padStart(2, "0")}`,
    overdue: true,
  }));
  api.salesPwaDashboard.mockResolvedValue({
    metrics: { revenue: 1000, receivables: 0, invoice_count: 1, receivable_invoice_count: 0, active_po: 2, buffer_po: 0, visit_month: 4, visit_overdue: 9 },
    upcoming_visits: [
      ...overdue,
      { id: "today-1", outlet: "Hari Ini Outlet", date: today, at: `${today}T09:15:00`, address: "Jl. Malioboro", next_action: "Bawa sampel" },
      { id: "next-1", outlet: "Besok Outlet", date: "2099-12-01", at: "2099-12-01T11:00:00", next_action: "Kirim harga" },
    ],
  });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaDashboardPage />); await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
  const agenda = node.querySelector('[data-testid="sales-dashboard-agenda"]');
  const late = node.querySelector('[data-testid="sales-dashboard-overdue"]');
  const upcoming = node.querySelector('[data-testid="sales-dashboard-upcoming"]');
  expect(agenda.textContent).toContain("Hari Ini Outlet");
  expect(agenda.textContent).toContain("Jl. Malioboro");
  expect(agenda.textContent).toContain("Bawa sampel");
  expect(agenda.textContent).not.toContain("Lama 0");
  expect(late.textContent).toContain("Lama 0");
  expect(late.textContent).toContain("Lama 8");
  expect(late.textContent).not.toContain("Hari Ini Outlet");
  expect(upcoming.textContent).toContain("Besok Outlet");
  expect(upcoming.textContent).toContain("Kirim harga");
  expect(node.querySelector('[data-testid="sales-dashboard-revenue"]').textContent).toContain("Rp1.000");
  expect(node.querySelector('[data-testid="sales-dashboard-visits"]').textContent).toContain("4");
  act(() => root.unmount());
});

test("agenda count stays complete when the visible list is capped", async () => {
  api.salesPwaDashboard.mockResolvedValue({
    metrics: { revenue: 1000, receivables: 0, invoice_count: 1, receivable_invoice_count: 0, active_po: 0, buffer_po: 0, visit_month: 0, visit_overdue: 31 },
    agenda_today: [{ id: "today-1", outlet: "Hari Ini Outlet", date: "2026-10-01", at: "2026-10-01T09:00:00+07:00" }],
    agenda_today_total: 31,
    follow_up_overdue: [],
    follow_up_overdue_total: 31,
    follow_up_upcoming: [],
    follow_up_upcoming_total: 0,
  });
  api.salesPwaVisits.mockResolvedValue({ visits: [] });
  const node = document.createElement("div");
  const root = createRoot(node);
  await act(async () => { root.render(<SalesPwaDashboardPage />); await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
  const agenda = node.querySelector('[data-testid="sales-dashboard-agenda"]');
  expect(agenda.textContent).toContain("Agenda hari ini (31)");
  expect(Array.from(agenda.querySelectorAll("a")).filter((item) => item.textContent.includes("Hari Ini Outlet"))).toHaveLength(1);
  expect(node.querySelector('[data-testid="sales-dashboard-visits"]').textContent).toContain("31");
  act(() => root.unmount());
});
