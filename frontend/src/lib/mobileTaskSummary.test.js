import {
  DEFAULT_TASK_SUMMARY_FILTER,
  filterTasksBySummary,
  hasTaskPartialProgress,
  isActiveTask,
  isInProgressTask,
  isTaskComplete,
  isTaskInCurrentWorkloadWindow,
  isTodayTask,
  isWaitingTask,
  matchesTaskSummaryFilter,
  taskCalendarKey,
  taskSummaryFromList,
  taskTodayKey,
} from "./mobileTaskSummary";

const NOW = new Date("2026-09-14T03:00:00.000Z"); // 10:00 WIB on 14/09/2026

function task(overrides) {
  return {
    task_id: "t1",
    work_date: "2026-09-14",
    status: "belum",
    completed_qty: 0,
    target_qty: 10,
    ...overrides,
  };
}

function expectAllFour(t, flags) {
  expect(isTodayTask(t, NOW)).toBe(flags.hariIni);
  expect(isActiveTask(t, NOW)).toBe(flags.aktif);
  expect(isInProgressTask(t, NOW)).toBe(flags.proses);
  expect(isWaitingTask(t, NOW)).toBe(flags.menunggu);
}

describe("mobileTaskSummary predicates", () => {
  test("today + waiting matches Hari Ini, Aktif, Menunggu — not Proses", () => {
    expectAllFour(task({ work_date: "2026-09-14", status: "belum", completed_qty: 0 }), {
      hariIni: true, aktif: true, proses: false, menunggu: true,
    });
  });

  test("today + partial matches Hari Ini, Aktif, Proses — not Menunggu", () => {
    expectAllFour(task({ work_date: "2026-09-14", status: "sebagian", completed_qty: 3 }), {
      hariIni: true, aktif: true, proses: true, menunggu: false,
    });
  });

  test("today + complete matches Hari Ini only", () => {
    expectAllFour(task({ work_date: "2026-09-14", status: "selesai", completed_qty: 10 }), {
      hariIni: true, aktif: false, proses: false, menunggu: false,
    });
  });

  test("yesterday unfinished waiting is Aktif + Menunggu, not Hari Ini", () => {
    expectAllFour(task({ work_date: "2026-09-10", status: "belum", completed_qty: 0 }), {
      hariIni: false, aktif: true, proses: false, menunggu: true,
    });
  });

  test("yesterday unfinished partial is Aktif + Proses, not Hari Ini", () => {
    expectAllFour(task({ work_date: "2026-09-10", status: "sebagian", completed_qty: 4 }), {
      hariIni: false, aktif: true, proses: true, menunggu: false,
    });
  });

  test("yesterday complete is excluded from all four filters", () => {
    expectAllFour(task({ work_date: "2026-09-10", status: "selesai", completed_qty: 10 }), {
      hariIni: false, aktif: false, proses: false, menunggu: false,
    });
  });

  test("tomorrow waiting is excluded from all four filters", () => {
    const t = task({ work_date: "2026-09-15", status: "belum", completed_qty: 0 });
    expect(isTaskInCurrentWorkloadWindow(t, NOW)).toBe(false);
    expectAllFour(t, { hariIni: false, aktif: false, proses: false, menunggu: false });
  });

  test("tomorrow partial is excluded from all four filters", () => {
    const t = task({ work_date: "2026-09-15", status: "sebagian", completed_qty: 1 });
    expect(hasTaskPartialProgress(t)).toBe(true);
    expect(isTaskInCurrentWorkloadWindow(t, NOW)).toBe(false);
    expectAllFour(t, { hariIni: false, aktif: false, proses: false, menunggu: false });
  });

  test("tomorrow complete is excluded from all four filters", () => {
    expectAllFour(task({ work_date: "2026-09-15", status: "selesai", completed_qty: 10 }), {
      hariIni: false, aktif: false, proses: false, menunggu: false,
    });
  });

  test("unscheduled waiting is excluded from all four filters", () => {
    const t = task({ work_date: null, status: "belum", completed_qty: 0 });
    expect(isTaskInCurrentWorkloadWindow(t, NOW)).toBe(false);
    expectAllFour(t, { hariIni: false, aktif: false, proses: false, menunggu: false });
  });

  test("unscheduled partial is excluded from all four filters", () => {
    const t = task({ work_date: null, status: "sebagian", completed_qty: 2 });
    expect(hasTaskPartialProgress(t)).toBe(true);
    expectAllFour(t, { hariIni: false, aktif: false, proses: false, menunggu: false });
  });

  test("WIB midnight is today — UTC calendar day must not shift the task", () => {
    const wibMidnight = new Date("2026-09-13T17:00:00.000Z");
    expect(taskTodayKey(wibMidnight)).toBe("2026-09-14");
    expect(taskCalendarKey("2026-09-14")).toBe("2026-09-14");
    expect(isTodayTask(task({ work_date: "2026-09-14" }), wibMidnight)).toBe(true);
    expect(isTodayTask(task({ work_date: "2026-09-13" }), wibMidnight)).toBe(false);
    expect(taskCalendarKey(new Date("2026-09-13T17:00:00.000Z"))).toBe("2026-09-14");
    expect(taskCalendarKey("2026-09-13T17:00:00.000Z")).toBe("2026-09-14");
    expect(isTaskInCurrentWorkloadWindow(task({ work_date: "2026-09-14" }), wibMidnight)).toBe(true);
    expect(isTaskInCurrentWorkloadWindow(task({ work_date: "2026-09-15" }), wibMidnight)).toBe(false);
  });

  test("delivery siap with photo progress is Proses, not Menunggu", () => {
    const t = task({
      division: "terkirim",
      status: "siap",
      completed_qty: 2,
      target_qty: 2,
    });
    expect(isInProgressTask(t, NOW)).toBe(true);
    expect(isWaitingTask(t, NOW)).toBe(false);
    expect(isTaskComplete(t)).toBe(false);
  });

  test("loading siap with zero completed_qty is Menunggu", () => {
    const t = task({ division: "loading", status: "siap", completed_qty: 0 });
    expect(isWaitingTask(t, NOW)).toBe(true);
    expect(isInProgressTask(t, NOW)).toBe(false);
  });

  test("missing completed_qty is not treated as progress 0 unless status is sebagian", () => {
    const waiting = task({ status: "belum", completed_qty: null });
    expect(isWaitingTask(waiting, NOW)).toBe(true);
    expect(isInProgressTask(waiting, NOW)).toBe(false);
    const partial = task({ status: "sebagian", completed_qty: null });
    expect(isInProgressTask(partial, NOW)).toBe(true);
    expect(isWaitingTask(partial, NOW)).toBe(false);
  });

  test("qty progress > 0 without status sebagian is still Proses", () => {
    const t = task({ status: "belum", completed_qty: 2 });
    expect(isInProgressTask(t, NOW)).toBe(true);
    expect(isWaitingTask(t, NOW)).toBe(false);
  });
});

