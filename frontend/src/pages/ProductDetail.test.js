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
    expect(apiSrc).toMatch(/correctOpeningBalance:/);
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
    // Navigation & Header
    expect(productDetailSrc).toMatch(/Kembali ke Barang\/Jasa/);
    expect(productDetailSrc).toMatch(/product-header-card/);

    // KPI Cards
    expect(productDetailSrc).toMatch(/Stok Total Saat Ini/);
    expect(productDetailSrc).toMatch(/Stok Cup Belum Dicetak/);
    expect(productDetailSrc).toMatch(/Satuan Dasar/);
    expect(productDetailSrc).toMatch(/Konversi Aktif/);

    // Tabs
    expect(productDetailSrc).toMatch(/tab-btn-umum/);
    expect(productDetailSrc).toMatch(/tab-btn-stok/);
    expect(productDetailSrc).toMatch(/tab-btn-mutasi/);
    expect(productDetailSrc).toMatch(/Informasi Produk/);
    expect(productDetailSrc).toMatch(/Setting Konversi Satuan/);
  });

  test("ProductDetail Unit Conversion form enforces business rules", () => {
    // Form fields
    expect(productDetailSrc).toMatch(/input-base-uom/);
    expect(productDetailSrc).toMatch(/input-tx-uom/);
    expect(productDetailSrc).toMatch(/input-conversion-factor/);
    expect(productDetailSrc).toMatch(/btn-save-conversion/);

    // Factor validation: must be > 0
    expect(productDetailSrc).toMatch(/Faktor konversi harus berupa angka lebih besar dari 0/);

    // Same UOM safety: if tx == base, factor is 1
    expect(productDetailSrc).toMatch(/txUom\.trim\(\)\.toUpperCase\(\) === baseUom\.trim\(\)\.toUpperCase\(\)/);
  });

  test("ProductDetail Mutation Table implements dense ledger and click mechanics", () => {
    // Table columns
    expect(productDetailSrc).toMatch(/Tanggal/);
    expect(productDetailSrc).toMatch(/No\. Sumber/);
    expect(productDetailSrc).toMatch(/Tipe Transaksi/);
    expect(productDetailSrc).toMatch(/Keterangan/);
    expect(productDetailSrc).toMatch(/Sumber/);
    expect(productDetailSrc).toMatch(/Tujuan/);
    expect(productDetailSrc).toMatch(/Masuk/);
    expect(productDetailSrc).toMatch(/Keluar/);
    expect(productDetailSrc).toMatch(/Saldo/);

    // Whole row click opens drawer
    expect(productDetailSrc).toMatch(/onClick=\{\(\) => setSelectedMutation\(m\)\}/);

    // Inner link clicks stop propagation
    expect(productDetailSrc).toMatch(/e\.stopPropagation\(\)/);

    // Slide-in Detail Mutasi Drawer
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
  });

  test("ProductDetail enforces Physical Stock Authority & Opening Balance card contracts", () => {
    // Opening balance card exists
    expect(productDetailSrc).toMatch(/opening-balance-card/);
    expect(productDetailSrc).toMatch(/badge-opening-status/);

    // Initialized state display
    expect(productDetailSrc).toMatch(/opening-display-total/);
    expect(productDetailSrc).toMatch(/opening-display-unprinted/);
    expect(productDetailSrc).toMatch(/opening-display-printed/);
    expect(productDetailSrc).toMatch(/Terkunci Permanen \(1 Kali Penetapan\)/);

    // Uninitialized form fields and live preview
    expect(productDetailSrc).toMatch(/input-opening-unprinted/);
    expect(productDetailSrc).toMatch(/input-opening-printed/);
    expect(productDetailSrc).toMatch(/display-opening-total/);
    expect(productDetailSrc).toMatch(/btn-save-opening/);

    // Base UOM locked once stock is initialized
    expect(productDetailSrc).toMatch(/disabled=\{!canEditConversion \|\| savingSetting \|\| isStockInitialized\}/);
    expect(productDetailSrc).toMatch(/Terkunci: Saldo awal stok fisik sudah ditetapkan/);

    // Permission enforcement
    expect(productDetailSrc).toMatch(/canEditOpeningBalance = hasPerm\("current_stock\.update"\) \|\| hasPerm\("permissions\.manage"\)/);
    expect(productDetailSrc).toMatch(/canCorrectOpening = String\(user\?\.role \|\| ""\)\.toLowerCase\(\) === "holding_team"/);
    expect(productDetailSrc).toMatch(/btn-correct-opening/);
    expect(productDetailSrc).toMatch(/correct-opening-modal/);
    expect(productDetailSrc).toMatch(/Koreksi Saldo Awal/);
    expect(productDetailSrc).toMatch(/correctOpeningBalance/);
    expect(productDetailSrc).toMatch(/client_request_id/);

    // One-time warning notice
    expect(productDetailSrc).toMatch(/Saldo awal stok fisik hanya dapat ditetapkan.*satu kali/);
  });
});
