/** Urgent PO approval — Phase A production helpers. */

import { classifyUrgentDisplay, URGENT_DISPLAY, REQUEST_STATUS } from "./urgentPoUi";

/** @deprecated legacy direct toggle — no longer mutates priority. */
export function canManageUrgent(hasPerm) {
  return typeof hasPerm === "function" && hasPerm("urgent.manage");
}

export function canRequestUrgent(hasPerm) {
  return typeof hasPerm === "function" && hasPerm("urgent.request");
}

export function canApproveUrgent(hasPerm) {
  return typeof hasPerm === "function" && hasPerm("urgent.approve");
}

export function canReviewUrgent(hasPerm) {
  return typeof hasPerm === "function" && hasPerm("urgent.review");
}

/** Show Urgent entry on Normal PO when user can request and no active pending. */
export function canShowUrgentRequestButton({ hasPerm, order, request } = {}) {
  if (!canRequestUrgent(hasPerm)) return false;
  if (!order || order.cancelled) return false;
  if (String(order.priority || "").toLowerCase() === "urgent") return false;
  const status = String(request?.status || "").toLowerCase();
  if (status === REQUEST_STATUS.PENDING || status === "approving") return false;
  return true;
}

export function urgentDisplayState(order, request) {
  return classifyUrgentDisplay({ priority: order?.priority, request });
}

export { URGENT_DISPLAY, REQUEST_STATUS, classifyUrgentDisplay };
