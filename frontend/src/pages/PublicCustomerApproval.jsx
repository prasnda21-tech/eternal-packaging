import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { CheckCircle2, XCircle, AlertTriangle, Clock } from "lucide-react";
import api from "../lib/api";
import { formatApiError } from "../lib/errors";
import SignaturePad from "../components/SignaturePad";

const STATUS_LABEL = {
  active: "Menunggu Approval Anda",
  approved: "Sudah Approved",
  rejected: "Revisi Diminta",
  expired: "Link Kedaluwarsa",
  revoked: "Link Sudah Dicabut",
  invalidated: "Design Sudah Diperbarui",
};

function fmt(d) {
  if (!d) return "-";
  try {
    const dt = new Date(d);
    return dt.toLocaleString("id-ID", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return String(d); }
}

function useCountdown(iso) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(t);
  }, []);
  if (!iso) return null;
  const target = new Date(iso).getTime();
  const ms = target - now;
  if (ms <= 0) return "kedaluwarsa";
  const hours = Math.floor(ms / 3600_000);
  const minutes = Math.floor((ms % 3600_000) / 60_000);
  if (hours >= 1) return `~${hours} jam ${minutes} menit lagi`;
  return `~${minutes} menit lagi`;
}

export default function PublicCustomerApproval() {
  const { token } = useParams();
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState("");
  const [actionErr, setActionErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState(null);
  const [signerName, setSignerName] = useState("");
  const [note, setNote] = useState("");
  const [signature, setSignature] = useState(null);

  const load = () => {
    setLoading(true);
    api.publicCustomerApprovalView(token).then((d) => {
      setView(d.view);
    }).catch((e) => {
      setLoadErr(formatApiError(e, "Link tidak dapat dibuka"));
    }).finally(() => setLoading(false));
  };

  useEffect(load, [token]);

  const countdown = useCountdown(view?.expires_at);
  const settings = view?.settings || {};
  const canInteract = view?.status === "active";
  const showApprove = canInteract && settings.allow_approve !== false;
  const showReject = canInteract && settings.allow_reject !== false;
  const sigEnabled = settings.signature_enabled !== false;

  const submit = async (action) => {
    setActionErr("");
    const trimmed = signerName.trim();
    if (!trimmed) {
      setActionErr("Nama penandatangan wajib diisi.");
      return;
    }
    const sigRequired = (
      action === "approve" && settings.signature_required_approve
    ) || (
      action === "reject" && settings.signature_required_reject
    );
    if (sigRequired && sigEnabled && !signature) {
      setActionErr("Tanda tangan wajib diisi.");
      return;
    }
    if (action === "reject" && settings.reject_reason_required && !note.trim()) {
      setActionErr("Alasan revisi wajib diisi.");
      return;
    }
    setBusy(true);
    try {
      const r = await api.publicCustomerApprovalSubmit(token, {
        action,
        signer_name: trimmed,
        signature_data_url: signature || null,
        note: note.trim() || null,
      });
      setView(r.view);
    } catch (e) {
      setActionErr(formatApiError(e, "Gagal mengirim approval"));
      // Refresh state (kadang link jadi expired/invalidated di server)
      load();
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <div className="public-approval-shell"><div className="loading">Memuat...</div></div>;
  }
  if (loadErr) {
    return (
      <div className="public-approval-shell">
        <div className="public-approval-card" data-testid="public-approval-load-error">
          <div className="brand"><span className="brand-mark">EP</span> Eternal Packaging</div>
          <h1>Link Tidak Tersedia</h1>
          <p className="muted">{loadErr}</p>
          <p className="muted small">Silakan hubungi Eternal Packaging untuk meminta link baru.</p>
        </div>
      </div>
    );
  }
  if (!view) return null;

  const isCompleted = view.status === "approved" || view.status === "rejected";
  const isExpired = view.status === "expired";
  const isRevoked = view.status === "revoked";
  const isInvalidated = view.status === "invalidated";

  return (
    <div className="public-approval-shell">
      <div className="public-approval-card" data-testid="public-approval-card">
        <div className="brand">
          <span className="brand-mark">EP</span>
          <div>
            <div className="brand-name">Eternal Packaging</div>
            <div className="brand-sub">Approval Design Customer</div>
          </div>
        </div>

        <div className={`status-chip status-${view.status}`} data-testid="public-approval-status">
          {view.status === "active" && <Clock size={14} />}
          {view.status === "approved" && <CheckCircle2 size={14} />}
          {view.status === "rejected" && <XCircle size={14} />}
          {(isExpired || isRevoked || isInvalidated) && <AlertTriangle size={14} />}
          <span>{STATUS_LABEL[view.status] || view.status}</span>
        </div>

        <div className="po-summary">
          <div><span className="lbl">No. PO</span><span data-testid="public-approval-po-number">{view.po_number}</span></div>
          <div><span className="lbl">Customer</span><span>{view.customer || "-"}</span></div>
          {view.outlet && <div><span className="lbl">Outlet</span><span>{view.outlet}</span></div>}
          {view.generated_at && <div><span className="lbl">Link dibuat</span><span>{fmt(view.generated_at)}</span></div>}
          {view.expires_at && (
            <div>
              <span className="lbl">Berlaku sampai</span>
              <span>
                {fmt(view.expires_at)}
                {canInteract && countdown ? <span className="muted small"> · {countdown}</span> : null}
              </span>
            </div>
          )}
        </div>

        {view.line_items?.length > 0 && (
          <div className="items-preview" data-testid="public-approval-items">
            <div className="items-head">Rincian barang</div>
            <ul>
              {view.line_items.map((li, i) => (
                <li key={i}>
                  <span>{li.name}</span>
                  <span className="muted">{li.color || "-"} · {(li.quantity || 0).toLocaleString("id-ID")} {li.unit || ""}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {view.design_file && (
          <div className="design-preview" data-testid="public-approval-design-file">
            <div className="items-head">File design</div>
            {(() => {
              const ct = (view.design_file.content_type || "").toLowerCase();
              const url = view.design_file.data_url;
              const isImg = ct.startsWith("image/") || (url && url.startsWith("data:image/"));
              const isPdf = ct === "application/pdf" || (url && url.startsWith("data:application/pdf"));
              if (isImg && url) return <img src={url} alt={view.design_file.name} data-testid="public-approval-design-image" />;
              if (isPdf && url) {
                return (
                  <div data-testid="public-approval-design-pdf">
                    <object data={url} type="application/pdf" width="100%" style={{ height: 480, background: "#fff", border: "1px solid #e6dfc7", borderRadius: 8 }}>
                      <p className="muted small">Browser Anda tidak mendukung preview PDF inline.</p>
                    </object>
                    <div style={{ marginTop: 8 }}>
                      <a href={url} target="_blank" rel="noopener noreferrer" download={view.design_file.name} className="ghost sm" data-testid="public-approval-design-pdf-open">
                        Buka Design PDF di tab baru
                      </a>
                    </div>
                  </div>
                );
              }
              return <p className="muted">{view.design_file.name}</p>;
            })()}
            <p className="muted small">{view.design_file.name}</p>
          </div>
        )}

        {isExpired && (
          <div className="alert warn" data-testid="public-approval-expired">
            <AlertTriangle size={16} />
            <div>Link approval sudah kedaluwarsa. Silakan meminta link baru dari Eternal Packaging.</div>
          </div>
        )}
        {isRevoked && (
          <div className="alert warn" data-testid="public-approval-revoked">
            <AlertTriangle size={16} />
            <div>Link approval sudah dinonaktifkan oleh Eternal Packaging.</div>
          </div>
        )}
        {isInvalidated && (
          <div className="alert warn" data-testid="public-approval-invalidated">
            <AlertTriangle size={16} />
            <div>Design telah diperbarui oleh Eternal Packaging. Silakan tunggu link approval versi terbaru.</div>
          </div>
        )}

        {isCompleted && (
          <div className={`alert ${view.status === "approved" ? "ok" : "err"}`} data-testid="public-approval-completed">
            {view.status === "approved" ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
            <div>
              <b>Approval sudah dikirim {view.status === "approved" ? "(Approved)" : "(Revisi Diminta)"}.</b>
              <div className="muted small">
                Oleh: {view.signer_name || "-"} · {fmt(view.responded_at)}
              </div>
              {view.status === "rejected" && view.reject_reason && (
                <div className="muted small">Alasan: {view.reject_reason}</div>
              )}
              {view.status === "approved" && view.customer_note && (
                <div className="muted small">Catatan: {view.customer_note}</div>
              )}
            </div>
          </div>
        )}

        {canInteract && (
          <div className="approval-form" data-testid="public-approval-form">
            <label className="pfld">
              <span>Nama Penandatangan <span className="req">*</span></span>
              <input
                type="text"
                value={signerName}
                onChange={(e) => setSignerName(e.target.value)}
                placeholder="Nama lengkap"
                disabled={busy}
                data-testid="public-approval-signer"
              />
            </label>

            <label className="pfld">
              <span>Catatan {settings.reject_reason_required ? <span className="muted small">(wajib untuk Revisi)</span> : null}</span>
              <textarea
                rows={2}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Opsional untuk Approve, wajib untuk Revisi."
                disabled={busy}
                data-testid="public-approval-note"
              />
            </label>

            {sigEnabled && (
              <div className="pfld">
                <span>Tanda Tangan {settings.signature_required_approve && <span className="muted small">(wajib untuk Approve)</span>}</span>
                <SignaturePad onChange={setSignature} disabled={busy} />
              </div>
            )}

            {actionErr && (
              <div className="alert err" role="alert" data-testid="public-approval-action-error">
                <XCircle size={14} />
                <div>{actionErr}</div>
              </div>
            )}

            <div className="approval-actions">
              {showApprove && (
                <button
                  className="primary big"
                  onClick={() => submit("approve")}
                  disabled={busy}
                  data-testid="public-approval-approve-button"
                >
                  <CheckCircle2 size={16} /> Approve Design
                </button>
              )}
              {showReject && (
                <button
                  className="ghost big"
                  onClick={() => submit("reject")}
                  disabled={busy}
                  data-testid="public-approval-revisi-button"
                >
                  <XCircle size={16} /> Revisi
                </button>
              )}
            </div>
          </div>
        )}

        <div className="foot">
          <p className="muted small">Halaman ini merupakan link resmi dari Eternal Packaging. Jangan bagikan link Anda ke pihak lain.</p>
        </div>
      </div>
    </div>
  );
}
