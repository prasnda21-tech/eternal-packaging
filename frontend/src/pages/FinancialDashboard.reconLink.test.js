import { act, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("react-router-dom", () => require("../test/clickRouterMock"), { virtual: true });

const router = require("../test/clickRouterMock");

import SalesFinanceDashboard from "./SalesFinanceDashboard";
import NilaiPersediaan from "./NilaiPersediaan";
import PenerimaanBarang from "./PenerimaanBarang";
import { inventoryValueRouteAllowed } from "../lib/inventoryValueLink";

const mockHasPerm = jest.fn();
const mockApi = require("../lib/api").default;

jest.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: { id: "dashboard-recon", name: "Pengguna Uji", role: "finance" },
    hasPerm: (key) => mockHasPerm(key),
  }),
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {
    salesDashboard: jest.fn(),
    financialDashboard: jest.fn(),
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
    listPurchaseGoodsReceipts: jest.fn(),
    listPurchaseOrders: jest.fn(),
    purchaseReceipts: jest.fn(),
    listPurchaseDocuments: jest.fn(),
    postPurchaseGoodsReceiptAccounting: jest.fn(),
    reversePurchaseGoodsReceiptAccounting: jest.fn(),
  },
}));

jest.mock("./PurchaseDocuments", () => () => <div data-testid="purchase-documents-stub" />);
jest.mock("./PurchaseActivityHistory", () => () => null);
jest.mock("./PurchasingFlowNav", () => () => null);
jest.mock("./PurchaseCancelModal", () => () => null);
jest.mock("../components/MasterItemSearchSelect", () => () => <div data-testid="master-item-search" />);

jest.mock("recharts", () => {
  const Pass = ({ children }) => <div>{children}</div>;
  return {
    ResponsiveContainer: Pass,
    LineChart: Pass,
    AreaChart: Pass,
    BarChart: Pass,
    PieChart: Pass,
    Pie: () => null,
    Cell: () => null,
    Line: () => null,
    Area: () => null,
    Bar: () => null,
    XAxis: () => null,
    YAxis: () => null,
    CartesianGrid: () => null,
    Legend: () => null,
    Tooltip: () => null,
  };
});

jest.mock("lucide-react", () => new Proxy({}, {
  get: (_target, prop) => {
    if (prop === "__esModule") return true;
    return (props) => <span data-icon={String(prop)} {...props} />;
  },
}), { virtual: true });

function financePayload(items) {
  return {
    loaded_at: "2026-09-27T10:00:00Z",
    end_date: "2026-09-27",
    performance: { status: "unavailable" },
    position: {},
    cash_movement: { status: "unavailable" },
    charts: {},
    completeness: { status: "complete", detail: "Fixture uji." },
    controls: { items },
    receivables: { status: "zero", items: [] },
    payables: { status: "zero", items: [] },
  };
}

function control(key, href, extra = {}) {
  return {
    key,
    label: key,
    count: extra.count ?? 1,
    kind: "action",
    href,
    status: extra.status || "incomplete",
    detail: extra.detail || "",
  };
}

function Harness() {
  const [, bump] = useState(0);
  useEffect(() => router.__subscribe(() => bump((value) => value + 1)), []);
  if (router.__path().startsWith("/persediaan/nilai-persediaan")) return <NilaiPersediaan />;
  if (router.__path().startsWith("/pembelian/penerimaan-barang")) return <PenerimaanBarang />;
  return <SalesFinanceDashboard />;
}

