import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import api from "../lib/api";
import {
  chartPlotsSeries,
  formatAxisMoney,
  formatLoadedAt,
  formatMoney,
  periodRange,
  shouldApplyDashboardResponse,
  trendShowsDot,
} from "../lib/financialDashboard";
import DashboardTabs from "./DashboardTabs";
import "./salesDashboardPremium.css";
import {
  balanceAsOfLabel,
  comparisonDisplay,
  displayAmount,
  displayCount,
  otherResultLines,
  plBars,
  positionLabel,
} from "../lib/salesDashboard";

const PRESETS = [
  { id: "today", label: "Hari ini" },
  { id: "month", label: "Bulan ini" },
  { id: "year", label: "Tahun ini" },
  { id: "custom", label: "Kustom" },
];

const COLOR = {
  omzet: "#176B4D",
  count: "#2563EB",
  bill: "#2563EB",
  settled: "#176B4D",
  open: "#D97706",
  muted: "#64748B",
  hpp: "#7d8f86",
  negative: "#9a3412",
};

const AGING_COLOR = {
  NOT_DUE: "#176B4D",
  "1_30": "#65A30D",
  "31_60": "#D97706",
  "61_90": "#EA580C",
  OVER_90: "#DC2626",
};

const MOBILE_PANELS = [
  { id: "tren", label: "Tren" },
  { id: "laba", label: "Laba/Rugi" },
  { id: "sales", label: "Sales" },
  { id: "produk", label: "Produk" },
  { id: "umur", label: "Umur piutang" },
];

function errText(ex) {
  const detail = ex?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return ex?.message || "Gagal memuat";
}

function viewStatus(section, loading, pageError) {
  if (pageError) return "error";
  if (!section) return loading ? "loading" : "empty";
  return section.status || "empty";
}

function MoneyTip({ active, payload, label, coordinate, boxRef }) {
  if (!active || !payload?.length) return null;
  const box = boxRef?.current?.getBoundingClientRect();
  const style = box && coordinate
    ? {
        position: "fixed",
        left: Math.min(box.left + coordinate.x + 12, window.innerWidth - 240),
        top: Math.max(8, box.top + coordinate.y - 8),
        zIndex: 80,
        pointerEvents: "none",
      }
    : undefined;
  const node = (
    <div className="sd-tip" style={style} role="tooltip">
      <p>{label}</p>
      {payload.filter((item) => item.value != null).map((item) => (
        <p key={item.dataKey || item.name} style={{ color: item.color || item.fill }}>
          {item.name}: {item.dataKey === "invoice_count" || item.dataKey === "count" ? Number(item.value).toLocaleString("id-ID") : formatMoney(item.value)}
        </p>
      ))}
    </div>
  );
  return style ? createPortal(node, boxRef?.current?.closest("dialog") || document.body) : node;
}

function Info({ label, children }) {
  return (
    <details className="sd-info">
      <summary aria-label={label}>Info</summary>
      <div>{children}</div>
    </details>
  );
}

function ChartFrame({ title, note, scope, status, detail, testid, plotRef, className, legend, footer, children }) {
  const plot = chartPlotsSeries(status);
  return (
    <section className={`sd-card sd-chart${className ? ` ${className}` : ""}`} data-testid={testid}>
      <header>
        <div>
          {scope ? <p className="sd-kicker">{scope}</p> : null}
          <h2>{title}</h2>
        </div>
        {note ? <span>{note}</span> : null}
      </header>
      {legend}
      <div className={`sd-plot${plot ? " is-series" : " is-message"}`} ref={plotRef}>
        {plot ? children : <p className={`sd-state is-${status || "empty"}`}>{status === "loading" ? "Memuat…" : (detail || "Belum tersedia")}</p>}
      </div>
      {footer}
    </section>
  );
}

