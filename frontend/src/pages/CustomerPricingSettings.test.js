import fs from "fs";
import path from "path";
import {
  appendExactProduct,
  calculateFormulaPrice,
  filterGroupProducts,
  groupProductCount,
  hppSourceSummary,
  modeSummary,
  productDrawerMeta,
  profitSummary,
  removeExactProduct,
  setProductTierPrice,
  tierLabel,
} from "./CustomerPricingSettings";

const settingsSrc = fs.readFileSync(path.join(__dirname, "Settings.jsx"), "utf8");
const cpSrc = fs.readFileSync(path.join(__dirname, "CustomerPricingSettings.jsx"), "utf8");
const cssSrc = fs.readFileSync(path.join(__dirname, "../App.css"), "utf8");
const appSrc = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "../lib/api.js"), "utf8");

describe("Customer Pricing multi-product group UI", () => {
  test("Settings APIs stay group-based while public pricing uses anonymous transport", () => {
    expect(settingsSrc).toMatch(/id:\s*"pricing-catalog"/);
    expect(settingsSrc).toMatch(/PricingCatalogSettings/);
    expect(settingsSrc).toMatch(/canPublishCustomerPricing/);
    expect(settingsSrc).toMatch(/canViewPricing/);
    expect(apiSrc).toMatch(/customerPricingGroups/);
    expect(apiSrc).toMatch(/createCustomerPricingGroup/);
    expect(apiSrc).toMatch(/customer-pricing\/groups/);
    expect(apiSrc).not.toMatch(/customer-pricing\/products/);
    expect(apiSrc).toMatch(/publicCustomerPricingGroups/);
    expect(apiSrc).toMatch(/\/public\/customer-pricing\/groups/);
    expect(apiSrc).toMatch(/credentials:\s*"omit"/);
    expect(apiSrc).toMatch(/uploadCustomerPricingCatalogImage/);
    expect(apiSrc).toMatch(/deleteCustomerPricingCatalogImage/);
  });

  test("group list has compact columns, count-only products, and product drawer", () => {
    for (const label of [
      "Kelompok", "Produk", "Mode Harga", "Sumber HPP", "Keuntungan",
      "Draft", "Published", "Status", "Updated", "Aksi",
    ]) {
      expect(cpSrc).toContain(`<th>${label}</th>`);
    }
    expect(cpSrc).toMatch(/summary_version/);
    expect(cpSrc).not.toMatch(/names\.join\(" • "\)/);
    expect(cpSrc).toMatch(/groupProductCount/);
    expect(cpSrc).toMatch(/cp-product-count/);
    expect(cpSrc).toMatch(/data-testid="cp-product-drawer"/);
    expect(cpSrc).toMatch(/createPortal/);
    expect(cpSrc).toMatch(/Cari produk dalam kelompok/);
    expect(cpSrc).toMatch(/Buka Editor Harga/);
    expect(cpSrc).toMatch(/openProductDrawer/);
    expect(cpSrc).toMatch(/openEditorFromDrawer/);
    expect(cpSrc).toMatch(/Escape/);
    expect(cpSrc).not.toMatch(/Hapus Kelompok/);
    expect(cpSrc).toMatch(/0 produk|produk exact|produk dalam kelompok/);
    expect(cpSrc).toMatch(/Atur kelompok harga dan harga khusus produk/);
  });

  test("shared Settings hero is hidden for pricing-catalog and design-cetakan", () => {
    expect(settingsSrc).toMatch(/pricingCatalogPage/);
    expect(settingsSrc).toMatch(/hideSharedSettingsHeading/);
    expect(settingsSrc).toMatch(/designCetakanPage \|\| pricingCatalogPage/);
    expect(settingsSrc).toMatch(/!hideSharedSettingsHeading \?/);
    expect(settingsSrc).toMatch(/tab === "design-cetakan"/);
    expect(settingsSrc).toMatch(/tab === "pricing-catalog"/);
    expect(settingsSrc).toMatch(/tab === "hpp"/);
    expect(settingsSrc).toMatch(/page-heading/);
  });

  test("general settings save stays independent from draft/publish", () => {
    expect(cpSrc).toMatch(/updateCustomerPricingSettings/);
    expect(cpSrc).toMatch(/data-testid="cp-save-settings"/);
    expect(cpSrc).toMatch(/createCustomerPricingDraft/);
    expect(cpSrc).toMatch(/publishCustomerPricingVersion/);
    expect(cpSrc).toMatch(/updateCustomerPricingVersion/);
    expect(cpSrc).toMatch(/const saveSettings = async/);
    expect(cpSrc).toMatch(/const saveDraft = async/);
    expect(cpSrc).toMatch(/const publish = async/);
    expect(cpSrc.indexOf("updateCustomerPricingSettings")).toBeGreaterThan(-1);
    expect(cpSrc.indexOf("updateCustomerPricingVersion")).toBeGreaterThan(-1);
    expect(cpSrc.indexOf("publishCustomerPricingVersion")).toBeGreaterThan(-1);
  });

  test("editor preserves shared tier sheet and per-product matrices", () => {
    expect(cpSrc).toMatch(/data-testid="cp-shared-tiers"/);
    expect(cpSrc).toMatch(/data-testid="cp-formula-matrix"/);
    expect(cpSrc).toMatch(/data-testid="cp-manual-price-matrix"/);
    expect(cpSrc).toMatch(/Preview Harga per Produk × Tier/);
    expect(cpSrc).toMatch(/Harga Manual per Produk × Tier/);
    expect(cpSrc).toMatch(/setProductTierPrice/);
    expect(cpSrc).toMatch(/tier_prices/);
  });

  test("manual selector is exact, exposes identity, and contains no inference", () => {
    expect(cpSrc).toMatch(/Tambah Produk Exact/);
    expect(cpSrc).toMatch(/Tidak ada pencocokan nama otomatis/);
    expect(cpSrc).toMatch(/accurate_item_id/);
    expect(cpSrc).toMatch(/master_item_id/);
    expect(cpSrc).not.toMatch(/oz.*match|code.*prefix|sku.*similar/i);
    expect(cpSrc).toMatch(/masterSearchSeq/);
    expect(cpSrc).toMatch(/cp-master-empty/);
    expect(cpSrc).not.toMatch(/searchMasters/);
  });

  test("Published UI is readonly and creates a new Draft to edit", () => {
    expect(cpSrc).toMatch(/data-testid="cp-readonly-version"/);
    expect(cpSrc).toMatch(/Versi Published/);
    expect(cpSrc).toMatch(/data-testid="cp-clone-draft"/);
    expect(cpSrc).toMatch(/createCustomerPricingDraft/);
    expect(cpSrc).toMatch(/disabled=\{readOnly \|\| !canEdit\}/);
  });

  test("spreadsheet and mobile overflow safeguards remain", () => {
    expect(cpSrc).toMatch(/cp-product-table-wrap/);
    expect(cpSrc).toMatch(/cp-sheet-scroll/);
    expect(cpSrc).toMatch(/cp-price-matrix/);
    expect(cssSrc).toMatch(/\.cp-product-table-wrap,.cp-sheet-scroll\{overflow:auto/);
    expect(cssSrc).toMatch(/@media\s*\(max-width:\s*700px\)/);
  });

  test("Phase 2 public route stays separate from Phase 1 settings UI", () => {
    expect(appSrc).toMatch(/path="\/harga"/);
    expect(cpSrc).not.toMatch(/PublicHargaPage|NavAccessMatrix/);
  });

  test("group presentation metadata is optional and managed outside pricing versions", () => {
    expect(cpSrc).toMatch(/data-testid="cp-catalog-presentation"/);
    expect(cpSrc).toMatch(/data-testid="cp-public-description"/);
    expect(cpSrc).toMatch(/maxLength=\{280\}/);
    expect(cpSrc).toMatch(/Upload Foto/);
    expect(cpSrc).toMatch(/Ganti Foto/);
    expect(cpSrc).toMatch(/Hapus Foto/);
    expect(cpSrc).toMatch(/patchCustomerPricingGroup/);
    expect(cpSrc).toMatch(/uploadCustomerPricingCatalogImage/);
    expect(cpSrc).toMatch(/deleteCustomerPricingCatalogImage/);
    expect(cpSrc).not.toMatch(/catalog_image:\s*catalogImage/);
    expect(cssSrc).toMatch(/\.cp-catalog-image-preview/);
  });
});

describe("compact table and product drawer helpers", () => {
  const longName =
    "Plastik Klip Pond Hitam 20 x 30 Premium Anti Sobek Extra Panjang Label Custom";

  test("groupProductCount uses summary products without joining names", () => {
    expect(groupProductCount({
      summary_version: {
        products: [
          { accurate_item_id: "a", display_name: longName },
          { accurate_item_id: "b", display_name: "BOWL 500" },
        ],
      },
    })).toBe(2);
    expect(groupProductCount({})).toBe(0);
  });

  test("drawer client search matches display_name and item_code", () => {
    const products = [
      { accurate_item_id: "1", display_name: longName, item_code: "100300" },
      { accurate_item_id: "2", display_name: "CUP PET 12oz", item_code: "CUP-12" },
    ];
    expect(filterGroupProducts(products, "klip").map((p) => p.accurate_item_id)).toEqual(["1"]);
    expect(filterGroupProducts(products, "cup-12").map((p) => p.accurate_item_id)).toEqual(["2"]);
    expect(filterGroupProducts(products, "zzz")).toEqual([]);
    expect(filterGroupProducts(products, "")).toHaveLength(2);
  });

  test("drawer meta derives only from already-loaded product fields", () => {
    expect(productDrawerMeta({
      item_code: "100300",
      hpp_snapshot: 1200,
      tier_prices: [2500, 2200],
    })).toEqual({
      code: "100300",
      hpp: expect.stringMatching(/1\.?200|1200/),
      price: expect.stringMatching(/2\.?500|2500/),
    });
    expect(productDrawerMeta({ accurate_item_id: "x" })).toEqual({
      code: "—",
      hpp: "—",
      price: "—",
    });
  });

  test("CSS scopes compact inputs and drawer under customer pricing", () => {
    expect(cssSrc).toMatch(/\.content \.cp-settings input/);
    expect(cssSrc).toMatch(/height:\s*36px/);
    expect(cssSrc).toMatch(/\.cp-product-drawer/);
    expect(cssSrc).toMatch(/-webkit-line-clamp:\s*2/);
    expect(cssSrc).toMatch(/\.cp-group-sheet/);
    expect(cssSrc).not.toMatch(/\.cp-product-count[\s\S]{0,80}!important/);
  });
});

describe("exact membership behavior", () => {
  const first = {
    id: "master-101",
    accurate_item_id: "101",
    item_name: "Paper cup 12oz",
    item_code: "CUP-12",
    unit: "pcs",
    is_active: true,
  };
  const second = {
    id: "master-102",
    accurate_item_id: "102",
    item_name: "Paper cup 14oz",
    item_code: "CUP-14",
    unit: "pcs",
    is_active: true,
  };

  test("adds multiple exact products with independent empty matrices", () => {
    const one = appendExactProduct([], first, 3);
    const two = appendExactProduct(one, second, 3);
    expect(two.map((product) => product.accurate_item_id)).toEqual(["101", "102"]);
    expect(two[0].master_item_id).toBe("master-101");
    expect(two[1].master_item_id).toBe("master-102");
    expect(two[0].tier_prices).toEqual([null, null, null]);
    expect(two[0].tier_prices).not.toBe(two[1].tier_prices);
  });

  test("duplicate exact product selection is rejected", () => {
    const current = appendExactProduct([], first, 2);
    expect(() => appendExactProduct(current, first, 2))
      .toThrow("Produk yang sama tidak boleh dipilih dua kali.");
  });

  test("Draft removal preserves other product identity", () => {
    const current = appendExactProduct(appendExactProduct([], first, 2), second, 2);
    expect(removeExactProduct(current, "101").map((product) => product.accurate_item_id))
      .toEqual(["102"]);
  });

  test("manual product × tier changes only one exact product", () => {
    const current = appendExactProduct(appendExactProduct([], first, 2), second, 2);
    const changed = setProductTierPrice(current, "101", 1, "2300", 2);
    expect(changed[0].tier_prices).toEqual([null, "2300"]);
    expect(changed[1].tier_prices).toEqual([null, null]);
  });
});

describe("shared pricing and independent formula calculations", () => {
  const formula = (profitMode, values) => ({
    status: "DRAFT",
    pricing_mode: "HPP_FORMULA",
    hpp_source: "AUTO_HPP",
    profit_mode: profitMode,
    tiers: values.map((profit_value) => ({ profit_value })),
  });

  test("different product HPPs produce different prices under shared percentage", () => {
    expect(calculateFormulaPrice(300, 40, "HPP_PLUS_PERCENT")).toBe(420);
    expect(calculateFormulaPrice(350, 40, "HPP_PLUS_PERCENT")).toBe(490);
    expect(calculateFormulaPrice(400, 40, "HPP_PLUS_PERCENT")).toBe(560);
  });

  test("shared fixed-profit calculation remains per HPP", () => {
    expect(calculateFormulaPrice(300, 200, "HPP_PLUS_FIXED")).toBe(500);
    expect(calculateFormulaPrice(450, 200, "HPP_PLUS_FIXED")).toBe(650);
  });

  test("profit summaries are group-level and never leak HPP", () => {
    expect(profitSummary(formula("HPP_PLUS_PERCENT", [40, 35, 25])).value)
      .toBe("40% → 25%");
    expect(profitSummary(formula("HPP_PLUS_FIXED", [500, 200])).value)
      .toBe("Rp 500 → Rp 200");
    expect(profitSummary({ ...formula("HPP_PLUS_PERCENT", [40]), hpp_snapshot: 999999 }).value)
      .toBe("40%");
  });

  test("manual pricing never fabricates profit", () => {
    expect(profitSummary({ status: "DRAFT", pricing_mode: "MANUAL_TIER_PRICE" }))
      .toMatchObject({ value: "Harga Manual", label: "Tier Manual" });
  });

  test("mode, HPP source and labels are deterministic", () => {
    expect(modeSummary(null)).toBe("Belum diatur");
    expect(hppSourceSummary(formula("HPP_PLUS_PERCENT", [40]))).toBe("HPP Eternal");
    expect(tierLabel({ min_qty: 5000, max_qty: null })).toBe("5000+");
  });

  test("each product has its own media area with upload/replace/delete controls bound to accurate_item_id", () => {
    expect(cpSrc).toMatch(/cp-product-photo-cell/);
    expect(cpSrc).toMatch(/cp-product-photo-wrap/);
    expect(cpSrc).toMatch(/uploadCustomerPricingProductImage/);
    expect(cpSrc).toMatch(/deleteCustomerPricingProductImage/);
    expect(cpSrc).toMatch(/customerPricingProductImageUrl/);
    expect(cpSrc).toMatch(/data-testid=\{`cp-product-photo-\$\{product\.accurate_item_id\}`\}/);
    expect(cpSrc).toMatch(/data-testid=\{`cp-upload-product-photo-\$\{product\.accurate_item_id\}`\}/);
    expect(cpSrc).toMatch(/data-testid=\{`cp-remove-product-photo-\$\{product\.accurate_item_id\}`\}/);
    expect(apiSrc).toMatch(/uploadCustomerPricingProductImage/);
    expect(apiSrc).toMatch(/deleteCustomerPricingProductImage/);
    expect(apiSrc).toMatch(/customerPricingProductImageUrl/);
  });

  test("same-name products maintain independent accurate_item_id bindings", () => {
    const p1 = { id: "m-1", accurate_item_id: "aid-1", item_name: "Kardus Polos", item_code: "KP-1", unit: "pcs" };
    const p2 = { id: "m-2", accurate_item_id: "aid-2", item_name: "Kardus Polos", item_code: "KP-2", unit: "pcs" };
    const list = appendExactProduct([], p1, 1);
    const updated = appendExactProduct(list, p2, 1);
    expect(updated).toHaveLength(2);
    expect(updated[0].accurate_item_id).toBe("aid-1");
    expect(updated[1].accurate_item_id).toBe("aid-2");
    expect(updated[0].display_name).toBe(updated[1].display_name);
  });
});
