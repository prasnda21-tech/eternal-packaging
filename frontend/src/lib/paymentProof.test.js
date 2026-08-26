import { MAX_PAYMENT_PROOFS, canAddPaymentProof, paymentProofCount } from "./paymentProof";

describe("payment proof cap", () => {
  test("allows 1-5 and rejects 6th", () => {
    expect(MAX_PAYMENT_PROOFS).toBe(5);
    expect(canAddPaymentProof([])).toBe(true);
    expect(canAddPaymentProof([1, 2, 3, 4])).toBe(true);
    expect(canAddPaymentProof([1, 2, 3, 4, 5])).toBe(false);
    expect(paymentProofCount([1, 2, 3])).toBe(3);
  });
});
