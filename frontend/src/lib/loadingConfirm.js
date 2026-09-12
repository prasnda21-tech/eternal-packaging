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

function isPrintLine(li) {
  return String(li?.production_process || "print").toLowerCase() !== "non_print";
}

/** Physical qty eligible for Loading: packed for cetak; PO qty for non-cetak (skip Packing). */
function physicalQtyForLoadingLine(order, snap, idx, li, qtyPo) {
  if (!isPrintLine(li)) {
    if (snap?.loading_cap != null && snap.loading_cap !== "") {
      return Number(snap.loading_cap) || 0;
    }
    return qtyPo;
  }
  return packedQtyForLine(order, snap, idx);
}

export function loadingPoLines(order, selectedSjId = null) {
  const lis = Array.isArray(order?.line_items) ? order.line_items : [];
  const snapshot = Array.isArray(order?.shipment_lines) ? order.shipment_lines : [];
  const files = sjFilesForLoading(order);
  const targetSjId = selectedSjId || files[0]?.id || "";

  // Find matching SJ record from order.shipments or order.surat_jalan_files or order.sj_files
  const shipments = Array.isArray(order?.shipments) ? order.shipments : [];
  const sjFiles = Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : [];
  const matchingSj = shipments.find((s) => String(s?.id) === String(targetSjId) || String(s?.number) === String(targetSjId) || String(s?.sales_delivery_id) === String(targetSjId))
    || sjFiles.find((f) => String(f?.id) === String(targetSjId) || String(f?.shipment_id) === String(targetSjId) || String(f?.sj_number) === String(targetSjId))
    || null;

  const targetIds = new Set([String(targetSjId)]);
  if (matchingSj) {
    if (matchingSj.id) targetIds.add(String(matchingSj.id));
    if (matchingSj.number) targetIds.add(String(matchingSj.number));
    if (matchingSj.shipment_id) targetIds.add(String(matchingSj.shipment_id));
    if (matchingSj.sales_delivery_id) targetIds.add(String(matchingSj.sales_delivery_id));
    if (matchingSj.sj_number) targetIds.add(String(matchingSj.sj_number));
  }

  const confirmations = loadingConfirmations(order);

  return lis.map((li, idx) => {
    const snap = snapshot.find((r) => Number(r.line_idx) === idx) || {};
    const qtyPo = Number(li.quantity ?? snap.qty_po ?? 0) || 0;
    const packed = packedQtyForLine(order, snap, idx);
    const physical = physicalQtyForLoadingLine(order, snap, idx, li, qtyPo);

    let sjLineQty = 0;
    if (matchingSj && Array.isArray(matchingSj.lines) && matchingSj.lines.length) {
      const ln = matchingSj.lines.find((l) => Number(l.line_idx) === idx);
      sjLineQty = Number(ln?.qty ?? ln?.quantity ?? 0) || 0;
    } else if (matchingSj && idx === 0) {
      sjLineQty = Number(matchingSj.total_qty ?? matchingSj.qty ?? 0) || 0;
    } else if (!matchingSj && !files.length) {
      const loadedSnap = Number(snap.loaded ?? 0) || 0;
      const cap = qtyPo > 0 ? Math.min(physical, qtyPo) : physical;
      const availSnap = snap.available_loading != null ? Number(snap.available_loading) : Math.max(0, cap - loadedSnap);
      return {
        line_idx: idx,
        name: li.name || snap.name || `Line #${idx + 1}`,
        item_code: li.item_code || snap.item_code || "",
        accurate_item_id: li.accurate_item_id || snap.accurate_item_id || "",
        color: li.color || snap.color || "",
        production_process: isPrintLine(li) ? "print" : "non_print",
        qty_po: qtyPo,
        qty_sj: physical,
        packed,
        loaded: loadedSnap,
        available: Math.max(0, availSnap),
        physical_available: Math.max(0, availSnap),
        sj_available: Math.max(0, availSnap),
        label: poLineLabel(li, idx),
      };
    }

    let loadedForSj = 0;
    let totalLoadedLine = 0;
    for (const conf of confirmations) {
      for (const ln of conf.lines || []) {
        if (Number(ln.line_idx) === idx) {
          const qty = Number(ln.qty ?? ln.quantity ?? 0) || 0;
          totalLoadedLine += qty;
          if (targetIds.has(String(conf.surat_jalan_id))) {
            loadedForSj += qty;
          }
        }
      }
    }

    const cap = qtyPo > 0 ? Math.min(physical, qtyPo) : physical;
    const physicalAvailable = Math.max(0, cap - totalLoadedLine);
    const sjAvailable = Math.max(0, sjLineQty - loadedForSj);
    const available = Math.min(sjAvailable, physicalAvailable);

    return {
      line_idx: idx,
      name: li.name || snap.name || `Line #${idx + 1}`,
      item_code: li.item_code || snap.item_code || "",
      accurate_item_id: li.accurate_item_id || snap.accurate_item_id || "",
      color: li.color || snap.color || "",
      production_process: isPrintLine(li) ? "print" : "non_print",
      qty_po: qtyPo,
      qty_sj: sjLineQty,
      packed,
      loaded: loadedForSj,
      available,
      physical_available: physicalAvailable,
      sj_available: sjAvailable,
      label: poLineLabel(li, idx),
    };
  });
}

