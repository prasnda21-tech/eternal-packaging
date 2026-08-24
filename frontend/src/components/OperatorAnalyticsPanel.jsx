import { useEffect, useMemo, useState } from "react";
import { BarChart3, RefreshCw, X } from "lucide-react";
import api from "../lib/api";

const PERIOD_OPTS = [
  { key: "today", label: "Hari ini" },
  { key: "week", label: "Minggu ini" },
  { key: "month", label: "Bulan ini" },
  { key: "custom", label: "Custom" },
];

const SECTIONS = [
  { key: "overview", label: "Overview" },
  { key: "performance", label: "Performance" },
  { key: "trend", label: "Trend & Ranking" },
  { key: "packing", label: "Packing" },
  { key: "prep", label: "Persiapan Cup" },
];

const RANK_OPTS = [
  { key: "output_qty", label: "Output Qty", field: "completed", na: false },
  { key: "achievement", label: "Achievement %", field: "achievement", na: false },
  { key: "on_time_pct", label: "On-Time %", field: "on_time_pct", na: true },
  { key: "sample_approval_pct", label: "Sample Approval %", field: "sample_approval_pct", na: true },
  { key: "job_count", label: "Job Count", field: "job_count", na: false },
];

function fmtQty(n) {
  return Number(n || 0).toLocaleString("id-ID");
}

function fmtPct(n, assigned) {
  if (!assigned) return "—";
  return `${Number(n || 0).toFixed(2)}%`;
}

function fmtPctOrNA(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "N/A";
  return `${Number(n).toFixed(1)}%`;
}

function fmtDaysOrNA(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "N/A";
  return `${Number(n).toLocaleString("id-ID", { maximumFractionDigits: 1 })} hari`;
}

