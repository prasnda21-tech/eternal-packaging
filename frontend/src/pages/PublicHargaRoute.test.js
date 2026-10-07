import fs from "fs";
import path from "path";

describe("public harga route and responsive shell", () => {
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const page = fs.readFileSync(path.join(__dirname, "PublicHargaPage.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "PublicHargaPage.css"), "utf8");
  const api = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

  test("catalog, opaque detail, and slug routes stay outside Protected and AppShell", () => {
    const publicHarga = app.indexOf('path="/harga"');
    const publicDetail = app.indexOf('path="/harga/:groupPublicId"');
    const publicSlug = app.indexOf('path="/produk/:slug"');
    const protectedShell = app.indexOf("<Route element={<Protected><AppShell /></Protected>}>");
    expect(publicHarga).toBeGreaterThan(-1);
    expect(publicHarga).toBeLessThan(protectedShell);
    expect(publicDetail).toBeGreaterThan(publicHarga);
    expect(publicDetail).toBeLessThan(protectedShell);
    expect(publicSlug).toBeGreaterThan(-1);
    expect(publicSlug).toBeLessThan(protectedShell);
    for (const pathName of ["/settings", "/produksi", "/penjualan", "/pembelian"]) {
      expect(app.lastIndexOf(`path="${pathName}"`)).toBeGreaterThan(protectedShell);
    }
    expect(app).toMatch(/path="\/harga" element=\{<PublicHargaPage \/>\}/);
    expect(app).toMatch(/path="\/harga\/:groupPublicId" element=\{<PublicHargaPage \/>\}/);
    expect(app).toMatch(/path="\/produk\/:slug" element=\{<PublicHargaPage \/>\}/);
  });

  test("home uses catalog bootstrap and detail uses public_id/slug APIs without local price math", () => {
    expect(api).toMatch(/publicCatalogHome:\s*\(\)\s*=>\s*publicJsonRequest\("\/public\/catalog"\)/);
    expect(api).toMatch(/publicCustomerPricingGroupBySlug/);
    expect(page).toMatch(/api\.publicCatalogHome\(\)/);
    expect(page).toMatch(/api\.publicCustomerPricingGroup\(groupPublicId\)/);
    expect(page).toMatch(/api\.publicCustomerPricingGroupBySlug\(slug\)/);
    expect(page).not.toMatch(/quantity\s*\*\s*.*unit_price/);
    expect(page).not.toMatch(/hpp|profit|accurate_item_id|pricing_group_id/i);
  });

  test("390px layout is bounded, stacked, readable, and touch friendly", () => {
    expect(css).toMatch(/overflow-x:\s*hidden/);
    expect(css).toMatch(/@media \(max-width:\s*560px\)/);
    expect(css).toMatch(/\.ph-catalog-grid/);
    expect(css).toMatch(/\.ph-detail-layout/);
    expect(css).toMatch(/min-height:\s*44px/);
    expect(css).toMatch(/\.ph-wa-primary[^{]*\{[^}]*min-height:\s*48px/);
  });

  test("thumbnail preview state stays separate from variant selection", () => {
    expect(page).toMatch(/setPreviewUrl\(image\.url\)/);
    expect(page).toMatch(/selectFlatVariant|setSelectedVariantId/);
    expect(page).toMatch(/harga-gallery-thumbs/);
  });

  test("home copy avoids popularity and unsupported trust claims", () => {
    expect(page).toMatch(/Produk Tersedia/);
    expect(page).not.toMatch(/Produk Populer/i);
    expect(page).not.toMatch(/Kualitas Terjamin/);
    expect(page).toMatch(/Harga Transparan/);
    expect(page).toMatch(/Banyak Pilihan Varian/);
    expect(page).toMatch(/Harga Berdasarkan Jumlah/);
    expect(page).toMatch(/Mudah Hubungi Tim/);
  });
});
