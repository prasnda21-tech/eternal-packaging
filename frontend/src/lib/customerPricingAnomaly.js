/** Draft pricing quality helpers for Customer Pricing publish confirmation. */

export const ANOMALY_MEDIAN_RATIO = 0.1;

export function isValidPositivePrice(value) {
  if (value === null || value === undefined || value === "") return false;
  if (typeof value === "boolean") return false;
  const number = Number(value);
  return Number.isFinite(number) && number > 0;
}

export function medianOf(values) {
  const numeric = [];
  for (const value of Array.isArray(values) ? values : []) {
    if (value === null || value === undefined || value === "") continue;
    if (typeof value === "boolean") continue;
    const number = Number(value);
    if (!Number.isFinite(number)) continue;
    numeric.push(number);
  }
  if (!numeric.length) return null;
  const sorted = [...numeric].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[middle];
  return (sorted[middle - 1] + sorted[middle]) / 2;
}

/**
 * Summarize draft/preview tier prices for publish confirmation.
 * Uses post-preview product.tier_prices only — not public-catalog eligibility.
 */
export function summarizeDraftPriceQuality(preview) {
  const products = Array.isArray(preview?.products) ? preview.products : [];
  const tiers = Array.isArray(preview?.tiers) ? preview.tiers : [];
  const observations = [];

  for (const product of products) {
    const prices = Array.isArray(product?.tier_prices) ? product.tier_prices : [];
    const count = Math.max(prices.length, tiers.length);
    for (let index = 0; index < count; index += 1) {
      const raw = prices[index];
      if (!isValidPositivePrice(raw)) continue;
      const tier = tiers[index] || {};
      observations.push({
        price: Number(raw),
        productName: String(product.display_name || product.accurate_item_id || "Produk"),
        accurateItemId: product.accurate_item_id || null,
        tierIndex: index,
        min_qty: tier.min_qty ?? null,
        max_qty: tier.max_qty ?? null,
      });
    }
  }

  if (!observations.length) {
    return {
      available: false,
      min: null,
      max: null,
      median: null,
      anomalies: [],
      warningCount: 0,
      threshold: null,
    };
  }

  const prices = observations.map((row) => row.price);
  const median = medianOf(prices);
  const threshold = median * ANOMALY_MEDIAN_RATIO;
  const anomalies = observations.filter((row) => row.price < threshold);

  return {
    available: true,
    min: Math.min(...prices),
    max: Math.max(...prices),
    median,
    anomalies,
    warningCount: anomalies.length,
    threshold,
  };
}

export function formatTierBand(observation) {
  const min = observation?.min_qty;
  const max = observation?.max_qty;
  if (min == null && max == null) {
    return `Tier ${Number(observation?.tierIndex || 0) + 1}`;
  }
  if (max == null || max === "") return `${min}+`;
  return `${min}–${max}`;
}
