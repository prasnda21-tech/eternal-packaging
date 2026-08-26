import { packingLinesForOrder } from "./packingCard";
import { shipmentLinesForOrder, storedShipments } from "./shipmentLines";
import { getWorkflowReady } from "./stageState";
import { entriesForLine, sumEntryQty, validEntries, visibleEntries } from "./workflowEntries";

export const WORK_DIVISION_IDS = [
  "screener",
  "helper",
  "operator",
  "packing",
  "surat_jalan",
  "loading",
  "terkirim",
];

export const DIVISION_SUBMIT_PERMS = [
  ["screener", "screen.update"],
  ["helper", "helper.prepare"],
  ["operator", "operator.update"],
  ["packing", "packing.update"],
  ["surat_jalan", "delivery_note.manage"],
  ["loading", "loading.update"],
  ["terkirim", "delivery.update"],
];

const isPrintLine = (li) => (li?.production_process || "print").toLowerCase() !== "non_print";

export function workPanelPath(orderId, divisionId = "operator") {
  return `/po/${encodeURIComponent(orderId)}/kerja?divisi=${encodeURIComponent(divisionId)}`;
}

export function permittedDivisionIds(hasPerm) {
  if (typeof hasPerm !== "function") return [];
  return DIVISION_SUBMIT_PERMS.filter(([, perm]) => hasPerm(perm)).map(([id]) => id);
}

export function defaultDivisionId(hasPerm, preferred) {
  const ids = permittedDivisionIds(hasPerm);
  if (preferred && ids.includes(preferred)) return preferred;
  if (ids.length === 1) return ids[0];
  return ids[0] || null;
}

/** List click: one or more work submit perms → focused work panel. Else full Detail PO. */
export function schedulePoHref(orderId, hasPerm, preferredDivision) {
  const id = defaultDivisionId(hasPerm, preferredDivision);
  if (id) return workPanelPath(orderId, id);
  return `/po/${orderId}`;
}

export const ACTION_DIVISION = {
  screener: "screener",
  helper: "helper",
  operator_work: "operator",
  cetak_qty: "operator",
  sample_upload: "operator",
  packing: "packing",
  surat_jalan: "surat_jalan",
  loading: "loading",
  terkirim: "terkirim",
};

/** Tugas Saya: work panel of own division unless the user may open full Detail PO and has no work submit perm. */
export function taskPoHref(orderId, hasPerm, actionKey) {
  const preferred = ACTION_DIVISION[actionKey];
  const workIds = permittedDivisionIds(hasPerm);
  const canFullPo = typeof hasPerm === "function" && hasPerm("po.view");
  if (preferred && workIds.includes(preferred)) {
    return workPanelPath(orderId, preferred);
  }
  if (workIds.length && (!canFullPo || preferred)) {
    return schedulePoHref(orderId, hasPerm, preferred);
  }
  if (canFullPo) return `/po/${orderId}`;
  if (workIds.length) return workPanelPath(orderId, workIds[0]);
  return `/po/${orderId}`;
}

export function divisionById(settings, id) {
  const rows = settings?.divisions || [];
  return rows.find((d) => d.id === id) || null;
}

export function fallbackDivision(id) {
  const map = {
    screener: { id: "screener", label: "Screener", active: true, implemented: true, submit_perm: "screen.update", input_label: "Qty Screener", target_source: "screener_qty", input_kind: "qty" },
    helper: { id: "helper", label: "Helper", active: true, implemented: true, submit_perm: "helper.prepare", input_label: "Qty Prepared", target_source: "helper_prepared", input_kind: "qty" },
    operator: { id: "operator", label: "Operator", active: true, implemented: true, submit_perm: "operator.update", input_label: "Qty Cetak", target_source: "print_line_qty", input_kind: "qty" },
    packing: { id: "packing", label: "Packing", active: true, implemented: true, submit_perm: "packing.update", input_label: "Qty Packing", target_source: "packing_cap", input_kind: "qty" },
    surat_jalan: { id: "surat_jalan", label: "Surat Jalan", active: true, implemented: true, submit_perm: "delivery_note.manage", input_label: "Surat Jalan", target_source: "packed_qty", input_kind: "evidence" },
    loading: { id: "loading", label: "Loading", active: true, implemented: true, submit_perm: "loading.update", input_label: "Qty Loading", target_source: "sj_qty", input_kind: "qty" },
    terkirim: { id: "terkirim", label: "Terkirim", active: true, implemented: true, submit_perm: "delivery.update", input_label: "Bukti Pengiriman", target_source: "delivered_qty", input_kind: "evidence" },
  };
  return map[id] || map.operator;
}

