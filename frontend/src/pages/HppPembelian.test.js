import { act } from "react";
import { createRoot } from "react-dom/client";
import fs from "fs";
import path from "path";
import { NAV_KEYS, purchasingMenuItems } from "../lib/navAccess";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("react-router-dom", () => {
  const React = require("react");
  return {
    NavLink: ({ to, children, className, ...props }) => (
      <a href={to} className={typeof className === "function" ? className({ isActive: false }) : className} {...props}>{children}</a>
    ),
  };
}, { virtual: true });

const has = (...keys) => (key) => keys.includes(key);
const mockHasPerm = jest.fn(() => true);

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "hpp-ui", name: "Pengguna Uji", role: "admin" },
    hasPerm: (key) => mockHasPerm(key),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    purchaseHpp: jest.fn(),
    purchaseHppItem: jest.fn(),
    purchaseHppExport: jest.fn(),
  },
}));

import HppPembelian from "./HppPembelian";

const mockApi = require("../lib/api").default;
const pageSrc = fs.readFileSync(path.join(__dirname, "HppPembelian.jsx"), "utf8");
const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
const flowSrc = fs.readFileSync(path.join(__dirname, "PurchasingFlowNav.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

const sampleRow = {
  master_item_id: "item-1",
  item_code: "HPP-REV-1",
  item_name: "Karton Uji",
  unit: "PCS",
  stock_fisik: 200,
  hpp_status: "ADA_HPP",
  hpp_status_label: "Ada HPP",
  current_hpp: 1150,
  inventory_value: 230000,
  latest_gr_id: "gr-2",
  latest_gr_number: "GR-2",
  latest_gr_date: "2026-10-06",
  latest_po_number: "PB-2",
  latest_supplier: "Supplier HPP",
  latest_gr_qty: 100,
  latest_purchase_unit_cost: 1300,
  hpp_before_latest_gr: 1000,
  hpp_after_latest_gr: 1150,
  price_vs_hpp_delta: 300,
  price_vs_hpp_pct: 30,
  hpp_current_diverges_from_last_gr: false,
  bootstrap_first_gr: false,
};

const downRow = {
  ...sampleRow,
  master_item_id: "item-2",
  item_code: "HPP-REV-2",
  item_name: "Lakban Turun",
  latest_purchase_unit_cost: 800,
  hpp_before_latest_gr: 1000,
  hpp_after_latest_gr: 900,
  price_vs_hpp_delta: -200,
  price_vs_hpp_pct: -20,
};

function flush() {
  return act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("HPP Pembelian navigation", () => {
  test("menu places HPP Pembelian after Pembayaran and keeps the transaction sequence intact", () => {
    const withoutHpp = purchasingMenuItems(has(
      NAV_KEYS.purchasing,
      NAV_KEYS.purchaseRequests,
      NAV_KEYS.purchaseOrders,
      NAV_KEYS.purchaseInvoices,
      NAV_KEYS.purchasePayments,
      NAV_KEYS.purchaseReceipts,
    )).map((item) => item.label);
    expect(withoutHpp).toEqual([
      "Permintaan",
      "Pesanan Pembelian",
      "Uang Muka",
      "Penerimaan Barang",
      "Retur Pembelian",
      "Faktur Pembelian",
      "Pembayaran Pembelian",
    ]);
    const withHpp = purchasingMenuItems(has(
      NAV_KEYS.purchasing,
      NAV_KEYS.purchaseRequests,
      NAV_KEYS.purchaseOrders,
      NAV_KEYS.purchaseInvoices,
      NAV_KEYS.purchasePayments,
      NAV_KEYS.purchaseReceipts,
      NAV_KEYS.purchaseHpp,
    )).map((item) => item.label);
    expect(withHpp).toEqual([...withoutHpp, "HPP Pembelian"]);
    expect(purchasingMenuItems(has(NAV_KEYS.purchasing, NAV_KEYS.purchaseHpp))[0].to).toBe("/pembelian/hpp-pembelian");
    expect(appSrc).toMatch(/path="\/pembelian\/hpp-pembelian"/);
    expect(appSrc).toMatch(/purchase_hpp\.view/);
    expect(appSrc).toMatch(/NAV_KEYS\.purchaseHpp/);
  });

  test("does not join PurchasingFlowNav", () => {
    expect(pageSrc).not.toMatch(/PurchasingFlowNav/);
    expect(flowSrc).not.toMatch(/hpp-pembelian/);
    expect(flowSrc).not.toMatch(/HPP Pembelian/);
  });
});

describe("HPP Pembelian page", () => {
  let container;
  let root;

  beforeEach(() => {
    mockApi.purchaseHpp.mockResolvedValue({ rows: [sampleRow, downRow], suppliers: ["Supplier HPP"] });
    mockApi.purchaseHppItem.mockResolvedValue({
      ...sampleRow,
      history: [{
        recognized_at: "2026-10-06T00:00:00+00:00",
        document_date: "2026-10-06",
        source_type: "GOODS_RECEIPT",
        source_type_label: "Penerimaan Barang",
        status: "RECOGNIZED",
        supplier: "Supplier HPP",
        po_number: "PB-2",
        gr_number: "GR-2",
        source_id: "gr:gr-2:line",
        qty: 100,
        incoming_unit_cost: 1300,
        incoming_cost_label: "Harga Pembelian",
        hpp_before: 1000,
        hpp_after: 1150,
        price_delta: 300,
        price_pct: 30,
      }],
    });
    mockApi.purchaseHppExport.mockResolvedValue(new Blob(["xlsx"]));
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.clearAllMocks();
  });

  test("main table stays to identity columns and shows mover summaries", async () => {
    await act(async () => {
      root.render(<HppPembelian />);
    });
    await flush();
    expect(container.querySelector('[data-testid="hpp-pembelian-page"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="hpp-pembelian-title"]').textContent).toBe("HPP Pembelian");
    expect(container.querySelector('[data-testid="hpp-pembelian-table"]').textContent).toMatch(/Kode Barang/);
    expect(container.querySelector('[data-testid="hpp-pembelian-table"]').textContent).toMatch(/Status HPP/);
    expect(container.querySelector('[data-testid="hpp-pembelian-table"]').textContent).not.toMatch(/HPP Sebelum Pembelian Terakhir/);
    expect(container.querySelector('[data-testid="hpp-pembelian-table"]').textContent).not.toMatch(/Harga Pembelian Terbaru/);
    expect(container.querySelector('[data-testid="hpp-stock"]').textContent).toContain("200");
    expect(container.querySelector('[data-testid="hpp-status"]').textContent).toBe("Ada HPP");
    expect(container.querySelector('[data-testid="hpp-top-up"]').textContent).toMatch(/Karton Uji/);
    expect(container.querySelector('[data-testid="hpp-top-up"]').textContent).toMatch(/1\.000\s*→\s*1\.300/);
    expect(container.querySelector('[data-testid="hpp-top-up"]').textContent).toMatch(/\+300/);
    expect(container.querySelector('[data-testid="hpp-top-up"]').textContent).toMatch(/\+30%/);
    expect(container.querySelector('[data-testid="hpp-top-down"]').textContent).toMatch(/Lakban Turun/);
    expect(container.querySelector('[data-testid="hpp-top-down"]').textContent).toMatch(/-200/);
    expect(container.querySelector('[data-testid="hpp-pembelian-export"]')).toBeTruthy();
    expect(apiSrc).toMatch(/purchaseHppExport/);
    expect(pageSrc).toMatch(/const MOVER_LIMIT = 10/);
    expect(pageSrc).toMatch(/b\._delta - a\._delta/);
    expect(pageSrc).toMatch(/a\._delta - b\._delta/);
  });

  test("row click opens right drawer with analysis and history", async () => {
    await act(async () => {
      root.render(<HppPembelian />);
    });
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="hpp-list-row"]').click();
    });
    await flush();
    expect(mockApi.purchaseHppItem).toHaveBeenCalledWith("item-1");
    expect(container.querySelector('[data-testid="hpp-detail"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="hpp-drawer-scrim"]')).toBeTruthy();
    const analysis = container.querySelector('[data-testid="hpp-detail-current"]').textContent;
    expect(analysis.indexOf("HPP Sebelum Pembelian Terakhir")).toBeLessThan(analysis.indexOf("Harga Pembelian Terbaru"));
    expect(analysis.indexOf("Harga Pembelian Terbaru")).toBeLessThan(analysis.indexOf("Selisih"));
    expect(analysis.indexOf("Selisih")).toBeLessThan(analysis.indexOf("Persentase"));
    expect(analysis.indexOf("Persentase")).toBeLessThan(analysis.indexOf("HPP Setelah Pembelian Terakhir"));
    expect(container.querySelector('[data-testid="hpp-detail-before"]').textContent).toContain("1.000");
    expect(container.querySelector('[data-testid="hpp-detail-price"]').textContent).toContain("1.300");
    expect(container.querySelector('[data-testid="hpp-detail-delta"]').textContent).toContain("+300");
    expect(container.querySelector('[data-testid="hpp-detail-pct"]').textContent).toContain("+30%");
    expect(container.querySelector('[data-testid="hpp-detail-receipt"]').textContent).toMatch(/GR-2/);
    expect(container.querySelector('[data-testid="hpp-history-type"]').textContent).toMatch(/Penerimaan Barang/);
    expect(container.querySelector('[data-testid="hpp-history-type"]').textContent).toMatch(/Supplier HPP/);
    expect(container.querySelector('[data-testid="hpp-history-table"]').textContent).not.toMatch(/HPP Sebelum Pembelian Terakhir/);
    expect(pageSrc).toMatch(/hpp-drawer/);
    expect(pageSrc).not.toMatch(/gradient/);
    expect(pageSrc).not.toMatch(/PurchasingFlowNav/);
  });
});
