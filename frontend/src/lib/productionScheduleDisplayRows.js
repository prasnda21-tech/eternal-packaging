/** View-model expansion: one spreadsheet display row per distinct operator in a PO.

Presentation only. Canonical PO rows stay 1:1 with the dashboard payload.
Multiple splits for the same operator collapse to one visible row; split/line
identities are retained on the display-row model.
*/

import { isPrintableScheduleSplit } from "./productionScheduleQty";

export function productionScheduleOperatorIdentity(op) {
  const userId = String(op?.operator_user_id || op?.operator_id || "").trim();
  if (userId) return `uid:${userId}`;
  const email = String(op?.operator_email || "").trim().toLowerCase();
  if (email) return `email:${email}`;
  const name = String(op?.operator_name || "").trim().toLowerCase();
  if (name) return `name:${name}`;
  return "";
}

export function productionScheduleAssignmentKey(op, index = 0) {
  if (op?.split_id) return String(op.split_id);
  if (op?.id) return String(op.id);
  const person = productionScheduleOperatorIdentity(op) || "unassigned";
  const line = op?.line_id || "";
  const idx = op?.line_idx == null || op?.line_idx === "" ? index : op.line_idx;
  return `${person}::${line}::${idx}::${index}`;
}

export function productionScheduleDisplayRowKey(orderId, operatorKey) {
  return `${orderId || "po"}::${operatorKey || "unassigned"}`;
}

export function productionScheduleSplitWorkDate(row, op) {
  if (op?.work_date) return op.work_date;
  if (op?.split_id) {
    const hit = (row?.operator_work_dates || []).find((entry) => entry?.split_id === op.split_id);
    if (hit?.work_date) return hit.work_date;
  }
  const name = op?.operator_name;
  if (name) {
    const named = (row?.operator_work_dates || []).filter((entry) => entry?.operator_name === name);
    if (named.length === 1 && named[0]?.work_date) return named[0].work_date;
  }
  return null;
}

export function productionScheduleDisplayWorkDate(row, op) {
  return productionScheduleSplitWorkDate(row, op) || row?.work_date || null;
}

export function groupedOperatorWorkDate(row, splits) {
  const list = Array.isArray(splits) ? splits : [];
  const dates = list.map((op) => productionScheduleSplitWorkDate(row, op));
  const nonempty = dates.filter(Boolean);
  if (list.length && nonempty.length === list.length && nonempty.every((d) => String(d) === String(nonempty[0]))) {
    return nonempty[0];
  }
  return row?.work_date || null;
}

export function groupedOperatorQuantity(splits) {
  let total = 0;
  let any = false;
  for (const op of splits || []) {
    if (op?.quantity == null || op.quantity === "") continue;
    const n = Number(op.quantity);
    if (!Number.isFinite(n)) continue;
    any = true;
    total += n;
  }
  return any ? total : null;
}

function buildGroupedDisplayOperator(splits) {
  const list = Array.isArray(splits) ? splits : [];
  const first = list[0] || {};
  const operator_split_ids = [];
  const line_ids = [];
  const line_indices = [];
  for (const op of list) {
    const sid = op?.split_id || op?.id;
    if (sid != null && sid !== "") operator_split_ids.push(String(sid));
    if (op?.line_id != null && op.line_id !== "") line_ids.push(op.line_id);
    if (op?.line_idx != null && op.line_idx !== "") line_indices.push(op.line_idx);
  }
  const quantity = groupedOperatorQuantity(list);
  const grouped = {
    operator_name: first.operator_name,
    operator_email: first.operator_email,
    operator_user_id: first.operator_user_id || first.operator_id,
    quantity,
    operator_split_ids,
    line_ids,
    line_indices,
    splits: list,
  };
  if (list.length === 1) {
    grouped.split_id = first.split_id || first.id;
    grouped.line_id = first.line_id;
    grouped.line_idx = first.line_idx;
    grouped.line_name = first.line_name;
  }
  return grouped;
}

export function buildProductionScheduleDisplayRows(rows) {
  const out = [];
  for (const row of rows || []) {
    const rawOps = Array.isArray(row.operators) ? row.operators : [];
    const ops = rawOps.filter(isPrintableScheduleSplit);
    if (!ops.length) {
      if (rawOps.length) continue;
      const printTarget = Number(row?.print_qty_total);
      const nonPrint = Number(row?.non_print_qty_total);
      if (printTarget === 0 && nonPrint > 0) continue;
      const operatorKey = "unassigned";
      out.push({
        ...row,
        displayRowKey: productionScheduleDisplayRowKey(row.id, operatorKey),
        displayOperatorKey: operatorKey,
        displayOperator: null,
        displayWorkDate: row.work_date || null,
      });
      continue;
    }
    const groups = new Map();
    ops.forEach((op, index) => {
      const identity = productionScheduleOperatorIdentity(op) || `unknown:${index}`;
      const bucket = groups.get(identity) || [];
      bucket.push(op);
      groups.set(identity, bucket);
    });
    const ranked = [...groups.entries()].map(([operatorKey, splits]) => {
      const displayWorkDate = groupedOperatorWorkDate(row, splits);
      const displayOperator = buildGroupedDisplayOperator(splits);
      const name = String(displayOperator.operator_name || "");
      return {
        operatorKey,
        splits,
        displayOperator,
        displayWorkDate,
        sort: `${displayWorkDate || "9999-12-31"}\0${name}\0${operatorKey}`,
      };
    });
    ranked.sort((a, b) => (a.sort < b.sort ? -1 : a.sort > b.sort ? 1 : 0));
    for (const item of ranked) {
      out.push({
        ...row,
        displayRowKey: productionScheduleDisplayRowKey(row.id, item.operatorKey),
        displayOperatorKey: item.operatorKey,
        displayOperator: item.displayOperator,
        displayWorkDate: item.displayWorkDate,
        work_date: item.displayWorkDate || row.work_date || null,
        operator_work_dates: item.displayWorkDate
          ? [{ split_id: item.operatorKey, operator_name: item.displayOperator.operator_name, work_date: item.displayWorkDate }]
          : [],
      });
    }
  }
  return out;
}
