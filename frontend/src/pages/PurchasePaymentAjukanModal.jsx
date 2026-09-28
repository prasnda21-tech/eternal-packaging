import { useEffect, useRef, useState } from "react";
import api from "../lib/api";
import {
  dueDateLabel,
  errDetail,
  formatPurchaseDate,
  formatRupiah,
  newIdempotencyKey,
  validatePurchasePaymentAmount,
} from "../lib/purchaseUi";
// uses existing purchaseUi helpers + submit-targets API (no Chat redesign)

/**
 * From invoice detail: Ajukan Pembayaran in one step (create as DIAJUKAN + chat).
 * Does not settle invoice finances.
 */
export default function PurchasePaymentAjukanModal({
  open,
  invoice,
  busy = false,
  onCancel,
  onSubmitted,
}) {
  const outstanding = Number(invoice?.outstanding_amount || 0);
  const [amount, setAmount] = useState("");
  const [q, setQ] = useState("");
  const [users, setUsers] = useState([]);
  const [selected, setSelected] = useState(null);
  const [notes, setNotes] = useState("");
  const [requestedPaymentDate, setRequestedPaymentDate] = useState("");
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const idempotencyKeyRef = useRef(null);

  useEffect(() => {
    if (!open || !invoice) return;
    setAmount(String(outstanding || ""));
    setQ("");
    setSelected(null);
    setNotes("");
    setRequestedPaymentDate("");
    setError("");
    setUsers([]);
    idempotencyKeyRef.current = newIdempotencyKey();
  }, [open, invoice?.id, outstanding]);

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

  if (!open || !invoice) return null;

  const confirm = async () => {
    if (!selected || submitting || busy) return;
    const feErr = validatePurchasePaymentAmount(amount, outstanding);
    if (feErr) {
      setError(feErr);
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const res = await api.ajukanPurchasePayment({
        purchase_invoice_id: invoice.id,
        amount: Number(amount),
        target_user_id: selected.id,
        requested_payment_date: requestedPaymentDate || null,
        notes,
        idempotency_key: idempotencyKeyRef.current || newIdempotencyKey(),
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
      data-testid="payment-ajukan-modal"
      onClick={locked ? undefined : onCancel}
    >
      <div className="modal-inner purchase-submit-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <h3>Ajukan Pembayaran</h3>
            <p className="purchase-modal-helper">
              Pengajuan dibuat sebagai DIAJUKAN. Status pelunasan faktur belum berubah.
            </p>
          </div>
          <button type="button" className="ghost sm" onClick={onCancel} disabled={locked} aria-label="Tutup">×</button>
        </div>
        <div className="purchase-modal-body" data-testid="payment-ajukan-modal-body">
          {error ? <p className="error" data-testid="payment-ajukan-error">{error}</p> : null}
          <div className="purchase-detail-meta" style={{ marginBottom: 12 }}>
            <div>
              <span className="label">Supplier</span>
              <span className="value">{invoice.supplier || "—"}</span>
            </div>
            <div>
              <span className="label">No Faktur</span>
              <span className="value mono">{invoice.invoice_number}</span>
            </div>
            <div>
              <span className="label">Tanggal Faktur</span>
              <span className="value">{formatPurchaseDate(invoice.invoice_date)}</span>
            </div>
            <div>
              <span className="label">Jatuh Tempo</span>
              <span className="value">{dueDateLabel(invoice.due_date)}</span>
            </div>
            <div>
              <span className="label">Total Faktur</span>
              <span className="value">{formatRupiah(invoice.grand_total)}</span>
            </div>
            <div>
              <span className="label">Sudah Dibayar</span>
              <span className="value">{formatRupiah(invoice.paid_total)}</span>
            </div>
            <div>
              <span className="label">Sisa Tagihan</span>
              <span className="value">{formatRupiah(outstanding)}</span>
            </div>
          </div>
          <label className="purchase-cancel-reason-label" htmlFor="payment-ajukan-amount">
            Nominal <span className="req">*</span>
          </label>
          <input
            id="payment-ajukan-amount"
            data-testid="payment-ajukan-amount"
            type="number"
            min="0"
            step="any"
            value={amount}
            disabled={locked}
            onChange={(e) => setAmount(e.target.value)}
          />
          <label className="purchase-cancel-reason-label" htmlFor="payment-ajukan-requested-date" style={{ marginTop: 12 }}>
            Tanggal Dibutuhkan / Target Pembayaran
          </label>
          <input
            id="payment-ajukan-requested-date"
            data-testid="payment-ajukan-requested-date"
            type="date"
            value={requestedPaymentDate}
            disabled={locked}
            onChange={(e) => setRequestedPaymentDate(e.target.value)}
          />
          <label className="purchase-cancel-reason-label" htmlFor="payment-ajukan-user-search" style={{ marginTop: 12 }}>
            Ditujukan kepada <span className="req">*</span>
          </label>
          <input
            id="payment-ajukan-user-search"
            data-testid="payment-ajukan-user-search"
            value={q}
            disabled={locked}
            placeholder="Cari nama / email / role"
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="purchase-submit-user-list" data-testid="payment-ajukan-user-list">
            {searching && <p className="muted small">Mencari…</p>}
            {!searching && users.length === 0 && <p className="muted small">Tidak ada user.</p>}
            {users.map((u) => {
              const active = selected?.id === u.id;
              return (
                <button
                  key={u.id}
                  type="button"
                  className={`purchase-submit-user${active ? " is-selected" : ""}`}
                  data-testid={`payment-ajukan-user-${u.id}`}
                  disabled={locked}
                  onClick={() => setSelected(u)}
                >
                  <span className="purchase-submit-user-name">{u.name}</span>
                  <span className="muted small">{u.role || u.team || u.email || ""}</span>
                </button>
              );
            })}
          </div>
          <label className="purchase-cancel-reason-label" htmlFor="payment-ajukan-notes" style={{ marginTop: 12 }}>
            Catatan
          </label>
          <textarea
            id="payment-ajukan-notes"
            data-testid="payment-ajukan-notes"
            rows={2}
            value={notes}
            disabled={locked}
            placeholder="Opsional"
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
        <div className="purchase-modal-actions" data-testid="payment-ajukan-modal-footer">
          <button type="button" className="ghost" onClick={onCancel} disabled={locked} data-testid="payment-ajukan-batal">
            Batal
          </button>
          <button
            type="button"
            className="primary"
            disabled={locked || !selected}
            data-testid="payment-ajukan-confirm"
            onClick={confirm}
          >
            {submitting ? "Mengajukan…" : "Ajukan Pembayaran"}
          </button>
        </div>
      </div>
    </div>
  );
}