export function sjFilesForLoading(order) {
  const list = [];
  const seen = new Set();

  const files = Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : [];
  for (const f of files) {
    const id = f?.id || f?.file_id;
    if (id && !seen.has(String(id))) {
      seen.add(String(id));
      const sjNumber = f.sj_number || "";
      list.push({
        id: String(id),
        file_id: String(id),
        shipment_id: f.shipment_id ? String(f.shipment_id) : "",
        sj_number: sjNumber,
        name: f.name || (sjNumber ? `Surat Jalan ${sjNumber}` : "Surat Jalan"),
        uploaded_by_name: f.uploaded_by_name || f.uploaded_by,
        uploaded_at: f.uploaded_at,
        source: f.source || "",
        lines: f.lines || [],
        total_qty: f.total_qty || 0,
        has_file: true,
      });
    }
  }

  return list;
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
        if (po?.physical_available <= 0) {
          const nonPrint = String(po?.production_process || "").toLowerCase() === "non_print";
          return {
            ok: false,
            error: nonPrint
              ? "Loading tidak dapat dilakukan karena qty eligible belum tersedia."
              : "Loading tidak dapat dilakukan karena belum ada qty Packing.",
            lines: [],
          };
        }
        return { ok: false, error: "Qty Loading belum bisa diisi. Seluruh kuantitas Surat Jalan untuk item ini sudah selesai di-loading.", lines: [] };
      }
      if (po?.physical_available != null && qty > po.physical_available) {
        const nonPrint = String(po?.production_process || "").toLowerCase() === "non_print";
        return {
          ok: false,
          error: nonPrint
            ? `Qty melebihi qty eligible yang tersedia (${po.physical_available}).`
            : `Qty melebihi qty Packing yang tersedia (${po.physical_available}).`,
          lines: [],
        };
      }
      return { ok: false, error: `Qty melebihi sisa Surat Jalan yang siap loading (${avail}).`, lines: [] };
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


export const LOADING_VIEW_PERM = "loading.view";
export const LOADING_CONFIRM_MATCH_PERM = "loading.confirm_match";
export const LOADING_REPORT_DISCREPANCY_PERM = "loading.report_discrepancy";
export const LOADING_VIEW_DISCREPANCY_HISTORY_PERM = "loading.view_discrepancy_history";
export const LOADING_RESOLVE_DISCREPANCY_PERM = "loading.resolve_discrepancy";

export function loadingDiscrepancies(order) {
  return (Array.isArray(order?.loading_discrepancies) ? order.loading_discrepancies : []).filter(
    (d) => d && !["resolved", "cancelled", "superseded"].includes(String(d.status || "")),
  );
}

export function loadingDiscrepancyHistory(order) {
  return (Array.isArray(order?.loading_discrepancies) ? order.loading_discrepancies : []).filter(Boolean);
}

