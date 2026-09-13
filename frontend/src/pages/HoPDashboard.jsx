import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AlertTriangle, CalendarClock, CheckCircle2, Clock, Cloud,
  Download, FileWarning, Flame, Layers, PauseCircle, RefreshCw, ShieldAlert, Truck,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import ReminderList from "../components/ReminderList";
import ActionCenter from "../components/ActionCenter";
import useAutoRefresh from "../lib/useAutoRefresh";
import { NAV_KEYS } from "../lib/navAccess";
import { DASHBOARD_LOAD_FAILED, dashboardFetchErrorMessage, dashboardPageView } from "../lib/dashboardAuth";

const CARD_VIEWS = {
  active: "active",
  urgent: "urgent",
  today: "due_today",
  h1: "h1",
  h6: "h6",
  overdue: "overdue",
  design: "design",
  payment: "payment",
  hold: "hold",
  completed: "completed",
  work_today: "work_today",
};

const KpiCard = ({ icon: Icon, label, value, tone = "default", testid, extraTestid, onClick }) => {
  const clickable = typeof onClick === "function";
  const Comp = clickable ? "button" : "div";
  return (
    <Comp
      type={clickable ? "button" : undefined}
      className={`kpi-card kpi-${tone}${clickable ? " kpi-card-clickable" : ""}`}
      data-testid={testid}
      onClick={onClick}
    >
      {extraTestid ? <span data-testid={extraTestid} hidden /> : null}
      <div className="kpi-head">
        <Icon size={18} />
        <span>{label}</span>
      </div>
      <strong>{value}</strong>
    </Comp>
  );
};

export default function HoPDashboard() {
  const { user, loading: authLoading, hasPerm } = useAuth();
  const navigate = useNavigate();
  const canStatusPo = hasPerm(NAV_KEYS.statusPo);
  const [data, setData] = useState(null);
  const [reminders, setReminders] = useState(null);
  const [accurate, setAccurate] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const openStatusPo = (view) => {
    if (!canStatusPo) return;
    navigate(`/laporan/status-po?view=${view}`);
  };

  const openDeliveryToday = () => {
    if (!canStatusPo) return;
    navigate("/produksi/jadwal-pengiriman?status=hari_ini");
  };

  const load = async () => {
    if (authLoading || !user) return;
    setBusy(true);
    setErr("");
    try {
      const [d, r, a] = await Promise.all([
        api.dashboardSummary(),
        api.reminders().catch(() => null),
        api.accurateStatus().catch(() => null),
      ]);
      setData(d);
      setReminders(r);
      setAccurate(a);
    } catch (e) {
      setErr(dashboardFetchErrorMessage(e, { user, loading: authLoading }) || DASHBOARD_LOAD_FAILED);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (authLoading || !user) return undefined;
    load();
    // eslint-disable-next-line
  }, [authLoading, user]);

  const { lastUpdated: dashLastUpdated, isPolling: dashIsPolling } = useAutoRefresh(
    () => api.dashboardSummary(),
    { intervalMs: 30_000, enabled: !!data, onData: (d) => setData(d) }
  );

  const pageView = dashboardPageView({ loading: authLoading, user, data, err });
  if (pageView === "loading") return <div className="loading" data-testid="hop-loading">Memuat dashboard...</div>;
  if (pageView === "unauthenticated") return null;
  if (pageView === "error") return <div className="error-page" data-testid="hop-error">{err}</div>;

  const { kpi, generated_at } = data;
  const click = (key) => (canStatusPo ? () => openStatusPo(CARD_VIEWS[key]) : undefined);

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
        <button className="icon-button" onClick={() => { load(); }} data-testid="hop-refresh-button" aria-label="Muat ulang">
          <RefreshCw size={19} className={busy || dashIsPolling ? "spin" : ""} />
        </button>
      </div>

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

      <section className="kpi-grid" data-testid="kpi-grid">
        <KpiCard icon={Layers} label="PO Aktif" value={kpi.total_active} testid="kpi-active" onClick={click("active")} />
        <KpiCard
          icon={CalendarClock}
          label="Pengerjaan Hari Ini"
          value={data.work_today?.total || 0}
          testid="kpi-work-today"
          extraTestid="today-work"
          onClick={click("work_today")}
        />
        <KpiCard
          icon={Truck}
          label="Pengiriman Hari Ini"
          value={data.shipments_today?.total || 0}
          testid="kpi-delivery-today"
          extraTestid="today-ship"
          onClick={canStatusPo ? openDeliveryToday : undefined}
        />
        <KpiCard icon={Flame} label="PO Urgent" value={kpi.total_urgent} tone="urgent" testid="kpi-urgent" onClick={click("urgent")} />
        <KpiCard icon={CalendarClock} label="Kirim Hari Ini" value={kpi.due_today} tone="warn" testid="kpi-today" onClick={click("today")} />
        <KpiCard icon={CalendarClock} label="H-1 Pengiriman" value={kpi.deadline_h1 || 0} tone="warn" testid="kpi-deadline-h1" onClick={click("h1")} />
        <KpiCard icon={ShieldAlert} label="Kirim ≤ 6 Jam" value={kpi.deadline_h6 || 0} tone="danger" testid="kpi-deadline-h6" onClick={click("h6")} />
        <KpiCard icon={ShieldAlert} label="Terlambat Kirim" value={kpi.overdue} tone="danger" testid="kpi-overdue" onClick={click("overdue")} />
        <KpiCard icon={FileWarning} label="Menunggu Approval Design" value={kpi.waiting_design_approval} testid="kpi-waiting-design" onClick={click("design")} />
        <KpiCard icon={Clock} label="Menunggu Approval Payment" value={kpi.waiting_payment_approval} testid="kpi-waiting-payment" onClick={click("payment")} />
        <KpiCard icon={PauseCircle} label="HOLD" value={kpi.hold} tone="warn" testid="kpi-hold" onClick={click("hold")} />
        <KpiCard icon={CheckCircle2} label="Selesai (Terkirim)" value={kpi.completed} tone="success" testid="kpi-completed" onClick={click("completed")} />
      </section>

      <section className="panel" data-testid="reminders-panel">
        <header className="panel-head">
          <h3><AlertTriangle size={14} /> Reminder Tanggal Pengiriman</h3>
          <span className="muted small">Otomatis dari Tanggal Pengiriman · H-1 &amp; 6 jam sebelum · overdue setelah lewat</span>
        </header>
        {reminders && <ReminderList {...reminders} testid="hop-reminders" />}
        <ActionCenter />
      </section>
    </div>
  );
}
