/** Bukti Payment helpers for Penerimaan Penjualan (sales receipts). */

export const RECEIPT_PROOF_ACCEPT = "image/jpeg,image/jpg,image/png,application/pdf";
export const RECEIPT_PROOF_MAX_BYTES = 6 * 1024 * 1024;
export const RECEIPT_PROOF_ALLOWED_TYPES = new Set([
  "image/jpeg",
  "image/jpg",
  "image/png",
  "application/pdf",
]);

export function validateReceiptProofFile(file) {
  if (!file) {
    return { ok: false, error: "Bukti Payment wajib dipilih." };
  }
  const type = String(file.type || "").toLowerCase();
  const name = String(file.name || "").toLowerCase();
  const byExt =
    name.endsWith(".jpg") ||
    name.endsWith(".jpeg") ||
    name.endsWith(".png") ||
    name.endsWith(".pdf");
  const byType = RECEIPT_PROOF_ALLOWED_TYPES.has(type);
  if (!byType && !byExt) {
    return { ok: false, error: "Tipe file tidak didukung. Gunakan JPG, JPEG, PNG, atau PDF." };
  }
  if (file.size > RECEIPT_PROOF_MAX_BYTES) {
    return { ok: false, error: "File terlalu besar (maksimal 6MB)." };
  }
  return { ok: true, error: "" };
}

export function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Gagal membaca file Bukti Payment."));
    reader.readAsDataURL(file);
  });
}

export async function buildReceiptProofPayload(file) {
  const check = validateReceiptProofFile(file);
  if (!check.ok) {
    throw new Error(check.error);
  }
  const data_url = await fileToDataUrl(file);
  return {
    name: file.name || "bukti-payment",
    data_url,
  };
}

/** Receipt amount / DP / LUNAS never come from the proof file. */
export function receiptPaymentStateFromAmounts(amount, outstandingBefore, grandTotal) {
  const a = Number(amount) || 0;
  const paid = (Number(grandTotal) || 0) - Math.max((Number(outstandingBefore) || 0) - a, 0);
  const gt = Number(grandTotal) || 0;
  return paid >= gt && gt > 0 ? "LUNAS" : "DP";
}

export function salesReceiptProofFileUrl(receiptId, { download = false } = {}) {
  if (!receiptId) return "";
  const q = download ? "?download=true" : "";
  return `/api/sales-receipts/${encodeURIComponent(receiptId)}/payment-proof${q}`;
}

export function receiptHasPaymentProof(receipt) {
  if (!receipt || typeof receipt !== "object") return false;
  if (receipt.has_payment_proof) return true;
  const p = receipt.payment_proof;
  return !!(p && (p.id || p.name));
}

/** View Bukti Payment — must match backend GET /sales-receipts/{id}/payment-proof.
 *  po.view alone is intentionally insufficient.
 */
export const SALES_RECEIPT_PROOF_VIEW_PERMS = ["sales.receipt.view", "payment.view"];

export function canViewSalesReceiptPaymentProof(hasPerm) {
  if (typeof hasPerm !== "function") return false;
  return SALES_RECEIPT_PROOF_VIEW_PERMS.some((p) => hasPerm(p));
}

export function receiptIsReversed(receipt) {
  const status = String(receipt?.financial_status || "").toLowerCase();
  return status === "cancelled" || status === "reversed" || receipt?.cancelled === true;
}

export function receiptProofStatusLabel(receipt) {
  if (receiptIsReversed(receipt)) return "Dibatalkan";
  const state = String(receipt?.payment_state || "").toUpperCase();
  if (state === "LUNAS") return "LUNAS";
  if (state === "DP") return "DP";
  return state || "DP";
}

export function orderReceiptProofStamp(order) {
  return (order?.payment_receipts || [])
    .map((row) => `${row?.receipt_id || ""}:${row?.payment_proof?.id || ""}`)
    .join("|");
}

/** Badge considers legacy PO files and penerimaan proofs. Approval text stays unchanged. */
export function paymentEvidenceBadge(order, receipts) {
  if (order?.payment_bypassed) return { tone: "bypass", label: "Payment Bypass" };
  if (order?.payment_status === "Approved") return { tone: "ok", label: "Payment Approved" };
  const legacy = (order?.payment_files || []).length > 0;
  const receiptProof = (receipts || []).some(receiptHasPaymentProof);
  if (!legacy && !receiptProof) return { tone: "warn", label: "Belum Ada Bukti Payment" };
  if (legacy) return { tone: "warn", label: "Menunggu Approval Payment" };
  return { tone: "warn", label: "Bukti Penerimaan Tersimpan" };
}
