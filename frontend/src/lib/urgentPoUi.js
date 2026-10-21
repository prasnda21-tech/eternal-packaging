/**
 * Urgent PO Approval — Phase A UI helpers (preview only).
 * No backend mutation. Display classification is conceptual; orders.priority unchanged by this module.
 */

export const URGENT_REASON_MAX = 500;
export const URGENT_NOTES_MAX = 500;
export const URGENT_DECISION_NOTES_MAX = 500;

/** Conceptual display labels — not orders.priority enum values. */
export const URGENT_DISPLAY = {
  NORMAL: "NORMAL",
  PENDING_REQUEST: "PENDING_REQUEST",
  LEGACY_URGENT: "LEGACY_URGENT",
  APPROVED_URGENT: "APPROVED_URGENT",
};

export const REQUEST_STATUS = {
  PENDING: "pending",
  APPROVED: "approved",
  REJECTED: "rejected",
  CANCELLED: "cancelled",
};

/** Preview-only permission keys (not granted by backend in Phase A). */
export const PREVIEW_PERMS = {
  REQUEST: "urgent.request",
  APPROVE: "urgent.approve",
  REVIEW: "urgent.review",
};

const ID_MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];

/**
 * Display-only Indonesian calendar date for Urgent UI: 2026-10-28 → "28 Okt 2026".
 * Does not change stored/payload values. Native date inputs stay YYYY-MM-DD.
 */
export function formatUrgentIdDate(iso) {
  const text = String(iso || "").trim();
  if (!text) return "";
  const m = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) {
    const day = Number(m[3]);
    const monthIdx = Number(m[2]) - 1;
    if (monthIdx < 0 || monthIdx > 11 || day < 1) return "";
    return `${day} ${ID_MONTHS[monthIdx]} ${m[1]}`;
  }
  const d = new Date(text);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getDate()} ${ID_MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

/** Display-only WIB timestamp companion: "21 Okt 2026 11:12". */
export function formatUrgentIdDateTime(iso) {
  const text = String(iso || "").trim();
  if (!text) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return formatUrgentIdDate(text);
  const d = new Date(text);
  if (Number.isNaN(d.getTime())) return "";
  try {
    const parts = Object.fromEntries(
      new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Jakarta",
        day: "numeric",
        month: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).formatToParts(d).map(({ type, value }) => [type, value]),
    );
    const monthIdx = Number(parts.month) - 1;
    if (monthIdx < 0 || monthIdx > 11) return "";
    return `${Number(parts.day)} ${ID_MONTHS[monthIdx]} ${parts.year} ${parts.hour}:${parts.minute}`;
  } catch {
    return "";
  }
}

export function isUrgentPoUiPreviewSearch(search) {
  const q = new URLSearchParams(typeof search === "string" ? search : "");
  return q.get("urgent_ui_preview") === "1" || q.get("urgent_ui_preview") === "approver";
}

export function previewApproverMode(search) {
  const q = new URLSearchParams(typeof search === "string" ? search : "");
  return q.get("urgent_ui_preview") === "approver";
}

/**
 * Classify UI state without mutating priority.
 * Legacy urgent = priority urgent with no approval-workflow request document.
 */
export function classifyUrgentDisplay({ priority, request } = {}) {
  const isUrgent = String(priority || "").toLowerCase() === "urgent";
  const status = request?.status ? String(request.status).toLowerCase() : "";

  if (status === REQUEST_STATUS.PENDING) {
    return URGENT_DISPLAY.PENDING_REQUEST;
  }
  if (isUrgent && status === REQUEST_STATUS.APPROVED) {
    return URGENT_DISPLAY.APPROVED_URGENT;
  }
  if (isUrgent) {
    return URGENT_DISPLAY.LEGACY_URGENT;
  }
  return URGENT_DISPLAY.NORMAL;
}

/** Priority badge label — Pending must never show as Urgent. */
export function priorityBadgeLabel({ priority, request } = {}) {
  const display = classifyUrgentDisplay({ priority, request });
  if (display === URGENT_DISPLAY.APPROVED_URGENT || display === URGENT_DISPLAY.LEGACY_URGENT) {
    return "URGENT";
  }
  return "NORMAL";
}

export function validateUrgentRequestForm({ reason, targetDate, notes } = {}) {
  const errors = {};
  const reasonTrim = String(reason || "").trim();
  const target = String(targetDate || "").trim();
  const notesTrim = String(notes || "").trim();

  if (!reasonTrim) errors.reason = "Alasan urgent wajib diisi.";
  else if (reasonTrim.length > URGENT_REASON_MAX) {
    errors.reason = `Alasan maksimal ${URGENT_REASON_MAX} karakter.`;
  }

  if (!target) errors.targetDate = "Target selesai wajib diisi.";
  else if (!/^\d{4}-\d{2}-\d{2}$/.test(target)) {
    errors.targetDate = "Format tanggal tidak valid.";
  }

  if (notesTrim.length > URGENT_NOTES_MAX) {
    errors.notes = `Catatan maksimal ${URGENT_NOTES_MAX} karakter.`;
  }

  return { ok: Object.keys(errors).length === 0, errors, values: { reason: reasonTrim, targetDate: target, notes: notesTrim } };
}

