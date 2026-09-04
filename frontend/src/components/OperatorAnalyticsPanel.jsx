import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BarChart3, Download, RefreshCw, Search } from "lucide-react";
import api from "../lib/api";
import FacetSearchSelect from "./FacetSearchSelect";

const PERIOD_OPTS = [
  { key: "today", label: "Hari Ini" },
  { key: "daily", label: "Harian" },
  { key: "week", label: "Mingguan" },
  { key: "month", label: "Bulanan" },
  { key: "months_3", label: "3 Bulan" },
  { key: "months_6", label: "6 Bulan" },
  { key: "months_12", label: "12 Bulan" },
  { key: "year", label: "Tahunan" },
  { key: "custom", label: "Custom" },
];

const PAGE_SIZES = [25, 50, 100];

function fmtQty(n) {
  if (n === null || n === undefined || n === "" || n === "N/A") return "N/A";
  return Number(n || 0).toLocaleString("id-ID");
}

function fmtQtyOrDash(n) {
  if (n === null || n === undefined || n === "" || n === "N/A") return "N/A";
  return Number(n || 0).toLocaleString("id-ID");
}

function fmtDate(v) {
  if (!v) return "—";
  const s = String(v).slice(0, 10);
  const [y, m, d] = s.split("-");
  if (y && m && d) return `${d}/${m}/${y}`;
  return s;
}

