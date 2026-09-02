/**
 * Domain rules and eligibility filters for Penerimaan Penjualan (Sales Receipts).
 */

export function filterEligibleInvoices(invoices) {
  return (invoices || []).filter(
    (inv) => Number(inv?.outstanding_amount) > 0
  );
}

export function resolvePreselectedInvoiceId(eligibleInvoices, queryInvoiceId) {
  if (!queryInvoiceId) return "";
  const exists = (eligibleInvoices || []).some((inv) => inv?.id === queryInvoiceId);
  return exists ? queryInvoiceId : "";
}

export function computeReceiptPreview(selectedInvoice, receiptAmount) {
  if (!selectedInvoice) return null;
  const grandTotal = Number(selectedInvoice.grand_total) || 0;
  const paidBefore = Number(selectedInvoice.paid_total) || 0;
  const outstandingBefore = Math.max(0, Number(selectedInvoice.outstanding_amount) || (grandTotal - paidBefore));

  const numAmount = Math.max(0, Number(receiptAmount) || 0);
  const newPaid = Math.min(grandTotal, paidBefore + numAmount);
  const newOutstanding = Math.max(0, outstandingBefore - numAmount);
  const newState = newPaid >= grandTotal ? "LUNAS" : "DP";
  const isOverpayment = numAmount > outstandingBefore;

  return {
    grandTotal,
    paidBefore,
    outstandingBefore,
    amount: numAmount,
    newPaid,
    newOutstanding,
    newState,
    isOverpayment,
  };
}
