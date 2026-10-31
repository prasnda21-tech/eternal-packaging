/** Grid placement for Dashboard Keuangan. Presentation only. No financial math. */

import { FINANCE_WIDGETS } from "./financeWidgetCatalog";
import {
  COLUMNS,
  ROW_HEIGHT,
  addWidget as addPlaced,
  boxesOverlap,
  gridHeight,
  layoutOverlaps,
  placeWidget as placePlaced,
  removeWidget,
} from "../widgetBoard/widgetGrid";

export { COLUMNS, ROW_HEIGHT, boxesOverlap, gridHeight, layoutOverlaps, removeWidget };

export const SIZES = {
  cash_available: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 4, h: 3 },
  active_users: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 6, h: 3 },
  cash: { minW: 3, maxW: 8, minH: 2, maxH: 6, w: 3, h: 2 },
  sales: { minW: 3, maxW: 8, minH: 2, maxH: 6, w: 3, h: 2 },
  purchases: { minW: 3, maxW: 8, minH: 2, maxH: 6, w: 3, h: 2 },
  profit: { minW: 3, maxW: 8, minH: 2, maxH: 6, w: 3, h: 2 },
  expense: { minW: 3, maxW: 8, minH: 2, maxH: 6, w: 4, h: 3 },
  trend: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 4, h: 3 },
  products: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 4, h: 3 },
  sellers: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 6, h: 3 },
  recon: { minW: 4, maxW: 12, minH: 3, maxH: 8, w: 6, h: 3 },
};

const PERIOD = new Set(["cash", "expense", "profit", "purchases", "sales", "products", "sellers", "trend"]);

export function placeWidget(widgets, widgetId, box) {
  return placePlaced(widgets, widgetId, box, SIZES);
}

export function addWidget(widgets, widgetId) {
  return addPlaced(widgets, widgetId, SIZES, PERIOD);
}

const DEFAULT_ORDER = ["cash", "sales", "purchases", "profit", "expense", "trend", "products", "sellers", "recon"];

export function defaultLayout(hasPerm) {
  return DEFAULT_ORDER.reduce((placed, id) => {
    const meta = FINANCE_WIDGETS.find((item) => item.id === id);
    if (!meta || (typeof hasPerm === "function" && !hasPerm(meta.perm))) return placed;
    return addWidget(placed, id);
  }, []);
}

