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
export const MSG_PHOTO_SJ_NOT_READY =
  "Surat Jalan untuk pengiriman ini belum siap.";
export const MSG_PHOTO_LOADING_ITEM_MISMATCH =
  "Qty Loading per item belum mencukupi untuk Surat Jalan pengiriman ini.";

export const REASON_SJ_NOT_READY = "SJ_NOT_READY";
export const REASON_LOADING_INCOMPLETE = "LOADING_INCOMPLETE";
export const REASON_LOADING_ITEM_MISMATCH = "LOADING_ITEM_MISMATCH";

function _safeInt(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

function _shipments(order) {
  return Array.isArray(order?.shipments) ? order.shipments.filter((s) => s && typeof s === "object") : [];
}

function _lineName(order, lineIdx) {
  const li = (order?.line_items || [])[lineIdx];
  if (li && typeof li === "object") {
    return String(li.name || li.item_name || li.product_name || "").trim() || `Line ${lineIdx}`;
  }
  return `Line ${lineIdx}`;
}

function _isPrintLine(li) {
  const p = String(li?.production_process || "").trim().toLowerCase();
  return p !== "non_print";
}

export function activeDeliveryBatch(order) {
  const ships = _shipments(order);
  return ships.length ? ships[ships.length - 1] : null;
}

export function shipmentIdentityKeys(order, shipment) {
  const keys = new Set();
  if (!shipment || typeof shipment !== "object") return keys;
  ["id", "sales_delivery_id", "number", "accurate_shipment_id", "sj_number"].forEach((k) => {
    const v = String(shipment[k] || "").trim();
    if (v) keys.add(v);
  });
  const files = Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : [];
  for (const f of files) {
    const fKeys = [f?.id, f?.file_id, f?.shipment_id, f?.sj_number, f?.number, f?.sales_delivery_id]
      .map((v) => String(v || "").trim())
      .filter(Boolean);
    if (fKeys.some((k) => keys.has(k))) fKeys.forEach((k) => keys.add(k));
  }
  return keys;
}

export function findDeliveryShipment(order, shipmentId) {
  const ships = _shipments(order);
  const want = String(shipmentId || "").trim();
  if (!want) return ships.length === 1 ? ships[0] : null;
  return ships.find((sh) => shipmentIdentityKeys(order, sh).has(want)) || null;
}

export function qtyDeliveredForShipment(order, shipment) {
  if (!shipment || typeof shipment !== "object") return 0;
  if (String(shipment.delivery_status || "").trim() === "Terkirim" || shipment.delivered_at) {
    return deliveryBatchQty(shipment);
  }
  const keys = shipmentIdentityKeys(order, shipment);
  let total = 0;
  for (const rec of order?.delivery_confirmations || []) {
    if (keys.has(String(rec?.shipment_id || "").trim())) {
      total += Math.max(0, _safeInt(rec?.qty));
    }
  }
  return total;
}

export function isShipmentDelivered(order, shipment) {
  const batch = deliveryBatchQty(shipment);
  if (!shipment || batch <= 0) return false;
  if (String(shipment.delivery_status || "").trim() === "Terkirim" || shipment.delivered_at) return true;
  return qtyDeliveredForShipment(order, shipment) >= batch;
}

/** Operator-facing SJ text. Never include internal shipment UUID. */
export function pengirimanSjDisplayLabel(opt, { includeDelivered = false } = {}) {
  const number = String(opt?.number || "").trim();
  if (!number) return "";
  if (includeDelivered && opt?.delivered) return `${number} · Terkirim`;
  return number;
}

export function pengirimanSjOptions(order) {
  return _shipments(order).map((sh, i) => {
    const id = deliveryBatchIdentity(sh) || `sj-${i}`;
    const number = String(sh.number || sh.sj_number || `SJ #${sh.shipment_index || i + 1}`);
    const date = String(sh.delivery_date || "").slice(0, 10);
    const qty = deliveryBatchQty(sh);
    const delivered = isShipmentDelivered(order, sh);
    const display = pengirimanSjDisplayLabel({ number, delivered }, { includeDelivered: true });
    return {
      id,
      shipment: sh,
      number,
      date,
      qty,
      delivered,
      shipmentIndex: sh.shipment_index || i + 1,
      label: display,
    };
  });
}

export function proofsForShipment(order, shipment) {
  const all = Array.isArray(order?.delivery_proofs) ? order.delivery_proofs.filter((p) => p && p.id) : [];
  if (!shipment) return all;
  const keys = shipmentIdentityKeys(order, shipment);
  const tagged = all.filter((p) => keys.has(String(p.shipment_id || "").trim()));
  if (tagged.length) return tagged;
  // Multi-SJ: never assign untagged/legacy proofs to an arbitrary sibling.
  if (_shipments(order).length > 1) return [];
  return all.filter((p) => !String(p.shipment_id || "").trim());
}

/** Upload/sign/confirm lock. Multi-SJ uses exact shipment evidence, not PO aggregates. */
export function isPengirimanMutationLocked(order, shipment) {
  if (isShipmentDelivered(order, shipment)) return true;
  if (_shipments(order).length > 1) return false;
  return order?.delivery_status === "Terkirim" || !!order?.terkirim_confirmed;
}

export function selectedPengirimanShipmentId(order, preferredId = "") {
  const options = pengirimanSjOptions(order);
  const want = String(preferredId || "").trim();
  if (want) {
    const hit = options.find((o) => o.id === want || shipmentIdentityKeys(order, o.shipment).has(want));
    return hit?.id || "";
  }
  if (options.length === 1) return options[0].id || "";
  return "";
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
    if (conf?.cancelled || conf?.deleted) continue;
    if (!targetIds.has(String(conf?.surat_jalan_id || "").trim())) continue;
    for (const ln of conf.lines || []) {
      if (_safeInt(ln?.line_idx, -1) === Number(lineIdx)) {
        total += Math.max(0, _safeInt(ln.qty ?? ln.quantity));
      }
    }
  }
  return total;
}

