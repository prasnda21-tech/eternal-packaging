import {
  MOBILE_WORKSPACES,
  MOBILE_NAV_ITEMS,
  OPS_ACTIVE_ROLE_IDS,
  activeMobileNav,
  deliveryScheduleShowsTable,
  loadingAvailability,
  loadingSkipsPacking,
  EXACT_SJ_REQUIRED_MESSAGE,
  matchLoadingSjFile,
  scheduleRowShipmentId,
  mobileLoadingScheduleHref,
  roleRequiresExactShipment,
  mobileScheduleWorkHref,
  mobileTaskHref,
  mobileWorkStatus,
  packingMetrics,
  permittedDeliveryScheduleTaskContexts,
  permittedMobileWorkspaces,
  permittedOpsActiveRoles,
  poCardPresentation,
  selectDeliveryScheduleTaskContext,
  selectMobileWorkspace,
  selectOpsActiveRole,
  taskSummaryFromList,
  visibleMobileNavItems,
} from "./mobileWorkspaces";
import { canSubmitConfirmDelivered } from "./deliveryProof";

const permSet = (...keys) => (p) => keys.includes(p);

describe("mobile workspace permissions", () => {
  test("helper.prepare / packing.update / loading.update are independent", () => {
    expect(permittedMobileWorkspaces(permSet("helper.prepare")).map((w) => w.id)).toEqual(["prep"]);
    expect(permittedMobileWorkspaces(permSet("packing.update")).map((w) => w.id)).toEqual(["packing"]);
    expect(permittedMobileWorkspaces(permSet("loading.update")).map((w) => w.id)).toEqual(["loading"]);
    expect(permittedMobileWorkspaces(permSet("helper.prepare", "packing.update", "loading.update")).map((w) => w.id))
      .toEqual(["prep", "packing", "loading"]);
  });

  test("screener / operator / delivery permissions", () => {
    expect(permittedMobileWorkspaces(permSet("screen.update")).map((w) => w.id)).toEqual(["screener"]);
    expect(permittedMobileWorkspaces(permSet("operator.update")).map((w) => w.id)).toEqual(["print"]);
    expect(permittedMobileWorkspaces(permSet("operator.sample_upload")).map((w) => w.id)).toEqual(["sample"]);
    expect(permittedMobileWorkspaces(permSet("operator.update", "operator.sample_upload")).map((w) => w.id))
      .toEqual(["print", "sample"]);
    expect(permittedMobileWorkspaces(permSet("delivery.update")).map((w) => w.id)).toEqual(["delivery"]);
  });

  test("unauthorized workspace query does not grant action", () => {
    const helper = permSet("helper.prepare", "packing.update");
    const result = selectMobileWorkspace(helper, "delivery");
    expect(result.unauthorized).toBe(true);
    expect(result.selected.id).toBe("prep");
    expect(result.permitted.some((w) => w.id === "delivery")).toBe(false);
  });

  test("deterministic first permitted workspace", () => {
    const driver = permSet("loading.update", "delivery.update");
    expect(selectMobileWorkspace(driver, null).selected.id).toBe("loading");
    expect(selectMobileWorkspace(driver, "delivery").selected.id).toBe("delivery");
    expect(selectMobileWorkspace(permSet(), "packing").selected).toBe(null);
  });

  test("catalog has no Semua workspace", () => {
    expect(MOBILE_WORKSPACES.some((w) => /semua/i.test(w.label) || w.id === "all")).toBe(false);
  });
});

describe("ops active role selector", () => {
  test("excludes sample and unauthorized roles", () => {
    const opts = permittedOpsActiveRoles(permSet(
      "helper.prepare",
      "packing.update",
      "operator.sample_upload",
      "screen.update",
    ));
    expect(opts.map((w) => w.id)).toEqual(["screener", "prep", "packing"]);
    expect(OPS_ACTIVE_ROLE_IDS).not.toContain("sample");
    expect(opts.some((w) => w.id === "sample")).toBe(false);
  });

  test("stored unauthorized role falls back to first permitted", () => {
    const helper = permSet("packing.update", "loading.update");
    const denied = selectOpsActiveRole(helper, "screener");
    expect(denied.unauthorized).toBe(true);
    expect(denied.selected.id).toBe("packing");
    expect(denied.permitted.map((w) => w.id)).toEqual(["packing", "loading"]);
  });
});

