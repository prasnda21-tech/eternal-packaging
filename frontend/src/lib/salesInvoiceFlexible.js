/** Model C helpers for Flexible Sales Invoice (PO or SJ source modes). */

export const INVOICE_SOURCE_PO = "po";
export const INVOICE_SOURCE_SJ = "sj";

export function normalizeInvoiceSourceMode(raw) {
  const mode = String(raw || "").trim().toLowerCase();
  if (mode === INVOICE_SOURCE_SJ) return INVOICE_SOURCE_SJ;
  return INVOICE_SOURCE_PO;
}

/** Build create payload. One mode per request — never mix PO+SJ ids. */
export function buildFlexibleInvoicePayload({
  sourceMode,
  sourcePoIds = [],
  sourceSjIds = [],
  lines = [],
  invoiceDate,
  paymentTermDays = 0,
  discountAmount = 0,
  ppnPercent = 0,
  notes = "",
  otherCharges = [],
  idempotencyKey,
}) {
  const mode = normalizeInvoiceSourceMode(sourceMode);
  const poIds = [...new Set((sourcePoIds || []).map((x) => String(x || "").trim()).filter(Boolean))];
  const sjIds = [...new Set((sourceSjIds || []).map((x) => String(x || "").trim()).filter(Boolean))];
  if (mode === INVOICE_SOURCE_PO && sjIds.length) {
    throw new Error("Mode PO tidak boleh mencampur Surat Jalan.");
  }
  if (mode === INVOICE_SOURCE_SJ && !sjIds.length) {
    throw new Error("Mode SJ memerlukan minimal satu Surat Jalan.");
  }
  if (mode === INVOICE_SOURCE_PO && !poIds.length) {
    throw new Error("Mode PO memerlukan minimal satu Pesanan Penjualan.");
  }
  const payload = {
    source_mode: mode,
    source_po_ids: mode === INVOICE_SOURCE_PO ? poIds : undefined,
    source_sj_ids: mode === INVOICE_SOURCE_SJ ? sjIds : undefined,
    po_id: mode === INVOICE_SOURCE_PO && poIds.length === 1 ? poIds[0] : poIds[0],
    invoice_date: invoiceDate,
    payment_term_days: Number(paymentTermDays) || 0,
    discount_amount: Number(discountAmount) || 0,
    ppn_percent: Number(ppnPercent) || 0,
    notes: String(notes || "").trim(),
    other_charges: otherCharges || [],
    lines,
    idempotency_key: idempotencyKey,
  };
  return payload;
}

export function summarizeInvoiceSources(invoice) {
  const poIds = Array.isArray(invoice?.source_po_ids) ? invoice.source_po_ids : [];
  const sjIds = Array.isArray(invoice?.source_sj_ids) ? invoice.source_sj_ids : [];
  return {
    sourceMode: normalizeInvoiceSourceMode(invoice?.source_mode || (sjIds.length ? "sj" : "po")),
    isMultiSource: Boolean(invoice?.is_multi_source) || poIds.length > 1 || sjIds.length > 1,
    sourcePoIds: poIds.length ? poIds : invoice?.po_id ? [invoice.po_id] : [],
    sourceSjIds: sjIds,
  };
}

/** True when SJ list row is a valid invoice source (active, not pending/cancelled). */
export function isInvoiceableSalesDelivery(row) {
  if (!row || typeof row !== "object") return false;
  if (row.is_cancelled === true || row.cancelled === true) return false;
  if (row.allocation_commit_pending === true) return false;
  if (row.billing_commit_pending === true) return false;
  const status = String(row.status || "").trim().toUpperCase();
  if (status === "BATAL" || status === "CANCELLED") return false;
  return Boolean(row.id);
}

/**
 * Build draft invoice lines from one or more operational SJ detail payloads.
 * Uses source_allocations when present; falls back to line_items.
 */
