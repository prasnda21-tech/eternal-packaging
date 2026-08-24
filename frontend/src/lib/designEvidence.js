// Design completion evidence — used by Timeline render path (and StageBar via stageState).
// Design is NEVER inferred from current_stage, payment, payment approval, or generic attachments.

const hasValue = (v) => v !== null && v !== undefined && String(v).trim() !== "";

/**
 * True only when order.design_files contains a real design upload record.
 * Does not read payment_files, current_stage, design_status, or timeline progression.
 */
export function hasRealDesignUpload(order) {
  const files = order?.design_files;
  if (!Array.isArray(files) || files.length === 0) return false;
  return files.some((f) => {
    if (!f || typeof f !== "object") return false;
    // Content from attachment upload / stored URL
    if (hasValue(f.data_url) || hasValue(f.url)) return true;
    // Minimal/legacy upload stamp from POST /orders/{id}/attachment kind=design
    return hasValue(f.name) && hasValue(f.uploaded_at);
  });
}

/** Chip for Timeline/StageBar stage index 1 (Design). */
export function resolveDesignChip(order, currentStage) {
  if (order?.design_bypassed) return { state: "bypass", label: "BYPASS" };
  if (hasRealDesignUpload(order)) return { state: "done", label: "Selesai" };
  if ((Number(currentStage) || 0) >= 1) return { state: "current", label: "Belum dikerjakan" };
  return { state: "pending", label: "Menunggu" };
}

/**
 * Chip for stage index 2 (Approval Design).
 * "Menunggu approval" requires real design upload — approval object / Waiting status alone is not enough.
 */
export function resolveApprovalDesignChip(order, currentStage) {
  if (order?.design_status === "Approved") return { state: "done", label: "Selesai" };
  if (order?.design_bypassed) return { state: "bypass", label: "BYPASS" };
  if (!hasRealDesignUpload(order)) return { state: "pending", label: "Menunggu" };
  const cs = Number(currentStage) || 0;
  if (cs === 2) return { state: "current", label: "Berjalan" };
  if (cs > 2) return { state: "current", label: "Menunggu approval" };
  return { state: "pending", label: "Menunggu" };
}
