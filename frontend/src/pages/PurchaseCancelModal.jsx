/**
 * Confirm soft-delete / cancel for purchasing documents.
 * Reason is not collected in UI; backend stores internal default when empty.
 */
export default function PurchaseCancelModal({
  open,
  title = "Hapus dokumen?",
  body = "Dokumen akan dikeluarkan dari daftar aktif. Riwayat pembatalan tetap tersimpan.",
  busy = false,
  error = "",
  reason = "",
  onReasonChange,
  requireReason = false,
  confirmLabel = "Hapus",
  busyLabel = "Menghapus…",
  onCancel,
  onConfirm,
}) {
  if (!open) return null;

  return (
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      data-testid="purchase-cancel-modal"
      onClick={busy ? undefined : onCancel}
    >
      <div className="modal-inner purchase-cancel-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <h3>{title}</h3>
            <p className="purchase-modal-helper">{body}</p>
          </div>
          <button type="button" className="ghost sm" onClick={onCancel} disabled={busy} aria-label="Tutup">×</button>
        </div>
        {error ? (
          <div className="purchase-modal-body">
            <p className="error" data-testid="purchase-cancel-error" style={{ marginBottom: 0 }}>{error}</p>
          </div>
        ) : null}
        {requireReason ? (
          <div className="purchase-modal-body">
            <label htmlFor="purchase-cancel-reason" className="label">Alasan pembatalan</label>
            <textarea
              id="purchase-cancel-reason"
              value={reason}
              onChange={(e) => onReasonChange?.(e.target.value)}
              disabled={busy}
              placeholder="Contoh: jumlah penerimaan salah"
              rows={3}
              data-testid="purchase-cancel-reason"
            />
          </div>
        ) : null}
        <div className="purchase-modal-actions">
          <button type="button" className="ghost" onClick={onCancel} disabled={busy} data-testid="purchase-cancel-batal">
            Batal
          </button>
          <button
            type="button"
            className="primary danger"
            disabled={busy || (requireReason && !String(reason || "").trim())}
            data-testid="purchase-cancel-confirm"
            onClick={() => onConfirm?.()}
          >
            {busy ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
