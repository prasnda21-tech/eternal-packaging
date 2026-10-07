import fs from "fs";
import path from "path";
import {
  SETTINGS_ROUTE_ALIASES,
  SETTINGS_TAB_IDS,
  SETTINGS_TILE_LABELS,
  settingsAliasFromPath,
  settingsMenuItems,
  settingsNavVisible,
  settingsTabFromPath,
} from "../lib/navAccess";
import {
  catalogStatusLabel,
  contextPricingValue,
  defaultGroupSection,
  defaultKelolaSection,
  filterUnifiedGroupRows,
  GLOBAL_CONTEXT_HIDDEN,
  GROUP_SCOPED_SECTIONS,
  mergeUnifiedGroupRows,
  pricingStatusLabel,
  productCountFromPricing,
  resolvePricingCatalogSection,
} from "./PricingCatalogSettings";

jest.mock("react-router-dom", () => ({
  useNavigate: () => jest.fn(),
  useSearchParams: () => [new URLSearchParams("section=kelompok")],
}), { virtual: true });

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const shellSrc = fs.readFileSync(path.join(__dirname, "PricingCatalogSettings.jsx"), "utf8");
const cssSrc = fs.readFileSync(path.join(__dirname, "PricingCatalogSettings.css"), "utf8");
const navSrc = fs.readFileSync(path.join(__dirname, "../lib/navAccess.js"), "utf8");
const workspaceSrc = fs.readFileSync(path.join(__dirname, "../lib/globalWorkspace.js"), "utf8");
const cpSrc = fs.readFileSync(path.join(__dirname, "CustomerPricingSettings.jsx"), "utf8");
const pcsSrc = fs.readFileSync(path.join(__dirname, "PublicCatalogSettings.jsx"), "utf8");

const pricingCup = {
  id: "pg-cup",
  group_name: "FX Cup Inject",
  enabled: true,
  current_draft_version_id: null,
  current_published_version_id: "ver-pub-1",
  current_published_version_number: 1,
  updated_at: "2026-10-08T00:00:00Z",
  summary_version: {
    id: "ver-pub-1",
    version_number: 1,
    products: Array.from({ length: 3 }, (_, i) => ({ accurate_item_id: `a${i}` })),
  },
};

const pricingDraftOnly = {
  id: "pg-draft",
  group_name: "DRAFT ONLY",
  enabled: true,
  current_draft_version_id: "ver-draft-1",
  current_draft_version_number: 2,
  current_published_version_id: null,
  summary_version: { id: "ver-draft-1", version_number: 2, products: [{ accurate_item_id: "x" }] },
};

const catalogCup = {
  pricing_group_id: "pg-cup",
  group_name: "FX Cup Inject",
  pricing_enabled: true,
  current_published_version_id: "ver-pub-1",
  catalog: {
    configured: true,
    public_name: "Cup Inject",
    public_name_display: "Cup Inject",
    slug: "cup-inject",
    category_name: "Cup",
    visible: true,
  },
};

const catalogHidden = {
  pricing_group_id: "pg-hidden",
  group_name: "HIDDEN BOX",
  pricing_enabled: true,
  catalog: {
    configured: true,
    public_name: "",
    slug: "hidden-box",
    category_name: "",
    visible: false,
  },
};

