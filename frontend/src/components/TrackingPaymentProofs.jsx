import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FileText } from "lucide-react";
import api from "../lib/api";
import InAppDocumentViewer from "./InAppDocumentViewer";

function money(amount) {
  return `Rp ${Number(amount || 0).toLocaleString("id-ID")}`;
}

/**
 * Customer payment proofs for one tracking token.
 * Fetches on mount, the same way Faktur Penjualan loads. Reversed rows stay
 * visible as history and are not treated as an active payment.
 */
export default function TrackingPaymentProofs({ token }) {
  const [phase, setPhase] = useState("loading");
  const [proofs, setProofs] = useState([]);
  const [open, setOpen] = useState(false);
  const [viewerId, setViewerId] = useState("");
  const req = useRef(0);

  const load = useCallback(() => {
    const id = ++req.current;
    setPhase("loading");
    setOpen(false);
    setViewerId("");
    api.publicTrackingPaymentProofs(token).then((data) => {
      if (req.current !== id) return;
      if (!Array.isArray(data?.proofs)) {
        setProofs([]);
        setPhase("error");
        return;
      }
      setProofs(data.proofs);
      setPhase("ready");
    }).catch(() => {
      if (req.current !== id) return;
      setProofs([]);
      setPhase("error");
    });
  }, [token]);

  useEffect(() => {
    load();
    return () => { req.current += 1; };
  }, [load]);

  const viewer = proofs.find((row) => row.receipt_id === viewerId) || null;
  const loadFile = useCallback(({ download = false, signal } = {}) => (
    api.publicTrackingPaymentProofFile(token, viewerId, { download, signal })
  ), [token, viewerId]);

  const activeCount = proofs.filter((row) => row.active).length;
  const available = phase === "ready" && proofs.length > 0;
  const statusText = phase === "loading"
    ? "Memuat..."
    : phase === "error"
      ? "Gagal dimuat"
      : activeCount > 0
        ? (activeCount === 1 ? "Bukti tersimpan" : `${activeCount} bukti`)
        : proofs.length > 0
          ? "Dibatalkan"
          : "Belum tersedia";

  const onTile = () => {
    if (phase === "error") {
      load();
      return;
    }
    if (!available) return;
    setOpen(true);
  };

  const sheet = open ? (
    <div className="tracking-sheet-root" data-testid="tracking-payment-sheet" onClick={() => setOpen(false)}>
      <div
        className="tracking-sheet"
        role="dialog"
        aria-modal="true"
        aria-label="Pembayaran"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="tracking-sheet-head">
          <h2>Pembayaran</h2>
          <button type="button" className="ghost sm" data-testid="tracking-payment-sheet-close" onClick={() => setOpen(false)}>
            Tutup
          </button>
        </header>
        <div className="tracking-sheet-body">
          <ul className="payment-files" data-testid="tracking-payment-proof-list">
            {proofs.map((row) => (
              <li key={row.receipt_id} data-testid={`tracking-payment-proof-${row.receipt_id}`}>
                <div>
                  <strong>{row.receipt_number || "Penerimaan"}</strong>
                  <span className="muted small">
                    {" "}
                    {row.receipt_date || "—"}
                    {" · "}
                    {money(row.amount)}
                    {" · "}
                    {row.status_label}
                  </span>
                </div>
                <button
                  type="button"
                  className="ghost sm"
                  data-testid={`tracking-payment-proof-open-${row.receipt_id}`}
                  onClick={() => setViewerId(row.receipt_id)}
                >
                  Lihat
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  ) : null;

  return (
    <>
      <button
        type="button"
        className={`tracking-doc-tile${available || phase === "error" ? "" : " is-off"}`}
        disabled={phase !== "error" && !available}
        data-testid="tracking-doc-tile-payment"
        data-active-count={activeCount}
        aria-label={`Pembayaran: ${statusText}`}
        onClick={onTile}
      >
        <span className="tracking-doc-ico"><FileText size={14} /></span>
        <strong>Pembayaran</strong>
        <span className="muted small" data-testid="tracking-payment-proof-status">
          {available ? `✓ ${statusText}` : `○ ${statusText}`}
        </span>
        <span className="tracking-doc-action">{phase === "error" ? "Coba lagi" : "Lihat"}</span>
      </button>
      {sheet ? createPortal(sheet, document.body) : null}
      {viewer ? createPortal(
        <InAppDocumentViewer
          documentKey={viewer.receipt_id}
          title={viewer.receipt_number || "Bukti pembayaran"}
          fileName={viewer.file_name || "bukti-pembayaran"}
          contentType={viewer.content_type || ""}
          loadFile={loadFile}
          allowDownload
          onClose={() => setViewerId("")}
          testId="tracking-payment-proof-viewer"
        />,
        document.body,
      ) : null}
    </>
  );
}
