import {
  canConfirmDelivered,
  canSubmitConfirmDelivered,
  confirmDeliveredBlockReason,
  MIN_DELIVERY_PROOFS,
  MAX_PHOTOS,
  proofUploadUserError,
  confirmableProofCount,
  hasValidRecipientName,
  authoritativeDriverDisplayName,
  deliveryProofClientError,
  deliveryProofCompressedSizeError,
  isDeliveryProofUploadBusy,
  emptyDeliveryPhotoSlotCount,
  DELIVERY_PROOF_FORMAT_MESSAGE,
  canUploadDeliveryBatchPhoto,
  deliveryBatchPhotoBlockReason,
  deliveryBatchPhotoReadiness,
  MSG_PHOTO_LOADING_INSUFFICIENT,
  MSG_PHOTO_PACKING_AND_LOADING_INSUFFICIENT,
  MSG_PHOTO_PACKING_INSUFFICIENT,
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

  test("rejects HEIC/HEIF before upload and lists backend-accepted formats", () => {
    expect(deliveryProofClientError({ name: "a.heic", type: "image/heic", size: 100 })).toBe(DELIVERY_PROOF_FORMAT_MESSAGE);
    expect(deliveryProofClientError({ name: "a.HEIF", type: "", size: 100 })).toBe(DELIVERY_PROOF_FORMAT_MESSAGE);
    expect(deliveryProofClientError({ name: "a.jpg", type: "image/jpeg", size: 100 })).toBeNull();
    expect(deliveryProofClientError({ name: "a.webp", type: "image/webp", size: 100 })).toBeNull();
    expect(deliveryProofClientError({ name: "a.gif", type: "image/gif", size: 100 })).toBeNull();
  });

  test("failed or in-progress local slots do not count as stored proof", () => {
    expect(canConfirmDelivered([{ id: "a" }])).toBe(false);
    expect(isDeliveryProofUploadBusy([{ status: "uploading" }])).toBe(true);
    expect(isDeliveryProofUploadBusy([{ status: "error" }])).toBe(false);
    expect(emptyDeliveryPhotoSlotCount(0, 0)).toBe(MIN_DELIVERY_PROOFS);
    expect(emptyDeliveryPhotoSlotCount(2, 0)).toBe(1);
    expect(emptyDeliveryPhotoSlotCount(MAX_PHOTOS, 0)).toBe(0);
  });

  test("compressed file over backend limit surfaces the existing 5MB message", () => {
    expect(deliveryProofCompressedSizeError({ size: 5 * 1024 * 1024 + 1 })).toMatch(/5MB/);
    expect(deliveryProofCompressedSizeError({ size: 100 })).toBeNull();
  });

  test("batch photo readiness uses physically-ready loaded qty, not full SJ", () => {
    const base = {
      line_items: [{ name: "Cup", quantity: 10000, line_id: "L0" }],
      packing_info: { qty_packed: 3000, lines: [{ line_idx: 0, packed_qty: 3000 }] },
      shipments: [{ id: "s1", number: "SJ-1", lines: [{ line_idx: 0, qty: 3000 }] }],
      loading_confirmations: [],
    };
    expect(deliveryBatchPhotoBlockReason(base)).toBe(MSG_PHOTO_LOADING_INSUFFICIENT);
    expect(canUploadDeliveryBatchPhoto(base)).toBe(false);

    const partialReady = {
      ...base,
      loading_confirmations: [{ id: "c1", surat_jalan_id: "s1", lines: [{ line_idx: 0, qty: 2000 }] }],
    };
    expect(canUploadDeliveryBatchPhoto(partialReady)).toBe(true);
    expect(deliveryBatchPhotoReadiness(partialReady).readyQty).toBe(2000);
    expect(deliveryBatchPhotoReadiness(partialReady).reason).toBeNull();

    const ready = {
      ...base,
      packing_info: { qty_packed: 4000, lines: [{ line_idx: 0, packed_qty: 4000 }] },
      shipments: [{ id: "s1", number: "SJ-1", lines: [{ line_idx: 0, qty: 4000 }] }],
      loading_confirmations: [{ id: "c1", surat_jalan_id: "s1", lines: [{ line_idx: 0, qty: 4000 }] }],
    };
    expect(canUploadDeliveryBatchPhoto(ready)).toBe(true);

    const noPack = {
      ...ready,
      packing_info: { qty_packed: 0, lines: [{ line_idx: 0, packed_qty: 0 }] },
      loading_confirmations: [],
    };
    expect(deliveryBatchPhotoBlockReason(noPack)).toBe(MSG_PHOTO_PACKING_AND_LOADING_INSUFFICIENT);

    const packLtLoaded = {
      ...base,
      packing_info: { qty_packed: 1500, lines: [{ line_idx: 0, packed_qty: 1500 }] },
      loading_confirmations: [{ id: "c1", surat_jalan_id: "s1", lines: [{ line_idx: 0, qty: 2000 }] }],
    };
    expect(deliveryBatchPhotoBlockReason(packLtLoaded)).toBe(MSG_PHOTO_PACKING_INSUFFICIENT);
  });
});