/** Client-side mirror of backend loading_verification_status when enrich absent. */
export function loadingVerificationStatus(order, suratJalanId = null) {
  const enriched = order?.loading_verification_status;
  if (enriched && typeof enriched === "object" && (!suratJalanId || String(enriched.surat_jalan_id || "") === String(suratJalanId))) {
    return enriched;
  }
  const sid = String(suratJalanId || "").trim();
  const open = loadingDiscrepancies(order).filter((d) => !sid || String(d.surat_jalan_id || "") === sid);
  if (open.length) {
    return { code: "held_qty_mismatch", label: "Tertahan — Qty Tidak Sesuai SJ", surat_jalan_id: sid, discrepancies: open };
  }
  const files = sjFilesForLoading(order);
  if (!files.length) return { code: "waiting_verification", label: "Menunggu Verifikasi", surat_jalan_id: sid };
  const poLines = loadingPoLines(order, sid || files[0]?.id);
  const printShort = poLines.find((ln) => ln.production_process !== "non_print" && Number(ln.qty_sj || 0) > 0 && Number(ln.physical_available || 0) < Number(ln.sj_available || ln.qty_sj || 0) && Number(ln.available || 0) <= 0 && Number(ln.loaded || 0) < Number(ln.qty_sj || 0));
  // Prefer backend; soft client hint only
  const remaining = poLines.reduce((s, r) => s + Number(r.available || 0), 0);
  const loaded = poLines.reduce((s, r) => s + Number(r.loaded || 0), 0);
  const need = poLines.reduce((s, r) => s + Number(r.qty_sj || 0), 0);
  if (need > 0 && loaded >= need && remaining <= 0) {
    return { code: "loading_complete", label: "Loading Selesai", surat_jalan_id: sid || files[0]?.id };
  }
  if (printShort) {
    return { code: "packing_insufficient", label: "Packing Belum Mencukupi", surat_jalan_id: sid || files[0]?.id };
  }
  return { code: "ready_to_verify", label: "Siap Diverifikasi", surat_jalan_id: sid || files[0]?.id };
}

export function sjLinesForDiscrepancy(order, selectedSjId = null) {
  return loadingPoLines(order, selectedSjId)
    .filter((ln) => Number(ln.qty_sj || 0) > 0)
    .map((ln) => ({
      line_idx: ln.line_idx,
      name: ln.name,
      label: ln.label,
      qty_sj: Number(ln.qty_sj || 0),
      unit: ln.unit || "pcs",
      production_process: ln.production_process,
    }));
}

export function discrepancyDiff(qtySj, actualQty) {
  const sj = Number(qtySj);
  const actual = actualQty === "" || actualQty == null ? null : Number(actualQty);
  if (actual == null || !Number.isFinite(actual)) {
    return { ok: false, diff: null, classification: null, label: "" };
  }
  if (actual < 0) return { ok: false, diff: null, classification: null, label: "Qty tidak boleh negatif" };
  if (actual === sj) return { ok: false, diff: 0, classification: null, label: "Sama dengan Qty SJ — bukan selisih" };
  const diff = actual - sj;
  const classification = diff > 0 ? "lebih" : "kurang";
  const label = `${classification === "kurang" ? "Kurang" : "Lebih"} ${Math.abs(diff).toLocaleString("id-ID")}`;
  return { ok: true, diff, classification, label };
}

export function validateDiscrepancySelection(selectedMap, sjLines) {
  const mismatches = [];
  for (const ln of sjLines || []) {
    const row = selectedMap?.[ln.line_idx];
    if (!row || !row.marked) continue;
    if (row.actual_qty === "" || row.actual_qty == null) {
      return { ok: false, error: `Qty Fisik Aktual wajib diisi untuk ${ln.name}.`, mismatches: [] };
    }
    const check = discrepancyDiff(ln.qty_sj, row.actual_qty);
    if (!check.ok) {
      return { ok: false, error: `${ln.name}: ${check.label || "Qty selisih tidak valid."}`, mismatches: [] };
    }
    mismatches.push({ line_idx: Number(ln.line_idx), actual_qty: Number(row.actual_qty) });
  }
  if (!mismatches.length) {
    return { ok: false, error: "Tandai minimal satu baris yang qty fisiknya berbeda dari Surat Jalan.", mismatches: [] };
  }
  return { ok: true, error: "", mismatches };
}

export function canManageLoadingActions(hasPerm) {
  if (typeof hasPerm !== "function") return false;
  return (
    hasPerm(LOADING_CONFIRM_MATCH_PERM)
    || hasPerm(LOADING_REPORT_DISCREPANCY_PERM)
    || hasPerm(LOADING_UPDATE_PERM)
  );
}

export function canViewLoading(hasPerm) {
  if (typeof hasPerm !== "function") return false;
  return (
    canManageLoadingActions(hasPerm)
    || hasPerm(LOADING_VIEW_PERM)
    || hasPerm(LOADING_VIEW_DISCREPANCY_HISTORY_PERM)
    || hasPerm(LOADING_RESOLVE_DISCREPANCY_PERM)
    || hasPerm(LOADING_DELETE_PERM)
  );
}
