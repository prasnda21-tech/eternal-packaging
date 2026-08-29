/** Assign Approval Design via link — server timestamp only. */

export const ASSIGN_APPROVAL_DESIGN_NOTE = "Assign Approval Design via Link";

export function approvalDesignAssignEvent(order) {
  const timeline = Array.isArray(order?.timeline) ? order.timeline : [];
  let fromTl = null;
  for (let i = timeline.length - 1; i >= 0; i -= 1) {
    const t = timeline[i];
    const note = t?.note || "";
    if (t?.stage === 2 && note.includes(ASSIGN_APPROVAL_DESIGN_NOTE)) {
      fromTl = t;
      break;
    }
  }
  const hist = Array.isArray(order?.customer_approval_history) ? order.customer_approval_history : [];
  let fromHist = null;
  for (let i = hist.length - 1; i >= 0; i -= 1) {
    const h = hist[i];
    if (h?.action === "generated" || h?.action === "regenerated") {
      fromHist = h;
      break;
    }
  }
  const ca = order?.customer_approval || {};
  const at = fromTl?.at || ca.generated_at || fromHist?.at || null;
  const name = fromTl?.by_name || fromTl?.by_email
    || ca.generated_by_name || ca.generated_by
    || fromHist?.by_name || fromHist?.by_email
    || null;
  const hasAt = !!(at && String(at).trim());
  const hasName = !!(name && String(name).trim());
  return {
    at: hasAt ? at : null,
    name: hasName ? name : null,
    method: hasAt ? "Link" : null,
  };
}
