import { defaultLayout, layoutOverlaps } from "./productionWidgetLayout";
import { PENDING_WIDGETS, PRODUCTION_WIDGETS } from "./productionWidgetCatalog";

const allow = (...keys) => (key) => keys.includes(key);

function box(widgets, id) {
  const item = widgets.find((row) => row.widget_id === id);
  return [item.x, item.y, item.w, item.h];
}

test("default production layout places the wave 1 rows without operator widgets", () => {
  const widgets = defaultLayout(allow("dashboard.production.view"));
  expect(widgets.map((item) => item.widget_id)).toEqual([
    "perlu_produksi",
    "po_urgent",
    "po_hold",
    "po_aktif",
    "po_terlambat",
    "output_hari_ini",
    "tren_output",
    "sisa_qty",
    "deadline",
    "distribusi_tahap",
    "approval",
    "prioritas",
  ]);
  expect(layoutOverlaps(widgets)).toBe(false);
  expect(box(widgets, "perlu_produksi")).toEqual([0, 0, 3, 2]);
  expect(box(widgets, "po_aktif")).toEqual([9, 0, 3, 2]);
  expect(box(widgets, "po_terlambat")).toEqual([0, 2, 6, 3]);
  expect(box(widgets, "output_hari_ini")).toEqual([6, 2, 4, 3]);
  expect(box(widgets, "tren_output")).toEqual([0, 5, 4, 3]);
  expect(box(widgets, "sisa_qty")).toEqual([4, 5, 4, 3]);
  expect(box(widgets, "deadline")).toEqual([8, 5, 4, 3]);
  expect(box(widgets, "distribusi_tahap")).toEqual([0, 8, 4, 3]);
  expect(box(widgets, "approval")).toEqual([4, 8, 4, 3]);
  expect(box(widgets, "prioritas")).toEqual([0, 11, 12, 4]);
});

test("operator analytics permission adds the pair before the priority table", () => {
  const widgets = defaultLayout(allow("dashboard.production.view", "dashboard.production.operator_analytics"));
  expect(layoutOverlaps(widgets)).toBe(false);
  expect(box(widgets, "po_terlambat")).toEqual([0, 2, 6, 3]);
  expect(box(widgets, "output_operator")).toEqual([0, 11, 6, 3]);
  expect(box(widgets, "beban_operator")).toEqual([6, 11, 6, 3]);
  expect(box(widgets, "prioritas")).toEqual([0, 14, 12, 4]);
});

test("missing production permission leaves the board empty", () => {
  expect(defaultLayout(allow("dashboard.finance.view"))).toEqual([]);
});

test("catalog keeps twenty-six widgets and leaves qc and machines pending", () => {
  expect(PRODUCTION_WIDGETS).toHaveLength(24);
  expect(PENDING_WIDGETS).toHaveLength(2);
  expect(PRODUCTION_WIDGETS.length + PENDING_WIDGETS.length).toBe(26);
  expect(PRODUCTION_WIDGETS.map((item) => item.id)).toEqual(expect.arrayContaining([
    "kesiapan", "target_produksi", "po_terlambat", "target_realisasi", "aging", "ketepatan", "bottleneck", "delivery_qty", "urgent_requests",
  ]));
  const defaults = defaultLayout(allow("dashboard.production.view", "delivery.schedule.view")).map((item) => item.widget_id);
  expect(defaults).toEqual(expect.arrayContaining(["po_terlambat"]));
  expect(defaults).not.toEqual(expect.arrayContaining(["kesiapan", "target_produksi", "bottleneck", "komposisi", "output_cabang", "delivery_qty", "urgent_requests"]));
  expect(PENDING_WIDGETS.map((item) => item.id)).toEqual(["qc_reject", "utilisasi_mesin"]);
});

test("urgent requests widget accepts monitor perm or review/approve, not production view alone", () => {
  const { productionWidgetAllowed } = require("./productionWidgetCatalog");
  expect(productionWidgetAllowed("urgent_requests", allow("dashboard.production.view"))).toBe(false);
  expect(productionWidgetAllowed("urgent_requests", allow("dashboard.production.urgent_requests.view"))).toBe(true);
  expect(productionWidgetAllowed("urgent_requests", allow("urgent.review"))).toBe(true);
  expect(productionWidgetAllowed("urgent_requests", allow("urgent.approve"))).toBe(true);
  expect(productionWidgetAllowed("po_urgent", allow("dashboard.production.view"))).toBe(true);
});
