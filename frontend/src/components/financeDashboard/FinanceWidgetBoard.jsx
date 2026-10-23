import { useEffect, useRef, useState } from "react";
import {
  ArrowLeftRight,
  GripVertical,
  Landmark,
  MoreHorizontal,
  Package,
  PieChart,
  Receipt,
  Scale,
  ShoppingBag,
  TrendingUp,
  Users,
} from "lucide-react";
import { formatMoney } from "../../lib/financialDashboard";
import SalesInvoiceDateField from "../SalesInvoiceDateField";
import { financeWidgetAllowed, financeWidgetMeta } from "./financeWidgetCatalog";
import { COLUMNS, ROW_HEIGHT, gridHeight, placeWidget, removeWidget } from "./financeWidgetLayout";

const GAP = 12;
const STATE_LABEL = {
  loading: "Memuat",
  empty: "Belum ada data",
  zero: "Nol tercatat",
  incomplete: "Belum lengkap",
  error: "Gagal memuat",
  forbidden: "Tidak ada izin",
  unavailable: "Belum tersedia",
};
const QUIET = new Set(["loading", "empty", "error", "forbidden", "unavailable", "incomplete"]);
const PRESET_LABEL = { today: "Hari ini", month: "Bulan ini", year: "Tahun ini", custom: "Kustom" };
const SLICE = ["#08784C", "#2563EB", "#D97706", "#7C3AED", "#0F766E", "#64748B", "#DC2626"];
const GLYPH = {
  cash: ArrowLeftRight,
  expense: PieChart,
  profit: Scale,
  sales: ShoppingBag,
  trend: TrendingUp,
  products: Package,
  sellers: Users,
  purchases: Receipt,
  recon: Landmark,
};
const CAPTION = {
  cash: "Kas masuk, keluar, dan neto",
  expense: "Komposisi beban operasional",
  profit: "Komponen laba rugi periode",
  sales: "Omzet faktur finalized",
  trend: "Deret omzet sesuai grain",
  products: "Peringkat dari line_subtotal",
  sellers: "Omzet yang terpetakan",
  purchases: "Nilai faktur pembelian",
  recon: "Dokumen rekonsiliasi",
};

export function WidgetGlyph({ id }) {
  const Icon = GLYPH[id] || PieChart;
  return (
    <span className={`kb-glyph is-${id}`}>
      <Icon size={16} strokeWidth={2} aria-hidden="true" />
    </span>
  );
}

function namedAmount(rows, name) {
  const row = (rows || []).find((item) => item.name === name);
  return row ? row.amount : null;
}

function moneyText(status, amount) {
  if (QUIET.has(status)) return null;
  if (status === "zero") return "Rp 0";
  if (amount == null || !Number.isFinite(Number(amount))) return null;
  return formatMoney(amount);
}

function comparisonLine(comparison) {
  if (!comparison || typeof comparison.percent !== "number" || !Number.isFinite(comparison.percent)) return null;
  const pct = comparison.percent * 100;
  const text = `${pct > 0 ? "+" : ""}${pct.toLocaleString("id-ID", { maximumFractionDigits: 1, minimumFractionDigits: 0 })}% vs periode lalu`;
  return { text, direction: comparison.direction || "" };
}

const EMPTY_NOTE = {
  cash: "Data arus kas belum tersedia pada periode ini.",
  expense: "Belum ada beban operasional pada periode ini.",
  profit: "Belum ada jurnal laba rugi pada periode ini.",
  sales: "Belum ada faktur penjualan pada periode ini.",
  trend: "Belum ada data penjualan pada periode ini.",
  products: "Belum ada data penjualan barang pada periode ini.",
  sellers: "Belum ada data performa penjual pada periode ini.",
  purchases: "Belum ada faktur pembelian pada periode ini.",
  recon: "Belum ada dokumen rekonsiliasi pada periode ini.",
};

function noteFor(id, status) {
  if (status === "error") return "Muat ulang untuk mencoba lagi.";
  if (status === "forbidden") return "Kartu ini tidak termasuk akses Anda.";
  if (status === "incomplete") return "Angka tidak ditampilkan sebelum data lengkap.";
  return EMPTY_NOTE[id] || "Sumber data kartu ini belum siap.";
}

