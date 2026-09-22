import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  Check,
  CheckCircle,
  Paperclip,
  Play,
  RotateCcw,
  Send,
  TrendingUp,
  XCircle,
} from "lucide-react";
import api from "../lib/api";
import { formatDisplayDate, formatDisplayDateTime } from "../lib/constants";
import {
  DEADLINE_LABELS,
  PRIORITY_LABELS,
  STATUS_LABELS,
  TYPE_LABELS,
  formatPercent,
  formatSpkError,
  previewOverall,
} from "../lib/spk";
import "./spk.css";

const TABS = [
  { id: "summary", label: "Ringkasan" },
  { id: "progress", label: "Target & Progress" },
  { id: "activity", label: "Aktivitas" },
  { id: "files", label: "Lampiran" },
];

export default function SpkDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [order, setOrder] = useState(null);
  const [tab, setTab] = useState("progress");
  const [error, setError] = useState("");
  const [drawer, setDrawer] = useState(null);
  const [reason, setReason] = useState("");
  const [askReason, setAskReason] = useState("");

  useEffect(() => {
    let cancelled = false;
    setError("");
    api.getSpk(id)
      .then((data) => { if (!cancelled) setOrder(data.order); })
      .catch((err) => { if (!cancelled) setError(formatSpkError(err)); });
    return () => { cancelled = true; };
  }, [id]);

  const act = async (fn) => {
    setError("");
    try {
      const data = await fn();
      setOrder(data.order || data);
      setAskReason("");
      setReason("");
    } catch (err) {
      setError(formatSpkError(err));
    }
  };

  const caps = order?.capabilities || {};

  if (!order) {
    return (
      <div className="spk-page" data-testid="spk-detail">
        {error ? <div className="spk-msg is-error">{error}</div> : <div className="spk-empty">Memuat SPK…</div>}
      </div>
    );
  }

  return (
    <div className="spk-page" data-testid="spk-detail">
      <header className="spk-detail-head">
        <div>
          <div className="spk-kicker">{order.spk_number}</div>
          <div className="spk-title-row">
            <h1 data-testid="spk-detail-title">{order.title}</h1>
            <span className={`spk-pill spk-pri-${order.priority}`} data-testid="spk-detail-priority">{PRIORITY_LABELS[order.priority] || order.priority}</span>
            <span className={`spk-pill spk-st-${order.workflow_status}`} data-testid="spk-detail-status">{STATUS_LABELS[order.workflow_status] || order.workflow_status}</span>
            {order.deadline_state && (
              <span className={`spk-pill spk-dead-${order.deadline_state}`} data-testid="spk-detail-deadline">
                {DEADLINE_LABELS[order.deadline_state]} · {formatDisplayDate(order.deadline)}
              </span>
            )}
          </div>
        </div>
        <div className="spk-meta">
          <div>
            <div className="spk-section-label">Progress</div>
            <div className="spk-overall" data-testid="spk-overall">{formatPercent(order.progress_percent)}%</div>
          </div>
          <div className="spk-bar" style={{ width: 88 }}><span style={{ width: `${Math.min(100, Number(order.progress_percent) || 0)}%` }} /></div>
        </div>
      </header>

      <div className="spk-actions" data-testid="spk-detail-actions">
        {caps.acknowledge && (
          <button type="button" className="spk-btn spk-btn-ack" data-testid="spk-ack" onClick={() => act(() => api.acknowledgeSpk(order.id, { doc_version: order.doc_version }))}>
            <Check size={14} /> Terima SPK
          </button>
        )}
        {caps.start && (
          <button type="button" className="spk-btn spk-btn-start" data-testid="spk-start" onClick={() => act(() => api.startSpk(order.id, { doc_version: order.doc_version }))}>
            <Play size={14} /> Mulai Kerjakan
          </button>
        )}
        {caps.request_verification && (
          <button type="button" className="spk-btn spk-btn-verify" data-testid="spk-request-verify" onClick={() => act(() => api.requestSpkVerification(order.id, { doc_version: order.doc_version }))}>
            <Send size={14} /> Ajukan Verifikasi
          </button>
        )}
        {caps.verify && (
          <button type="button" className="spk-btn spk-btn-approve" data-testid="spk-approve" onClick={() => act(() => api.verifySpk(order.id, { decision: "approve", doc_version: order.doc_version }))}>
            <CheckCircle size={14} /> Terima Hasil
          </button>
        )}
        {caps.verify && (
          <button type="button" className="spk-btn spk-btn-revision" data-testid="spk-revision" onClick={() => setAskReason("revision")}>
            <RotateCcw size={14} /> Minta Revisi
          </button>
        )}
        {caps.cancel && (
          <button type="button" className="spk-btn spk-btn-cancel" data-testid="spk-cancel" onClick={() => setAskReason("cancel")}>
            <XCircle size={14} /> Batalkan SPK
          </button>
        )}
        <button type="button" className="spk-btn spk-btn-ghost" onClick={() => navigate("/spk")}>Kembali</button>
      </div>

      {askReason && (
        <form
          className="spk-actions"
          data-testid="spk-reason-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (askReason === "cancel") {
              act(() => api.cancelSpk(order.id, { reason, doc_version: order.doc_version }));
            } else {
              act(() => api.verifySpk(order.id, { decision: "revision", reason, doc_version: order.doc_version }));
            }
          }}
        >
          <input data-testid="spk-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={askReason === "cancel" ? "Alasan pembatalan" : "Alasan revisi"} />
          <button type="submit" className={askReason === "cancel" ? "spk-btn spk-btn-cancel" : "spk-btn spk-btn-revision"}>
            <AlertTriangle size={14} /> Simpan
          </button>
        </form>
      )}
      {order.revision_reason && <div className="spk-msg" data-testid="spk-revision-note">Revisi: {order.revision_reason}</div>}
      {error && <div className="spk-msg is-error" data-testid="spk-detail-error">{error}</div>}

      <div className="spk-tabs-line" data-testid="spk-detail-tabs">
        {TABS.map((item) => (
          <button key={item.id} type="button" className={"spk-tab" + (tab === item.id ? " is-on" : "")} data-testid={`spk-detail-tab-${item.id}`} onClick={() => setTab(item.id)}>
            {item.label}
          </button>
        ))}
      </div>

      <div className="spk-panel-scroll">
        {tab === "summary" && (
          <div data-testid="spk-summary-panel">
            <p>{order.instruction || "Tidak ada instruksi."}</p>
            <p>Dari {order.source_division_label} ke {order.target_division_label}. PIC: {order.pic_label || "-"}.</p>
            <p>Mulai {formatDisplayDate(order.start_date)} · Deadline {formatDisplayDate(order.deadline)}</p>
            {order.branch_name && <p>Cabang {order.branch_name}</p>}
            {order.cancellation_reason && <p>Dibatalkan: {order.cancellation_reason}</p>}
          </div>
        )}
        {tab === "progress" && (
          <div data-testid="spk-progress-panel">
            {(order.milestones || []).map((row) => (
              <article key={row.id} className="spk-milestone" data-testid={`spk-milestone-${row.id}`}>
                <header>
                  <div>
                    <h3>{row.title}</h3>
                    <div className="spk-sub">
                      {TYPE_LABELS[row.type] || row.type}
                      {" · "}
                      {row.type === "QUANTITY" ? `${formatPercent(row.actual_value)} / ${formatPercent(row.target_value)} ${row.unit}` : row.status}
                      {" · Bobot "}{formatPercent(row.weight)}%
                      {" · Bukti "}{row.evidence_count || 0}
                    </div>
                  </div>
                  {caps.update_progress && (
                    <button type="button" className="spk-btn spk-btn-progress" data-testid={`spk-update-${row.id}`} onClick={() => setDrawer(row)}>
                      <TrendingUp size={14} /> Update Progress
                    </button>
                  )}
                </header>
                <div className="spk-bar"><span style={{ width: `${Math.min(100, Number(row.progress_percent) || 0)}%` }} /></div>
                <div className="spk-bar-label">{formatPercent(row.progress_percent)}%</div>
              </article>
            ))}
          </div>
        )}
        {tab === "activity" && (
          <div className="spk-timeline" data-testid="spk-activity">
            {(order.events || []).map((event) => (
              <article key={event.id} className="spk-event" data-testid="spk-event">
                <time>{formatDisplayDateTime(event.created_at)}</time>
                <div><strong>{event.actor_name || "Sistem"}</strong> · {event.event_type}</div>
                {event.milestone_title && (
                  <p>{event.milestone_title}: {event.previous_actual} → {event.new_actual}. Overall {event.overall_before}% → {event.overall_after}%. Bukti: {event.evidence_count}</p>
                )}
                {event.note && <p>{event.note}</p>}
              </article>
            ))}
            {(order.events || []).length === 0 && <div className="spk-empty">Belum ada aktivitas.</div>}
          </div>
        )}
        {tab === "files" && (
          <div data-testid="spk-files">
            {(order.attachments || []).length === 0 && <div className="spk-empty">Belum ada lampiran.</div>}
            {(order.attachments || []).map((file) => (
              <div key={`${file.kind}-${file.id || file.url}`} className="spk-event">
                <Paperclip size={12} /> {file.name || file.label || file.url}
                {file.url && <div><a href={file.url} target="_blank" rel="noreferrer">{file.url}</a></div>}
              </div>
            ))}
          </div>
        )}
      </div>

      {drawer && (
        <ProgressDrawer
          order={order}
          milestone={drawer}
          onClose={() => setDrawer(null)}
          onSaved={(next) => { setOrder(next); setDrawer(null); }}
        />
      )}
    </div>
  );
}

