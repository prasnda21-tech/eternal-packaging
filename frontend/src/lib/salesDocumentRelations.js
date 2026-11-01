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

/** Indonesian thousand separators + pcs unit for relation KPIs. */
export function formatPcs(value) {
  return `${asInt(value).toLocaleString("id-ID")} pcs`;
}

/**
 * Map backend SJ document/allocation status for relation UI.
 * "SELESAI" means qty fully allocated onto an issued SJ — NOT physically delivered/POD.
 * Does not mutate stored status values.
 */
export function sjDocumentAllocationStatusLabel(sjOrStatus) {
  if (sjOrStatus && typeof sjOrStatus === "object" && sjOrStatus.is_cancelled) {
    return "Dibatalkan";
  }
  const raw = String(
    typeof sjOrStatus === "string" || sjOrStatus == null
      ? sjOrStatus
      : (sjOrStatus.status || ""),
  )
    .trim()
    .toUpperCase();
  if (!raw || raw === "ACTIVE" || raw === "AKTIF") return "SJ dibuat";
  if (raw === "CANCELLED" || raw === "CANCELED" || raw === "DIBATALKAN") return "Dibatalkan";
  if (raw === "SELESAI" || raw === "COMPLETE" || raw === "COMPLETED") return "Alokasi lengkap";
  if (raw === "SEBAGIAN" || raw === "PARTIAL") return "Alokasi sebagian";
  if (raw === "DRAFT") return "Draft SJ";
  if (raw === "PENDING") return "Pending";
  return "SJ dibuat";
}

/** List/detail badge for sales delivery document status (allocation semantics). */
export function salesDeliveryDocumentStatusLabel(status, { cancelled = false } = {}) {
  if (cancelled) return "Dibatalkan";
  return sjDocumentAllocationStatusLabel(status);
}

export const PO_RELATION_QTY_KPI = [
  { key: "ordered", label: "Dipesan" },
  { key: "shipped", label: "Masuk SJ" },
  { key: "loaded", label: "Loading" },
  { key: "delivered", label: "Diterima" },
  { key: "invoiced", label: "Difakturkan" },
  { key: "remaining_delivery", label: "Belum Masuk SJ" },
  { key: "remaining_invoiceable", label: "Sisa Faktur" },
];

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

/** Canonical SJ qty from delivery allocations — never use primary PO ordered qty. */
export function sjTotalQty(delivery) {
  const direct = asInt(delivery?.total_qty);
  if (direct > 0) return direct;
  const lines = Array.isArray(delivery?.line_items) ? delivery.line_items : [];
  if (lines.length) {
    return lines.reduce((acc, li) => acc + asInt(li.qty ?? li.quantity ?? li.allocated_qty), 0);
  }
  const allocs = Array.isArray(delivery?.source_allocations) ? delivery.source_allocations : [];
  return allocs.reduce((acc, a) => acc + asInt(a.allocated_qty), 0);
}

export function sjSourcePoCount(delivery) {
  const ids = Array.isArray(delivery?.source_po_ids) ? delivery.source_po_ids : [];
  if (ids.length) return ids.length;
  if (delivery?.po_id) return 1;
  return 0;
}

/**
 * Document / fulfillment status for SJ detail KPI.
 * Never treat "SJ exists" as delivered/POD complete.
 */
