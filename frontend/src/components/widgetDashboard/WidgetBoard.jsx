import { useEffect, useRef, useState } from "react";
import { formatAxisMoney, formatMoney } from "../../lib/financialDashboard";
import { chartWithheld } from "../../lib/widgetDashboardModel";
import { TABS, WIDGETS } from "./widgetDashboardCatalog";
import "./widgetDashboard.css";

const BLOCKED = ["loading", "error", "empty", "unavailable", "forbidden"];

export function useNarrow() {
  const query = "(max-width: 900px)";
  const [narrow, setNarrow] = useState(() => (
    typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false
  ));
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const media = window.matchMedia(query);
    const onChange = () => setNarrow(media.matches);
    onChange();
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return narrow;
}

function compactMoney(amount) {
  const value = Number(amount);
  if (!Number.isFinite(value)) return "—";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const text = (n) => n.toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  if (abs >= 1_000_000_000) return `${sign}Rp ${text(abs / 1_000_000_000)} M`;
  if (abs >= 1_000_000) return `${sign}Rp ${text(abs / 1_000_000)} jt`;
  if (abs >= 1_000) return `${sign}Rp ${text(abs / 1_000)} rb`;
  return formatMoney(value);
}

function Money({ amount, zeroAsRecorded = false }) {
  const value = Number(amount);
  const full = formatMoney(value);
  const text = zeroAsRecorded && value === 0 ? "Nol tercatat · Rp 0" : compactMoney(value);
  return <span title={full}>{text}</span>;
}

function Overlay({ open, title, testid, onClose, children }) {
  if (!open) return null;
  return (
    <div className="wp-overlay" onMouseDown={onClose}>
      <div className="wp-dialog" role="dialog" aria-modal="true" aria-label={title} data-testid={testid} onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <h2>{title}</h2>
          <button type="button" onClick={onClose}>Tutup</button>
        </header>
        <div className="wp-dialog-body">{children}</div>
      </div>
    </div>
  );
}

function StateLine({ status, detail }) {
  const label = {
    loading: "Memuat…",
    error: "Gagal memuat",
    empty: "Belum ada data",
    unavailable: "Belum tersedia",
    zero: "Nol tercatat",
    incomplete: "Belum lengkap",
    forbidden: "Tidak ada izin",
  }[status];
  if (!label) return null;
  return <p className={`wp-state is-${status}`}>{label}.{detail ? ` ${detail}` : ""}</p>;
}

function Card({ id, meta, allowed, model, narrow, active, onInfo, children }) {
  const show = !narrow || active;
  return (
    <section className="wp-card" hidden={!show} data-testid={`widget-${id}`} data-area={id} data-status={allowed ? model.status : "forbidden"}>
      <header>
        <div>
          <h2><span className="wp-code">{meta.code}</span> {meta.title}</h2>
          <p className="wp-kicker">{meta.kicker}</p>
        </div>
        <button type="button" className="wp-text-btn" data-testid={`info-${id}`} onClick={() => onInfo(id)}>Info</button>
      </header>
      <div className="wp-body">
        {allowed ? children : (
          <p className="wp-state is-forbidden" data-testid={`forbidden-${id}`}>Tidak ada izin {(meta.perms || [meta.perm]).join(" atau ")}. Angka tidak ditampilkan.</p>
        )}
      </div>
    </section>
  );
}

function Bars({ rows, scale = "max" }) {
  const amounts = rows.map((row) => Number(row.amount));
  const withhold = chartWithheld(amounts);
  const max = Math.max(...amounts.map((amount) => (Number.isFinite(amount) ? amount : 0)), 0);
  const total = amounts.reduce((sum, amount) => sum + (Number.isFinite(amount) ? amount : 0), 0);
  const denom = scale === "total" ? total : max;
  return (
    <div className="wp-split">
      {rows.map((row) => {
        const amount = Number(row.amount);
        const share = !withhold && denom ? amount / denom : 0;
        return (
          <div className="wp-split-row" key={row.label}>
            <p>{row.label}</p>
            <strong><Money amount={row.amount} /></strong>
            <span className="wp-track" aria-hidden="true">
              <i className={row.kind} data-share={share.toFixed(4)} style={{ width: `${share * 100}%`, background: row.color }} />
            </span>
          </div>
        );
      })}
      <p className="wp-note">
        {withhold
          ? "Grafik tidak ditampilkan karena total nol atau ada nilai negatif."
          : (scale === "total"
            ? "Skala batang: bagian dari total, pembagi yang sama."
            : "Skala batang: dibanding nilai terbesar, bukan bagian dari total.")}
      </p>
    </div>
  );
}

