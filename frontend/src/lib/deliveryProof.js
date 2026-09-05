export const MAX_PHOTOS = 5;
export const MIN_DELIVERY_PROOFS = 2;

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
