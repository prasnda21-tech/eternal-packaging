/** Urgent control visibility — must match backend POST /orders/{id}/urgent (`urgent.manage`). */
export function canManageUrgent(hasPerm) {
  return typeof hasPerm === "function" && hasPerm("urgent.manage");
}