export function sjFulfillmentStatusLabels(delivery, sliceInfo, oneshot) {
  const slice = sliceInfo || oneshot?.slice_fulfillment || null;
  const recovery = fulfillmentRecoveryUi(oneshot, delivery);

  let loading = "Belum loading";
  if (slice?.loading_complete) loading = "Loading selesai";
  else if (asInt(slice?.loading_qty) > 0 || (slice?.sources || []).some((s) => asInt(s.loading_qty) > 0)) {
    loading = "Loading sebagian";
  }

  let pod = "Belum POD";
  if (slice?.pod_complete || slice?.complete) pod = "POD selesai";
  else if (
    asInt(slice?.delivered_qty) > 0
    || (slice?.sources || []).some((s) => asInt(s.delivered_qty) > 0 || s.delivered)
  ) {
    pod = "POD sebagian";
  }

  let documentLabel = "SJ dibuat";
  if (recovery.manual) documentLabel = "Perlu pemeriksaan";
  else if (
    recovery.inProgress
    || recovery.status === "partial_failed"
    || recovery.status === "partial"
    || recovery.status === "recovery_required"
  ) {
    documentLabel = "Fulfillment pending";
  } else if (slice?.complete || pod === "POD selesai") documentLabel = "Penerimaan selesai";
  else if (pod === "POD sebagian") documentLabel = "Penerimaan parsial";
  else if (loading === "Loading selesai") documentLabel = "Loading selesai";
  else if (loading === "Loading sebagian") documentLabel = "Loading sebagian";

  const deliveredQty = Array.isArray(slice?.sources)
    ? slice.sources.reduce((acc, s) => acc + asInt(s.delivered_qty), 0)
    : asInt(slice?.delivered_qty);

  return {
    documentLabel,
    loadingLabel: loading,
    podLabel: pod,
    aggregateLabel: sjAggregateFulfillmentLabel(slice),
    deliveredQty,
    totalQty: sjTotalQty(delivery),
    sourcePoCount: sjSourcePoCount(delivery),
  };
}

/**
 * Compact per-source rows for SJ detail.
 * One row per source allocation/line — never merge by master_item_id.
 */
export function buildSjSourceAllocationRows({ delivery, sliceInfo, sourcePoSummaries } = {}) {
  const summaries = Array.isArray(sourcePoSummaries) ? sourcePoSummaries : [];
  const sliceByPo = new Map(
    (Array.isArray(sliceInfo?.sources) ? sliceInfo.sources : []).map((s) => [String(s.source_po_id), s]),
  );
  const lines = Array.isArray(delivery?.line_items) ? delivery.line_items : [];
  const allocs = Array.isArray(delivery?.source_allocations) ? delivery.source_allocations : [];
  const base = lines.length
    ? lines.map((li, idx) => ({
        key: String(li.line_id || `${li.source_po_id || "po"}-${idx}`),
        source_po_id: li.source_po_id || delivery?.po_id,
        source_po_number: li.source_po_number,
        product: li.name || "—",
        allocated_qty: asInt(li.qty ?? li.quantity ?? li.allocated_qty),
      }))
    : allocs.map((a, idx) => ({
        key: String(a.source_line_id || `${a.source_po_id}-${idx}`),
        source_po_id: a.source_po_id || delivery?.po_id,
        source_po_number: a.source_po_number,
        product: a.name || "—",
        allocated_qty: asInt(a.allocated_qty),
      }));

  // Legacy single-PO without source_allocations: one synthetic row from delivery.
  const rowsIn = base.length
    ? base
    : delivery?.po_id
      ? [{
          key: String(delivery.po_id),
          source_po_id: delivery.po_id,
          source_po_number: delivery.po_number,
          product: (lines[0] && lines[0].name) || "—",
          allocated_qty: sjTotalQty(delivery),
        }]
      : [];

  return rowsIn.map((row) => {
    const poId = String(row.source_po_id || "");
    const slice = sliceByPo.get(poId) || null;
    const meta = summaries.find((s) => String(s.po_id) === poId);
    const allocated = asInt(row.allocated_qty);
    const loadingQty = asInt(slice?.loading_qty);
    const deliveredQty = asInt(slice?.delivered_qty);
    const remain = Math.max(0, allocated - deliveredQty);
    let status = "SJ dibuat";
    if (slice?.delivered || (slice?.loading_complete && deliveredQty >= allocated && allocated > 0)) {
      status = "Diterima";
    } else if (deliveredQty > 0) status = "Penerimaan parsial";
    else if (slice?.loading_complete) status = "Loading selesai";
    else if (loadingQty > 0) status = "Loading sebagian";
    return {
      ...row,
      source_po_number: row.source_po_number || meta?.po_number || poId || "—",
      loading_qty: loadingQty,
      delivered_qty: deliveredQty,
      remaining_undelivered: remain,
      status,
      loading_complete: Boolean(slice?.loading_complete),
      delivered: Boolean(slice?.delivered),
    };
  });
}
