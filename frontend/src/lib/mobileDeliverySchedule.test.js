import {
  MOBILE_DELIVERY_SCHEDULE_COLS,
  MOBILE_DELIVERY_SCHEDULE_EDIT_PERM,
  MOBILE_DELIVERY_SCHEDULE_PATH,
  MOBILE_DELIVERY_SCHEDULE_VIEW_PERMS,
  canEditMobileDeliveryScheduleLocation,
  canViewMobileDeliverySchedule,
  deliveryRowWithProductionStatus,
  groupSjItemLines,
  indexProductionStatusByOrderId,
  mobileDeliveryOrderHref,
  mobileDeliveryWorkHref,
  sjItemLineKey,
  sjItemLinesFromRow,
  withProductionRoleStatus,
} from "./mobileDeliverySchedule";
import {
  DELIVERY_SCHEDULE_STATUS_OPTIONS,
  EMPTY_DELIVERY_FILTERS,
  countActiveDeliveryFilters,
  matchesDeliveryScheduleFilters,
} from "./deliverySchedule";
import {
  PRODUCTION_SHEET_ZOOM_DEFAULT,
  PRODUCTION_SHEET_ZOOM_MAX,
  PRODUCTION_SHEET_ZOOM_MIN,
  beginSheetPinch,
  clampProductionSheetZoom,
  createSheetPinchGesture,
  endSheetPinch,
  updateSheetPinch,
} from "./productionSheetZoom";
import { NAV_KEYS } from "./navAccess";

describe("mobileDeliverySchedule access", () => {
  test("view uses same permission set as Web, not role name", () => {
    expect(MOBILE_DELIVERY_SCHEDULE_VIEW_PERMS).toEqual([
      NAV_KEYS.deliverySchedule,
      "delivery.schedule.view",
      "schedule.view",
    ]);
    expect(canViewMobileDeliverySchedule((p) => p === "schedule.view")).toBe(true);
    expect(canViewMobileDeliverySchedule((p) => p === "nav.delivery_schedule")).toBe(true);
    expect(canViewMobileDeliverySchedule((p) => p === "delivery.schedule.view")).toBe(true);
    expect(canViewMobileDeliverySchedule((p) => p === "driver")).toBe(false);
    expect(canViewMobileDeliverySchedule((p) => p === "delivery.update")).toBe(false);
  });

  test("edit is delivery.location.manage only — never roleName === driver", () => {
    expect(MOBILE_DELIVERY_SCHEDULE_EDIT_PERM).toBe("delivery.location.manage");
    expect(canEditMobileDeliveryScheduleLocation((p) => p === "delivery.location.manage")).toBe(true);
    expect(canEditMobileDeliveryScheduleLocation((p) => p === "schedule.view")).toBe(false);
    expect(canEditMobileDeliveryScheduleLocation((p) => p === "driver")).toBe(false);
    expect(canEditMobileDeliveryScheduleLocation((p) => p === "delivery.update")).toBe(false);
  });

  test("view-only can view without edit", () => {
    const hasPerm = (p) => p === "schedule.view";
    expect(canViewMobileDeliverySchedule(hasPerm)).toBe(true);
    expect(canEditMobileDeliveryScheduleLocation(hasPerm)).toBe(false);
  });

  test("route and action hrefs follow mobile convention", () => {
    expect(MOBILE_DELIVERY_SCHEDULE_PATH).toBe("/m/delivery-schedule");
    expect(mobileDeliveryOrderHref("abc")).toBe("/m/orders/abc");
    expect(mobileDeliveryWorkHref("abc")).toBe("/m/orders/abc?ws=delivery");
  });

  test("table columns match compact ops hierarchy", () => {
    const keys = MOBILE_DELIVERY_SCHEDULE_COLS.map((c) => c.key);
    expect(keys).toEqual([
      "prio",
      "effective_delivery_date",
      "customer",
      "item_line",
      "qty",
      "cup",
      "screen",
      "prod",
      "packing",
      "loading",
      "shipped",
    ]);
    expect(MOBILE_DELIVERY_SCHEDULE_COLS.map((c) => c.label)).toEqual([
      "Prioritas",
      "Tgl Kirim",
      "Customer / No PO",
      "Item Line",
      "Qty Total",
      "Cup",
      "Screen",
      "Prod",
      "Pack",
      "Load",
      "Kirim",
    ]);
    expect(keys).not.toContain("driver");
    expect(keys).not.toContain("status");
    expect(keys).not.toContain("actions");
    expect(keys).not.toContain("zona");
    const customerIdx = keys.indexOf("customer");
    expect(keys[customerIdx + 1]).toBe("item_line");
    expect(MOBILE_DELIVERY_SCHEDULE_COLS.some((c) => c.label === "Deadline")).toBe(false);
    expect(MOBILE_DELIVERY_SCHEDULE_COLS.some((c) => c.label === "Jadwal Manual")).toBe(false);
    expect(MOBILE_DELIVERY_SCHEDULE_COLS.some((c) => c.label === "Tgl Kirim")).toBe(true);
  });
});

