import { useState } from "react";
import {
  parseRollbackStage,
  rollbackStageOptions,
  rollbackTargets,
  validateRollbackSelection,
  validateRollbackStage,
} from "../lib/rollbackTargets";
import { fmtQty } from "./qtyUi";

export default function RollbackModal({
  open,
  order,
  busy,
  error,
  hasPermission,
  onCancel,
  onConfirm,
}) {
  const [selected, setSelected] = useState([]);
  const [toStage, setToStage] = useState("");
  const [reason, setReason] = useState("");
  const [localErr, setLocalErr] = useState("");
  const rows = rollbackTargets(order);
  const stageOptions = rollbackStageOptions(order?.current_stage);
  const parsedStage = parseRollbackStage(toStage);
  const reasonOk = !!(reason || "").trim();
  const confirmDisabled = !hasPermission || !!busy || selected.length === 0 || parsedStage == null || !reasonOk;

  if (!open) return null;

  const toggle = (id) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
    setLocalErr("");
  };

  const submit = () => {
    const selMsg = validateRollbackSelection(selected);
    if (selMsg) {
      setLocalErr(selMsg);
      return;
    }
    const stageMsg = validateRollbackStage(toStage);
    if (stageMsg) {
      setLocalErr(stageMsg);
      return;
    }
    if (!reasonOk) {
      setLocalErr("Alasan rollback wajib diisi.");
      return;
    }
    onConfirm({ ids: selected, to_stage: parsedStage, reason: reason.trim() });
  };

  const selectedSet = new Set(selected);

  return (
    <div className="modal" role="dialog" aria-modal="true" data-testid="rollback-modal" onClick={busy ? undefined : onCancel}>
      <div className="modal-inner confirm-action-inner" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>ROLLBACK WORKFLOW</h3>
        </div>
        <div className="confirm-action-body">
          <p className="muted small">Pilih nomor yang akan dikembalikan. Nomor yang tidak dipilih tetap utuh.</p>
          {rows.length === 0 && (
            <p className="muted small" data-testid="rollback-empty">Tidak ada nomor yang bisa di-rollback.</p>
          )}
          <ul className="rollback-nomor-list" data-testid="rollback-nomor-list">
            {rows.map((row) => (
              <li key={row.id} className={selectedSet.has(row.id) ? "is-selected" : ""}>
                <label data-testid={`rollback-nomor-${row.nomor}`}>
                  <input
                    type="checkbox"
                    checked={selectedSet.has(row.id)}
                    onChange={() => toggle(row.id)}
                    disabled={busy}
                    data-testid={`rollback-check-${row.id}`}
                  />
                  <span>
                    <strong>Nomor {row.nomor}</strong>
                    {" · "}
                    {row.label}
                    {row.qty != null ? ` · ${fmtQty(row.qty)} pcs` : ""}
                    {row.filled ? "" : " · kosong"}
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <label className="qty-notes">
            <span className="qty-notes-label">Rollback sampai tahap</span>
            <select
              className="manual-input"
              value={toStage}
              onChange={(e) => { setToStage(e.target.value); setLocalErr(""); }}
              disabled={busy}
              data-testid="rollback-stage-select"
            >
              <option value="">Pilih tahap</option>
              {stageOptions.map((opt) => (
                <option key={opt.value} value={String(opt.value)}>{opt.label}</option>
              ))}
            </select>
          </label>
          <label className="qty-notes">
            <span className="qty-notes-label">Alasan</span>
            <input
              type="text"
              className="manual-input"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={busy}
              data-testid="rollback-reason"
              placeholder="Alasan rollback (wajib)"
            />
          </label>
          {(localErr || error) && <p className="error" data-testid="rollback-error">{localErr || error}</p>}
        </div>
        <div className="delete-modal-actions">
          <button type="button" className="ghost" onClick={onCancel} disabled={busy} data-testid="rollback-cancel">Batal</button>
          <button type="button" className="primary" onClick={submit} disabled={confirmDisabled} data-testid="rollback-confirm">
            {busy ? "Menyimpan…" : "Konfirmasi Rollback"}
          </button>
        </div>
      </div>
    </div>
  );
}
