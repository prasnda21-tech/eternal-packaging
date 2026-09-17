/** Shared SJ identity/active filters. Filename is never a primary identity. */

export const INACTIVE_SJ_STATUSES = new Set(["cancelled", "superseded", "deleted"]);

export function shipmentIsInactive(sh) {
  if (!sh || typeof sh !== "object") return true;
  if (sh.cancelled || sh.is_cancelled || sh.deleted || sh.is_deleted || sh.superseded) return true;
  if (String(sh.superseded_by_shipment_id || "").trim()) return true;
  const st = String(sh.status || sh.sj_status || "").trim().toLowerCase();
  return INACTIVE_SJ_STATUSES.has(st) || st === "void";
}

export function shipmentIsActive(sh) {
  return Boolean(sh && typeof sh === "object" && !shipmentIsInactive(sh));
}

export function canonicalShipmentId(sh) {
  return String(sh?.id || "").trim();
}

export function sjFileIsActive(file, order) {
  if (!file || typeof file !== "object") return false;
  const st = String(file.status || "").trim().toLowerCase();
  if (INACTIVE_SJ_STATUSES.has(st) || file.cancelled || file.active === false) return false;
  const sid = String(file.shipment_id || file.id || "").trim();
  const ships = Array.isArray(order?.shipments) ? order.shipments : [];
  const sh = ships.find((s) => String(s?.id || "") === sid);
  if (sh) return shipmentIsActive(sh);
  return true;
}

export function actionablePengirimanOptions(options) {
  return (Array.isArray(options) ? options : []).filter((o) => o && !o.delivered && shipmentIsActive(o.shipment));
}

export function visibleHistorySj(entry) {
  return Boolean(entry);
}

export function selectSingleActionableId(actionable) {
  if (!Array.isArray(actionable) || actionable.length !== 1) return "";
  return String(actionable[0]?.id || "").trim();
}
