import {
  MOBILE_PRODUCTION_ABSENT_KEYS,
  MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS,
  buildMobileProductionScheduleCols,
} from "./mobileProductionScheduleCols";

describe("mobileProductionScheduleCols", () => {
  test("compact ops order is prio, date, customer, operator, item, then Cup/Screen/Cetak", () => {
    const keys = MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.key);
    const labels = MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label);
    expect(keys).toEqual(["prio", "work_date", "customer", "operator", "item", "helper", "screen", "qty_cetak", "packing", "loading", "shipped"]);
    expect(labels).toEqual(["Prioritas", "Tgl Kerja", "Customer / No PO", "Operator", "Item Line", "Cup", "Screen", "Cetak", "Pack", "Load", "Kirim"]);
    expect(labels).not.toContain("Surat Jalan");
    expect(labels).not.toContain("Qty Total");
    expect(keys).not.toContain("sj");
    expect(keys).not.toContain("qty");
  });

  test("Zona Driver Tahap Aktif and reclaimed columns are absent from PWA cols", () => {
    const keys = new Set(MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.key));
    const labels = MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label);
    for (const absent of MOBILE_PRODUCTION_ABSENT_KEYS) {
      expect(keys.has(absent)).toBe(false);
    }
    expect(labels).not.toContain("Zona");
    expect(labels).not.toContain("Driver");
    expect(labels).not.toContain("Tahap Aktif");
    expect(keys.has("po")).toBe(false);
    expect(keys.has("sj")).toBe(false);
    expect(keys.has("qty")).toBe(false);
    expect(keys.has("operator")).toBe(true);
    expect(keys.has("customer")).toBe(true);
    expect(keys.has("item")).toBe(true);
  });

  test("builder is stable for empty-ish source", () => {
    expect(buildMobileProductionScheduleCols([])).toEqual([]);
  });
});
