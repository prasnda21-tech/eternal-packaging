export const MAX_PHOTOS = 5;
export const MIN_DELIVERY_PROOFS = 2;
export const DELIVERY_PROOF_ACCEPT = "image/*";
export const DELIVERY_PROOF_ACCEPTED_TYPES = [
  "image/jpeg",
  "image/jpg",
  "image/png",
  "image/webp",
  "image/gif",
];
export const DELIVERY_PROOF_MAX_UPLOAD_BYTES = 5 * 1024 * 1024;
export const DELIVERY_PROOF_CLIENT_MAX_BYTES = 15 * 1024 * 1024;
export const DELIVERY_PROOF_FORMAT_MESSAGE =
  "Format foto belum didukung. Pilih foto JPG, PNG, WEBP, atau GIF.";

export function isAcceptedDeliveryProofType(type) {
  return DELIVERY_PROOF_ACCEPTED_TYPES.includes(String(type || "").toLowerCase());
}

export function deliveryProofClientError(file) {
  if (!file) return "Pilih foto terlebih dahulu.";
  const type = String(file.type || "").toLowerCase();
  const name = String(file.name || "");
  if (/heic|heif/.test(type) || /\.(heic|heif)$/i.test(name)) {
    return DELIVERY_PROOF_FORMAT_MESSAGE;
  }
  if (type && !type.startsWith("image/")) {
    return DELIVERY_PROOF_FORMAT_MESSAGE;
  }
  if (type && !isAcceptedDeliveryProofType(type)) {
    return DELIVERY_PROOF_FORMAT_MESSAGE;
  }
  if (Number(file.size || 0) > DELIVERY_PROOF_CLIENT_MAX_BYTES) {
    return "Ukuran file terlalu besar. Pilih foto lain atau kompres di HP.";
  }
  return null;
}

export function deliveryProofCompressedSizeError(file) {
  if (file && Number(file.size || 0) > DELIVERY_PROOF_MAX_UPLOAD_BYTES) {
    return "Ukuran file maksimal 5MB (silakan kompres di HP)";
  }
  return null;
}

export function isDeliveryProofUploadBusy(slots) {
  return (slots || []).some((s) => s.status === "selecting" || s.status === "uploading");
}

/** Empty add-slots to render: fill up to the minimum, then one extra Tambah Foto until max. */
export function emptyDeliveryPhotoSlotCount(storedCount, pendingCount = 0, min = MIN_DELIVERY_PROOFS, max = MAX_PHOTOS) {
  const used = Number(storedCount || 0) + Number(pendingCount || 0);
  if (used >= max) return 0;
  if (used < min) return min - used;
  return 1;
}

export function confirmableProofCount(staged) {
  return (staged || []).filter((s) => s.status === "ready" || (s.status === "error" && s.file)).length;
}

export function storedProofCount(proofs) {
  return Array.isArray(proofs) ? proofs.length : 0;
}

/** Button appearance rule: still based on existing 2-photo requirement only. */
export function canConfirmDelivered(proofs) {
  return storedProofCount(proofs) >= MIN_DELIVERY_PROOFS;
}

export function normalizeRecipientName(name) {
  return String(name || "").trim();
}

export function hasValidRecipientName(name) {
  return normalizeRecipientName(name).length >= 2;
}

function hasSavedRecipientSig(orderSig) {
  return !!(orderSig && orderSig.id && orderSig.frozen !== true
    && (orderSig.signature_storage_path || orderSig.recipient_signed_at)
    && hasValidRecipientName(orderSig.recipient_name));
}

function hasSavedDriverSig(driverSig) {
  return !!(driverSig && driverSig.id && driverSig.frozen !== true
    && (driverSig.signature_storage_path || driverSig.driver_signature_storage_path || driverSig.driver_signed_at));
}

function hasCanvasSig(dataUrl) {
  return !!(dataUrl && String(dataUrl).startsWith("data:image/"));
}

/** Submit eligibility: 2 photos + name + recipient sig + driver sig. */
export function canSubmitConfirmDelivered(
  proofs,
  recipientName,
  signatureDataUrl,
  orderSig,
  driverSignatureDataUrl,
  driverSig,
) {
  if (!canConfirmDelivered(proofs)) return false;
  const name = normalizeRecipientName(recipientName || orderSig?.recipient_name);
  if (!hasValidRecipientName(name)) return false;
  const recipientOk = hasSavedRecipientSig(orderSig) || hasCanvasSig(signatureDataUrl);
  const driverOk = hasSavedDriverSig(driverSig) || hasCanvasSig(driverSignatureDataUrl);
  return recipientOk && driverOk;
}

export function confirmDeliveredBlockReason(
  proofs,
  recipientName,
  signatureDataUrl,
  orderSig,
  driverSignatureDataUrl,
  driverSig,
) {
  if (!canConfirmDelivered(proofs)) {
    return `Wajib upload minimal ${MIN_DELIVERY_PROOFS} foto bukti kirim sebelum Konfirmasi Terkirim.`;
  }
  const name = normalizeRecipientName(recipientName || orderSig?.recipient_name);
  if (!hasValidRecipientName(name)) {
    return "Nama penerima wajib diisi sebelum Konfirmasi Terkirim.";
  }
  if (!hasSavedRecipientSig(orderSig) && !hasCanvasSig(signatureDataUrl)) {
    return "Tanda tangan penerima wajib sebelum Konfirmasi Terkirim.";
  }
  if (!hasSavedDriverSig(driverSig) && !hasCanvasSig(driverSignatureDataUrl)) {
    return "Tanda tangan driver/pengirim wajib sebelum Konfirmasi Terkirim.";
  }
  return null;
}

export function authoritativeDriverDisplayName(order, user) {
  const lc = order?.loading_checklist || {};
  if (lc.driver_name && String(lc.driver_name).trim()) return String(lc.driver_name).trim();
  const hist = order?.delivery_confirmations || [];
  for (let i = hist.length - 1; i >= 0; i -= 1) {
    const n = hist[i]?.driver_name;
    if (n && String(n).trim()) return String(n).trim();
  }
  const ships = order?.shipments || [];
  for (let i = 0; i < ships.length; i += 1) {
    const n = ships[i]?.driver_name;
    if (n && String(n).trim()) return String(n).trim();
  }
  // Signature-stored name is only a fallback when shipment has no assigned driver.
  // Prefer loading/confirmation over any actor-tainted signature label.
  if (order?.delivery_driver_signature?.driver_name) {
    return String(order.delivery_driver_signature.driver_name).trim();
  }
  if (user?.name) return String(user.name).trim();
  return "";
}

export function proofUploadUserError(ex) {
  const raw = ex?.response?.data?.detail ?? ex?.message ?? "";
  const text = typeof raw === "string" ? raw : (raw?.message || "");
  const status = ex?.response?.status;
  if (status === 502 || /EMERGENT_LLM_KEY|object storage|tidak terkonfigurasi|S3_BUCKET/i.test(text)) {
    return "Gagal mengunggah foto. Silakan coba lagi.";
  }
  if (status === 403) {
    if (/role Anda/i.test(text)) {
      return "Anda tidak punya izin untuk aksi ini.";
    }
    return text || "Anda tidak punya izin untuk aksi ini.";
  }
  return text || "Gagal mengunggah foto. Silakan coba lagi.";
}
