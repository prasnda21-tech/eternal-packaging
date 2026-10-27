/**
 * @jest-environment jsdom
 */
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import DeliveryPlanPage from "./DeliveryPlanPage";

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    deliveryPlan: jest.fn(),
  },
}));

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ user: { id: "u-test", email: "test@eternal.local" }, hasPerm: () => true }),
}));

const mockSearchParams = new URLSearchParams("");
jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...rest }) => <a href={to} {...rest}>{children}</a>,
  useNavigate: () => jest.fn(),
  useSearchParams: () => [mockSearchParams, jest.fn()],
}), { virtual: true });

jest.mock("react-day-picker", () => ({
  DayPicker: () => <div data-testid="dp-daterange-calendar-mock" />,
}));

jest.mock("react-day-picker/dist/style.css", () => ({}), { virtual: true });

const api = require("../lib/api").default;

function renderPage() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(<DeliveryPlanPage />);
  });
  return { container, root };
}

function setInputValue(el, value) {
  const proto = el.tagName === "SELECT" ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}

beforeEach(() => {
  api.deliveryPlan.mockReset();
  document.body.innerHTML = "";
});

test("loading empty and read-only actions", async () => {
  let resolvePlan;
  api.deliveryPlan.mockReturnValue(new Promise((r) => { resolvePlan = r; }));
  const { container, root } = renderPage();
  expect(container.querySelector('[data-testid="delivery-plan-loading"]')).toBeTruthy();
  await act(async () => {
    resolvePlan({ rows: [], total: 0, total_pages: 1, page: 1, counts: {} });
    await Promise.resolve();
  });
  expect(container.querySelector('[data-testid="delivery-plan-table-empty"]')?.textContent).toMatch(
    /Tidak ada PO dengan tanggal pengiriman/,
  );
  expect(container.querySelector('[data-testid="delivery-plan-page"]')).toBeTruthy();
  expect(container.textContent).not.toMatch(/Buat Surat Jalan|Simpan Jadwal|Approve/);
  act(() => root.unmount());
});

test("renders rows source badges remaining and po link", async () => {
  api.deliveryPlan.mockResolvedValue({
    rows: [
      {
        id: "o1",
        po_number: "PO/1",
        customer_name: "Cust A",
        branch: "Jogja",
        tanggal_pengiriman: "2026-10-20",
        tanggal_pengiriman_source: "manual",
        qty_total: 100,
        qty_delivered: 25,
        qty_remaining: 75,
        delivery_schedule_status: "hari_ini",
        delivery_schedule_status_label: "Hari Ini",
      },
      {
        id: "o2",
        po_number: "PO/2",
        customer_name: "Cust B",
        branch: "",
        tanggal_pengiriman: "2026-10-22",
        tanggal_pengiriman_source: "automatic",
        qty_total: 50,
        qty_delivered: 0,
        qty_remaining: 50,
        delivery_schedule_status: "terjadwal",
        delivery_schedule_status_label: "Terjadwal",
      },
    ],
    total: 2,
    total_pages: 1,
    page: 1,
  });
  const { container, root } = renderPage();
  await act(async () => { await Promise.resolve(); });
  expect(container.querySelector('[data-testid="dp-source-PO/1"]')?.textContent).toMatch(/Manual/);
  expect(container.querySelector('[data-testid="dp-source-PO/2"]')?.textContent).toBe("Otomatis");
  expect(container.querySelector('[data-testid="dp-remaining-PO/1"]')?.textContent).toBe("75");
  expect(container.querySelector('[data-testid="dp-status-PO/1"]')?.textContent).toMatch(/Hari Ini/);
  const link = container.querySelector('[data-testid="dp-open-po-PO/1"]');
  expect(link?.getAttribute("href")).toBe("/po/o1");
  expect(api.deliveryPlan).toHaveBeenCalled();
  act(() => root.unmount());
});

test("filter apply sends search params to api", async () => {
  api.deliveryPlan.mockResolvedValue({ rows: [], total: 0, total_pages: 1, page: 1 });
  const { container, root } = renderPage();
  await act(async () => { await Promise.resolve(); });
  await act(async () => {
    setInputValue(container.querySelector('[data-testid="dp-filter-q"]'), "PO/X");
    setInputValue(container.querySelector('[data-testid="dp-filter-source"]'), "manual");
  });
  await act(async () => {
    container.querySelector('[data-testid="dp-filter-apply"]').click();
    await Promise.resolve();
  });
  const last = api.deliveryPlan.mock.calls.at(-1)?.[0] || {};
  expect(last.q).toBe("PO/X");
  expect(last.source).toBe("manual");
  expect(last.page).toBe(1);
  act(() => root.unmount());
});

