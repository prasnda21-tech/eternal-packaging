import { canConfirmDelivered, MIN_DELIVERY_PROOFS } from "./workModels";

export { canConfirmDelivered, MIN_DELIVERY_PROOFS };

const RECIPIENT_NAME_MAX = 120;

export function normalizeRecipientName(name: unknown): string {
  return String(name || "").trim().slice(0, RECIPIENT_NAME_MAX);
}

export function hasValidRecipientName(name: unknown): boolean {
  return normalizeRecipientName(name).length >= 2;
}

export function hasCanvasSig(dataUrl: unknown): boolean {
  return !!(dataUrl && String(dataUrl).startsWith("data:image/"));
}

export function hasSavedRecipientSig(orderSig: any): boolean {
  return !!(
    orderSig
    && orderSig.id
    && orderSig.frozen !== true
    && (orderSig.signature_storage_path || orderSig.recipient_signed_at)
    && hasValidRecipientName(orderSig.recipient_name)
  );
}

export function hasSavedDriverSig(driverSig: any): boolean {
  return !!(
    driverSig
    && driverSig.id
    && driverSig.frozen !== true
    && (driverSig.signature_storage_path || driverSig.driver_signature_storage_path || driverSig.driver_signed_at)
  );
}

export function isSignatureFrozen(sig: any): boolean {
  return !!(sig && sig.frozen === true);
}

export function isDeliveryComplete(order: any): boolean {
  return order?.delivery_status === "Terkirim" || !!order?.terkirim_confirmed;
}

export function canEditDeliverySignatures(order: any): boolean {
  if (isDeliveryComplete(order)) return false;
  return true;
}

export function canSubmitConfirmDelivered(
  proofs: any[],
  recipientName: unknown,
  signatureDataUrl: unknown,
  orderSig: any,
  driverSignatureDataUrl: unknown,
  driverSig: any
): boolean {
  if (!canConfirmDelivered(proofs)) return false;
  const name = normalizeRecipientName(recipientName || orderSig?.recipient_name);
  if (!hasValidRecipientName(name)) return false;
  const recipientOk = hasSavedRecipientSig(orderSig) || hasCanvasSig(signatureDataUrl);
  const driverOk = hasSavedDriverSig(driverSig) || hasCanvasSig(driverSignatureDataUrl);
  return recipientOk && driverOk;
}

export function confirmDeliveredBlockReason(
  proofs: any[],
  recipientName: unknown,
  signatureDataUrl: unknown,
  orderSig: any,
  driverSignatureDataUrl: unknown,
  driverSig: any
): string | null {
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

export function shouldUploadRecipientSignature(opts: {
  recipientName: unknown;
  signatureDataUrl: unknown;
  orderSig: any;
}): boolean {
  void opts.recipientName;
  void opts.orderSig;
  return hasCanvasSig(opts.signatureDataUrl);
}

export function shouldUploadDriverSignature(opts: {
  signatureDataUrl: unknown;
  driverSig: any;
}): boolean {
  void opts.driverSig;
  return hasCanvasSig(opts.signatureDataUrl);
}

export type DeliveryConfirmStep = "recipient" | "driver" | "confirm";

export function deliveryConfirmSteps(opts: {
  proofs: any[];
  recipientName: unknown;
  signatureDataUrl: unknown;
  orderSig: any;
  driverSignatureDataUrl: unknown;
  driverSig: any;
}): { ok: boolean; reason: string | null; steps: DeliveryConfirmStep[] } {
  const reason = confirmDeliveredBlockReason(
    opts.proofs,
    opts.recipientName,
    opts.signatureDataUrl,
    opts.orderSig,
    opts.driverSignatureDataUrl,
    opts.driverSig
  );
  if (reason) return { ok: false, reason, steps: [] };
  const steps: DeliveryConfirmStep[] = [];
  if (shouldUploadRecipientSignature(opts)) steps.push("recipient");
  if (shouldUploadDriverSignature({
    signatureDataUrl: opts.driverSignatureDataUrl,
    driverSig: opts.driverSig,
  })) {
    steps.push("driver");
  }
  steps.push("confirm");
  return { ok: true, reason: null, steps };
}

export function recipientSignatureFormFields(recipientName: unknown, signatureDataUrl: string) {
  return {
    recipient_name: normalizeRecipientName(recipientName),
    signature_data_url: signatureDataUrl,
  };
}

export function driverSignatureFormFields(signatureDataUrl: string) {
  return {
    signature_data_url: signatureDataUrl,
  };
}

export function signatureReadinessLabel(saved: boolean, canvasReady: boolean): string {
  if (saved && !canvasReady) return "Tersimpan";
  if (canvasReady) return "Siap";
  return "Belum";
}

export function authoritativeDriverDisplayName(order: any, user?: { name?: string } | null): string {
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
  if (order?.delivery_driver_signature?.driver_name) {
    return String(order.delivery_driver_signature.driver_name).trim();
  }
  if (user?.name) return String(user.name).trim();
  return "";
}
