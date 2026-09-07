/** Read-only helpers for existing PO design_files. Does not invent versions or write state. */

type AnyOrder = Record<string, any>;

export function designFiles(order: AnyOrder | null | undefined): any[] {
  return Array.isArray(order?.design_files) ? order.design_files.filter(Boolean) : [];
}

/** Latest upload is last in the existing append-only list. */
export function currentDesignFile(order: AnyOrder | null | undefined): any | null {
  const files = designFiles(order);
  return files.length ? files[files.length - 1] : null;
}

export function designFileLabel(file: any): string {
  const name = String(file?.name || file?.original_filename || "").trim();
  return name || "File desain";
}

export function designStatusLabel(order: AnyOrder | null | undefined): string | null {
  if (order?.design_bypassed) return "BYPASS";
  const status = String(order?.design_status || "").trim();
  return status || null;
}

export function designFileIsImage(file: any): boolean {
  const ct = String(file?.content_type || "").toLowerCase();
  const url = String(file?.data_url || file?.url || "");
  const name = String(file?.name || file?.original_filename || "");
  return ct.startsWith("image/") || url.startsWith("data:image/") || /\.(jpe?g|png|gif|webp)$/i.test(name);
}

export function designFileIsPdf(file: any): boolean {
  const ct = String(file?.content_type || "").toLowerCase();
  const url = String(file?.data_url || file?.url || "");
  const name = String(file?.name || file?.original_filename || "");
  return ct.includes("pdf") || url.startsWith("data:application/pdf") || /\.pdf$/i.test(name);
}

export function designFilePreviewUri(file: any): string | null {
  const url = file?.data_url || file?.url || "";
  if (typeof url === "string" && url && (url.startsWith("data:image") || url.startsWith("http"))) {
    return url;
  }
  return null;
}

export type DesignOpenKind = "data" | "url" | "storage" | null;

export function designFileOpenRef(file: any): { kind: DesignOpenKind; value: string } {
  const dataUrl = typeof file?.data_url === "string" ? file.data_url.trim() : "";
  if (dataUrl) return { kind: "data", value: dataUrl };
  const url = typeof file?.url === "string" ? file.url.trim() : "";
  if (url.startsWith("http")) return { kind: "url", value: url };
  const storage = typeof file?.storage_path === "string" ? file.storage_path.trim() : "";
  if (storage) return { kind: "storage", value: storage };
  return { kind: null, value: "" };
}

export function canOpenDesignFile(file: any): boolean {
  return designFileOpenRef(file).kind != null;
}
