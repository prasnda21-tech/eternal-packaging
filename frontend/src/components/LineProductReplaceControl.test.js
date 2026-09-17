import { hasLineItemChanges, isSameCanonicalProduct, parsePositiveQty } from "./LineProductReplaceControl";

jest.mock("./MasterItemSearchSelect", () => ({
  __esModule: true,
  default: () => null,
  formatMasterItemLabel: (item) => item?.item_name || item?.name || "",
}));

jest.mock("../lib/api", () => ({
  __esModule: true,
  default: {},
}));

describe("LineProductReplaceControl qty helpers", () => {
  const line = { name: "Cup Lama", master_item_id: "old-item", quantity: 1000, unit: "pcs" };

  test("accepts only positive whole quantities", () => {
    expect(parsePositiveQty("1500")).toBe(1500);
    expect(parsePositiveQty("0")).toBeNull();
    expect(parsePositiveQty("-1")).toBeNull();
    expect(parsePositiveQty("1.5")).toBeNull();
    expect(parsePositiveQty("")).toBeNull();
  });

  test("detects qty-only, product-only, and combined changes", () => {
    expect(hasLineItemChanges(line, null, "1000")).toBe(false);
    expect(hasLineItemChanges(line, null, "1200")).toBe(true);
    expect(hasLineItemChanges(line, { id: "new-item" }, "1000")).toBe(true);
    expect(hasLineItemChanges(line, { id: "new-item" }, "1200")).toBe(true);
  });

  test("treats the same canonical product and quantity as a no-op", () => {
    expect(isSameCanonicalProduct(line, { id: "old-item" })).toBe(true);
    expect(hasLineItemChanges(line, { id: "old-item" }, "1000")).toBe(false);
    expect(hasLineItemChanges(line, { id: "old-item" }, "1200")).toBe(true);
  });
});
