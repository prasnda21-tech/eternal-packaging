/** Shared 12-column widget grid. Presentation only. No business math. */

export const COLUMNS = 12;
export const ROW_HEIGHT = 84;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function boxesOverlap(a, b) {
  return !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y);
}

export function firstFit(placed, w, h) {
  let y = 0;
  while (y < 80) {
    for (let x = 0; x + w <= COLUMNS; x += 1) {
      const candidate = { x, y, w, h };
      if (!placed.some((item) => boxesOverlap(candidate, item))) return { x, y };
    }
    y += 1;
  }
  return { x: 0, y };
}

export function clampBox(widgetId, box, sizes) {
  const spec = sizes[widgetId] || { minW: 3, maxW: 6, minH: 2, maxH: 6, w: 4, h: 3 };
  const w = clamp(Math.round(box.w), spec.minW, spec.maxW);
  const h = clamp(Math.round(box.h), spec.minH, spec.maxH);
  const x = clamp(Math.round(box.x), 0, COLUMNS - w);
  const y = Math.max(0, Math.round(box.y));
  return { x, y, w, h };
}

function pushOthers(anchor, rest) {
  const placed = [anchor];
  rest
    .slice()
    .sort((a, b) => a.y - b.y || a.x - b.x || a.widget_id.localeCompare(b.widget_id))
    .forEach((item) => {
      let y = item.y;
      while (placed.some((other) => boxesOverlap({ ...item, y }, other))) y += 1;
      placed.push({ ...item, y });
    });
  return placed;
}

export function placeWidget(widgets, widgetId, box, sizes) {
  const current = widgets.find((item) => item.widget_id === widgetId);
  if (!current) return widgets;
  const next = clampBox(widgetId, box, sizes);
  const anchor = { ...current, ...next };
  const rest = widgets.filter((item) => item.widget_id !== widgetId);
  return pushOthers(anchor, rest);
}

export function addWidget(widgets, widgetId, sizes, periodIds) {
  if (widgets.some((item) => item.widget_id === widgetId)) return widgets;
  const spec = sizes[widgetId];
  if (!spec) return widgets;
  const spot = firstFit(widgets, spec.w, spec.h);
  return [
    ...widgets,
    {
      widget_id: widgetId,
      x: spot.x,
      y: spot.y,
      w: spec.w,
      h: spec.h,
      settings: periodIds && periodIds.has(widgetId) ? { period_mode: "global" } : {},
    },
  ];
}

export function removeWidget(widgets, widgetId) {
  return widgets.filter((item) => item.widget_id !== widgetId);
}

export function layoutOverlaps(widgets) {
  return widgets.some((item, index) => widgets.slice(index + 1).some((other) => boxesOverlap(item, other)));
}

export function gridHeight(widgets) {
  const rows = widgets.reduce((max, item) => Math.max(max, item.y + item.h), 1);
  return rows * ROW_HEIGHT;
}
