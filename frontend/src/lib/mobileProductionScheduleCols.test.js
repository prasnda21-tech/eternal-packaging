import {
  MOBILE_PRODUCTION_ABSENT_KEYS,
  MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS,
  buildMobileProductionScheduleCols,
} from "./mobileProductionScheduleCols";

describe("mobileProductionScheduleCols", () => {
  test("compact ops order is prio, date, customer, item, qty, then Cup/Screen/Cetak", () => {
    const keys = MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.key);
    const labels = MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label);
    expect(keys).toEqual(["prio", "work_date", "customer", "sj", "item", "qty", "helper", "screen", "qty_cetak", "packing", "loading", "shipped"]);
    expect(labels).toEqual(["Prioritas", "Tgl Kerja", "Customer / No PO", "Surat Jalan", "Item Line", "Qty Total", "Cup", "Screen", "Cetak", "Pack", "Load", "Kirim"]);
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
    expect(keys.has("customer")).toBe(true);
    expect(keys.has("item")).toBe(true);
  });

  test("builder is stable for empty-ish source", () => {
    expect(buildMobileProductionScheduleCols([])).toEqual([]);
  });
});
