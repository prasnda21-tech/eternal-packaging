import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import SalesPwaVisitsPage from "./SalesPwaVisitsPage";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesPwaVisits: jest.fn(),
    createSalesPwaVisit: jest.fn(),
    scheduleSalesPwaVisit: jest.fn(),
    uploadSalesPwaVisitPhoto: jest.fn(),
  },
}));
global.IS_REACT_ACT_ENVIRONMENT = true;

test("renders a compact calendar and opens visit drawer", async () => {
  api.salesPwaVisits.mockResolvedValue({
    counts: { owned_outlets: 2, visit_month: 1, overdue: 1 },
    reminders: [{ id: "r1", outlet: "Kopi Baru", date: "2026-09-20", overdue: true }],
    outlets: [{ id: "o1", name: "Kopi Baru", pic_name: "Budi", ownership: { next_follow_up_at: "2026-09-20T09:00:00+07:00" } }],
    visit_config: { photo_required: false, next_follow_up_required: false },
  });
  const node = document.createElement("div");
  document.body.appendChild(node);
  const root = createRoot(node);
  await act(async () => {
    root.render(<SalesPwaVisitsPage />);
    await Promise.resolve();
  });
  expect(node.querySelector('[aria-label="Kalender reminder visit"]')).toBeTruthy();
  const button = Array.from(node.querySelectorAll("button")).find((item) => item.textContent.includes("Visit"));
  act(() => button.click());
  expect(document.body.textContent).toContain("CATAT VISIT");
  expect(document.body.textContent).toContain("Kopi Baru");
  act(() => root.unmount());
  node.remove();
});

test("renders visit history newest first and hides empty optional fields", async () => {
  api.salesPwaVisits.mockResolvedValue({
    counts: { owned_outlets: 1, visit_month: 2, overdue: 0 },
    reminders: [],
    outlets: [],
    visits: [
      { id: "old", outlet: "Outlet Lama", activity_at: "2026-09-01T08:00:00", result: "Sampel", next_follow_up_at: "", next_action: "  ", notes: "" },
      { id: "new", outlet: "Outlet Baru", activity_at: "2026-10-02T14:30:00", result: "Deal harga", next_follow_up_at: "2026-10-09T09:00:00", next_action: "Kirim penawaran", notes: "Minta revisi desain" },
    ],
  });
  const node = document.createElement("div");
  document.body.appendChild(node);
  const root = createRoot(node);
  await act(async () => {
    root.render(<SalesPwaVisitsPage />);
    await Promise.resolve();
  });
  const history = node.querySelector('[data-testid="sales-visit-history"]');
  const rows = history.querySelectorAll('[data-testid="sales-visit-history-row"]');
  expect(history.textContent).toContain("Riwayat Visit");
  expect(rows).toHaveLength(2);
  expect(rows[0].textContent).toContain("Outlet Baru");
  expect(rows[0].textContent).toContain("Deal harga");
  expect(rows[0].textContent).toContain("Follow-up:");
  expect(rows[0].textContent).toContain("Kirim penawaran");
  expect(rows[0].textContent).toContain("Minta revisi desain");
  expect(rows[1].textContent).toContain("Outlet Lama");
  expect(rows[1].textContent).toContain("Sampel");
  expect(rows[1].textContent).not.toContain("Follow-up:");
  expect(rows[1].textContent).not.toContain("Next action:");
  expect(node.querySelector('[data-testid="sales-visit-history-empty"]')).toBeNull();
  act(() => root.unmount());
  node.remove();
});

test("shows an empty visit history without inventing rows", async () => {
  api.salesPwaVisits.mockResolvedValue({
    counts: { owned_outlets: 0, visit_month: 0, overdue: 0 },
    reminders: [],
    outlets: [],
    visits: [],
  });
  const node = document.createElement("div");
  document.body.appendChild(node);
  const root = createRoot(node);
  await act(async () => {
    root.render(<SalesPwaVisitsPage />);
    await Promise.resolve();
  });
  expect(node.querySelector('[data-testid="sales-visit-history-empty"]').textContent).toBe("Belum ada riwayat visit.");
  expect(node.querySelectorAll('[data-testid="sales-visit-history-row"]')).toHaveLength(0);
  act(() => root.unmount());
  node.remove();
});

