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

/** Build field-scoped payload from draft (canonical order fields only). */
export function buildPoInfoUpdatePayload(draft) {
  return {
    address: draft?.address ?? "",
    deadline: draft?.deadline || "",
    estimated_delivery: draft?.estimated_delivery || "",
  };
}

export default function EditPoInfoModal({ open, order, onClose, onSaved }) {
  const [address, setAddress] = useState("");
  const [deadline, setDeadline] = useState("");
  const [estimatedDelivery, setEstimatedDelivery] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!open || !order) return;
    setAddress(order.address || "");
    setDeadline(toDateInputValue(order.deadline));
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
      deadline,
      estimated_delivery: estimatedDelivery,
    });
    if (!payload.deadline) {
      setErr("Deadline wajib diisi");
      return;
    }
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
      setErr(formatApiError(e) || e.response?.data?.detail || "Gagal menyimpan Informasi PO");
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
            <span>Deadline</span>
            <input
              type="date"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
              data-testid="edit-po-info-deadline"
            />
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
