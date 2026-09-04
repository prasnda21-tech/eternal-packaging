import {
  canViewSalesReceiptPaymentProof,
  receiptHasPaymentProof,
} from "../lib/salesReceiptPaymentProof";

/** Pure helpers mirroring Detail PO → Payment receipt proof UI decisions. */
export function salesReceiptProofButtonVisible(receipt, canView) {
  return Boolean(canView && receiptHasPaymentProof(receipt));
}

export function mapReceiptsToProofRows(receipts) {
  return (receipts || []).map((r) => ({
    id: r.id,
    receipt_number: r.receipt_number,
    amount: r.amount,
    payment_state: r.payment_state,
    hasProof: receiptHasPaymentProof(r),
    proofId: r.payment_proof?.id || null,
  }));
}

describe("PODetail sales receipt payment proof UI", () => {
  test("shows Lihat Bukti only when receipt has proof and user can view", () => {
    const withProof = { id: "r1", has_payment_proof: true, payment_proof: { id: "f1" } };
    const without = { id: "r2", has_payment_proof: false };
    expect(salesReceiptProofButtonVisible(withProof, true)).toBe(true);
    expect(salesReceiptProofButtonVisible(withProof, false)).toBe(false);
    expect(salesReceiptProofButtonVisible(without, true)).toBe(false);
  });

  test("po.view alone does not show Lihat Bukti Payment", () => {
    const withProof = { id: "r1", has_payment_proof: true, payment_proof: { id: "f1" } };
    const canView = canViewSalesReceiptPaymentProof((p) => p === "po.view");
    expect(canView).toBe(false);
    expect(salesReceiptProofButtonVisible(withProof, canView)).toBe(false);
  });

  test("sales.receipt.view or payment.view shows button when proof exists", () => {
    const withProof = { id: "r1", has_payment_proof: true, payment_proof: { id: "f1" } };
    expect(
      salesReceiptProofButtonVisible(
        withProof,
        canViewSalesReceiptPaymentProof((p) => p === "sales.receipt.view"),
      ),
    ).toBe(true);
    expect(
      salesReceiptProofButtonVisible(
        withProof,
        canViewSalesReceiptPaymentProof((p) => p === "payment.view"),
      ),
    ).toBe(true);
  });

  test("historical without proof does not crash and maps cleanly", () => {
    const rows = mapReceiptsToProofRows([
      { id: "old", receipt_number: "PAY-1", amount: 1000, payment_state: "DP" },
      { id: "new", receipt_number: "PAY-2", amount: 2000, payment_state: "DP", has_payment_proof: true, payment_proof: { id: "f2" } },
    ]);
    expect(rows).toHaveLength(2);
    expect(rows[0].hasProof).toBe(false);
    expect(rows[0].proofId).toBe(null);
    expect(rows[1].hasProof).toBe(true);
    expect(rows[1].proofId).toBe("f2");
  });

  test("multiple receipts keep distinct proof ids", () => {
    const rows = mapReceiptsToProofRows([
      { id: "a", has_payment_proof: true, payment_proof: { id: "pa" } },
      { id: "b", has_payment_proof: true, payment_proof: { id: "pb" } },
    ]);
    expect(rows[0].proofId).toBe("pa");
    expect(rows[1].proofId).toBe("pb");
    expect(rows[0].proofId).not.toBe(rows[1].proofId);
  });

  test("safe endpoint path uses receipt id not storage url", () => {
    const receiptId = "rec-123";
    const path = `/api/sales-receipts/${encodeURIComponent(receiptId)}/payment-proof`;
    expect(path).toContain("sales-receipts/rec-123/payment-proof");
    expect(path).not.toMatch(/eternal-packaging\//);
    expect(path).not.toMatch(/storage_path/);
  });
});
