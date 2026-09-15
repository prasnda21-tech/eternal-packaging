import { formatArmadaCapacityQty, parseArmadaCapacityQtyInput } from "./armadaCapacity";

describe("armadaCapacity parse/format", () => {
  test("formats with id-ID thousand separators", () => {
    expect(formatArmadaCapacityQty(50000)).toBe("50.000");
    expect(formatArmadaCapacityQty(40000)).toBe("40.000");
    expect(formatArmadaCapacityQty(null)).toBe("");
  });

  test("parses formatted and raw integers", () => {
    expect(parseArmadaCapacityQtyInput("50.000")).toEqual({ value: 50000 });
    expect(parseArmadaCapacityQtyInput("50000")).toEqual({ value: 50000 });
    expect(parseArmadaCapacityQtyInput("125.000")).toEqual({ value: 125000 });
  });

  test("rejects invalid values", () => {
    expect(parseArmadaCapacityQtyInput("").error).toBeTruthy();
    expect(parseArmadaCapacityQtyInput("0").error).toBeTruthy();
    expect(parseArmadaCapacityQtyInput("-1").error).toBeTruthy();
    expect(parseArmadaCapacityQtyInput("abc").error).toBeTruthy();
    expect(parseArmadaCapacityQtyInput("NaN").error).toBeTruthy();
    expect(parseArmadaCapacityQtyInput("Infinity").error).toBeTruthy();
    expect(parseArmadaCapacityQtyInput("50.5").error).toBeTruthy();
    expect(parseArmadaCapacityQtyInput("10,5").error).toBeTruthy();
  });
});
