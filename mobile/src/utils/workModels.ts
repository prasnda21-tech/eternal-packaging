import { fmtQty } from "./format";

type AnyOrder = Record<string, any>;

const isPrintLine = (li: any) => (li?.production_process || "print").toLowerCase() !== "non_print";

function sumEntryQty(entries: any[]): number {
  return (entries || [])
    .filter((e) => e && !e.cancelled && !e.replaced)
    .reduce((s, e) => s + Number(e.qty || 0), 0);
}

function splitDone(split: any): number {
  const entries = split?.progress_entries || [];
  const valid = entries.filter((e: any) => e && !e.cancelled && !e.replaced);
  if (valid.length) return sumEntryQty(valid);
  return Number(split?.qty_completed || 0);
}

export function statusLabel(done: number, target: number): string {
  if (done <= 0) return "Belum";
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
  const scheduled = Array.isArray(order?.operator_splits) && order.operator_splits.some((s: any) => Number(s.quantity || 0) > 0);
  return {
    title: "Pekerjaan Screener",
    target,
    done,
    remaining: Math.max(0, target - done),
    status: statusLabel(done, target),
    gateLocked: !scheduled,
    gateMsg: "Screener tersedia otomatis setelah Penjadwalan disimpan.",
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
  const scheduled = splits.some((s: any) => Number(s.quantity || 0) > 0);
  return {
    title: "Pekerjaan Helper",
    target: headerTarget,
    done: headerDone,
    remaining: Math.max(0, headerTarget - headerDone),
    status: statusLabel(headerDone, headerTarget),
    gateLocked: !scheduled,
    gateMsg: "Persiapan Helper tersedia setelah Penjadwalan disimpan.",
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
    const li = items[idx] || {};
    const done = splitDone(s);
    out.push({
      lineIdx: idx,
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

export function operatorCanSubmit(order: AnyOrder): { ok: boolean; reason?: string } {
  if (!order?.sample_approved && order?.requires_printing !== false) {
    return { ok: false, reason: "Cetak Sample harus disetujui sebelum Qty Selesai Cetak dapat diisi." };
  }
  return { ok: true };
}

export function metricsText(target: number, done: number, remain: number): string {
  return `Target ${fmtQty(target)} · Selesai ${fmtQty(done)} · Sisa ${fmtQty(remain)}`;
}

export const MIN_DELIVERY_PROOFS = 2;

export function canConfirmDelivered(proofs: any[]): boolean {
  return Array.isArray(proofs) && proofs.length >= MIN_DELIVERY_PROOFS;
}
