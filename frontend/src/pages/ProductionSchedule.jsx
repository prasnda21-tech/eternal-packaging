import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Download, LayoutGrid, RefreshCw, Search, Table as TableIcon, XCircle } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import useAutoRefresh from "../lib/useAutoRefresh";
import {
  FILTER_DEFAULTS,
  applyProductionFilters,
  sortProductionRows,
} from "../lib/productionScheduleFilters";

const STATUS_OPTS = [
  { key: "all", label: "Semua status" },
  { key: "aktif", label: "Aktif (non-HOLD)" },
  { key: "hold", label: "HOLD" },
  { key: "selesai", label: "Selesai" },
  { key: "cancelled", label: "Cancelled" },
];

const TABLE_COLS = [
  { key: "deadline", label: "Deadline", width: 118 },
  { key: "deadline_ship", label: "Deadline Pengiriman", width: 196, testid: "prod-th-deadline-effective" },
  { key: "work_date", label: "Tgl Pengerjaan", width: 136, testid: "prod-th-work-date" },
  { key: "prio", label: "Prio", width: 88 },
  { key: "buffer", label: "Buffer", width: 88, testid: "prod-th-buffer" },
  { key: "po", label: "No PO", width: 128 },
  { key: "customer", label: "Customer", width: 220 },
  { key: "outlet", label: "Outlet", width: 168 },
  { key: "qty", label: "Total Qty", width: 96, num: true },
  { key: "operator", label: "Operator", width: 248 },
  { key: "design", label: "Design", width: 118 },
  { key: "payment", label: "Payment", width: 118 },
  { key: "hold", label: "HOLD", width: 80 },
  { key: "screen", label: "Qty Screen", width: 100, num: true },
  { key: "helper", label: "Persiapan", width: 132 },
  { key: "packing", label: "Packing", width: 100 },
  { key: "sj", label: "Surat Jalan", width: 136 },
  { key: "loading", label: "Loading", width: 88 },
  { key: "driver", label: "Driver", width: 132 },
  { key: "status", label: "Status Akhir", width: 136 },
];