export function validateUrgentDecisionForm({ decision, decisionNotes } = {}) {
  const errors = {};
  const d = String(decision || "").toLowerCase();
  const notesTrim = String(decisionNotes || "").trim();

  if (d !== "approved" && d !== "rejected") {
    errors.decision = "Pilih Setujui atau Tolak.";
  }
  if (d === "rejected" && !notesTrim) {
    errors.decisionNotes = "Alasan penolakan wajib diisi.";
  }
  if (notesTrim.length > URGENT_DECISION_NOTES_MAX) {
    errors.decisionNotes = `Catatan maksimal ${URGENT_DECISION_NOTES_MAX} karakter.`;
  }

  return {
    ok: Object.keys(errors).length === 0,
    errors,
    values: { decision: d, decisionNotes: notesTrim },
  };
}

export function buildPreviewRequest({
  poNumber = "PO-PREVIEW-1048",
  reason,
  targetDate,
  notes = "",
  requesterName = "CRM Preview",
  requesterEmail = "crm.preview@eternal.local",
  at = null,
} = {}) {
  const now = at || new Date().toISOString();
  return {
    id: `urg-req-preview-${Date.now()}`,
    status: REQUEST_STATUS.PENDING,
    po_number: poNumber,
    reason: String(reason || "").trim(),
    target_completion_date: targetDate,
    notes: String(notes || "").trim(),
    requested_by_name: requesterName,
    requested_by_email: requesterEmail,
    requested_at: now,
    decided_by_name: null,
    decided_by_email: null,
    decided_at: null,
    decision_notes: "",
    history: [
      {
        action: "submitted",
        at: now,
        by_name: requesterName,
        note: reason,
      },
    ],
  };
}

export function applyPreviewDecision(request, { decision, decisionNotes, actorName = "Approver Preview", actorEmail = "approver.preview@eternal.local", at = null } = {}) {
  const now = at || new Date().toISOString();
  const next = {
    ...request,
    status: decision === "approved" ? REQUEST_STATUS.APPROVED : REQUEST_STATUS.REJECTED,
    decided_by_name: actorName,
    decided_by_email: actorEmail,
    decided_at: now,
    decision_notes: String(decisionNotes || "").trim(),
    history: [
      ...(request.history || []),
      {
        action: decision,
        at: now,
        by_name: actorName,
        note: String(decisionNotes || "").trim(),
      },
    ],
  };
  return next;
}

/** Dummy seed scenarios for isolated preview page. */
export function previewScenarioSeed(key) {
  const baseOrder = {
    id: "preview-order-1",
    po_number: "PO-PREVIEW-1048",
    customer: "Kopi Rumah",
    outlet: "Kemang",
    priority: "normal",
    urgency_approved_by: null,
  };

  if (key === "legacy_urgent") {
    return {
      order: {
        ...baseOrder,
        priority: "urgent",
        urgency_approved_by: "production@eternalpackaging.com",
      },
      request: null,
      role: "viewer",
    };
  }

  if (key === "pending") {
    const request = buildPreviewRequest({
      reason: "Customer membutuhkan pesanan untuk pembukaan outlet baru.",
      targetDate: "2026-10-28",
      notes: "Koordinasi dengan Kepala Produksi.",
      at: "2026-10-21T04:12:00.000Z",
    });
    return { order: { ...baseOrder }, request, role: "requester" };
  }

  if (key === "approver") {
    const pending = previewScenarioSeed("pending");
    return { ...pending, role: "approver" };
  }

  if (key === "approved") {
    const pending = previewScenarioSeed("pending").request;
    const request = applyPreviewDecision(pending, {
      decision: "approved",
      decisionNotes: "Disetujui — slot produksi tersedia.",
      at: "2026-10-21T05:00:00.000Z",
    });
    return {
      order: {
        ...baseOrder,
        priority: "urgent",
        urgency_approved_by: request.decided_by_email,
      },
      request,
      role: "viewer",
    };
  }

  if (key === "rejected") {
    const pending = previewScenarioSeed("pending").request;
    const request = applyPreviewDecision(pending, {
      decision: "rejected",
      decisionNotes: "Kapasitas minggu ini penuh. Ajukan ulang minggu depan.",
      at: "2026-10-21T05:05:00.000Z",
    });
    return {
      order: { ...baseOrder, priority: "normal" },
      request,
      role: "viewer",
    };
  }

  return { order: { ...baseOrder }, request: null, role: "requester" };
}
