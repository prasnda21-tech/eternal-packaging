// Semantic stage completion — shared by Timeline + StageBar.
// Design/Payment/Approval stages must NOT inherit "done" from current_stage alone.

import { isApprovalDesignComplete, resolveDesignChip, resolveApprovalDesignChip } from "./designEvidence.js";

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
  const designOk = isApprovalDesignComplete(order);
  const paymentOk = !!(order?.payment_bypassed) || order?.payment_status === "Approved";
  return designOk && paymentOk;
}

export function getWorkflowReady(order) {
  if (order?.workflow_ready && typeof order.workflow_ready === "object") {
    const ready = order.workflow_ready;
    if (ready.print_qty === undefined) {
      const reqPrint = order?.requires_printing !== false;
      return {
        ...ready,
        print_qty: !!(reqPrint && ready.operator && order?.sample_approved),
      };
    }
    return ready;
  }
  const g2 = group2Complete(order);
  const reqPrint = order?.requires_printing !== false;
  const scheduled = penjadwalanDone(order);
  const screen = screenerDone(order);
  const helper = helperDone(order);
  const op = reqPrint ? helper : scheduled;
  const uploaded = sampleUploaded(order);
  const sampleOk = reqPrint ? (op && sampleApproved(order)) : true;
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
    print_qty: reqPrint && op && sampleApproved(order),
    packing: reqPrint ? sampleOk : g2 && scheduled,
  };
}

