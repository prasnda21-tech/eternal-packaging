import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle, ArrowUpRight, CalendarClock, CheckCircle2, Clock, Cloud,
  Download, FileWarning, Flame, Layers, RefreshCw, ShieldAlert, Users, X, PauseCircle,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { STAGES, formatDeadline, formatDate } from "../lib/constants";
import ReminderList from "../components/ReminderList";
import ActionCenter from "../components/ActionCenter";
import TodayActivityCards from "../components/TodayActivityCards";
import useAutoRefresh from "../lib/useAutoRefresh";

// Semantic filter config: label + backend-response list key.
// SATU PO boleh muncul di beberapa filter list bila predikatnya independen
// (mis. design_pending + payment_pending). Cancelled dikecualikan di backend.
const FILTERS = {
  active:    { label: "PO Aktif",                    listKey: "active",                    kpiKey: "total_active" },
  urgent:    { label: "PO Urgent",                   listKey: "urgent",                    kpiKey: "total_urgent" },
  today:     { label: "Deadline Hari Ini",           listKey: "due_today",                 kpiKey: "due_today" },
  h1:        { label: "H-1 Pengiriman",              listKey: "deadline_h1",               kpiKey: "deadline_h1" },
  h6:        { label: "Deadline ≤ 6 Jam",            listKey: "deadline_h6",               kpiKey: "deadline_h6" },
  overdue:   { label: "Overdue",                     listKey: "overdue",                   kpiKey: "overdue" },
  design:    { label: "Menunggu Approval Design",    listKey: "waiting_design_approval",   kpiKey: "waiting_design_approval" },
  payment:   { label: "Menunggu Approval Payment",   listKey: "waiting_payment_approval",  kpiKey: "waiting_payment_approval" },
  hold:      { label: "HOLD",                        listKey: "hold",                      kpiKey: "hold" },
  completed: { label: "Selesai (Terkirim)",          listKey: "completed",                 kpiKey: "completed" },
};

const KpiCard = ({ icon: Icon, label, value, tone = "default", testid, active, onClick }) => (
  <button
    type="button"
    className={`kpi-card kpi-${tone} kpi-card-clickable ${active ? "kpi-card-active" : ""}`}
    data-testid={testid}
    data-active={active ? "true" : "false"}
    aria-pressed={active}
    onClick={onClick}
  >
    <div className="kpi-head">
      <Icon size={18} />
      <span>{label}</span>
    </div>
    <strong>{value}</strong>
  </button>
);

// Badge visualisasi deadline effective (source of truth: backend field
// `delivery_deadline_status`). Prioritas overdue > h6 > h1 > terkirim/normal.
const DeadlineBadge = ({ status, effective }) => {
  if (!status || status === "normal" || status === "unknown") return null;
  if (status === "terkirim") return null;
  const cfg = ({
    overdue: { text: "OVERDUE", cls: "urgent-badge", tone: "danger", title: "Deadline terlewat" },
    h6: { text: "≤6 JAM", cls: "status-warn", tone: "warn", title: "Deadline ≤ 6 jam" },
    h1: { text: "H-1", cls: "standard-badge", tone: "warn", title: "H-1 pengiriman" },
  })[status];
  if (!cfg) return null;
  const style = {
    overdue: { background: "#fce6e6", color: "#8f1f1f", border: "1px solid #efb9b9" },
    h6: { background: "#fce6e6", color: "#8f1f1f", border: "1px solid #efb9b9" },
    h1: { background: "#fef4d6", color: "#7a5a1e", border: "1px solid #efd9a1" },
  }[status];
  return (
    <span
      className={cfg.cls}
      title={effective ? `${cfg.title} · ${formatDeadline(effective)}` : cfg.title}
      data-testid={`deadline-badge-${status}`}
      style={{ ...style, padding: "2px 6px", borderRadius: 4, fontSize: 11, fontWeight: 700 }}
    >
      {cfg.text}
    </span>
  );
};

