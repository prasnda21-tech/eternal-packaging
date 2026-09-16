import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Filter } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { getCanonicalCustomerName } from "../lib/canonicalCustomer";
import { formatApiError } from "../lib/errors";
import useProductionSheetZoom from "../hooks/useProductionSheetZoom";
import ProductionScheduleSheetCells from "../components/ProductionScheduleSheetCells";
import { ProductionScheduleSearchCluster } from "../components/ProductionScheduleRingkasan";
import {
  FILTER_DEFAULTS,
  applyProductionFilters,
  isScheduledToday,
  productionFiltersAreActive,
} from "../lib/productionScheduleFilters";
import { buildProductionScheduleDisplayRows } from "../lib/productionScheduleDisplayRows";
import { operatorQtyCetakProgress, poTotalQtyDisplay } from "../lib/productionScheduleQty";
import { productionSheetColumnStyle } from "../lib/productionScheduleTable";
import { MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS, MOBILE_PRODUCTION_STATUS_COL_KEYS } from "../lib/mobileProductionScheduleCols";
import { MOBILE_LOAD_TIMEOUT_MS, withTimeout } from "../lib/mobileRequest";
import {
  canViewMobileSchedule,
  mobileProductionDashboardParams,
  rowsFromProductionDashboard,
} from "../lib/mobileSchedule";
import {
  EXACT_SJ_REQUIRED_MESSAGE,
  mobileScheduleWorkHref,
  permittedOpsActiveRoles,
  roleRequiresExactShipment,
  selectOpsActiveRole,
} from "../lib/mobileWorkspaces";
import {
  OPS_QUICK_FILTERS,
  matchesProductionOpsSearch,
  opsQuickFilterCounts,
  productionOpsBadge,
  productionQuickMatch,
  sortByOpsQueue,
  productionQueueRank,
} from "../lib/mobileOpsQueue";
import MobileWorkspaceSelect from "./components/MobileWorkspaceSelect";
import MobileTaskModal, { opsRoleIcon } from "./components/MobileTaskModal";
import OpsScheduleThead from "./components/OpsScheduleThead";
import OpsQueueHeader from "./components/OpsQueueHeader";
import OpsQueueLegend from "./components/OpsQueueLegend";
import MobileScreenerWorkspace from "./workspaces/MobileScreenerWorkspace";
import MobileHelperPrepWorkspace from "./workspaces/MobileHelperPrepWorkspace";
import MobilePackingWorkspace from "./workspaces/MobilePackingWorkspace";
import MobilePrintWorkspace from "./workspaces/MobilePrintWorkspace";
import MobileLoadingWorkspace from "./workspaces/MobileLoadingWorkspace";
import MobileDeliveryWorkspace from "./workspaces/MobileDeliveryWorkspace";
import {
  MOBILE_PROD_FILTER_KEY,
  MOBILE_PROD_SCROLL_KEY,
  readActiveRoleId,
  readJsonSession,
  readScrollSession,
  scopedOpsKey,
  writeActiveRoleId,
  writeJsonSession,
  writeScrollSession,
} from "../lib/mobileOpsSession";
import { storageUserKey } from "../lib/poListReturn";
import { isInteractiveSheetRowClickTarget, productionSheetZoomStyle } from "../lib/productionSheetZoom";

const SCHEDULE_FILTER_RAIL_KEYS = [
  "semua",
  "hari_ini",
  "besok",
  "terlambat",
  "belum_dijadwalkan",
  "minggu_ini",
  "semua_aktif",
];

const SCHEDULE_WORKSPACE_VIEWS = {
  screener: MobileScreenerWorkspace,
  prep: MobileHelperPrepWorkspace,
  packing: MobilePackingWorkspace,
  print: MobilePrintWorkspace,
  loading: MobileLoadingWorkspace,
  delivery: MobileDeliveryWorkspace,
};

function scheduleTaskContext(order, row) {
  return {
    customer: getCanonicalCustomerName(order || row || {}),
    po: order?.po_number || row?.po_number || row?.order_number || "",
    sj: row?.latest_sj_number || row?.sj_label || order?.delivery_note_number || "",
  };
}

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

