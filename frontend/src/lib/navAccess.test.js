import { inventoryNavVisible, purchasingNavVisible, NAV_KEYS } from "./navAccess";

const has = (...keys) => (k) => keys.includes(k);

describe("inventoryNavVisible", () => {
  test("hides parent when no child is granted", () => {
    const v = inventoryNavVisible(has(NAV_KEYS.inventory));
    expect(v.parent).toBe(false);
    expect(v.masterItems).toBe(false);
    expect(v.currentStock).toBe(false);
  });

  test("shows parent for master items only", () => {
    const v = inventoryNavVisible(has(NAV_KEYS.inventory, NAV_KEYS.masterItems));
    expect(v.parent).toBe(true);
    expect(v.masterItems).toBe(true);
    expect(v.currentStock).toBe(false);
  });

  test("shows parent for current stock only", () => {
    const v = inventoryNavVisible(has(NAV_KEYS.inventory, NAV_KEYS.currentStock));
    expect(v.parent).toBe(true);
    expect(v.masterItems).toBe(false);
    expect(v.currentStock).toBe(true);
  });
});

describe("purchasingNavVisible", () => {
  test("hides parent without receipts child", () => {
    const v = purchasingNavVisible(has(NAV_KEYS.purchasing));
    expect(v.parent).toBe(false);
    expect(v.receipts).toBe(false);
  });

  test("shows parent when receipts granted", () => {
    const v = purchasingNavVisible(has(NAV_KEYS.purchasing, NAV_KEYS.purchaseReceipts));
    expect(v.parent).toBe(true);
    expect(v.receipts).toBe(true);
  });
});
