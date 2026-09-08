import {
  FILTER_DEFAULTS,
  applyProductionFilters,
  sortProductionRows,
} from "./productionScheduleFilters";
import { PRODUCTION_SCHEDULE_TABLE_COLS, productionDashboardRequestParams } from "./productionScheduleTable";
import { totalQtyScheduledCell, scheduledPrintQtyProgress, formatIdQty } from "./productionScheduleQty";
import {
  MOBILE_SCHEDULE_ZOOM_DEFAULT,
  MOBILE_SCHEDULE_ZOOM_FIT,
  MOBILE_SCHEDULE_ZOOM_MAX,
  MOBILE_SCHEDULE_ZOOM_MIN,
  canViewMobileSchedule,
  clampMobileScheduleZoom,
  defaultProductionDashboardParams,
  fitMobileScheduleZoom,
  mobileScheduleHref,
  rowsFromProductionDashboard,
  stepMobileScheduleZoom,
} from "./mobileSchedule";

describe("mobile production schedule canonical helpers", () => {
  test("view permission uses schedule.view / nav key, not role names", () => {
    expect(canViewMobileSchedule((p) => p === "schedule.view")).toBe(true);
    expect(canViewMobileSchedule((p) => p === "nav.production_schedule")).toBe(true);
    expect(canViewMobileSchedule((p) => p === "operator.update")).toBe(false);
    expect(canViewMobileSchedule((p) => p === "admin")).toBe(false);
  });

  test("dashboard payload uses rows, never invented keys", () => {
    const rows = [{ id: "a" }];
    expect(rowsFromProductionDashboard({ rows, total: 1 })).toEqual(rows);
    expect(rowsFromProductionDashboard({ orders: rows })).toEqual([]);
    expect(rowsFromProductionDashboard(null)).toEqual([]);
  });

  test("default dashboard params match desktop FILTER_DEFAULTS", () => {
    expect(FILTER_DEFAULTS.includeCancelled).toBe(false);
    expect(FILTER_DEFAULTS.includeCompleted).toBe(true);
    expect(FILTER_DEFAULTS.sortBy).toBe("deadline");
    expect(defaultProductionDashboardParams()).toEqual({
      include_cancelled: false,
      include_completed: true,
    });
    expect(productionDashboardRequestParams(FILTER_DEFAULTS)).toEqual({
      include_cancelled: false,
      include_completed: true,
    });
  });

  test("canonical columns are PO-level and match desktop order", () => {
    expect(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).toEqual([
      "Customer",
      "Deadline",
      "Deadline Pengiriman",
      "Tgl Pengerjaan",
      "Prio",
      "Buffer",
      "No PO",
      "Outlet",
      "Total Qty",
      "Qty Cetak",
      "Operator",
      "Design",
      "Payment",
      "HOLD",
      "Qty Screen",
      "Persiapan",
      "Packing",
      "Surat Jalan",
      "Driver",
      "Status Akhir",
    ]);
    expect(PRODUCTION_SCHEDULE_TABLE_COLS).toHaveLength(20);
  });

  test("one dashboard PO stays one filtered row even with same-name lines", () => {
    const rows = [{
      id: "po-1",
      po_number: "PO-1",
      customer: "PT Example",
      outlet: "Tebet",
      current_stage: 8,
      operators: [
        { line_idx: 0, line_id: "lid-a", line_name: "Cup 16oz", operator_name: "Budi", quantity: 1000, line_printable: true },
        { line_idx: 2, line_id: "lid-b", line_name: "Cup 16oz", operator_name: "Sari", quantity: 500, line_printable: true },
      ],
    }];
    const filtered = applyProductionFilters(rows, FILTER_DEFAULTS);
    expect(filtered).toHaveLength(1);
    expect(sortProductionRows(filtered, FILTER_DEFAULTS.sortBy)).toHaveLength(1);
    const qty = totalQtyScheduledCell(rows[0]);
    expect(qty.groups).toHaveLength(2);
    expect(qty.groups[0].line_idx).toBe(0);
    expect(qty.groups[1].line_idx).toBe(2);
    expect(qty.groups[0].line_name).toBe("Cup 16oz");
    expect(qty.groups[1].line_name).toBe("Cup 16oz");
  });

  test("qty cetak uses canonical scheduledPrintQtyProgress", () => {
    const prog = scheduledPrintQtyProgress({
      operator_assigned_qty: 10000,
      print_qty_total: 20000,
      total_quantity: 25000,
    });
    expect(prog.text).toBe(`${formatIdQty(10000)} / ${formatIdQty(20000)}`);
  });

  test("search matches PO / customer / outlet like desktop", () => {
    const rows = [
      { id: "1", po_number: "PO-1001", customer: "PT Example", outlet: "Tebet", current_stage: 8 },
      { id: "2", po_number: "PO-1002", customer: "CV Lain", outlet: "PIK", current_stage: 8 },
    ];
    expect(applyProductionFilters(rows, { ...FILTER_DEFAULTS, q: "example" })).toHaveLength(1);
    expect(applyProductionFilters(rows, { ...FILTER_DEFAULTS, q: "pik" })).toHaveLength(1);
    expect(applyProductionFilters(rows, { ...FILTER_DEFAULTS, q: "PO-1002" })).toHaveLength(1);
  });

  test("href is mobile PO detail without granting workspace", () => {
    expect(mobileScheduleHref("abc")).toBe("/m/orders/abc");
    expect(mobileScheduleHref("abc")).not.toMatch(/ws=/);
    expect(mobileScheduleHref("")).toBe("/m/orders");
  });

  test("zoom steps clamp between 50 and 150", () => {
    expect(clampMobileScheduleZoom(100)).toBe(MOBILE_SCHEDULE_ZOOM_DEFAULT);
    expect(stepMobileScheduleZoom(100, 1)).toBe(110);
    expect(stepMobileScheduleZoom(100, -1)).toBe(90);
    expect(stepMobileScheduleZoom(50, -1)).toBe(MOBILE_SCHEDULE_ZOOM_MIN);
    expect(stepMobileScheduleZoom(150, 1)).toBe(MOBILE_SCHEDULE_ZOOM_MAX);
    expect(fitMobileScheduleZoom()).toBe(MOBILE_SCHEDULE_ZOOM_FIT);
  });
});
