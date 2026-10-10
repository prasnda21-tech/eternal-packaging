import fs from "fs";
import path from "path";
import {
  SALES_LIST_DEFAULT_PAGE_SIZE,
  salesFiveTabListParams,
  salesFiveTabCountsFromResponse,
} from "../lib/salesFiveTabs";
import {
  canBusinessCancelInvoice,
  canBusinessCancelReceipt,
  ensureIdempotencyIntent,
  interpretAutoLifecycleResponse,
  interpretBusinessCancelResponse,
} from "../lib/salesLifecycleUi";

const read = (rel) => fs.readFileSync(path.join(__dirname, rel), "utf8");

describe("Package B2 — five-tab + lifecycle UI contracts", () => {
  const pesanan = read("PesananPenjualan.jsx");
  const faktur = read("FakturPenjualan.jsx");
  const penerimaan = read("PenerimaanPenjualan.jsx");
  const api = read("../lib/api.js");
  const rowNav = read("salesDocumentRowNav.test.js");

  test("all three pages expose five tabs in required order", () => {
    for (const src of [pesanan, faktur, penerimaan]) {
      expect(src).toMatch(/SALES_LIST_TABS/);
    }
    expect(pesanan).toMatch(/sales-order-tab-\$\{tab\.testIdSuffix\}/);
    expect(faktur).toMatch(/tab-faktur-aktif/);
    expect(faktur).toMatch(/tab-faktur-\$\{tab\.testIdSuffix\}|tab-faktur-selesai|tab-faktur-pending/);
    expect(faktur).toMatch(/tab-faktur-dibatalkan/);
    expect(penerimaan).toMatch(/tab-receipt-\$\{tab\.testIdSuffix\}/);
    expect(read("../lib/salesFiveTabs.js")).toMatch(/testIdSuffix: "semua"/);
    expect(read("../lib/salesFiveTabs.js")).toMatch(/testIdSuffix: "pending"/);
    expect(read("../lib/salesFiveTabs.js")).toMatch(/testIdSuffix: "batal"/);
  });

  test("list requests use exclusive list_tab and default 50/page", () => {
    expect(salesFiveTabListParams({ listTab: "pending" })).toEqual({
      list_tab: "pending",
      page: 1,
      page_size: SALES_LIST_DEFAULT_PAGE_SIZE,
    });
    expect(pesanan).toMatch(/listTab/);
    expect(pesanan).toMatch(/SALES_ORDER_DEFAULT_PAGE_SIZE/);
    expect(faktur).toMatch(/salesFiveTabListParams|invoiceListParams/);
    expect(faktur).toMatch(/SALES_LIST_DEFAULT_PAGE_SIZE/);
    expect(penerimaan).toMatch(/salesFiveTabListParams/);
    expect(penerimaan).toMatch(/receipt-page-size/);
    expect(salesFiveTabCountsFromResponse({
      all_total: 9,
      five_tab_active_total: 3,
      active_total: 8,
      completed_total: 2,
      pending_total: 1,
      cancelled_total: 3,
    }).active).toBe(3);
  });

  test("clickable rows remain wired; receipt financial Edit stays blocked", () => {
    expect(rowNav).toMatch(/clickableDocumentRowProps/);
    expect(pesanan).toMatch(/clickableDocumentRowProps/);
    expect(faktur).toMatch(/clickableDocumentRowProps/);
    expect(penerimaan).toMatch(/clickableDocumentRowProps/);
    expect(faktur).toMatch(/financialLocked/);
    expect(penerimaan).not.toMatch(/updateSalesReceipt\(/);
    expect(penerimaan).not.toMatch(/Simpan Perubahan Penerimaan/);
  });

  test("invoice/receipt create send idempotency_key; cancel is opt-in only", () => {
    expect(faktur).toMatch(/idempotency_key:\s*idempotencyKey/);
    expect(penerimaan).toMatch(/idempotency_key:\s*idempotencyKey/);
    expect(api).toMatch(/businessCancelSalesInvoice/);
    expect(api).toMatch(/businessCancelSalesReceipt/);
    expect(api).toMatch(/recoverAutoSalesInvoiceAccounting/);
    expect(api).toMatch(/recoverAutoSalesReceiptAccounting/);
    expect(faktur).toMatch(/canBusinessCancelInvoice/);
    expect(penerimaan).toMatch(/canBusinessCancelReceipt/);
    expect(canBusinessCancelInvoice((p) => p === "sales.invoice.delete")).toBe(false);
    expect(canBusinessCancelInvoice((p) => p === "sales.invoice.cancel")).toBe(true);
    expect(canBusinessCancelReceipt((p) => p === "sales.receipt.cancel")).toBe(true);
    const ref = { current: null };
    const key = ensureIdempotencyIntent(ref, "a", { prefix: "x" });
    expect(ensureIdempotencyIntent(ref, "a", { prefix: "x" })).toBe(key);
  });

  test("flag-off legacy parity: finalize/post/reverse preserved; auto success needs financial_complete", () => {
    expect(faktur).toMatch(/finalizeSalesInvoice/);
    expect(faktur).toMatch(/postSalesInvoiceAccounting/);
    expect(faktur).toMatch(/reverseSalesInvoiceAccounting/);
    expect(faktur).toMatch(/btn-post-accounting/);
    expect(penerimaan).toMatch(/postSalesReceiptAccounting/);
    expect(penerimaan).toMatch(/btn-post-accounting-/);
    expect(interpretAutoLifecycleResponse({ invoice: { id: "1" }, message: "ok" }).mode).toBe("legacy");
    expect(interpretAutoLifecycleResponse({
      invoice: { id: "1" },
      financial_complete: true,
      auto_accounting_status: "complete",
    }).ok).toBe(true);
    expect(interpretBusinessCancelResponse({}, {
      statusCode: 409,
      httpErrorBody: "Pembatalan bisnis faktur belum diaktifkan.",
    }).flagOff).toBe(true);
    // Legacy hard-delete path remains for users without cancel perm.
    expect(faktur).toMatch(/await api\.deleteSalesInvoice\(id\)/);
    expect(penerimaan).toMatch(/await api\.deleteSalesReceipt\(id\)/);
    expect(faktur).toMatch(/Dokumen berikut akan dihapus:/);
    expect(penerimaan).toMatch(/Dokumen berikut akan dihapus:/);
  });

  test("invoice/receipt Hapus closes only on verified cancel success; 403/409 never hard-deletes", () => {
    for (const src of [faktur, penerimaan]) {
      expect(src).toMatch(/if \(!interpreted\.ok\)/);
      expect(src).toMatch(/clearIdempotencyIntent\(cancelIdempotencyRef\)/);
      expect(src).toMatch(/Fail closed: do not fall through to legacy hard delete|setDeleteErr\(interpreted\.message\);\s*\n\s*return;/);
      expect(src).not.toMatch(/if \(interpreted\.failed \|\| interpreted\.pending\) return/);
      expect(src).toMatch(/Dokumen berikut akan dibatalkan\. Riwayat tetap tersimpan\./);
      expect(src).toMatch(/businessCancelSales/);
    }
    // No automatic retry / duplicate navigation after cancel.
    expect(faktur).not.toMatch(/setInterval\(|while\s*\(.*businessCancel/);
    expect(penerimaan).not.toMatch(/setInterval\(|while\s*\(.*businessCancel/);
    expect(faktur).toMatch(/ensureIdempotencyIntent\(cancelIdempotencyRef/);
    expect(penerimaan).toMatch(/ensureIdempotencyIntent\(cancelIdempotencyRef/);
  });

  test("DD/MM/YYYY invoice date field remains the create/edit control", () => {
    expect(faktur).toMatch(/SalesInvoiceDateField/);
    expect(faktur).toMatch(/invoice-date-input|createDateRef|detailDateRef/);
  });
});
