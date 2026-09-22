/** SPK Antar Divisi — labels, derived progress, and permission-aware tabs.

Overall percent is derived from milestone weights. The UI never treats a
typed percent as the source of truth. Weight rule matches the server:
published milestones must total 100.
*/

export const SPK_LIST_PATH = "/spk";
export const SPK_NEW_PATH = "/spk/baru";

export const SPK_ACCESS_KEYS = [
  "nav.spk",
  "spk.create",
  "spk.view_own",
  "spk.view_division",
  "spk.view_all",
  "spk.assign",
  "spk.update_progress",
  "spk.comment",
  "spk.correct",
  "spk.verify",
  "spk.cancel",
  "spk.manage",
];

export const STATUS_LABELS = {
  DRAFT: "Draft",
  RETURNED: "Dikembalikan",
  PUBLISHED: "Terbit",
  ACKNOWLEDGED: "Diterima",
  IN_PROGRESS: "Berjalan",
  WAITING_VERIFICATION: "Menunggu Verifikasi",
  REVISION_REQUIRED: "Revisi",
  COMPLETED: "Selesai",
  CANCELLED: "Dibatalkan",
};

export const PRIORITY_LABELS = {
  LOW: "Rendah",
  NORMAL: "Normal",
  HIGH: "Tinggi",
  URGENT: "Mendesak",
};

export const DEADLINE_LABELS = {
  ON_TRACK: "On track",
  DUE_SOON: "≤ 3 hari",
  OVERDUE: "Terlambat",
};

export const TYPE_LABELS = {
  QUANTITY: "Kuantitas",
  CHECKLIST: "Checklist",
  APPROVAL: "Approval",
};

export const ACTIVE_STATUS_FILTER = "PUBLISHED,ACKNOWLEDGED,IN_PROGRESS,REVISION_REQUIRED";

export const EVENT_LABELS = {
  CREATE: "Draft dibuat",
  EDIT: "Draft diedit",
  PUBLISH: "SPK diterbitkan",
  REPUBLISH: "SPK diterbitkan ulang",
  PIC_CHANGE: "PIC diubah",
  RETURN: "SPK dikembalikan",
  ACKNOWLEDGE: "SPK diterima",
  START: "SPK dimulai",
  PROGRESS_UPDATE: "Progres diperbarui",
  NOTE: "Catatan ditambahkan",
  EVIDENCE: "Bukti ditambahkan",
  CORRECTION: "Nilai dikoreksi",
  VERIFICATION_REQUEST: "Verifikasi diajukan",
  VERIFY_REVISION: "Revisi diminta",
  VERIFY_APPROVE: "Hasil diterima",
  CANCEL: "SPK dibatalkan",
};

export function widestSpkTab(hasPerm) {
  const ids = visibleSpkTabs(hasPerm).map((tab) => tab.id);
  if (ids.includes("all")) return "all";
  if (ids.includes("division")) return "division";
  return "mine";
}

export function exceedsTarget(milestone) {
  if (!milestone || milestone.type !== "QUANTITY") return false;
  if (milestone.exceeds_target) return true;
  const target = Number(milestone.target_value);
  const actual = Number(milestone.actual_value);
  return Number.isFinite(target) && target > 0 && Number.isFinite(actual) && actual > target;
}

