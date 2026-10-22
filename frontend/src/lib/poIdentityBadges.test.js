import { isPoUrgent, isPoBufferStock, poIdentityBadgeItems } from "./poIdentityBadges";

describe("poIdentityBadges", () => {
  test("normal → no badges", () => {
    expect(poIdentityBadgeItems({ priority: "normal", buffer_stock: false })).toEqual([]);
  });

  test("urgent only", () => {
    expect(poIdentityBadgeItems({ priority: "urgent" })).toEqual([
      { key: "urgent", label: "URGENT", className: "urgent-badge" },
    ]);
  });

  test("buffer only", () => {
    expect(poIdentityBadgeItems({ priority: "normal", buffer_stock: true })).toEqual([
      { key: "buffer", label: "BUFFER STOCK", className: "buffer-stock-badge" },
    ]);
  });

  test("urgent + buffer together", () => {
    expect(poIdentityBadgeItems({ priority: "urgent", buffer_stock: true })).toEqual([
      { key: "urgent", label: "URGENT", className: "urgent-badge" },
      { key: "buffer", label: "BUFFER STOCK", className: "buffer-stock-badge" },
    ]);
  });

  test("legacy null/missing is not urgent and not buffer", () => {
    expect(isPoUrgent({})).toBe(false);
    expect(isPoBufferStock({})).toBe(false);
    expect(isPoUrgent({ priority: null })).toBe(false);
    expect(isPoBufferStock({ buffer_stock: null })).toBe(false);
    expect(isPoBufferStock({ buffer_stock: false })).toBe(false);
    expect(poIdentityBadgeItems({})).toEqual([]);
  });

  test("urgent flag boolean is honored when priority omitted", () => {
    expect(isPoUrgent({ urgent: true })).toBe(true);
    expect(isPoUrgent({ urgent: false, priority: "urgent" })).toBe(false);
  });

  test("does not use approval request status", () => {
    expect(poIdentityBadgeItems({
      priority: "normal",
      request: { status: "pending" },
    })).toEqual([]);
    expect(poIdentityBadgeItems({
      priority: "urgent",
      request: { status: "rejected" },
    })).toEqual([
      { key: "urgent", label: "URGENT", className: "urgent-badge" },
    ]);
  });

  test("omitUrgent skips URGENT but keeps BUFFER STOCK", () => {
    expect(
      poIdentityBadgeItems({ priority: "urgent", buffer_stock: true }, { omitUrgent: true }),
    ).toEqual([
      { key: "buffer", label: "BUFFER STOCK", className: "buffer-stock-badge" },
    ]);
    expect(
      poIdentityBadgeItems({ priority: "urgent", buffer_stock: false }, { omitUrgent: true }),
    ).toEqual([]);
  });
});
