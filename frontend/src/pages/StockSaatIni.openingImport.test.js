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

function siap(n, id = `item-${n}`) {
  return {
    row_number: n,
    item_code: `OB-${n}`,
    item_name: `Item ${n}`,
    unit: "PCS",
    warehouse_name: "Gudang Yogyakarta",
    physical_qty: n,
    status: "Siap",
    master_item_id: id,
    branch_id: "ygy",
    warehouse_id: "wh-main",
  };
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

  async function loadPreview(view, preview) {
    api.physicalOpeningImportPreview.mockResolvedValue(preview);
    const input = view.container.querySelector('[data-testid="opening-import-file"]');
    const file = chooseFile(input);
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    return file;
  }

  test("mixed preview enables confirm for Siap subset and ignores can_confirm=false", async () => {
    const view = await renderStock();
    const loads = api.physicalStockCatalog.mock.calls.length;
    await openImportTab(view);
    expect(view.container.querySelector('[data-testid="opening-import-confirm"]').disabled).toBe(true);

    await loadPreview(view, {
      can_confirm: false,
      ready_count: 1,
      blocked_count: 1,
      rows: [
        siap(2, "item-1"),
        { row_number: 3, item_code: "TIDAK-ADA", item_name: "", unit: "", warehouse_name: "MAIN", physical_qty: 5, status: "Kode Tidak Ditemukan" },
      ],
    });
    expect(view.container.querySelector('[data-testid="opening-import-status-2"]').textContent).toMatch(/Siap/);
    expect(view.container.querySelector('[data-testid="opening-import-status-3"]').textContent).toMatch(/Kode Tidak Ditemukan/);
    expect(view.container.querySelector('[data-testid="opening-import-count-ready"]').textContent).toMatch(/Siap:\s*1/);
    expect(view.container.querySelector('[data-testid="opening-import-count-selected"]').textContent).toMatch(/Terpilih:\s*1/);
    expect(view.container.querySelector('[data-testid="opening-import-count-rejected"]').textContent).toMatch(/Ditolak:\s*1/);
    expect(view.container.querySelector('[data-testid="opening-import-check-2"]').checked).toBe(true);
    expect(view.container.querySelector('[data-testid="opening-import-check-3"]').disabled).toBe(true);
    expect(view.container.querySelector('[data-testid="opening-import-confirm"]').disabled).toBe(false);
    expect(api.physicalOpeningImportConfirm).not.toHaveBeenCalled();
    expect(api.physicalStockCatalog.mock.calls.length).toBe(loads);
    view.cleanup();
  });

  test("confirm posts only selected Siap rows (17 of 20) and refreshes stock", async () => {
    const view = await renderStock();
    await openImportTab(view);
    const rows = [];
    for (let n = 2; n <= 18; n += 1) rows.push(siap(n, `item-${n}`));
    rows.push({ row_number: 19, item_code: "DUP", status: "Duplikat", physical_qty: 1 });
    rows.push({ row_number: 20, item_code: "MISS", status: "Kode Tidak Ditemukan", physical_qty: 1 });
    rows.push({ row_number: 21, item_code: "BAD", status: "Qty Tidak Valid", physical_qty: null });
    await loadPreview(view, { can_confirm: false, ready_count: 17, blocked_count: 3, rows });
    expect(view.container.querySelector('[data-testid="opening-import-count-ready"]').textContent).toMatch(/Siap:\s*17/);
    expect(view.container.querySelector('[data-testid="opening-import-count-rejected"]').textContent).toMatch(/Ditolak:\s*3/);
    api.physicalOpeningImportConfirm.mockResolvedValue({
      idempotent: false,
      engine: "warehouse_standalone",
      import: { status: "CONFIRMED", lines: new Array(17).fill({}) },
    });
    api.physicalStockCatalog.mockResolvedValue(catalog(100));
    await act(async () => {
      view.container.querySelector('[data-testid="opening-import-confirm"]').click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.physicalOpeningImportConfirm).toHaveBeenCalledTimes(1);
    const payload = api.physicalOpeningImportConfirm.mock.calls[0][0];
    expect(payload.lines).toHaveLength(17);
    expect(payload.idempotency_key).toBeTruthy();
    expect(payload.lines.every((line) => String(line.item_code).startsWith("OB-"))).toBe(true);
    expect(api.physicalStockCatalog.mock.calls.length).toBeGreaterThan(1);
    expect(view.container.querySelector('[data-testid="stock-physical-item-1"]').textContent).toMatch(/100/);
    view.cleanup();
  });

  test("checkbox subset posts only checked rows; none selected disables confirm", async () => {
    const view = await renderStock();
    await openImportTab(view);
    await loadPreview(view, {
      can_confirm: false,
      rows: [siap(2, "a"), siap(3, "b"), { row_number: 4, item_code: "X", status: "Duplikat" }],
    });
    await act(async () => {
      view.container.querySelector('[data-testid="opening-import-check-3"]').click();
    });
    expect(view.container.querySelector('[data-testid="opening-import-count-selected"]').textContent).toMatch(/Terpilih:\s*1/);
    api.physicalOpeningImportConfirm.mockResolvedValue({ idempotent: false, import: { status: "CONFIRMED" } });
    await act(async () => {
      view.container.querySelector('[data-testid="opening-import-confirm"]').click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.physicalOpeningImportConfirm.mock.calls[0][0].lines).toEqual([
      expect.objectContaining({ master_item_id: "a", item_code: "OB-2" }),
    ]);

    const view2 = await renderStock();
    await openImportTab(view2);
    await loadPreview(view2, { can_confirm: true, rows: [siap(2, "a"), siap(3, "b")] });
    await act(async () => {
      view2.container.querySelector('[data-testid="opening-import-check-2"]').click();
      view2.container.querySelector('[data-testid="opening-import-check-3"]').click();
    });
    expect(view2.container.querySelector('[data-testid="opening-import-count-selected"]').textContent).toMatch(/Terpilih:\s*0/);
    expect(view2.container.querySelector('[data-testid="opening-import-confirm"]').disabled).toBe(true);
    view.cleanup();
    view2.cleanup();
  });

  test("retry Lanjutkan keeps the same idempotency key and payload", async () => {
    const view = await renderStock();
    await openImportTab(view);
    await loadPreview(view, { can_confirm: false, rows: [siap(2, "item-1"), siap(3, "item-2")] });
    api.physicalOpeningImportConfirm.mockRejectedValueOnce({
      response: { status: 503, data: { detail: "Impor saldo awal gagal." } },
    });
    await act(async () => {
      view.container.querySelector('[data-testid="opening-import-confirm"]').click();
      await Promise.resolve();
      await Promise.resolve();
    });
    const firstKey = api.physicalOpeningImportConfirm.mock.calls[0][0].idempotency_key;
    const firstLines = api.physicalOpeningImportConfirm.mock.calls[0][0].lines;
    expect(view.container.querySelector('[data-testid="opening-import-confirm"]').textContent).toMatch(/Lanjutkan/);
    expect(view.container.querySelector('[data-testid="opening-import-check-2"]').disabled).toBe(true);
    expect(view.container.querySelector('[data-testid="opening-import-file"]').disabled).toBe(true);
    expect(view.container.querySelector('[data-testid="opening-import-error"]').textContent).toMatch(/idempotency key yang sama/i);

    api.physicalOpeningImportConfirm.mockResolvedValueOnce({
      idempotent: true,
      import: { status: "CONFIRMED", lines: firstLines },
    });
    await act(async () => {
      view.container.querySelector('[data-testid="opening-import-confirm"]').click();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.physicalOpeningImportConfirm).toHaveBeenCalledTimes(2);
    expect(api.physicalOpeningImportConfirm.mock.calls[1][0].idempotency_key).toBe(firstKey);
    expect(api.physicalOpeningImportConfirm.mock.calls[1][0].lines).toEqual(firstLines);
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
    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
    await act(async () => {
      setter.call(select, "wh-main");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      view.container.querySelector('[data-testid="opening-import-template"]').click();
      await Promise.resolve();
    });
    expect(api.physicalOpeningImportTemplate).toHaveBeenCalledWith({ warehouse_id: "wh-main" });
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
    expect(view.container.querySelector('[data-testid="saldo-awal-empty-items"]')).toBeNull();
    const select = view.container.querySelector('[data-testid="saldo-awal-item"]');
    expect(select.textContent).toMatch(/OB-UAT-001/);
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
    view.cleanup();
  });

  test("manual saldo awal form uses aligned fields and shows validation errors", async () => {
    const view = await renderStock();
    await act(async () => {
      view.container.querySelector('[data-testid="saldo-awal-button"]').click();
    });
    await act(async () => {
      view.container.querySelector('[data-testid="saldo-awal-submit"]').click();
      await Promise.resolve();
    });
    expect(view.container.querySelector('[data-testid="saldo-awal-error-item"]')?.textContent).toMatch(/Pilih barang/i);
    expect(api.createPhysicalOpeningBalance).not.toHaveBeenCalled();
    view.cleanup();
  });
});
