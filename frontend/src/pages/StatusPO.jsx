import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import api from "../lib/api";
import { formatDisplayDate } from "../lib/constants";
import { deliveredPercentLabel, statusPoViewLabel } from "../lib/statusPo";

export default function StatusPO() {
  const [params] = useSearchParams();
  const view = params.get("view") || "";
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.statusPo(view ? { view } : {});
      setRows(d.rows || []);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Status PO"));
    } finally {
      setBusy(false);
    }
  }, [view]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="ship-status-page status-po-page" data-testid="status-po-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading">
          <h1>Status PO</h1>
          {view ? (
            <p className="muted small" data-testid="status-po-view-label">{statusPoViewLabel(view)}</p>
          ) : null}
        </header>
        <div className="ship-status-toolbar">
          <button type="button" className="ship-icon-btn" onClick={load} title="Muat ulang" data-testid="status-po-refresh" disabled={busy}>
            <RefreshCw size={14} className={busy ? "spin" : ""} />
          </button>
        </div>
        {err && <div className="error" data-testid="status-po-error">{err}</div>}
      </div>
      <div className="ship-status-grid" data-testid="status-po-table">
        <table className="ship-table">
          <thead>
            <tr>
              <th>No PO</th>
              <th>Tanggal</th>
              <th>Pelanggan</th>
              <th>Status</th>
              <th className="num">Status Terkirim</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} data-testid={`status-po-row-${r.po_number}`}>
                <td className="mono">
                  {r.id ? (
                    <Link to={`/po/${r.id}`} data-testid={`status-po-link-${r.po_number}`}>{r.po_number}</Link>
                  ) : (r.po_number || "-")}
                </td>
                <td className="mono">{formatDisplayDate(r.order_date)}</td>
                <td className="ship-cust">{r.customer || "-"}</td>
                <td data-testid={`status-po-status-${r.po_number}`}>{r.status || "-"}</td>
                <td className="num mono" data-testid={`status-po-delivered-${r.po_number}`}>
                  {deliveredPercentLabel(r.percent_delivered)}
                </td>
              </tr>
            ))}
            {!rows.length && !busy && (
              <tr><td colSpan={5} className="empty-state">Tidak ada PO.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
