import { useEffect, useId, useRef, useState } from "react";
import { Check, Clock, FileText, Send, X, Zap } from "lucide-react";
import {
  URGENT_REASON_MAX,
  URGENT_NOTES_MAX,
  URGENT_DECISION_NOTES_MAX,
  URGENT_DISPLAY,
  REQUEST_STATUS,
  classifyUrgentDisplay,
  priorityBadgeLabel,
  validateUrgentRequestForm,
  validateUrgentDecisionForm,
  formatUrgentIdDate,
  formatUrgentIdDateTime,
} from "../../lib/urgentPoUi";

function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
}

function FieldError({ id, children }) {
  if (!children) return null;
  return (
    <p className="error small" id={id} role="alert" data-testid={id}>
      {children}
    </p>
  );
}

function SummaryRow({ label, children, testId, block = false }) {
  return (
    <div className={block ? "urgent-po-summary-block" : undefined}>
      <dt>{label}</dt>
      <dd data-testid={testId}>{children}</dd>
    </div>
  );
}

/** Structured history/detail fields by request status (compact modal content). */
export function UrgentHistoryDetail({ request }) {
  if (!request) return null;
  const status = String(request.status || "").toLowerCase();
  const targetLabel = formatUrgentIdDate(request.target_completion_date) || request.target_completion_date || "—";
  const requestedAt = formatUrgentIdDateTime(request.requested_at) || "—";
  const decidedAt = formatUrgentIdDateTime(request.decided_at) || "—";

  return (
    <dl className="urgent-po-summary" data-testid="urgent-history-detail" data-status={status}>
      <SummaryRow label="No PO" testId="urgent-history-po">{request.po_number || "—"}</SummaryRow>
      <SummaryRow label="Target selesai" testId="urgent-history-target">{targetLabel}</SummaryRow>
      <SummaryRow label="Pengaju" testId="urgent-history-requester">
        <span className="urgent-po-requester">
          <span className="urgent-po-avatar" aria-hidden>{initials(request.requested_by_name)}</span>
          {request.requested_by_name || "—"}
        </span>
      </SummaryRow>
      <SummaryRow label="Waktu pengajuan" testId="urgent-history-requested-at">{requestedAt}</SummaryRow>
      <SummaryRow label="Alasan pengajuan" testId="urgent-history-reason" block>
        {request.reason || "—"}
      </SummaryRow>
      {request.notes ? (
        <SummaryRow label="Catatan tambahan" testId="urgent-history-notes" block>
          {request.notes}
        </SummaryRow>
      ) : null}

      {status === REQUEST_STATUS.APPROVED ? (
        <>
          <SummaryRow label="Disetujui oleh" testId="urgent-history-actor">
            {request.decided_by_name || "—"}
          </SummaryRow>
          <SummaryRow label="Waktu persetujuan" testId="urgent-history-decided-at">
            {decidedAt}
          </SummaryRow>
          {request.decision_notes ? (
            <SummaryRow label="Catatan keputusan" testId="urgent-history-decision-notes" block>
              {request.decision_notes}
            </SummaryRow>
          ) : null}
        </>
      ) : null}

      {status === REQUEST_STATUS.REJECTED ? (
        <>
          <SummaryRow label="Ditolak oleh" testId="urgent-history-actor">
            {request.decided_by_name || "—"}
          </SummaryRow>
          <SummaryRow label="Waktu penolakan" testId="urgent-history-decided-at">
            {decidedAt}
          </SummaryRow>
          <SummaryRow label="Alasan penolakan" testId="urgent-history-reject-reason" block>
            {request.decision_notes || "—"}
          </SummaryRow>
        </>
      ) : null}
    </dl>
  );
}