export default function ProductionSchedule() {
  const { hasPerm } = useAuth();
  const canExport = hasPerm("dashboard.production.export");
  const [mode, setMode] = useState("tabel"); // 'kartu' | 'tabel'
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState(FILTER_DEFAULTS.q);
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

  // Status "Selesai" membutuhkan dataset completed dari server
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
    q, statusFilter, operatorFilter, customerFilter, urgentOnly, holdOnly,
    screenerFilter, helperFilter, packingFilter, deliveryFilter, bufferFilter,
    dateFrom, dateTo, workDateFilter, workDateFrom, workDateTo, sortBy,
    includeCancelled, includeCompleted,
  };

  const filtered = useMemo(
    () => applyProductionFilters(rows, filterState),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, q, statusFilter, operatorFilter, customerFilter, urgentOnly, holdOnly,
      screenerFilter, helperFilter, packingFilter, deliveryFilter, bufferFilter,
      dateFrom, dateTo, workDateFilter, workDateFrom, workDateTo,
      includeCancelled, includeCompleted]
  );

  const sorted = useMemo(() => sortProductionRows(filtered, sortBy), [filtered, sortBy]);

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

  return (
    <div className="prod-page" data-testid="production-schedule-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Produksi · Jadwal</p>
          <h1>Jadwal Produksi</h1>
          <p className="subhead">
            {total} PO real-time · sumber MongoDB · sortir, filter, dan export siap pakai
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

      <section className="prod-filters" data-testid="prod-filters">
        <div className="prod-filter-row">
          <div className="search-inline">
            <Search size={13}/>
            <input placeholder="Cari PO / Customer / Outlet" value={q} onChange={(e) => setQ(e.target.value)} data-testid="prod-search"/>
          </div>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} data-testid="prod-filter-status">
            {STATUS_OPTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <select value={customerFilter} onChange={(e) => setCustomerFilter(e.target.value)} data-testid="prod-filter-customer">
            {customers.map((c) => <option key={c} value={c}>{c === "all" ? "Semua customer" : c}</option>)}
          </select>
          <select value={operatorFilter} onChange={(e) => setOperatorFilter(e.target.value)} data-testid="prod-filter-operator">
            {operators.map((o) => <option key={o} value={o}>{o === "all" ? "Semua operator" : o}</option>)}
          </select>
          <select value={bufferFilter} onChange={(e) => setBufferFilter(e.target.value)} data-testid="prod-filter-buffer">
            <option value="all">Semua Buffer Stock</option>
            <option value="yes">Buffer Stock</option>
            <option value="no">Non Buffer Stock</option>
          </select>
        </div>
        <div className="prod-filter-row">
          <select value={screenerFilter} onChange={(e) => setScreenerFilter(e.target.value)} data-testid="prod-filter-screener">
            <option value="all">Semua Screener</option>
            <option value="yes">Ada Qty Screen</option>
            <option value="no">Belum Screener</option>
          </select>
          <select value={helperFilter} onChange={(e) => setHelperFilter(e.target.value)} data-testid="prod-filter-helper">
            <option value="all">Semua Helper</option>
            <option value="ok">Barang Cukup</option>
            <option value="kurang">Barang Kurang</option>
            <option value="none">Belum diinput</option>
          </select>
          <select value={packingFilter} onChange={(e) => setPackingFilter(e.target.value)} data-testid="prod-filter-packing">
            <option value="all">Semua Packing</option>
            <option value="Sudah">Sudah Packing</option>
            <option value="Berjalan">Packing Berjalan</option>
            <option value="Belum">Belum Packing</option>
          </select>
          <select value={deliveryFilter} onChange={(e) => setDeliveryFilter(e.target.value)} data-testid="prod-filter-delivery">
            <option value="all">Semua Pengiriman</option>
            <option value="Terkirim">Terkirim</option>
            <option value="Sebagian Terkirim">Sebagian Terkirim</option>
            <option value="Belum Terkirim">Belum Terkirim</option>
          </select>
          <label className="prod-check"><input type="checkbox" checked={urgentOnly} onChange={(e) => setUrgentOnly(e.target.checked)} data-testid="prod-filter-urgent"/> Urgent</label>
          <label className="prod-check"><input type="checkbox" checked={holdOnly} onChange={(e) => setHoldOnly(e.target.checked)} data-testid="prod-filter-hold"/> HOLD</label>
          <label className="prod-check"><input type="checkbox" checked={includeCompleted} onChange={(e) => setIncludeCompleted(e.target.checked)} data-testid="prod-include-completed"/> Termasuk Selesai</label>
          <label className="prod-check"><input type="checkbox" checked={includeCancelled} onChange={(e) => setIncludeCancelled(e.target.checked)} data-testid="prod-include-cancelled"/> Termasuk Cancelled</label>
        </div>
        <div className="prod-filter-row prod-filter-dates">
          <label className="prod-inline-date">Tanggal dari <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} data-testid="prod-date-from"/></label>
          <label className="prod-inline-date">Tanggal sampai <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} data-testid="prod-date-to"/></label>
          <select value={workDateFilter} onChange={(e) => setWorkDateFilter(e.target.value)} data-testid="prod-workdate-filter">
            <option value="all">Semua Pengerjaan</option>
            <option value="scheduled">Sudah Dijadwalkan</option>
            <option value="unscheduled">Belum Dijadwalkan</option>
          </select>
          <label className="prod-inline-date">Pekerjaan dari <input type="date" value={workDateFrom} onChange={(e) => setWorkDateFrom(e.target.value)} data-testid="prod-workdate-from"/></label>
          <label className="prod-inline-date">Pekerjaan sampai <input type="date" value={workDateTo} onChange={(e) => setWorkDateTo(e.target.value)} data-testid="prod-workdate-to"/></label>
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} data-testid="prod-sort-by">
            <option value="deadline">Urutkan: Deadline</option>
            <option value="work_date_asc">Urutkan: Pengerjaan (terlama)</option>
            <option value="work_date">Urutkan: Pengerjaan (terbaru)</option>
          </select>
          <div className="prod-filter-actions">
            <span className="muted small" data-testid="prod-total-visible">{filtered.length} / {rows.length} entri</span>
            <button className="icon-button" onClick={load} title="Muat ulang" data-testid="prod-refresh"><RefreshCw size={13} className={busy ? "spin" : ""}/></button>
            <button className="ghost sm prod-filter-reset" onClick={resetFilters} data-testid="prod-filter-reset">Reset Filter</button>
            {canExport && (
              <>
                <button className="ghost sm" disabled={exporting || !filtered.length} onClick={() => download("filtered")} data-testid="prod-export-filtered"><Download size={12}/> Export Terfilter</button>
                <button className="ghost sm" disabled={exporting} onClick={() => download("all")} data-testid="prod-export-all"><Download size={12}/> Export Semua</button>
              </>
            )}
          </div>
        </div>
      </section>

      {err && <div className="error" data-testid="prod-error">{err}</div>}

      {mode === "tabel" ? (
        <div className="prod-table-wrap" data-testid="prod-table">
          <table className="prod-table">
            <colgroup>
              {TABLE_COLS.map((c) => (
                <col key={c.key} style={{ width: c.width, minWidth: c.width }} />
              ))}
            </colgroup>
            <thead>
              <tr>
                {TABLE_COLS.map((c) => (
                  <th key={c.key} className={c.num ? "num" : undefined} data-testid={c.testid} scope="col">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={r.id} data-testid={`prod-row-${r.po_number}`} className={r.hold ? "hold" : (r.cancelled ? "cancelled" : "")}>
                  <td className="mono small">{formatDate(r.deadline)}</td>
                  <td className="mono small prod-cell-stack" data-testid={`prod-deadline-effective-${r.po_number}`}>
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
                  <td className="mono small prod-cell-stack" data-testid={`prod-work-date-${r.po_number}`}>
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
                  <td>{r.urgent ? <span className="urgent-badge">URGENT</span> : <span className="muted small">Normal</span>}</td>
                  <td data-testid={`prod-buffer-${r.po_number}`}>
                    {r.buffer_stock ? <span className="pill sm" style={{ background: "#e6ecf3", color: "#3a4b60" }}>Buffer</span> : <span className="muted small">—</span>}
                  </td>
                  <td className="mono"><Link to={`/po/${r.id}`} data-testid={`prod-po-link-${r.po_number}`}>{r.po_number}</Link></td>
                  <td className="prod-cell-wrap"><strong>{r.customer || "-"}</strong></td>
                  <td className="muted small prod-cell-wrap">{r.outlet || "-"}</td>
                  <td className="num">{(r.total_quantity || 0).toLocaleString("id-ID")}</td>
                  <td className="prod-ops">
                    {(r.operators || []).length === 0 ? <span className="muted small">-</span> :
                      (r.operators || []).map((op, i) => (
                        <div key={i} className="prod-op-line">
                          <span className="prod-op-name">{op.operator_name}</span>
                          <span className="muted small mono prod-op-qty">{op.qty_completed || 0}/{op.quantity}</span>
                        </div>
                      ))
                    }
                  </td>
                  <td>
                    <span className={"pill sm " + ((r.design_status === "Approved") ? "pill-ok" : "pill-warn")}>{r.design_status || "-"}</span>
                    {r.design_bypassed && <span className="pill sm pill-bypass" title="Bypass">BYPASS</span>}
                  </td>
                  <td>
                    <span className={"pill sm " + ((r.payment_status === "Approved") ? "pill-ok" : "pill-warn")}>{r.payment_status || "-"}</span>
                    {r.payment_bypassed && <span className="pill sm pill-bypass" title="Bypass">BYPASS</span>}
                  </td>
                  <td>{r.hold ? <span className="pill sm pill-hold" title={(r.hold_reasons || []).join("; ")}>HOLD</span> : <span className="muted small">-</span>}</td>
                  <td className="num">{r.screen_qty ?? "-"}</td>
                  <td className="small">
                    {r.helper_required != null ? (
                      <span className={r.helper_shortage_status === "Barang Kurang" ? "prod-helper-warn" : ""}>{r.helper_prepared ?? 0}/{r.helper_required} {r.helper_shortage ? `(-${r.helper_shortage})` : ""}</span>
                    ) : <span className="muted">-</span>}
                  </td>
                  <td data-testid={`prod-packing-${r.po_number}`}>
                    {r.packing_status === "Sudah" ? <span className="pill sm pill-ok">Sudah</span> :
                     r.packing_status === "Berjalan" ? <span className="pill sm">Berjalan</span> :
                     <span className="pill sm">Belum</span>}
                  </td>
                  <td className="mono small">{r.delivery_note_number || "-"}</td>
                  <td>{r.loading_ok ? <span className="pill sm pill-ok">OK</span> : <span className="muted small">-</span>}</td>
                  <td className="small">{r.driver_name || "-"}</td>
                  <td>
                    {r.cancelled ? <span className="pill sm pill-cancel"><XCircle size={10}/> Cancelled</span> :
                     r.status_final === "HOLD" ? <span className="pill sm pill-hold">HOLD</span> :
                     r.status_final === "Terkirim" ? <span className="pill sm pill-ok">Terkirim</span> :
                     <span className="pill sm">{r.status_final}</span>}
                  </td>
                </tr>
              ))}
              {!sorted.length && !busy && (
                <tr><td colSpan={TABLE_COLS.length} className="empty-state">Tidak ada PO cocok dengan filter.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <section className="prod-cards" data-testid="prod-cards">
          {sorted.map((r) => (
            <Link key={r.id} to={`/po/${r.id}`} className="prod-card" data-testid={`prod-card-${r.po_number}`}>
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

      <p className="muted small" style={{marginTop:12}}>
        <AlertCircle size={12} style={{verticalAlign:"-2px"}}/> Semua data diambil dari MongoDB real-time. Export Excel adalah satu arah — tidak mengubah data.
      </p>
    </div>
  );
}
