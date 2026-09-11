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

/** All review rows (selection for bulk driver / exclude). Eligibility is enforced separately by Optimalkan Rute. */
export function selectAllReviewRows(rows) {
  return (rows || [])
    .map((r) => String(r?.sales_order_id || "").trim())
    .filter(Boolean);
}

export function excludeFromSelection(selected, ids) {
  const drop = new Set((ids || []).map(String));
  return (selected || []).filter((id) => !drop.has(String(id)));
}

function persistedDriverUserId(row) {
  return String(row?.driver_user_id || "").trim();
}

export function summarizeSelection(rows, selectedIds) {
  const selected = new Set((selectedIds || []).map(String));
  const chosen = (rows || []).filter((r) => selected.has(String(r.sales_order_id)));
  const eligible = chosen.filter((r) => r.eligible);
  const ineligible = chosen.filter((r) => !r.eligible);
  // Driver: N = selected rows that already have a persisted driver assignment.
  // Do NOT derive this from eligible-only route groups — ineligible POs can still have drivers.
  const driversAssigned = chosen.filter((r) => Boolean(persistedDriverUserId(r))).length;
  // Route groups remain eligible-only (Optimalkan Rute semantics unchanged).
  const byDriver = new Map();
  for (const row of eligible) {
    const key = persistedDriverUserId(row);
    if (!key) continue;
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
    drivers: driversAssigned,
    routes_to_create: groups.length,
    groups,
  };
}

export function sourceTypeLabel(sourceType) {
  const key = String(sourceType || "manual").toLowerCase();
  if (key === "delivery_schedule") return "Jadwal Pengiriman";
  return "Manual";
}

export function driverDisplayLabel(row) {
  const name = String(row?.driver_name || "").trim();
  const uid = String(row?.driver_user_id || "").trim();
  if (uid) return name || uid;
  return "Belum Ada Driver";
}

/** Rows that would actually change if assigning driverUserId (exact id). */
export function rowsNeedingDriverChange(rows, selectedIds, driverUserId) {
  const selected = new Set((selectedIds || []).map(String));
  const target = String(driverUserId || "").trim();
  if (!target) return [];
  return (rows || []).filter((r) => {
    if (!selected.has(String(r.sales_order_id))) return false;
    return String(r.driver_user_id || "").trim() !== target;
  });
}

export function buildBulkAssignConfirm(rows, selectedIds, driver) {
  const selected = (selectedIds || []).map(String);
  const needing = rowsNeedingDriverChange(rows, selected, driver?.id);
  return {
    selectedCount: selected.length,
    changeCount: needing.length,
    unchangedCount: Math.max(0, selected.length - needing.length),
    driverName: driver?.name || driver?.id || "",
    driverId: driver?.id || "",
    message:
      `${selected.length} pengiriman dipilih\n` +
      `Driver akan diubah menjadi: ${driver?.name || driver?.id || "—"}\n` +
      (needing.length < selected.length
        ? `(${selected.length - needing.length} sudah memakai driver ini — tidak ditulis ulang)`
        : ""),
  };
}
