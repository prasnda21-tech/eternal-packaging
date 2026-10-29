import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowUpRight,
  BarChart3,
  Calendar,
  Clock,
  ExternalLink,
  Flame,
  GripVertical,
  Info,
  Layers,
  ListOrdered,
  MoreHorizontal,
  ClipboardCheck,
  CalendarClock,
  Timer,
  AlertTriangle,
  Target,
  PauseCircle,
  MapPin,
  PieChart,
  Printer,
  ShieldAlert,
  Truck,
  Users,
  Workflow,
} from "lucide-react";
import { COLUMNS } from "../widgetBoard/widgetGrid";
import {
  canOpenDrill,
  deliveryPlanDrillPath,
  isInteractiveDrillEventTarget,
} from "../../lib/dashboardDrillNav";
import { productionWidgetAllowed, productionWidgetMeta } from "./productionWidgetCatalog";
import { placeWidget, removeWidget } from "./productionWidgetLayout";
import { productionUpdatedLabel } from "./productionFormat";

const DELIVERY_PLAN_PATH = "/produksi/rencana-jadwal-kirim";

const GAP = 12;
const ROW = 72;
const STATE_LABEL = {
  loading: "Memuat",
  empty: "Belum ada data",
  zero: "Nol tercatat",
  incomplete: "Belum lengkap",
  error: "Gagal memuat",
  forbidden: "Tidak ada izin",
};
const QUIET = new Set(["loading", "empty", "error", "forbidden", "incomplete"]);
const GLYPH = {
  po_aktif: Layers,
  perlu_produksi: Printer,
  sisa_qty: Printer,
  po_urgent: Flame,
  po_hold: PauseCircle,
  deadline: Clock,
  approval: ShieldAlert,
  prioritas: ListOrdered,
  output_hari_ini: Printer,
  tren_output: BarChart3,
  distribusi_tahap: Workflow,
  output_operator: Users,
  beban_operator: Users,
  kesiapan: ClipboardCheck,
  target_produksi: Target,
  po_terlambat: AlertTriangle,
  target_realisasi: BarChart3,
  aging: Timer,
  ketepatan: CalendarClock,
  bottleneck: Workflow,
  komposisi: PieChart,
  output_cabang: MapPin,
  delivery_qty: Truck,
  sim_output: BarChart3,
  sim_workflow: Workflow,
  sim_operator: Users,
  sim_composition: PieChart,
};
const TONE = {
  perlu_produksi: "info",
  po_urgent: "warn",
  po_hold: "risk",
  po_aktif: "ok",
  output_hari_ini: "ok",
  tren_output: "analytics",
  distribusi_tahap: "info",
  output_operator: "analytics",
  beban_operator: "analytics",
  kesiapan: "info",
  target_produksi: "analytics",
  po_terlambat: "risk",
  target_realisasi: "analytics",
  aging: "info",
  ketepatan: "ok",
  bottleneck: "warn",
  komposisi: "analytics",
  output_cabang: "info",
  delivery_qty: "warn",
  sim_output: "analytics",
  sim_workflow: "info",
  sim_operator: "analytics",
  sim_composition: "ok",
};
const TITLE = {
  sim_output: "Output Produksi",
  sim_workflow: "Distribusi Tahap Workflow",
  sim_operator: "Performa Operator",
  sim_composition: "Komposisi Produk Diproduksi",
};
const CAPTION = {
  po_aktif: "Belum sepenuhnya terkirim",
  perlu_produksi: "PO dengan sisa cetak",
  sisa_qty: "Per satuan, tidak digabung",
  po_urgent: "Prioritas workflow urgent",
  po_hold: "Snapshot HOLD unik",
  deadline: "Jendela jam, bukan kalender",
  approval: "Design dan Payment terpisah",
  prioritas: "Urutan Kepala Produksi",
  output_hari_ini: "Terkonfirmasi hari ini",
  tren_output: "Per hari, satu satuan",
  distribusi_tahap: "Satu PO, tahap tertinggal",
  output_operator: "Output aktual dan target terkunci",
  beban_operator: "Sisa alokasi aktif",
  kesiapan: "Dua indikator terpisah",
  target_produksi: "Cohort jadwal, bukan kalender",
  po_terlambat: "Satu PO = satu baris",
  target_realisasi: "Awal, revisi, realisasi",
  aging: "Produksi terpisah dari total PO",
  ketepatan: "Produksi dan pengiriman",
  bottleneck: "Bukan kesalahan operator",
  komposisi: "Snapshot konfirmasi, satuan terpisah",
  output_cabang: "Lokasi aktual adalah default",
  delivery_qty: "Monitoring sisa qty berdasarkan tanggal rencana",
  sim_output: "Fixture visual, satuan pcs",
  sim_workflow: "Fixture visual, bukan status sah",
  sim_operator: "Fixture visual, satuan pcs",
  sim_composition: "Fixture visual, satu satuan pcs",
};

function formatCount(value) {
  return Number(value).toLocaleString("id-ID");
}

function DrillPanelLink({ drill, hasPerm, editing, label, children, testId, className = "" }) {
  const navigate = useNavigate();
  const path = !editing && canOpenDrill(drill, hasPerm) ? drill.path : null;
  if (!path) {
    return (
      <div className={className} data-testid={testId}>
        {children}
        {drill?.path && !editing ? (
          <p className="pd-drill-locked" data-testid={`${testId || "drill"}-locked`}>Akses daftar terbatas</p>
        ) : null}
      </div>
    );
  }
  const go = () => navigate(path);
  return (
    <div
      className={`pd-drill-panel ${className}`.trim()}
      role="link"
      tabIndex={0}
      data-testid={testId}
      aria-label={label || "Buka daftar terkait"}
      onClick={(event) => {
        if (isInteractiveDrillEventTarget(event.target)) return;
        go();
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          go();
        }
      }}
    >
      <span className="pd-drill-icon" aria-hidden="true"><ArrowUpRight size={14} /></span>
      {children}
    </div>
  );
}

function Quiet({ status, onRetry, note, title }) {
  if (status === "loading") {
    return (
      <div className="kb-skel" aria-busy="true" aria-label="Memuat">
        <span />
        <span />
        <span />
      </div>
    );
  }
  const fallback = status === "error"
    ? "Muat ulang untuk mencoba lagi."
    : status === "forbidden"
      ? "Kartu ini tidak termasuk akses Anda."
      : status === "incomplete"
        ? "Angka tidak ditampilkan sebelum data lengkap."
        : status === "zero"
          ? "Tidak ada angka pada periode ini."
          : "Sumber data kartu ini belum siap.";
  return (
    <div className={`kb-empty is-${status}`}>
      <strong>{title || STATE_LABEL[status] || "—"}</strong>
      <p>{note || fallback}</p>
      {status === "error" && onRetry ? (
        <button type="button" className="kb-btn" onClick={onRetry}>Coba lagi</button>
      ) : null}
    </div>
  );
}

function Drill({ drill, hasPerm, label }) {
  if (!canOpenDrill(drill, hasPerm)) return null;
  return <Link className="kb-link" to={drill.path}>{label}</Link>;
}

function CountBody({ model, kicker, tone }) {
  const spark = Array.isArray(model.spark) && model.spark.length > 1 ? model.spark : null;
  return (
    <div className="pd-kpi">
      <div>
        <p className="kb-metric">{formatCount(model.count)}</p>
        <p className="kb-kicker">{model.status === "zero" ? "Nol tercatat" : kicker}</p>
        {model.delta ? <p className="pd-delta">{model.delta} · contoh visual</p> : null}
      </div>
      {spark ? <Spark values={spark} tone={tone} /> : null}
    </div>
  );
}

function Spark({ values, tone }) {
  const color = { info: "#2563EB", warn: "#F97316", risk: "#E11D48", ok: "#059669" }[tone] || "#2563EB";
  const max = Math.max(...values);
  const min = Math.min(...values);
  const width = 72;
  const height = 26;
  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - ((value - min) / (max - min || 1)) * (height - 2) - 1;
    return `${x},${y}`;
  }).join(" ");
  return (
    <svg className="pd-spark" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <polyline points={points} fill="none" stroke={color} strokeWidth="2" />
    </svg>
  );
}

