import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import api from "../lib/api";
import { periodRange } from "../lib/financialDashboard";
import SalesDashboard from "./SalesDashboard";

jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={typeof to === "string" ? to : "#"} {...props}>{children}</a>,
}), { virtual: true });

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: { salesDashboard: jest.fn() },
}));

jest.mock("recharts", () => {
  const React = require("react");
  const Pass = ({ children }) => <div>{children}</div>;
  return {
    ResponsiveContainer: Pass,
    LineChart: ({ children, data }) => <div data-testid="sales-line" data-points={data?.length || 0}>{children}</div>,
    BarChart: ({ children, data }) => <div data-testid="sales-bars" data-points={data?.length || 0}>{children}</div>,
    PieChart: Pass,
    Pie: () => null,
    Cell: () => null,
    Line: () => null,
    Bar: () => null,
    XAxis: () => null,
    YAxis: () => null,
    CartesianGrid: () => null,
    Legend: () => null,
    Tooltip: ({ content }) => (typeof content === "function"
      ? content({
          active: true,
          label: "1 Sep",
          coordinate: { x: 12, y: 16 },
          payload: [{ name: "Omzet penjualan", value: 4000, dataKey: "omzet", color: "#176B4D" }],
        })
      : null),
  };
});

global.IS_REACT_ACT_ENVIRONMENT = true;

const pageSrc = fs.readFileSync(path.join(__dirname, "SalesDashboard.jsx"), "utf8");
const helperSrc = fs.readFileSync(path.join(__dirname, "../lib/salesDashboard.js"), "utf8");

const FORBIDDEN = [
  "125.430.000",
  "125430000",
  "98.250.000",
  "27.180.000",
  "27180000",
  "150.000.000",
  "Rp150",
  "83%",
  "Budi Santoso",
  "Siti Aminah",
  "Andi Wijaya",
  "Rina Marlina",
  "Dewi Lestari",
  "Kardus Box A3",
  "Contoh visual",
];

function payload(overrides = {}) {
  return {
    loaded_at: "2026-09-26T10:00:00Z",
    start_date: "2026-09-01",
    end_date: "2026-09-26",
    omzet: {
      status: "complete",
      amount: 4000,
      invoice_count: 1,
      grand_total: 4400,
      basis_note: "Setelah diskon barang, sebelum PPN dan biaya lainnya.",
      comparison: { available: true, percent: 0.25, direction: "up", detail: "Dibanding 2026-08-01 – 2026-08-26" },
    },
    trend: {
      status: "complete",
      grain_label: "Harian",
      points: [{ key: "2026-09-26", label: "26 Sep", omzet: 4000, invoice_count: 1 }],
    },
    settlement: {
      status: "complete",
      scope: "Status pelunasan saat ini untuk faktur terbit pada periode",
      settled_count: 1,
      settled_grand_total: 4400,
      open_count: 0,
      open_outstanding: 0,
      grain_label: "Harian",
      points: [{ key: "2026-09-26", label: "26 Sep", grand_total: 4400, settled_grand: 4400, outstanding: 0 }],
      detail: "Lebih bayar yang belum dipakai ke faktur tidak dihitung sebagai pelunasan.",
    },
    sales: { status: "complete", groups: [{ key: "user:1", label: "Ayu", omzet: 4000, invoice_count: 1 }], detail: null },
    customers: {
      status: "incomplete",
      new_count: 1,
      repeat_count: 0,
      percents_available: false,
      new_percent: null,
      repeat_percent: null,
      detail: "1 faktur tanpa customer_id stabil tidak diklasifikasi.",
    },
    products: { status: "complete", items: [{ key: "master:1", label: "Karton", amount: 4000, quantity: 2, unit: "pcs" }], detail: null },
    receivables: {
      status: "complete",
      outstanding: 1000,
      open_invoice_count: 1,
      customer_count: null,
      coverage_note: "1 faktur berpiutang tanpa customer_id stabil.",
      detail: "Umur piutang dihitung dari sisa tagihan saat permintaan ini.",
      aging: [{ key: "NOT_DUE", label: "Belum jatuh tempo", amount: 1000 }],
    },
    orders: {
      status: "complete",
      total: 1,
      scope: "Saat ini",
      buckets: [
        { key: "aktif", label: "Aktif", count: 1, href: "/penjualan/pesanan-penjualan?status=aktif" },
        { key: "hold", label: "HOLD", count: 0, href: null },
      ],
      list_note: "Daftar dibatasi 1000 pesanan.",
    },
    ...overrides,
  };
}

