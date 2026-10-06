/** Detail PO shows Proforma for one eligible saved invoice, on Order Masuk. */
export function proformaRowVisible({ onOrderMasuk, cancelled, hasView, available }) {
  if (!onOrderMasuk) return false;
  if (cancelled) return false;
  if (!hasView) return false;
  if (!available) return false;
  return true;
}

/** Open the read-only proforma PDF the same way Faktur Penjualan opens its PDF. */
export async function openProformaInvoicePdf(invoiceId, { download, openWindow = window.open } = {}) {
  if (!invoiceId || typeof download !== "function") return;
  const blob = await download(invoiceId);
  const url = URL.createObjectURL(blob);
  openWindow(url, "_blank");
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