describe("Jadwal Pengiriman task-context selector", () => {
  test("CASE A — Loading + Pengiriman options only from eligibility", () => {
    const opts = permittedDeliveryScheduleTaskContexts(permSet("loading.update", "delivery.update"));
    expect(opts.map((w) => w.id)).toEqual(["loading", "delivery"]);
    expect(opts.map((w) => w.label)).toEqual(["Loading", "Pengiriman"]);
    expect(opts.some((w) => /semua/i.test(w.label))).toBe(false);
  });

  test("CASE B — delivery-only user does not see Loading", () => {
    const opts = permittedDeliveryScheduleTaskContexts(permSet("delivery.update"));
    expect(opts.map((w) => w.id)).toEqual(["delivery"]);
    const denied = selectDeliveryScheduleTaskContext(permSet("delivery.update"), "loading");
    expect(denied.unauthorized).toBe(true);
    expect(denied.selected.id).toBe("delivery");
    expect(denied.permitted.some((w) => w.id === "loading")).toBe(false);
  });

  test("default prefers Pengiriman when eligible; honors valid ?ws=", () => {
    const both = permSet("loading.update", "delivery.update");
    expect(selectDeliveryScheduleTaskContext(both, null).selected.id).toBe("delivery");
    expect(selectDeliveryScheduleTaskContext(both, "loading").selected.id).toBe("loading");
    expect(selectDeliveryScheduleTaskContext(both, "delivery").selected.id).toBe("delivery");
    expect(selectDeliveryScheduleTaskContext(permSet("loading.update"), null).selected.id).toBe("loading");
  });

  test("Pengiriman, packing, and Loading all use the Jadwal Pengiriman table", () => {
    expect(deliveryScheduleShowsTable("delivery")).toBe(true);
    expect(deliveryScheduleShowsTable("packing")).toBe(true);
    expect(deliveryScheduleShowsTable(null)).toBe(true);
    expect(deliveryScheduleShowsTable("loading")).toBe(true);
  });

  test("Loading selection lands on the canonical schedule route", () => {
    expect(mobileLoadingScheduleHref()).toBe("/m/delivery-schedule?ws=loading");
  });

  test("Loading and Pengiriman schedule rows keep shipment identity; packing href stays unchanged", () => {
    const loading = (p) => p === "loading.update";
    expect(mobileScheduleWorkHref("po001", loading, "delivery", "loading")).toBe("/m/orders/po001?ws=loading");
    expect(mobileScheduleWorkHref("po001", loading, "delivery", "loading", { shipment_id: "sh-1" }))
      .toBe("/m/orders/po001?ws=loading&shipment=sh-1");
    expect(mobileScheduleWorkHref("po001", loading, "delivery", "loading", { shipment_id: "sh-2" }))
      .toBe("/m/orders/po001?ws=loading&shipment=sh-2");
    expect(mobileScheduleWorkHref("o1", (p) => p === "packing.update", "delivery", "packing", { shipment_id: "sh-1" }))
      .toBe("/m/orders/o1?ws=packing");
    expect(mobileScheduleWorkHref("o1", (p) => p === "delivery.update", "delivery", "delivery", { shipment_id: "sh-1" }))
      .toBe("/m/orders/o1?ws=delivery&shipment=sh-1");
  });

  test("matchLoadingSjFile uses exact shipment identity and never falls back to first SJ", () => {
    const files = [
      { id: "sj-a", shipment_id: "sh-1", sj_number: "SJ001", sales_delivery_id: "acc-1" },
      { id: "sj-b", shipment_id: "sh-2", sj_number: "SJ001-2", number: "SJ001-2" },
    ];
    expect(matchLoadingSjFile(files, "sh-1").id).toBe("sj-a");
    expect(matchLoadingSjFile(files, "sh-2").id).toBe("sj-b");
    expect(matchLoadingSjFile(files, "sj-a").id).toBe("sj-a");
    expect(matchLoadingSjFile(files, "SJ001-2").id).toBe("sj-b");
    expect(matchLoadingSjFile(files, "acc-1").id).toBe("sj-a");
    expect(matchLoadingSjFile(files, null)).toBeNull();
    expect(matchLoadingSjFile(files, "")).toBeNull();
    expect(matchLoadingSjFile(files, "missing")).toBeNull();
    expect(matchLoadingSjFile(files, "stale-sj-old")).toBeNull();
    expect(scheduleRowShipmentId({})).toBe("");
    expect(scheduleRowShipmentId({ shipment_id: null })).toBe("");
    expect(scheduleRowShipmentId({ shipment_id: "   " })).toBe("");
    expect(scheduleRowShipmentId({ shipment_id: "demo-sj-hardfail-001" })).toBe("demo-sj-hardfail-001");
    expect(roleRequiresExactShipment("loading")).toBe(true);
    expect(roleRequiresExactShipment("delivery")).toBe(true);
    expect(roleRequiresExactShipment("packing")).toBe(false);
    expect(EXACT_SJ_REQUIRED_MESSAGE).toMatch(/Jadwal Pengiriman/);
  });

  test("screener/print permissions are not Jadwal Pengiriman task contexts", () => {
    expect(permittedDeliveryScheduleTaskContexts(permSet("screen.update", "operator.update"))).toEqual([]);
  });
});

