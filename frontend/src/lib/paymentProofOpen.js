/** Open Bukti Payment without navigating to blocked `data:` URLs. */

export function paymentProofSource(file) {
  if (!file || typeof file !== "object") return "";
  return String(file.data_url || file.url || "");
}

export function paymentProofIsPresent(file) {
  if (!file || typeof file !== "object") return false;
  return Boolean(paymentProofSource(file) || file.id || file.name);
}

export function paymentProofIsImage(file, url) {
  const src = url || paymentProofSource(file);
  const name = file?.name || "";
  return !!(src && (src.startsWith("data:image/") || /\.(jpe?g|png|gif|webp)$/i.test(name)));
}

export function paymentProofIsPdf(file, url) {
  const src = url || paymentProofSource(file);
  const name = file?.name || "";
  return !!(src && (src.startsWith("data:application/pdf") || /\.pdf$/i.test(name)));
}

export function dataUrlToBlob(dataUrl) {
  const raw = String(dataUrl || "");
  const commaIndex = raw.indexOf(",");
  if (!raw.startsWith("data:") || commaIndex < 0) {
    throw new Error("Invalid data URL");
  }
  const header = raw.slice(0, commaIndex);
  const encoded = raw.slice(commaIndex + 1);
  const mimeMatch = header.match(/^data:([^;,]+)/i);
  const mime = (mimeMatch && mimeMatch[1]) || "application/octet-stream";
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: mime });
}

export function dataUrlToObjectUrl(dataUrl) {
  return URL.createObjectURL(dataUrlToBlob(dataUrl));
}

export function openPaymentProofSource(url, openWindow = (...args) => window.open(...args)) {
  if (!url) return { ok: false, reason: "missing" };
  try {
    if (String(url).startsWith("data:")) {
      const objectUrl = dataUrlToObjectUrl(url);
      const opened = openWindow(objectUrl, "_blank", "noopener,noreferrer");
      setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
      return { ok: true, blocked: !opened };
    }
    const opened = openWindow(url, "_blank", "noopener,noreferrer");
    return { ok: true, blocked: !opened };
  } catch {
    return { ok: false, reason: "decode" };
  }
}

export async function proofErrorMessage(error, fallback = "Gagal membuka Bukti Payment") {
  const data = error?.response?.data;
  if (data && typeof data === "object" && typeof Blob !== "undefined" && data instanceof Blob) {
    try {
      const text = typeof data.text === "function"
        ? await data.text()
        : await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result || ""));
          reader.onerror = () => reject(reader.error);
          reader.readAsText(data);
        });
      const parsed = JSON.parse(text);
      const detail = parsed?.detail;
      if (typeof detail === "string") return detail;
      if (detail && detail.message) return detail.message;
    } catch {
      return fallback;
    }
    return fallback;
  }
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && detail.message) return detail.message;
  return fallback;
}
