import { useCallback, useMemo, useState } from "react";
import { Copy, Zap } from "lucide-react";
import {
  UrgentPoStatusBanner,
  UrgentPriorityBadge,
  UrgentRequestSubmitModal,
  UrgentRequestPendingModal,
  UrgentRequestReviewModal,
  UrgentRequestResultModal,
} from "../components/urgent/UrgentPoApprovalUi";
import {
  classifyUrgentDisplay,
  previewScenarioSeed,
  buildPreviewRequest,
  applyPreviewDecision,
  REQUEST_STATUS,
} from "../lib/urgentPoUi";

const SCENARIOS = [
  { key: "normal", label: "Normal — siap mengajukan" },
  { key: "pending", label: "Pending (pemohon)" },
  { key: "approver", label: "Pending (approver preview)" },
  { key: "approved", label: "Approved → badge Urgent" },
  { key: "rejected", label: "Rejected → tetap Normal" },
  { key: "legacy_urgent", label: "Legacy Urgent (tanpa approval baru)" },
];

/**
 * Isolated Phase A UI preview — no auth, no setUrgent, no backend mutation.
 * Open: /dev/urgent-po-approval-preview
 */
export default function UrgentPoApprovalPreview() {
  const seed = previewScenarioSeed("normal");
  const [scenario, setScenario] = useState("normal");
  const [order, setOrder] = useState(seed.order);
  const [request, setRequest] = useState(seed.request);
  const [role, setRole] = useState(seed.role);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");

  const [submitOpen, setSubmitOpen] = useState(false);
  const [pendingOpen, setPendingOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [resultOpen, setResultOpen] = useState(false);

  const canRequest = role === "requester" && !request?.status && String(order.priority).toLowerCase() !== "urgent";
  const canApprove = role === "approver" && request?.status === REQUEST_STATUS.PENDING;
  const display = classifyUrgentDisplay({ priority: order.priority, request });

  const applyScenario = useCallback((key) => {
    const next = previewScenarioSeed(key);
    setScenario(key);
    setOrder(next.order);
    setRequest(next.request);
    setRole(next.role);
    setSubmitOpen(false);
    setPendingOpen(false);
    setReviewOpen(false);
    setResultOpen(false);
    setToast("");
  }, []);

  const showToast = (msg) => {
    setToast(msg);
    window.setTimeout(() => setToast(""), 2200);
  };

  const onSubmitRequest = async (values) => {
    setBusy(true);
    try {
      // Preview only — never calls mutation endpoint
      const created = buildPreviewRequest({
        poNumber: order.po_number,
        reason: values.reason,
        targetDate: values.targetDate,
        notes: values.notes,
      });
      setRequest(created);
      // Priority stays normal while pending
      setOrder((o) => ({ ...o, priority: "normal" }));
      setSubmitOpen(false);
      setPendingOpen(true);
      showToast("Pengajuan terkirim (preview — tanpa API)");
    } finally {
      setBusy(false);
    }
  };

  const onConfirmDecision = async (values) => {
    setBusy(true);
    try {
      const decided = applyPreviewDecision(request, {
        decision: values.decision,
        decisionNotes: values.decisionNotes,
      });
      setRequest(decided);
      if (values.decision === "approved") {
        setOrder((o) => ({
          ...o,
          priority: "urgent",
          urgency_approved_by: decided.decided_by_email,
        }));
      } else {
        setOrder((o) => ({ ...o, priority: "normal" }));
      }
      setReviewOpen(false);
      setResultOpen(true);
      showToast("Keputusan disimpan (preview — tanpa API)");
    } finally {
      setBusy(false);
    }
  };

  const chrome = useMemo(
    () => (
      <div className="urgent-po-preview-chrome" data-testid="urgent-po-preview-chrome">
        <div className="urgent-po-preview-hero">
          <div>
            <h2 className="urgent-po-preview-customer" data-testid="urgent-preview-customer">
              {order.customer}
            </h2>
            <div className="urgent-po-preview-meta">
              <span className="mono" data-testid="urgent-preview-po">{order.po_number}</span>
              <span className="po-hero-dot" aria-hidden>·</span>
              <span>{order.outlet}</span>
              <UrgentPriorityBadge priority={order.priority} request={request} />
            </div>
          </div>
          <div className="urgent-po-preview-actions">
            <button type="button" className="ghost sm" disabled data-testid="urgent-preview-dup">
              <Copy size={14} /> Duplikasi
            </button>
            {canRequest ? (
              <button
                type="button"
                className="ghost sm"
                onClick={() => setSubmitOpen(true)}
                data-testid="toggle-urgent-button"
              >
                <Zap size={14} /> Urgent
              </button>
            ) : null}
            {canApprove ? (
              <button
                type="button"
                className="primary sm"
                onClick={() => setReviewOpen(true)}
                data-testid="urgent-preview-open-review"
              >
                Review Urgent
              </button>
            ) : null}
            <button type="button" className="ghost sm" disabled>
              Buffer Stock
            </button>
          </div>
        </div>

        <UrgentPoStatusBanner
          order={order}
          request={request}
          canApprove={canApprove}
          onViewDetail={() => setPendingOpen(true)}
          onReview={() => setReviewOpen(true)}
          onViewHistory={() => setResultOpen(true)}
        />

        <p className="muted small" data-testid="urgent-preview-display-state">
          Display state: <code>{display}</code>
          {" · "}priority field: <code>{order.priority}</code>
          {" · "}role: <code>{role}</code>
        </p>
      </div>
    ),
    [order, request, canRequest, canApprove, display, role],
  );

  return (
    <div className="page urgent-po-preview-page" data-testid="urgent-po-approval-preview">
      <header className="urgent-po-preview-header">
        <div>
          <h1>Urgent PO Approval — Phase A UI Preview</h1>
          <p className="muted">
            Preview lokal terisolasi. Tidak memanggil endpoint mutation urgent, tidak mengubah database / permission / email.
          </p>
        </div>
        <p className="mono small">/dev/urgent-po-approval-preview</p>
      </header>

      <section className="urgent-po-preview-controls card-like" data-testid="urgent-preview-controls">
        <label>
          Skenario
          <select
            value={scenario}
            onChange={(e) => applyScenario(e.target.value)}
            data-testid="urgent-preview-scenario"
          >
            {SCENARIOS.map((s) => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </select>
        </label>
        <label>
          Peran preview (dummy)
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            data-testid="urgent-preview-role"
          >
            <option value="requester">Pemohon (urgent.request)</option>
            <option value="approver">Approver (urgent.approve)</option>
            <option value="viewer">Viewer</option>
          </select>
        </label>
        <button type="button" className="ghost sm" onClick={() => applyScenario(scenario)} data-testid="urgent-preview-reset">
          Reset skenario
        </button>
      </section>

      {chrome}

      {toast ? <div className="toast" data-testid="urgent-preview-toast">{toast}</div> : null}

      <UrgentRequestSubmitModal
        open={submitOpen}
        poNumber={order.po_number}
        busy={busy}
        onCancel={() => setSubmitOpen(false)}
        onSubmit={onSubmitRequest}
      />
      <UrgentRequestPendingModal
        open={pendingOpen}
        request={request}
        onClose={() => setPendingOpen(false)}
      />
      <UrgentRequestReviewModal
        open={reviewOpen}
        request={request}
        busy={busy}
        onCancel={() => setReviewOpen(false)}
        onConfirm={onConfirmDecision}
      />
      <UrgentRequestResultModal
        open={resultOpen}
        request={request}
        onClose={() => setResultOpen(false)}
      />
    </div>
  );
}
