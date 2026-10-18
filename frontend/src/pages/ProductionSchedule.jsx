import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import "../productionSheet.css";
import { Download, Filter, LayoutGrid, RefreshCw, Table as TableIcon } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import useAutoRefresh from "../lib/useAutoRefresh";
import useProductionSheetZoom from "../hooks/useProductionSheetZoom";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import ProductionScheduleSheetCells from "../components/ProductionScheduleSheetCells";
import ProductionScheduleRingkasan, { ProductionScheduleSearchCluster } from "../components/ProductionScheduleRingkasan";
import {
  PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS,
  clearProductionScheduleColWidths,
  productionScheduleResizeNextWidth,
  productionScheduleStickyCssVars,
  readProductionScheduleColWidths,
  writeProductionScheduleColWidths,
} from "../lib/productionScheduleColWidths";
import {
  FILTER_DEFAULTS,
  PRODUCTION_SCHEDULE_STATUS_TABS,
  applyProductionFilters,
  applyProductionScheduleDisplayRowFilters,
  applyDeadlineTodayShortcut,
  applyWorkDateFilterControl,
  applyWorkDateRangeControl,
  applyWorkDateTodayShortcut,
  buildHariIniDisplayRows,
  computeProductionScheduleStatusTabCounts,
  countUniqueProductionSchedulePos,
  isScheduledToday,
  DESKTOP_PRODUCTION_SCHEDULE_SORT_DEFAULT,
  productionFiltersAreActive,
  sortProductionRows,
  sortScheduleDisplayRows,
} from "../lib/productionScheduleFilters";
import { isProductionScheduleHariIniPreviewHost, mergeHariIniPreviewDummyRows } from "../lib/productionScheduleHariIniPreviewDummy";
import { buildProductionScheduleDisplayRows } from "../lib/productionScheduleDisplayRows";
import { desktopPrintQtyDisplay, operatorQtyCetakProgress } from "../lib/productionScheduleQty";
import { buildProductionScheduleSummary } from "../lib/productionScheduleSummary";
import { isProductionScheduleUrgent } from "../lib/productionSchedulePriority";
import {
  PRODUCTION_SCHEDULE_TABLE_COLS as TABLE_COLS,
  DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS as DESKTOP_COLS,
  desktopProductionScheduleGroupSpans,
  productionDashboardRequestParams,
  productionSheetColumnStyle,
} from "../lib/productionScheduleTable";
import { productionSheetZoomStyle } from "../lib/productionSheetZoom";
import { productionSchedulePoHref } from "../lib/workPanel";

if (DESKTOP_COLS.length > TABLE_COLS.length) {
  throw new Error("desktop schedule columns cannot exceed canonical inventory");
}

const STATUS_OPTS = [
  { key: "all", label: "Semua status" },
  { key: "aktif", label: "Aktif (non-HOLD)" },
  { key: "hold", label: "HOLD" },
  { key: "selesai", label: "Selesai" },
  { key: "cancelled", label: "Cancelled" },
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

function DateFilterTodayShortcut({ testid, onClick }) {
  return (
    <button type="button" className="ghost sm prod-th-today" onClick={onClick} data-testid={testid}>
      Hari Ini
    </button>
  );
}

function ingestProductionDashboard(d) {
  const nextRows = mergeHariIniPreviewDummyRows(d?.rows || []);
  const total = isProductionScheduleHariIniPreviewHost() ? nextRows.length : (d?.total || 0);
  return { rows: nextRows, total };
}

function ProdColResizeHandle({ colKey, onPointerDown, onDoubleClick }) {
  return (
    <span
      className="prod-col-resize"
      role="separator"
      aria-orientation="vertical"
      aria-label={`Ubah lebar kolom ${colKey}`}
      data-testid={`prod-resize-${colKey}`}
      onPointerDown={(event) => onPointerDown(event, colKey)}
      onDoubleClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onDoubleClick?.(colKey);
      }}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
      }}
    />
  );
}

function HeaderFilter({ label, testid, active, sortable, sortHint, onSort, onClear, children, resizeSlot }) {
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
      {resizeSlot}
    </div>
  );
}

