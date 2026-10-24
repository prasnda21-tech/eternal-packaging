/** Default grid for Dashboard Produksi. Uses the shared widget grid. */

import { PRODUCTION_WIDGETS } from "./productionWidgetCatalog";
import { addWidget as addPlaced, layoutOverlaps, placeWidget as placePlaced, removeWidget } from "../widgetBoard/widgetGrid";

export { layoutOverlaps, removeWidget };

export const SIZES = {
  perlu_produksi: { minW: 3, maxW: 6, minH: 2, maxH: 4, w: 3, h: 2 },
  po_urgent: { minW: 3, maxW: 6, minH: 2, maxH: 4, w: 3, h: 2 },
  po_hold: { minW: 3, maxW: 6, minH: 2, maxH: 4, w: 3, h: 2 },
  po_aktif: { minW: 3, maxW: 6, minH: 2, maxH: 4, w: 3, h: 2 },
  sisa_qty: { minW: 4, maxW: 8, minH: 2, maxH: 6, w: 4, h: 3 },
  deadline: { minW: 4, maxW: 8, minH: 2, maxH: 6, w: 4, h: 3 },
  approval: { minW: 3, maxW: 6, minH: 2, maxH: 5, w: 4, h: 3 },
  prioritas: { minW: 6, maxW: 12, minH: 3, maxH: 8, w: 12, h: 4 },
  output_hari_ini: { minW: 3, maxW: 6, minH: 2, maxH: 5, w: 4, h: 3 },
  tren_output: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 4, h: 3 },
  distribusi_tahap: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 4, h: 3 },
  output_operator: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 6, h: 3 },
  beban_operator: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 6, h: 3 },
  kesiapan: { minW: 6, maxW: 12, minH: 3, maxH: 6, w: 8, h: 4 },
  target_produksi: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 6, h: 4 },
  po_terlambat: { minW: 4, maxW: 8, minH: 3, maxH: 6, w: 4, h: 3 },
  target_realisasi: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 6, h: 4 },
  aging: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 6, h: 5 },
  ketepatan: { minW: 4, maxW: 12, minH: 3, maxH: 6, w: 6, h: 3 },
  bottleneck: { minW: 6, maxW: 12, minH: 3, maxH: 8, w: 8, h: 4 },
  komposisi: { minW: 6, maxW: 12, minH: 4, maxH: 8, w: 6, h: 5 },
  output_cabang: { minW: 6, maxW: 12, minH: 4, maxH: 8, w: 6, h: 5 },
};

const DEFAULT_ORDER = [
  "perlu_produksi",
  "po_urgent",
  "po_hold",
  "po_aktif",
  "output_hari_ini",
  "tren_output",
  "sisa_qty",
  "deadline",
  "distribusi_tahap",
  "approval",
  "output_operator",
  "beban_operator",
  "prioritas",
];

export function placeWidget(widgets, widgetId, box) {
  return placePlaced(widgets, widgetId, box, SIZES);
}

export function addWidget(widgets, widgetId) {
  return addPlaced(widgets, widgetId, SIZES, null);
}

export function defaultLayout(hasPerm) {
  return DEFAULT_ORDER.reduce((placed, id) => {
    const meta = PRODUCTION_WIDGETS.find((item) => item.id === id);
    if (!meta || (typeof hasPerm === "function" && !hasPerm(meta.perm))) return placed;
    return addWidget(placed, id);
  }, []);
}
