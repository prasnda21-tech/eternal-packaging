/**
 * Operational sales identity display from document snapshots.
 *
 * Canonical write identity remains sales_master.id (create flows).
 * Stored orders.sales / sales_deliveries.sales are historical snapshots.
 * Do not invent Master Sales labels from name similarity or live catalog.
 */

/**
 * @param {object|null|undefined} doc order, PO summary, or sales delivery row
 * @returns {string} trimmed snapshot label (may be empty)
 */
export function operationalSalesSnapshotLabel(doc) {
  if (!doc || typeof doc !== "object") return "";
  return String(
    doc.sales_name ||
      doc.sales ||
      doc.seller ||
      doc.sales_rep ||
      doc.sales_user ||
      ""
  ).trim();
}
