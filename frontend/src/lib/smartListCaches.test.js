import {
  __resetAllSmartListCachesForTests,
  clearAllSmartListCaches,
  productionDisplayListCache,
  purchasingListCache,
  salesDeliveryListCache,
  salesReceiptListCache,
  SALES_LIST_SWR_TTL_MS,
  PRODUCTION_DISPLAY_SWR_TTL_MS,
} from "./smartListCaches";

describe("smartListCaches", () => {
  beforeEach(() => {
    __resetAllSmartListCachesForTests();
  });

  test("domain caches isolate by endpoint and identity", () => {
    const receiptKey = salesReceiptListCache.buildKey({ u: "u1", b: "b1", tab: "active", page: 1 });
    const deliveryKey = salesDeliveryListCache.buildKey({ u: "u1", b: "b1", status: "all" });
    expect(receiptKey).not.toBe(deliveryKey);

    salesReceiptListCache.set(receiptKey, { receipts: [{ id: "r1" }] });
    salesDeliveryListCache.set(deliveryKey, { deliveries: [{ id: "d1" }] });
    expect(salesReceiptListCache.get(receiptKey)?.data.receipts[0].id).toBe("r1");
    expect(salesDeliveryListCache.get(deliveryKey)?.data.deliveries[0].id).toBe("d1");
    expect(salesReceiptListCache.get(deliveryKey)).toBeNull();
  });

  test("production display TTL is shorter than sales", () => {
    expect(PRODUCTION_DISPLAY_SWR_TTL_MS).toBeLessThanOrEqual(15_000);
    expect(SALES_LIST_SWR_TTL_MS).toBe(30_000);
  });

  test("clearAllSmartListCaches drops every domain on logout", () => {
    const k1 = salesReceiptListCache.buildKey({ u: "u1", page: 1 });
    const k2 = purchasingListCache.buildKey({ u: "u1", kind: "purchase_requests", q: "" });
    const k3 = productionDisplayListCache.buildKey({ u: "u1", role: "operator" });
    salesReceiptListCache.set(k1, { receipts: [] });
    purchasingListCache.set(k2, { requests: [] });
    productionDisplayListCache.set(k3, { tasks: [] });
    clearAllSmartListCaches();
    expect(salesReceiptListCache.get(k1)).toBeNull();
    expect(purchasingListCache.get(k2)).toBeNull();
    expect(productionDisplayListCache.get(k3)).toBeNull();
  });

  test("rejects error payloads", () => {
    const key = salesDeliveryListCache.buildKey({ u: "u1" });
    expect(salesDeliveryListCache.set(key, { error: "nope", deliveries: [{ id: "x" }] })).toBeNull();
    expect(salesDeliveryListCache.get(key)).toBeNull();
  });
});