export default function MobileProductionSchedulePage() {
  const { user, hasPerm } = useAuth();
  const allowed = canViewMobileSchedule(hasPerm);
  const userKey = storageUserKey(user);
  const permittedIds = permittedOpsActiveRoles(hasPerm).map((w) => w.id).join("|");
  const roleChoice = useMemo(
    () => selectOpsActiveRole(hasPerm, readActiveRoleId(userKey)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [userKey, permittedIds],
  );
  const [activeRoleId, setActiveRoleId] = useState(roleChoice.selected?.id || "");
  const filterKey = scopedOpsKey(MOBILE_PROD_FILTER_KEY, userKey);
  const scrollKey = scopedOpsKey(MOBILE_PROD_SCROLL_KEY, userKey);
  const saved = readJsonSession(filterKey, {
    q: FILTER_DEFAULTS.q,
    includeCancelled: FILTER_DEFAULTS.includeCancelled,
    includeCompleted: FILTER_DEFAULTS.includeCompleted,
    quick: "semua_aktif",
  });
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(allowed);
  const [reloadKey, setReloadKey] = useState(0);
  const [q, setQ] = useState(saved.q ?? FILTER_DEFAULTS.q);
  const [includeCancelled, setIncludeCancelled] = useState(Boolean(saved.includeCancelled));
  const [includeCompleted, setIncludeCompleted] = useState(
    saved.includeCompleted == null ? FILTER_DEFAULTS.includeCompleted : Boolean(saved.includeCompleted),
  );
  const [quick, setQuick] = useState(saved.quick || "semua_aktif");
  const [filterOpen, setFilterOpen] = useState(false);
  const sheetZoom = useProductionSheetZoom({ userId: userKey });
  const scrollRestored = useRef(false);
  const [task, setTask] = useState({
    open: false,
    order: null,
    shipmentId: "",
    row: null,
    busy: false,
    err: "",
  });

  const activeRoles = roleChoice.permitted;
  const activeRole = activeRoles.find((ws) => ws.id === activeRoleId) || roleChoice.selected;

  useEffect(() => {
    const next = selectOpsActiveRole(hasPerm, readActiveRoleId(userKey) || activeRoleId);
    const id = next.selected?.id || "";
    setActiveRoleId(id);
    if (readActiveRoleId(userKey) !== id) writeActiveRoleId(userKey, id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userKey, permittedIds]);

  const setActiveRole = (id) => {
    const next = selectOpsActiveRole(hasPerm, id);
    const resolved = next.selected?.id || "";
    setActiveRoleId(resolved);
    writeActiveRoleId(userKey, resolved);
  };

  const filterState = {
    ...FILTER_DEFAULTS,
    q,
    sortBy: FILTER_DEFAULTS.sortBy,
    includeCancelled,
    includeCompleted,
  };

  useEffect(() => {
    writeJsonSession(filterKey, {
      q,
      includeCancelled,
      includeCompleted,
      quick,
    });
  }, [filterKey, q, includeCancelled, includeCompleted, quick]);

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
          api.productionDashboard(mobileProductionDashboardParams({ includeCancelled, includeCompleted })),
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

  const scoped = useMemo(
    () => applyProductionFilters(rows, {
      ...FILTER_DEFAULTS,
      q: "",
      includeCancelled,
      includeCompleted,
    }),
    [rows, includeCancelled, includeCompleted],
  );
  const searched = useMemo(
    () => scoped.filter((r) => matchesProductionOpsSearch(r, q)),
    [scoped, q],
  );
  const filtered = useMemo(
    () => searched.filter((r) => productionQuickMatch(r, quick)),
    [searched, quick],
  );
  const sorted = useMemo(() => sortByOpsQueue(filtered, productionQueueRank), [filtered]);
  const displayRows = useMemo(() => buildProductionScheduleDisplayRows(sorted), [sorted]);
  const quickCounts = useMemo(() => opsQuickFilterCounts(searched, productionQuickMatch), [searched]);
  const filtersActive = productionFiltersAreActive(filterState) || quick !== "semua_aktif";

  useLayoutEffect(() => {
    if (loading || scrollRestored.current) return;
    const el = sheetZoom.viewportRef?.current;
    const savedScroll = readScrollSession(scrollKey);
    if (el && savedScroll) {
      el.scrollLeft = savedScroll.left;
      el.scrollTop = savedScroll.top;
    }
    scrollRestored.current = true;
  }, [loading, sheetZoom.viewportRef, displayRows.length, scrollKey]);

  const persistScroll = () => {
    const el = sheetZoom.viewportRef?.current;
    if (!el) return;
    writeScrollSession(scrollKey, el.scrollLeft, el.scrollTop);
  };

  const resetFilters = () => {
    setQ(FILTER_DEFAULTS.q);
    setIncludeCancelled(FILTER_DEFAULTS.includeCancelled);
    setIncludeCompleted(FILTER_DEFAULTS.includeCompleted);
    setQuick("semua_aktif");
  };

  const openRow = (row) => {
    if (!row?.id) return;
    if (!activeRole?.id || !hasPerm(activeRole.permission)) {
      const next = selectOpsActiveRole(hasPerm, activeRoleId);
      const resolved = next.selected?.id || "";
      if (resolved !== activeRoleId) {
        setActiveRoleId(resolved);
        writeActiveRoleId(userKey, resolved);
      }
      return;
    }
    persistScroll();
    if (roleRequiresExactShipment(activeRole.id)) {
      setTask({
        open: true,
        order: null,
        shipmentId: "",
        row,
        busy: false,
        err: EXACT_SJ_REQUIRED_MESSAGE,
      });
      return;
    }
    const shipmentId = "";
    setTask({ open: true, order: null, shipmentId, row, busy: true, err: "" });
    (async () => {
      try {
        const d = await withTimeout(api.getOrder(row.id), MOBILE_LOAD_TIMEOUT_MS);
        setTask((prev) => ({ ...prev, order: d?.order || d, busy: false }));
      } catch (e) {
        setTask((prev) => ({
          ...prev,
          busy: false,
          err: formatApiError(e, "Gagal memuat PO."),
        }));
      }
    })();
  };

  const activateRow = (row, event) => {
    if (isInteractiveSheetRowClickTarget(event?.target)) return;
    if (sheetZoom.consumeClickSuppression()) return;
    openRow(row);
  };

  const closeTask = () => setTask({
    open: false, order: null, shipmentId: "", row: null, busy: false, err: "",
  });

  const Workspace = activeRole ? SCHEDULE_WORKSPACE_VIEWS[activeRole.id] : null;
  const taskContext = scheduleTaskContext(task.order, task.row);

  return (
    <div className="mop-schedule-root">
      <OpsQueueHeader title="Jadwal Produksi" showBrand={false} subtitle="" />
      <main className="mop-content mop-schedule-page" data-testid="mobile-schedule-page">
        {!allowed && (
          <div className="mop-error" data-testid="mobile-schedule-forbidden">
            <p>Anda tidak punya akses untuk melihat jadwal produksi.</p>
          </div>
        )}
        {allowed && (
          <>
            {activeRoles.length > 0 && (
              <MobileWorkspaceSelect
                workspaces={activeRoles}
                value={activeRole?.id || ""}
                onChange={setActiveRole}
                label="Role Aktif"
                testid="mop-active-role"
                className="mop-active-role"
              />
            )}
            <div className="mop-schedule-toolbar" data-testid="mobile-schedule-toolbar">
              <div className="mop-ops-quick mop-ops-filter-rail" data-testid="mobile-schedule-quick-filters">
                {SCHEDULE_FILTER_RAIL_KEYS.map((key) => {
                  const f = OPS_QUICK_FILTERS.find((item) => item.key === key);
                  if (!f) return null;
                  return (
                    <button
                      key={f.key}
                      type="button"
                      className={`mop-ops-chip mop-ops-chip--${f.key}${quick === f.key ? " is-active" : ""}`}
                      data-testid={`mobile-schedule-quick-${f.key}`}
                      onClick={() => setQuick(f.key)}
                    >
                      {f.label}
                      <span className="mop-ops-chip-count" data-testid={`mobile-schedule-quick-count-${f.key}`}>
                        {quickCounts[f.key] ?? 0}
                      </span>
                    </button>
                  );
                })}
                <label className={`mop-ops-chip mop-ops-chip--selesai${includeCompleted ? " is-active" : ""}`}>
                  <input
                    type="checkbox"
                    checked={includeCompleted}
                    onChange={(e) => setIncludeCompleted(e.target.checked)}
                    data-testid="mobile-schedule-include-completed"
                  />
                  Selesai
                </label>
                <label className={`mop-ops-chip mop-ops-chip--cancelled${includeCancelled ? " is-active" : ""}`}>
                  <input
                    type="checkbox"
                    checked={includeCancelled}
                    onChange={(e) => setIncludeCancelled(e.target.checked)}
                    data-testid="mobile-schedule-include-cancelled"
                  />
                  Cancelled
                </label>
                <ProductionScheduleSearchCluster
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  inputTestId="mobile-schedule-search"
                  searchButtonTestId="mobile-schedule-search-btn"
                  placeholder="Cari PO / customer"
                />
                <div className="mop-schedule-filter-actions" data-testid="mobile-schedule-filter-actions">
                  <button
                    type="button"
                    className={"mop-btn-ghost" + (filterOpen ? " is-active" : "")}
                    onClick={() => setFilterOpen((v) => !v)}
                    data-testid="mobile-schedule-filter-btn"
                  >
                    <Filter size={12} /> Filter
                  </button>
                  <button
                    type="button"
                    className="mop-btn-ghost mop-schedule-reset"
                    onClick={resetFilters}
                    disabled={!filtersActive}
                    data-testid="mobile-schedule-reset"
                  >
                    Reset
                  </button>
                  <span className="mop-schedule-count" data-testid="mobile-schedule-count">
                    {filtered.length} / {rows.length} entri
                  </span>
                </div>
              </div>
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
            {!loading && !err && (
              <div
                className="mop-schedule-scroll production-sheet production-sheet-viewport"
                data-testid="mobile-schedule-scroll"
                ref={sheetZoom.setViewportRef}
                style={productionSheetZoomStyle(sheetZoom.zoom)}
                onScroll={persistScroll}
              >
                <table
                  className="production-sheet-table"
                  data-testid="mobile-schedule-table"
                >
                  <colgroup>
                    {MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.map((col) => (
                      <col key={col.key} style={productionSheetColumnStyle(col)} />
                    ))}
                  </colgroup>
                  <OpsScheduleThead
                    cols={MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS}
                    statusKeys={MOBILE_PRODUCTION_STATUS_COL_KEYS}
                    testid="mobile-schedule-thead"
                    mode="production"
                  />
                  <tbody>
                    {displayRows.map((row) => {
                      const qtyProg = poTotalQtyDisplay(row);
                      const cetakProg = operatorQtyCetakProgress(row, row.displayOperator?.splits);
                      const isToday = isScheduledToday(row);
                      const href = mobileScheduleWorkHref(row.id, hasPerm, "production", activeRole?.id, row);
                      const badge = productionOpsBadge(row);
                      return (
                        <tr
                          key={row.displayRowKey}
                          className={`production-sheet-row mop-schedule-row${row.hold ? " hold" : ""}${row.cancelled ? " cancelled" : ""}`}
                          data-testid={`mobile-schedule-row-${row.po_number}`}
                          data-row-key={row.displayRowKey}
                          data-operator-key={row.displayOperatorKey || "unassigned"}
                          data-operator-qty={row.displayOperator?.quantity ?? ""}
                          data-to={href}
                          tabIndex={0}
                          onClick={(event) => activateRow(row, event)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              activateRow(row, event);
                            }
                          }}
                        >
                          <ProductionScheduleSheetCells
                            row={row}
                            variant="mobile"
                            qtyProg={qtyProg}
                            cetakProg={cetakProg}
                            isToday={isToday}
                            opsBadge={badge}
                            columns={MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS}
                          />
                        </tr>
                      );
                    })}
                    {!sorted.length && (
                      <tr>
                        <td
                          colSpan={MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS.length}
                          className="production-sheet-empty mop-schedule-filter-empty"
                          data-testid={rows.length === 0 ? "mobile-schedule-empty" : "mobile-schedule-filter-empty"}
                        >
                          {rows.length === 0 ? "Belum ada jadwal produksi." : "Tidak ada PO cocok dengan filter."}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
            <OpsQueueLegend />
            <MobileTaskModal
              open={task.open}
              onClose={closeTask}
              title={activeRole?.label}
              icon={opsRoleIcon(activeRole?.id)}
              roleId={activeRole?.id}
              hideHeader={activeRole?.id === "delivery"}
              context={activeRole?.id === "delivery" ? null : taskContext}
              showClose={activeRole?.id !== "delivery"}
              testid="mobile-task-modal"
              labelledBy="mobile-task-modal-title"
            >
              {task.busy && <p className="mop-empty" data-testid="mop-schedule-task-loading">Memuat tugas...</p>}
              {task.err && <div className="mop-error" data-testid="mop-schedule-task-error">{task.err}</div>}
              {task.order && Workspace ? (
                <Workspace
                  order={task.order}
                  canEdit={hasPerm(activeRole.permission)}
                  onUpdated={(next) => setTask((prev) => ({ ...prev, order: next || prev.order }))}
                  onRequestClose={closeTask}
                  shipmentId={task.shipmentId}
                />
              ) : null}
            </MobileTaskModal>
          </>
        )}
      </main>
    </div>
  );
}
