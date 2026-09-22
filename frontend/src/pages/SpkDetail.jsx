import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle,
  Check,
  CheckCircle,
  Paperclip,
  Pencil,
  Play,
  RotateCcw,
  Send,
  Trash2,
  TrendingUp,
  Undo2,
  XCircle,
} from "lucide-react";
import api from "../lib/api";
import { formatDisplayDate, formatDisplayDateTime } from "../lib/constants";
import {
  DEADLINE_LABELS,
  EVENT_LABELS,
  PRIORITY_LABELS,
  STATUS_LABELS,
  TYPE_LABELS,
  exceedsTarget,
  formatPercent,
  formatSpkError,
  newIdempotencyKey,
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
  const location = useLocation();
  const [order, setOrder] = useState(null);
  const [tab, setTab] = useState("progress");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState(location.state?.notice || "");
  const [drawer, setDrawer] = useState(null);
  const [noteOpen, setNoteOpen] = useState(false);
  const [correctOpen, setCorrectOpen] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const publishKey = useRef("");

  const reload = () => api.getSpk(id).then((data) => setOrder(data.order));

  useEffect(() => {
    publishKey.current = "";
    let cancelled = false;
    setError("");
    api.getSpk(id)
      .then((data) => { if (!cancelled) setOrder(data.order); })
      .catch((err) => { if (!cancelled) setError(formatSpkError(err)); });
    return () => { cancelled = true; };
  }, [id]);

  const act = async (fn, success) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const data = await fn();
      if (!data?.skipReload) {
        const fresh = await api.getSpk(id);
        setOrder(fresh.order);
      }
      if (success) setNotice(success);
      setConfirm(null);
    } catch (err) {
      setError(formatSpkError(err));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  const caps = order?.capabilities || {};
  const version = order?.doc_version;

  const ask = (spec) => {
    if (busy || lock.current) return;
    setConfirm(spec);
  };

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
        {caps.edit_draft && (
          <button type="button" className="spk-btn spk-btn-draft" data-testid="spk-edit-draft" disabled={busy} onClick={() => navigate(`/spk/${order.id}/ubah`)}>
            <Pencil size={14} /> Edit {order.workflow_status === "RETURNED" ? "SPK" : "Draft"}
          </button>
        )}
        {caps.publish && (
          <button type="button" className="spk-btn spk-btn-create" data-testid="spk-publish-draft" disabled={busy} onClick={() => ask({
            title: order.workflow_status === "RETURNED" ? "Terbitkan ulang SPK" : "Terbitkan SPK",
            body: order.workflow_status === "RETURNED"
              ? "SPK memakai dokumen dan nomor yang sama. PIC baru akan menerima notifikasi."
              : "SPK akan diterbitkan dari draft ini.",
            label: order.workflow_status === "RETURNED" ? "Terbitkan ulang" : "Terbitkan",
            testId: "spk-confirm-publish",
            run: () => {
              if (!publishKey.current) publishKey.current = newIdempotencyKey();
              return api.publishSpk(order.id, { doc_version: version, idempotency_key: publishKey.current });
            },
            success: order.workflow_status === "RETURNED" ? "SPK diterbitkan ulang." : "SPK diterbitkan.",
          })}>
            <Send size={14} /> {order.workflow_status === "RETURNED" ? "Terbitkan ulang" : "Terbitkan SPK"}
          </button>
        )}
        {caps.delete_draft && (
          <button type="button" className="spk-btn spk-btn-cancel" data-testid="spk-delete-draft" disabled={busy} onClick={() => ask({
            title: "Hapus draft",
            body: "Draft ini akan dihapus dan tidak dapat dibuka kembali.",
            label: "Hapus Draft",
            testId: "spk-confirm-delete",
            run: async () => {
              await api.deleteSpkDraft(order.id);
              navigate("/spk", { state: { notice: "Draft dihapus." } });
              return { skipReload: true };
            },
            success: "",
          })}>
            <Trash2 size={14} /> Hapus Draft
          </button>
        )}
        {caps.acknowledge && (
          <button type="button" className="spk-btn spk-btn-ack" data-testid="spk-ack" disabled={busy} onClick={() => ask({
            title: "Terima SPK",
            body: "Anda menerima tugas ini sebagai PIC.",
            label: "Terima SPK",
            testId: "spk-confirm-ack",
            run: () => api.acknowledgeSpk(order.id, { doc_version: version }),
            success: "SPK diterima.",
          })}>
            <Check size={14} /> Terima SPK
          </button>
        )}
        {caps.return_spk && (
          <button type="button" className="spk-btn spk-btn-revision" data-testid="spk-return" disabled={busy} onClick={() => ask({
            title: "Kembalikan SPK",
            body: "Pengembalian oleh salah satu PIC mengembalikan seluruh SPK kepada pembuat, termasuk tugas PIC lain. Setelah dikembalikan, pekerjaan belum dianggap dimulai.",
            label: "Kembalikan SPK",
            reason: true,
            reasonLabel: "Alasan pengembalian",
            min: 5,
            testId: "spk-confirm-return",
            run: (reason) => api.returnSpk(order.id, { doc_version: version, reason }),
            success: "SPK dikembalikan kepada pembuat.",
          })}>
            <Undo2 size={14} /> Kembalikan SPK
          </button>
        )}
        {caps.start && (
          <button type="button" className="spk-btn spk-btn-start" data-testid="spk-start" disabled={busy} onClick={() => ask({
            title: "Mulai kerjakan",
            body: "Setelah dimulai, SPK tidak dapat dikembalikan. Gunakan catatan kendala atau revisi bila ada hambatan.",
            label: "Mulai Kerjakan",
            testId: "spk-confirm-start",
            run: () => api.startSpk(order.id, { doc_version: version }),
            success: "SPK mulai dikerjakan.",
          })}>
            <Play size={14} /> Mulai Kerjakan
          </button>
        )}
        {caps.add_note && (
          <button type="button" className="spk-btn spk-btn-ghost" data-testid="spk-add-note" disabled={busy} onClick={() => setNoteOpen(true)}>
            Tambah Catatan
          </button>
        )}
        {caps.request_verification && (
          <button type="button" className="spk-btn spk-btn-verify" data-testid="spk-request-verify" disabled={busy} onClick={() => ask({
            title: "Ajukan verifikasi",
            body: "Seluruh milestone yang siap akan diajukan. Progress terkunci sampai verifikator meminta revisi atau menerima hasil.",
            label: "Ajukan Verifikasi",
            testId: "spk-confirm-verify-request",
            run: () => api.requestSpkVerification(order.id, { doc_version: version }),
            success: "SPK diajukan untuk verifikasi.",
          })}>
            <Send size={14} /> Ajukan Verifikasi
          </button>
        )}
        {caps.verify && (
          <button type="button" className="spk-btn spk-btn-approve" data-testid="spk-approve" disabled={busy} onClick={() => ask({
            title: "Terima hasil",
            body: "SPK akan ditandai selesai.",
            label: "Terima Hasil",
            testId: "spk-confirm-approve",
            run: () => api.verifySpk(order.id, { decision: "approve", doc_version: version }),
            success: "Hasil diterima. SPK selesai.",
          })}>
            <CheckCircle size={14} /> Terima Hasil
          </button>
        )}
        {caps.verify && (
          <button type="button" className="spk-btn spk-btn-revision" data-testid="spk-revision" disabled={busy} onClick={() => ask({
            title: "Minta revisi",
            body: "PIC dapat memperbaiki pekerjaan setelah revisi diminta.",
            label: "Minta Revisi",
            reason: true,
            reasonLabel: "Alasan revisi",
            min: 3,
            testId: "spk-confirm-revision",
            run: (reason) => api.verifySpk(order.id, { decision: "revision", reason, doc_version: version }),
            success: "Revisi diminta.",
          })}>
            <RotateCcw size={14} /> Minta Revisi
          </button>
        )}
        {caps.cancel && (
          <button type="button" className="spk-btn spk-btn-cancel" data-testid="spk-cancel" disabled={busy} onClick={() => ask({
            title: "Batalkan SPK",
            body: "SPK yang dibatalkan tidak dapat dilanjutkan.",
            label: "Batalkan SPK",
            reason: true,
            reasonLabel: "Alasan pembatalan",
            min: 3,
            testId: "spk-confirm-cancel",
            run: (reason) => api.cancelSpk(order.id, { reason, doc_version: version }),
            success: "SPK dibatalkan.",
          })}>
            <XCircle size={14} /> Batalkan SPK
          </button>
        )}
        <button type="button" className="spk-btn spk-btn-ghost" onClick={() => navigate("/spk")}>Kembali</button>
      </div>

      {notice && <div className="spk-msg is-ok" data-testid="spk-detail-notice">{notice}</div>}
      {order.revision_reason && <div className="spk-msg" data-testid="spk-revision-note">Revisi: {order.revision_reason}</div>}
      {order.return_reason && <div className="spk-msg" data-testid="spk-return-note">Dikembalikan: {order.return_reason}</div>}
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
            <p>Dari {order.source_division_label} ke {order.target_division_label}.</p>
            <div data-testid="spk-pic-list">
              PIC: {(order.assigned_pics || []).length
                ? (order.assigned_pics || []).map((pic) => pic.name).join(", ")
                : (order.pic_label || "-")}
            </div>
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
                      {" · Target "}{row.type === "QUANTITY" ? `${formatPercent(row.target_value)} ${row.unit}` : "selesai"}
                      {" · Aktual "}{row.type === "QUANTITY" ? `${formatPercent(row.actual_value)} ${row.unit}` : row.status}
                      {" · Bobot "}{formatPercent(row.weight)}%
                      {" · Bukti "}{row.evidence_count || 0}
                    </div>
                    {exceedsTarget(row) && <div className="spk-exceeds" data-testid={`spk-exceeds-${row.id}`}>Melebihi Target</div>}
                  </div>
                  <div className="spk-actions">
                    {caps.update_progress && (
                      <button type="button" className="spk-btn spk-btn-progress" data-testid={`spk-update-${row.id}`} disabled={busy} onClick={() => setDrawer(row)}>
                        <TrendingUp size={14} /> Update Progress
                      </button>
                    )}
                    {caps.add_evidence && (
                      <button type="button" className="spk-btn spk-btn-evidence" data-testid={`spk-evidence-${row.id}`} disabled={busy} onClick={() => setDrawer({ ...row, evidenceOnly: true })}>
                        <Paperclip size={14} /> Tambah Bukti
                      </button>
                    )}
                    {caps.correct && row.type === "QUANTITY" && (
                      <button type="button" className="spk-btn spk-btn-ghost" data-testid={`spk-correct-${row.id}`} disabled={busy} onClick={() => setCorrectOpen(row)}>
                        Koreksi Nilai
                      </button>
                    )}
                  </div>
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
                <div><strong>{event.actor_name || "Sistem"}</strong> · {EVENT_LABELS[event.event_type] || event.event_type}</div>
                {event.milestone_title && (
                  <p>{event.milestone_title}: {event.previous_actual} → {event.new_actual}. Overall {event.overall_before}% → {event.overall_after}%. Bukti: {event.evidence_count}</p>
                )}
                {event.reason && <p>Alasan: {event.reason}</p>}
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
                {file.kind === "file" && file.id && (
                  <button type="button" className="spk-btn spk-btn-ghost" data-testid={`spk-download-${file.id}`} onClick={() => downloadFile(order.id, file, setError)}>
                    Unduh
                  </button>
                )}
                {file.url && <div><a href={file.url} target="_blank" rel="noreferrer">{file.url}</a></div>}
              </div>
            ))}
          </div>
        )}
      </div>

      {confirm && (
        <ConfirmDialog
          spec={confirm}
          busy={busy}
          onClose={() => { if (!busy) setConfirm(null); }}
          onConfirm={(reason) => act(() => confirm.run(reason), confirm.success)}
        />
      )}
      {drawer && (
        <ProgressDrawer
          order={order}
          milestone={drawer}
          evidenceOnly={!!drawer.evidenceOnly}
          onClose={() => setDrawer(null)}
          onSaved={(_next, message) => {
            setDrawer(null);
            reload();
            if (message) setNotice(message);
          }}
          onError={setError}
        />
      )}
      {noteOpen && (
        <NoteDialog
          busy={busy}
          onClose={() => setNoteOpen(false)}
          onSave={(note) => act(
            () => api.addSpkNote(order.id, { doc_version: order.doc_version, note, idempotency_key: newIdempotencyKey() }),
            "Catatan ditambahkan.",
          ).then(() => setNoteOpen(false))}
        />
      )}
      {correctOpen && (
        <CorrectDialog
          milestone={correctOpen}
          busy={busy}
          onClose={() => setCorrectOpen(null)}
          onSave={(body) => act(
            () => api.correctSpkValue(order.id, { ...body, doc_version: order.doc_version, milestone_id: correctOpen.id, idempotency_key: newIdempotencyKey() }),
            "Nilai dikoreksi.",
          ).then(() => setCorrectOpen(null))}
        />
      )}
    </div>
  );
}

