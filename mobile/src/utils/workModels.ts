import { fmtQty } from "./format";
import {
  cetakTaskReady,
  helperTaskReady,
  loadingTaskReady,
  packingTaskReady,
  pickSjFile,
  screenerTaskReady,
  sjFilesForLoading,
} from "./taskEligibility";

type AnyOrder = Record<string, any>;

const isPrintLine = (li: any) => (li?.production_process || "print").toLowerCase() !== "non_print";

function sumEntryQty(entries: any[]): number {
  return (entries || [])
    .filter((e) => e && !e.cancelled && !e.replaced)
    .reduce((s, e) => s + Number(e.qty || 0), 0);
}

function isVoidedEntry(e: any): boolean {
  return !!(e?.cancelled || e?.replaced);
}

/** Persisted Operator completion for one exact split. Never target / helper / stage. */
export function operatorSplitDone(split: any): number {
  if (split?.qty_completed != null && split.qty_completed !== "") {
    const n = Number(split.qty_completed);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }
  const entries = Array.isArray(split?.progress_entries) ? split.progress_entries : [];
  const valid = entries.filter((e: any) => e && !isVoidedEntry(e));
  if (!valid.length) return 0;
  return Math.max(0, sumEntryQty(valid));
}

export function operatorAssignmentKey(split: any): string {
  const id = String(split?.id || "").trim();
  if (id) return id;
  return [
    String(split?.operator_email || "").trim().toLowerCase(),
    String(split?.line_idx ?? ""),
    String(split?.work_date || ""),
  ].join(":");
}

export function operatorConfirmEnabled(opts: {
  hasOperatorUpdate: boolean;
  workflowEligible: boolean;
  remaining: number;
  inputQty: number;
  submitting: boolean;
}): boolean {
  if (!opts.hasOperatorUpdate || !opts.workflowEligible || opts.submitting) return false;
  if (opts.remaining <= 0) return false;
  if (!(opts.inputQty > 0)) return false;
  if (opts.inputQty > opts.remaining) return false;
  return true;
}

export function statusLabel(done: number, target: number): string {
  if (done <= 0) return "Belum";
  if (target > 0 && done >= target) return "Selesai";
  return "Proses";
}

export function operatorStatusLabel(done: number, target: number): string {
  if (done <= 0) return "Belum Mulai";
  if (target > 0 && done >= target) return "Selesai";
  return "Proses";
}

export function screenerModel(order: AnyOrder) {
  const printItems = (order?.line_items || [])
    .map((li: any, idx: number) => ({ li, idx }))
    .filter(({ li }: any) => isPrintLine(li));
  const target =
    typeof order?.print_qty_total === "number"
      ? Number(order.print_qty_total)
      : printItems.reduce((s: number, { li }: any) => s + Number(li.quantity || 0), 0);
  const info = order?.screener_info || {};
  const entries = info.entries || [];
  const done = entries.length ? sumEntryQty(entries) : Number(info.screen_qty || 0);
  const ready = screenerTaskReady(order);
  return {
    title: "Pekerjaan Screener",
    target,
    done,
    remaining: Math.max(0, target - done),
    status: statusLabel(done, target),
    gateLocked: !ready.actionable,
    gateMsg: ready.lockedReason || "Menunggu Penjadwalan",
    itemLabel: printItems.length === 1 ? printItems[0].li.name : `${printItems.length} item`,
  };
}

export function helperModel(order: AnyOrder) {
  const printItems = (order?.line_items || [])
    .map((li: any, idx: number) => ({ li, idx }))
    .filter(({ li }: any) => isPrintLine(li));
  const splits = order?.operator_splits || [];
  const helperItems = order?.helper_prep?.items || [];
  const helperEntries = order?.helper_prep?.entries || [];

  const lineTarget = (idx: number) => {
    const lineSplits = splits.filter((s: any) => Number(s.line_idx) === Number(idx));
    if (lineSplits.length) {
      return lineSplits.reduce((s: number, x: any) => s + Number(x.quantity || 0), 0);
    }
    const li = printItems.find((p: any) => p.idx === idx)?.li;
    return Number(li?.quantity || 0);
  };

  const lines = printItems.map(({ li, idx }: any) => {
    const stored = helperItems.find((it: any) => Number(it.line_idx) === Number(idx));
    const lineEntries = (helperEntries || []).filter(
      (e: any) => e && !e.cancelled && !e.replaced && Number(e.line_idx) === Number(idx)
    );
    const done = lineEntries.length ? sumEntryQty(lineEntries) : Number(stored?.qty_prepared || 0);
    const target = lineTarget(idx);
    return {
      lineIdx: idx,
      name: li.name || `Line ${idx + 1}`,
      color: li.color || "",
      target,
      done,
      remaining: Math.max(0, target - done),
    };
  });

  const headerTarget = splits.length
    ? splits.reduce((s: number, x: any) => s + Number(x.quantity || 0), 0)
    : lines.reduce((s: number, l: any) => s + l.target, 0);
  const headerDone = lines.reduce((s: number, l: any) => s + l.done, 0);
  const ready = helperTaskReady(order);
  return {
    title: "Pekerjaan Helper",
    target: headerTarget,
    done: headerDone,
    remaining: Math.max(0, headerTarget - headerDone),
    status: statusLabel(headerDone, headerTarget),
    gateLocked: !ready.actionable,
    gateMsg: ready.lockedReason || "Menunggu Penjadwalan",
    lines,
  };
}

