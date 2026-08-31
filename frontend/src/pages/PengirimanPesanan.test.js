import fs from "fs";
import path from "path";
import {
  NAV_KEYS,
  NAV_MENU_FALLBACK,
  SALES_NAV_ROUTES,
  SALES_TILE_TESTIDS,
  SALES_TILE_TONES,
  salesMenuItems,
  salesNavVisible,
} from "../lib/navAccess";
import api from "../lib/api";

describe("Pengiriman Pesanan UI & Data Invariants", () => {
  const src = fs.readFileSync(path.join(__dirname, "PengirimanPesanan.jsx"), "utf8");
  const poDetailSrc = fs.readFileSync(path.join(__dirname, "PODetail.jsx"), "utf8");
  const salesOrderSrc = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
  const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

  test("contains all required header fields for Pengiriman Pesanan", () => {
    expect(src).toMatch(/delivery_date/);
    expect(src).toMatch(/po_number/);
    expect(src).toMatch(/suggested_sj_number/);
    expect(src).toMatch(/customer/);
    expect(src).toMatch(/outlet/);
    expect(src).toMatch(/recipient/);
    expect(src).toMatch(/address/);
    expect(src).toMatch(/delivery_zone/);
    expect(src).toMatch(/sales/);
    expect(src).toMatch(/expedition/);
    expect(src).toMatch(/notes/);
  });

  test("supports multi-line items with exact line identity and remaining quantities", () => {
    expect(src).toMatch(/line_qtys/);
    expect(src).toMatch(/line_notes/);
    expect(src).toMatch(/remaining_qty/);
    expect(src).toMatch(/ordered_qty/);
    expect(src).toMatch(/totalShippingQty/);
    expect(src).toMatch(/overshipmentErrors/);
    expect(src).toMatch(/melebihi sisa PO/);
  });

  test("implements backend-authoritative sales delivery API calls", () => {
    expect(src).toMatch(/api\.listSalesDeliveries/);
    expect(src).toMatch(/api\.getPoDeliverySummary/);
    expect(src).toMatch(/api\.createSalesDelivery/);
    expect(src).toMatch(/api\.getSalesDelivery/);
    expect(src).toMatch(/api\.salesDeliveryPdfUrl/);
  });

  test("includes both list, create form, and detail modal with PDF print", () => {
    expect(src).toMatch(/viewMode === "create"/);
    expect(src).toMatch(/viewMode === "list"/);
    expect(src).toMatch(/delivery-detail-modal/);
    expect(src).toMatch(/Cetak PDF/);
    expect(src).toMatch(/Riwayat Pengiriman untuk PO/);
  });

  test("registers /penjualan/pengiriman-pesanan in App.js router", () => {
    expect(appSrc).toMatch(/path="\/penjualan\/pengiriman-pesanan"/);
    expect(appSrc).toMatch(/<PengirimanPesanan \/>/);
    expect(appSrc).toMatch(/NAV_KEYS\.salesDeliveryOrder/);
  });

  test("SJ list has checkbox, visible-only select all, Hapus Terpilih, and SJ number confirm", () => {
    expect(src).toMatch(/select-all-deliveries/);
    expect(src).toMatch(/delivery-checkbox-/);
    expect(src).toMatch(/btn-hapus-terpilih/);
    expect(src).toMatch(/Hapus Terpilih/);
    expect(src).toMatch(/delete-confirm-modal/);
    expect(src).toMatch(/sj_number/);
    expect(src).toMatch(/selectedIds\.size === deliveries\.length/);
  });

  test("Pengiriman row has Hapus button with SJ business number confirmation and safe-delete wiring", () => {
    expect(src).toMatch(/btn-delete-delivery-/);
    expect(src).toMatch(/number: d\.sj_number/);
    expect(src).toMatch(/numbers: \[d\.sj_number\]/);
    expect(src).toMatch(/api\.deleteSalesDelivery/);
    expect(src).toMatch(/handleBulkDelete\(\) : handleDelete\(deleteConfirm\.id\)/);
    expect(src).toMatch(/delete-error/);
    expect(src).toMatch(/loadDeliveries\(\)/);
  });

  test("renders required helper texts and read-only attributes for PO and SJ numbers", () => {
    expect(src).toMatch(/Data diisi otomatis dari Pesanan Penjualan dan dapat disesuaikan untuk pengiriman ini\./);
    expect(src).toMatch(/Terkunci berdasarkan PO dan urutan pengiriman\./);
    expect(src).toMatch(/data-testid="display-po-number"/);
    expect(src).toMatch(/data-testid="display-suggested-sj"/);
    expect(src).toMatch(/data-testid="display-customer"/);
    expect(src).toMatch(/data-testid="input-delivery-date"/);
    expect(src).toMatch(/data-testid="input-delivery-outlet"/);
    expect(src).toMatch(/data-testid="input-delivery-recipient"/);
    expect(src).toMatch(/data-testid="input-delivery-sales"/);
    expect(src).toMatch(/data-testid="input-delivery-expedition"/);
    expect(src).toMatch(/data-testid="input-delivery-zone"/);
    expect(src).toMatch(/data-testid="input-delivery-address"/);
    expect(src).toMatch(/data-testid="input-delivery-notes"/);
  });

  test("adds Proses ke Pengiriman Pesanan button and summary to PesananPenjualan", () => {
    expect(salesOrderSrc).toMatch(/Status Kirim/);
    expect(salesOrderSrc).toMatch(/Sisa Kirim/);
    expect(salesOrderSrc).toMatch(/\/penjualan\/pengiriman-pesanan\?po_id=/);
  });

  test("implements SEBAGIAN and SELESAI status filters and badges without Dikirim/Terkirim", () => {
    // Filter tabs
    expect(src).toMatch(/id:\s*"SEBAGIAN",\s*label:\s*"Sebagian"/);
    expect(src).toMatch(/id:\s*"SELESAI",\s*label:\s*"Selesai"/);
    expect(src).not.toMatch(/id:\s*"Dikirim"/);
    expect(src).not.toMatch(/id:\s*"Terkirim"/);

    // Detail modal PO Progress Summary
    expect(src).toMatch(/data-testid="delivery-po-progress-summary"/);
    expect(src).toMatch(/TOTAL QTY PO/);
    expect(src).toMatch(/SUDAH DIBUAT SURAT JALAN/);
    expect(src).toMatch(/SISA/);
    expect(src).toMatch(/STATUS/);
    expect(src).toMatch(/detail-total-po-qty/);
    expect(src).toMatch(/detail-cumulative-sj-qty/);
    expect(src).toMatch(/detail-remaining-po-qty/);
    expect(src).toMatch(/detail-delivery-status/);
  });
});

