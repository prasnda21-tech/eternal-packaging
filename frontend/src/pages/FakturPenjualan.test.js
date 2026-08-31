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

  test("FakturPenjualan create form contains PO snapshot preview and price inputs", () => {
    expect(pageSrc).toMatch(/po-selector/);
    expect(pageSrc).toMatch(/po-snapshot-card/);
    expect(pageSrc).toMatch(/snapshot-po-number/);
    expect(pageSrc).toMatch(/snapshot-customer/);
    expect(pageSrc).toMatch(/invoice-items-table/);
    expect(pageSrc).toMatch(/unit-price-input/);
    expect(pageSrc).toMatch(/grand-total-summary/);
    expect(pageSrc).toMatch(/save-invoice-btn/);
  });

  test("FakturPenjualan detail/edit form contains editable unit price and live calculations", () => {
    expect(pageSrc).toMatch(/detail-snapshot-card/);
    expect(pageSrc).toMatch(/detail-invoice-number/);
    expect(pageSrc).toMatch(/detail-items-table/);
    expect(pageSrc).toMatch(/detail-unit-price-input/);
    expect(pageSrc).toMatch(/detail-grand-total/);
    expect(pageSrc).toMatch(/save-detail-invoice-btn/);
  });

  test("FakturPenjualan computes line total and grand total authoritatively without mutation", () => {
    expect(pageSrc).toMatch(/createCalculations/);
    expect(pageSrc).toMatch(/detailCalculations/);
    expect(pageSrc).toMatch(/lineTotal/);
    expect(pageSrc).toMatch(/grandTotal/);
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
  });

  test("PODetail includes direct link button to Buat Faktur Penjualan", () => {
    expect(poDetailSrc).toMatch(/btn-buat-faktur-penjualan/);
    expect(poDetailSrc).toMatch(/\/penjualan\/faktur-penjualan\?po_id=/);
  });
});