export function buildSjInvoicePreviewLines(deliveries = []) {
  const lines = [];
  const relations = [];
  for (const raw of deliveries || []) {
    const delivery = raw?.delivery || raw;
    if (!isInvoiceableSalesDelivery(delivery)) continue;
    const sjId = String(delivery.id || "").trim();
    const sjNumber = String(delivery.sj_number || delivery.number || sjId).trim();
    const allocs = Array.isArray(delivery.source_allocations) && delivery.source_allocations.length
      ? delivery.source_allocations
      : (delivery.line_items || []).map((li, idx) => ({
          source_po_id: li.source_po_id || delivery.po_id,
          source_po_number: li.source_po_number || delivery.po_number,
          source_line_id: li.source_line_id || li.line_id || `${delivery.po_id}_line_${idx}`,
          source_line_idx: li.source_line_idx ?? li.line_idx ?? idx,
          name: li.name || li.item_name,
          item_code: li.item_code,
          unit: li.unit || "pcs",
          allocated_qty: li.qty ?? li.allocated_qty ?? 0,
          unit_price: li.unit_price ?? li.price,
          color: li.color,
          ordered_qty: li.ordered_qty,
        }));

    for (const alloc of allocs) {
      const sjQty = Math.max(0, Number(alloc.allocated_qty ?? alloc.qty) || 0);
      if (sjQty <= 0) continue;
      const poId = String(alloc.source_po_id || delivery.po_id || "").trim();
      const lineId = String(alloc.source_line_id || alloc.line_id || "").trim();
      const lineIdx = Number(alloc.source_line_idx ?? alloc.line_idx ?? 0);
      const unitPrice = alloc.unit_price != null && alloc.unit_price !== ""
        ? Number(alloc.unit_price)
        : null;
      const remaining = Math.max(0, Number(alloc.invoiceable_remaining ?? sjQty) || 0);
      const rowKey = `${sjId}::${poId}::${lineId || lineIdx}`;
      lines.push({
        rowKey,
        source_sj_id: sjId,
        source_sj_number: sjNumber,
        source_po_id: poId,
        source_po_number: String(alloc.source_po_number || delivery.po_number || "").trim(),
        source_po_line_id: lineId,
        source_po_line_idx: lineIdx,
        line_idx: lineIdx,
        line_id: lineId,
        item_name: alloc.name || alloc.item_name || `Item #${lineIdx + 1}`,
        item_code: alloc.item_code || "",
        color: alloc.color || "",
        unit: alloc.unit || "pcs",
        sj_line_qty: sjQty,
        ordered_qty: Number(alloc.ordered_qty) || sjQty,
        remaining_invoice_qty: remaining,
        quantity: remaining,
        unit_price: Number.isFinite(unitPrice) ? unitPrice : null,
      });
      relations.push({
        sjId,
        sjNumber,
        poId,
        poNumber: String(alloc.source_po_number || delivery.po_number || "").trim(),
        lineId,
        sjQty,
        remaining,
      });
    }
  }
  return { lines, relations };
}

export function validateSjInvoiceQtyLines(lines = []) {
  const errors = [];
  let positive = 0;
  for (const line of lines || []) {
    const qty = Number(line.quantity);
    const maxQty = Math.min(
      Number(line.sj_line_qty) || 0,
      Number(line.remaining_invoice_qty ?? line.sj_line_qty) || 0,
    );
    if (!Number.isFinite(qty) || qty < 0 || !Number.isInteger(qty)) {
      errors.push(`Qty Faktur untuk '${line.item_name}' harus bilangan bulat ≥ 0.`);
      continue;
    }
    if (qty > maxQty) {
      errors.push(
        `Qty Faktur (${qty}) untuk '${line.item_name}' melebihi sisa invoiceable SJ (${maxQty}).`,
      );
    }
    if (qty > 0) positive += qty;
  }
  if (!positive && !(errors.length)) {
    errors.push("Minimal satu baris harus memiliki Qty Faktur > 0.");
  }
  return { ok: errors.length === 0, errors, totalQty: positive };
}
