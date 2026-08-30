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

describe("Pesanan Penjualan UI & Data Invariants", () => {
  const src = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
  const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

  test("contains all required header fields matching Import PO model", () => {
    expect(src).toMatch(/po_number/);
    expect(src).toMatch(/order_date/);
    expect(src).toMatch(/customer/);
    expect(src).toMatch(/outlet/);
    expect(src).toMatch(/sales/);
    expect(src).toMatch(/created_by_user/);
    expect(src).toMatch(/recipient/);
    expect(src).toMatch(/address/);
    expect(src).toMatch(/delivery_zone/);
    expect(src).toMatch(/estimated_delivery/);
    expect(src).toMatch(/deadline/);
    expect(src).toMatch(/payment_terms/);
    expect(src).toMatch(/expedition/);
    expect(src).toMatch(/notes/);
  });

  test("supports multi-line items with exact line identity (no auto-merge)", () => {
    expect(src).toMatch(/line_items/);
    expect(src).toMatch(/addLineItem/);
    expect(src).toMatch(/removeLineItem/);
    expect(src).toMatch(/production_process/);
    expect(src).toMatch(/quantity/);
    expect(src).toMatch(/unit/);
    expect(src).toMatch(/color/);
    expect(src).toMatch(/item_code/);
  });

  test("implements authoritative validation and create sales order API call", () => {
    expect(src).toMatch(/api\.createSalesOrder/);
    expect(src).toMatch(/api\.salesOrderOptions/);
    expect(src).toMatch(/api\.listSalesOrders/);
    expect(src).toMatch(/Customer wajib diisi/);
    expect(src).toMatch(/Quantity harus angka lebih besar dari 0/);
  });

  test("includes both list and create views with Lihat PO link", () => {
    expect(src).toMatch(/viewMode === "create"/);
    expect(src).toMatch(/viewMode === "list"/);
    expect(src).toMatch(/to=\{`\/po\/\$\{o\.id\}`\}/);
    expect(src).toMatch(/Lihat PO/);
  });

  test("registers /penjualan/pesanan-penjualan in App.js router", () => {
    expect(appSrc).toMatch(/path="\/penjualan\/pesanan-penjualan"/);
    expect(appSrc).toMatch(/<PesananPenjualan \/>/);
    expect(appSrc).toMatch(/path="\/penjualan"/);
  });
});

describe("Pesanan Penjualan Nav Integration", () => {
  const has = (...keys) => (k) => keys.includes(k);

  test("NAV_KEYS includes salesOrder", () => {
    expect(NAV_KEYS.salesOrder).toBe("nav.sales.sales_order");
    expect(SALES_NAV_ROUTES[NAV_KEYS.salesOrder]).toBe("/penjualan/pesanan-penjualan");
    expect(SALES_TILE_TONES[NAV_KEYS.salesOrder]).toBe("blue");
    expect(SALES_TILE_TESTIDS[NAV_KEYS.salesOrder]).toBe("top-nav-sales-order");
  });

  test("salesNavVisible enables parent for salesOrder", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, NAV_KEYS.salesOrder));
    expect(v.parent).toBe(true);
    expect(v.salesOrder).toBe(true);
  });

  test("salesMenuItems renders Pesanan Penjualan tile", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesOrder));
    expect(items).toEqual([
      {
        key: NAV_KEYS.salesOrder,
        label: "Pesanan Penjualan",
        to: "/penjualan/pesanan-penjualan",
        tone: "blue",
        testid: "top-nav-sales-order",
      },
    ]);
  });
});
