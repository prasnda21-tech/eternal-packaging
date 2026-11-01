/**
 * Compact helpers for Flexible Document Relation UI (Stage 4).
 * Pure presentational — does not invent timestamps or mutate domain state.
 */

export function asInt(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

export function normalizeSourceMode(doc) {
  const raw = String(doc?.source_mode || "").trim().toLowerCase();
  if (raw === "po" || raw === "sj") return raw;
  if (Array.isArray(doc?.source_sj_ids) && doc.source_sj_ids.length) return "sj";
  return "po";
}

export function isMultiSourceDoc(doc) {
  if (doc?.is_multi_source) return true;
  const poCount = Array.isArray(doc?.source_po_ids) ? doc.source_po_ids.length : 0;
  const sjCount = Array.isArray(doc?.source_sj_ids) ? doc.source_sj_ids.length : 0;
  return poCount > 1 || sjCount > 1;
}

export function financialStatusLabel(status) {
  const s = String(status || "draft").toLowerCase();
  if (s === "finalized") return "Finalized";
  if (s === "cancelled" || s === "canceled") return "Dibatalkan";
  return "Draft";
}

export function paymentStatusLabel(status) {
  const s = String(status || "BELUM DIBAYAR").trim().toUpperCase();
  if (s === "LUNAS") return "Lunas";
  if (s === "SEBAGIAN" || s === "PARTIAL") return "Sebagian";
  return "Belum dibayar";
}

/** Loading/POD labels — never treat loading complete as POD complete. */
export function sliceLoadingLabel(slice) {
  if (!slice) return "Belum tercatat";
  if (slice.loading_complete) return "Loading selesai";
  if (asInt(slice.loading_qty) > 0) return "Loading sebagian";
  return "Belum loading";
}

export function slicePodLabel(slice) {
  if (!slice) return "Belum tercatat";
  if (slice.delivered || slice.pod_complete) return "POD selesai";
  if (asInt(slice.delivered_qty) > 0) return "POD sebagian";
  return "Belum POD";
}

export function sjAggregateFulfillmentLabel(sliceInfo) {
  if (!sliceInfo) return "Belum tercatat";
  if (sliceInfo.complete) return "Lengkap";
  if (sliceInfo.loading_complete && !sliceInfo.pod_complete) return "Loading selesai · POD belum";
  if (asInt(sliceInfo.loading_qty) > 0 || (sliceInfo.sources || []).some((s) => asInt(s.loading_qty) > 0)) {
    return "Sebagian";
  }
  return "Belum loading";
}

export function isPartialFailedOneshot(oneshot, delivery) {
  if (!oneshot && !delivery) return false;
  if (oneshot?.partial_failed) return true;
  const recovery = String(oneshot?.recovery_status || "");
  if (recovery === "partial" || recovery === "recovery_required") return true;
  const status = String(oneshot?.fulfillment_last_status || delivery?.fulfillment_last_status || "");
  if (status === "partial_failed" || status === "partial" || status === "recovery_required") return true;
  if (oneshot?.fulfillment_commit_pending || delivery?.fulfillment_commit_pending) return true;
  if (oneshot?.fulfillment_recovery_fence) return true;
  return false;
}

/** Stage 5B recovery UI gates from oneshot_fulfillment / recovery payload. */
export function fulfillmentRecoveryUi(oneshot, delivery) {
  const src = oneshot || {};
  const status = String(
    src.recovery_status
    || src.status
    || src.fulfillment_last_status
    || delivery?.fulfillment_last_status
    || "",
  ).trim();
  const manual = Boolean(src.fulfillment_manual_review_required) || status === "manual_review_required";
  const inProgress = Boolean(src.fulfillment_commit_pending || src.fulfillment_recovery_fence)
    || status === "in_progress";
  const complete = status === "complete" || (src.slice_fulfillment?.complete === true && !inProgress && !manual);
  const resumeLoading = Boolean(src.resume_loading_available);
  const resumePod = Boolean(src.resume_pod_available);
  const resumeAvailable = resumeLoading || resumePod;
  return {
    status: status || (manual ? "manual_review_required" : ""),
    manual,
    inProgress,
    complete,
    resumeAvailable,
    resumeLoading,
    resumePod,
    resumeKind: resumePod ? "pod" : resumeLoading ? "loading" : "",
    completeSources: Array.isArray(src.complete_sources) ? src.complete_sources : [],
    pendingSources: Array.isArray(src.pending_sources) ? src.pending_sources : [],
    ambiguousSources: Array.isArray(src.ambiguous_sources) ? src.ambiguous_sources : [],
    reason: String(src.reason || "").trim(),
  };
}

export function partialFailedSources(sliceInfo) {
  const sources = Array.isArray(sliceInfo?.sources) ? sliceInfo.sources : [];
  const ok = [];
  const pending = [];
  for (const s of sources) {
    const label = s.po_number || s.source_po_id || "PO";
    if (s.loading_complete && s.delivered) ok.push({ ...s, label, state: "lengkap" });
    else if (s.loading_complete && !s.delivered) pending.push({ ...s, label, state: "menunggu_pod" });
    else if (!s.has_mirror) pending.push({ ...s, label, state: "mirror_hilang" });
    else pending.push({ ...s, label, state: "menunggu_loading" });
  }
  return { ok, pending };
}

export function sortRelationEvents(events) {
  const list = Array.isArray(events) ? [...events] : [];
  list.sort((a, b) => String(a?.at || "").localeCompare(String(b?.at || "")));
  return list;
}

export function formatRelationEventLabel(ev) {
  if (!ev) return "Belum tercatat";
  return String(ev.label || ev.kind || "Kejadian").trim() || "Belum tercatat";
}

export function poQtySummary(relations) {
  const q = relations?.qty_summary || {};
  return {
    ordered: asInt(q.ordered),
    shipped: asInt(q.shipped),
    loaded: asInt(q.loaded),
    delivered: asInt(q.delivered),
    invoiced: asInt(q.invoiced),
    remaining_delivery: asInt(q.remaining_delivery),
    remaining_invoiceable: asInt(q.remaining_invoiceable),
  };
}

export function sharedProofSummary(oneshotOrDelivery) {
  const proof =
    oneshotOrDelivery?.shared_pod_proof ||
    oneshotOrDelivery?.delivery?.shared_pod_proof ||
    null;
  if (!proof || typeof proof !== "object") return null;
  return {
    reference: String(proof.reference || proof.id || "").trim() || "Belum tercatat",
    recipient_name: String(proof.recipient_name || "").trim() || "Belum tercatat",
    photo_count: asInt(proof.photo_count),
    has_signature: Boolean(proof.has_signature),
  };
}
