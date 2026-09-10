/** Canonical deep links for chat work context cards — reuse existing routes only. */

export function workContextHref(ref, { mobile = false } = {}) {
  const orderId = ref?.order_id || ref?.snapshot?.order_id;
  if (!orderId) return null;
  if (mobile) {
    return `/m/orders/${encodeURIComponent(orderId)}`;
  }
  return `/po/${encodeURIComponent(orderId)}`;
}

export function workContextLabel(ref) {
  const snap = ref?.snapshot || ref || {};
  const po = snap.po_number || snap.order_number || "PO";
  const parts = [String(po)];
  if (snap.item_name) parts.push(String(snap.item_name));
  if (snap.stage_label || snap.task_label) parts.push(String(snap.stage_label || snap.task_label));
  return parts.join(" · ");
}
