import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeftRight,
  ArrowUpRight,
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
import {
  fakturPembelianDrillPath,
  fakturPenjualanDrillPath,
  isInteractiveDrillEventTarget,
  labaRugiDrillPath,
  mutasiKasDrillPath,
} from "../../lib/dashboardDrillNav";
import SalesInvoiceDateField from "../SalesInvoiceDateField";
import { financeWidgetAllowed, financeWidgetMeta } from "./financeWidgetCatalog";
import { COLUMNS, ROW_HEIGHT, gridHeight, placeWidget, removeWidget } from "./financeWidgetLayout";

function financeDrillPath(id, range = {}, hasPerm) {
  const start = range.start || "";
  const end = range.end || "";
  if (id === "cash") {
    if (!(hasPerm("cash_bank.view") || hasPerm("nav.cash_bank.mutasi") || hasPerm("nav.cash_bank"))) return null;
    return mutasiKasDrillPath({ start, end, excludeInternal: true });
  }
  if (id === "expense" || id === "profit") {
    if (!(hasPerm("profit_loss.view") || hasPerm("nav.ledger.profit_loss"))) return null;
    return labaRugiDrillPath({ start, end, widget: id === "expense" ? "expense" : "profit" });
  }
  if (id === "purchases") {
    if (!(hasPerm("purchase_invoices.view") || hasPerm("purchase_invoices.create"))) return null;
    return fakturPembelianDrillPath({ dateFrom: start, dateTo: end });
  }
  // Period omzet only — per-seller exact drill needs API sales filter (not supported).
  if (id === "sales" || id === "trend") {
    if (!(
      hasPerm("sales.invoice.view")
      || hasPerm("sales_order.view")
      || hasPerm("po.view")
      || hasPerm("nav.sales.sales_invoice")
    )) return null;
    return fakturPenjualanDrillPath({
      dateFrom: start,
      dateTo: end,
      widget: id === "trend" ? "trend" : "sales",
      dashboard: "keuangan",
    });
  }
  if (id === "recon") {
    if (!(hasPerm("cash_bank.reconcile") || hasPerm("cash_bank.view") || hasPerm("nav.cash_bank.reconciliation"))) return null;
    return "/kas-bank/rekonsiliasi";
  }
  return null;
}

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
  products: "Nilai baris barang terjual",
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
  expense: "Belum ada jurnal beban pada periode.",
  profit: "Belum ada jurnal laba rugi pada periode ini.",
  sales: "Belum ada faktur penjualan pada periode ini.",
  trend: "Belum ada data penjualan pada periode ini.",
  products: "Belum ada data penjualan barang pada periode ini.",
  sellers: "Belum ada data performa penjual pada periode ini.",
  purchases: "Belum ada faktur pembelian pada periode ini.",
  recon: "Belum ada pencocokan transaksi maupun rekonsiliasi saldo lama.",
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
        <i key={`${point.label}-${index}`} style={{ height: `${Math.max(0, (Number(point.amount) / max) * 100)}%` }} />
      ))}
    </div>
  );
}

function SeriesChart({ points, grain }) {
  const rows = (points || []).filter((point) => point.amount != null && Number.isFinite(Number(point.amount)));
  if (!rows.length) return null;
  const max = rows.reduce((peak, point) => Math.max(peak, Number(point.amount)), 0);
  const min = rows.reduce((low, point) => Math.min(low, Number(point.amount)), 0);
  const span = max - min || 1;
  const visible = rows;
  return (
    <div className="kb-chart">
      <div className="kb-bars" role="img" aria-label={`Tren ${grain || "penjualan"}: ${visible.map((p) => `${p.label} ${formatMoney(p.amount)}`).join("; ")}`} style={{ minWidth: `${visible.length * 12}px` }}>
        {visible.map((point, index) => (
          <span key={`${point.label}-${index}`} className="kb-bar" title={`${point.label}: ${formatMoney(point.amount)}`}>
            <i style={{ position: "absolute", height: `${Math.abs(Number(point.amount)) / span * 100}%`, bottom: `${(Number(point.amount) < 0 ? Number(point.amount) - min : -min) / span * 100}%`, background: Number(point.amount) < 0 ? "#dc2626" : undefined }} />
          </span>
        ))}
      </div>
      <div className="kb-axis" style={{ minWidth: `${visible.length * 12}px` }}>
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
          <span>Beban positif</span>
          <strong>{formatMoney(total)}</strong>
        </div>
      </div>
      <ul className="kb-legend">
        {items.map((row, index) => (
          <li key={row.key || row.name}>
            <i style={{ background: SLICE[index % SLICE.length] }} />
            <span title={row.name}>{row.name}</span>
            <b>{((Number(row.amount) / total) * 100).toLocaleString("id-ID", { maximumFractionDigits: 0 })}%<small>{formatMoney(row.amount)}</small></b>
          </li>
        ))}
      </ul>
    </div>
  );
}

