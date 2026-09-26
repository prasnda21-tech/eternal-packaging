/**
 * Domain rules and eligibility filters for Penerimaan Penjualan (Sales Receipts).
 */

export function filterEligibleInvoices(invoices) {
  return (invoices || []).filter((inv) => {
    const status = String(inv?.financial_status || "draft").toLowerCase();
    return status === "finalized" && Number(inv?.outstanding_amount) > 0;
  });
}

export function resolvePreselectedInvoiceId(eligibleInvoices, queryInvoiceId) {
  if (!queryInvoiceId) return "";
  const exists = (eligibleInvoices || []).some((inv) => inv?.id === queryInvoiceId);
  return exists ? queryInvoiceId : "";
}

function roundMoney(value) {
  return Math.round((Number(value) || 0) * 100) / 100;
}

export function computeReceiptPreview(selectedInvoice, receiptAmount, allocatedAmount) {
  if (!selectedInvoice) return null;
  const grandTotal = Number(selectedInvoice.grand_total) || 0;
  const paidBefore = Number(selectedInvoice.paid_total) || 0;
  const outstandingBefore = Math.max(0, Number(selectedInvoice.outstanding_amount) || (grandTotal - paidBefore));

  const numAmount = Math.max(0, Number(receiptAmount) || 0);
  const defaultAllocated = Math.min(numAmount, outstandingBefore);
  const allocated = allocatedAmount == null || allocatedAmount === ""
    ? defaultAllocated
    : Math.max(0, Number(allocatedAmount) || 0);
  const allocationExceedsOutstanding = roundMoney(allocated - outstandingBefore) > 0;
  const allocationExceedsReceived = roundMoney(allocated - numAmount) > 0;
  const applied = allocationExceedsOutstanding || allocationExceedsReceived
    ? Math.min(allocated, outstandingBefore, numAmount)
    : allocated;
  const customerCredit = allocationExceedsOutstanding || allocationExceedsReceived
    ? 0
    : roundMoney(Math.max(0, numAmount - allocated));
  const newPaid = roundMoney(Math.min(grandTotal, paidBefore + applied));
  const newOutstanding = roundMoney(Math.max(0, outstandingBefore - applied));
  const newState = newPaid >= grandTotal ? "LUNAS" : "DP";
  const isOverpayment = numAmount > outstandingBefore;

  return {
    grandTotal,
    paidBefore,
    outstandingBefore,
    amount: numAmount,
    allocated: roundMoney(allocated),
    customerCredit,
    allocationExceedsOutstanding,
    allocationExceedsReceived,
    newPaid,
    newOutstanding,
    newState,
    isOverpayment,
  };
}
