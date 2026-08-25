import { formatDisplayDateTime } from "../lib/constants";
import { fmtQty } from "./qtyUi";

function q(n) {
  return fmtQty(n);
}

export default function ConfirmActionModal({
  open,
  stage,
  qty,
  name,
  time,
  extra,
  busy,
  error,
  onCancel,
  onConfirm,
}) {
  if (!open) return null;
  return (
    <div className="modal" role="dialog" aria-modal="true" data-testid="confirm-action-modal" onClick={busy ? undefined : onCancel}>
      <div className="modal-inner confirm-action-inner" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Konfirmasi</h3>
        </div>
        <div className="confirm-action-body">
          <dl className="confirm-action-dl">
            <div><dt>Tahap</dt><dd data-testid="confirm-stage">{stage || "—"}</dd></div>
            {qty != null && qty !== "" && (
              <div><dt>Qty</dt><dd data-testid="confirm-qty">{typeof qty === "number" ? q(qty) : qty}</dd></div>
            )}
            <div><dt>Nama</dt><dd data-testid="confirm-name">{name || "—"}</dd></div>
            <div><dt>Waktu</dt><dd data-testid="confirm-time">{time || formatDisplayDateTime(new Date().toISOString())}</dd></div>
          </dl>
          {extra}
          {error && <p className="error" data-testid="confirm-error">{error}</p>}
        </div>
        <div className="delete-modal-actions">
          <button type="button" className="ghost" onClick={onCancel} disabled={busy} data-testid="confirm-cancel">Batal</button>
          <button type="button" className="primary" onClick={onConfirm} disabled={busy} data-testid="confirm-ok">
            {busy ? "Menyimpan…" : "Konfirmasi"}
          </button>
        </div>
      </div>
    </div>
  );
}
