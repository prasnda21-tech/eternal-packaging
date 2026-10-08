import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  canAccessOwnerDashboard,
  countOrState,
  formatAxisMoney,
  formatMoney,
  formatOwnerStamp,
  mapOwnerTrend,
  moneyOrState,
  ownerRangeFromSearch,
  shouldApplyDashboardResponse,
} from "../lib/ownerAnalytics";
import "./ownerDashboard.css";

const PRESETS = [
  { id: "today", label: "Hari Ini" },
  { id: "month", label: "Bulan Ini" },
  { id: "year", label: "Tahun Ini" },
  { id: "custom", label: "Kustom" },
];

const MONTHS_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];

/** Format YYYY-MM-DD as "08 Okt 2026" using calendar parts (no TZ reinterpret). */
export function formatOwnerAsOfDay(ymd) {
  if (!ymd || typeof ymd !== "string") return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd.trim());
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (!year || month < 1 || month > 12 || day < 1 || day > 31) return null;
  return `${String(day).padStart(2, "0")} ${MONTHS_ID[month - 1]} ${year}`;
}

/** Compact period range for header, e.g. "01–08 Okt 2026". */
export function formatOwnerPeriodRange(start, end) {
  const a = formatOwnerAsOfDay(start);
  const b = formatOwnerAsOfDay(end);
  if (!a || !b) return `${start || "—"} – ${end || "—"}`;
  const sm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(start).trim());
  const em = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(end).trim());
  if (sm && em && sm[1] === em[1] && sm[2] === em[2]) {
    return `${sm[3]}–${em[3]} ${MONTHS_ID[Number(sm[2]) - 1]} ${sm[1]}`;
  }
  return `${a} – ${b}`;
}

export function ownerSnapshotContext(asOfDate) {
  const day = formatOwnerAsOfDay(asOfDate);
  return day ? `Saat Ini · ${day}` : "Saat Ini";
}

function TrendPlot({ points, status }) {
  if (status === "loading") return <p className="owner-empty" data-testid="owner-trend-loading">Memuat tren…</p>;
  if (status === "error") return <p className="owner-empty" data-testid="owner-trend-error">Tidak dapat memuat tren.</p>;
  if (status === "empty" || !points?.length) {
    return <p className="owner-empty" data-testid="owner-trend-empty">Belum ada invoice final pada periode ini.</p>;
  }
  const width = 640;
  const height = 210;
  const padL = 8;
  const padR = 12;
  const padT = 12;
  const padB = 28;
  const axis = 48;
  const amounts = points.map((p) => Number(p.amount) || 0);
  const yMax = Math.max(...amounts, 1);
  const plotW = Math.max(1, width - axis - padL - padR);
  const plotH = height - padT - padB;
  const xAt = (index) => {
    if (points.length === 1) return axis + padL + plotW / 2;
    return axis + padL + (plotW * index) / (points.length - 1);
  };
  const yAt = (amount) => padT + plotH * (1 - Number(amount) / yMax);
  const line = points.map((point, index) => `${index === 0 ? "M" : "L"}${xAt(index)},${yAt(point.amount)}`).join(" ");
  const area = `${line} L${xAt(points.length - 1)},${yAt(0)} L${xAt(0)},${yAt(0)} Z`;
  const labelEvery = points.length > 8 ? Math.ceil(points.length / 6) : 1;
  const ticks = [0, yMax * 0.5, yMax];
  return (
    <div className="owner-trend" data-testid="owner-trend-plot">
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Tren omzet invoice" preserveAspectRatio="none">
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={axis} x2={width - padR} y1={yAt(tick)} y2={yAt(tick)} stroke="#e7f0eb" />
            <text x={axis - 6} y={yAt(tick) + 4} textAnchor="end" fill="#3d4f48" fontSize="11">{formatAxisMoney(tick)}</text>
          </g>
        ))}
        <path d={area} fill="#176B4D" fillOpacity="0.16" />
        <path d={line} fill="none" stroke="#176B4D" strokeWidth="2.4" />
        {points.map((point, index) => (
          <g key={`${point.label}-${index}`}>
            <circle cx={xAt(index)} cy={yAt(point.amount)} r="3.8" fill="#176B4D">
              <title>{`${point.label}: ${formatMoney(point.amount)}`}</title>
            </circle>
            {index % labelEvery === 0 || index === points.length - 1 ? (
              <text x={xAt(index)} y={height - 8} textAnchor="middle" fill="#3d4f48" fontSize="11">{point.label}</text>
            ) : null}
          </g>
        ))}
      </svg>
    </div>
  );
}

