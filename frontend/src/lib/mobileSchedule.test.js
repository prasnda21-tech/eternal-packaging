import {
  FILTER_DEFAULTS,
  applyProductionFilters,
  sortProductionRows,
} from "./productionScheduleFilters";
import { PRODUCTION_SCHEDULE_TABLE_COLS, productionDashboardRequestParams } from "./productionScheduleTable";
import { operatorQtyCetakProgress, poTotalQtyDisplay, formatIdQty } from "./productionScheduleQty";
import {
  canViewMobileSchedule,
  defaultProductionDashboardParams,
  mobileProductionDashboardParams,
  mobileScheduleHref,
  rowsFromProductionDashboard,
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
    expect(mobileProductionDashboardParams(FILTER_DEFAULTS)).toEqual({
      include_cancelled: false,
      include_completed: true,
      view: "summary",
    });
    expect(defaultProductionDashboardParams()).not.toHaveProperty("view");
  });

  test("canonical columns are PO-level and match desktop order", () => {
    expect(PRODUCTION_SCHEDULE_TABLE_COLS.map((c) => c.label)).toEqual([
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
    ]);
    expect(PRODUCTION_SCHEDULE_TABLE_COLS).toHaveLength(21);
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
    const qty = poTotalQtyDisplay(rows[0]);
    expect(qty.text).toBe(formatIdQty(rows[0].total_quantity || 0));
  });

  test("qty cetak uses operator completed / PO total", () => {
    const prog = operatorQtyCetakProgress(
      { total_quantity: 20000 },
      [{ line_printable: true, qty_completed: 3000, split_id: "s1" }],
    );
    expect(prog.text).toBe("3.000 / 20.000");
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

});
