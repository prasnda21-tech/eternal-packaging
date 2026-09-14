/** Jadwal Produksi qty cells.

Total Qty — SUM(active scheduled printable qty) grouped by printable line
(order + stable line identity). Not per-operator, not PO total, not actual output.

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

/** Stable printable-line key: order line identity only. Never item name, SKU, or operator. */
export function printableLineKey(op) {
  const lineId = op?.line_id != null && String(op.line_id).trim();
  if (lineId) return `lid:${lineId}`;
  if (op?.line_idx != null && op.line_idx !== "") return `idx:${op.line_idx}`;
  return "";
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
      line_id: op.line_id || "",
      line_name: op.line_name || "",
      accurate_item_id: op.accurate_item_id || "",
      master_item_id: op.master_item_id || "",
      quantity: qty,
    });
  }
  return lines;
}

/** SUM active scheduled printable qty grouped by stable line identity. */
export function scheduledPrintableLineTotals(row) {
  const grouped = new Map();
  for (const op of operatorLineScheduledQtys(row)) {
    const key = printableLineKey(op) || `idx:${op.line_idx ?? grouped.size}`;
    const prev = grouped.get(key) || {
      key,
      line_idx: op.line_idx,
      line_id: op.line_id,
      line_name: op.line_name || "",
      accurate_item_id: op.accurate_item_id,
      master_item_id: op.master_item_id,
      total: 0,
      operators: [],
    };
    prev.total += op.quantity;
    prev.operators.push(op);
    grouped.set(key, prev);
  }
  return Array.from(grouped.values());
}

export function totalQtyScheduledCell(row) {
  const groups = scheduledPrintableLineTotals(row);
  const poTotal = qtyInt(row?.total_quantity);
  if (!groups.length) {
    return {
      text: "—",
      groups: [],
      poTotal,
      title: poTotal > 0 ? `Total Qty PO: ${formatIdQty(poTotal)} (bukan kolom ini)` : undefined,
    };
  }
  return {
    text: groups.map((g) => formatIdQty(g.total)).join("\n"),
    groups,
    poTotal,
    title: groups
      .map((g) => `${g.line_name || `Line ${g.line_idx}`}: ${formatIdQty(g.total)}`)
      .join("\n"),
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

/** Authoritative Cetak split: backend `line_printable` from `is_print_line` (production_process != non_print). */
export function isPrintableScheduleSplit(op) {
  return op?.line_printable !== false;
}

export function scheduleSplitIdentity(op, index = 0) {
  if (op?.split_id) return `split:${op.split_id}`;
  if (op?.id) return `split:${op.id}`;
  const lineId = op?.line_id != null && String(op.line_id).trim();
  if (lineId) return `lid:${lineId}`;
  if (op?.line_idx != null && op.line_idx !== "") return `idx:${op.line_idx}`;
  return `unknown:${index}`;
}

export function printableCompletedQty(splits) {
  const seen = new Set();
  let total = 0;
  (splits || []).forEach((op, index) => {
    if (!isPrintableScheduleSplit(op)) return;
    const key = scheduleSplitIdentity(op, index);
    if (seen.has(key)) return;
    seen.add(key);
    total += Math.max(0, qtyInt(op?.qty_completed));
  });
  return total;
}

export function operatorPrintableItems(splits) {
  const seen = new Set();
  const items = [];
  (splits || []).forEach((op, index) => {
    if (!isPrintableScheduleSplit(op)) return;
    const key = scheduleSplitIdentity(op, index);
    if (seen.has(key)) return;
    seen.add(key);
    const name = String(op?.line_name || "").trim();
    items.push({
      key,
      name: name || "—",
      line_id: op?.line_id || "",
      line_idx: op?.line_idx,
      split_id: op?.split_id || op?.id || "",
      screen_size: op?.screen_size || null,
      quantity: op?.quantity,
    });
  });
  return items;
}

export function operatorQtyCetakProgress(row, splits) {
  const actual = printableCompletedQty(splits);
  const poTotal = qtyInt(row?.total_quantity);
  return {
    text: `${formatIdQty(actual)} / ${formatIdQty(poTotal)}`,
    over: poTotal > 0 && actual > poTotal,
    actual,
    poTotal,
    title: [
      `Sudah dicetak operator: ${formatIdQty(actual)}`,
      `Total Qty PO: ${formatIdQty(poTotal)}`,
    ].join("\n"),
  };
}

export function poTotalQtyDisplay(row) {
  const poTotal = qtyInt(row?.total_quantity);
  return {
    text: formatIdQty(poTotal),
    poTotal,
    title: `Total Qty PO: ${formatIdQty(poTotal)}`,
  };
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
