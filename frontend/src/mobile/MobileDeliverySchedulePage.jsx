import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Filter, RefreshCw, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { formatApiError } from "../lib/errors";
import { clampPercent, terkirimTone } from "../lib/shipmentStatus";
import {
  DELIVERY_SCHEDULE_STATUS_OPTIONS,
  DELIVERY_SORT_KEYS,
  EMPTY_DELIVERY_FILTERS,
  EMPTY_DELIVERY_SORT,
  countActiveDeliveryFilters,
  cycleDeliverySort,
  matchesDeliveryScheduleFilters,
  sortDeliveryRows,
} from "../lib/deliverySchedule";
import {
  MOBILE_DELIVERY_SCHEDULE_COLS,
  MOBILE_DELIVERY_STATUS_COL_KEYS,
  canOptimizeFromDeliverySchedule,
  canViewMobileDeliverySchedule,
  deliveryRowWithProductionStatus,
  groupSjItemLines,
  indexProductionStatusByOrderId,
  mobileDeliverySheetColumnStyle,
  mobileScheduleReviewHref,
  optimizationStatusMeta,
  sjItemLineKey,
  sjItemLinesFromRow,
} from "../lib/mobileDeliverySchedule";
import { canAccessRoutePage } from "../lib/independentRoute";
import { navigateToOptimasiRute } from "../lib/mobileOpsShell";
import { sortOperationalTasks } from "../lib/mobileTaskCard";
import {
  deliveryScheduleShowsTable,
  mobileScheduleWorkHref,
  selectDeliveryScheduleTaskContext,
} from "../lib/mobileWorkspaces";
import {
  OPS_QUICK_FILTERS,
  deliveryOpsBadge,
  deliveryQuickMatch,
  deliveryQueueRank,
  matchesDeliveryOpsSearch,
  opsQuickFilterCounts,
  packingProgressText,
  sortByOpsQueue,
} from "../lib/mobileOpsQueue";
import { buildOpsRoleSummaryModel } from "../lib/mobileOpsRoleSummary";
import { canViewMobileSchedule, mobileProductionDashboardParams } from "../lib/mobileSchedule";
import MobileOpsRoleSummary from "./components/MobileOpsRoleSummary";
import OpsScheduleThead from "./components/OpsScheduleThead";
import OpsRoleMark from "./components/OpsRoleMark";
import {
  MOBILE_DELIVERY_FILTER_KEY,
  MOBILE_DELIVERY_SCROLL_KEY,
  readJsonSession,
  readScrollSession,
  scopedOpsKey,
  writeJsonSession,
  writeScrollSession,
} from "../lib/mobileOpsSession";
import {
  capturePoOpenFromList,
  poNavStateFromList,
  storageUserKey,
} from "../lib/poListReturn";
import { deliveryRoleMarkTones } from "../lib/mobileOpsStatusMark";
import { formatIdQty } from "../lib/productionScheduleQty";
import { MOBILE_LOAD_TIMEOUT_MS, withTimeout } from "../lib/mobileRequest";
import useProductionSheetZoom from "../hooks/useProductionSheetZoom";
import { productionSheetZoomStyle } from "../lib/productionSheetZoom";
import { ProductionScheduleSearchCluster } from "../components/ProductionScheduleRingkasan";
import OpsQueueHeader from "./components/OpsQueueHeader";
import OpsQueueLegend from "./components/OpsQueueLegend";
import MobileTaskCard from "./components/MobileTaskCard";
import MobileWorkspaceSelect from "./components/MobileWorkspaceSelect";

function ProgressCell({ pct, tone, testid, title, primaryLabel, contextLabel }) {
  const p = clampPercent(pct);
  return (
    <div className={"ship-bar-stack"} data-testid={testid} title={title}>
      <div className={"ship-bar ship-bar-" + tone}>
        <div className="ship-bar-fill" style={{ width: p + "%" }} />
        <span className="ship-bar-label">{primaryLabel != null ? primaryLabel : `${p}%`}</span>
      </div>
      {contextLabel ? <span className="mop-qty-context">{contextLabel}</span> : null}
    </div>
  );
}

