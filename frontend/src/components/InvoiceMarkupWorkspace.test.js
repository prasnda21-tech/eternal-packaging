import fs from "fs";
import path from "path";

const src = fs.readFileSync(path.join(__dirname, "InvoiceMarkupWorkspace.jsx"), "utf8");
const pageSrc = fs.readFileSync(path.join(__dirname, "../pages/FakturPenjualan.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
const designSrc = fs.readFileSync(path.join(__dirname, "../pages/DesignCetakanSettings.jsx"), "utf8");

describe("Faktur Markup UI wiring", () => {
  test("api exposes markup endpoints", () => {
    expect(apiSrc).toMatch(/listSalesInvoiceMarkups/);
    expect(apiSrc).toMatch(/createSalesInvoiceMarkup/);
    expect(apiSrc).toMatch(/downloadSalesInvoiceMarkupPdf/);
    expect(apiSrc).toMatch(/\/sales-invoices\/\$\{encodeURIComponent\(invoiceId\)\}\/markup/);
  });

  test("print chooser on list and detail", () => {
    expect(pageSrc).toMatch(/print-chooser-modal/);
    expect(pageSrc).toMatch(/print-chooser-original/);
    expect(pageSrc).toMatch(/print-chooser-markup/);
    expect(pageSrc).toMatch(/canViewMarkup/);
    expect(pageSrc).toMatch(/downloadOriginalInvoicePdf/);
    expect(pageSrc).toMatch(/InvoiceMarkupWorkspace/);
  });

  test("workspace locks non-price fields and edits price/bank", () => {
    expect(src).toMatch(/markup-price-/);
    expect(src).toMatch(/markup-bank-name/);
    expect(src).toMatch(/markup-account-number/);
    expect(src).toMatch(/markup-account-holder/);
    expect(src).toMatch(/markup-ro-discount/);
    expect(src).toMatch(/markup-ro-ppn/);
    expect(src).toMatch(/markup-qty-/);
    expect(src).toMatch(/ux90-qty-locked/);
    expect(src).toMatch(/Qty mengikuti faktur asli/);
    expect(src).toMatch(/markup-qty-lock-hint/);
    expect(src).toMatch(/markup-tab-items/);
    expect(src).toMatch(/markup-tab-info/);
    expect(src).toMatch(/markup-tab-charges/);
    expect(src).toMatch(/idempotency_key/);
  });

  test("UX90 create-shell parity classes", () => {
    expect(src).toMatch(/ux90-fp-create/);
    expect(src).toMatch(/ux90-fp-doc-host/);
    expect(src).toMatch(/ux90-fp-doc-header/);
    expect(src).toMatch(/ux90-fp-dh-grid/);
    expect(src).toMatch(/ux90-fp-vtabs/);
    expect(src).toMatch(/ux90-fp-info-cols/);
    expect(src).toMatch(/ux90-fp-totals/);
    expect(src).toMatch(/ux90-fp-doc-footer/);
    expect(src).not.toMatch(/save-invoice-btn/);
    expect(src).not.toMatch(/finalizeSalesInvoice|postSalesInvoiceAccounting/);
  });

  test("history and reprint hooks exist", () => {
    expect(src).toMatch(/markup-history/);
    expect(src).toMatch(/markup-reprint-/);
    expect(src).toMatch(/markup-new-revision/);
    expect(src).toMatch(/downloadSalesInvoiceMarkupPdf/);
    expect(src).toMatch(/formatMarkupHistoryTimestamp/);
    expect(src).toMatch(/formatUrgentIdDateTime/);
  });

  test("design cetakan has Faktur Markup tab and preview", () => {
    expect(designSrc).toMatch(/design-cetakan-tab-invoice-markup/);
    expect(designSrc).toMatch(/invoice_markup/);
    expect(designSrc).toMatch(/design-cetakan-preview-markup/);
    expect(designSrc).toMatch(/design-cetakan-markup-header-add/);
    expect(designSrc).toMatch(/design-cetakan-markup-footer-add/);
    expect(designSrc).toMatch(/markup_prepared_signature_image/);
    expect(designSrc).toMatch(/markup_approved_signature_image/);
    expect(designSrc).toMatch(/design-cetakan-markup-global-scope/);
    expect(designSrc).toMatch(/berlaku untuk semua cabang/);
    expect(designSrc).toMatch(/Pengaturan per\s*cabang belum tersedia/);
  });

  test("history timestamp helper uses WIB display pattern", () => {
    expect(src).toMatch(/WIB/);
    expect(src).toMatch(/formatMarkupHistoryTimestamp\(row\.created_at\)/);
    expect(src).toMatch(/return "—"/);
  });

  test("permissions keys referenced", () => {
    expect(pageSrc).toMatch(/sales\.invoice_markup\.view/);
    expect(pageSrc).toMatch(/sales\.invoice_markup\.create/);
  });
});
