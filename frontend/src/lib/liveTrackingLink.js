/** Absolute Live Tracking URL from the application origin and API path. */

export function trackingAbsoluteUrl(origin, path) {
  const base = String(origin || "").trim().replace(/\/$/, "");
  const rel = String(path || "").trim();
  if (!base || !rel || !rel.startsWith("/") || rel.startsWith("//")) return "";
  if (/^[a-z][a-z0-9+.-]*:/i.test(rel)) return "";
  try {
    const root = new URL(base);
    const url = new URL(rel, root.origin);
    if (url.origin !== root.origin) return "";
    return url.href;
  } catch {
    return "";
  }
}

export function trackingShareMessage({ customerName, poNumber, poLabel, url }) {
  const parts = ["Live Tracking Pesanan"];
  const customer = String(customerName || "").trim();
  if (customer) parts.push(customer);
  if (poLabel === "single") {
    const po = String(poNumber || "").trim();
    if (po) parts.push(po);
  }
  return `${parts.join(" — ")}: ${url}`;
}

export function trackingWhatsappHref(message) {
  return `https://wa.me/?text=${encodeURIComponent(String(message || ""))}`;
}

/**
 * Copy, WhatsApp, and QR all use the same absolute URL.
 * `public_path` is preferred. `token` still builds the legacy route when an
 * older API response has no pretty path.
 */
export function trackingShareFromLink({ origin, link, customerName, poNumber }) {
  const token = String(link?.token || "").trim();
  const publicPath = String(link?.public_path || "").trim();
  const pathField = String(link?.path || "").trim();
  const pretty = publicPath.startsWith("/t/")
    ? publicPath
    : (pathField.startsWith("/t/") ? pathField : "");
  const legacy = String(link?.legacy_path || "").trim()
    || (pathField.startsWith("/tracking/") ? pathField : "")
    || (token ? `/tracking/${encodeURIComponent(token)}` : "");
  const sharePath = pretty || legacy;
  const url = trackingAbsoluteUrl(origin, sharePath);
  const poLabel = String(link?.po_label || "");
  const message = url
    ? trackingShareMessage({ customerName, poNumber, poLabel, url })
    : "";
  return {
    url,
    message,
    whatsappHref: url ? trackingWhatsappHref(message) : "",
    qrValue: url,
    legacyUrl: trackingAbsoluteUrl(origin, legacy),
    poLabel,
  };
}
