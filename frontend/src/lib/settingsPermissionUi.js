/** Sidebar/menu visibility lives in catalog module "Akses Menu" (`nav.*` keys). */
export const SIDEBAR_NAV_MODULE = "Akses Menu";

/** Internal stamp: Role UI has configured Loading action keys (stops loading.update auto-expand). */
export const LOADING_GRANULAR_CONFIGURED = "loading.granular_configured";

export const LOADING_DEFAULTS_FROM_UPDATE = [
  "loading.view",
  "loading.confirm_match",
  "loading.report_discrepancy",
  "loading.view_discrepancy_history",
];

export function isSidebarNavPermissionKey(key) {
  return String(key || "").startsWith("nav.");
}

export function isLoadingGranularConfiguredKey(key) {
  return String(key || "") === LOADING_GRANULAR_CONFIGURED;
}

/** When editing a role that still relies on loading.update compat, show expanded action keys. */
export function expandLoadingDraftForEdit(permissions) {
  const set = new Set(permissions || []);
  const configured = set.has(LOADING_GRANULAR_CONFIGURED);
  set.delete(LOADING_GRANULAR_CONFIGURED);
  if (!configured && set.has("loading.update")) {
    for (const key of LOADING_DEFAULTS_FROM_UPDATE) set.add(key);
  }
  return set;
}

/** Business/functional permission groups only — hide nav/sidebar from generic editors. */
export function businessPermissionModules(catalog, query = "") {
  if (!catalog) return [];
  const q = String(query || "").trim().toLowerCase();
  const entries = Object.entries(catalog.modules || {})
    .filter(([moduleName]) => moduleName !== SIDEBAR_NAV_MODULE)
    .map(([moduleName, perms]) => [
      moduleName,
      (perms || []).filter(
        (p) => !isSidebarNavPermissionKey(p.key) && !isLoadingGranularConfiguredKey(p.key)
      ),
    ])
    .filter(([, perms]) => perms.length);
  if (!q) return entries;
  return entries
    .map(([moduleName, perms]) => [
      moduleName,
      perms.filter((p) => p.label.toLowerCase().includes(q) || p.key.toLowerCase().includes(q)),
    ])
    .filter(([, perms]) => perms.length);
}

/** Keep stored nav.* when saving business permissions from Role & Hak Akses. */
export function roleBusinessSavePayload(draftKeys, storedPermissions) {
  const business = [...(draftKeys || [])].filter(
    (k) => !isSidebarNavPermissionKey(k) && !isLoadingGranularConfiguredKey(k)
  );
  const preservedNav = (storedPermissions || []).filter((k) => isSidebarNavPermissionKey(k) && k !== "nav.configured");
  const payload = [...business, ...preservedNav];
  if (preservedNav.length) payload.push("nav.configured");
  const hasLoadingRelated = business.some((k) => String(k).startsWith("loading."));
  const hadLoadingConfigured = (storedPermissions || []).includes(LOADING_GRANULAR_CONFIGURED);
  if (hasLoadingRelated || hadLoadingConfigured) {
    payload.push(LOADING_GRANULAR_CONFIGURED);
  }
  return payload;
}
