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
    masterItems: jest.fn(),
    physicalOpeningBalance: jest.fn(),
    createPhysicalOpeningBalance: jest.fn(),
    physicalOpeningImportTemplate: jest.fn(),
    physicalOpeningImportPreview: jest.fn(),
    physicalOpeningImportConfirm: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

function catalog(physical = 0) {
  return {
    stock_read_mode: "warehouse_standalone",
    r2_authority_active: false,
    warehouse_stock_standalone: true,
    can_adjust_physical_stock: true,
    items: [{
      master_item_id: "item-1",
      item_code: "OB-UAT-001",
      item_name: "Opening",
      base_unit: "PCS",
      physical_qty: physical,
      balance_present: true,
      qty_pesanan: 0,
      qty_belum_terkirim: 0,
      qty_tersedia: physical,
      updated_at: null,
    }],
    page: 1,
    page_count: 1,
    total: 1,
    master_item_count: 1,
    warehouse_count: 1,
  };
}

async function renderStock(warehouses = [{ id: "wh-main", branch_id: "ygy", code: "MAIN", name: "Gudang Yogyakarta", active: true }]) {
  api.branches.mockResolvedValue({ items: [{ id: "ygy", code: "YGY", name: "Yogyakarta" }] });
  api.warehouses.mockResolvedValue({ items: warehouses });
  api.physicalStockCatalog.mockResolvedValue(catalog());
  api.masterItems.mockResolvedValue({ items: [] });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(<StockSaatIni />);
  });
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  return {
    container,
    cleanup: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

function chooseFile(input, name = "saldo.xlsx") {
  const file = new File(["workbook"], name, { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  Object.defineProperty(input, "files", { configurable: true, value: [file] });
  return file;
}

describe("Stock Saat Ini physical opening import", () => {
  beforeEach(() => {
    api.physicalStockStatusSummary.mockResolvedValue({
      counts: {
        items_physical_ok: 2,
        items_physical_incomplete: 2,
        items_physical_missing: 0,
        warehouse_slots_missing_balance: 2,
        warehouse_slots_unreconciled: 1,
        items_belum_terkirim_unverified: 0,
        items_qty_tersedia_invalid: 2,
      },
      operational_actions: [
        "Tetapkan Saldo Awal atau konfirmasi Stock Opname per barang×gudang yang belum punya saldo reconciled.",
      ],
      read_only: true,
    });
  });

  afterEach(() => {
    jest.clearAllMocks();
    document.body.innerHTML = "";
  });

  async function openImportTab(view) {
    await act(async () => {
      view.container.querySelector('[data-testid="saldo-awal-button"]').click();
    });
    await act(async () => {
      view.container.querySelector('[data-testid="saldo-awal-tab-import"]').click();
    });
  }

  test("opens saldo awal import and keeps preview from changing stock", async () => {
    const view = await renderStock();
    const loads = api.physicalStockCatalog.mock.calls.length;
    await openImportTab(view);
    expect(view.container.querySelector('[data-testid="saldo-awal-title"]').textContent).toBe("Saldo Awal Stock Fisik");
    expect(view.container.querySelector('[data-testid="opening-import-template"]')).not.toBeNull();
    expect(view.container.querySelector('[data-testid="opening-import-file"]')).not.toBeNull();
    expect(view.container.querySelector('[data-testid="opening-import-confirm"]').disabled).toBe(true);

    api.physicalOpeningImportPreview.mockResolvedValue({
      can_confirm: false,
      rows: [
        { row_number: 2, item_code: "OB-UAT-001", item_name: "Opening", unit: "PCS", warehouse_name: "Gudang Yogyakarta", physical_qty: 100, status: "Siap", master_item_id: "item-1", branch_id: "ygy", warehouse_id: "wh-main" },
        { row_number: 3, item_code: "TIDAK-ADA", item_name: "", unit: "", warehouse_name: "MAIN", physical_qty: 5, status: "Kode Tidak Ditemukan" },
      ],
    });
    const input = view.container.querySelector('[data-testid="opening-import-file"]');
    const file = chooseFile(input);
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    expect(api.physicalOpeningImportPreview).toHaveBeenCalledWith(file);
    expect(view.container.querySelector('[data-testid="opening-import-status-2"]').textContent).toMatch(/Siap/);
    expect(view.container.querySelector('[data-testid="opening-import-status-3"]').textContent).toMatch(/Kode Tidak Ditemukan/);
    expect(view.container.querySelector('[data-testid="opening-import-confirm"]').disabled).toBe(true);
    expect(api.physicalOpeningImportConfirm).not.toHaveBeenCalled();
    expect(api.createPhysicalOpeningBalance).not.toHaveBeenCalled();
    expect(api.physicalStockCatalog.mock.calls.length).toBe(loads);
    view.cleanup();
  });

  test("confirm calls the bulk endpoint and refreshes stock", async () => {
    const view = await renderStock();
    await openImportTab(view);
    api.physicalOpeningImportPreview.mockResolvedValue({
      can_confirm: true,
      rows: [
        { row_number: 2, item_code: "OB-UAT-001", item_name: "Opening", unit: "PCS", warehouse_name: "Gudang Yogyakarta", physical_qty: 100, status: "Siap", master_item_id: "item-1", branch_id: "ygy", warehouse_id: "wh-main" },
      ],
    });
    api.physicalOpeningImportConfirm.mockResolvedValue({ idempotent: false });
    api.physicalStockCatalog.mockResolvedValue(catalog(100));
    const input = view.container.querySelector('[data-testid="opening-import-file"]');
    chooseFile(input);
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    await act(async () => {
      view.container.querySelector('[data-testid="opening-import-confirm"]').click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.physicalOpeningImportConfirm).toHaveBeenCalledWith(expect.objectContaining({
      lines: [expect.objectContaining({
        master_item_id: "item-1",
        branch_id: "ygy",
        warehouse_id: "wh-main",
        physical_qty: 100,
        item_code: "OB-UAT-001",
      })],
    }));
    expect(api.physicalStockCatalog.mock.calls.length).toBeGreaterThan(1);
    expect(view.container.querySelector('[data-testid="stock-physical-item-1"]').textContent).toMatch(/100/);
    view.cleanup();
  });

  test("template download sends only the warehouse the user selects", async () => {
    const view = await renderStock([
      { id: "wh-main", branch_id: "ygy", code: "MAIN", name: "Gudang Yogyakarta", active: true },
      { id: "wh-sop", branch_id: "ygy", code: "SOPUAT", name: "Gudang Stock Opname UAT", active: true },
    ]);
    URL.createObjectURL = jest.fn(() => "blob:template");
    URL.revokeObjectURL = jest.fn();
    api.physicalOpeningImportTemplate.mockResolvedValue(new Blob(["xlsx"]));
    await openImportTab(view);
    const button = view.container.querySelector('[data-testid="opening-import-template"]');
    const select = view.container.querySelector('[data-testid="opening-import-warehouse"]');
    expect(button.disabled).toBe(true);
    expect(select.value).toBe("");
    expect(select.textContent).toMatch(/Gudang Yogyakarta/);
    expect(select.textContent).toMatch(/Gudang Stock Opname UAT/);
    await act(async () => {
      button.click();
    });
    expect(api.physicalOpeningImportTemplate).not.toHaveBeenCalled();
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
    await act(async () => {
      setter.call(select, "wh-main");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      view.container.querySelector('[data-testid="opening-import-template"]').click();
      await Promise.resolve();
    });
    expect(api.physicalOpeningImportTemplate).toHaveBeenCalledTimes(1);
    expect(api.physicalOpeningImportTemplate).toHaveBeenCalledWith({ warehouse_id: "wh-main" });
    expect(api.physicalOpeningImportPreview).not.toHaveBeenCalled();
    expect(api.physicalOpeningImportConfirm).not.toHaveBeenCalled();
    view.cleanup();
  });

  test("manual tab prefers catalog items when master list is empty", async () => {
    const view = await renderStock();
    await act(async () => {
      view.container.querySelector('[data-testid="saldo-awal-button"]').click();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(view.container.querySelector('[data-testid="saldo-awal-tab-manual"]')).not.toBeNull();
    expect(view.container.querySelector('[data-testid="saldo-awal-empty-items"]')).toBeNull();
    const select = view.container.querySelector('[data-testid="saldo-awal-item"]');
    expect(select.textContent).toMatch(/OB-UAT-001/);
    expect(select.textContent).toMatch(/Opening/);
    view.cleanup();
  });

  test("import errors stay human readable", async () => {
    const view = await renderStock();
    await openImportTab(view);
    api.physicalOpeningImportPreview.mockRejectedValue({
      response: { data: { detail: { message: "Impor ditolak. Tidak ada stok yang diubah.", rows: [{ status: "Duplikat" }] } } },
    });
    const input = view.container.querySelector('[data-testid="opening-import-file"]');
    chooseFile(input);
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    const text = view.container.querySelector('[data-testid="opening-import-error"]').textContent;
    expect(text).toMatch(/Impor ditolak/);
    expect(text).not.toMatch(/\{/);
    expect(view.container.textContent).not.toMatch(/JSON/);
    view.cleanup();
  });

  test("manual saldo awal form uses aligned fields and shows validation errors", async () => {
    const view = await renderStock();
    await act(async () => {
      view.container.querySelector('[data-testid="saldo-awal-button"]').click();
    });
    expect(view.container.querySelector('[data-testid="saldo-awal-fields"]')).not.toBeNull();
    expect(view.container.querySelector('[data-testid="saldo-awal-item"]')).not.toBeNull();
    expect(view.container.querySelector('[data-testid="saldo-awal-branch"]')).not.toBeNull();
    expect(view.container.querySelector('[data-testid="saldo-awal-warehouse"]')).not.toBeNull();
    expect(view.container.querySelector('[data-testid="saldo-awal-qty"]')).not.toBeNull();
    expect(view.container.querySelector('[data-testid="saldo-awal-unit"]')).not.toBeNull();
    expect(view.container.querySelector('[data-testid="saldo-awal-reason"]')).not.toBeNull();
    await act(async () => {
      view.container.querySelector('[data-testid="saldo-awal-submit"]').click();
      await Promise.resolve();
    });
    expect(view.container.querySelector('[data-testid="saldo-awal-error-item"]')?.textContent).toMatch(/Pilih barang/i);
    expect(view.container.querySelector('[data-testid="saldo-awal-error-branch"]')?.textContent).toMatch(/Pilih cabang/i);
    expect(view.container.querySelector('[data-testid="saldo-awal-error-warehouse"]')?.textContent).toMatch(/Pilih gudang/i);
    expect(view.container.querySelector('[data-testid="saldo-awal-error-qty"]')?.textContent).toMatch(/qty/i);
    expect(api.createPhysicalOpeningBalance).not.toHaveBeenCalled();
    expect(view.container.querySelector('[data-testid="stock-status-summary"]')).not.toBeNull();
    const counts = view.container.querySelector('[data-testid="stock-status-summary-counts"]');
    expect(counts?.textContent).toMatch(/Fisik OK:\s*2/);
    expect(counts?.textContent).toMatch(/Belum lengkap:\s*2/);
    expect(counts?.textContent).toMatch(/Belum saldo:\s*0/);
    expect(counts?.textContent).toMatch(/Slot tanpa saldo:\s*2/);
    expect(counts?.textContent).toMatch(/Slot unreconciled:\s*1/);
    expect(counts?.textContent).toMatch(/Belum Terkirim unverified:\s*0/);
    expect(counts?.textContent).toMatch(/Tersedia invalid:\s*2/);
    const detail = view.container.querySelector('[data-testid="stock-status-summary-detail"]');
    const toggle = view.container.querySelector('[data-testid="stock-status-summary-detail-toggle"]');
    expect(detail?.hasAttribute("hidden")).toBe(true);
    expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    await act(async () => {
      toggle.click();
    });
    expect(detail?.hasAttribute("hidden")).toBe(false);
    expect(toggle?.getAttribute("aria-expanded")).toBe("true");
    expect(view.container.querySelector('[data-testid="stock-status-summary-actions"]')?.textContent)
      .toMatch(/Saldo Awal/);
    await act(async () => {
      toggle.click();
    });
    expect(detail?.hasAttribute("hidden")).toBe(true);
    view.cleanup();
  });
});
