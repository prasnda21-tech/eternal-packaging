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
  Pie,
  PieChart,
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
import { comparisonDisplay, displayAmount, displayCount, formatRatio } from "../lib/salesDashboard";

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
};

const AGING_COLOR = {
  NOT_DUE: "#176B4D",
  "1_30": "#65A30D",
  "31_60": "#D97706",
  "61_90": "#EA580C",
  OVER_90: "#DC2626",
};

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
  return style ? createPortal(node, document.body) : node;
}

function SeriesLegend({ items, testid, stacked }) {
  return (
    <ul className={`sd-legend${stacked ? " sd-legend-stack" : ""}`} data-testid={testid}>
      {items.map((item) => (
        <li key={item.label}>
          <i style={{ background: item.color }} aria-hidden="true" />
          <span>{item.label}</span>
          {item.value ? <b>{item.value}</b> : null}
        </li>
      ))}
    </ul>
  );
}

function ChartFrame({ title, note, scope, status, detail, testid, plotRef, className, legend, footer, children }) {
  const plot = chartPlotsSeries(status);
  return (
    <section className={`sd-card sd-chart${className ? ` ${className}` : ""}`} data-testid={testid}>
      <header>
        <div>
          <p className="sd-kicker">{scope}</p>
          <h2>{title}</h2>
        </div>
        {note ? <span>{note}</span> : null}
      </header>
      {legend}
      <div className="sd-plot" ref={plotRef}>
        {plot ? children : <p className={`sd-state is-${status || "empty"}`}>{status === "loading" ? "Memuat…" : (detail || "Belum tersedia")}</p>}
      </div>
      {footer}
    </section>
  );
}

function Stat({ label, value, testid }) {
  return (
    <li data-testid={testid}>
      <span>{label}</span>
      <strong>{value}</strong>
    </li>
  );
}

