/** PWA Status per Role — 3-state marks from canonical persisted qty evidence. */

import { screenerFilled } from "./productionScheduleFilters";
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

function hasNumeric(value) {
  if (value == null || value === "") return false;
  return Number.isFinite(Number(value));
}

/** SJ line qty total — existing GET /delivery/schedule sj_item_lines. */
export function sjNeedQty(row) {
  const explicit = Number(row?.qty_sj ?? row?.qty_load_need);
  if (Number.isFinite(explicit) && explicit > 0) return explicit;
  const lines = Array.isArray(row?.sj_item_lines) ? row.sj_item_lines : [];
  if (!lines.length) return null;
  let sum = 0;
  for (const ln of lines) {
    const q = Number(ln?.qty);
    if (Number.isFinite(q)) sum += q;
  }
  return sum;
}

/** Cup: helper_prepared vs helper_required. */
export function cupMarkTone(row) {
  if (row?.helper_required == null || String(row.helper_required).trim() === "") return "idle";
  return qtyMarkTone(row.helper_prepared ?? 0, row.helper_required);
}

/**
 * Screen: persisted screen_qty only. No required/target exists, so amber is not invented.
 * empty → gray; saved qty → green.
 */
export function screenMarkTone(row) {
  if (screenerFilled(row)) return "done";
  return "idle";
}

/** Canonical printable completed qty: operators when present, else dashboard/delivery totals. */
export function prodCompletedQty(row) {
  if (Array.isArray(row?.operators) && row.operators.length > 0) {
    return printableCompletedQty(row.operators);
  }
  const reported = Number(row?.print_completed_qty ?? row?.qty_printed_operator);
  if (Number.isFinite(reported)) return reported;
  return printableCompletedQty(row?.operators);
}

/** Cetak: print_completed vs print_qty_total. */
export function prodMarkTone(row) {
  const target = Number(row?.print_qty_total);
  if (!Number.isFinite(target) || target <= 0) return "idle";
  return qtyMarkTone(prodCompletedQty(row), target);
}

export const cetakMarkTone = prodMarkTone;

/**
 * Pack: packing_status Sudah/Berjalan/Belum, else packed qty vs target.
 */
export function packMarkTone(row) {
  const status = String(row?.packing_status || "").trim();
  if (status === "Sudah") return "done";
  if (status === "Berjalan") return "pending";
  if (status === "Belum") return "idle";
  if (row?.qty_packed == null && row?.packing_qty == null && row?.qty_total == null) return "idle";
  return qtyMarkTone(row?.qty_packed ?? row?.packing_qty ?? 0, row?.qty_total ?? row?.total_quantity);
}

/**
 * Load: THIS SJ loaded qty vs SJ need.
 * Requires qty_loaded (or qty_load_need) on the row — not PO-level, not role_status.
 */
export function loadMarkTone(row) {
  if (!row || (!hasNumeric(row.qty_loaded) && !hasNumeric(row.qty_load_need))) return "idle";
  return qtyMarkTone(row.qty_loaded ?? 0, sjNeedQty(row));
}

/**
 * Kirim: THIS SJ delivered qty vs SJ need.
 * Requires qty_delivered_sj — never PO percent_delivered (would copy SJ1 onto SJ2).
 */
export function kirimMarkTone(row) {
  if (!row || !hasNumeric(row.qty_delivered_sj)) return "idle";
  return qtyMarkTone(row.qty_delivered_sj, sjNeedQty(row));
}

/**
 * Delivery Status per Role tones.
 * Screen stays idle unless production source is attached or screen_qty is present.
 */
export function deliveryRoleMarkTones(row, productionAttached = false) {
  const screenReady = productionAttached || screenerFilled(row);
  return {
    cup: cupMarkTone(row),
    screen: screenReady ? screenMarkTone(row) : "idle",
    prod: prodMarkTone(row),
    pack: packMarkTone(row),
    load: loadMarkTone(row),
    kirim: kirimMarkTone(row),
  };
}
