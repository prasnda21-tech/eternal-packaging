import { computeStockCurrent, isAccuratePurchaseSource, purchaseSourceLabel } from "./currentStock";

describe("computeStockCurrent", () => {
  test("FINAL (10000+2000)-3000-4000 = 5000", () => {
    expect(computeStockCurrent(3000, 10000, 2000, 4000)).toBe(5000);
  });
  test("Accurate example (10000+3500)-3000-4000 = 6500", () => {
    expect(computeStockCurrent(3000, 10000, 3500, 4000)).toBe(6500);
  });
  test("negative not clamped (2000+1000)-4000-1000 = -2000", () => {
    expect(computeStockCurrent(4000, 2000, 1000, 1000)).toBe(-2000);
  });
  test("old formula removed", () => {
    expect(computeStockCurrent(10000, 2000, 1000, 3000)).toBe(-10000);
    expect(computeStockCurrent(10000, 2000, 1000, 3000)).not.toBe(4000);
  });
});

describe("purchase source", () => {
  test("Accurate vs Manual labels", () => {
    expect(purchaseSourceLabel("ACCURATE")).toBe("Accurate");
    expect(purchaseSourceLabel("MANUAL_FALLBACK")).toBe("Manual");
    expect(isAccuratePurchaseSource("ACCURATE")).toBe(true);
    expect(isAccuratePurchaseSource("MANUAL_FALLBACK")).toBe(false);
  });
});
