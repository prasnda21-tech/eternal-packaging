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
    // One-click Simpan Faktur → backend create_and_finalize.
    finalize_on_save: true,
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

/**
 * Stable UI identity for invoice lines. Never use bare source line_idx alone —
 * multi-PO / multi-SJ invoices can share the same source line_idx.
 */
export function invoiceLineUiKey(line, index = 0) {
  const iid = String(line?.invoice_line_id || "").trim();
  if (iid) return iid;
  const sj = String(line?.source_sj_id || "").trim();
  const po = String(line?.source_po_id || line?.po_id || "").trim();
  const lid = String(line?.source_po_line_id || line?.line_id || "").trim();
  const idx = line?.source_po_line_idx ?? line?.line_idx;
  const idxPart = idx === null || idx === undefined || idx === "" ? String(index) : String(idx);
  if (sj || po || lid) {
    return [sj || "-", po || "-", lid || "-", idxPart, String(index)].join("::");
  }
  return `line-${index}`;
}

/** Sanitize ui key for data-testid attributes. */
export function invoiceLineTestId(prefix, line, index = 0) {
  const key = invoiceLineUiKey(line, index).replace(/[^a-zA-Z0-9_-]+/g, "_");
  return `${prefix}-${key}`;
}

/** Merge fingerprint for multi-PO invoice compatibility (customer / currency / branch). */
export function poInvoiceMergeFingerprint(poOrPreview) {
  const customerId = String(
    poOrPreview?.customer_id
      || poOrPreview?.customerId
      || "",
  ).trim();
  const currency = String(poOrPreview?.currency || "IDR").trim().toUpperCase() || "IDR";
  const branch = String(
    poOrPreview?.operational_branch_id
      || poOrPreview?.branch_id
      || "",
  ).trim();
  return { customerId, currency, branch };
}

export function isPoInvoiceMergeCompatible(anchor, candidate) {
  if (!anchor || !candidate) return true;
  const a = poInvoiceMergeFingerprint(anchor);
  const c = poInvoiceMergeFingerprint(candidate);
  if (a.customerId && c.customerId && a.customerId !== c.customerId) return false;
  if (a.currency !== c.currency) return false;
  if (a.branch && c.branch && a.branch !== c.branch) return false;
  return true;
}

/**
 * Flatten multiple PO invoice previews into distinct source rows.
 * Same product from different POs stay separate (identity / price).
 */
export function buildMultiPoInvoicePreviewLines(previews = []) {
  const lines = [];
  (previews || []).forEach((preview) => {
    if (!preview || typeof preview !== "object") return;
    const poId = String(preview.po_id || "").trim();
    const poNumber = String(preview.po_number || "").trim();
    if (!poId) return;
    (preview.lines || []).forEach((l, idx) => {
      const lineIdx = Number(l.line_idx ?? idx);
      const lineId = String(l.line_id || `${poId}_line_${lineIdx}`).trim();
      const remaining = Number(
        l.remaining_invoice_qty != null ? l.remaining_invoice_qty : l.quantity,
      ) || 0;
      const rowKey = `${poId}::${lineId}::${lineIdx}`;
      lines.push({
        rowKey,
        source_po_id: poId,
        source_po_number: poNumber,
        source_po_line_id: lineId,
        source_po_line_idx: lineIdx,
        line_idx: lineIdx,
        line_id: lineId,
        item_name: l.item_name || l.name || `Item #${lineIdx + 1}`,
        item_code: l.item_code || "",
        color: l.color || "",
        unit: l.unit || "pcs",
        ordered_qty: Number(l.ordered_qty != null ? l.ordered_qty : l.quantity) || 0,
        invoiced_cumulative: Number(l.invoiced_cumulative) || 0,
        remaining_invoice_qty: remaining,
        quantity: remaining,
        unit_price: l.unit_price === null || l.unit_price === undefined || l.unit_price === ""
          ? null
          : Number(l.unit_price),
        price_from_order: Boolean(l.price_from_order),
        customer: preview.customer,
        customer_id: preview.customer_id,
        currency: preview.currency || "IDR",
        operational_branch_id: preview.operational_branch_id || preview.branch_id,
      });
    });
  });
  return lines;
}

export function validateMultiPoInvoiceQtyLines(lines = []) {
  const errors = [];
  let positive = 0;
  for (const line of lines || []) {
    const qty = Number(line.quantity);
    const maxQty = Number(line.remaining_invoice_qty) || 0;
    if (!Number.isFinite(qty) || qty < 0 || !Number.isInteger(qty)) {
      errors.push(`Qty Faktur untuk '${line.item_name}' harus bilangan bulat ≥ 0.`);
      continue;
    }
    if (qty > maxQty) {
      errors.push(
        `Qty Faktur (${qty}) untuk '${line.item_name}' / ${line.source_po_number || line.source_po_id} melebihi sisa (${maxQty}).`,
      );
    }
    if (qty > 0) positive += qty;
  }
  if (!positive && !errors.length) {
    errors.push("Minimal satu baris harus memiliki Qty Faktur > 0.");
  }
  return { ok: errors.length === 0, errors, totalQty: positive };
}

/**
 * Resolve draft-detail qty caps without cross-contaminating other source lines
 * that share the same source line_idx.
 */
export function resolveDetailLineCapacity(line, { allLines = [], invoice = null } = {}) {
  const lines = Array.isArray(allLines) ? allLines : [];
  const lineIdx = Number(line?.line_idx ?? 0);
  const sourcePoId = String(line?.source_po_id || "").trim();
  const primaryPoId = String(invoice?.po_id || "").trim();
  const colliding = lines.filter((l) => Number(l?.line_idx ?? 0) === lineIdx).length > 1;
  const crossPo = Boolean(sourcePoId && primaryPoId && sourcePoId !== primaryPoId);
  const hasSj = Boolean(String(line?.source_sj_id || "").trim());
  const attachedMax = line?.max_qty;
  const canTrustAttached = !colliding && !crossPo && !hasSj
    && attachedMax !== null && attachedMax !== undefined && attachedMax !== "";

  if (canTrustAttached) {
    return {
      poQty: Number(line.po_qty || line.ordered_qty || 0),
      otherActive: Number(line.other_active_invoiced_qty || 0),
      maxQty: Number(attachedMax) || 0,
      sourceScoped: false,
    };
  }

  const ordered = Number(line?.ordered_qty ?? line?.po_qty ?? 0) || 0;
  const sjQty = line?.sj_line_qty != null && line?.sj_line_qty !== ""
    ? Number(line.sj_line_qty)
    : null;
  const remBefore = line?.remaining_invoice_qty_before != null && line?.remaining_invoice_qty_before !== ""
    ? Number(line.remaining_invoice_qty_before)
    : null;
  const current = Number(line?.quantity || 0) || 0;
  let maxQty = remBefore != null && Number.isFinite(remBefore)
    ? remBefore + current
    : (sjQty != null && Number.isFinite(sjQty) ? sjQty : ordered);
  if (sjQty != null && Number.isFinite(sjQty)) maxQty = Math.min(maxQty, sjQty);
  if (ordered > 0) maxQty = Math.min(maxQty, ordered);
  if (!Number.isFinite(maxQty) || maxQty < 0) maxQty = current;
  return {
    poQty: ordered,
    otherActive: Math.max(0, ordered - maxQty),
    maxQty,
    sourceScoped: true,
  };
}
