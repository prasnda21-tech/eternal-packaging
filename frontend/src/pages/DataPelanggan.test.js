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

describe("Data Pelanggan UI & Architecture Invariants", () => {
  const src = fs.readFileSync(path.join(__dirname, "DataPelanggan.jsx"), "utf8");
  const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
  const ppSrc = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");

  test("Data Pelanggan contains required master fields", () => {
    expect(src).toMatch(/input-customer-name/);
    expect(src).toMatch(/input-customer-phone/);
    expect(src).toMatch(/input-customer-address/);
    expect(src).toMatch(/Nama Pelanggan/);
    expect(src).toMatch(/No\. HP \/ WhatsApp/);
    expect(src).toMatch(/Alamat/);
  });

  test("Data Pelanggan contains optional fields (PIC, Email, Kota, Catatan)", () => {
    expect(src).toMatch(/input-customer-pic/);
    expect(src).toMatch(/input-customer-email/);
    expect(src).toMatch(/input-customer-city/);
    expect(src).toMatch(/input-customer-notes/);
  });

  test("Form uses FormSection consistent with Pesanan Penjualan and has NO item lines", () => {
    expect(src).toMatch(/FormSection/);
    // Strictly NO line items, prices, or quantities in customer master
    expect(src).not.toMatch(/line_items/);
    expect(src).not.toMatch(/unit_price/);
    expect(src).not.toMatch(/production_process/);
    expect(src).not.toMatch(/harga/i);
  });

  test("implements API integration methods in api.js", () => {
    expect(apiSrc).toMatch(/listSalesCustomers/);
    expect(apiSrc).toMatch(/getSalesCustomer/);
    expect(apiSrc).toMatch(/createSalesCustomer/);
    expect(apiSrc).toMatch(/updateSalesCustomer/);
  });

  test("implements authoritative CRUD operations in DataPelanggan.jsx", () => {
    expect(src).toMatch(/api\.listSalesCustomers/);
    expect(src).toMatch(/params\.view\s*=\s*"unified"/);
    expect(src).toMatch(/is_master/);
    expect(src).toMatch(/Ditemukan dari/);
    expect(src).toMatch(/isPersistedMaster/);
    expect(src).toMatch(/api\.createSalesCustomer/);
    expect(src).toMatch(/api\.updateSalesCustomer/);
    expect(src).toMatch(/btn-add-customer/);
    expect(src).toMatch(/btn-save-customer/);
    expect(src).toMatch(/btn-cancel-customer/);
  });

  test("registers /penjualan/data-pelanggan in App.js router with PermGate", () => {
    expect(appSrc).toMatch(/path="\/penjualan\/data-pelanggan"/);
    expect(appSrc).toMatch(/<DataPelanggan \/>/);
    expect(appSrc).toMatch(/sales\.customer\.view/);
  });

  test("PesananPenjualan integrates with master customers and auto-creates on PO save", () => {
    expect(ppSrc).toMatch(/master_customers/);
    expect(ppSrc).toMatch(/customer_id/);
    expect(ppSrc).toMatch(/customer_phone/);
    expect(ppSrc).toMatch(/badge-customer-registered/);
    expect(ppSrc).toMatch(/badge-customer-new/);
    expect(ppSrc).toMatch(/input-customer-phone/);
  });
});

describe("Data Pelanggan Navigation & Catalog Invariants", () => {
  const has = (...keys) => (k) => keys.includes(k);

  test("NAV_KEYS includes salesCustomers positioned before salesOrder", () => {
    expect(NAV_KEYS.salesCustomers).toBe("nav.sales.customers");
    expect(SALES_NAV_ROUTES[NAV_KEYS.salesCustomers]).toBe("/penjualan/data-pelanggan");
    expect(SALES_TILE_TESTIDS[NAV_KEYS.salesCustomers]).toBe("top-nav-sales-customers");

    const salesChildren = NAV_MENU_FALLBACK.filter((m) => m.parent === NAV_KEYS.sales).map((m) => m.key);
    const custIdx = salesChildren.indexOf(NAV_KEYS.salesCustomers);
    const orderIdx = salesChildren.indexOf(NAV_KEYS.salesOrder);
    expect(custIdx).toBeGreaterThanOrEqual(0);
    expect(orderIdx).toBeGreaterThanOrEqual(0);
    expect(custIdx).toBeLessThan(orderIdx);
  });

  test("salesNavVisible enables parent for salesCustomers", () => {
    const v = salesNavVisible(has(NAV_KEYS.sales, NAV_KEYS.salesCustomers));
    expect(v.parent).toBe(true);
    expect(v.salesCustomers).toBe(true);
  });

  test("salesMenuItems renders Data Pelanggan tile before Pesanan Penjualan", () => {
    const items = salesMenuItems(has(NAV_KEYS.sales, NAV_KEYS.salesCustomers, NAV_KEYS.salesOrder));
    expect(items.map((i) => i.key)).toEqual([NAV_KEYS.salesCustomers, NAV_KEYS.salesOrder]);
    expect(items[0]).toEqual({
      key: NAV_KEYS.salesCustomers,
      label: "Data Pelanggan",
      to: "/penjualan/data-pelanggan",
      tone: "teal",
      testid: "top-nav-sales-customers",
    });
  });
});
