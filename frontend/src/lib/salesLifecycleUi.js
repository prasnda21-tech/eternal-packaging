/**
 * Sales invoice/receipt one-save + business-cancel UI helpers.
 * Fail closed when feature flags cannot be read from the frontend.
 */

const SAFE_REASON_KEYS = [
  "message",
  "user_message",
  "reason",
  "error_message",
  "detail",
];

export function newIdempotencyKey(prefix = "sales") {
  const base = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  return `${prefix}-${base}`;
}

/**
 * Stable key for one user intent. Reuse while signature matches (retry).
 * Call bumpIdempotencyIntent for a new edit/cancel attempt.
 */
export function ensureIdempotencyIntent(ref, signature, { prefix = "sales" } = {}) {
  const nextSig = String(signature || "");
  if (!ref.current || ref.current.signature !== nextSig || !ref.current.key) {
    ref.current = { signature: nextSig, key: newIdempotencyKey(prefix) };
  }
  return ref.current.key;
}

export function bumpIdempotencyIntent(ref, signature, { prefix = "sales" } = {}) {
  const nextSig = String(signature || "");
  ref.current = { signature: nextSig, key: newIdempotencyKey(prefix) };
  return ref.current.key;
}

export function clearIdempotencyIntent(ref) {
  if (ref) ref.current = { signature: "", key: "" };
}

/** Opt-in cancel only — never treat admin/super-admin as implicit grant. */
export function canBusinessCancelInvoice(hasPerm) {
  return typeof hasPerm === "function" && hasPerm("sales.invoice.cancel") === true;
}

export function canBusinessCancelReceipt(hasPerm) {
  return typeof hasPerm === "function" && hasPerm("sales.receipt.cancel") === true;
}

/** Opt-in receipt edit only — never treat admin/create/delete as implicit grant. */
export function canBusinessEditReceipt(hasPerm) {
  return typeof hasPerm === "function" && hasPerm("sales.receipt.edit") === true;
}

/**
 * Read backend lifecycle_capability only — never guess feature flags on the client.
 * When auto_create_post is available, ordinary Finalisasi/Post/Reverse are not required.
 */
export function legacyManualActionsRequired(capability) {
  if (!capability || typeof capability !== "object") return true;
  if (Object.prototype.hasOwnProperty.call(capability, "legacy_manual_actions_required")) {
    return capability.legacy_manual_actions_required !== false;
  }
  return true;
}

export function lifecycleOpAvailable(capability, op) {
  if (!capability || typeof capability !== "object") return false;
  const slice = capability[op];
  return Boolean(slice && slice.available === true);
}

/** Hide ordinary Finalisasi / Post / Reverse only when backend capability says so. */
export function shouldHideLegacyManualAccounting(capability) {
  return legacyManualActionsRequired(capability) === false;
}

/**
 * Interpret receipt PUT response. Posted revision requires financial_complete;
 * unposted mode treats success when receipt is returned without lifecycle failure.
 */
export function interpretReceiptUpdateResponse(res = {}, { httpErrorBody = null } = {}) {
  const body = httpErrorBody && typeof httpErrorBody === "object" ? httpErrorBody : res;
  const mode = String(body?.mode || "").toLowerCase();
  const hasFinancial = Object.prototype.hasOwnProperty.call(body || {}, "financial_complete");
  if (mode === "posted_revision" || (hasFinancial && mode !== "unposted")) {
    const ok = body?.financial_complete === true;
    return {
      mode: mode || "posted_revision",
      ok,
      financialComplete: ok,
      message: ok
        ? (body?.message || "Penerimaan berhasil dikoreksi")
        : (safeLifecycleReason(body?.message || body) || "Koreksi penerimaan belum selesai secara keuangan"),
      document: body?.receipt || body,
    };
  }
  return {
    mode: mode || "unposted",
    ok: Boolean(body?.receipt || body?.id),
    financialComplete: body?.financial_complete !== false,
    message: body?.message || "Penerimaan berhasil diperbarui",
    document: body?.receipt || body,
  };
}

export function safeLifecycleReason(source) {
  if (source == null) return "";
  if (typeof source === "string") {
    const text = source.trim();
    if (!text) return "";
    if (/journal[_-]?id|idempotency|token|stack|traceback/i.test(text)) return "";
    return text.slice(0, 180);
  }
  if (typeof source !== "object") return "";
  for (const key of SAFE_REASON_KEYS) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) {
      const text = value.trim();
      if (/journal[_-]?id|idempotency|token|stack|traceback/i.test(text)) continue;
      return text.slice(0, 180);
    }
  }
  return "";
}

