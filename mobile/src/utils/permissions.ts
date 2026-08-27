/** Permission helpers — never hardcode access from role label alone. */

export type PermCheck = (perm: string) => boolean;

export const DIVISION_SUBMIT_PERMS: [string, string, string][] = [
  ["screener", "screen.update", "Screener"],
  ["helper", "helper.prepare", "Helper"],
  ["operator", "operator.update", "Operator"],
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
  return ids[0]?.id || null;
}

export function canViewSchedule(has: PermCheck): boolean {
  // Shared Jadwal Produksi table requires schedule.view (backend-authoritative).
  return has("schedule.view");
}