describe("Dashboard Penjualan source", () => {
  test("keeps current-state labels and has no mockup fallback numbers", () => {
    const combined = `${pageSrc}\n${helperSrc}`;
    FORBIDDEN.forEach((text) => expect(combined).not.toContain(text));
    expect(pageSrc).toContain("Umur piutang · Saat ini");
    expect(pageSrc).toContain('data-testid="sales-orders-scope"');
    expect(pageSrc).toContain("Saat ini");
    expect(pageSrc).not.toContain("Lihat detail");
    expect(pageSrc).not.toContain("Contoh visual");
    expect(pageSrc).toMatch(/dot=\{trendShowsDot\(trendPoints.length\)/);
    expect(pageSrc).toContain("Buka daftar");
  });
});

describe("Dashboard Penjualan runtime", () => {
  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.salesDashboard.mockReset();
    api.salesDashboard.mockResolvedValue(payload());
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

  const click = async (testid) => {
    await act(async () => {
      container.querySelector(`[data-testid="${testid}"]`).dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
  };

  const typeDate = async (testid, value) => {
    const input = container.querySelector(`[data-testid="${testid}"]`);
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
      setter.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();
  };

  test("refetches today, month, and a valid custom range, and reloads", async () => {
    await act(async () => { root.render(<SalesDashboard />); });
    await flush();
    const month = periodRange("month");
    expect(api.salesDashboard).toHaveBeenLastCalledWith({
      start_date: month.start,
      end_date: month.end,
      preset: "month",
    });
    expect(container.querySelector('[data-testid="sales-loaded"]').textContent).toMatch(/Diperbarui/);
    expect(container.querySelector('[data-testid="sales-omzet-delta"]').textContent).toMatch(/↑/);

    await click("sales-preset-today");
    const today = periodRange("today");
    expect(api.salesDashboard).toHaveBeenLastCalledWith({
      start_date: today.start,
      end_date: today.end,
      preset: "today",
    });

    const callsBeforeCustom = api.salesDashboard.mock.calls.length;
    await click("sales-preset-custom");
    expect(api.salesDashboard.mock.calls.length).toBe(callsBeforeCustom);
    expect(container.querySelector('[data-testid="sales-range-note"]').textContent).toMatch(/tanggal awal/i);
    expect(container.querySelector('[data-testid="sales-omzet-value"]').textContent).not.toMatch(/Rp/);

    await typeDate("sales-custom-start", "2026-09-10");
    await typeDate("sales-custom-end", "2026-09-01");
    expect(container.querySelector('[data-testid="sales-range-note"]').textContent).toMatch(/sebelum tanggal akhir/);
    expect(api.salesDashboard.mock.calls.length).toBe(callsBeforeCustom);

    await typeDate("sales-custom-end", "2026-09-20");
    expect(api.salesDashboard).toHaveBeenLastCalledWith({
      start_date: "2026-09-10",
      end_date: "2026-09-20",
      preset: "custom",
    });

    const beforeReload = api.salesDashboard.mock.calls.length;
    await click("sales-reload");
    expect(api.salesDashboard.mock.calls.length).toBe(beforeReload + 1);
  });

  test("shows an empty period, one chart point, a real order link, and a section error without zeroing siblings", async () => {
    api.salesDashboard.mockResolvedValue(payload({
      products: { status: "error", amount: null, items: [], detail: "Baris tidak terbaca." },
      orders: {
        status: "complete",
        total: 2,
        buckets: [
          { key: "aktif", label: "Aktif", count: 1, href: "/penjualan/pesanan-penjualan?status=aktif" },
          { key: "hold", label: "HOLD", count: 1, href: null },
        ],
      },
    }));
    await act(async () => { root.render(<SalesDashboard />); });
    await flush();

    expect(container.querySelector('[data-testid="sales-line"]').getAttribute("data-points")).toBe("1");
    expect(container.querySelector('[data-testid="sales-trend"] .sd-plot').classList.contains("is-series")).toBe(true);
    expect(container.querySelector('[data-testid="sales-omzet-value"]').textContent).toMatch(/4\.000|4,000/);
    expect(container.querySelector('[data-testid="sales-omzet-value"]').classList.contains("sd-hero")).toBe(true);
    expect(container.querySelector('[data-testid="sales-products"]').textContent).toMatch(/Gagal muat/);
    expect(container.querySelector('[data-testid="sales-products"]').textContent).not.toMatch(/Rp\s*0/);
    expect(container.querySelector('[data-testid="sales-order-aktif"]').getAttribute("href")).toBe("/penjualan/pesanan-penjualan?status=aktif");
    expect(container.querySelector('[data-testid="sales-order-aktif"]').textContent).toMatch(/Buka daftar/);
    expect(container.querySelector('[data-testid="sales-order-hold"]').tagName).toBe("DIV");
    expect(container.querySelector('[data-testid="sales-order-hold"]').textContent).not.toMatch(/Buka daftar/);
    expect(container.querySelector('[data-testid="sales-receivables-scope"]').textContent).toBe("Umur piutang · Saat ini");
    expect(container.querySelector('[data-testid="sales-customer-coverage"]').textContent).toMatch(/Persentase belum ditampilkan/);
    expect(document.body.textContent).toMatch(/Omzet penjualan: Rp/);

    api.salesDashboard.mockResolvedValue(payload({
      omzet: { status: "empty", amount: null, grand_total: null, detail: "Belum ada faktur pada periode ini", comparison: { available: false, percent: null } },
      trend: { status: "empty", points: [], detail: "Belum ada faktur pada periode ini" },
      settlement: { status: "empty", points: [], detail: "Belum ada faktur pada periode ini" },
      sales: { status: "empty", groups: [], detail: "Belum ada faktur pada periode ini" },
      customers: { status: "empty", detail: "Belum ada faktur pada periode ini", percents_available: false },
      products: { status: "empty", items: [], detail: "Belum ada faktur pada periode ini" },
    }));
    await click("sales-reload");
    const emptyOmzet = container.querySelector('[data-testid="sales-omzet-value"]');
    expect(emptyOmzet.textContent).toBe("Belum ada faktur pada periode ini");
    expect(emptyOmzet.classList.contains("sd-empty")).toBe(true);
    expect(emptyOmzet.classList.contains("sd-hero")).toBe(false);
    expect(container.querySelector('[data-testid="sales-trend"]').textContent).toMatch(/Belum ada faktur pada periode ini/);
    expect(container.querySelector('[data-testid="sales-trend"] .sd-plot').classList.contains("is-message")).toBe(true);
    expect(container.querySelector('[data-testid="sales-by-sales"] .sd-plot').classList.contains("is-message")).toBe(true);
    expect(container.querySelector('[data-testid="sales-settlement"] .sd-plot').classList.contains("is-message")).toBe(true);
    expect(container.textContent).not.toMatch(/125\.430|98\.250|27\.180|Budi Santoso|Contoh visual/);
    expect(container.querySelector('[data-testid="sales-omzet-delta"]').textContent).toContain("—");
    expect(container.querySelector('[data-testid="sales-omzet-delta"]').textContent).not.toMatch(/↑|↓/);
  });
});
