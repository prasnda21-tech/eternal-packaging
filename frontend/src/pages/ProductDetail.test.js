import fs from "fs";
import path from "path";

describe("Product Detail & Stock Ledger Frontend Contracts", () => {
  const productDetailSrc = fs.readFileSync(path.join(__dirname, "ProductDetail.jsx"), "utf8");
  const productDetailCss = fs.readFileSync(path.join(__dirname, "productDetail.css"), "utf8");
  const masterBarangSrc = fs.readFileSync(path.join(__dirname, "MasterBarangJasa.jsx"), "utf8");
  const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

  test("App.js registers route for Product Detail under Master Barang Jasa permission", () => {
    expect(appSrc).toMatch(/\/persediaan\/master-barang-jasa\/:id/);
    expect(appSrc).toMatch(/ProductDetail/);
    expect(appSrc).toMatch(/NAV_KEYS\.masterItems/);
  });

  test("API client exports all required methods for Product Detail & Stock Ledger", () => {
    expect(apiSrc).toMatch(/masterItemDetail:/);
    expect(apiSrc).toMatch(/saveUnitConversion:/);
    expect(apiSrc).toMatch(/initOpeningBalance:/);
    expect(apiSrc).toMatch(/listProductMutations:/);
    expect(apiSrc).toMatch(/getMutationDetail:/);
    expect(apiSrc).toMatch(/\/master-items\/\$\{encodeURIComponent\(id\)\}\/detail/);
    expect(apiSrc).toMatch(/\/master-items\/\$\{encodeURIComponent\(id\)\}\/unit-conversion/);
    expect(apiSrc).toMatch(/\/master-items\/\$\{encodeURIComponent\(id\)\}\/opening-balance/);
    expect(apiSrc).toMatch(/\/master-items\/\$\{encodeURIComponent\(id\)\}\/mutations/);
  });

  test("MasterBarangJasa rows are clickable and navigate to exact product detail", () => {
    expect(masterBarangSrc).toMatch(/\/persediaan\/master-barang-jasa\/\$\{it\.id/);
    expect(masterBarangSrc).toMatch(/cursor:\s*"pointer"/);
    expect(masterBarangSrc).toMatch(/navigate\(`/);
    expect(masterBarangSrc).toMatch(/tabIndex=\{0\}/);
    expect(masterBarangSrc).toMatch(/role="button"/);
  });

  test("ProductDetail has dense business UI header and KPI summary cards", () => {
    expect(productDetailSrc).toMatch(/Kembali ke Barang\/Jasa/);
    expect(productDetailSrc).toMatch(/product-header-card/);
    expect(productDetailSrc).toMatch(/Stok Fisik Saat Ini/);
    expect(productDetailSrc).toMatch(/api\.physicalStock\(/);
    expect(productDetailSrc).not.toMatch(/Stok Cup Belum Dicetak/);
    expect(productDetailSrc).toMatch(/Satuan Dasar/);
    expect(productDetailSrc).toMatch(/Konversi Terdefinisi/);
    expect(productDetailSrc).toMatch(/tab-btn-umum/);
    expect(productDetailSrc).toMatch(/tab-btn-stok/);
    expect(productDetailSrc).toMatch(/tab-btn-mutasi/);
    expect(productDetailSrc).toMatch(/Informasi Produk/);
    expect(productDetailSrc).toMatch(/Satuan &amp; Konversi/);
  });

  test("ProductDetail Unit Conversion form enforces multi-UOM business rules", () => {
    expect(productDetailSrc).toMatch(/input-base-uom/);
    expect(productDetailSrc).toMatch(/input-tx-uom/);
    expect(productDetailSrc).toMatch(/input-conversion-factor/);
    expect(productDetailSrc).toMatch(/btn-save-conversion/);
    expect(productDetailSrc).toMatch(/alternate-uoms-table/);
    expect(productDetailSrc).toMatch(/btn-add-alternate-uom/);
    expect(productDetailSrc).toMatch(/alternate_uoms/);
    expect(productDetailSrc).toMatch(/Faktor konversi harus berupa angka lebih besar dari 0/);
    expect(productDetailSrc).toMatch(/Satuan duplikat/);
    expect(productDetailSrc).toMatch(/txUom\.trim\(\)\.toUpperCase\(\) === baseUom\.trim\(\)\.toUpperCase\(\)/);
    expect(productDetailSrc).toMatch(/input-preview-qty/);
    expect(productDetailSrc).toMatch(/conversion-preview-result/);
  });

  test("ProductDetail Mutation Table implements dense ledger and click mechanics", () => {
    expect(productDetailSrc).toMatch(/api\.physicalMutations\(/);
    expect(productDetailSrc).toMatch(/mutationQtyColumns\(m\.qty_delta\)/);
    expect(productDetailSrc).toMatch(/fmtQty\(m\.balance_after\)/);
    expect(productDetailSrc).toMatch(/Tanggal/);
    expect(productDetailSrc).toMatch(/Referensi/);
    expect(productDetailSrc).toMatch(/Tipe Mutasi/);
    expect(productDetailSrc).toMatch(/Cabang/);
    expect(productDetailSrc).toMatch(/Gudang/);
    expect(productDetailSrc).toMatch(/Keterangan/);
    expect(productDetailSrc).toMatch(/Masuk/);
    expect(productDetailSrc).toMatch(/Keluar/);
    expect(productDetailSrc).toMatch(/Saldo/);
    expect(productDetailSrc).not.toMatch(/GUDANG_UTAMA/);
    expect(productDetailSrc).not.toMatch(/initOpeningBalance/);
    expect(productDetailSrc).toMatch(/onClick=\{\(\) => setSelectedMutation\(m\)\}/);
    expect(productDetailSrc).toMatch(/e\.stopPropagation\(\)/);
    expect(productDetailSrc).toMatch(/mutation-drawer/);
    expect(productDetailSrc).toMatch(/Detail Mutasi Stok/);
  });

  test("CSS contains dense styling and slide-in drawer animations", () => {
    expect(productDetailCss).toMatch(/\.product-detail-page/);
    expect(productDetailCss).toMatch(/\.product-header-card/);
    expect(productDetailCss).toMatch(/\.stock-kpi-card/);
    expect(productDetailCss).toMatch(/\.product-tab-btn/);
    expect(productDetailCss).toMatch(/\.mutation-table/);
    expect(productDetailCss).toMatch(/\.mutation-drawer/);
    expect(productDetailCss).toMatch(/\.mutation-drawer-overlay/);
    expect(productDetailCss).toMatch(/\.uom-alternate-table/);
  });

  test("ProductDetail enforces Physical Stock Authority & Opening Balance card contracts", () => {
    expect(productDetailSrc).toMatch(/opening-balance-card/);
    expect(productDetailSrc).toMatch(/SALDO AWAL SUDAH DITETAPKAN/);
    expect(productDetailSrc).toMatch(/createPhysicalOpeningBalance/);
    expect(productDetailSrc).toMatch(/physicalOpeningBalance/);
    expect(productDetailSrc).toMatch(/btn-save-opening/);
    expect(productDetailSrc).toMatch(/opening-branch/);
    expect(productDetailSrc).toMatch(/opening-warehouse/);
    expect(productDetailSrc).not.toMatch(/initOpeningBalance/);
    expect(productDetailSrc).not.toMatch(/input-opening-unprinted/);
    expect(productDetailSrc).not.toMatch(/input-opening-printed/);
    expect(productDetailSrc).toMatch(/disabled=\{!canEditConversion \|\| savingSetting \|\| isStockInitialized \|\| isInventoryItem\}/);
    expect(productDetailSrc).toMatch(/Terkunci: Saldo awal stok fisik sudah ditetapkan/);
    expect(productDetailSrc).toMatch(/canEditOpeningBalance = hasPerm\("current_stock\.update"\) \|\| hasPerm\("permissions\.manage"\)/);
    expect(productDetailSrc).not.toMatch(/>Koreksi Stock Fisik</);
    expect(productDetailSrc).not.toMatch(/currentStockUpdate/);
    expect(productDetailSrc).toMatch(/Penetapan hanya satu kali/);
  });
});
