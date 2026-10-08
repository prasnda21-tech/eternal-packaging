import {
  ANOMALY_MEDIAN_RATIO,
  formatTierBand,
  isValidPositivePrice,
  medianOf,
  summarizeDraftPriceQuality,
} from "./customerPricingAnomaly";

describe("customerPricingAnomaly helpers", () => {
  test("median odd/even/single/empty", () => {
    expect(medianOf([3, 1, 2])).toBe(2);
    expect(medianOf([4, 1, 3, 2])).toBe(2.5);
    expect(medianOf([990])).toBe(990);
    expect(medianOf([])).toBeNull();
    expect(medianOf([null, "x"])).toBeNull();
  });

  test("valid positive price handling", () => {
    expect(isValidPositivePrice(1.4)).toBe(true);
    expect(isValidPositivePrice("840")).toBe(true);
    expect(isValidPositivePrice(0)).toBe(false);
    expect(isValidPositivePrice(-5)).toBe(false);
    expect(isValidPositivePrice(null)).toBe(false);
    expect(isValidPositivePrice("")).toBe(false);
    expect(isValidPositivePrice(true)).toBe(false);
  });

  test("price below 10% median triggers; exactly 10% does not", () => {
    const preview = {
      tiers: [
        { min_qty: 1, max_qty: 99 },
        { min_qty: 100, max_qty: null },
      ],
      products: [
        { accurate_item_id: "a", display_name: "A", tier_prices: [1000, 900] },
        { accurate_item_id: "b", display_name: "B", tier_prices: [1000, 100] },
      ],
    };
    // prices: 1000,900,1000,100 → sorted 100,900,1000,1000 → median 950 → 10%=95
    // 100 is NOT < 95
    let summary = summarizeDraftPriceQuality(preview);
    expect(summary.median).toBe(950);
    expect(summary.warningCount).toBe(0);

    preview.products[1].tier_prices[1] = 94;
    summary = summarizeDraftPriceQuality(preview);
    expect(summary.threshold).toBe(950 * ANOMALY_MEDIAN_RATIO);
    expect(summary.warningCount).toBe(1);
    expect(summary.anomalies[0]).toMatchObject({
      productName: "B",
      tierIndex: 1,
      price: 94,
      min_qty: 100,
      max_qty: null,
    });

    // exactly 10%
    preview.products[1].tier_prices[1] = 95;
    summary = summarizeDraftPriceQuality(preview);
    expect(summary.warningCount).toBe(0);
  });

  test("1.4 alongside hundreds is anomalous; min/range/median exact", () => {
    const summary = summarizeDraftPriceQuality({
      tiers: [
        { min_qty: 500, max_qty: 999 },
        { min_qty: 1000, max_qty: null },
      ],
      products: [
        {
          accurate_item_id: "p1",
          display_name: "16 oz Inject Datar Frosted",
          tier_prices: [840, 1.4],
        },
        {
          accurate_item_id: "p2",
          display_name: "16 oz Inject Datar Silver",
          tier_prices: [990, 1250],
        },
      ],
    });
    // 840, 1.4, 990, 1250 → sorted 1.4, 840, 990, 1250 → median (840+990)/2 = 915
    expect(summary.available).toBe(true);
    expect(summary.min).toBe(1.4);
    expect(summary.max).toBe(1250);
    expect(summary.median).toBe(915);
    expect(summary.warningCount).toBe(1);
    expect(summary.anomalies[0].price).toBe(1.4);
    expect(summary.anomalies[0].productName).toContain("Frosted");
    expect(formatTierBand(summary.anomalies[0])).toBe("1000+");
  });

  test("mixed tiers multiple products and empty/unavailable", () => {
    const multi = summarizeDraftPriceQuality({
      tiers: [
        { min_qty: 1, max_qty: 10 },
        { min_qty: 11, max_qty: null },
      ],
      products: [
        { display_name: "One", tier_prices: [200, 180] },
        { display_name: "Two", tier_prices: [220, null] },
        { display_name: "Three", tier_prices: [0, -1, 210] },
      ],
    });
    // valid: 200,180,220,210 → median (200+210)/2 = 205; none < 20.5
    expect(multi.warningCount).toBe(0);
    expect(multi.min).toBe(180);
    expect(multi.max).toBe(220);

    expect(summarizeDraftPriceQuality({ products: [], tiers: [] }).available).toBe(false);
    expect(
      summarizeDraftPriceQuality({
        products: [{ display_name: "X", tier_prices: [null, 0, ""] }],
        tiers: [{ min_qty: 1, max_qty: null }],
      }).available
    ).toBe(false);
  });

  test("does not expose HPP or margin fields", () => {
    const summary = summarizeDraftPriceQuality({
      tiers: [{ min_qty: 1, max_qty: null }],
      products: [
        {
          display_name: "Cup",
          hpp_snapshot: 500,
          manual_hpp: 500,
          margin: 0.5,
          tier_prices: [40],
        },
        {
          display_name: "Bowl",
          hpp_snapshot: 800,
          tier_prices: [900],
        },
      ],
    });
    // median 470 → threshold 47; 40 is anomalous
    const serialized = JSON.stringify(summary);
    expect(serialized).not.toMatch(/hpp/i);
    expect(serialized).not.toMatch(/margin/i);
    expect(summary.anomalies[0].price).toBe(40);
  });
});