export function operatorStatusLabel(done, target) {
  const d = Number(done || 0);
  const t = Number(target || 0);
  if (d <= 0) return "Belum";
  if (t > 0 && d >= t) return "Selesai";
  return "Proses";
}

export function printLineTarget(li) {
  return Number(li?.quantity || 0);
}

export function orderPrintTarget(order) {
  if (typeof order?.print_qty_target === "number" && Number(order.print_qty_target) > 0) {
    return Number(order.print_qty_target);
  }
  const assigned = Number(order?.operator_assigned_qty || 0);
  if (assigned > 0) return assigned;
  return Number(order?.print_qty_total || 0);
}

export function orderPrintDone(order) {
  if (typeof order?.print_completed_qty === "number") {
    return Number(order.print_completed_qty);
  }
  return (order?.operator_splits || []).reduce((s, x) => s + Number(x?.qty_completed || 0), 0);
}

function splitDone(split) {
  const stored = validEntries(split?.progress_entries);
  if (stored.length) return sumEntryQty(stored);
  return Number(split?.qty_completed || 0);
}

export function operatorLineModels(order, targetSource = "print_line_qty") {
  const items = order?.line_items || [];
  const splits = order?.operator_splits || [];
  return items
    .map((li, idx) => ({ li, idx }))
    .filter(({ li }) => isPrintLine(li))
    .map(({ li, idx }) => {
      const lineSplits = splits.filter((s) => Number(s.line_idx) === Number(idx));
      const entries = lineSplits.flatMap((s) =>
        visibleEntries(s.progress_entries, s.qty_completed, s.updated_at, s.operator_name).map((e) => ({
          ...e,
          split_id: s.id,
        }))
      );
      const done = lineSplits.reduce((s, sp) => s + splitDone(sp), 0);
      let target = printLineTarget(li);
      if (targetSource === "split_allocation") {
        const alloc = lineSplits.reduce((s, sp) => s + Number(sp.quantity || 0), 0);
        if (alloc > 0) target = alloc;
      } else if (targetSource === "print_qty_target" && items.filter(isPrintLine).length === 1) {
        target = orderPrintTarget(order) || target;
      }
      return {
        lineIdx: idx,
        itemId: li.item_id || li.id || `line-${idx}`,
        name: li.name || `Line ${idx + 1}`,
        color: li.color || "",
        unit: li.unit || "pcs",
        target,
        done,
        remaining: Math.max(0, target - done),
        entries,
        splits: lineSplits,
      };
    });
}

export function pickSubmitSplit(line, user, hasPerm) {
  const splits = line?.splits || [];
  if (!splits.length) return null;
  const email = user?.email;
  const mine = splits.find((s) => s.operator_email && s.operator_email === email);
  if (mine) return mine;
  if (typeof hasPerm === "function" && hasPerm("schedule.edit")) return splits[0];
  return null;
}

/** Personal Operator rows: scheduled qty of this user, never line/PO aggregate. */
export function operatorMyAssignmentModels(order, user) {
  const email = (user?.email || "").trim().toLowerCase();
  const uid = String(user?.id || "").trim();
  const items = order?.line_items || [];
  const out = [];
  for (const s of order?.operator_splits || []) {
    const qty = Number(s?.quantity || 0);
    if (qty <= 0) continue;
    const se = (s.operator_email || "").trim().toLowerCase();
    const su = String(s.operator_user_id || s.operator_id || "").trim();
    const mine = (email && se && se === email) || (uid && su && su === uid);
    if (!mine) continue;
    const idx = Number(s.line_idx);
    const li = items[idx] || {};
    const done = splitDone(s);
    out.push({
      lineIdx: idx,
      itemId: li.item_id || li.id || s.id || `split-${idx}`,
      name: li.name || `Line ${idx + 1}`,
      color: li.color || "",
      unit: li.unit || "pcs",
      target: qty,
      done,
      remaining: Math.max(0, qty - done),
      entries: visibleEntries(s.progress_entries, s.qty_completed, s.updated_at, s.operator_name),
      splits: [s],
    });
  }
  return out;
}

