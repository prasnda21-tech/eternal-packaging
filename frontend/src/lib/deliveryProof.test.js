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
  deliveryBatchPhotoBlockCode,
  REASON_LOADING_INCOMPLETE,
  REASON_LOADING_ITEM_MISMATCH,
  MSG_PHOTO_SJ_NOT_READY,
  activeDeliveryBatch,
  findDeliveryShipment,
  isShipmentDelivered,
  pengirimanSjOptions,
  proofsForShipment,
  selectedPengirimanShipmentId,
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

  test("batch photo readiness follows Loading vs SJ per line, not Packing", () => {
    const base = {
      line_items: [{ name: "Cup", quantity: 10000, line_id: "L0", production_process: "print" }],
      packing_info: { qty_packed: 3000, lines: [{ line_idx: 0, packed_qty: 3000 }] },
      shipments: [{ id: "s1", number: "SJ-1", lines: [{ line_idx: 0, qty: 3000 }] }],
      loading_confirmations: [],
    };
    expect(deliveryBatchPhotoBlockCode(base)).toBe(REASON_LOADING_INCOMPLETE);
    expect(canUploadDeliveryBatchPhoto(base)).toBe(false);

    const partialReady = {
      ...base,
      loading_confirmations: [{ id: "c1", surat_jalan_id: "s1", lines: [{ line_idx: 0, qty: 2000 }] }],
    };
    expect(canUploadDeliveryBatchPhoto(partialReady)).toBe(false);
    expect(deliveryBatchPhotoBlockCode(partialReady)).toBe(REASON_LOADING_INCOMPLETE);

    const ready = {
      ...base,
      packing_info: { qty_packed: 4000, lines: [{ line_idx: 0, packed_qty: 4000 }] },
      shipments: [{ id: "s1", number: "SJ-1", lines: [{ line_idx: 0, qty: 4000 }] }],
      loading_confirmations: [{ id: "c1", surat_jalan_id: "s1", lines: [{ line_idx: 0, qty: 4000 }] }],
    };
    expect(canUploadDeliveryBatchPhoto(ready)).toBe(true);
    expect(deliveryBatchPhotoReadiness(ready).reason).toBeNull();

    const noLoading = {
      ...ready,
      packing_info: { qty_packed: 0, lines: [{ line_idx: 0, packed_qty: 0 }] },
      loading_confirmations: [],
    };
    expect(deliveryBatchPhotoBlockCode(noLoading)).toBe(REASON_LOADING_INCOMPLETE);

    // Packing lower than loaded must NOT independently block Delivery readiness
    const packLtLoadedButSjLoaded = {
      ...base,
      packing_info: { qty_packed: 1500, lines: [{ line_idx: 0, packed_qty: 1500 }] },
      loading_confirmations: [{ id: "c1", surat_jalan_id: "s1", lines: [{ line_idx: 0, qty: 3000 }] }],
    };
    expect(canUploadDeliveryBatchPhoto(packLtLoadedButSjLoaded)).toBe(true);
  });

  test("cup + LID: packing excludes LID but full Loading enables photos", () => {
    const order = {
      line_items: [
        { name: "Cup", quantity: 5000, line_id: "L0", production_process: "print" },
        { name: "LID", quantity: 5000, line_id: "L1", production_process: "non_print" },
      ],
      packing_info: { qty_packed: 5000, lines: [{ line_idx: 0, packed_qty: 5000 }] },
      shipments: [{
        id: "s1",
        number: "SJ-1",
        lines: [{ line_idx: 0, qty: 5000 }, { line_idx: 1, qty: 5000 }],
        total_qty: 10000,
      }],
      loading_confirmations: [{
        id: "c1",
        surat_jalan_id: "s1",
        lines: [{ line_idx: 0, qty: 5000 }, { line_idx: 1, qty: 5000 }],
      }],
    };
    expect(canUploadDeliveryBatchPhoto(order)).toBe(true);
    expect(deliveryBatchPhotoReadiness(order).packedAvailable).toBe(5000);
    expect(deliveryBatchPhotoReadiness(order).loadedTotal).toBe(10000);

    const missingLid = {
      ...order,
      loading_confirmations: [{
        id: "c1",
        surat_jalan_id: "s1",
        lines: [{ line_idx: 0, qty: 5000 }, { line_idx: 1, qty: 0 }],
      }],
    };
    expect(canUploadDeliveryBatchPhoto(missingLid)).toBe(false);
    expect(deliveryBatchPhotoBlockReason(missingLid)).toMatch(/LID|Loading/i);

    const wrongComposition = {
      ...order,
      loading_confirmations: [{
        id: "c1",
        surat_jalan_id: "s1",
        lines: [{ line_idx: 0, qty: 10000 }, { line_idx: 1, qty: 0 }],
      }],
    };
    expect(canUploadDeliveryBatchPhoto(wrongComposition)).toBe(false);
    expect([REASON_LOADING_INCOMPLETE, REASON_LOADING_ITEM_MISMATCH]).toContain(
      deliveryBatchPhotoBlockCode(wrongComposition),
    );
  });

  test("proof gates still require photos, name, and both signatures", () => {
    const proofs = [{ id: "p1" }, { id: "p2" }];
    expect(canSubmitConfirmDelivered(proofs, "", null, null, null, null)).toBe(false);
    expect(canSubmitConfirmDelivered(proofs, "Budi", "data:image/png;base64,x", null, "data:image/png;base64,y", null)).toBe(true);
  });

  test("no SJ yields SJ_NOT_READY", () => {
    const o = { line_items: [{ name: "Cup", quantity: 100 }], shipments: [], loading_confirmations: [] };
    expect(deliveryBatchPhotoBlockReason(o)).toBe(MSG_PHOTO_SJ_NOT_READY);
  });

  test("legacy multi-item shipment without SJ lines blocks aggregate bypass", () => {
    const o = {
      line_items: [
        { name: "Cup", quantity: 5000, production_process: "print" },
        { name: "LID", quantity: 5000, production_process: "non_print" },
      ],
      packing_info: { qty_packed: 5000, lines: [{ line_idx: 0, packed_qty: 5000 }] },
      shipments: [{ id: "s1", number: "SJ-1", total_qty: 10000 }],
      loading_confirmations: [{
        id: "c1", surat_jalan_id: "s1",
        lines: [{ line_idx: 0, qty: 10000 }],
      }],
    };
    expect(canUploadDeliveryBatchPhoto(o)).toBe(false);
    expect(deliveryBatchPhotoBlockCode(o)).toBe(REASON_LOADING_ITEM_MISMATCH);
  });

  test("Pengiriman selected SJ isolates qty, loading, and delivered state", () => {
    const sj1 = { id: "sj-1", number: "SJ/001", delivery_date: "2026-09-10", lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }], total_qty: 1000 };
    const sj2 = { id: "sj-2", number: "SJ/001_2", delivery_date: "2026-09-12", lines: [{ line_idx: 0, line_id: "LA", qty: 1000 }], total_qty: 1000 };
    const order = {
      line_items: [{ name: "Line A", quantity: 2000, line_id: "LA", production_process: "print" }],
      packing_info: { qty_packed: 2000, lines: [{ line_idx: 0, packed_qty: 2000 }] },
      shipments: [sj1, sj2],
      loading_confirmations: [{ id: "c1", surat_jalan_id: "sj-1", lines: [{ line_idx: 0, qty: 1000 }] }],
      delivery_proofs: [{ id: "p1", shipment_id: "sj-1" }, { id: "p2", shipment_id: "sj-1" }],
      delivery_confirmations: [],
    };
    expect(pengirimanSjOptions(order).map((o) => o.id)).toEqual(["sj-1", "sj-2"]);
    expect(activeDeliveryBatch(order).id).toBe("sj-2");
    expect(canUploadDeliveryBatchPhoto(order)).toBe(false);
    expect(canUploadDeliveryBatchPhoto(order, sj1)).toBe(true);
    expect(canUploadDeliveryBatchPhoto(order, sj2)).toBe(false);
    expect(deliveryBatchPhotoReadiness(order, sj1).requiredTotal).toBe(1000);
    expect(deliveryBatchPhotoReadiness(order, sj2).requiredTotal).toBe(1000);
    expect(proofsForShipment(order, sj1)).toHaveLength(2);
    expect(proofsForShipment(order, sj2)).toHaveLength(0);
    const shippedSj1 = {
      ...order,
      shipments: [{ ...sj1, delivered_at: "t1", delivery_status: "Terkirim" }, sj2],
      delivery_confirmations: [{ id: "d1", shipment_id: "sj-1", qty: 1000 }],
    };
    expect(isShipmentDelivered(shippedSj1, shippedSj1.shipments[0])).toBe(true);
    expect(isShipmentDelivered(shippedSj1, sj2)).toBe(false);
    expect(findDeliveryShipment(order, "sj-1").id).toBe("sj-1");
    expect(selectedPengirimanShipmentId(order, "sj-1")).toBe("sj-1");
  });

  test("same-name lines stay isolated by line_idx in selected SJ readiness", () => {
    const sj1 = { id: "sj-1", number: "SJ1", lines: [{ line_idx: 0, line_id: "L0", qty: 1000 }], total_qty: 1000 };
    const sj2 = { id: "sj-2", number: "SJ2", lines: [{ line_idx: 1, line_id: "L1", qty: 1000 }], total_qty: 1000 };
    const order = {
      line_items: [
        { name: "Item X", quantity: 1000, line_id: "L0", accurate_item_id: "AX", production_process: "print" },
        { name: "Item X", quantity: 1000, line_id: "L1", accurate_item_id: "AX", production_process: "print" },
      ],
      packing_info: { qty_packed: 2000, lines: [{ line_idx: 0, packed_qty: 1000 }, { line_idx: 1, packed_qty: 1000 }] },
      shipments: [sj1, sj2],
      loading_confirmations: [{ id: "c1", surat_jalan_id: "sj-1", lines: [{ line_idx: 0, qty: 1000 }] }],
    };
    const r1 = deliveryBatchPhotoReadiness(order, sj1);
    const r2 = deliveryBatchPhotoReadiness(order, sj2);
    expect(r1.allowed).toBe(true);
    expect(r1.lines.map((l) => l.lineIdx)).toEqual([0]);
    expect(r2.allowed).toBe(false);
    expect(r2.lines.map((l) => l.lineIdx)).toEqual([1]);
  });

  test("legacy single-item shipment without SJ lines remains supported", () => {
    const o = {
      line_items: [{ name: "Cup", quantity: 3000, production_process: "print" }],
      packing_info: { qty_packed: 3000, lines: [{ line_idx: 0, packed_qty: 3000 }] },
      shipments: [{ id: "s1", number: "SJ-1", total_qty: 3000 }],
      loading_confirmations: [{
        id: "c1", surat_jalan_id: "s1",
        lines: [{ line_idx: 0, qty: 3000 }],
      }],
    };
    expect(canUploadDeliveryBatchPhoto(o)).toBe(true);
  });
});
