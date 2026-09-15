import { wibDateKey } from "./suratJalan";
import { buildProductionScheduleDisplayRows } from "./productionScheduleDisplayRows";
import {
  FILTER_DEFAULTS,
  applyProductionFilters,
  applyWorkDateFilterControl,
  applyWorkDateRangeControl,
  applyDateRangeTodayShortcut,
  applyDeadlineTodayShortcut,
  applyWorkDateTodayShortcut,
  buildHariIniDisplayRows,
  isScheduledToday,
  isScheduledWorkUnfinished,
  isTodayWorkloadRow,
  productionScheduleWorkUnits,
  productionTodayKey,
  scheduledWorkDateKey,
  scheduledWorkProgress,
} from "./productionScheduleFilters";

const TODAY = "2026-09-14";

function op({ name, date, qty, done, extra = {} }) {
  return {
    split_id: `s-${name}-${date}`,
    operator_email: `${name}@x.com`,
    operator_user_id: `u-${name}`,
    operator_name: name,
    quantity: qty,
    qty_completed: done,
    work_date: date,
    line_printable: true,
    line_idx: extra.line_idx ?? 0,
    line_id: extra.line_id || `lid-${name}`,
    line_name: extra.line_name || "Cup",
    ...extra,
  };
}

function po(overrides = {}) {
  return {
    id: "po-1",
    po_number: "PO-1",
    customer: "PT Contoh",
    deadline: "2026-09-20",
    current_stage: 8,
    status_final: "Operator",
    cancelled: false,
    total_quantity: 1000,
    print_qty_total: 1000,
    print_completed_qty: 0,
    operators: [],
    ...overrides,
  };
}

function hariIni(rows, extras = {}) {
  return applyProductionFilters(rows, {
    ...FILTER_DEFAULTS,
    workDateFilter: "hari_ini",
    today: TODAY,
    includeCompleted: extras.includeCompleted ?? true,
    includeCancelled: extras.includeCancelled ?? false,
    ...extras,
  });
}

function displayHariIni(rows, includeCompleted = true) {
  const filtered = hariIni(rows, { includeCompleted });
  return buildHariIniDisplayRows(filtered, TODAY, includeCompleted);
}

describe("productionTodayKey WIB", () => {
  test("17. WIB midnight boundary and date-only work_date stay calendar dates", () => {
    expect(productionTodayKey(new Date("2026-09-13T17:00:00.000Z"))).toBe("2026-09-14");
    expect(productionTodayKey(new Date("2026-09-13T16:59:59.000Z"))).toBe("2026-09-13");
    expect(productionTodayKey(new Date("2026-09-14T16:59:59.000Z"))).toBe("2026-09-14");
    expect(wibDateKey(new Date("2026-09-13T17:00:00.000Z"))).toBe(productionTodayKey(new Date("2026-09-13T17:00:00.000Z")));
    expect(scheduledWorkDateKey({ work_date: "2026-09-14" })).toBe("2026-09-14");
    expect(scheduledWorkDateKey({ work_date: "2026-09-14T00:00:00Z" })).toBe("2026-09-14");
  });
});

