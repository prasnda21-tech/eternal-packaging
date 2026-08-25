/** Jadwal Produksi qty cells.

Total Qty (printQtyProgress) — DO NOT CHANGE semantics:
- numerator  = print_completed_qty  (operator_splits.qty_completed snapshot)
- denominator = print_qty_total     (sum of print line quantities, not PO total)

Qty Cetak (scheduledPrintQtyProgress) — Penjadwalan assignment, not actual print:
- numerator  = operator_assigned_qty, else sum of current operators[].quantity
- denominator = print_qty_total     (printable target, not PO total)
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

/** Qty cetak scheduled by Penjadwalan (current assignments only — no event double-count). */
export function scheduledAssignedQty(row) {
  if (row?.operator_assigned_qty != null && row.operator_assigned_qty !== "") {
    return qtyInt(row.operator_assigned_qty);
  }
  let total = 0;
  for (const op of row?.operators || []) {
    total += qtyInt(op?.quantity);
  }
  return total;
}

export function scheduledPrintQtyProgress(row) {
  const scheduled = scheduledAssignedQty(row);
  const target = qtyInt(row?.print_qty_total);
  const poTotal = qtyInt(row?.total_quantity);
  const applicable = target > 0 || scheduled > 0;
  if (!applicable) {
    return {
      text: "—",
      over: false,
      scheduled,
      target,
      poTotal,
      title: poTotal > 0 ? `Total Qty PO: ${formatIdQty(poTotal)}` : undefined,
    };
  }
  return {
    text: `${formatIdQty(scheduled)} / ${formatIdQty(target)}`,
    over: target > 0 && scheduled > target,
    scheduled,
    target,
    poTotal,
    title: [
      `Dijadwalkan cetak: ${formatIdQty(scheduled)}`,
      `Target item cetak: ${formatIdQty(target)}`,
      `Total Qty PO: ${formatIdQty(poTotal)}`,
    ].join("\n"),
  };
}
