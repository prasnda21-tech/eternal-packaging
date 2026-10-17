import {
  FILTER_DEFAULTS,
  applyProductionFilters,
  computeProductionScheduleStatusTabCounts,
  productionFiltersAreActive,
  productionScheduleStatusTabOfRow,
  rowMatchesProductionScheduleStatusTab,
} from "./productionScheduleFilters";

function row(partial) {
  return {
    id: partial.id || partial.po_number,
    po_number: partial.po_number,
    customer: partial.customer || "C",
    outlet: partial.outlet || "",
    hold: !!partial.hold,
    cancelled: !!partial.cancelled,
    current_stage: partial.current_stage || 5,
    work_date: partial.work_date ?? null,
    operator_work_dates: partial.operator_work_dates || [],
    operators: partial.operators || [],
    urgent: !!partial.urgent,
  };
}

const SAMPLE = [
  row({ po_number: "A", work_date: null }), // unscheduled non-hold
  row({ po_number: "B", work_date: "2026-10-01" }), // scheduled non-hold
  row({ po_number: "C", hold: true, work_date: null }), // hold unscheduled
  row({ po_number: "D", hold: true, work_date: "2026-10-02" }), // hold scheduled
  row({ po_number: "E", work_date: null, operator_work_dates: [{ work_date: "2026-10-03" }] }), // scheduled via op dates
];

describe("production schedule status tabs E1 + C2", () => {
  test("E1 classification: HOLD carved out regardless of schedule", () => {
    expect(productionScheduleStatusTabOfRow(SAMPLE[0])).toBe("unscheduled");
    expect(productionScheduleStatusTabOfRow(SAMPLE[1])).toBe("scheduled");
    expect(productionScheduleStatusTabOfRow(SAMPLE[2])).toBe("hold");
    expect(productionScheduleStatusTabOfRow(SAMPLE[3])).toBe("hold");
    expect(productionScheduleStatusTabOfRow(SAMPLE[4])).toBe("scheduled");
  });

  test("Semua includes HOLD; exclusive tabs partition", () => {
    expect(SAMPLE.every((r) => rowMatchesProductionScheduleStatusTab(r, "all"))).toBe(true);
    const counts = computeProductionScheduleStatusTabCounts(SAMPLE);
    expect(counts).toEqual({ all: 5, unscheduled: 1, scheduled: 2, hold: 2 });
    expect(counts.unscheduled + counts.scheduled + counts.hold).toBe(counts.all);
  });

  test("applyProductionFilters respects scheduleStatusTab and combines with search", () => {
    const hold = applyProductionFilters(SAMPLE, { ...FILTER_DEFAULTS, scheduleStatusTab: "hold" });
    expect(hold.map((r) => r.po_number).sort()).toEqual(["C", "D"]);

    const belum = applyProductionFilters(SAMPLE, { ...FILTER_DEFAULTS, scheduleStatusTab: "unscheduled" });
    expect(belum.map((r) => r.po_number)).toEqual(["A"]);

    const dijadwalkan = applyProductionFilters(SAMPLE, { ...FILTER_DEFAULTS, scheduleStatusTab: "scheduled" });
    expect(dijadwalkan.map((r) => r.po_number).sort()).toEqual(["B", "E"]);

    const searched = applyProductionFilters(SAMPLE, {
      ...FILTER_DEFAULTS,
      scheduleStatusTab: "hold",
      q: "D",
    });
    expect(searched.map((r) => r.po_number)).toEqual(["D"]);
  });

  test("C2 counts ignore active status tab (use filter context without tab)", () => {
    const context = applyProductionFilters(SAMPLE, {
      ...FILTER_DEFAULTS,
      scheduleStatusTab: "all",
      q: "",
    });
    const counts = computeProductionScheduleStatusTabCounts(context);
    // Switching tab must not change C2 base
    const holdOnlyView = applyProductionFilters(SAMPLE, {
      ...FILTER_DEFAULTS,
      scheduleStatusTab: "hold",
    });
    expect(holdOnlyView).toHaveLength(2);
    expect(computeProductionScheduleStatusTabCounts(context)).toEqual(counts);
  });

  test("H1: status tab combines with holdOnly column filter without auto-clear", () => {
    // Tab = Dijadwalkan (non-hold) + holdOnly column → empty
    const combo = applyProductionFilters(SAMPLE, {
      ...FILTER_DEFAULTS,
      scheduleStatusTab: "scheduled",
      holdOnly: true,
    });
    expect(combo).toHaveLength(0);

    // Tab = HOLD + unscheduled workDateFilter → only hold without work date
    const holdUnsched = applyProductionFilters(SAMPLE, {
      ...FILTER_DEFAULTS,
      scheduleStatusTab: "hold",
      workDateFilter: "unscheduled",
    });
    expect(holdUnsched.map((r) => r.po_number)).toEqual(["C"]);
  });

  test("scheduleStatusTab marks filters active", () => {
    expect(productionFiltersAreActive({ ...FILTER_DEFAULTS, scheduleStatusTab: "hold" })).toBe(true);
    expect(productionFiltersAreActive(FILTER_DEFAULTS)).toBe(false);
  });
});
