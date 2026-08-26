import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Download, Filter, LayoutGrid, RefreshCw, Search, Table as TableIcon, XCircle } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import useAutoRefresh from "../lib/useAutoRefresh";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import {
  FILTER_DEFAULTS,
  applyProductionFilters,
  productionFiltersAreActive,
  sortProductionRows,
} from "../lib/productionScheduleFilters";
import { formatIdQty, scheduledPrintQtyProgress, totalQtyScheduledCell } from "../lib/productionScheduleQty";
import { schedulePoHref } from "../lib/workPanel";

const STATUS_OPTS = [
  { key: "all", label: "Semua status" },
  { key: "aktif", label: "Aktif (non-HOLD)" },
  { key: "hold", label: "HOLD" },
  { key: "selesai", label: "Selesai" },
  { key: "cancelled", label: "Cancelled" },
];

const TABLE_COLS = [
  { key: "deadline", label: "Deadline", fit: true, filter: "deadline", sort: "deadline" },
  { key: "deadline_ship", label: "Deadline Pengiriman", testid: "prod-th-deadline-effective" },
  { key: "work_date", label: "Tgl Pengerjaan", testid: "prod-th-work-date", filter: "work_date", sort: "work_date" },
  { key: "prio", label: "Prio", fit: true, filter: "prio" },
  { key: "buffer", label: "Buffer", fit: true, testid: "prod-th-buffer", filter: "buffer" },
  { key: "po", label: "No PO", fit: true, filter: "po" },
  { key: "customer", label: "Customer", filter: "customer" },
  { key: "outlet", label: "Outlet" },
  { key: "qty", label: "Total Qty", num: true },
  { key: "qty_cetak", label: "Qty Cetak", num: true },
  { key: "operator", label: "Operator", filter: "operator" },
  { key: "design", label: "Design", fit: true },
  { key: "payment", label: "Payment", fit: true },
  { key: "hold", label: "HOLD", fit: true, filter: "hold" },
  { key: "screen", label: "Qty Screen", num: true, filter: "screener" },
  { key: "helper", label: "Persiapan", fit: true, filter: "helper" },
  { key: "packing", label: "Packing", fit: true, filter: "packing" },
  { key: "sj", label: "Surat Jalan", fit: true },
  { key: "loading", label: "Loading", fit: true },
  { key: "driver", label: "Driver" },
  { key: "status", label: "Status Akhir", fit: true, filter: "status" },
];

function columnFilterActive(filter, s) {
  switch (filter) {
    case "deadline": return !!(s.dateFrom || s.dateTo);
    case "work_date": return s.workDateFilter !== "all" || !!s.workDateFrom || !!s.workDateTo;
    case "prio": return !!s.urgentOnly;
    case "buffer": return s.bufferFilter !== "all";
    case "po": return !!String(s.poFilter || "").trim();
    case "customer": return s.customerFilter !== "all";
    case "operator": return s.operatorFilter !== "all";
    case "hold": return !!s.holdOnly;
    case "screener": return s.screenerFilter !== "all";
    case "helper": return s.helperFilter !== "all";
    case "packing": return s.packingFilter !== "all";
    case "status": return s.statusFilter !== "all";
    default: return false;
  }
}

function HeaderFilter({ label, testid, active, sortable, sortHint, onSort, onClear, children }) {
  return (
    <div className="prod-th-inner">
      {sortable ? (
        <button type="button" className="prod-th-label is-sort" onClick={onSort} title={sortHint || "Urutkan"}>
          {label}
        </button>
      ) : (
        <span className="prod-th-label">{label}</span>
      )}
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className={"prod-th-filter" + (active ? " is-active" : "")}
            aria-label={`Filter ${label}`}
            data-testid={testid}
            onClick={(e) => e.stopPropagation()}
          >
            <Filter size={11} />
            {active && <span className="prod-th-dot" />}
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="prod-th-popover" onClick={(e) => e.stopPropagation()}>
          <div className="prod-th-pop-label">{label}</div>
          {children}
          {active && (
            <button type="button" className="ghost sm prod-th-clear" onClick={onClear} data-testid={`${testid}-clear`}>
              Hapus filter
            </button>
          )}
        </PopoverContent>
      </Popover>
    </div>
  );
}

