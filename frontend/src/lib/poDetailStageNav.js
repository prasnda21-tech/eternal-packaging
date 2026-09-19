import { STAGES } from "./constants";

/** Semantic Detail PO section ids — navigation only, not workflow state. */
export const PO_DETAIL_STAGE_SECTION_IDS = {
  "Order Masuk": "po-stage-order-masuk",
  Design: "po-stage-design",
  "Approval Design": "po-stage-design",
  Payment: "po-stage-payment",
  "Approval Payment": "po-stage-payment",
  Penjadwalan: "po-stage-penjadwalan",
  Screener: "po-stage-screener",
  "Persiapan Helper": "po-stage-persiapan-helper",
  Operator: "po-stage-operator",
  "Konfirmasi Sample": "po-stage-konfirmasi-sample",
  Cetak: "po-stage-cetak",
  Packing: "po-stage-packing",
  "Surat Jalan": "po-stage-surat-jalan",
  Loading: "po-stage-loading",
  Pengiriman: "po-stage-pengiriman",
};

/** Display-only URL slugs (`?stage=`). Never sent to the backend. */
export const PO_DETAIL_STAGE_SLUGS = {
  "order-masuk": 0,
  design: 1,
  "approval-design": 2,
  payment: 3,
  "approval-payment": 4,
  penjadwalan: 5,
  screener: 6,
  "persiapan-helper": 7,
  operator: 8,
  "konfirmasi-sample": 9,
  cetak: 10,
  packing: 11,
  "surat-jalan": 12,
  loading: 13,
  pengiriman: 14,
};

export const PO_DETAIL_STAGE_SLUG_BY_INDEX = [
  "order-masuk",
  "design",
  "approval-design",
  "payment",
  "approval-payment",
  "penjadwalan",
  "screener",
  "persiapan-helper",
  "operator",
  "konfirmasi-sample",
  "cetak",
  "packing",
  "surat-jalan",
  "loading",
  "pengiriman",
];

export function poDetailStageSectionId(stageName) {
  return PO_DETAIL_STAGE_SECTION_IDS[stageName] || null;
}

export function clampWorkflowStageIndex(idx, fallback = 0) {
  const n = Number(idx);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(STAGES.length - 1, Math.max(0, Math.trunc(n)));
}

export function poDetailStageSlug(index) {
  return PO_DETAIL_STAGE_SLUG_BY_INDEX[clampWorkflowStageIndex(index, 0)] || "order-masuk";
}

export function parsePoDetailStageParam(search) {
  const rawSearch = String(search || "");
  const q = new URLSearchParams(rawSearch.startsWith("?") ? rawSearch.slice(1) : rawSearch);
  const raw = q.get("stage") || q.get("focus");
  if (raw == null || raw === "") return null;
  if (/^\d+$/.test(String(raw))) return clampWorkflowStageIndex(raw, 0);
  const slug = String(raw).toLowerCase().replace(/_/g, "-");
  if (Object.prototype.hasOwnProperty.call(PO_DETAIL_STAGE_SLUGS, slug)) {
    return PO_DETAIL_STAGE_SLUGS[slug];
  }
  return null;
}

/**
 * Display-only Detail PO stepper nodes.
 * Internal workflow stays 15 stages; these labels/numbers are UI grouping only.
 */
export const PO_DETAIL_VISUAL_STEPS = [
  { id: "order-masuk", label: "Order Masuk", displayNumber: "1", canonicalIndex: 0, internalIndexes: [0] },
  { id: "design", label: "Design", displayNumber: "2&3", canonicalIndex: 1, internalIndexes: [1, 2] },
  { id: "payment", label: "Payment", displayNumber: "4&5", canonicalIndex: 3, internalIndexes: [3, 4] },
  { id: "penjadwalan", label: "Penjadwalan", displayNumber: "6", canonicalIndex: 5, internalIndexes: [5] },
  { id: "screener", label: "Screener", displayNumber: "7", canonicalIndex: 6, internalIndexes: [6] },
  { id: "persiapan-helper", label: "Persiapan Helper", displayNumber: "8", canonicalIndex: 7, internalIndexes: [7] },
  { id: "operator", label: "Operator", displayNumber: "9", canonicalIndex: 8, internalIndexes: [8] },
  { id: "konfirmasi-sample", label: "Konfirmasi Sample", displayNumber: "10", canonicalIndex: 9, internalIndexes: [9] },
  { id: "cetak", label: "Cetak", displayNumber: "11", canonicalIndex: 10, internalIndexes: [10] },
  { id: "packing", label: "Packing", displayNumber: "12", canonicalIndex: 11, internalIndexes: [11] },
  { id: "surat-jalan", label: "Surat Jalan", displayNumber: "13", canonicalIndex: 12, internalIndexes: [12] },
  { id: "loading", label: "Loading", displayNumber: "14", canonicalIndex: 13, internalIndexes: [13] },
  { id: "pengiriman", label: "Pengiriman", displayNumber: "15", canonicalIndex: 14, internalIndexes: [14] },
];

