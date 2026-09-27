import { act } from "react";
import { createRoot } from "react-dom/client";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("react-router-dom", () => require("../test/reactRouterMock"), { virtual: true });

import NilaiPersediaan from "./NilaiPersediaan";

const mockHasPerm = jest.fn();
const mockApi = require("../lib/api").default;

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "finance-test", name: "Finance Test", role: "admin" },
    hasPerm: mockHasPerm,
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    inventoryValuation: jest.fn(),
    inventoryValuationItem: jest.fn(),
    inventoryValuationExport: jest.fn(),
    financialBookReconciliation: jest.fn(),
    financialInventoryOpenings: jest.fn(),
    previewFinancialInventoryOpening: jest.fn(),
    postFinancialInventoryOpening: jest.fn(),
    recoverFinancialInventoryOpening: jest.fn(),
    captureFinancialOpeningSnapshot: jest.fn(),
    inventoryIssues: jest.fn(),
    postInventoryIssue: jest.fn(),
    purchaseActivityReport: jest.fn(),
    salesActivityReport: jest.fn(),
  },
}));

jest.mock("../components/MasterItemSearchSelect", () => () => <div data-testid="master-item-search" />);

jest.mock("lucide-react", () => new Proxy({}, {
  get: (_target, prop) => {
    if (prop === "__esModule") return true;
    return (props) => <span data-icon={String(prop)} {...props} />;
  },
}), { virtual: true });

const PERMS = ["inventory_valuation.view", "nav.inventory.inventory_value"];

function renderAt(root, entry) {
  const query = String(entry || "").split("?")[1] || "";
  require("../test/reactRouterMock").__reset(query);
  return act(async () => { root.render(<NilaiPersediaan />); });
}

describe("Nilai Persediaan deep link", () => {
  let container;
  let root;

  beforeEach(() => {
    mockHasPerm.mockImplementation((perm) => PERMS.includes(perm));
    mockApi.inventoryValuation.mockResolvedValue({
      items: [{
        master_item_id: "mid-twin",
        accurate_item_id: "aid-twin",
        item_code: "CUP-100",
        item_name: "ZZZ Cup Polos",
        unit: "PCS",
        stock_fisik: 8,
        current_hpp: 10,
        inventory_value: 80,
        hpp_status: "OK",
      }],
      summary: {
        total_inventory_value: 10000,
        valued_items: 1,
        items_without_hpp: 1,
        problem_items: 0,
      },
      page: 1,
      page_count: 2,
      total: 56,
    });
    mockApi.inventoryValuationItem.mockResolvedValue({
      found: true,
      covered: true,
      master_item_id: "mid-target",
      matched_by: "master_item_id",
      item: {
        master_item_id: "mid-target",
        item_code: "CUP-1000",
        item_name: "ZZZ Cup Polos",
        stock_fisik: 4,
        current_hpp: null,
        inventory_value: null,
        hpp_status: "MISSING_COST",
      },
      inventory_value: null,
      book_value_per_item: null,
      writes: 0,
    });
    mockApi.financialBookReconciliation.mockResolvedValue({
      financial_remaining: 144000,
      inventory_gl_balance: 144000,
      reconciliation_status: "MATCH",
    });
    mockApi.financialInventoryOpenings.mockResolvedValue({ items: [] });
    mockApi.inventoryIssues.mockResolvedValue({ items: [] });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.clearAllMocks();
  });

  test("opens an item outside page one by master id without using the similar row", async () => {
    await renderAt(root, "/persediaan/nilai-persediaan?master_item_id=mid-target");
    await act(async () => { await Promise.resolve(); });
    expect(mockApi.inventoryValuationItem).toHaveBeenCalledWith("mid-target");
    expect(mockApi.inventoryValuation.mock.calls[0][0]).not.toMatchObject({ q: "ZZZ Cup Polos" });
    expect(container.querySelector('[data-testid="inventory-value-focus-id"]').textContent).toBe("ID master mid-target");
    expect(container.querySelector('[data-testid="inventory-value-focus-match"]').textContent).toBe("CUP-1000 · ZZZ Cup Polos");
    expect(container.querySelector('[data-testid="inventory-focus-estimate"]').textContent).toBe("Belum ada dasar");
    expect(container.querySelector('[data-testid="inventory-focus-estimate"]').textContent).not.toMatch(/Rp/);
    expect(container.querySelector('[data-testid="inventory-focus-physical"]').textContent).toBe("4");
    const available = container.querySelector('[data-testid="inventory-focus-available"]');
    expect(available.textContent).toBe("Lihat di Stok Saat Ini");
    expect(available.getAttribute("href")).toBe("/persediaan/stock-saat-ini");
    expect(container.querySelector('[data-testid="inventory-focus-meanings"]').textContent).not.toMatch(/Tersedia untuk pesanan/);
    expect(container.querySelector('[data-testid="inventory-focus-book"]').textContent).toMatch(/Tidak ditampilkan per barang/);
    expect(container.querySelector('[data-testid="inventory-focus-book"] a').getAttribute("href")).toBe(
      "/persediaan/nilai-persediaan?tab=recon&master_item_id=mid-target",
    );
    expect(container.querySelector('[data-master-item-id="mid-twin"]')).not.toBeNull();
    expect(container.querySelector("tr.is-focused")).toBeNull();
  });

  test("shows a clear reason instead of Rp0 when the item is not covered", async () => {
    mockApi.inventoryValuationItem.mockResolvedValue({
      found: false,
      covered: false,
      master_item_id: "mid-service",
      reason_code: "NOT_COVERED",
      reason: "Item berjenis Jasa tidak tercakup dalam daftar Nilai Persediaan.",
      item: null,
      inventory_value: null,
      book_value_per_item: null,
    });
    await renderAt(root, "/persediaan/nilai-persediaan?master_item_id=mid-service");
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('[data-testid="inventory-focus-reason"]').textContent).toMatch(/Jasa/);
    expect(container.querySelector('[data-testid="inventory-focus-estimate"]').textContent).toBe("Belum ada dasar");
    expect(container.querySelector('[data-testid="inventory-value-focus"]').textContent).not.toMatch(/Rp\s*0/);
  });

  test("selects the recon tab from the URL without another valuation formula", async () => {
    await renderAt(root, "/persediaan/nilai-persediaan?tab=recon");
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('[data-testid="inventory-tab-recon"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="inventory-panel-recon"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="inventory-value-focus"]')).toBeNull();
    expect(mockApi.inventoryValuationItem).not.toHaveBeenCalled();
    expect(mockApi.postInventoryIssue).not.toHaveBeenCalled();
    expect(mockApi.postFinancialInventoryOpening).not.toHaveBeenCalled();
  });
});
