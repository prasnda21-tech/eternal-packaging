// Client-side image compression for phone uploads.
// Resizes the longest edge to `maxDim` and encodes as JPEG at `quality`.
// Falls back to the original blob if it's already small enough or compression fails.

export async function compressImage(file, { maxDim = 1600, quality = 0.82, maxSizeKB = 900 } = {}) {
  if (!file || !file.type?.startsWith("image/")) return { file, compressed: false };
  const sizeKB = file.size / 1024;
  // If already tiny, skip compression
  if (sizeKB <= maxSizeKB && !/heic|heif/i.test(file.type)) {
    return { file, compressed: false, sizeKB };
  }

  const dataUrl = await readAsDataURL(file);
  const img = await loadImage(dataUrl);
  const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
  const w = Math.round(img.width * scale);
  const h = Math.round(img.height * scale);

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(img, 0, 0, w, h);

  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
  if (!blob) return { file, compressed: false, sizeKB };
  // Give it a sensible name
  const baseName = (file.name || "photo").replace(/\.[a-z0-9]+$/i, "");
  const compressedFile = new File([blob], `${baseName}.jpg`, { type: "image/jpeg" });
  return { file: compressedFile, compressed: true, sizeKB: compressedFile.size / 1024, originalSizeKB: sizeKB };
}

function readAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}
