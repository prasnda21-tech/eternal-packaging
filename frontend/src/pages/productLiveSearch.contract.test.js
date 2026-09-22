import fs from "fs";
import path from "path";

const so = fs.readFileSync(path.join(__dirname, "PesananPenjualan.jsx"), "utf8");
const pr = fs.readFileSync(path.join(__dirname, "PermintaanPembelian.jsx"), "utf8");
const cp = fs.readFileSync(path.join(__dirname, "CustomerPricingSettings.jsx"), "utf8");
const wage = fs.readFileSync(path.join(__dirname, "OperatorWageSettings.jsx"), "utf8");
const gaji = fs.readFileSync(path.join(__dirname, "GajiOperator.jsx"), "utf8");
const analytics = fs.readFileSync(path.join(__dirname, "../components/OperatorAnalyticsPanel.jsx"), "utf8");
const mis = fs.readFileSync(path.join(__dirname, "../components/MasterItemSearchSelect.jsx"), "utf8");
const master = fs.readFileSync(path.join(__dirname, "MasterBarangJasa.jsx"), "utf8");
const stock = fs.readFileSync(path.join(__dirname, "StockSaatIni.jsx"), "utf8");
const inventoryValue = fs.readFileSync(path.join(__dirname, "NilaiPersediaan.jsx"), "utf8");
const receipt = fs.readFileSync(path.join(__dirname, "PenerimaanBarang.jsx"), "utf8");
const hook = fs.readFileSync(path.join(__dirname, "../hooks/useDebouncedLiveSearch.js"), "utf8");

describe("product live search wiring", () => {
  test("PesananPenjualan uses MasterItemSearchSelect with canonical ids", () => {
    expect(so).toMatch(/MasterItemSearchSelect/);
    expect(so).toMatch(/accurate_item_id/);
    expect(so).toMatch(/handleSelectMasterItem/);
    expect(so).not.toMatch(/datalist id=\{`master-item-list-/);
  });

  test("PermintaanPembelian replaces native select with live search", () => {
    expect(pr).toMatch(/MasterItemSearchSelect/);
    expect(pr).toMatch(/accurate_item_id/);
    expect(pr).not.toMatch(/purchaseRequestOptions/);
  });

  test("Customer Pricing master picker is live-debounced and clears stale on type", () => {
    expect(cp).toMatch(/masterSearchSeq/);
    expect(cp).toMatch(/cp-master-loading/);
    expect(cp).toMatch(/cp-master-empty/);
    expect(cp).toMatch(/setMasterItems\(\[\]\)/);
    expect(cp).toMatch(/status:\s*"active"/);
    expect(cp).not.toMatch(/searchMasters/);
  });

  test("Customer Pricing group list search is live without Cari button", () => {
    expect(cp).toMatch(/queryInput/);
    expect(cp).toMatch(/LIVE_SEARCH_DEBOUNCE_MS/);
    expect(cp).toMatch(/cp-group-search/);
    expect(cp).not.toMatch(/>Cari</);
  });

  test("Operator Wage product search is live-debounced without forcing active-only", () => {
    expect(wage).toMatch(/WAGE_ITEM_SEARCH_DEBOUNCE_MS/);
    expect(wage).not.toMatch(/wage-item-search-btn/);
    expect(wage).toMatch(/setItems\(\[\]\)/);
    expect(wage).not.toMatch(/status:\s*"active"/);
  });

  test("GajiOperator and Analitik Output use searchable product facet filters", () => {
    expect(gaji).toMatch(/FacetSearchSelect/);
    expect(gaji).toMatch(/gaji-filter-item/);
    expect(analytics).toMatch(/FacetSearchSelect/);
    expect(analytics).toMatch(/op-an-item/);
    expect(analytics).toMatch(/op-an-period/);
    expect(analytics).toMatch(/op-an-date-from/);
    expect(analytics).toMatch(/op-an-date-to/);
    expect(analytics).toMatch(/op-an-user/);
    expect(analytics).toMatch(/op-an-customer/);
    expect(analytics).toMatch(/op-an-search/);
    expect(analytics).toMatch(/op-an-po/);
    expect(analytics).not.toMatch(/<select value=\{item\}/);
  });

  test("Persediaan and Penerimaan Barang lists use debounced live search", () => {
    expect(hook).toMatch(/LIVE_SEARCH_DEBOUNCE_MS = 300/);
    for (const src of [master, stock, inventoryValue, receipt]) {
      expect(src).toMatch(/useDebouncedLiveSearch/);
      expect(src).not.toMatch(/const \[qInput, setQInput\] = useState/);
      expect(src).not.toMatch(/const \[q, setQ\] = useState/);
    }
    expect(master).toMatch(/master-items-search/);
    expect(stock).toMatch(/stock-search/);
    expect(inventoryValue).toMatch(/inventory-value-search/);
    expect(receipt).toMatch(/penerimaan-search/);
  });

  test("reusable selector keeps display text non-authoritative and gates empty state", () => {
    expect(mis).toMatch(/Display text is never authoritative/);
    expect(mis).toMatch(/MASTER_ITEM_SEARCH_DEBOUNCE_MS = 300/);
    expect(mis).toMatch(/shouldApplySearchResult/);
    expect(mis).toMatch(/listboxViewState/);
    expect(mis).toMatch(/Produk tidak ditemukan/);
  });
});