const PORow = ({ o, testidPrefix }) => (
  <Link to={`/po/${o.id}`} className="hop-po-row" data-testid={`${testidPrefix}-${o.po_number}`}>
    <div className="hop-po-main">
      <span className="mono">{o.po_number}</span>
      <strong>{o.outlet}</strong>
      <span className="muted">{o.customer}</span>
    </div>
    <div className="hop-po-side">
      {o.hold && <span className="status-warn" title={(o.hold_reasons || []).join(" · ")}>HOLD</span>}
      <DeadlineBadge status={o.delivery_deadline_status} effective={o.delivery_deadline_effective} />
      <span className={o.priority === "urgent" ? "urgent-badge" : "standard-badge"}>{o.priority === "urgent" ? "URGENT" : "NORMAL"}</span>
      <span className="mono" title="Deadline">{formatDeadline(o.delivery_deadline_effective || o.deadline)}</span>
      <span className="mono muted small" data-testid={`hop-po-workdate-${o.id}`} title="Tanggal Pengerjaan">
        Pengerjaan: {o.work_date ? o.work_date : "Belum dijadwalkan"}
      </span>
      <span className="stage-chip">{STAGES[o.current_stage] || "—"}</span>
      <ArrowUpRight size={14} />
    </div>
  </Link>
);

