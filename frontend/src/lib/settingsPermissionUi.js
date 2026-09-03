/** Sidebar/menu visibility lives in catalog module "Akses Menu" (`nav.*` keys). */
export const SIDEBAR_NAV_MODULE = "Akses Menu";

export function isSidebarNavPermissionKey(key) {
  return String(key || "").startsWith("nav.");
}

/** Business/functional permission groups only — hide nav/sidebar from generic editors. */
export function businessPermissionModules(catalog, query = "") {
  if (!catalog) return [];
  const q = String(query || "").trim().toLowerCase();
  const entries = Object.entries(catalog.modules || {})
    .filter(([moduleName]) => moduleName !== SIDEBAR_NAV_MODULE)
    .map(([moduleName, perms]) => [
      moduleName,
      (perms || []).filter((p) => !isSidebarNavPermissionKey(p.key)),
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
  const business = [...(draftKeys || [])].filter((k) => !isSidebarNavPermissionKey(k));
  const preservedNav = (storedPermissions || []).filter((k) => isSidebarNavPermissionKey(k) && k !== "nav.configured");
  const payload = [...business, ...preservedNav];
  if (preservedNav.length) payload.push("nav.configured");
  return payload;
}