function ProgressDrawer({ order, milestone, onClose, onSaved }) {
  const [actual, setActual] = useState(String(milestone.actual_value ?? ""));
  const [note, setNote] = useState("");
  const [link, setLink] = useState("");
  const [files, setFiles] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const complete = milestone.type === "CHECKLIST";
  const approve = milestone.type === "APPROVAL";

  const preview = useMemo(() => {
    if (milestone.type === "QUANTITY") {
      return previewOverall(order.milestones, milestone.id, { actual_value: actual, type: "QUANTITY", target_value: milestone.target_value, weight: milestone.weight });
    }
    return previewOverall(order.milestones, milestone.id, { status: "COMPLETED", type: milestone.type, weight: milestone.weight });
  }, [actual, milestone, order.milestones]);

  const upload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setError("");
    try {
      const data = await api.uploadSpkFile(file);
      setFiles((rows) => [...rows, data.file]);
    } catch (err) {
      setError(formatSpkError(err));
    }
  };

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const data = await api.updateSpkProgress(order.id, {
        doc_version: order.doc_version,
        milestone_id: milestone.id,
        new_actual: milestone.type === "QUANTITY" ? actual : undefined,
        complete,
        approve,
        note,
        links: link ? [{ url: link, label: link }] : [],
        file_ids: files.map((file) => file.id),
        idempotency_key: (globalThis.crypto?.randomUUID?.() || `spk-${Date.now()}`),
      });
      onSaved(data.order);
    } catch (err) {
      setError(formatSpkError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="spk-drawer-back" data-testid="spk-progress-drawer" onClick={onClose}>
      <div className="spk-drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Update progress">
        <div className="spk-kicker">Update Progress</div>
        <h2 style={{ margin: 0, fontSize: 16 }}>{milestone.title}</h2>
        <div className="spk-field">
          <label>Aktual sebelumnya</label>
          <div className="spk-readonly" data-testid="spk-prev-actual">{formatPercent(milestone.actual_value)} {milestone.unit}</div>
        </div>
        {milestone.type === "QUANTITY" && (
          <div className="spk-field">
            <label htmlFor="spk-new-actual">Aktual baru</label>
            <input id="spk-new-actual" data-testid="spk-new-actual" value={actual} onChange={(e) => setActual(e.target.value)} />
          </div>
        )}
        <div className="spk-preview" data-testid="spk-progress-preview">
          Milestone {formatPercent(preview.milestone)}% · Overall {formatPercent(preview.before)}% → {formatPercent(preview.after)}%
        </div>
        <div className="spk-field">
          <label htmlFor="spk-note">Catatan</label>
          <textarea id="spk-note" data-testid="spk-note" value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <div className="spk-field">
          <label htmlFor="spk-link">Tautan bukti</label>
          <input id="spk-link" data-testid="spk-link" value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://" />
        </div>
        <label className="spk-btn spk-btn-evidence" data-testid="spk-upload-label">
          <Paperclip size={14} /> Tambah Bukti
          <input data-testid="spk-upload" type="file" hidden onChange={upload} />
        </label>
        <div>{files.map((file) => <span key={file.id} className="spk-file-chip">{file.name}</span>)}</div>
        {error && <div className="spk-msg is-error">{error}</div>}
        <button type="button" className="spk-btn spk-btn-progress" data-testid="spk-save-progress" disabled={busy} onClick={save}>
          <TrendingUp size={14} /> Simpan Progress
        </button>
      </div>
    </div>
  );
}
