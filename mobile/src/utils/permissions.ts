/** Permission helpers — never hardcode access from role label alone. */

export type PermCheck = (perm: string) => boolean;

export const DIVISION_SUBMIT_PERMS: [string, string, string][] = [
  ["screener", "screen.update", "Screener"],
  ["helper", "helper.prepare", "Persiapan Cup"],
  ["packing", "packing.update", "Packing"],
  ["operator", "operator.update", "Operator"],
  ["surat_jalan", "delivery_note.manage", "Surat Jalan"],
  ["loading", "loading.update", "Loading"],
  ["terkirim", "delivery.update", "Driver / Terkirim"],
];

export function hasPerm(permissions: string[] | undefined | null, perm: string): boolean {
  return Array.isArray(permissions) && permissions.includes(perm);
}

export function makeHasPerm(permissions: string[] | undefined | null): PermCheck {
  const set = new Set(permissions || []);
  return (perm: string) => set.has(perm);
}

export function permittedWorkDivisions(has: PermCheck): { id: string; perm: string; label: string }[] {
  return DIVISION_SUBMIT_PERMS.filter(([, perm]) => has(perm)).map(([id, perm, label]) => ({
    id,
    perm,
    label,
  }));
}

export function defaultWorkDivision(has: PermCheck, preferred?: string | null): string | null {
  const ids = permittedWorkDivisions(has);
  if (preferred && ids.some((d) => d.id === preferred)) return preferred;
  if (preferred === "design_approve" && has("design.approve")) return "design_approve";
  return ids[0]?.id || (has("design.approve") ? "design_approve" : null);
}

export function canViewSchedule(has: PermCheck): boolean {
  return has("schedule.view");
}

export function canApproveDesign(has: PermCheck): boolean {
  return has("design.approve");
}