function niceCeiling(max) {
  if (!(max > 0)) return 1;
  const padded = max * 1.08;
  const exp = Math.floor(Math.log10(padded));
  const base = 10 ** exp;
  const n = padded / base;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * base;
}

function TrendChart({ points }) {
  const ref = useRef(null);
  const [width, setWidth] = useState(560);
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const measure = () => {
      const next = Math.round(node.clientWidth);
      if (next > 0) setWidth(next);
    };
    measure();
    if (typeof ResizeObserver !== "function") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  if (!points.length) return null;
  const yMax = niceCeiling(Math.max(...points.map((point) => Number(point.amount) || 0)));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((part) => yMax * part);
  const axis = 58;
  const padL = 10;
  const padR = 36;
  const padT = 14;
  const padB = 28;
  const height = 164;
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
  return (
    <div className="wp-plot" data-testid="trend-plot" ref={ref}>
      <svg data-testid="trend-chart" data-points={points.length} width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Tren omzet">
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={axis} x2={width - padR} y1={yAt(tick)} y2={yAt(tick)} stroke="#e7f0eb" />
            <text data-testid="trend-y" x={axis - 8} y={yAt(tick) + 4} textAnchor="end" fill="#3d4f48" fontSize="13">{formatAxisMoney(tick)}</text>
          </g>
        ))}
        <path d={area} fill="#176B4D" fillOpacity="0.18" />
        <path data-testid="trend-line" d={line} fill="none" stroke="#176B4D" strokeWidth="2.5" />
        {points.map((point, index) => (
          <g key={`${point.label}-${index}`}>
            <circle data-testid="trend-point" data-label={point.label} data-amount={point.amount} cx={xAt(index)} cy={yAt(point.amount)} r="4.5" fill="#176B4D">
              <title>{`${point.label}: ${formatMoney(point.amount)}`}</title>
            </circle>
            {index % labelEvery === 0 || index === points.length - 1 ? (
              <text data-testid="trend-x" x={xAt(index)} y={height - 8} textAnchor="middle" fill="#3d4f48" fontSize="13">{point.label}</text>
            ) : null}
          </g>
        ))}
      </svg>
    </div>
  );
}

function share(amount, total) {
  const denom = Number(total);
  const value = Number(amount);
  if (!Number.isFinite(value) || !Number.isFinite(denom) || denom <= 0 || value < 0) return 0;
  return Math.round((value / denom) * 1000) / 10;
}

function RankList({ rows, onAll, extra, serviceShare = false }) {
  const visible = rows.slice(0, 5);
  const total = rows.reduce((sum, row) => sum + (Number(row.amount) || 0), 0);
  const withheld = !serviceShare && chartWithheld(rows.map((row) => row.amount));
  const percent = (row) => {
    if (serviceShare) {
      if (row.share == null || !Number.isFinite(Number(row.share))) return null;
      return Number(row.share);
    }
    if (withheld) return null;
    return share(row.amount, total);
  };
  return (
    <>
      <ol className="wp-rank-list">
        {visible.map((row, index) => {
          const value = percent(row);
          return (
            <li key={row.key || row.name}>
              <span className="wp-medal">{index + 1}</span>
              <div>
                <p className="wp-name">{row.name}</p>
                {extra ? extra(row) : null}
                <div className="wp-bar" aria-hidden="true"><i style={{ width: `${value == null ? 0 : value}%` }} /></div>
              </div>
              <div className="wp-amt">
                <Money amount={row.amount} />
                <span>{value == null ? "—" : `${value.toLocaleString("id-ID")}%`}</span>
              </div>
            </li>
          );
        })}
      </ol>
      {withheld ? <p className="wp-note">Grafik tidak ditampilkan karena total nol atau ada nilai negatif.</p> : null}
      {rows.length > 5 ? <button type="button" className="wp-more" onClick={onAll}>Lihat semua</button> : null}
    </>
  );
}

