import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Download, Minus, Plus, Search, XCircle } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { formatApiError } from "../lib/errors";
import {
  FILTER_DEFAULTS,
  applyProductionFilters,
  isScheduledToday,
  productionFiltersAreActive,
  sortProductionRows,
} from "../lib/productionScheduleFilters";
import { formatIdQty, scheduledPrintQtyProgress, totalQtyScheduledCell } from "../lib/productionScheduleQty";
import { PRODUCTION_SCHEDULE_TABLE_COLS, productionDashboardRequestParams } from "../lib/productionScheduleTable";
import { MOBILE_LOAD_TIMEOUT_MS, withTimeout } from "../lib/mobileRequest";
import {
  MOBILE_SCHEDULE_ZOOM_DEFAULT,
  canViewMobileSchedule,
  fitMobileScheduleZoom,
  mobileScheduleHref,
  mobileScheduleZoomFactor,
  rowsFromProductionDashboard,
  stepMobileScheduleZoom,
} from "../lib/mobileSchedule";
import MobileTopBar from "./MobileTopBar";

export function scheduleLoadErrorMessage(err) {
  const status = err?.response?.status;
  if (err?.code === "ETIMEDOUT" || err?.code === "ECONNABORTED") {
    return "Gagal memuat jadwal produksi.";
  }
  const msg = formatApiError(err, "Gagal memuat jadwal produksi.");
  if (status === 403) return msg || "Anda tidak punya akses untuk melihat jadwal produksi.";
  if (status === 401) return msg || "Sesi berakhir. Silakan masuk kembali.";
  return msg || "Gagal memuat jadwal produksi.";
}

function DeadlineShipCell({ row }) {
  if (!row.delivery_deadline_effective) {
    return <span className="muted small">Menunggu approval</span>;
  }
  return (
    <>
      {formatDate(row.delivery_deadline_effective)}
      {" "}
      <span
        className="pill sm"
        title={row.delivery_deadline_source === "manual" ? "Manual override" : "Otomatis"}
        style={{
          background: row.delivery_deadline_source === "manual" ? "#ffe9b8" : "#e6ecf3",
          color: row.delivery_deadline_source === "manual" ? "#7a5a1e" : "#3a4b60",
        }}
      >
        {row.delivery_deadline_source === "manual" ? "Manual" : "Otomatis"}
      </span>
          {row.delivery_deadline_status === "overdue" && (
            <span className="urgent-badge">OVERDUE</span>
          )}
          {row.delivery_deadline_status === "h6" && (
            <span className="urgent-badge">≤6 JAM</span>
          )}
          {row.delivery_deadline_status === "h1" && (
            <span className="standard-badge" style={{ background: "#fef4d6", color: "#7a5a1e" }}>H-1</span>
          )}
    </>
  );
}

function WorkDateCell({ row }) {
  if ((row.operator_work_dates || []).length > 0) {
    return (row.operator_work_dates || []).map((entry, i) => (
      <div key={entry.split_id || i} className="prod-date-line">{entry.work_date || "—"}</div>
    ));
  }
  if (row.work_date) return <span>{row.work_date}</span>;
  return <span className="muted small">Belum Dijadwalkan</span>;
}

function TotalQtyCell({ row, qtyProg }) {
  if (qtyProg.groups?.length) {
    return qtyProg.groups.map((g, i) => (
      <div
        key={g.key}
        className="prod-line-qty-block"
        style={{ minHeight: `${Math.max((g.operators || []).length, 1) * 1.35}em` }}
        data-testid={`mobile-schedule-qty-line-${row.po_number}-${i}`}
      >
        {g.line_name ? <span className="prod-line-qty-name">{g.line_name}</span> : null}
        <span className="prod-line-qty-value">{formatIdQty(g.total)}</span>
      </div>
    ));
  }
  return qtyProg.text;
}

