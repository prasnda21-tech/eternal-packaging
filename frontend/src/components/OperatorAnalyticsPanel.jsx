import { useEffect, useMemo, useState } from "react";
import { BarChart3, RefreshCw } from "lucide-react";
import api from "../lib/api";

const PERIOD_OPTS = [
  { key: "today", label: "Hari ini" },
  { key: "week", label: "Minggu ini" },
  { key: "month", label: "Bulan ini" },
  { key: "custom", label: "Custom" },
];

function fmtQty(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function fmtPct(n, assigned) {
  if (!assigned) return "—";
  return `${Number(n || 0).toFixed(2)}%`;
}

function KpiCard({ label, value, hint, testid }) {
  return (
    <div className="op-an-kpi" data-testid={testid}>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <em>{hint}</em> : null}
    </div>
  );
}

export default function OperatorAnalyticsPanel() {
  const [period, setPeriod] = useState("month");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [operatorId, setOperatorId] = useState("all");
  const [customer, setCustomer] = useState("all");
  const [item, setItem] = useState("all");
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = async () => {
    if (period === "custom" && (!dateFrom || !dateTo)) return;
    setBusy(true);
    setErr("");
    try {
      const params = { period };
      if (period === "custom") {
        if (dateFrom) params.from = dateFrom;
        if (dateTo) params.to = dateTo;
      }
      if (operatorId && operatorId !== "all") params.operator_id = operatorId;
      if (customer && customer !== "all") params.customer = customer;
      if (item && item !== "all") params.item = item;
      const d = await api.analyticsOperatorKpis(params);
      setData(d);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat analitik operator");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [period, dateFrom, dateTo, operatorId, customer, item]);

  const operators = data?.filters?.operators || [];
  const customers = data?.filters?.customers || [];
  const items = data?.filters?.items || [];
  const kpis = data?.kpis || {};
  const rows = data?.operators || [];
  const resolvedFrom = data?.period?.from || dateFrom;
  const resolvedTo = data?.period?.to || dateTo;

  const customDatesReady = period !== "custom" || (dateFrom && dateTo);

  const emptyHint = useMemo(() => {
    if (busy) return "Memuat…";
    if (!rows.length) return "Tidak ada aktivitas operator pada periode ini.";
    return "";
  }, [busy, rows.length]);

  return (
    <section className="op-an" data-testid="operator-analytics-panel">
      <div className="op-an-head">
        <div>
          <p className="eyebrow">Jadwal Produksi · Read only</p>
          <h2 data-testid="op-an-title">Analitik Operator</h2>
        </div>
        <button className="ghost sm" onClick={load} disabled={busy} data-testid="op-an-refresh">
          <RefreshCw size={12} className={busy ? "spin" : ""} /> Refresh
        </button>
      </div>

      <section className="prod-filters op-an-filters" data-testid="op-an-filters">
        <div className="prod-filter-row">
          <span className="prod-filter-kicker">Periode</span>
          <select value={period} onChange={(e) => setPeriod(e.target.value)} data-testid="op-an-period">
            {PERIOD_OPTS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
          <label className="prod-inline-date">
            Tanggal dari
            <input
              type="date"
              value={period === "custom" ? dateFrom : (resolvedFrom || "")}
              onChange={(e) => { setPeriod("custom"); setDateFrom(e.target.value); }}
              disabled={period !== "custom"}
              data-testid="op-an-date-from"
            />
          </label>
          <label className="prod-inline-date">
            Tanggal sampai
            <input
              type="date"
              value={period === "custom" ? dateTo : (resolvedTo || "")}
              onChange={(e) => { setPeriod("custom"); setDateTo(e.target.value); }}
              disabled={period !== "custom"}
              data-testid="op-an-date-to"
            />
          </label>
        </div>
        <div className="prod-filter-row">
          <span className="prod-filter-kicker">Filter</span>
          <select value={operatorId} onChange={(e) => setOperatorId(e.target.value)} data-testid="op-an-operator">
            <option value="all">Semua operator</option>
            {operators.map((o) => <option key={o.id} value={o.id}>{o.name || o.id}</option>)}
          </select>
          <select value={customer} onChange={(e) => setCustomer(e.target.value)} data-testid="op-an-customer">
            <option value="all">Semua customer</option>
            {customers.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <select value={item} onChange={(e) => setItem(e.target.value)} data-testid="op-an-item">
            <option value="all">Semua item</option>
            {items.map((it) => <option key={it} value={it}>{it}</option>)}
          </select>
          {!customDatesReady && <span className="muted small">Isi tanggal dari &amp; sampai untuk Custom.</span>}
        </div>
      </section>

      {err && <div className="error" data-testid="op-an-error">{err}</div>}

      <div className="op-an-kpi-row" data-testid="op-an-kpis">
        <KpiCard
          label="Total Produksi"
          value={`${fmtQty(kpis.total_completed)} pcs`}
          hint={`Target ${fmtQty(kpis.total_assigned)} · sisa ${fmtQty(kpis.total_remaining)}`}
          testid="op-an-kpi-completed"
        />
        <KpiCard
          label="Avg / Minggu"
          value={`${fmtQty(kpis.avg_weekly_qty)} pcs`}
          hint={`${kpis.active_weeks || 0} minggu aktif · ISO WIB`}
          testid="op-an-kpi-weekly"
        />
        <KpiCard
          label="Avg / Hari"
          value={`${fmtQty(kpis.avg_daily_qty)} pcs`}
          hint={`${kpis.active_days || 0} hari kerja`}
          testid="op-an-kpi-daily"
        />
        <KpiCard
          label="Avg Achievement"
          value={fmtPct(kpis.avg_achievement, kpis.total_assigned)}
          hint={`Selesai ${fmtQty(kpis.total_completed)} / target ${fmtQty(kpis.total_assigned)}`}
          testid="op-an-kpi-achievement"
        />
        <KpiCard
          label="Total Job"
          value={fmtQty(kpis.total_jobs)}
          hint={`Assigned ${fmtQty(kpis.jobs_assigned)} · selesai ${fmtQty(kpis.jobs_completed)} · sisa ${fmtQty(kpis.jobs_remaining)}`}
          testid="op-an-kpi-jobs"
        />
      </div>

      <div className="prod-table-wrap op-an-table-wrap" data-testid="op-an-table">
        <table className="prod-table op-an-table">
          <thead>
            <tr>
              <th>Operator</th>
              <th className="num">Assigned</th>
              <th className="num">Completed</th>
              <th className="num">Remaining</th>
              <th className="num">Avg/Minggu</th>
              <th className="num">Avg/Hari</th>
              <th className="num">Job</th>
              <th className="num">Achievement</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.email} data-testid={`op-an-row-${r.email}`}>
                <td>
                  <strong>{r.name}</strong>
                  <div className="muted small mono">{r.email}</div>
                </td>
                <td className="num">{fmtQty(r.assigned)}</td>
                <td className="num">{fmtQty(r.completed)}</td>
                <td className="num">{fmtQty(r.remaining)}</td>
                <td className="num">{fmtQty(r.avg_weekly_qty)}</td>
                <td className="num">{fmtQty(r.avg_daily_qty)}</td>
                <td className="num" title={`Assigned ${r.jobs_assigned} · selesai ${r.jobs_completed} · sisa ${r.jobs_remaining}`}>
                  {fmtQty(r.job_count)}
                </td>
                <td className="num">{fmtPct(r.achievement, r.assigned)}</td>
              </tr>
            ))}
            {!rows.length && !busy && (
              <tr>
                <td colSpan={8} className="empty-state">{emptyHint}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <p className="muted small op-an-note">
        <BarChart3 size={12} style={{ verticalAlign: "-2px" }} /> Qty selesai = aktual per operator, bukan qty PO.
        Minggu = ISO Senin–Minggu (WIB). Avg/hari hanya menghitung hari yang ada progress.
        {data?.period?.from && (
          <span data-testid="op-an-period-range"> · {data.period.from} → {data.period.to}</span>
        )}
      </p>
    </section>
  );
}
