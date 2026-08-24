import { useEffect, useMemo, useState } from "react";
import { BarChart3, Download, RefreshCw, X } from "lucide-react";
import api from "../lib/api";

const PERIOD_OPTS = [
  { key: "today", label: "Hari Ini" },
  { key: "daily", label: "Harian" },
  { key: "week", label: "Mingguan" },
  { key: "month", label: "Bulanan" },
  { key: "months_3", label: "3 Bulan" },
  { key: "months_6", label: "6 Bulan" },
  { key: "months_12", label: "12 Bulan" },
  { key: "year", label: "Tahunan" },
  { key: "custom", label: "Custom" },
];

const TABS = [
  { key: "operator", label: "Operator" },
  { key: "helper", label: "Helper" },
  { key: "screener", label: "Screener" },
  { key: "design", label: "Jumlah Design" },
];

function fmtQty(n) {
  if (n === null || n === undefined || n === "") return "—";
  return Number(n || 0).toLocaleString("id-ID");
}

function fmtPctOrNA(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "N/A";
  return `${Number(n).toFixed(1)}%`;
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

function WeekBars({ weeks, testid, valueKey = "qty" }) {
  const list = weeks || [];
  const max = Math.max(1, ...list.map((w) => Number(w[valueKey]) || 0));
  if (!list.length) return <p className="empty-state">Tidak ada data pada periode ini.</p>;
  return (
    <div className="op-an-weekbars" data-testid={testid}>
      {list.map((w) => (
        <div key={w.key || w.week} className="op-an-weekbar" title={`${w.label}: ${fmtQty(w[valueKey])}`}>
          <div className="op-an-weekbar-col">
            <i style={{ height: `${((Number(w[valueKey]) || 0) / max) * 100}%` }} />
          </div>
          <span>{String(w.label || w.week || "").replace(" ", "\n")}</span>
          <b>{fmtQty(w[valueKey])}</b>
          {w.wow_pct !== undefined ? <em className={(w.wow_pct || 0) < 0 ? "neg" : ""}>{fmtWow(w.wow_pct)}</em> : null}
        </div>
      ))}
    </div>
  );
}

export default function OperatorAnalyticsPanel() {
  const [period, setPeriod] = useState("month");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [tab, setTab] = useState("operator");
  const [userId, setUserId] = useState("all");
  const [role, setRole] = useState("all");
  const [customer, setCustomer] = useState("all");
  const [item, setItem] = useState("all");
  const [po, setPo] = useState("all");
  const [data, setData] = useState(null);
  const [helperData, setHelperData] = useState(null);
  const [packData, setPackData] = useState(null);
  const [screenerData, setScreenerData] = useState(null);
  const [designData, setDesignData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [exporting, setExporting] = useState(false);

  const [detailId, setDetailId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [helperOpen, setHelperOpen] = useState(null);
  const [screenerOpen, setScreenerOpen] = useState(null);

  const periodParams = () => {
    const params = { period };
    if (period === "custom") {
      if (dateFrom) params.from = dateFrom;
      if (dateTo) params.to = dateTo;
    }
    if (customer && customer !== "all") params.customer = customer;
    if (item && item !== "all") params.item = item;
    if (po && po !== "all") params.po = po;
    return params;
  };

  const loadOperator = async () => {
    const params = periodParams();
    if (userId && userId !== "all") params.operator_id = userId;
    setData(await api.analyticsOperatorKpis(params));
  };
  const loadHelper = async () => {
    const params = periodParams();
    if (userId && userId !== "all") params.user_id = userId;
    if (role && role !== "all") params.role = role;
    const [prep, pack] = await Promise.all([api.analyticsPreparation(params), api.analyticsPacking(params)]);
    setHelperData(prep);
    setPackData(pack);
  };
  const loadScreener = async () => {
    const params = periodParams();
    if (userId && userId !== "all") params.user_id = userId;
    if (role && role !== "all") params.role = role;
    setScreenerData(await api.analyticsScreener(params));
  };
  const loadDesign = async () => {
    const params = periodParams();
    if (userId && userId !== "all") params.operator_id = userId;
    setDesignData(await api.analyticsDesigns(params));
  };

  const load = async () => {
    if (period === "custom" && (!dateFrom || !dateTo)) return;
    setBusy(true);
    setErr("");
    try {
      if (tab === "operator") await loadOperator();
      else if (tab === "helper") await loadHelper();
      else if (tab === "screener") await loadScreener();
      else await loadDesign();
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat Analitik Output");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    setUserId("all");
    setRole("all");
    /* eslint-disable-next-line */
  }, [tab]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [tab, period, dateFrom, dateTo, userId, role, customer, item, po]);

  useEffect(() => {
    if (!detailId) return;
    const run = async () => {
      setDetailBusy(true);
      try {
        const params = periodParams();
        setDetail(await api.analyticsOperatorDetail(detailId, { ...params, page: 1, page_size: 50 }));
      } catch (e) {
        setErr(e.response?.data?.detail || "Gagal memuat detail operator");
      } finally {
        setDetailBusy(false);
      }
    };
    run();
    /* eslint-disable-next-line */
  }, [detailId, period, dateFrom, dateTo, customer, item, po]);

  const resolvedFrom = data?.period?.from || helperData?.period?.from || screenerData?.period?.from || designData?.period?.from || dateFrom;
  const resolvedTo = data?.period?.to || helperData?.period?.to || screenerData?.period?.to || designData?.period?.to || dateTo;
  const customDatesReady = period !== "custom" || (dateFrom && dateTo);

  const userOptions = useMemo(() => {
    if (tab === "operator") return data?.filters?.operators || [];
    if (tab === "helper") return helperData?.filters?.users || [];
    if (tab === "screener") return screenerData?.filters?.users || [];
    return (designData?.operators || []).map((o) => ({ id: o.email, name: o.name }));
  }, [tab, data, helperData, screenerData, designData]);

  const roleOptions = tab === "helper" ? (helperData?.filters?.roles || []) : tab === "screener" ? (screenerData?.filters?.roles || []) : [];
  const customers = data?.filters?.customers || helperData?.filters?.customers || screenerData?.filters?.customers || [];
  const items = data?.filters?.items || helperData?.filters?.items || screenerData?.filters?.items || [];
  const pos = data?.filters?.pos || helperData?.filters?.pos || screenerData?.filters?.pos || [];

  const kpis = data?.kpis || {};
  const rows = data?.operators || [];
  const chartBuckets = (data?.output_series?.buckets || data?.weekly_trend?.weeks || []);

  const exportData = async () => {
    setExporting(true);
    try {
      const params = { ...periodParams(), tab, fmt: "csv" };
      if (tab === "operator" || tab === "design") {
        if (userId && userId !== "all") params.operator_id = userId;
      } else if (userId && userId !== "all") params.user_id = userId;
      if (role && role !== "all") params.role = role;
      const blob = await api.analyticsOutputExport(params);
      const url = URL.createObjectURL(new Blob([blob], { type: "text/csv;charset=utf-8" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `analitik-output-${tab}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal export data");
    } finally {
      setExporting(false);
    }
  };

  const helperRows = helperData?.users || [];
  const packByUser = (email) => (packData?.lines || []).filter((l) => l.user_id === email);
  const helperLines = (email) => (helperData?.cup_lines || []).filter((l) => l.user_id === email);

  return (
    <section className="op-an" data-testid="operator-analytics-panel">
      <div className="op-an-head">
        <div>
          <p className="eyebrow">Jadwal Produksi · Read only</p>
          <h2 data-testid="op-an-title">Analitik Output</h2>
        </div>
        <div className="op-an-head-actions">
          <button className="ghost sm" onClick={load} disabled={busy} data-testid="op-an-refresh">
            <RefreshCw size={12} className={busy ? "spin" : ""} /> Refresh
          </button>
          <button className="primary sm" onClick={exportData} disabled={exporting || busy} data-testid="op-an-export">
            <Download size={12} /> Export Data
          </button>
        </div>
      </div>

      <section className="prod-filters op-an-filters" data-testid="op-an-filters">
        <div className="prod-filter-row">
          <span className="prod-filter-kicker">Periode</span>
          <select value={period} onChange={(e) => setPeriod(e.target.value)} data-testid="op-an-period">
            {PERIOD_OPTS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
          <label className="prod-inline-date">
            Dari tanggal
            <input
              type="date"
              value={period === "custom" ? dateFrom : (resolvedFrom || "")}
              onChange={(e) => { setPeriod("custom"); setDateFrom(e.target.value); }}
              data-testid="op-an-date-from"
            />
          </label>
          <label className="prod-inline-date">
            Sampai tanggal
            <input
              type="date"
              value={period === "custom" ? dateTo : (resolvedTo || "")}
              onChange={(e) => { setPeriod("custom"); setDateTo(e.target.value); }}
              data-testid="op-an-date-to"
            />
          </label>
        </div>
        <div className="prod-filter-row">
          <span className="prod-filter-kicker">Filter</span>
          <select value={userId} onChange={(e) => setUserId(e.target.value)} data-testid="op-an-user">
            <option value="all">{tab === "operator" || tab === "design" ? "Semua operator" : tab === "helper" ? "Semua helper" : "Semua screener"}</option>
            {userOptions.map((o) => <option key={o.id || o.email} value={o.id || o.email}>{o.name || o.id}</option>)}
          </select>
          {(tab === "helper" || tab === "screener") && (
            <select value={role} onChange={(e) => setRole(e.target.value)} data-testid="op-an-role">
              <option value="all">Semua role</option>
              {roleOptions.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          )}
          <select value={customer} onChange={(e) => setCustomer(e.target.value)} data-testid="op-an-customer">
            <option value="all">Semua customer</option>
            {customers.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <select value={item} onChange={(e) => setItem(e.target.value)} data-testid="op-an-item">
            <option value="all">Semua item</option>
            {items.map((it) => <option key={it} value={it}>{it}</option>)}
          </select>
          <select value={po} onChange={(e) => setPo(e.target.value)} data-testid="op-an-po">
            <option value="all">Semua PO</option>
            {pos.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          {!customDatesReady && <span className="muted small">Isi tanggal dari &amp; sampai untuk Custom.</span>}
        </div>
      </section>

      <div className="op-an-subnav" role="tablist" data-testid="op-an-subnav">
        {TABS.map((s) => (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={tab === s.key}
            className={tab === s.key ? "active" : ""}
            onClick={() => setTab(s.key)}
            data-testid={`op-an-tab-${s.key}`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {err && <div className="error" data-testid="op-an-error">{err}</div>}

      {tab === "operator" && (
        <>
          <div className="op-an-kpi-row op-an-kpi-row-6" data-testid="op-an-kpis">
            <KpiCard label="Total Output" value={`${fmtQty(kpis.total_completed)} pcs`} testid="op-an-kpi-completed" />
            <KpiCard label="Avg / Hari Aktif" value={`${fmtQty(kpis.avg_daily_qty)} pcs`} hint={`${kpis.active_days || 0} hari aktif`} testid="op-an-kpi-daily" />
            <KpiCard label="Output Minggu Ini" value={`${fmtQty(kpis.this_week_qty)} pcs`} hint="ISO Senin–Minggu WIB" testid="op-an-kpi-this-week" />
            <KpiCard
              label="Target / Hari"
              value={kpis.daily_target == null ? "—" : fmtQty(kpis.daily_target)}
              hint={kpis.target_achievement_pct == null ? "Atur di Pengaturan" : `Achievement ${fmtPctOrNA(kpis.target_achievement_pct)}`}
              testid="op-an-kpi-target"
            />
            <KpiCard label="Jumlah Job" value={fmtQty(kpis.total_jobs)} testid="op-an-kpi-jobs" />
            <KpiCard label="Jumlah Design / Line Cetak" value={fmtQty(kpis.total_designs)} testid="op-an-kpi-designs" />
          </div>
          <div className="op-an-chart" data-testid="op-an-week-chart">
            <h3>{(data?.period?.grouping || "week") === "day" ? "Output harian" : (data?.period?.grouping || "week") === "month" ? "Output bulanan" : "Output mingguan"}</h3>
            <WeekBars weeks={chartBuckets} testid="op-an-weekbars" />
          </div>
          <div className="op-an-section-label">Operator Performance</div>
          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-table">
            <table className="prod-table op-an-table">
              <thead>
                <tr>
                  <th>Operator</th>
                  <th className="num">Qty Cetak Selesai</th>
                  <th className="num">Avg / Hari Aktif</th>
                  <th className="num">Output Mingguan</th>
                  <th className="num">Job / PO</th>
                  <th className="num">Design / Line</th>
                  <th className="num">Target / Hari</th>
                  <th className="num">Actual vs Target</th>
                  <th>Cup / Item</th>
                  <th>Aktivitas terakhir</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.email} className="op-an-click" onClick={() => setDetailId(r.email)} data-testid={`op-an-row-${r.email}`}>
                    <td><strong>{r.name}</strong><div className="muted small mono">{r.email}</div></td>
                    <td className="num">{fmtQty(r.completed)}</td>
                    <td className="num">{fmtQty(r.avg_daily_qty)}</td>
                    <td className="num">{fmtQty(r.avg_weekly_qty)}</td>
                    <td className="num">{fmtQty(r.job_count)}</td>
                    <td className="num">{fmtQty(r.design_count)}</td>
                    <td className="num">{r.daily_target == null ? "—" : fmtQty(r.daily_target)}</td>
                    <td className="num">{r.target_achievement_pct == null ? "N/A" : `${fmtPctOrNA(r.target_achievement_pct)} (${r.target_gap >= 0 ? "+" : ""}${fmtQty(r.target_gap)})`}</td>
                    <td>{(r.cups || []).slice(0, 3).map((c) => `${c.item} ${fmtQty(c.qty)}`).join(" · ") || "—"}</td>
                    <td>{fmtDate(r.last_activity_at)}</td>
                  </tr>
                ))}
                {!rows.length && !busy && <tr><td colSpan={10} className="empty-state">Tidak ada output operator pada periode ini.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="op-an-cards op-an-mobile" data-testid="op-an-cards">
            {rows.map((r) => (
              <article key={r.email} className="op-an-card op-an-click" onClick={() => setDetailId(r.email)}>
                <header><strong>{r.name}</strong><span className="muted small mono">{r.email}</span></header>
                <dl>
                  <div><dt>Output</dt><dd>{fmtQty(r.completed)}</dd></div>
                  <div><dt>Avg/hari</dt><dd>{fmtQty(r.avg_daily_qty)}</dd></div>
                  <div><dt>Job</dt><dd>{fmtQty(r.job_count)}</dd></div>
                  <div><dt>Design</dt><dd>{fmtQty(r.design_count)}</dd></div>
                  <div><dt>Target</dt><dd>{r.daily_target == null ? "—" : fmtQty(r.daily_target)}</dd></div>
                  <div><dt>Achievement</dt><dd>{fmtPctOrNA(r.target_achievement_pct)}</dd></div>
                </dl>
              </article>
            ))}
          </div>
        </>
      )}

      {tab === "helper" && (
        <>
          <div className="op-an-kpi-row" data-testid="op-an-prep-kpis">
            <KpiCard label="Total Qty Persiapan" value={`${fmtQty(helperData?.kpis?.total_qty)} pcs`} testid="op-an-prep-total" />
            <KpiCard label="Avg / Hari" value={`${fmtQty(helperData?.kpis?.avg_daily_qty)} pcs`} testid="op-an-prep-avg" />
            <KpiCard label="Output Minggu Ini" value={`${fmtQty(helperData?.kpis?.this_week_qty)} pcs`} testid="op-an-prep-week" />
            <KpiCard label="Jumlah PO" value={fmtQty(helperData?.kpis?.po_count)} testid="op-an-prep-po" />
            <KpiCard label="Jumlah Line" value={fmtQty(helperData?.kpis?.line_count)} testid="op-an-prep-line" />
          </div>
          <div className="op-an-chart">
            <h3>Output Persiapan Mingguan</h3>
            <WeekBars weeks={helperData?.weekly_trend?.weeks || []} testid="op-an-prep-weekbars" />
          </div>
          <div className="op-an-section-label">Helper Output</div>
          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-prep-table">
            <table className="prod-table op-an-table">
              <thead>
                <tr>
                  <th>Helper</th>
                  <th className="num">Qty Persiapan</th>
                  <th className="num">Avg / Hari Aktif</th>
                  <th className="num">Mingguan</th>
                  <th className="num">PO</th>
                  <th className="num">Line</th>
                  <th>Cup / Item</th>
                  <th>Terakhir</th>
                </tr>
              </thead>
              <tbody>
                {helperRows.map((r) => (
                  <tr key={r.email} className="op-an-click" onClick={() => setHelperOpen(helperOpen === r.email ? null : r.email)} data-testid={`op-an-prep-${r.email}`}>
                    <td><strong>{r.name}</strong><div className="muted small mono">{r.email}</div></td>
                    <td className="num">{fmtQty(r.total_qty)}</td>
                    <td className="num">{fmtQty(r.avg_daily_qty)}</td>
                    <td className="num">{fmtQty(r.this_week_qty)}</td>
                    <td className="num">{fmtQty(r.po_count)}</td>
                    <td className="num">{fmtQty(r.line_count)}</td>
                    <td>{(r.cups || []).map((c) => `${c.item} ${fmtQty(c.qty)}`).join(" · ") || "—"}</td>
                    <td>{fmtDate(r.last_activity_at)}</td>
                  </tr>
                ))}
                {!helperRows.length && !busy && <tr><td colSpan={8} className="empty-state">Tidak ada aktivitas persiapan pada periode ini.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="op-an-cards op-an-mobile" data-testid="op-an-prep-cards">
            {helperRows.map((r) => (
              <article key={r.email} className="op-an-card op-an-click" onClick={() => setHelperOpen(helperOpen === r.email ? null : r.email)}>
                <header><strong>{r.name}</strong><span className="muted small mono">{r.email}</span></header>
                <dl>
                  <div><dt>Persiapan</dt><dd>{fmtQty(r.total_qty)}</dd></div>
                  <div><dt>Avg/hari</dt><dd>{fmtQty(r.avg_daily_qty)}</dd></div>
                  <div><dt>PO</dt><dd>{fmtQty(r.po_count)}</dd></div>
                  <div><dt>Line</dt><dd>{fmtQty(r.line_count)}</dd></div>
                </dl>
              </article>
            ))}
          </div>
          {helperOpen && (
            <div className="op-an-drill" data-testid="op-an-prep-lines">
              <h3>Cup / Item Dipersiapkan · {helperOpen}</h3>
              <div className="prod-table-wrap">
                <table className="prod-table op-an-table">
                  <thead><tr><th>Cup / Item</th><th>PO</th><th>Line</th><th className="num">Qty</th><th>Tanggal</th></tr></thead>
                  <tbody>
                    {helperLines(helperOpen).map((l) => (
                      <tr key={`${l.po_id}-${l.line_idx}`}>
                        <td>{l.item}</td>
                        <td className="mono">{l.po_number}</td>
                        <td>#{Number(l.line_idx) + 1}</td>
                        <td className="num">{fmtQty(l.qty_prepared)}</td>
                        <td>{fmtDate(l.day)}</td>
                      </tr>
                    ))}
                    {!helperLines(helperOpen).length && <tr><td colSpan={5} className="empty-state">Qty per line tidak diatribusikan (multi-helper atau snapshot legacy).</td></tr>}
                  </tbody>
                </table>
              </div>
              <h3>Cup / Item Dipacking</h3>
              <div className="prod-table-wrap">
                <table className="prod-table op-an-table">
                  <thead><tr><th>Cup / Item</th><th>PO</th><th>Line</th><th className="num">Qty Packed</th><th>Tanggal</th></tr></thead>
                  <tbody>
                    {packByUser(helperOpen).map((l) => (
                      <tr key={`${l.po_id}-${l.line_idx}`}>
                        <td>{l.item}</td>
                        <td className="mono">{l.po_number}</td>
                        <td>#{Number(l.line_idx) + 1}</td>
                        <td className="num">{fmtQty(l.packed_qty)}</td>
                        <td>{fmtDate(l.day)}</td>
                      </tr>
                    ))}
                    {!packByUser(helperOpen).length && <tr><td colSpan={5} className="empty-state">Tidak ada Save Packing teratribusi untuk helper ini.</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <p className="muted small op-an-note">{helperData?.attribution?.note}</p>
        </>
      )}

      {tab === "screener" && (
        <>
          <div className="op-an-kpi-row" data-testid="op-an-screen-kpis">
            <KpiCard label="Total Qty Screener" value={`${fmtQty(screenerData?.kpis?.total_qty)}`} testid="op-an-screen-total" />
            <KpiCard label="Avg / Hari" value={fmtQty(screenerData?.kpis?.avg_daily_qty)} testid="op-an-screen-avg" />
            <KpiCard label="Output Minggu Ini" value={fmtQty(screenerData?.kpis?.this_week_qty)} testid="op-an-screen-week" />
            <KpiCard label="Jumlah PO" value={fmtQty(screenerData?.kpis?.po_count)} testid="op-an-screen-po" />
            <KpiCard label="Jumlah Line" value={fmtQty(screenerData?.kpis?.line_count)} testid="op-an-screen-line" />
          </div>
          <div className="op-an-chart">
            <h3>Output Screener Mingguan</h3>
            <WeekBars weeks={screenerData?.weekly_trend?.weeks || []} testid="op-an-screen-weekbars" />
          </div>
          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-screen-table">
            <table className="prod-table op-an-table">
              <thead>
                <tr>
                  <th>Screener</th>
                  <th className="num">Total Qty</th>
                  <th className="num">Avg / Hari Aktif</th>
                  <th className="num">Mingguan</th>
                  <th className="num">PO</th>
                  <th className="num">Line</th>
                  <th>Cup / Item</th>
                  <th>Terakhir</th>
                </tr>
              </thead>
              <tbody>
                {(screenerData?.users || []).map((r) => (
                  <tr key={r.email} className="op-an-click" onClick={() => setScreenerOpen(screenerOpen === r.email ? null : r.email)} data-testid={`op-an-screen-${r.email}`}>
                    <td><strong>{r.name}</strong><div className="muted small mono">{r.email}</div></td>
                    <td className="num">{fmtQty(r.total_qty)}</td>
                    <td className="num">{fmtQty(r.avg_daily_qty)}</td>
                    <td className="num">{fmtQty(r.this_week_qty)}</td>
                    <td className="num">{fmtQty(r.po_count)}</td>
                    <td className="num">{fmtQty(r.line_count)}</td>
                    <td>{(r.cups || []).map((c) => `${c.item}${c.qty ? ` ${fmtQty(c.qty)}` : ""}`).join(" · ") || "—"}</td>
                    <td>{fmtDate(r.last_activity_at)}</td>
                  </tr>
                ))}
                {!screenerData?.users?.length && !busy && <tr><td colSpan={8} className="empty-state">Tidak ada aktivitas screener pada periode ini.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="op-an-cards op-an-mobile" data-testid="op-an-screen-cards">
            {(screenerData?.users || []).map((r) => (
              <article key={r.email} className="op-an-card op-an-click" onClick={() => setScreenerOpen(screenerOpen === r.email ? null : r.email)}>
                <header><strong>{r.name}</strong><span className="muted small mono">{r.email}</span></header>
                <dl>
                  <div><dt>Qty Screener</dt><dd>{fmtQty(r.total_qty)}</dd></div>
                  <div><dt>Avg/hari</dt><dd>{fmtQty(r.avg_daily_qty)}</dd></div>
                  <div><dt>PO</dt><dd>{fmtQty(r.po_count)}</dd></div>
                  <div><dt>Line</dt><dd>{fmtQty(r.line_count)}</dd></div>
                </dl>
              </article>
            ))}
          </div>
          {screenerOpen && (
            <div className="op-an-drill" data-testid="op-an-screen-lines">
              <h3>Cup / Item · {screenerOpen}</h3>
              <p className="muted small">{screenerData?.attribution?.line_note}</p>
              <table className="prod-table op-an-table">
                <thead><tr><th>Cup / Item</th><th>PO</th><th>Line</th><th className="num">Qty</th><th>Tanggal</th></tr></thead>
                <tbody>
                  {(screenerData?.cup_lines || []).filter((l) => l.user_id === screenerOpen).map((l, i) => (
                    <tr key={`${l.po_id}-${l.line_idx}-${i}`}>
                      <td>{l.item}</td>
                      <td className="mono">{l.po_number}</td>
                      <td>#{Number(l.line_idx) + 1}</td>
                      <td className="num">{l.qty_screener == null ? `PO ${fmtQty(l.po_qty_screener)}` : fmtQty(l.qty_screener)}</td>
                      <td>{fmtDate(l.day)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {tab === "design" && (
        <>
          <div className="op-an-kpi-row op-an-kpi-row-4" data-testid="op-an-design-kpis">
            <KpiCard label="Total Design / Line Cetak" value={fmtQty(designData?.kpis?.total_designs)} hint="Distinct printable line dengan output operator" testid="op-an-design-total" />
            <KpiCard label="Operator" value={fmtQty(designData?.kpis?.operator_count)} testid="op-an-design-ops" />
            <KpiCard label="Identity" value="Design / Line Cetak" hint="Tidak ada design_id per line di schema" testid="op-an-design-id" />
            <KpiCard label="Periode" value={`${fmtDate(designData?.period?.from)} → ${fmtDate(designData?.period?.to)}`} testid="op-an-design-period" />
          </div>
          <div className="op-an-chart">
            <h3>Design per Minggu</h3>
            <WeekBars weeks={designData?.weekly || []} testid="op-an-design-weekbars" valueKey="design_count" />
          </div>
          <div className="op-an-section-label">Design per Operator</div>
          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-design-ops-table">
            <table className="prod-table op-an-table">
              <thead><tr><th>Operator</th><th className="num">Jumlah Design / Line</th><th className="num">Qty Completed</th></tr></thead>
              <tbody>
                {(designData?.operators || []).map((r) => (
                  <tr key={r.email}><td><strong>{r.name}</strong></td><td className="num">{fmtQty(r.design_count)}</td><td className="num">{fmtQty(r.qty_completed)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="op-an-cards op-an-mobile" data-testid="op-an-design-ops-cards">
            {(designData?.operators || []).map((r) => (
              <article key={r.email} className="op-an-card">
                <header><strong>{r.name}</strong></header>
                <dl>
                  <div><dt>Design / Line</dt><dd>{fmtQty(r.design_count)}</dd></div>
                  <div><dt>Qty Completed</dt><dd>{fmtQty(r.qty_completed)}</dd></div>
                </dl>
              </article>
            ))}
          </div>
          <div className="op-an-section-label">Cup / Item · Qty Completed</div>
          <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid="op-an-design-table">
            <table className="prod-table op-an-table">
              <thead>
                <tr>
                  <th>Operator</th><th>No PO</th><th>Customer</th><th>Cup / Item</th><th>Line</th><th>Design / Line</th><th className="num">Qty Completed</th><th>Tanggal</th>
                </tr>
              </thead>
              <tbody>
                {(designData?.lines || []).map((r) => (
                  <tr key={`${r.operator_id}-${r.design_id}`}>
                    <td>{r.operator}</td>
                    <td className="mono">{r.po_number}</td>
                    <td>{r.customer}</td>
                    <td>{r.item}</td>
                    <td>#{r.line_idx == null ? "?" : Number(r.line_idx) + 1}</td>
                    <td>{r.design_label}</td>
                    <td className="num">{fmtQty(r.qty_completed)}</td>
                    <td>{fmtDate(r.last_activity_at)}</td>
                  </tr>
                ))}
                {!designData?.lines?.length && !busy && <tr><td colSpan={8} className="empty-state">Tidak ada design/line dengan output operator pada periode ini.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="op-an-cards op-an-mobile" data-testid="op-an-design-line-cards">
            {(designData?.lines || []).map((r) => (
              <article key={`${r.operator_id}-${r.design_id}`} className="op-an-card">
                <header><strong>{r.item}</strong><span className="muted small">{r.operator} · {r.po_number}</span></header>
                <dl>
                  <div><dt>Design / Line</dt><dd>{r.design_label}</dd></div>
                  <div><dt>Qty Completed</dt><dd>{fmtQty(r.qty_completed)}</dd></div>
                </dl>
              </article>
            ))}
          </div>
        </>
      )}

      <p className="muted small op-an-note">
        <BarChart3 size={12} style={{ verticalAlign: "-2px" }} /> Output = qty aktual yang dicatat, bukan qty PO / assignment.
        Minggu = ISO Senin–Minggu (WIB). Avg/hari hanya menghitung hari aktif.
        {(resolvedFrom) && <span data-testid="op-an-period-range"> · {resolvedFrom} → {resolvedTo}</span>}
      </p>

      {detailId && (
        <div className="modal op-an-modal" data-testid="op-an-detail-modal" onClick={(e) => e.target === e.currentTarget && setDetailId(null)}>
          <div className="modal-inner modal-wide op-an-detail" onClick={(e) => e.stopPropagation()}>
            <header className="modal-head">
              <div>
                <p className="eyebrow">Jadwal Produksi · Analitik Output</p>
                <h3 data-testid="op-an-detail-name">{detail?.operator?.name || detailId}</h3>
                <span className="mono muted small">{detail?.operator?.email || detailId}</span>
              </div>
              <button className="icon-button" onClick={() => setDetailId(null)} data-testid="op-an-detail-close"><X size={16} /></button>
            </header>
            {detailBusy && !detail ? <p className="empty-state">Memuat detail…</p> : (
              <div className="op-an-detail-body">
                <div className="op-an-kpi-row">
                  <KpiCard label="Total Output" value={`${fmtQty(detail?.operator?.completed)} pcs`} />
                  <KpiCard label="Avg / Hari Aktif" value={fmtQty(detail?.operator?.avg_daily_qty)} />
                  <KpiCard label="Target / Hari" value={detail?.operator?.daily_target == null ? "—" : fmtQty(detail?.operator?.daily_target)} hint={fmtPctOrNA(detail?.operator?.target_achievement_pct)} />
                  <KpiCard label="Jumlah Job" value={fmtQty(detail?.operator?.job_count)} />
                  <KpiCard label="Jumlah Design" value={fmtQty(detail?.operator?.design_count)} />
                </div>
                <div className="op-an-section-label">Output per Minggu</div>
                <WeekBars weeks={detail?.weekly_trend?.weeks || []} testid="op-an-detail-weeks" />
                <div className="op-an-section-label">Cup / Item yang Dikerjakan</div>
                <div className="prod-table-wrap op-an-table-wrap">
                  <table className="prod-table op-an-table">
                    <thead>
                      <tr>
                        <th>Cup / Item</th><th>No PO</th><th>Line</th><th>Design / Line</th>
                        <th className="num">Assigned</th><th className="num">Completed Print Qty</th><th>Tanggal</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(detail?.jobs || []).filter((j) => Number(j.completed_qty) > 0).map((j) => (
                        <tr key={j.assignment_id}>
                          <td>{j.cup || j.item}</td>
                          <td className="mono">{j.po_number}</td>
                          <td>#{j.line || (j.line_idx == null ? "?" : Number(j.line_idx) + 1)}</td>
                          <td>{j.design_label || "—"}</td>
                          <td className="num">{fmtQty(j.assigned_qty)}</td>
                          <td className="num">{fmtQty(j.completed_qty)}</td>
                          <td>{fmtDate(j.last_activity_at || j.work_date)}</td>
                        </tr>
                      ))}
                      {!(detail?.jobs || []).some((j) => Number(j.completed_qty) > 0) && <tr><td colSpan={7} className="empty-state">Tidak ada line dengan output pada periode ini.</td></tr>}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