export function canSubmitDivision(hasPerm, division) {
  if (!division || division.active === false || division.implemented === false) return false;
  const perm = division.submit_perm;
  return typeof hasPerm === "function" && perm ? !!hasPerm(perm) : false;
}

function printItems(order) {
  return (order?.line_items || [])
    .map((li, idx) => ({ li, idx }))
    .filter(({ li }) => isPrintLine(li));
}

export function screenerWorkModel(order) {
  const target = Number(order?.print_qty_total || printItems(order).reduce((s, { li }) => s + Number(li.quantity || 0), 0));
  const info = order?.screener_info || {};
  const entries = visibleEntries(info.entries, info.screen_qty, info.updated_at, info.updated_by_name);
  const done = entries.length ? sumEntryQty(entries) : Number(info.screen_qty || 0);
  const ready = getWorkflowReady(order);
  return {
    title: "Pekerjaan Screener",
    stage: "Screener",
    headerTarget: target,
    headerDone: done,
    headerRemain: Math.max(0, target - done),
    status: operatorStatusLabel(done, target),
    itemLabel: printItems(order).length === 1 ? printItems(order)[0].li.name : `${printItems(order).length || 0} item`,
    gateLocked: ready.screener !== true,
    gateMsg: "Screener tersedia otomatis setelah Penjadwalan disimpan.",
    lines: [{
      lineIdx: "po",
      itemId: "screener",
      name: "Qty Screener",
      unit: "pcs",
      target,
      done,
      remaining: Math.max(0, target - done),
      capRemaining: false,
      entries,
    }],
  };
}

export function helperScheduleTarget(order) {
  const splits = order?.operator_splits || [];
  if (splits.length) {
    return splits.reduce((s, x) => s + Number(x.quantity || 0), 0);
  }
  if (typeof order?.print_qty_total === "number") return Number(order.print_qty_total);
  return printItems(order).reduce((s, { li }) => s + Number(li.quantity || 0), 0);
}

export function helperScheduleTargetForLine(order, lineIdx) {
  const splits = order?.operator_splits || [];
  if (splits.length) {
    return splits
      .filter((s) => Number(s.line_idx) === Number(lineIdx))
      .reduce((s, x) => s + Number(x.quantity || 0), 0);
  }
  return Number((order?.line_items || [])[lineIdx]?.quantity || 0);
}

export function helperWorkModel(order) {
  const items = printItems(order);
  const helperItems = order?.helper_prep?.items || [];
  const helperEntries = order?.helper_prep?.entries || [];
  const lines = items.map(({ li, idx }) => {
    const stored = helperItems.find((it) => Number(it.line_idx) === Number(idx));
    const lineEntries = entriesForLine(helperEntries, idx);
    const vis = visibleEntries(lineEntries, stored?.qty_prepared, order?.helper_prep?.updated_at, order?.helper_prep?.updated_by_name);
    const done = vis.length ? sumEntryQty(vis) : Number(stored?.qty_prepared || 0);
    const target = helperScheduleTargetForLine(order, idx);
    const remaining = Math.max(0, target - done);
    return {
      lineIdx: idx,
      itemId: li.item_id || li.id || `helper-${idx}`,
      name: li.name || `Line ${idx + 1}`,
      color: li.color || "",
      unit: li.unit || "pcs",
      target,
      done,
      remaining,
      overPrepared: Math.max(0, done - target),
      entries: vis,
    };
  });
  const headerTarget = helperScheduleTarget(order);
  const headerDone = lines.reduce((s, l) => s + l.done, 0);
  const ready = getWorkflowReady(order);
  const headerRemain = Math.max(0, headerTarget - headerDone);
  return {
    title: "Pekerjaan Helper",
    stage: "Helper",
    headerTarget,
    headerDone,
    headerRemain,
    overPrepared: Math.max(0, headerDone - headerTarget),
    metricLabels: { target: "Target Persiapan", done: "Sudah Disiapkan", remain: "Sisa" },
    status: operatorStatusLabel(headerDone, headerTarget),
    itemLabel: lines.length === 1 ? lines[0].name : `${lines.length} item`,
    gateLocked: ready.helper !== true,
    gateMsg: "Persiapan Helper tersedia setelah Penjadwalan disimpan.",
    lines,
  };
}