describe("PricingCatalogSettings Phase 1 shell", () => {
  test("canonical route and sidebar label replace old tiles", () => {
    expect(SETTINGS_TAB_IDS).toContain("pricing-catalog");
    expect(SETTINGS_TAB_IDS).not.toContain("customer-pricing");
    expect(SETTINGS_TAB_IDS).not.toContain("public-catalog");
    expect(SETTINGS_TILE_LABELS["pricing-catalog"]).toBe("Harga & Katalog");
    expect(settingsSrc).toMatch(/settings-tab-pricing-catalog/);
    expect(shellSrc).toMatch(/data-testid="pricing-catalog-settings"/);
  });

  test("old routes alias preserve query including group", () => {
    expect(SETTINGS_ROUTE_ALIASES["customer-pricing"]).toEqual({
      tab: "pricing-catalog",
      section: "harga",
    });
    expect(SETTINGS_ROUTE_ALIASES["public-catalog"]).toEqual({
      tab: "pricing-catalog",
      section: "katalog",
    });
    expect(settingsAliasFromPath("/settings/customer-pricing")).toEqual({
      tab: "pricing-catalog",
      section: "harga",
    });
    expect(settingsTabFromPath("/settings/public-catalog")).toBe("pricing-catalog");
    expect(settingsSrc).toMatch(/const params = new URLSearchParams\(location\.search\)/);
  });

  test("workspace exposes one Harga & Katalog leaf", () => {
    const hasPerm = (key) => (
      key === "nav.settings"
      || key === "settings.customer_pricing.view"
      || key === "settings.public_catalog.view"
    );
    const items = settingsMenuItems(hasPerm);
    expect(items.filter((item) => item.label === "Harga & Katalog")).toHaveLength(1);
    expect(workspaceSrc).toMatch(/settingsMenuItems\(hasPerm\)\.forEach/);
  });

  test("RBAC matrix for workspace and sections", () => {
    const pricingOnly = (key) => key === "nav.settings" || key === "settings.customer_pricing.view";
    const catalogOnly = (key) => key === "nav.settings" || key === "settings.public_catalog.view";
    const none = () => false;
    const manage = (key) => key === "nav.settings" || key === "permissions.manage";
    expect(settingsNavVisible(pricingOnly)["pricing-catalog"]).toBe(true);
    expect(settingsNavVisible(catalogOnly)["pricing-catalog"]).toBe(true);
    expect(settingsNavVisible(none)["pricing-catalog"]).toBe(false);
    // permissions.manage already grants settings tiles (pre-existing); shell still gates domains.
    expect(settingsNavVisible(manage)["pricing-catalog"]).toBe(true);
    expect(resolvePricingCatalogSection("harga", true, false)).toBe("harga");
    expect(resolvePricingCatalogSection("katalog", true, false)).toBe("kelompok");
    expect(resolvePricingCatalogSection("seo", false, true)).toBe("seo");
    expect(resolvePricingCatalogSection("bogus", true, true)).toBe("kelompok");
    expect(shellSrc).toMatch(/canViewPricing/);
    expect(shellSrc).toMatch(/canEditPricing/);
    expect(shellSrc).toMatch(/canPublishPricing/);
    expect(shellSrc).toMatch(/canViewCatalog/);
    expect(shellSrc).toMatch(/canEditCatalog/);
  });

  test("reuses CP and PCS without domain merge", () => {
    expect(shellSrc).toMatch(/selectedGroupId=\{activeGroupId\}/);
    expect(cpSrc).toMatch(/selectedGroupId/);
    expect(pcsSrc).toMatch(/controlledGroupId/);
    expect(shellSrc).not.toMatch(/publishCustomerPricingVersion/);
    expect(navSrc).not.toMatch(/settings\.pricing_catalog\./);
  });
});