export default function ProductionSchedule() {
  const { hasPerm } = useAuth();
  const canExport = hasPerm("dashboard.production.export");
  const [mode, setMode] = useState("tabel"); // 'kartu' | 'tabel'
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState(FILTER_DEFAULTS.q);
  const [poFilter, setPoFilter] = useState(FILTER_DEFAULTS.poFilter);
  const [statusFilter, setStatusFilter] = useState(FILTER_DEFAULTS.statusFilter);
  const [operatorFilter, setOperatorFilter] = useState(FILTER_DEFAULTS.operatorFilter);
  const [customerFilter, setCustomerFilter] = useState(FILTER_DEFAULTS.customerFilter);
  const [urgentOnly, setUrgentOnly] = useState(FILTER_DEFAULTS.urgentOnly);
  const [holdOnly, setHoldOnly] = useState(FILTER_DEFAULTS.holdOnly);
  const [screenerFilter, setScreenerFilter] = useState(FILTER_DEFAULTS.screenerFilter);
  const [helperFilter, setHelperFilter] = useState(FILTER_DEFAULTS.helperFilter);
  const [packingFilter, setPackingFilter] = useState(FILTER_DEFAULTS.packingFilter);
  const [deliveryFilter, setDeliveryFilter] = useState(FILTER_DEFAULTS.deliveryFilter);
  const [bufferFilter, setBufferFilter] = useState(FILTER_DEFAULTS.bufferFilter);
  const [dateFrom, setDateFrom] = useState(FILTER_DEFAULTS.dateFrom);
  const [dateTo, setDateTo] = useState(FILTER_DEFAULTS.dateTo);
  const [workDateFilter, setWorkDateFilter] = useState(FILTER_DEFAULTS.workDateFilter);
  const [workDateFrom, setWorkDateFrom] = useState(FILTER_DEFAULTS.workDateFrom);
  const [workDateTo, setWorkDateTo] = useState(FILTER_DEFAULTS.workDateTo);
  const [sortBy, setSortBy] = useState(FILTER_DEFAULTS.sortBy);
  const [includeCancelled, setIncludeCancelled] = useState(FILTER_DEFAULTS.includeCancelled);
  const [includeCompleted, setIncludeCompleted] = useState(FILTER_DEFAULTS.includeCompleted);
  const [exporting, setExporting] = useState(false);

  const load = async () => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.productionDashboard({ include_cancelled: includeCancelled, include_completed: includeCompleted });
      setRows(d.rows || []);
      setTotal(d.total || 0);
    } catch (e) { setErr(e.response?.data?.detail || "Gagal memuat"); }
    finally { setBusy(false); }
  };

  const { lastUpdated: schedLastUpdated, isPolling: schedIsPolling, refreshNow: refreshSched } = useAutoRefresh(
    () => api.productionDashboard({ include_cancelled: includeCancelled, include_completed: includeCompleted }),
    { intervalMs: 30_000, enabled: true, onData: (d) => { setRows(d.rows || []); setTotal(d.total || 0); } }
  );
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [includeCancelled, includeCompleted]);

  useEffect(() => {
    if (statusFilter === "selesai" && !includeCompleted) setIncludeCompleted(true);
    if (statusFilter === "cancelled" && !includeCancelled) setIncludeCancelled(true);
  }, [statusFilter, includeCompleted, includeCancelled]);

  const operators = useMemo(() => {
    const s = new Set();
    rows.forEach((r) => (r.operators || []).forEach((op) => op.operator_name && s.add(op.operator_name)));
    return ["all", ...Array.from(s).sort()];
  }, [rows]);
  const customers = useMemo(() => {
    const s = new Set();
    rows.forEach((r) => r.customer && s.add(r.customer));
    return ["all", ...Array.from(s).sort()];
  }, [rows]);

  const resetFilters = () => {
    setQ(FILTER_DEFAULTS.q);
    setPoFilter(FILTER_DEFAULTS.poFilter);
    setStatusFilter(FILTER_DEFAULTS.statusFilter);
    setOperatorFilter(FILTER_DEFAULTS.operatorFilter);
    setCustomerFilter(FILTER_DEFAULTS.customerFilter);
    setUrgentOnly(FILTER_DEFAULTS.urgentOnly);
    setHoldOnly(FILTER_DEFAULTS.holdOnly);
    setScreenerFilter(FILTER_DEFAULTS.screenerFilter);
    setHelperFilter(FILTER_DEFAULTS.helperFilter);
    setPackingFilter(FILTER_DEFAULTS.packingFilter);
    setDeliveryFilter(FILTER_DEFAULTS.deliveryFilter);
    setBufferFilter(FILTER_DEFAULTS.bufferFilter);
    setDateFrom(FILTER_DEFAULTS.dateFrom);
    setDateTo(FILTER_DEFAULTS.dateTo);
    setWorkDateFilter(FILTER_DEFAULTS.workDateFilter);
    setWorkDateFrom(FILTER_DEFAULTS.workDateFrom);
    setWorkDateTo(FILTER_DEFAULTS.workDateTo);
    setSortBy(FILTER_DEFAULTS.sortBy);
    setIncludeCancelled(FILTER_DEFAULTS.includeCancelled);
    setIncludeCompleted(FILTER_DEFAULTS.includeCompleted);
  };

  const filterState = {
    q, poFilter, statusFilter, operatorFilter, customerFilter, urgentOnly, holdOnly,
    screenerFilter, helperFilter, packingFilter, deliveryFilter, bufferFilter,
    dateFrom, dateTo, workDateFilter, workDateFrom, workDateTo, sortBy,
    includeCancelled, includeCompleted,
  };

  const filtered = useMemo(
    () => applyProductionFilters(rows, filterState),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, q, poFilter, statusFilter, operatorFilter, customerFilter, urgentOnly, holdOnly,
      screenerFilter, helperFilter, packingFilter, deliveryFilter, bufferFilter,
      dateFrom, dateTo, workDateFilter, workDateFrom, workDateTo,
      includeCancelled, includeCompleted]
  );

  const sorted = useMemo(() => sortProductionRows(filtered, sortBy), [filtered, sortBy]);
  const filtersActive = productionFiltersAreActive(filterState);

  const cycleSort = (col) => {
    if (col === "deadline") setSortBy("deadline");
    else if (col === "work_date") setSortBy((prev) => (prev === "work_date_asc" ? "work_date" : "work_date_asc"));
  };

  const download = async (scope) => {
    setExporting(true);
    try {
      const payload = { scope };
      if (scope === "filtered") payload.ids = filtered.map((r) => r.id);
      payload.include_cancelled = includeCancelled;
      payload.include_completed = includeCompleted;
      const blob = await api.productionExport(payload);
      const url = URL.createObjectURL(new Blob([blob], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `jadwal-produksi-${new Date().toISOString().slice(0,10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(e.response?.data?.detail || "Gagal export Excel");
    } finally { setExporting(false); }
  };

  const renderColumnFilter = (c) => {
    switch (c.filter) {
      case "po":
        return (
          <input
            placeholder="Cari No PO"
            value={poFilter}
            onChange={(e) => setPoFilter(e.target.value)}
            data-testid="prod-filter-po"
          />
        );
      case "customer":
        return (
          <select value={customerFilter} onChange={(e) => setCustomerFilter(e.target.value)} data-testid="prod-filter-customer">
            {customers.map((cust) => <option key={cust} value={cust}>{cust === "all" ? "Semua customer" : cust}</option>)}
          </select>
        );
      case "operator":
        return (
          <select value={operatorFilter} onChange={(e) => setOperatorFilter(e.target.value)} data-testid="prod-filter-operator">
            {operators.map((o) => <option key={o} value={o}>{o === "all" ? "Semua operator" : o}</option>)}
          </select>
        );
      case "status":
        return (
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} data-testid="prod-filter-status">
            {STATUS_OPTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        );
      case "deadline":
        return (
          <>
            <label>Dari <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} data-testid="prod-date-from"/></label>
            <label>Sampai <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} data-testid="prod-date-to"/></label>
          </>
        );
      case "work_date":
        return (
          <>
            <select value={workDateFilter} onChange={(e) => setWorkDateFilter(e.target.value)} data-testid="prod-workdate-filter">
              <option value="all">Semua Pengerjaan</option>
              <option value="scheduled">Sudah Dijadwalkan</option>
              <option value="unscheduled">Belum Dijadwalkan</option>
            </select>
            <label>Dari <input type="date" value={workDateFrom} onChange={(e) => setWorkDateFrom(e.target.value)} data-testid="prod-workdate-from"/></label>
            <label>Sampai <input type="date" value={workDateTo} onChange={(e) => setWorkDateTo(e.target.value)} data-testid="prod-workdate-to"/></label>
          </>
        );
      case "prio":
        return (
          <label className="prod-check">
            <input type="checkbox" checked={urgentOnly} onChange={(e) => setUrgentOnly(e.target.checked)} data-testid="prod-filter-urgent"/> Urgent saja
          </label>
        );
      case "hold":
        return (
          <label className="prod-check">
            <input type="checkbox" checked={holdOnly} onChange={(e) => setHoldOnly(e.target.checked)} data-testid="prod-filter-hold"/> HOLD saja
          </label>
        );
      case "buffer":
        return (
          <select value={bufferFilter} onChange={(e) => setBufferFilter(e.target.value)} data-testid="prod-filter-buffer">
            <option value="all">Semua Buffer Stock</option>
            <option value="yes">Buffer Stock</option>
            <option value="no">Non Buffer Stock</option>
          </select>
        );
      case "screener":
        return (
          <select value={screenerFilter} onChange={(e) => setScreenerFilter(e.target.value)} data-testid="prod-filter-screener">
            <option value="all">Semua Screener</option>
            <option value="yes">Ada Qty Screen</option>
            <option value="no">Belum Screener</option>
          </select>
        );
      case "helper":
        return (
          <select value={helperFilter} onChange={(e) => setHelperFilter(e.target.value)} data-testid="prod-filter-helper">
            <option value="all">Semua Helper</option>
            <option value="ok">Barang Cukup</option>
            <option value="kurang">Barang Kurang</option>
            <option value="none">Belum diinput</option>
          </select>
        );
      case "packing":
        return (
          <select value={packingFilter} onChange={(e) => setPackingFilter(e.target.value)} data-testid="prod-filter-packing">
            <option value="all">Semua Packing</option>
            <option value="Sudah">Sudah Packing</option>
            <option value="Berjalan">Packing Berjalan</option>
            <option value="Belum">Belum Packing</option>
          </select>
        );
      default:
        return null;
    }
  };

  const clearColumn = (filter) => {
    switch (filter) {
      case "po": setPoFilter(FILTER_DEFAULTS.poFilter); break;
      case "customer": setCustomerFilter(FILTER_DEFAULTS.customerFilter); break;
      case "operator": setOperatorFilter(FILTER_DEFAULTS.operatorFilter); break;
      case "status": setStatusFilter(FILTER_DEFAULTS.statusFilter); break;
      case "deadline": setDateFrom(FILTER_DEFAULTS.dateFrom); setDateTo(FILTER_DEFAULTS.dateTo); break;
      case "work_date":
        setWorkDateFilter(FILTER_DEFAULTS.workDateFilter);
        setWorkDateFrom(FILTER_DEFAULTS.workDateFrom);
        setWorkDateTo(FILTER_DEFAULTS.workDateTo);
        break;
      case "prio": setUrgentOnly(FILTER_DEFAULTS.urgentOnly); break;
      case "hold": setHoldOnly(FILTER_DEFAULTS.holdOnly); break;
      case "buffer": setBufferFilter(FILTER_DEFAULTS.bufferFilter); break;
      case "screener": setScreenerFilter(FILTER_DEFAULTS.screenerFilter); break;
      case "helper": setHelperFilter(FILTER_DEFAULTS.helperFilter); break;
      case "packing": setPackingFilter(FILTER_DEFAULTS.packingFilter); break;
      default: break;
    }
  };

  return (
    <div className="prod-page prod-schedule-page" data-testid="production-schedule-page">
      <div className="prod-schedule-chrome">
        <div className="page-heading prod-heading">
          <div className="prod-heading-copy">
            <p className="eyebrow">Produksi · Jadwal</p>
            <h1>Jadwal Produksi</h1>
            <p className="subhead">
              {total} PO real-time · sumber MongoDB · sortir, filter, dan export satu arah
              {schedLastUpdated && (
                <span data-testid="prod-last-updated" style={{ marginLeft: 10, fontSize: 12, color: "#7a6f52" }}>
                  · Terakhir diperbarui: {new Date(schedLastUpdated).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}
                </span>
              )}
            </p>
          </div>
          <div className="prod-mode" role="tablist" data-testid="prod-mode-switch">
            <button className="ghost sm" onClick={refreshSched} disabled={schedIsPolling} data-testid="prod-refresh-button" title="Refresh sekarang">
              <RefreshCw size={12} className={schedIsPolling ? "spin" : ""}/> Refresh
            </button>
            <button role="tab" className={mode === "kartu" ? "active" : ""} onClick={() => setMode("kartu")} data-testid="prod-mode-kartu"><LayoutGrid size={13}/> Kartu</button>
            <button role="tab" className={mode === "tabel" ? "active" : ""} onClick={() => setMode("tabel")} data-testid="prod-mode-tabel"><TableIcon size={13}/> Tabel</button>
          </div>
        </div>

        <div className="prod-toolbar" data-testid="prod-filters">
          <div className="search-inline">
            <Search size={13}/>
            <input placeholder="Cari PO / Customer / Outlet" value={q} onChange={(e) => setQ(e.target.value)} data-testid="prod-search"/>
          </div>
          <span className="muted small" data-testid="prod-total-visible">{filtered.length} / {rows.length} entri</span>
          <label className="prod-check"><input type="checkbox" checked={includeCompleted} onChange={(e) => setIncludeCompleted(e.target.checked)} data-testid="prod-include-completed"/> Selesai</label>
          <label className="prod-check"><input type="checkbox" checked={includeCancelled} onChange={(e) => setIncludeCancelled(e.target.checked)} data-testid="prod-include-cancelled"/> Cancelled</label>
          <button className="ghost sm prod-filter-reset" onClick={resetFilters} data-testid="prod-filter-reset" disabled={!filtersActive && sortBy === FILTER_DEFAULTS.sortBy}>
            Reset Filter
          </button>
          {canExport && (
            <>
              <button className="ghost sm" disabled={exporting || !filtered.length} onClick={() => download("filtered")} data-testid="prod-export-filtered"><Download size={12}/> Export Terfilter</button>
              <button className="ghost sm" disabled={exporting} onClick={() => download("all")} data-testid="prod-export-all"><Download size={12}/> Export Semua</button>
            </>
          )}
        </div>

        {err && <div className="error" data-testid="prod-error">{err}</div>}
      </div>

      {mode === "tabel" ? (
        <div className="prod-table-wrap prod-schedule-grid" data-testid="prod-table">
          <table className="prod-table">
            <thead>
              <tr>
                {TABLE_COLS.map((c) => {
                  const active = columnFilterActive(c.filter, filterState);
                  return (
                    <th key={c.key} className={[c.num ? "num" : "", (c.num || c.fit) ? "col-fit" : "col-text"].filter(Boolean).join(" ")} data-testid={c.testid} scope="col">
                      {c.filter ? (
                        <HeaderFilter
                          label={c.label}
                          testid={`prod-th-filter-${c.key}`}
                          active={active}
                          sortable={!!c.sort}
                          sortHint={c.sort === "work_date" ? "Urutkan tanggal pengerjaan" : "Urutkan deadline"}
                          onSort={() => cycleSort(c.sort)}
                          onClear={() => clearColumn(c.filter)}
                        >
                          {renderColumnFilter(c)}
                        </HeaderFilter>
                      ) : (
                        <span className="prod-th-label">{c.label}</span>
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => {
                const qtyProg = totalQtyScheduledCell(r);
                const cetakProg = scheduledPrintQtyProgress(r);
                return (
                <tr key={r.id} data-testid={`prod-row-${r.po_number}`} className={r.hold ? "hold" : (r.cancelled ? "cancelled" : "")}>
                  <td className="mono small col-fit">{formatDate(r.deadline)}</td>
                  <td className="mono small prod-cell-stack col-text" data-testid={`prod-deadline-effective-${r.po_number}`}>
                    {r.delivery_deadline_effective ? (
                      <>
                        {formatDate(r.delivery_deadline_effective)}
                        {" "}
                        <span
                          className="pill sm"
                          title={r.delivery_deadline_source === "manual" ? "Manual override" : "Otomatis"}
                          style={{
                            fontSize: 10,
                            padding: "1px 5px",
                            background: r.delivery_deadline_source === "manual" ? "#ffe9b8" : "#e6ecf3",
                            color: r.delivery_deadline_source === "manual" ? "#7a5a1e" : "#3a4b60",
                          }}
                          data-testid={`prod-deadline-source-${r.po_number}`}
                        >
                          {r.delivery_deadline_source === "manual" ? "Manual" : "Otomatis"}
                        </span>
                        {r.delivery_deadline_status === "overdue" && (
                          <span className="urgent-badge" style={{ marginLeft: 4 }} data-testid={`prod-deadline-status-${r.po_number}`}>OVERDUE</span>
                        )}
                        {r.delivery_deadline_status === "h6" && (
                          <span className="urgent-badge" style={{ marginLeft: 4 }} data-testid={`prod-deadline-status-${r.po_number}`}>≤6 JAM</span>
                        )}
                        {r.delivery_deadline_status === "h1" && (
                          <span className="standard-badge" style={{ marginLeft: 4, background: "#fef4d6", color: "#7a5a1e" }} data-testid={`prod-deadline-status-${r.po_number}`}>H-1</span>
                        )}
                      </>
                    ) : (
                      <span className="muted small">Menunggu approval</span>
                    )}
                  </td>
                  <td className="mono small prod-cell-stack col-text" data-testid={`prod-work-date-${r.po_number}`}>
                    {(r.operator_work_dates || []).length > 0 ? (
                      (r.operator_work_dates || []).map((o, i) => (
                        <div key={i} className="prod-date-line" data-testid={`prod-op-work-date-${r.po_number}-${i}`}>
                          {o.work_date || "—"}
                        </div>
                      ))
                    ) : r.work_date ? (
                      <span data-testid={`prod-order-work-date-${r.po_number}`}>{r.work_date}</span>
                    ) : (
                      <span className="muted small">Belum Dijadwalkan</span>
                    )}
                  </td>
                  <td className="col-fit">{r.urgent ? <span className="urgent-badge">URGENT</span> : <span className="muted small">Normal</span>}</td>
                  <td className="col-fit" data-testid={`prod-buffer-${r.po_number}`}>
                    {r.buffer_stock ? <span className="pill sm" style={{ background: "#e6ecf3", color: "#3a4b60" }}>Buffer</span> : <span className="muted small">—</span>}
                  </td>
                  <td className="mono col-fit"><Link to={schedulePoHref(r.id, hasPerm)} data-testid={`prod-po-link-${r.po_number}`}>{r.po_number}</Link></td>
                  <td className="prod-cell-wrap col-text"><strong>{r.customer || "-"}</strong></td>
                  <td className="muted small prod-cell-wrap col-text">{r.outlet || "-"}</td>
                  <td
                    className="num col-fit prod-qty-cell"
                    data-testid={`prod-qty-${r.po_number}`}
                    title={qtyProg.title}
                  >
                    {qtyProg.groups?.length ? (
                      qtyProg.groups.map((g, i) => (
                        <div
                          key={g.key}
                          className="prod-line-qty-block"
                          style={{ minHeight: `${Math.max((g.operators || []).length, 1) * 1.35}em` }}
                          data-testid={`prod-qty-line-${r.po_number}-${i}`}
                        >
                          {g.line_name ? (
                            <span className="prod-line-qty-name">{g.line_name}</span>
                          ) : null}
                          <span className="prod-line-qty-value">{formatIdQty(g.total)}</span>
                        </div>
                      ))
                    ) : qtyProg.text}
                  </td>
                  <td
                    className={"num col-fit" + (cetakProg.over ? " prod-qty-over" : "")}
                    data-testid={`prod-qty-cetak-${r.po_number}`}
                    title={cetakProg.title}
                  >
                    {cetakProg.text}
                  </td>
                  <td className="prod-ops col-text">
                    {qtyProg.groups?.length ? (
                      qtyProg.groups.map((g) => (
                        <div
                          key={g.key}
                          className="prod-line-ops-block"
                          style={{ minHeight: `${Math.max((g.operators || []).length, 1) * 1.35}em` }}
                          data-testid={`prod-ops-line-${r.po_number}-${g.key}`}
                        >
                          {(g.operators || []).map((op, i) => (
                            <div key={`${op.operator_email || op.operator_user_id || i}`} className="prod-op-line">
                              <span className="prod-op-name">{op.operator_name || "-"}</span>
                              <span className="prod-op-qty muted small mono">{formatIdQty(op.quantity)}</span>
                            </div>
                          ))}
                        </div>
                      ))
                    ) : (
                      (r.operators || []).length === 0 ? <span className="muted small">-</span> :
                      (r.operators || []).map((op, i) => (
                        <div key={i} className="prod-op-line">
                          <span className="prod-op-name">{op.operator_name || "-"}</span>
                        </div>
                      ))
                    )}
                  </td>
                  <td className="col-fit">
                    <span className={"pill sm " + ((r.design_status === "Approved") ? "pill-ok" : "pill-warn")}>{r.design_status || "-"}</span>
                    {r.design_bypassed && <span className="pill sm pill-bypass" title="Bypass">BYPASS</span>}
                  </td>
                  <td className="col-fit">
                    <span className={"pill sm " + ((r.payment_status === "Approved") ? "pill-ok" : "pill-warn")}>{r.payment_status || "-"}</span>
                    {r.payment_bypassed && <span className="pill sm pill-bypass" title="Bypass">BYPASS</span>}
                  </td>
                  <td className="col-fit">{r.hold ? <span className="pill sm pill-hold" title={(r.hold_reasons || []).join("; ")}>HOLD</span> : <span className="muted small">-</span>}</td>
                  <td className="num col-fit">{r.screen_qty ?? "-"}</td>
                  <td className="small col-fit">
                    {r.helper_required != null ? (
                      <span className={r.helper_shortage_status === "Barang Kurang" ? "prod-helper-warn" : ""}>{r.helper_prepared ?? 0}/{r.helper_required} {r.helper_shortage ? `(-${r.helper_shortage})` : ""}</span>
                    ) : <span className="muted">-</span>}
                  </td>
                  <td className="col-fit" data-testid={`prod-packing-${r.po_number}`}>
                    {r.packing_status === "Sudah" ? <span className="pill sm pill-ok">Sudah</span> :
                     r.packing_status === "Berjalan" ? <span className="pill sm">Berjalan</span> :
                     <span className="pill sm">Belum</span>}
                  </td>
                  <td className="mono small col-fit">{r.delivery_note_number || "-"}</td>
                  <td className="col-fit">{r.loading_ok ? <span className="pill sm pill-ok">OK</span> : <span className="muted small">-</span>}</td>
                  <td className="small col-text">{r.driver_name || "-"}</td>
                  <td className="col-fit">
                    {r.cancelled ? <span className="pill sm pill-cancel"><XCircle size={10}/> Cancelled</span> :
                     r.status_final === "HOLD" ? <span className="pill sm pill-hold">HOLD</span> :
                     r.status_final === "Terkirim" ? <span className="pill sm pill-ok">Terkirim</span> :
                     <span className="pill sm">{r.status_final}</span>}
                  </td>
                </tr>
                );
              })}
              {!sorted.length && !busy && (
                <tr><td colSpan={TABLE_COLS.length} className="empty-state">Tidak ada PO cocok dengan filter.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <section className="prod-cards prod-schedule-cards" data-testid="prod-cards">
          {sorted.map((r) => (
            <Link key={r.id} to={schedulePoHref(r.id, hasPerm)} className="prod-card" data-testid={`prod-card-${r.po_number}`}>
              <header>
                <strong>{r.po_number}</strong>
                {r.urgent && <span className="urgent-badge">URGENT</span>}
                {r.buffer_stock && <span className="pill sm" style={{ background: "#e6ecf3", color: "#3a4b60" }}>Buffer</span>}
                {r.hold && <span className="pill sm pill-hold">HOLD</span>}
                {r.cancelled && <span className="pill sm pill-cancel">Cancelled</span>}
              </header>
              <p><b>{r.customer}</b> · {r.outlet || "-"}</p>
              <p className="muted small">Deadline: {formatDate(r.deadline)} · Total: {(r.total_quantity || 0).toLocaleString("id-ID")} pcs</p>
              <div className="prod-card-tags">
                <span className={"pill sm " + ((r.design_status === "Approved") ? "pill-ok" : "pill-warn")}>Design {r.design_status || "-"}</span>
                <span className={"pill sm " + ((r.payment_status === "Approved") ? "pill-ok" : "pill-warn")}>Payment {r.payment_status || "-"}</span>
                {r.packing_status === "Sudah" && <span className="pill sm pill-ok">Packed</span>}
                {r.packing_status === "Berjalan" && <span className="pill sm">Packing</span>}
              </div>
            </Link>
          ))}
          {!sorted.length && !busy && <div className="empty-state" data-testid="prod-empty">Tidak ada PO cocok dengan filter.</div>}
        </section>
      )}
    </div>
  );
}
