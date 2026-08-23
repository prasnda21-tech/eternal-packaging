// Single source of truth for workflow stages, roles, permissions, and Indonesian labels.

export const STAGES = [
  "Order Masuk",         // 0
  "Design",              // 1
  "Approval Design",     // 2
  "Payment",             // 3
  "Approval Payment",    // 4
  "Penjadwalan",         // 5
  "Screener",            // 6
  "Persiapan Helper",    // 7
  "Operator",            // 8
  "Konfirmasi Sample",   // 9
  "Cetak",               // 10
  "Packing",             // 11
  "Surat Jalan",         // 12
  "Loading",             // 13
  "Terkirim",            // 14  (Driver dihapus sebagai stage produksi)
];

export const ROLES = {
  admin: "Admin",
  head_production: "Head of Production",
  designer: "Designer",
  screener: "Screener",
  helper: "Helper",
  operator: "Operator",
  driver: "Driver",
  crm: "CRM",
};

// Who is allowed to advance from a stage index (matches backend STAGE_EDIT_ROLES)
export const STAGE_EDIT_ROLES = {
  0: ["admin", "head_production"],
  1: ["admin", "designer", "head_production"],
  2: ["admin", "designer", "head_production"],
  3: ["admin", "head_production"],
  4: ["admin", "head_production"],
  5: ["admin", "head_production"],
  6: ["admin", "head_production", "screener"],
  7: ["admin", "head_production", "helper"],
  8: ["admin", "head_production", "operator"],
  9: ["admin", "head_production", "operator", "designer"],
  10: ["admin", "head_production", "operator"],
  11: ["admin", "head_production", "helper"],
  12: ["admin", "head_production"],
  13: ["admin", "head_production", "driver", "helper"],
  14: ["admin", "head_production"],  // Task 13: stage 14 = Terkirim (final). Driver hanya metadata pengantar di stage 13 Loading + mark_delivered — bukan actor advance stage.
};

export const canAdvance = (role, stageIdx) => {
  const allowed = STAGE_EDIT_ROLES[stageIdx] || [];
  return role === "admin" || allowed.includes(role);
};

export const formatDate = (iso, opts = {}) => {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleString("id-ID", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
      ...opts,
    });
  } catch { return iso; }
};

export const formatDeadline = (iso) => {
  if (!iso) return "-";
  const d = new Date(iso);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const tomorrow = new Date(today);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const target = new Date(d);
  target.setHours(0, 0, 0, 0);
  const timePart = d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
  if (target.getTime() === today.getTime()) return `Hari ini · ${timePart}`;
  if (target.getTime() === tomorrow.getTime()) return `Besok · ${timePart}`;
  return d.toLocaleDateString("id-ID", { weekday: "short", day: "2-digit", month: "short" });
};

export const isToday = (iso) => {
  if (!iso) return false;
  const d = new Date(iso);
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
};