function PackingCell({ row }) {
  const done = Number(row.qty_packed ?? 0);
  const target = Number(row.qty_total ?? 0);
  const pct = target > 0 ? Math.min(100, Math.round((done / target) * 100)) : 0;
  return (
    <span className="mop-qty-progress" data-testid={`mds-packing-${row.order_number}`} title={packingProgressText(row)}>
      <span>{packingProgressText(row)}</span>
      {target > 0 ? (
        <span className="mop-qty-progress-bar" aria-hidden>
          <span className="mop-qty-progress-fill" style={{ width: `${pct}%` }} />
        </span>
      ) : null}
    </span>
  );
}

function scheduleRowSuffix(row) {
  if (row?.shipment_id) return `${row.order_number}-${row.shipment_id}`;
  return row?.order_number || "row";
}

function DeliveryIdentityCell({ row, onOpenRoute, canOpenRoute }) {
  const suffix = scheduleRowSuffix(row);
  const meta = optimizationStatusMeta(row);
  return (
    <div className="mop-ops-identity" data-testid={`mds-identity-${suffix}`}>
      <strong className="mop-ops-customer">{row.customer_name || "-"}</strong>
      <span className="mop-ops-po">{row.order_number || ""}</span>
      {row.latest_sj_number || row.sj_label ? (
        <span className="mop-ops-po" data-testid={`mds-sj-${suffix}`}>{row.latest_sj_number || row.sj_label}</span>
      ) : (
        <span className="mop-ops-po" data-testid={`mds-sj-${suffix}`}>—</span>
      )}
      {meta.code === "sudah_dioptimasi" && meta.routeId && canOpenRoute ? (
        <button
          type="button"
          className="mop-ops-route-link"
          data-testid={`mds-lihat-rute-${row.order_number}`}
          onClick={(e) => {
            e.stopPropagation();
            onOpenRoute?.(meta.routeId);
          }}
        >
          Lihat Rute
        </button>
      ) : null}
    </div>
  );
}