/** Diagnostic only — Delivery readiness does not gate on Packing. */
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
  if (lines.length) {
    return lines.reduce((sum, ln) => {
      if (!ln || typeof ln !== "object") return sum;
      const idx = _safeInt(ln.line_idx, -1);
      if (idx < 0) return sum;
      return sum + _loadedQtyForSjLine(order, sid, idx);
    }, 0);
  }
  // Legacy shipment without line breakdown: sum confirmations linked to this SJ
  const targetIds = new Set([sid]);
  ["id", "number", "sales_delivery_id"].forEach((k) => {
    const v = String(sh[k] || "").trim();
    if (v) targetIds.add(v);
  });
  let total = 0;
  for (const conf of order?.loading_confirmations || []) {
    if (conf?.cancelled || conf?.deleted) continue;
    if (!targetIds.has(String(conf?.surat_jalan_id || "").trim())) continue;
    for (const ln of conf.lines || []) {
      total += Math.max(0, _safeInt(ln?.qty ?? ln?.quantity));
    }
  }
  return total;
}

function _resolvedSjLines(order, shipment) {
  const raw = Array.isArray(shipment?.lines) ? shipment.lines.filter((ln) => ln && typeof ln === "object") : [];
  if (raw.length) return raw;
  const sid = deliveryBatchIdentity(shipment);
  if (!sid) return [];
  const files = Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : [];
  for (const f of files) {
    if (!f || typeof f !== "object") continue;
    const ids = [f.id, f.shipment_id, f.sj_number, f.number]
      .map((v) => String(v || "").trim())
      .filter(Boolean);
    if (!ids.includes(sid) && !ids.some((id) => id === sid)) continue;
    const fl = Array.isArray(f.lines) ? f.lines.filter((ln) => ln && typeof ln === "object") : [];
    if (fl.length) return fl;
  }
  // Also match when shipment id appears on file.shipment_id while sid is number, etc.
  for (const f of files) {
    if (!f || typeof f !== "object") continue;
    const ids = [f.id, f.shipment_id, f.sj_number, f.number, f.sales_delivery_id]
      .map((v) => String(v || "").trim())
      .filter(Boolean);
    const shIds = [shipment.id, shipment.number, shipment.sales_delivery_id]
      .map((v) => String(v || "").trim())
      .filter(Boolean);
    if (!ids.some((id) => shIds.includes(id))) continue;
    const fl = Array.isArray(f.lines) ? f.lines.filter((ln) => ln && typeof ln === "object") : [];
    if (fl.length) return fl;
  }
  return [];
}

