export const PUBLIC_APPROVAL_DESIGN_OPEN_STATUSES = ["active", "approved", "rejected"];

export function canOpenPublicApprovalDesign(status) {
  return PUBLIC_APPROVAL_DESIGN_OPEN_STATUSES.includes(status);
}

export function isPublicApprovalPdf(designFile) {
  const ct = String(designFile?.content_type || "").toLowerCase();
  const name = String(designFile?.name || "").toLowerCase();
  const dataUrl = String(designFile?.data_url || "");
  return ct === "application/pdf" || name.endsWith(".pdf") || dataUrl.startsWith("data:application/pdf");
}

export function isPublicApprovalImage(designFile) {
  const ct = String(designFile?.content_type || "").toLowerCase();
  const dataUrl = String(designFile?.data_url || "");
  return ct.startsWith("image/") || dataUrl.startsWith("data:image/");
}

function isAbsoluteHttpUrl(value) {
  return /^https?:\/\//i.test(String(value || ""));
}

export function resolvePublicApprovalDesignUrl(token, designFile, { apiBase, status, fallbackUrl } = {}) {
  if (!token || !designFile) return "";
  if (status && !canOpenPublicApprovalDesign(status)) return "";
  const viewUrl = designFile.view_url || "";
  const fallback = fallbackUrl || "";
  if (isAbsoluteHttpUrl(fallback) && !isAbsoluteHttpUrl(viewUrl)) return fallback;
  if (isAbsoluteHttpUrl(viewUrl)) return viewUrl;
  if (isAbsoluteHttpUrl(fallback)) return fallback;
  const relative = viewUrl.startsWith("/") ? viewUrl : "";
  if (relative && apiBase) {
    const origin = String(apiBase).replace(/\/api\/?$/, "");
    return `${origin}${relative}`;
  }
  if (!apiBase) return "";
  const base = String(apiBase).replace(/\/$/, "");
  return `${base}/public/customer-approval/${encodeURIComponent(token)}/design`;
}

export function preferCompactPdfPreview() {
  if (typeof navigator !== "undefined" && navigator.pdfViewerEnabled === false) return true;
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  try {
    return window.matchMedia("(max-width: 768px)").matches;
  } catch {
    return false;
  }
}
