/** PWA six-role summary — presentation counts from already-loaded schedule rows.

Production-role counts use Jadwal Produksi dashboard rows only.
Delivery-role counts use Jadwal Pengiriman rows only.
Do not mix sources. Unsupported counts stay null (not guessed).
*/

import { isProductionScheduleUrgent } from "./productionSchedulePriority";
import { printableCompletedQty } from "./productionScheduleQty";
import { screenerFilled, statusOfRow } from "./productionScheduleFilters";
import { deliveryQuickMatch } from "./mobileOpsQueue";

export const OPS_ROLE_SUMMARY = [
  {
    id: "prep",
    label: "Persiapan Cup",
    noun: "tugas",
    source: "production",
    href: "/m/schedule",
  },
  {
    id: "screener",
    label: "Screener",
    noun: "tugas",
    source: "production",
    href: "/m/schedule",
  },
  {
    id: "produksi",
    label: "Produksi",
    noun: "antrian",
    source: "production",
    href: "/m/schedule",
  },
  {
    id: "packing",
    label: "Packing",
    noun: "tugas",
    source: "delivery",
    href: "/m/delivery-schedule?ws=packing",
    workspaceId: "packing",
  },
  {
    id: "loading",
    label: "Loading",
    noun: "tugas",
    source: "delivery",
    href: "/m/delivery-schedule?ws=loading",
    workspaceId: "loading",
  },
  {
    id: "pengiriman",
    label: "Pengiriman",
    noun: "tugas",
    source: "delivery",
    href: "/m/delivery-schedule?ws=delivery",
    workspaceId: "delivery",
  },
];

function isActiveProductionRow(row) {
  if (!row) return false;
  const st = statusOfRow(row);
  if (st === "cancelled" || st === "selesai") return false;
  if (row.cancelled) return false;
  return true;
}

/** Open Persiapan Cup work: helper target exists and prepared qty has not reached it. */
export function isOpenHelperWork(row) {
  if (!isActiveProductionRow(row)) return false;
  if (row?.helper_required == null || String(row.helper_required).trim() === "") return false;
  const target = Number(row.helper_required);
  if (!Number.isFinite(target) || target <= 0) return false;
  return Number(row.helper_prepared ?? 0) < target;
}

/** Open Screener work: existing screenerFilled predicate is false. */
export function isOpenScreenerWork(row) {
  if (!isActiveProductionRow(row)) return false;
  return !screenerFilled(row);
}

/** Open Produksi work: printable completed qty has not reached print_qty_total. */
export function isOpenProduksiWork(row) {
  if (!isActiveProductionRow(row)) return false;
  const target = Number(row?.print_qty_total);
  if (!Number.isFinite(target) || target <= 0) return false;
  return printableCompletedQty(row?.operators) < target;
}

/** Active Jadwal Pengiriman rows (existing semua_aktif quick-match). */
export function isOpenPengirimanWork(row, today) {
  return deliveryQuickMatch(row, "semua_aktif", today);
}

function countWithUrgent(rows, pred, urgentPred) {
  let total = 0;
  let urgent = 0;
  for (const row of rows || []) {
    if (!pred(row)) continue;
    total += 1;
    if (urgentPred(row)) urgent += 1;
  }
  return { total, urgent };
}

/**
 * @param {{ productionRows?: array|null, deliveryRows?: array|null, today?: Date }} args
 * null dataset → that source's role counts are unsupported (null).
 * Empty array → supported, counted as 0.
 */
export function deriveOpsRoleCounts({ productionRows = null, deliveryRows = null, today = new Date() } = {}) {
  const prod = productionRows == null ? null : productionRows;
  const del = deliveryRows == null ? null : deliveryRows;
  return {
    prep: prod == null ? null : countWithUrgent(prod, isOpenHelperWork, isProductionScheduleUrgent),
    screener: prod == null ? null : countWithUrgent(prod, isOpenScreenerWork, isProductionScheduleUrgent),
    produksi: prod == null ? null : countWithUrgent(prod, isOpenProduksiWork, isProductionScheduleUrgent),
    packing: null,
    loading: null,
    pengiriman: del == null
      ? null
      : countWithUrgent(del, (row) => isOpenPengirimanWork(row, today), (row) => row?.urgent === true),
  };
}

export function buildOpsRoleSummaryModel(args) {
  const counts = deriveOpsRoleCounts(args);
  return OPS_ROLE_SUMMARY.map((role) => ({
    ...role,
    count: counts[role.id],
  }));
}
