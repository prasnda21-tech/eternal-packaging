const MAX_BYTES = 4 * 1024 * 1024;

export function dataUrlByteLength(dataUrl: string): number {
  const comma = dataUrl.indexOf(",");
  const b64 = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
  const padding = b64.endsWith("==") ? 2 : b64.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((b64.length * 3) / 4) - padding);
}

export async function uriToDataUrl(uri: string, mime: string): Promise<string> {
  const res = await fetch(uri);
  if (!res.ok) throw new Error("Gagal membaca file");
  const blob = await res.blob();
  const type = mime || blob.type || "image/jpeg";
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = reader.result;
      if (typeof result === "string" && result.startsWith("data:")) {
        resolve(result);
        return;
      }
      if (typeof result === "string") {
        resolve(`data:${type};base64,${result}`);
        return;
      }
      reject(new Error("Gagal membaca file"));
    };
    reader.onerror = () => reject(new Error("Gagal membaca file"));
    reader.readAsDataURL(blob);
  });
  if (dataUrlByteLength(dataUrl) > MAX_BYTES) {
    throw new Error("Ukuran file maksimal 4MB");
  }
  return dataUrl;
}
