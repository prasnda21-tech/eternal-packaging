import fs from "fs";
import path from "path";

describe("Faktur Penjualan UI & Navigation Suite", () => {
  const pageSrc = fs.readFileSync(path.join(__dirname, "FakturPenjualan.jsx"), "utf8");
  const navSrc = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
  const shellSrc = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
  const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const poDetailSrc = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");

  test("FakturPenjualan page contains list, search, table and create elements", () => {
    expect(pageSrc).toMatch(/faktur-penjualan-page/);
    expect(pageSrc).toMatch(/search-invoice-input/);
    expect(pageSrc).toMatch(/refresh-invoice-btn/);
    expect(pageSrc).toMatch(/invoices-table/);
    expect(pageSrc).toMatch(/btn-create-invoice/);
  });

  test("FakturPenjualan create form contains PO snapshot preview, partial qty and financial inputs", () => {
    expect(pageSrc).toMatch(/po-selector/);
    expect(pageSrc).toMatch(/po-snapshot-card/);
    expect(pageSrc).toMatch(/snapshot-po-number/);
    expect(pageSrc).toMatch(/snapshot-customer/);
    expect(pageSrc).toMatch(/invoice-date-input/);
    expect(pageSrc).toMatch(/invoice-term-input/);
    expect(pageSrc).toMatch(/invoice-discount-input/);
    expect(pageSrc).toMatch(/invoice-ppn-input/);
    expect(pageSrc).toMatch(/qty-input/);
    expect(pageSrc).toMatch(/unit-price-input/);
    expect(pageSrc).toMatch(/save-invoice-btn/);
  });

  test("PODetail -> Faktur Penjualan passes exact po_id and bypasses redundant PO chooser", () => {
    expect(poDetailSrc).toMatch(/btn-buat-faktur-penjualan/);
    expect(poDetailSrc).toMatch(/\/penjualan\/faktur-penjualan\?po_id=\$\{order\.id\}&action=create/);
    expect(pageSrc).toMatch(/!queryPoId/);
    expect(pageSrc).toMatch(/queryPoId/);
    expect(pageSrc).toMatch(/loadPoPreview\(selectedPoId\)/);
  });

  test("FakturPenjualan computes line total, discount, PPN, grand total, and remaining qty", () => {
    expect(pageSrc).toMatch(/createCalculations/);
    expect(pageSrc).toMatch(/detailCalculations/);
    expect(pageSrc).toMatch(/subtotal/);
    expect(pageSrc).toMatch(/discount/);
    expect(pageSrc).toMatch(/afterDiscount/);
    expect(pageSrc).toMatch(/ppn/);
    expect(pageSrc).toMatch(/grandTotal/);
    expect(pageSrc).toMatch(/totalRemaining/);
    expect(pageSrc).toMatch(/fully-invoiced-banner/);
  });

  test("navAccess includes Faktur Penjualan under Penjualan parent", () => {
    expect(navSrc).toMatch(/salesInvoice:\s*"nav\.sales\.sales_invoice"/);
    expect(navSrc).toMatch(/Faktur Penjualan/);
    expect(navSrc).toMatch(/\/penjualan\/faktur-penjualan/);
    expect(navSrc).toMatch(/top-nav-sales-invoice/);
  });

  test("AppShell includes FileText tile icon for salesInvoice", () => {
    expect(shellSrc).toMatch(/\[NAV_KEYS\.salesInvoice\]:\s*FileText/);
  });

  test("App.js wires up route for /penjualan/faktur-penjualan with PermGate", () => {
    expect(appSrc).toMatch(/import FakturPenjualan from "\.\/pages\/FakturPenjualan"/);
    expect(appSrc).toMatch(/path="\/penjualan\/faktur-penjualan"/);
    expect(appSrc).toMatch(/NAV_KEYS\.salesInvoice/);
  });

  test("api.js exposes complete sales invoice methods", () => {
    expect(apiSrc).toMatch(/listSalesInvoices:/);
    expect(apiSrc).toMatch(/getSalesInvoice:/);
    expect(apiSrc).toMatch(/getPoInvoicePreview:/);
    expect(apiSrc).toMatch(/createSalesInvoice:/);
    expect(apiSrc).toMatch(/updateSalesInvoice:/);
    expect(apiSrc).toMatch(/deleteSalesInvoice:/);
    expect(apiSrc).toMatch(/bulkDeleteSalesInvoices:/);
  });

  test("Faktur list has checkbox, visible-only select all, Hapus Terpilih, and business-number confirm", () => {
    expect(pageSrc).toMatch(/select-all-invoices/);
    expect(pageSrc).toMatch(/invoice-checkbox-/);
    expect(pageSrc).toMatch(/btn-hapus-terpilih/);
    expect(pageSrc).toMatch(/Hapus Terpilih/);
    expect(pageSrc).toMatch(/delete-confirm-modal/);
    expect(pageSrc).toMatch(/delete-confirm-numbers/);
    expect(pageSrc).toMatch(/invoice_number/);
    expect(pageSrc).toMatch(/selectedIds\.size === invoices\.length/);
  });

  test("FakturPenjualan create mode uses createCalculations safely without undefined invoiceLines", () => {
    expect(pageSrc).not.toMatch(/\binvoiceLines\b/);
    expect(pageSrc).toMatch(/createCalculations\?\.lines/);
    expect(pageSrc).toMatch(/FormProgress/);
    expect(pageSrc).toMatch(/FormSection/);
  });
});