describe("operator / packing / loading presentation", () => {
  test("operator status 0 / partial / done", () => {
    expect(mobileWorkStatus(0, 100).label).toBe("Belum Mulai");
    expect(mobileWorkStatus(40, 100).label).toBe("Proses");
    expect(mobileWorkStatus(100, 100).label).toBe("Selesai");
    expect(mobileWorkStatus(120, 100).label).toBe("Selesai");
  });

  test("packing metrics preserve line_idx identity", () => {
    const metrics = packingMetrics({}, [
      { line_idx: 0, name: "Cup A", packed: 10, remaining: 5, qty_po: 15, print_completed: 15 },
      { line_idx: 1, name: "Cup A", packed: 2, remaining: 8, qty_po: 10, print_completed: 10 },
    ]);
    expect(metrics.lines).toHaveLength(2);
    expect(metrics.lines[0].line_idx).toBe(0);
    expect(metrics.lines[1].line_idx).toBe(1);
    expect(metrics.lines[0].name).toBe("Cup A");
    expect(metrics.lines[1].name).toBe("Cup A");
    expect(metrics.packed).toBe(12);
  });

  test("loading blocked without packing or SJ", () => {
    const noPack = loadingAvailability({}, [{ line_idx: 0, packed: 0, loaded: 0, available: 0 }], [{ id: "sj1" }]);
    expect(noPack.blocked).toBe(true);
    expect(noPack.reason).toMatch(/Packing/i);

    const noSj = loadingAvailability({}, [{ line_idx: 0, packed: 10, loaded: 0, available: 10 }], []);
    expect(noSj.blocked).toBe(true);
    expect(noSj.reason).toMatch(/Surat Jalan/i);

    const ok = loadingAvailability({ surat_jalan_created: true }, [{ line_idx: 0, packed: 10, loaded: 2, available: 8 }], [{ id: "sj1" }]);
    expect(ok.blocked).toBe(false);
    expect(ok.packed).toBe(10);
    expect(ok.loaded).toBe(2);
    expect(ok.remaining).toBe(8);
  });

  test("CASE A printable packed 0 remains packing-blocked", () => {
    const avail = loadingAvailability({}, [{
      line_idx: 0,
      production_process: "print",
      packed: 0,
      loaded: 0,
      available: 0,
      physical_available: 0,
      qty_sj: 1000,
      qty_po: 1000,
    }], [{ id: "sj1" }]);
    expect(avail.blocked).toBe(true);
    expect(avail.reason).toBe("Loading tidak dapat dilakukan karena belum ada qty Packing.");
    expect(loadingSkipsPacking([{ production_process: "print", qty_sj: 1000, available: 0 }])).toBe(false);
  });

  test("CASE B printable packed > 0 stays unblocked", () => {
    const avail = loadingAvailability({}, [{
      line_idx: 0,
      production_process: "print",
      packed: 600,
      loaded: 200,
      available: 400,
      physical_available: 400,
      qty_sj: 1000,
    }], [{ id: "sj1" }]);
    expect(avail.blocked).toBe(false);
    expect(avail.reason).toBe("");
    expect(avail.remaining).toBe(400);
  });

  test("CASE C non-print packed 0 with remaining is not packing-blocked", () => {
    const lines = [{
      line_idx: 0,
      production_process: "non_print",
      packed: 0,
      loaded: 0,
      available: 1000,
      physical_available: 1000,
      qty_sj: 1000,
      qty_po: 1000,
    }];
    const avail = loadingAvailability({}, lines, [{ id: "sj1" }]);
    expect(avail.blocked).toBe(false);
    expect(avail.reason).not.toMatch(/Packing/i);
    expect(avail.packed).toBe(0);
    expect(avail.remaining).toBe(1000);
    expect(loadingSkipsPacking(lines)).toBe(true);
  });

  test("mixed print packed 0 does not treat non-print remaining as a packing-zero bypass of print identity", () => {
    const mixed = loadingAvailability({}, [
      {
        line_idx: 0,
        production_process: "print",
        packed: 0,
        loaded: 0,
        available: 0,
        physical_available: 0,
        qty_sj: 500,
        qty_po: 500,
      },
      {
        line_idx: 1,
        production_process: "non_print",
        packed: 0,
        loaded: 0,
        available: 300,
        physical_available: 300,
        qty_sj: 300,
        qty_po: 300,
      },
    ], [{ id: "sj1" }]);
    expect(mixed.blocked).toBe(false);
    expect(mixed.reason).not.toMatch(/qty Packing/i);
    expect(mixed.remaining).toBe(300);
    expect(loadingSkipsPacking([
      { production_process: "print", qty_sj: 500, available: 0 },
      { production_process: "non_print", qty_sj: 300, available: 300 },
    ])).toBe(false);
  });

  test("packed 0 without canonical non-print classification still fails safe", () => {
    const avail = loadingAvailability({}, [{
      line_idx: 0,
      packed: 0,
      loaded: 0,
      available: 1000,
      qty_po: 1000,
    }], [{ id: "sj1" }]);
    expect(avail.blocked).toBe(true);
    expect(avail.reason).toMatch(/qty Packing/i);
  });
});