export function packingModel(order: AnyOrder) {
  const items = order?.line_items || [];
  const packLines = order?.packing_info?.lines || [];
  const snapRows = order?.packing_lines || [];
  const lines = items.map((li: any, idx: number) => {
    const snap = snapRows.find((r: any) => Number(r.line_idx) === Number(idx));
    const stored = packLines.find((x: any) => Number(x.line_idx) === Number(idx));
    const done = Number(snap?.packed != null ? snap.packed : stored?.packed_qty || 0);
    const target = Number(snap?.available != null ? snap.available : li.quantity || 0);
    const remaining = Number(snap?.remaining != null ? snap.remaining : Math.max(0, target - done));
    return {
      lineIdx: idx,
      name: li.name || `Line ${idx + 1}`,
      color: li.color || "",
      target,
      done,
      remaining,
    };
  }).filter((l: any) => l.target > 0 || l.done > 0);
  const headerTarget = lines.reduce((s: number, l: any) => s + l.target, 0);
  const headerDone = lines.reduce((s: number, l: any) => s + l.done, 0);
  const ready = packingTaskReady(order);
  const hasQty = lines.some((l: any) => l.remaining > 0);
  const waiting = !hasQty;
  return {
    title: "Pekerjaan Packing",
    target: headerTarget,
    done: headerDone,
    remaining: Math.max(0, headerTarget - headerDone),
    status: statusLabel(headerDone, headerTarget),
    gateLocked: !ready.actionable || waiting,
    gateMsg: !ready.actionable
      ? ready.lockedReason || "Belum siap Packing"
      : "Qty Packing menunggu qty yang tersedia (Operator/non-cetak).",
    lines,
  };
}

export function loadingModel(order: AnyOrder, preferredSjId?: string | null) {
  const files = sjFilesForLoading(order);
  const selected = pickSjFile(files, preferredSjId);
  const items = order?.line_items || [];
  const snapRows = order?.shipment_lines || [];
  const packLines = order?.packing_info?.lines || [];
  const lines = items.map((li: any, idx: number) => {
    const snap = snapRows.find((r: any) => Number(r.line_idx) === Number(idx)) || {};
    const packedRow = packLines.find((x: any) => Number(x.line_idx) === Number(idx));
    const packed = Number(snap.packed != null ? snap.packed : packedRow?.packed_qty || 0);
    const loaded = Number(snap.loaded || 0);
    const qtyPo = Number(li.quantity || snap.qty_po || 0);
    const cap = qtyPo > 0 ? Math.min(packed, qtyPo) : packed;
    const available = Number(
      snap.available_loading != null ? snap.available_loading : Math.max(0, cap - loaded)
    );
    return {
      lineIdx: idx,
      name: li.name || `Line ${idx + 1}`,
      color: li.color || "",
      target: cap,
      done: loaded,
      remaining: Math.max(0, available),
    };
  });
  const headerTarget = lines.reduce((s: number, l: any) => s + l.target, 0);
  const headerDone = lines.reduce((s: number, l: any) => s + l.done, 0);
  const ready = loadingTaskReady(order);
  return {
    title: "Pekerjaan Loading",
    target: headerTarget,
    done: headerDone,
    remaining: Math.max(0, headerTarget - headerDone),
    status: statusLabel(headerDone, headerTarget),
    gateLocked: !ready.actionable,
    gateMsg: ready.lockedReason || "Belum ada qty Packing",
    files,
    selectedSjId: selected?.id || "",
    lines,
  };
}

export function operatorMyLines(order: AnyOrder, user: { email?: string; id?: string } | null) {
  const email = (user?.email || "").trim().toLowerCase();
  const uid = String(user?.id || "").trim();
  const items = order?.line_items || [];
  const out: any[] = [];
  for (const s of order?.operator_splits || []) {
    const qty = Number(s?.quantity || 0);
    if (qty <= 0) continue;
    const se = (s.operator_email || "").trim().toLowerCase();
    const su = String(s.operator_user_id || s.operator_id || "").trim();
    const mine = (email && se && se === email) || (uid && su && su === uid);
    if (!mine) continue;
    const idx = Number(s.line_idx);
    const li = Number.isFinite(idx) ? items[idx] || {} : {};
    const done = operatorSplitDone(s);
    const splitId = operatorAssignmentKey(s);
    out.push({
      lineIdx: idx,
      splitId,
      name: li.name || `Line ${idx + 1}`,
      color: li.color || "",
      target: qty,
      done,
      remaining: Math.max(0, qty - done),
      split: s,
    });
  }
  return out;
}

export function operatorPanelModel(order: AnyOrder, user: { email?: string; id?: string } | null) {
  const lines = operatorMyLines(order, user);
  const target = lines.reduce((s, l) => s + l.target, 0);
  const done = lines.reduce((s, l) => s + l.done, 0);
  const remaining = Math.max(0, target - done);
  return {
    title: "Pekerjaan Cetak",
    target,
    done,
    remaining,
    status: operatorStatusLabel(done, target),
    lines,
  };
}

export function operatorCanSubmit(order: AnyOrder): { ok: boolean; reason?: string } {
  const ready = cetakTaskReady(order);
  if (!ready.actionable) return { ok: false, reason: ready.lockedReason || undefined };
  return { ok: true };
}

export function metricsText(target: number, done: number, remain: number): string {
  return `Target ${fmtQty(target)} · Selesai ${fmtQty(done)} · Sisa ${fmtQty(remain)}`;
}

export const MIN_DELIVERY_PROOFS = 2;

export function canConfirmDelivered(proofs: any[]): boolean {
  return Array.isArray(proofs) && proofs.length >= MIN_DELIVERY_PROOFS;
}
