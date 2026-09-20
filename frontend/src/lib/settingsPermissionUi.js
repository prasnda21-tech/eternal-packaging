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

/** Explicit high-impact keys only — presentational marker, no RBAC effect. */
export const SENSITIVE_PERMISSION_KEYS = new Set([
  "po.delete",
  "po.cancel",
  "sales.delivery.delete",
  "sales.invoice.delete",
  "sales.receipt.delete",
  "delivery_note.delete_file",
  "loading.delete",
  "workflow.rollback",
  "payroll.reopen",
  "general_journal.reverse",
  "opening_balance.reverse",
  "accounting.period.close",
  "accounting.period.reopen",
  "cash_bank.reverse",
  "design.bypass_approval",
  "payment.bypass_approval",
  "super_admin.manage",
  "route_optimizer.manage",
]);

export function isSensitivePermissionKey(key) {
  return SENSITIVE_PERMISSION_KEYS.has(String(key || ""));
}

export function isBusinessPermissionKey(key) {
  return !isSidebarNavPermissionKey(key) && !isLoadingGranularConfiguredKey(key);
}

/** Frontend search + Aktif/Tidak Aktif filter over already-loaded catalog modules. */
export function filterRolePermissionModules(modules, { query = "", stateFilter = "all", draftSet } = {}) {
  const q = String(query || "").trim().toLowerCase();
  const draft = draftSet instanceof Set ? draftSet : new Set(draftSet || []);
  return (modules || [])
    .map(([moduleName, perms]) => {
      let next = perms || [];
      if (q) {
        const catHit = String(moduleName).toLowerCase().includes(q);
        next = catHit
          ? next
          : next.filter(
              (p) =>
                String(p.label || "").toLowerCase().includes(q) ||
                String(p.key || "").toLowerCase().includes(q)
            );
      }
      if (stateFilter === "active") next = next.filter((p) => draft.has(p.key));
      if (stateFilter === "inactive") next = next.filter((p) => !draft.has(p.key));
      return [moduleName, next];
    })
    .filter(([, perms]) => perms.length);
}

export function countRolePermissionTotals(modules, draftSet) {
  const draft = draftSet instanceof Set ? draftSet : new Set(draftSet || []);
  let total = 0;
  let active = 0;
  for (const [, perms] of modules || []) {
    for (const p of perms || []) {
      total += 1;
      if (draft.has(p.key)) active += 1;
    }
  }
  return { active, total };
}

export function countRolePermissionDirty(draftSet, initialSet) {
  const a = new Set([...(draftSet || [])].filter(isBusinessPermissionKey));
  const b = new Set([...(initialSet || [])].filter(isBusinessPermissionKey));
  let n = 0;
  for (const key of a) if (!b.has(key)) n += 1;
  for (const key of b) if (!a.has(key)) n += 1;
  return n;
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