function ConfirmDialog({ spec, busy, onClose, onConfirm }) {
  const [reason, setReason] = useState("");
  const min = spec.min || 0;
  const blocked = busy || (spec.reason && reason.trim().length < min);
  return (
    <div className="spk-drawer-back" data-testid="spk-confirm" onClick={onClose}>
      <form className="spk-drawer" data-testid={spec.testId} onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); if (!blocked) onConfirm(reason.trim()); }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>{spec.title}</h2>
        <p>{spec.body}</p>
        {spec.reason && (
          <div className="spk-field">
            <label htmlFor="spk-confirm-reason">{spec.reasonLabel}</label>
            <textarea id="spk-confirm-reason" data-testid="spk-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
        )}
        <div className="spk-actions">
          <button type="button" className="spk-btn spk-btn-ghost" disabled={busy} onClick={onClose}>Batal</button>
          <button type="submit" className="spk-btn spk-btn-create" data-testid="spk-confirm-submit" disabled={blocked}>{spec.label}</button>
        </div>
      </form>
    </div>
  );
}

function NoteDialog({ busy, onClose, onSave }) {
  const [note, setNote] = useState("");
  const lock = useRef(false);
  return (
    <div className="spk-drawer-back" data-testid="spk-note-dialog" onClick={onClose}>
      <form className="spk-drawer" onClick={(e) => e.stopPropagation()} onSubmit={(e) => {
        e.preventDefault();
        if (lock.current || busy || !note.trim()) return;
        lock.current = true;
        Promise.resolve(onSave(note.trim())).finally(() => { lock.current = false; });
      }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Tambah Catatan</h2>
        <p>Catatan tidak mengubah nilai progres.</p>
        <textarea data-testid="spk-note-text" value={note} onChange={(e) => setNote(e.target.value)} />
        <button type="submit" className="spk-btn spk-btn-progress" data-testid="spk-note-save" disabled={busy || !note.trim()}>Simpan Catatan</button>
      </form>
    </div>
  );
}

function CorrectDialog({ milestone, busy, onClose, onSave }) {
  const [actual, setActual] = useState(String(milestone.actual_value ?? ""));
  const [reason, setReason] = useState("");
  const lock = useRef(false);
  const ready = reason.trim().length >= 5 && actual !== "";
  return (
    <div className="spk-drawer-back" data-testid="spk-correct-dialog" onClick={onClose}>
      <form className="spk-drawer" onClick={(e) => e.stopPropagation()} onSubmit={(e) => {
        e.preventDefault();
        if (lock.current || busy || !ready) return;
        lock.current = true;
        Promise.resolve(onSave({ new_actual: actual, reason: reason.trim() })).finally(() => { lock.current = false; });
      }}>
        <h2 style={{ margin: 0, fontSize: 16 }}>Koreksi Nilai</h2>
        <p>Alasan wajib. Riwayat progres sebelumnya tetap tersimpan. Target {formatPercent(milestone.target_value)} {milestone.unit}. Aktual saat ini {formatPercent(milestone.actual_value)} {milestone.unit}.</p>
        <div className="spk-field">
          <label htmlFor="spk-correct-actual">Nilai baru</label>
          <input id="spk-correct-actual" data-testid="spk-correct-actual" value={actual} onChange={(e) => setActual(e.target.value)} />
        </div>
        <div className="spk-field">
          <label htmlFor="spk-correct-reason">Alasan koreksi</label>
          <textarea id="spk-correct-reason" data-testid="spk-correct-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        <button type="submit" className="spk-btn spk-btn-progress" data-testid="spk-correct-save" disabled={busy || !ready}>Simpan Koreksi</button>
      </form>
    </div>
  );
}

async function downloadFile(spkId, file, setError) {
  try {
    const blob = await api.downloadSpkFile(spkId, file.id);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = file.name || "lampiran";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    setError(formatSpkError(err));
  }
}

function ProgressDrawer({ order, milestone, evidenceOnly, onClose, onSaved }) {
  const [actual, setActual] = useState(String(milestone.actual_value ?? ""));
  const [note, setNote] = useState("");
  const [link, setLink] = useState("");
  const [files, setFiles] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const keySlot = useRef(null);
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
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      const body = {
        doc_version: order.doc_version,
        milestone_id: milestone.id,
        note,
        links: link ? [{ url: link, label: link }] : [],
        file_ids: files.map((file) => file.id),
      };
      const payload = evidenceOnly
        ? { ...body, evidence_only: true }
        : {
          ...body,
          new_actual: milestone.type === "QUANTITY" ? actual : undefined,
          complete,
          approve,
        };
      const serial = JSON.stringify(payload);
      if (!keySlot.current || keySlot.current.serial !== serial) {
        keySlot.current = { serial, key: newIdempotencyKey() };
      }
      const data = evidenceOnly
        ? await api.addSpkNote(order.id, { ...payload, idempotency_key: keySlot.current.key })
        : await api.updateSpkProgress(order.id, { ...payload, idempotency_key: keySlot.current.key });
      onSaved(data.order, evidenceOnly ? "Bukti ditambahkan." : "Progress diperbarui.");
    } catch (err) {
      setError(formatSpkError(err));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="spk-drawer-back" data-testid="spk-progress-drawer" onClick={onClose}>
      <div className="spk-drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={evidenceOnly ? "Tambah bukti" : "Update progress"}>
        <div className="spk-kicker">{evidenceOnly ? "Tambah Bukti" : "Update Progress"}</div>
        <h2 style={{ margin: 0, fontSize: 16 }}>{milestone.title}</h2>
        {!evidenceOnly && (
          <div className="spk-field">
            <label>Aktual sebelumnya</label>
            <div className="spk-readonly" data-testid="spk-prev-actual">{formatPercent(milestone.actual_value)} {milestone.unit}</div>
          </div>
        )}
        {!evidenceOnly && milestone.type === "QUANTITY" && (
          <div className="spk-field">
            <label htmlFor="spk-new-actual">Aktual baru</label>
            <input id="spk-new-actual" data-testid="spk-new-actual" value={actual} onChange={(e) => setActual(e.target.value)} />
          </div>
        )}
        {!evidenceOnly && (
          <div className="spk-preview" data-testid="spk-progress-preview">
            Milestone {formatPercent(preview.milestone)}% · Overall {formatPercent(preview.before)}% → {formatPercent(preview.after)}%
            {exceedsTarget({ ...milestone, actual_value: actual }) && <div data-testid="spk-preview-exceeds">Melebihi Target</div>}
          </div>
        )}
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
        {error && <div className="spk-msg is-error" data-testid="spk-drawer-error">{error}</div>}
        <button type="button" className="spk-btn spk-btn-progress" data-testid="spk-save-progress" disabled={busy} onClick={save}>
          <TrendingUp size={14} /> {evidenceOnly ? "Simpan Bukti" : "Simpan Progress"}
        </button>
      </div>
    </div>
  );
}
