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
const ssiStatus = fs.readFileSync(path.join(__dirname, "../lib/ssiStatusDisplay.js"), "utf8");
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

  test("Stock Saat Ini shows Owner Excel SSI compact list + inline detail", () => {
    const detail = fs.readFileSync(path.join(__dirname, "StockSaatIniDetailDrawer.jsx"), "utf8");
    const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
    expect(stock).toMatch(/Stock Dapat Dijual = Stock [-\u2212] \(PO aktif [-\u2212] qty dikirim\)/);
    expect(stock).toMatch(/Nilai negatif = kebutuhan stok/);
    expect(stock).toMatch(/tidak memblokir operasional/);
    expect(stock).toMatch(/Persediaan barang per cabang dan gudang/);
    expect(stock).toMatch(/stock-legend-bar/);
    expect(stock).toMatch(/Filter lanjutan/);
    expect(stock).toMatch(/Lihat penjelasan lengkap/);
    expect(stock).not.toMatch(/stock-filter-focus/);
    expect(stock).not.toMatch(/Filter \(fokus\)/);
    expect(stock).toMatch(/stock-table-compact/);
    expect(stock).toMatch(/stock-row-clickable/);
    expect(stock).toMatch(/openDetail/);
    expect(stock).toMatch(/Kembali ke Daftar|closeDetail/);
    expect(stock).not.toMatch(/Lihat Detail/);
    expect(stock).not.toMatch(/stock-detail-header/);
    expect(stock).toMatch(/StockSaatIniDetailDrawer/);
    expect(stock).toMatch(/stock-mobile-list/);
    expect(stock).toMatch(/data-testid="stock-filters"/);
    expect(stock).toMatch(/data-testid="stock-search"/);
    expect(stock).toMatch(/bookPhysicalDisplay/);
    expect(stock).toMatch(/stockPhysicalPrimaryDisplay/);
    expect(stock).toMatch(/data-balance-present/);
    expect(stock).toMatch(/api\.physicalStockCatalog\(/);
    expect(stock).toMatch(/Satuan Dasar/);
    expect(stock).not.toMatch(/data-testid="stock-unit-header"/);
    expect(stock).not.toMatch(/stock-expand-m-/);
    expect(stock).toMatch(/stock-status-badge/);
    expect(stock).toMatch(/data-baseline-label/);
    expect(stock).toMatch(/EXCEL_SSI_COLUMNS/);
    expect(stock).toMatch(/label: "Saldo Awal"/);
    expect(stock).toMatch(/label: "Pembelian"/);
    expect(stock).toMatch(/label: "Stock"/);
    expect(stock).toMatch(/label: "Stock Dapat Dijual"/);
    expect(stock).toMatch(/label: "Qty Tercetak"/);
    expect(stock).toMatch(/label: "Qty Polos"/);
    expect(stock).toMatch(/label: "Barang Tercetak Belum Kirim"/);
    expect(stock).toMatch(/label: "Barang Cetak Sudah Dikirim"/);
    expect(stock).toMatch(/stock-physical-header/);
    expect(stock).toMatch(/stock-polos-header|testId: "stock-polos"/);
    expect(stock).toMatch(/stock-siap-jual-header|testId: "stock-siap-jual"/);
    expect(stock).toMatch(/BELUM_TERSEDIA/);
    expect(stock).toMatch(/Belum terverifikasi/);
    expect(stock).toMatch(/Belum saldo/);
    expect(stock).not.toMatch(/Belum tersedia/);
    expect(detail).toMatch(/Belum terverifikasi/);
    expect(detail).toMatch(/ssi-item-detail|inline-document/);
    expect(detail).toMatch(/Kembali ke Daftar/);
    expect(detail).toMatch(/ssi-history-filter-\$\{f\.id\}/);
    expect(detail).toMatch(/id: "FISIK"/);
    expect(detail).toMatch(/id: "SO"/);
    expect(detail).toMatch(/id: "CETAK"/);
    expect(detail).toMatch(/api\.ssiItemActivity/);
    expect(apiSrc).toMatch(/ssiItemActivity/);
    expect(apiSrc).toMatch(/ssi-activity/);
    expect(stock).not.toMatch(/stock-filter-branch/);
    expect(stock).not.toMatch(/stock-filter-warehouse/);
    expect(stock).not.toMatch(/Belum Dicetak/);
    expect(stock).not.toMatch(/Sudah Dicetak/);
    expect(stock).not.toMatch(/<th[^>]*>Stock Saat Ini<\/th>/);
    expect(stock).not.toMatch(/<th[^>]*>Cabang<\/th>/);
    expect(stock).not.toMatch(/Qty Saat Ini \+ Pembelian Barang/);
    expect(stock).not.toMatch(/Qty Tersedia = Stock Fisik/);
    expect(stock).not.toMatch(/Qty Cetak Operator/);
    expect(stock).not.toMatch(/GUDANG_UTAMA/);
    expect(stock).not.toMatch(/YGY \/ MAIN/);
    expect(stock).toMatch(/data-testid="saldo-awal-button"/);
    expect(stock).toMatch(/createPhysicalOpeningBalance/);
    expect(stock).not.toMatch(/initOpeningBalance/);
    expect(stock).toMatch(/Belum ada Barang\/Jasa pada environment preview/);
    expect(stock).toMatch(/data-testid="stock-opname-button"/);
    expect(stock).toMatch(/\/persediaan\/stock-opname/);
  });

  test("authorized physical correction uses a target dialog and refreshes the read", () => {
    expect(stock).toMatch(/can_adjust_physical_stock/);
    expect(stock).toMatch(/canAdjust \?/);
    expect(stock).toMatch(/data-testid=\{`stock-adjust-\$\{it\.master_item_id\}`\}/);
    expect(stock).toMatch(/data-testid="stock-adjust-modal"/);
    expect(stock).toMatch(/data-testid="stock-adjust-current"/);
    expect(stock).toMatch(/data-testid="stock-adjust-target"/);
    expect(stock).toMatch(/data-testid="stock-adjust-warehouse"/);
    expect(stock).toMatch(/data-testid="stock-adjust-reason"/);
    expect(stock).toMatch(/Alasan Koreksi/);
    expect(stock).toMatch(/Simpan Koreksi/);
    expect(stock).toMatch(/stock-adjust-dialog/);
    expect(stock).toMatch(/>\s*Batal\s*</);
    expect(stock).toMatch(/createPhysicalAdjustment/);
    expect(stock).toMatch(/Koreksi manual dari Stock Saat Ini/);
    expect(stock).toMatch(/stock-inline-input/);
    expect(stock).toMatch(/adjustWarehouses\.length > 1/);
    expect(stock).toMatch(/await onSaved\(\)/);
    expect(stock).toMatch(/stock_dapat_dijual/);
    expect(stock).toMatch(/qty_tercetak/);
    expect(stock).toMatch(/qty_polos/);
    expect(stock).toMatch(/tercetak_belum_kirim/);
    expect(stock).toMatch(/cetak_sudah_dikirim/);
    // Reason codes live in shared status contract helper (P0-B1).
    expect(ssiStatus).toMatch(/NO_OPERATOR_EVIDENCE/);
    expect(ssiStatus).toMatch(/INDICATIVE/);
    expect(ssiStatus).toMatch(/BASELINE_INCOMPLETE/);
    expect(stock).toMatch(/resolveDapatDijualDisplay/);
    expect(stock).toMatch(/resolvePolosDisplay/);
    expect(stock).toMatch(/Indikatif/);
    expect(stock).toMatch(/MANUAL_ADJUSTMENT/);
  });

  test("Stock Saat Ini keeps in-scope rows while soft-refreshing search", () => {
    // P0-A: ordinary search/page refetch must not wipe rows; actions lock instead.
    expect(stock).toMatch(/useDebouncedLiveSearch/);
    expect(stock).toMatch(/loadSeq/);
    expect(stock).toMatch(/stockCatalogTableView/);
    expect(stock).toMatch(/searchPending/);
    expect(stock).toMatch(/refreshing/);
    expect(stock).toMatch(/actionsLocked/);
    expect(stock).toMatch(/stock-refreshing/);
    expect(stock).toMatch(/stock-loading/);
    expect(stock).toMatch(/Cari kode, nama, atau Accurate ID/);
    expect(stock).toMatch(/data-testid="stock-search"/);
    expect(stock).not.toMatch(/>Cari</);
    expect(stock).not.toMatch(/api\.currentStock\(/);
    expect(stock).not.toMatch(/currentStockUpdate/);
    expect(stock).not.toMatch(/if \(!searchPending\) return/);
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
    // Approved valuation meaning: estimate from physical stock × canonical HPP,
    // never a posted book balance, never Customer Pricing / published HPP fallback.
    expect(value).toMatch(/Nilai estimasi stok, bukan saldo buku posted/);
    expect(value).toMatch(/Qty cabang × HPP company-wide|current_hpp|inventory_value/);
    expect(value).toMatch(/Belum Ada HPP/);
    expect(value).not.toMatch(/currentStockUpdate|manual_hpp|hpp_snapshot|tier_prices/);
    expect(value).not.toMatch(/PUBLISHED_CUSTOMER_PRICING_FALLBACK/);
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
