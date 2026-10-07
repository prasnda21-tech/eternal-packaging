import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "FakturPembelian.jsx"), "utf8");
const ui = fs.readFileSync(path.join(__dirname, "../lib/purchaseUi.js"), "utf8");

test("invoice payment status is user-facing and advance stays optional", () => {
  expect(ui).toMatch(/Dibayar Sebagian/);
  expect(ui).toMatch(/Belum Dibayar/);
  expect(ui).toMatch(/Lunas/);
  expect(page).toMatch(/purchasePaymentStatusLabel/);
  expect(page).toMatch(/Status Pembayaran/);
  expect(page).toMatch(/Uang Muka Dialokasikan/);
  expect(page).toMatch(/Sisa Tagihan/);
  expect(page).toMatch(/data-testid="detail-normal-payment"/);
  expect(page).toMatch(/data-testid="payment-max"/);
  expect(page).toMatch(/payment_status !== "LUNAS"/);
  expect(page).toMatch(/Gunakan Uang Muka/);
  expect(page).toMatch(/btn-ajukan-pembayaran-inv-detail/);
  expect(page).not.toMatch(/>\s*DP\s*</);
});
