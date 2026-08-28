/** Rollback targets: numbered rows with stable ids (never UI index or stage slug). */

import { STAGES } from "./constants";

/** Last selective destination. Surat Jalan / Loading / Terkirim are PO-wide, not per-card. */
export const SELECTIVE_STAGE_MAX = STAGES.indexOf("Packing");

function hasEntries(split) {
  return (split?.progress_entries || []).some((e) => e && !e.cancelled);
}

function lineName(order, lineIdx) {
  const lis = order?.line_items || [];
  if (lineIdx == null || lineIdx === "") return "";
  const li = lis[Number(lineIdx)];
  return li?.name || li?.item || "";
}

function splitHasId(split) {
  return !!(split && String(split.id || "").trim());
}

export function lineStableId(order, idx) {
  const lis = order?.line_items || [];
  const li = lis[idx];
  if (!li) return null;
  const direct = li.id || li.line_id || li.accurate_line_id;
  if (direct) return String(direct);
  const aid = String(li.accurate_item_id || "").trim();
  if (!aid) return null;
  const same = lis.filter((row) => String(row?.accurate_item_id || "").trim() === aid);
  if (same.length === 1) return `item:${aid}`;
  return null;
}

function splitRow(order, s) {
  return {
    id: String(s.id).trim(),
    label: [s.operator_name || s.operator_email, lineName(order, s.line_idx)].filter(Boolean).join(" · "),
    filled: Number(s.qty_completed || 0) > 0 || hasEntries(s),
    qty: s.qty_completed,
    operator: s.operator_name || s.operator_email || "",
  };
}

function lineRow(order, idx, sid) {
  const li = (order?.line_items || [])[idx] || {};
  const splits = (order?.operator_splits || []).filter((s) => {
    try {
      return Number(s?.line_idx) === Number(idx);
    } catch {
      return false;
    }
  });
  const qty = splits.reduce((sum, s) => sum + Number(s?.qty_completed || 0), 0);
  const filled = qty > 0 || splits.some(hasEntries);
  return {
    id: sid,
    label: lineName(order, idx) || sid,
    filled,
    qty: splits.length ? qty : null,
    operator: splits.map((s) => s.operator_name || s.operator_email).filter(Boolean).join(", "),
  };
}

function lineTargets(order) {
  const lis = order?.line_items || [];
  const rows = [];
  const seen = new Set();
  for (let idx = 0; idx < lis.length; idx += 1) {
    const sid = lineStableId(order, idx);
    if (!sid || seen.has(sid)) continue;
    seen.add(sid);
    rows.push(lineRow(order, idx, sid));
  }
  return rows;
}

export function rollbackTargets(order) {
  const allSplits = Array.isArray(order?.operator_splits) ? order.operator_splits : [];
  const withId = allSplits.filter(splitHasId);
  const base = withId.length
    ? withId.map((s) => splitRow(order, s))
    : lineTargets(order);
  return base.map((row, i) => ({ ...row, nomor: i + 1 }));
}

export function validateRollbackSelection(selectedIds) {
  const ids = (selectedIds || []).map((x) => String(x || "").trim()).filter(Boolean);
  if (!ids.length) return "Pilih minimal satu nomor.";
  return "";
}

export function rollbackStageOptions(currentStage) {
  const current = Number(currentStage);
  const last = SELECTIVE_STAGE_MAX >= 0 ? SELECTIVE_STAGE_MAX : STAGES.length - 1;
  const cap = Number.isFinite(current) ? Math.min(current - 1, last) : last;
  const options = [];
  for (let i = 0; i <= cap; i += 1) {
    options.push({ value: i, label: STAGES[i] });
  }
  return options;
}

export function parseRollbackStage(value) {
  if (value === "" || value == null) return null;
  const n = Number(value);
  const last = SELECTIVE_STAGE_MAX >= 0 ? SELECTIVE_STAGE_MAX : STAGES.length - 1;
  if (!Number.isInteger(n) || n < 0 || n > last) return null;
  return n;
}

export function validateRollbackStage(value) {
  if (parseRollbackStage(value) == null) return "Pilih tahap tujuan rollback.";
  return "";
}

export function selectiveRollbackBody(ids, reason, toStage) {
  const body = {
    ids: (ids || []).map((x) => String(x || "").trim()).filter(Boolean),
    reason: String(reason || "").trim(),
  };
  const stage = parseRollbackStage(toStage);
  if (stage != null) body.to_stage = stage;
  return body;
}