function statusFromAutoAccounting(raw) {
  const s = String(raw || "").toLowerCase();
  if (!s || s === "none" || s === "n/a") return null;
  if (s === "complete" || s === "completed" || s === "posted" || s === "success") {
    return { tone: "complete", label: "Posted/Complete" };
  }
  if (s === "pending" || s === "in_progress" || s === "processing") {
    return { tone: "pending", label: "Pending" };
  }
  if (s === "failed" || s === "error" || s === "gagal") {
    return { tone: "failed", label: "Gagal" };
  }
  if (s === "needs_review" || s === "review" || s === "inspection") {
    return { tone: "review", label: "Perlu Pemeriksaan" };
  }
  return null;
}

function statusFromCancelProgress(raw) {
  const s = String(raw || "").toLowerCase();
  if (!s || s === "none") return null;
  if (s === "complete" || s === "completed") {
    return { tone: "cancelled", label: "Batal" };
  }
  if (s === "pending" || s === "in_progress") {
    return { tone: "pending", label: "Pending" };
  }
  if (s === "failed" || s === "error") {
    return { tone: "failed", label: "Gagal" };
  }
  return null;
}

/**
 * Compact user-facing lifecycle chip from authoritative document fields only.
 * Does not invent Retry APIs or expose journal IDs.
 */
export function salesDocumentLifecyclePresentation(doc = {}) {
  const cancel = statusFromCancelProgress(doc.business_cancel_progress);
  if (cancel) {
    return {
      ...cancel,
      reason: safeLifecycleReason(doc.business_cancel_message || doc.business_cancel_reason || doc.cancel_reason),
      recover: false,
    };
  }

  const listTab = String(doc.list_tab || "").toLowerCase();
  if (listTab === "cancelled" || String(doc.financial_status || "").toLowerCase() === "cancelled") {
    return { tone: "cancelled", label: "Batal", reason: "", recover: false };
  }

  const auto = statusFromAutoAccounting(doc.auto_accounting_status);
  if (auto && (auto.tone === "pending" || auto.tone === "failed" || auto.tone === "review")) {
    return {
      ...auto,
      reason: safeLifecycleReason(doc.auto_accounting_message || doc.lifecycle_message || doc.message),
      recover: auto.tone === "pending" || auto.tone === "failed",
    };
  }

  if (doc.revision_incomplete === true || doc.edit_revision_incomplete === true) {
    return {
      tone: "pending",
      label: "Pending",
      reason: safeLifecycleReason(doc.revision_message || "Revisi keuangan belum selesai"),
      recover: false,
    };
  }

  if (doc.claim_active === true || doc.period_claim_active === true) {
    return {
      tone: "review",
      label: "Perlu Pemeriksaan",
      reason: safeLifecycleReason(doc.claim_message),
      recover: false,
    };
  }

  const payment = String(doc.payment_status || "").toUpperCase();
  const accounting = String(doc.accounting_status || "").toLowerCase();
  const financialComplete = doc.financial_complete === true;
  const posted = accounting === "posted" || doc.accounting_posted === true;

  if (financialComplete || (posted && (payment === "LUNAS" || listTab === "completed"))) {
    return { tone: "complete", label: "Posted/Complete", reason: "", recover: false };
  }
  if (posted && payment && payment !== "LUNAS") {
    return { tone: "unpaid", label: "Belum Lunas", reason: "", recover: false };
  }
  if (auto) {
    return { ...auto, reason: safeLifecycleReason(doc.auto_accounting_message), recover: false };
  }
  return null;
}

/**
 * Interpret create/update/recover response when auto-lifecycle may be ON.
 * Flag OFF responses lack financial_complete — treat as legacy draft success.
 */
export function interpretAutoLifecycleResponse(res = {}, { httpErrorBody = null } = {}) {
  const body = httpErrorBody && typeof httpErrorBody === "object" ? httpErrorBody : res;
  const hasLifecycleField = Object.prototype.hasOwnProperty.call(body || {}, "financial_complete")
    || Object.prototype.hasOwnProperty.call(body || {}, "auto_accounting_status");
  if (!hasLifecycleField) {
    return {
      mode: "legacy",
      ok: true,
      financialComplete: false,
      status: null,
      message: body?.message || "Berhasil disimpan",
      document: body?.invoice || body?.receipt || body,
    };
  }
  const financialComplete = body.financial_complete === true;
  const status = statusFromAutoAccounting(body.auto_accounting_status)
    || (financialComplete
      ? { tone: "complete", label: "Posted/Complete" }
      : { tone: "pending", label: "Pending" });
  return {
    mode: "auto",
    ok: financialComplete,
    financialComplete,
    status: {
      ...status,
      reason: safeLifecycleReason(body.message || body.error_code),
    },
    message: financialComplete
      ? (body.message || "Berhasil disimpan dan diposting")
      : (safeLifecycleReason(body.message) || "Penyimpanan belum selesai secara keuangan"),
    document: body.invoice || body.receipt || body,
    errorCode: body.error_code || null,
  };
}

