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
  test("sidebar Persediaan only exposes Barang/Jasa and Stock Saat Ini", () => {
    expect(nav).toMatch(/label: "Persediaan"/);
    expect(nav).toMatch(/label: "Barang\/Jasa"/);
    expect(nav).toMatch(/label: "Stock Saat Ini"/);
    expect(nav).not.toMatch(/Nilai Stock|Kartu Stock|Mutasi Stock|Stock Valuation/i);
    expect(app).toMatch(/path="\/persediaan\/master-barang-jasa"/);
    expect(app).toMatch(/path="\/persediaan\/stock-saat-ini"/);
    expect(app).not.toMatch(/persediaan\/nilai|persediaan\/kartu|persediaan\/mutasi/i);
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

  test("Stock Saat Ini clears rows while query pending and gates physical edit to holding team", () => {
    expect(stock).toMatch(/useDebouncedLiveSearch/);
    expect(stock).toMatch(/loadSeq/);
    expect(stock).toMatch(/liveSearchTableView/);
    expect(stock).toMatch(/searchPending/);
    expect(stock).toMatch(/stock-loading/);
    expect(stock).toMatch(/if \(!searchPending\) return/);
    expect(stock).toMatch(/items: \[\]/);
    expect(stock).toMatch(/canEditPhysical.*can_correct_physical_stock/);
    expect(stock).toMatch(/api\.currentStockUpdate/);
    expect(stock).toMatch(/stock-physical-inline-input/);
    expect(stock).toMatch(/e\.key === "Enter"/);
    expect(stock).toMatch(/e\.key === "Escape"/);
    expect(stock).toMatch(/Koreksi manual Stock Fisik dari Stock Saat Ini/);
    expect(stock).not.toMatch(/>Koreksi Stock Fisik</);
    expect(stock).toMatch(/api\.currentStock\(/);
    expect(stock).toMatch(/Cari kode \/ nama \/ Accurate ID \(live\)/);
    expect(stock).not.toMatch(/>Cari</);
    expect(currentStockPy).toMatch(/aid = str\(item\.get\("accurate_item_id"/);
    expect(masterItemsPy).toMatch(/"accurate_item_id": \{"\$regex"/);
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
