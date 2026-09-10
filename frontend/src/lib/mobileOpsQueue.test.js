import {
  OPS_QUICK_FILTERS,
  deliveryQuickMatch,
  productionQuickMatch,
  deliveryQueueRank,
  productionQueueRank,
  sortByOpsQueue,
  qtyProgressText,
  packingProgressText,
  helperProgressText,
  screenProgressText,
} from "./mobileOpsQueue";
import { preferredMobileWorkspaceForTable, mobileScheduleWorkHref } from "./mobileWorkspaces";

describe("mobileOpsQueue", () => {
  test("quick filter keys cover required ops chips", () => {
    expect(OPS_QUICK_FILTERS.map((f) => f.key)).toEqual([
      "hari_ini",
      "besok",
      "terlambat",
      "belum_dijadwalkan",
      "minggu_ini",
      "semua_aktif",
    ]);
  });

  test("production quick filters use work_date semantics", () => {
    const today = new Date("2026-09-10T12:00:00");
    const todayRow = { work_date: "2026-09-10", current_stage: 8 };
    const tomorrowRow = { work_date: "2026-09-11", current_stage: 8 };
    const lateRow = { work_date: "2026-09-01", current_stage: 8 };
    const noneRow = { current_stage: 8 };
    expect(productionQuickMatch(todayRow, "hari_ini", today)).toBe(true);
    expect(productionQuickMatch(tomorrowRow, "besok", today)).toBe(true);
    expect(productionQuickMatch(lateRow, "terlambat", today)).toBe(true);
    expect(productionQuickMatch(noneRow, "belum_dijadwalkan", today)).toBe(true);
    expect(productionQuickMatch({ ...todayRow, current_stage: 14 }, "semua_aktif", today)).toBe(false);
  });

  test("delivery quick filters align with schedule status/date", () => {
    const today = new Date("2026-09-10T12:00:00");
    expect(deliveryQuickMatch({ status: "hari_ini", effective_delivery_date: "2026-09-10" }, "hari_ini", today)).toBe(true);
    expect(deliveryQuickMatch({ status: "terjadwal", effective_delivery_date: "2026-09-11" }, "besok", today)).toBe(true);
    expect(deliveryQuickMatch({ status: "terlambat", effective_delivery_date: "2026-09-01" }, "terlambat", today)).toBe(true);
    expect(deliveryQuickMatch({ status: "belum_dijadwalkan" }, "belum_dijadwalkan", today)).toBe(true);
    expect(deliveryQuickMatch({ status: "terkirim", effective_delivery_date: "2026-09-10" }, "semua_aktif", today)).toBe(false);
  });

  test("queue order is late → today → tomorrow → next → unscheduled", () => {
    const today = new Date("2026-09-10T12:00:00");
    const rows = [
      { id: "u", status: "belum_dijadwalkan" },
      { id: "n", status: "terjadwal", effective_delivery_date: "2026-09-20" },
      { id: "t", status: "hari_ini", effective_delivery_date: "2026-09-10" },
      { id: "l", status: "terlambat", effective_delivery_date: "2026-09-01" },
      { id: "b", status: "terjadwal", effective_delivery_date: "2026-09-11" },
    ];
    expect(sortByOpsQueue(rows, deliveryQueueRank, today).map((r) => r.id)).toEqual(["l", "t", "b", "n", "u"]);
    expect(productionQueueRank({ work_date: "2026-09-01" }, today).bucket).toBe(10);
  });

  test("progress text uses id-ID thousands", () => {
    expect(qtyProgressText(3000, 5000)).toBe("3.000 / 5.000");
    expect(helperProgressText({ helper_prepared: 3000, helper_required: 5000 })).toBe("3.000 / 5.000");
    expect(screenProgressText({ screen_qty: 2000, print_qty_total: 5000 })).toBe("2.000");
    expect(screenProgressText({ screen_qty: 3000 })).toBe("3.000");
    expect(packingProgressText({ qty_packed: 3000, qty_total: 5000 })).toBe("3.000 / 5.000");
  });
});

describe("permission-based schedule row routing", () => {
  test("does not use role name; prefers workspace by permission", () => {
    expect(preferredMobileWorkspaceForTable((p) => p === "screen.update", "production")?.id).toBe("screener");
    expect(preferredMobileWorkspaceForTable((p) => p === "helper.prepare", "production")?.id).toBe("prep");
    expect(preferredMobileWorkspaceForTable((p) => p === "operator.update", "production")?.id).toBe("print");
    expect(preferredMobileWorkspaceForTable((p) => p === "packing.update", "delivery")?.id).toBe("packing");
    expect(preferredMobileWorkspaceForTable((p) => p === "delivery.update", "delivery")?.id).toBe("delivery");
    expect(mobileScheduleWorkHref("o1", (p) => p === "packing.update", "delivery")).toBe("/m/orders/o1?ws=packing");
    expect(mobileScheduleWorkHref("o1", (p) => p === "driver", "delivery")).toBe("/m/orders/o1");
  });
});