export default function HoPDashboard() {
  const { user, hasPerm } = useAuth();
  const [data, setData] = useState(null);
  const [reminders, setReminders] = useState(null);
  const [accurate, setAccurate] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [selectedFilter, setSelectedFilter] = useState(null);

  const load = async () => {
    setBusy(true);
    setErr("");
    try {
      const [d, r, a] = await Promise.all([
        api.dashboardSummary(),
        api.reminders(),
        api.accurateStatus().catch(() => null),
      ]);
      setData(d);
      setReminders(r);
      setAccurate(a);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat dashboard");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { load(); }, []);

  // Batch 2: auto-refresh 30s (Dashboard read-only).
  const { lastUpdated: dashLastUpdated, isPolling: dashIsPolling, refreshNow: refreshDash } = useAutoRefresh(
    () => api.dashboardSummary(),
    { intervalMs: 30_000, enabled: !!data, onData: (d) => setData(d) }
  );

  const toggleFilter = (key) => setSelectedFilter((cur) => (cur === key ? null : key));
  const clearFilter = () => setSelectedFilter(null);

  if (!data && !err) return <div className="loading" data-testid="hop-loading">Memuat dashboard...</div>;
  if (err) return <div className="error-page" data-testid="hop-error">{err}</div>;

  const { kpi, stage_breakdown, operator_load, generated_at } = data;
  const maxStage = Math.max(1, ...stage_breakdown.map((s) => s.count));

  const filterCfg = selectedFilter ? FILTERS[selectedFilter] : null;
  const filteredList = filterCfg ? (data[filterCfg.listKey] || []) : [];

  return (
    <div className="hop-dashboard" data-testid="hop-dashboard">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Head of Production · Kontrol Produksi</p>
          <h1 data-testid="hop-title">Dashboard Produksi</h1>
          <p className="subhead">
            Ringkasan real-time dari database PO · pembaruan {formatDate(generated_at)}
            {dashLastUpdated && (
              <span data-testid="hop-last-updated" style={{ marginLeft: 10, fontSize: 12, color: "#7a6f52" }}>
                · Terakhir diperbarui: {new Date(dashLastUpdated).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}
              </span>
            )}
          </p>
        </div>
        <button className="icon-button" onClick={() => { load(); refreshDash(); }} data-testid="hop-refresh-button" aria-label="Muat ulang">
          <RefreshCw size={19} className={busy || dashIsPolling ? "spin" : ""} />
        </button>
      </div>

      {/* Accurate status banner (READ-ONLY notice) */}
      {accurate && (
        <section className="accurate-banner" data-testid="accurate-banner">
          <div>
            <Cloud size={16} />
            <div>
              <strong>Accurate Online · Mode {accurate.mode === "live" ? "Live" : "MOCK"} · READ-ONLY</strong>
              <p>{accurate.message}</p>
            </div>
          </div>
          {hasPerm("po.import.view") && (
            <Link to="/admin/import" className="ghost sm" data-testid="import-link-banner">
              <Download size={14} /> Import PO dari Accurate
            </Link>
          )}
        </section>
      )}

      {/* KPI Grid (clickable) */}
      <section className="kpi-grid" data-testid="kpi-grid">
        <KpiCard icon={Layers}       label="PO Aktif"                    value={kpi.total_active}               testid="kpi-active"          active={selectedFilter === "active"}    onClick={() => toggleFilter("active")} />
        <KpiCard icon={Flame}        label="PO Urgent"                   value={kpi.total_urgent}               tone="urgent" testid="kpi-urgent"          active={selectedFilter === "urgent"}    onClick={() => toggleFilter("urgent")} />
        <KpiCard icon={CalendarClock} label="Deadline Hari Ini"          value={kpi.due_today}                  tone="warn"   testid="kpi-today"           active={selectedFilter === "today"}     onClick={() => toggleFilter("today")} />
        <KpiCard icon={CalendarClock} label="H-1 Pengiriman"              value={kpi.deadline_h1 || 0}           tone="warn"   testid="kpi-deadline-h1"     active={selectedFilter === "h1"}        onClick={() => toggleFilter("h1")} />
        <KpiCard icon={ShieldAlert}   label="Deadline ≤ 6 Jam"            value={kpi.deadline_h6 || 0}           tone="danger" testid="kpi-deadline-h6"     active={selectedFilter === "h6"}        onClick={() => toggleFilter("h6")} />
        <KpiCard icon={ShieldAlert}  label="Overdue"                     value={kpi.overdue}                    tone="danger" testid="kpi-overdue"         active={selectedFilter === "overdue"}   onClick={() => toggleFilter("overdue")} />
        <KpiCard icon={FileWarning}  label="Menunggu Approval Design"    value={kpi.waiting_design_approval}                  testid="kpi-waiting-design"  active={selectedFilter === "design"}    onClick={() => toggleFilter("design")} />
        <KpiCard icon={Clock}        label="Menunggu Approval Payment"   value={kpi.waiting_payment_approval}                 testid="kpi-waiting-payment" active={selectedFilter === "payment"}   onClick={() => toggleFilter("payment")} />
        <KpiCard icon={PauseCircle}  label="HOLD"                        value={kpi.hold}                       tone="warn"   testid="kpi-hold"            active={selectedFilter === "hold"}      onClick={() => toggleFilter("hold")} />
        <KpiCard icon={CheckCircle2} label="Selesai (Terkirim)"          value={kpi.completed}                  tone="success" testid="kpi-completed"      active={selectedFilter === "completed"} onClick={() => toggleFilter("completed")} />
      </section>

      <TodayActivityCards data={data} />

      {/* Dynamic filtered list — muncul tepat di bawah card ketika salah satu card diklik */}
      {filterCfg && (
        <section className="panel" data-testid="hop-filter-panel">
          <header className="panel-head">
            <h3 data-testid="hop-filter-title">
              Menampilkan: {filterCfg.label}{" "}
              <span className="muted small" data-testid="hop-filter-count">
                ({filteredList.length} PO)
              </span>
            </h3>
            <button type="button" className="ghost sm" onClick={clearFilter} data-testid="hop-filter-clear">
              <X size={14} /> Hapus Filter
            </button>
          </header>
          {filteredList.length === 0 ? (
            <p className="empty" data-testid="hop-filter-empty">Tidak ada PO pada kategori ini.</p>
          ) : (
            <div className="hop-po-list" data-testid="hop-filter-list">
              {filteredList.map((o) => (
                <PORow key={o.id} o={o} testidPrefix={`hop-row-${selectedFilter}`} />
              ))}
            </div>
          )}
        </section>
      )}

      {/* Reminders */}
      <section className="panel" data-testid="reminders-panel">
        <header className="panel-head">
          <h3><AlertTriangle size={14} /> Reminder Deadline</h3>
          <span className="muted small">Otomatis dihitung dari deadline PO · H-1 &amp; 6 jam sebelum deadline · overdue setelah lewat</span>
        </header>
        {reminders && <ReminderList {...reminders} testid="hop-reminders" />}

        {/* Batch 3 · Action Center — tugas berdasarkan Effective Permission */}
        <ActionCenter />
      </section>

      {/* Stage Breakdown */}
      <section className="panel" data-testid="stage-breakdown-panel">
        <header className="panel-head"><h3>Progres per Tahap</h3></header>
        <div className="stage-breakdown">
          {stage_breakdown.map((s) => (
            <div key={s.stage} className="stage-bar-row" data-testid={`stage-count-${s.stage}`}>
              <span className="stage-lbl"><b>{String(s.stage + 1).padStart(2, "0")}</b> {s.label}</span>
              <div className="stage-track">
                <div className="stage-fill" style={{ width: `${(s.count / maxStage) * 100}%` }} />
              </div>
              <span className="stage-count">{s.count}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Operator load */}
      <section className="panel" data-testid="operator-load-panel">
        <header className="panel-head">
          <h3><Users size={14} /> Beban Operator</h3>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span className="muted small">Qty terjadwal / selesai / sisa · dihitung dari data PO aktif</span>
            <Link to="/analytics/operators" className="ghost sm" data-testid="analytics-link">Lihat analitik lengkap</Link>
          </div>
        </header>
        {operator_load.length === 0 ? <p className="empty">Belum ada operator ditugaskan pada PO aktif.</p> : (
          <div className="op-load-table">
            <div className="op-load-head">
              <span>Operator</span>
              <span>PO Aktif</span>
              <span>Terjadwal</span>
              <span>Selesai</span>
              <span>Sisa</span>
              <span>Detail</span>
            </div>
            {operator_load.map((r) => {
              const pct = r.qty_scheduled ? Math.round((r.qty_completed / r.qty_scheduled) * 100) : 0;
              return (
                <details key={r.email} className="op-load-row" data-testid={`op-load-${r.email}`}>
                  <summary>
                    <span><strong>{r.name}</strong><span className="mono block">{r.email}</span></span>
                    <span>{r.active_po_count}</span>
                    <span className="mono">{r.qty_scheduled.toLocaleString("id-ID")}</span>
                    <span className="mono status-ok">{r.qty_completed.toLocaleString("id-ID")}</span>
                    <span className="mono status-wait">{r.qty_remaining.toLocaleString("id-ID")}</span>
                    <span>
                      <div className="pct-track"><div className="pct-fill" style={{ width: `${pct}%` }} /></div>
                      <span className="mono">{pct}%</span>
                    </span>
                  </summary>
                  <div className="op-load-detail">
                    {r.pos.map((p) => (
                      <Link key={p.po_id} to={`/po/${p.po_id}`} className="op-load-po" data-testid={`op-load-po-${r.email}-${p.po_number}`}>
                        <span className="mono">{p.po_number}</span>
                        <span>{p.outlet}</span>
                        <span className="stage-chip">{p.stage_label}</span>
                        <span className="mono">{p.qty_completed.toLocaleString("id-ID")} / {p.quantity.toLocaleString("id-ID")}</span>
                        <span className={p.priority === "urgent" ? "urgent-badge" : "standard-badge"}>{p.priority === "urgent" ? "URGENT" : "NORMAL"}</span>
                        <span className="mono muted">{formatDeadline(p.deadline)}</span>
                      </Link>
                    ))}
                  </div>
                </details>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
