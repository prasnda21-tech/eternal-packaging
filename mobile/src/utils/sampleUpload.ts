import { apiErrorMessage } from "./format";
import { canUploadSample, type PermCheck } from "./permissions";
import type { PickerResult } from "./mediaPicker";

export const SAMPLE_KIND = "sample";
export const SAMPLE_NOT_READY_MSG = "Upload sampel tersedia setelah tahap sebelumnya selesai.";

type AnyOrder = Record<string, any>;

const isPrintLine = (li: any) => (li?.production_process || "print").toLowerCase() !== "non_print";

export function orderRequiresPrinting(order: AnyOrder | null | undefined): boolean {
  if (!order) return false;
  if (order.requires_printing === false) return false;
  if (order.requires_printing === true) return true;
  const items = Array.isArray(order.line_items) ? order.line_items : [];
  if (!items.length) return true;
  return items.some(isPrintLine);
}

export function sampleUploadReady(order: AnyOrder | null | undefined): boolean {
  if (!order || !orderRequiresPrinting(order)) return false;
  const ready = order.workflow_ready;
  if (ready && typeof ready === "object" && typeof ready.sample_upload === "boolean") {
    return ready.sample_upload;
  }
  const scheduled = Array.isArray(order.operator_splits) && order.operator_splits.length > 0;
  const screenQty = order?.screener_info?.screen_qty;
  const screenDone = screenQty != null && String(screenQty).trim() !== "";
  return scheduled && screenDone;
}

export function sampleGateMessage(order: AnyOrder | null | undefined): string {
  const reason = typeof order?.screener_gate_reason === "string" ? order.screener_gate_reason.trim() : "";
  return reason || SAMPLE_NOT_READY_MSG;
}

export function canShowSamplePanel(has: PermCheck, order: AnyOrder | null | undefined): boolean {
  return canUploadSample(has) && orderRequiresPrinting(order);
}

export function canShowSampleUploadActions(has: PermCheck, order: AnyOrder | null | undefined): boolean {
  return canShowSamplePanel(has, order) && sampleUploadReady(order);
}

export function sampleFiles(order: AnyOrder | null | undefined): any[] {
  return Array.isArray(order?.sample_files) ? order.sample_files.filter(Boolean) : [];
}

export function sampleFilePreviewUri(file: any): string | null {
  const url = file?.data_url || file?.url || "";
  if (typeof url === "string" && url && (url.startsWith("data:image") || url.startsWith("http"))) {
    return url;
  }
  return null;
}

export function sampleAttachmentPayload(name: string, dataUrl: string): {
  name: string;
  data_url: string;
  kind: typeof SAMPLE_KIND;
} {
  return { name, data_url: dataUrl, kind: SAMPLE_KIND };
}

export function shouldUploadPicked(result: PickerResult): boolean {
  return result.status === "ok";
}

export function pickerUserMessage(result: PickerResult): string | null {
  if (result.status === "denied") return result.message;
  return null;
}

export function sampleUploadErrorMessage(err: unknown): string {
  const e = err as { status?: number; message?: string; detail?: unknown };
  if (e?.status === 401) return "Sesi berakhir. Silakan masuk kembali.";
  if (e?.status === 403) {
    const detail = typeof e.detail === "string" ? e.detail.trim() : "";
    return detail || "Tidak punya izin upload sampel.";
  }
  const msg = e?.message || "";
  if (/network|fetch|Failed to fetch|Network request failed/i.test(msg)) {
    return "Koneksi gagal. Coba lagi.";
  }
  return apiErrorMessage(err, "Gagal mengunggah sampel");
}
