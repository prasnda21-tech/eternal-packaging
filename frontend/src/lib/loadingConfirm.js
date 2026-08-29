import { poLineLabel, parseSjQty } from "./suratJalan";

export const LOADING_UPDATE_PERM = "loading.update";

export function emptyLoadingLine() {
  return { line_idx: "", qty: "" };
}

function packedQtyForLine(order, snap, idx) {
  if (snap?.packed != null && snap.packed !== "") return Number(snap.packed) || 0;
  const lines = Array.isArray(order?.packing_info?.lines) ? order.packing_info.lines : [];
  const row = lines.find((r) => Number(r.line_idx) === idx);
  if (row) return Number(row.packed_qty || 0) || 0;
  if (Number(idx) === 0) return Number(order?.packing_info?.qty_packed || 0) || 0;
  return 0;
}

export function loadingPoLines(order) {
  const lis = Array.isArray(order?.line_items) ? order.line_items : [];
  const snapshot = Array.isArray(order?.shipment_lines) ? order.shipment_lines : [];
  return lis.map((li, idx) => {
    const snap = snapshot.find((r) => Number(r.line_idx) === idx) || {};
    const qtyPo = Number(li.quantity ?? snap.qty_po ?? 0) || 0;
    const packed = packedQtyForLine(order, snap, idx);
    const loaded = Number(snap.loaded ?? 0) || 0;
    const cap = qtyPo > 0 ? Math.min(packed, qtyPo) : packed;
    const available = Number(
      snap.available_loading != null ? snap.available_loading : Math.max(0, cap - loaded),
    );
    return {
      line_idx: idx,
      name: li.name || snap.name || `Line #${idx + 1}`,
      item_code: li.item_code || snap.item_code || "",
      accurate_item_id: li.accurate_item_id || snap.accurate_item_id || "",
      color: li.color || snap.color || "",
      qty_po: qtyPo,
      packed,
      loaded,
      available: Math.max(0, available),
      label: poLineLabel(li, idx),
    };
  });
}

export function sjFilesForLoading(order) {
  const files = Array.isArray(order?.sj_files) && order.sj_files.length
    ? order.sj_files
    : (Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : []);
  return files
    .filter((f) => f && (f.file_id || f.id))
    .map((f) => ({
      id: f.file_id || f.id,
      name: f.name || "Surat Jalan",
      uploaded_by_name: f.uploaded_by_name || f.uploaded_by,
      uploaded_at: f.uploaded_at,
    }));
}

export function loadingConfirmations(order) {
  return (Array.isArray(order?.loading_confirmations) ? order.loading_confirmations : [])
    .filter((c) => c && !c.cancelled && !c.deleted);
}

export function deliveredQtyForLoadingConfirmation(order, confirmationId) {
  let leftover = Math.max(0, Number(order?.qty_delivered || order?.delivered_qty || 0));
  const list = loadingConfirmations(order);
  for (const conf of list) {
    const cap = (conf.lines || []).reduce((s, ln) => s + Math.max(0, Number(ln.qty || ln.quantity || 0)), 0);
    const allocated = Math.min(cap, leftover);
    leftover -= allocated;
    if (String(conf.id) === String(confirmationId)) return allocated;
  }
  return 0;
}

export function canDeleteLoadingConfirmation(order, confirmation) {
  if (!confirmation?.id) return false;
  if (confirmation.cancelled || confirmation.deleted) return false;
  if (order?.delivery_status === "Terkirim") return false;
  const delivered = Number(order?.qty_delivered || order?.delivered_qty || 0);
  const required = Number(order?.total_quantity || 0);
  if (required > 0 && delivered >= required) return false;
  if (deliveredQtyForLoadingConfirmation(order, confirmation.id) > 0) return false;
  return true;
}

export const LOADING_DELETE_PERM = "loading.delete";

export function validateLoadingDraftLines(rows, poLines) {
  const used = new Set();
  const out = [];
  for (const row of rows || []) {
    if (row.line_idx === "" || row.line_idx == null) {
      return { ok: false, error: "Pilih item Loading.", lines: [] };
    }
    const idx = Number(row.line_idx);
    if (!Number.isFinite(idx) || used.has(idx)) {
      return { ok: false, error: "Item Loading duplikat. Gunakan line PO yang berbeda.", lines: [] };
    }
    used.add(idx);
    const qty = parseSjQty(row.qty);
    if (qty == null) {
      return { ok: false, error: "Qty Loading harus angka lebih dari 0.", lines: [] };
    }
    const po = (poLines || []).find((p) => Number(p.line_idx) === idx);
    const avail = po ? Number(po.available) : null;
    if (avail != null && qty > avail) {
      if (avail <= 0) {
        return { ok: false, error: "Qty Loading belum bisa diisi. Qty fisik yang sudah diproses belum siap.", lines: [] };
      }
      return { ok: false, error: `Qty melebihi qty packing yang siap loading (${avail}).`, lines: [] };
    }
    const payload = { line_idx: idx, qty };
    if (po?.accurate_item_id) payload.accurate_item_id = String(po.accurate_item_id);
    out.push(payload);
  }
  if (!out.length) return { ok: false, error: "Isi minimal satu item dan qty Loading.", lines: [] };
  return { ok: true, error: "", lines: out };
}

export function loadingLineLabel(ln, order) {
  const idx = Number(ln?.line_idx);
  const li = (order?.line_items || [])[idx] || {};
  return ln?.name || poLineLabel(li, idx);
}
