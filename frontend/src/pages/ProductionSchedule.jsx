import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import "../productionSheet.css";
import "../productionCompact.css";
import { Download, Filter, LayoutGrid, RefreshCw, Table as TableIcon } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import useAutoRefresh from "../lib/useAutoRefresh";
import useProductionSheetZoom from "../hooks/useProductionSheetZoom";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import ProductionScheduleSheetCells from "../components/ProductionScheduleSheetCells";
import ProductionScheduleRingkasan, { ProductionScheduleSearchCluster } from "../components/ProductionScheduleRingkasan";
import PoIdentityBadges from "../components/PoIdentityBadges";
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
  PRODUCTION_SCHEDULE_SCOPE_ALL,
  PRODUCTION_SCHEDULE_SCOPE_DEFAULT,
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
  normalizeProductionScheduleScope,
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
  fetchProductionDashboardPages,
  fetchProductionDashboardRefresh,
  productionDashboardRequestParams,
  productionSheetColumnStyle,
} from "../lib/productionScheduleTable";
import { productionSheetZoomStyle } from "../lib/productionSheetZoom";
import {
  capturePoOpenFromList,
  poNavStateFromList,
  readListUiState,
  storageUserKey,
  writeListUiState,
} from "../lib/poListReturn";
import { productionSchedulePoHref } from "../lib/workPanel";

