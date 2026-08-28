import { confirmableProofCount, canConfirmDelivered, MIN_DELIVERY_PROOFS, proofUploadUserError } from "./deliveryProof";

describe("deliveryProof helpers", () => {
  test("confirmable count includes ready files", () => {
    expect(confirmableProofCount([{ status: "ready", file: {} }])).toBe(1);
  });

  test("failed upload with a file still counts so retry is possible", () => {
    expect(confirmableProofCount([
      { status: "error", file: {}, error: "Gagal" },
    ])).toBe(1);
  });

  test("done files are not counted as pending confirm", () => {
    expect(confirmableProofCount([{ status: "done", file: {} }])).toBe(0);
  });

  test("hides EMERGENT_LLM_KEY from the user", () => {
    const msg = proofUploadUserError({
      response: { status: 502, data: { detail: "Object storage tidak terkonfigurasi (EMERGENT_LLM_KEY belum di-set)" } },
    });
    expect(msg).toBe("Gagal mengunggah foto. Silakan coba lagi.");
    expect(msg).not.toMatch(/EMERGENT_LLM_KEY/);
  });

  test("confirm delivered needs at least 2 stored photos", () => {
    expect(MIN_DELIVERY_PROOFS).toBe(2);
    expect(canConfirmDelivered([])).toBe(false);
    expect(canConfirmDelivered([{ id: "a" }])).toBe(false);
    expect(canConfirmDelivered([{ id: "a" }, { id: "b" }])).toBe(true);
    expect(canConfirmDelivered([{ id: "a" }, { id: "b" }, { id: "c" }])).toBe(true);
  });
});
