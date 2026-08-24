// Semantic stage completion — shared by Timeline + StageBar.
// Design/Payment/Approval stages must NOT inherit "done" from current_stage alone.

import { resolveDesignChip, resolveApprovalDesignChip } from "./designEvidence.js";

const STAGE_DESIGN = 1;
const STAGE_APPROVAL_DESIGN = 2;
const STAGE_PAYMENT = 3;
const STAGE_APPROVAL_PAYMENT = 4;
const STAGE_PENJADWALAN = 5;
const STAGE_SCREENER = 6;
const STAGE_HELPER = 7;
const STAGE_OPERATOR = 8;
const STAGE_KONFIRMASI_SAMPLE = 9;
const STAGE_CETAK = 10;
const STAGE_PACKING = 11;
const STAGE_SURAT_JALAN = 12;
const STAGE_LOADING = 13;
const STAGE_TERKIRIM = 14;

const hasValue = (v) => v !== null && v !== undefined && String(v).trim() !== "";

export function group2Complete(order) {
  const designOk = !!(order?.design_bypassed) || order?.design_status === "Approved";
  const paymentOk = !!(order?.payment_bypassed) || order?.payment_status === "Approved";
  return designOk && paymentOk;
}

export function getWorkflowReady(order) {
  if (order?.workflow_ready && typeof order.workflow_ready === "object") {
    return order.workflow_ready;
  }
  const g2 = group2Complete(order);
  const scheduled = penjadwalanDone(order);
  const screen = screenerDone(order);
  const reqPrint = order?.requires_printing !== false;
  const op = reqPrint ? scheduled && screen : scheduled;
  const uploaded = sampleUploaded(order);
  return {
    design: true,
    approval_design: (order?.design_files || []).length > 0 || !!order?.design_bypassed,
    payment: true,
    approval_payment: (order?.payment_files || []).length > 0 || !!order?.payment_bypassed,
    penjadwalan: g2,
    screener: g2 && scheduled,
    helper: g2 && scheduled,
    operator: op,
    sample_upload: reqPrint && op,
    sample_approval: reqPrint && uploaded && op,
    packing: reqPrint ? sampleApproved(order) : g2 && scheduled,
  };
}

function penjadwalanDone(order) {
  if (order?.requires_printing === false) return true;
  return (order?.operator_splits || []).length > 0;
}
function screenerDone(order) {
  if (order?.requires_printing === false) return true;
  return hasValue(order?.screener_info?.screen_qty);
}
function helperDone(order) {
  if (order?.requires_printing === false) return true;
  return hasValue(order?.helper_prep?.qty_prepared);
}
function sampleUploaded(order) {
  return (order?.sample_files || []).length > 0;
}
function sampleApproved(order) {
  return !!order?.sample_approved;
}
function printQtyInput(order) {
  return (order?.operator_splits || []).some(
    (s) => hasValue(s?.qty_completed) && Number(s.qty_completed) > 0
  );
}
function packingQtyInput(order) {
  const v = order?.packing_info?.qty_packed;
  return hasValue(v) && Number(v) > 0;
}
function packingDone(order) {
  return (order?.current_stage || 0) >= 12;
}
function suratJalanCreated(order) {
  return !!(order?.surat_jalan_created);
}
function loadingCompleted(order) {
  return !!(order?.loading_completed);
}
function terkirimConfirmed(order) {
  return !!(order?.terkirim_confirmed);
}

export function resolveStageState(idx, order, currentStage) {
  const ready = getWorkflowReady(order);

  // Design + Approval Design: same evidence helpers as Timeline (never idx < currentStage).
  if (idx === STAGE_DESIGN) return resolveDesignChip(order, currentStage);
  if (idx === STAGE_APPROVAL_DESIGN) return resolveApprovalDesignChip(order, currentStage);
  if (idx === STAGE_PAYMENT) {
    if (order?.payment_bypassed) return { state: "bypass", label: "BYPASS" };
    if ((order?.payment_files || []).length > 0) return { state: "done", label: "Selesai" };
    return { state: "current", label: "Belum dikerjakan" };
  }
  if (idx === STAGE_APPROVAL_PAYMENT) {
    if (order?.payment_status === "Approved") return { state: "done", label: "Selesai" };
    if (order?.payment_bypassed) return { state: "bypass", label: "BYPASS" };
    if ((order?.payment_files || []).length > 0) return { state: "current", label: "Menunggu approval" };
    return { state: "pending", label: "Menunggu" };
  }

  const _reqPrint = order?.requires_printing !== false;
  const _nonPrintPenjDone = !_reqPrint && (
    hasValue(order?.work_date) || penjadwalanDone(order) || (currentStage || 0) > STAGE_PENJADWALAN
  );
  if (idx === STAGE_PENJADWALAN) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone || group2Complete(order)) return { state: "done", label: "Selesai" };
      if (ready.penjadwalan) return { state: "current", label: "Menunggu jadwal" };
      return { state: "pending", label: "Menunggu" };
    }
    if (penjadwalanDone(order)) return { state: "done", label: "Selesai" };
    if (ready.penjadwalan) return { state: "current", label: "Menunggu jadwal" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_SCREENER) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone || group2Complete(order)) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (screenerDone(order)) return { state: "done", label: "Selesai" };
    if (ready.screener) return { state: "current", label: "Menunggu input" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_HELPER) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone || group2Complete(order)) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (helperDone(order)) return { state: "done", label: "Selesai" };
    if (ready.helper) return { state: "current", label: "Menunggu input" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_OPERATOR) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone || group2Complete(order)) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (sampleUploaded(order)) return { state: "done", label: "Selesai" };
    if (ready.operator) return { state: "current", label: "Menunggu upload sampel" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_KONFIRMASI_SAMPLE) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone || group2Complete(order)) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (sampleApproved(order)) return { state: "done", label: "Selesai" };
    if (sampleUploaded(order)) return { state: "current", label: "Menunggu approval sampel" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_CETAK) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone || group2Complete(order)) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (printQtyInput(order)) return { state: "done", label: "Selesai" };
    if (ready.operator) return { state: "current", label: "Menunggu input qty cetak" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_PACKING) {
    if (packingDone(order)) return { state: "done", label: "Selesai" };
    if (packingQtyInput(order) && currentStage === idx) return { state: "current", label: "Berjalan" };
    if (ready.packing || currentStage >= idx) return { state: "current", label: "Menunggu input qty packing" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_SURAT_JALAN) {
    if (suratJalanCreated(order) && currentStage > idx) return { state: "done", label: "Selesai" };
    if (suratJalanCreated(order) && currentStage === idx) return { state: "current", label: "SJ dibuat — siap Loading" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu pembuatan Surat Jalan" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_LOADING) {
    if (loadingCompleted(order) && currentStage > idx) return { state: "done", label: "Selesai" };
    if (loadingCompleted(order) && currentStage === idx) return { state: "current", label: "Loading selesai — siap Terkirim" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu checklist Loading" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_TERKIRIM) {
    if (terkirimConfirmed(order)) return { state: "done", label: "Selesai" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu konfirmasi Terkirim" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx < currentStage) return { state: "done", label: "Selesai" };
  if (idx === currentStage) return { state: "current", label: "Berjalan" };
  return { state: "pending", label: "Menunggu" };
}
