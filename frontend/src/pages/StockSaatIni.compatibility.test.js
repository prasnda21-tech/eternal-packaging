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
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

describe("Stock Saat Ini compatibility mode", () => {
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

  test("shows compatibility read mode and cutover-pending belum", async () => {
    api.physicalStockCatalog.mockResolvedValue({
      stock_read_mode: "compatibility",
      r2_authority_active: false,
      can_adjust_physical_stock: false,
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
        qty_pesanan: 600,
        qty_belum_terkirim: null,
        qty_tersedia: 15300,
        belum_terkirim_status: "CUTOVER_PENDING",
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
    expect(container.querySelector('[data-testid="stock-compat-branch-note"]')?.textContent).toMatch(/cabang/i);
    expect(container.querySelector('[data-testid="stock-branch-select-input"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-undelivered-m1"]')?.textContent).toMatch(/Cutover pending/i);
    expect(container.querySelector('[data-testid="stock-undelivered-m1"]')?.getAttribute("data-belum-status")).toBe("CUTOVER_PENDING");
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
  });
});
