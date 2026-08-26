import { formatDisplayDateTime } from "./constants";

export function displayOrDash(value) {
  if (value == null) return "—";
  const text = String(value).trim();
  return text || "—";
}

export function terkirimQtySummary(order) {
  const total = Number(order?.terkirim_total_po_qty ?? order?.packing_required_total ?? order?.total_quantity ?? 0) || 0;
  const delivered = Number(order?.qty_delivered ?? 0) || 0;
  return {
    total,
    delivered,
    remaining: Math.max(0, total - delivered),
  };
}

export function deliveryHistoryEntries(order) {
  const rows = Array.isArray(order?.delivery_history) ? order.delivery_history : [];
  if (rows.length) return rows;
  const recs = Array.isArray(order?.delivery_confirmations) ? order.delivery_confirmations : [];
  return recs.map((rec, i) => ({
    id: rec.id || `confirm-${i}`,
    driver_name: rec.driver_name,
    item: rec.item,
    qty: rec.qty,
    time: rec.confirmed_at || rec.time,
    vehicle_plate: rec.vehicle_plate,
    proof_ids: rec.proof_ids || [],
    photos: rec.photos || [],
  }));
}

export function historyTimeLabel(iso) {
  if (!iso) return "—";
  return formatDisplayDateTime(iso);
}
