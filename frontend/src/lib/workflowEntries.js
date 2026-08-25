/** Append-only confirmed workflow entries (display + idempotency helpers). */

export function newActionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function validEntries(entries) {
  return (Array.isArray(entries) ? entries : []).filter((e) => e && !e.cancelled && !e.replaced);
}

export function sumEntryQty(entries) {
  return validEntries(entries).reduce((s, e) => s + Number(e.qty || 0), 0);
}

/** Show stored entries, or a single locked legacy snapshot so history is visible. */
export function visibleEntries(stored, snapshotQty, snapshotAt, snapshotName) {
  const list = validEntries(stored);
  if (list.length) return list;
  const qty = Number(snapshotQty || 0);
  if (qty > 0) {
    return [{
      id: "legacy",
      qty,
      created_at: snapshotAt || null,
      created_by_name: snapshotName || "",
      locked: true,
      legacy: true,
    }];
  }
  return [];
}

export function findEntryByActionId(entries, actionId) {
  if (!actionId) return null;
  return validEntries(entries).find((e) => e.action_id && e.action_id === actionId) || null;
}

export function entriesForLine(entries, lineIdx) {
  return validEntries(entries).filter((e) => Number(e.line_idx) === Number(lineIdx));
}
