import { useEffect, useMemo, useState } from "react";
import { BarChart3, RefreshCw } from "lucide-react";
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
  { key: "sample", label: "Sample" },
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

export default function OperatorAnalyticsPanel() {
  const [period, setPeriod] = useState("month");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [operatorId, setOperatorId] = useState("all");
  const [customer, setCustomer] = useState("all");
  const [item, setItem] = useState("all");
  const [section, setSection] = useState("overview");
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

  const chartRows = rows.slice(0, 12);

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

          <div className="op-an-section-label">Workload Operator</div>
          <div className="op-an-kpi-row op-an-kpi-row-4" data-testid="op-an-workload-kpis">
            <KpiCard label="Assigned" value={`${fmtQty(kpis.total_assigned)} pcs`} testid="op-an-kpi-wl-assigned" />
            <KpiCard label="Completed" value={`${fmtQty(kpis.total_completed)} pcs`} testid="op-an-kpi-wl-completed" />
            <KpiCard label="Remaining" value={`${fmtQty(kpis.total_remaining)} pcs`} testid="op-an-kpi-wl-remaining" />
            <KpiCard
              label="Jobs"
              value={`${fmtQty(kpis.active_jobs)} aktif`}
              hint={`${fmtQty(kpis.completed_jobs)} selesai`}
              testid="op-an-kpi-wl-jobs"
            />
          </div>

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
                  <tr key={r.email} data-testid={`op-an-wl-${r.email}`}>
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
                  <tr>
                    <td colSpan={7} className="empty-state">{emptyHint}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="op-an-cards op-an-mobile" data-testid="op-an-workload-cards">
            {rows.map((r) => (
              <article key={r.email} className="op-an-card">
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
        </>
      )}

      {section === "performance" && (
        <>
          <div className="op-an-section-label">Kecepatan Produksi</div>
          <div className="op-an-kpi-row op-an-kpi-row-4" data-testid="op-an-speed-kpis">
            <KpiCard
              label="Avg / Hari Aktif"
              value={`${fmtQty(kpis.avg_daily_qty)} pcs`}
              hint={`${kpis.active_days || 0} hari aktif · bukan 24 jam / shift`}
              testid="op-an-kpi-speed-daily"
            />
            <KpiCard
              label="Avg Completion"
              value={fmtDaysOrNA(kpis.avg_completion_days)}
              hint="Hanya assignment dengan timestamp mulai &amp; selesai"
              testid="op-an-kpi-speed-duration"
            />
            <KpiCard
              label="On-Time %"
              value={fmtPctOrNA(kpis.on_time_pct)}
              hint={kpis.on_time_basis ? `${fmtQty(kpis.on_time)} / ${fmtQty(kpis.on_time_basis)} job ber-deadline` : "Tidak ada job dengan deadline valid"}
              testid="op-an-kpi-ontime-pct"
            />
            <KpiCard
              label="Late / Overdue"
              value={`${fmtQty(kpis.late)} / ${fmtQty(kpis.overdue_active)}`}
              hint={`On-time ${fmtQty(kpis.on_time)} · on progress ${fmtQty(kpis.on_progress)}`}
              testid="op-an-kpi-late"
            />
          </div>

          <div className="op-an-charts" data-testid="op-an-charts">
            <div className="op-an-chart">
              <h3>Assigned vs Completed</h3>
              {chartRows.map((r) => (
                <div key={r.email} className="op-an-chart-row">
                  <span>{r.name}</span>
                  <DualBar a={r.assigned} b={r.completed} aLabel="Assigned" bLabel="Completed" />
                </div>
              ))}
              {!chartRows.length && !busy && <p className="empty-state">{emptyHint}</p>}
            </div>
            <div className="op-an-chart">
              <h3>On-Time vs Late</h3>
              {chartRows.map((r) => (
                <div key={r.email} className="op-an-chart-row">
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
                  <tr key={r.email} data-testid={`op-an-perf-${r.email}`}>
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
                  <tr>
                    <td colSpan={7} className="empty-state">{emptyHint}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="op-an-cards op-an-mobile" data-testid="op-an-perf-cards">
            {rows.map((r) => (
              <article key={r.email} className="op-an-card">
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
        </>
      )}

      {section === "sample" && (
        <>
          <div className="op-an-section-label">Sample Quality</div>
          <div className="op-an-kpi-row op-an-kpi-row-4" data-testid="op-an-sample-kpis">
            <KpiCard
              label="Unique Sample Jobs"
              value={fmtQty(kpis.sample_jobs)}
              hint={`${fmtQty(kpis.sample_submissions)} submissions (termasuk upload ulang)`}
              testid="op-an-kpi-sample-jobs"
            />
            <KpiCard label="Approved" value={fmtQty(kpis.samples_approved)} testid="op-an-kpi-sample-approved" />
            <KpiCard label="Rejected" value={fmtQty(kpis.samples_rejected)} testid="op-an-kpi-sample-rejected" />
            <KpiCard
              label="Pending / First-Pass"
              value={`${fmtQty(kpis.samples_pending)} · ${fmtPctOrNA(kpis.first_pass_rate)}`}
              hint="First-pass = approved pada keputusan pertama"
              testid="op-an-kpi-sample-pending"
            />
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
                  <tr key={r.email} data-testid={`op-an-sample-${r.email}`}>
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
                  <tr>
                    <td colSpan={7} className="empty-state">{emptyHint}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="op-an-cards op-an-mobile" data-testid="op-an-sample-cards">
            {rows.map((r) => (
              <article key={r.email} className="op-an-card">
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

      {section === "overview" && (
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
      )}

      <p className="muted small op-an-note">
        <BarChart3 size={12} style={{ verticalAlign: "-2px" }} /> Qty selesai = aktual per operator, bukan qty PO.
        Minggu = ISO Senin–Minggu (WIB). Avg/hari hanya menghitung hari yang ada progress.
        On-time memakai Deadline PO per assignment. pcs/jam tidak ditampilkan (tidak ada data jam kerja).
        {data?.period?.from && (
          <span data-testid="op-an-period-range"> · {data.period.from} → {data.period.to}</span>
        )}
      </p>
    </section>
  );
}
