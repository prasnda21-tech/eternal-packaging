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
    expect(pageSrc).toMatch(/btn-submit-receipt/);
  });

  test("PenerimaanPenjualan includes overpayment protection and live status preview", () => {
    expect(pageSrc).toMatch(/previewCalc/);
    expect(pageSrc).toMatch(/isOverpayment/);
    expect(pageSrc).toMatch(/melebihi sisa tagihan/);
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
    expect(appSrc).toMatch(/import PenerimaanPenjualan from "\.\/pages\/PenerimaanPenjualan"/);
    expect(appSrc).toMatch(/path="\/penjualan\/penerimaan-penjualan"/);
    expect(appSrc).toMatch(/NAV_KEYS\.salesReceipt/);
  });

  test("api.js exposes complete sales receipt methods", () => {
    expect(apiSrc).toMatch(/listSalesReceipts:/);
    expect(apiSrc).toMatch(/getSalesReceipt:/);
    expect(apiSrc).toMatch(/createSalesReceipt:/);
    expect(apiSrc).toMatch(/deleteSalesReceipt:/);
    expect(apiSrc).toMatch(/bulkDeleteSalesReceipts:/);
  });

  test("Penerimaan list has checkbox, visible-only select all, Hapus Terpilih, and PAY number confirm", () => {
    expect(pageSrc).toMatch(/select-all-receipts/);
    expect(pageSrc).toMatch(/receipt-checkbox-/);
    expect(pageSrc).toMatch(/btn-hapus-terpilih/);
    expect(pageSrc).toMatch(/Hapus Terpilih/);
    expect(pageSrc).toMatch(/delete-confirm-modal/);
    expect(pageSrc).toMatch(/receipt_number/);
    expect(pageSrc).toMatch(/selectedIds\.size === receipts\.length/);
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

  test("PenerimaanPenjualan modal has accessible close controls, testids, escape listener, and backdrop close", () => {
    expect(pageSrc).toMatch(/btn-close-receipt-modal/);
    expect(pageSrc).toMatch(/btn-cancel-receipt-modal/);
    expect(pageSrc).toMatch(/receipt-create-modal/);
    expect(pageSrc).toMatch(/handleCloseCreateModal/);
    expect(pageSrc).toMatch(/Escape/);
  });
});
