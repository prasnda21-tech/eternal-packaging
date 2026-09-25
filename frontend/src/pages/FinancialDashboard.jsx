import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import api from "../lib/api";
import {
  formatLoadedAt,
  formatMargin,
  formatMetric,
  formatMoney,
  periodRange,
  shouldApplyDashboardResponse,
} from "../lib/financialDashboard";

const PRESETS = [
  { id: "today", label: "Hari ini" },
  { id: "month", label: "Bulan ini" },
  { id: "year", label: "Tahun ini" },
  { id: "custom", label: "Kustom" },
];

const TABS = [
  { id: "ringkasan", label: "Ringkasan" },
  { id: "piutang", label: "Piutang & Utang" },
  { id: "kontrol", label: "Kontrol Pembukuan" },
];

function errText(ex) {
  const detail = ex?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return ex?.message || "Gagal memuat";
}

function MetricCard({ testid, title, scope, status, amount, hint, warning }) {
  const shown = formatMetric(status, amount);
  return (
    <article className={`fd-card is-${shown.kind}`} data-testid={testid}>
      <p className="fd-kicker">{scope}</p>
      <h2>{title}</h2>
      <strong data-testid={`${testid}-value`}>{shown.text}</strong>
      {hint ? <p className="fd-hint">{hint}</p> : null}
      {warning ? <p className="fd-warn">{warning}</p> : null}
    </article>
  );
}

function ChartFrame({ title, status, detail, testid, children }) {
  return (
    <section className="fd-chart" data-testid={testid}>
      <header>
        <h3>{title}</h3>
        {status === "empty" ? <span>Tidak ada transaksi</span> : null}
        {status === "zero" ? <span>Nol tercatat</span> : null}
        {status === "error" ? <span>Gagal muat</span> : null}
      </header>
      {status === "empty" || status === "error" || status === "unavailable" ? (
        <p className={`fd-chart-state is-${status || "empty"}`}>{detail || (status === "error" ? "Gagal muat" : "Tidak ada transaksi")}</p>
      ) : children}
    </section>
  );
}