function EmptyMark({ id }) {
  if (id === "expense") {
    return (
      <svg className="kb-illus" viewBox="0 0 48 48" aria-hidden="true">
        <circle cx="24" cy="24" r="14" fill="none" stroke="#dce8e2" strokeWidth="6" />
        <circle cx="24" cy="24" r="6" fill="#fff" />
      </svg>
    );
  }
  if (id === "trend" || id === "sales") {
    return (
      <svg className="kb-illus" viewBox="0 0 48 48" aria-hidden="true">
        <rect x="8" y="26" width="6" height="12" rx="1" fill="#dce8e2" />
        <rect x="18" y="18" width="6" height="20" rx="1" fill="#c5ddd2" />
        <rect x="28" y="12" width="6" height="26" rx="1" fill="#dce8e2" />
      </svg>
    );
  }
  if (id === "products" || id === "sellers") {
    return (
      <svg className="kb-illus" viewBox="0 0 48 48" aria-hidden="true">
        <rect x="10" y="14" width="28" height="4" rx="2" fill="#dce8e2" />
        <rect x="10" y="22" width="22" height="4" rx="2" fill="#c5ddd2" />
        <rect x="10" y="30" width="16" height="4" rx="2" fill="#dce8e2" />
      </svg>
    );
  }
  if (id === "recon") {
    return (
      <svg className="kb-illus" viewBox="0 0 48 48" aria-hidden="true">
        <rect x="14" y="10" width="20" height="28" rx="3" fill="none" stroke="#dce8e2" strokeWidth="2" />
        <path d="M19 20h10M19 26h10M19 32h6" stroke="#c5ddd2" strokeWidth="2" />
      </svg>
    );
  }
  return <span className="kb-empty-mark" aria-hidden="true" />;
}

function Quiet({ id, status, onRetry }) {
  if (status === "loading") {
    return (
      <div className="kb-skel" aria-busy="true" aria-label="Memuat">
        <span />
        <span />
        <span />
      </div>
    );
  }
  return (
    <div className={`kb-empty is-${status}`}>
      <EmptyMark id={id} />
      <strong>{STATE_LABEL[status] || "—"}</strong>
      <p>{noteFor(id, status)}</p>
      {status === "error" && onRetry ? (
        <button type="button" className="kb-btn" onClick={onRetry}>Coba lagi</button>
      ) : null}
    </div>
  );
}

function Spark({ points }) {
  const values = (points || []).map((point) => Number(point.amount)).filter((value) => Number.isFinite(value));
  const max = values.length ? Math.max(...values) : 0;
  if (values.length < 2 || max <= 0) return null;
  return (
    <div className="kb-spark" aria-hidden="true">
      {points.map((point, index) => (
        <i key={`${point.label}-${index}`} style={{ height: `${Math.max(8, (Number(point.amount) / max) * 100)}%` }} />
      ))}
    </div>
  );
}

function SeriesChart({ points, grain }) {
  const rows = (points || []).filter((point) => point.amount != null && Number.isFinite(Number(point.amount)));
  const max = rows.reduce((peak, point) => Math.max(peak, Number(point.amount)), 0);
  if (!rows.length || max <= 0) return null;
  const visible = rows.slice(-31);
  return (
    <div className="kb-chart">
      <div className="kb-bars">
        {visible.map((point, index) => (
          <span key={`${point.label}-${index}`} className="kb-bar" title={`${point.label}: ${formatMoney(point.amount)}`}>
            <i style={{ height: `${Math.max(4, (Number(point.amount) / max) * 100)}%` }} />
          </span>
        ))}
      </div>
      <div className="kb-axis">
        <span>{visible[0].label}</span>
        {grain ? <span>{grain}</span> : null}
        {visible.length > 1 ? <span>{visible[visible.length - 1].label}</span> : null}
      </div>
    </div>
  );
}

function Donut({ rows }) {
  const items = (rows || []).filter((row) => row.amount != null && Number.isFinite(Number(row.amount)) && Number(row.amount) > 0);
  const total = items.reduce((sum, row) => sum + Number(row.amount), 0);
  if (!items.length || total <= 0) return null;
  let cursor = 0;
  const stops = items.map((row, index) => {
    const start = cursor;
    cursor += (Number(row.amount) / total) * 100;
    return `${SLICE[index % SLICE.length]} ${start}% ${cursor}%`;
  });
  return (
    <div className="kb-donut-wrap">
      <div className="kb-donut" style={{ background: `conic-gradient(${stops.join(", ")})` }}>
        <div className="kb-donut-hole">
          <span>Total</span>
          <strong>{formatMoney(total)}</strong>
        </div>
      </div>
      <ul className="kb-legend">
        {items.slice(0, 6).map((row, index) => (
          <li key={row.key || row.name}>
            <i style={{ background: SLICE[index % SLICE.length] }} />
            <span title={row.name}>{row.name}</span>
            <b>{((Number(row.amount) / total) * 100).toLocaleString("id-ID", { maximumFractionDigits: 0 })}%</b>
          </li>
        ))}
      </ul>
    </div>
  );
}

