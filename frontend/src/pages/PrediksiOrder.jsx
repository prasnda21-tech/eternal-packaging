import { useCallback, useEffect, useMemo, useState } from "react";
import { RefreshCw, Upload, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";
import "./prediksiOrderDensity.css";
import ImportPreviewModal from "../components/ImportPreviewModal";
import {
  CONFIDENCE_FILTERS,
  EMPTY_PREDIKSI_FILTERS,
  FOLLOWUP_STATUSES,
  PATTERN_FILTERS,
  dashGap,
  dashOmzet,
  dashValue,
  formatStat,
  kpiFilterForCard,
} from "../lib/orderPrediction";

function Kpi({ label, value, testid, active, onClick, tone }) {
  return (
    <button
      type="button"
      className={`kpi-card kpi-card-clickable${active ? " kpi-card-active" : ""}${tone ? ` kpi-${tone}` : ""}`}
      data-testid={testid}
      onClick={onClick}
    >
      <div className="kpi-head"><span>{label}</span></div>
      <strong>{value ?? 0}</strong>
    </button>
  );
}

function Field({ label, children }) {
  return (
    <div className="kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function followupTone(value) {
  const status = String(value || "").toLowerCase();
  if (status.includes("terlambat") || status.includes("risiko")) return "danger";
  if (status.includes("jatuh tempo")) return "urgent";
  if (status.includes("follow-up") || status.includes("follow up")) return "success";
  if (status.includes("tidak aktif")) return "muted";
  return "neutral";
}

export default function PrediksiOrder() {
  const { hasPerm } = useAuth();
  const canImport = hasPerm("reports.order_prediction.import");
  const [tab, setTab] = useState("analisis");
  const [filters, setFilters] = useState(EMPTY_PREDIKSI_FILTERS);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  const params = useMemo(() => ({
    search: filters.search || undefined,
    status: filters.status || undefined,
    pattern: filters.pattern || undefined,
    confidence: filters.confidence || undefined,
    sales: filters.sales || undefined,
  }), [filters]);

  const load = useCallback(async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.orderPrediction(params);
      setData(d);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Prediksi Order"));
    } finally {
      setBusy(false);
    }
  }, [params]);

  useEffect(() => { load(); }, [load]);

  const setFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));
  const summary = data?.summary || {};
  const rows = data?.rows || [];
  const orderRows = data?.order_rows || [];
  const salesOptions = data?.sales_options || [];

  const openDetail = async (row) => {
    setDetailBusy(true);
    try {
      const d = await api.orderPredictionDetail(row.customer_key);
      setDetail(d);
    } catch (e) {
      const det = e.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat detail"));
    } finally {
      setDetailBusy(false);
    }
  };

  const clickKpi = (card) => {
    setTab("analisis");
    const next = kpiFilterForCard(card);
    setFilters((prev) => ({ ...prev, status: prev.status === next ? "" : next }));
  };

  const analysis = detail?.analysis;
  const history = detail?.history || [];

  return (
    <div className="ship-status-page prediksi-order-page" data-testid="prediksi-order-page">
      <div className="ship-status-chrome">
        <header className="ship-status-heading page-heading prediksi-heading">
          <div className="prediksi-heading-copy">
            <h1>Prediksi Order Customer</h1>
            <p className="muted small" data-testid="prediksi-disclaimer">
              Estimasi follow-up berdasarkan pola histori order.
            </p>
          </div>
          {canImport && (
            <button type="button" className="primary" onClick={() => setImportOpen(true)} data-testid="prediksi-import-button">
              <Upload size={14} /> Impor Histori Order
            </button>
          )}
        </header>

        <section className="kpi-grid prediksi-kpi" data-testid="prediksi-kpi">
          <Kpi label="Total Customer" value={summary.total_customers} testid="prediksi-kpi-total" active={filters.status === ""} onClick={() => clickKpi("total")} />
          <Kpi label="Waktu Follow-up" value={summary.waktu_follow_up} testid="prediksi-kpi-followup" tone="success" active={filters.status === "waktu_follow_up"} onClick={() => clickKpi("waktu_follow_up")} />
          <Kpi label="Jatuh Tempo" value={summary.jatuh_tempo} testid="prediksi-kpi-due" tone="urgent" active={filters.status === "jatuh_tempo"} onClick={() => clickKpi("jatuh_tempo")} />
          <Kpi label="Terlambat / Risiko" value={summary.terlambat_risiko} testid="prediksi-kpi-late" tone="danger" active={filters.status === "terlambat_risiko"} onClick={() => clickKpi("terlambat_risiko")} />
          <Kpi label="Tidak Aktif" value={summary.tidak_aktif} testid="prediksi-kpi-inactive" active={filters.status === "tidak_aktif"} onClick={() => clickKpi("tidak_aktif")} />
        </section>

        <div className="prediksi-controls-row">
          <div className="prediksi-tabs" data-testid="prediksi-tabs">
            <button type="button" className={tab === "analisis" ? "is-active" : ""} onClick={() => setTab("analisis")} data-testid="prediksi-tab-analisis">Analisis</button>
            <button type="button" className={tab === "orders" ? "is-active" : ""} onClick={() => setTab("orders")} data-testid="prediksi-tab-orders">Data Order</button>
          </div>

          <div className="ship-status-toolbar" data-testid="prediksi-filter-bar">
          <label className="ship-filter-field">
            <span>Customer / Outlet</span>
            <input
              type="text"
              value={filters.search}
              onChange={(e) => setFilter("search", e.target.value)}
              placeholder="Cari customer atau outlet"
              data-testid="prediksi-filter-search"
            />
          </label>
          <label className="ship-filter-field">
            <span>Status</span>
            <select value={filters.status} onChange={(e) => setFilter("status", e.target.value)} data-testid="prediksi-filter-status">
              {FOLLOWUP_STATUSES.map((s) => <option key={s.value || "all"} value={s.value}>{s.label}</option>)}
            </select>
          </label>
          <label className="ship-filter-field">
            <span>Pola</span>
            <select value={filters.pattern} onChange={(e) => setFilter("pattern", e.target.value)} data-testid="prediksi-filter-pattern">
              {PATTERN_FILTERS.map((s) => <option key={s.value || "all"} value={s.value}>{s.label}</option>)}
            </select>
          </label>
          <label className="ship-filter-field">
            <span>Keyakinan</span>
            <select value={filters.confidence} onChange={(e) => setFilter("confidence", e.target.value)} data-testid="prediksi-filter-confidence">
              {CONFIDENCE_FILTERS.map((s) => <option key={s.value || "all"} value={s.value}>{s.label}</option>)}
            </select>
          </label>
          {salesOptions.length > 0 && (
            <label className="ship-filter-field">
              <span>Sales</span>
              <select value={filters.sales} onChange={(e) => setFilter("sales", e.target.value)} data-testid="prediksi-filter-sales">
                <option value="">Semua</option>
                {salesOptions.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
          )}
          <button type="button" className="ship-icon-btn" onClick={load} title="Muat ulang" data-testid="prediksi-refresh" disabled={busy}>
            <RefreshCw size={14} className={busy ? "spin" : ""} />
          </button>
            <button type="button" className="ship-reset-btn" onClick={() => setFilters(EMPTY_PREDIKSI_FILTERS)} data-testid="prediksi-filter-reset">
              Reset
            </button>
          </div>
        </div>
        {err && <div className="error" data-testid="prediksi-error">{err}</div>}
      </div>

      {tab === "analisis" ? (
        <div className="ship-status-grid prediksi-compact-table" data-testid="prediksi-table">
          <table className="ship-table">
            <thead>
              <tr>
                <th>Customer / Outlet</th>
                <th>Sales</th>
                <th>Order Terakhir</th>
                <th>Prediksi Order</th>
                <th>Status</th>
                <th>Pola</th>
                <th>Keyakinan</th>
                <th className="num">Total</th>
                <th>Aksi</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.customer_key}
                  className="prediksi-row"
                  data-testid={`prediksi-row-${r.customer_key}`}
                  onClick={() => openDetail(r)}
                >
                  <td className="prediksi-customer-cell" title={[r.customer, r.outlet].filter(Boolean).join(" · ") || undefined}>
                    <strong>{r.customer || "-"}</strong>
                    <span>{r.outlet || "-"}</span>
                  </td>
                  <td>{r.sales || "-"}</td>
                  <td className="mono">{formatDisplayDate(r.last_order_date)}</td>
                  <td className="mono">{formatDisplayDate(r.prediction_mid)}</td>
                  <td data-testid={`prediksi-status-${r.customer_key}`}>
                    <span className={`prediksi-status-pill is-${followupTone(r.followup_status)}`}>{r.followup_status || "-"}</span>
                  </td>
                  <td>{r.pattern || "-"}</td>
                  <td><span className="prediksi-confidence">{r.confidence || "-"}</span></td>
                  <td className="num mono">{r.total_orders}</td>
                  <td>
                    <button
                      type="button"
                      className="prediksi-detail-button"
                      onClick={(event) => {
                        event.stopPropagation();
                        openDetail(r);
                      }}
                    >
                      Detail
                    </button>
                  </td>
                </tr>
              ))}
              {!rows.length && (
                <tr><td colSpan={9} className="muted">Tidak ada customer/outlet pada Order Masuk yang valid.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="ship-status-grid" data-testid="prediksi-order-table">
          <table className="ship-table">
            <thead>
              <tr>
                <th>Customer</th>
                <th>Outlet/Cabang</th>
                <th>Tanggal Order</th>
                <th>Nomor PO</th>
                <th>Sales</th>
                <th>Sumber</th>
                <th>Tanggal Order Sebelumnya</th>
                <th>Jarak Order (Hari)</th>
                <th>Omzet</th>
                <th>Catatan</th>
              </tr>
            </thead>
            <tbody>
              {orderRows.map((r) => (
                <tr key={`${r.id}-${r.po_number}`} data-testid={`prediksi-order-row-${r.po_number}`}>
                  <td className="ship-cust" title={r.customer || undefined}>{r.customer || "-"}</td>
                  <td
                    title={r.outlet || ""}
                    style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  >
                    {r.outlet || "-"}
                  </td>
                  <td className="mono">{formatDisplayDate(r.order_date)}</td>
                  <td className="mono">{r.po_number || "-"}</td>
                  <td>{r.sales || "-"}</td>
                  <td data-testid={`prediksi-source-${r.po_number || r.id}`}>{r.source || "ORDER MASUK"}</td>
                  <td className="mono">{formatDisplayDate(r.previous_order_date)}</td>
                  <td className="num mono" data-testid={`prediksi-gap-${r.po_number}`}>{dashGap(r.gap_days)}</td>
                  <td className="mono" data-testid={`prediksi-omzet-${r.po_number}`}>{dashOmzet(r.omzet)}</td>
                  <td>{r.notes || "-"}</td>
                </tr>
              ))}
              {!orderRows.length && (
                <tr><td colSpan={10} className="muted">Tidak ada Data Order dari Order Masuk.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {detail && analysis && (
        <div className="drawer" role="dialog" aria-modal="true" data-testid="prediksi-detail" onClick={() => setDetail(null)}>
          <div className="drawer-panel" onClick={(e) => e.stopPropagation()}>
            <header className="drawer-head">
              <div>
                <p className="eyebrow">Detail customer</p>
                <h3 data-testid="prediksi-detail-title">{analysis.customer}</h3>
                <p className="muted small" data-testid="prediksi-detail-outlet">{analysis.outlet || "-"}</p>
              </div>
              <button className="icon-button" onClick={() => setDetail(null)} aria-label="Tutup" data-testid="prediksi-detail-close"><X size={18} /></button>
            </header>
            <div className="drawer-body">
              <div className="drawer-block">
                <h4>Ringkasan</h4>
                <dl className="kv-grid">
                  <Field label="Outlet">{analysis.outlet || "-"}</Field>
                  <Field label="Total Order">{analysis.total_orders}</Field>
                  <Field label="Order Pertama">{formatDisplayDate(analysis.first_order_date)}</Field>
                  <Field label="Order Terakhir">{formatDisplayDate(analysis.last_order_date)}</Field>
                  <Field label="Hari Sejak Order">{dashValue(analysis.days_since_order)}</Field>
                </dl>
              </div>
              <div className="drawer-block">
                <h4>Statistik</h4>
                <dl className="kv-grid">
                  <Field label="Median">{formatStat(analysis.median_gap)}</Field>
                  <Field label="Rata-rata">{formatStat(analysis.average_gap)}</Field>
                  <Field label="Deviasi">{formatStat(analysis.deviation_gap)}</Field>
                  <Field label="CV">{formatStat(analysis.cv, 2)}</Field>
                  <Field label="Kestabilan">{analysis.stability}</Field>
                  <Field label="Pola">{analysis.pattern}</Field>
                </dl>
              </div>
              <div className="drawer-block">
                <h4>Prediksi</h4>
                <dl className="kv-grid">
                  <Field label="Prediksi Awal">{formatDisplayDate(analysis.prediction_start)}</Field>
                  <Field label="Prediksi Tengah">{formatDisplayDate(analysis.prediction_mid)}</Field>
                  <Field label="Prediksi Akhir">{formatDisplayDate(analysis.prediction_end)}</Field>
                  <Field label="Mulai Follow-up">{formatDisplayDate(analysis.followup_start)}</Field>
                  <Field label="Status">{analysis.followup_status}</Field>
                  <Field label="Tingkat Keyakinan">{analysis.confidence}</Field>
                </dl>
                <p className="muted small">{analysis.recommendation}</p>
              </div>
              <div className="drawer-block">
                <h4>Riwayat Order</h4>
                <table className="drawer-table" data-testid="prediksi-detail-history">
                  <thead>
                    <tr>
                      <th>Tanggal</th>
                      <th>Nomor PO</th>
                      <th>Jarak</th>
                      <th>Sales</th>
                      <th>Sumber</th>
                      <th>Omzet</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((h) => (
                      <tr key={h.id || h.po_number}>
                        <td>{formatDisplayDate(h.order_date)}</td>
                        <td>{h.po_number || "-"}</td>
                        <td>{dashGap(h.gap_days)}</td>
                        <td>{h.sales || "-"}</td>
                        <td data-testid={`prediksi-detail-source-${h.po_number || h.id}`}>{h.source || "ORDER MASUK"}</td>
                        <td data-testid={`prediksi-detail-omzet-${h.po_number}`}>{dashOmzet(h.omzet)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}
      {detailBusy && <div className="muted small" data-testid="prediksi-detail-loading">Memuat detail...</div>}

      <ImportPreviewModal
        open={importOpen}
        title="Impor Histori Order"
        testid="prediksi-import-modal"
        applyLabel="Impor Histori Terpilih"
        confirmText="Impor baris histori terpilih ke Prediksi Order? Ini bukan PO produksi."
        columns={[
          { key: "customer", label: "Customer" },
          { key: "outlet", label: "Outlet" },
          { key: "order_date", label: "Tanggal Order" },
          { key: "po_number", label: "No Order" },
          { key: "sales", label: "Sales" },
          { key: "omzet", label: "Omzet", render: (r) => (r.omzet == null || r.omzet === "" ? "-" : r.omzet) },
          { key: "status", label: "Status", render: (r) => r.status + (r.reason ? ` — ${r.reason}` : "") },
        ]}
        onClose={() => setImportOpen(false)}
        onPreviewFile={(file) => api.orderPredictionImportPreview(file)}
        onApply={async (selected) => {
          const result = await api.orderPredictionImportApply(selected);
          if (result?.prediction) setData(result.prediction);
          else await load();
          return result;
        }}
      />
    </div>
  );
}