test("default sort is tanggal_pengiriman DESC and reset restores it", async () => {
  api.deliveryPlan.mockResolvedValue({ rows: [], total: 0, total_pages: 1, page: 1 });
  const { container, root } = renderPage();
  await act(async () => { await Promise.resolve(); });
  const first = api.deliveryPlan.mock.calls[0]?.[0] || {};
  expect(first.sort_by).toBe("tanggal_pengiriman");
  expect(first.sort_dir).toBe("desc");
  const sortBtn = container.querySelector('[data-testid="dp-sort-delivery-plan-tanggal_pengiriman"]');
  expect(sortBtn?.classList.contains("is-active")).toBe(true);
  expect(sortBtn?.querySelector("svg")).toBeTruthy();
  await act(async () => {
    container.querySelector('[data-testid="dp-filter-reset"]').click();
    await Promise.resolve();
  });
  const afterReset = api.deliveryPlan.mock.calls.at(-1)?.[0] || {};
  expect(afterReset.sort_by).toBe("tanggal_pengiriman");
  expect(afterReset.sort_dir).toBe("desc");
  act(() => root.unmount());
});

test("Rincian Pengiriman fetches all pages not only current page", async () => {
  api.deliveryPlan.mockImplementation(async (params = {}) => {
    if (!params.page || params.page === 1) {
      if (params.page_size === 200) {
        return {
          rows: [
            {
              id: "p1",
              po_number: "PO/1",
              customer_name: "A",
              delivery_zone: "JT",
              tanggal_pengiriman: "2026-10-27",
              qty_total: 10,
              qty_delivered: 2,
              qty_remaining: 8,
            },
          ],
          total: 2,
          total_pages: 2,
          page: 1,
          totals: { total_po: 2, qty_total: 30, qty_delivered: 2, qty_remaining: 28 },
        };
      }
      return {
        rows: [
          {
            id: "p1",
            po_number: "PO/1",
            customer_name: "A",
            delivery_zone: "JT",
            tanggal_pengiriman: "2026-10-27",
            qty_total: 10,
            qty_delivered: 2,
            qty_remaining: 8,
          },
        ],
        total: 2,
        total_pages: 2,
        page: 1,
        page_size: 50,
        totals: { total_po: 2, qty_total: 30, qty_delivered: 2, qty_remaining: 28 },
      };
    }
    return {
      rows: [
        {
          id: "p2",
          po_number: "PO/2",
          customer_name: "B",
          delivery_zone: "JS",
          tanggal_pengiriman: "2026-10-24",
          qty_total: 20,
          qty_delivered: 0,
          qty_remaining: 20,
        },
      ],
      total: 2,
      total_pages: 2,
      page: 2,
      totals: { total_po: 2, qty_total: 30, qty_delivered: 2, qty_remaining: 28 },
    };
  });
  const { container, root } = renderPage();
  await act(async () => { await Promise.resolve(); });
  await act(async () => {
    container.querySelector('[data-testid="dp-rincian-pengiriman"]').click();
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(container.querySelector('[data-testid="dp-rincian-modal"]')).toBeTruthy();
  const detailCalls = api.deliveryPlan.mock.calls.filter((c) => c[0]?.page_size === 200);
  expect(detailCalls.length).toBeGreaterThanOrEqual(2);
  expect(detailCalls.every((c) => c[0].sort_by === "tanggal_pengiriman")).toBe(true);
  // KPI / pager / modal must share authoritative filtered totals.total_po + total.
  expect(container.querySelector('[data-testid="dp-kpi-total-po"]')?.textContent).toBe("2");
  expect(container.querySelector('[data-testid="delivery-plan-total"]')?.textContent).toMatch(/2\s*PO/);
  expect(container.querySelector('[data-testid="dp-rincian-po-count"]')?.textContent).toBe("2");
  expect(container.querySelector('[data-testid="dp-rincian-qty-pesanan"]')?.textContent).toBe("30");
  expect(container.querySelector('[data-testid="dp-kpi-qty-total"]')?.textContent).toBe("30");
  expect(container.querySelector('[data-testid="dp-rincian-day-2026-10-27"]')).toBeTruthy();
  expect(container.querySelector('[data-testid="dp-rincian-day-2026-10-24"]')).toBeTruthy();
  const dayOrder = Array.from(container.querySelectorAll('[data-testid^="dp-rincian-day-"]'))
    .map((el) => el.getAttribute("data-testid"));
  expect(dayOrder.indexOf("dp-rincian-day-2026-10-27")).toBeLessThan(dayOrder.indexOf("dp-rincian-day-2026-10-24"));
  act(() => root.unmount());
});

test("KPI Total PO matches meta.total and modal when totals.total_po is present", async () => {
  const rows = [
    { id: "a", po_number: "PO-A", customer_name: "Alpha", tanggal_pengiriman: "2026-10-27", qty_total: 5000, qty_delivered: 1000, qty_remaining: 4000 },
    { id: "b", po_number: "PO-B", customer_name: "Beta", tanggal_pengiriman: "2026-10-25", qty_total: 3000, qty_delivered: 500, qty_remaining: 2500 },
    { id: "c", po_number: "PO-C", customer_name: "Gamma", tanggal_pengiriman: "2026-10-27", qty_total: 2000, qty_delivered: 0, qty_remaining: 2000 },
    { id: "d", po_number: "PO-D", customer_name: "Delta", tanggal_pengiriman: "2026-10-24", qty_total: 8000, qty_delivered: 8000, qty_remaining: 0 },
    { id: "e", po_number: "PO-E", customer_name: "Epsilon", tanggal_pengiriman: "2026-10-26", qty_total: 1500, qty_delivered: 200, qty_remaining: 1300 },
  ];
  const totals = { total_po: 5, qty_total: 19500, qty_delivered: 9700, qty_remaining: 9800 };
  api.deliveryPlan.mockImplementation(async (params = {}) => ({
    rows: params.page_size === 200 ? rows : rows.slice(0, params.page_size || 50),
    total: 5,
    total_pages: 1,
    page: 1,
    page_size: params.page_size || 50,
    totals,
    counts: { total: 5, by_status: { terjadwal: 5 } },
  }));
  const { container, root } = renderPage();
  await act(async () => { await Promise.resolve(); });
  expect(container.querySelector('[data-testid="dp-kpi-total-po"]')?.textContent).toBe("5");
  expect(container.querySelector('[data-testid="delivery-plan-total"]')?.textContent).toMatch(/5\s*PO/);
  expect(container.querySelector('[data-testid="dp-kpi-qty-total"]')?.textContent).toBe("19.500");
  expect(container.querySelector('[data-testid="dp-kpi-qty-delivered"]')?.textContent).toBe("9.700");
  expect(container.querySelector('[data-testid="dp-kpi-qty-remaining"]')?.textContent).toBe("9.800");
  expect(container.querySelectorAll('tbody tr').length).toBe(5);
  await act(async () => {
    container.querySelector('[data-testid="dp-rincian-pengiriman"]').click();
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(container.querySelector('[data-testid="dp-rincian-po-count"]')?.textContent).toBe("5");
  expect(container.querySelector('[data-testid="dp-rincian-qty-pesanan"]')?.textContent).toBe("19.500");
  expect(container.querySelector('[data-testid="dp-rincian-qty-terkirim"]')?.textContent).toBe("9.700");
  expect(container.querySelector('[data-testid="dp-rincian-sisa-qty"]')?.textContent).toBe("9.800");
  act(() => root.unmount());
});

test("page wires route api and no mutate verbs", () => {
  const src = fs.readFileSync(path.join(__dirname, "DeliveryPlanPage.jsx"), "utf8");
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  expect(src).toMatch(/api\.deliveryPlan/);
  expect(src).not.toMatch(/api\.(create|post|patch|put|delete)/i);
  expect(app).toMatch(/path="\/produksi\/rencana-jadwal-kirim"/);
  expect(app).toMatch(/DeliveryPlanPage/);
});

test("Nomor PO cell keeps identity badges visible (no clip)", () => {
  const src = fs.readFileSync(path.join(__dirname, "DeliveryPlanPage.jsx"), "utf8");
  const tableCss = fs.readFileSync(
    path.join(__dirname, "../components/deliveryPlan/DeliveryPlanAdvancedTable.css"),
    "utf8",
  );
  expect(src).toMatch(/PoIdentityBadges/);
  expect(src).toMatch(/dp-po-identity/);
  expect(src).toMatch(/minWidth:\s*200/);
  expect(tableCss).toMatch(/td:has\(\.dp-po-identity\)/);
  expect(tableCss).toMatch(/\.dp-po-identity\s*\{/);
});

test("compact ux9 contracts and akan datang label", async () => {
  const src = fs.readFileSync(path.join(__dirname, "DeliveryPlanPage.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "DeliveryPlanPage.css"), "utf8");
  expect(src).toMatch(/DeliveryPlanAdvancedTable/);
  expect(src).toMatch(/encodeHeaderFilters/);
  expect(src).toMatch(/Akan Datang/);
  expect(src).toMatch(/DeliveryPlanDateRangeField/);
  expect(src).toMatch(/label:\s*"Keterangan"/);
  expect(src).toMatch(/label:\s*"Zona"/);
  const rangeSrc = fs.readFileSync(
    path.join(__dirname, "../components/deliveryPlan/DeliveryPlanDateRangeField.jsx"),
    "utf8",
  );
  expect(rangeSrc).toMatch(/data-testid=\{testIdPrefix\}/);
  expect(rangeSrc).toMatch(/testIdPrefix = "dp-period"/);
  expect(rangeSrc).toMatch(/DayPicker/);
  expect(rangeSrc).toMatch(/mode="range"/);
  expect(css).toMatch(/--dp-green:\s*#166534/);
  expect(css).toMatch(/overflow-x:\s*hidden/);
  expect(css).toMatch(/dp-notes-cell/);
  api.deliveryPlan.mockResolvedValue({
    rows: [{
      id: "o9",
      po_number: "PO/9",
      customer_name: "Cust",
      branch: "YGY",
      delivery_zone: "JT",
      notes: "SABLON MERAH\nCETAK 2 WARNA",
      tanggal_pengiriman: "2026-10-26",
      tanggal_pengiriman_source: "automatic",
      delivery_sla_effective: {
        date: "2026-10-26",
        source: "legacy",
        contract: "legacy_working_days",
        countable: false,
        estimate_adjusted: true,
        estimate_from: "2026-10-25",
        estimate_to: "2026-10-26",
        legacy_calendar_date: "2026-10-25",
      },
      qty_total: 10,
      qty_delivered: 0,
      qty_remaining: 10,
      delivery_schedule_status: "terjadwal",
      delivery_schedule_status_label: "Terjadwal",
    }],
    total: 1,
    total_pages: 1,
    page: 1,
    totals: { total_po: 1, qty_total: 10, qty_delivered: 0, qty_remaining: 10 },
    counts: { by_status: { terjadwal: 1 } },
  });
  const { container, root } = renderPage();
  await act(async () => { await Promise.resolve(); });
  expect(container.querySelector('[data-testid="dp-source-PO/9"]')?.textContent).toBe("Otomatis (Legacy · Hari Kerja)");
  expect(container.querySelector('[data-testid="dp-estimate-notice-PO/9"]')?.textContent)
    .toBe("Estimasi menyesuaikan hari kerja: 25 Okt → 26 Okt");
  expect(container.querySelector('[data-testid="dp-status-PO/9"]')?.textContent).toMatch(/Akan Datang/);
  expect(container.querySelector('[data-testid="dp-kpi-total-po"]')?.textContent).toBe("1");
  expect(container.querySelector('[data-testid="dp-reset-cols-delivery-plan"]')).toBeTruthy();
  expect(container.querySelector('[data-testid="dp-notes-cell"]')?.textContent).toMatch(/SABLON MERAH/);
  expect(container.textContent).toMatch(/JT/);
  expect(container.querySelector('[data-testid="dp-period-trigger"]')).toBeTruthy();
  act(() => root.unmount());
});

test("date range apply sends date_from and date_to", async () => {
  api.deliveryPlan.mockResolvedValue({ rows: [], total: 0, total_pages: 1, page: 1 });
  const { container, root } = renderPage();
  await act(async () => { await Promise.resolve(); });
  await act(async () => {
    container.querySelector('[data-testid="dp-period-trigger"]').click();
    await Promise.resolve();
  });
  expect(container.querySelector('[data-testid="dp-period-panel"]')).toBeTruthy();
  expect(container.querySelectorAll('input[type="date"]').length).toBe(0);
  act(() => root.unmount());
});
