/** Line-level Surat Jalan / Loading helpers — backend shipment_lines is source of truth. */

function safeInt(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.max(0, n) : fallback;
}

export function shipmentLinesForOrder(order) {
  if (Array.isArray(order?.shipment_lines) && order.shipment_lines.length) {
    return order.shipment_lines;
  }
  const packing = order?.packing_lines || [];
  const shipments = order?.shipments || [];
  const loadingLines = order?.loading_info?.lines || [];
  const packedOf = (idx) => {
    const row = packing.find((r) => Number(r.line_idx) === Number(idx));
    if (row) return safeInt(row.packed);
    const stored = (order?.packing_info?.lines || []).find((x) => Number(x.line_idx) === Number(idx));
    if (stored) return safeInt(stored.packed_qty);
    return Number(idx) === 0 ? safeInt(order?.packing_info?.qty_packed) : 0;
  };
  const shippedOf = (idx) =>
    shipments.reduce((s, sh) => {
      const line = (sh.lines || []).find((x) => Number(x.line_idx) === Number(idx));
      return s + safeInt(line?.qty ?? line?.quantity);
    }, 0);
  const loadedOf = (idx) => {
    const row = loadingLines.find((x) => Number(x.line_idx) === Number(idx));
    return safeInt(row?.loaded_qty);
  };
  const lis = order?.line_items || [];
  const n = lis.length || (packedOf(0) > 0 ? 1 : packing.length);
  const rows = [];
  for (let idx = 0; idx < n; idx += 1) {
    const li = lis[idx] || packing[idx] || {};
    const packed = packedOf(idx);
    const shipped = shippedOf(idx);
    const loaded = loadedOf(idx);
    const qtyPo = safeInt(li.quantity ?? packing[idx]?.qty_po);
    const cap = qtyPo;
    rows.push({
      line_idx: idx,
      name: li.name || packing[idx]?.name || `Line #${idx + 1}`,
      color: li.color || packing[idx]?.color,
      qty_po: qtyPo,
      packed,
      shipped,
      available_sj: Math.max(0, packed - shipped),
      loaded,
      loading_cap: cap,
      available_loading: Math.max(0, cap - loaded),
      remaining_loading: Math.max(0, cap - loaded),
      mismatch: shipped > packed,
    });
  }
  return rows;
}

export function storedShipments(order) {
  return Array.isArray(order?.shipments) ? order.shipments : [];
}

export function loadingStatusText(loaded) {
  const n = Number(loaded || 0);
  if (n <= 0) return "Belum Loading";
  return n.toLocaleString("id-ID");
}

export function parsePositiveQty(raw) {
  if (raw === "" || raw == null) return null;
  const n = parseInt(String(raw), 10);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n;
}

export function canSubmitLoadingIncrement(raw, available) {
  const n = parsePositiveQty(raw);
  const avail = Number(available || 0);
  return n != null && avail > 0 && n <= avail;
}
