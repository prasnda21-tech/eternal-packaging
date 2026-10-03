import fs from "fs";
import path from "path";
import {
  isDebouncedSearchPending,
  liveSearchTableView,
} from "../hooks/useDebouncedLiveSearch";

const nav = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
const master = fs.readFileSync(path.join(__dirname, "MasterBarangJasa.jsx"), "utf8");
const stock = fs.readFileSync(path.join(__dirname, "StockSaatIni.jsx"), "utf8");
const value = fs.readFileSync(path.join(__dirname, "NilaiPersediaan.jsx"), "utf8");
const hook = fs.readFileSync(path.join(__dirname, "../hooks/useDebouncedLiveSearch.js"), "utf8");
const currentStockPy = fs.readFileSync(
  path.join(__dirname, "../../../backend/services/current_stock.py"),
  "utf8"
);
const masterItemsPy = fs.readFileSync(
  path.join(__dirname, "../../../backend/services/master_items.py"),
  "utf8"
);

describe("Persediaan live search inventory scope", () => {
  test("sidebar Persediaan exposes Barang/Jasa, Cabang & Gudang, Stock Saat Ini, and Nilai Persediaan", () => {
    expect(nav).toMatch(/label: "Persediaan"/);
    expect(nav).toMatch(/label: "Barang\/Jasa"/);
    expect(nav).toMatch(/label: "Cabang & Gudang"/);
    expect(nav).toMatch(/label: "Stock Saat Ini"/);
    expect(nav).toMatch(/label: "Nilai Persediaan"/);
    expect(nav).not.toMatch(/Nilai Stock|Kartu Stock|Mutasi Stock|Stock Valuation/i);
    expect(app).toMatch(/path="\/persediaan\/master-barang-jasa"/);
    expect(app).toMatch(/path="\/persediaan\/cabang-gudang"/);
    expect(app).toMatch(/path="\/persediaan\/stock-saat-ini"/);
    expect(app).toMatch(/path="\/persediaan\/nilai-persediaan"/);
    expect(app).not.toMatch(/persediaan\/kartu|persediaan\/mutasi/i);
  });

  test("Barang/Jasa live search reuses debounce hook with race-safe load", () => {
    expect(master).toMatch(/useDebouncedLiveSearch/);
    expect(master).toMatch(/loadSeq/);
    expect(master).toMatch(/seq !== loadSeq\.current/);
    expect(master).toMatch(/liveSearchTableView/);
    expect(master).toMatch(/searchPending/);
    expect(master).toMatch(/master-items-loading/);
    expect(master).toMatch(/Cari kode \/ nama \/ Accurate ID \(live\)/);
    expect(master).not.toMatch(/>Cari</);
    expect(hook).toMatch(/LIVE_SEARCH_DEBOUNCE_MS = 300/);
  });

  test("Stock Saat Ini shows branch physical stock minus outstanding orders", () => {
    expect(stock).toMatch(/stok fisik cabang − qty pesanan outstanding/);
    expect(stock).toMatch(/Progress cetak tidak mengurangi stok/);
    expect(stock).toMatch(/api\.physicalStockCatalog\(/);
    expect(stock).toMatch(/Nama Barang/);
    expect(stock).toMatch(/Satuan Dasar/);
    expect(stock).toMatch(/Stock Fisik/);
    expect(stock).toMatch(/Qty Pesanan/);
    expect(stock).toMatch(/Belum Dicetak/);
    expect(stock).toMatch(/Sudah Dicetak/);
    expect(stock).toMatch(/stock_current/);
    expect(stock).not.toMatch(/stock-filter-warehouse/);
    expect(stock).not.toMatch(/Qty Saat Ini \+ Pembelian Barang/);
    expect(stock).not.toMatch(/Pembelian Barang/);
    expect(stock).not.toMatch(/Qty PO/);
    expect(stock).not.toMatch(/Qty Cetak Operator/);
    expect(stock).not.toMatch(/GUDANG_UTAMA/);
    expect(stock).not.toMatch(/YGY \/ MAIN/);
    expect(stock).toMatch(/data-testid="saldo-awal-button"/);
    expect(stock).toMatch(/createPhysicalOpeningBalance/);
    expect(stock).not.toMatch(/initOpeningBalance/);
    expect(stock).toMatch(/Belum ada Barang\/Jasa pada environment preview/);
    expect(stock).toMatch(/stock-opname-disabled/);
    expect(stock).toMatch(/Belum tersedia untuk stok baru/);
  });

  test("Stock Saat Ini clears rows while query pending", () => {
    expect(stock).toMatch(/useDebouncedLiveSearch/);
    expect(stock).toMatch(/loadSeq/);
    expect(stock).toMatch(/liveSearchTableView/);
    expect(stock).toMatch(/searchPending/);
    expect(stock).toMatch(/stock-loading/);
    expect(stock).toMatch(/if \(!searchPending\) return/);
    expect(stock).toMatch(/items: \[\]/);
    expect(stock).toMatch(/Cari kode \/ nama \/ Accurate ID \(live\)/);
    expect(stock).not.toMatch(/>Cari</);
    expect(stock).not.toMatch(/api\.currentStock\(/);
    expect(stock).not.toMatch(/currentStockUpdate/);
    expect(currentStockPy).toMatch(/aid = str\(item\.get\("accurate_item_id"/);
    expect(masterItemsPy).toMatch(/"accurate_item_id": \{"\$regex"/);
  });

  test("Nilai Persediaan is a race-safe read-only live table", () => {
    expect(value).toMatch(/useDebouncedLiveSearch/);
    expect(value).toMatch(/loadSeq/);
    expect(value).toMatch(/seq !== loadSeq\.current/);
    expect(value).toMatch(/liveSearchTableView/);
    expect(value).toMatch(/api\.inventoryValuation\(/);
    expect(value).toMatch(/api\.inventoryValuationExport\(/);
    expect(value).toMatch(/Nilai estimasi stok, bukan saldo buku posted/);
    expect(value).toMatch(/Belum Ada HPP/);
    expect(value).not.toMatch(/currentStockUpdate|manual_hpp|hpp_snapshot|tier_prices/);
  });

  test("query A then B: stale rows leave results view until B settles", () => {
    expect(isDebouncedSearchPending("PC-12", "PC-12")).toBe(false);
    expect(isDebouncedSearchPending("101", "PC-12")).toBe(true);
    expect(liveSearchTableView({ pending: true, loading: false, itemCount: 3 })).toBe("loading");
    expect(liveSearchTableView({ pending: false, loading: true, itemCount: 0 })).toBe("loading");
    expect(liveSearchTableView({ pending: false, loading: false, itemCount: 1 })).toBe("results");
    expect(liveSearchTableView({ pending: false, loading: false, itemCount: 0 })).toBe("empty");
  });

  test("Persediaan pages do not invent SKU field separate from item_code", () => {
    expect(masterItemsPy).not.toMatch(/"sku"/);
    expect(currentStockPy).not.toMatch(/item\.get\("sku"\)/);
  });
});