function SjItemLineCell({ row }) {
  const suffix = scheduleRowSuffix(row);
  const lines = sjItemLinesFromRow(row);
  if (!lines.length) {
    return <span className="mds-item-line-empty" data-testid={`mds-item-lines-${suffix}`}>—</span>;
  }
  const groups = groupSjItemLines(lines);
  return (
    <div className="mds-item-lines" data-testid={`mds-item-lines-${suffix}`}>
      {groups.map((group, gi) => (
        <div
          key={`${group.shipment_id || "sj"}-${group.sj_number || gi}`}
          className="mds-item-line-group"
          data-testid={`mds-item-line-group-${suffix}-${gi}`}
        >
          {group.lines.map((ln, li) => {
            const qtyPresent = ln.qty !== undefined && ln.qty !== null && ln.qty !== "";
            return (
              <div
                key={sjItemLineKey(ln, li)}
                className="mds-item-line"
                data-testid={`mds-item-line-${suffix}-${gi}-${li}`}
              >
                <span className="mds-item-line-name">
                  <span className="mds-item-line-prefix" aria-hidden="true">- </span>
                  {ln.name || "—"}
                </span>
                {qtyPresent ? (
                  <span className="mds-item-line-qty">{formatIdQty(ln.qty)}</span>
                ) : null}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

export function deliveryScheduleLoadErrorMessage(err) {
  const status = err?.response?.status;
  if (err?.code === "ETIMEDOUT" || err?.code === "ECONNABORTED") {
    return "Gagal memuat Jadwal Pengiriman.";
  }
  const msg = formatApiError(err, "Gagal memuat Jadwal Pengiriman.");
  if (status === 403) return msg || "Anda tidak punya akses untuk melihat Jadwal Pengiriman.";
  if (status === 401) return msg || "Sesi berakhir. Silakan masuk kembali.";
  return msg || "Gagal memuat Jadwal Pengiriman.";
}

export default function MobileDeliverySchedulePage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const { user, hasPerm } = useAuth();
  const allowed = canViewMobileDeliverySchedule(hasPerm);
  const canOpenProduction = canViewMobileSchedule(hasPerm);
  const canOptimize = canOptimizeFromDeliverySchedule(hasPerm);
  const requestedWs = params.get("ws") || "";
  const taskChoice = useMemo(
    () => selectDeliveryScheduleTaskContext(hasPerm, requestedWs),
    [hasPerm, requestedWs],
  );
  const taskContexts = taskChoice.permitted;
  const selectedTask = taskChoice.selected;
  const showTable = deliveryScheduleShowsTable(selectedTask?.id);
  const listPathname = location.pathname || "/m/delivery-schedule";
  const listSearch = location.search || "";
  const userKey = storageUserKey(user);
  const filterKey = scopedOpsKey(MOBILE_DELIVERY_FILTER_KEY, userKey);
  const scrollKey = scopedOpsKey(MOBILE_DELIVERY_SCROLL_KEY, userKey);
  const saved = readJsonSession(filterKey, {
    filters: EMPTY_DELIVERY_FILTERS,
    quick: "semua_aktif",
    sort: EMPTY_DELIVERY_SORT,
    q: "",
  });
  const [rows, setRows] = useState([]);
  const [productionRows, setProductionRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [taskRows, setTaskRows] = useState([]);
  const [taskBusy, setTaskBusy] = useState(false);
  const [taskErr, setTaskErr] = useState("");
  const [filters, setFilters] = useState({ ...EMPTY_DELIVERY_FILTERS, ...(saved.filters || {}) });
  const [sort, setSort] = useState({ ...EMPTY_DELIVERY_SORT, ...(saved.sort || {}) });
  const [quick, setQuick] = useState(saved.quick || "semua_aktif");
  const [q, setQ] = useState(saved.q || "");
  const [filterOpen, setFilterOpen] = useState(false);
  const [draftFilters, setDraftFilters] = useState(EMPTY_DELIVERY_FILTERS);
  const [reloadKey, setReloadKey] = useState(0);
  const sheetZoom = useProductionSheetZoom();
  const scrollRestored = useRef(false);

  // Keep selected task context in ?ws= so refresh + Back restore it (presentation only).
  useEffect(() => {
    if (!allowed || !taskContexts.length || !selectedTask?.id) return;
    if (params.get("ws") === selectedTask.id) return;
    const next = new URLSearchParams(params);
    next.set("ws", selectedTask.id);
    setParams(next, { replace: true });
  }, [allowed, taskContexts.length, selectedTask?.id, params, setParams]);

  const setTaskContextId = (id) => {
    if (!id) return;
    const next = new URLSearchParams(params);
    next.set("ws", id);
    setParams(next, { replace: true });
  };

  const load = useCallback(async () => {
    if (!allowed) {
      setBusy(false);
      setRows([]);
      setProductionRows([]);
      setErr("");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const productionP = canOpenProduction
        ? withTimeout(
          api.productionDashboard(mobileProductionDashboardParams({
            includeCancelled: false,
            includeCompleted: true,
          })),
          MOBILE_LOAD_TIMEOUT_MS,
        ).catch(() => ({ rows: [] }))
        : Promise.resolve({ rows: [] });
      const [d, prod] = await Promise.all([
        withTimeout(api.deliverySchedule(), MOBILE_LOAD_TIMEOUT_MS),
        productionP,
      ]);
      setRows(d.rows || []);
      setProductionRows(Array.isArray(prod?.rows) ? prod.rows : []);
    } catch (e) {
      setRows([]);
      setProductionRows([]);
      setErr(deliveryScheduleLoadErrorMessage(e));
    } finally {
      setBusy(false);
    }
  }, [allowed, canOpenProduction]);

  const loadTasks = useCallback(async (division) => {
    if (!allowed || !division) {
      setTaskBusy(false);
      setTaskRows([]);
      setTaskErr("");
      return;
    }
    setTaskBusy(true);
    setTaskErr("");
    try {
      const d = await withTimeout(api.divisionTasks(division, "mine"), MOBILE_LOAD_TIMEOUT_MS);
      setTaskRows(Array.isArray(d?.tasks) ? d.tasks : []);
    } catch (e) {
      setTaskRows([]);
      setTaskErr(e?.response?.data?.detail || formatApiError(e, "Gagal memuat tugas."));
    } finally {
      setTaskBusy(false);
    }
  }, [allowed]);

  useEffect(() => {
    if (showTable) load();
  }, [load, reloadKey, showTable]);

  useEffect(() => {
    if (!showTable && selectedTask?.division) {
      loadTasks(selectedTask.division);
    }
  }, [loadTasks, reloadKey, showTable, selectedTask?.id, selectedTask?.division]);

  useEffect(() => {
    writeJsonSession(filterKey, { filters, quick, sort, q });
  }, [filterKey, filters, quick, sort, q]);

  const filteredRows = useMemo(
    () => (rows || [])
      .filter((r) => matchesDeliveryScheduleFilters(r, filters))
      .filter((r) => matchesDeliveryOpsSearch(r, q)),
    [rows, filters, q],
  );
  const quickFiltered = useMemo(
    () => filteredRows.filter((r) => deliveryQuickMatch(r, quick)),
    [filteredRows, quick],
  );
  const sorted = useMemo(() => sortDeliveryRows(quickFiltered, sort), [quickFiltered, sort]);
  const visible = useMemo(() => sortByOpsQueue(sorted, deliveryQueueRank), [sorted]);
  const quickCounts = useMemo(() => opsQuickFilterCounts(filteredRows, deliveryQuickMatch), [filteredRows]);
  const roleSummary = useMemo(
    () => buildOpsRoleSummaryModel({ productionRows: null, deliveryRows: rows }),
    [rows],
  );
  const productionById = useMemo(
    () => indexProductionStatusByOrderId(productionRows),
    [productionRows],
  );
  const activeFilterCount = useMemo(() => countActiveDeliveryFilters(filters), [filters]);
  const sortedTasks = useMemo(() => sortOperationalTasks(taskRows), [taskRows]);
  const workWsId = selectedTask?.id;

  useLayoutEffect(() => {
    if (!showTable || busy || scrollRestored.current) return;
    const el = sheetZoom.viewportRef?.current;
    const savedScroll = readScrollSession(scrollKey);
    if (el && savedScroll) {
      el.scrollLeft = savedScroll.left;
      el.scrollTop = savedScroll.top;
    }
    scrollRestored.current = true;
  }, [busy, sheetZoom.viewportRef, visible.length, scrollKey, showTable]);

  const persistScroll = () => {
    const el = sheetZoom.viewportRef?.current;
    if (!el) return;
    writeScrollSession(scrollKey, el.scrollLeft, el.scrollTop);
  };

  const openPoReturnState = (orderId) => {
    persistScroll();
    capturePoOpenFromList({
      user,
      orderId,
      pathname: listPathname,
      search: listSearch,
    });
    return poNavStateFromList({
      pathname: listPathname,
      search: listSearch,
    });
  };

  const setDraft = (key, value) => setDraftFilters((prev) => ({ ...prev, [key]: value }));
  const onHeaderSort = (key) => setSort((prev) => cycleDeliverySort(prev, key));

  const openFilterPanel = () => {
    setDraftFilters({ ...EMPTY_DELIVERY_FILTERS, ...filters });
    setFilterOpen(true);
  };
  const closeFilterPanel = () => setFilterOpen(false);
  const applyDraftFilters = () => {
    setFilters({ ...EMPTY_DELIVERY_FILTERS, ...draftFilters });
    setFilterOpen(false);
  };
  const resetFilters = () => {
    const empty = { ...EMPTY_DELIVERY_FILTERS };
    setDraftFilters(empty);
    setFilters(empty);
    setQuick("semua_aktif");
    setQ("");
  };

  const openRow = (r) => {
    if (!r?.id) return;
    const state = openPoReturnState(r.id);
    navigate(mobileScheduleWorkHref(r.id, hasPerm, "delivery", workWsId, r), { state });
  };

  const onRefresh = () => setReloadKey((n) => n + 1);

  return (
    <div className="mop-schedule-root mop-delivery-schedule-root">
      <OpsQueueHeader title="Jadwal Pengiriman" showBrand={false} subtitle="" />
      <main className="mop-content mop-schedule-page" data-testid="mobile-delivery-schedule-page">
        {!allowed && (
          <div className="mop-error" data-testid="mobile-delivery-schedule-forbidden">
            <p>Anda tidak punya akses untuk melihat Jadwal Pengiriman.</p>
          </div>
        )}
        {allowed && (
          <>
            {taskContexts.length > 0 && !showTable && (
              <MobileWorkspaceSelect
                workspaces={taskContexts}
                value={selectedTask?.id}
                onChange={setTaskContextId}
                label="Tugas"
                testid="mds-task-select"
              />
            )}

            {!showTable && (
              <div className="mop-ds-task-panel" data-testid="mds-task-list">
                <div className="mop-schedule-filter-actions" data-testid="mds-task-actions">
                  <button
                    type="button"
                    className="mop-btn-ghost"
                    onClick={onRefresh}
                    title="Muat ulang"
                    data-testid="mds-refresh"
                    disabled={taskBusy}
                    aria-label="Muat ulang"
                  >
                    <RefreshCw size={14} className={taskBusy ? "spin" : ""} aria-hidden />
                  </button>
                </div>
                {taskBusy && !sortedTasks.length && (
                  <p className="mop-empty" data-testid="mds-task-loading">Memuat tugas...</p>
                )}
                {taskErr && (
                  <div className="mop-error" data-testid="mds-task-error">
                    <p>{taskErr}</p>
                    <button type="button" className="mop-btn-primary" onClick={onRefresh} data-testid="mds-task-retry">
                      Coba Lagi
                    </button>
                  </div>
                )}
                {!taskBusy && !taskErr && sortedTasks.map((task) => (
                  <MobileTaskCard
                    key={task.task_id || task.order_id}
                    task={task}
                    workspaceId={selectedTask.id}
                  />
                ))}
                {!taskBusy && !taskErr && !sortedTasks.length && (
                  <p className="mop-empty" data-testid="mds-task-empty">Tidak ada tugas.</p>
                )}
              </div>
            )}

            {showTable && (
            <>
            <MobileOpsRoleSummary
              roles={roleSummary.map((role) => ({
                ...role,
                interactive: (role.source === "delivery" && Boolean(role.workspaceId))
                  || (role.source === "production" && canOpenProduction),
              }))}
              activeRoleId={selectedTask?.id || ""}
              onSelectRole={(role) => {
                if (role.source === "delivery" && role.workspaceId) {
                  setTaskContextId(role.workspaceId);
                  return;
                }
                if (role.source === "production" && canOpenProduction && role.href) {
                  navigate(role.href);
                }
              }}
            />
            <div className="mop-schedule-toolbar mop-ds-toolbar" data-testid="mobile-delivery-schedule-toolbar">
              <div className="mop-ops-quick mop-ops-filter-rail" data-testid="mds-quick-filters">
                {OPS_QUICK_FILTERS.map((f) => (
                  <button
                    key={f.key}
                    type="button"
                    className={`mop-ops-chip mop-ops-chip--${f.key}${quick === f.key ? " is-active" : ""}`}
                    data-testid={`mds-quick-${f.key}`}
                    onClick={() => setQuick(f.key)}
                  >
                    {f.label}
                    <span className="mop-ops-chip-count" data-testid={`mds-quick-count-${f.key}`}>
                      {quickCounts[f.key] ?? 0}
                    </span>
                  </button>
                ))}
                <ProductionScheduleSearchCluster
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  inputTestId="mds-search"
                  searchButtonTestId="mds-search-btn"
                  placeholder="Cari customer / PO / SJ / item"
                />
                <div className="mop-schedule-filter-actions" data-testid="mds-filter-actions">
                  <button
                    type="button"
                    className={"mop-btn-ghost mop-ds-filter-btn" + (activeFilterCount > 0 ? " is-active" : "")}
                    onClick={openFilterPanel}
                    data-testid="mds-filter-btn"
                    aria-label="Filter"
                    aria-expanded={filterOpen}
                  >
                    <Filter size={13} aria-hidden />
                    <span>Filter{activeFilterCount > 0 ? ` (${activeFilterCount})` : ""}</span>
                  </button>
                  <button
                    type="button"
                    className="mop-btn-ghost mop-schedule-reset"
                    onClick={resetFilters}
                    disabled={activeFilterCount === 0 && quick === "semua_aktif" && !String(q || "").trim()}
                    data-testid="mds-filter-reset"
                  >
                    Reset
                  </button>
                </div>
                <span className="mop-schedule-count" data-testid="mds-count">
                  {visible.length} / {rows.length} entri
                </span>
                <button
                  type="button"
                  className="mop-btn-ghost mop-ops-toolbar-refresh"
                  onClick={onRefresh}
                  title="Muat ulang"
                  data-testid="mds-refresh"
                  disabled={busy}
                  aria-label="Muat ulang"
                >
                  <RefreshCw size={14} className={busy ? "spin" : ""} aria-hidden />
                </button>
                {canOptimize && (
                  <button
                    type="button"
                    className="mop-btn-primary"
                    data-testid="mds-optimasi-rute"
                    disabled={busy || visible.length === 0}
                    onClick={() => {
                      persistScroll();
                      const ids = visible.map((r) => r.id).filter(Boolean);
                      navigate(mobileScheduleReviewHref(ids));
                    }}
                  >
                    Optimasi Rute
                  </button>
                )}
              </div>
            </div>
            </>
            )}

            {showTable && busy && (
              <p className="mop-empty" data-testid="mds-loading">Memuat Jadwal Pengiriman...</p>
            )}
            {showTable && !busy && err && (
              <div className="mop-error" data-testid="mds-error">
                <p>Gagal memuat Jadwal Pengiriman.</p>
                {err !== "Gagal memuat Jadwal Pengiriman." ? <p className="mop-schedule-error-detail">{err}</p> : null}
                <button
                  type="button"
                  className="mop-btn-primary"
                  onClick={onRefresh}
                  data-testid="mds-retry"
                >
                  Coba Lagi
                </button>
              </div>
            )}
            {showTable && !busy && !err && (
              <div
                className="mop-schedule-scroll production-sheet production-sheet-viewport"
                data-testid="mds-scroll"
                ref={sheetZoom.setViewportRef}
                style={productionSheetZoomStyle(sheetZoom.zoom)}
                onScroll={persistScroll}
              >
                <table className="production-sheet-table" data-testid="mds-table">
                  <colgroup>
                    {MOBILE_DELIVERY_SCHEDULE_COLS.map((col) => (
                      <col key={col.key} style={mobileDeliverySheetColumnStyle(col)} />
                    ))}
                  </colgroup>
                  <OpsScheduleThead
                    cols={MOBILE_DELIVERY_SCHEDULE_COLS}
                    statusKeys={MOBILE_DELIVERY_STATUS_COL_KEYS}
                    testid="mds-thead"
                    mode="delivery"
                    renderSort={(col) => (
                      col.sort ? (
                        <button
                          type="button"
                          className="production-sheet-th-sort mop-schedule-th-sort"
                          onClick={() => onHeaderSort(DELIVERY_SORT_KEYS[col.sort] || col.sort)}
                          data-testid={`mds-sort-${col.key}`}
                          aria-label={`Urutkan ${col.label}`}
                        >
                          {col.label}
                        </button>
                      ) : col.label
                    )}
                  />
                  <tbody>
                    {visible.map((r) => {
                      const badge = deliveryOpsBadge(r);
                      const workHref = mobileScheduleWorkHref(r.id, hasPerm, "delivery", workWsId, r);
                      const productionRow = r?.id != null && String(r.id).trim() !== ""
                        ? productionById.get(String(r.id).trim())
                        : undefined;
                      const statusRow = deliveryRowWithProductionStatus(r, productionById);
                      const tones = deliveryRoleMarkTones(statusRow, Boolean(productionRow));
                      return (
                        <tr
                          key={r.shipment_id ? `${r.id}:${r.shipment_id}` : (r.id || r.order_number)}
                          className="production-sheet-row mop-schedule-row"
                          data-testid={r.shipment_id ? `mds-row-${r.order_number}-${r.shipment_id}` : `mds-row-${r.order_number}`}
                          data-to={workHref}
                          tabIndex={0}
                          onClick={() => {
                            if (sheetZoom.consumeClickSuppression()) return;
                            openRow(r);
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              if (sheetZoom.consumeClickSuppression()) return;
                              openRow(r);
                            }
                          }}
                        >
                          <td className="production-sheet-cell">
                            {badge ? (
                              <div className="mop-ops-prio">
                                <span className={`mop-ops-badge mop-ops-badge-${badge.tone}`}>{badge.label}</span>
                                {badge.caption ? (
                                  <span className={`mop-ops-prio-caption is-${badge.captionTone || badge.tone}`}>{badge.caption}</span>
                                ) : null}
                              </div>
                            ) : "—"}
                          </td>
                          <td className="mono production-sheet-cell">
                            {formatDate(r.effective_delivery_date, { dateOnly: true })}
                          </td>
                          <td className="production-sheet-cell">
                            <DeliveryIdentityCell
                              row={r}
                              canOpenRoute={canAccessRoutePage(hasPerm)}
                              onOpenRoute={(routeId) => {
                                persistScroll();
                                navigateToOptimasiRute(navigate, routeId, {
                                  from: `${listPathname}${listSearch}`,
                                });
                              }}
                            />
                          </td>
                          <td className="production-sheet-cell">
                            <SjItemLineCell row={r} />
                          </td>
                          <td className="production-sheet-cell num" data-testid={`mds-qty-${r.order_number}`}>
                            <strong className="mop-ops-qty-total">{formatIdQty(r.qty_total ?? 0)}</strong>
                          </td>
                          <td className="production-sheet-cell num">
                            <OpsRoleMark
                              tone={tones.cup}
                              title="Persiapan Cup"
                              testid={`mds-cup-${r.order_number}`}
                            />
                          </td>
                          <td className="production-sheet-cell num">
                            <OpsRoleMark
                              tone={tones.screen}
                              title="Screener"
                              testid={`mds-screen-${r.order_number}`}
                            />
                          </td>
                          <td className="production-sheet-cell num">
                            <OpsRoleMark
                              tone={tones.prod}
                              title="Produksi"
                              testid={`mds-prod-${r.order_number}`}
                            />
                          </td>
                          <td className="production-sheet-cell num">
                            <OpsRoleMark
                              tone={tones.pack}
                              title={packingProgressText(r)}
                              testid={`mds-packing-${r.order_number}`}
                            />
                          </td>
                          <td className="production-sheet-cell num">
                            <OpsRoleMark
                              tone={tones.load}
                              title="Loading"
                              testid={`mds-load-${r.order_number}`}
                            />
                          </td>
                          <td className="production-sheet-cell num">
                            <OpsRoleMark
                              tone={tones.kirim}
                              title={`Terkirim ${formatIdQty(r.qty_delivered ?? 0)} / ${formatIdQty(r.qty_total ?? 0)}`}
                              testid={`mds-terkirim-${r.order_number}`}
                            />
                          </td>
                        </tr>
                      );
                    })}
                    {!visible.length && (
                      <tr>
                        <td
                          colSpan={MOBILE_DELIVERY_SCHEDULE_COLS.length}
                          className="production-sheet-empty mop-schedule-filter-empty"
                          data-testid={rows.length === 0 ? "mds-empty" : "mds-filter-empty"}
                        >
                          Tidak ada jadwal pengiriman.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
            {showTable && !busy && !err ? <OpsQueueLegend /> : null}
          </>
        )}
      </main>

      {filterOpen && (
        <div
          className="mop-ds-filter-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Filter Jadwal Pengiriman"
          data-testid="mds-filter-panel"
          onClick={(e) => e.target === e.currentTarget && closeFilterPanel()}
        >
          <div className="mop-ds-filter-panel" onClick={(e) => e.stopPropagation()} data-testid="mds-filter-panel-body">
            <header className="mop-ds-filter-head">
              <h3>Filter</h3>
              <button
                type="button"
                className="mop-menu-close"
                onClick={closeFilterPanel}
                aria-label="Tutup"
                data-testid="mds-filter-close"
              >
                <X size={16} aria-hidden />
              </button>
            </header>
            <div className="mop-ds-filter-fields">
              <label className="mop-ds-filter-field">
                <span>Nama Customer</span>
                <input type="text" value={draftFilters.customer} onChange={(e) => setDraft("customer", e.target.value)} placeholder="Cari customer" data-testid="mds-adv-customer" />
              </label>
              <label className="mop-ds-filter-field">
                <span>No PO</span>
                <input type="text" value={draftFilters.po} onChange={(e) => setDraft("po", e.target.value)} placeholder="Cari No PO" data-testid="mds-adv-po" />
              </label>
              <label className="mop-ds-filter-field">
                <span>Tanggal Mulai</span>
                <input type="date" value={draftFilters.dateFrom} onChange={(e) => setDraft("dateFrom", e.target.value)} data-testid="mds-adv-from" />
              </label>
              <label className="mop-ds-filter-field">
                <span>Tanggal Akhir</span>
                <input type="date" value={draftFilters.dateTo} onChange={(e) => setDraft("dateTo", e.target.value)} data-testid="mds-adv-to" />
              </label>
              <label className="mop-ds-filter-field">
                <span>Status</span>
                <select value={draftFilters.status} onChange={(e) => setDraft("status", e.target.value)} data-testid="mds-adv-status">
                  {DELIVERY_SCHEDULE_STATUS_OPTIONS.map((o) => (
                    <option key={o.key} value={o.key}>{o.label}</option>
                  ))}
                </select>
              </label>
              <label className="mop-ds-filter-field">
                <span>Driver</span>
                <input type="text" value={draftFilters.driver} onChange={(e) => setDraft("driver", e.target.value)} placeholder="Cari driver" data-testid="mds-adv-driver" />
              </label>
              <label className="mop-ds-filter-field">
                <span>Terkirim</span>
                <select value={draftFilters.terkirim || "all"} onChange={(e) => setDraft("terkirim", e.target.value)} data-testid="mds-adv-terkirim">
                  <option value="all">Semua</option>
                  <option value="none">Belum Terkirim</option>
                  <option value="partial">Terkirim Sebagian</option>
                  <option value="full">Terkirim 100%</option>
                </select>
              </label>
              <label className="mop-ds-filter-field">
                <span>Dapat Dikirim</span>
                <select value={draftFilters.dapatDikirim || "all"} onChange={(e) => setDraft("dapatDikirim", e.target.value)} data-testid="mds-adv-dapat">
                  <option value="all">Semua</option>
                  <option value="none">Tidak Dapat Dikirim</option>
                  <option value="partial">Dapat Dikirim Sebagian</option>
                  <option value="full">Dapat Dikirim 100%</option>
                </select>
              </label>
            </div>
            <footer className="mop-ds-filter-foot" data-testid="mds-filter-panel-foot">
              <button type="button" className="mop-btn-ghost" onClick={resetFilters} data-testid="mds-adv-reset">
                Reset Filter
              </button>
              <button type="button" className="mop-btn-primary" onClick={applyDraftFilters} data-testid="mds-filter-apply">
                Terapkan Filter
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}
