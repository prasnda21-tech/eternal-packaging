import {
  canConfirmDelivered,
  canSubmitConfirmDelivered,
  confirmDeliveredBlockReason,
  MIN_DELIVERY_PROOFS,
  proofUploadUserError,
  confirmableProofCount,
  hasValidRecipientName,
  authoritativeDriverDisplayName,
} from "./deliveryProof";

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

  test("confirm delivered button appearance needs at least 2 stored photos", () => {
    expect(MIN_DELIVERY_PROOFS).toBe(2);
    expect(canConfirmDelivered([])).toBe(false);
    expect(canConfirmDelivered([{ id: "a" }])).toBe(false);
    expect(canConfirmDelivered([{ id: "a" }, { id: "b" }])).toBe(true);
    expect(canConfirmDelivered([{ id: "a" }, { id: "b" }, { id: "c" }])).toBe(true);
  });

  test("submit requires recipient+driver signatures and recipient name after 2 photos", () => {
    const proofs = [{ id: "a" }, { id: "b" }];
    const recipCanvas = "data:image/png;base64,xx";
    const driverCanvas = "data:image/png;base64,yy";
    expect(canSubmitConfirmDelivered(proofs, "", null, null, null, null)).toBe(false);
    expect(canSubmitConfirmDelivered(proofs, "Budi", null, null, null, null)).toBe(false);
    expect(canSubmitConfirmDelivered(proofs, "Budi", recipCanvas, null, null, null)).toBe(false);
    expect(canSubmitConfirmDelivered(proofs, "Budi", recipCanvas, null, driverCanvas, null)).toBe(true);
    expect(canSubmitConfirmDelivered(proofs, "", recipCanvas, null, driverCanvas, null)).toBe(false);
    expect(
      canSubmitConfirmDelivered(
        proofs,
        "",
        null,
        { id: "s1", recipient_name: "Budi", signature_storage_path: "x", frozen: false },
        null,
        { id: "d1", driver_signature_storage_path: "y", frozen: false },
      )
    ).toBe(true);
  });

  test("block reason messages are clear", () => {
    expect(confirmDeliveredBlockReason([{ id: "a" }], "Budi", "data:image/png;base64,x", null, null, null)).toMatch(/foto/i);
    expect(confirmDeliveredBlockReason([{ id: "a" }, { id: "b" }], "", null, null, null, null)).toMatch(/nama/i);
    expect(confirmDeliveredBlockReason([{ id: "a" }, { id: "b" }], "Budi", null, null, null, null)).toMatch(/tanda tangan/i);
    expect(
      confirmDeliveredBlockReason([{ id: "a" }, { id: "b" }], "Budi", "data:image/png;base64,x", null, null, null)
    ).toMatch(/driver/i);
    expect(
      confirmDeliveredBlockReason(
        [{ id: "a" }, { id: "b" }],
        "Budi",
        "data:image/png;base64,x",
        null,
        "data:image/png;base64,y",
        null,
      )
    ).toBeNull();
  });

  test("recipient name validation", () => {
    expect(hasValidRecipientName("")).toBe(false);
    expect(hasValidRecipientName("A")).toBe(false);
    expect(hasValidRecipientName("AB")).toBe(true);
  });

  test("admin login does not override loading checklist driver name", () => {
    expect(
      authoritativeDriverDisplayName(
        {
          loading_checklist: { driver_name: "Budi Driver Assigned" },
          delivery_driver_signature: { driver_name: "Local Preview Admin" },
        },
        { name: "Local Preview Admin" },
      )
    ).toBe("Budi Driver Assigned");
  });

  test("403 role-gate copy is mapped to a permission message", () => {
    const msg = proofUploadUserError({
      response: { status: 403, data: { detail: "Aksi ini tidak tersedia untuk role Anda" } },
    });
    expect(msg).toBe("Anda tidak punya izin untuk aksi ini.");
    expect(msg).not.toMatch(/role Anda/);
  });

  test("403 permission copy is kept", () => {
    const msg = proofUploadUserError({
      response: { status: 403, data: { detail: "Permission 'delivery.update' diperlukan" } },
    });
    expect(msg).toBe("Permission 'delivery.update' diperlukan");
  });
});
