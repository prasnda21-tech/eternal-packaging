import { deliveryBatchQty, findDeliveryShipment, isShipmentDelivered } from "./deliveryProof";

function trimmed(value) {
  return String(value || "").trim();
}

/**
 * Paint one confirmed SJ onto schedule rows already on screen.
 * Matches order id and shipment id. A SJ number is used only when the row
 * has no shipment id. Does not copy PO delivery status or other stages.
 */
export function patchScheduleRowsForDeliveredShipment(rows, order, shipmentId) {
  const list = Array.isArray(rows) ? rows : [];
  if (!order || typeof order !== "object") return list;
  const shipment = findDeliveryShipment(order, shipmentId);
  if (!isShipmentDelivered(order, shipment)) return list;
  const deliveredQty = deliveryBatchQty(shipment);
  if (deliveredQty <= 0) return list;
  const orderId = trimmed(order.id);
  if (!orderId) return list;
  const shipmentIds = new Set(
    [shipmentId, shipment.id, shipment.sales_delivery_id].map(trimmed).filter(Boolean),
  );
  const shipmentNumbers = new Set(
    [shipment.number, shipment.sj_number].map(trimmed).filter(Boolean),
  );
  let changed = false;
  const next = list.map((row) => {
    if (!row || typeof row !== "object") return row;
    if (trimmed(row.id || row.order_id) !== orderId) return row;
    const rowShipmentId = trimmed(row.shipment_id);
    let hit = false;
    if (rowShipmentId) {
      hit = shipmentIds.has(rowShipmentId);
    } else {
      const number = trimmed(row.latest_sj_number || row.sj_label || row.sj_number);
      hit = Boolean(number) && shipmentNumbers.has(number);
    }
    if (!hit) return row;
    if (Number(row.qty_delivered_sj) === deliveredQty) return row;
    changed = true;
    return { ...row, qty_delivered_sj: deliveredQty };
  });
  return changed ? next : list;
}