async function flush() {
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

async function openDetail(container) {
  await act(async () => {
    container.querySelector('[data-testid="finance-open-detail"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

describe("dashboard inventory reconciliation link", () => {
  let container;
  let root;

  beforeEach(() => {
    router.__reset("/dashboard/penjualan");
    mockHasPerm.mockImplementation(() => false);
    mockApi.financialDashboard.mockResolvedValue(financePayload([]));
    mockApi.salesDashboard.mockResolvedValue({});
    mockApi.inventoryValuation.mockResolvedValue({ items: [], summary: null, total: 0 });
    mockApi.inventoryValuationItem.mockResolvedValue({ covered: false, item: null });
    mockApi.financialBookReconciliation.mockResolvedValue({
      opening_value: 1000,
      goods_receipt_value: 0,
      cogs_value: 0,
      financial_remaining: 1000,
      inventory_gl_balance: 1000,
      reconciliation_status: "MATCH",
    });
    mockApi.financialInventoryOpenings.mockResolvedValue({ items: [] });
    mockApi.inventoryIssues.mockResolvedValue({ items: [] });
    mockApi.ledgerInventoryReconciliationSummary.mockResolvedValue({
      reconciliation_status: "DIFFERENCE",
      historical_as_of: false,
      has_activity: true,
      activity_state: "HAS_ACTIVITY",
      read_stability: "STABLE",
      account_count: { inventory: 1, cogs: 0 },
      item_count: 1,
      posting_flags: ["BLOCKED_COGS"],
      pending: { blocked_cogs: { count: 1, items: [] } },
      integrity_anomalies: [],
      unresolved_accounts: [],
      inventory: {
        financial_inventory_basis_value: 6000,
        inventory_source_journal_balance: 7500,
        inventory_gl_balance: null,
        basis_vs_source_difference: 1500,
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
    });
    mockApi.listPurchaseGoodsReceipts.mockResolvedValue({ receipts: [] });
    mockApi.listPurchaseOrders.mockResolvedValue({ orders: [] });
    mockApi.purchaseReceipts.mockResolvedValue({ items: [], page_count: 1 });
    mockApi.listPurchaseDocuments.mockResolvedValue({ documents: [] });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    jest.clearAllMocks();
  });

  test("a dashboard user with only reconciliation view opens the ledger panel from Buka", async () => {
    const perms = ["dashboard.finance.view", "inventory_reconciliation.view"];
    mockHasPerm.mockImplementation((key) => perms.includes(key));
    mockApi.financialDashboard.mockResolvedValue(financePayload([
      control("inventory_reconciliation", "/persediaan/nilai-persediaan?tab=ledger-recon", {
        detail: "Status rekonsiliasi: DIFFERENCE.",
      }),
      control("cogs_unposted", null),
    ]));
    await act(async () => { root.render(<Harness />); });
    await flush();
    await openDetail(container);
    const open = container.querySelector('[data-testid="finance-control-open-inventory_reconciliation"]');
    expect(open).not.toBeNull();
    expect(open.getAttribute("href")).toBe("/persediaan/nilai-persediaan?tab=ledger-recon");
    expect(container.querySelector('[data-testid="finance-control-open-cogs_unposted"]')).toBeNull();
    expect(container.querySelector('[data-testid="finance-control-inventory_reconciliation"]').textContent).toMatch(/DIFFERENCE/);

    await act(async () => {
      open.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="inventory-panel-ledger"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="ledger-reconciliation-status"]').textContent).toMatch(/DIFFERENCE/);
    expect(container.querySelector('[data-testid="inventory-tab-daftar"]')).toBeNull();
    expect(container.querySelector('[data-testid="inventory-tab-opening"]')).toBeNull();
    expect(container.querySelector('[data-testid="inventory-tab-hpp"]')).toBeNull();
    expect(container.querySelector('[data-testid="inventory-tab-recon"]')).toBeNull();
    expect(container.querySelector('[data-testid="financial-opening-form"]')).toBeNull();
    expect(container.querySelector("[data-testid^='cogs-post-']")).toBeNull();
    expect(mockApi.ledgerInventoryReconciliationSummary).toHaveBeenCalled();
    expect(mockApi.inventoryValuation).not.toHaveBeenCalled();
    expect(mockApi.financialBookReconciliation).not.toHaveBeenCalled();
    expect(mockApi.financialInventoryOpenings).not.toHaveBeenCalled();
    expect(mockApi.postInventoryIssue).not.toHaveBeenCalled();
    expect(mockApi.postFinancialInventoryOpening).not.toHaveBeenCalled();
    expect(mockApi.recoverFinancialInventoryOpening).not.toHaveBeenCalled();
  });

  test("valuation permission keeps the opening book and does not create the ledger button", async () => {
    const perms = ["dashboard.finance.view", "inventory_valuation.view", "nav.inventory.inventory_value"];
    mockHasPerm.mockImplementation((key) => perms.includes(key));
    mockApi.financialDashboard.mockResolvedValue(financePayload([
      control("inventory_reconciliation", null, { count: 2, detail: "Status rekonsiliasi: DIFFERENCE." }),
      control("cogs_unposted", "/persediaan/nilai-persediaan"),
    ]));
    await act(async () => { root.render(<Harness />); });
    await flush();
    await openDetail(container);
    const row = container.querySelector('[data-testid="finance-control-inventory_reconciliation"]');
    expect(row.textContent).toMatch(/2/);
    expect(row.textContent).toMatch(/DIFFERENCE/);
    expect(container.querySelector('[data-testid="finance-control-open-inventory_reconciliation"]')).toBeNull();
    expect(container.querySelector('[data-testid="finance-control-open-cogs_unposted"]').getAttribute("href")).toBe("/persediaan/nilai-persediaan");
    expect(container.querySelector('[data-testid="section-keuangan"]')).not.toBeNull();

    await act(async () => {
      router.__reset("/persediaan/nilai-persediaan?tab=recon");
    });
    await flush();
    expect(container.querySelector('[data-testid="inventory-panel-recon"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="inventory-tab-recon"]').textContent).toBe("Rekonsiliasi Buku Opening");
    expect(container.querySelector('[data-testid="inventory-panel-ledger"]')).toBeNull();
    expect(mockApi.financialBookReconciliation).toHaveBeenCalled();
    expect(mockApi.ledgerInventoryReconciliationSummary).not.toHaveBeenCalled();
  });

  test("navigation permission does not open the ledger report", async () => {
    expect(inventoryValueRouteAllowed((key) => key === "nav.inventory.inventory_value")).toBe(false);
    const perms = ["dashboard.finance.view", "nav.inventory.inventory_value"];
    mockHasPerm.mockImplementation((key) => perms.includes(key));
    mockApi.financialDashboard.mockResolvedValue(financePayload([
      control("inventory_reconciliation", null),
      control("cogs_unposted", "/persediaan/nilai-persediaan"),
    ]));
    await act(async () => { root.render(<Harness />); });
    await flush();
    await openDetail(container);
    expect(container.querySelector('[data-testid="finance-control-open-inventory_reconciliation"]')).toBeNull();
    expect(container.querySelector('[data-testid="finance-control-open-cogs_unposted"]').getAttribute("href")).toBe("/persediaan/nilai-persediaan");

    await act(async () => {
      router.__reset("/persediaan/nilai-persediaan?tab=ledger-recon");
    });
    await flush();
    expect(container.querySelector('[data-testid="ledger-access-denied"]').textContent).toBe("Akses ditolak.");
    expect(mockApi.ledgerInventoryReconciliationSummary).not.toHaveBeenCalled();
    expect(mockApi.inventoryValuation).not.toHaveBeenCalled();
  });

  test("both permissions keep each report on its own destination", async () => {
    const perms = [
      "dashboard.finance.view",
      "inventory_reconciliation.view",
      "inventory_valuation.view",
      "nav.inventory.inventory_value",
    ];
    mockHasPerm.mockImplementation((key) => perms.includes(key));
    mockApi.financialDashboard.mockResolvedValue(financePayload([
      control("inventory_reconciliation", "/persediaan/nilai-persediaan?tab=ledger-recon"),
      control("cogs_unposted", "/persediaan/nilai-persediaan"),
    ]));
    await act(async () => { root.render(<Harness />); });
    await flush();
    await openDetail(container);
    const ledger = container.querySelector('[data-testid="finance-control-open-inventory_reconciliation"]');
    const cogs = container.querySelector('[data-testid="finance-control-open-cogs_unposted"]');
    expect(ledger.getAttribute("href")).toBe("/persediaan/nilai-persediaan?tab=ledger-recon");
    expect(cogs.getAttribute("href")).toBe("/persediaan/nilai-persediaan");
    await act(async () => {
      ledger.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="inventory-panel-ledger"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="inventory-panel-recon"]')).toBeNull();

    await act(async () => {
      router.__reset("/persediaan/nilai-persediaan?tab=recon");
    });
    await flush();
    expect(container.querySelector('[data-testid="inventory-panel-recon"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="inventory-panel-ledger"]')).toBeNull();
    expect(mockApi.financialBookReconciliation).toHaveBeenCalled();
  });

  test("Buka on receipt recovery lands on Penerimaan Barang without a reversal right", async () => {
    const perms = [
      "dashboard.finance.view",
      "nav.purchasing.receipts",
      "purchase_receipts.view",
    ];
    mockHasPerm.mockImplementation((key) => perms.includes(key));
    mockApi.listPurchaseGoodsReceipts.mockResolvedValue({
      receipts: [{
        id: "gr-recovery-dashboard",
        receipt_number: "GR-UJI-RECOVERY",
        receipt_date: "2026-10-02",
        supplier: "Supplier uji",
        po_number: "PO-UJI",
        lines: [],
        status: "ACTIVE",
        cancelled: false,
        accounting_status: "posted",
        accounting_recovery_required: true,
      }],
    });
    mockApi.financialDashboard.mockResolvedValue(financePayload([
      control("inventory_recovery", "/pembelian/penerimaan-barang", { count: 1 }),
      control("cogs_unposted", "/persediaan/nilai-persediaan"),
      control("inventory_reconciliation", "/persediaan/nilai-persediaan?tab=ledger-recon"),
    ]));
    await act(async () => { root.render(<Harness />); });
    await flush();
    await openDetail(container);
    const open = container.querySelector('[data-testid="finance-control-open-inventory_recovery"]');
    expect(open.getAttribute("href")).toBe("/pembelian/penerimaan-barang");
    expect(container.querySelector('[data-testid="finance-control-open-cogs_unposted"]').getAttribute("href")).toBe("/persediaan/nilai-persediaan");
    expect(container.querySelector('[data-testid="finance-control-open-inventory_reconciliation"]').getAttribute("href")).toBe("/persediaan/nilai-persediaan?tab=ledger-recon");
    await act(async () => {
      open.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    await flush();
    expect(container.querySelector('[data-testid="penerimaan-title"]').textContent).toBe("Penerimaan Barang");
    await act(async () => {
      container.querySelector('[data-testid="tab-riwayat-penerimaan"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    const row = container.querySelector('[data-testid="po-history-row-gr-recovery-dashboard"]');
    expect(row.textContent).toMatch(/GR-UJI-RECOVERY/);
    await act(async () => {
      row.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="po-history-accounting-recovery-gr-recovery-dashboard"]').textContent)
      .toMatch(/Diperlukan pemulihan/);
    expect(container.querySelector('[data-testid="btn-retry-reverse-gr-accounting-gr-recovery-dashboard"]')).toBeNull();
    expect(container.querySelector('[data-testid="btn-post-gr-accounting-gr-recovery-dashboard"]')).toBeNull();
    expect(mockApi.postPurchaseGoodsReceiptAccounting).not.toHaveBeenCalled();
    expect(mockApi.reversePurchaseGoodsReceiptAccounting).not.toHaveBeenCalled();
    expect(mockApi.recoverFinancialInventoryOpening).not.toHaveBeenCalled();
  });

  test("opening recovery opens the opening tab and a book blocker opens the book tab", async () => {
    const perms = [
      "dashboard.finance.view",
      "inventory_valuation.view",
      "nav.inventory.inventory_value",
    ];
    mockHasPerm.mockImplementation((key) => perms.includes(key));
    mockApi.financialInventoryOpenings.mockResolvedValue({
      items: [{
        id: "fio-recovery-dashboard",
        status: "NEEDS_RECOVERY",
        client_request_id: "fio-uji-dashboard",
        effective_date: "2026-10-01",
        lines: [{ item_code: "CUP", item_name: "Cup uji", qty: 2, base_uom: "PCS", total: 1000 }],
      }],
    });
    mockApi.financialDashboard.mockResolvedValue(financePayload([
      control("opening_incomplete", "/persediaan/nilai-persediaan?tab=opening", {
        count: 1,
        detail: "Opening atau identitas buku persediaan belum lengkap. Dashboard tidak menjalankan recovery.",
      }),
      control("cogs_unposted", "/persediaan/nilai-persediaan"),
    ]));
    await act(async () => { root.render(<Harness />); });
    await flush();
    await openDetail(container);
    const open = container.querySelector('[data-testid="finance-control-open-opening_incomplete"]');
    expect(open.getAttribute("href")).toBe("/persediaan/nilai-persediaan?tab=opening");
    await act(async () => {
      open.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="inventory-panel-opening"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="inventory-panel-recon"]')).toBeNull();
    expect(container.querySelector('[data-testid="financial-opening-row-fio-recovery-dashboard"]').textContent).toMatch(/Cup uji/);
    expect(container.querySelector('[data-testid="financial-opening-recover"]')).toBeNull();
    expect(mockApi.recoverFinancialInventoryOpening).not.toHaveBeenCalled();
    expect(mockApi.postFinancialInventoryOpening).not.toHaveBeenCalled();
  });

  test("a book blocker opens the opening book and keeps the master item deep link", async () => {
    const perms = [
      "dashboard.finance.view",
      "inventory_valuation.view",
      "nav.inventory.inventory_value",
    ];
    mockHasPerm.mockImplementation((key) => perms.includes(key));
    mockApi.financialDashboard.mockResolvedValue(financePayload([
      control("opening_incomplete", "/persediaan/nilai-persediaan?tab=recon&master_item_id=mid-target", { count: 1 }),
      control("cogs_unposted", "/persediaan/nilai-persediaan"),
    ]));
    await act(async () => { root.render(<Harness />); });
    await flush();
    await openDetail(container);
    const book = container.querySelector('[data-testid="finance-control-open-opening_incomplete"]');
    expect(book.getAttribute("href")).toBe("/persediaan/nilai-persediaan?tab=recon&master_item_id=mid-target");
    expect(container.querySelector('[data-testid="finance-control-open-cogs_unposted"]').getAttribute("href")).toBe("/persediaan/nilai-persediaan");
    await act(async () => {
      book.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await flush();
    expect(container.querySelector('[data-testid="inventory-panel-recon"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="inventory-panel-opening"]')).toBeNull();
    expect(container.querySelector('[data-testid="inventory-panel-ledger"]')).toBeNull();
    expect(mockApi.financialBookReconciliation).toHaveBeenCalled();
    expect(mockApi.inventoryValuationItem).toHaveBeenCalledWith("mid-target");
    expect(mockApi.ledgerInventoryReconciliationSummary).not.toHaveBeenCalled();
    expect(mockApi.recoverFinancialInventoryOpening).not.toHaveBeenCalled();
  });

  test("reconciliation view does not grant the dashboard", async () => {
    mockHasPerm.mockImplementation((key) => key === "inventory_reconciliation.view");
    await act(async () => { root.render(<Harness />); });
    await flush();
    expect(container.querySelector('[data-testid="access-denied"]').getAttribute("data-to")).toBe("/");
    expect(container.querySelector('[data-testid="section-keuangan"]')).toBeNull();
    expect(mockApi.financialDashboard).not.toHaveBeenCalled();
    expect(mockApi.salesDashboard).not.toHaveBeenCalled();
  });
});
