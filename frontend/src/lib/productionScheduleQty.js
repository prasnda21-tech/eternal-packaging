/** Jadwal Produksi qty cells.

Total Qty — active scheduled printable qty per operator per line.
Not PO total, not print_qty_total repeated on every operator, not actual output.

Qty Cetak (scheduledPrintQtyProgress) — Penjadwalan assignment vs print target
(PO-level ratio). Not identical to Total Qty.
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

/** Active scheduled printable qty per operator per line. quantity is current assignment (cancel already subtracted). */
export function operatorLineScheduledQtys(row) {
  const lines = [];
  for (const op of row?.operators || []) {
    const printable = op?.line_printable !== false;
    const qty = qtyInt(op?.quantity);
    if (!printable || qty <= 0) continue;
    lines.push({
      operator_user_id: op.operator_user_id || "",
      operator_email: op.operator_email || "",
      operator_name: op.operator_name || "",
      line_idx: op.line_idx,
      quantity: qty,
    });
  }
  return lines;
}

export function totalQtyScheduledCell(row) {
  const lines = operatorLineScheduledQtys(row);
  const poTotal = qtyInt(row?.total_quantity);
  if (!lines.length) {
    return {
      text: "—",
      lines: [],
      poTotal,
      title: poTotal > 0 ? `Total Qty PO: ${formatIdQty(poTotal)} (bukan kolom ini)` : undefined,
    };
  }
  return {
    text: lines.map((l) => formatIdQty(l.quantity)).join("\n"),
    lines,
    poTotal,
    title: lines.map((l) => `${l.operator_name || "Operator"} · line ${l.line_idx}: ${formatIdQty(l.quantity)}`).join("\n"),
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
