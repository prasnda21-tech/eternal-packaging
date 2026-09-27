import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertCircle, FileText } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { currentStepLabel, displayValue, statusLabel } from "../lib/employeeAdmin";
import { formatDisplayDateTime } from "../lib/constants";
import { FormSection, FormField } from "../components/form-standard";
import InAppDocumentViewer from "../components/InAppDocumentViewer";

function fileIsPdf(value) {
  const type = String(value?.content_type || "").toLowerCase();
  const name = String(value?.name || "").toLowerCase();
  return type.includes("pdf") || name.endsWith(".pdf");
}

function errDetail(ex) {
  return ex?.response?.data?.detail || ex?.message || "Gagal memuat";
}

export default function EmployeeAdminDetail() {
  const { id } = useParams();
  const { hasPerm } = useAuth();
  const [row, setRow] = useState(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [pdfFile, setPdfFile] = useState(null);

  const load = async () => {
    setErr("");
    try {
      const res = await api.employeeAdminGetRequest(id);
      setRow(res.request);
    } catch (ex) {
      setErr(errDetail(ex));
    }
  };

  useEffect(() => { load(); }, [id]);

  const loadPdfFile = useCallback(({ signal } = {}) => {
    if (!row?.id || !pdfFile?.id) {
      const error = new Error("missing");
      error.status = 404;
      return Promise.reject(error);
    }
    return api.employeeAdminFileBlob(row.id, pdfFile.id, { signal });
  }, [row?.id, pdfFile?.id]);

  const decide = async (action) => {
    setBusy(true);
    setErr("");
    try {
      const res = await api.employeeAdminDecide(id, { action, note });
      setRow(res.request);
      setNote("");
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  if (err && !row) return <div className="error" data-testid="ea-detail-error"><AlertCircle size={14} /> {err}</div>;
  if (!row) return <p className="muted">Memuat…</p>;

  const fields = (row.form_snapshot?.fields || []).filter((f) => f.active !== false);
  const waiting = row.status === "DIAJUKAN" || row.status === "MENUNGGU_PERSETUJUAN";
  const canAct = waiting && hasPerm("employee_admin.approve");
  const canDoc = row.status === "DISETUJUI" && row.document?.generated;

  return (
    <div className="ea-page" data-testid="ea-detail-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Administrasi Karyawan</p>
          <h1>{row.request_number}</h1>
          <p className="subhead">{row.type_name} · {statusLabel(row.status)} · {currentStepLabel(row)}</p>
        </div>
        <Link to="/administrasi-karyawan/pengajuan-saya" className="ghost">Kembali</Link>
      </div>
      {err && <div className="error"><AlertCircle size={14} /> {err}</div>}
      <div className="ea-detail-grid">
        <section className="settings-card" data-testid="ea-detail-employee">
          <h3>Karyawan</h3>
          <p><strong>{row.employee?.name}</strong></p>
          <p className="muted small">{row.employee?.employee_id} · {row.employee?.role} · {row.employee?.email}</p>
        </section>
        <section className="settings-card" data-testid="ea-submitted-fields">
          <h3>Isi pengajuan</h3>
          <dl className="ea-dl">
            {fields.map((field) => (
              <div key={field.key}>
                <dt>{field.label}</dt>
                <dd>
                  {field.type === "file" && row.values?.[field.key]?.id ? (
                    fileIsPdf(row.values[field.key]) ? (
                      <button
                        type="button"
                        className="linkish"
                        data-testid={`ea-file-open-${row.values[field.key].id}`}
                        onClick={() => setPdfFile(row.values[field.key])}
                      >
                        {displayValue(field, row.values[field.key])}
                      </button>
                    ) : (
                      <a href={api.employeeAdminFileUrl(row.id, row.values[field.key].id)} target="_blank" rel="noreferrer">{displayValue(field, row.values[field.key])}</a>
                    )
                  ) : displayValue(field, row.values?.[field.key])}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      </div>
      <section className="settings-card" data-testid="ea-approval-timeline">
        <h3>Jejak persetujuan</h3>
        <ol className="ea-timeline">
          {(row.approval_history || []).map((h) => (
            <li key={h.id} data-testid={`ea-hist-${h.id}`}>
              <strong>{h.action === "approved" ? "Disetujui" : "Ditolak"}</strong> · Level {h.level} · {h.actor?.name} · {formatDisplayDateTime(h.timestamp)}
              {h.note ? <div className="muted small">{h.note}</div> : null}
            </li>
          ))}
          {!row.approval_history?.length && <li className="muted">Belum ada keputusan.</li>}
        </ol>
      </section>
      {canAct && (
        <FormSection
          id="ea-decide-section"
          title={`Keputusan Level ${row.current_step_level}`}
          description="Berikan persetujuan atau penolakan terhadap pengajuan ini."
          status="incomplete"
          className="settings-card"
        >
          <div data-testid="ea-decide-box">
            <FormField id="ea-decide-note-field" label="Catatan Keputusan" helpText="Opsional: tambahkan alasan persetujuan atau penolakan.">
              <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Catatan (opsional)" data-testid="ea-decide-note" />
            </FormField>
            <div className="ea-actions mt-3">
              <button type="button" className="primary" disabled={busy} onClick={() => decide("approve")} data-testid="ea-approve">Setujui</button>
              <button type="button" className="danger" disabled={busy} onClick={() => decide("reject")} data-testid="ea-reject">Tolak</button>
            </div>
          </div>
        </FormSection>
      )}
      {canDoc && (
        <a className="primary" href={api.employeeAdminDocumentUrl(row.id)} target="_blank" rel="noreferrer" data-testid="ea-pdf-link">
          <FileText size={14} /> Unduh dokumen resmi {row.document?.document_number || ""}
        </a>
      )}
      {pdfFile && (
        <InAppDocumentViewer
          documentKey={pdfFile.id}
          title={pdfFile.name || "Lampiran"}
          fileName={pdfFile.name || "lampiran.pdf"}
          contentType={pdfFile.content_type || "application/pdf"}
          allowDownload={false}
          loadFile={loadPdfFile}
          onClose={() => setPdfFile(null)}
          testId="ea-file-viewer"
        />
      )}
    </div>
  );
}