function UnitBars({ entries, note }) {
  const max = Math.max(...entries.map((row) => Number(row.qty) || 0), 1);
  return (
    <div className="pd-units">
      {entries.map((row, index) => (
        <div key={`${row.name}-${index}`} className="pd-unit">
          <i className="pd-swatch" data-tone={String(index % 5)} style={row.color ? { background: row.color } : undefined} />
          <span className="pd-unit-name">{row.name}</span>
          <span className="pd-track" data-tone={String(index % 5)}>
            <span style={{ width: `${Math.max(4, (Number(row.qty) / max) * 100)}%`, background: row.color }} />
          </span>
          <strong>{formatCount(row.qty)}{row.suffix || ""}</strong>
        </div>
      ))}
      {note ? <p className="pd-note">{note}</p> : null}
    </div>
  );
}

function splitLabels(labels) {
  const status = [];
  const deadline = [];
  (labels || []).forEach((label) => {
    if (label === "DUE TODAY") deadline.push({ key: "today", text: "Hari ini" });
    else if (label === "OVERDUE") deadline.push({ key: "overdue", text: "Lewat tanggal kirim" });
    else if (label === "≤ 24 jam") deadline.push({ key: "soon", text: "≤ 24 jam" });
    else status.push(label);
  });
  if (!status.length) status.push("NORMAL");
  return { status, deadline };
}

function badgeClass(label) {
  if (label === "URGENT") return "pd-badge is-urgent";
  if (label === "HOLD") return "pd-badge is-hold";
  if (label === "OVERDUE") return "pd-badge is-overdue";
  return "pd-badge";
}

function StackedBars({ model }) {
  const categories = model.categories || [];
  const series = model.series || [];
  const max = Math.max(...series.map((day) => day.parts.reduce((sum, value) => sum + value, 0)), 1);
  return (
    <div className="pd-chart">
      <div className="pd-bars">
        {series.map((day) => {
          const total = day.parts.reduce((sum, value) => sum + value, 0);
          const tip = day.parts.map((value, index) => `${categories[index]?.name || ""} ${formatCount(value)}`).join(", ");
          return (
            <div key={day.label} className="pd-bar-col" title={`${day.label}: ${tip}`}>
              <div className="pd-bar-stack" style={{ height: Math.max(10, Math.round((total / max) * 112)) }}>
                {day.parts.map((value, index) => (
                  <span key={categories[index]?.name || index} style={{ flexGrow: value, background: categories[index]?.color || "#94A3B8" }} />
                ))}
              </div>
              <em>{day.label}</em>
            </div>
          );
        })}
      </div>
      <div className="pd-legend">
        {categories.map((item) => (
          <span key={item.name}><i style={{ background: item.color }} />{item.name}</span>
        ))}
      </div>
      <p className="pd-note">Total tiap hari dalam {model.unit}. Kategori fixture ini tidak dipakai sebagai output operasional.</p>
    </div>
  );
}

