/**
 * Physical PO qty for Order Masuk display.
 * Prefer backend-derived packing_required_total (excludes process-only SERVICE).
 * Never fall back to total_quantity — that sum includes Add On work qty.
 */

export function physicalOrderQty(order) {
  if (order == null || typeof order !== "object") return null;
  if (!Object.prototype.hasOwnProperty.call(order, "packing_required_total")) {
    return null;
  }
  const raw = order.packing_required_total;
  if (raw == null || raw === "") return null;
  const qty = Number(raw);
  if (!Number.isFinite(qty) || qty < 0) return null;
  return qty;
}

export function formatPhysicalOrderQty(order, { suffix = " pcs" } = {}) {
  const qty = physicalOrderQty(order);
  if (qty == null) return null;
  return `${qty.toLocaleString("id-ID")}${suffix}`;
}
