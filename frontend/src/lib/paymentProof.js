export const MAX_PAYMENT_PROOFS = 5;

export function paymentProofCount(files) {
  return Array.isArray(files) ? files.length : 0;
}

export function canAddPaymentProof(files) {
  return paymentProofCount(files) < MAX_PAYMENT_PROOFS;
}