export function packingWorkModel(order) {
  const rows = packingLinesForOrder(order);
  const events = order?.packing_info?.events || [];
  const lines = rows.map((row) => {
    const vis = visibleEntries(
      entriesForLine(events, row.line_idx),
      row.packed,
      order?.packing_info?.updated_at,
      order?.packing_info?.updated_by_name,
    );
    const done = Number(row.packed || 0);
    const target = Number(row.available != null ? row.available : row.qty_po || 0);
    const remaining = Number(row.remaining != null ? row.remaining : Math.max(0, target - done));
    return {
      lineIdx: row.line_idx,
      itemId: `pack-${row.line_idx}`,
      name: row.name,
      color: row.color || "",
      unit: "pcs",
      target,
      done,
      remaining,
      entries: vis,
    };
  });
  const headerTarget = lines.reduce((s, l) => s + l.target, 0);
  const headerDone = lines.reduce((s, l) => s + l.done, 0);
  const packingWait = lines.length === 0 || lines.every((l) => l.remaining <= 0 && l.done <= 0);
  return {
    title: "Pekerjaan Packing",
    stage: "Packing",
    headerTarget,
    headerDone,
    headerRemain: Math.max(0, headerTarget - headerDone),
    status: operatorStatusLabel(headerDone, headerTarget),
    itemLabel: lines.length === 1 ? lines[0].name : `${lines.length} item`,
    gateLocked: packingWait,
    gateMsg: "Qty Packing menunggu qty yang tersedia (Operator/non-cetak).",
    lines,
  };
}

export function loadingWorkModel(order) {
  const rows = shipmentLinesForOrder(order);
  const events = order?.loading_info?.events || [];
  const lines = rows.map((row) => {
    const vis = visibleEntries(
      entriesForLine(events, row.line_idx),
      row.loaded,
      order?.loading_info?.updated_at,
      order?.loading_checklist?.updated_by_name,
    );
    const done = Number(row.loaded || 0);
    const target = Number(row.loading_cap || row.packed || 0);
    return {
      lineIdx: row.line_idx,
      itemId: `load-${row.line_idx}`,
      name: row.name,
      color: row.color || "",
      unit: "pcs",
      target,
      done,
      remaining: Math.max(0, Number(row.available_loading != null ? row.available_loading : target - done)),
      entries: vis,
    };
  });
  const headerTarget = lines.reduce((s, l) => s + l.target, 0);
  const headerDone = lines.reduce((s, l) => s + l.done, 0);
  const sjReady = !!order?.surat_jalan_created;
  return {
    title: "Pekerjaan Loading",
    stage: "Loading",
    headerTarget,
    headerDone,
    headerRemain: Math.max(0, headerTarget - headerDone),
    status: operatorStatusLabel(headerDone, headerTarget),
    itemLabel: lines.length === 1 ? lines[0].name : `${lines.length} item`,
    gateLocked: !sjReady,
    gateMsg: "Qty Loading dapat diisi setelah Surat Jalan dibuat.",
    lines,
  };
}

export function suratJalanEvidence(order) {
  return storedShipments(order).map((sh, i) => ({
    id: sh.id || sh.accurate_shipment_id || `sj-${i}`,
    qty: (sh.lines || []).reduce((s, l) => s + Number(l.qty || l.quantity || 0), 0),
    created_at: sh.created_at,
    created_by_name: sh.created_by_name || sh.created_by || "",
    label: sh.number || sh.accurate_shipment_id || "Surat Jalan",
    locked: true,
  }));
}

export function terkirimEvidence(order) {
  return (order?.delivery_proofs || []).map((p, i) => ({
    id: p.id || `proof-${i}`,
    qty: p.qty != null ? Number(p.qty) : null,
    created_at: p.created_at || p.uploaded_at,
    created_by_name: p.uploaded_by_name || p.created_by_name || p.created_by || "",
    label: p.note || "Bukti pengiriman",
    locked: true,
  }));
}

export function deliveryPartialPercent(order) {
  const delivered = Number(order?.qty_delivered || order?.delivered_qty || 0);
  const total = Number(order?.total_quantity || 0);
  if (total <= 0) return 0;
  return Math.round((delivered / total) * 100);
}
