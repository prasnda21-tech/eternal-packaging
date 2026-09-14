import { useEffect, useState } from "react";
import api from "../lib/api";
import { errDetail, newIdempotencyKey } from "../lib/purchaseUi";

/**
 * Submit payment to an existing user via personal Chat (existing DM + message APIs).
 */
export default function PurchasePaymentSubmitModal({
  open,
  payment,
  busy = false,
  onCancel,
  onSubmitted,
}) {
  const [q, setQ] = useState("");
  const [users, setUsers] = useState([]);
  const [selected, setSelected] = useState(null);
  const [notes, setNotes] = useState("");
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setQ("");
    setSelected(null);
    setNotes("");
    setError("");
    setUsers([]);
  }, [open, payment?.id]);

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await api.listPurchasePaymentSubmitTargets({ q });
        if (!cancelled) setUsers(res.users || []);
      } catch (ex) {
        if (!cancelled) setError(errDetail(ex));
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [open, q]);

  if (!open || !payment) return null;

  const confirm = async () => {
    if (!selected || submitting || busy) return;
    setSubmitting(true);
    setError("");
    try {
      const res = await api.submitPurchasePayment(payment.id, {
        target_user_id: selected.id,
        notes,
        idempotency_key: newIdempotencyKey(),
      });
      onSubmitted?.(res);
    } catch (ex) {
      setError(errDetail(ex));
    } finally {
      setSubmitting(false);
    }
  };

  const locked = busy || submitting;

  return (
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      data-testid="payment-submit-modal"
      onClick={locked ? undefined : onCancel}
    >
      <div className="modal-inner purchase-submit-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <h3>Ajukan Pembayaran</h3>
            <p className="purchase-modal-helper">
              Pilih user tujuan pengajuan. Status pelunasan tidak berubah.
            </p>
          </div>
          <button type="button" className="ghost sm" onClick={onCancel} disabled={locked} aria-label="Tutup">×</button>
        </div>
        <div className="purchase-modal-body" data-testid="payment-submit-modal-body">
          {error ? <p className="error" data-testid="payment-submit-error">{error}</p> : null}
          <label className="purchase-cancel-reason-label" htmlFor="payment-submit-user-search">
            Ditujukan kepada <span className="req">*</span>
          </label>
          <input
            id="payment-submit-user-search"
            data-testid="payment-submit-user-search"
            value={q}
            disabled={locked}
            placeholder="Cari nama / email / role"
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="purchase-submit-user-list" data-testid="payment-submit-user-list">
            {searching && <p className="muted small">Mencari…</p>}
            {!searching && users.length === 0 && <p className="muted small">Tidak ada user.</p>}
            {users.map((u) => {
              const active = selected?.id === u.id;
              return (
                <button
                  key={u.id}
                  type="button"
                  className={`purchase-submit-user${active ? " is-selected" : ""}`}
                  data-testid={`payment-submit-user-${u.id}`}
                  disabled={locked}
                  onClick={() => setSelected(u)}
                >
                  <span className="purchase-submit-user-name">{u.name}</span>
                  <span className="muted small">{u.role || u.team || u.email || ""}</span>
                </button>
              );
            })}
          </div>
          <label className="purchase-cancel-reason-label" htmlFor="payment-submit-notes" style={{ marginTop: 12 }}>
            Catatan
          </label>
          <textarea
            id="payment-submit-notes"
            data-testid="payment-submit-notes"
            rows={2}
            value={notes}
            disabled={locked}
            placeholder="Opsional"
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
        <div className="purchase-modal-actions" data-testid="payment-submit-modal-footer">
          <button type="button" className="ghost" onClick={onCancel} disabled={locked} data-testid="payment-submit-batal">
            Batal
          </button>
          <button
            type="button"
            className="primary"
            disabled={locked || !selected}
            data-testid="payment-submit-confirm"
            onClick={confirm}
          >
            {submitting ? "Mengajukan…" : "Ajukan Pembayaran"}
          </button>
        </div>
      </div>
    </div>
  );
}
