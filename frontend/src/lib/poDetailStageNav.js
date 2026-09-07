import { STAGES } from "./constants";

/** Semantic Detail PO section ids — navigation only, not workflow state. */
export const PO_DETAIL_STAGE_SECTION_IDS = {
  "Order Masuk": "po-stage-order-masuk",
  Design: "po-stage-design",
  "Approval Design": "po-stage-approval-design",
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

export function poDetailStageSectionId(stageName) {
  return PO_DETAIL_STAGE_SECTION_IDS[stageName] || null;
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