function OperatorCell({ row, qtyProg }) {
  if (qtyProg.groups?.length) {
    return qtyProg.groups.map((g) => (
      <div
        key={g.key}
        className="prod-line-ops-block"
        style={{ minHeight: `${Math.max((g.operators || []).length, 1) * 1.35}em` }}
      >
        {(g.operators || []).map((op, i) => (
          <div key={`${op.operator_email || op.operator_user_id || i}`} className="prod-op-line">
            <span className="prod-op-name">{op.operator_name || "-"}</span>
            <span className="prod-op-qty muted small mono">{formatIdQty(op.quantity)}</span>
          </div>
        ))}
      </div>
    ));
  }
  if (!(row.operators || []).length) return <span className="muted small">-</span>;
  return (row.operators || []).map((op, i) => (
    <div key={i} className="prod-op-line">
      <span className="prod-op-name">{op.operator_name || "-"}</span>
    </div>
  ));
}

function StatusCell({ row }) {
  if (row.cancelled) {
    return <span className="pill sm pill-cancel"><XCircle size={10} /> Cancelled</span>;
  }
  if (row.status_final === "HOLD") return <span className="pill sm pill-hold">HOLD</span>;
  if (row.status_final === "Terkirim") return <span className="pill sm pill-ok">Terkirim</span>;
  return <span className="pill sm">{row.status_final}</span>;
}