describe("mobile delivery schedule filter parity with Web", () => {
  const webFilterKeys = Object.keys(EMPTY_DELIVERY_FILTERS);

  test("every Web filter key is present with same defaults", () => {
    expect(webFilterKeys.sort()).toEqual([
      "customer",
      "dapatDikirim",
      "dateFrom",
      "dateTo",
      "driver",
      "po",
      "status",
      "terkirim",
    ].sort());
    expect(EMPTY_DELIVERY_FILTERS.status).toBe("semua");
    expect(EMPTY_DELIVERY_FILTERS.terkirim).toBe("all");
    expect(EMPTY_DELIVERY_FILTERS.dapatDikirim).toBe("all");
    expect(DELIVERY_SCHEDULE_STATUS_OPTIONS.map((o) => o.key)).toEqual([
      "semua",
      "belum_dijadwalkan",
      "terjadwal",
      "hari_ini",
      "terlambat",
      "terkirim",
    ]);
  });

  test("reset restores EMPTY_DELIVERY_FILTERS and count is zero", () => {
    expect(countActiveDeliveryFilters(EMPTY_DELIVERY_FILTERS)).toBe(0);
    expect(countActiveDeliveryFilters({
      ...EMPTY_DELIVERY_FILTERS,
      po: "PO-1",
      driver: "Budi",
      status: "terjadwal",
    })).toBe(3);
  });

  test("filter semantics match Web helper", () => {
    const row = {
      order_number: "PO-100",
      customer_name: "PT Alpha",
      status: "terjadwal",
      effective_delivery_date: "2026-09-10",
      driver_name: "Budi",
      percent_delivered: 50,
      percent_ready_to_ship: 100,
    };
    expect(matchesDeliveryScheduleFilters(row, { ...EMPTY_DELIVERY_FILTERS, po: "po-1" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { ...EMPTY_DELIVERY_FILTERS, driver: "budi" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { ...EMPTY_DELIVERY_FILTERS, status: "terlambat" })).toBe(false);
    expect(matchesDeliveryScheduleFilters(row, { ...EMPTY_DELIVERY_FILTERS, terkirim: "partial" })).toBe(true);
    expect(matchesDeliveryScheduleFilters(row, { ...EMPTY_DELIVERY_FILTERS, dapatDikirim: "full" })).toBe(true);
  });
});

describe("delivery schedule pinch/zoom reuses production sheet helpers", () => {
  test("default/min/max match Jadwal Produksi", () => {
    expect(PRODUCTION_SHEET_ZOOM_DEFAULT).toBe(0.85);
    expect(PRODUCTION_SHEET_ZOOM_MIN).toBe(0.45);
    expect(PRODUCTION_SHEET_ZOOM_MAX).toBe(1.5);
    expect(clampProductionSheetZoom(0)).toBe(PRODUCTION_SHEET_ZOOM_MIN);
    expect(clampProductionSheetZoom(9)).toBe(PRODUCTION_SHEET_ZOOM_MAX);
  });

  test("pinch gesture changes scale within bounds and cleans up", () => {
    let g = createSheetPinchGesture(PRODUCTION_SHEET_ZOOM_DEFAULT);
    g = beginSheetPinch(g, PRODUCTION_SHEET_ZOOM_DEFAULT, 100, {
      centerX: 10,
      centerY: 10,
      scrollLeft: 0,
      scrollTop: 0,
    });
    expect(g.pinching).toBe(true);
    g = updateSheetPinch(g, 150);
    expect(g.zoom).toBeGreaterThan(PRODUCTION_SHEET_ZOOM_DEFAULT);
    expect(g.zoom).toBeLessThanOrEqual(PRODUCTION_SHEET_ZOOM_MAX);
    g = updateSheetPinch(g, 40);
    expect(g.zoom).toBeGreaterThanOrEqual(PRODUCTION_SHEET_ZOOM_MIN);
    g = endSheetPinch(g);
    expect(g.pinching).toBe(false);
  });
});

describe("sj_item_lines helpers", () => {
  test("never falls back to item_label or PO items", () => {
    expect(sjItemLinesFromRow({
      item_label: "Cup 12oz",
      line_items: [{ name: "PO item" }],
    })).toEqual([]);
    expect(sjItemLinesFromRow({ sj_item_lines: null })).toEqual([]);
  });

  test("same-name lines stay separate and keys use identity not name", () => {
    const lines = [
      { shipment_id: "sh-1", sj_number: "SJ/001", line_id: "a", line_idx: 0, name: "Cup", qty: 1000 },
      { shipment_id: "sh-1", sj_number: "SJ/001", line_id: "b", line_idx: 1, name: "Cup", qty: 2000 },
    ];
    expect(sjItemLineKey(lines[0], 0)).not.toBe(sjItemLineKey(lines[1], 1));
    expect(sjItemLineKey(lines[0], 0)).not.toBe("Cup");
    const groups = groupSjItemLines(lines);
    expect(groups).toHaveLength(1);
    expect(groups[0].lines).toHaveLength(2);
  });

  test("missing/null line_idx does not collapse to index 0", () => {
    const missing = { shipment_id: "sh-1", line_id: "lid-z", name: "Cup" };
    const idx0 = { shipment_id: "sh-1", line_idx: 0, name: "Cup" };
    expect(sjItemLineKey(missing, 5)).toBe("sh-1:lid-z:5");
    expect(sjItemLineKey(idx0, 5)).toBe("sh-1:idx:0:5");
    expect(sjItemLineKey(missing, 5)).not.toBe(sjItemLineKey(idx0, 5));
    expect(sjItemLineKey({ shipment_id: "sh-1", name: "Cup" }, 3)).toBe("sh-1:pos:3");
    expect(sjItemLineKey({ shipment_id: "sh-1", name: "Cup" }, 3)).not.toBe("sh-1:idx:0:3");
  });

  test("schedule helpers treat payload as latest-SJ lines only", () => {
    const latest = [
      { shipment_id: "sh-3", sj_number: "SJ26/09/001-3", line_id: "c", line_idx: 2, name: "Current C", qty: 200 },
    ];
    expect(sjItemLinesFromRow({
      sj_label: "SJ26/09/001-3",
      latest_sj_number: "SJ26/09/001-3",
      sj_item_lines: latest,
    })).toEqual(latest);
    expect(groupSjItemLines(latest)).toHaveLength(1);
    expect(groupSjItemLines(latest)[0].sj_number).toBe("SJ26/09/001-3");
  });
});

describe("production role-status overlay identity", () => {
  test("indexes and merges only by canonical order id", () => {
    const production = [
      { id: "po-a", customer: "RENEA", helper_required: 100, helper_prepared: 100, screen_qty: 10 },
      { id: "po-b", customer: "RENEA", helper_required: 80, helper_prepared: 10 },
    ];
    const byId = indexProductionStatusByOrderId(production);
    expect(byId.get("po-a").helper_prepared).toBe(100);
    expect(byId.get("RENEA")).toBeUndefined();
    expect(byId.get(0)).toBeUndefined();

    const deliveryA = { id: "po-a", customer_name: "RENEA", qty_packed: 40, qty_total: 100 };
    const deliveryB = { id: "po-b", customer_name: "RENEA", qty_packed: 0, qty_total: 80 };
    expect(withProductionRoleStatus(deliveryA, byId.get("po-a")).helper_required).toBe(100);
    expect(withProductionRoleStatus(deliveryB, byId.get("po-b")).helper_prepared).toBe(10);
    expect(withProductionRoleStatus(deliveryA, byId.get("po-b")).helper_required).toBeUndefined();
  });

  test("6. same-name customers stay isolated by order id", () => {
    const byId = indexProductionStatusByOrderId([
      { id: "po-1", customer: "SPECIAL LALAP", helper_required: 50, helper_prepared: 50, screen_qty: 5 },
    ]);
    const sameNameOther = deliveryRowWithProductionStatus(
      { id: "po-other", customer_name: "SPECIAL LALAP", qty_packed: 50, qty_total: 50 },
      byId,
    );
    expect(sameNameOther.helper_required).toBeUndefined();
    expect(sameNameOther.screen_qty).toBeUndefined();
    const matched = deliveryRowWithProductionStatus(
      { id: "po-1", customer_name: "SPECIAL LALAP" },
      byId,
    );
    expect(matched.helper_required).toBe(50);
    expect(matched.screen_qty).toBe(5);
  });

  test("7. multiple SJ item lines share PO production status, not first-name match", () => {
    const delivery = {
      id: "po-mix",
      sj_item_lines: [
        { shipment_id: "sh-1", line_id: "lid-a", line_idx: 0, name: "Cup 16oz", qty: 100 },
        { shipment_id: "sh-1", line_id: "lid-b", line_idx: 1, name: "Cup 16oz", qty: 200 },
      ],
    };
    const production = {
      id: "po-mix",
      helper_required: 300,
      helper_prepared: 300,
      screen_qty: 20,
      operators: [
        { split_id: "s1", line_id: "lid-a", line_idx: 0, line_printable: true, qty_completed: 100 },
        { split_id: "s2", line_id: "lid-b", line_idx: 1, line_printable: true, qty_completed: 200 },
      ],
    };
    const merged = withProductionRoleStatus(delivery, production);
    expect(merged.helper_required).toBe(300);
    expect(merged.sj_item_lines).toHaveLength(2);
    expect(merged.operators.map((op) => op.line_id)).toEqual(["lid-a", "lid-b"]);
  });

  test("8. regenerated SJ rows keep production status via order id", () => {
    const byId = indexProductionStatusByOrderId([
      { id: "po-sj", helper_required: 90, helper_prepared: 90, screen_qty: 9 },
    ]);
    const oldSj = deliveryRowWithProductionStatus(
      { id: "po-sj", shipment_id: "sh-old", sj_label: "SJ-1" },
      byId,
    );
    const newSj = deliveryRowWithProductionStatus(
      { id: "po-sj", shipment_id: "sh-new", sj_label: "SJ-2" },
      byId,
    );
    expect(oldSj.helper_required).toBe(90);
    expect(newSj.helper_required).toBe(90);
    expect(oldSj.shipment_id).toBe("sh-old");
    expect(newSj.shipment_id).toBe("sh-new");
  });

  test("11. missing line_idx never attaches production status via index 0", () => {
    const production = {
      id: "po-idx",
      helper_required: 10,
      operators: [{ split_id: "s0", line_idx: 0, line_printable: true, qty_completed: 10 }],
    };
    const unmatched = withProductionRoleStatus(
      { id: "po-other", line_idx: null, customer_name: "Seven Corner Coffee And Space" },
      production,
    );
    expect(unmatched.helper_required).toBeUndefined();
    expect(unmatched.operators).toBeUndefined();
    expect(indexProductionStatusByOrderId([production]).get("0")).toBeUndefined();
  });

  test("does not copy packing or delivery qty from production", () => {
    const merged = withProductionRoleStatus(
      { id: "po-1", qty_packed: 4, qty_total: 10, qty_delivered: 1 },
      {
        id: "po-1",
        helper_required: 10,
        packing_qty: 99,
        qty_packed: 99,
        qty_delivered: 99,
      },
    );
    expect(merged.helper_required).toBe(10);
    expect(merged.qty_packed).toBe(4);
    expect(merged.qty_delivered).toBe(1);
  });

  test("copies PO role_status but never production Load/Kirim onto an SJ row", () => {
    const merged = withProductionRoleStatus(
      {
        id: "po-1",
        shipment_id: "sh-2",
        role_status: { pack: "done", load: "pending", kirim: "idle" },
      },
      {
        id: "po-1",
        role_status: { cup: "done", screen: "done", prod: "done", pack: "done", load: "done", kirim: "done" },
      },
    );
    expect(merged.role_status.cup).toBe("done");
    expect(merged.role_status.pack).toBe("done");
    expect(merged.role_status.load).toBe("pending");
    expect(merged.role_status.kirim).toBe("idle");
  });
});

