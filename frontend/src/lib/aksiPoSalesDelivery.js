/** Aksi PO — Surat Jalan / Pengiriman Pesanan action state from canonical remain. */

export function aksiPoSalesDeliveryState(order, { canCreateDelivery = true } = {}) {
  const summary = (order && order.sales_delivery_summary) || {};
  const ordered = Number(
    summary.total_ordered != null ? summary.total_ordered : (order?.total_quantity || 0),
  ) || 0;
  const shipped = Number(summary.total_shipped || 0) || 0;
  const remaining = summary.total_remaining !== undefined && summary.total_remaining !== null
    ? Number(summary.total_remaining) || 0
    : Math.max(0, ordered - shipped);
  const canCreateFromSummary = summary.can_create_delivery !== undefined
    ? !!summary.can_create_delivery
    : remaining > 0;
  const showProcess = !order?.cancelled && canCreateFromSummary && !!canCreateDelivery;
  // Completion only when canonical remain is exhausted — never from history-only SJ.
  const showAllQtyDone = !showProcess && remaining <= 0 && ordered > 0;
  return {
    ordered,
    shipped,
    remaining,
    showProcess,
    showAllQtyDone,
  };
}
