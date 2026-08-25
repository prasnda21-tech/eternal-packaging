import { computeStockCurrent } from "./currentStock";

describe("computeStockCurrent", () => {
  test("TEST 1 = 4000", () => {
    expect(computeStockCurrent(10000, 2000, 1000, 3000)).toBe(4000);
  });
  test("TEST 2 negative not clamped", () => {
    expect(computeStockCurrent(5000, 2000, 1000, 3000)).toBe(-1000);
  });
  test("TEST 3 zero", () => {
    expect(computeStockCurrent(5000, 1000, 1000, 3000)).toBe(0);
  });
});
