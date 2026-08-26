import { useEffect, useState } from "react";
import { formatDate } from "../lib/constants";
import { newActionId } from "../lib/workflowEntries";
import { rescheduleConfirmLabel } from "../lib/deliverySchedule";
import api from "../lib/api";

export default function RescheduleDeliveryModal({
  open,
  order,
  canSetDate,
  currentDate,
  initialReason = "",
  confirmMode = "create",
  rescheduleId,
  busy: busyProp,
  onCancel,
  onDone,
}) {
  const [reason, setReason] = useState(initialReason);
  const [newDate, setNewDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const saving = busy || busyProp;

  useEffect(() => {
    if (open) {
      setReason(initialReason || "");
      setNewDate("");
      setErr("");
    }
  }, [open, initialReason]);

  if (!open || !order) return null;

  const submit = async () => {
    if (saving) return;
    const trimmed = (reason || "").trim();
    if (!trimmed) {
      setErr("Alasan penjadwalan ulang wajib diisi.");
      return;
    }
    if (canSetDate && !(newDate || "").trim()) {
      setErr("Tanggal pengiriman baru wajib diisi.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const actionId = newActionId();
      let res;
      if (confirmMode === "set-date") {
        res = await api.setDeliveryRescheduleDate(rescheduleId, {
          new_date: newDate,
          reason: trimmed,
          action_id: actionId,
        });
      } else {
        res = await api.requestDeliveryReschedule(order.id, {
          reason: trimmed,
          new_date: canSetDate ? newDate : undefined,
          action_id: actionId,
        });
      }
      onDone?.(res.order, res.reschedule);
    } catch (e) {
      const d = e.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Gagal menyimpan penjadwalan ulang"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal" role="dialog" aria-modal="true" data-testid="reschedule-modal" onClick={saving ? undefined : onCancel}>
      <div className="modal-inner confirm-action-inner" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>JADWALKAN ULANG PENGIRIMAN</h3>
        </div>
        <div className="confirm-action-body">
          <dl className="confirm-action-dl">
            <div><dt>No PO</dt><dd data-testid="reschedule-po">{order.po_number || order.order_number}</dd></div>
            <div><dt>Customer</dt><dd data-testid="reschedule-customer">{order.customer_name || order.customer || "—"}</dd></div>
            <div>
              <dt>Tanggal Pengiriman Saat Ini</dt>
              <dd data-testid="reschedule-current-date">{formatDate(currentDate, { dateOnly: true })}</dd>
            </div>
          </dl>
          <label className="reschedule-field">
            Alasan Penjadwalan Ulang
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
              rows={3}
              disabled={saving}
              data-testid="reschedule-reason"
            />
          </label>
          {canSetDate ? (
            <label className="reschedule-field">
              Tanggal Pengiriman Baru
              <input
                type="date"
                value={newDate}
                onChange={(e) => setNewDate(e.target.value)}
                required
                disabled={saving}
                data-testid="reschedule-new-date"
              />
            </label>
          ) : (
            <p className="muted small" data-testid="reschedule-date-readonly">Tanggal baru akan ditetapkan oleh petugas yang berwenang.</p>
          )}
          {err && <p className="error" data-testid="reschedule-error">{err}</p>}
        </div>
        <div className="delete-modal-actions">
          <button type="button" className="ghost" onClick={onCancel} disabled={saving} data-testid="reschedule-cancel">Batal</button>
          <button type="button" className="primary" onClick={submit} disabled={saving} data-testid="reschedule-confirm">
            {saving ? "Menyimpan…" : rescheduleConfirmLabel(canSetDate)}
          </button>
        </div>
      </div>
    </div>
  );
}
