import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertCircle, FileText } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { currentStepLabel, displayValue, statusLabel } from "../lib/employeeAdmin";
import { formatDisplayDateTime } from "../lib/constants";

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
                    <a href={api.employeeAdminFileUrl(row.id, row.values[field.key].id)} target="_blank" rel="noreferrer">{displayValue(field, row.values[field.key])}</a>
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
        <section className="settings-card" data-testid="ea-decide-box">
          <h3>Keputusan Level {row.current_step_level}</h3>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Catatan (opsional)" data-testid="ea-decide-note" />
          <div className="ea-actions">
            <button type="button" className="primary" disabled={busy} onClick={() => decide("approve")} data-testid="ea-approve">Setujui</button>
            <button type="button" className="danger" disabled={busy} onClick={() => decide("reject")} data-testid="ea-reject">Tolak</button>
          </div>
        </section>
      )}
      {canDoc && (
        <a className="primary" href={api.employeeAdminDocumentUrl(row.id)} target="_blank" rel="noreferrer" data-testid="ea-pdf-link">
          <FileText size={14} /> Unduh dokumen resmi {row.document?.document_number || ""}
        </a>
      )}
    </div>
  );
}