export function newIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `spk-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function reuseIdempotencyKey(slot, payload) {
  const serial = JSON.stringify(payload);
  if (!slot.current || slot.current.serial !== serial) {
    slot.current = { serial, key: newIdempotencyKey() };
  }
  return slot.current.key;
}

export function canSeeSpkNav(hasPerm) {
  if (typeof hasPerm !== "function") return false;
  return SPK_ACCESS_KEYS.some((key) => hasPerm(key));
}

export function visibleSpkTabs(hasPerm) {
  const allow = typeof hasPerm === "function" ? hasPerm : () => false;
  const tabs = [{ id: "mine", label: "SPK Saya" }];
  if (allow("spk.view_division") || allow("spk.view_all") || allow("spk.manage")) {
    tabs.push({ id: "division", label: "Divisi" });
  }
  if (allow("spk.view_all") || allow("spk.manage")) {
    tabs.push({ id: "all", label: "Semua SPK" });
  }
  tabs.push({ id: "overdue", label: "Terlambat" });
  tabs.push({ id: "verification", label: "Menunggu Verifikasi" });
  return tabs;
}

function round2(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.round((n + Number.EPSILON) * 100) / 100;
}

export function milestoneProgress(milestone) {
  if (!milestone) return 0;
  const kind = milestone.type;
  if (kind === "CHECKLIST" || kind === "APPROVAL") {
    return milestone.status === "COMPLETED" ? 100 : 0;
  }
  const target = Number(milestone.target_value);
  const actual = Number(milestone.actual_value);
  if (!Number.isFinite(target) || target <= 0) return 0;
  const safeActual = Number.isFinite(actual) ? Math.max(actual, 0) : 0;
  return round2(Math.min(safeActual / target, 1) * 100);
}

export function weightTotal(milestones) {
  return round2((milestones || []).reduce((sum, row) => sum + (Number(row.weight) || 0), 0));
}

/** Derived overall. Never accepts a manual percent argument. Capped at 100. */
export function overallProgress(milestones) {
  const rows = milestones || [];
  const total = rows.reduce((sum, row) => sum + (Number(row.weight) || 0), 0);
  if (total <= 0) return 0;
  const accrued = rows.reduce((sum, row) => sum + milestoneProgress(row) * (Number(row.weight) || 0), 0);
  return Math.min(100, Math.max(0, round2(accrued / total)));
}

export function previewMilestone(milestone, patch) {
  const next = { ...milestone, ...patch };
  if (next.type === "QUANTITY") {
    const target = Number(next.target_value);
    const actual = Number(next.actual_value);
    if (Number.isFinite(target) && target > 0 && Number.isFinite(actual) && actual >= target) {
      next.status = "COMPLETED";
    } else if (Number.isFinite(actual) && actual > 0) {
      next.status = "IN_PROGRESS";
    } else {
      next.status = "PENDING";
    }
  }
  return next;
}

export function previewOverall(milestones, milestoneId, patch) {
  const before = overallProgress(milestones);
  const next = (milestones || []).map((row) => (row.id === milestoneId ? previewMilestone(row, patch) : row));
  return {
    before,
    after: overallProgress(next),
    milestone: milestoneProgress(next.find((row) => row.id === milestoneId)),
  };
}

export function deadlineState(workflowStatus, deadline, today) {
  if (workflowStatus === "COMPLETED" || workflowStatus === "CANCELLED") return null;
  if (!deadline || !today) return "ON_TRACK";
  const due = Date.parse(`${String(deadline).slice(0, 10)}T00:00:00Z`);
  const now = Date.parse(`${String(today).slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(due) || !Number.isFinite(now)) return "ON_TRACK";
  const days = Math.round((due - now) / 86400000);
  if (days < 0) return "OVERDUE";
  if (days <= 3) return "DUE_SOON";
  return "ON_TRACK";
}

export function rowTone(order) {
  const status = order?.workflow_status;
  if (status === "COMPLETED") return "done";
  if (status === "WAITING_VERIFICATION") return "verify";
  if (status === "REVISION_REQUIRED") return "revision";
  if (status === "DRAFT" || status === "CANCELLED") return "draft";
  if (order?.deadline_state === "OVERDUE") return "overdue";
  if (order?.deadline_state === "DUE_SOON") return "soon";
  return "active";
}

export function formatSpkError(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || item).filter(Boolean).join(", ");
  }
  return "Tidak dapat memproses SPK.";
}

export function formatPercent(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0";
  return String(round2(n)).replace(/\.00$/, "");
}
