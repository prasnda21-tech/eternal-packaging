import fs from "fs";
import path from "path";
import { NAV_KEYS, purchasingMenuItems } from "../lib/navAccess";

const has = (...keys) => (k) => keys.includes(k);

describe("Pembelian workflow UI", () => {
  const permintaan = fs.readFileSync(path.join(__dirname, "PermintaanPembelian.jsx"), "utf8");
  const po = fs.readFileSync(path.join(__dirname, "PesananPembelian.jsx"), "utf8");
  const inv = fs.readFileSync(path.join(__dirname, "FakturPembelian.jsx"), "utf8");
  const pay = fs.readFileSync(path.join(__dirname, "PembayaranPembelian.jsx"), "utf8");
  const hpp = fs.readFileSync(path.join(__dirname, "HppSettings.jsx"), "utf8");
  const penerimaan = fs.readFileSync(path.join(__dirname, "PenerimaanBarang.jsx"), "utf8");
  const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");

  test("sidebar Pembelian items stay ordered without duplicating Penerimaan Barang", () => {
    const items = purchasingMenuItems(has(
      NAV_KEYS.purchasing,
      NAV_KEYS.purchaseRequests,
      NAV_KEYS.purchaseOrders,
      NAV_KEYS.purchaseInvoices,
      NAV_KEYS.purchasePayments,
      NAV_KEYS.purchaseReceipts,
    ));
    expect(items.map((i) => i.label)).toEqual([
      "Permintaan", "Pesanan Pembelian", "Faktur Pembelian", "Pembayaran Pembelian", "Penerimaan Barang",
    ]);
  });

  test("PO list/create/detail and create from Permintaan", () => {
    expect(permintaan).toMatch(/btn-buat-pesanan-pembelian/);
    expect(po).toMatch(/pesanan-pembelian-page/);
    expect(po).toMatch(/request-selector/);
    expect(po).toMatch(/unit-price-input/);
    expect(po).toMatch(/save-po/);
    expect(po).toMatch(/detail-po-number/);
    expect(appSrc).toMatch(/path="\/pembelian\/pesanan-pembelian"/);
  });

  test("Faktur from PO with partial qty", () => {
    expect(inv).toMatch(/faktur-pembelian-page/);
    expect(inv).toMatch(/po-selector/);
    expect(inv).toMatch(/qty-input/);
    expect(inv).toMatch(/save-invoice-btn/);
    expect(po).toMatch(/btn-buat-faktur/);
  });

  test("Pembayaran DP/partial/lunas outstanding", () => {
    expect(pay).toMatch(/pembayaran-pembelian-page/);
    expect(pay).toMatch(/payment-amount/);
    expect(pay).toMatch(/outstanding/);
    expect(pay).toMatch(/paid-total/);
    expect(pay).toMatch(/save-payment/);
  });

  test("HPP settings FIFO/RATA-RATA recognition confirmation status", () => {
    expect(hpp).toMatch(/hpp-method/);
    expect(hpp).toMatch(/FIFO/);
    expect(hpp).toMatch(/RATA-RATA/);
    expect(hpp).toMatch(/Saat Pesanan Pembelian/);
    expect(hpp).toMatch(/Saat Penerimaan Barang/);
    expect(hpp).toMatch(/hpp-confirm-modal/);
    expect(hpp).toMatch(/hpp-last-status/);
    expect(settingsSrc).toMatch(/settings-tab-hpp/);
    expect(settingsSrc).toMatch(/Pengaturan HPP/);
  });

  test("Penerimaan Barang keeps Accurate sync and adds PO-linked receive", () => {
    expect(penerimaan).toMatch(/penerimaan-sync-button/);
    expect(penerimaan).toMatch(/po-linked-receipts/);
    expect(penerimaan).toMatch(/save-po-receipt/);
    expect(penerimaan).toMatch(/Sinkronkan dari Accurate/);
    expect(penerimaan).toMatch(/btn-buat-penerimaan/);
    expect(penerimaan).toMatch(/penerimaan-create-form/);
  });

  test("api and login route untouched", () => {
    expect(apiSrc).toMatch(/createPurchaseOrder:/);
    expect(apiSrc).toMatch(/createPurchaseInvoice:/);
    expect(apiSrc).toMatch(/createPurchasePayment:/);
    expect(apiSrc).toMatch(/updateHppSettings:/);
    expect(appSrc).toMatch(/path="\/login"/);
  });
});
