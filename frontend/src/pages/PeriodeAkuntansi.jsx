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

export default function PeriodeAkuntansi() {
  const { hasPerm } = useAuth();
  const canClose = hasPerm("accounting.period.close");
  const canReopen = hasPerm("accounting.period.reopen");
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actingKey, setActingKey] = useState("");
  const [actionError, setActionError] = useState("");

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

  const runAction = async (row, kind) => {
    if (actingKey) return;
    setActingKey(`${kind}:${row.period_key}`);
    setActionError("");
    try {
      if (kind === "close") {
        await api.closeAccountingPeriod(row.period_key, {});
      } else {
        await api.reopenAccountingPeriod(row.period_key, {});
      }
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
                          onClick={() => runAction(row, "reopen")}
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
                        onClick={() => runAction(row, "close")}
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
    </div>
  );
}