export default function SalesDashboard({ embedded = false }) {
  const [preset, setPreset] = useState("month");
  const [custom, setCustom] = useState({ start: "", end: "" });
  const [payload, setPayload] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestId = useRef(0);
  const trendRef = useRef(null);
  const agingRef = useRef(null);
  const salesRef = useRef(null);
  const settlementRef = useRef(null);

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
    if (!rangeReady) {
      setPayload(null);
      setLoading(false);
      setError("");
      return;
    }
    const current = requestId.current + 1;
    requestId.current = current;
    setLoading(true);
    setError("");
    setPayload(null);
    try {
      const data = await api.salesDashboard({
        start_date: range.start,
        end_date: range.end,
        preset,
      });
      if (!shouldApplyDashboardResponse(current, requestId.current)) return;
      setPayload(data);
      setLoading(false);
    } catch (ex) {
      if (!shouldApplyDashboardResponse(current, requestId.current)) return;
      setPayload(null);
      setError(errText(ex));
      setLoading(false);
    }
  }, [preset, range.start, range.end, rangeReady]);

  useEffect(() => { load(); }, [load]);

  const omzet = payload?.omzet;
  const trend = payload?.trend;
  const settlement = payload?.settlement;
  const sales = payload?.sales;
  const customers = payload?.customers;
  const products = payload?.products;
  const receivables = payload?.receivables;
  const orders = payload?.orders;
  const omzetStatus = viewStatus(omzet, loading, error);
  const trendStatus = viewStatus(trend, loading, error);
  const settlementStatus = viewStatus(settlement, loading, error);
  const salesStatus = viewStatus(sales, loading, error);
  const customerStatus = viewStatus(customers, loading, error);
  const productStatus = viewStatus(products, loading, error);
  const receivableStatus = viewStatus(receivables, loading, error);
  const orderStatus = viewStatus(orders, loading, error);
  const delta = comparisonDisplay(omzet?.comparison);
  const trendPoints = trend?.points || [];
  const settlementPoints = settlement?.points || [];
  const salesGroups = sales?.groups || [];
  const orderBuckets = orders?.buckets || [];
  const orderMax = Math.max(1, ...orderBuckets.map((row) => Number(row.count) || 0));
  const customerSlices = customers?.percents_available
    ? [
        { name: "Pelanggan baru", value: customers.new_count, fill: COLOR.omzet },
        { name: "Pelanggan repeat", value: customers.repeat_count, fill: COLOR.count },
      ].filter((row) => row.value > 0)
    : [];

  return (
    <div className={`sales-dashboard-page${embedded ? " is-embedded" : ""}`} data-testid="sales-dashboard-page" data-preset={preset} data-start={range.start} data-end={range.end}>
      <header className="sd-head">
        {embedded ? <h2>Penjualan</h2> : <h1>Dashboard Penjualan</h1>}
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
        <button type="button" className="sd-reload" onClick={load} data-testid="sales-reload">Muat ulang</button>
        <p className="sd-loaded" data-testid="sales-loaded">Diperbarui {formatLoadedAt(payload?.loaded_at)}</p>
      </header>

      {rangeMessage ? <p className="sd-banner" data-testid="sales-range-note">{rangeMessage}</p> : null}
      {error ? <p className="sd-banner is-error" role="alert" data-testid="sales-error">Gagal muat. {error}</p> : null}

      <div className="sd-scroll">
        <div className="sd-grid">
          <section className="sd-card" data-testid="sales-omzet">
            <p className="sd-kicker">Selama periode · tanggal faktur</p>
            <h2>Omzet penjualan</h2>
            <strong className="sd-hero" data-testid="sales-omzet-value">{displayAmount(omzetStatus, omzet?.amount, omzet?.detail)}</strong>
            <p className={`sd-delta is-${delta.arrow || "none"}`} data-testid="sales-omzet-delta">
              {delta.arrow === "up" ? "↑ " : null}
              {delta.arrow === "down" ? "↓ " : null}
              {delta.text}
              {omzet?.comparison?.detail && delta.text !== "—" ? <span>{omzet.comparison.detail}</span> : null}
            </p>
            {omzetStatus === "complete" || omzetStatus === "zero" ? <p className="sd-note">{omzet?.basis_note}</p> : null}
            {omzetStatus === "complete" || omzetStatus === "zero" || settlementStatus === "complete" || settlementStatus === "error" ? (
              <ul className="sd-split">
                <Stat testid="sales-settled" label="Faktur lunas" value={`${displayCount(settlementStatus, settlement?.settled_count, settlement?.detail)} · ${displayAmount(settlementStatus, settlement?.settled_grand_total, settlement?.detail)}`} />
                <Stat testid="sales-open-value" label="Nilai belum lunas" value={displayAmount(settlementStatus, settlement?.open_outstanding, settlement?.detail)} />
                <Stat testid="sales-open-count" label="Jumlah faktur belum lunas" value={displayCount(settlementStatus, settlement?.open_count, settlement?.detail)} />
                <Stat testid="sales-bill" label="Total tagihan" value={displayAmount(omzetStatus, omzet?.grand_total, omzet?.detail)} />
              </ul>
            ) : null}
          </section>

          <ChartFrame
            title="Tren penjualan"
            scope="Selama periode"
            note={trend?.grain_label ? `Resolusi ${trend.grain_label.toLowerCase()}` : null}
            status={trendStatus}
            detail={trend?.detail || (error ? "Gagal muat" : "")}
            testid="sales-trend"
            plotRef={trendRef}
            legend={chartPlotsSeries(trendStatus) ? (
              <SeriesLegend items={[
                { label: "Omzet penjualan", color: COLOR.omzet },
                { label: "Jumlah faktur", color: COLOR.count },
              ]} />
            ) : null}
          >
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trendPoints} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#E2E8F0" />
                <XAxis dataKey="label" minTickGap={28} tick={{ fontSize: 11, fill: "#3d4f48" }} padding={{ left: 8, right: 8 }} />
                <YAxis yAxisId="omzet" tickFormatter={formatAxisMoney} tick={{ fontSize: 11, fill: "#3d4f48" }} width={48} />
                <YAxis yAxisId="count" orientation="right" allowDecimals={false} tick={{ fontSize: 11, fill: "#3d4f48" }} width={28} />
                <Tooltip content={(props) => <MoneyTip {...props} boxRef={trendRef} />} />
                <Line yAxisId="omzet" dataKey="omzet" name="Omzet penjualan" stroke={COLOR.omzet} strokeWidth={2} dot={trendShowsDot(trendPoints.length) ? { r: 4 } : false} activeDot={{ r: 5 }} isAnimationActive={false} />
                <Line yAxisId="count" dataKey="invoice_count" name="Jumlah faktur" stroke={COLOR.count} strokeWidth={2} dot={trendShowsDot(trendPoints.length) ? { r: 4 } : false} activeDot={{ r: 5 }} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </ChartFrame>

          <section className="sd-card" data-testid="sales-receivables">
            <p className="sd-kicker" data-testid="sales-receivables-scope">Umur piutang · Saat ini</p>
            <h2>Piutang pelanggan</h2>
            <ul className="sd-split">
              <Stat testid="sales-ar-outstanding" label="Saldo piutang" value={displayAmount(receivableStatus, receivables?.outstanding, receivables?.detail)} />
              <Stat testid="sales-ar-count" label="Jumlah faktur belum lunas" value={displayCount(receivableStatus, receivables?.open_invoice_count, receivables?.detail)} />
              <Stat testid="sales-ar-customers" label="Pelanggan berpiutang" value={displayCount(receivableStatus, receivables?.customer_count, receivables?.coverage_note || receivables?.detail)} />
            </ul>
            {receivables?.coverage_note ? <p className="sd-note">{receivables.coverage_note}</p> : null}
            <div className="sd-plot sd-plot-short" ref={agingRef}>
              {chartPlotsSeries(receivableStatus) && receivableStatus !== "zero" ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={receivables?.aging || []} margin={{ top: 8, right: 4, left: 0, bottom: 0 }}>
                    <CartesianGrid stroke="#E2E8F0" />
                    <XAxis dataKey="label" interval={0} minTickGap={8} tick={{ fontSize: 11, fill: "#172126" }} tickFormatter={(value) => String(value).replace(" hari", "").replace("Belum jatuh tempo", "Belum")} />
                    <YAxis tickFormatter={formatAxisMoney} tick={{ fontSize: 11, fill: "#3d4f48" }} width={48} />
                    <Tooltip content={(props) => <MoneyTip {...props} boxRef={agingRef} />} />
                    <Bar dataKey="amount" name="Saldo piutang" isAnimationActive={false}>
                      {(receivables?.aging || []).map((row) => (
                        <Cell key={row.key} fill={AGING_COLOR[row.key] || COLOR.omzet} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <p className={`sd-state is-${receivableStatus}`}>{receivableStatus === "loading" ? "Memuat…" : (receivables?.detail || (error ? "Gagal muat" : "Belum tersedia"))}</p>
              )}
            </div>
          </section>

          <section className="sd-card" data-testid="sales-orders">
            <p className="sd-kicker" data-testid="sales-orders-scope">Saat ini</p>
            <h2>Pesanan penjualan</h2>
            {orderStatus === "complete" || orderStatus === "zero" ? (
              <>
                <p className="sd-note" data-testid="sales-orders-total">Total {displayCount(orderStatus, orders?.total)} pesanan</p>
                <ul className="sd-orders">
                  {orderBuckets.map((bucket) => {
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

          <ChartFrame
            title="Penjualan per sales"
            scope="Selama periode"
            note={salesStatus === "complete" || salesStatus === "incomplete" ? sales?.detail : null}
            status={salesStatus === "incomplete" ? "complete" : salesStatus}
            detail={sales?.detail || (error ? "Gagal muat" : "")}
            testid="sales-by-sales"
            plotRef={salesRef}
            legend={chartPlotsSeries(salesStatus === "incomplete" ? "complete" : salesStatus) ? (
              <SeriesLegend items={[
                { label: "Omzet penjualan", color: COLOR.omzet },
                ...(salesGroups.some((row) => row.key === "unassigned") ? [{ label: "Belum ditetapkan", color: COLOR.muted }] : []),
              ]} />
            ) : null}
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={salesGroups} layout="vertical" margin={{ top: 4, right: 8, left: 4, bottom: 0 }}>
                <CartesianGrid stroke="#E2E8F0" horizontal={false} />
                <XAxis type="number" tickFormatter={formatAxisMoney} tick={{ fontSize: 11, fill: "#3d4f48" }} />
                <YAxis type="category" dataKey="label" width={108} tick={{ fontSize: 11, fill: "#172126" }} tickFormatter={(value) => (String(value).length > 16 ? `${String(value).slice(0, 15)}…` : value)} />
                <Tooltip content={(props) => <MoneyTip {...props} boxRef={salesRef} />} />
                <Bar dataKey="omzet" name="Omzet penjualan" isAnimationActive={false} barSize={14}>
                  {salesGroups.map((row) => (
                    <Cell key={row.key} fill={row.key === "unassigned" ? COLOR.muted : COLOR.omzet} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartFrame>

          <section className="sd-card" data-testid="sales-customers">
            <p className="sd-kicker">Selama periode · customer_id</p>
            <h2>Pelanggan</h2>
            {customerStatus === "complete" || customerStatus === "incomplete" || customerStatus === "zero" ? (
              <>
                <ul className="sd-split">
                  <Stat testid="sales-customer-new" label="Pelanggan baru" value={displayCount(customerStatus, customers?.new_count, customers?.detail)} />
                  <Stat testid="sales-customer-repeat" label="Pelanggan repeat" value={displayCount(customerStatus, customers?.repeat_count, customers?.detail)} />
                </ul>
                {customers?.percents_available ? (
                  <div className="sd-customer-chart">
                    <div className="sd-donut">
                      <ResponsiveContainer width="100%" height="100%">
                        <PieChart>
                          <Pie data={customerSlices} dataKey="value" nameKey="name" innerRadius={22} outerRadius={34} stroke="#fff" strokeWidth={2} isAnimationActive={false}>
                            {customerSlices.map((row) => <Cell key={row.name} fill={row.fill} />)}
                          </Pie>
                          <Tooltip content={({ active, payload }) => (active && payload?.length ? (
                            <div className="sd-tip" role="tooltip">
                              {payload.map((item) => <p key={item.name}>{item.name}: {Number(item.value).toLocaleString("id-ID")}</p>)}
                            </div>
                          ) : null)} />
                        </PieChart>
                      </ResponsiveContainer>
                    </div>
                    <SeriesLegend
                      stacked
                      testid="sales-customer-percent"
                      items={[
                        { label: "Baru", color: COLOR.omzet, value: formatRatio(customers.new_percent) },
                        { label: "Repeat", color: COLOR.count, value: formatRatio(customers.repeat_percent) },
                      ]}
                    />
                  </div>
                ) : (
                  <p className="sd-note" data-testid="sales-customer-coverage">Persentase belum ditampilkan. {customers?.detail}</p>
                )}
              </>
            ) : (
              <p className={`sd-state is-${customerStatus}`}>{customerStatus === "loading" ? "Memuat…" : customerStatus === "error" ? `Gagal muat. ${customers?.detail || error || ""}`.trim() : (customers?.detail || "Belum tersedia")}</p>
            )}
          </section>

          <ChartFrame
            title="Faktur dan pembayaran"
            scope={settlement?.scope || "Status pelunasan saat ini"}
            note={settlement?.grain_label ? `Faktur terbit · ${settlement.grain_label.toLowerCase()}` : "Bukan arus kas periode"}
            status={settlementStatus}
            detail={settlement?.detail || (error ? "Gagal muat" : "")}
            testid="sales-settlement"
            plotRef={settlementRef}
            className="sd-card-span"
            legend={chartPlotsSeries(settlementStatus) ? (
              <SeriesLegend items={[
                { label: "Total tagihan", color: COLOR.bill },
                { label: "Tagihan lunas", color: COLOR.settled },
                { label: "Nilai belum lunas", color: COLOR.open },
              ]} />
            ) : null}
            footer={settlement?.detail && (settlementStatus === "complete" || settlementStatus === "incomplete") ? <p className="sd-note">{settlement.detail}</p> : null}
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={settlementPoints} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#E2E8F0" />
                <XAxis dataKey="label" minTickGap={28} tick={{ fontSize: 11, fill: "#3d4f48" }} />
                <YAxis tickFormatter={formatAxisMoney} tick={{ fontSize: 11, fill: "#3d4f48" }} width={48} />
                <Tooltip content={(props) => <MoneyTip {...props} boxRef={settlementRef} />} />
                <Bar dataKey="grand_total" name="Total tagihan" fill={COLOR.bill} barSize={8} isAnimationActive={false} />
                <Bar dataKey="settled_grand" name="Tagihan lunas" fill={COLOR.settled} barSize={8} isAnimationActive={false} />
                <Bar dataKey="outstanding" name="Nilai belum lunas" fill={COLOR.open} barSize={8} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </ChartFrame>

          <section className="sd-card" data-testid="sales-products">
            <p className="sd-kicker">Selama periode · nilai barang</p>
            <h2>Produk terlaris</h2>
            {productStatus === "complete" || productStatus === "incomplete" ? (
              <>
                <ol className="sd-products">
                  {(products?.items || []).map((item, index) => (
                    <li key={item.key}>
                      <span>{index + 1}</span>
                      <strong>{item.label}</strong>
                      <em>{item.quantity == null ? "—" : `${Number(item.quantity).toLocaleString("id-ID")} ${item.unit || ""}`.trim()}</em>
                      <b>{formatMoney(item.amount)}</b>
                    </li>
                  ))}
                </ol>
                {products?.detail ? <p className="sd-note">{products.detail}</p> : null}
              </>
            ) : (
              <p className={`sd-state is-${productStatus}`}>{productStatus === "loading" ? "Memuat…" : productStatus === "error" ? `Gagal muat. ${products?.detail || error || ""}`.trim() : (products?.detail || "Belum tersedia")}</p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
