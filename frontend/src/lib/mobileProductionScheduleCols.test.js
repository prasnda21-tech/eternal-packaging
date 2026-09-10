import {
  MOBILE_PRODUCTION_ABSENT_KEYS,
  MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS,
  buildMobileProductionScheduleCols,
} from "./mobileProductionScheduleCols";

describe("mobileProductionScheduleCols", () => {
  test("Persiapan Cup immediately follows Total Qty; Screen is qty-only column", () => {
    const keys = MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.key);
    const labels = MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label);
    const qtyIdx = keys.indexOf("qty");
    expect(keys[qtyIdx + 1]).toBe("helper");
    expect(keys[qtyIdx + 2]).toBe("screen");
    expect(labels[qtyIdx + 1]).toBe("Persiapan Cup");
    expect(labels[qtyIdx + 2]).toBe("Screen");
    expect(labels.filter((l) => l === "Persiapan Screen")).toHaveLength(0);
    expect(labels.filter((l) => l === "Screen")).toHaveLength(1);
  });

  test("Payment Packing SJ Driver and screen_size are absent from PWA cols", () => {
    const keys = new Set(MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.key));
    for (const absent of MOBILE_PRODUCTION_ABSENT_KEYS) {
      expect(keys.has(absent)).toBe(false);
    }
    expect(keys.has("payment")).toBe(false);
    expect(keys.has("packing")).toBe(false);
    expect(keys.has("sj")).toBe(false);
    expect(keys.has("driver")).toBe(false);
    expect(keys.has("po")).toBe(true);
    expect(keys.has("customer")).toBe(true);
    expect(keys.has("work_date")).toBe(true);
    expect(keys.has("operator")).toBe(true);
    expect(keys.has("item")).toBe(true);
    expect(keys.has("qty_cetak")).toBe(true);
    expect(keys.has("prio")).toBe(true);
    expect(keys.has("buffer")).toBe(true);
    expect(keys.has("deadline")).toBe(true);
    expect(keys.has("deadline_ship")).toBe(true);
  });

  test("builder is stable for empty-ish source", () => {
    expect(buildMobileProductionScheduleCols([])).toEqual([]);
  });
});