const BUSINESS_CANCEL_SUCCESS_OUTCOMES = new Set([
  "cancelled",
  "idempotent_replay",
  "already_cancelled",
  "recovered",
]);

/**
 * Shared invoice/receipt business-cancel response interpreter.
 * HTTP 200 alone is never success — require verified completion evidence.
 * Explicit pending/failed/incomplete fields win over generic cancelled signals.
 */
export function interpretBusinessCancelResponse(res = {}, { httpErrorBody = null, statusCode = 200 } = {}) {
  const body = httpErrorBody && typeof httpErrorBody === "object" ? httpErrorBody : res;
  const document = body?.invoice || body?.receipt || null;

  if (statusCode === 409 || statusCode === 403) {
    return {
      ok: false,
      pending: false,
      failed: true,
      flagOff: statusCode === 409,
      message: safeLifecycleReason(body) || (statusCode === 409
        ? "Pembatalan bisnis belum diaktifkan."
        : "Anda tidak berwenang membatalkan dokumen ini."),
      document,
    };
  }

  const progress = String(
    body?.business_cancel_progress
    || body?.phase
    || document?.business_cancel_progress
    || document?.phase
    || "",
  ).toLowerCase();
  const outcome = String(body?.outcome || "").toLowerCase();
  const listClass = String(
    body?.list_class
    || document?.list_class
    || document?.list_tab
    || "",
  ).toLowerCase();

  const failed = progress === "failed" || progress === "error";
  const pendingProgress = progress === "pending" || progress === "in_progress";
  const explicitlyIncomplete = body?.financial_complete === false
    || body?.business_complete === false
    || document?.financial_complete === false
    || document?.business_complete === false;

  // Incomplete / failed evidence always blocks success — including contradictory cancelled flags.
  if (failed) {
    return {
      ok: false,
      pending: false,
      failed: true,
      flagOff: false,
      message: safeLifecycleReason(body?.message || body) || "Pembatalan gagal",
      document: document || body,
    };
  }
  if (pendingProgress || explicitlyIncomplete) {
    return {
      ok: false,
      pending: true,
      failed: false,
      flagOff: false,
      message: safeLifecycleReason(body?.message || body) || "Pembatalan masih diproses",
      document: document || body,
    };
  }

  const progressComplete = progress === "complete" || progress === "completed";
  const flagComplete = body?.financial_complete === true
    || body?.business_complete === true
    || document?.financial_complete === true
    || document?.business_complete === true;
  const outcomeSuccess = BUSINESS_CANCEL_SUCCESS_OUTCOMES.has(outcome);
  const listCancelled = listClass === "cancelled";

  // Contradictory: success-looking outcome/flags with a non-cancelled list class.
  if (listClass && listClass !== "cancelled" && (outcomeSuccess || flagComplete || progressComplete)) {
    return {
      ok: false,
      pending: false,
      failed: true,
      flagOff: false,
      message: safeLifecycleReason(body?.message || body) || "Respons pembatalan tidak konsisten",
      document: document || body,
    };
  }
  // Contradictory: non-success outcome present alongside cancelled list/progress.
  if (outcome && !outcomeSuccess && (listCancelled || progressComplete || flagComplete)) {
    return {
      ok: false,
      pending: false,
      failed: true,
      flagOff: false,
      message: safeLifecycleReason(body?.message || body) || "Respons pembatalan tidak konsisten",
      document: document || body,
    };
  }

  const complete = progressComplete
    || flagComplete
    || (outcomeSuccess && (!listClass || listCancelled))
    || (listCancelled && outcomeSuccess);

  return {
    ok: complete,
    pending: !complete,
    failed: false,
    flagOff: false,
    message: complete
      ? (body?.message || "Dokumen dibatalkan")
      : (safeLifecycleReason(body?.message || body) || "Pembatalan tidak dapat diverifikasi dari respons server"),
    document: document || body,
  };
}

/** Recovery only when backend fields + create-scoped auth already exist. */
export function canRecoverAutoAccounting(doc, { hasPerm, kind = "invoice" } = {}) {
  const presentation = salesDocumentLifecyclePresentation(doc || {});
  if (!presentation?.recover) return false;
  if (typeof hasPerm !== "function") return false;
  if (kind === "receipt") {
    return hasPerm("sales.receipt.create") === true
      || hasPerm("sales.invoice.create") === true
      || hasPerm("sales_order.create") === true
      || hasPerm("po.edit") === true;
  }
  return hasPerm("sales.invoice.create") === true
    || hasPerm("sales_order.create") === true
    || hasPerm("po.edit") === true;
}

export function lifecycleChipClass(tone) {
  switch (tone) {
    case "complete":
      return "is-complete";
    case "unpaid":
      return "is-unpaid";
    case "pending":
      return "is-pending";
    case "failed":
      return "is-failed";
    case "cancelled":
      return "is-cancelled";
    case "review":
      return "is-review";
    default:
      return "";
  }
}
