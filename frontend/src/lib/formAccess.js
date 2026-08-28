/**
 * Role × form many-to-many over existing RBAC keys.
 * Not a second authorization system — toggling a form adds/removes the
 * existing submit permission (and matching nav.tasks.* when present).
 */

export const FORM_ACCESS_DEFS = [
  { id: "helper", label: "Persiapan Cup", perm: "helper.prepare", nav: "nav.tasks.helper", work: true },
  { id: "packing", label: "Packing", perm: "packing.update", nav: "nav.tasks.packing", work: true },
  { id: "loading", label: "Loading", perm: "loading.update", nav: "nav.tasks.loading", work: true },
  { id: "screener", label: "Screener", perm: "screen.update", nav: "nav.tasks.screener", work: true },
  { id: "operator", label: "Operator", perm: "operator.update", nav: "nav.tasks.operator", work: true },
  { id: "surat_jalan", label: "Surat Jalan", perm: "delivery_note.manage", nav: "nav.tasks.surat_jalan", work: true },
  { id: "terkirim", label: "Driver / Terkirim", perm: "delivery.update", nav: "nav.tasks.delivery", work: true },
  { id: "design_approve", label: "Approve Design", perm: "design.approve", nav: null, work: false },
];

const TASK_PARENT_NAVS = ["nav.tasks", "nav.production"];

export function formDef(id) {
  return FORM_ACCESS_DEFS.find((f) => f.id === id) || null;
}

export function permSet(permissions) {
  return new Set(Array.isArray(permissions) ? permissions : []);
}

export function roleHasForm(permissions, formId) {
  const def = formDef(formId);
  if (!def) return false;
  return permSet(permissions).has(def.perm);
}

export function formsForRole(permissions) {
  return FORM_ACCESS_DEFS.filter((f) => permSet(permissions).has(f.perm)).map((f) => f.id);
}

export function rolesForForm(roles, formId) {
  const def = formDef(formId);
  if (!def) return [];
  return (roles || [])
    .filter((r) => r && r.role !== "admin" && Array.isArray(r.permissions) && r.permissions.includes(def.perm))
    .map((r) => r.role);
}

function anyTaskNav(next) {
  return FORM_ACCESS_DEFS.some((f) => f.nav && next.has(f.nav));
}

export function applyFormToggle(permissions, formId, enabled) {
  const def = formDef(formId);
  if (!def) return [...permSet(permissions)];
  const next = permSet(permissions);
  if (enabled) {
    next.add(def.perm);
    if (def.work) next.add("work_panel.view");
    if (def.id === "design_approve") next.add("design.view");
    if (def.nav) {
      next.add(def.nav);
      TASK_PARENT_NAVS.forEach((k) => next.add(k));
      next.add("nav.configured");
    }
  } else {
    next.delete(def.perm);
    if (def.nav) next.delete(def.nav);
    if (!anyTaskNav(next)) {
      next.delete("nav.tasks");
    }
  }
  return [...next];
}

export function payloadForRoleSave(permissions) {
  const payload = [...permSet(permissions)].filter((k) => k !== "nav.configured");
  if (payload.some((k) => String(k).startsWith("nav."))) payload.push("nav.configured");
  return payload;
}
