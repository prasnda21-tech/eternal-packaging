import {
  PRODUCTION_SCHEDULE_COLUMN_IDS,
  PRODUCTION_SCHEDULE_REQUIRED_COLUMN_IDS,
  PRODUCTION_SCHEDULE_TABLE_COLS,
} from "./productionScheduleTable";

const UNIFIED_ORDER = [
  "po",
  "customer",
  "work_date",
  "operator",
  "item",
  "qty_cetak",
  "qty",
  "prio",
  "buffer",
  "deadline",
  "deadline_ship",
  "outlet",
  "screen",
  "design",
  "payment",
  "hold",
  "helper",
  "packing",
  "sj",
  "driver",
  "status",
];

const UNIFIED_LABELS = [
  "No PO",
  "Customer",
  "Tgl Pengerjaan",
  "Operator",
  "Item",
  "Qty Cetak",
  "Total Qty",
  "Prio",
  "Buffer",
  "Deadline",
  "Deadline Pengiriman",
  "Outlet",
  "Qty Screen",
  "Design",
  "Payment",
  "HOLD",
  "Persiapan",
  "Packing",
  "Surat Jalan",
  "Driver",
  "Status Akhir",
];

describe("productionScheduleTable canonical columns", () => {
  test("keeps every previously legitimate column id plus Item", () => {
    expect(PRODUCTION_SCHEDULE_TABLE_COLS).toHaveLength(PRODUCTION_SCHEDULE_REQUIRED_COLUMN_IDS.length);
    expect(new Set(PRODUCTION_SCHEDULE_COLUMN_IDS)).toEqual(new Set(PRODUCTION_SCHEDULE_REQUIRED_COLUMN_IDS));
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS).toHaveLength(21);
  });

  test("uses one workflow-oriented order for desktop and PWA", () => {
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS).toEqual(UNIFIED_ORDER);
    expect(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.id)).toEqual(UNIFIED_ORDER);
    expect(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).toEqual(UNIFIED_LABELS);
    expect(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label).slice(0, 10)).toEqual([
      "No PO",
      "Customer",
      "Tgl Pengerjaan",
      "Operator",
      "Item",
      "Qty Cetak",
      "Total Qty",
      "Prio",
      "Buffer",
      "Deadline",
    ]);
    expect(new Set(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).size).toBe(21);
  });

  test("qty columns are numeric/right-aligned", () => {
    const qty = PRODUCTION_SCHEDULE_TABLE_COLS.filter((c) => ["qty", "screen", "qty_cetak"].includes(c.key));
    expect(qty).toHaveLength(3);
    qty.forEach((col) => {
      expect(col.align).toBe("right");
      expect(col.type).toBe("qty");
      expect(col.num).toBe(true);
    });
  });
});