describe("mobile cards and nav", () => {
  test("PO cards do not merge same-name lines", () => {
    const card = poCardPresentation({
      line_items: [
        { name: "Cup A", quantity: 100 },
        { name: "Cup A", quantity: 50 },
      ],
    });
    expect(card.lines).toHaveLength(2);
    expect(card.lines[0].line_idx).toBe(0);
    expect(card.lines[1].line_idx).toBe(1);
    expect(card.qty).toBe(null);
    expect(card.product).toMatch(/\+1 line/);
  });

  test("task href is presentational workspace only", () => {
    expect(mobileTaskHref("abc", "packing")).toBe("/m/orders/abc?ws=packing");
  });

  test("bottom nav active state", () => {
    expect(activeMobileNav("/m/tasks")).toBe("tasks");
    expect(activeMobileNav("/m/orders/123?ws=print")).toBe("orders");
    expect(activeMobileNav("/m/schedule")).toBe("schedule");
    expect(activeMobileNav("/m/delivery-schedule")).toBe("deliverySchedule");
    expect(activeMobileNav("/m/notifications")).toBe("notifications");
    expect(activeMobileNav("/m/profile")).toBe("profile");
    expect(activeMobileNav("/po/123")).toBe(null);
  });

  test("Jadwal Produksi and Jadwal Pengiriman nav are permission gated, not role-name gated", () => {
    expect(visibleMobileNavItems((p) => p === "schedule.view").map((i) => i.id))
      .toEqual(["tasks", "orders", "schedule", "deliverySchedule", "notifications", "profile"]);
    expect(visibleMobileNavItems((p) => p === "nav.production_schedule").map((i) => i.id))
      .toEqual(["tasks", "orders", "schedule", "notifications", "profile"]);
    expect(visibleMobileNavItems((p) => p === "nav.delivery_schedule").map((i) => i.id))
      .toEqual(["tasks", "orders", "deliverySchedule", "notifications", "profile"]);
    expect(visibleMobileNavItems((p) => p === "operator.update").map((i) => i.id))
      .toEqual(["tasks", "orders", "notifications", "profile"]);
    expect(visibleMobileNavItems((p) => p === "admin").map((i) => i.id))
      .toEqual(["tasks", "orders", "notifications", "profile"]);
    expect(visibleMobileNavItems((p) => p === "driver").map((i) => i.id))
      .toEqual(["tasks", "orders", "notifications", "profile"]);
  });

  test("Optimasi Rute PWA nav uses existing permission model, not DRIVER role name", () => {
    expect(visibleMobileNavItems((p) => p === "route_optimizer.view").map((i) => i.id))
      .toContain("optimasiRute");
    expect(visibleMobileNavItems((p) => p === "nav.route_optimizer").map((i) => i.id))
      .toContain("optimasiRute");
    expect(visibleMobileNavItems((p) => p === "route_optimizer.optimize").map((i) => i.id))
      .toContain("optimasiRute");
    expect(visibleMobileNavItems((p) => p === "DRIVER" || p === "driver").map((i) => i.id))
      .not.toContain("optimasiRute");
    expect(visibleMobileNavItems((p) => false).map((i) => i.id))
      .not.toContain("optimasiRute");
    const item = MOBILE_NAV_ITEMS.find((i) => i.id === "optimasiRute");
    expect(item.to).toBe("/optimasi-rute");
    expect(activeMobileNav("/optimasi-rute/iro-1")).toBe("optimasiRute");
  });

  test("task counts use shared date/progress predicates", () => {
    const now = new Date("2026-09-14T03:00:00.000Z");
    const summary = taskSummaryFromList([
      { work_date: "2026-09-14", status: "belum", completed_qty: 0 },
      { work_date: "2026-09-14", status: "sebagian", completed_qty: 1 },
      { work_date: "2026-09-14", status: "selesai", completed_qty: 2 },
      { work_date: "2026-09-10", status: "belum", completed_qty: 0 },
      { work_date: "2026-09-15", status: "belum", completed_qty: 0 },
      { work_date: "2026-09-15", status: "sebagian", completed_qty: 1 },
      { work_date: null, status: "sebagian", completed_qty: 2 },
    ], now);
    expect(summary.hariIni).toBe(3);
    expect(summary.aktif).toBe(3);
    expect(summary.proses).toBe(1);
    expect(summary.menunggu).toBe(2);
  });
});

describe("delivery CTA requirements stay authoritative", () => {
  test("photos + recipient + both signatures required", () => {
    const proofs = [{ id: "1" }, { id: "2" }];
    expect(canSubmitConfirmDelivered(proofs, "Budi", null, null, null, null)).toBe(false);
    expect(canSubmitConfirmDelivered(proofs, "Budi", "data:image/png;base64,aa", null, null, null)).toBe(false);
    expect(canSubmitConfirmDelivered(
      proofs,
      "Budi",
      "data:image/png;base64,aa",
      null,
      "data:image/png;base64,bb",
      null,
    )).toBe(true);
  });
});
