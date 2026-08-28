export const MAX_PHOTOS = 5;
export const MIN_DELIVERY_PROOFS = 2;

export function confirmableProofCount(staged) {
  return (staged || []).filter((s) => s.status === "ready" || (s.status === "error" && s.file)).length;
}

export function storedProofCount(proofs) {
  return Array.isArray(proofs) ? proofs.length : 0;
}

export function canConfirmDelivered(proofs) {
  return storedProofCount(proofs) >= MIN_DELIVERY_PROOFS;
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