function penjadwalanDone(order) {
  if (order?.requires_printing === false) return true;
  return group2Complete(order) && (order?.operator_splits || []).length > 0;
}
function screenerDone(order) {
  if (order?.requires_printing === false) return true;
  return penjadwalanDone(order) && hasValue(order?.screener_info?.screen_qty);
}
function helperDone(order) {
  if (order?.requires_printing === false) return true;
  return penjadwalanDone(order) && hasValue(order?.helper_prep?.qty_prepared);
}
function sampleUploaded(order) {
  return (order?.sample_files || []).length > 0;
}
function sampleApproved(order) {
  const reqPrint = order?.requires_printing !== false;
  if (!reqPrint) return true;
  return helperDone(order) && !!order?.sample_approved;
}
function printCompletedQty(order) {
  if (typeof order?.print_completed_qty === "number") return Number(order.print_completed_qty);
  return (order?.operator_splits || []).reduce((s, x) => s + Number(x?.qty_completed || 0), 0);
}
function printQtyTarget(order) {
  if (typeof order?.print_qty_target === "number" && Number(order.print_qty_target) > 0) {
    return Number(order.print_qty_target);
  }
  const assigned = Number(order?.operator_assigned_qty || 0);
  if (assigned > 0) return assigned;
  return Number(order?.print_qty_total || 0);
}
function printStatusOf(order) {
  if (order?.print_status) return order.print_status;
  const completed = printCompletedQty(order);
  const target = printQtyTarget(order);
  if (completed <= 0) return "BELUM MULAI CETAK";
  if (target > 0 && completed >= target) return "SELESAI CETAK";
  return "BELUM SELESAI CETAK";
}
function packingQtyInput(order) {
  const lines = order?.packing_info?.lines;
  if (Array.isArray(lines) && lines.length) {
    return lines.some((it) => Number(it?.packed_qty || 0) > 0);
  }
  const v = order?.packing_info?.qty_packed;
  return hasValue(v) && Number(v) > 0;
}
function packingDone(order) {
  return (order?.current_stage || 0) >= 12;
}
function suratJalanCreated(order) {
  // Prefer authoritative backend flag when present.
  if (order && Object.prototype.hasOwnProperty.call(order, "surat_jalan_created")) {
    return !!order.surat_jalan_created;
  }
  // Fallback for partial fixtures: live SJ files only (same as Loading / FILE TERSIMPAN).
  const hist = Array.isArray(order?.sj_files)
    ? order.sj_files.filter((f) => f && f.has_file !== false && !f.is_deleted)
    : [];
  if (hist.length) return true;
  const files = Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : [];
  return files.some((f) => f && !f.is_deleted && (f.id || f.file_id));
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
    // Empty Payment is current only when workflow is actually on Payment — not a default empty-files active.
    if (Number(currentStage || 0) === STAGE_PAYMENT) return { state: "current", label: "Belum dikerjakan" };
    return { state: "pending", label: "Menunggu" };
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
    if (!penjadwalanDone(order)) return { state: "pending", label: "Menunggu" };
    if (screenerDone(order)) return { state: "done", label: "Selesai" };
    if (ready.screener) return { state: "current", label: "Menunggu input" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_HELPER) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone || group2Complete(order)) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (!penjadwalanDone(order)) return { state: "pending", label: "Menunggu" };
    if (helperDone(order)) return { state: "done", label: "Selesai" };
    if (ready.helper) return { state: "current", label: "Menunggu input" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_OPERATOR) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone || group2Complete(order)) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (!helperDone(order)) return { state: "pending", label: "Menunggu" };
    if (sampleUploaded(order)) return { state: "done", label: "Selesai" };
    if (ready.operator) return { state: "current", label: "Menunggu upload sampel" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_KONFIRMASI_SAMPLE) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone || group2Complete(order)) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (!helperDone(order)) return { state: "pending", label: "Menunggu" };
    if (sampleApproved(order)) return { state: "done", label: "Selesai" };
    if (sampleUploaded(order)) return { state: "current", label: "Menunggu approval sampel" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_CETAK) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone || group2Complete(order)) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (!sampleApproved(order)) return { state: "pending", label: "Menunggu" };
    const status = printStatusOf(order);
    if (status === "SELESAI CETAK") return { state: "done", label: "SELESAI CETAK" };
    if (!ready.print_qty) return { state: "pending", label: "Menunggu" };
    if (status === "BELUM SELESAI CETAK") return { state: "current", label: "BELUM SELESAI CETAK" };
    return { state: "current", label: "BELUM MULAI CETAK" };
  }
  if (idx === STAGE_PACKING) {
    if (_reqPrint && !sampleApproved(order)) return { state: "pending", label: "Menunggu" };
    if (!_reqPrint && !(group2Complete(order) && penjadwalanDone(order))) return { state: "pending", label: "Menunggu" };
    if (packingDone(order)) return { state: "done", label: "Selesai" };
    if (packingQtyInput(order) && currentStage === idx) return { state: "current", label: "Berjalan" };
    if (ready.packing || currentStage >= idx) return { state: "current", label: "Menunggu input qty packing" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_SURAT_JALAN) {
    const sjStatus = order?.surat_jalan_status_semantic;
    if (suratJalanCreated(order)) {
      const loadingDone = order?.loading_qty_status === "completed";
      if (sjStatus === "completed" || loadingDone) {
        return { state: "done", label: "Selesai" };
      }
      if (sjStatus === "partial") {
        return { state: "current", label: "SJ parsial — siap Loading" };
      }
      return { state: "current", label: "SJ dibuat — siap Loading" };
    }
    if (!packingQtyInput(order) && (currentStage || 0) < STAGE_SURAT_JALAN) {
      return { state: "pending", label: "Menunggu" };
    }
    if (currentStage >= idx) return { state: "current", label: "Menunggu pembuatan Surat Jalan" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_LOADING) {
    const verification = order?.loading_verification_status;
    if (verification?.label) {
      const code = String(verification.code || "");
      if (code.includes("complete")) return { state: "done", label: verification.label };
      if (code.includes("held") || code.includes("mismatch")) return { state: "current", label: verification.label };
      if (code.includes("packing")) return { state: "current", label: verification.label };
      // Default waiting_verification (or any unused code) is not current without a live Surat Jalan.
      if (suratJalanCreated(order)) return { state: "current", label: verification.label };
    }
    const confirmations = Array.isArray(order?.loading_confirmations) ? order.loading_confirmations : [];
    const hasConfirm = confirmations.length > 0;
    const qtyStatus = order?.loading_qty_status;
    const hasLegacy = Number(order?.loading_info?.lines?.length || 0) > 0
      || Number(order?.loading_info?.events?.length || 0) > 0
      || !!(order?.loading_completed)
      || qtyStatus === "completed"
      || qtyStatus === "partial";
    if (hasConfirm) {
      if (qtyStatus === "completed" || loadingCompleted(order)) {
        return { state: "done", label: "Loading Selesai" };
      }
      if (qtyStatus === "partial") return { state: "current", label: "Siap Diverifikasi" };
      return { state: "current", label: "Verifikasi Kesesuaian SJ" };
    }
    if (suratJalanCreated(order)) {
      return { state: "current", label: "Verifikasi Kesesuaian SJ" };
    }
    if (hasLegacy) {
      return { state: "done", label: "Riwayat Loading" };
    }
    if (terkirimConfirmed(order) || order?.delivery_status === "Terkirim") {
      return { state: "done", label: "Dilewati" };
    }
    return { state: "pending", label: "Menunggu Surat Jalan" };
  }
  if (idx === STAGE_TERKIRIM) {
    if (terkirimConfirmed(order) || order?.delivery_status === "Terkirim") return { state: "done", label: "Selesai" };
    if (order?.delivery_status === "Sebagian Terkirim" || Number(order?.qty_delivered || 0) > 0) {
      return { state: "current", label: "Parsial" };
    }
    const eligible = Number(order?.eligible_delivery_qty || 0);
    if (eligible > 0) {
      return { state: "current", label: "Menunggu bukti pengiriman" };
    }
    if (suratJalanCreated(order)) {
      return { state: "pending", label: "Menunggu konfirmasi Loading" };
    }
    if (currentStage >= STAGE_SURAT_JALAN) {
      return { state: "pending", label: "Menunggu konfirmasi Loading" };
    }
    return { state: "pending", label: "Menunggu" };
  }
  if (idx < currentStage) return { state: "done", label: "Selesai" };
  if (idx === currentStage) return { state: "current", label: "Berjalan" };
  return { state: "pending", label: "Menunggu" };
}
