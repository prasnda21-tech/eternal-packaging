/**
 * Absolute http(s) URLs outside this page and the configured API origin.
 * Those PDFs are not fetched by PDF.js and are not proxied.
 */
export function isCrossOriginDocumentUrl(href) {
  const value = String(href || "").trim();
  if (!value || value.startsWith("data:") || value.startsWith("blob:") || value.startsWith("/")) return false;
  let url;
  try {
    const base = typeof window !== "undefined" ? window.location.origin : "http://localhost";
    url = new URL(value, base);
  } catch {
    return false;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return false;
  const pageOrigin = typeof window !== "undefined" ? window.location.origin : url.origin;
  if (url.origin === pageOrigin) return false;
  const configured = String(process.env.REACT_APP_BACKEND_URL || "").trim();
  if (configured) {
    try {
      const api = new URL(configured.includes("://") ? configured : `http://${configured}`, pageOrigin);
      if (url.origin === api.origin) return false;
    } catch {
      /* malformed backend origin stays cross-origin */
    }
  }
  return true;
}
