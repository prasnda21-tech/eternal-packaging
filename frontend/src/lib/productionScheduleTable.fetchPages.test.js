import {
  fetchProductionDashboardPages,
  fetchProductionDashboardRefresh,
  productionDashboardRequestParams,
} from "./productionScheduleTable";
import { FILTER_DEFAULTS } from "./productionScheduleFilters";

describe("fetchProductionDashboardPages", () => {
  test("merges cursor pages without duplicates and marks incomplete when stopped early", async () => {
    const api = {
      productionDashboard: jest.fn()
        .mockResolvedValueOnce({
          rows: [{ id: "a" }, { id: "b" }],
          next_cursor: "b",
          scope_complete: false,
          completeness: "incomplete",
          matched_count: 3,
          read_limit: 2,
          schedule_scope: "default",
        })
        .mockResolvedValueOnce({
          rows: [{ id: "b" }, { id: "c" }],
          next_cursor: null,
          scope_complete: true,
          completeness: "incomplete",
          matched_count: 3,
          read_limit: 2,
          schedule_scope: "default",
        }),
    };
    const merged = await fetchProductionDashboardPages(api, productionDashboardRequestParams(FILTER_DEFAULTS));
    expect(merged.rows.map((r) => r.id).sort()).toEqual(["a", "b", "c"]);
    expect(merged.scope_complete).toBe(true);
    expect(merged.completeness).toBe("complete");
    expect(merged.pages_fetched).toBe(2);
    expect(api.productionDashboard).toHaveBeenCalledTimes(2);
    expect(api.productionDashboard.mock.calls[1][0].cursor).toBe("b");
  });

  test("does not claim complete when page cap stops with remaining cursor", async () => {
    const api = {
      productionDashboard: jest.fn().mockResolvedValue({
        rows: [{ id: "x" }],
        next_cursor: "x",
        scope_complete: false,
        completeness: "incomplete",
        schedule_scope: "all",
      }),
    };
    const merged = await fetchProductionDashboardPages(api, { schedule_scope: "all" }, { maxPages: 1 });
    expect(merged.completeness).toBe("incomplete");
    expect(merged.scope_complete).toBe(false);
    expect(merged.next_cursor).toBe("x");
  });

  test("passes server search and histori date params", () => {
    expect(productionDashboardRequestParams({
      ...FILTER_DEFAULTS,
      q: "Kopi",
      workDateFrom: "2026-01-01",
      workDateTo: "2026-01-31",
      dateFrom: "2026-02-01",
      dateTo: "2026-02-28",
    })).toEqual({
      include_cancelled: false,
      include_completed: true,
      view: "summary",
      schedule_scope: "all",
      q: "Kopi",
      work_date_from: "2026-01-01",
      work_date_to: "2026-01-31",
      deadline_from: "2026-02-01",
      deadline_to: "2026-02-28",
    });
  });
});

function printRow(id, workDate, qty, done, extra = {}) {
  return {
    id,
    work_date: workDate,
    operators: [{ quantity: qty, qty_completed: done, work_date: workDate, line_idx: 0, line_printable: true }],
    line_items: [{ production_process: "print" }],
    current_stage: 8,
    ...extra,
  };
}

