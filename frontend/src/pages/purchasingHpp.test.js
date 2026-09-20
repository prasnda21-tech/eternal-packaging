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
      "Permintaan", "Pesanan Pembelian", "Penerimaan Barang", "Faktur Pembelian", "Pembayaran Pembelian",
    ]);
  });

  test("PO list/create/detail and create from Permintaan", () => {
    expect(permintaan).toMatch(/canCreatePo/);
    expect(permintaan).toMatch(/purchase_orders\.create/);
    expect(permintaan).not.toMatch(/Atau nama item bebas/);
    expect(permintaan).toMatch(/btn-buat-pesanan-pembelian/);
    expect(permintaan).toMatch(/canCreatePo && Number\(detail\.total_remaining_order_qty\) > 0/);
    expect(po).toMatch(/pesanan-pembelian-page/);
    expect(po).toMatch(/po-mode-direct/);
    expect(po).toMatch(/request-selector/);
    expect(po).toMatch(/unit-price-input/);
    expect(po).toMatch(/save-po/);
    expect(po).toMatch(/detail-po-number/);
    expect(appSrc).toMatch(/path="\/pembelian\/pesanan-pembelian"/);
  });

  test("Faktur from PO with partial qty and rich detail", () => {
    expect(inv).toMatch(/faktur-pembelian-page/);
    expect(inv).toMatch(/invoice-source-receipt/);
    expect(inv).toMatch(/precedes-goods-receipt/);
    expect(inv).toMatch(/po-selector/);
    expect(inv).toMatch(/qty-input/);
    expect(inv).toMatch(/save-invoice-btn/);
    expect(inv).toMatch(/invoice-detail-lines/);
    expect(inv).toMatch(/invoice-detail-summary/);
    expect(inv).toMatch(/invoice-payment-history/);
    expect(inv).toMatch(/detail-outstanding/);
    expect(inv).toMatch(/purchase-table-inv/);
    expect(inv).toMatch(/>Referensi</);
    expect(po).toMatch(/btn-buat-faktur/);
  });

  test("Purchasing page headers and action titles stay consistent", () => {
    expect(permintaan).toMatch(/Ajukan kebutuhan barang sebelum dibuat menjadi pesanan pembelian/);
    expect(permintaan).toMatch(/Buat Permintaan/);
    expect(permintaan).toMatch(/Buat Permintaan Pembelian/);
    expect(po).toMatch(/Buat pesanan langsung atau berdasarkan permintaan pembelian/);
    expect(po).toMatch(/Buat Pesanan Pembelian/);
    expect(po).toMatch(/Pilih sumber pesanan dan tentukan barang yang akan dibeli/);
    expect(penerimaan).toMatch(/Catat barang yang diterima dari pesanan pembelian atau penerimaan langsung/);
    expect(penerimaan).toMatch(/Pilih PO dan catat jumlah barang yang benar-benar diterima/);
    expect(penerimaan).toMatch(/Lihat seluruh riwayat penerimaan dari PO, manual\/langsung, dan Accurate/);
    expect(penerimaan).toMatch(/Masukkan jumlah barang yang diterima pada tanggal ini/);
    expect(inv).toMatch(/Catat tagihan supplier dari penerimaan barang atau dari PO jika faktur datang lebih dahulu/);
    expect(inv).toMatch(/Buat Faktur Pembelian/);
    expect(pay).toMatch(/Daftar pengajuan pembayaran dari Faktur/);
    expect(pay).toMatch(/Konfirmasi dibayar dilakukan dari detail pengajuan/);
    expect(pay).not.toMatch(/Buat Pembayaran</);
    expect(pay).toMatch(/payment-process-modal/);
  });

  test("compact lists: single-click row opens detail; actions live in detail", () => {
    const css = fs.readFileSync(path.join(__dirname, "purchasePages.css"), "utf8");
    const ui = fs.readFileSync(path.join(__dirname, "../lib/purchaseUi.js"), "utf8");
    const cancelModal = fs.readFileSync(path.join(__dirname, "PurchaseCancelModal.jsx"), "utf8");
    const submitModal = fs.readFileSync(path.join(__dirname, "PurchasePaymentSubmitModal.jsx"), "utf8");
    expect(ui).toMatch(/purchaseRowInteractionProps/);
    expect(ui).toMatch(/handlePurchaseRowClick/);
    expect(ui).toMatch(/handlePurchaseRowKeyDown/);
    expect(permintaan).toMatch(/purchaseRowInteractionProps/);
    expect(permintaan).toMatch(/PurchaseCancelModal/);
    expect(permintaan).toMatch(/cancelPurchaseRequest/);
    expect(permintaan).toMatch(/btn-hapus-pr-detail/);
    expect(permintaan).toMatch(/btn-buat-pesanan-pembelian/);
    expect(permintaan).not.toMatch(/purchase-table-pr[\s\S]{0,400}<th>Item<\/th>/);
    expect(permintaan).not.toMatch(/purchase-table-pr[\s\S]{0,500}cell-actions/);
    expect(po).toMatch(/purchaseRowInteractionProps/);
    expect(po).toMatch(/cancelPurchaseOrder/);
    expect(po).toMatch(/btn-hapus-po-detail/);
    expect(po).not.toMatch(/purchase-table-po[\s\S]{0,400}<th>Item<\/th>/);
    expect(inv).toMatch(/purchaseRowInteractionProps/);
    expect(inv).toMatch(/cancelPurchaseInvoice/);
    expect(inv).toMatch(/btn-ajukan-pembayaran-inv-detail/);
    expect(inv).toMatch(/Ajukan Pembayaran/);
    expect(inv).toMatch(/PurchasePaymentAjukanModal/);
    expect(inv).toMatch(/btn-hapus-inv-detail/);
    expect(inv).not.toMatch(/PurchaseRowMenu/);
    expect(inv).not.toMatch(/purchase-table-inv[\s\S]{0,600}cell-actions/);
    expect(pay).toMatch(/purchaseRowInteractionProps/);
    expect(pay).toMatch(/cancelPurchasePayment/);
    expect(pay).toMatch(/btn-hapus-pay-detail/);
    expect(pay).not.toMatch(/PurchaseRowMenu/);
    expect(pay).not.toMatch(/purchase-table-pay[\s\S]{0,400}cell-actions/);
    expect(penerimaan).toMatch(/purchaseRowInteractionProps/);
    expect(penerimaan).toMatch(/penerimaan-po-detail/);
    expect(penerimaan).toMatch(/btn-terima-po-\$/);
    expect(penerimaan).not.toMatch(/PurchaseRowMenu/);
    expect(penerimaan).not.toMatch(/purchase-table-recv-po[\s\S]{0,400}<th>Item<\/th>/);
    expect(penerimaan).toMatch(/cancelPurchaseGoodsReceipt/);
    expect(penerimaan).toMatch(/btn-buat-faktur-receipt/);
    expect(penerimaan).toMatch(/btn-batalkan-receipt/);
    expect(penerimaan).toMatch(/PurchaseCancelModal/);
    expect(apiSrc).toMatch(/cancelPurchaseGoodsReceipt/);
    expect(cancelModal).not.toMatch(/Alasan penghapusan/);
    expect(cancelModal).toMatch(/purchase-cancel-confirm/);
    expect(cancelModal).toMatch(/Dokumen akan dikeluarkan dari daftar aktif/);
    expect(pay).toMatch(/payment-process-body|payment-process-modal/);
    expect(pay).toMatch(/payment-process-footer/);
    expect(pay).not.toMatch(/btn-create-payment/);
    expect(pay).toMatch(/Status Pengajuan/);
    expect(permintaan).toMatch(/PurchaseActivityHistory/);
    expect(po).toMatch(/PurchaseActivityHistory/);
    expect(inv).toMatch(/PurchaseActivityHistory/);
    expect(pay).toMatch(/PurchaseActivityHistory/);
    expect(css).toMatch(/\.modal-inner\.purchase-payment-modal/);
    expect(css).toMatch(/purchase-activity-history/);
    expect(css).toMatch(/purchase-status-badge/);
    expect(css).toMatch(/thead th\.cell-status/);
    expect(css).toMatch(/modal-inner\.purchase-submit-modal/);
    expect(css).toMatch(/purchase-submit-modal[\s\S]*purchase-modal-actions[\s\S]*sticky/);
    expect(css).toMatch(/is-row-clickable:hover/);
    expect(css).toMatch(/purchase-action-slot/);
    expect(css).not.toMatch(/td\.cell-actions \{\s*display:\s*flex/);
    expect(permintaan).toMatch(/cell-status">Status</);
    expect(po).toMatch(/cell-status th-wrap">Status Penerimaan</);
    expect(po).toMatch(/cell-status th-wrap">Status Faktur</);
    expect(pay).toMatch(/cell-status">Status</);
    expect(submitModal).toMatch(/payment-submit-modal-footer/);
    expect(submitModal).toMatch(/Ajukan Pembayaran/);
    expect(pay).toMatch(/btn-konfirmasi-dibayar/);
    expect(pay).toMatch(/Status Pengajuan/);
    expect(pay).toMatch(/Status Settlement|Status Pembayaran/);
    expect(pay).toMatch(/Tgl Pengajuan|Tanggal Pengajuan/);
  });

  test("Purchasing list tables use scoped full-width classes", () => {
    expect(permintaan).toMatch(/purchase-table-pr/);
    expect(po).toMatch(/purchase-table-po/);
    expect(penerimaan).toMatch(/purchase-table-recv-po/);
    expect(penerimaan).toMatch(/purchase-table-history/);
    expect(inv).toMatch(/purchase-table-inv/);
    expect(pay).toMatch(/purchase-table-pay/);
    expect(permintaan).not.toMatch(/Atau nama item bebas/);
  });

  test("Pembayaran lists submissions with DIAJUKAN/SELESAI filters and process modal", () => {
    expect(pay).toMatch(/pembayaran-pembelian-page/);
    expect(pay).not.toMatch(/btn-create-payment/);
    expect(pay).not.toMatch(/\+ Buat Pembayaran|Buat Pembayaran</);
    expect(pay).toMatch(/payment-status-filters/);
    expect(pay).toMatch(/payment-filter-\$\{f\.id\.toLowerCase\(\)\}/);
    expect(pay).toMatch(/id: "DIAJUKAN"/);
    expect(pay).toMatch(/id: "SELESAI"/);
    expect(pay).toMatch(/payment-process-modal/);
    expect(pay).toMatch(/btn-konfirmasi-dibayar/);
    expect(pay).toMatch(/Tgl Pengajuan|Tanggal Pengajuan|submitted_at/);
  });

  test("HPP settings FIFO/RATA-RATA recognition confirmation status", () => {
    expect(hpp).toMatch(/hpp-method/);
    expect(hpp).toMatch(/FIFO/);
    expect(hpp).toMatch(/RATA-RATA/);
    expect(hpp).toMatch(/Saat Pesanan Pembelian/);
    expect(hpp).toMatch(/Saat Penerimaan Barang/);
    expect(hpp).toMatch(/hpp-confirm-modal/);
    expect(hpp).toMatch(/hpp-last-status/);
    expect(hpp).toMatch(/Titik Pengakuan HPP/);
    expect(hpp).toMatch(/Pengaturan ini mengatur perhitungan valuasi HPP. Accounting Persediaan tetap diakui dari Penerimaan Barang./);
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
    expect(penerimaan).toMatch(/accurate-receipts-heading/);
    expect(penerimaan).toMatch(/po-receipts-heading/);
    expect(penerimaan).toMatch(/Penerimaan terhadap Pesanan Pembelian/);
    expect(penerimaan).toMatch(/tab-penerimaan-po/);
    expect(penerimaan).toMatch(/createPurchaseGoodsReceipt/);
  });

  test("api and login route untouched", () => {
    expect(apiSrc).toMatch(/createPurchaseOrder:/);
    expect(apiSrc).toMatch(/createPurchaseInvoice:/);
    expect(apiSrc).toMatch(/createPurchasePayment:/);
    expect(apiSrc).toMatch(/updateHppSettings:/);
    expect(appSrc).toMatch(/path="\/login"/);
  });
});