function fmtWow(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n)) || !Number.isFinite(Number(n))) return "N/A";
  const v = Number(n);
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}%`;
}

function KpiCard({ label, value, hint, testid }) {
  return (
    <div className="op-an-kpi op-an-kpi-compact" data-testid={testid}>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <em>{hint}</em> : null}
    </div>
  );
}

function WeekBars({ weeks, testid, valueKey = "qty" }) {
  const list = weeks || [];
  const max = Math.max(1, ...list.map((w) => Number(w[valueKey]) || 0));
  if (!list.length) return <p className="empty-state">Tidak ada data pada periode ini.</p>;
  return (
    <div className="op-an-weekbars op-an-weekbars-compact" data-testid={testid}>
      {list.map((w) => (
        <div key={w.key || w.week} className="op-an-weekbar" title={`${w.label}: ${fmtQty(w[valueKey])}`}>
          <div className="op-an-weekbar-col">
            <i style={{ height: `${((Number(w[valueKey]) || 0) / max) * 100}%` }} />
          </div>
          <span>{String(w.label || w.week || "").replace(" ", "\n")}</span>
          <b>{fmtQty(w[valueKey])}</b>
          {w.wow_pct !== undefined ? <em className={(w.wow_pct || 0) < 0 ? "neg" : ""}>{fmtWow(w.wow_pct)}</em> : null}
        </div>
      ))}
    </div>
  );
}

function PoLink({ orderId, poNumber }) {
  if (!orderId && !poNumber) return <span>—</span>;
  const to = `/po/${encodeURIComponent(orderId || poNumber)}`;
  return (
    <Link to={to} className="op-an-po-link" data-testid={`op-an-po-${poNumber}`} onClick={(e) => e.stopPropagation()}>
      {poNumber || orderId}
    </Link>
  );
}

function sortRows(rows, key, dir) {
  const list = [...(rows || [])];
  const mul = dir === "asc" ? 1 : -1;
  list.sort((a, b) => {
    const va = a?.[key];
    const vb = b?.[key];
    if (va == null && vb == null) return 0;
    if (va == null || va === "N/A") return 1;
    if (vb == null || vb === "N/A") return -1;
    if (typeof va === "number" && typeof vb === "number") return (va - vb) * mul;
    return String(va).localeCompare(String(vb), "id") * mul;
  });
  return list;
}

function matchesSearch(row, q) {
  if (!q) return true;
  const hay = [
    row.operator, row.helper, row.screener, row.designer, row.employee_name,
    row.no_po, row.customer, row.cup_item, row.item_name, row.design_line,
  ].filter(Boolean).join(" ").toLowerCase();
  return hay.includes(q);
}

function colClass(c) {
  const fit = c.num || c.mono || c.fit;
  return [c.num ? "num" : "", c.mono ? "mono" : "", fit ? "col-fit" : "col-text"].filter(Boolean).join(" ");
}

function ReportTable({ columns, rows, testid, empty, sortKey, sortDir, onSort }) {
  return (
    <div className="prod-table-wrap op-an-table-wrap op-an-desktop" data-testid={testid}>
      <table className="prod-table op-an-table op-an-report">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                className={colClass(c)}
                onClick={c.sort === false ? undefined : () => onSort?.(c.key)}
                data-sort={sortKey === c.key ? sortDir : undefined}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r._key || i}>
              {columns.map((c) => (
                <td key={c.key} className={colClass(c)}>
                  {c.render ? c.render(r) : (c.num ? fmtQtyOrDash(r[c.key]) : (r[c.key] || "—"))}
                </td>
              ))}
            </tr>
          ))}
          {!rows.length && (
            <tr><td colSpan={columns.length} className="empty-state">{empty}</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function ReportCards({ rows, fields, testid }) {
  return (
    <div className="op-an-cards op-an-mobile" data-testid={testid}>
      {rows.map((r, i) => (
        <article key={r._key || i} className="op-an-card op-an-report-card">
          <dl>
            {fields.map((f) => (
              <div key={f.key}>
                <dt>{f.label}</dt>
                <dd>{f.render ? f.render(r) : (f.num ? fmtQtyOrDash(r[f.key]) : (r[f.key] || "—"))}</dd>
              </div>
            ))}
          </dl>
        </article>
      ))}
    </div>
  );
}

function PeopleTable({ rows, columns, onPick, testid, activeId }) {
  if (!rows?.length) return null;
  return (
    <div className="prod-table-wrap op-an-people-wrap" data-testid={testid}>
      <table className="prod-table op-an-table op-an-people">
        <thead>
          <tr>{columns.map((c) => <th key={c.key} className={colClass(c)}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id || r.email}
              className={"op-an-click" + (activeId && (r.id === activeId || r.email === activeId) ? " op-an-row-on" : "")}
              onClick={() => onPick?.(r.id || r.email)}
            >
              {columns.map((c) => (
                <td key={c.key} className={colClass(c)}>
                  {c.render ? c.render(r) : (c.num ? fmtQty(r[c.key]) : (r[c.key] || "—"))}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Pager({ page, pageSize, total, onPage, onSize }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="op-an-pager" data-testid="op-an-pager">
      <span className="muted small">{total.toLocaleString("id-ID")} baris</span>
      <select value={pageSize} onChange={(e) => onSize(Number(e.target.value))} data-testid="op-an-page-size">
        {PAGE_SIZES.map((n) => <option key={n} value={n}>{n} / halaman</option>)}
      </select>
      <button type="button" className="ghost sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Prev</button>
      <span className="muted small">{page} / {pages}</span>
      <button type="button" className="ghost sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  );
}

export default function OperatorAnalyticsPanel() {
  const [period, setPeriod] = useState("month");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [tab, setTab] = useState("operator");
  const [userId, setUserId] = useState("all");
  const [customer, setCustomer] = useState("all");
  const [item, setItem] = useState("all");
  const [po, setPo] = useState("all");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [sortKey, setSortKey] = useState("tanggal");
  const [sortDir, setSortDir] = useState("desc");
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [exporting, setExporting] = useState(false);

  const periodParams = () => {
    const params = { period };
    if (period === "custom") {
      if (dateFrom) params.from = dateFrom;
      if (dateTo) params.to = dateTo;
    }
    if (customer && customer !== "all") params.customer = customer;
    if (item && item !== "all") params.item = item;
    if (po && po !== "all") params.po = po;
    return params;
  };

  const load = async () => {
    if (period === "custom" && (!dateFrom || !dateTo)) return;
    setBusy(true);
    setErr("");
    try {
      const params = periodParams();
      if (tab === "operator") {
        if (userId && userId !== "all") params.operator_id = userId;
        setData(await api.analyticsOperatorKpis(params));
      } else if (tab === "helper") {
        if (userId && userId !== "all") params.user_id = userId;
        setData(await api.analyticsPreparation(params));
      } else if (tab === "screener") {
        if (userId && userId !== "all") params.user_id = userId;
        setData(await api.analyticsScreener(params));
      } else {
        if (userId && userId !== "all") params.user_id = userId;
        setData(await api.analyticsDesigns(params));
      }
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat Analitik Output");
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    setUserId("all");
    setQ("");
    setPage(1);
    /* eslint-disable-next-line */
  }, [tab]);

  useEffect(() => { setPage(1); }, [userId, customer, item, po, period, dateFrom, dateTo, q]);

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [tab, period, dateFrom, dateTo, userId, customer, item, po]);

  const resolvedFrom = data?.period?.from || dateFrom;
  const resolvedTo = data?.period?.to || dateTo;
  const customDatesReady = period !== "custom" || (dateFrom && dateTo);
  const designerValid = data?.designer_attribution?.valid !== false;
  const tabs = [
    { key: "operator", label: "Operator" },
    { key: "helper", label: "Helper" },
    { key: "screener", label: "Screener" },
    { key: "design", label: tab === "design" && data && !designerValid ? "Jumlah Design" : "Designer" },
  ];

  const userOptions = useMemo(() => {
    if (tab === "operator") return data?.filters?.operators || [];
    if (tab === "helper" || tab === "screener") return data?.filters?.users || [];
    return data?.filters?.users || (data?.designers || []).map((o) => ({ id: o.email, name: o.name }));
  }, [tab, data]);

  const customers = data?.filters?.customers || [];
  const items = data?.filters?.items || [];
  const pos = data?.filters?.pos || [];
  const kpis = data?.kpis || {};
  const people = data?.people || [];
  const chartBuckets = data?.output_series?.buckets || data?.weekly_trend?.weeks || data?.weekly || [];

  const filtered = useMemo(() => {
    const rows = data?.rows || [];
    const needle = q.trim().toLowerCase();
    return sortRows(rows.filter((r) => matchesSearch(r, needle)), sortKey, sortDir);
  }, [data, q, sortKey, sortDir]);

  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);

  const onSort = (key) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  };

  const exportData = async () => {
    setExporting(true);
    try {
      const params = { ...periodParams(), tab, fmt: "xlsx" };
      if (tab === "operator" || tab === "design") {
        if (userId && userId !== "all") params.operator_id = userId;
        if (userId && userId !== "all") params.user_id = userId;
      } else if (userId && userId !== "all") params.user_id = userId;
      const blob = await api.analyticsOutputExport(params);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const label = { operator: "Operator", helper: "Helper", screener: "Screener", design: "Designer" }[tab] || tab;
      a.download = `Laporan_${label}_${resolvedFrom || "from"}_${resolvedTo || "to"}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal export laporan");
    } finally {
      setExporting(false);
    }
  };

  const userFilterLabel = tab === "operator" ? "Semua operator" : tab === "helper" ? "Semua helper" : tab === "screener" ? "Semua screener" : "Semua designer";

  const operatorCols = [
    { key: "tanggal", label: "Tanggal", fit: true, render: (r) => fmtDate(r.tanggal) },
    { key: "operator", label: "Operator" },
    { key: "customer", label: "Outlet / Customer" },
    { key: "no_po", label: "No. PO", mono: true, render: (r) => <PoLink orderId={r.order_id} poNumber={r.no_po} /> },
    { key: "cup_item", label: "Cup / Item" },
    { key: "design_line", label: "Design / Line" },
    { key: "qty_completed", label: "Qty Cetak Selesai", num: true },
  ];
  const helperCols = [
    { key: "tanggal", label: "Tanggal", fit: true, render: (r) => fmtDate(r.tanggal) },
    { key: "helper", label: "Helper" },
    { key: "customer", label: "Outlet / Customer" },
    { key: "no_po", label: "No. PO", mono: true, render: (r) => <PoLink orderId={r.order_id} poNumber={r.no_po} /> },
    { key: "cup_item", label: "Cup / Item" },
    { key: "qty_prepared", label: "Qty Persiapan", num: true },
    { key: "qty_packed", label: "Qty Packing", num: true },
  ];
  const screenerCols = [
    { key: "tanggal", label: "Tanggal", fit: true, render: (r) => fmtDate(r.tanggal) },
    { key: "screener", label: "Screener" },
    { key: "customer", label: "Outlet / Customer" },
    { key: "no_po", label: "No. PO", mono: true, render: (r) => <PoLink orderId={r.order_id} poNumber={r.no_po} /> },
    { key: "cup_item", label: "Cup / Item" },
    { key: "qty_screener", label: "Qty Screener", num: true },
  ];
  const designCols = designerValid ? [
    { key: "tanggal", label: "Tanggal", fit: true, render: (r) => fmtDate(r.tanggal) },
    { key: "designer", label: "Designer" },
    { key: "customer", label: "Outlet / Customer" },
    { key: "no_po", label: "No. PO", mono: true, render: (r) => <PoLink orderId={r.order_id} poNumber={r.no_po} /> },
    { key: "cup_item", label: "Cup / Item" },
    { key: "design_line", label: "Design / Line" },
    { key: "jumlah_design", label: "Jumlah Design", num: true },
  ] : [
    { key: "tanggal", label: "Tanggal", fit: true, render: (r) => fmtDate(r.tanggal) },
    { key: "customer", label: "Outlet / Customer" },
    { key: "no_po", label: "No. PO", mono: true, render: (r) => <PoLink orderId={r.order_id} poNumber={r.no_po} /> },
    { key: "cup_item", label: "Cup / Item" },
    { key: "design_line", label: "Design / Line" },
    { key: "jumlah_design", label: "Jumlah Design", num: true },
  ];

  const cols = tab === "operator" ? operatorCols : tab === "helper" ? helperCols : tab === "screener" ? screenerCols : designCols;
  const cardFields = cols.map((c) => ({ key: c.key, label: c.label, num: c.num, render: c.render }));
  const empty = "Tidak ada aktivitas pada periode / filter ini.";

  return (
    <section className="op-an" data-testid="operator-analytics-panel">
      <div className="op-an-head">
        <div>
          <p className="eyebrow">Laporan · Analitik Output</p>
          <h2 data-testid="op-an-title">Analitik Output</h2>
        </div>
        <div className="op-an-head-actions">
          <button className="ghost sm" onClick={load} disabled={busy} data-testid="op-an-refresh">
            <RefreshCw size={12} className={busy ? "spin" : ""} /> Refresh
          </button>
          <button className="primary sm" onClick={exportData} disabled={exporting || busy} data-testid="op-an-export">
            <Download size={12} /> Export Laporan
          </button>
        </div>
      </div>

      <section className="prod-filters op-an-filters" data-testid="op-an-filters">
        <div className="prod-filter-row">
          <span className="prod-filter-kicker">Periode</span>
          <select value={period} onChange={(e) => setPeriod(e.target.value)} data-testid="op-an-period">
            {PERIOD_OPTS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
          <label className="prod-inline-date">
            Dari tanggal
            <input type="date" value={period === "custom" ? dateFrom : (resolvedFrom || "")} onChange={(e) => { setPeriod("custom"); setDateFrom(e.target.value); }} data-testid="op-an-date-from" />
          </label>
          <label className="prod-inline-date">
            Sampai tanggal
            <input type="date" value={period === "custom" ? dateTo : (resolvedTo || "")} onChange={(e) => { setPeriod("custom"); setDateTo(e.target.value); }} data-testid="op-an-date-to" />
          </label>
        </div>
        <div className="prod-filter-row">
          <span className="prod-filter-kicker">Filter</span>
          <select value={userId} onChange={(e) => setUserId(e.target.value)} data-testid="op-an-user">
            <option value="all">{userFilterLabel}</option>
            {userOptions.map((o) => <option key={o.id || o.email} value={o.id || o.email}>{o.name || o.id}</option>)}
          </select>
          <select value={customer} onChange={(e) => setCustomer(e.target.value)} data-testid="op-an-customer">
            <option value="all">Semua outlet / customer</option>
            {customers.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <FacetSearchSelect
            testId="op-an-item"
            options={items}
            value={item === "all" ? "" : item}
            onChange={(next) => setItem(next ? next : "all")}
            emptyValue=""
            emptyLabel="Semua item / cup"
            placeholder="Cari item / cup…"
            getOptionValue={(it) => String(it)}
            getOptionLabel={(it) => String(it)}
            getSearchText={(it) => String(it)}
          />
          <select value={po} onChange={(e) => setPo(e.target.value)} data-testid="op-an-po">
            <option value="all">Semua PO</option>
            {pos.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <label className="op-an-search">
            <Search size={12} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cari nama, PO, outlet, item" data-testid="op-an-search" />
          </label>
          {!customDatesReady && <span className="muted small">Isi tanggal dari &amp; sampai untuk Custom.</span>}
        </div>
      </section>

      <div className="op-an-subnav" role="tablist" data-testid="op-an-subnav">
        {tabs.map((s) => (
          <button
            key={s.key}
            type="button"
            role="tab"
            aria-selected={tab === s.key}
            className={tab === s.key ? "active" : ""}
            onClick={() => setTab(s.key)}
            data-testid={`op-an-tab-${s.key}`}
          >
            {s.label}
          </button>
        ))}
      </div>

      {err && <div className="error" data-testid="op-an-error">{err}</div>}

      {tab === "design" && data?.designer_attribution && !data.designer_attribution.valid && (
        <div className="op-an-limit" data-testid="op-an-design-limitation">
          <strong>DESIGNER ATTRIBUTION: DATA LIMITATION</strong>
          <p>Source historis tidak mempunyai user Designer yang valid pada file design. Jumlah Design / Line Cetak tetap ditampilkan, tanpa nama karyawan.</p>
        </div>
      )}

      {tab === "operator" && (
        <div className="op-an-kpi-row op-an-kpi-row-5" data-testid="op-an-kpis">
          <KpiCard label="Total Output" value={`${fmtQty(kpis.total_completed)} pcs`} testid="op-an-kpi-completed" />
          <KpiCard label="Avg / Hari Aktif" value={`${fmtQty(kpis.avg_daily_qty)} pcs`} hint={`${kpis.active_days || 0} hari aktif`} testid="op-an-kpi-daily" />
          <KpiCard label="Jumlah Operator" value={fmtQty(kpis.operator_count)} testid="op-an-kpi-ops" />
          <KpiCard label="Jumlah PO" value={fmtQty(kpis.po_count || kpis.total_jobs)} testid="op-an-kpi-jobs" />
          <KpiCard label="Jumlah Design / Line" value={fmtQty(kpis.total_designs)} testid="op-an-kpi-designs" />
        </div>
      )}
      {tab === "helper" && (
        <div className="op-an-kpi-row" data-testid="op-an-prep-kpis">
          <KpiCard label="Total Qty Persiapan" value={`${fmtQty(kpis.total_qty)} pcs`} testid="op-an-prep-total" />
          <KpiCard label="Total Qty Packing" value={`${fmtQty(kpis.total_qty_packing)} pcs`} testid="op-an-prep-pack" />
          <KpiCard label="Jumlah Helper" value={fmtQty(kpis.user_count)} testid="op-an-prep-users" />
          <KpiCard label="Jumlah PO" value={fmtQty(kpis.po_count)} testid="op-an-prep-po" />
          <KpiCard label="Jumlah Item" value={fmtQty(kpis.item_count || kpis.line_count)} testid="op-an-prep-line" />
        </div>
      )}
      {tab === "screener" && (
        <div className="op-an-kpi-row" data-testid="op-an-screen-kpis">
          <KpiCard label="Total Qty Screener" value={fmtQty(kpis.total_qty)} testid="op-an-screen-total" />
          <KpiCard label="Jumlah Screener" value={fmtQty(kpis.user_count)} testid="op-an-screen-users" />
          <KpiCard label="Jumlah PO" value={fmtQty(kpis.po_count)} testid="op-an-screen-po" />
          <KpiCard label="Jumlah Item" value={fmtQty(kpis.item_count || kpis.line_count)} testid="op-an-screen-line" />
          <KpiCard label="Avg / Hari Aktif" value={fmtQty(kpis.avg_daily_qty)} testid="op-an-screen-avg" />
        </div>
      )}
      {tab === "design" && (
        <div className="op-an-kpi-row op-an-kpi-row-4" data-testid="op-an-design-kpis">
          <KpiCard label="Total Design" value={fmtQty(kpis.total_designs)} testid="op-an-design-total" />
          <KpiCard label="Jumlah Designer" value={fmtQty(kpis.designer_count)} testid="op-an-design-ops" />
          <KpiCard label="Jumlah PO" value={fmtQty(kpis.po_count)} testid="op-an-design-po" />
          <KpiCard label="Jumlah Item" value={fmtQty(kpis.item_count)} testid="op-an-design-item" />
        </div>
      )}

      {!!people.length && (
        <>
          <div className="op-an-section-label">
            {tab === "operator" ? "Summary per Operator" : tab === "helper" ? "Summary per Helper" : tab === "screener" ? "Summary per Screener" : "Summary per Designer"}
            <span className="muted"> · klik nama untuk filter</span>
          </div>
          {tab === "operator" && (
            <PeopleTable
              testid="op-an-people"
              rows={people}
              activeId={userId}
              onPick={(id) => setUserId(userId === id ? "all" : id)}
              columns={[
                { key: "name", label: "Operator" },
                { key: "qty", label: "Total Qty Cetak", num: true },
                { key: "po_count", label: "Jumlah PO", num: true },
                { key: "item_count", label: "Jumlah Cup/Item", num: true },
                { key: "design_count", label: "Jumlah Design", num: true },
              ]}
            />
          )}
          {tab === "helper" && (
            <PeopleTable
              testid="op-an-prep-people"
              rows={people}
              activeId={userId}
              onPick={(id) => setUserId(userId === id ? "all" : id)}
              columns={[
                { key: "name", label: "Helper" },
                { key: "qty", label: "Qty Persiapan", num: true },
                { key: "packing", label: "Qty Packing", num: true },
                { key: "po_count", label: "Jumlah PO", num: true },
                { key: "item_count", label: "Jumlah Item", num: true },
              ]}
            />
          )}
          {tab === "screener" && (
            <PeopleTable
              testid="op-an-screen-people"
              rows={people}
              activeId={userId}
              onPick={(id) => setUserId(userId === id ? "all" : id)}
              columns={[
                { key: "name", label: "Screener" },
                { key: "qty", label: "Total Qty", num: true },
                { key: "po_count", label: "Jumlah PO", num: true },
                { key: "item_count", label: "Jumlah Item", num: true },
                { key: "avg_daily_qty", label: "Avg/Hari", num: true },
              ]}
            />
          )}
          {tab === "design" && designerValid && (
            <PeopleTable
              testid="op-an-design-people"
              rows={people.filter((p) => p.email !== "__unattributed__")}
              activeId={userId}
              onPick={(id) => setUserId(userId === id ? "all" : id)}
              columns={[
                { key: "name", label: "Designer" },
                { key: "design_count", label: "Jumlah Design", num: true },
                { key: "po_count", label: "Jumlah PO", num: true },
                { key: "item_count", label: "Jumlah Item", num: true },
              ]}
            />
          )}
        </>
      )}

      <div className="op-an-section-label">Laporan {tabs.find((t) => t.key === tab)?.label}</div>
      <ReportTable
        columns={cols}
        rows={paged}
        testid={tab === "operator" ? "op-an-table" : tab === "helper" ? "op-an-prep-table" : tab === "screener" ? "op-an-screen-table" : "op-an-design-table"}
        empty={busy ? "Memuat…" : empty}
        sortKey={sortKey}
        sortDir={sortDir}
        onSort={onSort}
      />
      <ReportCards
        rows={paged}
        fields={cardFields}
        testid={tab === "operator" ? "op-an-cards" : tab === "helper" ? "op-an-prep-cards" : tab === "screener" ? "op-an-screen-cards" : "op-an-design-line-cards"}
      />
      <Pager page={page} pageSize={pageSize} total={filtered.length} onPage={setPage} onSize={(n) => { setPageSize(n); setPage(1); }} />

      <div className="op-an-chart op-an-chart-compact" data-testid="op-an-week-chart">
        <h3>{tab === "design" ? "Design per minggu" : "Output mingguan"}</h3>
        <WeekBars weeks={chartBuckets} testid="op-an-weekbars" valueKey={tab === "design" ? "design_count" : "qty"} />
      </div>

      <p className="muted small op-an-note">
        <BarChart3 size={12} style={{ verticalAlign: "-2px" }} /> Qty = aktivitas aktual (delta), bukan qty PO / snapshot kumulatif.
        Packing tanpa actor yang sama = N/A. Minggu = ISO Senin–Minggu (WIB).
        {resolvedFrom && <span data-testid="op-an-period-range"> · {resolvedFrom} → {resolvedTo}</span>}
      </p>
    </section>
  );
}
