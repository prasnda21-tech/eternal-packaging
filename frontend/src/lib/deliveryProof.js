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

export const MSG_PHOTO_PACKING_INSUFFICIENT =
  "Qty Packing untuk pengiriman ini belum mencukupi.";
export const MSG_PHOTO_LOADING_INSUFFICIENT =
  "Qty Loading untuk pengiriman ini belum mencukupi.";
export const MSG_PHOTO_PACKING_AND_LOADING_INSUFFICIENT =
  "Foto pengiriman dapat diinput setelah qty Packing dan Loading untuk pengiriman ini mencukupi.";

function _safeInt(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

function _shipments(order) {
  return Array.isArray(order?.shipments) ? order.shipments.filter((s) => s && typeof s === "object") : [];
}

export function activeDeliveryBatch(order) {
  const ships = _shipments(order);
  return ships.length ? ships[ships.length - 1] : null;
}

export function deliveryBatchIdentity(shipment) {
  if (!shipment || typeof shipment !== "object") return "";
  return String(shipment.id || shipment.sales_delivery_id || shipment.number || "").trim();
}

export function deliveryBatchQty(shipment) {
  if (!shipment || typeof shipment !== "object") return 0;
  const lines = Array.isArray(shipment.lines) ? shipment.lines : [];
  if (lines.length) {
    return lines.reduce((sum, ln) => {
      if (!ln || typeof ln !== "object") return sum;
      return sum + Math.max(0, _safeInt(ln.qty ?? ln.quantity));
    }, 0);
  }
  return Math.max(0, _safeInt(shipment.total_qty ?? shipment.qty));
}

function _packedQtyForLine(order, lineIdx) {
  const lines = order?.packing_info?.lines;
  if (Array.isArray(lines) && lines.length) {
    const row = lines.find((l) => _safeInt(l?.line_idx, -1) === Number(lineIdx));
    return Math.max(0, _safeInt(row?.packed_qty));
  }
  if (Number(lineIdx) === 0) {
    return Math.max(0, _safeInt(order?.packing_info?.qty_packed));
  }
  return 0;
}

function _shippedQtyForLine(order, lineIdx, excludeId = "") {
  let total = 0;
  const skip = String(excludeId || "").trim();
  for (const sh of _shipments(order)) {
    const sid = String(sh.id || "");
    const acc = String(sh.accurate_shipment_id || "");
    if (skip && (skip === sid || skip === acc || skip === String(sh.sales_delivery_id || ""))) continue;
    for (const it of sh.lines || []) {
      if (_safeInt(it?.line_idx, -1) === Number(lineIdx)) {
        total += Math.max(0, _safeInt(it.qty ?? it.quantity));
      }
    }
  }
  return total;
}

function _loadedQtyForSjLine(order, suratJalanId, lineIdx) {
  const want = String(suratJalanId || "").trim();
  if (!want) return 0;
  const targetIds = new Set([want]);
  for (const sh of _shipments(order)) {
    const ids = [sh.id, sh.number, sh.sales_delivery_id].map((v) => String(v || "").trim()).filter(Boolean);
    if (ids.includes(want)) ids.forEach((id) => targetIds.add(id));
  }
  let total = 0;
  for (const conf of order?.loading_confirmations || []) {
    if (conf?.cancelled) continue;
    if (!targetIds.has(String(conf?.surat_jalan_id || "").trim())) continue;
    for (const ln of conf.lines || []) {
      if (_safeInt(ln?.line_idx, -1) === Number(lineIdx)) {
        total += Math.max(0, _safeInt(ln.qty ?? ln.quantity));
      }
    }
  }
  return total;
}

export function packedAvailableForDeliveryBatch(order, shipment = null) {
  const sh = shipment || activeDeliveryBatch(order);
  if (!sh) return 0;
  const sid = deliveryBatchIdentity(sh);
  const lines = Array.isArray(sh.lines) ? sh.lines : [];
  if (!lines.length) return 0;
  let avail = 0;
  for (const ln of lines) {
    if (!ln || typeof ln !== "object") continue;
    const idx = _safeInt(ln.line_idx, -1);
    if (idx < 0) continue;
    const need = Math.max(0, _safeInt(ln.qty ?? ln.quantity));
    const lineAvail = Math.max(0, _packedQtyForLine(order, idx) - _shippedQtyForLine(order, idx, sid));
    avail += need ? Math.min(lineAvail, need) : lineAvail;
  }
  return avail;
}

export function loadedQtyForDeliveryBatch(order, shipment = null) {
  const sh = shipment || activeDeliveryBatch(order);
  if (!sh) return 0;
  const sid = deliveryBatchIdentity(sh);
  if (!sid) return 0;
  const lines = Array.isArray(sh.lines) ? sh.lines : [];
  if (!lines.length) return 0;
  return lines.reduce((sum, ln) => {
    if (!ln || typeof ln !== "object") return sum;
    const idx = _safeInt(ln.line_idx, -1);
    if (idx < 0) return sum;
    return sum + _loadedQtyForSjLine(order, sid, idx);
  }, 0);
}

/** UX mirror of backend delivery_batch_photo_readiness — server remains authoritative. */
export function deliveryBatchPhotoReadiness(order, shipment = null) {
  const sh = shipment || activeDeliveryBatch(order);
  const sjQty = deliveryBatchQty(sh);
  const packedAvail = packedAvailableForDeliveryBatch(order, sh);
  const loaded = loadedQtyForDeliveryBatch(order, sh);
  // Physically ready batch = loaded (not full SJ). Cap by SJ allocation.
  const readyQty = sjQty > 0 ? Math.min(loaded, sjQty) : 0;
  const loadedOk = readyQty > 0;
  let packedOk = loadedOk && packedAvail >= readyQty;
  if (loaded > packedAvail) packedOk = false;
  const allowed = Boolean(loadedOk && packedOk);
  let reason = null;
  if (!allowed) {
    if (!loadedOk && packedAvail <= 0) reason = MSG_PHOTO_PACKING_AND_LOADING_INSUFFICIENT;
    else if (!loadedOk) reason = MSG_PHOTO_LOADING_INSUFFICIENT;
    else if (!packedOk) reason = MSG_PHOTO_PACKING_INSUFFICIENT;
    else reason = MSG_PHOTO_PACKING_AND_LOADING_INSUFFICIENT;
  }
  return {
    allowed,
    batchQty: sjQty,
    readyQty,
    packedAvailable: packedAvail,
    loaded,
    shipmentId: deliveryBatchIdentity(sh),
    reason,
  };
}

export function deliveryBatchPhotoBlockReason(order, shipment = null) {
  return deliveryBatchPhotoReadiness(order, shipment).reason || null;
}

export function canUploadDeliveryBatchPhoto(order, shipment = null) {
  return deliveryBatchPhotoReadiness(order, shipment).allowed === true;
}