function OutstandingTable({ title, data, testid }) {
  if (!data || data.status === "error") {
    return (
      <section className="fd-panel" data-testid={testid}>
        <h3>{title}</h3>
        <p className="fd-chart-state is-error">Gagal muat. {data?.detail || ""}</p>
      </section>
    );
  }
  const rows = data.items || [];
  return (
    <section className="fd-panel" data-testid={testid}>
      <header className="fd-panel-head">
        <h3>{title}</h3>
        <p>Saat ini · {data.item_total || 0} dokumen</p>
      </header>
      <p className="fd-hint">{data.detail}</p>
      {data.difference ? (
        <p className="fd-warn" data-testid={`${testid}-diff`}>
          Selisih subledger dan GL {formatMoney(data.difference)}. {data.difference_note}
        </p>
      ) : (
        <p className="fd-hint">Subledger {formatMoney(data.total_outstanding)} · GL {formatMoney(data.gl_balance)}</p>
      )}
      <div className="fd-table-scroll">
        <table>
          <thead>
            <tr>
              <th>Pihak</th>
              <th>Dokumen</th>
              <th>Jatuh tempo</th>
              <th>Sisa</th>
              <th>Hari</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={6}>{data.status === "zero" ? "Nol tercatat" : "Tidak ada tagihan terbuka"}</td></tr>
            ) : rows.map((row) => (
              <tr key={row.invoice_id || row.document}>
                <td>{row.party || "—"}</td>
                <td>
                  {row.href ? <Link to={row.href}>{row.document || "Dokumen"}</Link> : (row.document || "—")}
                </td>
                <td>{row.due_date || "—"}</td>
                <td>{formatMoney(row.outstanding)}</td>
                <td>{row.days_overdue || 0}</td>
                <td>{row.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function FinancialDashboard() {
  const [preset, setPreset] = useState("month");
  const [custom, setCustom] = useState({ start: "", end: "" });
  const [tab, setTab] = useState("ringkasan");
  const [payload, setPayload] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [drawer, setDrawer] = useState(false);
  const requestId = useRef(0);

  const range = periodRange(preset, custom);
  const rangeReady = Boolean(range.start && range.end && range.start <= range.end);

  const load = useCallback(async () => {
    if (!rangeReady) return;
    const current = requestId.current + 1;
    requestId.current = current;
    setLoading(true);
    setError("");
    try {
      const data = await api.financialDashboard({ start_date: range.start, end_date: range.end });
      if (!shouldApplyDashboardResponse(current, requestId.current)) return;
      setPayload(data);
      setLoading(false);
    } catch (ex) {
      if (!shouldApplyDashboardResponse(current, requestId.current)) return;
      setError(errText(ex));
      setLoading(false);
    }
  }, [range.start, range.end, rangeReady]);

  useEffect(() => { load(); }, [load]);

  const performance = payload?.performance || {};
  const position = payload?.position || {};
  const cash = payload?.cash_movement || {};
  const charts = payload?.charts || {};
  const completeness = payload?.completeness || {};
  const perfScope = "Selama periode";
  const posScope = `Saldo per ${payload?.end_date || range.end || "tanggal akhir"}`;

  return (
    <div className="finance-dashboard-page" data-testid="finance-dashboard-page">
      <header className="fd-head">
        <div>
          <p className="fd-kicker">Keuangan</p>
          <h1>Dashboard Keuangan</h1>
        </div>
        <p className={`fd-completeness is-${completeness.status || "unavailable"}`} data-testid="finance-completeness">
          {completeness.status === "complete" ? "Kelengkapan: lengkap" : completeness.status === "incomplete" ? "Kelengkapan: belum lengkap" : "Kelengkapan: belum tersedia"}
          <span>{completeness.detail || ""}</span>
        </p>
      </header>

      <div className="fd-toolbar">
        <div className="fd-presets" role="group" aria-label="Rentang tanggal">
          {PRESETS.map((item) => (
            <button key={item.id} type="button" className={preset === item.id ? "is-active" : ""} onClick={() => setPreset(item.id)} data-testid={`finance-preset-${item.id}`}>
              {item.label}
            </button>
          ))}
        </div>
        {preset === "custom" ? (
          <div className="fd-custom">
            <input aria-label="Tanggal mulai" type="date" value={custom.start} onChange={(event) => setCustom((cur) => ({ ...cur, start: event.target.value }))} />
            <input aria-label="Tanggal akhir" type="date" value={custom.end} onChange={(event) => setCustom((cur) => ({ ...cur, end: event.target.value }))} />
          </div>
        ) : (
          <p className="fd-range" data-testid="finance-range">{range.start} – {range.end}</p>
        )}
        <button type="button" className="fd-reload" onClick={load} data-testid="finance-reload">Muat ulang</button>
        <p className="fd-loaded" data-testid="finance-loaded">Data terakhir {formatLoadedAt(payload?.loaded_at)}</p>
      </div>

      <div className="fd-tabs" role="tablist" aria-label="Dashboard Keuangan">
        {TABS.map((item) => (
          <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? "is-active" : ""} onClick={() => setTab(item.id)} data-testid={`finance-tab-${item.id}`}>
            {item.label}
          </button>
        ))}
      </div>

      {error ? <p className="fd-banner is-error" role="alert" data-testid="finance-error">Gagal muat. {error}</p> : null}
      {loading && !payload ? <p className="fd-banner" data-testid="finance-loading">Memuat data keuangan…</p> : null}

      {tab === "ringkasan" ? (
        <div className="fd-panel-scroll" data-testid="finance-panel-ringkasan">
          <div className="fd-grid fd-grid-perf">
            <MetricCard testid="finance-revenue" title="Pendapatan" scope={perfScope} status={performance.status} amount={performance.revenue} />
            <MetricCard testid="finance-cogs" title="HPP penjualan" scope={perfScope} status={performance.status} amount={performance.cogs} />
            <MetricCard testid="finance-gross" title="Laba kotor" scope={perfScope} status={performance.status} amount={performance.gross_profit} hint={`Margin ${formatMargin(performance.gross_margin)}`} />
            <MetricCard testid="finance-expense" title="Beban operasional" scope={perfScope} status={performance.status} amount={performance.operating_expense} />
            <MetricCard testid="finance-net" title="Laba bersih" scope={perfScope} status={performance.status} amount={performance.net_profit} hint={`Margin ${formatMargin(performance.net_margin)}`} warning={performance.detail} />
          </div>
          {performance.blocker_code ? <p className="fd-warn">Blocker {performance.blocker_code}. <span>Detail: {performance.detail}</span></p> : null}
          <div className="fd-grid fd-grid-pos">
            <MetricCard testid="finance-cash" title="Kas dan bank" scope={posScope} status={position.cash_and_bank?.status} amount={position.cash_and_bank?.amount} warning={position.cash_and_bank?.warning} />
            <MetricCard testid="finance-ar" title="Piutang usaha" scope={posScope} status={position.accounts_receivable?.status} amount={position.accounts_receivable?.amount} />
            <MetricCard testid="finance-ap" title="Utang usaha" scope={posScope} status={position.accounts_payable?.status} amount={position.accounts_payable?.amount} />
            <MetricCard testid="finance-inventory" title="Nilai buku persediaan" scope={posScope} status={position.inventory_book?.status} amount={position.inventory_book?.amount} warning={position.inventory_book?.warning} hint="Bukan Published Pricing" />
            <MetricCard testid="finance-grni" title="GRNI" scope={posScope} status={position.grni?.status} amount={position.grni?.amount} hint={position.grni?.detail} />
          </div>
          <section className="fd-cash" data-testid="finance-cash-movement">
            <header>
              <h3>Pergerakan kas</h3>
              <button type="button" onClick={() => setDrawer(true)} data-testid="finance-cash-detail">Rincian rekening</button>
            </header>
            <div className="fd-grid fd-grid-cash">
              <MetricCard testid="finance-cash-in" title="Kas masuk" scope={perfScope} status={cash.status} amount={cash.cash_in} />
              <MetricCard testid="finance-cash-out" title="Kas keluar" scope={perfScope} status={cash.status} amount={cash.cash_out} />
              <MetricCard testid="finance-cash-net" title="Perubahan bersih kas" scope={perfScope} status={cash.status} amount={cash.net_change} hint="Bukan saldo akhir" />
            </div>
            <p className="fd-hint">{cash.note || "Bukan laporan arus kas formal."}</p>
          </section>
          <div className="fd-charts">
            <ChartFrame title="Tren pendapatan, HPP, laba bersih" status={charts.trend?.status} detail={charts.trend?.detail} testid="finance-chart-trend">
              <ResponsiveContainer width="100%" height={148}>
                <LineChart data={charts.trend?.points || []} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="#E2E8F0" />
                  <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={48} />
                  <Tooltip />
                  <Legend />
                  <Line dataKey="revenue" name="Pendapatan" stroke="#176B4D" dot={false} />
                  <Line dataKey="cogs" name="HPP" stroke="#D97706" dot={false} />
                  <Line dataKey="net_profit" name="Laba bersih" stroke="#2563EB" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </ChartFrame>
            <ChartFrame title="Komposisi beban operasional" status={charts.expense_composition?.status} detail={charts.expense_composition?.detail} testid="finance-chart-expense">
              <ResponsiveContainer width="100%" height={148}>
                <BarChart data={charts.expense_composition?.items || []} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="#E2E8F0" />
                  <XAxis dataKey="code" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} width={48} />
                  <Tooltip />
                  <Bar dataKey="amount" name="Beban" fill="#0F3D2E" />
                </BarChart>
              </ResponsiveContainer>
            </ChartFrame>
            <ChartFrame title="Umur piutang dan utang · Saat ini" status={!payload ? "empty" : (charts.aging?.receivable?.status === "error" && charts.aging?.payable?.status === "error" ? "error" : (charts.aging?.receivable?.status === "zero" && charts.aging?.payable?.status === "zero" ? "zero" : "complete"))} detail={charts.aging?.detail || "Kondisi saat ini, bukan saldo historis tanggal filter."} testid="finance-chart-aging">
              <ResponsiveContainer width="100%" height={148}>
                <BarChart data={(charts.aging?.receivable?.buckets || []).map((row, index) => ({
                  label: row.label,
                  piutang: row.amount,
                  utang: charts.aging?.payable?.buckets?.[index]?.amount || 0,
                }))} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="#E2E8F0" />
                  <XAxis dataKey="label" tick={{ fontSize: 10 }} />
                  <YAxis tick={{ fontSize: 11 }} width={48} />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="piutang" name="Piutang saat ini" fill="#176B4D" />
                  <Bar dataKey="utang" name="Utang saat ini" fill="#D97706" />
                </BarChart>
              </ResponsiveContainer>
            </ChartFrame>
          </div>
        </div>
      ) : null}

      {tab === "piutang" ? (
        <div className="fd-split" data-testid="finance-panel-piutang">
          <OutstandingTable title="Piutang usaha" data={payload?.receivables} testid="finance-receivables" />
          <OutstandingTable title="Utang usaha" data={payload?.payables} testid="finance-payables" />
        </div>
      ) : null}

      {tab === "kontrol" ? (
        <div className="fd-panel-scroll" data-testid="finance-panel-kontrol">
          <p className="fd-hint">{payload?.controls?.detail}</p>
          <ul className="fd-controls">
            {(payload?.controls?.items || []).map((item) => (
              <li key={item.key} className={`is-${item.kind} is-${item.status}`} data-testid={`finance-control-${item.key}`}>
                <div>
                  <strong>{item.label}</strong>
                  <p>{item.detail || (item.kind === "historical" ? "Bukan antrean kesalahan." : "")}</p>
                </div>
                <b>{item.status === "error" ? "Gagal muat" : item.count}</b>
                {item.href && item.status !== "error" ? <Link to={item.href}>Buka</Link> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {drawer ? (
        <div className="fd-drawer" role="dialog" aria-label="Rincian rekening kas" data-testid="finance-cash-drawer">
          <header>
            <h3>Rincian rekening</h3>
            <button type="button" onClick={() => setDrawer(false)}>Tutup</button>
          </header>
          <div className="fd-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Rekening</th>
                  <th>Awal</th>
                  <th>Masuk</th>
                  <th>Keluar</th>
                  <th>Transfer</th>
                  <th>Akhir</th>
                </tr>
              </thead>
              <tbody>
                {(cash.accounts || []).map((row) => (
                  <tr key={row.account_id}>
                    <td>{row.code} {row.name}</td>
                    <td>{formatMoney(row.opening)}</td>
                    <td>{formatMoney(row.cash_in)}</td>
                    <td>{formatMoney(row.cash_out)}</td>
                    <td>{formatMoney((row.internal_in || 0) - (row.internal_out || 0))}</td>
                    <td>{formatMoney(row.ending)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="fd-hint">Transfer internal tidak masuk total kas masuk atau kas keluar gabungan.</p>
        </div>
      ) : null}
    </div>
  );
}
