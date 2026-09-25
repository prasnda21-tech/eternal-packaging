import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import api from "../lib/api";
import {
  agingChartStatus,
  chartPlotsSeries,
  formatAxisMoney,
  formatLoadedAt,
  groupDetailNotes,
  formatMargin,
  formatMetric,
  formatMoney,
  periodRange,
  shouldApplyDashboardResponse,
  trendShowsDot,
} from "../lib/financialDashboard";

const PRESETS = [
  { id: "today", label: "Hari ini" },
  { id: "month", label: "Bulan ini" },
  { id: "year", label: "Tahun ini" },
  { id: "custom", label: "Kustom" },
];

const SERIES = {
  revenue: "#176B4D",
  cogs: "#D97706",
  net: "#2563EB",
  expense: "#0F3D2E",
  cashOut: "#B45309",
};

function errText(ex) {
  const detail = ex?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return ex?.message || "Gagal memuat";
}

function needsFlag(status, warning, blocker) {
  return status === "incomplete" || status === "error" || Boolean(warning) || Boolean(blocker);
}

function statusLabel(status) {
  if (status === "incomplete") return "Belum lengkap";
  if (status === "complete") return "Lengkap tercatat";
  if (status === "error") return "Gagal muat";
  return "Belum tersedia";
}

function chartStateCopy(status, detail) {
  if (status === "loading") return "Memuat…";
  if (status === "error") return detail || "Gagal muat";
  if (status === "unavailable") return detail || "Belum tersedia";
  if (status === "zero") return "Nol tercatat";
  return detail || "Tidak ada transaksi";
}

function MoneyTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="fd-tip">
      <p>{label}</p>
      {payload.filter((item) => item.value != null).map((item) => (
        <p key={item.dataKey} style={{ color: item.color }}>{item.name}: {formatMoney(item.value)}</p>
      ))}
    </div>
  );
}

function MetricCard({ testid, title, scope, status, amount, hint, flagged }) {
  const shown = formatMetric(status, amount);
  return (
    <article className={`fd-card is-${shown.kind}`} data-testid={testid}>
      <p className="fd-kicker">
        {flagged ? <span className="fd-flag" data-testid={`${testid}-flag`} title="Ada catatan kelengkapan" /> : null}
        {scope}
      </p>
      <h2>{title}</h2>
      <strong data-testid={`${testid}-value`}>{shown.text}</strong>
      {hint ? <p className="fd-hint">{hint}</p> : null}
    </article>
  );
}

function ChartFrame({ title, note, status, detail, testid, children }) {
  const plot = chartPlotsSeries(status);
  return (
    <section className="fd-chart" data-testid={testid}>
      <header>
        <h3>{title}</h3>
        {note ? <span>{note}</span> : null}
        {status === "zero" ? <span className="is-zero">Nol tercatat</span> : null}
        {status === "incomplete" ? <span className="fd-flag" title="Ada catatan kelengkapan" /> : null}
      </header>
      <div className="fd-plot">
        {plot ? children : <p className={`fd-chart-state is-${status || "empty"}`}>{chartStateCopy(status, detail)}</p>}
      </div>
    </section>
  );
}

function PositionStat({ testid, title, status, amount, flagged }) {
  const shown = formatMetric(status, amount);
  return (
    <li data-testid={testid}>
      <span>
        {flagged ? <i className="fd-flag" data-testid={`${testid}-flag`} title="Ada catatan kelengkapan" /> : null}
        {title}
      </span>
      <strong data-testid={`${testid}-value`}>{shown.text}</strong>
    </li>
  );
}

