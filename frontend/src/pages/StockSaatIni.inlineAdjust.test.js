import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import StockSaatIni from "./StockSaatIni";

jest.mock("react-router-dom", () => require("../test/reactRouterMock"), { virtual: true });

const mockHasPerm = jest.fn(() => false);

jest.mock("../lib/auth", () => ({
  useAuth: () => ({ hasPerm: (p) => mockHasPerm(p) }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    branches: jest.fn(),
    warehouses: jest.fn(),
    physicalStockCatalog: jest.fn(),
    createPhysicalAdjustment: jest.fn(),
    masterItems: jest.fn(),
    physicalOpeningBalance: jest.fn(),
    createPhysicalOpeningBalance: jest.fn(),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const ITEM = {
  master_item_id: "item-1",
  item_code: "UAT-003",
  item_name: "Cup",
  base_unit: "PCS",
  physical_qty: 1050,
  qty_pesanan: 100,
  qty_belum_terkirim: 200,
  qty_tersedia: 750,
  updated_at: null,
};

const YGY = { id: "ygy", code: "YGY", name: "Yogyakarta" };

function warehouse(id, code = "MAIN") {
  return { id, branch_id: "ygy", code, name: `Gudang ${code}`, active: true };
}

function catalog() {
  return {
    can_adjust_physical_stock: true,
    items: [ITEM],
    page: 1,
    page_count: 1,
    total: 1,
    master_item_count: 1,
    warehouse_count: 1,
  };
}

function setNativeValue(el, value) {
  const proto = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
  proto.set.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

async function renderStock(warehouses) {
  api.branches.mockResolvedValue({ items: [YGY] });
  api.warehouses.mockResolvedValue({ items: warehouses });
  api.physicalStockCatalog.mockResolvedValue(catalog());
  api.createPhysicalAdjustment.mockResolvedValue({ ok: true });
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

describe("Stock Saat Ini inline physical correction", () => {
  afterEach(() => {
    jest.clearAllMocks();
    mockHasPerm.mockImplementation(() => false);
    document.body.innerHTML = "";
  });

  test("Belum tersedia opens Saldo Awal (not a dead click) when opening permission exists", async () => {
    mockHasPerm.mockImplementation((perm) => (
      perm === "current_stock.update" || perm === "permissions.manage"
    ));
    api.masterItems.mockResolvedValue({ items: [] });
    api.physicalOpeningBalance.mockResolvedValue({ established: false });
    const missingItem = {
      ...ITEM,
      physical_qty: null,
      balance_present: false,
      baseline_status: "unreconciled",
      warehouse_balances: [{
        warehouse_id: "wh-main",
        warehouse_code: "MAIN",
        physical_qty: null,
        balance_present: false,
        baseline_status: "unreconciled",
      }],
    };
    api.branches.mockResolvedValue({ items: [YGY] });
    api.warehouses.mockResolvedValue({ items: [warehouse("wh-main")] });
    api.physicalStockCatalog.mockResolvedValue({
      ...catalog(),
      warehouse_stock_standalone: true,
      items: [missingItem],
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<StockSaatIni />); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const button = container.querySelector('[data-testid="stock-adjust-item-1"]');
    // Aggregate view: missing WH → "Belum lengkap"; filtered WH → "Belum tersedia".
    expect(button?.textContent).toMatch(/Belum (tersedia|lengkap)/);
    await act(async () => { button.click(); });
    expect(container.querySelector('[data-testid="saldo-awal-modal"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="saldo-awal-item"]')?.value).toBe("item-1");
    expect(container.querySelector('[data-testid="stock-inline-input-item-1"]')).toBeNull();
    expect(api.createPhysicalAdjustment).not.toHaveBeenCalled();
    act(() => root.unmount());
    container.remove();
  });

  test("Belum tersedia without opening permission does not open Adjustment", async () => {
    mockHasPerm.mockReturnValue(false);
    const missingItem = {
      ...ITEM,
      physical_qty: null,
      balance_present: false,
      warehouse_balances: [{
        warehouse_id: "wh-main",
        warehouse_code: "MAIN",
        physical_qty: null,
        balance_present: false,
      }],
    };
    api.branches.mockResolvedValue({ items: [YGY] });
    api.warehouses.mockResolvedValue({ items: [warehouse("wh-main")] });
    api.physicalStockCatalog.mockResolvedValue({
      ...catalog(),
      items: [missingItem],
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<StockSaatIni />); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      container.querySelector('[data-testid="stock-adjust-item-1"]').click();
    });
    expect(container.querySelector('[data-testid="stock-inline-input-item-1"]')).toBeNull();
    expect(container.querySelector('[data-testid="saldo-awal-modal"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-adjust-modal"]')).toBeNull();
    expect(container.textContent).toMatch(/Saldo Awal memerlukan izin/);
    expect(api.createPhysicalAdjustment).not.toHaveBeenCalled();
    act(() => root.unmount());
    container.remove();
  });

  test("filtered warehouse Belum tersedia opens Saldo Awal with warehouse seed", async () => {
    mockHasPerm.mockImplementation((perm) => perm === "current_stock.update");
    api.masterItems.mockResolvedValue({ items: [] });
    api.physicalOpeningBalance.mockResolvedValue({ established: false });
    const uatBranch = { id: "ygy", code: "YGY", name: "Yogyakarta", active: true };
    const whMain = warehouse("wh-main", "MAIN");
    const whB = warehouse("wh-b", "WHB");
    const missingItem = {
      ...ITEM,
      physical_qty: null,
      baseline_status: "unreconciled",
      warehouse_balances: [
        {
          warehouse_id: "wh-main",
          warehouse_code: "MAIN",
          physical_qty: null,
          balance_present: false,
          baseline_status: "unreconciled",
        },
        {
          warehouse_id: "wh-b",
          warehouse_code: "WHB",
          physical_qty: 10,
          balance_present: true,
          baseline_status: "reconciled",
        },
      ],
    };
    api.branches.mockResolvedValue({ items: [uatBranch] });
    api.warehouses.mockResolvedValue({ items: [whMain, whB] });
    api.physicalStockCatalog.mockImplementation(async (params = {}) => ({
      ...catalog(),
      warehouse_stock_standalone: true,
      can_switch_branches: true,
      effective_branch_id: "ygy",
      items: [missingItem],
    }));
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<StockSaatIni />); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const whSelect = container.querySelector('[data-testid="stock-warehouse-select"]');
    expect(whSelect).not.toBeNull();
    await act(async () => {
      whSelect.value = "wh-main";
      whSelect.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    await act(async () => { await Promise.resolve(); });
    const button = container.querySelector('[data-testid="stock-adjust-item-1"]');
    expect(button?.textContent).toMatch(/Belum tersedia/);
    await act(async () => { button.click(); });
    expect(container.querySelector('[data-testid="saldo-awal-modal"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="saldo-awal-item"]')?.value).toBe("item-1");
    expect(container.querySelector('[data-testid="saldo-awal-warehouse"]')?.value).toBe("wh-main");
    act(() => root.unmount());
    container.remove();
  });

  test("single warehouse edits inline and does not open the correction modal", async () => {
    const view = await renderStock([warehouse("wh-main")]);
    const button = view.container.querySelector('[data-testid="stock-adjust-item-1"]');
    expect(button.textContent).toMatch(/1\.050|1050/);
    await act(async () => { button.click(); });
    const input = view.container.querySelector('[data-testid="stock-inline-input-item-1"]');
    expect(input).not.toBeNull();
    expect(input.value).toBe("1050");
    expect(view.container.querySelector('[data-testid="stock-adjust-modal"]')).toBeNull();
    expect(view.container.textContent).not.toMatch(/Koreksi Stock Fisik/);
    expect(view.container.querySelector('[data-testid="stock-order-item-1"] input')).toBeNull();
    expect(view.container.querySelector('[data-testid="stock-undelivered-item-1"] input')).toBeNull();
    view.cleanup();
  });

  test("Enter saves the target quantity, warehouse, and standard reason", async () => {
    const view = await renderStock([warehouse("wh-main")]);
    await act(async () => {
      view.container.querySelector('[data-testid="stock-adjust-item-1"]').click();
    });
    const input = view.container.querySelector('[data-testid="stock-inline-input-item-1"]');
    await act(async () => { setNativeValue(input, "1100"); });
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      await Promise.resolve();
    });
    expect(api.createPhysicalAdjustment).toHaveBeenCalledTimes(1);
    expect(api.createPhysicalAdjustment).toHaveBeenCalledWith("item-1", expect.objectContaining({
      branch_id: "ygy",
      warehouse_id: "wh-main",
      target_physical_qty: 1100,
      reason: "Koreksi manual dari Stock Saat Ini",
    }));
    view.cleanup();
  });

  test("Escape cancels without calling the adjustment API", async () => {
    const view = await renderStock([warehouse("wh-main")]);
    await act(async () => {
      view.container.querySelector('[data-testid="stock-adjust-item-1"]').click();
    });
    const input = view.container.querySelector('[data-testid="stock-inline-input-item-1"]');
    await act(async () => { setNativeValue(input, "1100"); });
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(view.container.querySelector('[data-testid="stock-inline-input-item-1"]')).toBeNull();
    expect(api.createPhysicalAdjustment).not.toHaveBeenCalled();
    view.cleanup();
  });

  test("an unchanged target does not create an adjustment", async () => {
    const view = await renderStock([warehouse("wh-main")]);
    await act(async () => {
      view.container.querySelector('[data-testid="stock-adjust-item-1"]').click();
    });
    const input = view.container.querySelector('[data-testid="stock-inline-input-item-1"]');
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(api.createPhysicalAdjustment).not.toHaveBeenCalled();
    expect(view.container.querySelector('[data-testid="stock-inline-input-item-1"]')).toBeNull();
    view.cleanup();
  });

  test("a negative target cannot be submitted", async () => {
    const view = await renderStock([warehouse("wh-main")]);
    await act(async () => {
      view.container.querySelector('[data-testid="stock-adjust-item-1"]').click();
    });
    const input = view.container.querySelector('[data-testid="stock-inline-input-item-1"]');
    await act(async () => { setNativeValue(input, "-5"); });
    expect(input.value).toBe("1050");
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(api.createPhysicalAdjustment).not.toHaveBeenCalled();
    view.cleanup();
  });

  test("a second save while the first request is open is ignored", async () => {
    const view = await renderStock([warehouse("wh-main")]);
    let release;
    api.createPhysicalAdjustment.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    await act(async () => {
      view.container.querySelector('[data-testid="stock-adjust-item-1"]').click();
    });
    const input = view.container.querySelector('[data-testid="stock-inline-input-item-1"]');
    await act(async () => { setNativeValue(input, "1100"); });
    const save = view.container.querySelector('[data-testid="stock-inline-save-item-1"]');
    await act(async () => { save.click(); });
    await act(async () => { save.click(); });
    expect(api.createPhysicalAdjustment).toHaveBeenCalledTimes(1);
    await act(async () => { release({ ok: true }); await Promise.resolve(); });
    view.cleanup();
  });

  test("multiple warehouses open explicit selection and are not guessed", async () => {
    const view = await renderStock([warehouse("wh-main", "MAIN"), warehouse("wh-b", "WHB")]);
    await act(async () => {
      view.container.querySelector('[data-testid="stock-adjust-item-1"]').click();
    });
    expect(view.container.querySelector('[data-testid="stock-inline-input-item-1"]')).toBeNull();
    const modal = view.container.querySelector('[data-testid="stock-adjust-modal"]');
    expect(modal).not.toBeNull();
    expect(modal.querySelector('[data-testid="stock-adjust-warehouse"]').value).toBe("");
    expect(api.createPhysicalAdjustment).not.toHaveBeenCalled();
    view.cleanup();
  });

  test("zero warehouses keeps correction unavailable", async () => {
    const view = await renderStock([]);
    await act(async () => {
      view.container.querySelector('[data-testid="stock-adjust-item-1"]').click();
    });
    expect(view.container.querySelector('[data-testid="stock-inline-input-item-1"]')).toBeNull();
    expect(view.container.querySelector('[data-testid="stock-adjust-modal"]')).toBeNull();
    expect(view.container.textContent).toMatch(/belum ada gudang aktif/);
    expect(api.createPhysicalAdjustment).not.toHaveBeenCalled();
    view.cleanup();
  });

  test("sole non-YGY branch auto-selects and opens multi-warehouse adjust modal", async () => {
    const uatBranch = { id: "br-uat", code: "UAT-YGY", name: "Cabang UAT Adjust", active: true };
    const whA = { id: "wh-a", branch_id: "br-uat", code: "GUD-A", name: "Gudang A", active: true, is_operational: true };
    const whB = { id: "wh-b", branch_id: "br-uat", code: "GUD-B", name: "Gudang B", active: true, is_operational: true };
    api.branches.mockResolvedValue({ items: [uatBranch] });
    api.warehouses.mockResolvedValue({ items: [whA, whB] });
    api.physicalStockCatalog.mockImplementation(async (params = {}) => ({
      ...catalog(),
      can_adjust_physical_stock: true,
      can_switch_branches: true,
      r2_authority_active: true,
      stock_read_mode: "r2",
      effective_branch_id: params.branch_id || uatBranch.id,
      effective_branch_code: "UAT-YGY",
      warehouse_count: 2,
      items: [{ ...ITEM, physical_qty: 15 }],
    }));
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<StockSaatIni />); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    const select = container.querySelector('[data-testid="stock-branch-select-input"]');
    expect(select).not.toBeNull();
    expect(select.value).toBe("br-uat");
    expect(api.physicalStockCatalog).toHaveBeenCalledWith(
      expect.objectContaining({ branch_id: "br-uat" }),
    );
    await act(async () => {
      container.querySelector('[data-testid="stock-adjust-item-1"]').click();
    });
    expect(container.textContent).not.toMatch(/belum ada gudang aktif/);
    const modal = container.querySelector('[data-testid="stock-adjust-modal"]');
    expect(modal).not.toBeNull();
    const options = [...modal.querySelectorAll('[data-testid="stock-adjust-warehouse"] option')]
      .map((opt) => opt.value)
      .filter(Boolean);
    expect(options).toEqual(["wh-a", "wh-b"]);
    act(() => root.unmount());
    container.remove();
  });

  test("selected warehouse filter scopes adjust + opname link to that warehouse id", async () => {
    const uatBranch = { id: "br-uat", code: "UAT-YGY", name: "Cabang UAT Adjust", active: true };
    const whA = { id: "wh-a", branch_id: "br-uat", code: "GUD-A", name: "Gudang A", active: true, is_operational: true };
    const whB = { id: "wh-b", branch_id: "br-uat", code: "GUD-B", name: "Gudang B", active: true, is_operational: true };
    api.branches.mockResolvedValue({ items: [uatBranch] });
    api.warehouses.mockResolvedValue({ items: [whA, whB] });
    api.physicalStockCatalog.mockImplementation(async (params = {}) => ({
      ...catalog(),
      can_adjust_physical_stock: true,
      can_switch_branches: true,
      r2_authority_active: true,
      stock_read_mode: "r2",
      effective_branch_id: params.branch_id || uatBranch.id,
      effective_branch_code: "UAT-YGY",
      warehouse_count: 2,
      items: [{
        ...ITEM,
        physical_qty: 20,
        baseline_status: "unreconciled",
        baseline_label: "Belum direkonsiliasi",
        warehouse_balances: [{
          warehouse_id: "wh-a",
          warehouse_code: "GUD-A",
          physical_qty: 20,
          balance_present: true,
          baseline_status: "unreconciled",
        }],
      }],
    }));
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<StockSaatIni />); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    const whSelect = container.querySelector('[data-testid="stock-warehouse-select"]');
    expect(whSelect).not.toBeNull();
    await act(async () => {
      whSelect.value = "wh-a";
      whSelect.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(api.physicalStockCatalog).toHaveBeenCalledWith(
      expect.objectContaining({ branch_id: "br-uat", warehouse_id: "wh-a" }),
    );
    expect(container.querySelector('[data-testid="stock-physical-header"]')?.textContent).toBe("Stock Fisik");
    expect(container.querySelector('[data-testid="stock-opname-button"]')?.getAttribute("href")).toBe(
      "/persediaan/stock-opname?warehouse_id=wh-a",
    );
    expect(container.querySelector('[data-testid="stock-physical-item-1"]')?.getAttribute("data-baseline-label"))
      .toMatch(/Belum direkonsiliasi/i);
    expect(container.querySelector('[data-testid="stock-physical-item-1"]')?.getAttribute("data-balance-present")).toBe("true");
    expect(container.querySelector('[data-testid="stock-physical-item-1"]')?.getAttribute("data-baseline-status")).toBe("unreconciled");
    expect(container.querySelector('[data-testid="stock-physical-item-1"]')?.textContent)
      .not.toMatch(/Belum direkonsiliasi/i);
    await act(async () => {
      container.querySelector('[data-testid="stock-adjust-item-1"]').click();
    });
    // Single selected warehouse → inline edit, not multi-warehouse modal.
    expect(container.querySelector('[data-testid="stock-adjust-modal"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-inline-input-item-1"]')).not.toBeNull();
    act(() => root.unmount());
    container.remove();
  });

  test("warehouse without balance shows Belum tersedia, not zero", async () => {
    const uatBranch = { id: "br-uat", code: "UAT-YGY", name: "Cabang UAT Adjust", active: true };
    const whB = { id: "wh-b", branch_id: "br-uat", code: "GUD-B", name: "Gudang B", active: true, is_operational: true };
    api.branches.mockResolvedValue({ items: [uatBranch] });
    api.warehouses.mockResolvedValue({ items: [whB] });
    api.physicalStockCatalog.mockResolvedValue({
      ...catalog(),
      can_adjust_physical_stock: true,
      r2_authority_active: true,
      stock_read_mode: "r2",
      effective_branch_id: uatBranch.id,
      effective_branch_code: "UAT-YGY",
      warehouse_count: 1,
      items: [{
        ...ITEM,
        item_code: "GR-FIN-601-001",
        physical_qty: 0,
        qty_tersedia: 0,
        qty_tersedia_valid: false,
        baseline_status: "unreconciled",
        baseline_label: "Saldo awal belum direkonsiliasi",
        warehouse_balances: [{
          warehouse_id: "wh-b",
          warehouse_code: "GUD-B",
          physical_qty: null,
          balance_present: false,
          baseline_status: "unreconciled",
        }],
      }],
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => { root.render(<StockSaatIni />); });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
    await act(async () => {
      const sel = container.querySelector('[data-testid="stock-warehouse-select"]');
      sel.value = "wh-b";
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    const physical = container.querySelector('[data-testid="stock-physical-item-1"]');
    expect(physical?.getAttribute("data-balance-present")).toBe("false");
    expect(physical?.textContent).toMatch(/Belum tersedia/i);
    expect(physical?.textContent).not.toMatch(/^0|[^0-9]0[^0-9]/);
    expect(physical?.textContent).not.toMatch(/belum direkonsiliasi/i);
    expect(container.querySelector('[data-testid="stock-available-invalid-item-1"]')?.textContent).toMatch(/Belum valid/i);
    expect(physical?.getAttribute("data-baseline-label")).toMatch(/belum direkonsiliasi/i);
    act(() => root.unmount());
    container.remove();
  });
});