function signedMoney(amount) {
  if (amount == null || !Number.isFinite(Number(amount))) return "—";
  return formatMoney(amount);
}

function RankList({ rows, expanded, showShare }) {
  const list = expanded ? rows : rows.slice(0, 5);
  return list.map((row, index) => {
    const share = Number(row.share);
    const width = showShare && row.share != null && Number.isFinite(share)
      ? Math.min(100, Math.max(0, share))
      : 0;
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

function ExpenseBody({ model, onRetry }) {
  const status = model?.status || "loading";
  if (status === "loading" || status === "error" || status === "forbidden" || status === "unavailable" || status === "incomplete") {
    return <Quiet id="expense" status={status} onRetry={onRetry} />;
  }
  if (status === "empty") return <Quiet id="expense" status="empty" />;
  const rows = model?.rows || [];
  const positive = rows.filter((row) => row.amount != null && Number(row.amount) > 0);
  const negative = rows.filter((row) => row.amount != null && Number(row.amount) < 0);
  if (status === "zero" || (!positive.length && !negative.length)) {
    return (
      <div className="kb-empty is-zero">
        <strong>Nol tercatat</strong>
        <p>Tidak ada beban operasional bernilai nonnol pada periode yang dipilih.</p>
      </div>
    );
  }
  if (!positive.length) {
    return (
      <div className="kb-empty is-zero">
        <strong>Tidak ada komposisi positif</strong>
        <p>Nilai negatif ditampilkan apa adanya dan tidak dibalik menjadi beban positif.</p>
        <div className="kb-lines">
          {negative.map((row) => (
            <div key={row.key || row.name}><span>{row.name}</span><strong>{signedMoney(row.amount)}</strong></div>
          ))}
        </div>
      </div>
    );
  }
  return (
    <>
      <Donut rows={positive} />
      {negative.length ? (
        <div className="kb-lines">
          <p className="kb-kicker">Akun bernilai negatif tidak masuk donut dan tidak dibalik tandanya.</p>
          {negative.map((row) => (
            <div key={row.key || row.name}><span>{row.name}</span><strong>{signedMoney(row.amount)}</strong></div>
          ))}
        </div>
      ) : null}
    </>
  );
}

function CardBody({ id, model, compact, expanded, onRetry }) {
  const status = model?.status || "loading";
  if (id === "expense") return <ExpenseBody model={model} onRetry={onRetry} />;
  const rankedPartial = (id === "products" || id === "sellers") && status === "incomplete" && (model?.rows || []).length > 0;
  if (QUIET.has(status) && !rankedPartial) return <Quiet id={id} status={status} onRetry={onRetry} />;
  if (id === "cash") {
    return (
      <><div className="kb-trio">
        <div><span>Kas Masuk</span><strong>{moneyText(status, model?.cashIn) || "—"}</strong></div>
        <div><span>Kas Keluar</span><strong>{moneyText(status, model?.cashOut) || "—"}</strong></div>
        <div className="kb-net"><span>Neto periode</span><strong>{moneyText(status, model?.net) || "—"}</strong></div>
      </div>
      {!compact && model?.accounts?.length ? <div className="kb-lines kb-cash-accounts">
        <p className="kb-kicker">Saldo akhir per rekening · termasuk transfer internal</p>
        {model.accounts.map(row => <div key={row.account_id}><span>{row.name || row.code}</span><strong>{row.ending == null ? "—" : formatMoney(row.ending)}</strong></div>)}
      </div> : null}</>
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
        <p className="kb-kicker">Lunas {moneyText(status, model?.settledGrand) || "—"} · Nilai Faktur Belum Lunas {moneyText(status, model?.openOutstanding) || "—"}</p>
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
              <div key={row.name} className={/Laba/.test(row.name) ? "kb-subtotal" : ""}><span>{row.name}</span><strong>{row.amount == null ? "—" : formatMoney(row.amount)}</strong></div>
            ))}
          </div>
        ) : null}
      </div>
    );
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
    if (!model?.rows?.length) return <Quiet id={id} status={status === "complete" ? "empty" : status} />;
    const gap = id === "products"
      ? (model.unidentified ? `${model.unidentified} baris tidak teridentifikasi` : "")
      : (model.unassigned ? `${model.unassigned} faktur belum ditetapkan` : "");
    return (
      <>
        {status === "incomplete" ? (
          <p className="kb-kicker">Data belum lengkap{gap ? ` · ${gap}` : ""}</p>
        ) : null}
        <RankList rows={model.rows} expanded={expanded} showShare={status === "complete"} />
      </>
    );
  }
  if (id === "recon") {
    const pairs = model?.pairs || [];
    const legacy = model?.rows || [];
    if (!pairs.length && !legacy.length) return <Quiet id={id} status={status === "complete" ? "empty" : status} />;
    const limit = expanded ? Math.max(pairs.length, legacy.length) : 5;
    return (
      <div className="kb-lines">
        {pairs.length ? <p className="kb-kicker">Pencocokan transaksi</p> : null}
        {pairs.slice(0, limit).map((row) => (
          <div key={row.key}>
            <span title={row.name}>{row.name}</span>
            <strong>{row.statusLabel}{row.date ? ` · ${row.date}` : ""}{row.direction ? ` · ${row.direction}` : ""}</strong>
          </div>
        ))}
        {legacy.length ? <p className="kb-kicker">Rekonsiliasi saldo legacy</p> : null}
        {legacy.slice(0, limit).map((row) => (
          <div key={row.key || row.name}>
            <span title={row.name}>{row.name}</span>
            <strong>{row.statusLabel || "—"}{row.amount == null ? "" : ` · selisih ${signedMoney(row.amount)}`}</strong>
          </div>
        ))}
      </div>
    );
  }
  return <Quiet id={id} status={status} />;
}

