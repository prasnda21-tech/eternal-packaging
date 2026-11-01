import {
  FILTER_DEFAULTS,
  PRODUCTION_SCHEDULE_SCOPE_ALL,
  PRODUCTION_SCHEDULE_SCOPE_DEFAULT,
  applyProductionFilters,
  normalizeProductionScheduleScope,
  productionMonthKey,
  productionScheduleHistoryOverrideActive,
  rowMatchesDefaultProductionScheduleScope,
  shouldApplyDefaultProductionScheduleScope,
} from "./productionScheduleFilters";

const TODAY = "2026-11-15";

function op({ name, date, qty, done, extra = {} }) {
  return {
    split_id: `s-${name}-${date}-${extra.line_idx ?? 0}`,
    operator_email: `${name}@x.com`,
    operator_user_id: `u-${name}`,
    operator_name: name,
    quantity: qty,
    qty_completed: done,
    work_date: date,
    line_printable: true,
    line_idx: extra.line_idx ?? 0,
    line_id: extra.line_id || `lid-${name}-${extra.line_idx ?? 0}`,
    line_name: extra.line_name || "Cup",
    ...extra,
  };
}

function po(overrides = {}) {
  return {
    id: "po-1",
    po_number: "PO-1",
    customer: "PT Contoh",
    deadline: "2026-11-20",
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

function scoped(rows, extras = {}) {
  return applyProductionFilters(rows, {
    ...FILTER_DEFAULTS,
    today: TODAY,
    ...extras,
  });
}

describe("production schedule default month scope", () => {
  test("month key from WIB date", () => {
    expect(productionMonthKey("2026-11-15")).toBe("2026-11");
    expect(normalizeProductionScheduleScope(undefined)).toBe(PRODUCTION_SCHEDULE_SCOPE_DEFAULT);
    expect(normalizeProductionScheduleScope("all")).toBe(PRODUCTION_SCHEDULE_SCOPE_ALL);
    expect(normalizeProductionScheduleScope("legacy-missing")).toBe(PRODUCTION_SCHEDULE_SCOPE_DEFAULT);
  });

  test("1. current month 0/50/100% all visible", () => {
    const rows = [
      po({
        id: "c0",
        po_number: "CUR-0",
        work_date: "2026-11-01",
        operators: [op({ name: "A", date: "2026-11-01", qty: 1000, done: 0 })],
      }),
      po({
        id: "c50",
        po_number: "CUR-50",
        work_date: "2026-11-10",
        operators: [op({ name: "A", date: "2026-11-10", qty: 1000, done: 500 })],
      }),
      po({
        id: "c100",
        po_number: "CUR-100",
        work_date: "2026-11-12",
        print_completed_qty: 1000,
        operators: [op({ name: "A", date: "2026-11-12", qty: 1000, done: 1000 })],
      }),
    ];
    expect(scoped(rows).map((r) => r.po_number).sort()).toEqual(["CUR-0", "CUR-100", "CUR-50"]);
  });

  test("2. prior month partial remains visible", () => {
    const row = po({
      id: "old-partial",
      po_number: "OLD-P",
      work_date: "2026-10-05",
      operators: [op({ name: "A", date: "2026-10-05", qty: 1000, done: 400 })],
    });
    expect(rowMatchesDefaultProductionScheduleScope(row, TODAY)).toBe(true);
    expect(scoped([row])).toHaveLength(1);
  });

  test("3. prior month proven 100% hidden in default", () => {
    const row = po({
      id: "old-done",
      po_number: "OLD-D",
      work_date: "2026-09-01",
      print_completed_qty: 1000,
      operators: [op({ name: "A", date: "2026-09-01", qty: 1000, done: 1000 })],
    });
    expect(rowMatchesDefaultProductionScheduleScope(row, TODAY)).toBe(false);
    expect(scoped([row])).toHaveLength(0);
  });

  test("4. active without schedule qty is not treated as done", () => {
    const row = po({
      id: "no-qty",
      po_number: "NO-Q",
      work_date: "2026-08-01",
      operators: [{
        split_id: "s-empty",
        operator_name: "A",
        work_date: "2026-08-01",
        line_printable: true,
        line_idx: 0,
        line_id: "lid-a",
        // no quantity → work unit dropped; date still present → unknown ≠ 100%
      }],
    });
    expect(rowMatchesDefaultProductionScheduleScope(row, TODAY)).toBe(true);
  });

  test("4b. unscheduled aktif included; stage-selesai unscheduled excluded from scope", () => {
    const aktif = po({ id: "unsched", po_number: "U1", work_date: null, operators: [] });
    const selesai = po({
      id: "done-stage",
      po_number: "U2",
      work_date: null,
      operators: [],
      current_stage: 14,
      status_final: "Terkirim",
    });
    expect(rowMatchesDefaultProductionScheduleScope(aktif, TODAY)).toBe(true);
    expect(rowMatchesDefaultProductionScheduleScope(selesai, TODAY)).toBe(false);
  });

  test("5. cancelled follows includeCancelled", () => {
    const row = po({
      id: "cx",
      po_number: "CX",
      cancelled: true,
      work_date: "2026-11-02",
      operators: [op({ name: "A", date: "2026-11-02", qty: 100, done: 0 })],
    });
    expect(scoped([row], { includeCancelled: false })).toHaveLength(0);
    expect(scoped([row], { includeCancelled: true })).toHaveLength(1);
  });

  test("6. cross-month unfinished keeps PO", () => {
    const row = po({
      id: "cross",
      po_number: "CROSS",
      work_date: "2026-10-01",
      operators: [
        op({ name: "A", date: "2026-10-01", qty: 1000, done: 1000, extra: { line_idx: 0 } }),
        op({ name: "B", date: "2026-09-15", qty: 800, done: 100, extra: { line_idx: 1, line_id: "lid-b" } }),
      ],
    });
    expect(rowMatchesDefaultProductionScheduleScope(row, TODAY)).toBe(true);
  });

  test("7. future-only hidden in default; visible via histori / scope all", () => {
    const row = po({
      id: "fut",
      po_number: "FUT",
      work_date: "2026-12-01",
      operators: [op({ name: "A", date: "2026-12-01", qty: 1000, done: 0 })],
    });
    expect(rowMatchesDefaultProductionScheduleScope(row, TODAY)).toBe(false);
    expect(scoped([row])).toHaveLength(0);
    expect(scoped([row], { scheduleScope: PRODUCTION_SCHEDULE_SCOPE_ALL })).toHaveLength(1);
    expect(scoped([row], { workDateFrom: "2026-12-01", workDateTo: "2026-12-31" })).toHaveLength(1);
  });

  test("8. histori date range reveals prior-month 100%", () => {
    const row = po({
      id: "hist",
      po_number: "HIST",
      work_date: "2026-08-01",
      print_completed_qty: 1000,
      operators: [op({ name: "A", date: "2026-08-01", qty: 1000, done: 1000 })],
    });
    expect(scoped([row])).toHaveLength(0);
    expect(scoped([row], { workDateFrom: "2026-08-01", workDateTo: "2026-08-31" })).toHaveLength(1);
    expect(scoped([row], { scheduleScope: PRODUCTION_SCHEDULE_SCOPE_ALL })).toHaveLength(1);
  });

  test("history override and shouldApply helpers", () => {
    expect(productionScheduleHistoryOverrideActive({ workDateFrom: "2026-01-01" })).toBe(true);
    expect(productionScheduleHistoryOverrideActive({ workDateFilter: "hari_ini" })).toBe(true);
    expect(productionScheduleHistoryOverrideActive({})).toBe(false);
    expect(shouldApplyDefaultProductionScheduleScope({
      scheduleScope: PRODUCTION_SCHEDULE_SCOPE_ALL,
    })).toBe(false);
    expect(shouldApplyDefaultProductionScheduleScope({
      scheduleScope: PRODUCTION_SCHEDULE_SCOPE_DEFAULT,
    })).toBe(true);
  });
});
