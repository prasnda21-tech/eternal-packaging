import {
  REPORT_CATALOG,
  categoryCounts,
  filterReportCatalog,
  readReportFavorites,
  reportFavoritesStorageKey,
  toggleReportFavorite,
  userHasReportCatalogAccess,
  visibleReportCatalog,
  writeReportFavorites,
} from "./reportCatalog";

const has = (...keys) => (k) => keys.includes(k);

describe("reportCatalog registry", () => {
  test("does not invent unimplemented placeholder reports", () => {
    expect(REPORT_CATALOG.every((row) => row.id && row.to && typeof row.canAccess === "function")).toBe(true);
    expect(REPORT_CATALOG.some((row) => /retur-penjualan|placeholder|coming-soon/i.test(row.id))).toBe(false);
  });

  test("filters by existing permissions only", () => {
    const sales = visibleReportCatalog(has("reports.sales.view"));
    expect(sales.map((r) => r.id)).toEqual([
      "penjualan-per-barang",
      "rincian-penjualan-per-barang",
      "daftar-faktur-penjualan",
      "penjualan-per-pelanggan",
      "penjualan-per-sales",
    ]);
    expect(userHasReportCatalogAccess(has("reports.sales.view"))).toBe(true);
    expect(userHasReportCatalogAccess(has())).toBe(false);
    expect(visibleReportCatalog(has("profit_loss.view")).map((r) => r.id)).toEqual(["laba-rugi"]);
    expect(visibleReportCatalog(has("reports.purchase.view")).map((r) => r.id)).toEqual([
      "daftar-faktur-pembelian",
      "pembelian-per-barang",
      "pembelian-per-supplier",
    ]);
    expect(visibleReportCatalog(has("purchase_invoices.view")).map((r) => r.id)).not.toContain(
      "daftar-faktur-pembelian",
    );
  });

  test("search and category filter stay local to metadata", () => {
    const rows = visibleReportCatalog(has(
      "reports.sales.view",
      "profit_loss.view",
      "nav.cash_bank.mutasi",
    ));
    expect(filterReportCatalog(rows, { category: "penjualan" })).toHaveLength(5);
    expect(filterReportCatalog(rows, { query: "neraca" })).toEqual([]);
    expect(filterReportCatalog(rows, { query: "laba" }).map((r) => r.id)).toEqual(["laba-rugi"]);
    expect(categoryCounts(rows).all).toBe(rows.length);
  });

  test("favorites are versioned and user-scoped ids only", () => {
    const a = "user-a";
    const b = "user-b";
    window.localStorage.clear();
    expect(reportFavoritesStorageKey(a)).toBe("eternal-report-favorites-v1:user-a");
    writeReportFavorites(a, ["penjualan-per-barang", { evil: true }, ""]);
    expect(readReportFavorites(a)).toEqual(["penjualan-per-barang"]);
    expect(readReportFavorites(b)).toEqual([]);
    toggleReportFavorite(a, "laba-rugi");
    expect(readReportFavorites(a)).toEqual(["penjualan-per-barang", "laba-rugi"]);
    toggleReportFavorite(a, "penjualan-per-barang");
    expect(readReportFavorites(a)).toEqual(["laba-rugi"]);
    const filtered = filterReportCatalog(
      visibleReportCatalog(has("reports.sales.view", "profit_loss.view")),
      { category: "favorites", favoriteIds: readReportFavorites(a) },
    );
    expect(filtered.map((r) => r.id)).toEqual(["laba-rugi"]);
  });

  test("restricted roles never see unauthorized catalog cards", () => {
    expect(visibleReportCatalog(has("reports.sales.view")).every((r) => r.category === "penjualan")).toBe(true);
    expect(visibleReportCatalog(has("profit_loss.view")).map((r) => r.id)).toEqual(["laba-rugi"]);
    expect(visibleReportCatalog(has("po.view", "sales.pwa.view"))).toEqual([]);
    const deniedFavorite = filterReportCatalog(
      visibleReportCatalog(has("profit_loss.view")),
      { category: "favorites", favoriteIds: ["penjualan-per-barang", "laba-rugi"] },
    );
    expect(deniedFavorite.map((r) => r.id)).toEqual(["laba-rugi"]);
  });

  test("deep-link targets stay on existing routes", () => {
    const byId = Object.fromEntries(REPORT_CATALOG.map((r) => [r.id, r.to]));
    expect(byId["penjualan-per-barang"]).toBe("/laporan/penjualan?report=penjualan-per-barang");
    expect(byId["laba-rugi"]).toBe("/buku-besar/laba-rugi");
    expect(byId["nilai-persediaan"]).toBe("/persediaan/nilai-persediaan");
    expect(byId["mutasi-kas-bank"]).toBe("/kas-bank/mutasi");
  });

  test("continuous search typing filters without inventing rows", () => {
    const rows = visibleReportCatalog(has("reports.sales.view", "profit_loss.view", "balance_sheet.view"));
    let q = "";
    let prev = rows.length;
    for (const ch of "ABCDE123") {
      q += ch;
      const filtered = filterReportCatalog(rows, { query: q });
      expect(filtered.length).toBeLessThanOrEqual(prev);
      expect(filtered.every((r) => rows.some((x) => x.id === r.id))).toBe(true);
      prev = filtered.length;
    }
    expect(filterReportCatalog(rows, { query: "ABCDE123" })).toEqual([]);
    expect(filterReportCatalog(rows, { query: "penjualan" }).length).toBe(5);
    expect(filterReportCatalog(rows, { query: "neraca" }).map((r) => r.id)).toEqual(["neraca"]);
  });

  test("wave1 cards deep-link to report workspaces", () => {
    const byId = Object.fromEntries(REPORT_CATALOG.map((r) => [r.id, r.to]));
    expect(byId["penjualan-per-pelanggan"]).toBe("/laporan/penjualan?report=penjualan-per-pelanggan");
    expect(byId["daftar-faktur-pembelian"]).toBe("/laporan/pembelian?report=daftar-faktur-pembelian");
  });

  test("penjualan-per-sales deep-links to penjualan workspace", () => {
    const byId = Object.fromEntries(REPORT_CATALOG.map((r) => [r.id, r.to]));
    expect(byId["penjualan-per-sales"]).toBe("/laporan/penjualan?report=penjualan-per-sales");
  });

  test("wave2 purchase cards deep-link to pembelian workspace", () => {
    const byId = Object.fromEntries(REPORT_CATALOG.map((r) => [r.id, r.to]));
    expect(byId["pembelian-per-barang"]).toBe("/laporan/pembelian?report=pembelian-per-barang");
    expect(byId["pembelian-per-supplier"]).toBe("/laporan/pembelian?report=pembelian-per-supplier");
  });

  test("kartu-stok deep-links and requires stock+item view", () => {
    const byId = Object.fromEntries(REPORT_CATALOG.map((r) => [r.id, r.to]));
    expect(byId["kartu-stok"]).toBe("/laporan/persediaan?report=kartu-stok");
    expect(visibleReportCatalog(has("current_stock.view")).map((r) => r.id)).not.toContain("kartu-stok");
    expect(visibleReportCatalog(has("current_stock.view", "master_items.view")).map((r) => r.id)).toContain("kartu-stok");
  });
});