export default function MobileProductionSchedulePage() {
  const navigate = useNavigate();
  const { hasPerm } = useAuth();
  const allowed = canViewMobileSchedule(hasPerm);
  const canExport = hasPerm("dashboard.production.export");
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(allowed);
  const [reloadKey, setReloadKey] = useState(0);
  const [zoom, setZoom] = useState(MOBILE_SCHEDULE_ZOOM_DEFAULT);
  const [q, setQ] = useState(FILTER_DEFAULTS.q);
  const [sortBy, setSortBy] = useState(FILTER_DEFAULTS.sortBy);
  const [includeCancelled, setIncludeCancelled] = useState(FILTER_DEFAULTS.includeCancelled);
  const [includeCompleted, setIncludeCompleted] = useState(FILTER_DEFAULTS.includeCompleted);
  const [exporting, setExporting] = useState(false);

  const filterState = {
    ...FILTER_DEFAULTS,
    q,
    sortBy,
    includeCancelled,
    includeCompleted,
  };

  useEffect(() => {
    if (!allowed) {
      setLoading(false);
      setRows([]);
      setErr("");
      return undefined;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr("");
      try {
        const payload = await withTimeout(
          api.productionDashboard(productionDashboardRequestParams({ includeCancelled, includeCompleted })),
          MOBILE_LOAD_TIMEOUT_MS,
        );
        if (cancelled) return;
        setRows(rowsFromProductionDashboard(payload));
      } catch (e) {
        if (cancelled) return;
        setRows([]);
        setErr(scheduleLoadErrorMessage(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [allowed, includeCancelled, includeCompleted, reloadKey]);

  const filtered = useMemo(
    () => applyProductionFilters(rows, filterState),
    [rows, q, includeCancelled, includeCompleted],
  );
  const sorted = useMemo(() => sortProductionRows(filtered, sortBy), [filtered, sortBy]);
  const filtersActive = productionFiltersAreActive(filterState);
  const zoomFactor = mobileScheduleZoomFactor(zoom);

  const resetFilters = () => {
    setQ(FILTER_DEFAULTS.q);
    setSortBy(FILTER_DEFAULTS.sortBy);
    setIncludeCancelled(FILTER_DEFAULTS.includeCancelled);
    setIncludeCompleted(FILTER_DEFAULTS.includeCompleted);
  };

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
      a.download = `jadwal-produksi-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(scheduleLoadErrorMessage(e) || "Gagal memuat jadwal produksi.");
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="mop-schedule-root">
      <MobileTopBar kicker="ETERNAL PACKAGING" title="Jadwal Produksi" />
      <main className="mop-content mop-schedule-page" data-testid="mobile-schedule-page">
        {!allowed && (
          <div className="mop-error" data-testid="mobile-schedule-forbidden">
            <p>Anda tidak punya akses untuk melihat jadwal produksi.</p>
          </div>
        )}
        {allowed && (
          <>
            <div className="mop-schedule-toolbar" data-testid="mobile-schedule-toolbar">
              <label className="mop-schedule-search">
                <Search size={13} />
                <input
                  placeholder="Cari PO / Customer / Outlet"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  data-testid="mobile-schedule-search"
                />
              </label>
              <span className="mop-schedule-count" data-testid="mobile-schedule-count">
                {filtered.length} / {rows.length} entri
              </span>
              <label className="mop-schedule-check">
                <input
                  type="checkbox"
                  checked={includeCompleted}
                  onChange={(e) => setIncludeCompleted(e.target.checked)}
                  data-testid="mobile-schedule-include-completed"
                />
                Selesai
              </label>
              <label className="mop-schedule-check">
                <input
                  type="checkbox"
                  checked={includeCancelled}
                  onChange={(e) => setIncludeCancelled(e.target.checked)}
                  data-testid="mobile-schedule-include-cancelled"
                />
                Cancelled
              </label>
              <button
                type="button"
                className="mop-btn-ghost mop-schedule-reset"
                onClick={resetFilters}
                disabled={!filtersActive && sortBy === FILTER_DEFAULTS.sortBy}
                data-testid="mobile-schedule-reset"
              >
                Reset Filter
              </button>
              {canExport && (
                <>
                  <button
                    type="button"
                    className="mop-btn-ghost"
                    disabled={exporting || !filtered.length}
                    onClick={() => download("filtered")}
                    data-testid="mobile-schedule-export-filtered"
                  >
                    <Download size={12} /> Export Terfilter
                  </button>
                  <button
                    type="button"
                    className="mop-btn-ghost"
                    disabled={exporting}
                    onClick={() => download("all")}
                    data-testid="mobile-schedule-export-all"
                  >
                    <Download size={12} /> Export Semua
                  </button>
                </>
              )}
            </div>
            <div className="mop-schedule-zoom" data-testid="mobile-schedule-zoom">
              <span className="mop-schedule-zoom-label">Tampilan</span>
              <button
                type="button"
                className="mop-schedule-zoom-btn"
                aria-label="Perkecil"
                onClick={() => setZoom((prev) => stepMobileScheduleZoom(prev, -1))}
                data-testid="mobile-schedule-zoom-out"
              >
                <Minus size={16} />
              </button>
              <span className="mop-schedule-zoom-value" data-testid="mobile-schedule-zoom-value">{zoom}%</span>
              <button
                type="button"
                className="mop-schedule-zoom-btn"
                aria-label="Perbesar"
                onClick={() => setZoom((prev) => stepMobileScheduleZoom(prev, 1))}
                data-testid="mobile-schedule-zoom-in"
              >
                <Plus size={16} />
              </button>
              <button
                type="button"
                className="mop-schedule-zoom-fit"
                onClick={() => setZoom(fitMobileScheduleZoom())}
                data-testid="mobile-schedule-zoom-fit"
              >
                Fit
              </button>
            </div>
            {loading && (
              <p className="mop-empty" data-testid="mobile-schedule-loading">Memuat jadwal produksi...</p>
            )}
            {!loading && err && (
              <div className="mop-error" data-testid="mobile-schedule-error">
                <p>Gagal memuat jadwal produksi.</p>
                {err !== "Gagal memuat jadwal produksi." ? <p className="mop-schedule-error-detail">{err}</p> : null}
                <button
                  type="button"
                  className="mop-btn-primary"
                  onClick={() => setReloadKey((n) => n + 1)}
                  data-testid="mobile-schedule-retry"
                >
                  Coba Lagi
                </button>
              </div>
            )}
            {!loading && !err && rows.length === 0 && (
              <p className="mop-empty" data-testid="mobile-schedule-empty">Belum ada jadwal produksi.</p>
            )}
            {!loading && !err && rows.length > 0 && (
              <div className="mop-schedule-scroll" data-testid="mobile-schedule-scroll">
                <table
                  className="mop-schedule-table"
                  data-testid="mobile-schedule-table"
                  style={{ ["--mop-sched-zoom"]: String(zoomFactor) }}
                >
                  <thead data-testid="mobile-schedule-thead">
                    <tr>
                      {PRODUCTION_SCHEDULE_TABLE_COLS.map((col) => (
                        <th
                          key={col.key}
                          scope="col"
                          className={[col.num ? "num" : "", (col.num || col.fit) ? "col-fit" : "col-text"].filter(Boolean).join(" ")}
                        >
                          {col.sort ? (
                            <button
                              type="button"
                              className="mop-schedule-th-sort"
                              onClick={() => cycleSort(col.sort)}
                              data-testid={`mobile-schedule-sort-${col.key}`}
                            >
                              {col.label}
                            </button>
                          ) : col.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {sorted.map((row) => {
                      const qtyProg = totalQtyScheduledCell(row);
                      const cetakProg = scheduledPrintQtyProgress(row);
                      const isToday = isScheduledToday(row);
                      const href = mobileScheduleHref(row.id);
                      const open = () => { if (href) navigate(href); };
                      return (
                        <tr
                          key={row.id}
                          className={`mop-schedule-row${row.hold ? " hold" : ""}${row.cancelled ? " cancelled" : ""}`}
                          data-testid={`mobile-schedule-row-${row.po_number}`}
                          data-to={href}
                          tabIndex={0}
                          onClick={open}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              open();
                            }
                          }}
                        >
                          <td className={"col-text" + (isToday ? " prod-cell-today" : "")} style={isToday ? { backgroundColor: "#fef08a", color: "#713f12" } : undefined}>
                            <strong>{row.customer || "-"}</strong>
                          </td>
                          <td className="mono small col-fit">{formatDate(row.deadline)}</td>
                          <td className="mono small prod-cell-stack col-text"><DeadlineShipCell row={row} /></td>
                          <td className="mono small prod-cell-stack col-text"><WorkDateCell row={row} /></td>
                          <td className="col-fit">{row.urgent ? <span className="urgent-badge">URGENT</span> : <span className="muted small">Normal</span>}</td>
                          <td className="col-fit">{row.buffer_stock ? <span className="pill sm" style={{ background: "#e6ecf3", color: "#3a4b60" }}>Buffer</span> : <span className="muted small">—</span>}</td>
                          <td className="mono col-fit">{row.po_number}</td>
                          <td className="muted small col-text">{row.outlet || "-"}</td>
                          <td className="num col-fit prod-qty-cell" title={qtyProg.title}><TotalQtyCell row={row} qtyProg={qtyProg} /></td>
                          <td className={"num col-fit" + (cetakProg.over ? " prod-qty-over" : "")} title={cetakProg.title}>{cetakProg.text}</td>
                          <td className="prod-ops col-text"><OperatorCell row={row} qtyProg={qtyProg} /></td>
                          <td className="col-fit">
                            <span className={"pill sm " + ((row.design_status === "Approved") ? "pill-ok" : "pill-warn")}>{row.design_status || "-"}</span>
                            {row.design_bypassed && <span className="pill sm pill-bypass" title="Bypass">BYPASS</span>}
                          </td>
                          <td className="col-fit">
                            <span className={"pill sm " + ((row.payment_status === "Approved") ? "pill-ok" : "pill-warn")}>{row.payment_status || "-"}</span>
                            {row.payment_bypassed && <span className="pill sm pill-bypass" title="Bypass">BYPASS</span>}
                          </td>
                          <td className="col-fit">{row.hold ? <span className="pill sm pill-hold" title={(row.hold_reasons || []).join("; ")}>HOLD</span> : <span className="muted small">-</span>}</td>
                          <td className="num col-fit">{row.screen_qty ?? "-"}</td>
                          <td className="small col-fit">
                            {row.helper_required != null ? (
                              <span className={row.helper_shortage_status === "Barang Kurang" ? "prod-helper-warn" : ""}>
                                {row.helper_prepared ?? 0}/{row.helper_required} {row.helper_shortage ? `(-${row.helper_shortage})` : ""}
                              </span>
                            ) : <span className="muted">-</span>}
                          </td>
                          <td className="col-fit">
                            {row.packing_status === "Sudah" ? <span className="pill sm pill-ok">Sudah</span> :
                              row.packing_status === "Berjalan" ? <span className="pill sm">Berjalan</span> :
                              <span className="pill sm">Belum</span>}
                          </td>
                          <td className="mono small col-fit">{row.delivery_note_number || "-"}</td>
                          <td className="small col-text">{row.driver_name || "-"}</td>
                          <td className="col-fit"><StatusCell row={row} /></td>
                        </tr>
                      );
                    })}
                    {!sorted.length && (
                      <tr>
                        <td colSpan={PRODUCTION_SCHEDULE_TABLE_COLS.length} className="mop-schedule-filter-empty">
                          Tidak ada PO cocok dengan filter.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
