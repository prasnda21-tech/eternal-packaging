import { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../lib/api";
import {
  __resetStockSaatIniPageCacheForTests,
  buildStockSaatIniPageCacheKey,
  getStockSaatIniPageCache,
  setStockSaatIniPageCache,
} from "../lib/stockSaatIniPageCache";
import StockSaatIni, { stockCatalogTableView } from "./StockSaatIni";

jest.mock("react-router-dom", () => require("../test/reactRouterMock"), { virtual: true });

const authState = {
  user: { id: "user-1", email: "ops@example.com" },
  hasPerm: () => true,
};

jest.mock("../lib/auth", () => ({
  useAuth: () => authState,
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    branches: jest.fn(),
    warehouses: jest.fn(),
    physicalStockCatalog: jest.fn(),
    physicalStockStatusSummary: jest.fn().mockResolvedValue({
      counts: {},
      operational_actions: [],
      read_only: true,
    }),
  },
}));

global.IS_REACT_ACT_ENVIRONMENT = true;

const BRANCH_A = { id: "br-a", code: "YGY", name: "Yogyakarta", active: true };
const BRANCH_B = { id: "br-b", code: "JKT", name: "Jakarta", active: true };
const WH_A = {
  id: "wh-a",
  branch_id: "br-a",
  code: "MAIN",
  name: "Gudang A",
  active: true,
  is_operational: true,
};

