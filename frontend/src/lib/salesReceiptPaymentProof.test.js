import {
  validateReceiptProofFile,
  receiptHasPaymentProof,
  receiptPaymentStateFromAmounts,
  buildReceiptProofPayload,
  canViewSalesReceiptPaymentProof,
  SALES_RECEIPT_PROOF_VIEW_PERMS,
} from "./salesReceiptPaymentProof";

describe("salesReceiptPaymentProof", () => {
  test("create form validation accepts image/pdf", () => {
    expect(validateReceiptProofFile({ name: "a.jpg", type: "image/jpeg", size: 100 }).ok).toBe(true);
    expect(validateReceiptProofFile({ name: "a.png", type: "image/png", size: 100 }).ok).toBe(true);
    expect(validateReceiptProofFile({ name: "a.pdf", type: "application/pdf", size: 100 }).ok).toBe(true);
  });

  test("rejects invalid type and oversized", () => {
    const bad = validateReceiptProofFile({ name: "x.exe", type: "application/octet-stream", size: 10 });
    expect(bad.ok).toBe(false);
    expect(bad.error).toMatch(/tidak didukung/i);

    const big = validateReceiptProofFile({ name: "a.png", type: "image/png", size: 7 * 1024 * 1024 });
    expect(big.ok).toBe(false);
    expect(big.error).toMatch(/6MB/i);
  });

  test("requires file", () => {
    expect(validateReceiptProofFile(null).ok).toBe(false);
  });

  test("frontend does not derive LUNAS from file — amounts only", () => {
    expect(receiptPaymentStateFromAmounts(3000, 10000, 10000)).toBe("DP");
    expect(receiptPaymentStateFromAmounts(10000, 10000, 10000)).toBe("LUNAS");
  });

  test("receiptHasPaymentProof handles historical without proof", () => {
    expect(receiptHasPaymentProof(null)).toBe(false);
    expect(receiptHasPaymentProof({})).toBe(false);
    expect(receiptHasPaymentProof({ has_payment_proof: false })).toBe(false);
    expect(receiptHasPaymentProof({ has_payment_proof: true, payment_proof: { id: "f1" } })).toBe(true);
    expect(receiptHasPaymentProof({ payment_proof: { id: "f1", name: "a.png" } })).toBe(true);
  });

  test("buildReceiptProofPayload carries name + data_url", async () => {
    const file = new File([new Uint8Array([1, 2, 3])], "bukti.png", { type: "image/png" });
    const payload = await buildReceiptProofPayload(file);
    expect(payload.name).toBe("bukti.png");
    expect(payload.data_url.startsWith("data:image/png;base64,")).toBe(true);
  });

  test("view gate uses receipt/payment view only — not po.view", () => {
    expect(SALES_RECEIPT_PROOF_VIEW_PERMS).toEqual(["sales.receipt.view", "payment.view"]);
    expect(SALES_RECEIPT_PROOF_VIEW_PERMS).not.toContain("po.view");
    expect(canViewSalesReceiptPaymentProof((p) => p === "po.view")).toBe(false);
    expect(canViewSalesReceiptPaymentProof((p) => p === "sales.receipt.view")).toBe(true);
    expect(canViewSalesReceiptPaymentProof((p) => p === "payment.view")).toBe(true);
    expect(canViewSalesReceiptPaymentProof((p) => p === "sales.invoice.view")).toBe(false);
  });
});
