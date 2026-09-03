import fs from "fs";
import path from "path";

describe("public harga route and responsive shell", () => {
  const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
  const page = fs.readFileSync(path.join(__dirname, "PublicHargaPage.jsx"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "PublicHargaPage.css"), "utf8");

  test("catalog and opaque detail routes stay outside Protected and AppShell", () => {
    const publicHarga = app.indexOf('path="/harga"');
    const publicDetail = app.indexOf('path="/harga/:groupPublicId"');
    const protectedShell = app.indexOf("<Route element={<Protected><AppShell /></Protected>}>");
    expect(publicHarga).toBeGreaterThan(-1);
    expect(publicHarga).toBeLessThan(protectedShell);
    expect(publicDetail).toBeGreaterThan(publicHarga);
    expect(publicDetail).toBeLessThan(protectedShell);
    for (const pathName of ["/settings", "/produksi", "/penjualan", "/pembelian"]) {
      expect(app.lastIndexOf(`path="${pathName}"`)).toBeGreaterThan(protectedShell);
    }
    expect(app).toMatch(/path="\/harga" element=\{<PublicHargaPage \/>\}/);
    expect(app).toMatch(/path="\/harga\/:groupPublicId" element=\{<PublicHargaPage \/>\}/);
  });

  test("calculator sends identifiers and quantity only and displays backend values", () => {
    expect(page).toMatch(/api\.calculatePublicCustomerPrice\(\{/);
    expect(page).toMatch(/group_public_id: groupPublicId/);
    expect(page).toMatch(/public_variant_id: selectedVariantId/);
    expect(page).toMatch(/quantity: Number\(quantity\)/);
    expect(page).toMatch(/formatMoney\(result\.unit_price\)/);
    expect(page).toMatch(/formatMoney\(result\.total\)/);
    expect(page).not.toMatch(/quantity\s*\*\s*.*unit_price/);
  });

  test("390px layout is bounded, stacked, readable, and touch friendly", () => {
    expect(css).toMatch(/overflow-x:hidden/);
    expect(css).toMatch(/@media \(max-width:560px\)/);
    expect(css).toMatch(/\.ph-catalog-grid\{grid-template-columns:1fr/);
    expect(css).toMatch(/\.ph-detail-hero\{grid-template-columns:1fr/);
    expect(css).toMatch(/\.ph-calculate-button\{min-height:54px/);
    expect(css).toMatch(/\.ph-result-grid\{grid-template-columns:1fr/);
    expect(css).toMatch(/\.ph-tier-table-wrap\{[^}]*overflow-x:auto/);
  });
});
