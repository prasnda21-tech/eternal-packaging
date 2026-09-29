import { useMemo, useState } from "react";
import { CheckCircle2, XCircle, AlertTriangle, Clock, Copy, Link as LinkIcon, RefreshCw, Shield, Send } from "lucide-react";
import api from "../lib/api";
import { formatApiError } from "../lib/errors";

const STATUS_LABEL = {
  none: "Belum Dibuat",
  active: "Aktif · Menunggu Customer",
  approved: "Approved oleh Customer",
  rejected: "Rejected oleh Customer",
  expired: "Expired",
  revoked: "Revoked",
  invalidated: "Design Diperbarui (Tidak Berlaku)",
};

function fmt(d) {
  if (!d) return "-";
  try {
    return new Date(d).toLocaleString("id-ID", {
      day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch { return String(d); }
}

function remaining(iso) {
  if (!iso) return null;
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "kedaluwarsa";
  const h = Math.floor(ms / 3600_000);
  const m = Math.floor((ms % 3600_000) / 60_000);
  if (h >= 1) return `${h} jam ${m} menit`;
  return `${m} menit`;
}

/**
 * Customer Approval panel — internal user (staff) melihat status link,
 * membuat/regenerate/revoke link, copy URL, copy pesan WhatsApp siap kirim.
 */
export default function CustomerApprovalPanel({ order, canManage, canView, onOrderUpdated }) {
  const ca = order?.customer_approval || null;
  const status = ca?.status || "none";
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [pendingToken, setPendingToken] = useState(""); // freshly-generated plaintext token (shown once)
  const [copyMsg, setCopyMsg] = useState("");

  const publicUrl = useMemo(() => {
    if (!pendingToken) return "";
    return `${window.location.origin}/customer/design-approval/${pendingToken}`;
  }, [pendingToken]);

  const waMessage = useMemo(() => {
    if (!publicUrl) return "";
    const exp = ca?.expires_at ? fmt(ca.expires_at) : "-";
    return `Halo, mohon melakukan approval design untuk PO ${order.po_number} melalui link berikut: ${publicUrl}. Link berlaku sampai ${exp}. Terima kasih — Eternal Packaging.`;
  }, [publicUrl, ca, order]);

  const doCopy = async (text, label) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopyMsg(`${label} disalin`);
      setTimeout(() => setCopyMsg(""), 2200);
    } catch {
      setCopyMsg("Gagal menyalin — pilih manual");
      setTimeout(() => setCopyMsg(""), 2500);
    }
  };

  const doGenerate = async ({ regenerateConfirm = false } = {}) => {
    setErr("");
    if (status === "active" && !regenerateConfirm) {
      const ok = window.confirm("Link lama akan dinonaktifkan. Buat link approval baru?");
      if (!ok) return;
    }
    setBusy(true);
    try {
      const r = await api.generateCustomerApproval(order.id, {});
      setPendingToken(r.token);
      onOrderUpdated?.(r.order);
    } catch (e) {
      setErr(formatApiError(e, "Gagal membuat link approval"));
    } finally {
      setBusy(false);
    }
  };

  const doRevoke = async () => {
    const reason = window.prompt("Alasan revoke (opsional):") ?? "";
    setBusy(true);
    setErr("");
    try {
      const r = await api.revokeCustomerApproval(order.id, reason);
      setPendingToken("");
      onOrderUpdated?.(r.order);
    } catch (e) {
      setErr(formatApiError(e, "Gagal revoke link"));
    } finally {
      setBusy(false);
    }
  };

  const StatusIcon = ({ st }) => {
    if (st === "active") return <Clock size={16} />;
    if (st === "approved") return <CheckCircle2 size={16} />;
    if (st === "rejected") return <XCircle size={16} />;
    return <AlertTriangle size={16} />;
  };

  if (!canView) return null;

  return (
    <section className="ca-panel ca-approval-card" data-testid="customer-approval-panel">
      <header className="ca-approval-head">
        <div>
          <h3>Customer Approval</h3>
          <p className="muted small" style={{ margin: 0 }}>Link approval untuk dikirim ke customer melalui WhatsApp secara manual.</p>
        </div>
        <span className={`status-chip ca-status-lg status-${status}`} data-testid="ca-status-chip">
          <StatusIcon st={status} />
          <span>{STATUS_LABEL[status]}</span>
        </span>
      </header>

      <p className="ca-info-label">Approval Information</p>
      <ol className="ca-timeline" data-testid="ca-response">
        <li>
          <span className="lbl">Created</span>
          <span>{fmt(ca?.generated_at)}</span>
        </li>
        <li>
          <span className="lbl">Valid Until</span>
          <span>{fmt(ca?.expires_at)}</span>
        </li>
        <li>
          <span className="lbl">Signer</span>
          <span>{ca?.signer_name || "-"}</span>
        </li>
        <li>
          <span className="lbl">Approval Time</span>
          <span>{(status === "approved" || status === "rejected") ? fmt(ca?.responded_at) : "-"}</span>
        </li>
      </ol>

      {ca && ((status === "active" && ca.expires_at) || ca.expiry_hours) && (
        <div className="row" data-testid="ca-meta">
          {status === "active" && ca.expires_at && (
            <div data-testid="ca-time-remaining"><span className="lbl">Sisa waktu</span>{remaining(ca.expires_at)}</div>
          )}
          {ca.expiry_hours && <div><span className="lbl">Durasi</span>{ca.expiry_hours} jam</div>}
        </div>
      )}

      {status === "rejected" && ca?.reject_reason && (
        <p className="muted small"><span className="lbl">Alasan Reject</span> {ca.reject_reason}</p>
      )}
      {status === "approved" && ca?.customer_note && (
        <p className="muted small"><span className="lbl">Catatan Customer</span> {ca.customer_note}</p>
      )}

      {ca?.signature_storage_path && (
        <div className="ca-signature-block">
          <span className="lbl">Signature</span>
          <img
            src={`${process.env.REACT_APP_BACKEND_URL}/api/files/${ca.signature_storage_path}`}
            alt="Tanda tangan customer"
            className="ca-signature-preview"
            data-testid="ca-signature-preview"
          />
          <dl className="ca-signature-audit">
            <div>
              <dt>Signer</dt>
              <dd>{ca?.signer_name || "-"}</dd>
            </div>
            <div>
              <dt>Approval Time</dt>
              <dd>{(status === "approved" || status === "rejected") ? fmt(ca?.responded_at) : "-"}</dd>
            </div>
          </dl>
        </div>
      )}

      {status === "invalidated" && (
        <p className="muted small" style={{ marginTop: 8 }} data-testid="ca-invalidated-note">
          Design telah di-upload ulang. Buat link approval baru untuk versi design terbaru.
        </p>
      )}

      {/* Freshly-generated token banner (shown once, hides on refresh) */}
      {pendingToken && (
        <div style={{ marginTop: 10, padding: 10, background: "#e5f5e5", border: "1px solid #b7dcb7", borderRadius: 8 }} data-testid="ca-fresh-token">
          <p className="small" style={{ marginTop: 0 }}><b>Link approval siap dikirim.</b> Token hanya ditampilkan sekali — salin & kirim sekarang.</p>
          <div className="link-box" data-testid="ca-public-url">{publicUrl}</div>
        </div>
      )}

      {err && (
        <div style={{ marginTop: 10, padding: 8, background: "#fce6e6", color: "#8f1f1f", borderRadius: 6, whiteSpace: "pre-line" }} data-testid="ca-error">
          {err}
        </div>
      )}

      {canManage && (
        <div className="actions" data-testid="ca-actions">
          {status === "none" && (
            <button className="primary" onClick={() => doGenerate()} disabled={busy} data-testid="ca-generate-button">
              <LinkIcon size={14} /> Buat Link Approval
            </button>
          )}
          {status === "active" && (
            <>
              {pendingToken && (
                <>
                  <button className="ghost" onClick={() => doCopy(publicUrl, "Link")} data-testid="ca-copy-link-button">
                    <Copy size={14} /> Copy Link
                  </button>
                  <button className="ghost" onClick={() => doCopy(waMessage, "Pesan WhatsApp")} data-testid="ca-copy-wa-button">
                    <Send size={14} /> Salin Pesan WhatsApp
                  </button>
                </>
              )}
              <button className="ghost" onClick={() => doGenerate()} disabled={busy} data-testid="ca-regenerate-button">
                <RefreshCw size={14} /> Regenerate Link
              </button>
              <button className="ghost" onClick={doRevoke} disabled={busy} data-testid="ca-revoke-button">
                <Shield size={14} /> Cabut Link
              </button>
            </>
          )}
          {(status === "expired" || status === "revoked" || status === "invalidated" || status === "rejected") && (
            <button className="primary" onClick={() => doGenerate({ regenerateConfirm: true })} disabled={busy} data-testid="ca-resend-button">
              <RefreshCw size={14} /> Buat Link Baru / Kirim Ulang
            </button>
          )}
        </div>
      )}

      {copyMsg && <p className="muted small" style={{ marginTop: 6 }} data-testid="ca-copy-msg">{copyMsg}</p>}
    </section>
  );
}