const VISUAL_STEP_BY_INTERNAL = PO_DETAIL_VISUAL_STEPS.reduce((acc, step) => {
  step.internalIndexes.forEach((idx) => {
    acc[idx] = step;
  });
  return acc;
}, {});

export function visualStepForInternalIndex(stageIndex) {
  const i = clampWorkflowStageIndex(stageIndex, 0);
  return VISUAL_STEP_BY_INTERNAL[i] || PO_DETAIL_VISUAL_STEPS[0];
}

export function visualCanonicalIndex(stageIndex) {
  return visualStepForInternalIndex(stageIndex).canonicalIndex;
}

/** Presentation index for a visual node: live internal stage, else last if passed, else first. */
export function visualStepPresentationIndex(step, currentStage) {
  const current = Number(currentStage);
  if (step?.internalIndexes?.includes(current)) return current;
  const last = step?.internalIndexes?.[step.internalIndexes.length - 1];
  if (Number.isFinite(last) && current > last) return last;
  return step?.internalIndexes?.[0] ?? 0;
}

export function isVisualStepActive(step, currentStage) {
  return !!step?.internalIndexes?.includes(Number(currentStage));
}

export function isVisualStepCompleted(step, currentStage) {
  const last = step?.internalIndexes?.[step.internalIndexes.length - 1];
  return Number.isFinite(last) && Number(currentStage) > last;
}

/** "Tahap Aktif Saat Ini" panel: Order Masuk only. */
export function shouldShowPoActiveStagePanel(viewedStage) {
  return clampWorkflowStageIndex(viewedStage, 0) === 0;
}

export function accordionIndexForFocus(stageIndex) {
  const i = clampWorkflowStageIndex(stageIndex, 0);
  if (i === 2) return 1;
  if (i === 4) return 3;
  if (i === 9) return 8;
  return i;
}

export function deskPanelOwnsStage(stageToken, viewedStage) {
  const viewed = Number(viewedStage);
  const token = String(stageToken);
  if (token === "1" || token === "2" || token === "design") return viewed === 1 || viewed === 2;
  if (token === "3" || token === "4" || token === "payment") return viewed === 3 || viewed === 4;
  if (token === "8" || token === "9" || token === "sample") return viewed === 8 || viewed === 9;
  return viewed === Number(token);
}

export function poDetailStagePanelTitle(stageIndex) {
  const step = visualStepForInternalIndex(stageIndex);
  if (step.internalIndexes.length > 1) {
    return `${step.label.toUpperCase()} — ${step.displayNumber}`;
  }
  return `Workspace ${step.displayNumber} — ${step.label}`;
}

/**
 * Display-only URL sync. Uses history so Back/Forward restore the viewed stage.
 * Does not touch React Router (custom MemoryRouter mocks omit search).
 */
export function syncPoDetailStageQuery(stageIndex, { push = true } = {}) {
  if (typeof window === "undefined" || !window.history) return;
  const slug = poDetailStageSlug(stageIndex);
  try {
    const url = new URL(window.location.href);
    url.searchParams.set("stage", slug);
    url.searchParams.delete("focus");
    const next = `${url.pathname}${url.search}${url.hash}`;
    const state = { ...(window.history.state || {}), poStage: slug };
    if (push) window.history.pushState(state, "", next);
    else window.history.replaceState(state, "", next);
  } catch {
    /* ignore malformed location in tests */
  }
}

export function scrollToPoDetailStage(stageName, { behavior = "smooth" } = {}) {
  const id = poDetailStageSectionId(stageName);
  if (!id) return false;
  const el = typeof document !== "undefined" ? document.getElementById(id) : null;
  if (!el) return false;
  el.scrollIntoView({ behavior, block: "start" });
  return true;
}

export function scrollToPoDetailStageIndex(stageIndex, options) {
  return scrollToPoDetailStage(STAGES[stageIndex], options);
}