function RankList({ rows, expanded }) {
  const list = expanded ? rows : rows.slice(0, 5);
  const amounts = list.map((row) => Number(row.amount)).filter((value) => Number.isFinite(value) && value > 0);
  const max = amounts.length ? Math.max(...amounts) : 0;
  return list.map((row, index) => {
    const width = row.share != null && Number.isFinite(Number(row.share))
      ? Math.min(100, Math.max(0, Number(row.share)))
      : (max > 0 && row.amount != null ? Math.min(100, (Math.abs(Number(row.amount)) / max) * 100) : 0);
    return (
      <div className="kb-rank" key={row.key || row.name}>
        <span className="kb-rank-no">{index + 1}</span>
        <div>
          <div className="kb-rank-line">
            <span title={row.name}>{row.name}</span>
            <strong>{row.amount == null ? "—" : formatMoney(row.amount)}</strong>
          </div>
          {width > 0 ? <div className="kb-track"><i style={{ width: `${width}%` }} /></div> : null}
        </div>
      </div>
    );
  });
}

function CardBody({ id, model, compact, expanded, onRetry }) {
  const status = model?.status || "loading";
  if (QUIET.has(status)) return <Quiet id={id} status={status} onRetry={onRetry} />;
  if (id === "cash") {
    return (
      <div className="kb-trio">
        <div><span>Kas Masuk</span><strong>{moneyText(status, model?.cashIn) || "—"}</strong></div>
        <div><span>Kas Keluar</span><strong>{moneyText(status, model?.cashOut) || "—"}</strong></div>
        <div><span>Neto</span><strong>{moneyText(status, model?.net) || "—"}</strong></div>
      </div>
    );
  }
  if (id === "sales") {
    const comparison = comparisonLine(model?.comparison);
    return (
      <div className="kb-kpi">
        <p className="kb-metric">{moneyText(status, model?.amount) || "—"}</p>
        {comparison ? <p className={`kb-delta is-${comparison.direction}`}>{comparison.text}</p> : null}
        {status === "zero" ? <p className="kb-kicker">Nol tercatat</p> : null}
        {model?.invoiceCount != null ? <p className="kb-kicker">{model.invoiceCount} faktur</p> : null}
        {compact ? null : <Spark points={model?.series} />}
      </div>
    );
  }
  if (id === "purchases") {
    return (
      <div className="kb-kpi">
        <p className="kb-metric">{moneyText(status, model?.amount) || "—"}</p>
        {status === "zero" ? <p className="kb-kicker">Nol tercatat</p> : null}
        <p className="kb-kicker">Lunas {moneyText(status, model?.settledGrand) || "—"} · Belum {moneyText(status, model?.openOutstanding) || "—"}</p>
      </div>
    );
  }
  if (id === "profit") {
    const net = namedAmount(model?.rows, "Laba bersih");
    const ytd = namedAmount(model?.ytd, "Laba bersih");
    const ytdText = model?.ytdStatus === "complete" || model?.ytdStatus === "zero" ? moneyText(model.ytdStatus, ytd) : null;
    return (
      <div className="kb-kpi">
        <p className="kb-metric">{moneyText(status, net) || "—"}</p>
        <p className="kb-kicker">Laba bersih periode</p>
        {ytdText ? <p className="kb-kicker">YTD {ytdText}</p> : <p className="kb-kicker">YTD {STATE_LABEL[model?.ytdStatus] || "belum tersedia"}</p>}
        {!compact && model?.rows?.length ? (
          <div className="kb-lines">
            {model.rows.filter((row) => row.name !== "Laba bersih").map((row) => (
              <div key={row.name}><span>{row.name}</span><strong>{row.amount == null ? "—" : formatMoney(row.amount)}</strong></div>
            ))}
          </div>
        ) : null}
      </div>
    );
  }
  if (id === "expense") {
    return model?.rows?.length ? <Donut rows={model.rows} /> : <Quiet id="expense" status="empty" />;
  }
  if (id === "trend") {
    return model?.points?.length ? (
      <>
        {model.points.length === 1 ? <p className="kb-kicker">{model.points[0].label}</p> : null}
        <SeriesChart points={model.points} grain={model.grain} />
      </>
    ) : <Quiet id={id} status={status === "complete" ? "empty" : status} />;
  }
  if (id === "products" || id === "sellers") {
    return model?.rows?.length ? <RankList rows={model.rows} expanded={expanded} /> : <Quiet id={id} status={status === "complete" ? "empty" : status} />;
  }
  if (id === "recon") {
    return model?.rows?.length ? (
      <div className="kb-lines">
        {model.rows.slice(0, expanded ? 12 : 5).map((row) => (
          <div key={row.key || row.name}>
            <span title={row.name}>{row.name}</span>
            <strong>{row.statusLabel || "—"}{row.amount == null ? "" : ` · selisih ${formatMoney(row.amount)}`}</strong>
          </div>
        ))}
      </div>
    ) : <Quiet id={id} status={status === "complete" ? "empty" : status} />;
  }
  return <Quiet id={id} status={status} />;
}

