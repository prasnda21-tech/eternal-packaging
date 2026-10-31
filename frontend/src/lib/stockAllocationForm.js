/** Helpers for sales-order warehouse allocation (recording only — never blocks submit). */

export function isInventoryLineForAllocation(line) {
  const itemType = String(line?.item_type || "").trim().toUpperCase();
  if (itemType === "SERVICE" || itemType === "NON_INVENTORY") return false;
  if (itemType === "INVENTORY") return true;
  return Boolean(String(line?.master_item_id || "").trim());
}

export function emptyAllocationRow() {
  return { warehouse_id: "", qty: "" };
}

export function normalizeAllocationRows(rows) {
  if (!Array.isArray(rows) || !rows.length) return [emptyAllocationRow()];
  return rows.map((row) => ({
    warehouse_id: String(row?.warehouse_id || "").trim(),
    qty: row?.qty === 0 || row?.qty === "0" ? "0" : (row?.qty ?? row?.quantity ?? ""),
    allocation_id: row?.allocation_id || undefined,
  }));
}

export function allocatedTotal(rows) {
  return (Array.isArray(rows) ? rows : []).reduce((sum, row) => {
    if (!String(row?.warehouse_id || "").trim()) return sum;
    const n = Number(row?.qty);
    if (!Number.isFinite(n) || n <= 0) return sum;
    return sum + n;
  }, 0);
}

export function allocationVariance(orderedQty, rows) {
  const ordered = Number(orderedQty);
  const safeOrdered = Number.isFinite(ordered) ? ordered : 0;
  return allocatedTotal(rows) - safeOrdered;
}

/** Soft business warning only — never a submit blocker. */
export function allocationWarningMessage(line) {
  if (!isInventoryLineForAllocation(line)) return "";
  const rows = Array.isArray(line?.stock_allocations) ? line.stock_allocations : [];
  const filled = rows.filter((r) => String(r?.warehouse_id || "").trim() && Number(r?.qty) > 0);
  if (!filled.length) return "Alokasi gudang belum lengkap";
  const variance = allocationVariance(line?.quantity, filled);
  if (variance < 0) return "Total alokasi kurang dari qty pesanan";
  if (variance > 0) return "Total alokasi melebihi qty pesanan";
  return "";
}

export function summarizeFormAllocationWarnings(lineItems) {
  const warnings = [];
  (Array.isArray(lineItems) ? lineItems : []).forEach((line, index) => {
    const msg = allocationWarningMessage(line);
    if (msg) warnings.push({ lineIndex: index, line_id: line?.line_id || line?.rowKey, message: msg });
  });
  return warnings;
}

export function payloadAllocationsFromLine(line) {
  if (!isInventoryLineForAllocation(line)) return undefined;
  const rows = Array.isArray(line?.stock_allocations) ? line.stock_allocations : [];
  const out = [];
  rows.forEach((row) => {
    const wid = String(row?.warehouse_id || "").trim();
    const qty = Number(row?.qty);
    if (!wid || !Number.isFinite(qty) || qty <= 0) return;
    out.push({
      warehouse_id: wid,
      qty,
      ...(row.allocation_id ? { allocation_id: row.allocation_id } : {}),
    });
  });
  return out;
}

export function formatAllocationWarningsList(warnings) {
  if (!Array.isArray(warnings) || !warnings.length) return "";
  return warnings
    .map((w) => (typeof w === "string" ? w : (w?.message || w?.code || "")))
    .filter(Boolean)
    .join(" · ");
}
