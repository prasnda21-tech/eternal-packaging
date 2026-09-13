import { validatePurchasePaymentAmount } from "../lib/purchaseUi";

describe("validatePurchasePaymentAmount", () => {
  test("rejects empty, zero, and over-outstanding amounts", () => {
    expect(validatePurchasePaymentAmount("", 1000)).toMatch(/wajib/i);
    expect(validatePurchasePaymentAmount("0", 1000)).toMatch(/lebih besar dari 0/i);
    expect(validatePurchasePaymentAmount("-1", 1000)).toMatch(/lebih besar dari 0/i);
    expect(validatePurchasePaymentAmount("9000", 8000)).toMatch(/melebihi sisa tagihan/i);
  });

  test("allows valid partial and full settlement amounts", () => {
    expect(validatePurchasePaymentAmount("2000", 10000)).toBe("");
    expect(validatePurchasePaymentAmount("8000", 8000)).toBe("");
  });
});