describe("fetchProductionDashboardRefresh", () => {
  test("head discovery + id reconcile drops prior-month 100% outside page 1", async () => {
    const api = {
      productionDashboard: jest.fn()
        .mockResolvedValueOnce({
          rows: [printRow("new", "2026-11-20", 10, 0)],
          refresh: true,
          schedule_scope: "default",
        })
        .mockImplementationOnce(async (params) => {
          expect(params.ids).toContain("old-upd");
          expect(params.ids).toContain("cached-unfin");
          expect(params.ids).toContain("cur-month");
          return {
            mode: "reconcile",
            rows: [
              printRow("cached-unfin", "2026-08-01", 50, 10),
              printRow("cur-month", "2026-11-05", 80, 80),
              printRow("old-upd", "2026-09-01", 100, 100),
            ],
            requested_ids: String(params.ids).split(","),
            found_ids: ["cached-unfin", "cur-month", "old-upd"],
            missing_ids: [],
          };
        }),
    };

    const prev = [
      printRow("cached-unfin", "2026-08-01", 50, 10),
      printRow("old-upd", "2026-09-01", 100, 40),
      printRow("cur-month", "2026-11-05", 80, 40),
    ];
    const merged = await fetchProductionDashboardRefresh(api, { schedule_scope: "default" }, {
      prevRows: prev,
      today: "2026-11-15",
      priorCompleteness: "complete",
      priorScopeComplete: true,
    });
    const ids = merged.rows.map((r) => r.id).sort();
    expect(ids).toContain("cached-unfin");
    expect(ids).toContain("new");
    expect(ids).toContain("cur-month"); // current month 100% stays
    expect(ids).not.toContain("old-upd"); // prior month now 100% → drop
    expect(merged.requests_this_cycle).toBe(2);
    expect(merged.completeness).toBe("complete");
    expect(api.productionDashboard.mock.calls[0][0]).toEqual({ schedule_scope: "default", refresh: true });
  });

  test("explicit missing_ids removes cancelled/inaccessible without wiping others", async () => {
    const api = {
      productionDashboard: jest.fn()
        .mockResolvedValueOnce({
          rows: [printRow("keep-head", "2026-11-01", 10, 0)],
          refresh: true,
          schedule_scope: "default",
        })
        .mockResolvedValueOnce({
          mode: "reconcile",
          rows: [printRow("keep-old", "2026-08-01", 50, 10)],
          requested_ids: ["gone", "keep-old"],
          found_ids: ["keep-old"],
          missing_ids: ["gone"],
        }),
    };
    const merged = await fetchProductionDashboardRefresh(api, { schedule_scope: "default" }, {
      prevRows: [
        printRow("gone", "2026-08-01", 50, 10),
        printRow("keep-old", "2026-08-01", 50, 10),
      ],
      today: "2026-11-15",
      priorCompleteness: "complete",
      priorScopeComplete: true,
    });
    const ids = merged.rows.map((r) => r.id).sort();
    expect(ids).toEqual(["keep-head", "keep-old"]);
    expect(merged.missing_ids).toEqual(["gone"]);
  });

  test("partial reconcile failure keeps cached rows and marks stale incomplete", async () => {
    const api = {
      productionDashboard: jest.fn()
        .mockResolvedValueOnce({
          rows: [printRow("head", "2026-11-01", 10, 0)],
          refresh: true,
          schedule_scope: "default",
        })
        .mockRejectedValueOnce(new Error("network")),
    };
    const merged = await fetchProductionDashboardRefresh(api, { schedule_scope: "default" }, {
      prevRows: [printRow("cached-unfin", "2026-08-01", 50, 10)],
      today: "2026-11-15",
      priorCompleteness: "complete",
      priorScopeComplete: true,
    });
    expect(merged.rows.map((r) => r.id).sort()).toEqual(["cached-unfin", "head"]);
    expect(merged.reconcile_stale).toBe(true);
    expect(merged.completeness).toBe("incomplete");
    expect(merged.scope_complete).toBe(false);
  });

  test("over-budget cache marks stale and rotates offset without full staged fetch", async () => {
    const prev = [];
    for (let i = 0; i < 5; i += 1) {
      prev.push(printRow(`old-${i}`, "2026-08-01", 50, 10));
    }
    const api = {
      productionDashboard: jest.fn()
        .mockResolvedValueOnce({ rows: [], refresh: true, schedule_scope: "default" })
        .mockImplementation(async (params) => ({
          mode: "reconcile",
          rows: String(params.ids).split(",").map((id) => printRow(id, "2026-08-01", 50, 10)),
          requested_ids: String(params.ids).split(","),
          found_ids: String(params.ids).split(","),
          missing_ids: [],
        })),
    };
    const merged = await fetchProductionDashboardRefresh(api, { schedule_scope: "default" }, {
      prevRows: prev,
      today: "2026-11-15",
      priorCompleteness: "complete",
      priorScopeComplete: true,
      reconcileIdsPerRequest: 2,
      reconcileMaxRequests: 1,
      reconcileOffset: 0,
    });
    expect(merged.requests_this_cycle).toBe(2); // head + 1 reconcile batch
    expect(merged.reconcile_stale).toBe(true);
    expect(merged.completeness).toBe("incomplete");
    expect(merged.reconcile_offset).toBe(2);
    expect(api.productionDashboard.mock.calls.length).toBe(2);
  });
});
