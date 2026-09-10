/** Helpers for Delivery Schedule → Route Optimizer bridge (PWA). */

export function parseReviewOrderIds(search) {
  const raw = String(search || "");
  const params = new URLSearchParams(raw.startsWith("?") ? raw : `?${raw}`);
  const ids = String(params.get("ids") || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const seen = new Set();
  const out = [];
  for (const id of ids) {
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

export function toggleId(selected, id) {
  const set = new Set(selected || []);
  const key = String(id);
  if (set.has(key)) set.delete(key);
  else set.add(key);
  return Array.from(set);
}

export function selectAllEligible(rows) {
  return (rows || []).filter((r) => r.eligible).map((r) => String(r.sales_order_id));
}

export function excludeFromSelection(selected, ids) {
  const drop = new Set((ids || []).map(String));
  return (selected || []).filter((id) => !drop.has(String(id)));
}

export function summarizeSelection(rows, selectedIds) {
  const selected = new Set((selectedIds || []).map(String));
  const chosen = (rows || []).filter((r) => selected.has(String(r.sales_order_id)));
  const eligible = chosen.filter((r) => r.eligible);
  const ineligible = chosen.filter((r) => !r.eligible);
  const byDriver = new Map();
  for (const row of eligible) {
    const key = String(row.driver_user_id || "");
    if (!byDriver.has(key)) {
      byDriver.set(key, {
        driver_user_id: key,
        driver_name: row.driver_name || key,
        rows: [],
      });
    }
    byDriver.get(key).rows.push(row);
  }
  const groups = Array.from(byDriver.values());
  return {
    selected: chosen.length,
    eligible: eligible.length,
    ineligible: ineligible.length,
    drivers: groups.length,
    routes_to_create: groups.length,
    groups,
  };
}

export function sourceTypeLabel(sourceType) {
  const key = String(sourceType || "manual").toLowerCase();
  if (key === "delivery_schedule") return "Jadwal Pengiriman";
  return "Manual";
}
