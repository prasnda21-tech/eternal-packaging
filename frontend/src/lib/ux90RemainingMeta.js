/**
 * Module-specific remaining labels for UX 9.0 create lookups.
 * Never invent totals from PO total_quantity or cross-module summaries.
 */

function formatQty(qty) {
  const n = Number(qty);
  if (!Number.isFinite(n)) return null;
  return n.toLocaleString("id-ID");
}

export function formatRemainingByUnit(remainingByUnit, fallbackTotal, fallbackUnit = "pcs") {
  const rows = Array.isArray(remainingByUnit) ? remainingByUnit.filter(Boolean) : [];
  if (rows.length === 1) {
    const qty = formatQty(rows[0].qty);
    if (qty == null) return null;
    return `${qty} ${String(rows[0].unit || fallbackUnit).trim() || fallbackUnit}`;
  }
  if (rows.length > 1) {
    const parts = rows
      .map((row) => {
        const qty = formatQty(row.qty);
        if (qty == null) return null;
        return `${qty} ${String(row.unit || fallbackUnit).trim() || fallbackUnit}`;
      })
      .filter(Boolean);
    if (!parts.length) return null;
    return parts.join(" · ");
  }
  const qty = formatQty(fallbackTotal);
  if (qty == null) return null;
  return `${qty} ${fallbackUnit}`;
}

/** Faktur: Belum difaktur from invoiceable_summary only. */
export function invoiceBelumDifakturMeta(po) {
  const summary = po?.invoiceable_summary;
  if (!summary || summary.available === false) {
    return "Belum difaktur: belum tersedia";
  }
  const formatted = formatRemainingByUnit(
    summary.remaining_by_unit,
    summary.total_remaining_invoice_qty,
  );
  if (!formatted) return "Belum difaktur: belum tersedia";
  return `Belum difaktur: ${formatted}`;
}

/** Pengiriman: Belum dibuat SJ from delivery_summary only (no total_quantity fallback). */
export function deliveryBelumSjMeta(po) {
  const summary = po?.delivery_summary;
  if (!summary || summary.available === false) {
    return "Belum dibuat SJ: belum tersedia";
  }
  if (summary.total_remaining == null && !Array.isArray(summary.remaining_by_unit)) {
    return "Belum dibuat SJ: belum tersedia";
  }
  const formatted = formatRemainingByUnit(
    summary.remaining_by_unit,
    summary.total_remaining,
  );
  if (!formatted) return "Belum dibuat SJ: belum tersedia";
  return `Belum dibuat SJ: ${formatted}`;
}

/**
 * Penerimaan: Belum dibayar from outstanding_amount.
 * Zero is valid zero. Missing/NaN → belum tersedia (no grand_total fallback).
 */
export function receiptBelumDibayarMeta(invoice, formatRupiah) {
  if (!invoice || !Object.prototype.hasOwnProperty.call(invoice, "outstanding_amount")) {
    return "Belum dibayar: belum tersedia";
  }
  const raw = invoice.outstanding_amount;
  if (raw === null || raw === undefined || raw === "") {
    return "Belum dibayar: belum tersedia";
  }
  const n = Number(raw);
  if (!Number.isFinite(n)) return "Belum dibayar: belum tersedia";
  return `Belum dibayar: ${formatRupiah(n)}`;
}