function KpiCard({
  testid, title, tip, valueState, meta, tone, onOpen, openLabel, openDisabled, badge,
}) {
  return (
    <article className={`owner-kpi ${tone || ""}`} data-testid={testid} data-status={valueState.kind}>
      <p className="owner-kpi__title">
        <span className="owner-kpi__title-text">{title}</span>
        {badge ? <span className="owner-kpi__badge" data-testid={`${testid}-badge`}>{badge}</span> : null}
        {tip ? <button type="button" className="owner-kpi__tip" title={tip} aria-label={`Info ${title}`}>ⓘ</button> : null}
      </p>
      <p className={`owner-kpi__value is-${valueState.kind}`}>{valueState.text}</p>
      {meta ? <p className="owner-kpi__meta">{meta}</p> : null}
      {onOpen ? (
        <button type="button" className="owner-link" disabled={openDisabled} onClick={onOpen} data-testid={`${testid}-drill`}>
          {openLabel || "Lihat detail"}
        </button>
      ) : null}
    </article>
  );
}

export default function OwnerDashboard() {
  const { user, hasPerm, loading: authLoading } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const requestRef = useRef(0);
  const range = useMemo(() => ownerRangeFromSearch(location.search), [location.search]);
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [deliveryTab, setDeliveryTab] = useState("upcoming");

  const allowed = canAccessOwnerDashboard(user, hasPerm);

  const setPreset = (preset) => {
    const query = new URLSearchParams(location.search || "");
    query.set("preset", preset);
    if (preset !== "custom") {
      query.delete("start");
      query.delete("end");
    } else {
      query.set("start", range.start);
      query.set("end", range.end);
    }
    navigate({ pathname: "/dashboard/owner", search: `?${query.toString()}` }, { replace: true });
  };

  const setCustomDate = (key, value) => {
    const query = new URLSearchParams(location.search || "");
    query.set("preset", "custom");
    query.set("start", key === "start" ? value : range.start);
    query.set("end", key === "end" ? value : range.end);
    navigate({ pathname: "/dashboard/owner", search: `?${query.toString()}` }, { replace: true });
  };

  useEffect(() => {
    if (authLoading) return undefined;
    if (!allowed) {
      setLoading(false);
      return undefined;
    }
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    setLoading(true);
    setLoadError(null);
    let cancelled = false;
    (async () => {
      try {
        const body = await api.ownerDashboard({
          start_date: range.start,
          end_date: range.end,
          preset: range.preset,
        });
        if (cancelled || !shouldApplyDashboardResponse(requestId, requestRef.current)) return;
        setData(body);
        setLoading(false);
      } catch (err) {
        if (cancelled || !shouldApplyDashboardResponse(requestId, requestRef.current)) return;
        setData(null);
        setLoadError(err?.response?.data?.detail || "Tidak dapat memuat data.");
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [allowed, authLoading, range.start, range.end, range.preset]);

  if (authLoading) {
    return <div className="owner-dash" data-testid="owner-dashboard-loading">Memuat…</div>;
  }
  if (!allowed) {
    return (
      <div className="owner-dash" data-testid="owner-dashboard-denied">
        <p className="owner-banner-error">Dashboard Owner hanya untuk Direktur atau Holding.</p>
      </div>
    );
  }

  const omzetSection = loading ? { status: "loading" } : (data?.omzet || { status: "error" });
  const aovSection = loading ? { status: "loading" } : (data?.aov_invoice || { status: "error" });
  const avgPoSection = loading ? { status: "loading" } : (data?.average_po || { status: "error" });
  const poActiveSection = loading ? { status: "loading" } : (data?.po_active || { status: "error" });
  const lateSection = loading ? { status: "loading" } : (data?.delivery_late || { status: "error" });
  const invoiceCountSection = loading
    ? { status: "loading" }
    : (data?.invoice_count || { status: "error" });

  const trend = mapOwnerTrend(loading ? { status: "loading" } : data?.trend);
  const ops = data?.operations && data.operations.status !== "error" ? data.operations : null;
  const drills = data?.drilldowns || {};
  const openDrill = (key) => {
    const item = drills[key];
    if (!item?.allowed || !item.path) return;
    navigate(item.path);
  };

  const poStatus = ops?.po_status || {};
  const aktif = Number(poStatus.aktif) || 0;
  const selesai = Number(poStatus.selesai) || 0;
  const lateOverlay = Number(poStatus.terlambat_overlay) || 0;
  const denom = Math.max(aktif + selesai, 1);

  const deliveryRows = (() => {
    const delivery = ops?.delivery || {};
    if (deliveryTab === "late") return delivery.late || [];
    if (deliveryTab === "today") return delivery.today || [];
    return delivery.upcoming || [];
  })();

  const stages = (ops?.stage_breakdown?.rows || []).filter((row) => row.count > 0);
  const bottleneck = ops?.stage_breakdown?.bottleneck;
  const periodLabel = formatOwnerPeriodRange(range.start, range.end);
  const snapshotLabel = ownerSnapshotContext(data?.as_of_date);
  const snapshotPanelNote = data?.as_of_date
    ? `Kondisi Saat Ini · ${formatOwnerAsOfDay(data.as_of_date)}`
    : "Kondisi Saat Ini";

  return (
    <div className="owner-dash" data-testid="owner-dashboard">
      <header className="owner-dash__header">
        <div className="owner-dash__title-block">
          <h1>Dashboard Owner</h1>
          <p className="owner-dash__context" data-testid="owner-range-label">
            <span className="owner-dash__context-line">
              <span className="owner-dash__context-key">Kinerja Periode:</span>{" "}
              {periodLabel}
              <span className="owner-dash__context-sep"> · </span>
              Total Eternal (semua cabang)
            </span>
            <span className="owner-dash__context-line" data-testid="owner-ops-label">
              <span className="owner-dash__context-key">Operasional:</span>{" "}
              {data?.as_of_date ? `${snapshotLabel} WIB` : "Saat Ini"}
            </span>
          </p>
        </div>
        <div className="owner-dash__filters">
          <div className="owner-dash__presets" role="group" aria-label="Filter periode">
            {PRESETS.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-pressed={range.preset === item.id}
                data-testid={`owner-preset-${item.id}`}
                onClick={() => setPreset(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          {range.preset === "custom" ? (
            <div className="owner-dash__custom">
              <label>
                Dari
                <input
                  type="date"
                  value={range.start}
                  data-testid="owner-start"
                  onChange={(e) => setCustomDate("start", e.target.value)}
                />
              </label>
              <label>
                Sampai
                <input
                  type="date"
                  value={range.end}
                  data-testid="owner-end"
                  onChange={(e) => setCustomDate("end", e.target.value)}
                />
              </label>
            </div>
          ) : null}
          <span className="owner-dash__stamp" data-testid="owner-loaded-at">
            Diperbarui {formatOwnerStamp(data?.loaded_at)} WIB
          </span>
        </div>
      </header>

      {loadError ? (
        <p className="owner-banner-error" data-testid="owner-load-error">{String(loadError)}</p>
      ) : null}

      <section className="owner-dash__kpis" aria-label="KPI kinerja periode">
        <KpiCard
          testid="owner-kpi-omzet"
          title="Omzet Invoice"
          tip="Nilai after_discount faktur finalized pada periode terpilih. Bukan nilai PO atau pembayaran."
          valueState={moneyOrState(omzetSection, { emptyText: "Belum ada invoice final" })}
          meta={omzetSection.invoice_count != null ? `${omzetSection.invoice_count} faktur · after_discount` : "after_discount · invoice_date"}
          onOpen={() => openDrill("sales_dashboard")}
          openDisabled={!drills.sales_dashboard?.allowed}
          openLabel={drills.sales_dashboard?.allowed ? "Dashboard Penjualan" : "Tanpa izin drill-down"}
        />
        <KpiCard
          testid="owner-kpi-invoice-count"
          title="Jumlah Invoice Final"
          tip="Jumlah faktur finalized yang sama dengan dasar omzet pada periode terpilih."
          valueState={countOrState({
            status: invoiceCountSection.status,
            count: invoiceCountSection.count ?? omzetSection.invoice_count,
          })}
          meta="invoice"
        />
        <KpiCard
          testid="owner-kpi-aov"
          title="AOV Invoice"
          tip="Omzet after_discount ÷ jumlah invoice final pada periode yang sama."
          valueState={moneyOrState(aovSection, { emptyText: "Belum tersedia" })}
          meta="Rp / invoice"
        />
        <KpiCard
          testid="owner-kpi-avg-po"
          title="Rata-rata Nilai PO"
          tip="selling_total PO non-batal dengan harga lengkap (selling_prices_complete) pada periode terpilih."
          valueState={moneyOrState(avgPoSection, { emptyText: "Belum tersedia" })}
          meta={
            avgPoSection.excluded_incomplete_price_count
              ? `${avgPoSection.excluded_incomplete_price_count} PO tanpa harga tidak dihitung`
              : (avgPoSection.po_count != null ? `${avgPoSection.po_count} PO lengkap` : "selling_total · order_date")
          }
          tone={avgPoSection.status === "incomplete" ? "is-warn" : ""}
        />
        <KpiCard
          testid="owner-kpi-po-active"
          title="PO Aktif"
          tip="Jumlah PO aktif pada kondisi operasional saat ini."
          badge="Saat Ini"
          valueState={countOrState(poActiveSection)}
          meta="PO"
          onOpen={() => openDrill("status_po")}
          openDisabled={!drills.status_po?.allowed}
          openLabel={drills.status_po?.allowed ? "Status PO" : "Tanpa izin drill-down"}
        />
        <KpiCard
          testid="owner-kpi-late"
          title="Pengiriman Terlambat"
          tip="Jumlah pengiriman yang terlambat berdasarkan jadwal aktif saat ini."
          badge="Saat Ini"
          valueState={countOrState(lateSection)}
          meta={lateSection.label || "jadwal SJ aktif"}
          tone={Number(lateSection.count) > 0 ? "is-danger" : ""}
          onOpen={() => openDrill("delivery_schedule_late")}
          openDisabled={!drills.delivery_schedule_late?.allowed}
          openLabel={drills.delivery_schedule_late?.allowed ? "Jadwal terlambat" : "Tanpa izin drill-down"}
        />
      </section>

      <section className="owner-dash__kpis" aria-label="KPI keuangan belum siap" style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))", maxWidth: 420 }}>
        <KpiCard
          testid="owner-kpi-gross-margin"
          title="Gross Margin"
          tip="Menunggu HPP/COGS dan jurnal yang tervalidasi."
          valueState={{ kind: "not_ready", text: "Data Belum Siap" }}
          meta="Sedang dalam proses penyusunan data."
          tone="is-muted"
        />
        <KpiCard
          testid="owner-kpi-profit"
          title="Laba"
          tip="Gross/Net profit Owner ditunda hingga data keuangan siap."
          valueState={{ kind: "not_ready", text: "Data Belum Siap" }}
          meta="Bukan omzet − pembelian."
          tone="is-muted"
        />
      </section>

      <section className="owner-dash__grid">
        <div className="owner-panel owner-panel--trend">
          <div className="owner-panel__head">
            <h2>Tren Omzet Invoice</h2>
            <span data-testid="owner-trend-period">{trend.grain || "Kinerja periode"}</span>
          </div>
          <TrendPlot points={trend.points} status={loading ? "loading" : trend.status} />
        </div>

        <div className="owner-panel" data-testid="owner-po-status">
          <div className="owner-panel__head">
            <h2>Status PO</h2>
            <span data-testid="owner-po-status-asof">{snapshotPanelNote}</span>
          </div>
          {!ops ? (
            <p className="owner-empty">{loading ? "Memuat…" : "Tidak dapat memuat status PO."}</p>
          ) : (
            <>
              <div className="owner-po-bars">
                <div className="owner-po-bar">
                  <span>Aktif</span>
                  <div className="owner-po-bar__track"><i style={{ width: `${(aktif / denom) * 100}%` }} /></div>
                  <strong data-testid="owner-po-aktif">{aktif}</strong>
                </div>
                <div className="owner-po-bar">
                  <span>Selesai</span>
                  <div className="owner-po-bar__track"><i style={{ width: `${(selesai / denom) * 100}%` }} /></div>
                  <strong data-testid="owner-po-selesai">{selesai}</strong>
                </div>
                <div className="owner-po-bar is-late">
                  <span>Terlambat*</span>
                  <div className="owner-po-bar__track"><i style={{ width: `${aktif ? (lateOverlay / aktif) * 100 : 0}%` }} /></div>
                  <strong data-testid="owner-po-late-overlay">{lateOverlay}</strong>
                </div>
              </div>
              <p className="owner-po-bar__note">
                *Terlambat adalah overlay pada PO aktif (bukan irisan pie 100%).
              </p>
            </>
          )}
        </div>

        <div className="owner-panel" data-testid="owner-production">
          <div className="owner-panel__head">
            <h2>Progress Produksi</h2>
            <span data-testid="owner-production-asof">{snapshotPanelNote}</span>
          </div>
          {!ops ? (
            <p className="owner-empty">{loading ? "Memuat…" : "Tidak dapat memuat produksi."}</p>
          ) : stages.length === 0 ? (
            <p className="owner-empty">Tidak ada PO aktif per tahap.</p>
          ) : (
            <div className="owner-stages">
              {stages.map((row) => (
                <div
                  key={row.stage}
                  className={`owner-stage ${bottleneck && bottleneck.stage === row.stage ? "is-hot" : ""}`}
                  data-testid={`owner-stage-${row.stage}`}
                >
                  <span>{row.label}</span>
                  <strong>{row.count} PO</strong>
                </div>
              ))}
            </div>
          )}
          <p className="owner-po-bar__note">Jumlah PO pada tahap saat ini — bukan total qty produksi. Metrik on-time produksi ditunda.</p>
          <button
            type="button"
            className="owner-link"
            disabled={!drills.production?.allowed}
            onClick={() => openDrill("production")}
          >
            {drills.production?.allowed ? "Buka Jadwal Produksi" : "Tanpa izin ke produksi"}
          </button>
        </div>
      </section>

      <section className="owner-dash__lower">
        <div className="owner-panel" data-testid="owner-delivery">
          <div className="owner-panel__head">
            <h2>Monitoring Pengiriman</h2>
            <span data-testid="owner-delivery-asof">{snapshotPanelNote}</span>
          </div>
          <div className="owner-tabs" role="tablist">
            {[
              { id: "upcoming", label: `Mendatang (${ops?.delivery?.upcoming_count ?? 0})` },
              { id: "today", label: `Hari ini (${ops?.delivery?.today_count ?? 0})` },
              { id: "late", label: `Terlambat (${ops?.delivery?.late_count ?? 0})` },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={deliveryTab === tab.id}
                data-testid={`owner-delivery-tab-${tab.id}`}
                onClick={() => setDeliveryTab(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>
          {!ops ? (
            <p className="owner-empty">{loading ? "Memuat…" : "Tidak dapat memuat pengiriman."}</p>
          ) : deliveryRows.length === 0 ? (
            <p className="owner-empty" data-testid="owner-delivery-empty">Tidak ada baris pada filter ini.</p>
          ) : (
            <table className="owner-table">
              <thead>
                <tr>
                  <th>SJ / PO</th>
                  <th>Pelanggan</th>
                  <th>Tanggal</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {deliveryRows.map((row) => (
                  <tr key={`${row.id}-${row.sj_number}-${row.delivery_date}`}>
                    <td>
                      <div>{row.sj_number || "—"}</div>
                      <div>{row.po_number || "—"}</div>
                    </td>
                    <td>{row.customer || "—"}</td>
                    <td>{row.delivery_date || "—"}</td>
                    <td>
                      <span className={`owner-pill ${row.status === "terlambat" ? "is-late" : row.status === "hari_ini" ? "is-today" : ""}`}>
                        {row.status_label || row.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="owner-panel" data-testid="owner-exceptions">
          <div className="owner-panel__head">
            <h2>Pengecualian / Perhatian</h2>
            <span data-testid="owner-exceptions-asof">{snapshotPanelNote}</span>
          </div>
          {!ops ? (
            <p className="owner-empty">{loading ? "Memuat…" : "Tidak dapat memuat pengecualian."}</p>
          ) : !(ops.exceptions?.items || []).length ? (
            <p className="owner-empty" data-testid="owner-exceptions-empty">Tidak ada pengecualian operasional saat ini.</p>
          ) : (
            <ul className="owner-exc-list">
              {ops.exceptions.items.map((item, index) => (
                <li key={`${item.category}-${item.po_number}-${index}`}>
                  <span className="owner-exc-cat">{item.category}</span>
                  <div>
                    <strong>{item.label}</strong>
                    <div>{item.po_number ? `${item.po_number} · ` : ""}{item.customer || ""}</div>
                    <div>{item.detail}</div>
                    {item.href_hint ? (
                      <button
                        type="button"
                        className="owner-link"
                        disabled={!drills[item.href_hint]?.allowed && !drills.delivery_schedule_late?.allowed}
                        onClick={() => openDrill(item.href_hint === "delivery_schedule_late" ? "delivery_schedule_late" : item.href_hint)}
                      >
                        Tindak lanjut
                      </button>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
