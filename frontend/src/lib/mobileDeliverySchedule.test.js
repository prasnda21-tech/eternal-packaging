import {
  MOBILE_DELIVERY_SCHEDULE_COLS,
  MOBILE_DELIVERY_SCHEDULE_EDIT_PERM,
  MOBILE_DELIVERY_SCHEDULE_PATH,
  MOBILE_DELIVERY_SCHEDULE_VIEW_PERMS,
  canEditMobileDeliveryScheduleLocation,
  canViewMobileDeliverySchedule,
  groupSjItemLines,
  mobileDeliveryOrderHref,
  mobileDeliveryWorkHref,
  sjItemLineKey,
  sjItemLinesFromRow,
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
