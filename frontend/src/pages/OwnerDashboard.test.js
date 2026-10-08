/**
 * @jest-environment jsdom
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

jest.mock("react-router-dom", () => ({
  useNavigate: () => (global.__ownerNavigate || jest.fn()),
  useLocation: () => global.__ownerLocation || { pathname: "/dashboard/owner", search: "?preset=month" },
}), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => global.__ownerAuth,
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    ownerDashboard: jest.fn(),
  },
}));

import api from "../lib/api";
import OwnerDashboard, {
  formatOwnerAsOfDay,
  formatOwnerPeriodRange,
  ownerSnapshotContext,
} from "./OwnerDashboard";

function render() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(<OwnerDashboard />); });
  return {
    host,
    unmount() {
      act(() => { root.unmount(); });
      host.remove();
    },
  };
}

beforeEach(() => {
  document.body.innerHTML = "";
  global.__ownerNavigate = jest.fn();
  global.__ownerLocation = { pathname: "/dashboard/owner", search: "?preset=month" };
  global.__ownerAuth = {
    user: { role: "direktur_utama", permissions: ["dashboard.owner.view", "nav.dashboard.owner"] },
    hasPerm: (p) => ["dashboard.owner.view", "nav.dashboard.owner"].includes(p),
    loading: false,
  };
  api.ownerDashboard.mockReset();
  api.ownerDashboard.mockResolvedValue({
    start_date: "2026-03-01",
    end_date: "2026-03-15",
    as_of_date: "2026-03-15",
    loaded_at: "2026-03-15T07:00:00+00:00",
    timezone: "Asia/Jakarta",
    branch_filter: null,
    omzet: { status: "complete", amount: 4000, invoice_count: 2, basis: "after_discount" },
    invoice_count: { status: "complete", count: 2 },
    aov_invoice: { status: "complete", amount: 2000, invoice_count: 2 },
    average_po: {
      status: "incomplete",
      amount: 14000,
      po_count: 3,
      excluded_incomplete_price_count: 1,
      detail: "1 PO tidak dihitung karena harga belum lengkap.",
    },
    po_active: { status: "complete", count: 5 },
    delivery_late: { status: "complete", count: 2, label: "Pengiriman terlambat (jadwal SJ aktif)" },
    trend: {
      status: "complete",
      grain_label: "Harian",
      points: [{ label: "01 Mar", omzet: 1000 }, { label: "02 Mar", omzet: 3000 }],
    },
    profit: { status: "not_ready", detail: "Data Belum Siap." },
    otp_otd: { status: "not_ready" },
    finance: {
      status: "incomplete",
      cash_balance: {
        status: "complete",
        amount: 65000,
        as_of: "2026-03-15",
        scope_label: "Saat Ini",
      },
      cash_in: { status: "complete", amount: 30000, scope_label: "Periode" },
      cash_out: { status: "complete", amount: 15000, scope_label: "Periode" },
      cash_trend: {
        status: "complete",
        grain: "day",
        points: [
          { period: "2026-03-01", cash_in: 10000, cash_out: 5000 },
          { period: "2026-03-02", cash_in: 20000, cash_out: 10000 },
          { period: "2026-03-15", cash_in: 5000, cash_out: 1000 },
        ],
      },
      reconciliation: {
        status: "incomplete",
        status_label: "Belum Lengkap",
        unmatched_count: 3,
        review_count: 1,
        label: "Rekonsiliasi Bulan Ini",
      },
    },
    operations: {
      status: "complete",
      po_status: { aktif: 5, selesai: 3, terlambat_overlay: 2, partition: "disjoint_active_completed" },
      stage_breakdown: {
        unit: "po_count",
        active_po_count: 5,
        bottleneck: { stage: 8, label: "Operator", count: 3 },
        rows: [
          { stage: 8, label: "Operator", count: 3 },
          { stage: 11, label: "Packing", count: 2 },
        ],
      },
      delivery: {
        late_count: 2,
        today_count: 1,
        upcoming_count: 1,
        late: [{ id: "s1", order_id: "o1", po_number: "PO-1", customer: "A", delivery_date: "2026-03-10", status: "terlambat", status_label: "Terlambat", sj_number: "SJ-1" }],
        today: [],
        upcoming: [{ id: "s2", order_id: "o2", po_number: "PO-2", customer: "B", delivery_date: "2026-03-20", status: "terjadwal", status_label: "Terjadwal", sj_number: "SJ-2" }],
      },
      exceptions: {
        status: "complete",
        items: [{ category: "pengiriman", label: "Pengiriman terlambat", po_number: "PO-1", customer: "A", detail: "Lewat", href_hint: "delivery_schedule_late" }],
      },
    },
    drilldowns: {
      sales_dashboard: { allowed: false, path: "/dashboard/penjualan" },
      status_po: { allowed: false, path: "/laporan/status-po" },
      delivery_schedule_late: { allowed: false, path: "/produksi/jadwal-pengiriman?status=terlambat" },
      production: { allowed: false, path: "/produksi" },
    },
  });
});

test("App registers owner route with OwnerDashboardGate", () => {
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  expect(app).toMatch(/path="\/dashboard\/owner"/);
  expect(app).toMatch(/OwnerDashboardGate/);
  expect(app).toMatch(/OwnerDashboard/);
});

test("authorized director loads KPIs without hardcoded sample millions", async () => {
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(view.host.querySelector("[data-testid='owner-dashboard']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='owner-kpi-omzet']").textContent).toMatch(/4\.000|4,000|Rp/);
  expect(view.host.textContent).not.toMatch(/2\.487\.650\.000/);
  expect(view.host.querySelector("[data-testid='owner-kpi-avg-po']").textContent).toMatch(/tidak dihitung|1 PO/);
  expect(view.host.querySelector("[data-testid='owner-kpi-gross-margin']").textContent).toMatch(/Data Belum Siap/);
  expect(view.host.querySelector("[data-testid='owner-kpi-profit']").textContent).toMatch(/Data Belum Siap/);
  expect(view.host.textContent).not.toMatch(/\bOTP\b|\bOTD\b/);
  expect(view.host.querySelector("[data-testid='owner-po-late-overlay']").textContent).toBe("2");
  expect(view.host.querySelector("[data-testid='owner-finance']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='owner-kpi-cash-balance']").textContent).toMatch(/65\.000|65,000|Rp/);
  expect(view.host.querySelector("[data-testid='owner-kpi-cash-balance-badge']").textContent).toBe("Saat Ini");
  expect(view.host.querySelector("[data-testid='owner-kpi-cash-in-badge']").textContent).toBe("Periode");
  expect(view.host.querySelector("[data-testid='owner-kpi-cash-out']").textContent).toMatch(/15\.000|15,000|Rp/);
  expect(view.host.querySelector("[data-testid='owner-kpi-recon']").textContent).toMatch(/Belum Lengkap/);
  expect(view.host.querySelector("[data-testid='owner-recon-counts']").textContent).toMatch(/Belum cocok:\s*3/);
  expect(view.host.querySelector("[data-testid='owner-cash-trend-plot']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='owner-cash-trend-period']").textContent).toBe("Harian");
  expect(view.host.querySelector("[data-testid='owner-period-toolbar']")).toBeTruthy();
  expect(view.host.textContent).toMatch(/01 Mar|02 Mar|15 Mar/);
  expect(view.host.textContent).not.toMatch(/\bday\b/);
  expect(api.ownerDashboard).toHaveBeenCalled();
  view.unmount();
});

test("custom filter toolbar stays outside sticky collision zone", async () => {
  global.__ownerLocation = {
    pathname: "/dashboard/owner",
    search: "?preset=custom&start=2025-01-01&end=2025-01-31",
  };
  const view = render();
  await act(async () => { await flush(); await flush(); });
  const toolbar = view.host.querySelector("[data-testid='owner-period-toolbar']");
  const custom = view.host.querySelector("[data-testid='owner-custom-range']");
  const title = view.host.querySelector("h1");
  expect(toolbar).toBeTruthy();
  expect(custom).toBeTruthy();
  expect(title?.textContent).toBe("Dashboard Owner");
  expect(view.host.querySelector("[data-testid='owner-start']").value).toBe("2025-01-01");
  expect(view.host.querySelector("[data-testid='owner-end']").value).toBe("2025-01-31");
  expect(custom.textContent).toMatch(/s\/d/);
  // Snapshot badges remain Saat Ini even on historical custom period.
  expect(view.host.querySelector("[data-testid='owner-kpi-cash-balance-badge']").textContent).toBe("Saat Ini");
  expect(view.host.querySelector("[data-testid='owner-kpi-omzet']")).toBeTruthy();
  view.unmount();
});

test("recon null counts do not invent totals", async () => {
  const base = {
    start_date: "2026-03-01",
    end_date: "2026-03-15",
    as_of_date: "2026-03-15",
    loaded_at: "2026-03-15T07:00:00+00:00",
    omzet: { status: "complete", amount: 4000, invoice_count: 2 },
    invoice_count: { status: "complete", count: 2 },
    aov_invoice: { status: "complete", amount: 2000, invoice_count: 2 },
    average_po: { status: "empty", amount: null, po_count: 0 },
    po_active: { status: "complete", count: 1 },
    delivery_late: { status: "complete", count: 0 },
    trend: { status: "empty", points: [] },
    profit: { status: "not_ready" },
    operations: {
      status: "complete",
      po_status: { aktif: 1, selesai: 0, terlambat_overlay: 0 },
      stage_breakdown: { unit: "po_count", active_po_count: 1, rows: [] },
      delivery: { late_count: 0, today_count: 0, upcoming_count: 0, late: [], today: [], upcoming: [] },
      exceptions: { status: "complete", items: [] },
    },
    drilldowns: {},
    finance: {
      status: "error",
      cash_balance: { status: "error", amount: null },
      cash_in: { status: "error", amount: null },
      cash_out: { status: "error", amount: null },
      cash_trend: { status: "error", points: [] },
      reconciliation: {
        status: "error",
        status_label: "Belum Lengkap",
        unmatched_count: null,
        review_count: null,
        detail: "Sebagian rekening tidak dapat dimuat.",
      },
    },
  };
  api.ownerDashboard.mockResolvedValue(base);
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(view.host.querySelector("[data-testid='owner-kpi-omzet']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='owner-recon-counts']").textContent).not.toMatch(/Belum cocok:\s*\d/);
  expect(view.host.querySelector("[data-testid='owner-kpi-cash-balance']").getAttribute("data-status")).toBe("error");
  view.unmount();
});

test("admin role cannot open owner page content", async () => {
  global.__ownerAuth = {
    user: { role: "admin", permissions: ["dashboard.owner.view"] },
    hasPerm: () => true,
    loading: false,
  };
  const view = render();
  await act(async () => { await flush(); });
  expect(view.host.querySelector("[data-testid='owner-dashboard-denied']")).toBeTruthy();
  expect(api.ownerDashboard).not.toHaveBeenCalled();
  view.unmount();
});

test("api failure shows error not zero omzet", async () => {
  api.ownerDashboard.mockRejectedValue({ response: { data: { detail: "Gagal layanan" } } });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(view.host.querySelector("[data-testid='owner-load-error']").textContent).toMatch(/Gagal layanan/);
  expect(view.host.querySelector("[data-testid='owner-kpi-omzet']").getAttribute("data-status")).toBe("error");
  view.unmount();
});

test("date preset buttons present and no branch filter", async () => {
  global.__ownerLocation = { pathname: "/dashboard/owner", search: "?preset=today" };
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(view.host.querySelector("[data-testid='owner-preset-today']")).toBeTruthy();
  expect(view.host.querySelector("[data-testid='owner-preset-month']")).toBeTruthy();
  expect(view.host.textContent).not.toMatch(/Yogyakarta|Semarang|Filter cabang/i);
  view.unmount();
});

test("formatOwnerAsOfDay and period helpers", () => {
  expect(formatOwnerAsOfDay("2026-10-08")).toBe("08 Okt 2026");
  expect(formatOwnerAsOfDay(null)).toBeNull();
  expect(formatOwnerPeriodRange("2026-10-01", "2026-10-08")).toBe("01–08 Okt 2026");
  expect(ownerSnapshotContext("2026-10-08")).toBe("Saat Ini · 08 Okt 2026");
  expect(ownerSnapshotContext(undefined)).toBe("Saat Ini");
});

test("header and snapshot badges distinguish period vs saat ini", async () => {
  global.__ownerLocation = {
    pathname: "/dashboard/owner",
    search: "?preset=custom&start=2026-03-01&end=2026-03-15",
  };
  const view = render();
  await act(async () => { await flush(); await flush(); });
  const range = view.host.querySelector("[data-testid='owner-range-label']").textContent;
  expect(range).toMatch(/Kinerja Periode/);
  expect(range).toMatch(/01–15 Mar 2026/);
  expect(view.host.querySelector("[data-testid='owner-ops-label']").textContent).toMatch(/Operasional/);
  expect(view.host.querySelector("[data-testid='owner-ops-label']").textContent).toMatch(/Saat Ini · 15 Mar 2026/);
  expect(view.host.querySelector("[data-testid='owner-kpi-po-active-badge']").textContent).toBe("Saat Ini");
  expect(view.host.querySelector("[data-testid='owner-kpi-late-badge']").textContent).toBe("Saat Ini");
  expect(view.host.querySelector("[data-testid='owner-kpi-omzet-badge']")).toBeNull();
  expect(view.host.querySelector("[data-testid='owner-po-status-asof']").textContent).toMatch(/Kondisi Saat Ini · 15 Mar 2026/);
  expect(view.host.querySelector("[data-testid='owner-production-asof']").textContent).toMatch(/Kondisi Saat Ini/);
  expect(view.host.querySelector("[data-testid='owner-delivery-asof']").textContent).toMatch(/Kondisi Saat Ini/);
  expect(view.host.querySelector("[data-testid='owner-exceptions-asof']").textContent).toMatch(/Kondisi Saat Ini/);
  expect(view.host.querySelector("[data-testid='owner-trend-period']").textContent).toMatch(/Harian|periode/i);
  expect(api.ownerDashboard).toHaveBeenCalledWith(expect.objectContaining({
    start_date: "2026-03-01",
    end_date: "2026-03-15",
    preset: "custom",
  }));
  view.unmount();
});

test("missing as_of_date falls back to Saat Ini without inventing a date", async () => {
  api.ownerDashboard.mockResolvedValue({
    start_date: "2026-03-01",
    end_date: "2026-03-15",
    loaded_at: "2026-03-15T07:00:00+00:00",
    omzet: { status: "complete", amount: 1, invoice_count: 1 },
    invoice_count: { status: "complete", count: 1 },
    aov_invoice: { status: "complete", amount: 1, invoice_count: 1 },
    average_po: { status: "empty", amount: null, po_count: 0 },
    po_active: { status: "complete", count: 2 },
    delivery_late: { status: "complete", count: 0 },
    trend: { status: "empty", points: [] },
    operations: {
      status: "complete",
      po_status: { aktif: 2, selesai: 0, terlambat_overlay: 0 },
      stage_breakdown: { unit: "po_count", active_po_count: 2, rows: [] },
      delivery: { late_count: 0, today_count: 0, upcoming_count: 0, late: [], today: [], upcoming: [] },
      exceptions: { status: "complete", items: [] },
    },
    drilldowns: {},
  });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(view.host.querySelector("[data-testid='owner-ops-label']").textContent.trim()).toMatch(/Operasional:\s*Saat Ini$/);
  expect(view.host.querySelector("[data-testid='owner-ops-label']").textContent).not.toMatch(/\d{2} \w{3} \d{4}/);
  expect(view.host.querySelector("[data-testid='owner-po-status-asof']").textContent).toBe("Kondisi Saat Ini");
  view.unmount();
});

test("custom Jan 2099 keeps period empty and snapshot labeled", async () => {
  global.__ownerLocation = {
    pathname: "/dashboard/owner",
    search: "?preset=custom&start=2099-01-01&end=2099-01-31",
  };
  api.ownerDashboard.mockResolvedValue({
    start_date: "2099-01-01",
    end_date: "2099-01-31",
    as_of_date: "2026-10-08",
    loaded_at: "2026-10-08T02:00:00+00:00",
    omzet: { status: "empty", amount: null, invoice_count: 0, detail: "Belum ada faktur pada periode ini" },
    invoice_count: { status: "empty", count: 0 },
    aov_invoice: { status: "empty", amount: null, invoice_count: 0 },
    average_po: { status: "empty", amount: null, po_count: 0 },
    po_active: { status: "complete", count: 2 },
    delivery_late: { status: "complete", count: 1, label: "Pengiriman terlambat (jadwal SJ aktif)" },
    trend: { status: "empty", points: [] },
    profit: { status: "not_ready" },
    operations: {
      status: "complete",
      po_status: { aktif: 2, selesai: 0, terlambat_overlay: 1 },
      stage_breakdown: {
        unit: "po_count",
        active_po_count: 2,
        rows: [{ stage: 8, label: "Operator", count: 1 }],
      },
      delivery: {
        late_count: 1,
        today_count: 0,
        upcoming_count: 0,
        late: [{ id: "s1", po_number: "PO-1", customer: "A", delivery_date: "2026-09-20", status: "terlambat", status_label: "Terlambat", sj_number: "SJ-1" }],
        today: [],
        upcoming: [],
      },
      exceptions: {
        status: "complete",
        items: [{ category: "po", label: "PO HOLD", po_number: "PO-2", detail: "HOLD", href_hint: "status_po" }],
      },
    },
    drilldowns: {},
  });
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(api.ownerDashboard).toHaveBeenCalledWith(expect.objectContaining({
    start_date: "2099-01-01",
    end_date: "2099-01-31",
    preset: "custom",
  }));
  expect(view.host.querySelector("[data-testid='owner-range-label']").textContent).toMatch(/01–31 Jan 2099|2099/);
  expect(view.host.querySelector("[data-testid='owner-ops-label']").textContent).toMatch(/Saat Ini · 08 Okt 2026/);
  expect(view.host.querySelector("[data-testid='owner-kpi-omzet']").textContent).toMatch(/Belum ada invoice|Belum ada/);
  expect(view.host.querySelector("[data-testid='owner-kpi-aov']").getAttribute("data-status")).toMatch(/empty|unavailable/);
  expect(view.host.querySelector("[data-testid='owner-kpi-po-active']").textContent).toMatch(/2/);
  expect(view.host.querySelector("[data-testid='owner-kpi-po-active-badge']").textContent).toBe("Saat Ini");
  expect(view.host.querySelector("[data-testid='owner-exceptions-asof']").textContent).toMatch(/Kondisi Saat Ini · 08 Okt 2026/);
  expect(view.host.textContent).toMatch(/PO HOLD/);
  view.unmount();
});

test("holding role remains allowed", async () => {
  global.__ownerAuth = {
    user: { role: "holding_team", permissions: ["dashboard.owner.view"] },
    hasPerm: (p) => p === "dashboard.owner.view",
    loading: false,
  };
  const view = render();
  await act(async () => { await flush(); await flush(); });
  expect(view.host.querySelector("[data-testid='owner-dashboard']")).toBeTruthy();
  expect(api.ownerDashboard).toHaveBeenCalled();
  view.unmount();
});
