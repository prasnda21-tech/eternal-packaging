import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Lock, Unlock } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";

const errText = (ex) => {
  const det = ex?.response?.data?.detail;
  if (typeof det === "string") return det;
  if (det && typeof det === "object" && det.message) return det.message;
  return ex?.message || "Permintaan gagal";
};

const formatWhen = (raw) => {
  const s = String(raw || "").trim();
  if (!s) return "—";
  return s.slice(0, 19).replace("T", " ");
};

const actorName = (actor) => {
  if (!actor) return "—";
  if (typeof actor === "string") return actor || "—";
  return actor.name || actor.email || actor.id || "—";
};

const CLOSE_WARNING = "Setelah periode ditutup, jurnal baru dan reversal pada bulan ini akan diblokir sampai periode dibuka kembali.";
const REOPEN_WARNING = "Membuka kembali periode memungkinkan posting dan reversal pada bulan tersebut.";

export default function PeriodeAkuntansi() {
  const { hasPerm } = useAuth();
  const canClose = hasPerm("accounting.period.close");
  const canReopen = hasPerm("accounting.period.reopen");
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actingKey, setActingKey] = useState("");
  const [actionError, setActionError] = useState("");
  const [dialog, setDialog] = useState(null);
  const [reason, setReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const data = await api.accountingPeriods();
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (ex) {
      setLoadError(errText(ex));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openDialog = (row, kind) => {
    setDialog({ kind, period_key: row.period_key });
    setReason("");
    setActionError("");
  };

  const closeDialog = () => {
    if (actingKey) return;
    setDialog(null);
    setReason("");
  };

  const trimmedReason = String(reason || "").trim();
  const reasonValid = trimmedReason.length >= 3;

  const confirmAction = async () => {
    if (!dialog || !reasonValid || actingKey) return;
    const kind = dialog.kind;
    const periodKey = dialog.period_key;
    setActingKey(`${kind}:${periodKey}`);
    setActionError("");
    try {
      const payload = { reason: trimmedReason };
      if (kind === "close") {
        await api.closeAccountingPeriod(periodKey, payload);
      } else {
        await api.reopenAccountingPeriod(periodKey, payload);
      }
      setDialog(null);
      setReason("");
      await load();
    } catch (ex) {
      setActionError(errText(ex));
    } finally {
      setActingKey("");
    }
  };

  return (
    <div className="prod-page" data-testid="periode-akuntansi-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Buku Besar</p>
          <h1 data-testid="periode-akuntansi-title">Periode Akuntansi</h1>
          <p className="subhead">Tutup atau buka ulang bulan. Draft tetap boleh dibuat; posting jurnal yang dikunci.</p>
        </div>
      </div>

      {loadError && (
        <div className="signal-strip" role="alert" data-testid="periode-akuntansi-error">
          <div><AlertCircle size={16} /><span>{loadError}</span></div>
        </div>
      )}
      {actionError && (
        <div className="signal-strip" role="alert" data-testid="periode-akuntansi-action-error">
          <div><AlertCircle size={16} /><span>{actionError}</span></div>
        </div>
      )}

      <div className="prod-table-wrap">
        <table className="prod-table" data-testid="periode-akuntansi-table">
          <thead>
            <tr>
              <th className="col-fit">Bulan</th>
              <th className="col-fit">Status</th>
              <th className="col-text">Ditutup pada</th>
              <th className="col-text">Ditutup oleh</th>
              <th className="col-fit">Aksi</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr data-testid="periode-akuntansi-loading">
                <td colSpan={5} className="muted small">Memuat…</td>
              </tr>
            )}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={5} className="muted small" data-testid="periode-akuntansi-empty">
                  Tidak ada periode.
                </td>
              </tr>
            )}
            {!loading && items.map((row) => {
              const closed = String(row.status) === "closed";
              return (
                <tr key={row.period_key} data-testid={`periode-row-${row.period_key}`}>
                  <td className="mono">{row.period_key}</td>
                  <td>
                    <span
                      className={`pill xs ${closed ? "kb-status-reversed" : "kb-status-posted"}`}
                      data-testid={`periode-status-${row.period_key}`}
                    >
                      {closed ? "CLOSED" : "OPEN"}
                    </span>
                  </td>
                  <td>{closed ? formatWhen(row.closed_at) : "—"}</td>
                  <td>{closed ? actorName(row.closed_by) : "—"}</td>
                  <td>
                    {closed ? (
                      canReopen ? (
                        <button
                          type="button"
                          className="ghost sm"
                          disabled={Boolean(actingKey)}
                          onClick={() => openDialog(row, "reopen")}
                          data-testid={`periode-reopen-${row.period_key}`}
                        >
                          <Unlock size={12} /> Buka
                        </button>
                      ) : (
                        <span className="muted small">—</span>
                      )
                    ) : canClose ? (
                      <button
                        type="button"
                        className="ghost sm"
                        disabled={Boolean(actingKey)}
                        onClick={() => openDialog(row, "close")}
                        data-testid={`periode-close-${row.period_key}`}
                      >
                        <Lock size={12} /> Tutup
                      </button>
                    ) : (
                      <span className="muted small">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {dialog && (
        <div
          className="modal"
          role="dialog"
          aria-modal="true"
          data-testid={`periode-${dialog.kind}-modal`}
          onClick={closeDialog}
        >
          <div className="modal-inner" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <h3 data-testid="periode-modal-title">
                {dialog.kind === "close"
                  ? `Tutup Periode ${dialog.period_key}`
                  : `Buka Kembali Periode ${dialog.period_key}`}
              </h3>
            </header>
            <p className="muted small" data-testid="periode-modal-warning">
              {dialog.kind === "close" ? CLOSE_WARNING : REOPEN_WARNING}
            </p>
            {actionError && (
              <p className="error" data-testid="periode-modal-error">{actionError}</p>
            )}
            <label>
              {dialog.kind === "close" ? "Alasan penutupan" : "Alasan membuka kembali"}
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={3}
                autoFocus
                data-testid="periode-modal-reason"
              />
            </label>
            <div className="delete-modal-actions">
              <button type="button" className="ghost" disabled={Boolean(actingKey)} onClick={closeDialog}>
                Batal
              </button>
              <button
                type="button"
                className="primary"
                disabled={!reasonValid || Boolean(actingKey)}
                onClick={confirmAction}
                data-testid="periode-modal-confirm"
              >
                {dialog.kind === "close" ? "Tutup" : "Buka"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
