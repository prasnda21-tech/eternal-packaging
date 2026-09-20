import fs from "fs";
import path from "path";

describe("Pembayaran Pembelian accounting UI", () => {
  const src = fs.readFileSync(path.join(__dirname, "PembayaranPembelian.jsx"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

  test("accounting is separate from Konfirmasi Dibayar", () => {
    expect(src).toMatch(/btn-konfirmasi-dibayar/);
    expect(src).toMatch(/btn-post-payment-accounting/);
    expect(src).toMatch(/btn-reverse-payment-accounting/);
    expect(src).toMatch(/payment-post-accounting-modal/);
    expect(src).toMatch(/select-payment-cash-bank/);
    expect(src).toMatch(/payment-detail-accounting-status/);
    expect(src).toMatch(/Terpisah dari Konfirmasi Dibayar/);
    expect(src).toMatch(/purchase\.payment\.accounting\.post/);
    expect(src).toMatch(/searchParams\.get\("id"\) \|\| searchParams\.get\("payment_id"\)/);
  });

  test("API helpers exist", () => {
    expect(api).toMatch(/listPurchasePaymentCashBankOptions/);
    expect(api).toMatch(/postPurchasePaymentAccounting/);
    expect(api).toMatch(/reversePurchasePaymentAccounting/);
  });
});
