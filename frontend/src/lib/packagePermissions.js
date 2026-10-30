/** Production package keys (Pembelian / Persediaan / HPP) — mirror backend PACKAGE_PERMISSIONS. */

export const PACKAGE_PERMISSIONS = Object.freeze([
  "purchase_requests.view",
  "purchase_requests.create",
  "purchase_orders.view",
  "purchase_orders.create",
  "purchase_orders.edit_branch",
  "purchase_invoices.view",
  "purchase_invoices.create",
  "purchase_invoices.edit",
  "purchase_payments.view",
  "purchase_payments.create",
  "purchase_receipts.view",
  "purchase_receipts.sync",
  "purchase_receipts.export",
  "purchase_receipts.receive",
  "purchase_hpp.view",
  "master_items.view",
  "master_items.manage",
  "org.branches.view",
  "org.branches.manage",
  "org.warehouses.view",
  "org.warehouses.manage",
  "current_stock.view",
  "current_stock.update",
  "current_stock.import",
  "current_stock.export",
  "inventory_valuation.view",
  "inventory_valuation.export",
  "inventory_valuation.manage_opening",
  "nav.purchasing",
  "nav.purchasing.requests",
  "nav.purchasing.orders",
  "nav.purchasing.advances",
  "nav.purchasing.invoices",
  "nav.purchasing.payments",
  "nav.purchasing.receipts",
  "nav.purchasing.returns",
  "nav.purchasing.hpp",
  "nav.inventory",
  "nav.inventory.master_items",
  "nav.inventory.branches_warehouses",
  "nav.inventory.current_stock",
  "nav.inventory.inventory_value",
]);

export const PACKAGE_PERMISSION_SET = new Set(PACKAGE_PERMISSIONS);

export const DIRECTOR_FULL_ACCESS_ROLE = "direktur_utama";
export const HOLDING_TEAM_ROLE = "holding_team";

export function isDirectorFullAccessRole(role) {
  return String(role || "").trim().toLowerCase() === DIRECTOR_FULL_ACCESS_ROLE;
}

export function isHoldingTeamRole(role) {
  return String(role || "").trim().toLowerCase() === HOLDING_TEAM_ROLE;
}

/** Roles whose stored package grants are already effective, without a separate approval. */
export function roleMayReceivePackageAccess(role) {
  return isHoldingTeamRole(role) || isDirectorFullAccessRole(role);
}

export function isPackagePermissionKey(key) {
  return PACKAGE_PERMISSION_SET.has(String(key || ""));
}

export function normalizePackageConfirmedKeys(raw) {
  const out = new Set();
  if (!Array.isArray(raw)) return out;
  for (const item of raw) {
    const key = String(item || "").trim();
    if (PACKAGE_PERMISSION_SET.has(key)) out.add(key);
  }
  return out;
}

/** Package keys present in the next grant set (role perms or override-true). */
export function packageKeysInGrant(grantKeys) {
  const out = [];
  for (const key of grantKeys || []) {
    if (PACKAGE_PERMISSION_SET.has(key)) out.push(key);
  }
  return out.sort();
}

/**
 * Keys that would become production-active if confirm_package_keys is sent
 * as `reviewedKeys` for the next grant set.
 */
export function packageKeysToActivate(grantKeys, reviewedKeys) {
  const grant = new Set(packageKeysInGrant(grantKeys));
  const reviewed = normalizePackageConfirmedKeys(reviewedKeys);
  return [...reviewed].filter((k) => grant.has(k)).sort();
}

/**
 * Keep only reviewed package keys that are still granted.
 * Unchecked grants drop out; newly granted keys are NOT added.
 */
export function pruneConfirmReviewKeys(reviewKeys, grantKeys) {
  return packageKeysToActivate(grantKeys, reviewKeys);
}

/**
 * Payload confirm list must equal the UI activation preview:
 * intersection of current grant and explicitly reviewed keys.
 */
export function confirmPackageKeysForSave(grantKeys, reviewKeys, { optIn = false } = {}) {
  if (!optIn) return undefined;
  return packageKeysToActivate(grantKeys, reviewKeys);
}

/** Package keys in draft that are not yet production-confirmed. */
export function unconfirmedPackageKeys(grantKeys, confirmedKeys) {
  const confirmed = normalizePackageConfirmedKeys(confirmedKeys);
  return packageKeysInGrant(grantKeys).filter((k) => !confirmed.has(k));
}

/**
 * Build PUT payload. Omitting confirmPackageKeys keeps legacy shrink-only behavior.
 * Passing an array (even empty) sends explicit confirmation of that reviewed set.
 */
export function rolePermissionsPutBody(permissions, confirmPackageKeys, expectedPermissions) {
  const body = { permissions: [...(permissions || [])] };
  if (confirmPackageKeys !== undefined && confirmPackageKeys !== null) {
    body.confirm_package_keys = [...confirmPackageKeys];
  }
  if (expectedPermissions !== undefined && expectedPermissions !== null) {
    body.expected_permissions = [...expectedPermissions];
  }
  return body;
}

export function userOverridePutBody(permissionsOverride, confirmPackageKeys) {
  const body = { permissions_override: { ...(permissionsOverride || {}) } };
  if (confirmPackageKeys !== undefined && confirmPackageKeys !== null) {
    body.confirm_package_keys = [...confirmPackageKeys];
  }
  return body;
}

/** Override-True package keys only (user stamp tracks these). */
export function packageKeysFromOverride(override) {
  const out = [];
  for (const [key, val] of Object.entries(override || {})) {
    if (val === true && PACKAGE_PERMISSION_SET.has(key)) out.push(key);
  }
  return out.sort();
}
