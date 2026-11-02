/** Helpers for multi-PO Surat Jalan create/detail (Stage 1). */

export function lineAllocKey(poId, lineIdx) {
  return `${poId}::${lineIdx}`;
}

export function buildCombinedAllocationRows(summariesByPoId) {
  const rows = [];
  Object.values(summariesByPoId || {}).forEach((summary) => {
    if (!summary?.po_id) return;
    (summary.lines || []).forEach((line) => {
      rows.push({
        key: lineAllocKey(summary.po_id, line.line_idx),
        source_po_id: summary.po_id,
        source_po_number: summary.po_number,
        source_line_idx: line.line_idx,
        source_line_id: line.line_id,
        name: line.name,
        unit: line.unit || "pcs",
        ordered_qty: Number(line.ordered_qty) || 0,
        remaining_qty: Number(line.remaining_qty) || 0,
        customer: summary.customer,
      });
    });
  });
  return rows;
}

export function buildSourceAllocationsPayload(rows, lineQtys, lineNotes = {}) {
  const out = [];
  (rows || []).forEach((row) => {
    const qty = Number(lineQtys[row.key] || 0) || 0;
    if (qty <= 0) return;
    out.push({
      source_po_id: row.source_po_id,
      source_line_id: row.source_line_id,
      source_line_idx: row.source_line_idx,
      allocated_qty: qty,
      notes: lineNotes[row.key] || "",
    });
  });
  return out;
}

export function validateMultiSourceSelection(summaries) {
  const list = Object.values(summaries || {}).filter(Boolean);
  if (list.length < 2) {
    return "Pilih minimal 2 PO untuk penggabungan.";
  }
  const customerIds = list.map((s) => String(s.customer_id || "").trim()).filter(Boolean);
  if (customerIds.length !== list.length) {
    return "Setiap PO harus punya customer_id kanonik. Penggabungan ditolak.";
  }
  if (new Set(customerIds).size > 1) {
    return "Customer harus sama untuk menggabungkan PO.";
  }
  const addresses = list.map((s) => String(s.address || "").trim().toLowerCase().replace(/\s+/g, " "));
  if (addresses.some((a) => !a) || new Set(addresses).size > 1) {
    return "Alamat tujuan harus sama persis pada semua PO.";
  }
  return "";
}

export function formatSourcePoLabel(delivery) {
  const ids = delivery?.source_po_ids || [];
  if (ids.length > 1) {
    return `${ids.length} PO sumber`;
  }
  return delivery?.po_number || "—";
}