describe("taskSummaryFromList shares predicates with the visible list", () => {
  const rows = [
    task({ task_id: "today-wait", work_date: "2026-09-14", status: "belum", completed_qty: 0 }),
    task({ task_id: "today-part", work_date: "2026-09-14", status: "sebagian", completed_qty: 3 }),
    task({ task_id: "today-done", work_date: "2026-09-14", status: "selesai", completed_qty: 10 }),
    task({ task_id: "old-wait", work_date: "2026-09-10", status: "belum", completed_qty: 0 }),
    task({ task_id: "old-part", work_date: "2026-09-10", status: "sebagian", completed_qty: 4 }),
    task({ task_id: "old-done", work_date: "2026-09-10", status: "selesai", completed_qty: 10 }),
    task({ task_id: "future-wait", work_date: "2026-09-15", status: "belum", completed_qty: 0 }),
    task({ task_id: "future-part", work_date: "2026-09-15", status: "sebagian", completed_qty: 1 }),
    task({ task_id: "unscheduled", work_date: null, status: "belum", completed_qty: 0 }),
  ];

  test("each card count equals filterTasksBySummary length", () => {
    const summary = taskSummaryFromList(rows, NOW);
    expect(summary.hariIni).toBe(filterTasksBySummary(rows, "hariIni", NOW).length);
    expect(summary.aktif).toBe(filterTasksBySummary(rows, "aktif", NOW).length);
    expect(summary.proses).toBe(filterTasksBySummary(rows, "proses", NOW).length);
    expect(summary.menunggu).toBe(filterTasksBySummary(rows, "menunggu", NOW).length);
    expect(summary).toEqual({ hariIni: 3, aktif: 4, proses: 2, menunggu: 2 });
    expect(DEFAULT_TASK_SUMMARY_FILTER).toBe("hariIni");
    expect(filterTasksBySummary(rows, "hariIni", NOW).map((t) => t.task_id))
      .toEqual(["today-wait", "today-part", "today-done"]);
    expect(filterTasksBySummary(rows, "aktif", NOW).map((t) => t.task_id))
      .toEqual(["today-wait", "today-part", "old-wait", "old-part"]);
    expect(filterTasksBySummary(rows, "proses", NOW).map((t) => t.task_id))
      .toEqual(["today-part", "old-part"]);
    expect(filterTasksBySummary(rows, "menunggu", NOW).map((t) => t.task_id))
      .toEqual(["today-wait", "old-wait"]);
  });

  test("future and unscheduled tasks increment none of the four counts", () => {
    const extras = rows.filter((t) => ["future-wait", "future-part", "unscheduled"].includes(t.task_id));
    expect(taskSummaryFromList(extras, NOW)).toEqual({
      hariIni: 0, aktif: 0, proses: 0, menunggu: 0,
    });
  });

  test("matchesTaskSummaryFilter is the single gate for counts and list", () => {
    const t = rows[0];
    expect(matchesTaskSummaryFilter(t, "hariIni", NOW)).toBe(isTodayTask(t, NOW));
    expect(matchesTaskSummaryFilter(t, "aktif", NOW)).toBe(isActiveTask(t, NOW));
    expect(matchesTaskSummaryFilter(t, "proses", NOW)).toBe(isInProgressTask(t, NOW));
    expect(matchesTaskSummaryFilter(t, "menunggu", NOW)).toBe(isWaitingTask(t, NOW));
  });
});