function Donut({ model }) {
  let cursor = 0;
  const gradient = (model.slices || []).map((slice) => {
    const start = cursor;
    cursor += slice.pct;
    return `${slice.color} ${start}% ${cursor}%`;
  }).join(", ");
  return (
    <div className="pd-donut-wrap">
      <div className="pd-donut" style={{ background: `conic-gradient(${gradient})` }}>
        <div>
          <strong>{formatCount(model.total)}</strong>
          <span>{model.unit}</span>
        </div>
      </div>
      <ul className="pd-legend-list">
        {(model.slices || []).map((slice) => (
          <li key={slice.name} title={slice.qty == null ? slice.name : `${slice.name}: ${formatCount(slice.qty)} ${model.unit || ""}`}>
            <i className="pd-swatch" style={{ background: slice.color }} />
            <span>{slice.name}</span>
            {slice.qty == null ? null : <span>{formatCount(slice.qty)} {model.unit}</span>}
            <b>{slice.pct}%</b>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PeriodSwitch({ days, onChange, prefix }) {
  return (
    <div className="pd-switch">
      <button type="button" className={`kb-btn${days === 7 ? " is-on" : ""}`} data-testid={`${prefix}-7`} onClick={() => onChange(7)}>7 hari</button>
      <button type="button" className={`kb-btn${days === 30 ? " is-on" : ""}`} data-testid={`${prefix}-30`} onClick={() => onChange(30)}>30 hari</button>
    </div>
  );
}

function OutputToday({ model }) {
  const units = Object.entries(model.byUnit || {});
  if (!units.length) {
    const status = model.status === "incomplete" ? "incomplete" : "zero";
    return <Quiet status={status} note={model.note} />;
  }
  return (
    <div className="pd-output">
      <ul className="pd-output-units">
        {units.map(([unit, qty]) => (
          <li key={unit}>
            <strong>{formatCount(qty)}</strong>
            <span>{unit}</span>
          </li>
        ))}
      </ul>
      {model.note ? <p className="pd-note">{model.note}</p> : null}
      <p className="pd-output-foot">{productionUpdatedLabel(model.updatedAt)}</p>
    </div>
  );
}

function trendTip(point, model) {
  const qty = Number(point.qty) || 0;
  if (qty > 0) return `${point.day}: ${formatCount(qty)} ${model.unit}`;
  if (model.status === "incomplete") return `${point.day}: Histori tidak tersedia.`;
  return `${point.day}: Output 0`;
}

function deliveryChartCeiling(value) {
  const n = Number(value) || 0;
  if (n <= 0) return 1;
  const exp = 10 ** Math.floor(Math.log10(n));
  const scaled = n / exp;
  const nice = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return nice * exp;
}

function deliveryYTicks(ceiling) {
  const top = Number(ceiling) || 1;
  return [top, Math.round(top * 0.5), 0];
}

function DeliveryQtyBody({ model, controls, editing, hasPerm }) {
  const navigate = useNavigate();
  const series = model?.series || [];
  const units = series.map((row) => row.unit);
  const unitKey = units.join("|");
  const [unit, setUnit] = useState(units[0] || "");
  useEffect(() => {
    const list = unitKey ? unitKey.split("|") : [];
    if (!list.length) {
      if (unit) setUnit("");
      return;
    }
    if (!list.includes(unit)) setUnit(list[0]);
  }, [unitKey, unit]);
  const chosen = series.find((row) => row.unit === unit) || series[0];
  const points = chosen?.points || [];
  const max = Math.max(...points.map((point) => Number(point.qty) || 0), 0);
  const ceiling = deliveryChartCeiling(max);
  const ticks = deliveryYTicks(ceiling);
  const label = chosen?.label || "";
  const canOpenPlan = !editing && typeof hasPerm === "function" && (
    hasPerm("delivery.schedule.view") || hasPerm("schedule.view") || hasPerm("nav.delivery_schedule")
  );
  const today = model?.today || "";
  const openPlan = (opts) => {
    if (!canOpenPlan) return;
    navigate(deliveryPlanDrillPath({ ...opts, unit: chosen?.unit || "" }));
  };
  if (!chosen) {
    return <Quiet status={model?.status === "complete" ? "empty" : (model?.status || "empty")} onRetry={controls.onRetry} note={model?.detail} />;
  }
  return (
    <div className="pd-delivery-qty" data-testid="produksi-delivery-qty-body">
      <div className="pd-delivery-controls" data-drill-ignore="true">
        <PeriodSwitch days={controls.days} onChange={controls.setDays} prefix="produksi-delivery-qty-days" />
        {units.length > 1 ? (
          <div className="pd-switch" role="group" aria-label="Satuan Qty Rencana Pengiriman">
            {series.map((row) => (
              <button
                type="button"
                key={row.unit}
                className={`kb-btn${chosen.unit === row.unit ? " is-on" : ""}`}
                data-testid={`produksi-delivery-qty-unit-${row.unit}`}
                onClick={() => setUnit(row.unit)}
              >
                {row.label}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      <div className="pd-delivery-kpis">
        <div
          className={`pd-delivery-kpi is-today${canOpenPlan ? " is-drill" : ""}`}
          data-testid="produksi-delivery-qty-today"
          role={canOpenPlan ? "link" : undefined}
          tabIndex={canOpenPlan ? 0 : undefined}
          onClick={() => openPlan({ status: "hari_ini", dateFrom: today, dateTo: today })}
          onKeyDown={(event) => {
            if (!canOpenPlan) return;
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              openPlan({ status: "hari_ini", dateFrom: today, dateTo: today });
            }
          }}
        >
          {canOpenPlan ? <span className="pd-drill-icon" aria-hidden="true"><ArrowUpRight size={14} /></span> : null}
          <span>
            <Calendar size={14} aria-hidden="true" />
            Rencana Hari Ini
          </span>
          <strong>{formatCount(chosen.todayQty)} <em>{label}</em></strong>
          <p>{formatCount(chosen.todayPoCount)} PO aktif · {model.todayLabel || "hari ini"} WIB</p>
        </div>
        <div
          className={`pd-delivery-kpi is-late${canOpenPlan ? " is-drill" : ""}`}
          data-testid="produksi-delivery-qty-overdue"
          role={canOpenPlan ? "link" : undefined}
          tabIndex={canOpenPlan ? 0 : undefined}
          onClick={() => openPlan({ status: "terlambat" })}
          onKeyDown={(event) => {
            if (!canOpenPlan) return;
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              openPlan({ status: "terlambat" });
            }
          }}
        >
          {canOpenPlan ? <span className="pd-drill-icon" aria-hidden="true"><ArrowUpRight size={14} /></span> : null}
          <span>
            <Clock size={14} aria-hidden="true" />
            Lewat Rencana
          </span>
          <strong>{formatCount(chosen.overdueQty)} <em>{label}</em></strong>
          <p>{formatCount(chosen.overduePoCount)} PO masih bersisa</p>
        </div>
      </div>
      <div className="pd-delivery-chart">
        <div className="pd-delivery-chart-head">
          <strong>Rencana per Hari</strong>
          <span>Snapshot rencana aktif, bukan realisasi pengiriman.</span>
        </div>
        <div className="pd-delivery-plot" data-testid="produksi-delivery-qty-chart">
          <div className="pd-delivery-y" aria-hidden="true">
            {ticks.map((tick) => (
              <span key={`y-${tick}`}>{formatCount(tick)}</span>
            ))}
          </div>
          <div className={`pd-bars pd-output-plot pd-delivery-bars${points.length > 10 ? " is-scroll" : ""}`}>
            {points.map((point) => {
              const qty = Number(point.qty) || 0;
              const poCount = Number(point.poCount) || 0;
              const tipDate = point.tipLabel || point.label || point.day;
              const axis = point.shortLabel || String(point.date || point.day || "").slice(8);
              return (
                <button
                  type="button"
                  key={point.date || point.day}
                  className={`pd-bar-col pd-delivery-bar${qty > 0 ? "" : " is-zero"}`}
                  data-testid="produksi-delivery-qty-bar-col"
                  aria-label={`${tipDate}. Rencana ${formatCount(qty)} ${label}. Jumlah PO ${formatCount(poCount)}.`}
                  onClick={() => {
                    const day = point.date || point.day;
                    if (!canOpenPlan || !day) return;
                    openPlan({ dateFrom: day, dateTo: day });
                  }}
                >
                  <span className="pd-output-slot">
                    <span className="pd-output-spacer" style={{ flexGrow: qty > 0 ? Math.max(ceiling - qty, 0) : 1 }} />
                    {qty > 0 ? (
                      <span className="pd-output-bar" data-testid="produksi-delivery-qty-bar" style={{ flexGrow: qty }} />
                    ) : (
                      <span className="pd-output-zero" data-testid="produksi-delivery-qty-zero" />
                    )}
                  </span>
                  <em>{axis}</em>
                  <span className="pd-delivery-tip" role="tooltip" data-testid="produksi-delivery-qty-tip">
                    <strong>{tipDate}</strong>
                    <span className="pd-delivery-tip-row">
                      <i className="is-qty" aria-hidden="true" />
                      Rencana: {formatCount(qty)} {label}
                    </span>
                    <span className="pd-delivery-tip-row">
                      <i className="is-po" aria-hidden="true" />
                      Jumlah PO: {formatCount(poCount)}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
      <p className="pd-delivery-foot">
        <Info size={14} aria-hidden="true" />
        <span>Menampilkan total qty sisa per tanggal rencana. Gunakan menu untuk melihat detail di Rencana Pengiriman.</span>
      </p>
    </div>
  );
}

function TrendChart({ model, controls }) {
  const series = model.series || [];
  const incomplete = model.status === "incomplete" && !series.length;
  const empty = !series.length;
  const max = Math.max(...series.map((item) => Number(item.qty) || 0), 0);
  return (
    <div className="pd-chart">
      <PeriodSwitch days={controls.days} onChange={controls.setDays} prefix="produksi-trend-days" />
      {(model.units || []).length > 1 ? (
        <div className="pd-switch">
          {model.units.map((unit) => (
            <button type="button" key={unit} className={`kb-btn${model.unit === unit ? " is-on" : ""}`} data-testid={`produksi-trend-unit-${unit}`} onClick={() => controls.setTrendUnit(unit)}>{unit}</button>
          ))}
        </div>
      ) : null}
      {incomplete || empty ? (
        <Quiet
          status={model.status === "incomplete" ? "incomplete" : "zero"}
          title={model.status === "incomplete" ? "Data historis tidak lengkap" : undefined}
          note={model.note}
        />
      ) : (
        <>
          <div className={`pd-bars pd-output-plot${series.length > 10 ? " is-scroll" : ""}`}>
            {series.map((point) => {
              const qty = Number(point.qty) || 0;
              return (
                <div key={point.day} className="pd-bar-col" title={trendTip(point, model)}>
                  <div className="pd-output-slot">
                    <span className="pd-output-spacer" style={{ flexGrow: qty > 0 ? Math.max(max - qty, 0) : 1 }} />
                    {qty > 0 ? (
                      <span className="pd-output-bar" data-testid="produksi-trend-bar" style={{ flexGrow: qty }} />
                    ) : (
                      <span className="pd-output-zero" data-testid="produksi-trend-zero" />
                    )}
                  </div>
                  <em>{point.day.slice(8)}</em>
                </div>
              );
            })}
          </div>
          <p className="pd-note">{model.unit} · {series.length} hari{model.note ? ` · ${model.note}` : ""}</p>
        </>
      )}
    </div>
  );
}

function WorkflowBars({ model, controls }) {
  const bars = model.bars || [];
  const max = Math.max(...bars.map((bar) => Number(bar.count) || 0), 1);
  const scopeNote = model.scope === "semua" ? "Seluruh workflow" : "Produksi saja";
  return (
    <div className="pd-chart">
      <div className="pd-switch">
        <button type="button" className={`kb-btn${model.scope === "produksi" ? " is-on" : ""}`} data-testid="produksi-scope-produksi" onClick={() => controls.setScope("produksi")}>Produksi Saja</button>
        <button type="button" className={`kb-btn${model.scope === "semua" ? " is-on" : ""}`} data-testid="produksi-scope-semua" onClick={() => controls.setScope("semua")}>Seluruh Workflow</button>
      </div>
      {bars.length ? (
        <div className="pd-hbars">
          {bars.map((bar) => (
            <div key={bar.stage} className="pd-hbar" title={`${bar.stage}: ${formatCount(bar.count)} PO unik. ${scopeNote}.`}>
              <span>{bar.stage}</span>
              <span className="pd-track"><span style={{ width: `${Math.max(4, (Number(bar.count) / max) * 100)}%` }} /></span>
              <strong>{formatCount(bar.count)}</strong>
            </div>
          ))}
        </div>
      ) : <Quiet status={model.status === "error" ? "error" : "zero"} note={model.note || "Tidak ada PO pada cakupan ini."} />}
      <p className="pd-note">Total {formatCount(model.total || 0)} PO unik · {scopeNote}</p>
      {model.scope === "semua" && model.approval ? (
        <p className="pd-note">Design menunggu {formatCount(model.approval.design_waiting || 0)}. Payment menunggu {formatCount(model.approval.payment_waiting || 0)}. Rincian ini tidak ditambahkan ke total.</p>
      ) : null}
      <p className="pd-note">{model.perLine}</p>
    </div>
  );
}

function OperatorOutput({ model, controls }) {
  const rows = model.rows || [];
  const max = Math.max(...rows.map((row) => Number(row.qty) || 0), 1);
  return (
    <div className="pd-chart">
      <PeriodSwitch days={controls.days} onChange={controls.setDays} prefix="produksi-operator-days" />
      {(model.units || []).length > 1 ? (
        <div className="pd-switch">
          {model.units.map((unit) => (
            <button type="button" key={unit} className={`kb-btn${model.unit === unit ? " is-on" : ""}`} data-testid={`produksi-operator-unit-${unit}`} onClick={() => controls.setOperatorUnit(unit)}>{unit}</button>
          ))}
        </div>
      ) : null}
      <p className="pd-note" data-testid="produksi-operator-target-note">{model.note}</p>
      {rows.length ? (
        <div className="pd-rank">
          {rows.map((row, index) => (
            <div key={row.operator_id || row.name} className="pd-rank-row" title={`${row.name}: ${formatCount(row.qty)} ${model.unit}`}>
              <i className="pd-swatch" data-tone={String(index % 5)} />
              <span className="pd-rank-name">{index + 1}. {row.name}</span>
              <span className="pd-track" data-tone={String(index % 5)}><span style={{ width: `${Math.max(4, (Number(row.qty) / max) * 100)}%` }} /></span>
              <strong>{formatCount(row.qty)} {model.unit}</strong>
              {row.target_awal != null ? (
                <span className="pd-note" data-testid="produksi-operator-target">
                  Awal {formatCount(row.target_awal)}
                  {row.pencapaian_awal != null ? ` · ${row.pencapaian_awal}%` : ""}
                  {row.target_revisi != null ? ` · Revisi ${formatCount(row.target_revisi)}` : " · Belum ada revisi"}
                  {row.pencapaian_revisi != null ? ` · ${row.pencapaian_revisi}%` : ""}
                </span>
              ) : null}
            </div>
          ))}
        </div>
      ) : <Quiet status={model.status === "incomplete" ? "incomplete" : "zero"} note={model.note} />}
    </div>
  );
}

function WorkloadBody({ model }) {
  const rows = model.rows || [];
  if (!rows.length) return <Quiet status={model.status === "incomplete" ? "incomplete" : "zero"} note={model.note || "Tidak ada alokasi aktif."} />;
  return (
    <div className="pd-table-wrap">
      <table className="pd-table">
        <thead>
          <tr>
            <th>Operator</th>
            <th>Sisa</th>
            <th>PO</th>
            <th>Line</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.operator_id || row.name}>
              <td>{row.name}</td>
              <td>{Object.entries(row.remaining_by_unit || {}).map(([unit, qty]) => `${formatCount(qty)} ${unit}`).join(", ") || "—"}</td>
              <td>{formatCount(row.active_po || 0)}</td>
              <td>{formatCount(row.active_lines || 0)}{row.lines_without_index ? ` · ${row.lines_without_index} tanpa line` : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {model.note ? <p className="pd-note">{model.note}</p> : null}
    </div>
  );
}

function Meter({ value, total }) {
  if (!total) return null;
  const pct = Math.max(0, Math.min(100, (value / total) * 100));
  return (
    <div className="pd-track" data-tone="1" title={`${formatCount(value)} dari ${formatCount(total)} PO`}>
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

function KesiapanBody({ model }) {
  if (model.status === "zero") {
    return (
      <Quiet
        status="zero"
        title="Tidak ada PO aktif"
        note="Snapshot ini tidak punya PO aktif. Angka nol tidak ditampilkan."
      />
    );
  }
  const siap = model.siap;
  const cetak = model.cetak;
  if (model.status !== "complete" || !siap || !cetak) return <Quiet status="incomplete" />;
  const siapTotal = Number(siap.ready || 0) + Number(siap.not_ready || 0);
  const cetakTotal = Number(cetak.complete || 0) + Number(cetak.partial || 0) + Number(cetak.none || 0);
  return (
    <div className="pd-kesiapan" data-testid="produksi-kesiapan">
      <section
        className="pd-kesiapan-panel"
        title="Siap Produksi: Approval Design dan Approval Payment selesai secara sah, termasuk bypass resmi. Bukan izin menjadwalkan, termasuk untuk PO HOLD."
      >
        <h3>Siap Produksi</h3>
        <div className="pd-kesiapan-metrics">
          <div className="pd-metric is-ready" title="Kedua approval selesai. Bukan izin menjadwalkan.">
            <span>Siap</span>
            <strong>{formatCount(siap.ready)}</strong>
          </div>
          <div className="pd-metric is-wait" title="Masih menunggu salah satu atau kedua approval.">
            <span>Belum siap</span>
            <strong>{formatCount(siap.not_ready)}</strong>
          </div>
        </div>
        <Meter value={Number(siap.ready || 0)} total={siapTotal} />
        {siap.hold_ready ? (
          <p className="pd-hold-note" data-testid="produksi-kesiapan-hold" title="HOLD tidak menghapus approval yang sudah selesai, dan bukan izin menjadwalkan.">
            {siap.note}
          </p>
        ) : null}
      </section>
      <section
        className="pd-kesiapan-panel"
        title="Hasil Cetak Tersedia: qty cetak yang dikonfirmasi operator pada setiap line cetak. Bukan barang siap diberangkatkan, packing, surat jalan, atau pengiriman."
      >
        <h3>Hasil Cetak Tersedia</h3>
        <div className="pd-kesiapan-metrics">
          <div className="pd-metric is-ready" title="Semua line cetak wajib sudah mencapai qty-nya.">
            <span>Selesai</span>
            <strong>{formatCount(cetak.complete)}</strong>
          </div>
          <div className="pd-metric is-wait" title="Sebagian line atau qty cetak sudah dikonfirmasi.">
            <span>Sebagian</span>
            <strong>{formatCount(cetak.partial)}</strong>
          </div>
          <div className="pd-metric is-wait" title="Belum ada qty cetak yang dikonfirmasi.">
            <span>Belum</span>
            <strong>{formatCount(cetak.none)}</strong>
          </div>
        </div>
        <Meter value={Number(cetak.complete || 0)} total={cetakTotal} />
        {cetak.not_applicable ? <p className="pd-note" data-testid="produksi-kesiapan-na">{cetak.note}</p> : null}
      </section>
    </div>
  );
}

function CountPair({ label, value, title, tone, text }) {
  return (
    <div className={`pd-metric${tone ? ` is-${tone}` : ""}`} title={title}>
      <span>{label}</span>
      <strong className={text ? "is-text" : undefined}>{text || formatCount(value)}</strong>
    </div>
  );
}

function knownNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function cohortScale(row) {
  return Math.max(0, ...[row.awal, row.revisi, row.realisasi].filter(knownNumber));
}

function CompareRow({ label, value, tone, empty, scale }) {
  const known = knownNumber(value);
  const width = known && scale > 0 && value > 0 ? Math.min(100, (value / scale) * 100) : 0;
  return (
    <div className="pd-compare-row">
      <span className="pd-compare-label">{label}</span>
      <span className="pd-compare-track" aria-hidden="true">
        {known ? <i className={`pd-compare-bar is-${tone}`} style={{ width: `${width}%` }} /> : null}
      </span>
      <strong>{known ? formatCount(value) : empty}</strong>
    </div>
  );
}

function realisasiShare(row) {
  const parts = [];
  if (knownNumber(row.persen_awal) && knownNumber(row.awal) && row.awal > 0) parts.push(`${row.persen_awal}% dari awal`);
  if (knownNumber(row.persen_revisi) && knownNumber(row.revisi) && row.revisi > 0) parts.push(`${row.persen_revisi}% dari revisi`);
  return parts.join(" · ");
}

function CohortBars({ row }) {
  const scale = cohortScale(row);
  const share = realisasiShare(row);
  return (
    <div className="pd-compare" data-testid={`produksi-realisasi-day-${row.day}-${row.unit}`}>
      <p className="pd-compare-day">{row.day} · {row.unit}</p>
      <CompareRow label="Target Awal" value={row.awal} tone="awal" empty="Tidak tersedia" scale={scale} />
      <CompareRow label="Target Revisi" value={row.revisi} tone="revisi" empty="Belum ada revisi" scale={scale} />
      <CompareRow label="Realisasi" value={row.realisasi} tone="realisasi" empty="Belum ada bukti" scale={scale} />
      {share ? <p className="pd-compare-share">{share}</p> : null}
    </div>
  );
}

function CohortBody({ model, testId }) {
  if (model.status === "zero" || model.status === "unlocked") {
    return <Quiet status="zero" title={model.status === "unlocked" ? "Target belum dikunci" : "Tidak ada PO aktif"} note={model.note || "Angka tidak dibuat dari jadwal yang masih draft."} />;
  }
  const series = model.series || [];
  if (model.status !== "complete" || !series.length) return <Quiet status="incomplete" note={model.note} />;
  return (
    <div className="pd-table-wrap" data-testid={testId}>
      <table className="pd-table">
        <thead>
          <tr>
            <th>Tanggal</th>
            <th>Awal</th>
            <th>Revisi</th>
            <th>Realisasi</th>
          </tr>
        </thead>
        <tbody>
          {series.slice(0, 6).map((row) => (
            <tr key={`${row.day}-${row.unit}`} title="Realisasi mengikuti tanggal jadwal. Output kalender memakai tanggal konfirmasi.">
              <td>{row.day} · {row.unit}</td>
              <td>{formatCount(row.awal)}{row.persen_awal != null ? ` · ${row.persen_awal}%` : ""}</td>
              <td>{row.revisi == null ? "Belum ada revisi" : formatCount(row.revisi)}</td>
              <td>{formatCount(row.realisasi)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {model.note ? <p className="pd-note">{model.note}</p> : null}
    </div>
  );
}

function RealisasiBody({ model }) {
  if (model.status === "zero" || model.status === "unlocked") {
    return <Quiet status="zero" title={model.status === "unlocked" ? "Target belum dikunci" : "Tidak ada PO aktif"} note={model.note || "Angka tidak dibuat dari jadwal yang masih draft."} />;
  }
  const series = model.series || [];
  if (model.status !== "complete" || !series.length) return <Quiet status="incomplete" note={model.note} />;
  const groups = [];
  series.forEach((row) => {
    const unit = row.unit || "tanpa satuan";
    const group = groups.find((item) => item.unit === unit);
    if (group) group.rows.push(row);
    else groups.push({ unit, rows: [row] });
  });
  return (
    <div className="pd-realisasi" data-testid="produksi-realisasi">
      {groups.map((group) => {
        const only = group.rows.length === 1 ? group.rows[0] : null;
        return (
          <section key={group.unit} data-testid={`produksi-realisasi-unit-${group.unit}`}>
            {only ? (
              <div className="pd-kesiapan-metrics pd-compare-kpis">
                <CountPair label="Target Awal" value={only.awal} title="Target yang dikunci saat jadwal difinalisasi." />
                <CountPair label="Target Revisi" value={knownNumber(only.revisi) ? only.revisi : undefined} text={knownNumber(only.revisi) ? "" : "Belum ada revisi"} title="Revisi tanggal atau qty setelah finalisasi." />
                <CountPair label="Realisasi" value={knownNumber(only.realisasi) ? only.realisasi : undefined} text={knownNumber(only.realisasi) ? "" : "Belum ada bukti"} title="Qty terkonfirmasi pada tanggal jadwal. Nol berarti output nol, bukan bukti yang hilang." />
              </div>
            ) : (
              <p className="pd-compare-unit">{group.unit}</p>
            )}
            {group.rows.map((row) => <CohortBars key={`${row.day}-${row.unit}`} row={row} />)}
          </section>
        );
      })}
      {model.note ? <p className="pd-note">{model.note}</p> : null}
    </div>
  );
}

function LateBody({ model }) {
  if (model.status === "empty" || model.status === "zero") {
    return <Quiet status="zero" title="Tidak ada PO terlambat" note={model.note || "PO aktif melewati tanggal pengiriman."} />;
  }
  if (model.status !== "complete") return <Quiet status={model.status === "incomplete" ? "incomplete" : model.status} />;
  const counts = model.counts || {};
  return (
    <div className="pd-late" data-testid="produksi-terlambat">
      <div className="pd-late-total">
        <p className="kb-metric" data-testid="produksi-terlambat-total">{formatCount(model.overdue)}</p>
        <p className="kb-kicker">PO terlambat aktif</p>
      </div>
      <div className="pd-late-cats" aria-label="Rincian kategori terlambat">
        <CountPair label="Lewat 1 Hari" value={counts.lewat_1_hari || 0} title="Terlambat tepat 1 hari kalender WIB." />
        <CountPair label="Terlambat" value={counts.terlambat || 0} title="Terlambat 2–3 hari kalender WIB." />
        <CountPair label="Kritis" value={counts.kritis || 0} title="Terlambat ≥ 4 hari kalender WIB." />
      </div>
    </div>
  );
}

function AgingPanel({ title, caption, bucket, doneTitle, testId }) {
  return (
    <section className="pd-aging-panel" data-testid={testId} title="Urgent dihitung sejak PO masuk. Normal dan Buffer Stock dihitung setelah Design dan Payment selesai.">
      <h3>{title}</h3>
      <p className="pd-note">{caption}</p>
      <div className="pd-kesiapan-metrics">
        <CountPair label="Aktif" value={bucket.aktif || 0} title="Awal hitung sudah ada dan pekerjaan ini belum selesai." />
        <CountPair label="Selesai" value={bucket.selesai || 0} tone="ready" title={doneTitle} />
        <CountPair label="Belum dapat dihitung" value={bucket.belum || 0} title="Urgent tanpa waktu PO masuk, atau Normal dan Buffer Stock sebelum kedua approval selesai." />
        <CountPair label="Histori tidak lengkap" value={bucket.histori || 0} title="Pekerjaan sudah selesai, tetapi cap waktu akhirnya tidak tersimpan. Durasi tidak dihitung." />
      </div>
    </section>
  );
}

function AgingBody({ model }) {
  if (model.status === "zero") return <Quiet status="zero" title="Tidak ada PO aktif" />;
  if (model.status !== "complete" || !model.produksi || !model.total) return <Quiet status="incomplete" />;
  return (
    <div className="pd-aging" data-testid="produksi-aging">
      <AgingPanel
        testId="produksi-aging-produksi"
        title="Aging Produksi"
        caption="Berhenti setelah produksi termasuk packing selesai."
        bucket={model.produksi}
        doneTitle="Produksi termasuk packing sudah selesai dan waktu akhirnya tersimpan."
      />
      <AgingPanel
        testId="produksi-aging-total"
        title="Aging Total PO"
        caption="Berhenti setelah seluruh qty wajib terkirim."
        bucket={model.total}
        doneTitle="Seluruh qty wajib sudah terkirim dan waktu kirim tersimpan."
      />
      {model.note ? <p className="pd-note">{model.note}</p> : null}
    </div>
  );
}

function AccuracyBody({ model }) {
  if (model.status === "zero") return <Quiet status="zero" title="Tidak ada PO aktif" />;
  if (model.status !== "complete" || !model.produksi || !model.pengiriman) return <Quiet status="incomplete" />;
  const cell = (bucket, key) => formatCount(bucket[key] || 0);
  return (
    <div className="pd-table-wrap" data-testid="produksi-ketepatan">
      <table className="pd-table">
        <thead>
          <tr><th></th><th>Tepat</th><th>Terlambat</th><th>Berjalan</th><th>Tidak tersedia</th></tr>
        </thead>
        <tbody>
          <tr title="Target adalah tanggal selesai produksi yang dikunci Kepala Produksi."><td>Produksi</td><td>{cell(model.produksi, "tepat")}</td><td>{cell(model.produksi, "terlambat")}</td><td>{cell(model.produksi, "berjalan")}</td><td>{cell(model.produksi, "tidak_tersedia")}</td></tr>
          <tr title="Selesai hanya saat qty wajib terkirim penuh."><td>Pengiriman</td><td>{cell(model.pengiriman, "tepat")}</td><td>{cell(model.pengiriman, "terlambat")}</td><td>{cell(model.pengiriman, "berjalan")}</td><td>{cell(model.pengiriman, "tidak_tersedia")}</td></tr>
        </tbody>
      </table>
      <p className="pd-note">{model.note}</p>
    </div>
  );
}

function BottleneckBody({ model }) {
  if (model.status === "zero") return <Quiet status="zero" title="Tidak ada PO aktif" />;
  if (model.status !== "complete") return <Quiet status="incomplete" />;
  const units = Object.entries(model.by_unit || {});
  const rows = Array.isArray(model.rows) ? model.rows : [];
  return (
    <div data-testid="produksi-bottleneck">
      <div className="pd-kesiapan-metrics">
        <CountPair label="PO Bottleneck" value={model.po} tone={model.po > 0 ? "wait" : undefined} title="PO unik yang melewati target selesai produksi. HOLD dan menunggu approval tidak dihitung." />
        <CountPair label="Line Bottleneck" value={model.lines} tone={model.lines > 0 ? "wait" : undefined} title="Line cetak yang belum selesai. Dihitung terpisah dari jumlah PO." />
        <CountPair label="Pengiriman belum selesai" value={model.pengiriman_belum_selesai} tone="ship" title="Produksi termasuk packing sudah selesai, qty wajib belum terkirim. Ini bukan bottleneck produksi." />
      </div>
      {units.map(([unit, qty]) => <p key={unit} className="pd-note" data-testid={`produksi-bottleneck-sisa-${unit}`}>Sisa {formatCount(qty)} {unit}</p>)}
      {rows.slice(0, 4).map((row) => {
        const sisa = Object.entries(row.by_unit || {}).map(([unit, qty]) => `${formatCount(qty)} ${unit}`).join(", ");
        return (
          <p key={row.order_id || row.po_number} className="pd-note" data-testid="produksi-bottleneck-row">
            {row.po_number || "PO"}{row.target ? ` · target ${row.target}` : ""}{sisa ? ` · sisa ${sisa}` : ""}
          </p>
        );
      })}
      <p className="pd-note">Bukan otomatis kesalahan operator.{model.note ? ` ${model.note}` : ""}</p>
    </div>
  );
}

const SLICE_COLORS = ["#3b82f6", "#10b981", "#8b5cf6", "#f59e0b", "#ef4444", "#64748b"];

function sharePct(qty, total) {
  const base = Number(total);
  const part = Number(qty);
  if (!Number.isFinite(base) || base <= 0 || !Number.isFinite(part)) return null;
  return Math.round((part / base) * 1000) / 10;
}

function CompositionBody({ model, controls }) {
  const [mode, setMode] = useState("kategori");
  const units = model.units || [];
  const unitKey = units.join("|");
  const [unit, setUnit] = useState(units[0] || "");
  useEffect(() => {
    const list = unitKey ? unitKey.split("|") : [];
    if (!list.length) return;
    if (!list.includes(unit)) setUnit(list[0]);
  }, [unitKey, unit]);
  const chosen = unit || units[0] || "";
  const category = (model.categories || {})[chosen] || null;
  const products = ((model.products || {})[chosen]) || [];
  const unverified = (model.unverified_by_unit || {})[chosen];
  const unmapped = (model.unmapped_by_unit || {})[chosen];
  const productTotal = products.reduce((sum, row) => sum + Number(row.qty || 0), 0);
  const categoryRows = category ? [...(category.slices || [])] : [];
  if (category && Number(category.uncategorized_qty) > 0) {
    categoryRows.push({ key: "uncategorized", name: "Belum Dikategorikan", qty: category.uncategorized_qty });
  }
  const categoryTotal = categoryRows.reduce((sum, row) => sum + Number(row.qty || 0), 0);
  const showDonut = mode === "kategori" && category && category.status !== "incomplete" && categoryRows.length > 0;
  const slices = showDonut ? categoryRows.map((row, index) => ({
    name: row.name,
    qty: row.qty,
    pct: sharePct(row.qty, categoryTotal) || 0,
    color: SLICE_COLORS[index % SLICE_COLORS.length],
  })) : [];
  return (
    <div data-testid="produksi-komposisi">
      <div className="pd-switch">
        <button type="button" className={`kb-btn${mode === "kategori" ? " is-on" : ""}`} data-testid="produksi-komposisi-kategori" onClick={() => setMode("kategori")}>Kategori</button>
        <button type="button" className={`kb-btn${mode === "produk" ? " is-on" : ""}`} data-testid="produksi-komposisi-produk" onClick={() => setMode("produk")}>Produk spesifik</button>
        <PeriodSwitch days={controls.days || 7} onChange={controls.setDays || (() => {})} prefix="produksi-komposisi-periode" />
      </div>
      {units.length > 1 ? (
        <div className="pd-switch">
          {units.map((item) => (
            <button type="button" key={item} className={`kb-btn${item === chosen ? " is-on" : ""}`} onClick={() => setUnit(item)}>{item}</button>
          ))}
        </div>
      ) : null}
      {mode === "kategori" && !showDonut ? (
        <p className="pd-note" data-testid="produksi-komposisi-histori">Histori tidak lengkap. Kategori master saat ini tidak dipakai untuk event lama.</p>
      ) : null}
      {showDonut ? <Donut model={{ total: categoryTotal, unit: chosen, slices }} /> : null}
      {mode === "kategori" && category && Number(category.excluded_historical_qty) > 0 ? (
        <p className="pd-note" data-testid="produksi-komposisi-excluded">Histori tanpa snapshot kategori: {formatCount(category.excluded_historical_qty)} {chosen}. Tidak masuk donut.</p>
      ) : null}
      {mode === "produk" ? (
        <div data-testid="produksi-komposisi-ranking">
          {products.length ? (
            <UnitBars
              entries={products.map((row) => ({
                name: row.name,
                qty: row.qty,
                suffix: sharePct(row.qty, productTotal) == null ? ` ${chosen}` : ` ${chosen} · ${sharePct(row.qty, productTotal)}%`,
              }))}
              note={unverified || unmapped ? "Bagian di bawah ini tidak masuk ranking produk." : "Kontribusi dihitung di dalam satuan yang sama."}
            />
          ) : <p className="pd-note">Belum ada produk dengan identitas snapshot pada periode ini.</p>}
          {unmapped ? <p className="pd-note" data-testid="produksi-komposisi-unmapped">Belum terpetakan {formatCount(unmapped)} {chosen}</p> : null}
          {unverified ? <p className="pd-note" data-testid="produksi-komposisi-unverified">Identitas historis tidak terverifikasi {formatCount(unverified)} {chosen}</p> : null}
        </div>
      ) : null}
      {model.note ? <p className="pd-note">{model.note}</p> : null}
    </div>
  );
}

function BranchBody({ model, controls }) {
  const [mode, setMode] = useState("actual");
  const units = model.units || [];
  const unitKey = units.join("|");
  const [unit, setUnit] = useState(units[0] || "");
  useEffect(() => {
    const list = unitKey ? unitKey.split("|") : [];
    if (!list.length) return;
    if (!list.includes(unit)) setUnit(list[0]);
  }, [unitKey, unit]);
  const chosen = unit || units[0] || "";
  const rows = ((mode === "origin" ? model.origin : model.actual) || {})[chosen] || [];
  const total = (model.company_total || {})[chosen];
  return (
    <div data-testid="produksi-output-cabang">
      <div className="pd-switch">
        <button type="button" className={`kb-btn${mode === "actual" ? " is-on" : ""}`} data-testid="produksi-cabang-aktual" onClick={() => setMode("actual")}>Lokasi aktual</button>
        <button type="button" className={`kb-btn${mode === "origin" ? " is-on" : ""}`} data-testid="produksi-cabang-asal" onClick={() => setMode("origin")}>Cabang asal PO</button>
        <PeriodSwitch days={controls.days || 7} onChange={controls.setDays || (() => {})} prefix="produksi-cabang-periode" />
      </div>
      {units.length > 1 ? (
        <div className="pd-switch">
          {units.map((item) => (
            <button type="button" key={item} className={`kb-btn${item === chosen ? " is-on" : ""}`} onClick={() => setUnit(item)}>{item}</button>
          ))}
        </div>
      ) : null}
      {rows.length ? (
        <UnitBars
          entries={rows.map((row) => ({
            name: row.name,
            qty: row.qty,
            suffix: ` ${chosen}`,
          }))}
          note={mode === "actual"
            ? "Lokasi mengikuti snapshot penjadwalan pada saat konfirmasi."
            : "Cabang asal PO. Bukan lokasi pengerjaan."}
        />
      ) : <p className="pd-note">Tidak ada output terkonfirmasi pada periode ini.</p>}
      {rows.some((row) => row.name === "Belum Teratribusi") ? (
        <p className="pd-note" data-testid="produksi-cabang-unattributed">Belum Teratribusi tetap dihitung di total perusahaan.</p>
      ) : null}
      {total != null ? <p className="pd-note" data-testid="produksi-cabang-total">Total perusahaan {formatCount(total)} {chosen}</p> : null}
    </div>
  );
}

function CardBody({ id, model, tone, onRetry, controls, editing, hasPerm }) {
  const status = model?.status || "loading";
  if (status === "loading" || status === "error" || status === "forbidden") return <Quiet status={status} onRetry={onRetry} />;
  if (id === "output_hari_ini") return <OutputToday model={model} />;
  if (id === "delivery_qty") {
    return <DeliveryQtyBody model={model} controls={controls} editing={editing} hasPerm={hasPerm} />;
  }
  if (id === "tren_output") return <TrendChart model={model} controls={controls} />;
  if (id === "distribusi_tahap") return <WorkflowBars model={model} controls={controls} />;
  if (id === "output_operator") return <OperatorOutput model={model} controls={controls} />;
  if (id === "beban_operator") return <WorkloadBody model={model} />;
  if (id === "kesiapan") return <KesiapanBody model={model} />;
  if (id === "target_produksi") return <CohortBody model={model} testId="produksi-target" />;
  if (id === "target_realisasi") return <RealisasiBody model={model} />;
  if (id === "po_terlambat") return <LateBody model={model} />;
  if (id === "aging") return <AgingBody model={model} />;
  if (id === "ketepatan") return <AccuracyBody model={model} />;
  if (id === "bottleneck") return <BottleneckBody model={model} />;
  if (id === "komposisi") return <CompositionBody model={model} controls={controls} />;
  if (id === "output_cabang") return <BranchBody model={model} controls={controls} />;
  if (QUIET.has(status)) return <Quiet status={status} onRetry={onRetry} />;
  if (id === "sisa_qty" && model.presentation === "simulated-products") {
    return (
      <UnitBars
        entries={(model.rows || []).map((row) => ({ name: row.name, qty: row.qty, suffix: ` ${model.unit}` }))}
        note="SIMULASI. Semua baris fixture ini memakai pcs. Bukan sisa operasional."
      />
    );
  }
  if (id === "sisa_qty") {
    const units = Object.entries(model.byUnit || {});
    if (!units.length) {
      return (
        <div className="pd-kpi">
          <div>
            <p className="kb-metric">0</p>
            <p className="kb-kicker">Tidak ada sisa produksi</p>
          </div>
        </div>
      );
    }
    return (
      <UnitBars
        entries={units.map(([unit, qty]) => ({ name: unit, qty }))}
        note="Panjang bar dibanding nilai terbesar di kartu ini. Satuan tidak digabung."
      />
    );
  }
  if (id === "deadline") {
    return (
      <div className="pd-stack">
        <div className="pd-deadline">
          <DrillPanelLink
            className="pd-status is-critical"
            drill={model.drill6h}
            hasPerm={hasPerm}
            editing={editing}
            label="Buka daftar deadline ≤ 6 jam"
            testId="produksi-drill-deadline-6h"
          >
            <span>Kritis</span>
            <strong>{formatCount(model.within6h)}</strong>
            <em>≤ 6 jam</em>
          </DrillPanelLink>
          <DrillPanelLink
            className="pd-status is-soon"
            drill={model.drill24h}
            hasPerm={hasPerm}
            editing={editing}
            label="Buka daftar deadline ≤ 24 jam"
            testId="produksi-drill-deadline-24h"
          >
            <span>Mendekati</span>
            <strong>{formatCount(model.within24h)}</strong>
            <em>≤ 24 jam</em>
          </DrillPanelLink>
        </div>
        <p className="pd-note">≤ 24 jam sudah mencakup ≤ 6 jam. Dua angka ini bukan total unik.</p>
      </div>
    );
  }
  if (id === "approval") {
    return (
      <div className="pd-approval">
        <DrillPanelLink
          className="pd-panel is-design"
          drill={model.drillDesign}
          hasPerm={hasPerm}
          editing={editing}
          label="Buka daftar menunggu approval design"
          testId="produksi-drill-approval-design"
        >
          <span>Design</span>
          <strong>{formatCount(model.design)}</strong>
          <p>Menunggu desain</p>
        </DrillPanelLink>
        <DrillPanelLink
          className="pd-panel is-payment"
          drill={model.drillPayment}
          hasPerm={hasPerm}
          editing={editing}
          label="Buka daftar menunggu approval payment"
          testId="produksi-drill-approval-payment"
        >
          <span>Payment</span>
          <strong>{formatCount(model.payment)}</strong>
          <p>Menunggu pembayaran</p>
        </DrillPanelLink>
      </div>
    );
  }
  if (id === "prioritas") {
    const rows = model.rows || [];
    if (!rows.length) return <Quiet status="empty" />;
    return (
      <div className="pd-table-wrap">
        <table className="pd-table">
          <thead>
            <tr>
              <th>No. PO</th>
              <th>Line</th>
              <th>Produk</th>
              <th>Qty</th>
              <th>Status</th>
              <th>Deadline</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const parts = splitLabels(row.labels);
              return (
                <tr key={`${row.order_id}:${row.line_idx}`}>
                  <td className="pd-po">{row.po_number}</td>
                  <td>{row.line_idx}</td>
                  <td>{row.item}</td>
                  <td className="pd-qty">{formatCount(row.qty_production_remaining)} {row.unit}</td>
                  <td>{parts.status.map((label) => <span key={label} className={badgeClass(label)}>{label}</span>)}</td>
                  <td>{parts.deadline.length ? parts.deadline.map((item) => <span key={item.key} className={`pd-badge is-${item.key === "today" ? "soon" : item.key}`}>{item.text}</span>) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }
  if (model.kind === "bars") return <StackedBars model={model} />;
  if (model.kind === "donut") return <Donut model={model} />;
  if (model.kind === "rank") {
    return (
      <UnitBars
        entries={(model.rows || []).map((row) => ({ name: row.name, qty: row.qty, suffix: ` ${model.unit}`, color: row.color }))}
        note="SIMULASI. Angka fixture tidak berasal dari analytics operasional."
      />
    );
  }
  return <CountBody model={model} kicker={CAPTION[id]} tone={tone} />;
}

export function WidgetGlyph({ id }) {
  const Icon = GLYPH[id] || Layers;
  return (
    <span className={`kb-glyph pd-glyph-${TONE[id] || "info"}`}>
      <Icon size={16} strokeWidth={2} aria-hidden="true" />
    </span>
  );
}

const CARD_DRILL_IDS = new Set([
  "po_aktif",
  "perlu_produksi",
  "sisa_qty",
  "prioritas",
  "po_urgent",
  "po_hold",
  "po_terlambat",
]);

export default function ProductionWidgetBoard({ widgets, models, editing, hasPerm, onLayout, onRetry, controls = {} }) {
  const navigate = useNavigate();
  const gridRef = useRef(null);
  const dragRef = useRef(null);
  const [hint, setHint] = useState("");
  const [infoFor, setInfoFor] = useState("");
  const [menuFor, setMenuFor] = useState("");

  useEffect(() => () => {
    if (!dragRef.current) return;
    window.removeEventListener("pointermove", dragRef.current.move);
    window.removeEventListener("pointerup", dragRef.current.up);
  }, []);

  function unitSize() {
    const width = gridRef.current?.clientWidth || 1;
    const col = (width - GAP * (COLUMNS - 1)) / COLUMNS;
    return { col: col + GAP, row: ROW };
  }

  function beginPointer(event, widget, mode) {
    if (!editing || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const start = { x: event.clientX, y: event.clientY, widget: { ...widget } };
    const move = (ev) => {
      const { col, row } = unitSize();
      const dx = Math.round((ev.clientX - start.x) / col);
      const dy = Math.round((ev.clientY - start.y) / row);
      const box = mode === "resize"
        ? { x: start.widget.x, y: start.widget.y, w: start.widget.w + dx, h: start.widget.h + dy }
        : { x: start.widget.x + dx, y: start.widget.y + dy, w: start.widget.w, h: start.widget.h };
      setHint(`${mode === "resize" ? "Ukuran" : "Posisi"} ${box.w}×${box.h} · kolom ${box.x + 1} · baris ${box.y + 1}`);
      onLayout(placeWidget(widgets, widget.widget_id, box));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      dragRef.current = null;
      setHint("");
    };
    dragRef.current = { move, up };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function nudge(widget, dx, dy, dw, dh) {
    onLayout(placeWidget(widgets, widget.widget_id, {
      x: widget.x + dx,
      y: widget.y + dy,
      w: widget.w + dw,
      h: widget.h + dh,
    }));
  }

  return (
    <div
      className={`kb-grid${editing ? " is-editing" : ""}`}
      ref={gridRef}
      style={{ height: Math.max(widgets.reduce((max, item) => Math.max(max, item.y + item.h), 1) * ROW, ROW) }}
      data-testid="produksi-grid"
    >
      {hint ? <p className="kb-ghost" data-testid="produksi-grid-hint">{hint}</p> : null}
      {widgets.map((widget) => {
        const meta = productionWidgetMeta(widget.widget_id);
        const simulated = Boolean(models[widget.widget_id]?.spark || models[widget.widget_id]?.kind || models[widget.widget_id]?.presentation || TITLE[widget.widget_id]);
        const allowed = simulated || productionWidgetAllowed(widget.widget_id, hasPerm);
        const model = allowed ? (models[widget.widget_id] || { status: "loading" }) : { status: "forbidden" };
        const title = widget.settings?.title || meta?.title || TITLE[widget.widget_id] || widget.widget_id;
        const cardDrillPath = (
          !editing
          && CARD_DRILL_IDS.has(widget.widget_id)
          && (model.status === "complete" || model.status === "zero" || model.status === "empty")
          && canOpenDrill(model.drill, hasPerm)
        ) ? model.drill.path : null;
        const cardDrillLocked = (
          !editing
          && CARD_DRILL_IDS.has(widget.widget_id)
          && model.drill?.path
          && !canOpenDrill(model.drill, hasPerm)
        );
        return (
          <article
            key={widget.widget_id}
            className={[
              "kb-card",
              TONE[widget.widget_id] ? `pd-tone-${TONE[widget.widget_id]}` : "",
              widget.h <= 2 ? "is-compact" : "",
              editing ? "is-edit" : "",
              menuFor === widget.widget_id ? "is-menu" : "",
              widget.widget_id === "delivery_qty" ? "is-delivery-qty" : "",
              widget.widget_id === "delivery_qty" && widget.h < 6 ? "is-delivery-tight" : "",
              cardDrillPath ? "is-drillable" : "",
              cardDrillLocked ? "is-drill-locked" : "",
            ].filter(Boolean).join(" ")}
            data-testid={`produksi-widget-${widget.widget_id}`}
            data-status={model.status}
            data-drill={cardDrillPath ? "ready" : (cardDrillLocked ? "locked" : undefined)}
            style={{
              left: `calc((100% - ${GAP * (COLUMNS - 1)}px) * ${widget.x / COLUMNS} + ${widget.x * GAP}px)`,
              width: `calc((100% - ${GAP * (COLUMNS - 1)}px) * ${widget.w / COLUMNS} + ${(widget.w - 1) * GAP}px)`,
              top: widget.y * ROW,
              height: widget.h * ROW - GAP,
            }}
          >
            <header>
              {editing ? (
                <button
                  type="button"
                  className="kb-handle"
                  aria-label={`Geser ${title}`}
                  data-testid={`produksi-drag-${widget.widget_id}`}
                  onPointerDown={(event) => beginPointer(event, widget, "move")}
                >
                  <GripVertical size={14} aria-hidden="true" />
                </button>
              ) : null}
              <WidgetGlyph id={widget.widget_id} />
              <div className="kb-card-title">
                <h2 title={title}>{title}</h2>
                <p>{CAPTION[widget.widget_id] || ""}</p>
              </div>
              {cardDrillPath ? (
                <span className="pd-drill-icon kb-card-drill" aria-hidden="true"><ArrowUpRight size={14} /></span>
              ) : null}
              {!editing && (model.status === "complete" || model.status === "zero" || model.status === "empty") ? (
                <Drill drill={model.drill} hasPerm={hasPerm} label="Rincian" />
              ) : null}
              {cardDrillLocked ? (
                <span className="pd-drill-locked" data-testid={`produksi-drill-locked-${widget.widget_id}`}>Akses terbatas</span>
              ) : null}
              <div className="kb-card-tools">
                <button
                  type="button"
                  className="kb-icon-btn"
                  aria-label={`Menu ${title}`}
                  aria-expanded={menuFor === widget.widget_id}
                  data-testid={`produksi-menu-${widget.widget_id}`}
                  onClick={() => setMenuFor(menuFor === widget.widget_id ? "" : widget.widget_id)}
                >
                  <MoreHorizontal size={16} aria-hidden="true" />
                </button>
                {menuFor === widget.widget_id ? (
                  <div className="kb-pop" role="menu">
                    {widget.widget_id === "delivery_qty" ? (
                      <Link
                        to={DELIVERY_PLAN_PATH}
                        role="menuitem"
                        data-testid="produksi-open-delivery_qty"
                        onClick={() => setMenuFor("")}
                      >
                        <ExternalLink size={14} aria-hidden="true" />
                        Lihat Rencana Pengiriman
                      </Link>
                    ) : null}
                    <button type="button" data-testid={`produksi-info-${widget.widget_id}`} onClick={() => { setInfoFor(infoFor === widget.widget_id ? "" : widget.widget_id); setMenuFor(""); }}>Info Widget</button>
                    <button type="button" data-testid={`produksi-remove-${widget.widget_id}`} onClick={() => onLayout(removeWidget(widgets, widget.widget_id))}>Hapus dari Dashboard</button>
                    {editing ? (
                      <div className="kb-nudge" role="group" aria-label={`Papan ketik ${title}`}>
                        <button type="button" className="kb-icon-btn" aria-label={`Geser ${title} ke kiri`} onClick={() => nudge(widget, -1, 0, 0, 0)}>Kiri</button>
                        <button type="button" className="kb-icon-btn" aria-label={`Geser ${title} ke kanan`} onClick={() => nudge(widget, 1, 0, 0, 0)}>Kanan</button>
                        <button type="button" className="kb-icon-btn" aria-label={`Geser ${title} ke atas`} onClick={() => nudge(widget, 0, -1, 0, 0)}>Atas</button>
                        <button type="button" className="kb-icon-btn" aria-label={`Geser ${title} ke bawah`} onClick={() => nudge(widget, 0, 1, 0, 0)}>Bawah</button>
                        <button type="button" className="kb-icon-btn" aria-label={`Perlebar ${title}`} onClick={() => nudge(widget, 0, 0, 1, 0)}>Lebar</button>
                        <button type="button" className="kb-icon-btn" aria-label={`Persempit ${title}`} onClick={() => nudge(widget, 0, 0, -1, 0)}>Sempit</button>
                        <button type="button" className="kb-icon-btn" aria-label={`Pertinggi ${title}`} onClick={() => nudge(widget, 0, 0, 0, 1)}>Tinggi</button>
                        <button type="button" className="kb-icon-btn" aria-label={`Perpendek ${title}`} onClick={() => nudge(widget, 0, 0, 0, -1)}>Pendek</button>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </header>
            <div
              className="kb-body"
              role={cardDrillPath ? "link" : undefined}
              tabIndex={cardDrillPath ? 0 : undefined}
              aria-label={cardDrillPath ? `Buka daftar ${title}` : undefined}
              data-testid={cardDrillPath ? `produksi-drill-${widget.widget_id}` : undefined}
              onClick={(event) => {
                if (!cardDrillPath || isInteractiveDrillEventTarget(event.target)) return;
                navigate(cardDrillPath);
              }}
              onKeyDown={(event) => {
                if (!cardDrillPath) return;
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  navigate(cardDrillPath);
                }
              }}
            >
              {infoFor === widget.widget_id ? (
                <div className="kb-info" data-testid={`produksi-info-panel-${widget.widget_id}`}>
                  <p>{meta?.summary}</p>
                </div>
              ) : null}
              <CardBody
                id={widget.widget_id}
                model={model}
                tone={TONE[widget.widget_id]}
                onRetry={onRetry}
                controls={controls}
                editing={editing}
                hasPerm={hasPerm}
              />
            </div>
            {editing ? (
              <button
                type="button"
                className="kb-resize"
                aria-label={`Ubah ukuran ${title}`}
                data-testid={`produksi-resize-${widget.widget_id}`}
                onPointerDown={(event) => beginPointer(event, widget, "resize")}
              />
            ) : null}
          </article>
        );
      })}
    </div>
  );
}
