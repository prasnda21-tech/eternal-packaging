import { fileUrl } from "./api";

function inferKind(file, href) {
  const name = String(file?.name || file?.filename || "");
  const type = String(file?.content_type || file?.mime || "");
  if (type.startsWith("image/") || String(href || "").startsWith("data:image/") || /\.(jpe?g|png|gif|webp)$/i.test(name)) {
    return "image";
  }
  if (type.includes("pdf") || String(href || "").startsWith("data:application/pdf") || /\.pdf$/i.test(name)) {
    return "pdf";
  }
  return "file";
}

export function browserSafeOpenTarget(file) {
  if (!file) return { ok: false, reason: "File tidak tersedia." };
  if (file.storage_path) {
    const href = fileUrl(file.storage_path);
    return { ok: true, href, mode: "url", kind: inferKind(file, href) };
  }
  if (file.url && !String(file.url).startsWith("data:")) {
    return { ok: true, href: file.url, mode: "url", kind: inferKind(file, file.url) };
  }
  if (file.view_url && !String(file.view_url).startsWith("data:")) {
    return { ok: true, href: file.view_url, mode: "url", kind: inferKind(file, file.view_url) };
  }
  if (file.data_url) {
    const kind = inferKind(file, file.data_url);
    return {
      ok: true,
      href: file.data_url,
      mode: kind === "pdf" ? "pdf-data" : "data",
      kind,
    };
  }
  return { ok: false, reason: "File tidak memiliki data yang dapat dibuka." };
}

export function pdfDataUrlToBlobUrl(dataUrl, createObjectURL = URL.createObjectURL) {
  const commaIndex = String(dataUrl).indexOf(",");
  if (commaIndex < 0) throw new Error("Invalid PDF data URL");
  const encoded = dataUrl.slice(commaIndex + 1);
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const blob = new Blob([bytes], { type: "application/pdf" });
  return createObjectURL(blob);
}

export function openBrowserSafeFile(file, openFn = (typeof window !== "undefined" ? window.open : undefined)) {
  const target = browserSafeOpenTarget(file);
  if (!target.ok) return target;
  if (typeof openFn !== "function") return { ...target, opened: false, reason: "Browser tidak dapat membuka file." };
  try {
    let href = target.href;
    if (target.mode === "pdf-data") {
      href = pdfDataUrlToBlobUrl(target.href);
    }
    const opened = openFn(href, "_blank", "noopener,noreferrer");
    if (!opened) return { ...target, opened: false, reason: "Browser memblokir tab. Izinkan pop-up untuk situs ini." };
    return { ...target, opened: true, href };
  } catch {
    return { ...target, opened: false, reason: "File gagal dibuka. Silakan coba kembali." };
  }
}

export function latestDesignFile(order) {
  const files = Array.isArray(order?.design_files) ? order.design_files : [];
  if (!files.length) return null;
  return files[files.length - 1];
}

export function designFileMeta(order, file) {
  const chosen = file || latestDesignFile(order);
  if (!chosen) {
    return { name: "—", type: "", approval: order?.design_status || "—" };
  }
  const name = chosen.name || chosen.filename || "File desain";
  const type = chosen.content_type || chosen.mime || (/\.pdf$/i.test(name) ? "PDF" : /\.(jpe?g|png|gif|webp)$/i.test(name) ? "Gambar" : "");
  return {
    name,
    type,
    approval: order?.design_status || chosen.approval_status || "—",
  };
}