describe("Hari Ini workload predicate", () => {
  test("1. today unfinished INCLUDE", () => {
    const row = po({
      work_date: TODAY,
      operators: [op({ name: "Budi", date: TODAY, qty: 1000, done: 200 })],
    });
    expect(hariIni([row])).toHaveLength(1);
    expect(displayHariIni([row]).map((r) => r.displayWorkDate)).toEqual([TODAY]);
    expect(isTodayWorkloadRow(displayHariIni([row])[0], TODAY, true)).toBe(true);
  });

  test("2. today complete + Selesai ON INCLUDE", () => {
    const row = po({
      work_date: TODAY,
      current_stage: 14,
      status_final: "Terkirim",
      print_completed_qty: 1000,
      operators: [op({ name: "Budi", date: TODAY, qty: 1000, done: 1000 })],
    });
    expect(hariIni([row], { includeCompleted: true })).toHaveLength(1);
    expect(displayHariIni([row], true)).toHaveLength(1);
  });

  test("3. today complete + Selesai OFF EXCLUDE", () => {
    const row = po({
      work_date: TODAY,
      current_stage: 14,
      status_final: "Terkirim",
      print_completed_qty: 1000,
      operators: [op({ name: "Budi", date: TODAY, qty: 1000, done: 1000 })],
    });
    expect(hariIni([row], { includeCompleted: false })).toHaveLength(0);
    expect(displayHariIni([row], false)).toHaveLength(0);
  });

  test("4. historical unfinished INCLUDE as carry-over", () => {
    const row = po({
      work_date: "2026-09-12",
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 400 })],
    });
    expect(hariIni([row])).toHaveLength(1);
    expect(displayHariIni([row])[0].displayWorkDate).toBe("2026-09-12");
  });

  test("5. historical complete + Selesai ON EXCLUDE", () => {
    const row = po({
      work_date: "2026-09-12",
      current_stage: 14,
      status_final: "Terkirim",
      print_completed_qty: 1000,
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 1000 })],
    });
    expect(hariIni([row], { includeCompleted: true })).toHaveLength(0);
    expect(displayHariIni([row], true)).toHaveLength(0);
  });

  test("6. historical complete + Selesai OFF EXCLUDE", () => {
    const row = po({
      work_date: "2026-09-12",
      current_stage: 14,
      status_final: "Terkirim",
      print_completed_qty: 1000,
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 1000 })],
    });
    expect(hariIni([row], { includeCompleted: false })).toHaveLength(0);
  });

  test("7. future unfinished EXCLUDE", () => {
    const row = po({
      work_date: "2026-09-15",
      operators: [op({ name: "Budi", date: "2026-09-15", qty: 1000, done: 0 })],
    });
    expect(hariIni([row])).toHaveLength(0);
    expect(displayHariIni([row])).toHaveLength(0);
  });

  test("8. Belum Dijadwalkan EXCLUDE", () => {
    const row = po({ work_date: null, operators: [] });
    expect(hariIni([row])).toHaveLength(0);
    expect(displayHariIni([row])).toHaveLength(0);
  });

  test("9. same PO 12/09 complete + 14/09 unfinished shows only 14/09", () => {
    const row = po({
      work_date: TODAY,
      operators: [
        op({ name: "Andi", date: "2026-09-12", qty: 1000, done: 1000 }),
        op({ name: "Budi", date: TODAY, qty: 1000, done: 500 }),
      ],
    });
    const display = displayHariIni([row]);
    expect(display).toHaveLength(1);
    expect(display[0].displayOperator.operator_name).toBe("Budi");
    expect(display[0].displayWorkDate).toBe(TODAY);
  });

  test("10. same PO 12/09 unfinished + 15/09 unfinished shows only 12/09 carry-over", () => {
    const row = po({
      work_date: "2026-09-12",
      operators: [
        op({ name: "Andi", date: "2026-09-12", qty: 1000, done: 200 }),
        op({ name: "Budi", date: "2026-09-15", qty: 1000, done: 0 }),
      ],
    });
    const display = displayHariIni([row]);
    expect(display).toHaveLength(1);
    expect(display[0].displayOperator.operator_name).toBe("Andi");
    expect(display[0].displayWorkDate).toBe("2026-09-12");
  });

  test("11. same PO 12/09 partial + 14/09 complete: carry-over always, today complete only if Selesai ON", () => {
    const active = po({
      work_date: TODAY,
      current_stage: 8,
      operators: [
        op({ name: "Andi", date: "2026-09-12", qty: 1000, done: 600 }),
        op({ name: "Budi", date: TODAY, qty: 1000, done: 1000 }),
      ],
    });
    const activeDisplay = displayHariIni([active], true);
    expect(activeDisplay.map((r) => r.displayWorkDate).sort()).toEqual(["2026-09-12", TODAY]);
    expect(displayHariIni([active], false).map((r) => r.displayWorkDate)).toEqual(["2026-09-12"]);

    const selesai = po({
      ...active,
      current_stage: 14,
      status_final: "Terkirim",
    });
    expect(displayHariIni([selesai], true).map((r) => r.displayWorkDate).sort()).toEqual(["2026-09-12", TODAY]);
    expect(displayHariIni([selesai], false).map((r) => r.displayWorkDate)).toEqual(["2026-09-12"]);
  });

  test("12. historical partial 1000/600 INCLUDE", () => {
    const row = po({
      work_date: "2026-09-12",
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 600 })],
    });
    const display = displayHariIni([row])[0];
    expect(scheduledWorkProgress(display)).toEqual({ scheduled: 1000, completed: 600 });
    expect(isScheduledWorkUnfinished(display)).toBe(true);
    expect(hariIni([row])).toHaveLength(1);
  });

  test("13. historical exact 1000/1000 EXCLUDE", () => {
    const row = po({
      work_date: "2026-09-12",
      current_stage: 8,
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 1000 })],
    });
    expect(hariIni([row])).toHaveLength(0);
    expect(displayHariIni([row])).toHaveLength(0);
  });

  test("14. do not treat all rows unfinished from PO current_stage", () => {
    const row = po({
      work_date: TODAY,
      current_stage: 8,
      operators: [
        op({ name: "Andi", date: "2026-09-12", qty: 1000, done: 1000 }),
        op({ name: "Budi", date: TODAY, qty: 1000, done: 100 }),
      ],
    });
    const display = displayHariIni([row]);
    expect(display).toHaveLength(1);
    expect(display[0].displayWorkDate).toBe(TODAY);
    expect(display.some((r) => r.displayWorkDate === "2026-09-12")).toBe(false);
  });

  test("15. cancelled historical work is not carry-over", () => {
    const historical = po({
      work_date: "2026-09-12",
      cancelled: true,
      status_final: "Cancelled",
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 100 })],
    });
    expect(hariIni([historical], { includeCancelled: true })).toHaveLength(0);
    expect(hariIni([historical], { includeCancelled: false })).toHaveLength(0);

    const todayCancelled = po({
      id: "po-c",
      po_number: "PO-C",
      work_date: TODAY,
      cancelled: true,
      status_final: "Cancelled",
      operators: [op({ name: "Budi", date: TODAY, qty: 1000, done: 0 })],
    });
    expect(hariIni([todayCancelled], { includeCancelled: false })).toHaveLength(0);
    expect(hariIni([todayCancelled], { includeCancelled: true })).toHaveLength(1);
  });

  test("16. Tanggal Pengiriman / deadline does not determine Hari Ini", () => {
    const pastDeadline = po({
      deadline: "2026-09-10",
      delivery_deadline_effective: "2026-09-10",
      work_date: "2026-09-15",
      operators: [op({ name: "Budi", date: "2026-09-15", qty: 1000, done: 0 })],
    });
    const todayDeadline = po({
      id: "po-2",
      po_number: "PO-2",
      deadline: TODAY,
      delivery_deadline_effective: TODAY,
      work_date: null,
      operators: [],
    });
    const carry = po({
      id: "po-3",
      po_number: "PO-3",
      deadline: "2026-09-20",
      work_date: "2026-09-12",
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 100 })],
    });
    expect(hariIni([pastDeadline, todayDeadline, carry]).map((r) => r.po_number)).toEqual(["PO-3"]);
  });

  test("18. Semua Pengerjaan keeps mixed dates and unscheduled", () => {
    const rows = [
      po({ id: "a", po_number: "A", work_date: TODAY, operators: [op({ name: "A", date: TODAY, qty: 10, done: 0 })] }),
      po({ id: "b", po_number: "B", work_date: "2026-09-12", operators: [op({ name: "B", date: "2026-09-12", qty: 10, done: 10 })] }),
      po({ id: "c", po_number: "C", work_date: "2026-09-15", operators: [op({ name: "C", date: "2026-09-15", qty: 10, done: 0 })] }),
      po({ id: "d", po_number: "D", work_date: null, operators: [] }),
    ];
    const all = applyProductionFilters(rows, FILTER_DEFAULTS);
    expect(all.map((r) => r.po_number)).toEqual(["A", "B", "C", "D"]);
  });

  test("19. Belum Dijadwalkan existing filter still works", () => {
    const rows = [
      po({ id: "a", po_number: "A", work_date: TODAY, operators: [op({ name: "A", date: TODAY, qty: 10, done: 0 })] }),
      po({ id: "d", po_number: "D", work_date: null, operators: [] }),
    ];
    const unscheduled = applyProductionFilters(rows, { ...FILTER_DEFAULTS, workDateFilter: "unscheduled" });
    expect(unscheduled.map((r) => r.po_number)).toEqual(["D"]);
  });

  test("20. Dari/Sampai existing range still works and is mutually exclusive with Hari Ini", () => {
    const rows = [
      po({ id: "a", po_number: "A", work_date: "2026-09-12", operators: [op({ name: "A", date: "2026-09-12", qty: 10, done: 0 })] }),
      po({ id: "b", po_number: "B", work_date: TODAY, operators: [op({ name: "B", date: TODAY, qty: 10, done: 0 })] }),
      po({ id: "c", po_number: "C", work_date: "2026-09-15", operators: [op({ name: "C", date: "2026-09-15", qty: 10, done: 0 })] }),
    ];
    const ranged = applyProductionFilters(rows, {
      ...FILTER_DEFAULTS,
      workDateFrom: "2026-09-15",
      workDateTo: "2026-09-15",
    });
    expect(ranged.map((r) => r.po_number)).toEqual(["C"]);

    expect(applyWorkDateFilterControl("hari_ini")).toEqual({
      workDateFilter: "hari_ini",
      workDateFrom: "",
      workDateTo: "",
    });
    expect(applyWorkDateRangeControl("2026-09-15", "", "hari_ini")).toEqual({
      workDateFilter: "all",
      workDateFrom: "2026-09-15",
      workDateTo: "",
    });
    expect(applyWorkDateRangeControl("2026-09-15", "2026-09-16", "all")).toEqual({
      workDateFilter: "all",
      workDateFrom: "2026-09-15",
      workDateTo: "2026-09-16",
    });
    expect(applyDateRangeTodayShortcut(new Date("2026-09-15T10:00:00+07:00"))).toEqual({
      from: "2026-09-15",
      to: "2026-09-15",
    });
    expect(applyWorkDateTodayShortcut("hari_ini", new Date("2026-09-15T10:00:00+07:00"))).toEqual({
      workDateFilter: "all",
      workDateFrom: "2026-09-15",
      workDateTo: "2026-09-15",
    });
    expect(applyDeadlineTodayShortcut(new Date("2026-09-15T10:00:00+07:00"))).toEqual({
      dateFrom: "2026-09-15",
      dateTo: "2026-09-15",
    });
  });

  test("21. today highlight stays exact-today, not historical carry-over", () => {
    const mockToday = new Date("2026-09-14T10:00:00+07:00");
    const carry = po({
      work_date: "2026-09-12",
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 100 })],
    });
    const todayRow = po({
      work_date: TODAY,
      operators: [op({ name: "Budi", date: TODAY, qty: 1000, done: 100 })],
    });
    expect(isTodayWorkloadRow(buildProductionScheduleDisplayRows([carry])[0], TODAY, true)).toBe(true);
    expect(isScheduledToday(carry, mockToday)).toBe(false);
    expect(isScheduledToday(todayRow, mockToday)).toBe(true);
  });
});

