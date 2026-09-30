import { FILTER_DEFAULTS } from "./productionScheduleFilters";
import {
  DESKTOP_PRODUCTION_SCHEDULE_COL_KEYS,
  DESKTOP_PRODUCTION_SCHEDULE_HIDDEN_KEYS,
  DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS,
  PRODUCTION_SCHEDULE_COLUMN_IDS,
  PRODUCTION_SCHEDULE_REQUIRED_COLUMN_IDS,
  PRODUCTION_SCHEDULE_TABLE_COLS,
  desktopProductionScheduleGroupSpans,
  productionDashboardRequestParams,
} from "./productionScheduleTable";

const UNIFIED_ORDER = [
  "po",
  "customer",
  "work_date",
  "operator",
  "item",
  "qty_operator",
  "qty_cetak",
  "progress",
  "qty",
  "prio",
  "buffer",
  "deadline",
  "deadline_ship",
  "outlet",
  "screen_size",
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
  "Qty Pengerjaan Operator",
  "Qty Cetak (Scheduled)",
  "Progress",
  "Total Qty",
  "Prio",
  "Buffer",
  "Tanggal Pengiriman",
  "Sumber Jadwal",
  "Outlet",
  "Screen",
  "Persiapan Screen",
  "Design",
  "Payment",
  "HOLD",
  "Persiapan Cup",
  "Packing",
  "Surat Jalan",
  "Driver",
  "Status Akhir",
];

describe("productionDashboardRequestParams", () => {
  test("desktop and shared callers request existing summary view", () => {
    expect(productionDashboardRequestParams(FILTER_DEFAULTS)).toEqual({
      include_cancelled: false,
      include_completed: true,
      view: "summary",
    });
    expect(productionDashboardRequestParams({
      includeCancelled: true,
      includeCompleted: false,
    })).toEqual({
      include_cancelled: true,
      include_completed: false,
      view: "summary",
    });
    expect(productionDashboardRequestParams({
      includeCompleted: false,
      workDateFilter: "hari_ini",
    })).toEqual({
      include_cancelled: false,
      include_completed: true,
      view: "summary",
    });
  });
});

describe("productionScheduleTable canonical columns", () => {
  test("keeps every previously legitimate column id plus Item", () => {
    expect(PRODUCTION_SCHEDULE_TABLE_COLS).toHaveLength(PRODUCTION_SCHEDULE_REQUIRED_COLUMN_IDS.length);
    expect(new Set(PRODUCTION_SCHEDULE_COLUMN_IDS)).toEqual(new Set(PRODUCTION_SCHEDULE_REQUIRED_COLUMN_IDS));
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS).toHaveLength(24);
  });

  test("uses one workflow-oriented order for desktop and PWA", () => {
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS).toEqual(UNIFIED_ORDER);
    expect(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.id)).toEqual(UNIFIED_ORDER);
    expect(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).toEqual(UNIFIED_LABELS);
    expect(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label).slice(0, 12)).toEqual([
      "No PO",
      "Customer",
      "Tgl Pengerjaan",
      "Operator",
      "Item",
      "Qty Pengerjaan Operator",
      "Qty Cetak (Scheduled)",
      "Progress",
      "Total Qty",
      "Prio",
      "Buffer",
      "Tanggal Pengiriman",
    ]);
    expect(new Set(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).size).toBe(24);
  });

  test("desktop jadwal hides Driver and Surat Jalan without deleting them", () => {
    expect(PRODUCTION_SCHEDULE_COLUMN_IDS).toEqual(expect.arrayContaining(["sj", "driver"]));
    expect(DESKTOP_PRODUCTION_SCHEDULE_HIDDEN_KEYS).toEqual(["sj", "driver"]);
    expect(DESKTOP_PRODUCTION_SCHEDULE_COL_KEYS).not.toContain("sj");
    expect(DESKTOP_PRODUCTION_SCHEDULE_COL_KEYS).not.toContain("driver");
    expect(DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.key)).toEqual(DESKTOP_PRODUCTION_SCHEDULE_COL_KEYS);
    expect(DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).toEqual(expect.arrayContaining([
      "No PO",
      "Customer",
      "Status Akhir",
    ]));
    expect(DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).not.toContain("Driver");
    expect(DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).not.toContain("Surat Jalan");
    const po = DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS.find((c) => c.key === "po");
    const customer = DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS.find((c) => c.key === "customer");
    expect(po.sticky).toBe("po");
    expect(customer.sticky).toBe("customer");
    expect(desktopProductionScheduleGroupSpans().map((g) => ({ label: g.label, span: g.span, keys: g.keys }))).toEqual([
      { label: "Identitas PO", span: 2, keys: ["po", "customer"] },
      { label: "Penjadwalan", span: 2, keys: ["work_date", "operator"] },
      { label: "Produksi", span: 11, keys: ["item", "qty_cetak", "qty_operator", "progress", "qty", "prio", "buffer", "deadline", "deadline_ship", "outlet", "screen_size"] },
      { label: "Progress Produksi", span: 6, keys: ["screen", "design", "payment", "hold", "helper", "packing"] },
      { label: "Status", span: 1, keys: ["status"] },
    ]);
    expect(DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).toEqual([
      "No PO",
      "Customer",
      "Tgl Pengerjaan",
      "Operator",
      "Item",
      "Qty Cetak (Scheduled)",
      "Qty Pengerjaan Operator",
      "Progress",
      "Total Qty",
      "Prio",
      "Buffer",
      "Tanggal Pengiriman",
      "Sumber Jadwal",
      "Outlet",
      "Screen",
      "Persiapan Screen",
      "Design",
      "Payment",
      "HOLD",
      "Persiapan Cup",
      "Packing",
      "Status Akhir",
    ]);
  });

  test("qty columns are numeric/right-aligned", () => {
    const qty = PRODUCTION_SCHEDULE_TABLE_COLS.filter((c) => ["qty", "screen", "qty_cetak", "qty_operator", "progress"].includes(c.key));
    expect(qty).toHaveLength(5);
    qty.forEach((col) => {
      expect(col.align).toBe("right");
      expect(col.type).toBe("qty");
      expect(col.num).toBe(true);
    });
  });
});