export default function FinanceWidgetBoard({ widgets, models, editing, hasPerm, globalPreset, onLayout, onSettings, onRetry }) {
  const gridRef = useRef(null);
  const dragRef = useRef(null);
  const [hint, setHint] = useState("");
  const [infoFor, setInfoFor] = useState("");
  const [menuFor, setMenuFor] = useState("");
  const [settingsFor, setSettingsFor] = useState("");
  const [expanded, setExpanded] = useState({});

  useEffect(() => () => {
    if (!dragRef.current) return;
    window.removeEventListener("pointermove", dragRef.current.move);
    window.removeEventListener("pointerup", dragRef.current.up);
  }, []);

  function unitSize() {
    const width = gridRef.current?.clientWidth || 1;
    const col = (width - GAP * (COLUMNS - 1)) / COLUMNS;
    return { col: col + GAP, row: ROW_HEIGHT };
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
      style={{ height: Math.max(gridHeight(widgets), ROW_HEIGHT) }}
      data-testid="finance-grid"
    >
      {hint ? <p className="kb-ghost" data-testid="finance-grid-hint">{hint}</p> : null}
      {widgets.map((widget) => {
        const meta = financeWidgetMeta(widget.widget_id);
        const allowed = financeWidgetAllowed(widget.widget_id, hasPerm);
        const model = allowed ? (models[widget.widget_id] || { status: "loading" }) : { status: "forbidden", detail: "Tidak ada izin." };
        const title = widget.settings?.title || meta?.title || widget.widget_id;
        const rows = model?.rows || [];
        const canExpand = (widget.widget_id === "products" || widget.widget_id === "sellers" || widget.widget_id === "recon") && rows.length > 5;
        const periodMode = widget.settings?.period_mode || "global";
        const choosePeriod = (period_mode) => {
          onSettings(widget.widget_id, { ...(widget.settings || {}), period_mode });
          setMenuFor("");
          if (period_mode === "custom") setSettingsFor(widget.widget_id);
        };
        return (
          <article
            key={widget.widget_id}
            className={`kb-card${widget.h <= 2 ? " is-compact" : ""}${editing ? " is-edit" : ""}${menuFor === widget.widget_id ? " is-menu" : ""}`}
            data-testid={`finance-widget-${widget.widget_id}`}
            data-status={model.status}
            style={{
              left: `calc((100% - ${GAP * (COLUMNS - 1)}px) * ${widget.x / COLUMNS} + ${widget.x * GAP}px)`,
              width: `calc((100% - ${GAP * (COLUMNS - 1)}px) * ${widget.w / COLUMNS} + ${(widget.w - 1) * GAP}px)`,
              top: widget.y * ROW_HEIGHT,
              height: widget.h * ROW_HEIGHT - GAP,
            }}
          >
            <header>
              {editing ? (
                <button
                  type="button"
                  className="kb-handle"
                  aria-label={`Geser ${title}`}
                  data-testid={`finance-drag-${widget.widget_id}`}
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
              {canExpand ? (
                <button type="button" className="kb-link" onClick={() => setExpanded((current) => ({ ...current, [widget.widget_id]: !current[widget.widget_id] }))}>
                  {expanded[widget.widget_id] ? "Ringkas" : "Lihat Semua"}
                </button>
              ) : null}
              {meta?.period && widget.w > 3 ? (
                <select
                  className="kb-chip"
                  aria-label={`Periode ${title}`}
                  value={widget.settings?.period_mode || "global"}
                  onChange={(event) => choosePeriod(event.target.value)}
                >
                  <option value="global">{PRESET_LABEL[globalPreset] || "Dashboard"}</option>
                  <option value="today">Hari ini</option>
                  <option value="month">Bulan ini</option>
                  <option value="year">Tahun ini</option>
                  <option value="custom">Kustom</option>
                </select>
              ) : null}
              <div className="kb-card-tools">
                <button
                  type="button"
                  className="kb-icon-btn"
                  aria-label={`Menu ${title}`}
                  aria-expanded={menuFor === widget.widget_id}
                  data-testid={`finance-menu-${widget.widget_id}`}
                  onClick={() => setMenuFor(menuFor === widget.widget_id ? "" : widget.widget_id)}
                >
                  <MoreHorizontal size={16} aria-hidden="true" />
                </button>
                {menuFor === widget.widget_id ? (
                  <div className="kb-pop" role="menu">
                    <button type="button" data-testid={`finance-info-${widget.widget_id}`} onClick={() => { setInfoFor(infoFor === widget.widget_id ? "" : widget.widget_id); setMenuFor(""); }}>Info Widget</button>
                    <button type="button" onClick={() => { setSettingsFor(settingsFor === widget.widget_id ? "" : widget.widget_id); setMenuFor(""); }}>Pengaturan</button>
                    {meta?.period && widget.w <= 3 ? (
                      <div className="kb-period-menu" role="group" aria-label={`Periode ${title}`}>
                        {[
                          ["global", "Ikuti dashboard"],
                          ["today", "Hari ini"],
                          ["month", "Bulan ini"],
                          ["year", "Tahun ini"],
                          ["custom", "Kustom"],
                        ].map(([value, label]) => (
                          <button type="button" key={value} aria-pressed={periodMode === value} onClick={() => choosePeriod(value)}>{label}</button>
                        ))}
                      </div>
                    ) : null}
                    <button type="button" data-testid={`finance-remove-${widget.widget_id}`} onClick={() => onLayout(removeWidget(widgets, widget.widget_id))}>Hapus dari Dashboard</button>
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
            <div className="kb-body">
              {infoFor === widget.widget_id ? (
                <div className="kb-info" data-testid={`finance-info-panel-${widget.widget_id}`}>
                  <p>{meta?.summary}</p>
                  {model?.detail ? <p>{model.detail}</p> : null}
                </div>
              ) : null}
              <CardBody
                id={widget.widget_id}
                model={model}
                compact={widget.h <= 2}
                expanded={!!expanded[widget.widget_id]}
                onRetry={onRetry}
              />
              {settingsFor === widget.widget_id ? (
                <WidgetSettings widget={widget} onSettings={onSettings} />
              ) : null}
            </div>
            {editing ? (
              <button
                type="button"
                className="kb-resize"
                aria-label={`Ubah ukuran ${title}`}
                data-testid={`finance-resize-${widget.widget_id}`}
                onPointerDown={(event) => beginPointer(event, widget, "resize")}
              />
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

function WidgetSettings({ widget, onSettings }) {
  const meta = financeWidgetMeta(widget.widget_id);
  const settings = widget.settings || {};
  function patch(next) {
    onSettings(widget.widget_id, { ...settings, ...next });
  }
  return (
    <form className="kb-settings" data-testid={`finance-settings-${widget.widget_id}`} onSubmit={(event) => event.preventDefault()}>
      <label>
        Judul
        <input value={settings.title || ""} maxLength={60} onChange={(event) => patch({ title: event.target.value })} />
      </label>
      {meta?.period && settings.period_mode === "custom" ? (
        <div className="kb-dates">
          <SalesInvoiceDateField valueIso={settings.start_date || ""} onChange={(iso) => patch({ start_date: iso })} calendarLabel="Kalender awal widget" testId={`finance-widget-start-${widget.widget_id}`} />
          <SalesInvoiceDateField valueIso={settings.end_date || ""} onChange={(iso) => patch({ end_date: iso })} calendarLabel="Kalender akhir widget" testId={`finance-widget-end-${widget.widget_id}`} />
        </div>
      ) : null}
    </form>
  );
}