const LIST_UI_ROUTE_KEY = "production-schedule";

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
  return {
    rows: nextRows,
    total,
    completeness: d?.completeness || "",
    readLimit: d?.read_limit,
    scopeComplete: d?.scope_complete,
    nextCursor: d?.next_cursor || null,
    scheduleScope: d?.schedule_scope || "",
    candidateCount: d?.candidate_count,
    matchedCount: d?.matched_count,
    scanCapped: Boolean(d?.scan_capped),
    refresh: Boolean(d?.refresh),
    reconcileStale: Boolean(d?.reconcile_stale),
    reconcileFailed: Boolean(d?.reconcile_failed),
    reconcileOffset: Number.isFinite(Number(d?.reconcile_offset)) ? Number(d.reconcile_offset) : 0,
  };
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
  const fullLabel = String(label || "");
  const sortTitle = sortable
    ? (sortHint ? `${fullLabel} — ${sortHint}` : fullLabel)
    : fullLabel;
  return (
    <div className="prod-th-inner" title={fullLabel}>
      {sortable ? (
        <button type="button" className="prod-th-label is-sort" onClick={onSort} title={sortTitle}>
          {label}
        </button>
      ) : (
        <span className="prod-th-label" title={fullLabel}>{label}</span>
      )}
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className={"prod-th-filter" + (active ? " is-active" : "")}
            aria-label={`Filter ${label}`}
            title={`Filter ${fullLabel}`}
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

function readProductionScheduleListUi(userKey) {
  const saved = readListUiState(userKey, LIST_UI_ROUTE_KEY, {});
  const filters = saved.filters && typeof saved.filters === "object" ? saved.filters : {};
  // Legacy sessions without scheduleScope must land on the new default — never "all".
  const scheduleScope = Object.prototype.hasOwnProperty.call(filters, "scheduleScope")
    ? normalizeProductionScheduleScope(filters.scheduleScope)
    : PRODUCTION_SCHEDULE_SCOPE_DEFAULT;
  const mode = saved.mode === "kartu" ? "kartu" : "tabel";
  const columnView = saved.columnView === "ringkas" ? "ringkas" : "lengkap";
  const sortBy = typeof saved.sortBy === "string" && saved.sortBy
    ? saved.sortBy
    : DESKTOP_PRODUCTION_SCHEDULE_SORT_DEFAULT;
  const scroll = saved.scroll && typeof saved.scroll === "object" ? saved.scroll : {};
  return {
    filters: { ...FILTER_DEFAULTS, ...filters, scheduleScope },
    mode,
    columnView,
    sortBy,
    scrollLeft: Number.isFinite(Number(scroll.left)) ? Number(scroll.left) : 0,
    scrollTop: Number.isFinite(Number(scroll.top)) ? Number(scroll.top) : 0,
  };
}

export default function ProductionSchedule() {
  const { user, hasPerm } = useAuth();
  const location = useLocation();
  const userKey = storageUserKey(user);
  const listUserId = user?.id || user?.email || user?.sub || "";
  const canExport = hasPerm("dashboard.production.export");
  const initialUiRef = useRef(null);
  if (initialUiRef.current == null) {
    initialUiRef.current = readProductionScheduleListUi(userKey);
  }
  const initialUi = initialUiRef.current;
  const [mode, setMode] = useState(initialUi.mode);
  const [columnView, setColumnView] = useState(initialUi.columnView);
  const visibleColumns = useMemo(() => columnView === "ringkas"
    ? DESKTOP_COLS.filter((col) => ["po", "customer", "work_date", "operator", "item", "qty_cetak", "qty_operator", "progress", "prio", "buffer", "deadline", "status"].includes(col.key))
    : DESKTOP_COLS, [columnView]);
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [hasLoadedOnce, setHasLoadedOnce] = useState(false);
  const [err, setErr] = useState("");
  const [total, setTotal] = useState(0);
  const [scheduleRead, setScheduleRead] = useState(null);
  const [colWidths, setColWidths] = useState(() => readProductionScheduleColWidths(listUserId));
  const colResizeRef = useRef(null);
  const [q, setQ] = useState(initialUi.filters.q);
  /** Debounced server search — keeps typing/focus local while still querying beyond cache. */
  const [serverQ, setServerQ] = useState(() => String(initialUi.filters.q || "").trim());
  const [poFilter, setPoFilter] = useState(initialUi.filters.poFilter);
  const [statusFilter, setStatusFilter] = useState(initialUi.filters.statusFilter);
  const [operatorFilter, setOperatorFilter] = useState(initialUi.filters.operatorFilter);
  const [customerFilter, setCustomerFilter] = useState(initialUi.filters.customerFilter);
  const [urgentOnly, setUrgentOnly] = useState(initialUi.filters.urgentOnly);
  const [holdOnly, setHoldOnly] = useState(initialUi.filters.holdOnly);
  const [scheduleStatusTab, setScheduleStatusTab] = useState(initialUi.filters.scheduleStatusTab);
  const [scheduleScope, setScheduleScope] = useState(
    normalizeProductionScheduleScope(initialUi.filters.scheduleScope),
  );
  const [screenerFilter, setScreenerFilter] = useState(initialUi.filters.screenerFilter);
  const [helperFilter, setHelperFilter] = useState(initialUi.filters.helperFilter);
  const [packingFilter, setPackingFilter] = useState(initialUi.filters.packingFilter);
  // Desktop has no Delivery header control — never apply a hidden persisted value.
  const [deliveryFilter, setDeliveryFilter] = useState(FILTER_DEFAULTS.deliveryFilter);
  const [bufferFilter, setBufferFilter] = useState(initialUi.filters.bufferFilter);
  const [dateFrom, setDateFrom] = useState(initialUi.filters.dateFrom);
  const [dateTo, setDateTo] = useState(initialUi.filters.dateTo);
  const [workDateFilter, setWorkDateFilter] = useState(initialUi.filters.workDateFilter);
  const [workDateFrom, setWorkDateFrom] = useState(initialUi.filters.workDateFrom);
  const [workDateTo, setWorkDateTo] = useState(initialUi.filters.workDateTo);
  const [sortBy, setSortBy] = useState(initialUi.sortBy);
  const [includeCancelled, setIncludeCancelled] = useState(initialUi.filters.includeCancelled);
  const [includeCompleted, setIncludeCompleted] = useState(initialUi.filters.includeCompleted);
  const [exporting, setExporting] = useState(false);
  const sheetZoom = useProductionSheetZoom();
  /** Monotonic id for list GETs — success/error apply only if still the latest request. */
  const listReqIdRef = useRef(0);
  const scrollRestoredRef = useRef(false);
  const pendingScrollRef = useRef({
    left: initialUi.scrollLeft,
    top: initialUi.scrollTop,
  });
  const toolbarRef = useRef(null);
  const toolbarScrollInitRef = useRef(false);
  const [toolbarOverflow, setToolbarOverflow] = useState({ left: false, right: false });
  const persistReadyRef = useRef(false);
  const rowsRef = useRef(rows);
  const scheduleReadRef = useRef(scheduleRead);
  const reconcileOffsetRef = useRef(0);
  rowsRef.current = rows;
  scheduleReadRef.current = scheduleRead;

  useEffect(() => {
    setColWidths(readProductionScheduleColWidths(listUserId));
  }, [listUserId]);

  const updateToolbarOverflow = useCallback(() => {
    const el = toolbarRef.current;
    if (!el) return;
    // scrollbar-gutter / subpixel can leave a few px of unreachable scrollWidth.
    const remain = el.scrollWidth - el.clientWidth - el.scrollLeft;
    const left = el.scrollLeft > 2;
    const right = remain > 12;
    setToolbarOverflow((prev) => (
      prev.left === left && prev.right === right ? prev : { left, right }
    ));
  }, []);

  // Toolbar scroll is independent of table/list UI restore. Start at left so
  // "Semua" stays visible; do not rewrite table viewport scroll here.
  useLayoutEffect(() => {
    const el = toolbarRef.current;
    if (!el) return undefined;
    if (!toolbarScrollInitRef.current) {
      el.scrollLeft = 0;
      toolbarScrollInitRef.current = true;
    }
    updateToolbarOverflow();
    const onScroll = () => updateToolbarOverflow();
    el.addEventListener("scroll", onScroll, { passive: true });
    const ro = typeof ResizeObserver !== "undefined"
      ? new ResizeObserver(() => updateToolbarOverflow())
      : null;
    ro?.observe(el);
    window.addEventListener("resize", updateToolbarOverflow);
    return () => {
      el.removeEventListener("scroll", onScroll);
      ro?.disconnect();
      window.removeEventListener("resize", updateToolbarOverflow);
    };
  }, [updateToolbarOverflow, mode, columnView, hasLoadedOnce]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      setServerQ(String(q || "").trim());
    }, 300);
    return () => window.clearTimeout(handle);
  }, [q]);

  const dashboardParams = useMemo(
    () => productionDashboardRequestParams({
      includeCancelled,
      includeCompleted,
      workDateFilter,
      workDateFrom,
      workDateTo,
      dateFrom,
      dateTo,
      scheduleScope,
      q: serverQ,
    }),
    [includeCancelled, includeCompleted, workDateFilter, workDateFrom, workDateTo, dateFrom, dateTo, scheduleScope, serverQ],
  );

  const applyDashboardPayload = useCallback((payload) => {
    const ingested = ingestProductionDashboard(payload);
    setRows(ingested.rows);
    setTotal(ingested.total);
    setScheduleRead(ingested);
    setHasLoadedOnce(true);
    setErr("");
  }, []);

  useEffect(() => {
    let cancelled = false;
    const reqId = ++listReqIdRef.current;
    setBusy(true);
    setErr("");
    fetchProductionDashboardPages(api, dashboardParams)
      .then((payload) => {
        if (cancelled || reqId !== listReqIdRef.current) return;
        applyDashboardPayload(payload);
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
  }, [dashboardParams, applyDashboardPayload]);

  const runFullScheduleReload = useCallback(async () => {
    const reqId = ++listReqIdRef.current;
    setBusy(true);
    try {
      const payload = await fetchProductionDashboardPages(api, dashboardParams);
      if (reqId !== listReqIdRef.current) return;
      applyDashboardPayload(payload);
    } catch (error) {
      if (reqId !== listReqIdRef.current) return;
      setErr(error.response?.data?.detail || "Gagal memuat");
      setHasLoadedOnce(true);
    } finally {
      if (reqId === listReqIdRef.current) setBusy(false);
    }
  }, [dashboardParams, applyDashboardPayload]);

  const { lastUpdated: schedLastUpdated, isPolling: schedIsPolling } = useAutoRefresh(
    async () => {
      // Snapshot only — do not bump listReqIdRef (that would cancel an in-flight full reload).
      const reqId = listReqIdRef.current;
      try {
        const prior = scheduleReadRef.current;
        const payload = await fetchProductionDashboardRefresh(api, dashboardParams, {
          prevRows: rowsRef.current,
          priorCompleteness: prior?.completeness,
          priorScopeComplete: prior?.scopeComplete,
          priorNextCursor: prior?.nextCursor,
          priorReadLimit: prior?.readLimit,
          priorMatchedCount: prior?.matchedCount,
          priorCandidateCount: prior?.candidateCount,
          priorScanCapped: prior?.scanCapped,
          reconcileOffset: reconcileOffsetRef.current,
        });
        return { reqId, payload };
      } catch (error) {
        if (error && typeof error === "object") error.__prodListReqId = reqId;
        throw error;
      }
    },
    {
      intervalMs: 30_000,
      enabled: hasLoadedOnce && !busy,
      onData: (result) => {
        const reqId = result?.reqId;
        if (reqId == null || reqId !== listReqIdRef.current) return;
        if (Number.isFinite(Number(result?.payload?.reconcile_offset))) {
          reconcileOffsetRef.current = Number(result.payload.reconcile_offset);
        }
        applyDashboardPayload(result.payload);
        // Partial poll failure keeps rows but must still surface an error status.
        if (result?.payload?.reconcile_failed) {
          setErr(result.payload.refresh_error || "Gagal memverifikasi ulang sebagian jadwal");
        }
      },
      onError: (error) => {
        const reqId = error?.__prodListReqId;
        if (reqId == null || reqId !== listReqIdRef.current) return;
        // Hard poll failure (no usable cache merge) — keep rows, mark stale.
        setScheduleRead((prev) => (prev ? {
          ...prev,
          completeness: "incomplete",
          reconcileStale: true,
          reconcileFailed: true,
        } : prev));
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
    setServerQ("");
    setPoFilter(FILTER_DEFAULTS.poFilter);
    setStatusFilter(FILTER_DEFAULTS.statusFilter);
    setOperatorFilter(FILTER_DEFAULTS.operatorFilter);
    setCustomerFilter(FILTER_DEFAULTS.customerFilter);
    setUrgentOnly(FILTER_DEFAULTS.urgentOnly);
    setHoldOnly(FILTER_DEFAULTS.holdOnly);
    setScheduleStatusTab(FILTER_DEFAULTS.scheduleStatusTab);
    setScheduleScope(PRODUCTION_SCHEDULE_SCOPE_DEFAULT);
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
    scheduleStatusTab, scheduleScope,
    screenerFilter, helperFilter, packingFilter,
    // Force all: desktop Jadwal Produksi has no delivery filter control (F5).
    deliveryFilter: FILTER_DEFAULTS.deliveryFilter,
    bufferFilter,
    dateFrom, dateTo, workDateFilter, workDateFrom, workDateTo, sortBy,
    includeCancelled, includeCompleted,
  };

  /** C2 base: all active filters except the status tab itself. */
  const filterContextRows = useMemo(
    () => applyProductionFilters(rows, { ...filterState, scheduleStatusTab: "all" }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rows, q, poFilter, statusFilter, operatorFilter, customerFilter, urgentOnly, holdOnly,
      scheduleScope,
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
      scheduleStatusTab, scheduleScope,
      screenerFilter, helperFilter, packingFilter, deliveryFilter, bufferFilter,
      dateFrom, dateTo, workDateFilter, workDateFrom, workDateTo,
      includeCancelled, includeCompleted]
  );

  const listNavState = poNavStateFromList({
    pathname: location.pathname,
    search: location.search,
  });

  const onOpenPo = useCallback((orderId) => {
    const viewport = sheetZoom.viewportRef?.current;
    capturePoOpenFromList({
      user,
      orderId,
      pathname: location.pathname,
      search: location.search,
      scrollY: viewport ? viewport.scrollTop : (window.scrollY || 0),
    });
  }, [user, location.pathname, location.search, sheetZoom.viewportRef]);

  useEffect(() => {
    if (!persistReadyRef.current) {
      persistReadyRef.current = true;
      return undefined;
    }
    const viewport = sheetZoom.viewportRef?.current;
    writeListUiState(userKey, LIST_UI_ROUTE_KEY, {
      filters: {
        q,
        poFilter,
        statusFilter,
        operatorFilter,
        customerFilter,
        urgentOnly,
        holdOnly,
        scheduleStatusTab,
        scheduleScope: normalizeProductionScheduleScope(scheduleScope),
        screenerFilter,
        helperFilter,
        packingFilter,
        deliveryFilter: FILTER_DEFAULTS.deliveryFilter,
        bufferFilter,
        dateFrom,
        dateTo,
        workDateFilter,
        workDateFrom,
        workDateTo,
        includeCancelled,
        includeCompleted,
      },
      sortBy,
      mode,
      columnView,
      scroll: {
        left: viewport ? viewport.scrollLeft : 0,
        top: viewport ? viewport.scrollTop : 0,
      },
    });
    return undefined;
  }, [
    userKey, q, poFilter, statusFilter, operatorFilter, customerFilter, urgentOnly, holdOnly,
    scheduleStatusTab, scheduleScope, screenerFilter, helperFilter, packingFilter, deliveryFilter,
    bufferFilter, dateFrom, dateTo, workDateFilter, workDateFrom, workDateTo, includeCancelled,
    includeCompleted, sortBy, mode, columnView, sheetZoom.viewportRef,
  ]);

  useEffect(() => {
    const el = sheetZoom.viewportRef?.current;
    if (!el) return undefined;
    const onScroll = () => {
      pendingScrollRef.current = { left: el.scrollLeft, top: el.scrollTop };
      writeListUiState(userKey, LIST_UI_ROUTE_KEY, {
        ...(readListUiState(userKey, LIST_UI_ROUTE_KEY, {}) || {}),
        scroll: { left: el.scrollLeft, top: el.scrollTop },
      });
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [userKey, sheetZoom.viewportRef, mode, hasLoadedOnce]);

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

  useLayoutEffect(() => {
    if (scrollRestoredRef.current) return;
    if (!hasLoadedOnce || busy) return;
    const target = pendingScrollRef.current;
    if (!target || (target.left <= 0 && target.top <= 0)) {
      scrollRestoredRef.current = true;
      return;
    }
    if (mode === "tabel") {
      const el = sheetZoom.viewportRef?.current;
      if (!el) return;
      el.scrollLeft = target.left;
      el.scrollTop = target.top;
    } else {
      window.scrollTo(0, target.top || 0);
    }
    scrollRestoredRef.current = true;
  }, [hasLoadedOnce, busy, mode, sheetZoom.viewportRef, displayRows.length]);

  const cycleSort = (col) => {
    if (col === "deadline") setSortBy("deadline");
    else if (col === "work_date") setSortBy((prev) => (prev === "work_date_asc" ? "work_date" : "work_date_asc"));
  };

  const scheduleExportReady = scheduleRead?.completeness === "complete"
    && !scheduleRead?.reconcileStale
    && !scheduleRead?.reconcileFailed
    && !scheduleRead?.scanCapped;

  const download = async (scope) => {
    if (!scheduleExportReady) {
      alert(
        "Ekspor dibatalkan: daftar jadwal belum lengkap atau masih stale "
        + "(batas baca/rekonsiliasi). Gunakan Refresh sampai data complete, lalu coba lagi.",
      );
      return;
    }
    if (scope === "filtered" && !filtered.length) {
      alert("Ekspor dibatalkan: tidak ada PO terfilter untuk diekspor.");
      return;
    }
    setExporting(true);
    try {
      const payload = {
        scope,
        schedule_scope: normalizeProductionScheduleScope(scheduleScope),
        include_cancelled: includeCancelled,
        include_completed: includeCompleted,
        require_complete: true,
        client_completeness: scheduleRead?.completeness || "incomplete",
      };
      if (scope === "filtered") {
        payload.ids = filtered.map((r) => r.id).filter(Boolean);
      }
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
      const detail = e.response?.data?.detail;
      alert(typeof detail === "string" ? detail : (detail ? JSON.stringify(detail) : "Gagal export Excel"));
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
    <div className="prod-page prod-schedule-page production-compact" data-testid="production-schedule-page">
      <div className="prod-schedule-chrome">
        <div className="prod-schedule-heading" data-testid="prod-schedule-heading">
          <div className="prod-schedule-heading-main">
            <h1 className="prod-schedule-title" data-testid="prod-schedule-title">Jadwal Produksi</h1>
            <p className="muted small" data-testid="prod-schedule-target-hint">Target Awal dikunci dari editor pembagian operator. Tanggal baris jadwal tidak mengunci target.</p>
            {hasLoadedOnce && Number.isFinite(Number(total)) && (
              <span className="prod-schedule-count-pill" data-testid="prod-schedule-count">
                {Number(total).toLocaleString("id-ID")} pekerjaan
              </span>
            )}
            {scheduleRead?.completeness === "incomplete" && (
              <p className="muted small" data-testid="prod-schedule-incomplete" role="status">
                {scheduleRead.reconcileStale
                  ? "Sebagian data jadwal belum diverifikasi ulang (batas rekonsiliasi/poll). Daftar tidak dihapus; gunakan Refresh untuk pemuatan penuh."
                  : (
                    <>
                      Daftar belum lengkap (batas keamanan baca
                      {scheduleRead.readLimit != null ? ` ${Number(scheduleRead.readLimit).toLocaleString("id-ID")}` : ""}
                      ). Gunakan pencarian/filter histori atau Refresh untuk mencoba lagi — ini bukan berarti tidak ada PO.
                    </>
                  )}
              </p>
            )}
          </div>
          <div className="prod-schedule-heading-actions">
            {mode === "tabel" && (
              <div className="production-column-view" role="group" aria-label="Kelengkapan kolom">
                {["ringkas", "lengkap"].map((view) => (
                  <button key={view} type="button" className="ghost sm"
                    aria-pressed={columnView === view}
                    data-testid={`prod-columns-${view}`}
                    onClick={() => setColumnView(view)}>
                    {view === "ringkas" ? "Ringkas" : "Lengkap"}
                  </button>
                ))}
              </div>
            )}
            <button className="ghost sm" onClick={runFullScheduleReload} disabled={schedIsPolling || busy} data-testid="prod-refresh-button" title="Refresh sekarang">
              <RefreshCw size={12} className={(schedIsPolling || busy) ? "spin" : ""}/> Refresh
            </button>
            {canExport && (
              <>
                <button
                  className="ghost sm"
                  disabled={exporting || !filtered.length || !scheduleExportReady}
                  onClick={() => download("filtered")}
                  data-testid="prod-export-filtered"
                  title={scheduleExportReady ? "Export PO terfilter" : "Tunggu daftar complete sebelum ekspor"}
                >
                  <Download size={12}/> Export Terfilter
                </button>
                <button
                  className="ghost sm"
                  disabled={exporting || !scheduleExportReady}
                  onClick={() => download("all")}
                  data-testid="prod-export-all"
                  title={scheduleExportReady ? "Export sesuai cakupan Jadwal Produksi" : "Tunggu daftar complete sebelum ekspor"}
                >
                  <Download size={12}/> Export Semua
                </button>
              </>
            )}
          </div>
        </div>
        {schedLastUpdated && (
          <span data-testid="prod-last-updated" hidden>
            {new Date(schedLastUpdated).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" })}
          </span>
        )}

        <div
          className={
            "prod-toolbar-shell"
            + (toolbarOverflow.left ? " is-overflow-left" : "")
            + (toolbarOverflow.right ? " is-overflow-right" : "")
          }
          data-testid="prod-toolbar-shell"
        >
          <div className="prod-toolbar" data-testid="prod-filters" ref={toolbarRef}>
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
              onSearch={(value) => setServerQ(String(value ?? q ?? "").trim())}
              inputTestId="prod-search"
              searchButtonTestId="prod-search-btn"
            />
            <label className="muted small" htmlFor="prod-schedule-scope">
              Cakupan
              <select
                id="prod-schedule-scope"
                className="prod-schedule-scope"
                value={normalizeProductionScheduleScope(scheduleScope)}
                onChange={(e) => setScheduleScope(normalizeProductionScheduleScope(e.target.value))}
                data-testid="prod-schedule-scope"
              >
                <option value={PRODUCTION_SCHEDULE_SCOPE_DEFAULT}>Bulan Ini + Belum Selesai</option>
                <option value={PRODUCTION_SCHEDULE_SCOPE_ALL}>Semua / Riwayat</option>
              </select>
            </label>
            <ProductionScheduleRingkasan
              metrics={summaryMetrics}
              testidPrefix="prod"
              basisNote="Basis: PO terfilter (termasuk tab status & filter aktif), bukan seluruh cache."
            />
            <span
              className={`muted small${
                scheduleRead?.completeness !== "complete"
                || scheduleRead?.reconcileStale
                || scheduleRead?.scanCapped
                  ? " prod-po-count-is-partial"
                  : ""
              }`}
              data-testid="prod-total-visible"
              title={
                scheduleRead?.completeness !== "complete"
                || scheduleRead?.reconcileStale
                || scheduleRead?.scanCapped
                  ? "Hitungan masih partial/stale — bukan klaim daftar lengkap"
                  : "PO unik di tampilan terfilter / PO termuat pada cakupan aktif"
              }
            >
              {mode === "tabel" ? tableVisiblePoCount : filtered.length} / {rows.length} PO
            </span>
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
        </div>

        {columnView === "ringkas" && mode === "tabel" && DESKTOP_COLS.some((col) =>
          !visibleColumns.includes(col) && columnFilterActive(col.filter, filterState)) && (
          <div className="production-hidden-filter" role="status">
            Filter kolom tambahan masih aktif.
            <button type="button" className="ghost sm" onClick={() => setColumnView("lengkap")}>Lihat filter di Lengkap</button>
          </div>
        )}
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
              {visibleColumns.map((c) => (
                <col
                  key={c.key}
                  className={c.sticky === "po" ? "prod-col-po" : c.sticky === "customer" ? "prod-col-customer" : undefined}
                  style={productionSheetColumnStyle(c, colWidths[c.key])}
                />
              ))}
            </colgroup>
            <thead className="production-sheet-header">
              <tr className="prod-col-group-row" data-testid="prod-col-groups">
                {desktopProductionScheduleGroupSpans(visibleColumns).map((group) => (
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
                {visibleColumns.map((c) => {
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
                    <th
                      key={c.key}
                      className={thClass}
                      data-testid={c.testid}
                      scope="col"
                      title={c.label}
                      style={productionSheetColumnStyle(c, colWidths[c.key])}
                    >
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
                        <div className="prod-th-inner" title={c.label}>
                          <span className="prod-th-label" title={c.label}>{c.label}</span>
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
                    columns={visibleColumns}
                    qtyProg={qtyProg}
                    cetakProg={cetakProg}
                    isToday={isToday}
                    poHref={productionSchedulePoHref(r.id, { stage: "penjadwalan" })}
                    poLinkState={listNavState}
                    onPoOpen={() => onOpenPo(r.id)}
                  />
                </tr>
                );
              })}
              {!sorted.length && !busy && !initialLoading && (
                <tr>
                  <td colSpan={visibleColumns.length} className="production-sheet-empty" data-testid="prod-empty">
                    <span className="production-sheet-empty-msg">
                      {scheduleRead?.completeness === "incomplete"
                        ? "Pencarian belum lengkap — daftar masih dibatasi batas keamanan, bukan pasti kosong."
                        : "Tidak ada PO cocok dengan filter."}
                    </span>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <section className="prod-cards prod-schedule-cards" data-testid="prod-cards">
          {sorted.map((r) => (
            <Link
              key={r.id}
              to={productionSchedulePoHref(r.id, { stage: "penjadwalan" })}
              state={listNavState}
              onClick={() => onOpenPo(r.id)}
              className="prod-card"
              data-testid={`prod-card-${r.po_number}`}
            >
              <header>
                <strong>{r.po_number}</strong>
                <PoIdentityBadges order={r} testIdPrefix={`prod-card-${r.po_number}`} />
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
          {!sorted.length && !busy && (
            <div className="empty-state" data-testid="prod-empty">
              {scheduleRead?.completeness === "incomplete"
                ? "Pencarian belum lengkap — daftar masih dibatasi batas keamanan, bukan pasti kosong."
                : "Tidak ada PO cocok dengan filter."}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

