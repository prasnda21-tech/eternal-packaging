import { hasCanvasSig } from "./deliverySignature";

export type SignatureSlot = "recipient" | "driver";

export type SignaturePair = {
  recipient: string | null;
  driver: string | null;
};

export function emptySignatureCannotSave(dataUrl: string | null | undefined): boolean {
  return !hasCanvasSig(dataUrl);
}

export function applySavedSignature(
  pair: SignaturePair,
  slot: SignatureSlot,
  dataUrl: string
): SignaturePair {
  if (slot === "recipient") return { recipient: dataUrl, driver: pair.driver };
  return { recipient: pair.recipient, driver: dataUrl };
}

export function cancelKeepsSavedPair(pair: SignaturePair): SignaturePair {
  return { recipient: pair.recipient, driver: pair.driver };
}

export function clearModalDraft(): string | null {
  return null;
}

/** Android back / overlay dismiss. Explicit Batal may still discard. */
export function canDismissSignatureModal(hasUnsavedDraft: boolean): boolean {
  return !hasUnsavedDraft;
}

export function signatureCollapsedState(opts: {
  hasLocal: boolean;
  hasSaved: boolean;
  frozen: boolean;
}): { status: string; action: "none" | "create" | "edit" } {
  if (opts.frozen) return { status: "Tanda tangan terkunci", action: "none" };
  if (opts.hasLocal || opts.hasSaved) return { status: "TTD sudah dibuat", action: "edit" };
  return { status: "Belum ada tanda tangan", action: "create" };
}

export function signatureOpenButtonTitle(slot: SignatureSlot, action: "create" | "edit"): string {
  if (action === "edit") return "Ubah TTD";
  return slot === "recipient" ? "Buat TTD Penerima" : "Buat TTD Driver";
}

export function signatureModalTitle(slot: SignatureSlot): string {
  return slot === "recipient" ? "Tanda Tangan Penerima" : "Tanda Tangan Driver";
}
