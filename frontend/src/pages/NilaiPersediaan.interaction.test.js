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

jest.mock("../components/MasterItemSearchSelect", () => (props) => (
  <div data-testid={props.testId || "master-item-search"}>
    <button
      type="button"
      data-testid="financial-opening-pick"
      onClick={() => props.onChange({
        id: "mid-1",
        item_code: "CUP",
        item_name: "Cup",
        unit: "PCS",
      })}
    >
      Pilih Cup
    </button>
    <button
      type="button"
      data-testid="financial-opening-pick-name"
      onClick={() => props.onChange({ item_name: "Cup tanpa id", item_code: "CUP" })}
    >
      Nama saja
    </button>
  </div>
));

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

const FULL_PERMS = [
  "inventory_valuation.view",
  "inventory_valuation.manage_opening",
  "inventory_valuation.export",
  "inventory_issue.view",
  "inventory_issue.post",
];

describe("Nilai Persediaan financial actions", () => {
  let container;
  let root;

  beforeEach(() => {
    mockHasPerm.mockImplementation((perm) => FULL_PERMS.includes(perm));
    mockApi.inventoryValuation.mockResolvedValue({
      items: [
        {
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
        },
        {
          master_item_id: "mid-zero",
          item_code: "ZERO",
          item_name: "Kosong",
          unit: "PCS",
          stock_fisik: 0,
          current_hpp: 10,
          inventory_value: 0,
          hpp_status: "OK",
        },
        {
          master_item_id: "mid-unknown",
          item_code: "UNK",
          item_name: "Tanpa bukti stok",
          unit: "PCS",
          stock_fisik: 0,
          hpp_reason: "STOCK_STATE_UNAVAILABLE",
          current_hpp: null,
          inventory_value: null,
          hpp_status: "QTY_MISMATCH",
        },
      ],
      summary: {
        total_inventory_value: 500000,
        valued_items: 1,
        items_without_hpp: 4,
        problem_items: 2,
      },
      page: 1,
      page_count: 1,
      total: 3,
    });
    mockApi.financialBookReconciliation.mockResolvedValue({
      opening_value: 100000,
      goods_receipt_value: 60000,
      cogs_value: 16000,
      financial_remaining: 144000,
      inventory_gl_balance: 144000,
      reconciliation_status: "MATCH",
      pending_cogs_count: 1,
      physical_vs_financial: "Qty fisik mengikuti mutasi operasional. Qty finansial mengikuti jurnal posted.",
      item_hpp_scope: "HPP barang tidak mencakup biaya sablon.",
      blocked_stock_receipts: [],
    });
    mockApi.financialInventoryOpenings.mockResolvedValue({
      items: [
        {
          id: "open-1",
          status: "POSTED",
          effective_date: "2026-10-01",
          journal_number: "JU-1",
          journal_origin: "new_journal",
        journal_authority: "valid",
        total: 100000,
        client_request_id: "req-open",
        snapshot_id: "snap-9",
        inventory_account_id: "1-100",
        lines: [{ master_item_id: "mid-1", item_code: "CUP", item_name: "Cup", qty: 100, base_uom: "PCS", total: 100000 }],
      },
      {
        id: "open-multi",
        status: "POSTED",
        effective_date: "2026-10-02",
        total: 30000,
        lines: [
          { master_item_id: "a", item_code: "AAA", item_name: "Dus", qty: 2, base_uom: "PCS", total: 10000 },
          { master_item_id: "b", item_code: "BBB", item_name: "Tinta", qty: 3, base_uom: "KG", total: 20000 },
        ],
      },
        {
          id: "open-2",
          status: "NEEDS_RECOVERY",
          effective_date: "2026-10-03",
          client_request_id: "req-recover",
          total: 1000,
        },
      ],
    });
    mockApi.inventoryIssues.mockResolvedValue({
      items: [
        {
          movement_id: "mov-15",
          reference_number: "SJ-E2E",
          journal_date: "2026-10-05",
          base_qty: 15,
          base_uom: "PCS",
          computed_cogs: 16000,
          financial_status: "pending",
          can_post: true,
          blocker_message: "",
          item: { item_name: "Cup" },
          acquisition_sources: [{ acquisition_key: "opening:open-1" }],
        },
        {
          movement_id: "mov-16",
          reference_number: "SJ-E2E",
          journal_date: "2026-09-01",
          base_qty: 2,
          base_uom: "PCS",
          computed_cogs: 2000,
          financial_status: "excluded",
          can_post: false,
          blocker_code: "PRE_EFFECTIVE",
          blocker_message: "Sebelum tanggal efektif",
          item: { item_name: "Cup" },
        },
      ],
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
      item: {
        id: "snap-1",
        status: "CAPTURED",
        business_date: "2026-10-05",
        captured_at: "2026-10-04T17:00:00Z",
        lines: [{ qty: 100, base_uom: "PCS" }],
      },
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

  async function openTab(id) {
    await act(async () => {
      container.querySelector(`[data-testid="inventory-tab-${id}"]`).dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  }

  async function fillOpening() {
    await openTab("opening");
    await act(async () => {
      container.querySelector('[data-testid="financial-opening-pick"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      setControl('[data-testid="financial-opening-date"]', "2026-10-01");
      setControl('[data-testid="financial-opening-qty"]', "100");
      setControl('[data-testid="financial-opening-cost"]', "1000");
    });
  }

  test("default tab is the item list and summary uses the full filter, not the page", async () => {
    await renderPage();
    expect(container.querySelector('[data-testid="inventory-tab-daftar"]').getAttribute("aria-selected")).toBe("true");
    expect(container.querySelectorAll('[role="tab"]')).toHaveLength(4);
    expect(container.querySelector('[data-testid="financial-opening-form"]')).toBeNull();
    expect(container.querySelector('[data-testid="financial-book-status"]')).toBeNull();
    expect(container.querySelector('[data-testid="inventory-estimate-label"]').textContent).toBe("Estimasi sebagian");
    expect(container.querySelector('[data-testid="inventory-estimate-total"]').textContent).toMatch(/Rp 500\.000/);
    expect(container.querySelector('[data-testid="inventory-estimate-total"]').textContent).not.toMatch(/144\.000/);
    expect(container.querySelector('[data-testid="inventory-value-summary-scope"]').textContent).toMatch(/belum tercakup/);
    expect(container.querySelector('[data-testid="inventory-estimate-missing"]').textContent).toBe("4");
    expect(container.querySelector('[data-testid="inventory-estimate-review"]').textContent).toBe("2");
    expect(container.querySelector('[data-testid="inventory-value-stock-mid-zero"]').textContent).toBe("0");
    expect(container.querySelector('[data-testid="inventory-value-stock-mid-unknown"]').textContent).toBe("Tidak tersedia");
    expect(container.querySelector('[data-testid="inventory-value-search"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="inventory-value-export"]').textContent).toMatch(/Export Excel/);
    expect(mockApi.captureFinancialOpeningSnapshot).not.toHaveBeenCalled();
    expect(mockApi.postFinancialInventoryOpening).not.toHaveBeenCalled();

    const callsBefore = mockApi.inventoryValuation.mock.calls.length;
    await openTab("hpp");
    await openTab("recon");
    await openTab("daftar");
    expect(mockApi.inventoryValuation.mock.calls.length).toBe(callsBefore);
    expect(mockApi.postInventoryIssue).not.toHaveBeenCalled();
  });

  test("preview does not post, a form change drops the old preview, and a second click does not submit again", async () => {
    const pending = deferred();
    mockApi.postFinancialInventoryOpening.mockReturnValue(pending.promise);
    await renderPage();
    await fillOpening();
    expect(container.querySelector('[data-testid="financial-opening-selected"]').textContent).toMatch(/CUP/);
    expect(container.querySelector('[data-testid="financial-opening-selected"]').textContent).toMatch(/Cup/);
    expect(container.querySelector('[data-testid="financial-opening-selected"]').textContent).toMatch(/PCS/);
    expect(container.querySelector('[data-testid="financial-opening-cost"]').value).toBe("1000");
    expect(container.querySelector('[data-testid="financial-opening-row-open-1"]').textContent).toMatch(/CUP · Cup/);
    expect(container.querySelector('[data-testid="financial-opening-row-open-multi"]').textContent).toMatch(/2 barang/);
    expect(container.querySelector('[data-testid="financial-opening-row-open-multi"]').textContent).not.toMatch(/5 /);
    await act(async () => {
      container.querySelector('[data-testid="financial-opening-detail-open-1"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="financial-opening-journal"]').textContent).toMatch(/Otoritas jurnal valid/);
    expect(container.querySelector('[data-testid="financial-opening-drawer"]').textContent).toMatch(/snap-9/);
    expect(mockApi.captureFinancialOpeningSnapshot).not.toHaveBeenCalled();
    expect(mockApi.postFinancialInventoryOpening).not.toHaveBeenCalled();
    expect(mockApi.recoverFinancialInventoryOpening).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="financial-opening-dirty"]').textContent).toMatch(/belum diposting/);
    expect(mockApi.captureFinancialOpeningSnapshot).not.toHaveBeenCalled();

    await act(async () => {
      container.querySelector('[data-testid="financial-opening-preview-button"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockApi.previewFinancialInventoryOpening).toHaveBeenCalledTimes(1);
    expect(mockApi.postFinancialInventoryOpening).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="financial-opening-notice"]').textContent).toMatch(/Tidak ada jurnal yang ditulis/);
    const previewPayload = mockApi.previewFinancialInventoryOpening.mock.calls[0][0];
    expect(previewPayload.effective_date).toBe("2026-10-01");
    expect(previewPayload.lines[0]).toMatchObject({ master_item_id: "mid-1", qty: "100", unit_cost: "1000", source: "APPROVED_OPENING" });

    await act(async () => {
      setControl('[data-testid="financial-opening-qty"]', "90");
    });
    expect(container.querySelector('[data-testid="financial-opening-preview"]')).toBeNull();
    expect(container.querySelector('[data-testid="financial-opening-post"]').disabled).toBe(true);
    expect(mockApi.postFinancialInventoryOpening).not.toHaveBeenCalled();

    await act(async () => {
      setControl('[data-testid="financial-opening-qty"]', "100");
    });
    await act(async () => {
      container.querySelector('[data-testid="financial-opening-preview-button"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const form = container.querySelector('[data-testid="financial-opening-form"]');
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(mockApi.postFinancialInventoryOpening).toHaveBeenCalledTimes(1);
    expect(mockApi.postFinancialInventoryOpening.mock.calls[0][0].client_request_id).toBe(
      mockApi.previewFinancialInventoryOpening.mock.calls[0][0].client_request_id,
    );
    const pendingNotice = container.querySelector('[data-testid="financial-opening-notice"]');
    expect(pendingNotice?.textContent || "").not.toMatch(/Opening posted/);

    pending.resolve({ item: { status: "POSTED", journal_number: "JU-9", journal_origin: "new_journal" } });
    await act(async () => { await pending.promise; });
    expect(container.querySelector('[data-testid="financial-opening-notice"]').textContent).toMatch(/Opening posted/);
    expect(container.querySelector('[data-testid="financial-opening-dirty"]')).toBeNull();
  });

  test("a name without an id is not sent as the item", async () => {
    await renderPage();
    await openTab("opening");
    await act(async () => {
      container.querySelector('[data-testid="financial-opening-pick-name"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await act(async () => {
      setControl('[data-testid="financial-opening-date"]', "2026-10-01");
      setControl('[data-testid="financial-opening-qty"]', "100");
      setControl('[data-testid="financial-opening-cost"]', "1000");
    });
    await act(async () => {
      container.querySelector('[data-testid="financial-opening-preview-button"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="financial-opening-selected"]').textContent).toMatch(/Belum ada barang/);
    const payload = mockApi.previewFinancialInventoryOpening.mock.calls[0][0];
    expect(payload.lines[0].master_item_id).toBe("");
    expect(payload.lines[0].master_item_id).not.toBe("Cup tanpa id");
  });

  test("snapshot and posting run only from explicit clicks", async () => {
    await renderPage();
    await fillOpening();
    await openTab("daftar");
    await openTab("opening");
    expect(mockApi.captureFinancialOpeningSnapshot).not.toHaveBeenCalled();
    expect(mockApi.postFinancialInventoryOpening).not.toHaveBeenCalled();

    await act(async () => {
      container.querySelector('[data-testid="financial-opening-capture"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockApi.captureFinancialOpeningSnapshot).toHaveBeenCalledTimes(1);
    expect(mockApi.captureFinancialOpeningSnapshot.mock.calls[0][0].lines[0].master_item_id).toBe("mid-1");
    expect(mockApi.postFinancialInventoryOpening).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="financial-opening-snapshot"]').textContent).toMatch(/snap-1/);
    expect(container.querySelector('[data-testid="financial-opening-snapshot"]').textContent).toMatch(/2026-10-04T17:00:00Z/);
    expect(container.querySelector('[data-testid="financial-opening-date"]').value).toBe("2026-10-05");
    expect(container.querySelector('[data-testid="financial-opening-date"]').readOnly).toBe(true);
    expect(container.querySelector('[data-testid="financial-opening-qty-compare"]').textContent).toMatch(/Stok fisik saat ini: 135/);
    expect(container.querySelector('[data-testid="financial-opening-qty-compare"]').textContent).toMatch(/Qty snapshot: 100/);
    expect(container.querySelector('[data-testid="financial-opening-qty-compare"]').textContent).toMatch(/Qty opening: 100/);
    expect(container.querySelector('[data-testid="financial-opening-preview"]')).toBeNull();

    await openTab("hpp");
    expect(mockApi.captureFinancialOpeningSnapshot).toHaveBeenCalledTimes(1);
  });

  test("posts one pending COGS row, keeps excluded rows, and loads report totals from the full filter", async () => {
    const pending = deferred();
    mockApi.postInventoryIssue.mockReturnValue(pending.promise);
    await renderPage();
    await openTab("hpp");
    expect(container.querySelector('[data-testid="cogs-group-process"]').textContent).toMatch(/SJ-E2E/);
    expect(container.querySelector('[data-testid="cogs-row-mov-15"]').textContent).toMatch(/Perlu diproses/);
    expect(container.querySelector('[data-testid="cogs-row-mov-15"]').textContent).toMatch(/Cup/);
    expect(container.querySelector('[data-testid="cogs-row-mov-16"]').textContent).toMatch(/Tidak masuk periode/);
    expect(container.querySelector('[data-testid="cogs-row-mov-16"]').textContent).toMatch(/SJ-E2E/);
    expect(container.querySelector('[data-testid="cogs-post-mov-16"]')).toBeNull();
    expect(container.querySelector('[data-testid="cogs-row-mov-16"]').textContent).not.toMatch(/Gagal/);

    await act(async () => {
      container.querySelector('[data-testid="cogs-detail-mov-15"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(container.querySelector('[data-testid="cogs-sources-mov-15"]').textContent).toMatch(/opening:open-1/);

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

    await openTab("recon");
    expect(container.querySelector('[data-testid="financial-book-status"]').textContent).toBe("Cocok");
    expect(container.querySelector('[data-testid="financial-book-identity"]').textContent).toMatch(/Saldo awal diposting \+ Penerimaan diposting − HPP diposting = Sisa nilai buku/);
    expect(container.querySelector('[data-testid="financial-book-remaining"]').textContent).toMatch(/Rp 144\.000/);
    expect(container.querySelector('[data-testid="inventory-estimate-total"]')).toBeNull();
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
    expect(container.querySelector('[href="/buku-besar/laba-rugi"]')).toBeTruthy();
  });

  test("blocked and incomplete book status stay visible, and a failed load is not a match", async () => {
    mockApi.financialBookReconciliation.mockResolvedValue({
      opening_value: 100000,
      goods_receipt_value: null,
      cogs_value: 16000,
      financial_remaining: 144000,
      inventory_gl_balance: 100000,
      reconciliation_status: "BLOCKED",
      blocked_stock_receipts: [{
        receipt_id: "gr-old",
        receipt_number: "GR-OLD",
        code: "HISTORICAL_RECEIPT_EXCLUDED",
        message: "Penerimaan sudah mengubah stok, tetapi posting finansial ditolak.",
      }],
      blockers: [{ code: "INVALID_JOURNAL_SOURCE", message: "Sumber jurnal tidak sah." }],
    });
    await renderPage();
    await openTab("recon");
    expect(container.querySelector('[data-testid="financial-book-status"]').textContent).toBe("Perlu diselesaikan");
    expect(container.querySelector('[data-testid="financial-book-status-code"]').textContent).toMatch(/BLOCKED/);
    expect(container.querySelector('[data-testid="financial-book-blocked-receipt"]').textContent).toMatch(/HISTORICAL_RECEIPT_EXCLUDED/);
    expect(container.querySelector('[data-testid="financial-book-blocker"]').textContent).toMatch(/INVALID_JOURNAL_SOURCE/);
    expect(container.querySelector('[data-testid="financial-book-difference"]').textContent).toMatch(/Rp 44\.000/);
    expect(container.querySelector('[data-testid="financial-book-reconciliation"]').textContent).not.toMatch(/Cocok/);

    mockApi.financialBookReconciliation.mockRejectedValue({ response: { data: { detail: "database down" } } });
    await act(() => root.unmount());
    container.remove();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await renderPage();
    await openTab("recon");
    expect(container.querySelector('[data-testid="financial-book-error"]').textContent).toMatch(/Gagal dimuat/);
    expect(container.querySelector('[data-testid="financial-book-status"]')).toBeNull();
    expect(container.querySelector('[data-testid="financial-book-error"]').textContent).not.toMatch(/Cocok/);
    expect(container.querySelector('[data-testid="inventory-panel-recon"]').textContent).not.toMatch(/Rp 0/);
    await openTab("opening");
    expect(container.querySelector('[data-testid="financial-opening-history-error"]').textContent).toMatch(/Gagal dimuat/);
    expect(container.querySelector('[data-testid="financial-opening-history-empty"]')).toBeNull();
    expect(container.querySelector('[data-testid="financial-opening-history"]')).toBeNull();
  });

  test("incomplete status is labeled and opening recovery follows permission", async () => {
    mockApi.financialBookReconciliation.mockResolvedValue({
      opening_value: null,
      goods_receipt_value: null,
      cogs_value: null,
      financial_remaining: null,
      inventory_gl_balance: null,
      reconciliation_status: "INCOMPLETE",
    });
    await renderPage();
    await openTab("recon");
    expect(container.querySelector('[data-testid="financial-book-status"]').textContent).toBe("Belum lengkap");
    expect(container.querySelector('[data-testid="financial-book-remaining"]').textContent).toMatch(/Belum ada dasar/);
    expect(container.querySelector('[data-testid="financial-book-status"]').textContent).not.toMatch(/Cocok/);

    await openTab("opening");
    await act(async () => {
      container.querySelector('[data-testid="financial-opening-recover"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
      container.querySelector('[data-testid="financial-opening-recover"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockApi.recoverFinancialInventoryOpening).toHaveBeenCalledTimes(1);
    expect(mockApi.recoverFinancialInventoryOpening).toHaveBeenCalledWith({ client_request_id: "req-recover" });
  });

  test("opening and HPP actions stay hidden without permission", async () => {
    mockHasPerm.mockImplementation((perm) => [
      "inventory_valuation.view",
      "inventory_issue.view",
    ].includes(perm));
    await renderPage();
    expect(container.querySelector('[data-testid="inventory-value-export"]')).toBeNull();
    await openTab("opening");
    expect(container.querySelector('[data-testid="financial-opening-form"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="financial-opening-capture"]')).toBeNull();
    expect(container.querySelector('[data-testid="financial-opening-post"]')).toBeNull();
    expect(container.querySelector('[data-testid="financial-opening-recover"]')).toBeNull();
    expect(container.querySelector('[data-testid="financial-opening-preview-button"]')).toBeTruthy();
    await openTab("hpp");
    expect(container.querySelector('[data-testid="cogs-row-mov-15"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="cogs-post-mov-15"]')).toBeNull();
  });

  test("search and export stay available", async () => {
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    URL.createObjectURL = jest.fn(() => "blob:nilai");
    URL.revokeObjectURL = jest.fn();
    mockApi.inventoryValuationExport.mockResolvedValue(new Blob(["xlsx"]));
    await renderPage();
    await act(async () => {
      setControl('[data-testid="inventory-value-search"]', "cup");
    });
    await act(async () => {
      await new Promise((resolve) => { setTimeout(resolve, 350); });
    });
    expect(mockApi.inventoryValuation).toHaveBeenCalledWith(expect.objectContaining({ q: "cup" }));
    await act(async () => {
      container.querySelector('[data-testid="inventory-value-export"]').dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(mockApi.inventoryValuationExport).toHaveBeenCalledWith(expect.objectContaining({
      q: "cup",
      status_filter: "all",
    }));
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  });

  test("stock without a cost is not shown as a zero estimate", async () => {
    mockApi.inventoryValuation.mockResolvedValue({
      items: [{
        master_item_id: "mid-1",
        item_code: "CUP",
        item_name: "Cup",
        unit: "PCS",
        stock_fisik: 135,
        current_hpp: null,
        inventory_value: null,
        hpp_status: "MISSING_COST",
      }],
      summary: { total_inventory_value: 0, valued_items: 0, items_without_hpp: 1, problem_items: 0 },
      total: 1,
      page: 1,
      page_count: 1,
    });
    await renderPage();
    expect(container.querySelector('[data-testid="inventory-estimate-total"]').textContent).toBe("Belum dapat dihitung");
    expect(container.querySelector('[data-testid="inventory-estimate-label"]').textContent).toBe("Nilai estimasi stok");
    expect(container.querySelector('[data-testid="inventory-value-stock-mid-1"]').textContent).toBe("135");
    expect(container.querySelector('[data-testid="inventory-value-summary"]').textContent).not.toMatch(/Rp 0/);
  });

  test("a real zero estimate stays Rp 0 and an empty filter does not", async () => {
    mockApi.inventoryValuation.mockResolvedValue({
      items: [{
        master_item_id: "mid-zero",
        item_code: "Z",
        item_name: "Kosong",
        unit: "PCS",
        stock_fisik: 0,
        current_hpp: 10,
        inventory_value: 0,
        hpp_status: "OK",
      }],
      summary: { total_inventory_value: 0, valued_items: 1, items_without_hpp: 0, problem_items: 0 },
      total: 1,
      page: 1,
      page_count: 1,
    });
    await renderPage();
    expect(container.querySelector('[data-testid="inventory-estimate-label"]').textContent).toBe("Nilai estimasi stok");
    expect(container.querySelector('[data-testid="inventory-estimate-total"]').textContent).toMatch(/^Rp 0$/);

    mockApi.inventoryValuation.mockResolvedValue({
      items: [],
      summary: { total_inventory_value: 0, valued_items: 0, items_without_hpp: 0, problem_items: 0 },
      total: 0,
      page: 1,
      page_count: 1,
    });
    await act(() => root.unmount());
    container.remove();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await renderPage();
    expect(container.querySelector('[data-testid="inventory-estimate-total"]').textContent).toBe("Tidak ada barang");
  });

  test("a failed valuation load is not a complete zero", async () => {
    mockApi.inventoryValuation.mockRejectedValue({ response: { data: { detail: "valuasi gagal" } } });
    await renderPage();
    expect(container.querySelector('[data-testid="inventory-estimate-total"]').textContent).toBe("Gagal dimuat");
    expect(container.querySelector('[data-testid="inventory-estimate-total"]').textContent).not.toMatch(/Rp/);
    expect(container.querySelector('[data-testid="inventory-value-summary-scope"]').textContent).toMatch(/bukan saldo buku/);
  });

  test("an active filter does not reuse the unfiltered summary or the current page", async () => {
    await renderPage();
    const select = container.querySelector('[data-testid="inventory-value-status-filter"]');
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
      setter.call(select, "missing");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => { await Promise.resolve(); });
    expect(container.querySelector('[data-testid="inventory-estimate-total"]').textContent).toBe("Kelengkapan nilai belum diketahui");
    expect(container.querySelector('[data-testid="inventory-estimate-total"]').textContent).not.toMatch(/500\.000|144\.000/);
    expect(mockApi.inventoryValuation).toHaveBeenCalledWith(expect.objectContaining({ status_filter: "missing" }));
  });
});