describe("Hari Ini exact work-unit regressions", () => {
  function sameBudi({ date, qty, done, extra = {} }) {
    return op({
      name: "Budi",
      date,
      qty,
      done,
      extra: {
        operator_user_id: "u-budi",
        operator_email: "budi@x.com",
        line_idx: extra.line_idx,
        line_id: extra.line_id || `lid-${date}`,
        ...extra,
      },
    });
  }

  test("1. TODAY complete on active PO: Selesai OFF hides the today row, not the unfinished sibling", () => {
    const row = po({
      current_stage: 8,
      status_final: "Operator",
      operators: [
        sameBudi({ date: TODAY, qty: 1000, done: 1000, extra: { line_idx: 0 } }),
        op({ name: "Andi", date: "2026-09-12", qty: 1000, done: 400, extra: { line_idx: 1, line_id: "lid-andi" } }),
      ],
    });
    expect(row.current_stage).toBe(8);
    expect(displayHariIni([row], false).map((r) => r.displayWorkDate)).toEqual(["2026-09-12"]);
    expect(displayHariIni([row], true).map((r) => r.displayWorkDate).sort()).toEqual(["2026-09-12", TODAY]);
  });

  test("2. SAME OPERATOR historical complete + today partial → only 14/09", () => {
    const row = po({
      operators: [
        sameBudi({ date: "2026-09-12", qty: 1000, done: 1000, extra: { line_idx: 0 } }),
        sameBudi({ date: TODAY, qty: 1000, done: 500, extra: { line_idx: 1 } }),
      ],
    });
    const units = productionScheduleWorkUnits(row);
    expect(units).toHaveLength(2);
    expect(new Set(units.map((u) => u.operatorKey))).toEqual(new Set(["uid:u-budi"]));
    expect(units.map((u) => u.workDate).sort()).toEqual(["2026-09-12", TODAY]);
    expect(units.map((u) => ({ date: u.workDate, scheduled: u.scheduled, completed: u.completed }))).toEqual([
      { date: "2026-09-12", scheduled: 1000, completed: 1000 },
      { date: TODAY, scheduled: 1000, completed: 500 },
    ]);
    const display = displayHariIni([row], true);
    expect(display).toHaveLength(1);
    expect(display[0].displayWorkDate).toBe(TODAY);
    expect(display[0].displayOperator.operator_name).toBe("Budi");
    expect(display[0].displayOperator.splits).toHaveLength(1);
    expect(display[0].displayOperator.splits[0].work_date).toBe(TODAY);
  });

  test("3. SAME OPERATOR historical partial + future → only 12/09", () => {
    const row = po({
      operators: [
        sameBudi({ date: "2026-09-12", qty: 1000, done: 600, extra: { line_idx: 0 } }),
        sameBudi({ date: "2026-09-15", qty: 1000, done: 0, extra: { line_idx: 1 } }),
      ],
    });
    const units = productionScheduleWorkUnits(row);
    expect(units).toHaveLength(2);
    expect(units.every((u) => u.operatorKey === "uid:u-budi")).toBe(true);
    const display = displayHariIni([row], true);
    expect(display).toHaveLength(1);
    expect(display[0].displayWorkDate).toBe("2026-09-12");
    expect(display[0].displayOperator.splits[0].qty_completed).toBe(600);
  });

  test("4. SAME OPERATOR two historical dates → only unfinished 13/09", () => {
    const row = po({
      operators: [
        sameBudi({ date: "2026-09-12", qty: 1000, done: 1000, extra: { line_idx: 0 } }),
        sameBudi({ date: "2026-09-13", qty: 1000, done: 200, extra: { line_idx: 1 } }),
      ],
    });
    const display = displayHariIni([row], true);
    expect(display).toHaveLength(1);
    expect(display[0].displayWorkDate).toBe("2026-09-13");
  });

  test("5. SAME OPERATOR today complete + future unfinished", () => {
    const row = po({
      current_stage: 8,
      operators: [
        sameBudi({ date: TODAY, qty: 1000, done: 1000, extra: { line_idx: 0 } }),
        sameBudi({ date: "2026-09-15", qty: 1000, done: 0, extra: { line_idx: 1 } }),
      ],
    });
    expect(displayHariIni([row], false)).toHaveLength(0);
    const on = displayHariIni([row], true);
    expect(on).toHaveLength(1);
    expect(on[0].displayWorkDate).toBe(TODAY);
  });

  test("6. current_stage < 14 cannot force completed today row visible when Selesai OFF", () => {
    const row = po({
      current_stage: 8,
      operators: [sameBudi({ date: TODAY, qty: 1000, done: 1000 })],
    });
    expect(row.current_stage).toBeLessThan(14);
    expect(displayHariIni([row], false)).toHaveLength(0);
    expect(hariIni([row], { includeCompleted: false })).toHaveLength(0);
  });

  test("7. current_stage >= 14 cannot force historical completed work back into Hari Ini", () => {
    const row = po({
      current_stage: 14,
      status_final: "Terkirim",
      operators: [sameBudi({ date: "2026-09-12", qty: 1000, done: 1000 })],
    });
    expect(displayHariIni([row], true)).toHaveLength(0);
    expect(hariIni([row], { includeCompleted: true })).toHaveLength(0);
  });
});

