/** Compact status-per-role marks from existing schedule fields. Presentation only. */

import { screenerFilled, statusOfRow } from "./productionScheduleFilters";
import { printableCompletedQty } from "./productionScheduleQty";

export function qtyMarkTone(done, target) {
  if (target == null && done == null) return "idle";
  const t = Number(target);
  const d = Number(done);
  if (!Number.isFinite(t) || t <= 0) {
    if (!Number.isFinite(d) || d <= 0) return "idle";
    return "pending";
  }
  if (Number.isFinite(d) && d >= t) return "done";
  if (Number.isFinite(d) && d > 0) return "pending";
  return "idle";
}

export function cupMarkTone(row) {
  if (row?.helper_required == null || String(row.helper_required).trim() === "") return "idle";
  return qtyMarkTone(row.helper_prepared ?? 0, row.helper_required);
}

export function screenMarkTone(row) {
  if (screenerFilled(row)) return "done";
  const st = statusOfRow(row);
  if (st === "cancelled") return "idle";
  return "pending";
}

export function prodMarkTone(row) {
  const target = Number(row?.print_qty_total);
  if (!Number.isFinite(target) || target <= 0) return "idle";
  return qtyMarkTone(printableCompletedQty(row?.operators), target);
}

export function packMarkTone(row) {
  if (row?.qty_packed == null && row?.packing_qty == null && row?.qty_total == null) return "idle";
  return qtyMarkTone(row?.qty_packed ?? row?.packing_qty ?? 0, row?.qty_total ?? row?.total_quantity);
}

/** Loading is not in GET /delivery/schedule — always unsupported. */
export function loadMarkTone() {
  return "idle";
}

export function kirimMarkTone(row) {
  const pct = Number(row?.percent_delivered_display ?? row?.percent_delivered);
  if (Number.isFinite(pct) && pct >= 100) return "done";
  if (Number.isFinite(pct) && pct > 0) return "pending";
  return qtyMarkTone(row?.qty_delivered ?? 0, row?.qty_total ?? row?.total_quantity);
}
