import fs from "fs";
import path from "path";

describe("Penerimaan Penjualan UI & Navigation Suite", () => {
  const pageSrc = fs.readFileSync(path.join(__dirname, "PenerimaanPenjualan.jsx"), "utf8");
  const navSrc = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const shellSrc = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

  test("PenerimaanPenjualan page contains list, search, and create elements", () => {
    expect(pageSrc).toMatch(/penerimaan-penjualan-page/);
    expect(pageSrc).toMatch(/search-receipt-input/);
    expect(pageSrc).toMatch(/btn-create-receipt/);
  });

  test("PenerimaanPenjualan create modal contains invoice selector, amount input, date input and submit button", () => {
    expect(pageSrc).toMatch(/select-receipt-invoice/);
    expect(pageSrc).toMatch(/input-receipt-amount/);
    expect(pageSrc).toMatch(/input-receipt-date/);
    expect(pageSrc).toMatch(/input-receipt-notes/);
    expect(pageSrc).toMatch(/receipt-modal-body/);
    expect(pageSrc).toMatch(/receipt-modal-footer/);
    expect(pageSrc).toMatch(/btn-submit-receipt/);
  });

  test("PenerimaanPenjualan includes allocation, customer credit, and live status preview", () => {
    expect(pageSrc).toMatch(/previewCalc/);
    expect(pageSrc).toMatch(/melebihi sisa tagihan/);
    expect(pageSrc).toMatch(/Sisa menjadi saldo pelanggan/);
    expect(pageSrc).toMatch(/btn-gunakan-saldo/);
    expect(pageSrc).toMatch(/btn-lihat-bukti/);
    expect(pageSrc).toMatch(/setProofViewerReceipt/);
    expect(pageSrc).toMatch(/SalesReceiptProofViewer/);
    const viewerSrc = fs.readFileSync(path.join(__dirname, "SalesReceiptProofViewer.jsx"), "utf8");
    const inAppSrc = fs.readFileSync(path.join(__dirname, "../components/InAppDocumentViewer.jsx"), "utf8");
    const cssSrc = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
    expect(viewerSrc).toMatch(/InAppDocumentViewer/);
    expect(viewerSrc).toMatch(/layout=["']compact["']/);
    expect(viewerSrc).not.toMatch(/window\.open/);
    expect(inAppSrc).toMatch(/pdfjs-dist/);
    expect(inAppSrc).not.toMatch(/window\.open/);
    expect(inAppSrc).toMatch(/layout = "default"/);
    expect(inAppSrc).toMatch(/inapp-doc-overlay--compact/);
    expect(cssSrc).toMatch(/\.inapp-doc-overlay--compact/);
    expect(cssSrc).toMatch(/\.receipt-proof-row/);
    expect(pageSrc).toMatch(/receipt-proof-compact-row/);
    expect(pageSrc).toMatch(/receipt-detail-proof-row/);
    expect(pageSrc).toMatch(/LUNAS/);
    expect(pageSrc).toMatch(/DP/);
  });

  test("navAccess includes Penerimaan Penjualan under Penjualan parent", () => {
    expect(navSrc).toMatch(/salesReceipt:\s*"nav\.sales\.sales_receipt"/);
    expect(navSrc).toMatch(/Penerimaan Penjualan/);
    expect(navSrc).toMatch(/\/penjualan\/penerimaan-penjualan/);
    expect(navSrc).toMatch(/top-nav-sales-receipt/);
  });

  test("AppShell includes Receipt tile icon for salesReceipt", () => {
    expect(shellSrc).toMatch(/\[NAV_KEYS\.salesReceipt\]:\s*Receipt/);
  });

  test("App.js wires up route for /penjualan/penerimaan-penjualan with PermGate", () => {
    expect(appSrc).toMatch(/const PenerimaanPenjualan = lazy\(\(\) => import\("\.\/pages\/PenerimaanPenjualan"\)\)/);
    expect(appSrc).toMatch(/path="\/penjualan\/penerimaan-penjualan"/);
    expect(appSrc).toMatch(/NAV_KEYS\.salesReceipt/);
  });

  test("api.js exposes complete sales receipt methods", () => {
    expect(apiSrc).toMatch(/listSalesReceipts:/);
    expect(apiSrc).toMatch(/getSalesReceipt:/);
    expect(apiSrc).toMatch(/createSalesReceipt:/);
    expect(apiSrc).toMatch(/deleteSalesReceipt:/);
    expect(apiSrc).toMatch(/bulkDeleteSalesReceipts:/);
    expect(apiSrc).toMatch(/salesReceiptCashBankOptions:/);
    expect(apiSrc).toMatch(/postSalesReceiptAccounting:/);
    expect(apiSrc).toMatch(/reverseSalesReceiptAccounting:/);
  });

  test("Penerimaan list has checkbox, visible-only select all, Hapus Terpilih, and PAY number confirm", () => {
    expect(pageSrc).toMatch(/select-all-receipts/);
    expect(pageSrc).toMatch(/receipt-checkbox-/);
    expect(pageSrc).toMatch(/btn-hapus-terpilih/);
    expect(pageSrc).toMatch(/Hapus Terpilih/);
    expect(pageSrc).toMatch(/delete-confirm-modal/);
    expect(pageSrc).toMatch(/receipt_number/);
    expect(pageSrc).toMatch(/selectedIds\.size === deletableReceipts\.length/);
  });

  test("Penerimaan accounting UI has Kas/Bank picker, status, post, and reverse", () => {
    expect(pageSrc).toMatch(/select-receipt-cash-bank/);
    expect(pageSrc).toMatch(/sales\.receipt\.accounting\.post/);
    expect(pageSrc).toMatch(/sales\.receipt\.accounting\.reverse/);
    expect(pageSrc).toMatch(/receipt-accounting-status-/);
    expect(pageSrc).toMatch(/btn-post-accounting-/);
    expect(pageSrc).toMatch(/btn-reverse-accounting-/);
    expect(pageSrc).toMatch(/post-accounting-confirm-modal/);
    expect(pageSrc).toMatch(/reverse-accounting-confirm-modal/);
    expect(pageSrc).toMatch(/btn-confirm-post-accounting/);
    expect(pageSrc).toMatch(/btn-confirm-reverse-accounting/);
    expect(pageSrc).toMatch(/Belum posting/);
    expect(pageSrc).toMatch(/api\.postSalesReceiptAccounting/);
    expect(pageSrc).toMatch(/api\.reverseSalesReceiptAccounting/);
    expect(pageSrc).toMatch(/can_delete !== false/);
  });

  test("Penerimaan row has Hapus button with PAY business number confirmation and safe-delete wiring", () => {
    expect(pageSrc).toMatch(/btn-delete-receipt-/);
    expect(pageSrc).toMatch(/number: r\.receipt_number/);
    expect(pageSrc).toMatch(/numbers: \[r\.receipt_number\]/);
    expect(pageSrc).toMatch(/api\.deleteSalesReceipt/);
    expect(pageSrc).toMatch(/handleBulkDelete\(\) : handleDelete\(deleteConfirm\.id\)/);
    expect(pageSrc).toMatch(/delete-error/);
    expect(pageSrc).toMatch(/loadReceipts\(\)/);
  });

  test("receipt Edit/Batalkan UI is gated by capability helpers and soft-cancel only", () => {
    expect(pageSrc).toMatch(/canShowReceiptEditUi/);
    expect(pageSrc).toMatch(/canShowReceiptCancelUi/);
    expect(pageSrc).toMatch(/btn-edit-receipt/);
    expect(pageSrc).toMatch(/btn-cancel-receipt-detail/);
    expect(pageSrc).toMatch(/receipt-mutation-frozen-note/);
    expect(pageSrc).toMatch(/Admin Keuangan\/Direktur sesuai SOP/);
    expect(pageSrc).not.toMatch(/Catat penerimaan baru bila perlu koreksi/);
    expect(pageSrc).toMatch(/showDeleteControls\s*=\s*canCancelBusiness/);
    expect(pageSrc).toMatch(/Soft-cancel only/);
  });

  test("PenerimaanPenjualan create is full-page with close controls and escape listener", () => {
    expect(pageSrc).toMatch(/btn-close-receipt-modal/);
    expect(pageSrc).toMatch(/btn-cancel-receipt-modal/);
    expect(pageSrc).toMatch(/receipt-create-page/);
    expect(pageSrc).toMatch(/data-create-mode=\{isModalOpen \? "full-page" : "list"\}/);
    expect(pageSrc).toMatch(/ux90-sr-page/);
    expect(pageSrc).not.toMatch(/aria-modal="true"/);
    expect(pageSrc).toMatch(/ux90-sr-doc-panel/);
    expect(pageSrc).toMatch(/sr-tab-items/);
    expect(pageSrc).toMatch(/sr-tab-info/);
    expect(pageSrc).toMatch(/receipt-summary-boxes/);
    expect(pageSrc).toMatch(/handleCloseCreateModal/);
    expect(pageSrc).toMatch(/Escape/);
    expect(pageSrc).toMatch(/no-eligible-invoices-hint/);
    expect(pageSrc).toMatch(/Buat Penerimaan Penjualan/);
  });

  describe("Critical Penerimaan Invoice Selector Eligibility Tests", () => {
    const { filterEligibleInvoices } = require("../lib/salesReceipts");

    test("CRITICAL: invoice A with outstanding > 0 is eligible and rendered", () => {
      const invoices = [
        {
          id: "inv-a-123",
          invoice_number: "INV-A",
          po_id: "po-a-123",
          po_number: "PO-A",
          customer: "Customer A",
          grand_total: 500000,
          paid_total: 200000,
          outstanding_amount: 300000,
          payment_status: "DP",
          financial_status: "finalized",
        },
      ];

      const eligible = filterEligibleInvoices(invoices);
      expect(eligible).toHaveLength(1);
      expect(eligible[0].id).toBe("inv-a-123");
      expect(eligible[0].po_id).toBe("po-a-123");
      expect(eligible[0].outstanding_amount).toBe(300000);
    });

    test("CRITICAL: paid invoice with outstanding = 0 (LUNAS) must NOT become eligible", () => {
      const invoices = [
        {
          id: "inv-b-paid",
          invoice_number: "INV-B",
          po_id: "po-b-123",
          po_number: "PO-B",
          customer: "Customer B",
          grand_total: 500000,
          paid_total: 500000,
          outstanding_amount: 0,
          payment_status: "LUNAS",
        },
      ];

      const eligible = filterEligibleInvoices(invoices);
      expect(eligible).toHaveLength(0);
    });

    test("CRITICAL: zero grand-total invoice (outstanding = 0) must NOT become eligible", () => {
      const invoices = [
        {
          id: "inv-c-zero",
          invoice_number: "INV-C",
          po_id: "po-c-123",
          po_number: "PO-C",
          customer: "Customer C",
          grand_total: 0,
          paid_total: 0,
          outstanding_amount: 0,
          payment_status: "BELUM DIBAYAR",
        },
      ];

      const eligible = filterEligibleInvoices(invoices);
      expect(eligible).toHaveLength(0);
    });

    test("CRITICAL: mixed list isolates only invoices with outstanding > 0", () => {
      const mixedInvoices = [
        { id: "inv-1", invoice_number: "INV-1", financial_status: "finalized", grand_total: 100000, paid_total: 0, outstanding_amount: 100000 },
        { id: "inv-2", invoice_number: "INV-2", financial_status: "finalized", grand_total: 200000, paid_total: 200000, outstanding_amount: 0 },
        { id: "inv-3", invoice_number: "INV-3", financial_status: "finalized", grand_total: 0, paid_total: 0, outstanding_amount: 0 },
        { id: "inv-4", invoice_number: "INV-4", financial_status: "finalized", grand_total: 350000, paid_total: 100000, outstanding_amount: 250000 },
      ];

      const eligible = filterEligibleInvoices(mixedInvoices);
      expect(eligible).toHaveLength(2);
      expect(eligible.map((x) => x.id)).toEqual(["inv-1", "inv-4"]);
    });

    test("queryInvoiceId targeting an invoice with balance 0 does NOT make it eligible (no bypass)", () => {
      const invoices = [
        { id: "inv-target", invoice_number: "INV-TARGET", outstanding_amount: 0 },
        { id: "inv-other", invoice_number: "INV-OTHER", outstanding_amount: 0 },
      ];

      const eligible = filterEligibleInvoices(invoices);
      expect(eligible).toHaveLength(0);
    });
  });
});