describe("Hari Ini completion precedence over current_stage", () => {
  test("1. historical unfinished + PO stage 14 + Selesai OFF INCLUDE", () => {
    const row = po({
      current_stage: 14,
      status_final: "Terkirim",
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 600 })],
    });
    expect(hariIni([row], { includeCompleted: false })).toHaveLength(1);
    expect(displayHariIni([row], false).map((r) => r.displayWorkDate)).toEqual(["2026-09-12"]);
  });

  test("2. historical unfinished + PO stage 14 + Selesai ON INCLUDE", () => {
    const row = po({
      current_stage: 14,
      status_final: "Terkirim",
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 600 })],
    });
    expect(hariIni([row], { includeCompleted: true })).toHaveLength(1);
    expect(displayHariIni([row], true).map((r) => r.displayWorkDate)).toEqual(["2026-09-12"]);
  });

  test("3. today unfinished + PO stage 14 + Selesai OFF INCLUDE", () => {
    const row = po({
      current_stage: 14,
      status_final: "Terkirim",
      operators: [op({ name: "Budi", date: TODAY, qty: 1000, done: 500 })],
    });
    expect(hariIni([row], { includeCompleted: false })).toHaveLength(1);
    expect(displayHariIni([row], false).map((r) => r.displayWorkDate)).toEqual([TODAY]);
  });

  test("4. today complete + PO stage 8: Selesai OFF EXCLUDE, ON INCLUDE", () => {
    const row = po({
      current_stage: 8,
      operators: [op({ name: "Budi", date: TODAY, qty: 1000, done: 1000 })],
    });
    expect(displayHariIni([row], false)).toHaveLength(0);
    expect(displayHariIni([row], true)).toHaveLength(1);
  });

  test("5. today complete + PO stage 14: Selesai OFF EXCLUDE, ON INCLUDE", () => {
    const row = po({
      current_stage: 14,
      status_final: "Terkirim",
      operators: [op({ name: "Budi", date: TODAY, qty: 1000, done: 1000 })],
    });
    expect(displayHariIni([row], false)).toHaveLength(0);
    expect(displayHariIni([row], true)).toHaveLength(1);
  });

  test("6. historical complete + stage 8 or 14 stays EXCLUDE", () => {
    const stage8 = po({
      current_stage: 8,
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 1000 })],
    });
    const stage14 = po({
      id: "po-14",
      po_number: "PO-14",
      current_stage: 14,
      status_final: "Terkirim",
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 1000 })],
    });
    expect(displayHariIni([stage8], false)).toHaveLength(0);
    expect(displayHariIni([stage8], true)).toHaveLength(0);
    expect(displayHariIni([stage14], false)).toHaveLength(0);
    expect(displayHariIni([stage14], true)).toHaveLength(0);
  });

  test("7. Semua Pengerjaan still uses PO-level Selesai", () => {
    const unfinishedAtStage14 = po({
      current_stage: 14,
      status_final: "Terkirim",
      operators: [op({ name: "Budi", date: "2026-09-12", qty: 1000, done: 600 })],
    });
    const active = po({
      id: "po-active",
      po_number: "PO-ACTIVE",
      current_stage: 8,
      operators: [op({ name: "Budi", date: TODAY, qty: 1000, done: 100 })],
    });
    expect(applyProductionFilters([unfinishedAtStage14, active], FILTER_DEFAULTS).map((r) => r.po_number))
      .toEqual(["PO-1", "PO-ACTIVE"]);
    expect(applyProductionFilters([unfinishedAtStage14, active], {
      ...FILTER_DEFAULTS,
      includeCompleted: false,
    }).map((r) => r.po_number)).toEqual(["PO-ACTIVE"]);
    expect(hariIni([unfinishedAtStage14], { includeCompleted: false }).map((r) => r.po_number)).toEqual(["PO-1"]);
  });
});