export function deliveryBatchLineReadiness(order, shipment = null) {
  const sh = shipment || activeDeliveryBatch(order);
  if (!sh) return [];
  const sid = deliveryBatchIdentity(sh);
  if (!sid) return [];
  const lines = _resolvedSjLines(order, sh);
  if (lines.length) {
    const out = [];
    for (const ln of lines) {
      if (!ln || typeof ln !== "object") continue;
      const idx = _safeInt(ln.line_idx, -1);
      if (idx < 0) continue;
      const required = Math.max(0, _safeInt(ln.qty ?? ln.quantity));
      if (required <= 0) continue;
      const loaded = _loadedQtyForSjLine(order, sid, idx);
      const li = (order?.line_items || [])[idx];
      out.push({
        lineIdx: idx,
        name: _lineName(order, idx),
        itemCode: li?.item_code ?? null,
        accurateItemId: li?.accurate_item_id ?? null,
        productionProcess: li && !_isPrintLine(li) ? "non_print" : "print",
        required,
        loaded,
        shortage: Math.max(0, required - loaded),
      });
    }
    return out;
  }
  // Multi-item PO without resolvable SJ lines → refuse aggregate-only bypass
  if ((order?.line_items || []).length > 1) {
    return [];
  }
  // Legacy single-line shipment without line breakdown
  const required = deliveryBatchQty(sh);
  if (required <= 0) return [];
  const loaded = loadedQtyForDeliveryBatch(order, sh);
  return [{
    lineIdx: 0,
    name: _lineName(order, 0),
    itemCode: null,
    accurateItemId: null,
    productionProcess: "print",
    required,
    loaded,
    shortage: Math.max(0, required - loaded),
  }];
}

/** UX mirror of backend delivery_batch_photo_readiness — server remains authoritative. */
export function deliveryBatchPhotoReadiness(order, shipment = null) {
  const sh = shipment || activeDeliveryBatch(order);
  const sjQty = deliveryBatchQty(sh);
  const packedAvail = packedAvailableForDeliveryBatch(order, sh);
  const loaded = loadedQtyForDeliveryBatch(order, sh);
  const lineRows = deliveryBatchLineReadiness(order, sh);
  const sid = deliveryBatchIdentity(sh);

  let reason = null;
  let reasonCode = null;
  let allowed = false;

  if (!sh || !sid || sjQty <= 0) {
    reason = MSG_PHOTO_SJ_NOT_READY;
    reasonCode = REASON_SJ_NOT_READY;
  } else if (!lineRows.length) {
    const multiWithoutLines = (order?.line_items || []).length > 1 && !_resolvedSjLines(order, sh).length;
    if (multiWithoutLines) {
      reasonCode = REASON_LOADING_ITEM_MISMATCH;
      reason = `${MSG_PHOTO_LOADING_ITEM_MISMATCH} Komposisi item Loading tidak dapat diverifikasi untuk Surat Jalan multi-item.`;
    } else {
      reason = MSG_PHOTO_LOADING_INSUFFICIENT;
      reasonCode = REASON_LOADING_INCOMPLETE;
    }
  } else {
    const shortRows = lineRows.filter((r) => (r.shortage || 0) > 0);
    if (!shortRows.length) {
      allowed = true;
    } else {
      const totalShort = shortRows.reduce((s, r) => s + (r.shortage || 0), 0);
      if (loaded >= sjQty && totalShort > 0) {
        reasonCode = REASON_LOADING_ITEM_MISMATCH;
        const names = shortRows.slice(0, 3).map((r) => r.name || `line ${r.lineIdx}`);
        reason = names.length
          ? `${MSG_PHOTO_LOADING_ITEM_MISMATCH} Kekurangan: ${names.join(", ")}.`
          : MSG_PHOTO_LOADING_ITEM_MISMATCH;
      } else {
        reasonCode = REASON_LOADING_INCOMPLETE;
        const first = shortRows[0];
        reason = first?.name && first.shortage
          ? `Qty Loading '${first.name}' belum mencukupi (kurang ${first.shortage} dari ${first.required}).`
          : MSG_PHOTO_LOADING_INSUFFICIENT;
      }
    }
  }

  const readyQty = allowed ? sjQty : (sjQty > 0 ? Math.min(loaded, sjQty) : 0);
  return {
    allowed,
    deliveryReady: allowed,
    batchQty: sjQty,
    requiredTotal: sjQty,
    loadedTotal: loaded,
    readyQty,
    packedAvailable: packedAvail,
    loaded,
    shipmentId: sid,
    reason,
    reasonCode,
    lines: lineRows,
  };
}

export function deliveryBatchPhotoBlockReason(order, shipment = null) {
  return deliveryBatchPhotoReadiness(order, shipment).reason || null;
}

export function deliveryBatchPhotoBlockCode(order, shipment = null) {
  return deliveryBatchPhotoReadiness(order, shipment).reasonCode || null;
}

export function canUploadDeliveryBatchPhoto(order, shipment = null) {
  return deliveryBatchPhotoReadiness(order, shipment).allowed === true;
}