function Sheet({ columns, rows, onAll }) {
  const visible = rows.slice(0, 5);
  return (
    <>
      <div className="wp-sheet">
        <div className="wp-sheet-head" style={{ gridTemplateColumns: columns.map((col) => col.width).join(" ") }}>
          {columns.map((col) => <span key={col.key} className={col.align || ""}>{col.label}</span>)}
        </div>
        <div className="wp-sheet-body">
          {visible.map((row, index) => (
            <div className="wp-sheet-row" key={row.key || `${row.name}-${row.order || row.date || index}`} style={{ gridTemplateColumns: columns.map((col) => col.width).join(" ") }}>
              {columns.map((col) => <div key={col.key} className={col.align || ""}>{col.render(row, index)}</div>)}
            </div>
          ))}
        </div>
      </div>
      {rows.length > 5 ? <button type="button" className="wp-more" onClick={onAll}>Lihat semua</button> : null}
    </>
  );
}

function AllRows({ rows }) {
  const total = rows.reduce((sum, row) => sum + (Number(row.amount) || 0), 0);
  const withheld = chartWithheld(rows.map((row) => row.amount));
  return (
    <ol className="wp-rank-list">
      {rows.map((row, index) => (
        <li key={row.key || `${row.name}-${index}`}>
          <span className="wp-medal">{index + 1}</span>
          <div>
            <p className="wp-name">{row.name}</p>
            {row.order ? <p className="wp-meta">{row.order}</p> : null}
            {row.date ? <p className="wp-meta">{row.date}</p> : null}
            {row.statusLabel ? <p className="wp-meta">{row.statusLabel}</p> : null}
            {row.days != null ? <p className="wp-meta">{row.days} hari</p> : null}
            {row.invoices != null ? <p className="wp-meta">{row.invoices} faktur</p> : null}
            {row.qty != null ? <p className="wp-meta">{row.qty} {row.unit}</p> : null}
          </div>
          {row.amount != null ? (
            <div className="wp-amt">
              <Money amount={row.amount} zeroAsRecorded={row.zero} />
              {row.share == null
                ? (row.share === undefined && !withheld && total > 0 ? <span>{share(row.amount, total).toLocaleString("id-ID")}%</span> : null)
                : <span>{Number(row.share).toLocaleString("id-ID")}%</span>}
            </div>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function KpiStack({ items }) {
  return (
    <div className="wp-kpi-stack">
      {items.map((item) => (
        <article key={item.label}>
          <p>{item.label}</p>
          <strong data-testid={item.testid}>{item.value}</strong>
        </article>
      ))}
    </div>
  );
}

export default function WidgetBoard({
  tabId,
  panelId,
  widgetId,
  models,
  allowed,
  onNavigate,
  periodLabel,
  sampleBadge = false,
  periodControl = null,
  narrow,
}) {
  const tab = TABS.find((item) => item.id === tabId) || TABS[0];
  const panel = tab.panels.find((item) => item.id === panelId) || tab.panels[0];
  const [dialog, setDialog] = useState(null);
  const [drawer, setDrawer] = useState(false);
  const [plSpan, setPlSpan] = useState("period");

  function body(id) {
    const model = models[id] || { status: "loading" };
    if (!allowed(id)) return null;
    if (id !== "pl" && BLOCKED.includes(model.status)) return <StateLine status={model.status} detail={model.detail} />;
    if (id === "omzet") {
      return (
        <>
          <p className="wp-hero" data-testid="omzet-amount"><Money amount={model.amount} /></p>
          <p className="wp-note">{model.invoiceCount} faktur · {model.periodLabel || periodLabel}</p>
          <StateLine status={model.status} />
        </>
      );
    }
    if (id === "settlement") {
      return (
        <>
          <Bars scale="total" rows={[
            { label: `Lunas · ${model.settledCount} faktur`, amount: model.settledGrand, kind: "is-paid" },
            { label: `Belum lunas · ${model.openCount} faktur`, amount: model.openGrand, kind: "is-open" },
          ]} />
          <StateLine status={model.status} detail={model.detail} />
        </>
      );
    }
    if (id === "purchases") {
      return (
        <>
          <p className="wp-hero"><Money amount={model.amount} /></p>
          <Bars scale="total" rows={[
            { label: `Lunas · ${model.settledCount} faktur`, amount: model.settledGrand, kind: "is-paid" },
            { label: `Belum lunas · ${model.openCount} faktur`, amount: model.openOutstanding, kind: "is-open" },
          ]} />
          <StateLine status={model.status} detail={model.detail} />
        </>
      );
    }
    if (id === "trend") {
      const negative = (model.points || []).some((point) => Number(point.amount) < 0);
      if (negative || !(model.points || []).some((point) => Number(point.amount) > 0)) {
        return <StateLine status={model.status === "complete" ? "zero" : model.status} detail={negative ? "Grafik tidak ditampilkan karena ada nilai negatif." : model.detail} />;
      }
      return (
        <>
          <TrendChart points={model.points} />
          {model.grain ? <p className="wp-note">{model.grain}</p> : null}
        </>
      );
    }
    if (id === "sales" || id === "products" || id === "expense") {
      return (
        <>
          <RankList rows={model.rows || []} onAll={() => setDialog(id)} serviceShare={id === "sales" || id === "products"} extra={id === "products" ? (row) => (row.qty != null ? <p className="wp-meta">{row.qty} {row.unit}</p> : null) : null} />
          <StateLine status={model.status} detail={model.status === "incomplete" ? model.detail : ""} />
        </>
      );
    }
    if (id === "customers") {
      return (
        <>
          <Sheet
            rows={model.rows || []}
            onAll={() => setDialog(id)}
            columns={[
              { key: "n", label: "#", width: "28px", render: (_row, index) => <span className="wp-medal">{index + 1}</span> },
              { key: "name", label: "Pelanggan", width: "minmax(0, 1fr)", render: (row) => <p className="wp-name">{row.name}</p> },
              { key: "amount", label: "Nominal", width: "92px", align: "is-end", render: (row) => <Money amount={row.amount} /> },
              { key: "invoices", label: "Faktur", width: "52px", align: "is-end", render: (row) => row.invoices },
            ]}
          />
          <StateLine status={model.status} detail={model.status === "incomplete" ? model.detail : ""} />
        </>
      );
    }
    if (id === "aging") {
      return (
        <>
          <Bars scale="max" rows={(model.rows || []).map((row) => ({ ...row, kind: "" }))} />
          <p className="wp-note">{model.scope || "Saat ini"} · {model.asOfLabel}. Di luar umur: <Money amount={model.unbucketed} />. Bukan saldo akhir filter.</p>
          <StateLine status={model.status} />
        </>
      );
    }
    if (id === "today") {
      return (
        <>
          <KpiStack items={[
            { label: "Total omzet", value: <Money amount={model.amount} />, testid: "today-amount" },
            { label: "Jumlah faktur", value: model.invoiceCount, testid: "today-count" },
            { label: "Rata-rata per faktur", value: model.average == null ? "Belum tersedia" : <Money amount={model.average} />, testid: "today-average" },
          ]} />
          <p className="wp-note">{model.todayLabel}</p>
          <StateLine status={model.status} />
        </>
      );
    }
    if (id === "shipToday" || id === "shipLate" || id === "shipNext") {
      return (
        <>
          <Sheet
            rows={model.rows || []}
            onAll={() => setDialog(id)}
            columns={[
              { key: "name", label: "Pelanggan", width: "minmax(0, 1.4fr)", render: (row) => <p className="wp-name">{row.name}</p> },
              { key: "order", label: "Pesanan", width: "minmax(72px, 0.6fr)", render: (row) => row.order },
              { key: "date", label: "Tanggal kirim", width: "minmax(88px, 0.7fr)", align: "is-end", render: (row) => row.date },
            ]}
          />
          <StateLine status={model.status} detail={model.detail} />
        </>
      );
    }
    if (id === "production") {
      return (
        <>
          <Sheet
            rows={model.rows || []}
            onAll={() => setDialog(id)}
            columns={[
              { key: "name", label: "Barang", width: "minmax(0, 1.5fr)", render: (row) => <p className="wp-name">{row.name}</p> },
              { key: "order", label: "Pesanan", width: "minmax(72px, 0.5fr)", render: (row) => row.order },
              { key: "date", label: "Tanggal pengerjaan", width: "minmax(108px, 0.8fr)", align: "is-end", render: (row) => row.date },
            ]}
          />
          <StateLine status={model.status} detail={model.detail} />
        </>
      );
    }
    if (id === "ap") {
      return (
        <>
          <Bars scale="total" rows={[
            { label: "Belum jatuh tempo", amount: model.notDue, kind: "is-wait" },
            { label: "Lewat jatuh tempo", amount: model.overdue, kind: "is-late" },
          ]} />
          <p className="wp-note">{model.scope} · {model.asOfLabel}. Filter periode tidak mengubah saldo ini.</p>
          <StateLine status={model.status} detail={model.detail} />
        </>
      );
    }
    if (id === "cash") {
      return (
        <>
          <KpiStack items={[
            { label: "Kas masuk", value: <Money amount={model.cashIn} /> },
            { label: "Kas keluar", value: <Money amount={model.cashOut} /> },
            { label: "Neto", value: <Money amount={model.net} /> },
          ]} />
          <p className="wp-note">Transfer internal {model.internal == null ? "tidak tersedia" : <Money amount={model.internal} />} tidak masuk arus ini.</p>
          <StateLine status={model.status} />
        </>
      );
    }
    if (id === "pl") {
      const ytd = plSpan === "ytd";
      const status = ytd ? model.ytdStatus : model.status;
      const rows = ytd ? (model.ytd || []) : (model.rows || []);
      return (
        <>
          <div className="wp-span" role="group" aria-label="Rentang laba rugi">
            <button type="button" aria-pressed={plSpan === "period"} onClick={() => setPlSpan("period")}>Periode</button>
            <button type="button" aria-pressed={plSpan === "ytd"} data-testid="pl-ytd" onClick={() => setPlSpan("ytd")}>Tahun berjalan</button>
          </div>
          <p className="wp-note">{ytd ? model.ytdLabel : periodLabel}</p>
          {BLOCKED.includes(status) ? <StateLine status={status} detail={ytd ? model.ytdDetail : model.detail} /> : (
            <Bars rows={rows.map((row, index) => ({ ...row, label: row.name, kind: index === rows.length - 1 ? "is-paid" : "is-wait" }))} />
          )}
          {status === "incomplete" ? <StateLine status="incomplete" detail={ytd ? model.ytdDetail : model.detail} /> : null}
        </>
      );
    }
    if (id === "assets") {
      return (
        <>
          <p className="wp-hero" data-testid="asset-amount"><Money amount={model.amount} /></p>
          <p className="wp-note">
            {model.scope} · {model.asOfLabel}. Selisih neraca <Money amount={model.difference} zeroAsRecorded /> · {model.balanced ? "seimbang" : "belum seimbang"}. Filter periode tidak mengubah saldo ini.
          </p>
        </>
      );
    }
    if (id === "overdue") {
      return (
        <>
          <Sheet
            rows={model.rows || []}
            onAll={() => setDialog(id)}
            columns={[
              { key: "name", label: "Pelanggan", width: "minmax(0, 1.4fr)", render: (row) => <p className="wp-name">{row.name}</p> },
              { key: "amount", label: "Sisa tagihan", width: "96px", align: "is-end", render: (row) => <Money amount={row.amount} /> },
              { key: "days", label: "Lama", width: "72px", align: "is-end", render: (row) => `${row.days} hari` },
            ]}
          />
          <p className="wp-note">{model.scope} · {model.asOfLabel}. Bukan piutang operasional.</p>
          <StateLine status={model.status} detail={model.status === "incomplete" ? model.detail : ""} />
        </>
      );
    }
    return (
      <>
        <Sheet
          rows={model.rows || []}
          onAll={() => setDialog(id)}
          columns={[
            { key: "name", label: "Akun", width: "minmax(0, 1.3fr)", render: (row) => (<><p className="wp-name">{row.name}</p>{row.order ? <p className="wp-meta">{row.order}</p> : null}</>) },
            { key: "date", label: "Tanggal", width: "92px", render: (row) => row.date },
            { key: "status", label: "Status", width: "78px", render: (row) => row.statusLabel },
            { key: "amount", label: "Selisih", width: "124px", align: "is-end", render: (row) => <Money amount={row.amount} zeroAsRecorded={row.zero} /> },
          ]}
        />
        <StateLine status={model.status} detail={model.detail} />
      </>
    );
  }

  const aboutId = typeof dialog === "string" && dialog.startsWith("about:") ? dialog.slice(6) : null;
  const about = aboutId ? WIDGETS[aboutId] : null;
  const dialogModel = dialog && WIDGETS[dialog] ? models[dialog] : null;

  return (
    <div className="widget-dashboard-page" data-testid="widget-dashboard">
      <header className="wp-head">
        <h1>Dashboard widget</h1>
        <div className="wp-tabs" role="tablist" aria-label="Bagian dashboard">
          {TABS.map((item) => (
            <button key={item.id} type="button" role="tab" aria-selected={item.id === tab.id} onClick={() => onNavigate({ tab: item.id, panel: item.panels[0].id, widget: item.panels[0].widgets[0] })}>
              {item.label}
            </button>
          ))}
        </div>
        {tab.panels.length > 1 ? (
          <div className="wp-tabs" role="tablist" aria-label="Panel">
            {tab.panels.map((item) => (
              <button key={item.id} type="button" role="tab" aria-selected={item.id === panel.id} onClick={() => onNavigate({ panel: item.id, widget: item.widgets[0] })}>
                {item.label}
              </button>
            ))}
          </div>
        ) : null}
        <div className="wp-period">
          <p data-testid="widget-range">{periodLabel} · Asia/Jakarta</p>
          {periodControl}
          {sampleBadge ? <span className="wp-badge">DATA CONTOH</span> : null}
          {narrow ? <button type="button" className="wp-text-btn" onClick={() => setDrawer(true)}>Widget</button> : null}
          <button type="button" className="wp-text-btn" data-testid="open-info" onClick={() => setDialog("info")}>Info</button>
        </div>
      </header>
      <div className={`wp-board ${panel.layout}`} data-testid={`panel-${tab.id}-${panel.id}`}>
        {panel.widgets.map((id) => (
          <Card key={id} id={id} meta={WIDGETS[id]} allowed={allowed(id)} model={models[id] || { status: "loading" }} narrow={narrow} active={id === widgetId} onInfo={(widget) => setDialog(`about:${widget}`)}>
            {body(id)}
          </Card>
        ))}
      </div>
      {drawer ? (
        <div className="wp-drawer" data-testid="widget-drawer">
          <strong>Widget {panel.label}</strong>
          {panel.widgets.map((id) => (
            <button key={id} type="button" className="wp-text-btn" onClick={() => { onNavigate({ widget: id }); setDrawer(false); }}>
              {WIDGETS[id].code} · {WIDGETS[id].title}
            </button>
          ))}
          <button type="button" className="wp-text-btn" onClick={() => setDrawer(false)}>Tutup</button>
        </div>
      ) : null}
      <Overlay open={Boolean(about)} title={about ? `${about.code} · ${about.title}` : ""} testid="widget-info" onClose={() => setDialog(null)}>
        {about ? (
          <>
            <p className="wp-note">{about.info}</p>
            <p className="wp-note">Sumber: {about.endpoint}</p>
            <p className="wp-note">Izin: {about.perm}</p>
            {models[aboutId]?.detail ? <p className="wp-note">{models[aboutId].detail}</p> : null}
          </>
        ) : null}
      </Overlay>
      <Overlay open={dialog === "info"} title="Catatan kualitas data" testid="widget-panel-info" onClose={() => setDialog(null)}>
        <p className="wp-note">Angka mengikuti service. Gagal memuat tidak diganti angka contoh. Dashboard /dashboard dan /dashboard/penjualan tetap ada.</p>
        {panel.widgets.map((id) => (
          <p key={id} className="wp-note"><strong>{WIDGETS[id].code} {WIDGETS[id].title}.</strong> {WIDGETS[id].info}</p>
        ))}
      </Overlay>
      <Overlay open={Boolean(dialogModel?.rows)} title={WIDGETS[dialog]?.title || "Daftar"} testid="widget-all" onClose={() => setDialog(null)}>
        {dialogModel?.rows ? <AllRows rows={dialogModel.rows} /> : null}
      </Overlay>
    </div>
  );
}
