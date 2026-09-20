import fs from "fs";
import path from "path";

describe("Faktur Pembelian accounting UI", () => {
  const src = fs.readFileSync(path.join(__dirname, "FakturPembelian.jsx"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

  test("compact accounting section and deep-link", () => {
    expect(src).toMatch(/invoice-accounting-section/);
    expect(src).toMatch(/invoice-detail-accounting-status/);
    expect(src).toMatch(/btn-post-invoice-accounting/);
    expect(src).toMatch(/btn-reverse-invoice-accounting/);
    expect(src).toMatch(/invoice-reverse-reason-input/);
    expect(src).toMatch(/searchParams\.get\("id"\)/);
    expect(src).toMatch(/purchase_accounting\.post/);
    expect(src).toMatch(/purchase_accounting\.reverse/);
    expect(src).toMatch(/Pengaturan Akuntansi Pembelian belum siap/);
  });

  test("API helpers exist", () => {
    expect(api).toMatch(/postPurchaseInvoiceAccounting/);
    expect(api).toMatch(/reversePurchaseInvoiceAccounting/);
    expect(api).toMatch(/\/purchase-invoices\/\$\{encodeURIComponent\(id\)\}\/post-accounting/);
  });
});
