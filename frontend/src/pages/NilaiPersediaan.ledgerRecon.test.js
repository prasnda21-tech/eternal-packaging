import fs from "fs";
import path from "path";
import { act } from "react";
import { createRoot } from "react-dom/client";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("react-router-dom", () => require("../test/reactRouterMock"), { virtual: true });

import NilaiPersediaan from "./NilaiPersediaan";
import { reconciliationControlHref } from "../lib/inventoryValueLink";
import { ledgerAccountParams, ledgerItemParams, ledgerMoneyText, ledgerQtyText } from "../components/InventoryLedgerReconciliation";

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
    ledgerInventoryReconciliationSummary: jest.fn(),
    ledgerInventoryReconciliationAccounts: jest.fn(),
    ledgerInventoryReconciliationItems: jest.fn(),
  },
}));

jest.mock("../components/MasterItemSearchSelect", () => () => <div data-testid="master-item-search" />);

jest.mock("lucide-react", () => new Proxy({}, {
  get: (_target, prop) => {
    if (prop === "__esModule") return true;
    return (props) => <span data-icon={String(prop)} {...props} />;
  },
}), { virtual: true });

const VALUATION = ["inventory_valuation.view", "nav.inventory.inventory_value", "inventory_valuation.manage_opening", "inventory_issue.view", "inventory_issue.post"];
const LEDGER = ["inventory_reconciliation.view"];
const BOTH = [...VALUATION, ...LEDGER];

function summaryPayload(overrides = {}) {
  return {
    reconciliation_status: "DIFFERENCE",
    historical_as_of: false,
    has_activity: true,
    activity_state: "HAS_ACTIVITY",
    read_stability: "STABLE",
    basis_coverage_complete: false,
    account_count: { inventory: 2, cogs: 1 },
    item_count: 4,
    posting_flags: ["BLOCKED_COGS"],
    pending: {
      blocked_cogs: { count: 1, items: [{ id: "mov-blocked", blocker_code: "BLOCKED" }] },
      pending_cogs: { count: 0, items: [] },
    },
    integrity_anomalies: [{ code: "BASIS_JOURNAL_MISMATCH", message: "Basis dan jurnal sumber berbeda.", account_id: "acc-inv" }],
    unresolved_accounts: [],
    inventory: {
      financial_inventory_basis_value: 6000,
      inventory_source_journal_balance: 6000,
      inventory_gl_balance: null,
      basis_vs_source_difference: 0,
      other_journal_balance: null,
      gl_vs_basis_difference: -1500,
      value_status: "DIFFERENCE",
      identity_holds: false,
    },
    cogs: {
      financial_cogs_basis_value: 0,
      cogs_source_journal_balance: 0,
      cogs_gl_balance: 0,
      basis_vs_source_difference: 0,
      other_journal_balance: 0,
      gl_vs_basis_difference: 0,
      value_status: "RECONCILED",
      identity_holds: true,
    },
    ...overrides,
  };
}

function renderAt(root, entry) {
  const query = String(entry || "").split("?")[1] || "";
  return act(async () => {
    require("../test/reactRouterMock").__reset(query);
    root.render(<NilaiPersediaan />);
  });
}

