/** Jadwal Produksi Total Qty cell: actual printed / printable target.

Uses backend-normalized fields from /dashboard/production:
- numerator  = print_completed_qty  (operator_splits.qty_completed snapshot)
- denominator = print_qty_total     (sum of print line quantities, not PO total)

Does not re-aggregate raw line_items or operator_splits on the client.
*/

function qtyInt(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 0;
  return n;
}

export function formatIdQty(n) {
  return qtyInt(n).toLocaleString("id-ID");
}

export function printQtyProgress(row) {
  const actual = qtyInt(row?.print_completed_qty);
  const target = qtyInt(row?.print_qty_total);
  const poTotal = qtyInt(row?.total_quantity);
  const applicable = target > 0 || actual > 0;
  if (!applicable) {
    return {
      text: "—",
      over: false,
      actual,
      target,
      poTotal,
      title: poTotal > 0 ? `Total Qty PO: ${formatIdQty(poTotal)}` : undefined,
    };
  }
  return {
    text: `${formatIdQty(actual)} / ${formatIdQty(target)}`,
    over: target > 0 && actual > target,
    actual,
    target,
    poTotal,
    title: [
      `Sudah cetak: ${formatIdQty(actual)}`,
      `Target item cetak: ${formatIdQty(target)}`,
      `Total Qty PO: ${formatIdQty(poTotal)}`,
    ].join("\n"),
  };
}