export default function FinanceWidgetBoard({
  widgets,
  models,
  editing,
  hasPerm,
  globalPreset,
  periodForWidget,
  onLayout,
  onSettings,
  onRetry,
}) {
  const navigate = useNavigate();
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
        const reconCount = rows.length + (model?.pairs || []).length;
        const canExpand = (widget.widget_id === "products" || widget.widget_id === "sellers")
          ? rows.length > 5
          : widget.widget_id === "recon" && reconCount > 5;
        const periodMode = widget.settings?.period_mode || "global";
        const choosePeriod = (period_mode) => {
          onSettings(widget.widget_id, { ...(widget.settings || {}), period_mode });
          setMenuFor("");
          if (period_mode === "custom") setSettingsFor(widget.widget_id);
        };
        const range = typeof periodForWidget === "function" ? (periodForWidget(widget) || {}) : {};
        const drillPath = (!editing && !QUIET.has(model.status))
          ? financeDrillPath(widget.widget_id, range, hasPerm)
          : null;
        const drillLocked = !editing
          && ["cash", "expense", "profit", "purchases", "sales", "trend", "recon"].includes(widget.widget_id)
          && !drillPath
          && !QUIET.has(model.status);
        return (
          <article
            key={widget.widget_id}
            className={`kb-card kb-concept-card kb-type-${widget.widget_id}${widget.h <= 2 ? " is-compact" : ""}${editing ? " is-edit" : ""}${menuFor === widget.widget_id ? " is-menu" : ""}${drillPath ? " is-drillable" : ""}`}
            data-testid={`finance-widget-${widget.widget_id}`}
            data-status={model.status}
            data-drill={drillPath ? "ready" : (drillLocked ? "locked" : undefined)}
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
              {drillPath ? <span className="pd-drill-icon kb-card-drill" aria-hidden="true"><ArrowUpRight size={14} /></span> : null}
              {drillLocked ? <span className="pd-drill-locked" data-testid={`finance-drill-locked-${widget.widget_id}`}>Akses terbatas</span> : null}
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
            <div
              className="kb-body"
              role={drillPath ? "link" : undefined}
              tabIndex={drillPath ? 0 : undefined}
              aria-label={drillPath ? `Buka rincian ${title}` : undefined}
              data-testid={drillPath ? `finance-drill-${widget.widget_id}` : undefined}
              onClick={(event) => {
                if (!drillPath || isInteractiveDrillEventTarget(event.target)) return;
                navigate(drillPath);
              }}
              onKeyDown={(event) => {
                if (!drillPath || isInteractiveDrillEventTarget(event.target)) return;
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  navigate(drillPath);
                }
              }}
            >
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
            {!editing ? <footer className="kb-card-footer">
              <span>{widget.widget_id === "recon" ? "Snapshot saat ini" : "Periode terpilih"}</span>
              {drillPath ? <button type="button" onClick={() => navigate(drillPath)} aria-label={`Lihat rincian ${title}`}>Lihat rincian ↗</button> : drillLocked ? <span className="pd-drill-locked">Akses rincian terbatas</span> : <span>Rincian pada kartu</span>}
            </footer> : null}
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

