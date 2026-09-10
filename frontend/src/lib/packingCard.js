/** Visibility vs submit for Packing card — used by PODetail + PackingPanel. */

export function shouldShowPackingCard(hasPackingUpdatePerm) {
  return !!hasPackingUpdatePerm;
}

export function packingSubmitEnabled(hasPackingUpdatePerm, availableForPacking) {
  return !!hasPackingUpdatePerm && Number(availableForPacking) > 0;
}

function isPrintLine(li) {
  return String(li?.production_process || "print").toLowerCase() !== "non_print";
}

function safeInt(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.max(0, n) : fallback;
}

function packedForLine(order, idx) {
  const lines = order?.packing_info?.lines;
  if (!Array.isArray(lines) || !lines.length) return 0;
  const found = lines.find((x) => Number(x.line_idx) === Number(idx));
  return safeInt(found?.packed_qty);
}

/** Frontend fallback if backend belum mengirim packing_lines. Cetak saja. */
export function computePackingLines(order) {
  const lis = order?.line_items || [];
  const helperAgg = order?.helper_prepared_print_qty;
  const helperItems = order?.helper_prep?.items || [];
  const splits = order?.operator_splits || [];

  const helperForLine = (idx) => {
    const it = helperItems.find((x) => Number(x.line_idx) === Number(idx));
    if (!it) return null;
    const req = safeInt(it.qty_required);
    const prep = safeInt(it.qty_prepared);
    return req ? Math.min(prep, req) : prep;
  };

  const completedForLine = (idx) =>
    splits.reduce((s, sp) => {
      if (sp.line_idx == null) return s;
      return Number(sp.line_idx) === Number(idx) ? s + safeInt(sp.qty_completed) : s;
    }, 0);

  const unassigned = splits.reduce((s, sp) => (sp.line_idx == null ? s + safeInt(sp.qty_completed) : s), 0);
  const printIdxs = lis.map((li, i) => (isPrintLine(li) ? i : -1)).filter((i) => i >= 0);

  return lis
    .map((li, idx) => {
      if (!isPrintLine(li)) return null;
      const qty = safeInt(li.quantity);
      let completed = completedForLine(idx);
      if (printIdxs.length === 1 && printIdxs[0] === idx) {
        completed += unassigned;
      }
      completed = Math.min(completed, qty);
      let prepared = helperForLine(idx);
      if (prepared == null && helperAgg != null && helperAgg !== "" && printIdxs.length === 1) {
        prepared = safeInt(helperAgg);
      }
      const available = Math.min(qty, prepared == null ? completed : Math.min(completed, prepared));
      const packed = packedForLine(order, idx);
      return {
        line_idx: idx,
        name: li.name || `Line #${idx + 1}`,
        color: li.color || null,
        production_process: "print",
        is_print: true,
        qty_po: qty,
        helper_prepared: prepared,
        print_completed: completed,
        print_remaining: Math.max(0, qty - completed),
        available,
        packed,
        remaining: Math.max(0, available - packed),
      };
    })
    .filter(Boolean);
}

export function packingLinesForOrder(order) {
  const rows = Array.isArray(order?.packing_lines) && order.packing_lines.length
    ? order.packing_lines
    : computePackingLines(order);
  // Non-cetak never shown in Packing (backend may still send legacy rows).
  return rows.filter((r) => r?.is_print !== false && String(r?.production_process || "print").toLowerCase() !== "non_print");
}