function OutstandingTable({ title, data, testid }) {
  if (!data || data.status === "error") {
    return (
      <section data-testid={testid}>
        <h3>{title}</h3>
        <p className="fd-chart-state is-error">Gagal muat. {data?.detail || ""}</p>
      </section>
    );
  }
  const rows = data.items || [];
  return (
    <section data-testid={testid}>
      <header className="fd-panel-head">
        <h3>{title}</h3>
        <p>Saat ini · {data.item_total || 0} dokumen</p>
      </header>
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
                <td>{row.href ? <Link to={row.href}>{row.document || "Dokumen"}</Link> : (row.document || "—")}</td>
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
  const [payload, setPayload] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detailOpen, setDetailOpen] = useState(false);
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

  useEffect(() => {
    if (!detailOpen) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") setDetailOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detailOpen]);

  const performance = payload?.performance || {};
  const position = payload?.position || {};
  const cash = payload?.cash_movement || {};
  const charts = payload?.charts || {};
  const completeness = payload?.completeness || {};
  const perfScope = "Selama periode";
  const endLabel = payload?.end_date || range.end || "tanggal akhir";
  const posScope = `Saldo per ${endLabel}`;
  const perfFlag = needsFlag(performance.status, null, performance.blocker_code);
  const trendPoints = charts.trend?.points || [];
  const expenseItems = (charts.expense_composition?.items || []).map((row) => ({
    ...row,
    label: row.name || row.code || "Akun",
  }));
  const aging = charts.aging || {};
  const agingStatus = payload
    ? agingChartStatus(aging.receivable?.status, aging.payable?.status)
    : (loading ? "loading" : "empty");
  const agingRows = (aging.receivable?.buckets?.length ? aging.receivable.buckets : aging.payable?.buckets || []).map((row, index) => ({
    label: row.label,
    piutang: aging.receivable?.status === "error" ? null : aging.receivable?.buckets?.[index]?.amount,
    utang: aging.payable?.status === "error" ? null : aging.payable?.buckets?.[index]?.amount,
  }));
  const cashRows = chartPlotsSeries(cash.status) && cash.cash_in != null
    ? [
      { name: "Kas masuk", amount: cash.cash_in },
      { name: "Kas keluar", amount: cash.cash_out },
    ]
    : [];
  const openControls = (payload?.controls?.items || []).filter((item) => item.kind === "action" && (item.status === "incomplete" || item.status === "error")).length;
  const completenessNotes = groupDetailNotes([
    { metric: "Kelengkapan", text: completeness.detail },
    { metric: "Pendapatan, laba kotor, dan laba bersih", text: performance.detail },
    { metric: "Saldo kas dan bank", text: position.cash_and_bank?.warning },
    { metric: "Piutang usaha", text: position.accounts_receivable?.warning },
    { metric: "Utang usaha", text: position.accounts_payable?.warning },
    { metric: "Nilai buku persediaan", text: position.inventory_book?.warning },
    { metric: "GRNI", text: position.grni?.status && position.grni.status !== "complete" ? position.grni.detail : "" },
    { metric: "Pergerakan kas", text: cash.blocker_code ? cash.detail : "" },
  ]);

  return (
    <div className="finance-dashboard-page" data-testid="finance-dashboard-page">
      <header className="fd-head">
        <h1>Dashboard Keuangan</h1>
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
        <p className="fd-loaded" data-testid="finance-loaded">Diperbarui {formatLoadedAt(payload?.loaded_at)}</p>
        <button type="button" className="fd-detail-btn" onClick={() => setDetailOpen(true)} data-testid="finance-open-detail">
          Lihat detail
          {openControls ? <b>{openControls}</b> : null}
        </button>
      </header>

      <p className={`fd-banner is-${completeness.status || "unavailable"}`} data-testid="finance-banner">
        <span className={`fd-status is-${completeness.status || "unavailable"}`} data-testid="finance-completeness">{statusLabel(completeness.status)}</span>
        <span>
          {completeness.status === "incomplete"
            ? "Angka yang tampil adalah saldo tercatat, bukan laporan final."
            : completeness.status === "complete"
              ? "Sumber periode ini terbaca. Bukan tutup buku."
              : "Kelengkapan belum tersedia."}
        </span>
      </p>
      {error ? <p className="fd-banner is-error" role="alert" data-testid="finance-error">Gagal muat. {error}</p> : null}
      {loading && !payload ? <p className="fd-banner" data-testid="finance-loading">Memuat data keuangan…</p> : null}

      <div className="fd-stage" data-testid="finance-panel-ringkasan">
        <div className="fd-kpis">
          <MetricCard testid="finance-revenue" title="Pendapatan" scope={perfScope} status={performance.status} amount={performance.revenue} flagged={perfFlag} />
          <MetricCard testid="finance-gross" title="Laba kotor" scope={perfScope} status={performance.status} amount={performance.gross_profit} hint={`Margin ${formatMargin(performance.gross_margin)}`} flagged={perfFlag} />
          <MetricCard testid="finance-net" title="Laba bersih" scope={perfScope} status={performance.status} amount={performance.net_profit} hint={`Margin ${formatMargin(performance.net_margin)}`} flagged={perfFlag} />
          <MetricCard testid="finance-cash" title="Kas dan bank" scope={posScope} status={position.cash_and_bank?.status} amount={position.cash_and_bank?.amount} flagged={needsFlag(position.cash_and_bank?.status, position.cash_and_bank?.warning, position.cash_and_bank?.blocker_code)} />
        </div>

        <div className="fd-visual">
          <ChartFrame title="Tren pendapatan, HPP, dan laba bersih" note={perfScope} status={payload ? charts.trend?.status : (loading ? "loading" : "empty")} detail={charts.trend?.detail} testid="finance-chart-trend">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trendPoints} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#E2E8F0" />
                <XAxis dataKey="period" tick={{ fontSize: 12, fill: "#3d4f48" }} padding={{ left: 12, right: 12 }} />
                <YAxis tickFormatter={formatAxisMoney} tick={{ fontSize: 12, fill: "#3d4f48" }} width={58} />
                <Tooltip content={<MoneyTooltip />} />
                <Legend />
                <Line dataKey="revenue" name="Pendapatan" stroke={SERIES.revenue} strokeWidth={2} dot={trendShowsDot(trendPoints.length) ? { r: 5 } : false} activeDot={{ r: 5 }} isAnimationActive={false} />
                <Line dataKey="cogs" name="HPP" stroke={SERIES.cogs} strokeWidth={2} dot={trendShowsDot(trendPoints.length) ? { r: 5 } : false} activeDot={{ r: 5 }} isAnimationActive={false} />
                <Line dataKey="net_profit" name="Laba bersih" stroke={SERIES.net} strokeWidth={2} dot={trendShowsDot(trendPoints.length) ? { r: 5 } : false} activeDot={{ r: 5 }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </ChartFrame>
          <div className="fd-side">
            <ChartFrame title="Komposisi beban operasional" note="Nama akun" status={payload ? charts.expense_composition?.status : (loading ? "loading" : "empty")} detail={charts.expense_composition?.detail} testid="finance-chart-expense">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={expenseItems} layout="vertical" margin={{ top: 4, right: 8, left: 4, bottom: 0 }}>
                  <CartesianGrid stroke="#E2E8F0" horizontal={false} />
                  <XAxis type="number" tickFormatter={formatAxisMoney} tick={{ fontSize: 12, fill: "#3d4f48" }} />
                  <YAxis type="category" dataKey="label" width={132} tick={{ fontSize: 12, fill: "#172126" }} tickFormatter={(value) => (String(value).length > 18 ? `${String(value).slice(0, 17)}…` : value)} />
                  <Tooltip content={<MoneyTooltip />} />
                  <Bar dataKey="amount" name="Beban" fill={SERIES.expense} barSize={16} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </ChartFrame>
            <ChartFrame title="Kas masuk dan kas keluar" note={cash.net_change == null ? "Eksternal" : `Eksternal · bersih ${formatMoney(cash.net_change)}`} status={payload ? cash.status : (loading ? "loading" : "empty")} detail={cash.detail} testid="finance-chart-cash">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={cashRows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="#E2E8F0" />
                  <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#172126" }} />
                  <YAxis tickFormatter={formatAxisMoney} tick={{ fontSize: 12, fill: "#3d4f48" }} width={58} />
                  <Tooltip content={<MoneyTooltip />} />
                  <Bar dataKey="amount" name="Nominal" barSize={28} isAnimationActive={false}>
                    {cashRows.map((row) => (
                      <Cell key={row.name} fill={row.name === "Kas keluar" ? SERIES.cashOut : SERIES.revenue} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </ChartFrame>
          </div>
        </div>

        <div className="fd-bottom">
          <section className="fd-position" data-testid="finance-position">
            <h3>{posScope}</h3>
            <ul>
              <PositionStat testid="finance-ar" title="Piutang usaha" status={position.accounts_receivable?.status} amount={position.accounts_receivable?.amount} flagged={needsFlag(position.accounts_receivable?.status, position.accounts_receivable?.warning)} />
              <PositionStat testid="finance-ap" title="Utang usaha" status={position.accounts_payable?.status} amount={position.accounts_payable?.amount} flagged={needsFlag(position.accounts_payable?.status, position.accounts_payable?.warning)} />
              <PositionStat testid="finance-inventory" title="Nilai buku persediaan" status={position.inventory_book?.status} amount={position.inventory_book?.amount} flagged={needsFlag(position.inventory_book?.status, position.inventory_book?.warning)} />
              <PositionStat testid="finance-grni" title="GRNI" status={position.grni?.status} amount={position.grni?.amount} flagged={needsFlag(position.grni?.status, position.grni?.warning)} />
            </ul>
          </section>
          <ChartFrame title="Umur piutang dan utang" note="Saat ini" status={agingStatus} detail={aging.detail || "Kondisi saat ini, bukan saldo historis tanggal filter."} testid="finance-chart-aging">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={agingRows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#E2E8F0" />
                <XAxis dataKey="label" interval={0} tick={{ fontSize: 11, fill: "#172126" }} />
                <YAxis tickFormatter={formatAxisMoney} tick={{ fontSize: 12, fill: "#3d4f48" }} width={58} />
                <Tooltip content={<MoneyTooltip />} />
                <Legend />
                <Bar dataKey="piutang" name="Piutang saat ini" fill={SERIES.revenue} barSize={14} isAnimationActive={false} />
                <Bar dataKey="utang" name="Utang saat ini" fill={SERIES.cogs} barSize={14} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </ChartFrame>
        </div>
      </div>

      {detailOpen ? (
        <div className="fd-drawer-root">
          <button type="button" className="fd-drawer-scrim" aria-label="Tutup detail" onClick={() => setDetailOpen(false)} />
          <div className="fd-drawer" role="dialog" aria-label="Detail dashboard keuangan" data-testid="finance-detail-drawer">
            <header>
              <h3>Detail</h3>
              <button type="button" onClick={() => setDetailOpen(false)} data-testid="finance-detail-close">Tutup</button>
            </header>
            <div className="fd-drawer-body">
              <section data-testid="finance-detail-kelengkapan">
                <h3>Kelengkapan</h3>
                <p className={`fd-status is-${completeness.status || "unavailable"}`}>{statusLabel(completeness.status)}</p>
                {completenessNotes.map((note) => (
                  <p key={note.text} className="fd-note">
                    {note.text}
                    {note.metrics.length ? <span>Metrik: {note.metrics.join(", ")}.</span> : null}
                  </p>
                ))}
              </section>
              <section data-testid="finance-cash-drawer">
                <h3>Kas/Bank</h3>
                <p className="fd-hint">Transfer internal tidak masuk total kas masuk atau kas keluar gabungan. Bukan laporan arus kas formal.</p>
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
              </section>
              <OutstandingTable title="Piutang" data={payload?.receivables} testid="finance-receivables" />
              <OutstandingTable title="Utang" data={payload?.payables} testid="finance-payables" />
              <section data-testid="finance-panel-kontrol">
                <h3>Kontrol pembukuan</h3>
                <p className="fd-hint">Dashboard hanya membaca. Tidak ada posting dari halaman ini.</p>
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
              </section>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
