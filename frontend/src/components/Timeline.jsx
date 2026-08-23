import { STAGES, formatDate } from "../lib/constants";
import { ROLES } from "../lib/constants";

// Stages whose "done"-ness is NOT determined by stage progression but by
// their own approval flags. Fixes cross-contamination where approving Payment
// (which advances current_stage past Approval Design) would mark Approval
// Design as "Selesai" even though design_status was still Waiting.
const STAGE_APPROVAL_DESIGN = 2; // 03 Approval Design
const STAGE_APPROVAL_PAYMENT = 4; // 05 Approval Payment
// Workflow 06-12 targeted bugfix: stages 5..10 use SEMANTIC predicates based
// on actual data (screener_info, helper_prep, sample_files, sample_approved,
// operator_splits[].qty_completed) — bukan sekadar `idx < currentStage`.
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

function penjadwalanDone(order) {
  return (order?.operator_splits || []).length > 0;
}
function screenerDone(order) {
  return hasValue(order?.screener_info?.screen_qty);
}
function helperDone(order) {
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
  // Packing SELESAI hanya jika PO sudah keluar dari stage 11 (advance ke 12+).
  return (order?.current_stage || 0) >= 12;
}
// Task 13: Surat Jalan SELESAI hanya jika sudah dibuat eksplisit (source of
// truth = delivery_note_number + delivery_note_created_by/at).
function suratJalanCreated(order) {
  return !!(order?.surat_jalan_created);
}
// Task 14: Loading SELESAI hanya jika checklist valid (items_loaded && seal_ok).
function loadingCompleted(order) {
  return !!(order?.loading_completed);
}
// Task 14: Terkirim CONFIRMED hanya jika delivery action nyata (delivered_at + _by).
function terkirimConfirmed(order) {
  return !!(order?.terkirim_confirmed);
}

function resolveStageState(idx, order, currentStage) {
  // Approval stages use their own field, not current_stage crossing.
  if (idx === STAGE_APPROVAL_DESIGN) {
    if (order?.design_status === "Approved") return { state: "done", label: "Selesai" };
    if (order?.design_bypassed) return { state: "bypass", label: "BYPASS" };
    if (currentStage === idx) return { state: "current", label: "Berjalan" };
    if (currentStage > idx) return { state: "current", label: "Menunggu approval" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_APPROVAL_PAYMENT) {
    if (order?.payment_status === "Approved") return { state: "done", label: "Selesai" };
    if (order?.payment_bypassed) return { state: "bypass", label: "BYPASS" };
    if (currentStage === idx) return { state: "current", label: "Berjalan" };
    if (currentStage > idx) return { state: "current", label: "Menunggu approval" };
    return { state: "pending", label: "Menunggu" };
  }
  // Workflow 06-12 semantic resolvers.
  // Revisi visual: untuk PO Non-Cetak (requires_printing===false), print-prep
  // stages (Penjadwalan/Screener/Helper/Operator/Sample/Cetak) TIDAK ditandai
  // "Tidak Diperlukan" lagi. Visualnya hanya MENUNGGU atau SELESAI. Setelah
  // Penjadwalan selesai (work_date valid ATAU stage sudah lewat), stage 07–11
  // di-fold sebagai SELESAI secara visual. Backend skip logic tidak berubah;
  // TIDAK ada actor/history/data palsu yang dibuat.
  const _reqPrint = order?.requires_printing !== false; // undefined → legacy print
  const _nonPrintPenjDone = !_reqPrint && (
    hasValue(order?.work_date) || (currentStage || 0) > STAGE_PENJADWALAN
  );
  if (idx === STAGE_PENJADWALAN) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone) return { state: "done", label: "Selesai" };
      if (currentStage >= idx) return { state: "current", label: "Menunggu jadwal" };
      return { state: "pending", label: "Menunggu" };
    }
    if (penjadwalanDone(order)) return { state: "done", label: "Selesai" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu jadwal" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_SCREENER) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (screenerDone(order)) return { state: "done", label: "Selesai" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu input" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_HELPER) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (helperDone(order)) return { state: "done", label: "Selesai" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu input" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_OPERATOR) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (sampleUploaded(order)) return { state: "done", label: "Selesai" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu upload sampel" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_KONFIRMASI_SAMPLE) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (sampleApproved(order)) return { state: "done", label: "Selesai" };
    if (sampleUploaded(order)) return { state: "current", label: "Menunggu approval sampel" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu upload sampel" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_CETAK) {
    if (!_reqPrint) {
      if (_nonPrintPenjDone) return { state: "done", label: "Selesai" };
      return { state: "pending", label: "Menunggu" };
    }
    if (printQtyInput(order)) return { state: "done", label: "Selesai" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu input qty cetak" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_PACKING) {
    if (packingDone(order)) return { state: "done", label: "Selesai" };
    if (packingQtyInput(order) && currentStage === idx) return { state: "current", label: "Berjalan" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu input qty packing" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_SURAT_JALAN) {
    // SELESAI hanya kalau SJ sudah dibuat DAN PO sudah keluar dari stage 12.
    if (suratJalanCreated(order) && currentStage > idx) return { state: "done", label: "Selesai" };
    if (suratJalanCreated(order) && currentStage === idx) return { state: "current", label: "SJ dibuat — siap Loading" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu pembuatan Surat Jalan" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_LOADING) {
    // SELESAI hanya kalau checklist Loading complete DAN PO keluar dari 13.
    if (loadingCompleted(order) && currentStage > idx) return { state: "done", label: "Selesai" };
    if (loadingCompleted(order) && currentStage === idx) return { state: "current", label: "Loading selesai — siap Terkirim" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu checklist Loading" };
    return { state: "pending", label: "Menunggu" };
  }
  if (idx === STAGE_TERKIRIM) {
    // SELESAI hanya kalau delivery action nyata (delivered_at + _by).
    if (terkirimConfirmed(order)) return { state: "done", label: "Selesai" };
    if (currentStage >= idx) return { state: "current", label: "Menunggu konfirmasi Terkirim" };
    return { state: "pending", label: "Menunggu" };
  }
  // Default: pure stage progression
  if (idx < currentStage) return { state: "done", label: "Selesai" };
  if (idx === currentStage) return { state: "current", label: "Berjalan" };
  return { state: "pending", label: "Menunggu" };
}

export default function Timeline({ timeline = [], currentStage = 0, order = null }) {
  // Build display: for each stage, show last event or "belum".
  const byStage = STAGES.map((label, idx) => {
    const events = timeline.filter((t) => t.stage === idx);
    const last = events[events.length - 1] || null;
    return { idx, label, last };
  });

  return (
    <ol className="timeline" data-testid="order-timeline">
      {byStage.map(({ idx, label, last }) => {
        const { state, label: stateLabel } = resolveStageState(idx, order, currentStage);
        return (
          <li key={idx} className={`timeline-item timeline-${state}`} data-testid={`timeline-stage-${idx}`}>
            <span className="dot" />
            <div className="body">
              <div className="row-top">
                <span className="idx">{String(idx + 1).padStart(2, "0")}</span>
                <span className="lbl">{label}</span>
                <span className={`chip chip-${state}`} data-testid={`timeline-stage-${idx}-chip`}>
                  {stateLabel}
                </span>
              </div>
              {last && (
                <div className="meta">
                  <span>{formatDate(last.at)}</span>
                  <span>·</span>
                  <span>{last.by_name || last.by_email}</span>
                  <span className="role-tag">{ROLES[last.by_role] || last.by_role}</span>
                  {last.note && <p className="note">{last.note}</p>}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
