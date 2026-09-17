import { poLineLabel, parseSjQty } from "./suratJalan";
import { shipmentIsInactive } from "./sjIntegrity";

export const LOADING_UPDATE_PERM = "loading.update";

export function emptyLoadingLine() {
  return { line_idx: "", qty: "" };
}

function explicitLineIdx(value) {
  if (value === undefined || value === null || String(value).trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Exact SJ/PO line identity. Never matches by item name. line_id wins over stale line_idx. */
export function resolveLoadingPoLineIdx(order, shipLine) {
  const lis = Array.isArray(order?.line_items) ? order.line_items : [];
  const lid = String(shipLine?.line_id || "").trim();
  const aid = String(shipLine?.accurate_item_id || "").trim();
  const explicit = explicitLineIdx(shipLine?.line_idx);

  if (lid) {
    const matches = [];
    lis.forEach((li, i) => {
      if (String(li?.line_id || li?.id || "").trim() === lid) matches.push(i);
    });
    if (matches.length === 1) return matches[0];
    if (matches.length > 1 && explicit != null && matches.includes(explicit)) return explicit;
    if (matches.length > 1) return null;
  }
  if (aid) {
    const matches = [];
    lis.forEach((li, i) => {
      if (String(li?.accurate_item_id || "").trim() === aid) matches.push(i);
    });
    if (explicit != null && matches.includes(explicit)) return explicit;
    if (matches.length === 1) return matches[0];
    return null;
  }
  if (explicit != null) {
    if (!lis.length && explicit === 0) return 0;
    if (explicit >= 0 && explicit < lis.length) return explicit;
  }
  return null;
}

function packedQtyForLine(order, snap, idx) {
  const lines = Array.isArray(order?.packing_info?.lines) ? order.packing_info.lines : [];
  const matches = [];
  for (const row of lines) {
    if (resolveLoadingPoLineIdx(order, row) === idx) matches.push(row);
  }
  if (matches.length === 1) return Number(matches[0].packed_qty || 0) || 0;
  const fromSnap = snap?.packed;
  if (fromSnap != null && fromSnap !== "") {
    const n = Number(fromSnap);
    if (Number.isFinite(n) && n > 0) return n;
  }
  if (!lines.length && Number(idx) === 0) return Number(order?.packing_info?.qty_packed || 0) || 0;
  return 0;
}

function legacyLoadedForLine(order, idx) {
  const rows = Array.isArray(order?.loading_info?.lines) ? order.loading_info.lines : [];
  if (rows.length) {
    const row = rows.find((r) => explicitLineIdx(r?.line_idx) === idx);
    return row ? Number(row.loaded_qty ?? row.loaded ?? 0) || 0 : 0;
  }
  const events = Array.isArray(order?.loading_info?.events) ? order.loading_info.events : [];
  return events.reduce((sum, ev) => (
    explicitLineIdx(ev?.line_idx) === idx ? sum + (Number(ev.qty || 0) || 0) : sum
  ), 0);
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

function sjIdentityKeys(row) {
  if (!row || typeof row !== "object") return [];
  return [
    row.id,
    row.file_id,
    row.shipment_id,
    row.sales_delivery_id,
    row.number,
    row.sj_number,
  ].map((v) => String(v || "").trim()).filter(Boolean);
}

export function isInactiveSjRecord(row) {
  return shipmentIsInactive(row);
}

/** Aliases for the selected dropdown SJ. Canonical shipment_id first; never cross-bind siblings. */
export function selectedSjIdentitySet(order, selectedSjId = null) {
  const targetSjId = String(selectedSjId || "").trim();
  const ids = new Set(targetSjId ? [targetSjId] : []);
  const shipments = Array.isArray(order?.shipments) ? order.shipments : [];
  const sjFiles = Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : [];
  const exactShip = shipments.find((s) => String(s?.id || "").trim() === targetSjId) || null;
  if (exactShip) {
    sjIdentityKeys(exactShip).forEach((k) => ids.add(k));
    const ownFile = sjFiles.find((f) => (
      String(f?.shipment_id || "").trim() === String(exactShip.id || "").trim()
      || String(f?.id || "").trim() === String(exactShip.id || "").trim()
    ));
    if (ownFile) sjIdentityKeys(ownFile).forEach((k) => ids.add(k));
    return ids;
  }
  const exactFile = sjFiles.find((f) => (
    String(f?.id || "").trim() === targetSjId || String(f?.shipment_id || "").trim() === targetSjId
  )) || null;
  if (exactFile) {
    sjIdentityKeys(exactFile).forEach((k) => ids.add(k));
    const ownShip = shipments.find((s) => String(s?.id || "").trim() === String(exactFile.shipment_id || exactFile.id || "").trim());
    if (ownShip) sjIdentityKeys(ownShip).forEach((k) => ids.add(k));
    return ids;
  }
  const aliasShips = shipments.filter((s) => sjIdentityKeys(s).includes(targetSjId));
  const aliasFiles = sjFiles.filter((f) => sjIdentityKeys(f).includes(targetSjId));
  if (aliasShips.length + aliasFiles.length !== 1) return ids;
  const row = aliasShips[0] || aliasFiles[0];
  sjIdentityKeys(row).forEach((k) => ids.add(k));
  return ids;
}

function selectedSjLineSource(order, selectedSjId = null) {
  const targetSjId = String(selectedSjId || "").trim();
  const shipments = Array.isArray(order?.shipments) ? order.shipments : [];
  const sjFiles = Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : [];
  const matchingFile = sjFiles.find((f) => sjIdentityKeys(f).includes(targetSjId)) || null;
  const matchingShipment = shipments.find((s) => {
    const keys = sjIdentityKeys(s);
    if (keys.includes(targetSjId)) return true;
    return matchingFile ? sjIdentityKeys(matchingFile).some((k) => keys.includes(k)) : false;
  }) || null;
  const shipLines = Array.isArray(matchingShipment?.lines) ? matchingShipment.lines : [];
  const fileLines = Array.isArray(matchingFile?.lines) ? matchingFile.lines : [];
  const matchingSj = (shipLines.length ? matchingShipment : matchingFile)
    || matchingShipment
    || matchingFile
    || null;
  return { matchingSj, matchingFile, matchingShipment, lineSource: shipLines.length ? matchingShipment : matchingFile };
}

export function loadingPoLines(order, selectedSjId = null) {
  const lis = Array.isArray(order?.line_items) ? order.line_items : [];
  const snapshot = Array.isArray(order?.shipment_lines) ? order.shipment_lines : [];
  const files = sjFilesForLoading(order);
  const targetSjId = String(selectedSjId || "").trim();
  const { matchingSj, lineSource } = selectedSjLineSource(order, targetSjId);

  const targetIds = selectedSjIdentitySet(order, targetSjId);

  const confirmations = loadingConfirmations(order);

  const sourceLines = Array.isArray(lineSource?.lines) ? lineSource.lines : [];
  const sjQtyByIdx = {};
  if (sourceLines.length) {
    for (const ln of sourceLines) {
      const resolved = resolveLoadingPoLineIdx(order, ln);
      if (resolved == null) continue;
      sjQtyByIdx[resolved] = (sjQtyByIdx[resolved] || 0) + (Number(ln?.qty ?? ln?.quantity ?? 0) || 0);
    }
  }

  return lis.map((li, idx) => {
    const snap = snapshot.find((r) => Number(r.line_idx) === idx) || {};
    const qtyPo = Number(li.quantity ?? snap.qty_po ?? 0) || 0;
    const packed = packedQtyForLine(order, snap, idx);
    const physical = physicalQtyForLoadingLine(order, snap, idx, li, qtyPo);

    let sjLineQty = 0;
    if (sourceLines.length) {
      sjLineQty = Number(sjQtyByIdx[idx] || 0) || 0;
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
        loaded_all: loadedSnap,
        available: Math.max(0, availSnap),
        physical_available: Math.max(0, availSnap),
        sj_available: Math.max(0, availSnap),
        label: poLineLabel(li, idx),
      };
    }

    let loadedForSj = 0;
    let totalLoadedLine = legacyLoadedForLine(order, idx);
    for (const conf of confirmations) {
      const confSj = String(conf.surat_jalan_id || "").trim();
      for (const ln of conf.lines || []) {
        const confIdx = explicitLineIdx(ln?.line_idx);
        if (confIdx === idx) {
          const qty = Number(ln.qty ?? ln.quantity ?? 0) || 0;
          totalLoadedLine += qty;
          if (targetIds.has(confSj)) {
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
      loaded_all: totalLoadedLine,
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
  const shipments = Array.isArray(order?.shipments) ? order.shipments : [];

  const files = Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : [];
  for (const f of files) {
    if (isInactiveSjRecord(f)) continue;
    const linked = shipments.find((s) => (
      String(s?.id || "").trim() === String(f?.shipment_id || f?.id || "").trim()
    ));
    if (linked && isInactiveSjRecord(linked)) continue;
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

export function actionableLoadingSjFiles(order) {
  const files = sjFilesForLoading(order);
  return files.filter((f) => {
    const lines = loadingPoLines(order, f.id);
    return lines.some((ln) => Number(ln.available || 0) > 0);
  });
}

export function nextActionableLoadingSjId(order, sjId) {
  const files = sjFilesForLoading(order);
  const id = String(sjId || "").trim();
  if (id && files.some((f) => String(f.id) === id)) return id;
  const actionable = actionableLoadingSjFiles(order);
  if (actionable.length === 1) return String(actionable[0].id);
  if (files.length === 1) return String(files[0].id);
  return "";
}

/** Exact selected Loading SJ. Never falls back to files[0] / first sibling. */
export function selectedLoadingSjOrNull(files, sjId) {
  const id = String(sjId || "").trim();
  if (!id) return null;
  const list = Array.isArray(files) ? files : [];
  return list.find((f) => String(f?.id || "") === id) || null;
}

/** Empty selection may default to the first file. A stale/unknown id stays unresolved. */
export function nextSelectedLoadingSjId(files, sjId) {
  const list = Array.isArray(files) ? files : [];
  const id = String(sjId || "").trim();
  if (!id) return list[0]?.id ? String(list[0].id) : "";
  if (list.some((f) => String(f?.id || "") === id)) return id;
  return id;
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
          if (nonPrint) {
            return {
              ok: false,
              error: "Loading tidak dapat dilakukan karena qty eligible belum tersedia.",
              lines: [],
            };
          }
          const packed = Number(po?.packed || 0);
          const need = Number(po?.sj_available || po?.qty_sj || qty);
          if (packed >= need && Number(po?.loaded_all || 0) > 0) {
            return {
              ok: false,
              error: `Qty sudah pernah dikonfirmasi Loading untuk ${po.name}: diperlukan ${fmtQtyId(need)}, sisa fisik ${fmtQtyId(po.physical_available)}.`,
              lines: [],
            };
          }
          return {
            ok: false,
            error: "Loading tidak dapat dilakukan karena belum ada qty Packing.",
            lines: [],
          };
        }
        return { ok: false, error: "Qty Loading belum bisa diisi. Seluruh kuantitas Surat Jalan untuk item ini sudah selesai di-loading.", lines: [] };
      }
      if (po?.physical_available != null && qty > po.physical_available) {
        const nonPrint = String(po?.production_process || "").toLowerCase() === "non_print";
        if (nonPrint) {
          return {
            ok: false,
            error: `Qty melebihi qty eligible yang tersedia (${po.physical_available}).`,
            lines: [],
          };
        }
        const packed = Number(po?.packed || 0);
        const loadedAll = Number(po?.loaded_all || 0);
        if (packed >= qty && loadedAll > 0) {
          return {
            ok: false,
            error: `Sisa fisik tidak mencukupi untuk ${po.name}: diperlukan ${fmtQtyId(qty)}, sisa fisik ${fmtQtyId(po.physical_available)} (sudah Loading ${fmtQtyId(loadedAll)}).`,
            lines: [],
          };
        }
        return {
          ok: false,
          error: `Qty melebihi qty Packing yang tersedia (${po.physical_available}).`,
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

function fmtQtyId(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function physicalBlockFromSelectedLines(poLines) {
  const printShort = (poLines || []).find((ln) => (
    ln.production_process !== "non_print"
    && Number(ln.qty_sj || 0) > 0
    && Number(ln.physical_available || 0) < Number(ln.sj_available || ln.qty_sj || 0)
    && Number(ln.loaded || 0) < Number(ln.qty_sj || 0)
  ));
  if (!printShort) return null;
  const need = Number(printShort.sj_available || printShort.qty_sj || 0);
  const remain = Number(printShort.physical_available || 0);
  const packed = Number(printShort.packed || 0);
  const loadedAll = Number(printShort.loaded_all ?? 0);
  if (packed < need) {
    return {
      kind: "packing_insufficient",
      line: printShort,
      reason: `Packing belum mencukupi untuk ${printShort.name}: diperlukan ${fmtQtyId(need)}, tersedia ${fmtQtyId(remain)}.`,
    };
  }
  if (remain <= 0) {
    return {
      kind: "prior_loading_consumed",
      line: printShort,
      reason: `Qty sudah pernah dikonfirmasi Loading untuk ${printShort.name}: diperlukan ${fmtQtyId(need)}, sisa fisik ${fmtQtyId(remain)}.`,
    };
  }
  return {
    kind: "prior_loading_consumed",
    line: printShort,
    reason: `Sisa fisik tidak mencukupi untuk ${printShort.name}: diperlukan ${fmtQtyId(need)}, sisa fisik ${fmtQtyId(remain)} (sudah Loading ${fmtQtyId(loadedAll)}).`,
  };
}

/** Selected-SJ verification. Never reuse order-level enrich from another / latest SJ. */
export function loadingVerificationStatus(order, suratJalanId = null) {
  const files = sjFilesForLoading(order);
  const sid = String(suratJalanId || "").trim();
  const identities = selectedSjIdentitySet(order, sid);
  const enriched = order?.loading_verification_status;
  const enrichMatchesSelected = !!(
    enriched
    && typeof enriched === "object"
    && sid
    && identities.has(String(enriched.surat_jalan_id || "").trim())
  );

  const open = loadingDiscrepancies(order).filter((d) => !sid || identities.has(String(d.surat_jalan_id || "").trim()));
  if (open.length) {
    return { code: "held_qty_mismatch", label: "Tertahan — Qty Tidak Sesuai SJ", surat_jalan_id: sid, discrepancies: open };
  }
  if (enrichMatchesSelected && String(enriched.code || "").includes("held")) {
    return { ...enriched, surat_jalan_id: sid };
  }
  if (!files.length) return { code: "waiting_verification", label: "Menunggu Verifikasi", surat_jalan_id: sid };
  if (enrichMatchesSelected && enriched.sj_changed) {
    return { ...enriched, surat_jalan_id: sid };
  }

  const poLines = loadingPoLines(order, sid);
  const remaining = poLines.reduce((s, r) => s + Number(r.available || 0), 0);
  const loaded = poLines.reduce((s, r) => s + Number(r.loaded || 0), 0);
  const need = poLines.reduce((s, r) => s + Number(r.qty_sj || 0), 0);
  if (need > 0 && loaded >= need && remaining <= 0) {
    return { code: "loading_complete", label: "Loading Selesai", surat_jalan_id: sid };
  }
  if (enrichMatchesSelected && String(enriched.code || "").includes("complete")) {
    return { ...enriched, surat_jalan_id: sid };
  }

  const short = physicalBlockFromSelectedLines(poLines);
  if (short) {
    if (short.kind === "packing_insufficient") {
      return {
        code: "packing_insufficient",
        label: "Packing Belum Mencukupi",
        surat_jalan_id: sid,
        reason: short.reason,
      };
    }
    return {
      code: "prior_loading_consumed",
      label: "Qty Sudah Dikonfirmasi Loading",
      surat_jalan_id: sid,
      reason: short.reason,
    };
  }
  return { code: "ready_to_verify", label: "Siap Diverifikasi", surat_jalan_id: sid };
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