describe("Pengiriman Pesanan Nav Integration", () => {
  const has = (...keys) => (k) => keys.includes(k);

  test("NAV_KEYS includes salesDeliveryOrder", () => {
    expect(NAV_KEYS.salesDeliveryOrder).toBe("nav.sales.delivery_order");
    expect(SALES_NAV_ROUTES[NAV_KEYS.salesDeliveryOrder]).toBe("/penjualan/pengiriman-pesanan");
    expect(SALES_TILE_TONES[NAV_KEYS.salesDeliveryOrder]).toBe("amber");
    expect(SALES_TILE_TESTIDS[NAV_KEYS.salesDeliveryOrder]).toBe("top-nav-sales-delivery-order");
  });

  test("salesNavVisible enables parent for salesDeliveryOrder", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, NAV_KEYS.salesDeliveryOrder));
    expect(v.parent).toBe(true);
    expect(v.salesDeliveryOrder).toBe(true);
  });

  test("salesMenuItems renders Pengiriman Pesanan tile", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesDeliveryOrder));
    expect(items).toContainEqual({
      key: NAV_KEYS.salesDeliveryOrder,
      label: "Pengiriman Pesanan",
      to: "/penjualan/pengiriman-pesanan",
      tone: "amber",
      testid: "top-nav-sales-delivery-order",
    });
  });

  test("api.js contains all sales delivery methods", () => {
    expect(typeof api.listSalesDeliveries).toBe("function");
    expect(typeof api.getSalesDelivery).toBe("function");
    expect(typeof api.getPoDeliverySummary).toBe("function");
    expect(typeof api.createSalesDelivery).toBe("function");
    expect(typeof api.salesDeliveryPdfUrl).toBe("function");
  });
});