export default function ProductionSchedule() {
  const { user, hasPerm } = useAuth();
  const listUserId = user?.id || user?.email || user?.sub || "";
  const canExport = hasPerm("dashboard.production.export");
  const [mode, setMode] = useState("tabel"); // 'kartu' | 'tabel'
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [err, setErr] = useState("");
  const [total, setTotal] = useState(0);
  const [colWidths, setColWidths] = useState(() => readProductionScheduleColWidths(listUserId));
  const colResizeRef = useRef(null);
  const [q, setQ] = useState(FILTER_DEFAULTS.q);
  const [poFilter, setPoFilter] = useState(FILTER_DEFAULTS.poFilter);
  const [statusFilter, setStatusFilter] = useState(FILTER_DEFAULTS.statusFilter);
  const [operatorFilter, setOperatorFilter] = useState(FILTER_DEFAULTS.operatorFilter);
  const [customerFilter, setCustomerFilter] = useState(FILTER_DEFAULTS.customerFilter);
  const [urgentOnly, setUrgentOnly] = useState(FILTER_DEFAULTS.urgentOnly);
  const [holdOnly, setHoldOnly] = useState(FILTER_DEFAULTS.holdOnly);
  const [scheduleStatusTab, setScheduleStatusTab] = useState(FILTER_DEFAULTS.scheduleStatusTab);
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
  const [sortBy, setSortBy] = useState(DESKTOP_PRODUCTION_SCHEDULE_SORT_DEFAULT);
  const [includeCancelled, setIncludeCancelled] = useState(FILTER_DEFAULTS.includeCancelled);
  const [includeCompleted, setIncludeCompleted] = useState(FILTER_DEFAULTS.includeCompleted);
  const [exporting, setExporting] = useState(false);
  const sheetZoom = useProductionSheetZoom();
  /** Monotonic id for list GETs — success/error apply only if still the latest request. */
  const listReqIdRef = useRef(0);

  useEffect(() => {
    setColWidths(readProductionScheduleColWidths(listUserId));
  }, [listUserId]);

  useEffect(() => {
    let cancelled = false;
    const reqId = ++listReqIdRef.current;
    setBusy(true);
    setErr("");
    api.productionDashboard(productionDashboardRequestParams({ includeCancelled, includeCompleted, workDateFilter }))
      .then((payload) => {
        if (cancelled || reqId !== listReqIdRef.current) return;
        const ingested = ingestProductionDashboard(payload);
        setRows(ingested.rows);
        setTotal(ingested.total);
        setHasLoadedOnce(true);
        setErr("");
      })
      .catch((error) => {
        if (cancelled || reqId !== listReqIdRef.current) return;
        setErr(error.response?.data?.detail || "Gagal memuat");
        setHasLoadedOnce(true);
      })
      .finally(() => {
        if (!cancelled && reqId === listReqIdRef.current) setBusy(false);
      });
    return () => { cancelled = true; };
  }, [includeCancelled, includeCompleted, workDateFilter]);

  const { lastUpdated: schedLastUpdated, isPolling: schedIsPolling, refreshNow: refreshSched } = useAutoRefresh(
    async () => {
      const reqId = ++listReqIdRef.current;
      try {
        const payload = await api.productionDashboard(
          productionDashboardRequestParams({ includeCancelled, includeCompleted, workDateFilter })
        );
        return { reqId, payload };
      } catch (error) {
        if (error && typeof error === "object") error.__prodListReqId = reqId;
        throw error;
      }
    },
    {
      intervalMs: 30_000,
      enabled: true,
      onData: (result) => {
        const reqId = result?.reqId;
        if (reqId == null || reqId !== listReqIdRef.current) return;
        const ingested = ingestProductionDashboard(result.payload);
        setRows(ingested.rows);
        setTotal(ingested.total);
        setHasLoadedOnce(true);
        setErr("");
      },
      onError: (error) => {
        const reqId = error?.__prodListReqId;
        if (reqId == null || reqId !== listReqIdRef.current) return;
        setErr(error.response?.data?.detail || "Gagal memuat");
        setHasLoadedOnce(true);
      },
    }
  );
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
    setScheduleStatusTab(FILTER_DEFAULTS.scheduleStatusTab);
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
    setSortBy(DESKTOP_PRODUCTION_SCHEDULE_SORT_DEFAULT);
    setIncludeCancelled(FILTER_DEFAULTS.includeCancelled);
    setIncludeCompleted(FILTER_DEFAULTS.includeCompleted);
  };

  const filterState = {
    q, poFilter, statusFilter, operatorFilter, customerFilter, urgentOnly, holdOnly,
    scheduleStatusTab,
    screenerFilter, helperFilter, packingFilter, deliveryFilter, bufferFilter,
    dateFrom, dateTo, workDateFilter, workDateFrom, workDateTo, sortBy,
    includeCancelled, includeCompleted,
  };

  /** C2 base: all active filters except the status tab itself. */
  const filterContextRows = useMemo(
    () => applyProductionFilters(rows, { ...filterState, scheduleStatusTab: "all" }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, q, poFilter, statusFilter, operatorFilter, customerFilter, urgentOnly, holdOnly,
      screenerFilter, helperFilter, packingFilter, deliveryFilter, bufferFilter,
      dateFrom, dateTo, workDateFilter, workDateFrom, workDateTo,
      includeCancelled, includeCompleted]
  );

  const statusTabCounts = useMemo(
    () => (hasLoadedOnce && !err ? computeProductionScheduleStatusTabCounts(filterContextRows) : null),
    [hasLoadedOnce, err, filterContextRows]
  );

  const filtered = useMemo(
    () => applyProductionFilters(rows, filterState),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, q, poFilter, statusFilter, operatorFilter, customerFilter, urgentOnly, holdOnly,
      scheduleStatusTab,
      screenerFilter, helperFilter, packingFilter, deliveryFilter, bufferFilter,
      dateFrom, dateTo, workDateFilter, workDateFrom, workDateTo,
      includeCancelled, includeCompleted]
  );

  const sorted = useMemo(() => sortProductionRows(filtered, sortBy), [filtered, sortBy]);
  const displayRows = useMemo(() => {
    const expanded = workDateFilter === "hari_ini"
      ? buildHariIniDisplayRows(sorted, undefined, includeCompleted)
      : buildProductionScheduleDisplayRows(sorted);
    const ordered = sortScheduleDisplayRows(expanded, sortBy);
    return applyProductionScheduleDisplayRowFilters(ordered, { operatorFilter });
  }, [sorted, workDateFilter, includeCompleted, sortBy, operatorFilter]);
  const tableVisiblePoCount = useMemo(
    () => countUniqueProductionSchedulePos(displayRows),
    [displayRows],
  );
  const summaryMetrics = useMemo(() => buildProductionScheduleSummary(filtered), [filtered]);
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
            <DateFilterTodayShortcut
              testid="prod-th-today-deadline"
              onClick={() => {
                const next = applyDeadlineTodayShortcut();
                setDateFrom(next.dateFrom);
                setDateTo(next.dateTo);
              }}
            />
          </>
        );
      case "work_date":
        return (
          <>
            <select
              value={workDateFilter}
              onChange={(e) => {
                const next = applyWorkDateFilterControl(e.target.value);
                setWorkDateFilter(next.workDateFilter);
                if (Object.prototype.hasOwnProperty.call(next, "workDateFrom")) setWorkDateFrom(next.workDateFrom);
                if (Object.prototype.hasOwnProperty.call(next, "workDateTo")) setWorkDateTo(next.workDateTo);
              }}
              data-testid="prod-workdate-filter"
            >
              <option value="all">Semua Pengerjaan</option>
              <option value="hari_ini">Hari Ini</option>
              <option value="scheduled">Sudah Dijadwalkan</option>
              <option value="unscheduled">Belum Dijadwalkan</option>
            </select>
            <label>Dari <input type="date" value={workDateFrom} onChange={(e) => {
              const next = applyWorkDateRangeControl(e.target.value, workDateTo, workDateFilter);
              setWorkDateFilter(next.workDateFilter);
              setWorkDateFrom(next.workDateFrom);
              setWorkDateTo(next.workDateTo);
            }} data-testid="prod-workdate-from"/></label>
            <label>Sampai <input type="date" value={workDateTo} onChange={(e) => {
              const next = applyWorkDateRangeControl(workDateFrom, e.target.value, workDateFilter);
              setWorkDateFilter(next.workDateFilter);
              setWorkDateFrom(next.workDateFrom);
              setWorkDateTo(next.workDateTo);
            }} data-testid="prod-workdate-to"/></label>
            <DateFilterTodayShortcut
              testid="prod-th-today-work_date"
              onClick={() => {
                const next = applyWorkDateTodayShortcut(workDateFilter);
                setWorkDateFilter(next.workDateFilter);
                setWorkDateFrom(next.workDateFrom);
                setWorkDateTo(next.workDateTo);
              }}
            />
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

  const resetColumnWidths = useCallback(() => {
    clearProductionScheduleColWidths(listUserId);
    setColWidths({ ...PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS });
  }, [listUserId]);

  const resetOneColumnWidth = useCallback((key) => {
    setColWidths((prev) => {
      const next = {
        ...prev,
        [key]: PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS[key] ?? prev[key],
      };
      writeProductionScheduleColWidths(next, listUserId);
      return next;
    });
  }, [listUserId]);

  const onColResizePointerDown = useCallback((event, key) => {
    if (event.button != null && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const handleEl = event.currentTarget;
    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startW = colWidths[key] ?? PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS[key];
    const zoom = Number(sheetZoom.zoom) > 0 ? Number(sheetZoom.zoom) : 1;
    let rafId = 0;
    let pendingW = startW;
    try {
      handleEl.setPointerCapture(pointerId);
    } catch {
      /* ignore */
    }
    document.body.classList.add("prod-col-resizing");
    handleEl.classList.add("is-active");

    const flushWidth = () => {
      rafId = 0;
      const nextW = pendingW;
      setColWidths((prev) => {
        if (prev[key] === nextW) return prev;
        return { ...prev, [key]: nextW };
      });
    };

    const onMove = (moveEvent) => {
      if (moveEvent.pointerId !== pointerId) return;
      pendingW = productionScheduleResizeNextWidth(key, startW, startX, moveEvent.clientX, zoom);
      if (!rafId) rafId = window.requestAnimationFrame(flushWidth);
    };
    const onUp = (upEvent) => {
      if (upEvent.pointerId !== pointerId) return;
      handleEl.removeEventListener("pointermove", onMove);
      handleEl.removeEventListener("pointerup", onUp);
      handleEl.removeEventListener("pointercancel", onUp);
      if (rafId) {
        window.cancelAnimationFrame(rafId);
        rafId = 0;
      }
      try {
        handleEl.releasePointerCapture(pointerId);
      } catch {
        /* already released */
      }
      handleEl.classList.remove("is-active");
      document.body.classList.remove("prod-col-resizing");
      const endX = Number.isFinite(upEvent.clientX) ? upEvent.clientX : startX;
      const finalW = productionScheduleResizeNextWidth(key, startW, startX, endX, zoom);
      setColWidths((prev) => {
        const next = { ...prev, [key]: finalW };
        writeProductionScheduleColWidths(next, listUserId);
        return next;
      });
      if (colResizeRef.current?.pointerId === pointerId) colResizeRef.current = null;
    };

    colResizeRef.current = { pointerId, handleEl, onMove, onUp, cancelRaf: () => {
      if (rafId) window.cancelAnimationFrame(rafId);
      rafId = 0;
    } };
    handleEl.addEventListener("pointermove", onMove);
    handleEl.addEventListener("pointerup", onUp);
    handleEl.addEventListener("pointercancel", onUp);
  }, [colWidths, listUserId, sheetZoom.zoom]);

  useEffect(() => () => {
    const active = colResizeRef.current;
    if (!active) return;
    active.handleEl.removeEventListener("pointermove", active.onMove);
    active.handleEl.removeEventListener("pointerup", active.onUp);
    active.handleEl.removeEventListener("pointercancel", active.onUp);
    active.cancelRaf?.();
    document.body.classList.remove("prod-col-resizing");
    colResizeRef.current = null;
  }, []);

  const sheetStyle = useMemo(() => ({
    ...productionSheetZoomStyle(sheetZoom.zoom),
    ...productionScheduleStickyCssVars(colWidths),
  }), [sheetZoom.zoom, colWidths]);

  const initialLoading = busy && !hasLoadedOnce && rows.length === 0;
  const refreshing = (busy || schedIsPolling) && hasLoadedOnce;

  return (
    <div className="prod-page prod-schedule-page" data-testid="production-schedule-page">
      <div className="prod-schedule-chrome">
        <div className="prod-schedule-heading" data-testid="prod-schedule-heading">
          <div className="prod-schedule-heading-main">
            <h1 className="prod-schedule-title" data-testid="prod-schedule-title">Jadwal Produksi</h1>
            {hasLoadedOnce && Number.isFinite(Number(total)) && (
              <span className="prod-schedule-count-pill" data-testid="prod-schedule-count">
                {Number(total).toLocaleString("id-ID")} pekerjaan
              </span>
            )}
          </div>
          <div className="prod-schedule-heading-actions">
            <button className="ghost sm" onClick={refreshSched} disabled={schedIsPolling || busy} data-testid="prod-refresh-button" title="Refresh sekarang">
              <RefreshCw size={12} className={schedIsPolling ? "spin" : ""}/> Refresh
            </button>
            {canExport && (
              <>
                <button className="ghost sm" disabled={exporting || !filtered.length} onClick={() => download("filtered")} data-testid="prod-export-filtered"><Download size={12}/> Export Terfilter</button>
                <button className="ghost sm" disabled={exporting} onClick={() => download("all")} data-testid="prod-export-all"><Download size={12}/> Export Semua</button>
              </>
            )}
          </div>
        </div>
        {schedLastUpdated && (
          <span data-testid="prod-last-updated" hidden>
            {new Date(schedLastUpdated).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}
          </span>
        )}

        <div className="prod-toolbar" data-testid="prod-filters">
          <div className="prod-schedule-status-tabs" role="tablist" aria-label="Status jadwal produksi" data-testid="prod-status-tabs">
            {PRODUCTION_SCHEDULE_STATUS_TABS.map((tab) => {
              const selected = scheduleStatusTab === tab.id;
              const count = statusTabCounts == null ? null : Number(statusTabCounts[tab.id] ?? 0);
              return (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  className={
                    "prod-schedule-status-tab"
                    + (selected ? " is-active" : "")
                    + (tab.id === "hold" ? " is-hold" : "")
                  }
                  onClick={() => setScheduleStatusTab(tab.id)}
                  data-testid={`prod-status-tab-${tab.id}`}
                >
                  <span className="prod-schedule-status-tab-label">{tab.label}</span>
                  {count != null && (
                    <span className="prod-schedule-status-tab-count" data-testid={`prod-status-tab-count-${tab.id}`}>
                      {count.toLocaleString("id-ID")}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <ProductionScheduleSearchCluster
            value={q}
            onChange={(e) => setQ(e.target.value)}
            inputTestId="prod-search"
            searchButtonTestId="prod-search-btn"
          />
          <ProductionScheduleRingkasan metrics={summaryMetrics} testidPrefix="prod" />
          <span className="muted small" data-testid="prod-total-visible">{mode === "tabel" ? tableVisiblePoCount : filtered.length} / {rows.length} entri</span>
          <label className="prod-check"><input type="checkbox" checked={includeCompleted} onChange={(e) => setIncludeCompleted(e.target.checked)} data-testid="prod-include-completed"/> Selesai</label>
          <label className="prod-check"><input type="checkbox" checked={includeCancelled} onChange={(e) => setIncludeCancelled(e.target.checked)} data-testid="prod-include-cancelled"/> Cancelled</label>
          <button className="ghost sm prod-filter-reset" onClick={resetFilters} data-testid="prod-filter-reset" disabled={!filtersActive && sortBy === DESKTOP_PRODUCTION_SCHEDULE_SORT_DEFAULT}>
            Reset Filter
          </button>
          <button type="button" className="ghost sm prod-reset-widths" onClick={resetColumnWidths} data-testid="prod-reset-col-widths" title="Reset lebar kolom">
            Reset Lebar
          </button>
          <div className="prod-mode" role="tablist" data-testid="prod-mode-switch">
            <button role="tab" className={mode === "kartu" ? "active" : ""} onClick={() => setMode("kartu")} data-testid="prod-mode-kartu"><LayoutGrid size={13}/> Kartu</button>
            <button role="tab" className={mode === "tabel" ? "active" : ""} onClick={() => setMode("tabel")} data-testid="prod-mode-tabel"><TableIcon size={13}/> Tabel</button>
          </div>
        </div>

        {err && <div className="error" data-testid="prod-error" role="alert">{err}</div>}
        {initialLoading && (
          <div className="prod-schedule-loading" data-testid="prod-loading" role="status" aria-live="polite">
            Memuat jadwal produksi…
          </div>
        )}
        {refreshing && !initialLoading && !err && (
          <div className="prod-schedule-refreshing" data-testid="prod-refreshing" role="status" aria-live="polite">
            Menyegarkan data…
          </div>
        )}
      </div>

      {mode === "tabel" ? (
        <div
          className="prod-table-wrap prod-schedule-grid production-sheet production-sheet-viewport"
          data-testid="prod-table"
          ref={sheetZoom.setViewportRef}
          style={sheetStyle}
        >
          {initialLoading && (
            <div className="prod-schedule-skeleton" data-testid="prod-skeleton" aria-hidden="true">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="prod-schedule-skeleton-row" />
              ))}
            </div>
          )}
          <table className="production-sheet-table" data-testid="production-sheet-table" hidden={initialLoading}>
            <colgroup>
              {DESKTOP_COLS.map((c) => (
                <col
                  key={c.key}
                  className={c.sticky === "po" ? "prod-col-po" : c.sticky === "customer" ? "prod-col-customer" : undefined}
                  style={productionSheetColumnStyle(c, colWidths[c.key])}
                />
              ))}
            </colgroup>
            <thead className="production-sheet-header">
              <tr className="prod-col-group-row" data-testid="prod-col-groups">
                {desktopProductionScheduleGroupSpans(DESKTOP_COLS).map((group) => (
                  <th
                    key={group.id}
                    colSpan={group.span}
                    scope="colgroup"
                    className={`prod-col-group-band prod-col-group-${group.id}${group.sticky ? " prod-sticky-identity-span" : ""}`}
                    data-testid={`prod-col-group-${group.id}`}
                  >
                    {group.label}
                  </th>
                ))}
              </tr>
              <tr className="prod-col-label-row">
                {DESKTOP_COLS.map((c) => {
                  const active = columnFilterActive(c.filter, filterState);
                  const thClass = [
                    "prod-col-label",
                    c.num || c.align === "right" || c.type === "qty" ? "num" : "",
                    c.sticky === "po" ? "prod-sticky-col prod-sticky-po" : "",
                    c.sticky === "customer" ? "prod-sticky-col prod-sticky-customer" : "",
                    c.groupStart ? "prod-group-start" : "",
                  ].filter(Boolean).join(" ");
                  const resizeSlot = (
                    <ProdColResizeHandle
                      colKey={c.key}
                      onPointerDown={onColResizePointerDown}
                      onDoubleClick={resetOneColumnWidth}
                    />
                  );
                  return (
                    <th key={c.key} className={thClass} data-testid={c.testid} scope="col" style={productionSheetColumnStyle(c, colWidths[c.key])}>
                      {c.filter ? (
                        <HeaderFilter
                          label={c.label}
                          testid={`prod-th-filter-${c.key}`}
                          active={active}
                          sortable={!!c.sort}
                          sortHint={c.sort === "work_date" ? "Urutkan tanggal pengerjaan" : "Urutkan deadline"}
                          onSort={() => cycleSort(c.sort)}
                          onClear={() => clearColumn(c.filter)}
                          resizeSlot={resizeSlot}
                        >
                          {renderColumnFilter(c)}
                        </HeaderFilter>
                      ) : (
                        <div className="prod-th-inner">
                          <span className="prod-th-label">{c.label}</span>
                          {resizeSlot}
                        </div>
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {displayRows.map((r) => {
                const qtyProg = desktopPrintQtyDisplay(r);
                const cetakProg = operatorQtyCetakProgress(r, r.displayOperator?.splits);
                const isToday = isScheduledToday(r);
                const rowClass = [
                  r.hold ? "hold" : "",
                  r.cancelled ? "cancelled" : "",
                  isProductionScheduleUrgent(r) ? "prod-row-urgent" : "",
                  r.delivery_deadline_status === "overdue" ? "prod-row-overdue" : "",
                ].filter(Boolean).join(" ");
                return (
                <tr key={r.displayRowKey} data-testid={`prod-row-${r.po_number}`} data-row-key={r.displayRowKey} className={rowClass}>
                  <ProductionScheduleSheetCells
                    row={r}
                    variant="desktop"
                    columns={DESKTOP_COLS}
                    qtyProg={qtyProg}
                    cetakProg={cetakProg}
                    isToday={isToday}
                    poHref={productionSchedulePoHref(r.id, { stage: "penjadwalan" })}
                  />
                </tr>
                );
              })}
              {!sorted.length && !busy && !initialLoading && (
                <tr>
                  <td colSpan={DESKTOP_COLS.length} className="production-sheet-empty" data-testid="prod-empty">
                    Tidak ada PO cocok dengan filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <section className="prod-cards prod-schedule-cards" data-testid="prod-cards">
          {sorted.map((r) => (
            <Link key={r.id} to={productionSchedulePoHref(r.id, { stage: "penjadwalan" })} className="prod-card" data-testid={`prod-card-${r.po_number}`}>
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
