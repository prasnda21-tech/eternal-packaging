/** Canonical PO identity badges from orders.priority + orders.buffer_stock.

Display-only. Does not consult urgent_requests approval state.
Missing/null priority → not urgent. Missing/null buffer_stock → not buffer.
*/

export function isPoUrgent(orderOrRow) {
  if (!orderOrRow || typeof orderOrRow !== "object") return false;
  if (orderOrRow.urgent === true) return true;
  if (orderOrRow.urgent === false) return false;
  if (orderOrRow.explicit_urgent === true) return true;
  return String(orderOrRow.priority || "").trim().toLowerCase() === "urgent";
}

export function isPoBufferStock(orderOrRow) {
  if (!orderOrRow || typeof orderOrRow !== "object") return false;
  return orderOrRow.buffer_stock === true;
}

/**
 * @param {object} [opts]
 * @param {boolean} [opts.omitUrgent] — skip URGENT when a sibling already shows it
 *   (e.g. ProductionSchedulePriorityBadges / KP priority_display). Buffer still renders.
 * @returns {{ key: string, label: string, className: string }[]}
 * Empty when neither flag is active — no NORMAL badge.
 */
export function poIdentityBadgeItems(orderOrRow, opts = {}) {
  const omitUrgent = Boolean(opts?.omitUrgent);
  const items = [];
  if (!omitUrgent && isPoUrgent(orderOrRow)) {
    items.push({ key: "urgent", label: "URGENT", className: "urgent-badge" });
  }
  if (isPoBufferStock(orderOrRow)) {
    items.push({ key: "buffer", label: "BUFFER STOCK", className: "buffer-stock-badge" });
  }
  return items;
}