describe("PricingCatalogSettings Phase 2+3 shared context + landing", () => {
  test("URL group identity and section helpers", () => {
    expect(shellSrc).toMatch(/searchParams\.get\("group"\)/);
    expect(shellSrc).toMatch(/next\.set\("group", gid\)/);
    expect(GROUP_SCOPED_SECTIONS.has("seo")).toBe(true);
    expect(GLOBAL_CONTEXT_HIDDEN.has("umum")).toBe(true);
    expect(defaultKelolaSection(true, true)).toBe("harga");
    expect(defaultKelolaSection(false, true)).toBe("katalog");
    expect(defaultGroupSection(true, true, "varian")).toBe("varian");
    expect(shellSrc).toMatch(/kelolaGroup/);
    expect(shellSrc).toMatch(/defaultKelolaSection/);
  });

  test("URL & SEO placement is group-scoped utility not global", () => {
    expect(shellSrc).toMatch(/GROUP_UTILITY_SECTIONS/);
    expect(shellSrc).toMatch(/showGroupUtility/);
    expect(shellSrc).toMatch(/hk-nav-seo/);
    expect(shellSrc).toMatch(/Global/);
    expect(shellSrc).toMatch(/Kelompok/);
    expect(shellSrc).toMatch(/nextSection === "seo" && !groupParam/);
  });

  test("context bar compact and suppressed on global sections", () => {
    expect(shellSrc).toMatch(/showContextBar/);
    expect(shellSrc).toMatch(/GROUP_SCOPED_SECTIONS\.has\(section\)/);
    expect(shellSrc).toMatch(/hk-context-meta/);
    expect(shellSrc).toMatch(/hk-context-internal/);
    expect(shellSrc).toMatch(/contextPricingValue/);
    expect(shellSrc).toMatch(/Ganti Kelompok/);
    expect(shellSrc).toMatch(/hk-change-to-landing/);
    expect(cssSrc).toMatch(/hk-context-meta dt/);
  });

  test("display name prefers public_name with internal secondary", () => {
    const rows = mergeUnifiedGroupRows({
      pricingGroups: [pricingCup],
      catalogGroups: [catalogCup],
      canViewPricing: true,
      canViewCatalog: true,
    });
    expect(rows[0].display_name).toBe("Cup Inject");
    expect(rows[0].group_name).toBe("FX Cup Inject");
    expect(shellSrc).toMatch(/Internal:/);
  });

  test("single product count semantic — no duplicate Varian column", () => {
    const rows = mergeUnifiedGroupRows({
      pricingGroups: [pricingCup, pricingDraftOnly],
      catalogGroups: [catalogCup, catalogHidden],
      canViewPricing: true,
      canViewCatalog: true,
    });
    const cup = rows.find((row) => row.pricing_group_id === "pg-cup");
    expect(cup.productCount).toBe(3);
    expect(cup.variantCount).toBeUndefined();
    expect(productCountFromPricing(pricingCup)).toBe(3);
    expect(shellSrc).toMatch(/<th>Produk<\/th>/);
    expect(shellSrc).not.toMatch(/<th>Varian<\/th>/);
    expect(shellSrc).not.toMatch(/<th>Draft<\/th>/);
    expect(shellSrc).toMatch(/<th>Published<\/th>/);
  });

  test("status labels stay distinct", () => {
    expect(pricingStatusLabel({ hasPricing: true, publishedVersionId: "p" })).toBe("Published");
    expect(pricingStatusLabel({ hasPricing: true, draftVersionId: "d" })).toBe("Draft");
    expect(catalogStatusLabel({ hasCatalogOverlay: true, catalogVisible: false })).toBe("Tersembunyi");
    expect(contextPricingValue({
      hasPricing: true,
      publishedVersionId: "p",
      publishedVersionLabel: "v1",
      draftVersionLabel: "—",
    })).toBe("Published v1");
  });

  test("search filters and RBAC field isolation", () => {
    const rows = mergeUnifiedGroupRows({
      pricingGroups: [pricingCup, pricingDraftOnly],
      catalogGroups: [catalogCup, catalogHidden],
      canViewPricing: true,
      canViewCatalog: true,
    });
    expect(filterUnifiedGroupRows(rows, { search: "cup-inject" })).toHaveLength(1);
    expect(filterUnifiedGroupRows(rows, { pricingFilter: "draft" })[0].pricing_group_id).toBe("pg-draft");

    const pricingOnly = mergeUnifiedGroupRows({
      pricingGroups: [pricingCup],
      catalogGroups: [catalogCup],
      canViewPricing: true,
      canViewCatalog: false,
    });
    expect(pricingOnly[0].hasCatalogOverlay).toBe(false);
    expect(pricingOnly[0].slug).toBe("");

    const catalogOnly = mergeUnifiedGroupRows({
      pricingGroups: [pricingCup],
      catalogGroups: [catalogCup],
      canViewPricing: false,
      canViewCatalog: true,
    });
    expect(catalogOnly[0].hasPricing).toBe(false);
    expect(catalogOnly[0].productCount).toBe(null);
  });

  test("embed cleanup and no duplicate pickers", () => {
    expect(shellSrc).toMatch(/hk-embed-focused/);
    expect(cssSrc).toMatch(/cp-page-head-copy/);
    expect(cssSrc).toMatch(/hk-embed-focused/);
    expect(pcsSrc).toMatch(/hideGroupPicker/);
    expect(pcsSrc).toMatch(/Pilih varian untuk mengelola galeri/);
    expect(pcsSrc).toMatch(/Pilih kelompok dari Kelompok Produk/);
    expect(shellSrc).toMatch(/hk-section-pick-group/);
    expect(cpSrc).toMatch(/focusedGroupRef/);
  });

  test("landing list APIs only — no N\u002b1 detail", () => {
    expect(shellSrc).toMatch(/customerPricingGroups\(\)/);
    expect(shellSrc).toMatch(/publicCatalogGroups\(\)/);
    expect(shellSrc).not.toMatch(/customerPricingGroup\(/);
    expect(shellSrc).not.toMatch(/publicCatalogVariants\(/);
    expect(shellSrc).not.toMatch(/publicCatalogVariantImages\(/);
  });

  test("accessibility labels present", () => {
    expect(shellSrc).toMatch(/aria-label="Cari kelompok"/);
    expect(shellSrc).toMatch(/aria-label="Filter harga"/);
    expect(shellSrc).toMatch(/aria-label=\{`Kelola \$\{row\.display_name\}`\}/);
    expect(shellSrc).toMatch(/aria-haspopup="listbox"/);
    expect(shellSrc).toMatch(/aria-current=\{section === item\.id \? "page" : undefined\}/);
    expect(cssSrc).toMatch(/:focus-visible/);
  });

  test("no backend merge markers", () => {
    expect(shellSrc).not.toMatch(/pricing_catalog_groups|combined.?save|cross.?domain/i);
  });
});