export default function SalesDashboard({ embedded = false, managed = null }) {
  const external = managed != null;
  const [ownPreset, setOwnPreset] = useState("month");
  const [ownCustom, setOwnCustom] = useState({ start: "", end: "" });
  const [ownPayload, setOwnPayload] = useState(null);
  const [ownLoading, setOwnLoading] = useState(!external);
  const [ownError, setOwnError] = useState("");
  const requestId = useRef(0);
  const [detailTab, setDetailTab] = useState("orders");
  const [mobilePanel, setMobilePanel] = useState("tren");
  const invoiceDialog = useRef(null);
  const trendRef = useRef(null);
  const plRef = useRef(null);
  const [trendMetric, setTrendMetric] = useState("omzet");

  const preset = external ? managed.preset : ownPreset;
  const setPreset = external ? managed.setPreset : setOwnPreset;
  const custom = external ? managed.custom : ownCustom;
  const setCustom = external ? managed.setCustom : setOwnCustom;
  const payload = external ? managed.payload : ownPayload;
  const loading = external ? managed.loading : ownLoading;
  const error = external ? managed.error : ownError;
  const financeAllowed = external ? Boolean(managed.financeAllowed) : false;
  const financePayload = external ? managed.financePayload : null;
  const financeLoading = external ? managed.financeLoading : false;
  const financeError = external ? managed.financeError : "";
  const personalSales = external ? Boolean(managed.personalSales) : false;

  const range = periodRange(preset, custom);
  const rangeReady = Boolean(range.start && range.end && range.start <= range.end);
  const rangeMessage = preset !== "custom"
    ? ""
    : !range.start || !range.end
      ? "Isi tanggal awal dan tanggal akhir."
      : range.start > range.end
        ? "Tanggal awal harus sama atau sebelum tanggal akhir."
        : "";

  const load = useCallback(async () => {
    if (external) {
      managed.reload?.();
      return;
    }
    const current = ++requestId.current;
    if (!rangeReady) {
      setOwnPayload(null);
      setOwnLoading(false);
      setOwnError("");
      return;
    }
    setOwnLoading(true);
    setOwnError("");
    setOwnPayload(null);
    try {
      const data = await api.salesDashboard({
        start_date: range.start,
        end_date: range.end,
        preset,
      });
      if (!shouldApplyDashboardResponse(current, requestId.current)) return;
      setOwnPayload(data);
      setOwnLoading(false);
    } catch (ex) {
      if (!shouldApplyDashboardResponse(current, requestId.current)) return;
      setOwnPayload(null);
      setOwnError(errText(ex));
      setOwnLoading(false);
    }
  }, [external, managed, preset, range.start, range.end, rangeReady]);

  useEffect(() => {
    if (external) return undefined;
    load();
    return () => { requestId.current += 1; };
  }, [external, load]);

  const omzet = payload?.omzet;
  const trend = payload?.trend;
  const settlement = payload?.settlement;
  const sales = payload?.sales;
  const customers = payload?.customers;
  const products = payload?.products;
  const receivables = payload?.receivables;
  const orders = payload?.orders;
  const performance = financePayload?.performance;
  const cash = financePayload?.position?.cash_and_bank;
  const omzetStatus = viewStatus(omzet, loading, error);
  const trendStatus = viewStatus(trend, loading, error);
  const settlementStatus = viewStatus(settlement, loading, error);
  const salesStatus = viewStatus(sales, loading, error);
  const customerStatus = viewStatus(customers, loading, error);
  const productStatus = viewStatus(products, loading, error);
  const receivableStatus = viewStatus(receivables, loading, error);
  const orderStatus = viewStatus(orders, loading, error);
  const financeRequestStatus = financeError ? "error" : (!financePayload ? (financeLoading ? "loading" : "empty") : null);
  const labaStatus = financeRequestStatus || performance?.status || "empty";
  const cashStatus = financeRequestStatus || cash?.status || (financePayload ? "unavailable" : "empty");
  const plStatus = financeRequestStatus || performance?.status || "empty";
  const delta = comparisonDisplay(omzet?.comparison);
  const trendPoints = trend?.points || [];
  const settlementPoints = settlement?.points || [];
  const salesGroups = sales?.groups || [];
  const orderBuckets = orders?.buckets || [];
  const salesMax = Math.max(1, ...salesGroups.map((row) => Number(row.omzet) > 0 ? Number(row.omzet) : 0));
  const bars = plBars(plStatus === "error" ? { status: "error" } : performance);
  const otherLines = otherResultLines(plStatus === "error" ? { status: "error" } : performance);
  const plPlot = bars != null && chartPlotsSeries(plStatus);
  const salesScope = personalSales ? "Penjualan saya" : "Selama periode";
  const financeScope = personalSales ? "Keuangan perusahaan" : "Selama periode";
  const positionScope = positionLabel(payload?.as_of_date);
  const cashScope = personalSales
    ? `Keuangan perusahaan · ${balanceAsOfLabel(financePayload?.end_date || range.end)}`
    : balanceAsOfLabel(financePayload?.end_date || range.end);
  const mobileItems = MOBILE_PANELS.filter((item) => item.id !== "laba" || financeAllowed);
  const showFilters = !external;

  return (
    <div className={`sales-dashboard-page sd-premium${embedded ? " is-embedded" : ""}`} data-testid="sales-dashboard-page" data-preset={preset} data-start={range.start} data-end={range.end} data-finance={financeAllowed ? "yes" : "no"}>
      {showFilters ? (
        <header className="sd-head">
          {embedded ? <span className="sd-filter-label">Periode laporan</span> : <h1>Dashboard Penjualan</h1>}
          <div className="sd-presets" role="group" aria-label="Rentang tanggal">
            {PRESETS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={preset === item.id ? "is-active" : ""}
                aria-pressed={preset === item.id}
                onClick={() => setPreset(item.id)}
                data-testid={`sales-preset-${item.id}`}
              >
                {item.label}
              </button>
            ))}
          </div>
          {preset === "custom" ? (
            <div className="sd-custom">
              <input aria-label="Tanggal awal" type="date" value={custom.start} onChange={(event) => setCustom((cur) => ({ ...cur, start: event.target.value }))} data-testid="sales-custom-start" />
              <input aria-label="Tanggal akhir" type="date" value={custom.end} onChange={(event) => setCustom((cur) => ({ ...cur, end: event.target.value }))} data-testid="sales-custom-end" />
            </div>
          ) : (
            <p className="sd-range" data-testid="sales-range">{range.start} – {range.end}</p>
          )}
          <button type="button" className="sd-reload" disabled={loading || !rangeReady} onClick={load} data-testid="sales-reload">Muat ulang</button>
          <p className="sd-loaded" data-testid="sales-loaded">Diperbarui {formatLoadedAt(payload?.loaded_at)}</p>
        </header>
      ) : (
        <p className="sd-loaded sd-loaded-inline" data-testid="sales-loaded">Diperbarui {formatLoadedAt(payload?.loaded_at)}</p>
      )}

      {showFilters && rangeMessage ? <p className="sd-banner" data-testid="sales-range-note">{rangeMessage}</p> : null}
      {error ? <p className="sd-banner is-error" role="alert" data-testid="sales-error">Gagal muat. {error}</p> : null}

      <div className="sd-board">
        <div className={`sd-kpis${financeAllowed ? "" : " is-two"}`}>
          <section className="sd-card sd-kpi" data-testid="sales-omzet">
            <div className="sd-kpi-top">
              <p className="sd-kicker" data-testid="scope-penjualan">{salesScope}</p>
              <Info label="Tentang penjualan bersih">
                <p>Setelah diskon faktur, sebelum PPN dan biaya lain. Tanggal acuan adalah tanggal faktur. Retur sebagian tidak memiliki dokumen, jadi tidak mengurangi angka ini.</p>
                <p>{omzet?.basis_note}</p>
                <p><span data-testid="sales-invoice-count">{displayCount(omzetStatus, omzet?.invoice_count, omzet?.detail)}</span> faktur</p>
              </Info>
            </div>
            <h2>Penjualan bersih</h2>
            {omzetStatus === "complete" || omzetStatus === "zero" ? (
              <strong className="sd-hero" data-testid="sales-omzet-value">{displayAmount(omzetStatus, omzet?.amount, omzet?.detail)}</strong>
            ) : (
              <p className="sd-empty" data-testid="sales-omzet-value">{displayAmount(omzetStatus, omzet?.amount, omzet?.detail)}</p>
            )}
            <p className={`sd-delta is-${delta.arrow || "none"}`} data-testid="sales-omzet-delta">
              {delta.arrow === "up" ? "↑ " : null}
              {delta.arrow === "down" ? "↓ " : null}
              {delta.text}
              {omzet?.comparison?.detail && delta.text !== "—" ? <span>{omzet.comparison.detail}</span> : null}
            </p>
          </section>

          {financeAllowed ? (
            <section className="sd-card sd-kpi" data-testid="kpi-laba">
              <div className="sd-kpi-top">
                <p className="sd-kicker" data-testid="scope-keuangan">{financeScope}</p>
                <Info label="Tentang laba bersih">
                  <p>Laba bersih dari laba rugi: pendapatan dikurangi HPP, beban operasional, dan beban lain, ditambah pendapatan lain. Bukan omzet dikali margin.</p>
                </Info>
              </div>
              <h2>Laba bersih</h2>
              {labaStatus === "complete" || labaStatus === "zero" ? (
                <strong className="sd-hero" data-testid="kpi-laba-value">{displayAmount(labaStatus, performance?.net_profit, performance?.detail)}</strong>
              ) : (
                <p className="sd-empty" data-testid="kpi-laba-value">{displayAmount(labaStatus, performance?.net_profit, labaStatus === "empty" ? (performance?.detail || "Tidak ada transaksi") : performance?.detail)}</p>
              )}
            </section>
          ) : null}

          <section className="sd-card sd-kpi" data-testid="kpi-piutang">
            <div className="sd-kpi-top">
              <p className="sd-kicker" data-testid="sales-piutang-scope">{personalSales ? `Penjualan saya · ${positionScope}` : positionScope}</p>
              <Info label="Tentang sisa tagihan">
                <p>Sisa tagihan operasional faktur yang masih terbuka, setelah alokasi penerimaan dan pemakaian saldo pelanggan. Bukan saldo akun piutang dan bukan subledger. Angka ini tidak mengikuti tanggal akhir periode.</p>
              </Info>
            </div>
            <h2>Sisa tagihan saat ini</h2>
            {receivableStatus === "complete" || receivableStatus === "zero" || receivableStatus === "incomplete" ? (
              <strong className="sd-hero" data-testid="kpi-piutang-value">{displayAmount(receivableStatus, receivables?.outstanding, receivables?.detail)}</strong>
            ) : (
              <p className="sd-empty" data-testid="kpi-piutang-value">{displayAmount(receivableStatus, receivables?.outstanding, receivables?.detail)}</p>
            )}
          </section>

          {financeAllowed ? (
            <section className="sd-card sd-kpi" data-testid="kpi-kas">
              <div className="sd-kpi-top">
                <p className="sd-kicker" data-testid="sales-kas-scope">{cashScope}</p>
                <Info label="Tentang kas dan bank">
                  <p>Saldo akun kas dan bank pada neraca sampai tanggal akhir periode, termasuk saldo awal. Bukan kas masuk atau kas keluar periode.</p>
                </Info>
              </div>
              <h2>Kas &amp; Bank</h2>
              {cashStatus === "complete" || cashStatus === "zero" || cashStatus === "incomplete" ? (
                <strong className="sd-hero" data-testid="kpi-kas-value">{displayAmount(cashStatus, cash?.amount, cash?.detail)}</strong>
              ) : (
                <p className="sd-empty" data-testid="kpi-kas-value">{displayAmount(cashStatus, cash?.amount, cashStatus === "empty" ? "Tidak ada transaksi" : cash?.detail)}</p>
              )}
            </section>
          ) : null}
        </div>

        <div className="sd-mobile-nav" role="tablist" aria-label="Panel penjualan">
          {mobileItems.map((item) => (
            <button key={item.id} type="button" role="tab" aria-selected={mobilePanel === item.id} onClick={() => setMobilePanel(item.id)}>{item.label}</button>
          ))}
        </div>

        <div className={`sd-main-grid${financeAllowed ? "" : " is-single"}`}>
          <ChartFrame
            className={`sd-switch-panel${mobilePanel === "tren" ? " is-mobile-on" : ""}`}
            title="Tren penjualan"
            scope={salesScope}
            note={trend?.grain_label ? `Resolusi ${trend.grain_label.toLowerCase()}` : null}
            status={trendStatus}
            detail={trend?.detail || (error ? "Gagal muat" : "")}
            testid="sales-trend"
            plotRef={trendRef}
            legend={<div className="sd-trend-controls">
              <span className="sd-legend-label">{trendMetric === "omzet" ? "Omzet penjualan" : "Jumlah faktur"}</span>
              <div className="sd-metric-switch" role="group" aria-label="Metrik tren">
                <button type="button" aria-pressed={trendMetric === "omzet"} onClick={() => setTrendMetric("omzet")}>Omzet</button>
                <button type="button" aria-pressed={trendMetric === "invoice_count"} onClick={() => setTrendMetric("invoice_count")}>Jumlah faktur</button>
              </div>
            </div>}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trendPoints} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
                <CartesianGrid stroke="#E2E8F0" />
                <XAxis dataKey="label" minTickGap={28} tick={{ fontSize: 11, fill: "#3d4f48" }} padding={{ left: 8, right: 8 }} />
                <YAxis yAxisId="omzet" hide={trendMetric !== "omzet"} tickFormatter={formatAxisMoney} tick={{ fontSize: 11, fill: "#3d4f48" }} width={48} />
                <YAxis yAxisId="count" hide={trendMetric !== "invoice_count"} orientation="left" allowDecimals={false} tick={{ fontSize: 11, fill: "#3d4f48" }} width={28} />
                <Tooltip content={(props) => <MoneyTip {...props} boxRef={trendRef} />} />
                {trendMetric === "omzet" ? <Line yAxisId="omzet" dataKey="omzet" name="Omzet penjualan" stroke={COLOR.omzet} strokeWidth={2} dot={trendShowsDot(trendPoints.length) ? { r: 4 } : false} activeDot={{ r: 5 }} isAnimationActive={false} /> : null}
                {trendMetric === "invoice_count" ? <Line yAxisId="count" dataKey="invoice_count" name="Jumlah faktur" stroke={COLOR.count} strokeWidth={2} dot={trendShowsDot(trendPoints.length) ? { r: 4 } : false} activeDot={{ r: 5 }} isAnimationActive={false} /> : null}
              </LineChart>
            </ResponsiveContainer>
          </ChartFrame>

          {financeAllowed ? (
            <section className={`sd-card sd-chart sd-switch-panel${mobilePanel === "laba" ? " is-mobile-on" : ""}`} data-testid="panel-laba-rugi">
              <header>
                <div>
                  <p className="sd-kicker">{financeScope}</p>
                  <h2>Laba / Rugi</h2>
                </div>
                <Info label="Tentang laba rugi periode">
                  <p>Tiga batang adalah pendapatan, HPP, dan beban operasional selama periode. Laba bersih pada kartu tetap memakai laba rugi lengkap, termasuk pendapatan lain dan beban lain bila ada. Nilai negatif tetap negatif.</p>
                </Info>
              </header>
              <div className={`sd-plot${plPlot ? " is-series" : " is-message"}`} ref={plRef} data-testid="pl-chart" data-amounts={bars ? bars.map((row) => row.amount).join("|") : ""}>
                {plPlot ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={bars} layout="vertical" margin={{ top: 4, right: 12, left: 4, bottom: 0 }}>
                      <CartesianGrid stroke="#E2E8F0" horizontal={false} />
                      <XAxis type="number" tickFormatter={formatAxisMoney} tick={{ fontSize: 11, fill: "#3d4f48" }} />
                      <YAxis type="category" dataKey="label" width={118} tick={{ fontSize: 11, fill: "#172126" }} />
                      <Tooltip content={(props) => <MoneyTip {...props} boxRef={plRef} />} />
                      <Bar dataKey="amount" name="Nilai" isAnimationActive={false} barSize={14}>
                        {bars.map((row) => (
                          <Cell key={row.key} fill={row.amount < 0 ? COLOR.negative : row.key === "revenue" ? COLOR.omzet : COLOR.hpp} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <p className={`sd-state is-${plStatus}`}>{plStatus === "loading" ? "Memuat…" : plStatus === "error" ? "Gagal muat" : (performance?.detail || (plStatus === "empty" ? "Tidak ada transaksi" : "Belum tersedia"))}</p>
                )}
              </div>
              {otherLines.length ? (
                <ul className="sd-other" data-testid="pl-other">
                  {otherLines.map((line) => (
                    <li key={line.key} data-testid={`pl-${line.key}`}>{line.label} {formatMoney(line.amount)}</li>
                  ))}
                </ul>
              ) : null}
            </section>
          ) : null}
        </div>

        <div className="sd-bottom-grid">
          <section className={`sd-card sd-switch-panel${mobilePanel === "sales" ? " is-mobile-on" : ""}`} data-testid="sales-by-sales">
            <header>
              <div>
                <p className="sd-kicker">{salesScope}</p>
                <h2>Penjualan per sales</h2>
              </div>
            </header>
            {sales?.detail ? <p className="sd-note">{sales.detail}</p> : null}
            <div className={`sd-plot${chartPlotsSeries(salesStatus) ? " is-series" : " is-message"}`}>
              {chartPlotsSeries(salesStatus) ? (
                <ol className="sd-ranking">
                  {salesGroups.map((row) => (
                    <li key={row.key}>
                      <span className="sd-sales-name">{row.label}</span>
                      <span className="sd-bar" aria-hidden="true"><i style={{ width: `${row.omzet > 0 ? Math.min(100, (Number(row.omzet) / salesMax) * 100) : 0}%`, background: row.key === "unassigned" || Number(row.omzet) < 0 ? COLOR.muted : COLOR.omzet }} /></span>
                      <strong>{displayAmount(salesStatus, row.omzet)}</strong>
                    </li>
                  ))}
                </ol>
              ) : <p className={`sd-state is-${salesStatus}`}>{salesStatus === "loading" ? "Memuat…" : salesStatus === "error" ? "Gagal muat" : sales?.detail || "Belum tersedia"}</p>}
            </div>
          </section>

          <section className={`sd-card sd-switch-panel${mobilePanel === "produk" ? " is-mobile-on" : ""}`} data-testid="sales-products">
            <header>
              <div>
                <p className="sd-kicker">Nilai sebelum diskon faktur</p>
                <h2>Produk terlaris</h2>
              </div>
              <Info label="Tentang produk terlaris">
                <p>Peringkat memakai nilai baris sebelum diskon faktur. Diskon faktur tidak dialokasikan ke barang. Maksimum lima produk dari respons.</p>
              </Info>
            </header>
            {productStatus === "complete" || productStatus === "incomplete" ? (
              <>
                <ol className="sd-ranking">
                  {(products?.items || []).slice(0, 5).map((item) => (
                    <li key={item.key}>
                      <span className="sd-sales-name">{item.label}</span>
                      <span className="sd-bar" aria-hidden="true"><i style={{ width: `${Math.min(100, (Math.max(Number(item.amount) || 0, 0) / Math.max(1, ...(products?.items || []).map((row) => Math.max(Number(row.amount) || 0, 0)))) * 100)}%` }} /></span>
                      <strong>{item.amount == null ? "—" : formatMoney(item.amount)}</strong>
                    </li>
                  ))}
                </ol>
                {products?.detail ? <p className="sd-note">{products.detail}</p> : null}
              </>
            ) : (
              <p className={`sd-state is-${productStatus}`}>{productStatus === "loading" ? "Memuat…" : productStatus === "error" ? `Gagal muat. ${products?.detail || error || ""}`.trim() : (products?.detail || "Belum tersedia")}</p>
            )}
          </section>

          <section className={`sd-card sd-switch-panel${mobilePanel === "umur" ? " is-mobile-on" : ""}`} data-testid="sales-receivables">
            <header>
              <div>
                <p className="sd-kicker" data-testid="sales-receivables-scope">{positionScope}</p>
                <h2>Umur piutang saat ini</h2>
              </div>
              <Info label="Tentang umur piutang">
                <p>Posisi saat ini, bukan saldo historis di akhir periode. Kelompok memakai tanggal jatuh tempo. Jika tanggal jatuh tempo kosong, tanggal faktur dipakai. Saldo tanpa kedua tanggal masuk Tanggal tidak tersedia dan tetap dihitung pada sisa tagihan.</p>
              </Info>
            </header>
            <p className="sd-note" data-testid="sales-ar-outstanding">Sisa tagihan {displayAmount(receivableStatus, receivables?.outstanding, receivables?.detail)}</p>
            {receivables?.coverage_note ? <p className="sd-note">{receivables.coverage_note}</p> : null}
            <div className={`sd-plot${chartPlotsSeries(receivableStatus) && receivableStatus !== "zero" ? " is-series" : " is-message"}`}>
              {chartPlotsSeries(receivableStatus) && receivableStatus !== "zero" ? (
                <ul className="sd-aging">
                  {(receivables?.aging || []).map((row) => {
                    const max = Math.max(1, ...(receivables?.aging || []).map((item) => Math.max(Number(item.amount) || 0, 0)));
                    return (
                      <li key={row.key}>
                        <span>{row.label}</span>
                        <span className="sd-bar" aria-hidden="true"><i style={{ width: `${(Math.max(Number(row.amount) || 0, 0) / max) * 100}%`, background: AGING_COLOR[row.key] || COLOR.omzet }} /></span>
                        <strong>{formatMoney(row.amount)}</strong>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className={`sd-state is-${receivableStatus}`}>{receivableStatus === "loading" ? "Memuat…" : (receivableStatus === "zero" ? "Tidak ada saldo piutang saat ini." : receivables?.detail || (error ? "Gagal muat" : "Belum tersedia"))}</p>
              )}
            </div>
            {Number.isFinite(Number(receivables?.unbucketed_outstanding)) && Number(receivables.unbucketed_outstanding) !== 0 ? (
              <p className="sd-note" data-testid="sales-ar-unbucketed">Tanggal tidak tersedia {formatMoney(receivables.unbucketed_outstanding)}. Saldo ini tetap termasuk dalam sisa tagihan.</p>
            ) : null}
          </section>
        </div>

        <div className="sd-foot">
          <span>Penjualan memakai tanggal faktur. Sisa tagihan dan umur piutang adalah posisi saat ini.</span>
          <button type="button" onClick={() => invoiceDialog.current?.showModal()}>Rincian faktur dan pesanan</button>
        </div>
      </div>

      <dialog className="sd-invoice-dialog" ref={invoiceDialog} aria-labelledby="sales-invoice-title">
        <header>
          <div>
            <p className="sd-kicker">Periode {range.start} – {range.end}</p>
            <h2 id="sales-invoice-title">Rincian faktur dan pesanan</h2>
          </div>
          <button type="button" onClick={() => invoiceDialog.current?.close()} aria-label="Tutup rincian faktur">Tutup</button>
        </header>
        <DashboardTabs id="sales-detail" label="Rincian operasional" value={detailTab} onChange={setDetailTab}
          items={[{ id: "orders", label: "Status pesanan" }, { id: "invoices", label: "Faktur" }, { id: "customers", label: "Pelanggan" }]} />
        <div role="tabpanel" id="sales-detail-panel-orders" aria-labelledby="sales-detail-tab-orders" hidden={detailTab !== "orders"} tabIndex={0}>
          <section className="sd-card" data-testid="sales-orders">
            <p className="sd-kicker" data-testid="sales-orders-scope">Saat ini</p>
            <h2>Pesanan penjualan</h2>
            {orderStatus === "complete" || orderStatus === "zero" ? (
              <>
                <p className="sd-note" data-testid="sales-orders-total">Total {displayCount(orderStatus, orders?.total)} pesanan</p>
                <ul className="sd-orders">
                  {orderBuckets.map((bucket) => {
                    const orderMax = Math.max(1, ...orderBuckets.map((row) => Number(row.count) || 0));
                    const width = `${Math.round(((Number(bucket.count) || 0) / orderMax) * 100)}%`;
                    const body = (
                      <>
                        <span>{bucket.label}</span>
                        <span className="sd-bar" aria-hidden="true"><i style={{ width }} /></span>
                        <b>{displayCount(orderStatus, bucket.count)}</b>
                        {bucket.href ? <em>Buka daftar</em> : null}
                      </>
                    );
                    return (
                      <li key={bucket.key}>
                        {bucket.href ? (
                          <Link to={bucket.href} className="sd-order-link" data-testid={`sales-order-${bucket.key}`}>{body}</Link>
                        ) : (
                          <div className="sd-order-link is-static" data-testid={`sales-order-${bucket.key}`}>{body}</div>
                        )}
                      </li>
                    );
                  })}
                </ul>
                {orders?.list_note ? <p className="sd-note">{orders.list_note}</p> : null}
              </>
            ) : (
              <p className={`sd-state is-${orderStatus}`}>{orderStatus === "loading" ? "Memuat…" : orderStatus === "error" ? `Gagal muat. ${orders?.detail || error || ""}`.trim() : (orders?.detail || "Belum tersedia")}</p>
            )}
          </section>
        </div>
        <div role="tabpanel" id="sales-detail-panel-invoices" aria-labelledby="sales-detail-tab-invoices" hidden={detailTab !== "invoices"} tabIndex={0}>
          <p className="sd-note">{omzet?.basis_note}</p>
          {omzetStatus === "complete" || omzetStatus === "zero" || settlementStatus === "complete" || settlementStatus === "error" ? (
            <ul className="sd-split">
              <li data-testid="sales-settled"><span>Faktur lunas</span><strong>{displayCount(settlementStatus, settlement?.settled_count, settlement?.detail)} · {displayAmount(settlementStatus, settlement?.settled_grand_total, settlement?.detail)}</strong></li>
              <li data-testid="sales-open-value"><span>Nilai belum lunas</span><strong>{displayAmount(settlementStatus, settlement?.open_outstanding, settlement?.detail)}</strong></li>
              <li data-testid="sales-open-count"><span>Jumlah faktur belum lunas</span><strong>{displayCount(settlementStatus, settlement?.open_count, settlement?.detail)}</strong></li>
              <li data-testid="sales-bill"><span>Total tagihan</span><strong>{displayAmount(omzetStatus, omzet?.grand_total, omzet?.detail)}</strong></li>
            </ul>
          ) : null}
          <ChartFrame
            title="Faktur dan pembayaran"
            scope={settlement?.scope || "Status pelunasan saat ini"}
            note={settlement?.grain_label ? `Faktur terbit · ${settlement.grain_label.toLowerCase()}` : "Bukan arus kas periode"}
            status={settlementStatus}
            detail={settlement?.detail || (error ? "Gagal muat" : "")}
            testid="sales-settlement"
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={settlementPoints} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#E2E8F0" />
                <XAxis dataKey="label" minTickGap={28} tick={{ fontSize: 11, fill: "#3d4f48" }} />
                <YAxis tickFormatter={formatAxisMoney} tick={{ fontSize: 11, fill: "#3d4f48" }} width={48} />
                <Tooltip />
                <Bar dataKey="grand_total" name="Total tagihan" fill={COLOR.bill} barSize={8} isAnimationActive={false} />
                <Bar dataKey="settled_grand" name="Tagihan lunas" fill={COLOR.settled} barSize={8} isAnimationActive={false} />
                <Bar dataKey="outstanding" name="Nilai belum lunas" fill={COLOR.open} barSize={8} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </ChartFrame>
        </div>
        <div role="tabpanel" id="sales-detail-panel-customers" aria-labelledby="sales-detail-tab-customers" hidden={detailTab !== "customers"} tabIndex={0}>
          <section className="sd-card" data-testid="sales-customers">
            <p className="sd-kicker">Selama periode · customer_id</p>
            <h2>Pelanggan</h2>
            {customerStatus === "complete" || customerStatus === "incomplete" || customerStatus === "zero" ? (
              <>
                <ul className="sd-split">
                  <li data-testid="sales-customer-new"><span>Pelanggan baru</span><strong>{displayCount(customerStatus, customers?.new_count, customers?.detail)}</strong></li>
                  <li data-testid="sales-customer-repeat"><span>Pelanggan repeat</span><strong>{displayCount(customerStatus, customers?.repeat_count, customers?.detail)}</strong></li>
                </ul>
                {customers?.percents_available ? (
                  <p data-testid="sales-customer-percent">Baru dan repeat dihitung dari customer_id stabil.</p>
                ) : (
                  <p className="sd-note" data-testid="sales-customer-coverage">Persentase belum ditampilkan. {customers?.detail}</p>
                )}
              </>
            ) : (
              <p className={`sd-state is-${customerStatus}`}>{customerStatus === "loading" ? "Memuat…" : customerStatus === "error" ? `Gagal muat. ${customers?.detail || error || ""}`.trim() : (customers?.detail || "Belum tersedia")}</p>
            )}
          </section>
        </div>
      </dialog>
    </div>
  );
}
