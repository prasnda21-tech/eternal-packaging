/** Section helpers for Pengaturan Sales hub (`/settings/pengaturan-sales`). */

export const SALES_SETTINGS_TAB = "pengaturan-sales";

export const SALES_SETTINGS_SECTIONS = [
  { id: "semua", label: "Semua Sales", testid: "ps-nav-semua", domain: "master" },
  { id: "verifikasi", label: "Perlu Verifikasi", testid: "ps-nav-verifikasi", domain: "master" },
  { id: "riwayat", label: "Riwayat", testid: "ps-nav-riwayat", domain: "hub" },
];

export const SALES_SETTINGS_SECTION_IDS = SALES_SETTINGS_SECTIONS.map((s) => s.id);

/** Audit actions that are actually written today — do not invent others. */
export const SALES_SETTINGS_AUDIT_ACTIONS = Object.freeze([
  "settings.sales_master.create",
  "settings.sales_master.update",
  "settings.sales_master_alias.bind",
  "settings.sales_label_candidate.approve_new",
  "settings.sales_label_candidate.link",
  "sales_user_mapping.update",
]);

export function sectionAllowed(sectionId, { canViewMaster, canViewMapping }) {
  const meta = SALES_SETTINGS_SECTIONS.find((item) => item.id === sectionId);
  if (!meta) return false;
  if (meta.domain === "master") return Boolean(canViewMaster);
  // Riwayat: hub-visible users may open the tab; content gated separately.
  return Boolean(canViewMaster || canViewMapping);
}

export function firstAllowedSalesSettingsSection({ canViewMaster, canViewMapping }) {
  for (const item of SALES_SETTINGS_SECTIONS) {
    if (sectionAllowed(item.id, { canViewMaster, canViewMapping })) return item.id;
  }
  return "riwayat";
}

/** Map legacy section ids (master/mapping/akun-cabang) onto the unified tabs. */
export function normalizeSalesSettingsSection(raw) {
  const requested = String(raw || "").trim().toLowerCase();
  if (!requested) return "";
  if (requested === "master" || requested === "akun-cabang" || requested === "mapping") {
    return "semua";
  }
  return requested;
}

export function resolveSalesSettingsSection(raw, access) {
  const requested = normalizeSalesSettingsSection(raw);
  if (requested && sectionAllowed(requested, access)) return requested;
  return firstAllowedSalesSettingsSection(access);
}

export function filterSalesSettingsAuditLogs(logs) {
  const allow = new Set(SALES_SETTINGS_AUDIT_ACTIONS);
  return (Array.isArray(logs) ? logs : []).filter((row) => allow.has(String(row?.action || "")));
}

export function salesSettingsLegacyAlias(child) {
  // Legacy URLs land on Semua Sales; Mapping is no longer a separate hub tab.
  if (child === "sales-master") return { tab: SALES_SETTINGS_TAB, section: "semua" };
  if (child === "sales-user-mapping") return { tab: SALES_SETTINGS_TAB, section: "semua" };
  return null;
}
