export function confirmableProofCount(staged) {
  return (staged || []).filter((s) => s.status === "ready" || (s.status === "error" && s.file)).length;
}

export function proofUploadUserError(ex) {
  const raw = ex?.response?.data?.detail ?? ex?.message ?? "";
  const text = typeof raw === "string" ? raw : (raw?.message || "");
  const status = ex?.response?.status;
  if (status === 502 || /EMERGENT_LLM_KEY|object storage|tidak terkonfigurasi|S3_BUCKET/i.test(text)) {
    return "Gagal mengunggah foto. Silakan coba lagi.";
  }
  return text || "Gagal mengunggah foto. Silakan coba lagi.";
}
