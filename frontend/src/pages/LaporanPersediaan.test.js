/** @jest-environment jsdom */
import React from "react";
import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";

jest.mock("react-router-dom", () => {
  const mock = require("../test/reactRouterMock");
  return {
    ...mock,
    useNavigate: () => jest.fn(),
    Link: ({ to, children, ...rest }) => <a href={to} {...rest}>{children}</a>,
    Navigate: ({ to }) => <div data-testid="navigate" data-to={to} />,
  };
}, { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    hasPerm: (k) => ["current_stock.view", "current_stock.export", "master_items.view"].includes(k),
    user: { id: "u1", email: "a@test.com" },
  }),
}));

jest.mock("../lib/api", () => {
  const api = {
    stockReportCatalog: jest.fn(() => Promise.resolve({
      reports: [{
        id: "kartu-stok",
        name: "Kartu Stok / Mutasi Barang",
        implemented: true,
        columns: [
          { key: "waktu", label: "Tanggal/Waktu", align: "left", default: true },
          { key: "qty_masuk", label: "Qty Masuk", align: "right", default: true },
          { key: "status_histori", label: "Status Histori", align: "left", default: true },
        ],
        movement_types: ["OPENING_BALANCE", "PURCHASE_RECEIPT"],
        movement_labels: { OPENING_BALANCE: "Saldo Awal", PURCHASE_RECEIPT: "Penerimaan Pembelian" },
      }],
      branches: [{ id: "br-a", label: "Jogja" }],
      warehouses: [{ id: "wh-a1", branch_id: "br-a", label: "Gudang A1" }],
      memorized: [],
    })),
    stockReportShow: jest.fn(() => Promise.resolve({
      name: "Kartu Stok / Mutasi Barang",
      company_name: "Eternal",
      period_label: "Dari 01 Mar 2026 s/d 31 Mar 2026",
      branch_label: "Lokasi : Gudang A1",
      history_status: "incomplete",
      history_status_label: "Histori Tidak Lengkap",
      gap: "Histori Tidak Lengkap",
      columns: [
        { key: "waktu", label: "Tanggal/Waktu", align: "left" },
        { key: "qty_masuk", label: "Qty Masuk", align: "right" },
      ],
      rows: [],
      totals: {},
      page: 1,
      page_count: 1,
    })),
    stockReportExport: jest.fn(() => Promise.resolve(new Blob(["xlsx"]))),
    stockReportMemorize: jest.fn(() => Promise.resolve({ id: "m1" })),
    masterItems: jest.fn(() => Promise.resolve({ items: [] })),
  };
  return { __esModule: true, api, default: api };
});

import { MemoryRouter } from "react-router-dom";
import { api } from "../lib/api";
import LaporanPersediaan from "./LaporanPersediaan";

function renderAt(path) {
  const search = String(path || "").includes("?") ? String(path).slice(String(path).indexOf("?")) : "";
  require("../test/reactRouterMock").__reset(search);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter>
        <LaporanPersediaan />
      </MemoryRouter>,
    );
  });
  return { container, root };
}

describe("LaporanPersediaan kartu-stok", () => {
  test("loads catalog and renders filter/export controls", async () => {
    expect(typeof api.stockReportCatalog).toBe("function");
    const { container, root } = renderAt("/laporan/persediaan?report=kartu-stok");
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(api.stockReportCatalog).toHaveBeenCalled();
    expect(container.textContent).toContain("Kartu Stok");
    expect(container.querySelector('[data-testid="stock-report-filter"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-report-export"]')).toBeTruthy();
    act(() => root.unmount());
  });

  test("filter popover exposes structured fields and primary apply", async () => {
    const { container, root } = renderAt("/laporan/persediaan?report=kartu-stok");
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      container.querySelector('[data-testid="stock-report-filter"]').click();
    });
    expect(container.querySelector('[data-testid="erw-settings-popover"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-report-filter-panel"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-report-item-query"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-report-warehouse"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-report-branch"]')).toBeTruthy();
    const apply = container.querySelector('[data-testid="stock-report-apply-filter"]');
    expect(apply).toBeTruthy();
    expect(apply.textContent).toMatch(/Terapkan & Tampilkan/);
    act(() => root.unmount());
  });
});
