/** Visible-row selection for Status PO / Status Pengiriman. Never implies hidden rows. */

export function visibleRowIds(rows) {
  return (rows || []).map((r) => r?.id).filter(Boolean);
}

export function pruneSelection(selected, visibleIds) {
  const vis = new Set(visibleIds || []);
  const next = new Set();
  for (const id of selected || []) {
    if (vis.has(id)) next.add(id);
  }
  return next;
}

export function toggleId(selected, id) {
  const next = new Set(selected || []);
  if (!id) return next;
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

export function selectAllVisible(visibleIds, currentlySelected) {
  const ids = visibleIds || [];
  const selected = currentlySelected || new Set();
  if (!ids.length) return new Set();
  const allOn = ids.every((id) => selected.has(id));
  return allOn ? new Set() : new Set(ids);
}

export function headerCheckState(selected, visibleIds) {
  const ids = visibleIds || [];
  if (!ids.length) return { checked: false, indeterminate: false };
  const n = ids.filter((id) => selected?.has(id)).length;
  return { checked: n === ids.length, indeterminate: n > 0 && n < ids.length };
}

export function removeSucceeded(selected, succeededIds) {
  const drop = new Set(succeededIds || []);
  const next = new Set();
  for (const id of selected || []) {
    if (!drop.has(id)) next.add(id);
  }
  return next;
}
