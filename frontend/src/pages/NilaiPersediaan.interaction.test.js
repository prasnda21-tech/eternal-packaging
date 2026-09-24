import { act } from "react";
import { createRoot } from "react-dom/client";
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

jest.mock("lucide-react", () => new Proxy({}, {
  get: (_target, prop) => {
    if (prop === "__esModule") return true;
    return (props) => <span data-icon={String(prop)} {...props} />;
  },
}), { virtual: true });

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

describe("Nilai Persediaan financial actions", () => {
  let container;
  let root;

  beforeEach(() => {
    mockHasPerm.mockImplementation((perm) => [
      "inventory_valuation.view",
      "inventory_valuation.manage_opening",
      "inventory_valuation.export",
      "inventory_issue.view",
      "inventory_issue.post",
    ].includes(perm));
    mockApi.inventoryValuation.mockResolvedValue({
      items: [{
        master_item_id: "mid-1",
        item_code: "CUP",
        item_name: "Cup",
        unit: "PCS",
        stock_fisik: 135,
        current_hpp: 1066.67,
        inventory_value: 144000,
        hpp_status: "OK",
        hpp_source: "APPROVED_OPENING",
        financial_posting_status: "POSTED",
      }],
      summary: { item_count: 1 },
      page: 1,
      page_count: 1,
      total: 1,
    });
    mockApi.financialBookReconciliation.mockResolvedValue({
      financial_remaining: 144000,
      inventory_gl_balance: 144000,
      reconciliation_status: "MATCH",
      pending_cogs_count: 1,
      physical_vs_financial: "Qty fisik mengikuti mutasi operasional. Qty finansial mengikuti jurnal posted.",
      item_hpp_scope: "HPP barang tidak mencakup biaya sablon.",
    });
    mockApi.financialInventoryOpenings.mockResolvedValue({
      items: [{
        id: "open-1",
        status: "POSTED",
        effective_date: "2026-10-01",
        journal_number: "JU-1",
        journal_origin: "new_journal",
        journal_authority: "valid",
        total: 100000,
        client_request_id: "req-open",
      }],
    });
    mockApi.inventoryIssues.mockResolvedValue({
      items: [{
        movement_id: "mov-15",
        reference_number: "SJ-E2E",
        journal_date: "2026-10-05",
        base_qty: 15,
        base_uom: "PCS",
        computed_cogs: 16000,
        financial_status: "pending",
        can_post: true,
        blocker_message: "",
        acquisition_sources: [{ acquisition_key: "opening:open-1" }],
      }],
    });
    mockApi.previewFinancialInventoryOpening.mockResolvedValue({
      can_post: true,
      total: 100000,
      effective_date: "2026-10-01",
      timezone: "Asia/Jakarta",
      book_value_source: "APPROVED_OPENING",
      blockers: [],
      snapshots: [],
      writes: 0,
    });
    mockApi.captureFinancialOpeningSnapshot.mockResolvedValue({
      item: { id: "snap-1", status: "CAPTURED", business_date: "2026-10-05", lines: [{ qty: 100, base_uom: "PCS" }] },
    });
    mockApi.purchaseActivityReport.mockResolvedValue({
      filtered_count: 2,
      total_amount: 60000,
      items: [{ document_id: "gr-1", document_number: "GR-1", supplier: "Supplier", date: "2026-10-02", qty: 50, uom: "PCS", amount: 60000, receipt_status: "received", invoice_numbers: ["FPB-E2E"], payment_numbers: ["PPB-E2E"], stock_posting_status: "POSTED" }],
    });
    mockApi.salesActivityReport.mockResolvedValue({
      filtered_count: 1,
      total_amount: 30000,
      items: [{ document_id: "inv-1", document_number: "INV-1", customer: "Pelanggan Uji", date: "2026-10-06", qty: 1, amount: 30000, posting_status: "POSTED" }],
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

  async function renderPage() {
    await act(async () => { root.render(<NilaiPersediaan />); });
    await act(async () => { await Promise.resolve(); });
  }

  function setControl(selector, value) {
    const node = container.querySelector(selector);
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    setter.call(node, value);
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
  }

  test("preview does not post, success appears only after posting, and a second click does not submit again", async () => {
    const pending = deferred();
    mockApi.postFinancialInventoryOpening.mockReturnValue(pending.promise);
    await renderPage();
    expect(container.querySelector('[data-testid="financial-book-status"]').textContent).toBe("MATCH");
    expect(container.querySelector('[data-testid="inventory-value-source-mid-1"]').textContent).toBe("Opening disetujui");
    expect(container.querySelector('[data-testid="financial-opening-journal"]').textContent).toMatch(/Otoritas jurnal valid/);

    await act(async () => {
      setControl('[data-testid="financial-opening-date"]', "2026-10-01");
      setControl('[data-testid="financial-opening-item"]', "mid-1");
      setControl('[data-testid="financial-opening-qty"]', "100");
      setControl('[data-testid="financial-opening-cost"]', "1000");
    });
    expect(container.querySelector('[data-testid="financial-opening-dirty"]').textContent).toMatch(/belum diposting/);

    await act(async () => {
      container.querySelector('[data-testid="financial-opening-preview-button"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockApi.previewFinancialInventoryOpening).toHaveBeenCalledTimes(1);
    expect(mockApi.postFinancialInventoryOpening).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="financial-opening-notice"]').textContent).toMatch(/Tidak ada jurnal yang ditulis/);
    const previewPayload = mockApi.previewFinancialInventoryOpening.mock.calls[0][0];
    expect(previewPayload.effective_date).toBe("2026-10-01");
    expect(previewPayload.lines[0]).toMatchObject({ master_item_id: "mid-1", qty: "100", unit_cost: "1000", source: "APPROVED_OPENING" });

    const form = container.querySelector('[data-testid="financial-opening-form"]');
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(mockApi.postFinancialInventoryOpening).toHaveBeenCalledTimes(1);
    const pendingNotice = container.querySelector('[data-testid="financial-opening-notice"]');
    expect(pendingNotice?.textContent || "").not.toMatch(/Opening posted/);

    pending.resolve({ item: { status: "POSTED", journal_number: "JU-9", journal_origin: "new_journal" } });
    await act(async () => { await pending.promise; });
    expect(container.querySelector('[data-testid="financial-opening-notice"]').textContent).toMatch(/Opening posted/);
    expect(container.querySelector('[data-testid="financial-opening-dirty"]')).toBeNull();
  });

  test("posts one pending COGS row and loads report totals from the full filter", async () => {
    const pending = deferred();
    mockApi.postInventoryIssue.mockReturnValue(pending.promise);
    await renderPage();
    expect(container.querySelector('[data-testid="cogs-row-mov-15"]').textContent).toMatch(/pending/);
    expect(container.querySelector('[data-testid="cogs-row-mov-15"]').textContent).toMatch(/opening:open-1/);

    const button = container.querySelector('[data-testid="cogs-post-mov-15"]');
    await act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      button.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockApi.postInventoryIssue).toHaveBeenCalledTimes(1);
    expect(mockApi.postInventoryIssue).toHaveBeenCalledWith("mov-15");
    expect(container.querySelector('[data-testid="cogs-queue-notice"]')).toBeNull();

    pending.resolve({ item: { financial_status: "posted", accounting_journal_id: "J-COGS" } });
    await act(async () => { await pending.promise; });
    expect(container.querySelector('[data-testid="cogs-queue-notice"]').textContent).toMatch(/posted/);

    await act(async () => {
      setControl('[data-testid="activity-report-start"]', "2026-10-01");
      setControl('[data-testid="activity-report-end"]', "2026-10-31");
      container.querySelector('[data-testid="activity-report-load"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockApi.purchaseActivityReport).toHaveBeenCalledWith({
      start_date: "2026-10-01",
      end_date: "2026-10-31",
      page: 1,
      page_size: 50,
    });
    expect(container.querySelector('[data-testid="purchase-report-total"]').textContent).toMatch(/2 baris/);
    expect(container.querySelector('[data-testid="purchase-report-total"]').textContent).toMatch(/Halaman ini 1 baris/);
    expect(container.querySelector('[data-testid="sales-report-total"]').textContent).toMatch(/Rp 30\.000/);
    expect(container.querySelector('[data-testid="purchase-report-table"]').textContent).toMatch(/FPB-E2E/);
  });
});