async function flush() {
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

function setControl(node, value) {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  setter.call(node, value);
  node.dispatchEvent(new Event("input", { bubbles: true }));
  node.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("Phase 5B.1 ledger reconciliation panel", () => {
  let container;
  let root;

  beforeEach(() => {
    mockHasPerm.mockImplementation((perm) => BOTH.includes(perm));
    mockApi.inventoryValuation.mockResolvedValue({ items: [], summary: null, total: 0, page: 1, page_count: 1 });
    mockApi.inventoryValuationItem.mockResolvedValue({
      found: true,
      covered: true,
      master_item_id: "mid-target",
      matched_by: "master_item_id",
      item: { master_item_id: "mid-target", item_code: "CUP", item_name: "Cup", stock_fisik: 2 },
      writes: 0,
    });
    mockApi.financialBookReconciliation.mockResolvedValue({
      financial_remaining: 100,
      inventory_gl_balance: 100,
      reconciliation_status: "MATCH",
      opening_value: 100,
      goods_receipt_value: 0,
      cogs_value: 0,
    });
    mockApi.financialInventoryOpenings.mockResolvedValue({ items: [] });
    mockApi.inventoryIssues.mockResolvedValue({ items: [] });
    mockApi.ledgerInventoryReconciliationSummary.mockResolvedValue(summaryPayload());
    mockApi.ledgerInventoryReconciliationAccounts.mockResolvedValue({
      ...summaryPayload(),
      global_account_count: { inventory: 2, cogs: 1 },
      total: 1,
      inventory_accounts: [{
        account_id: "acc-inv",
        code: "1140",
        name: "Persediaan",
        classification: "inventory",
        coa_resolved: true,
        financial_basis_value: 6000,
        financial_inventory_basis_value: 6000,
        source_journal_balance: 6000,
        inventory_source_journal_balance: 6000,
        gl_balance: null,
        inventory_gl_balance: null,
        basis_vs_source_difference: 0,
        other_journal_balance: null,
        gl_vs_basis_difference: -1500,
        value_status: "DIFFERENCE",
      }],
      cogs_accounts: [],
    });
    mockApi.ledgerInventoryReconciliationItems.mockResolvedValue({
      ...summaryPayload(),
      global_item_count: 4,
      total: 1,
      items: [{
        financial_inventory_item_key: "m:mid-1",
        master_item_id: "mid-1",
        master_item_name: "Cup Uji",
        master_missing: false,
        base_uom: "PCS",
        physical_qty: null,
        financial_qty: 3,
        qty_difference: null,
        quantity_comparable: false,
        quantity_status: "MISSING_PHYSICAL",
        inventory_account_ids: ["acc-inv"],
      }],
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.clearAllMocks();
  });

  test("keeps null distinct from zero and passes only contract filters", () => {
    expect(ledgerMoneyText(null)).toBe("Belum tersedia");
    expect(ledgerMoneyText("")).toBe("Belum tersedia");
    expect(ledgerMoneyText(0)).toBe("Rp 0");
    expect(ledgerMoneyText(-1500)).toMatch(/-1\.500|−1\.500/);
    expect(ledgerQtyText(null, "PCS")).toBe("Belum tersedia");
    expect(ledgerQtyText(0, "PCS")).toBe("0 PCS");
    expect(ledgerAccountParams(" acc-inv ")).toEqual({ account_id: "acc-inv" });
    expect(ledgerAccountParams("")).toEqual({});
    expect(ledgerItemParams(" cup ", " mid-1 ")).toEqual({ q: "cup", item: "mid-1" });
    expect(ledgerItemParams("", "")).toEqual({});
  });

  test("opens the ledger panel from tab=ledger-recon and keeps opening book on tab=recon", async () => {
    await renderAt(root, "/persediaan/nilai-persediaan?tab=ledger-recon");
    await flush();
    expect(container.querySelector('[data-testid="inventory-tab-ledger-recon"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="inventory-panel-ledger"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="inventory-panel-recon"]')).toBeNull();
    expect(container.querySelector('[data-testid="ledger-current-note"]').textContent).toMatch(/saat ini/);
    expect(container.querySelector('[data-testid="ledger-current-note"]').textContent).toMatch(/historical_as_of: false/);
    expect(mockApi.ledgerInventoryReconciliationSummary).toHaveBeenCalledTimes(1);
    expect(mockApi.ledgerInventoryReconciliationAccounts).not.toHaveBeenCalled();
    expect(mockApi.ledgerInventoryReconciliationItems).not.toHaveBeenCalled();

    await renderAt(root, "/persediaan/nilai-persediaan?tab=recon");
    await flush();
    expect(container.querySelector('[data-testid="inventory-tab-recon"]').textContent).toBe("Rekonsiliasi Buku Opening");
    expect(container.querySelector('[data-testid="inventory-tab-recon"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="inventory-panel-recon"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="inventory-panel-ledger"]')).toBeNull();
    expect(container.querySelector('[data-testid="financial-book-identity"]').textContent).toMatch(/Saldo awal diposting/);
    expect(mockApi.financialBookReconciliation).toHaveBeenCalled();
    expect(mockApi.ledgerInventoryReconciliationSummary).toHaveBeenCalledTimes(1);
  });

  test("dashboard control href lands on the ledger panel", async () => {
    const href = reconciliationControlHref({
      key: "inventory_reconciliation",
      href: "/persediaan/nilai-persediaan?tab=ledger-recon",
      status: "incomplete",
    });
    expect(href).toBe("/persediaan/nilai-persediaan?tab=ledger-recon");
    await renderAt(root, href);
    await flush();
    expect(container.querySelector('[data-testid="inventory-panel-ledger"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="inventory-panel-recon"]')).toBeNull();
    expect(mockApi.ledgerInventoryReconciliationSummary).toHaveBeenCalled();
  });

  test("summary, accounts, and items call their own endpoints and keep global counts", async () => {
    await renderAt(root, "/persediaan/nilai-persediaan?tab=ledger-recon");
    await flush();
    expect(container.querySelector('[data-testid="ledger-reconciliation-status"]').textContent).toMatch(/DIFFERENCE/);
    expect(container.querySelector('[data-testid="ledger-reconciliation-status"]').textContent).toMatch(/Selisih/);
    expect(container.querySelector('[data-testid="ledger-posting-flags"]').textContent).toMatch(/BLOCKED_COGS/);
    expect(container.querySelector('[data-testid="ledger-blocked-cogs"]').textContent).toMatch(/1/);
    expect(container.querySelector('[data-testid="ledger-inventory"]').textContent).toMatch(/Belum tersedia/);
    expect(container.querySelector('[data-testid="ledger-inventory"]').textContent).toMatch(/Rp 6\.000|Rp 6000/);
    expect(container.querySelector('[data-testid="ledger-cogs"]').textContent).toMatch(/Rp 0/);
    expect(container.querySelector('[data-testid="ledger-global-item-count"]').textContent).toMatch(/4/);
    expect(container.querySelector('[data-testid="ledger-global-account-count"]').textContent).toMatch(/persediaan 2/);

    await act(async () => {
      container.querySelector('[data-testid="ledger-view-accounts"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(mockApi.ledgerInventoryReconciliationAccounts).toHaveBeenCalledWith({});
    expect(container.querySelector('[data-testid="ledger-account-status-acc-inv"]').textContent).toMatch(/DIFFERENCE/);
    expect(container.querySelector('[data-testid="ledger-account-acc-inv"]').textContent).toMatch(/Belum tersedia/);
    expect(container.querySelector('[data-testid="ledger-global-account-count"]').textContent).toMatch(/persediaan 2/);
    expect(container.querySelector('[data-testid="ledger-filtered-total"]').textContent).toMatch(/1/);

    await act(async () => {
      setControl(container.querySelector('[data-testid="ledger-account-id"]'), " acc-inv ");
      container.querySelector('[data-testid="ledger-account-filter"]').dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(mockApi.ledgerInventoryReconciliationAccounts).toHaveBeenLastCalledWith({ account_id: "acc-inv" });

    await act(async () => {
      container.querySelector('[data-testid="ledger-view-items"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(mockApi.ledgerInventoryReconciliationItems).toHaveBeenCalledWith({});
    expect(container.querySelector('[data-testid="ledger-global-item-count"]').textContent).toBe("Barang keseluruhan 4");
    expect(container.querySelector('[data-testid="ledger-filtered-total"]').textContent).toBe("Baris tampilan 1");
    expect(container.querySelector('[data-testid="ledger-item-mid-1"]').textContent).toMatch(/Belum tersedia/);
    expect(container.querySelector('[data-testid="ledger-item-mid-1"]').textContent).toMatch(/3 PCS/);
    expect(container.querySelector('[data-testid="ledger-item-status-mid-1"]').textContent).toMatch(/MISSING_PHYSICAL/);
    expect(container.querySelector('[data-testid="ledger-items-table"]').textContent).not.toMatch(/\bGL\b/);

    await act(async () => {
      setControl(container.querySelector('[data-testid="ledger-item-id"]'), "mid-1");
      setControl(container.querySelector('[data-testid="ledger-item-q"]'), "cup");
      container.querySelector('[data-testid="ledger-item-filter"]').dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(mockApi.ledgerInventoryReconciliationItems).toHaveBeenLastCalledWith({ item: "mid-1", q: "cup" });
  });

  test("a late response does not replace the newest filter", async () => {
    let resolveFirst;
    mockApi.ledgerInventoryReconciliationItems
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({
        ...summaryPayload(),
        global_item_count: 4,
        total: 1,
        items: [{
          financial_inventory_item_key: "m:mid-2",
          master_item_id: "mid-2",
          master_item_name: "Barang Terbaru",
          base_uom: "PCS",
          physical_qty: 1,
          financial_qty: 1,
          qty_difference: 0,
          quantity_comparable: true,
          quantity_status: "DIFFERENCE",
        }],
      });
    await renderAt(root, "/persediaan/nilai-persediaan?tab=ledger-recon");
    await flush();
    await act(async () => {
      container.querySelector('[data-testid="ledger-view-items"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      setControl(container.querySelector('[data-testid="ledger-item-id"]'), "mid-2");
      container.querySelector('[data-testid="ledger-item-filter"]').dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="ledger-item-mid-2"]').textContent).toMatch(/Barang Terbaru/);
    await act(async () => {
      resolveFirst({
        ...summaryPayload(),
        global_item_count: 4,
        total: 4,
        items: [{
          financial_inventory_item_key: "m:mid-old",
          master_item_id: "mid-old",
          master_item_name: "Barang Lama",
          base_uom: "PCS",
          physical_qty: 9,
          financial_qty: 9,
          quantity_comparable: true,
          qty_difference: 0,
          quantity_status: "MATCHED",
        }],
      });
    });
    await flush();
    expect(container.querySelector('[data-testid="ledger-item-mid-2"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="ledger-item-mid-old"]')).toBeNull();
    expect(container.querySelector('[data-testid="ledger-global-item-count"]').textContent).toBe("Barang keseluruhan 4");
  });

  test("shows access denied for 403 and does not call an empty success state", async () => {
    mockApi.ledgerInventoryReconciliationSummary.mockRejectedValue({ response: { status: 403, data: { detail: "forbidden" } } });
    await renderAt(root, "/persediaan/nilai-persediaan?tab=ledger-recon");
    await flush();
    expect(container.querySelector('[data-testid="ledger-access-denied"]').textContent).toBe("Akses ditolak.");
    expect(container.querySelector('[data-testid="ledger-empty"]')).toBeNull();
    expect(container.querySelector('[data-testid="ledger-summary"]')).toBeNull();
  });

  test("an empty recorded state does not claim reconciliation succeeded", async () => {
    mockApi.ledgerInventoryReconciliationSummary.mockResolvedValue(summaryPayload({
      reconciliation_status: "RECONCILED",
      has_activity: false,
      activity_state: "NO_ACTIVITY",
      item_count: 0,
      account_count: { inventory: 0, cogs: 0 },
      posting_flags: [],
      integrity_anomalies: [],
      pending: { blocked_cogs: { count: 0, items: [] } },
    }));
    await renderAt(root, "/persediaan/nilai-persediaan?tab=ledger-recon");
    await flush();
    expect(container.querySelector('[data-testid="ledger-empty"]').textContent).toMatch(/Belum ada aktivitas/);
    expect(container.querySelector('[data-testid="ledger-empty"]').textContent).not.toMatch(/Cocok|berhasil|RECONCILED/);
    expect(container.querySelector('[data-testid="ledger-reconciliation-status"]').textContent).not.toMatch(/Cocok/);
  });

  test("separates the two permissions and does not add mutation calls", async () => {
    mockHasPerm.mockImplementation((perm) => VALUATION.includes(perm));
    await renderAt(root, "/persediaan/nilai-persediaan?tab=recon&master_item_id=mid-target");
    await flush();
    expect(container.querySelector('[data-testid="inventory-panel-recon"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="inventory-value-focus-id"]').textContent).toBe("ID master mid-target");
    expect(mockApi.inventoryValuationItem).toHaveBeenCalledWith("mid-target");
    expect(mockApi.financialBookReconciliation).toHaveBeenCalled();
    expect(mockApi.ledgerInventoryReconciliationSummary).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="inventory-tab-ledger-recon"]')).toBeNull();

    await renderAt(root, "/persediaan/nilai-persediaan?tab=ledger-recon");
    await flush();
    expect(container.querySelector('[data-testid="ledger-access-denied"]').textContent).toBe("Akses ditolak.");
    expect(mockApi.ledgerInventoryReconciliationSummary).not.toHaveBeenCalled();

    mockHasPerm.mockImplementation((perm) => LEDGER.includes(perm));
    mockApi.inventoryValuation.mockClear();
    mockApi.inventoryValuationItem.mockClear();
    mockApi.financialBookReconciliation.mockClear();
    mockApi.financialInventoryOpenings.mockClear();
    mockApi.inventoryIssues.mockClear();
    await renderAt(root, "/persediaan/nilai-persediaan?tab=ledger-recon");
    await flush();
    expect(container.querySelector('[data-testid="inventory-panel-ledger"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="inventory-tab-daftar"]')).toBeNull();
    expect(container.querySelector('[data-testid="inventory-tab-opening"]')).toBeNull();
    expect(container.querySelector('[data-testid="inventory-tab-hpp"]')).toBeNull();
    expect(container.querySelector('[data-testid="inventory-tab-recon"]')).toBeNull();
    expect(container.querySelector('[data-testid="financial-opening-form"]')).toBeNull();
    expect(container.querySelector("[data-testid^='cogs-post-']")).toBeNull();
    expect(mockApi.inventoryValuation).not.toHaveBeenCalled();
    expect(mockApi.financialBookReconciliation).not.toHaveBeenCalled();
    expect(mockApi.ledgerInventoryReconciliationSummary).toHaveBeenCalled();

    await act(async () => {
      container.querySelector('[data-testid="ledger-view-accounts"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector('[data-testid="ledger-reload"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(mockApi.postInventoryIssue).not.toHaveBeenCalled();
    expect(mockApi.postFinancialInventoryOpening).not.toHaveBeenCalled();
    expect(mockApi.recoverFinancialInventoryOpening).not.toHaveBeenCalled();
    expect(mockApi.previewFinancialInventoryOpening).not.toHaveBeenCalled();
    expect(mockApi.captureFinancialOpeningSnapshot).not.toHaveBeenCalled();

    const component = fs.readFileSync(path.join(__dirname, "../components/InventoryLedgerReconciliation.jsx"), "utf8");
    const client = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
    expect(component).not.toMatch(/\.post\(|\.put\(|\.patch\(|\.delete\(|postInventoryIssue|postFinancialInventoryOpening|financial-opening\/recover/);
    expect(client).toMatch(/ledgerInventoryReconciliationSummary: \(\) =>\s*client\.get\(`\/ledger\/inventory-reconciliation\/summary`\)/);
    expect(client).toMatch(/ledgerInventoryReconciliationAccounts: \(params = \{\}\) =>\s*client\.get\(`\/ledger\/inventory-reconciliation\/accounts`/);
    expect(client).toMatch(/ledgerInventoryReconciliationItems: \(params = \{\}\) =>\s*client\.get\(`\/ledger\/inventory-reconciliation\/items`/);
    expect(component).not.toMatch(/type="date"|historical_as_of" \/>|DatePicker/);
  });

  test("existing item and opening tabs still resolve for a valuation user", async () => {
    mockHasPerm.mockImplementation((perm) => VALUATION.includes(perm));
    await renderAt(root, "/persediaan/nilai-persediaan?tab=hpp");
    await flush();
    expect(container.querySelector('[data-testid="inventory-tab-hpp"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector('[data-testid="inventory-panel-hpp"]')).not.toBeNull();
    expect(mockApi.ledgerInventoryReconciliationSummary).not.toHaveBeenCalled();
    await renderAt(root, "/persediaan/nilai-persediaan?master_item_id=mid-target");
    await flush();
    expect(container.querySelector('[data-testid="inventory-tab-daftar"]').getAttribute("aria-selected")).toBe("true");
    expect(mockApi.inventoryValuationItem).toHaveBeenCalledWith("mid-target");
    expect(container.querySelector('[data-testid="inventory-focus-book"] a').getAttribute("href")).toBe(
      "/persediaan/nilai-persediaan?tab=recon&master_item_id=mid-target",
    );
  });
});
