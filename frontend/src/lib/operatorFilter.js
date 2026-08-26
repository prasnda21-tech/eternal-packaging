export function operatorKey(o) {
  return String(o?.operator_user_id || o?.id || o?.email || "").trim();
}

export function allOperatorIds(operators) {
  const seen = new Set();
  const out = [];
  for (const o of operators || []) {
    const id = operatorKey(o);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

/** Search only filters the visible list. Select All still uses allOperatorIds. */
export function searchOperators(operators, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return operators || [];
  return (operators || []).filter((o) => {
    const name = String(o?.name || o?.operator_name || "").toLowerCase();
    const email = String(o?.email || o?.operator_email || "").toLowerCase();
    const id = operatorKey(o).toLowerCase();
    return name.includes(q) || email.includes(q) || id.includes(q);
  });
}

export function selectAllState(selectedIds, allIds) {
  const all = (allIds || []).filter(Boolean);
  const selected = (selectedIds || []).filter(Boolean);
  if (!all.length || !selected.length) return "none";
  const set = new Set(selected);
  const selectedCount = all.filter((id) => set.has(id)).length;
  if (selectedCount === 0) return "none";
  if (selectedCount === all.length) return "all";
  return "partial";
}

/** One click: empty/partial → all ids in the full dataset. All selected → clear. */
export function nextSelectAll(selectedIds, allIds) {
  const all = (allIds || []).filter(Boolean);
  if (!all.length) return [];
  const state = selectAllState(selectedIds, all);
  if (state === "all") return [];
  return [...all];
}

export function toggleOperatorId(selectedIds, id) {
  const val = String(id || "").trim();
  if (!val) return selectedIds || [];
  const prev = selectedIds || [];
  return prev.includes(val) ? prev.filter((x) => x !== val) : [...prev, val];
}

export function filterOperatorSummary(selectedIds, operators) {
  const allIds = allOperatorIds(operators);
  const selected = (selectedIds || []).filter(Boolean);
  if (!selected.length) return "Semua Operator";
  const state = selectAllState(selected, allIds);
  if (state === "all") return "Semua Operator";
  if (selected.length === 1) {
    const op = (operators || []).find((o) => operatorKey(o) === selected[0]);
    return op?.name || op?.operator_name || op?.email || selected[0];
  }
  return `${selected.length} Operator dipilih`;
}

export function exportOperatorIds(selectedIds, operators) {
  const selected = (selectedIds || []).filter(Boolean);
  if (!selected.length) return undefined;
  const allIds = allOperatorIds(operators);
  if (selectAllState(selected, allIds) === "all") return undefined;
  return selected.join(",");
}
