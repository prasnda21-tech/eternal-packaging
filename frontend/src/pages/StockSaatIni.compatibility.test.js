import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import StockSaatIni from "./StockSaatIni";

jest.mock("react-router-dom", () => require("../test/reactRouterMock"), { virtual: true });

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: () => true }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    branches: jest.fn(),
    warehouses: jest.fn(),
    physicalStockCatalog: jest.fn(),
    physicalStockStatusSummary: jest.fn().mockResolvedValue({ counts: {}, operational_actions: [], read_only: true }),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("Stock Saat Ini compatibility mode", () => {
  beforeEach(() => {
    api.physicalStockStatusSummary.mockResolvedValue({
      counts: {},
      operational_actions: [],
      read_only: true,
    });
  });

  let container;
  let root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.branches.mockResolvedValue({ items: [] });
    api.warehouses.mockResolvedValue({ items: [] });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.clearAllMocks();
  });

  test("shows compatibility read mode, belum terverifikasi, and belum valid tersedia", async () => {
    api.physicalStockCatalog.mockResolvedValue({
      stock_read_mode: "compatibility",
      r2_authority_active: false,
      can_adjust_physical_stock: false,
      physical_qty_warehouse_valid: false,
      qty_tersedia_valid: false,
      master_item_count: 1,
      warehouse_count: 0,
      total: 1,
      page: 1,
      page_count: 1,
      items: [{
        master_item_id: "m1",
        item_code: "BOX",
        item_name: "Box",
        base_unit: "PCS",
        physical_qty: 15900,
        physical_qty_warehouse_valid: false,
        qty_pesanan: 600,
        qty_belum_terkirim: null,
        qty_tersedia: 15300,
        qty_tersedia_valid: false,
        belum_terkirim_status: "BELUM_TERVERIFIKASI",
        baseline_status: "unreconciled",
        baseline_label: "Stok fisik legacy (perusahaan) — bukan saldo per gudang",
      }],
    });
    await act(async () => {
      root.render(<StockSaatIni />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="stock-read-mode"]')?.textContent).toMatch(/kompatibilitas/i);
    expect(container.querySelector('[data-testid="stock-read-mode"]')?.textContent).toMatch(/perusahaan/i);
    expect(container.querySelector('[data-testid="stock-compat-branch-note"]')?.textContent).toMatch(/perusahaan|gudang/i);
    expect(container.querySelector('[data-testid="stock-adjust-blocked-reason"]')?.textContent).toMatch(/r2_authority_active|warehouse_stock_standalone|R2/i);
    expect(container.querySelector('[data-testid="saldo-awal-button"]')?.disabled).toBe(true);
    expect(container.querySelector('[data-testid="stock-opname-button"]')?.disabled).toBe(true);
    expect(container.querySelector('[data-testid="stock-adjust-blocked-reason"]')?.textContent).not.toMatch(/Preview|R2 OFF/i);
    expect(container.querySelector('[data-testid="stock-physical-header"]')?.textContent).toMatch(/^Stock$/);
    expect(container.querySelector('[data-testid="stock-physical-header"]')?.getAttribute("data-physical-scope")).toBe("COMPANY");
    expect(container.querySelector('[data-testid="stock-branch-select-input"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-siap-jual-header"]')?.textContent).toMatch(/Stock Dapat Dijual/i);
    expect(container.querySelector('[data-testid="stock-polos-header"]')?.textContent).toMatch(/Qty Polos/i);
    expect(container.querySelector('[data-testid="stock-excel-formula-note"]')?.textContent).toMatch(/Stock Dapat Dijual/i);
    expect(container.querySelector('[data-testid="stock-physical-m1"]')?.getAttribute("data-baseline-label"))
      .toMatch(/legacy|gudang|direkonsiliasi/i);
    expect(container.querySelector('[data-testid="stock-physical-m1"]')?.textContent)
      .not.toMatch(/Stok fisik legacy|Saldo awal belum direkonsiliasi/i);
    expect(container.querySelector('[data-testid="stock-unit-header"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-detail-header"]')?.textContent).toMatch(/Aksi/i);
    expect(container.querySelector('[data-testid="stock-detail-m1"]')?.textContent).toMatch(/Lihat Detail/i);
  });

  test("legacy CUTOVER_PENDING status still keeps compact headers", async () => {
    api.physicalStockCatalog.mockResolvedValue({
      stock_read_mode: "compatibility",
      r2_authority_active: false,
      can_adjust_physical_stock: false,
      total: 1,
      page: 1,
      page_count: 1,
      items: [{
        master_item_id: "m2",
        item_code: "CUP",
        item_name: "Cup",
        base_unit: "PCS",
        physical_qty: 10,
        qty_pesanan: 1,
        qty_belum_terkirim: null,
        qty_tersedia: 9,
        qty_tersedia_valid: false,
        physical_qty_warehouse_valid: false,
        belum_terkirim_status: "CUTOVER_PENDING",
        cetak_sudah_dikirim: null,
        cetak_sudah_dikirim_status: "UNVERIFIED",
      }],
    });
    await act(async () => {
      root.render(<StockSaatIni />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="stock-physical-header"]')?.textContent).toMatch(/^Stock$/);
    expect(container.querySelector('[data-testid="stock-cetak-dikirim-header"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-polos-header"]')?.textContent).toMatch(/Qty Polos/i);
    expect(container.querySelector('[data-testid="stock-detail-m2"]')).toBeTruthy();
  });

  test("shows R2 mode when authority active", async () => {
    api.physicalStockCatalog.mockResolvedValue({
      stock_read_mode: "r2",
      r2_authority_active: true,
      can_adjust_physical_stock: false,
      master_item_count: 0,
      warehouse_count: 0,
      total: 0,
      page: 1,
      page_count: 0,
      items: [],
    });
    await act(async () => {
      root.render(<StockSaatIni />);
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(container.querySelector('[data-testid="stock-read-mode"]')?.textContent).toMatch(/Mode R2/i);
    expect(container.querySelector('[data-testid="stock-physical-header"]')?.textContent).toBe("Stock");
    expect(container.querySelector('[data-testid="stock-physical-header"]')?.getAttribute("data-physical-scope")).toBe("BRANCH");
    expect(container.querySelector('[data-testid="stock-warehouse-select"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-book-vs-count-note"]')?.textContent).toMatch(/Excel|cabang|Belum saldo|Belum terverifikasi|defisit/i);
    expect(container.querySelector('[data-testid="stock-opname-button"]')?.getAttribute("href")).toMatch(/stock-opname/);
    expect(container.textContent).not.toMatch(/Preview.*R2 OFF|R2 OFF/i);
  });
});
