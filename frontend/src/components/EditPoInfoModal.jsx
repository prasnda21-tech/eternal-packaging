import { useEffect, useState } from "react";
import api from "../lib/api";
import { formatApiError } from "../lib/errors";

/** Slice persisted ISO / date string to YYYY-MM-DD for <input type="date">. */
export function toDateInputValue(raw) {
  if (!raw) return "";
  const s = String(raw).trim();
  if (!s) return "";
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return "";
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Prefill Tanggal Pengiriman only when a manual override exists.
 * Automatic dates stay empty so saving without edits does not lock them in as manual.
 */
export function resolveTanggalPengirimanInput(order) {
  if (!order) return "";
  const isManual =
    order.tanggal_pengiriman_source === "manual"
    || order.delivery_deadline_source === "manual"
    || Boolean(order.delivery_deadline_manual)
    || Boolean(order.scheduled_delivery_date);
  if (!isManual) return "";
  return (
    toDateInputValue(order.tanggal_pengiriman)
    || toDateInputValue(order.scheduled_delivery_date)
    || toDateInputValue(order.delivery_deadline_manual)
    || ""
  );
}

/** Build field-scoped payload from draft (canonical order fields only). */
export function buildPoInfoUpdatePayload(draft) {
  const payload = {
    address: draft?.address ?? "",
    recipient: draft?.recipient ?? "",
    customer_phone: draft?.customer_phone ?? "",
    estimated_delivery: draft?.estimated_delivery || "",
  };
  // Empty string clears manual override → automatic latest approval + 3 days.
  if (draft && Object.prototype.hasOwnProperty.call(draft, "tanggal_pengiriman")) {
    payload.tanggal_pengiriman = draft.tanggal_pengiriman || "";
  }
  return payload;
}

export default function EditPoInfoModal({ open, order, onClose, onSaved }) {
  const [address, setAddress] = useState("");
  const [recipient, setRecipient] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [tanggalPengiriman, setTanggalPengiriman] = useState("");
  const [estimatedDelivery, setEstimatedDelivery] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!open || !order) return;
    setAddress(order.address || "");
    setRecipient(order.recipient || "");
    setCustomerPhone(order.customer_phone || "");
    setTanggalPengiriman(resolveTanggalPengirimanInput(order));
    setEstimatedDelivery(toDateInputValue(order.estimated_delivery));
    setErr("");
    setBusy(false);
  }, [open, order]);

  if (!open || !order) return null;

  const handleCancel = () => {
    if (busy) return;
    onClose?.();
  };

  const handleSave = async () => {
    if (busy) return;
    const payload = buildPoInfoUpdatePayload({
      address,
      recipient,
      customer_phone: customerPhone,
      tanggal_pengiriman: tanggalPengiriman,
      estimated_delivery: estimatedDelivery,
    });
    if (!payload.estimated_delivery) {
      setErr("Estimasi kirim wajib diisi");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const res = await api.updateOrderInfo(order.id, payload);
      onSaved?.(res.order || res);
      onClose?.();
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = formatApiError(e)
        || (typeof detail === "string" ? detail : null)
        || "Gagal menyimpan Informasi PO";
      setErr(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      data-testid="edit-po-info-modal"
      onClick={busy ? undefined : handleCancel}
    >
      <div className="modal-inner confirm-action-inner edit-po-info-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Edit Informasi PO</h3>
        </div>
        <div className="confirm-action-body edit-po-info-form">
          <label className="edit-po-info-field">
            <span>Alamat</span>
            <textarea
              rows={3}
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              data-testid="edit-po-info-address"
              placeholder="Alamat pengiriman"
            />
          </label>
          <label className="edit-po-info-field">
            <span>Penerima</span>
            <input
              type="text"
              value={recipient}
              onChange={(e) => setRecipient(e.target.value)}
              data-testid="edit-po-info-recipient"
              placeholder="Nama penerima"
            />
          </label>
          <label className="edit-po-info-field">
            <span>No. HP Penerima</span>
            <input
              type="text"
              value={customerPhone}
              onChange={(e) => setCustomerPhone(e.target.value)}
              data-testid="edit-po-info-customer-phone"
              placeholder="mis. 0812-3456-7890"
            />
          </label>
          <label className="edit-po-info-field">
            <span>Tanggal Pengiriman</span>
            <input
              type="date"
              value={tanggalPengiriman}
              onChange={(e) => setTanggalPengiriman(e.target.value)}
              data-testid="edit-po-info-tanggal-pengiriman"
            />
            <span className="muted small" style={{ marginTop: 4, display: "block" }}>
              Kosongkan untuk memakai tanggal otomatis (approval terakhir + 3 hari).
            </span>
          </label>
          <label className="edit-po-info-field">
            <span>Estimasi Kirim</span>
            <input
              type="date"
              value={estimatedDelivery}
              onChange={(e) => setEstimatedDelivery(e.target.value)}
              data-testid="edit-po-info-estimated-delivery"
            />
          </label>
          {err && <p className="error" data-testid="edit-po-info-error">{err}</p>}
        </div>
        <div className="delete-modal-actions">
          <button
            type="button"
            className="ghost"
            onClick={handleCancel}
            disabled={busy}
            data-testid="edit-po-info-cancel"
          >
            Batal
          </button>
          <button
            type="button"
            className="primary"
            onClick={handleSave}
            disabled={busy}
            data-testid="edit-po-info-save"
          >
            {busy ? "Menyimpan…" : "Simpan"}
          </button>
        </div>
      </div>
    </div>
  );
}
