import fs from "fs";
import path from "path";
import { previewProductUrl, slugifyCategoryName } from "./PublicCatalogSettings";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const pageSrc = fs.readFileSync(path.join(__dirname, "PublicCatalogSettings.jsx"), "utf8");
const cssSrc = fs.readFileSync(path.join(__dirname, "PublicCatalogSettings.css"), "utf8");
const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");
const navSrc = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
const shellSrc = fs.readFileSync(path.join(__dirname, "../components/AppShell.jsx"), "utf8");
const hargaSrc = fs.readFileSync(path.join(__dirname, "PublicHargaPage.jsx"), "utf8");

describe("Public Catalog Settings Phase A", () => {
  test("Katalog Publik tab appears with permission wiring", () => {
    expect(settingsSrc).toMatch(/id:\s*"public-catalog"/);
    expect(settingsSrc).toMatch(/label:\s*"Katalog Publik"/);
    expect(settingsSrc).toMatch(/settings-tab-public-catalog/);
    expect(settingsSrc).toMatch(/canViewPublicCatalog/);
    expect(settingsSrc).toMatch(/canEditPublicCatalog/);
    expect(settingsSrc).toMatch(/"public-catalog":\s*canViewPublicCatalog/);
    expect(settingsSrc).toMatch(/tab === "public-catalog" && canViewPublicCatalog/);
    expect(settingsSrc).toMatch(/PublicCatalogSettings/);
    expect(navSrc).toMatch(/"public-catalog"/);
    expect(navSrc).toMatch(/Katalog Publik/);
    expect(navSrc).toMatch(/settings\.public_catalog\.view/);
    expect(navSrc).toMatch(/settings\.public_catalog\.manage/);
    expect(navSrc).not.toMatch(/settings\.public_catalog\.publish/);
    expect(appSrc).toMatch(/settings\.public_catalog\.view/);
    expect(appSrc).toMatch(/settings\.public_catalog\.manage/);
    expect(shellSrc).toMatch(/"public-catalog":\s*LayoutGrid/);
  });

  test("page renders Phase A sections with own title", () => {
    expect(pageSrc).toMatch(/data-testid="public-catalog-settings"/);
    expect(pageSrc).toMatch(/Katalog Publik/);
    expect(pageSrc).toMatch(/Atur tampilan, struktur produk, gambar, dan URL katalog customer/);
    expect(pageSrc).toMatch(/pcs-meta-strip/);
    expect(pageSrc).toMatch(/katalog\.eternalpackaging\.id/);
    expect(pageSrc).toMatch(/Draft Konfigurasi/);
    expect(pageSrc).toMatch(/pcs-nav-umum/);
    expect(pageSrc).toMatch(/pcs-nav-kategori/);
    expect(pageSrc).toMatch(/Informasi Umum/);
    expect(pageSrc).toMatch(/Kategori Produk/);
    expect(pageSrc).toMatch(/Kelompok Produk/);
    expect(pageSrc).toMatch(/URL & SEO/);
    expect(pageSrc).not.toMatch(/Tahap berikutnya/);
    expect(cssSrc).toMatch(/\.pcs-page/);
    expect(cssSrc).toMatch(/background:\s*#fff\s*!important/);
    expect(cssSrc).toMatch(/\.pcs-section-nav/);
    expect(cssSrc).toMatch(/\.pcs-drawer-footer/);
  });

  test("shared Settings hero hidden only for public-catalog among new wiring", () => {
    expect(settingsSrc).toMatch(/publicCatalogPage/);
    expect(settingsSrc).toMatch(/hideSharedSettingsHeading/);
    expect(settingsSrc).toMatch(/designCetakanPage \|\| customerPricingPage \|\| publicCatalogPage/);
    expect(settingsSrc).toMatch(/!hideSharedSettingsHeading \?/);
    expect(settingsSrc).toMatch(/tab === "hpp"/);
    expect(settingsSrc).toMatch(/page-heading/);
  });

  test("global settings load/save stay independent", () => {
    expect(apiSrc).toMatch(/publicCatalogSettings:\s*\(\)\s*=>\s*client\.get\(`\/settings\/public-catalog`/);
    expect(apiSrc).toMatch(/updatePublicCatalogSettings/);
    expect(pageSrc).toMatch(/publicCatalogSettings/);
    expect(pageSrc).toMatch(/updatePublicCatalogSettings/);
    expect(pageSrc).toMatch(/data-testid="pcs-save-general"/);
    expect(pageSrc).toMatch(/Simpan Informasi/);
    expect(pageSrc).toMatch(/pcs-catalog-name/);
    expect(pageSrc).toMatch(/pcs-public-whatsapp/);
    expect(pageSrc).toMatch(/pcs-hero-title/);
    expect(pageSrc).toMatch(/pcs-hero-subtitle/);
    expect(pageSrc).toMatch(/pcs-catalog-enabled/);
    expect(pageSrc).not.toMatch(/admin_phone/);
  });

  test("category empty state, editor, CRUD, order, image, soft disable", () => {
    expect(pageSrc).toMatch(/Belum ada kategori katalog/);
    expect(pageSrc).toMatch(/Tambahkan kategori untuk mengelompokkan produk/);
    expect(pageSrc).toMatch(/pcs-category-empty/);
    expect(pageSrc).toMatch(/pcs-add-category/);
    expect(pageSrc).toMatch(/pcs-empty-add-category/);
    expect(pageSrc).toMatch(/pcs-category-drawer/);
    expect(pageSrc).toMatch(/pcs-drawer-footer/);
    expect(pageSrc).toMatch(/createPortal/);
    expect(pageSrc).toMatch(/pcs-editor-name/);
    expect(pageSrc).toMatch(/pcs-editor-slug/);
    expect(pageSrc).toMatch(/pcs-editor-order/);
    expect(pageSrc).toMatch(/pcs-editor-enabled/);
    expect(pageSrc).toMatch(/pcs-editor-image/);
    expect(pageSrc).toMatch(/Belum ada gambar/);
    expect(pageSrc).toMatch(/pcs-editor-image-upload/);
    expect(pageSrc).toMatch(/pcs-editor-image-remove/);
    expect(pageSrc).toMatch(/createPublicCatalogCategory/);
    expect(pageSrc).toMatch(/patchPublicCatalogCategory/);
    expect(pageSrc).toMatch(/uploadPublicCatalogCategoryImage/);
    expect(pageSrc).toMatch(/deletePublicCatalogCategoryImage/);
    expect(pageSrc).toMatch(/display_order/);
    expect(pageSrc).toMatch(/enabled/);
    expect(pageSrc).toMatch(/<select/);
    expect(slugifyCategoryName("Cup PET!")).toBe("cup-pet");
  });

  test("manage controls gated by canEdit / view-only", () => {
    expect(pageSrc).toMatch(/canEdit/);
    expect(pageSrc).toMatch(/\{canEdit \?/);
    expect(pageSrc).toMatch(/disabled=\{!canEdit \|\| busy\}/);
    expect(settingsSrc).toMatch(/canEdit=\{canEditPublicCatalog\}/);
    expect(settingsSrc).toMatch(/settings\.public_catalog\.manage/);
  });

  test("no /harga behavior change in Phase A", () => {
    expect(pageSrc).not.toMatch(/\/harga/);
    expect(pageSrc).not.toMatch(/publicCustomerPricing/);
    expect(apiSrc).toMatch(/\/public\/customer-pricing\/groups/);
    expect(hargaSrc).toMatch(/publicCustomerPricingGroups|\/harga/);
  });
});

describe("Public Catalog Settings Phase B", () => {
  test("Kelompok Produk and URL & SEO sections remain intact", () => {
    expect(pageSrc).toMatch(/pcs-nav-kelompok/);
    expect(pageSrc).toMatch(/pcs-nav-seo/);
    expect(pageSrc).toMatch(/data-testid="pcs-groups"/);
    expect(pageSrc).toMatch(/data-testid="pcs-seo"/);
  });

  test("group list, drawer, visibility and read-only pricing status", () => {
    expect(apiSrc).toMatch(/publicCatalogGroups/);
    expect(apiSrc).toMatch(/updatePublicCatalogGroup/);
    expect(apiSrc).toMatch(/\/settings\/public-catalog\/groups/);
    expect(pageSrc).toMatch(/pcs-group-list/);
    expect(pageSrc).toMatch(/pcs-group-row/);
    expect(pageSrc).toMatch(/pcs-group-configure/);
    expect(pageSrc).toMatch(/pcs-group-drawer/);
    expect(pageSrc).toMatch(/pcs-group-editor-category/);
    expect(pageSrc).toMatch(/pcs-group-editor-public-name/);
    expect(pageSrc).toMatch(/pcs-group-editor-slug/);
    expect(pageSrc).toMatch(/pcs-group-editor-order/);
    expect(pageSrc).toMatch(/pcs-group-editor-visible/);
    expect(pageSrc).toMatch(/pcs-group-pricing-status/);
    expect(pageSrc).toMatch(/pcs-group-catalog-status/);
    expect(pageSrc).toMatch(/Status Pricing/);
    expect(pageSrc).toMatch(/Deskripsi Publik/);
    expect(pageSrc).toMatch(/customerPricingCatalogImageUrl/);
    expect(pageSrc).not.toMatch(/patchCustomerPricingGroup/);
    expect(pageSrc).not.toMatch(/updateCustomerPricingVersion/);
  });

  test("URL & SEO preview and meta fields", () => {
    expect(pageSrc).toMatch(/pcs-seo-group-select/);
    expect(pageSrc).toMatch(/pcs-seo-public-name/);
    expect(pageSrc).toMatch(/pcs-seo-slug/);
    expect(pageSrc).toMatch(/pcs-seo-preview-url/);
    expect(pageSrc).toMatch(/pcs-seo-meta-title/);
    expect(pageSrc).toMatch(/pcs-seo-meta-description/);
    expect(pageSrc).toMatch(/pcs-seo-save/);
    expect(pageSrc).toMatch(/previewProductUrl/);
    expect(previewProductUrl("")).toBe("Belum ada slug");
    expect(previewProductUrl("cup-inject")).toBe(
      "katalog.eternalpackaging.id/produk/cup-inject"
    );
    expect(pageSrc).not.toMatch(/path:\s*["']\/produk/);
  });
});

describe("Public Catalog Settings Phase C", () => {
  test("Varian and Galeri nav appear with Phase A/B sections", () => {
    expect(pageSrc).toMatch(/pcs-nav-varian/);
    expect(pageSrc).toMatch(/pcs-nav-galeri/);
    expect(pageSrc).toMatch(/pcs-nav-umum/);
    expect(pageSrc).toMatch(/pcs-nav-kategori/);
    expect(pageSrc).toMatch(/pcs-nav-kelompok/);
    expect(pageSrc).toMatch(/pcs-nav-seo/);
    expect(pageSrc).toMatch(/data-testid="pcs-variants"/);
    expect(pageSrc).toMatch(/data-testid="pcs-gallery"/);
  });

  test("variant list, drawer, attrs and read-only pricing/master status", () => {
    expect(apiSrc).toMatch(/publicCatalogVariants/);
    expect(apiSrc).toMatch(/updatePublicCatalogVariant/);
    expect(pageSrc).toMatch(/pcs-variant-group-select/);
    expect(pageSrc).toMatch(/pcs-variant-search/);
    expect(pageSrc).toMatch(/pcs-variant-list/);
    expect(pageSrc).toMatch(/pcs-variant-row/);
    expect(pageSrc).toMatch(/pcs-variant-configure/);
    expect(pageSrc).toMatch(/pcs-variant-drawer/);
    expect(pageSrc).toMatch(/pcs-variant-editor-size/);
    expect(pageSrc).toMatch(/pcs-variant-editor-color/);
    expect(pageSrc).toMatch(/pcs-variant-editor-finishing/);
    expect(pageSrc).toMatch(/pcs-variant-editor-order/);
    expect(pageSrc).toMatch(/pcs-variant-editor-visible/);
    expect(pageSrc).toMatch(/pcs-variant-readonly/);
    expect(pageSrc).toMatch(/Status Master/);
    expect(pageSrc).toMatch(/pcs-variant-name/);
    expect(pageSrc).not.toMatch(/patchCustomerPricingGroup/);
    expect(pageSrc).not.toMatch(/updateCustomerPricingVersion/);
    expect(pageSrc).not.toMatch(/hpp|profit|tier_prices/i);
  });

  test("gallery manager, legacy read-only, upload and preview interactions", () => {
    expect(apiSrc).toMatch(/publicCatalogVariantImages/);
    expect(apiSrc).toMatch(/uploadPublicCatalogVariantImage/);
    expect(apiSrc).toMatch(/patchPublicCatalogVariantImage/);
    expect(apiSrc).toMatch(/deletePublicCatalogVariantImage/);
    expect(pageSrc).toMatch(/pcs-gallery-group-select/);
    expect(pageSrc).toMatch(/pcs-gallery-variant-select/);
    expect(pageSrc).toMatch(/pcs-gallery-add/);
    expect(pageSrc).toMatch(/pcs-gallery-preview/);
    expect(pageSrc).toMatch(/pcs-gallery-thumbs/);
    expect(pageSrc).toMatch(/pcs-gallery-thumb/);
    expect(pageSrc).toMatch(/setGalleryPreviewId/);
    expect(pageSrc).toMatch(/pcs-gallery-drawer/);
    expect(pageSrc).toMatch(/pcs-gallery-editor-role/);
    expect(pageSrc).toMatch(/pcs-gallery-editor-order/);
    expect(pageSrc).toMatch(/pcs-gallery-editor-default/);
    expect(pageSrc).toMatch(/pcs-gallery-editor-enabled/);
    expect(pageSrc).toMatch(/pcs-gallery-legacy/);
    expect(pageSrc).toMatch(/Legacy image/);
    expect(pageSrc).toMatch(/Read-only/);
    expect(pageSrc).toMatch(/Hapus gambar galeri ini/);
    expect(pageSrc).toMatch(/onError/);
    expect(pageSrc).not.toMatch(/\/produk\/\{/);
    expect(hargaSrc).toMatch(/publicCustomerPricingGroups|\/harga/);
  });
});
