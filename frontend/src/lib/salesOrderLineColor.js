/**
 * Canonical Sales Order / PO line color for web display.
 *
 * Source of truth is the exact line's `color` field (persisted as
 * orders.line_items[].color, optionally enriched by the order detail API
 * from that same line's notes). Never borrow from another line, product
 * master, or header-level color.
 */
export function salesOrderLineColor(line) {
  if (!line || typeof line !== "object") return "";
  const raw = line.color;
  if (raw == null) return "";
  const text = String(raw).trim();
  return text;
}

/**
 * Map order line_items to display colors keyed by line index.
 * Same-name lines stay independent — identity is index / line id, not name.
 */
export function salesOrderLineColorsByIndex(lineItems) {
  const list = Array.isArray(lineItems) ? lineItems : [];
  return list.map((li, index) => ({
    index,
    line_id: li?.id || li?.line_id || li?.accurate_line_id || null,
    name: li?.name || li?.item_name || "",
    color: salesOrderLineColor(li),
  }));
}
