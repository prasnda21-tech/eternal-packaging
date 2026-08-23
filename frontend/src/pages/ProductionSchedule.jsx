import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Download, Filter, LayoutGrid, RefreshCw, Search, Table as TableIcon, XCircle } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import useAutoRefresh from "../lib/useAutoRefresh";

const STATUS_OPTS = [
  { key: "all", label: "Semua status" },
  { key: "aktif", label: "Aktif (non-HOLD)" },
  { key: "hold", label: "HOLD" },
  { key: "selesai", label: "Selesai" },
  { key: "cancelled", label: "Cancelled" },
];

function statusOfRow(r) {
  if (r.cancelled) return "cancelled";
  if (r.current_stage >= 14) return "selesai";
  if (r.hold) return "hold";
  return "aktif";
}

export default function ProductionSchedule() {
  const { hasPerm } = useAuth();
  const canExport = hasPerm("dashboard.production.export");
  const [mode, setMode] = useState("tabel"); // 'kartu' | 'tabel'
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [operatorFilter, setOperatorFilter] = useState("all");
  const [customerFilter, setCustomerFilter] = useState("all");
  const [urgentOnly, setUrgentOnly] = useState(false);
  const [holdOnly, setHoldOnly] = useState(false);
  const [screenerFilter, setScreenerFilter] = useState("all"); // 'all' | 'yes' | 'no'
  const [helperFilter, setHelperFilter] = useState("all"); // 'all' | 'ok' | 'kurang' | 'none'
  const [packingFilter, setPackingFilter] = useState("all");
  const [deliveryFilter, setDeliveryFilter] = useState("all");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  // Req 13: Filter & sort by Tanggal Pengerjaan
  const [workDateFilter, setWorkDateFilter] = useState("all"); // 'all' | 'scheduled' | 'unscheduled'
  const [workDateFrom, setWorkDateFrom] = useState("");
  const [workDateTo, setWorkDateTo] = useState("");
  const [sortBy, setSortBy] = useState("deadline"); // 'deadline' | 'work_date' | 'work_date_asc'
  const [includeCancelled, setIncludeCancelled] = useState(false);
  const [includeCompleted, setIncludeCompleted] = useState(true);
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

  // Batch 2: auto-refresh 30s untuk Jadwal Produksi (read-only).
  const { lastUpdated: schedLastUpdated, isPolling: schedIsPolling, refreshNow: refreshSched } = useAutoRefresh(
    () => api.productionDashboard({ include_cancelled: includeCancelled, include_completed: includeCompleted }),
    { intervalMs: 30_000, enabled: true, onData: (d) => { setRows(d.rows || []); setTotal(d.total || 0); } }
  );
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [includeCancelled, includeCompleted]);

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

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (statusFilter !== "all" && statusOfRow(r) !== statusFilter) return false;
      if (urgentOnly && !r.urgent) return false;
      if (holdOnly && !r.hold) return false;
      if (customerFilter !== "all" && r.customer !== customerFilter) return false;
      if (operatorFilter !== "all") {
        const ops = (r.operators || []).map((o) => o.operator_name);
        if (!ops.includes(operatorFilter)) return false;
      }
      if (screenerFilter === "yes" && !(r.screen_qty && r.screen_qty > 0)) return false;
      if (screenerFilter === "no" && r.screen_qty && r.screen_qty > 0) return false;
      if (helperFilter === "ok" && r.helper_shortage_status !== "Cukup") return false;
      if (helperFilter === "kurang" && r.helper_shortage_status !== "Barang Kurang") return false;
      if (helperFilter === "none" && r.helper_required) return false;
      if (packingFilter !== "all" && (r.packing_status || "Belum") !== packingFilter) return false;
      if (deliveryFilter !== "all" && (r.delivery_status || "Belum Terkirim") !== deliveryFilter) return false;
      if (dateFrom) {
        if (!r.deadline || r.deadline < dateFrom) return false;
      }
      if (dateTo) {
        if (!r.deadline || r.deadline > dateTo + "T23:59:59") return false;
      }
      // Req 13: Tanggal Pengerjaan filter
      if (workDateFilter === "scheduled" && !r.work_date) return false;
      if (workDateFilter === "unscheduled" && r.work_date) return false;
      if (workDateFrom && (!r.work_date || r.work_date < workDateFrom)) return false;
      if (workDateTo && (!r.work_date || r.work_date > workDateTo)) return false;
      if (q) {
        const t = q.toLowerCase();
        const hay = `${r.po_number} ${r.customer} ${r.outlet}`.toLowerCase();
        if (!hay.includes(t)) return false;
      }
      return true;
    });
  }, [rows, statusFilter, urgentOnly, holdOnly, customerFilter, operatorFilter, screenerFilter, helperFilter, packingFilter, deliveryFilter, dateFrom, dateTo, workDateFilter, workDateFrom, workDateTo, q]);

  // Req 13: sort by chosen key
  const sorted = useMemo(() => {
    const arr = [...filtered];
    const cmp = (a, b) => {
      if (sortBy === "work_date" || sortBy === "work_date_asc") {
        const aw = a.work_date || "9999-12-31";
        const bw = b.work_date || "9999-12-31";
        return sortBy === "work_date_asc" ? aw.localeCompare(bw) : bw.localeCompare(aw);
      }
      const ad = a.deadline || "9999-12-31";
      const bd = b.deadline || "9999-12-31";
      return ad.localeCompare(bd);
    };
    arr.sort(cmp);
    return arr;
  }, [filtered, sortBy]);

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
    <div data-testid="production-schedule-page">
      <div className="page-heading">
        <div>
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
        <div className="search-inline">
          <Search size={14}/>
          <input placeholder="Cari PO / Customer / Outlet" value={q} onChange={(e) => setQ(e.target.value)} data-testid="prod-search"/>
        </div>
        <div className="prod-filter-group">
          <Filter size={13}/>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} data-testid="prod-filter-status">
            {STATUS_OPTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <select value={customerFilter} onChange={(e) => setCustomerFilter(e.target.value)} data-testid="prod-filter-customer">
            {customers.map((c) => <option key={c} value={c}>{c === "all" ? "Semua customer" : c}</option>)}
          </select>
          <select value={operatorFilter} onChange={(e) => setOperatorFilter(e.target.value)} data-testid="prod-filter-operator">
            {operators.map((o) => <option key={o} value={o}>{o === "all" ? "Semua operator" : o}</option>)}
          </select>
        </div>
        <div className="prod-filter-group">
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
            <option value="Belum">Belum Packing</option>
          </select>
          <select value={deliveryFilter} onChange={(e) => setDeliveryFilter(e.target.value)} data-testid="prod-filter-delivery">
            <option value="all">Semua Pengiriman</option>
            <option value="Terkirim">Terkirim</option>
            <option value="Belum Terkirim">Belum Terkirim</option>
          </select>
        </div>
        <div className="prod-filter-group">
          <label className="prod-check"><input type="checkbox" checked={urgentOnly} onChange={(e) => setUrgentOnly(e.target.checked)} data-testid="prod-filter-urgent"/> Urgent</label>
          <label className="prod-check"><input type="checkbox" checked={holdOnly} onChange={(e) => setHoldOnly(e.target.checked)} data-testid="prod-filter-hold"/> HOLD</label>
          <label className="prod-check"><input type="checkbox" checked={includeCompleted} onChange={(e) => setIncludeCompleted(e.target.checked)} data-testid="prod-include-completed"/> Termasuk Selesai</label>
          <label className="prod-check"><input type="checkbox" checked={includeCancelled} onChange={(e) => setIncludeCancelled(e.target.checked)} data-testid="prod-include-cancelled"/> Termasuk Cancelled</label>
        </div>
        <div className="prod-filter-group">
          <label className="prod-inline-date">Tanggal dari <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} data-testid="prod-date-from"/></label>
          <label className="prod-inline-date">sampai <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} data-testid="prod-date-to"/></label>
        </div>
        <div className="prod-filter-group">
          <select value={workDateFilter} onChange={(e) => setWorkDateFilter(e.target.value)} data-testid="prod-workdate-filter">
            <option value="all">Semua Pengerjaan</option>
            <option value="scheduled">Sudah Dijadwalkan</option>
            <option value="unscheduled">Belum Dijadwalkan</option>
          </select>
          <label className="prod-inline-date">Pengerjaan dari <input type="date" value={workDateFrom} onChange={(e) => setWorkDateFrom(e.target.value)} data-testid="prod-workdate-from"/></label>
          <label className="prod-inline-date">sampai <input type="date" value={workDateTo} onChange={(e) => setWorkDateTo(e.target.value)} data-testid="prod-workdate-to"/></label>
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} data-testid="prod-sort-by">
            <option value="deadline">Urutkan: Deadline</option>
            <option value="work_date_asc">Urutkan: Pengerjaan (terlama)</option>
            <option value="work_date">Urutkan: Pengerjaan (terbaru)</option>
          </select>
        </div>
        <div className="prod-filter-group">
          <button className="icon-button" onClick={load} title="Muat ulang" data-testid="prod-refresh"><RefreshCw size={15} className={busy ? "spin" : ""}/></button>
          <span className="muted small" data-testid="prod-total-visible">{filtered.length} / {rows.length} entri</span>
          {canExport && (
            <>
              <button className="ghost sm" disabled={exporting || !filtered.length} onClick={() => download("filtered")} data-testid="prod-export-filtered"><Download size={13}/> Export Terfilter</button>
              <button className="ghost sm" disabled={exporting} onClick={() => download("all")} data-testid="prod-export-all"><Download size={13}/> Export Semua</button>
            </>
          )}
        </div>
      </section>

      {err && <div className="error" data-testid="prod-error">{err}</div>}

      {mode === "tabel" ? (
        <div className="prod-table-wrap" data-testid="prod-table">
          <table className="prod-table">
            <thead>
              <tr>
                <th>Deadline</th><th data-testid="prod-th-deadline-effective">Deadline Pengiriman</th><th data-testid="prod-th-work-date">Pengerjaan</th><th>Prio</th><th>No PO</th><th>Customer</th><th>Outlet</th>
                <th className="num">Total Qty</th><th>Operator</th>
                <th>Design</th><th>Payment</th><th>HOLD</th>
                <th className="num">Qty Screen</th>
                <th>Persiapan</th>
                <th>Packing</th><th>Surat Jalan</th><th>Loading</th><th>Driver</th>
                <th>Status Akhir</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={r.id} data-testid={`prod-row-${r.po_number}`} className={r.hold ? "hold" : (r.cancelled ? "cancelled" : "")}>
                  <td className="mono small nowrap">{formatDate(r.deadline)}</td>
                  <td className="mono small nowrap" data-testid={`prod-deadline-effective-${r.po_number}`}>
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
                  <td className="mono small nowrap" data-testid={`prod-work-date-${r.po_number}`}>
                    {(r.operator_work_dates || []).length > 0 ? (
                      // Print/Mixed: tanggal per operator (stacked lines).
                      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                        {(r.operator_work_dates || []).map((o, i) => (
                          <span key={i} data-testid={`prod-op-work-date-${r.po_number}-${i}`}>
                            <span className="muted small">{o.operator_name}</span>
                            <br />
                            <span className="mono">{o.work_date || "—"}</span>
                          </span>
                        ))}
                      </div>
                    ) : r.work_date ? (
                      // Full Non-Cetak / legacy: gunakan orders.work_date.
                      <span data-testid={`prod-order-work-date-${r.po_number}`}>{r.work_date}</span>
                    ) : (
                      <span className="muted small">Belum Dijadwalkan</span>
                    )}
                  </td>
                  <td>{r.urgent ? <span className="urgent-badge">URGENT</span> : <span className="muted small">Normal</span>}</td>
                  <td className="mono"><Link to={`/po/${r.id}`} data-testid={`prod-po-link-${r.po_number}`}>{r.po_number}</Link></td>
                  <td><strong>{r.customer || "-"}</strong></td>
                  <td className="muted small">{r.outlet || "-"}</td>
                  <td className="num">{(r.total_quantity || 0).toLocaleString("id-ID")}</td>
                  <td className="prod-ops">
                    {(r.operators || []).length === 0 ? <span className="muted small">-</span> :
                      (r.operators || []).map((op, i) => (
                        <div key={i} className="prod-op-line">
                          <span>{op.operator_name}</span>
                          <span className="muted small mono"> {op.qty_completed || 0}/{op.quantity}</span>
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
              {!filtered.length && !busy && (
                <tr><td colSpan={17} className="empty-state">Tidak ada PO cocok dengan filter.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <section className="prod-cards" data-testid="prod-cards">
          {filtered.map((r) => (
            <Link key={r.id} to={`/po/${r.id}`} className="prod-card" data-testid={`prod-card-${r.po_number}`}>
              <header>
                <strong>{r.po_number}</strong>
                {r.urgent && <span className="urgent-badge">URGENT</span>}
                {r.hold && <span className="pill sm pill-hold">HOLD</span>}
                {r.cancelled && <span className="pill sm pill-cancel">Cancelled</span>}
              </header>
              <p><b>{r.customer}</b> · {r.outlet || "-"}</p>
              <p className="muted small">Deadline: {formatDate(r.deadline)} · Total: {(r.total_quantity || 0).toLocaleString("id-ID")} pcs</p>
              <div className="prod-card-tags">
                <span className={"pill sm " + ((r.design_status === "Approved") ? "pill-ok" : "pill-warn")}>Design {r.design_status || "-"}</span>
                <span className={"pill sm " + ((r.payment_status === "Approved") ? "pill-ok" : "pill-warn")}>Payment {r.payment_status || "-"}</span>
                {r.packing_status === "Sudah" && <span className="pill sm pill-ok">Packed</span>}
              </div>
            </Link>
          ))}
          {!filtered.length && !busy && <div className="empty-state" data-testid="prod-empty">Tidak ada PO cocok dengan filter.</div>}
        </section>
      )}

      <p className="muted small" style={{marginTop:12}}>
        <AlertCircle size={12} style={{verticalAlign:"-2px"}}/> Semua data diambil dari MongoDB real-time. Export Excel adalah satu arah — tidak mengubah data.
      </p>
    </div>
  );
}
