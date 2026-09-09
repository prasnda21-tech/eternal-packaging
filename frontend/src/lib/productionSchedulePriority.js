/** Production Schedule Prio + Deadline Pengiriman H-1 semantics.

Do not recompute deadlines here. Cards and the spreadsheet both consume
the same dashboard/task fields:

- URGENT: `urgent === true` or `priority === "urgent"`
- H-1: `delivery_deadline_status === "h1"` (backend `_deadline_status_for`
  on `delivery_deadline_effective`; overdue/h6/terkirim are not H-1)

Both indicators can appear together, matching the two schedule columns.
*/

export function isProductionScheduleUrgent(row) {
  if (!row || typeof row !== "object") return false;
  if (row.urgent === true) return true;
  if (row.urgent === false) return false;
  return row.priority === "urgent";
}

export function isProductionScheduleH1(row) {
  return (row?.delivery_deadline_status || "") === "h1";
}

export function productionSchedulePriorityIndicators(row) {
  const badges = [];
  if (isProductionScheduleUrgent(row)) {
    badges.push({ key: "urgent", label: "URGENT" });
  }
  if (isProductionScheduleH1(row)) {
    badges.push({ key: "h1", label: "H-1" });
  }
  return badges;
}

/** Sort rank: URGENT (0) then H-1 (1) then normal (2). Dual-flag rows rank as URGENT. */
export function productionSchedulePriorityRank(row) {
  if (isProductionScheduleUrgent(row)) return 0;
  if (isProductionScheduleH1(row)) return 1;
  return 2;
}
