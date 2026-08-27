import { resolveBackendOrigin } from "./backendOrigin";

const pageHost = typeof window !== "undefined" ? window.location.hostname : "";
const API = `${resolveBackendOrigin(process.env.REACT_APP_BACKEND_URL, pageHost)}/api`;

export const SJ_VIEW_FILE_PERM = "delivery_note.view_file";
export const SJ_MANAGE_PERM = "delivery_note.manage";

export function poLineLabel(li, idx) {
  const name = (li && li.name) || `Line #${Number(idx) + 1}`;
  const code = (li && (li.item_code || li.sku)) || "";
  const color = (li && li.color) || "";
  return [code, name, color].filter(Boolean).join(" · ");
}

export function sjPoLines(order) {
  const lis = Array.isArray(order?.line_items) ? order.line_items : [];
  const snapshot = Array.isArray(order?.shipment_lines) ? order.shipment_lines : [];
  return lis.map((li, idx) => {
    const snap = snapshot.find((r) => Number(r.line_idx) === idx) || {};
    return {
      line_idx: idx,
      name: li.name || snap.name || `Line #${idx + 1}`,
      item_code: li.item_code || snap.item_code || "",
      accurate_item_id: li.accurate_item_id || snap.accurate_item_id || "",
      color: li.color || snap.color || "",
      qty_po: Number(li.quantity ?? snap.qty_po ?? 0) || 0,
      shipped: Number(snap.shipped || 0) || 0,
      available: Number(
        snap.available_sj_file != null ? snap.available_sj_file : Math.max(0, Number(li.quantity || 0) - Number(snap.shipped || 0)),
      ) || 0,
      label: poLineLabel(li, idx),
    };
  });
}

export function sjHistory(order) {
  if (Array.isArray(order?.sj_files) && order.sj_files.length) return order.sj_files;
  const files = Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : [];
  if (files.length) {
    return files.map((f, i) => ({
      id: f.id || `sj-${i}`,
      file_id: f.id || null,
      shipment_id: f.shipment_id || null,
      name: f.name || "Surat Jalan",
      uploaded_by: f.uploaded_by,
      uploaded_by_name: f.uploaded_by_name || f.uploaded_by,
      uploaded_at: f.uploaded_at,
      lines: Array.isArray(f.lines) ? f.lines : [],
      has_item_qty: Array.isArray(f.lines) && f.lines.some((l) => Number(l.qty || l.quantity) > 0),
      has_file: true,
      legacy_incomplete: !(Array.isArray(f.lines) && f.lines.some((l) => Number(l.qty || l.quantity) > 0)),
    }));
  }
  return [];
}

export function sjLineSummary(entry, order) {
  const lines = Array.isArray(entry?.lines) ? entry.lines : [];
  if (!lines.length) return [];
  const lis = Array.isArray(order?.line_items) ? order.line_items : [];
  return lines.map((ln, i) => {
    const idx = ln.line_idx != null ? Number(ln.line_idx) : i;
    const li = lis[idx] || {};
    return {
      line_idx: idx,
      label: poLineLabel(li, idx),
      qty: Number(ln.qty ?? ln.quantity ?? 0) || 0,
    };
  });
}

export function emptySjLine() {
  return { line_idx: "", qty: "" };
}

export function parseSjQty(raw) {
  if (raw === "" || raw == null) return null;
  const n = parseInt(String(raw), 10);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n;
}

export function validateSjDraftLines(rows, poLines) {
  const used = new Set();
  const out = [];
  for (const row of rows || []) {
    if (row.line_idx === "" || row.line_idx == null) {
      return { ok: false, error: "Pilih item Surat Jalan.", lines: [] };
    }
    const idx = Number(row.line_idx);
    if (!Number.isFinite(idx) || used.has(idx)) {
      return { ok: false, error: "Item Surat Jalan duplikat. Gunakan line PO yang berbeda.", lines: [] };
    }
    used.add(idx);
    const qty = parseSjQty(row.qty);
    if (qty == null) {
      return { ok: false, error: "Qty Surat Jalan harus angka lebih dari 0.", lines: [] };
    }
    const po = (poLines || []).find((p) => Number(p.line_idx) === idx);
    const avail = po ? Number(po.available) : null;
    if (avail != null && qty > avail) {
      return { ok: false, error: `Qty melebihi sisa qty PO line (${avail}).`, lines: [] };
    }
    const payload = { line_idx: idx, qty };
    if (po?.accurate_item_id) payload.accurate_item_id = String(po.accurate_item_id);
    out.push(payload);
  }
  if (!out.length) return { ok: false, error: "Isi minimal satu item dan qty Surat Jalan.", lines: [] };
  return { ok: true, error: "", lines: out };
}

export function isPreviewableSj(entry) {
  const name = String(entry?.name || "").toLowerCase();
  const ct = String(entry?.content_type || "").toLowerCase();
  if (ct.includes("pdf") || name.endsWith(".pdf")) return "pdf";
  if (ct.startsWith("image/") || /\.(jpe?g|png|gif|webp)$/i.test(name)) return "image";
  return null;
}

function sniffBlobType(buf, filename = "", headerCt = "") {
  const name = String(filename || "").toLowerCase();
  const claimed = String(headerCt || "").split(";")[0].trim().toLowerCase();
  const head = buf && buf.byteLength ? new Uint8Array(buf, 0, Math.min(8, buf.byteLength)) : new Uint8Array();
  const isPdf = head.length >= 4 && head[0] === 0x25 && head[1] === 0x50 && head[2] === 0x44 && head[3] === 0x46;
  if (isPdf || name.endsWith(".pdf") || claimed.includes("pdf")) return "application/pdf";
  if (claimed.startsWith("image/")) return claimed;
  if (head.length >= 3 && head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) return "image/jpeg";
  if (head.length >= 4 && head[0] === 0x89 && head[1] === 0x50 && head[2] === 0x4e && head[3] === 0x47) return "image/png";
  if (/\.(jpe?g)$/i.test(name)) return "image/jpeg";
  if (name.endsWith(".png")) return "image/png";
  return claimed || "application/octet-stream";
}

export function suratJalanFileUrl(orderId, fileId, { download = false } = {}) {
  if (!orderId || !fileId) return "";
  const base = `${API}/orders/${encodeURIComponent(orderId)}/surat-jalan/files/${encodeURIComponent(fileId)}`;
  return download ? `${base}?download=1` : base;
}

export async function fetchSuratJalanBlob(orderId, fileId, { download = false, filename = "" } = {}) {
  const url = suratJalanFileUrl(orderId, fileId, { download });
  const resp = await fetch(url, { credentials: "include" });
  if (resp.status === 403) {
    const err = new Error("Anda tidak punya permission untuk melihat file Surat Jalan.");
    err.status = 403;
    throw err;
  }
  if (resp.status === 404) {
    const err = new Error("File Surat Jalan tidak ditemukan.");
    err.status = 404;
    throw err;
  }
  if (resp.status === 502) {
    const err = new Error("Penyimpanan file tidak tersedia.");
    err.status = 502;
    throw err;
  }
  if (!resp.ok) {
    const err = new Error("Gagal memuat file Surat Jalan.");
    err.status = resp.status;
    throw err;
  }
  const buf = await resp.arrayBuffer();
  const headerCt = (resp.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
  const type = sniffBlobType(buf, filename, headerCt);
  return new Blob([buf], { type: type || "application/octet-stream" });
}

export async function downloadSuratJalanFile(orderId, fileId, filename) {
  const blob = await fetchSuratJalanBlob(orderId, fileId, { download: true, filename });
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename || "surat-jalan";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1500);
}