/** Compact status banner on PO detail / preview chrome. */
export function UrgentPoStatusBanner({
  order,
  request,
  canApprove = false,
  onViewDetail,
  onReview,
  onViewHistory,
}) {
  const display = classifyUrgentDisplay({ priority: order?.priority, request });

  if (display === URGENT_DISPLAY.PENDING_REQUEST && request) {
    return (
      <div className="urgent-po-banner urgent-po-banner--pending" data-testid="urgent-po-banner-pending" role="status">
        <div className="urgent-po-banner-text">
          <strong>Pengajuan Urgent sedang menunggu persetujuan.</strong>
          <span>
            {" "}
            Diajukan pada {formatUrgentIdDateTime(request.requested_at)} oleh {request.requested_by_name || "—"}.
            {" "}PO tetap berprioritas Normal.
          </span>
        </div>
        <div className="urgent-po-banner-actions">
          <button type="button" className="ghost sm" onClick={onViewDetail} data-testid="urgent-po-banner-view">
            Lihat Detail
          </button>
          {canApprove ? (
            <button type="button" className="primary sm" onClick={onReview} data-testid="urgent-po-banner-review">
              Review
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  if (display === URGENT_DISPLAY.APPROVED_URGENT && request) {
    return (
      <div className="urgent-po-banner urgent-po-banner--approved" data-testid="urgent-po-banner-approved" role="status">
        <div className="urgent-po-banner-text">
          <strong>PO ini berstatus Urgent.</strong>
          <span>
            {" "}
            Disetujui pada {formatUrgentIdDateTime(request.decided_at)} oleh {request.decided_by_name || "—"}.
          </span>
        </div>
        <div className="urgent-po-banner-actions">
          <button type="button" className="ghost sm" onClick={onViewHistory} data-testid="urgent-po-banner-history">
            Lihat Riwayat
          </button>
        </div>
      </div>
    );
  }

  if (display === URGENT_DISPLAY.LEGACY_URGENT) {
    return (
      <div className="urgent-po-banner urgent-po-banner--legacy" data-testid="urgent-po-banner-legacy" role="status">
        <div className="urgent-po-banner-text">
          <strong>PO Urgent (historis).</strong>
          <span>
            {" "}Tampilan lama — tidak memerlukan pengajuan ulang.
            {order?.urgency_approved_by ? ` Ditandai Urgent oleh ${order.urgency_approved_by}.` : ""}
          </span>
        </div>
      </div>
    );
  }

  if (request?.status === REQUEST_STATUS.REJECTED) {
    return (
      <div className="urgent-po-banner urgent-po-banner--rejected" data-testid="urgent-po-banner-rejected" role="status">
        <div className="urgent-po-banner-text">
          <strong>Pengajuan Urgent ditolak.</strong>
          <span>
            {" "}
            Prioritas PO tetap Normal. Ditolak pada {formatUrgentIdDateTime(request.decided_at)} oleh{" "}
            {request.decided_by_name || "—"}.
          </span>
        </div>
        <div className="urgent-po-banner-actions">
          <button type="button" className="ghost sm" onClick={onViewHistory} data-testid="urgent-po-banner-history">
            Lihat Riwayat
          </button>
        </div>
      </div>
    );
  }

  return null;
}

export function UrgentPriorityBadge({ priority, request }) {
  const label = priorityBadgeLabel({ priority, request });
  const urgent = label === "URGENT";
  return (
    <span
      className={urgent ? "urgent-badge" : "standard-badge"}
      data-testid="urgent-po-priority-badge"
      data-urgent-display={classifyUrgentDisplay({ priority, request })}
    >
      {label}
    </span>
  );
}

export function UrgentRequestSubmitModal({ open, poNumber, busy = false, onCancel, onSubmit }) {
  const titleId = useId();
  const reasonRef = useRef(null);
  const [reason, setReason] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [notes, setNotes] = useState("");
  const [errors, setErrors] = useState({});
  const [submitErr, setSubmitErr] = useState("");

  useEffect(() => {
    if (!open) return;
    setReason("");
    setTargetDate("");
    setNotes("");
    setErrors({});
    setSubmitErr("");
    const t = window.setTimeout(() => reasonRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [open]);

  if (!open) return null;

  const targetIdLabel = formatUrgentIdDate(targetDate);

  const handleSubmit = async () => {
    if (busy) return;
    const v = validateUrgentRequestForm({ reason, targetDate, notes });
    setErrors(v.errors);
    setSubmitErr("");
    if (!v.ok) {
      if (v.errors.reason) reasonRef.current?.focus();
      return;
    }
    try {
      await onSubmit?.(v.values);
    } catch (e) {
      setSubmitErr(e?.message || "Gagal mengirim pengajuan (preview).");
    }
  };

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} data-testid="urgent-request-submit-modal" onClick={busy ? undefined : onCancel}>
      <div className="modal-inner urgent-po-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3 id={titleId}>
            <Zap size={16} className="urgent-po-modal-icon urgent-po-modal-icon--warn" aria-hidden />
            {" "}Pengajuan Urgent PO
          </h3>
          <button type="button" className="icon-button" onClick={onCancel} disabled={busy} aria-label="Tutup" data-testid="urgent-submit-close">
            <X size={16} />
          </button>
        </div>
        <div className="urgent-po-modal-body">
          <div className="urgent-po-callout urgent-po-callout--warn" data-testid="urgent-submit-callout">
            Pengajuan akan masuk antrean persetujuan. Status PO tetap Normal sampai disetujui.
          </div>
          {poNumber ? (
            <p className="muted small" data-testid="urgent-submit-po">
              PO <span className="mono">{poNumber}</span>
            </p>
          ) : null}
          <label className="urgent-po-field">
            <span>
              Alasan urgent <abbr title="wajib">*</abbr>
            </span>
            <textarea
              ref={reasonRef}
              rows={3}
              maxLength={URGENT_REASON_MAX}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={busy}
              aria-invalid={Boolean(errors.reason)}
              aria-describedby={errors.reason ? "urgent-submit-reason-err" : undefined}
              data-testid="urgent-submit-reason"
              placeholder="Contoh: Customer membutuhkan pesanan untuk pembukaan outlet baru."
            />
            <span className="urgent-po-counter" data-testid="urgent-submit-reason-count">
              {reason.length}/{URGENT_REASON_MAX}
            </span>
            <FieldError id="urgent-submit-reason-err">{errors.reason}</FieldError>
          </label>
          <label className="urgent-po-field">
            <span>
              Target selesai yang diminta <abbr title="wajib">*</abbr>
            </span>
            <input
              type="date"
              value={targetDate}
              onChange={(e) => setTargetDate(e.target.value)}
              disabled={busy}
              aria-invalid={Boolean(errors.targetDate)}
              aria-describedby={targetIdLabel ? "urgent-submit-target-id" : undefined}
              data-testid="urgent-submit-target"
            />
            {targetIdLabel ? (
              <span className="urgent-po-date-hint" data-testid="urgent-submit-target-id">
                Tampilan: {targetIdLabel}
              </span>
            ) : null}
            <FieldError id="urgent-submit-target-err">{errors.targetDate}</FieldError>
          </label>
          <label className="urgent-po-field">
            <span>Catatan tambahan (opsional)</span>
            <textarea
              rows={2}
              maxLength={URGENT_NOTES_MAX}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              disabled={busy}
              data-testid="urgent-submit-notes"
              placeholder="Catatan untuk approver"
            />
            <FieldError id="urgent-submit-notes-err">{errors.notes}</FieldError>
          </label>
          {submitErr ? <p className="error" data-testid="urgent-submit-error">{submitErr}</p> : null}
        </div>
        <div className="delete-modal-actions">
          <button type="button" className="ghost" onClick={onCancel} disabled={busy} data-testid="urgent-submit-cancel">
            Batal
          </button>
          <button type="button" className="primary" onClick={handleSubmit} disabled={busy} data-testid="urgent-submit-send">
            <Send size={14} /> {busy ? "Mengirim…" : "Kirim Pengajuan"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function UrgentRequestPendingModal({ open, request, onClose }) {
  const titleId = useId();
  if (!open || !request) return null;
  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} data-testid="urgent-request-pending-modal" onClick={onClose}>
      <div className="modal-inner urgent-po-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3 id={titleId}>
            <Clock size={16} className="urgent-po-modal-icon urgent-po-modal-icon--warn" aria-hidden />
            {" "}Pengajuan Terkirim
          </h3>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Tutup" data-testid="urgent-pending-close">
            <X size={16} />
          </button>
        </div>
        <div className="urgent-po-modal-body">
          <div className="urgent-po-callout urgent-po-callout--warn" data-testid="urgent-pending-callout">
            Menunggu Persetujuan. Pengajuan sedang menunggu keputusan pengguna berizin. PO tetap berprioritas Normal.
          </div>
          <UrgentHistoryDetail request={request} />
        </div>
        <div className="delete-modal-actions">
          <button type="button" className="ghost" onClick={onClose} data-testid="urgent-pending-tutup">
            Tutup
          </button>
        </div>
      </div>
    </div>
  );
}

export function UrgentRequestReviewModal({ open, request, busy = false, onCancel, onConfirm }) {
  const titleId = useId();
  const notesRef = useRef(null);
  const [decision, setDecision] = useState("");
  const [decisionNotes, setDecisionNotes] = useState("");
  const [errors, setErrors] = useState({});
  const [submitErr, setSubmitErr] = useState("");

  useEffect(() => {
    if (!open) return;
    setDecision("");
    setDecisionNotes("");
    setErrors({});
    setSubmitErr("");
  }, [open]);

  if (!open || !request) return null;

  const selectDecision = (next) => {
    setDecision(next);
    setErrors((prev) => {
      const copy = { ...prev };
      delete copy.decision;
      if (next !== "rejected") delete copy.decisionNotes;
      return copy;
    });
  };

  const handleConfirm = async () => {
    if (busy) return;
    const v = validateUrgentDecisionForm({ decision, decisionNotes });
    setErrors(v.errors);
    setSubmitErr("");
    if (!v.ok) {
      if (v.errors.decisionNotes) notesRef.current?.focus();
      return;
    }
    try {
      await onConfirm?.(v.values);
    } catch (e) {
      setSubmitErr(e?.message || "Gagal menyimpan keputusan (preview).");
    }
  };

  const confirmLabel = busy
    ? "Menyimpan…"
    : decision === "rejected"
      ? "Konfirmasi Penolakan"
      : decision === "approved"
        ? "Konfirmasi Persetujuan"
        : "Pilih keputusan";

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} data-testid="urgent-request-review-modal" onClick={busy ? undefined : onCancel}>
      <div className="modal-inner urgent-po-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3 id={titleId}>
            <FileText size={16} className="urgent-po-modal-icon" aria-hidden />
            {" "}Review Pengajuan Urgent
          </h3>
          <button type="button" className="icon-button" onClick={onCancel} disabled={busy} aria-label="Tutup" data-testid="urgent-review-close">
            <X size={16} />
          </button>
        </div>
        <div className="urgent-po-modal-body">
          <UrgentHistoryDetail request={request} />
          <fieldset className="urgent-po-decision" data-testid="urgent-review-decision">
            <legend>
              Keputusan <abbr title="wajib">*</abbr>
            </legend>
            <div className="urgent-po-decision-cards" role="radiogroup" aria-label="Keputusan pengajuan urgent">
              <label
                className={`urgent-po-decision-card${decision === "approved" ? " is-selected is-approve" : ""}`}
                data-testid="urgent-review-approve-card"
              >
                <input
                  type="radio"
                  name="urgent-decision"
                  value="approved"
                  checked={decision === "approved"}
                  onChange={() => selectDecision("approved")}
                  disabled={busy}
                  data-testid="urgent-review-approve"
                />
                <span className="urgent-po-decision-indicator" aria-hidden>
                  {decision === "approved" ? <Check size={12} /> : null}
                </span>
                <strong>Setujui</strong>
                <span className="muted small">Prioritas PO akan menjadi Urgent</span>
              </label>
              <label
                className={`urgent-po-decision-card${decision === "rejected" ? " is-selected is-reject" : ""}`}
                data-testid="urgent-review-reject-card"
              >
                <input
                  type="radio"
                  name="urgent-decision"
                  value="rejected"
                  checked={decision === "rejected"}
                  onChange={() => selectDecision("rejected")}
                  disabled={busy}
                  data-testid="urgent-review-reject"
                />
                <span className="urgent-po-decision-indicator" aria-hidden>
                  {decision === "rejected" ? <Check size={12} /> : null}
                </span>
                <strong>Tolak</strong>
                <span className="muted small">Prioritas PO tetap Normal</span>
              </label>
            </div>
            <FieldError id="urgent-review-decision-err">{errors.decision}</FieldError>
          </fieldset>
          <label className="urgent-po-field">
            <span>
              {decision === "rejected" ? "Alasan penolakan" : "Catatan keputusan"}
              {decision === "rejected" ? " " : " "}
              {decision === "rejected" ? <abbr title="wajib">*</abbr> : <span className="muted">(opsional)</span>}
            </span>
            <textarea
              ref={notesRef}
              rows={2}
              maxLength={URGENT_DECISION_NOTES_MAX}
              value={decisionNotes}
              onChange={(e) => setDecisionNotes(e.target.value)}
              disabled={busy}
              aria-invalid={Boolean(errors.decisionNotes)}
              data-testid="urgent-review-notes"
              placeholder={decision === "rejected" ? "Alasan penolakan wajib" : "Catatan untuk pemohon (opsional)"}
            />
            <FieldError id="urgent-review-notes-err">{errors.decisionNotes}</FieldError>
          </label>
          {submitErr ? <p className="error" data-testid="urgent-review-error">{submitErr}</p> : null}
        </div>
        <div className="delete-modal-actions">
          <button type="button" className="ghost" onClick={onCancel} disabled={busy} data-testid="urgent-review-cancel">
            Batal
          </button>
          <button
            type="button"
            className={decision === "rejected" ? "ghost danger" : "primary"}
            onClick={handleConfirm}
            disabled={busy || !decision}
            data-testid="urgent-review-confirm"
            data-decision={decision || ""}
          >
            <Check size={14} /> {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function UrgentRequestResultModal({ open, request, onClose }) {
  const titleId = useId();
  if (!open || !request) return null;
  const approved = request.status === REQUEST_STATUS.APPROVED;
  const rejected = request.status === REQUEST_STATUS.REJECTED;
  const title = approved ? "Riwayat — Urgent Disetujui" : rejected ? "Riwayat — Pengajuan Ditolak" : "Riwayat Pengajuan";
  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} data-testid="urgent-request-result-modal" onClick={onClose}>
      <div className="modal-inner urgent-po-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3 id={titleId}>{title}</h3>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Tutup" data-testid="urgent-result-close">
            <X size={16} />
          </button>
        </div>
        <div className="urgent-po-modal-body">
          <div
            className={`urgent-po-callout ${approved ? "urgent-po-callout--ok" : rejected ? "urgent-po-callout--danger" : "urgent-po-callout--warn"}`}
            data-testid="urgent-result-callout"
          >
            {approved
              ? "Prioritas PO telah diubah menjadi Urgent."
              : rejected
                ? "Prioritas PO tetap Normal."
                : "Pengajuan masih menunggu keputusan."}
          </div>
          <UrgentHistoryDetail request={request} />
        </div>
        <div className="delete-modal-actions">
          <button type="button" className="ghost" onClick={onClose} data-testid="urgent-result-tutup">
            Tutup
          </button>
        </div>
      </div>
    </div>
  );
}
