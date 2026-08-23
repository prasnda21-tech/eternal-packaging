import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Info, RefreshCw } from "lucide-react";
import api from "../lib/api";
import { STAGES, formatDate } from "../lib/constants";

export default function AnalyticsOperators() {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.analyticsOperators();
      setData(d);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat analitik");
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => { load(); }, []);

  if (!data && !err) return <div className="loading">Memuat analitik...</div>;
  if (err) return <div className="error-page">{err}</div>;

  return (
    <div data-testid="analytics-operators-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Analitik · Data Dasar</p>
          <h1 data-testid="analytics-title">Analitik Operator</h1>
          <p className="subhead">Data mentah untuk penentuan KPI · tidak ada rating final</p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Link to="/dashboard" className="ghost sm"><ArrowLeft size={14} /> Dashboard</Link>
          <button className="icon-button" onClick={load} data-testid="analytics-refresh">
            <RefreshCw size={19} className={busy ? "spin" : ""} />
          </button>
        </div>
      </div>

      <div className="analytics-note" data-testid="analytics-note">
        <Info size={14} />
        <p>{data.note}</p>
      </div>

      {data.operators.length === 0 ? (
        <p className="empty">Belum ada operator ditugaskan pada PO.</p>
      ) : (
        data.operators.map((op) => (
          <section key={op.email} className="panel" data-testid={`analytics-op-${op.email}`}>
            <header className="panel-head">
              <div>
                <h3>{op.name}</h3>
                <span className="mono muted">{op.email}</span>
              </div>
              <div className="analytics-op-summary">
                <span>Total PO: <b>{op.all_time_po_count}</b></span>
                <span>Aktif: <b>{op.active_po_count}</b></span>
                <span>Selesai: <b>{op.completed_po_count}</b></span>
                <span>Terjadwal: <b className="mono">{op.qty_scheduled.toLocaleString("id-ID")}</b></span>
                <span>Selesai: <b className="mono status-ok">{op.qty_completed.toLocaleString("id-ID")}</b></span>
                <span>Sisa: <b className="mono status-wait">{op.qty_remaining.toLocaleString("id-ID")}</b></span>
              </div>
            </header>

            <div className="analytics-table" data-testid={`analytics-history-${op.email}`}>
              <div className="analytics-head">
                <span>PO</span>
                <span>Outlet</span>
                <span>Qty terjadwal</span>
                <span>Qty selesai</span>
                <span>Sisa</span>
                <span>Stage</span>
                <span>Ditugaskan</span>
                <span>Progress pertama</span>
                <span>Progress terakhir</span>
              </div>
              {op.po_history.map((p) => (
                <Link key={p.po_id} to={`/po/${p.po_id}`} className="analytics-row" data-testid={`analytics-po-${op.email}-${p.po_number}`}>
                  <span className="mono">{p.po_number}</span>
                  <span>{p.outlet}</span>
                  <span className="mono">{p.quantity.toLocaleString("id-ID")}</span>
                  <span className="mono status-ok">{p.qty_completed.toLocaleString("id-ID")}</span>
                  <span className="mono status-wait">{p.qty_remaining.toLocaleString("id-ID")}</span>
                  <span className="stage-chip">{p.stage_label}</span>
                  <span className="mono muted">{p.assigned_at ? formatDate(p.assigned_at) : "-"}</span>
                  <span className="mono muted">{p.first_progress_at ? formatDate(p.first_progress_at) : "-"}</span>
                  <span className="mono muted">{p.last_progress_at ? formatDate(p.last_progress_at) : "-"}</span>
                </Link>
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}
