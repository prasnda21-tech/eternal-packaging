/** QR payload is the Live Tracking URL. No external shortener. */

export async function trackingQrDataUrl(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const mod = await import("qrcode");
  const QRCode = mod.default || mod;
  return QRCode.toDataURL(text, {
    errorCorrectionLevel: "M",
    margin: 1,
    width: 220,
  });
}