function catalogPayload(overrides = {}) {
  return {
    stock_read_mode: "warehouse_standalone",
    stock_read_mode_label: "Mode warehouse standalone — stok fisik per cabang/gudang (tanpa Replica Set).",
    r2_authority_active: false,
    warehouse_stock_standalone: true,
    can_adjust_physical_stock: true,
    can_switch_branches: true,
    effective_branch_id: BRANCH_A.id,
    effective_branch_code: "YGY",
    master_item_count: 1,
    warehouse_count: 1,
    total: 1,
    page: 1,
    page_count: 1,
    items: [{
      master_item_id: "m1",
      item_code: "BOX",
      item_name: "Box Kraft",
      base_unit: "PCS",
      physical_qty: 10,
      stock_dapat_dijual: 10,
      qty_polos: 10,
      qty_polos_status: "READY",
      warehouse_balances: [
        { warehouse_id: "wh-a", warehouse_code: "MAIN", physical_qty: 10, balance_present: true },
      ],
    }],
    ...overrides,
  };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function setNativeValue(el, value) {
  const proto = el.tagName === "SELECT"
    ? window.HTMLSelectElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(el, value);
  else el.value = value;
  el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}

async function submitSearch(container, value) {
  await act(async () => {
    const input = container.querySelector('[data-testid="stock-search"]');
    setNativeValue(input, value);
    container.querySelector("form.stock-filter-search")
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await flush();
}

describe("stockCatalogTableView", () => {
  test("keeps results while soft-refreshing; loading only when empty", () => {
    expect(stockCatalogTableView({
      pending: true, loading: false, refreshing: true, itemCount: 3,
    })).toBe("results");
    expect(stockCatalogTableView({
      pending: false, loading: true, refreshing: false, itemCount: 0,
    })).toBe("loading");
    expect(stockCatalogTableView({
      pending: false, loading: false, refreshing: false, itemCount: 0,
    })).toBe("empty");
  });
});

describe("Stock Saat Ini P0-A loading stability", () => {
  let container;
  let root;

  beforeEach(() => {
    __resetStockSaatIniPageCacheForTests();
    authState.user = { id: "user-1", email: "ops@example.com" };
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    api.branches.mockResolvedValue({ items: [BRANCH_A] });
    api.warehouses.mockResolvedValue({ items: [WH_A] });
    api.physicalStockCatalog.mockResolvedValue(catalogPayload());
    api.physicalStockStatusSummary.mockResolvedValue({
      counts: {},
      operational_actions: [],
      read_only: true,
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.clearAllMocks();
    __resetStockSaatIniPageCacheForTests();
  });

  test("cold mount: neutral mode UI, reserved Gudang slot, single catalog GET for sole branch", async () => {
    let resolveCatalog;
    api.physicalStockCatalog.mockImplementation(() => new Promise((resolve) => {
      resolveCatalog = resolve;
    }));

    await act(async () => { root.render(<StockSaatIni />); });
    await flush();

    expect(container.querySelector('[data-testid="stock-subtitle"]')?.textContent)
      .toMatch(/Memuat persediaan/i);
    expect(container.querySelector('[data-testid="stock-subtitle"]')?.textContent)
      .not.toMatch(/kompatibilitas/i);
    expect(container.querySelector('[data-testid="stock-mode-chip"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-mode-chip-pending"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-warehouse-filter"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-warehouse-placeholder"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-branch-filter-slot"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-search"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-page-size"]')).toBeTruthy();

    await act(async () => {
      resolveCatalog(catalogPayload());
      await Promise.resolve();
    });
    await flush();

    expect(api.physicalStockCatalog).toHaveBeenCalledTimes(1);
    expect(api.physicalStockCatalog.mock.calls[0][0]).toEqual(
      expect.objectContaining({ branch_id: BRANCH_A.id, page: 1, limit: 50 }),
    );
    expect(container.querySelector('[data-testid="stock-mode-chip"]')?.textContent).toMatch(/Gudang/i);
    expect(container.querySelector('[data-testid="stock-subtitle"]')?.textContent)
      .toMatch(/cabang dan gudang/i);
    expect(container.querySelector('[data-testid="stock-warehouse-select"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-row-m1"]')).toBeTruthy();
  });

  test("compatibility mode label only after confirmed response", async () => {
    api.physicalStockCatalog.mockResolvedValue(catalogPayload({
      stock_read_mode: "compatibility",
      warehouse_stock_standalone: false,
      can_adjust_physical_stock: false,
      can_switch_branches: false,
      items: [{
        master_item_id: "m-compat",
        item_code: "C1",
        item_name: "Compat Item",
        base_unit: "PCS",
        physical_qty: 1,
      }],
    }));
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    expect(container.querySelector('[data-testid="stock-mode-chip"]')?.textContent)
      .toMatch(/Kompatibilitas/i);
    expect(container.querySelector('[data-testid="stock-subtitle"]')?.textContent)
      .toMatch(/kompatibilitas/i);
    expect(container.querySelector('[data-testid="stock-warehouse-placeholder"]')).toBeTruthy();
  });

  test("search refetch keeps rows, shows refreshing, disables stale actions", async () => {
    let resolveSecond;
    api.physicalStockCatalog
      .mockResolvedValueOnce(catalogPayload())
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));

    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    expect(container.querySelector('[data-testid="stock-row-m1"]')).toBeTruthy();

    await submitSearch(container, "BOX");

    expect(container.querySelector('[data-testid="stock-row-m1"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-refreshing"]')?.textContent)
      .toMatch(/Menyegarkan/i);
    expect(container.querySelector('[data-testid="stock-detail-m1"]')?.disabled).toBe(true);
    expect(container.querySelector('[data-testid="stock-loading"]')).toBeNull();

    await act(async () => {
      resolveSecond(catalogPayload({
        items: [{
          master_item_id: "m1",
          item_code: "BOX",
          item_name: "Box Kraft",
          base_unit: "PCS",
          physical_qty: 10,
          stock_dapat_dijual: 10,
          qty_polos: 10,
          qty_polos_status: "READY",
        }],
      }));
      await Promise.resolve();
    });
    await flush();
    expect(container.querySelector('[data-testid="stock-refreshing"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-detail-m1"]')?.disabled).toBe(false);
  });

  test("branch switch clears prior rows before new catalog arrives", async () => {
    api.branches.mockResolvedValue({ items: [BRANCH_A, BRANCH_B] });
    let resolveSecond;
    api.physicalStockCatalog
      .mockResolvedValueOnce(catalogPayload())
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));

    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    expect(container.querySelector('[data-testid="stock-row-m1"]')).toBeTruthy();

    const branchSelect = container.querySelector('[data-testid="stock-branch-select-input"]');
    expect(branchSelect).toBeTruthy();
    await act(async () => {
      setNativeValue(branchSelect, BRANCH_B.id);
    });
    await flush();

    expect(container.querySelector('[data-testid="stock-row-m1"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-loading"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-mode-chip"]')?.textContent).toMatch(/Gudang/i);
    expect(container.querySelector('[data-testid="stock-branch-select-input"]')?.value).toBe(BRANCH_B.id);
    expect(container.querySelector('[data-testid="stock-subtitle"]')?.textContent)
      .not.toMatch(/kompatibilitas/i);

    await act(async () => {
      resolveSecond(catalogPayload({
        effective_branch_id: BRANCH_B.id,
        effective_branch_code: "JKT",
        items: [{
          master_item_id: "m-jkt",
          item_code: "J1",
          item_name: "Jakarta Item",
          base_unit: "PCS",
          physical_qty: 3,
          stock_dapat_dijual: 3,
          qty_polos: 3,
          qty_polos_status: "READY",
        }],
      }));
      await Promise.resolve();
    });
    await flush();
    expect(container.querySelector('[data-testid="stock-row-m1"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-row-m-jkt"]')).toBeTruthy();
  });

  test("stale slower response does not overwrite newer page", async () => {
    let resolvePage1;
    let resolvePage2;
    api.physicalStockCatalog
      .mockResolvedValueOnce(catalogPayload({ page: 1, page_count: 2, total: 2 }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolvePage1 = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolvePage2 = resolve; }));

    await act(async () => { root.render(<StockSaatIni />); });
    await flush();

    // Fire request A (OLD), then request B (NEW); resolve A after B — A must not win.
    await submitSearch(container, "OLD");
    await submitSearch(container, "NEW");

    await act(async () => {
      resolvePage2(catalogPayload({
        items: [{
          master_item_id: "m-new",
          item_code: "NEW",
          item_name: "New Hit",
          base_unit: "PCS",
          physical_qty: 1,
          stock_dapat_dijual: 1,
          qty_polos: 1,
          qty_polos_status: "READY",
        }],
      }));
      await Promise.resolve();
    });
    await flush();

    await act(async () => {
      resolvePage1(catalogPayload({
        items: [{
          master_item_id: "m-old",
          item_code: "OLD",
          item_name: "Old Hit",
          base_unit: "PCS",
          physical_qty: 9,
          stock_dapat_dijual: 9,
          qty_polos: 9,
          qty_polos_status: "READY",
        }],
      }));
      await Promise.resolve();
    });
    await flush();

    expect(container.querySelector('[data-testid="stock-row-m-new"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-row-m-old"]')).toBeNull();
  });

  test("back-navigation cache paints rows then revalidates; anon identity skips cache", async () => {
    const key = buildStockSaatIniPageCacheKey({
      userId: "user-1",
      branchId: BRANCH_A.id,
      warehouseId: "",
      q: "",
      page: 1,
      pageSize: 50,
    });
    setStockSaatIniPageCache(key, catalogPayload({
      items: [{
        master_item_id: "m-cached",
        item_code: "CACHE",
        item_name: "Cached Row",
        base_unit: "PCS",
        physical_qty: 7,
        stock_dapat_dijual: 7,
        qty_polos: 7,
        qty_polos_status: "READY",
      }],
    }));

    let resolveCatalog;
    api.physicalStockCatalog.mockImplementation(() => new Promise((resolve) => {
      resolveCatalog = resolve;
    }));

    await act(async () => { root.render(<StockSaatIni />); });
    await flush();

    expect(container.querySelector('[data-testid="stock-row-m-cached"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="stock-loading"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-refreshing"]')).toBeTruthy();

    await act(async () => {
      resolveCatalog(catalogPayload());
      await Promise.resolve();
    });
    await flush();
    expect(container.querySelector('[data-testid="stock-row-m1"]')).toBeTruthy();
    expect(getStockSaatIniPageCache(key)?.data.items[0].master_item_id).toBe("m1");

    // Remount as anon — must not reuse previous user's cache.
    act(() => root.unmount());
    container.remove();
    __resetStockSaatIniPageCacheForTests();
    setStockSaatIniPageCache(key, catalogPayload({
      items: [{
        master_item_id: "m-leak",
        item_code: "LEAK",
        item_name: "Should Not Show",
        base_unit: "PCS",
        physical_qty: 1,
      }],
    }));
    authState.user = null;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    let resolveAnon;
    api.physicalStockCatalog.mockImplementation(() => new Promise((resolve) => {
      resolveAnon = resolve;
    }));
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    expect(container.querySelector('[data-testid="stock-row-m-leak"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-loading"]')).toBeTruthy();

    await act(async () => {
      resolveAnon(catalogPayload());
      await Promise.resolve();
    });
    await flush();
    expect(container.querySelector('[data-testid="stock-row-m1"]')).toBeTruthy();
  });

  test("API failure shows error and retry-capable state without claiming compatibility forever", async () => {
    api.physicalStockCatalog.mockRejectedValue({
      response: { data: { detail: "Gagal memuat katalog uji" } },
    });
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    expect(container.querySelector('[data-testid="stock-error"]')?.textContent)
      .toMatch(/Gagal memuat katalog uji/i);
    expect(container.querySelector('[data-testid="stock-mode-chip"]')).toBeNull();
    expect(container.querySelector('[data-testid="stock-mode-chip-pending"]')).toBeTruthy();
  });

  test("effective_branch pin does not issue a second catalog GET", async () => {
    api.branches.mockResolvedValue({ items: [BRANCH_A, BRANCH_B] });
    api.physicalStockCatalog.mockResolvedValue(catalogPayload({
      effective_branch_id: BRANCH_A.id,
    }));
    await act(async () => { root.render(<StockSaatIni />); });
    await flush();
    expect(api.physicalStockCatalog).toHaveBeenCalledTimes(1);
    // Controlled select syncs to effective branch without refetch.
    const branchSelect = container.querySelector('[data-testid="stock-branch-select-input"]');
    expect(branchSelect?.value).toBe(BRANCH_A.id);
  });
});
