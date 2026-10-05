import fs from "fs";
import path from "path";
import { NAV_KEYS, purchasingMenuItems } from "../lib/navAccess";

const has = (...keys) => (key) => keys.includes(key);
const page = fs.readFileSync(path.join(__dirname, "UangMukaPembelian.jsx"), "utf8");
const po = fs.readFileSync(path.join(__dirname, "PesananPembelian.jsx"), "utf8");
const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

test("Pembelian sidebar places Uang Muka after Pesanan and before Penerimaan", () => {
  const labels = purchasingMenuItems(has(
    NAV_KEYS.purchasing,
    NAV_KEYS.purchaseRequests,
    NAV_KEYS.purchaseOrders,
    NAV_KEYS.purchaseInvoices,
    NAV_KEYS.purchasePayments,
    NAV_KEYS.purchaseReceipts,
  )).map((item) => item.label);
  expect(labels).toEqual([
    "Permintaan",
    "Pesanan Pembelian",
    "Uang Muka",
    "Penerimaan Barang",
    "Faktur Pembelian",
    "Pembayaran Pembelian",
  ]);
  const item = purchasingMenuItems(has(NAV_KEYS.purchasing, NAV_KEYS.purchaseOrders))
    .find((row) => row.label === "Uang Muka");
  expect(item.to).toBe("/pembelian/uang-muka");
  expect(app).toMatch(/path="\/pembelian\/uang-muka"/);
});

test("standalone page lists advances and keeps creation on the existing API", () => {
  expect(page).toMatch(/data-testid="uang-muka-page"/);
  expect(page).toMatch(/listPurchaseSupplierAdvances/);
  expect(page).toMatch(/data-testid="uang-muka-supplier"/);
  expect(page).toMatch(/data-testid="uang-muka-po"/);
  expect(page).toMatch(/data-testid="uang-muka-original"/);
  expect(page).toMatch(/data-testid="uang-muka-allocated"/);
  expect(page).toMatch(/data-testid="uang-muka-available"/);
  expect(page).toMatch(/data-testid="uang-muka-list-status"/);
  expect(page).toMatch(/createPurchaseSupplierAdvance/);
  expect(page).toMatch(/Belum ada uang muka/);
  expect(page).not.toMatch(/createPurchaseSupplierAdvanceAllocation/);
  expect(po).toMatch(/data-testid="po-uang-muka"/);
  expect(po).toMatch(/createPurchaseSupplierAdvance/);
});