test("schedules a visit without recording a completed visit", async () => {
  api.salesPwaVisits.mockResolvedValue({
    counts: { owned_outlets: 1, visit_month: 0, overdue: 0 },
    reminders: [],
    agenda_today: [],
    follow_up_overdue: [],
    follow_up_upcoming: [{ id: "plan-1", outlet: "Kopi Baru", date: "2099-04-02", at: "2099-04-02T09:00:00+07:00", kind: "schedule", overdue: false }],
    outlets: [{ id: "o1", name: "Kopi Baru", address: "Jl. Kaliurang", ownership: {} }],
    visits: [],
    visit_config: { photo_required: false, next_follow_up_required: false },
  });
  api.scheduleSalesPwaVisit.mockResolvedValue({ scheduled: true });
  const node = document.createElement("div");
  document.body.appendChild(node);
  const root = createRoot(node);
  await act(async () => {
    root.render(<SalesPwaVisitsPage />);
    await Promise.resolve();
  });
  expect(node.querySelector('[data-testid="sales-visit-agenda"]').textContent).toContain("Kopi Baru");
  expect(node.querySelector('[data-testid="sales-visit-agenda"]').textContent).toContain("Jadwal");
  const button = Array.from(node.querySelectorAll("button")).find((item) => item.textContent.includes("Jadwalkan"));
  await act(async () => { button.click(); });
  const form = node.querySelector('[data-testid="sales-visit-schedule"]');
  const input = form.querySelector('input[type="datetime-local"]');
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(input, "2099-04-02T09:00");
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await act(async () => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  expect(api.scheduleSalesPwaVisit).toHaveBeenCalledWith({ outlet_id: "o1", next_follow_up_at: "2099-04-02T09:00" });
  expect(api.createSalesPwaVisit).not.toHaveBeenCalled();
  act(() => root.unmount());
  node.remove();
});

test("filters the agenda when a calendar day is pressed", async () => {
  const now = new Date();
  const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const day = `${month}-15`;
  api.salesPwaVisits.mockResolvedValue({
    month,
    counts: { owned_outlets: 1, visit_month: 1, overdue: 1 },
    reminders: [
      { id: "late", outlet: "Outlet Terlambat", date: "2020-01-05", overdue: true, kind: "follow_up" },
      { id: "mid", outlet: "Outlet Tanggal", date: day, kind: "schedule" },
    ],
    agenda_today: [],
    follow_up_overdue: [{ id: "late", outlet: "Outlet Terlambat", date: "2020-01-05", overdue: true, kind: "follow_up" }],
    follow_up_upcoming: [{ id: "mid", outlet: "Outlet Tanggal", date: day, at: `${day}T09:00:00+07:00`, kind: "schedule" }],
    outlets: [{ id: "o1", name: "Outlet Tanggal", address: "Jl. Uji", ownership: {} }],
    visits: [{ id: "done", outlet: "Outlet Tanggal", activity_at: `${day}T08:00:00+07:00`, result: "Sudah datang" }],
  });
  const node = document.createElement("div");
  document.body.appendChild(node);
  const root = createRoot(node);
  await act(async () => {
    root.render(<SalesPwaVisitsPage />);
    await Promise.resolve();
  });
  const main = node.querySelector('[data-testid="sales-pwa-visits"]').innerHTML;
  expect(main.indexOf("sales-visit-agenda")).toBeLessThan(main.indexOf("sales-visit-history"));
  expect(node.textContent).toContain("Outlet Terlambat");
  const dayButton = Array.from(node.querySelectorAll(".spwa-calendar-grid button")).find((item) => item.textContent.includes("15"));
  expect(dayButton.disabled).toBe(false);
  await act(async () => { dayButton.click(); });
  const agenda = node.querySelector('[data-testid="sales-visit-agenda"]');
  expect(agenda.textContent).toContain("Outlet Tanggal");
  expect(agenda.textContent).toContain("Jadwal");
  expect(agenda.textContent).not.toContain("Outlet Terlambat");
  expect(node.querySelector('[data-testid="sales-visit-history"]').textContent).toContain("Sudah datang");
  act(() => root.unmount());
  node.remove();
});

test("shows the new follow-up in WIB and hides the replaced schedule", async () => {
  api.salesPwaVisits.mockResolvedValue({
    counts: { owned_outlets: 1, visit_month: 1, overdue: 0 },
    reminders: [{
      id: "one",
      outlet_id: "o1",
      outlet: "Kopi Baru",
      date: "2026-10-01",
      at: "2026-10-01T00:30:00+07:00",
      kind: "follow_up",
    }],
    agenda_today: [{
      id: "one",
      outlet_id: "o1",
      outlet: "Kopi Baru",
      date: "2026-10-01",
      at: "2026-10-01T00:30:00+07:00",
      kind: "follow_up",
    }],
    follow_up_overdue: [],
    follow_up_upcoming: [],
    outlets: [{
      id: "o1",
      name: "Kopi Baru",
      ownership: { next_follow_up_at: "2026-09-30T17:30:00+00:00" },
    }],
    visits: [{
      id: "done",
      outlet: "Kopi Baru",
      activity_at: "2026-10-01T02:00:00+00:00",
      result: "Sudah datang",
      next_follow_up_at: "2026-09-30T17:30:00+00:00",
    }],
  });
  const node = document.createElement("div");
  document.body.appendChild(node);
  const root = createRoot(node);
  await act(async () => {
    root.render(<SalesPwaVisitsPage />);
    await Promise.resolve();
  });
  const agenda = node.querySelector('[data-testid="sales-visit-agenda"]');
  expect(agenda.textContent).toContain("Follow up");
  expect(agenda.textContent).toContain("01/10/26 00:30");
  expect(agenda.textContent).not.toContain("17:30");
  expect(node.querySelector(".spwa-outlet-list").textContent).toContain("Follow up");
  expect(node.querySelector(".spwa-outlet-list").textContent).toContain("01/10/26 00:30");
  expect(node.querySelector(".spwa-outlet-list").textContent).not.toContain("30/09/26");
  const history = node.querySelector('[data-testid="sales-visit-history-row"]');
  expect(history.textContent).toContain("Follow-up: 01/10/26 00:30");
  expect(history.textContent).not.toContain("17:30");
  act(() => root.unmount());
  node.remove();
});