function fmtWow(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n)) || !Number.isFinite(Number(n))) return "N/A";
  const v = Number(n);
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}%`;
}

function fmtDate(v) {
  if (!v) return "—";
  return String(v).slice(0, 10);
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

function ThinProgress({ completed, assigned }) {
  const pct = assigned > 0 ? Math.min(100, Math.max(0, (Number(completed) / Number(assigned)) * 100)) : 0;
  return (
    <div className="op-an-prog" title={`${fmtQty(completed)} / ${fmtQty(assigned)}`}>
      <i style={{ width: `${pct}%` }} />
    </div>
  );
}

function DualBar({ a, b, aLabel, bLabel }) {
  const max = Math.max(Number(a) || 0, Number(b) || 0, 1);
  return (
    <div className="op-an-dual">
      <div className="op-an-dual-row">
        <span>{aLabel}</span>
        <b>{fmtQty(a)}</b>
        <div className="op-an-prog op-an-prog-a"><i style={{ width: `${((Number(a) || 0) / max) * 100}%` }} /></div>
      </div>
      <div className="op-an-dual-row">
        <span>{bLabel}</span>
        <b>{fmtQty(b)}</b>
        <div className="op-an-prog op-an-prog-b"><i style={{ width: `${((Number(b) || 0) / max) * 100}%` }} /></div>
      </div>
    </div>
  );
}

function StackedOnTime({ onTime, late }) {
  const total = Math.max((Number(onTime) || 0) + (Number(late) || 0), 0);
  const ot = total ? ((Number(onTime) || 0) / total) * 100 : 0;
  const lt = total ? ((Number(late) || 0) / total) * 100 : 0;
  return (
    <div className="op-an-stack" title={`On-time ${fmtQty(onTime)} · Late ${fmtQty(late)}`}>
      {total === 0 ? <i className="empty" /> : (
        <>
          <i className="ot" style={{ width: `${ot}%` }} />
          <i className="lt" style={{ width: `${lt}%` }} />
        </>
      )}
    </div>
  );
}

function WeekBars({ weeks, testid }) {
  const max = Math.max(1, ...(weeks || []).map((w) => Number(w.qty) || 0));
  if (!weeks?.length) return <p className="empty-state">Tidak ada data minggu pada periode ini.</p>;
  return (
    <div className="op-an-weekbars" data-testid={testid}>
      {weeks.map((w) => (
        <div key={w.week} className="op-an-weekbar" title={`${w.label}: ${fmtQty(w.qty)} · WoW ${fmtWow(w.wow_pct)}`}>
          <div className="op-an-weekbar-col">
            <i style={{ height: `${((Number(w.qty) || 0) / max) * 100}%` }} />
          </div>
          <span>{w.label.replace(" ", "\n")}</span>
          <b>{fmtQty(w.qty)}</b>
          <em className={(w.wow_pct || 0) < 0 ? "neg" : ""}>{fmtWow(w.wow_pct)}</em>
        </div>
      ))}
    </div>
  );
}

function rankRows(rows, metricKey) {
  const spec = RANK_OPTS.find((x) => x.key === metricKey) || RANK_OPTS[0];
  const sorted = [...rows].sort((a, b) => {
    const va = a[spec.field];
    const vb = b[spec.field];
    const aNa = spec.na && (va === null || va === undefined);
    const bNa = spec.na && (vb === null || vb === undefined);
    if (aNa && bNa) return (a.name || "").localeCompare(b.name || "");
    if (aNa) return 1;
    if (bNa) return -1;
    return (Number(vb) || 0) - (Number(va) || 0) || (a.name || "").localeCompare(b.name || "");
  });
  let n = 0;
  return sorted.map((r) => {
    const v = r[spec.field];
    const isNa = spec.na && (v === null || v === undefined);
    if (!isNa) n += 1;
    return { ...r, _rank: isNa ? null : n, _value: v, _na: isNa };
  });
}

function fmtRankValue(metricKey, v, row) {
  if (v === null || v === undefined) return "N/A";
  if (metricKey === "achievement") return fmtPct(v, row.assigned);
  if (metricKey === "on_time_pct" || metricKey === "sample_approval_pct") return fmtPctOrNA(v);
  return fmtQty(v);
}

function jobStatusLabel(s) {
  if (s === "completed") return "Selesai";
  if (s === "overdue") return "Overdue";
  return "Aktif";
}

export default function OperatorAnalyticsPanel() {
  const [period, setPeriod] = useState("month");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [operatorId, setOperatorId] = useState("all");
  const [customer, setCustomer] = useState("all");
  const [item, setItem] = useState("all");
  const [section, setSection] = useState("overview");
  const [rankMetric, setRankMetric] = useState("output_qty");
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const [detailId, setDetailId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailStatus, setDetailStatus] = useState("all");
  const [detailPage, setDetailPage] = useState(1);

  const [packData, setPackData] = useState(null);
  const [packBusy, setPackBusy] = useState(false);
  const [packUser, setPackUser] = useState("all");
  const [packRole, setPackRole] = useState("all");
  const [packPo, setPackPo] = useState("all");
  const [packOpen, setPackOpen] = useState(null);

  const [prepData, setPrepData] = useState(null);
  const [prepBusy, setPrepBusy] = useState(false);
  const [prepUser, setPrepUser] = useState("all");
  const [prepRole, setPrepRole] = useState("all");
  const [prepPo, setPrepPo] = useState("all");
  const [prepOpen, setPrepOpen] = useState(null);

  const periodParams = () => {
    const params = { period };
    if (period === "custom") {
      if (dateFrom) params.from = dateFrom;
      if (dateTo) params.to = dateTo;
    }
    return params;
  };

  const load = async () => {
    if (period === "custom" && (!dateFrom || !dateTo)) return;
    setBusy(true);
    setErr("");
    try {
      const params = periodParams();
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

  const loadDetail = async (id, page = 1, status = detailStatus) => {
    if (!id) return;
    setDetailBusy(true);
    try {
      const params = { ...periodParams(), page, page_size: 50, status };
      if (customer && customer !== "all") params.customer = customer;
      if (item && item !== "all") params.item = item;
      const d = await api.analyticsOperatorDetail(id, params);
      setDetail(d);
      setDetailPage(page);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat detail operator");
    } finally {
      setDetailBusy(false);
    }
  };

  const loadPacking = async () => {
    if (period === "custom" && (!dateFrom || !dateTo)) return;
    setPackBusy(true);
    try {
      const params = periodParams();
      if (customer && customer !== "all") params.customer = customer;
      if (item && item !== "all") params.item = item;
      if (packUser && packUser !== "all") params.user_id = packUser;
      if (packRole && packRole !== "all") params.role = packRole;
      if (packPo && packPo !== "all") params.po = packPo;
      setPackData(await api.analyticsPacking(params));
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat laporan packing");
    } finally {
      setPackBusy(false);
    }
  };

  const loadPrep = async () => {
    if (period === "custom" && (!dateFrom || !dateTo)) return;
    setPrepBusy(true);
    try {
      const params = periodParams();
      if (customer && customer !== "all") params.customer = customer;
      if (item && item !== "all") params.item = item;
      if (prepUser && prepUser !== "all") params.user_id = prepUser;
      if (prepRole && prepRole !== "all") params.role = prepRole;
      if (prepPo && prepPo !== "all") params.po = prepPo;
      setPrepData(await api.analyticsPreparation(params));
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat laporan persiapan");
    } finally {
      setPrepBusy(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [period, dateFrom, dateTo, operatorId, customer, item]);
  useEffect(() => {
    if (section === "packing") loadPacking();
    /* eslint-disable-next-line */
  }, [section, period, dateFrom, dateTo, customer, item, packUser, packRole, packPo]);
  useEffect(() => {
    if (section === "prep") loadPrep();
    /* eslint-disable-next-line */
  }, [section, period, dateFrom, dateTo, customer, item, prepUser, prepRole, prepPo]);
  useEffect(() => {
    if (detailId) loadDetail(detailId, 1, detailStatus);
    /* eslint-disable-next-line */
  }, [detailId, detailStatus, period, dateFrom, dateTo, customer, item]);

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
  const chartRows = rows.slice(0, 12);
  const ranked = useMemo(() => rankRows(rows, rankMetric), [rows, rankMetric]);
  const weeks = data?.weekly_trend?.weeks || [];

  const openOperator = (email) => {
    setDetailId(email);
    setDetailStatus("all");
    setDetailPage(1);
    setDetail(null);
  };

  const packLinesFor = (email) => (packData?.lines || []).filter((l) => l.user_id === email);
  const prepPosFor = (email) =>
    (prepData?.pos || []).filter((p) => (p.users || []).some((u) => u.user_id === email));

  return (
    <section className="op-an" data-testid="operator-analytics-panel">
      <div className="op-an-head">
        <div>
          <p className="eyebrow">Jadwal Produksi · Read only</p>
          <h2 data-testid="op-an-title">Analitik Operator</h2>
        </div>
        <button className="ghost sm" onClick={() => { load(); if (section === "packing") loadPacking(); if (section === "prep") loadPrep(); }} disabled={busy} data-testid="op-an-refresh">
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

      <div className="op-an-subnav" role="tablist" data-testid="op-an-subnav">
        {SECTIONS.map((s) => (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={section === s.key}
            className={section === s.key ? "active" : ""}
            onClick={() => setSection(s.key)}
            data-testid={`op-an-tab-${s.key}`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {err && <div className="error" data-testid="op-an-error">{err}</div>}

      {section === "overview" && (
        <>
          <div className="op-an-kpi-row" data-testid="op-an-kpis">
            <KpiCard label="Total Produksi" value={`${fmtQty(kpis.total_completed)} pcs`} hint={`Target ${fmtQty(kpis.total_assigned)} · sisa ${fmtQty(kpis.total_remaining)}`} testid="op-an-kpi-completed" />
            <KpiCard label="Avg / Minggu" value={`${fmtQty(kpis.avg_weekly_qty)} pcs`} hint={`${kpis.active_weeks || 0} minggu aktif · ISO WIB`} testid="op-an-kpi-weekly" />
            <KpiCard label="Avg / Hari" value={`${fmtQty(kpis.avg_daily_qty)} pcs`} hint={`${kpis.active_days || 0} hari kerja`} testid="op-an-kpi-daily" />
            <KpiCard label="Avg Achievement" value={fmtPct(kpis.avg_achievement, kpis.total_assigned)} hint={`Selesai ${fmtQty(kpis.total_completed)} / target ${fmtQty(kpis.total_assigned)}`} testid="op-an-kpi-achievement" />
            <KpiCard label="Total Job" value={fmtQty(kpis.total_jobs)} hint={`Assigned ${fmtQty(kpis.jobs_assigned)} · selesai ${fmtQty(kpis.jobs_completed)} · sisa ${fmtQty(kpis.jobs_remaining)}`} testid="op-an-kpi-jobs" />
          </div>

          <div className="op-an-section-label">Workload Operator</div>
          <div className="op-an-kpi-row op-an-kpi-row-4" data-testid="op-an-workload-kpis">
            <KpiCard label="Assigned" value={`${fmtQty(kpis.total_assigned)} pcs`} testid="op-an-kpi-wl-assigned" />
            <KpiCard label="Completed" value={`${fmtQty(kpis.total_completed)} pcs`} testid="op-an-kpi-wl-completed" />
            <KpiCard label="Remaining" value={`${fmtQty(kpis.total_remaining)} pcs`} testid="op-an-kpi-wl-remaining" />
            <KpiCard label="Jobs" value={`${fmtQty(kpis.active_jobs)} aktif`} hint={`${fmtQty(kpis.completed_jobs)} selesai`} testid="op-an-kpi-wl-jobs" />
          </div>
          <p className="muted small op-an-note">Utilization % tidak dihitung — tidak ada data capacity/shift/jam kerja. LOAD band tidak dipakai (tidak ada threshold existing).</p>

          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-workload-table">
            <table className="prod-table op-an-table">
              <thead>
                <tr>
                  <th>Operator</th>
                  <th className="num">Assigned</th>
                  <th className="num">Completed</th>
                  <th className="num">Remaining</th>
                  <th>Progress</th>
                  <th className="num">Active Jobs</th>
                  <th className="num">Completed Jobs</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.email} className="op-an-click" onClick={() => openOperator(r.email)} data-testid={`op-an-wl-${r.email}`}>
                    <td>
                      <strong>{r.name}</strong>
                      <div className="muted small mono">{r.email}</div>
                    </td>
                    <td className="num">{fmtQty(r.assigned)}</td>
                    <td className="num">{fmtQty(r.completed)}</td>
                    <td className="num">{fmtQty(r.remaining)}</td>
                    <td><ThinProgress completed={r.completed} assigned={r.assigned} /></td>
                    <td className="num">{fmtQty(r.active_jobs)}</td>
                    <td className="num">{fmtQty(r.completed_jobs)}</td>
                  </tr>
                ))}
                {!rows.length && !busy && (
                  <tr><td colSpan={7} className="empty-state">{emptyHint}</td></tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="op-an-cards op-an-mobile" data-testid="op-an-workload-cards">
            {rows.map((r) => (
              <article key={r.email} className="op-an-card op-an-click" onClick={() => openOperator(r.email)}>
                <header>
                  <strong>{r.name}</strong>
                  <span className="muted small mono">{r.email}</span>
                </header>
                <ThinProgress completed={r.completed} assigned={r.assigned} />
                <dl>
                  <div><dt>Assigned</dt><dd>{fmtQty(r.assigned)}</dd></div>
                  <div><dt>Completed</dt><dd>{fmtQty(r.completed)}</dd></div>
                  <div><dt>Remaining</dt><dd>{fmtQty(r.remaining)}</dd></div>
                  <div><dt>Active</dt><dd>{fmtQty(r.active_jobs)}</dd></div>
                  <div><dt>Selesai</dt><dd>{fmtQty(r.completed_jobs)}</dd></div>
                </dl>
              </article>
            ))}
            {!rows.length && !busy && <p className="empty-state">{emptyHint}</p>}
          </div>

          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-table">
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
                  <tr key={r.email} className="op-an-click" onClick={() => openOperator(r.email)} data-testid={`op-an-row-${r.email}`}>
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
                  <tr><td colSpan={8} className="empty-state">{emptyHint}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {section === "performance" && (
        <>
          <div className="op-an-section-label">Kecepatan Produksi</div>
          <div className="op-an-kpi-row op-an-kpi-row-4" data-testid="op-an-speed-kpis">
            <KpiCard label="Avg / Hari Aktif" value={`${fmtQty(kpis.avg_daily_qty)} pcs`} hint={`${kpis.active_days || 0} hari aktif · bukan 24 jam / shift`} testid="op-an-kpi-speed-daily" />
            <KpiCard label="Avg Completion" value={fmtDaysOrNA(kpis.avg_completion_days)} hint="Hanya assignment dengan timestamp mulai &amp; selesai" testid="op-an-kpi-speed-duration" />
            <KpiCard label="On-Time %" value={fmtPctOrNA(kpis.on_time_pct)} hint={kpis.on_time_basis ? `${fmtQty(kpis.on_time)} / ${fmtQty(kpis.on_time_basis)} job ber-deadline` : "Tidak ada job dengan deadline valid"} testid="op-an-kpi-ontime-pct" />
            <KpiCard label="Late / Overdue" value={`${fmtQty(kpis.late)} / ${fmtQty(kpis.overdue_active)}`} hint={`On-time ${fmtQty(kpis.on_time)} · on progress ${fmtQty(kpis.on_progress)}`} testid="op-an-kpi-late" />
          </div>

          <div className="op-an-charts" data-testid="op-an-charts">
            <div className="op-an-chart">
              <h3>Assigned vs Completed</h3>
              {chartRows.map((r) => (
                <div key={r.email} className="op-an-chart-row op-an-click" onClick={() => openOperator(r.email)}>
                  <span>{r.name}</span>
                  <DualBar a={r.assigned} b={r.completed} aLabel="Assigned" bLabel="Completed" />
                </div>
              ))}
              {!chartRows.length && !busy && <p className="empty-state">{emptyHint}</p>}
            </div>
            <div className="op-an-chart">
              <h3>On-Time vs Late</h3>
              {chartRows.map((r) => (
                <div key={r.email} className="op-an-chart-row op-an-click" onClick={() => openOperator(r.email)}>
                  <span>{r.name}</span>
                  <div className="op-an-ot-meta">
                    <em>{fmtQty(r.on_time)} on-time</em>
                    <em>{fmtQty(r.late)} late</em>
                    <strong>{fmtPctOrNA(r.on_time_pct)}</strong>
                  </div>
                  <StackedOnTime onTime={r.on_time} late={r.late} />
                </div>
              ))}
              {!chartRows.length && !busy && <p className="empty-state">{emptyHint}</p>}
            </div>
          </div>

          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-perf-table">
            <table className="prod-table op-an-table">
              <thead>
                <tr>
                  <th>Operator</th>
                  <th className="num">Avg / Hari Aktif</th>
                  <th className="num">Avg Completion</th>
                  <th className="num">On-Time</th>
                  <th className="num">Late</th>
                  <th className="num">Overdue</th>
                  <th className="num">On-Time %</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.email} className="op-an-click" onClick={() => openOperator(r.email)} data-testid={`op-an-perf-${r.email}`}>
                    <td>
                      <strong>{r.name}</strong>
                      <div className="muted small mono">{r.email}</div>
                    </td>
                    <td className="num">{fmtQty(r.avg_daily_qty)}</td>
                    <td className="num">{fmtDaysOrNA(r.avg_completion_days)}</td>
                    <td className="num">{fmtQty(r.on_time)}</td>
                    <td className="num">{fmtQty(r.late)}</td>
                    <td className="num">{fmtQty(r.overdue_active)}</td>
                    <td className="num">{fmtPctOrNA(r.on_time_pct)}</td>
                  </tr>
                ))}
                {!rows.length && !busy && (
                  <tr><td colSpan={7} className="empty-state">{emptyHint}</td></tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="op-an-cards op-an-mobile" data-testid="op-an-perf-cards">
            {rows.map((r) => (
              <article key={r.email} className="op-an-card op-an-click" onClick={() => openOperator(r.email)}>
                <header>
                  <strong>{r.name}</strong>
                  <span className="muted small mono">{r.email}</span>
                </header>
                <dl>
                  <div><dt>Avg / Hari</dt><dd>{fmtQty(r.avg_daily_qty)}</dd></div>
                  <div><dt>Avg Completion</dt><dd>{fmtDaysOrNA(r.avg_completion_days)}</dd></div>
                  <div><dt>On-Time</dt><dd>{fmtQty(r.on_time)}</dd></div>
                  <div><dt>Late</dt><dd>{fmtQty(r.late)}</dd></div>
                  <div><dt>Overdue</dt><dd>{fmtQty(r.overdue_active)}</dd></div>
                  <div><dt>On-Time %</dt><dd>{fmtPctOrNA(r.on_time_pct)}</dd></div>
                </dl>
              </article>
            ))}
          </div>

          <div className="op-an-section-label">Sample Quality</div>
          <div className="op-an-kpi-row op-an-kpi-row-4" data-testid="op-an-sample-kpis">
            <KpiCard label="Unique Sample Jobs" value={fmtQty(kpis.sample_jobs)} hint={`${fmtQty(kpis.sample_submissions)} submissions (termasuk upload ulang)`} testid="op-an-kpi-sample-jobs" />
            <KpiCard label="Approved" value={fmtQty(kpis.samples_approved)} testid="op-an-kpi-sample-approved" />
            <KpiCard label="Rejected" value={fmtQty(kpis.samples_rejected)} testid="op-an-kpi-sample-rejected" />
            <KpiCard label="Pending / First-Pass" value={`${fmtQty(kpis.samples_pending)} · ${fmtPctOrNA(kpis.first_pass_rate)}`} hint="First-pass = approved pada keputusan pertama" testid="op-an-kpi-sample-pending" />
          </div>

          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-sample-table">
            <table className="prod-table op-an-table">
              <thead>
                <tr>
                  <th>Operator</th>
                  <th className="num">Sample Jobs</th>
                  <th className="num">Submissions</th>
                  <th className="num">Approved</th>
                  <th className="num">Rejected</th>
                  <th className="num">Pending</th>
                  <th className="num">First-Pass</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.email} className="op-an-click" onClick={() => openOperator(r.email)} data-testid={`op-an-sample-${r.email}`}>
                    <td>
                      <strong>{r.name}</strong>
                      <div className="muted small mono">{r.email}</div>
                    </td>
                    <td className="num">{fmtQty(r.sample_jobs)}</td>
                    <td className="num">{fmtQty(r.sample_submissions)}</td>
                    <td className="num">{fmtQty(r.samples_approved)}</td>
                    <td className="num">{fmtQty(r.samples_rejected)}</td>
                    <td className="num">{fmtQty(r.samples_pending)}</td>
                    <td className="num">{fmtPctOrNA(r.first_pass_rate)}</td>
                  </tr>
                ))}
                {!rows.length && !busy && (
                  <tr><td colSpan={7} className="empty-state">{emptyHint}</td></tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="op-an-cards op-an-mobile" data-testid="op-an-sample-cards">
            {rows.map((r) => (
              <article key={r.email} className="op-an-card op-an-click" onClick={() => openOperator(r.email)}>
                <header>
                  <strong>{r.name}</strong>
                  <span className="muted small mono">{r.email}</span>
                </header>
                <dl>
                  <div><dt>Jobs</dt><dd>{fmtQty(r.sample_jobs)}</dd></div>
                  <div><dt>Submissions</dt><dd>{fmtQty(r.sample_submissions)}</dd></div>
                  <div><dt>Approved</dt><dd>{fmtQty(r.samples_approved)}</dd></div>
                  <div><dt>Rejected</dt><dd>{fmtQty(r.samples_rejected)}</dd></div>
                  <div><dt>Pending</dt><dd>{fmtQty(r.samples_pending)}</dd></div>
                  <div><dt>First-Pass</dt><dd>{fmtPctOrNA(r.first_pass_rate)}</dd></div>
                </dl>
              </article>
            ))}
          </div>
        </>
      )}

      {section === "trend" && (
        <>
          <div className="op-an-section-label">Trend Mingguan · Qty Completed</div>
          <div className="op-an-kpi-row op-an-kpi-row-4" data-testid="op-an-trend-kpis">
            <KpiCard label="WoW minggu terakhir" value={fmtWow(data?.weekly_trend?.wow_pct)} hint="previous = 0 → N/A, bukan Infinity" testid="op-an-kpi-wow" />
            <KpiCard label="Minggu pada periode" value={fmtQty(weeks.length)} hint="ISO Senin–Minggu WIB" testid="op-an-kpi-week-count" />
            <KpiCard label="Total Completed" value={`${fmtQty(kpis.total_completed)} pcs`} testid="op-an-kpi-trend-completed" />
            <KpiCard label="Operator" value={operatorId === "all" ? "Semua" : (rows[0]?.name || operatorId)} testid="op-an-kpi-trend-op" />
          </div>
          <div className="op-an-chart" data-testid="op-an-week-chart">
            <h3>Output per minggu</h3>
            <WeekBars weeks={weeks} testid="op-an-weekbars" />
          </div>

          <div className="op-an-section-label">Ranking Operator</div>
          <div className="prod-filter-row op-an-rank-filter">
            <span className="prod-filter-kicker">Metric</span>
            <select value={rankMetric} onChange={(e) => setRankMetric(e.target.value)} data-testid="op-an-rank-metric">
              {RANK_OPTS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select>
            <span className="muted small">Tidak ada overall score — pilih satu metric.</span>
          </div>
          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-rank-table">
            <table className="prod-table op-an-table">
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Operator</th>
                  <th className="num">{RANK_OPTS.find((x) => x.key === rankMetric)?.label}</th>
                </tr>
              </thead>
              <tbody>
                {ranked.map((r) => (
                  <tr key={r.email} className="op-an-click" onClick={() => openOperator(r.email)} data-testid={`op-an-rank-${r.email}`}>
                    <td className="num">{r._rank ?? "—"}</td>
                    <td>
                      <strong>{r.name}</strong>
                      <div className="muted small mono">{r.email}</div>
                    </td>
                    <td className="num">{r._na ? "N/A" : fmtRankValue(rankMetric, r._value, r)}</td>
                  </tr>
                ))}
                {!ranked.length && !busy && (
                  <tr><td colSpan={3} className="empty-state">{emptyHint}</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="op-an-cards op-an-mobile" data-testid="op-an-rank-cards">
            {ranked.map((r) => (
              <article key={r.email} className="op-an-card op-an-click" onClick={() => openOperator(r.email)}>
                <header>
                  <strong>#{r._rank ?? "—"} {r.name}</strong>
                  <span className="muted small mono">{r.email}</span>
                </header>
                <dl>
                  <div><dt>{RANK_OPTS.find((x) => x.key === rankMetric)?.label}</dt><dd>{r._na ? "N/A" : fmtRankValue(rankMetric, r._value, r)}</dd></div>
                </dl>
              </article>
            ))}
          </div>
        </>
      )}

      {section === "packing" && (
        <>
          <div className="op-an-section-label">Laporan Qty Packing</div>
          <div className="prod-filter-row">
            <span className="prod-filter-kicker">Packing</span>
            <select value={packUser} onChange={(e) => setPackUser(e.target.value)} data-testid="op-an-pack-user">
              <option value="all">Semua user</option>
              {(packData?.filters?.users || []).map((u) => <option key={u.id} value={u.id}>{u.name || u.id}</option>)}
            </select>
            <select value={packRole} onChange={(e) => setPackRole(e.target.value)} data-testid="op-an-pack-role">
              <option value="all">Semua role</option>
              {(packData?.filters?.roles || []).map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <select value={packPo} onChange={(e) => setPackPo(e.target.value)} data-testid="op-an-pack-po">
              <option value="all">Semua PO</option>
              {(packData?.filters?.pos || []).map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <div className="op-an-kpi-row op-an-kpi-row-4" data-testid="op-an-pack-kpis">
            <KpiCard label="Total Qty Packing" value={`${fmtQty(packData?.kpis?.total_qty)} pcs`} testid="op-an-pack-total" />
            <KpiCard label="User" value={fmtQty(packData?.kpis?.user_count)} hint={`${fmtQty(packData?.kpis?.po_count)} PO · ${fmtQty(packData?.kpis?.line_count)} line`} testid="op-an-pack-users" />
            <KpiCard label="WoW packing" value={fmtWow(packData?.weekly_trend?.wow_pct)} testid="op-an-pack-wow" />
            <KpiCard label="Attribution" value={packData?.attribution?.fallback_used ? "Last saver*" : "Save Packing"} hint="Delta timeline, bukan jumlah snapshot" testid="op-an-pack-attr" />
          </div>
          <div className="op-an-chart">
            <h3>Qty Packing per minggu</h3>
            <WeekBars weeks={packData?.weekly_trend?.weeks || []} testid="op-an-pack-weekbars" />
          </div>
          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-pack-table">
            <table className="prod-table op-an-table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Role</th>
                  <th className="num">Total Qty</th>
                  <th className="num">PO</th>
                  <th className="num">Line</th>
                  <th className="num">Avg / Hari Aktif</th>
                  <th>Terakhir</th>
                  <th className="num">Partial / Done</th>
                </tr>
              </thead>
              <tbody>
                {(packData?.users || []).map((r) => (
                  <tr key={r.email} className="op-an-click" onClick={() => setPackOpen(packOpen === r.email ? null : r.email)} data-testid={`op-an-pack-${r.email}`}>
                    <td><strong>{r.name}</strong><div className="muted small mono">{r.email}</div></td>
                    <td>{r.role || "—"}</td>
                    <td className="num">{fmtQty(r.total_qty)}</td>
                    <td className="num">{fmtQty(r.po_count)}</td>
                    <td className="num">{fmtQty(r.line_count)}</td>
                    <td className="num">{fmtQty(r.avg_daily_qty)}</td>
                    <td>{fmtDate(r.last_activity_at)}</td>
                    <td className="num">{fmtQty(r.partial_jobs)} / {fmtQty(r.completed_jobs)}</td>
                  </tr>
                ))}
                {!packData?.users?.length && !packBusy && (
                  <tr><td colSpan={8} className="empty-state">Tidak ada aktivitas packing pada periode ini.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="op-an-cards op-an-mobile">
            {(packData?.users || []).map((r) => (
              <article key={r.email} className="op-an-card op-an-click" onClick={() => setPackOpen(packOpen === r.email ? null : r.email)}>
                <header><strong>{r.name}</strong><span className="muted small">{r.role || "—"}</span></header>
                <dl>
                  <div><dt>Qty</dt><dd>{fmtQty(r.total_qty)}</dd></div>
                  <div><dt>PO / Line</dt><dd>{fmtQty(r.po_count)} / {fmtQty(r.line_count)}</dd></div>
                  <div><dt>Avg/hari</dt><dd>{fmtQty(r.avg_daily_qty)}</dd></div>
                  <div><dt>Terakhir</dt><dd>{fmtDate(r.last_activity_at)}</dd></div>
                </dl>
              </article>
            ))}
          </div>
          {packOpen && (
            <div className="op-an-drill" data-testid="op-an-pack-lines">
              <h3>Per line · {packLinesFor(packOpen)[0]?.user_name || packOpen}</h3>
              <div className="prod-table-wrap op-an-table-wrap">
                <table className="prod-table op-an-table">
                  <thead>
                    <tr><th>PO</th><th>Line</th><th>Item</th><th className="num">Packed</th><th>Customer</th></tr>
                  </thead>
                  <tbody>
                    {packLinesFor(packOpen).map((l) => (
                      <tr key={`${l.po_id}-${l.line_idx}`}>
                        <td className="mono">{l.po_number}</td>
                        <td>#{Number(l.line_idx) + 1}</td>
                        <td>{l.item}</td>
                        <td className="num">{fmtQty(l.packed_qty)}</td>
                        <td>{l.customer}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <p className="muted small op-an-note">{packData?.attribution?.note}</p>
        </>
      )}

      {section === "prep" && (
        <>
          <div className="op-an-section-label">Laporan Persiapan Qty Cup</div>
          <div className="prod-filter-row">
            <span className="prod-filter-kicker">Persiapan</span>
            <select value={prepUser} onChange={(e) => setPrepUser(e.target.value)} data-testid="op-an-prep-user">
              <option value="all">Semua user</option>
              {(prepData?.filters?.users || []).map((u) => <option key={u.id} value={u.id}>{u.name || u.id}</option>)}
            </select>
            <select value={prepRole} onChange={(e) => setPrepRole(e.target.value)} data-testid="op-an-prep-role">
              <option value="all">Semua role</option>
              {(prepData?.filters?.roles || []).map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <select value={prepPo} onChange={(e) => setPrepPo(e.target.value)} data-testid="op-an-prep-po">
              <option value="all">Semua PO</option>
              {(prepData?.filters?.pos || []).map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <div className="op-an-kpi-row op-an-kpi-row-4" data-testid="op-an-prep-kpis">
            <KpiCard label="Total Qty Disiapkan" value={`${fmtQty(prepData?.kpis?.total_qty)} pcs`} testid="op-an-prep-total" />
            <KpiCard label="User" value={fmtQty(prepData?.kpis?.user_count)} hint={`${fmtQty(prepData?.kpis?.po_count)} PO · ${fmtQty(prepData?.kpis?.line_count)} line`} testid="op-an-prep-users" />
            <KpiCard label="WoW persiapan" value={fmtWow(prepData?.weekly_trend?.wow_pct)} testid="op-an-prep-wow" />
            <KpiCard label="Cup field" value="Line Cetak" hint="Tidak ada field cup_qty" testid="op-an-prep-cup" />
          </div>
          <div className="op-an-chart">
            <h3>Qty Prepared per minggu</h3>
            <WeekBars weeks={prepData?.weekly_trend?.weeks || []} testid="op-an-prep-weekbars" />
          </div>
          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-prep-table">
            <table className="prod-table op-an-table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Role</th>
                  <th className="num">Qty Disiapkan</th>
                  <th className="num">PO</th>
                  <th className="num">Line</th>
                  <th className="num">Avg / Hari Aktif</th>
                  <th>Terakhir</th>
                </tr>
              </thead>
              <tbody>
                {(prepData?.users || []).map((r) => (
                  <tr key={r.email} className="op-an-click" onClick={() => setPrepOpen(prepOpen === r.email ? null : r.email)} data-testid={`op-an-prep-${r.email}`}>
                    <td><strong>{r.name}</strong><div className="muted small mono">{r.email}</div></td>
                    <td>{r.role || "—"}</td>
                    <td className="num">{fmtQty(r.total_qty)}</td>
                    <td className="num">{fmtQty(r.po_count)}</td>
                    <td className="num">{fmtQty(r.line_count)}</td>
                    <td className="num">{fmtQty(r.avg_daily_qty)}</td>
                    <td>{fmtDate(r.last_activity_at)}</td>
                  </tr>
                ))}
                {!prepData?.users?.length && !prepBusy && (
                  <tr><td colSpan={7} className="empty-state">Tidak ada aktivitas persiapan pada periode ini.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="op-an-cards op-an-mobile">
            {(prepData?.users || []).map((r) => (
              <article key={r.email} className="op-an-card op-an-click" onClick={() => setPrepOpen(prepOpen === r.email ? null : r.email)}>
                <header><strong>{r.name}</strong><span className="muted small">{r.role || "—"}</span></header>
                <dl>
                  <div><dt>Qty</dt><dd>{fmtQty(r.total_qty)}</dd></div>
                  <div><dt>PO / Line</dt><dd>{fmtQty(r.po_count)} / {fmtQty(r.line_count)}</dd></div>
                  <div><dt>Avg/hari</dt><dd>{fmtQty(r.avg_daily_qty)}</dd></div>
                  <div><dt>Terakhir</dt><dd>{fmtDate(r.last_activity_at)}</dd></div>
                </dl>
              </article>
            ))}
          </div>
          {prepOpen && (
            <div className="op-an-drill" data-testid="op-an-prep-lines">
              {prepPosFor(prepOpen).map((p) => (
                <div key={p.po_id} className="op-an-po-block">
                  <h3>{p.po_number} · {p.customer}</h3>
                  <p className="muted small">
                    {(p.users || []).map((u) => `${u.name}: ${fmtQty(u.qty_prepared)}`).join(" · ")}
                    {p.limitation ? ` · ${p.limitation}` : ""}
                  </p>
                  <div className="prod-table-wrap">
                    <table className="prod-table op-an-table">
                      <thead>
                        <tr><th>Line</th><th>Item</th><th className="num">Target</th><th className="num">Prepared</th><th className="num">Remaining</th></tr>
                      </thead>
                      <tbody>
                        {(p.lines || []).map((l) => (
                          <tr key={`${p.po_id}-${l.line_idx}`}>
                            <td>#{Number(l.line_idx) + 1}</td>
                            <td>{l.item}</td>
                            <td className="num">{fmtQty(l.target)}</td>
                            <td className="num">{l.prepared == null ? "N/A" : fmtQty(l.prepared)}</td>
                            <td className="num">{l.remaining == null ? "N/A" : fmtQty(l.remaining)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          )}
          <p className="muted small op-an-note">{prepData?.attribution?.note}</p>
        </>
      )}

      <p className="muted small op-an-note">
        <BarChart3 size={12} style={{ verticalAlign: "-2px" }} /> Qty selesai = aktual per operator, bukan qty PO.
        Minggu = ISO Senin–Minggu (WIB). Avg/hari hanya menghitung hari yang ada progress.
        On-time memakai Deadline PO per assignment. pcs/jam tidak ditampilkan (tidak ada data jam kerja).
        {data?.period?.from && (
          <span data-testid="op-an-period-range"> · {data.period.from} → {data.period.to}</span>
        )}
      </p>

      {detailId && (
        <div className="modal op-an-modal" data-testid="op-an-detail-modal" onClick={(e) => e.target === e.currentTarget && setDetailId(null)}>
          <div className="modal-inner modal-wide op-an-detail" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <div>
                <p className="eyebrow">Jadwal Produksi · Analitik Operator</p>
                <h3 data-testid="op-an-detail-name">{detail?.operator?.name || detailId}</h3>
                <span className="mono muted small">{detail?.operator?.email || detailId}</span>
              </div>
              <button className="icon-button" onClick={() => setDetailId(null)} data-testid="op-an-detail-close"><X size={16} /></button>
            </header>
            {detailBusy && !detail ? <p className="empty-state">Memuat detail…</p> : (
              <div className="op-an-detail-body">
                <div className="op-an-section-label">Produktivitas</div>
                <div className="op-an-kpi-row op-an-kpi-row-4">
                  <KpiCard label="Assigned" value={`${fmtQty(detail?.operator?.assigned)} pcs`} />
                  <KpiCard label="Completed" value={`${fmtQty(detail?.operator?.completed)} pcs`} />
                  <KpiCard label="Remaining" value={`${fmtQty(detail?.operator?.remaining)} pcs`} />
                  <KpiCard label="Achievement" value={fmtPct(detail?.operator?.achievement, detail?.operator?.assigned)} hint={`Avg/minggu ${fmtQty(detail?.operator?.avg_weekly_qty)} · avg/hari ${fmtQty(detail?.operator?.avg_daily_qty)}`} />
                </div>
                <div className="op-an-section-label">Performance</div>
                <div className="op-an-kpi-row op-an-kpi-row-4">
                  <KpiCard label="On-Time" value={fmtQty(detail?.operator?.on_time)} />
                  <KpiCard label="Late" value={fmtQty(detail?.operator?.late)} />
                  <KpiCard label="Overdue" value={fmtQty(detail?.operator?.overdue_active)} />
                  <KpiCard label="On-Time %" value={fmtPctOrNA(detail?.operator?.on_time_pct)} />
                </div>
                <div className="op-an-section-label">Sample</div>
                <div className="op-an-kpi-row op-an-kpi-row-4">
                  <KpiCard label="Submitted" value={fmtQty(detail?.operator?.sample_submissions)} hint={`${fmtQty(detail?.operator?.sample_jobs)} unique jobs`} />
                  <KpiCard label="Approved" value={fmtQty(detail?.operator?.samples_approved)} />
                  <KpiCard label="Rejected" value={fmtQty(detail?.operator?.samples_rejected)} />
                  <KpiCard label="Pending" value={fmtQty(detail?.operator?.samples_pending)} />
                </div>
                <div className="op-an-section-label">Trend</div>
                <WeekBars weeks={detail?.weekly_trend?.weeks || []} testid="op-an-detail-weeks" />
                <div className="op-an-detail-filters">
                  <span className="prod-filter-kicker">Job history</span>
                  <select value={detailStatus} onChange={(e) => setDetailStatus(e.target.value)} data-testid="op-an-detail-status">
                    <option value="all">Semua status</option>
                    <option value="active">Aktif</option>
                    <option value="completed">Selesai</option>
                    <option value="overdue">Overdue</option>
                  </select>
                </div>
                <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-detail-jobs">
                  <table className="prod-table op-an-table">
                    <thead>
                      <tr>
                        <th>No PO</th>
                        <th>Customer</th>
                        <th>Item</th>
                        <th className="num">Assigned</th>
                        <th className="num">Completed</th>
                        <th className="num">Remaining</th>
                        <th>Work Date</th>
                        <th>Deadline</th>
                        <th>Sample</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(detail?.jobs || []).map((j) => (
                        <tr key={j.assignment_id}>
                          <td className="mono">{j.po_number}</td>
                          <td>{j.customer}</td>
                          <td>{j.item}</td>
                          <td className="num">{fmtQty(j.assigned_qty)}</td>
                          <td className="num">{fmtQty(j.completed_qty)}</td>
                          <td className="num">{fmtQty(j.remaining)}</td>
                          <td>{fmtDate(j.work_date)}</td>
                          <td>{fmtDate(j.deadline)}</td>
                          <td>{j.sample_status || "—"}</td>
                          <td>{jobStatusLabel(j.job_status)}</td>
                        </tr>
                      ))}
                      {!detail?.jobs?.length && <tr><td colSpan={10} className="empty-state">Tidak ada job pada filter ini.</td></tr>}
                    </tbody>
                  </table>
                </div>
                <div className="op-an-cards op-an-mobile">
                  {(detail?.jobs || []).map((j) => (
                    <article key={j.assignment_id} className="op-an-card">
                      <header><strong>{j.po_number}</strong><span className="muted small">{j.customer}</span></header>
                      <dl>
                        <div><dt>Item</dt><dd>{j.item}</dd></div>
                        <div><dt>Assigned</dt><dd>{fmtQty(j.assigned_qty)}</dd></div>
                        <div><dt>Completed</dt><dd>{fmtQty(j.completed_qty)}</dd></div>
                        <div><dt>Remaining</dt><dd>{fmtQty(j.remaining)}</dd></div>
                        <div><dt>Work / Deadline</dt><dd>{fmtDate(j.work_date)} / {fmtDate(j.deadline)}</dd></div>
                        <div><dt>Status</dt><dd>{jobStatusLabel(j.job_status)}</dd></div>
                      </dl>
                    </article>
                  ))}
                </div>
                {detail?.jobs_page?.total > (detail?.jobs_page?.page_size || 50) && (
                  <div className="op-an-pager">
                    <button className="ghost sm" disabled={detailPage <= 1} onClick={() => loadDetail(detailId, detailPage - 1)}>Prev</button>
                    <span className="muted small">{detailPage} / {Math.ceil((detail.jobs_page.total || 0) / (detail.jobs_page.page_size || 50))}</span>
                    <button className="ghost sm" disabled={!detail.jobs_page.has_more} onClick={() => loadDetail(detailId, detailPage + 1)}>Next</button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
