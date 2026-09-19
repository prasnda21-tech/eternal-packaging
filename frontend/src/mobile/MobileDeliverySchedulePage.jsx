import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import "../productionSheet.css";
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
  pwaDeliveryScheduleRows,
  pwaDeliverySjRowKey,
  sjItemLineKey,
  sjItemLinesFromRow,
} from "../lib/mobileDeliverySchedule";
import {
  getPwaDeliverySjPreviewProductionRows,
  getPwaDeliverySjPreviewRows,
  isPwaDeliverySjPreviewEnabled,
} from "../lib/mobileDeliverySchedulePreview";
import { canAccessRoutePage } from "../lib/independentRoute";
import { navigateToOptimasiRute } from "../lib/mobileOpsShell";
import { sortOperationalTasks } from "../lib/mobileTaskCard";
import {
  DELIVERY_SCHEDULE_TASK_IDS,
  deliveryScheduleShowsTable,
  EXACT_SJ_REQUIRED_MESSAGE,
  matchLoadingSjFile,
  mobileScheduleWorkHref,
  permittedOpsActiveRoles,
  roleRequiresExactShipment,
  scheduleRowShipmentId,
  selectDeliveryScheduleTaskContext,
  selectOpsActiveRole,
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
import { canViewMobileSchedule, mobileProductionDashboardParams } from "../lib/mobileSchedule";
import { getCanonicalCustomerName } from "../lib/canonicalCustomer";
import MobileWorkspaceSelect from "./components/MobileWorkspaceSelect";
import MobileTaskModal, { opsRoleIcon } from "./components/MobileTaskModal";
import OpsScheduleThead from "./components/OpsScheduleThead";
import OpsRoleMark from "./components/OpsRoleMark";
import {
  MOBILE_DELIVERY_FILTER_KEY,
  MOBILE_DELIVERY_SCROLL_KEY,
  readActiveRoleId,
  readJsonSession,
  readScrollSession,
  scopedOpsKey,
  writeActiveRoleId,
  writeJsonSession,
  writeScrollSession,
} from "../lib/mobileOpsSession";
import { storageUserKey } from "../lib/poListReturn";
import { deliveryRoleMarkTones } from "../lib/mobileOpsStatusMark";
import { formatIdQty } from "../lib/productionScheduleQty";
import { MOBILE_LOAD_TIMEOUT_MS, withTimeout } from "../lib/mobileRequest";
import { sjFilesForLoading } from "../lib/loadingConfirm";
import { findDeliveryShipment } from "../lib/deliveryProof";
import useProductionSheetZoom from "../hooks/useProductionSheetZoom";
import { isInteractiveSheetRowClickTarget, productionSheetZoomStyle } from "../lib/productionSheetZoom";
import { ProductionScheduleSearchCluster } from "../components/ProductionScheduleRingkasan";
import OpsQueueHeader from "./components/OpsQueueHeader";
import OpsQueueLegend from "./components/OpsQueueLegend";
import MobileTaskCard from "./components/MobileTaskCard";
import MobileScreenerWorkspace from "./workspaces/MobileScreenerWorkspace";
import MobileHelperPrepWorkspace from "./workspaces/MobileHelperPrepWorkspace";
import MobilePackingWorkspace from "./workspaces/MobilePackingWorkspace";
import MobilePrintWorkspace from "./workspaces/MobilePrintWorkspace";
import MobileLoadingWorkspace from "./workspaces/MobileLoadingWorkspace";
import MobileDeliveryWorkspace from "./workspaces/MobileDeliveryWorkspace";

const SCHEDULE_WORKSPACE_VIEWS = {
  screener: MobileScreenerWorkspace,
  prep: MobileHelperPrepWorkspace,
  packing: MobilePackingWorkspace,
  print: MobilePrintWorkspace,
  loading: MobileLoadingWorkspace,
  delivery: MobileDeliveryWorkspace,
};

function orderMatchesRequestedShipment(roleId, order, shipmentId) {
  const wanted = String(shipmentId || "").trim();
  if (!wanted) return false;
  if (roleId === "loading") return Boolean(matchLoadingSjFile(sjFilesForLoading(order), wanted));
  if (roleId === "delivery") return Boolean(findDeliveryShipment(order, wanted));
  return true;
}

function scheduleTaskContext(order, row) {
  return {
    customer: getCanonicalCustomerName(order || row || {}),
    po: order?.po_number || row?.po_number || row?.order_number || "",
    sj: row?.latest_sj_number || row?.sj_label || order?.delivery_note_number || "",
  };
}

/** Local preview-only order from dummy SJ rows. Never invents a shipment_id. */
function previewOrderFromDummyRows(orderId, previewRows) {
  const id = String(orderId || "");
  const group = (Array.isArray(previewRows) ? previewRows : []).filter(
    (row) => row?.preview && String(row.id) === id,
  );
  if (!group.length) return null;
  const first = group[0];
  const shipments = group.map((row) => {
    const sid = scheduleRowShipmentId(row);
    if (!sid) return null;
    const number = String(row.sj_label || row.latest_sj_number || sid);
    const lines = (row.sj_item_lines || []).map((ln) => ({
      line_idx: ln.line_idx,
      line_id: ln.line_id,
      name: ln.name,
      qty: ln.qty,
    }));
    return {
      id: sid,
      shipment_id: sid,
      number,
      sj_number: number,
      delivery_date: String(row.effective_delivery_date || "").slice(0, 10),
      lines,
      total_qty: lines.reduce((sum, ln) => sum + (Number(ln.qty) || 0), 0),
    };
  }).filter(Boolean);
  return {
    id,
    preview: true,
    po_number: first.order_number,
    customer: first.customer_name,
    customer_name: first.customer_name,
    address: first.customer_name || "",
    shipments,
    surat_jalan_files: shipments.map((sh) => ({
      id: sh.id,
      file_id: sh.id,
      shipment_id: sh.id,
      number: sh.number,
      sj_number: sh.number,
      lines: sh.lines,
      total_qty: sh.total_qty,
    })),
    line_items: shipments.flatMap((sh) => sh.lines.map((ln) => ({
      name: ln.name,
      quantity: ln.qty,
      line_id: ln.line_id,
      production_process: "print",
    }))),
    packing_info: {
      qty_packed: shipments.reduce((sum, sh) => sum + Number(sh.total_qty || 0), 0),
      lines: shipments.flatMap((sh) => sh.lines.map((ln) => ({
        line_idx: ln.line_idx,
        line_id: ln.line_id,
        packed_qty: ln.qty,
      }))),
    },
    loading_confirmations: shipments.map((sh) => ({
      id: `preview-load-${sh.id}`,
      surat_jalan_id: sh.id,
      lines: sh.lines.map((ln) => ({ line_idx: ln.line_idx, qty: ln.qty })),
    })),
    delivery_proofs: [],
    delivery_status: "Belum Terkirim",
  };
}

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
  const previewEnabled = isPwaDeliverySjPreviewEnabled(params.toString() || location.search);
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
  const permittedIds = permittedOpsActiveRoles(hasPerm).map((w) => w.id).join("|");
  const roleChoice = useMemo(
    () => selectOpsActiveRole(hasPerm, readActiveRoleId(userKey)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [userKey, permittedIds],
  );
  const [activeRoleId, setActiveRoleId] = useState(roleChoice.selected?.id || "");
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

  const setTaskContextId = (id) => {
    if (!id) return;
    if (!DELIVERY_SCHEDULE_TASK_IDS.includes(id)) return;
    const next = new URLSearchParams(params);
    next.set("ws", id);
    setParams(next, { replace: true });
  };

  const setActiveRole = (id) => {
    const next = selectOpsActiveRole(hasPerm, id);
    const resolved = next.selected?.id || "";
    setActiveRoleId(resolved);
    writeActiveRoleId(userKey, resolved);
    if (DELIVERY_SCHEDULE_TASK_IDS.includes(resolved)) setTaskContextId(resolved);
  };

  // Persist packing/loading/delivery in ?ws= only. Never write prep/screener/print.
  useEffect(() => {
    if (!allowed || !activeRoleId) return;
    if (!DELIVERY_SCHEDULE_TASK_IDS.includes(activeRoleId)) return;
    if (params.get("ws") === activeRoleId) return;
    const next = new URLSearchParams(params);
    next.set("ws", activeRoleId);
    setParams(next, { replace: true });
  }, [allowed, activeRoleId, params, setParams]);

  const load = useCallback(async () => {
    if (!allowed) {
      setBusy(false);
      setRows([]);
      setProductionRows([]);
      setErr("");
      return;
    }
    if (previewEnabled) {
      setBusy(false);
      setErr("");
      setRows(getPwaDeliverySjPreviewRows());
      setProductionRows(getPwaDeliverySjPreviewProductionRows());
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
  }, [allowed, canOpenProduction, previewEnabled]);

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

  const sjRows = useMemo(() => pwaDeliveryScheduleRows(rows), [rows]);
  const filteredRows = useMemo(
    () => (sjRows || [])
      .filter((r) => matchesDeliveryScheduleFilters(r, filters))
      .filter((r) => matchesDeliveryOpsSearch(r, q)),
    [sjRows, filters, q],
  );
  const quickFiltered = useMemo(
    () => filteredRows.filter((r) => deliveryQuickMatch(r, quick)),
    [filteredRows, quick],
  );
  const sorted = useMemo(() => sortDeliveryRows(quickFiltered, sort), [quickFiltered, sort]);
  const visible = useMemo(() => sortByOpsQueue(sorted, deliveryQueueRank), [sorted]);
  const quickCounts = useMemo(() => opsQuickFilterCounts(filteredRows, deliveryQuickMatch), [filteredRows]);
  const productionById = useMemo(
    () => indexProductionStatusByOrderId(productionRows),
    [productionRows],
  );
  const activeFilterCount = useMemo(() => countActiveDeliveryFilters(filters), [filters]);
  const sortedTasks = useMemo(() => sortOperationalTasks(taskRows), [taskRows]);
  const workWsId = (activeRole?.id && DELIVERY_SCHEDULE_TASK_IDS.includes(activeRole.id))
    ? activeRole.id
    : selectedTask?.id;

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
    const needsShipment = roleRequiresExactShipment(activeRole.id);
    const shipmentId = needsShipment ? scheduleRowShipmentId(r) : "";
    if (needsShipment && !shipmentId) {
      setTask({
        open: true,
        order: null,
        shipmentId: "",
        row: r,
        busy: false,
        err: EXACT_SJ_REQUIRED_MESSAGE,
      });
      return;
    }
    if (previewEnabled && r.preview) {
      const previewOrder = previewOrderFromDummyRows(r.id, getPwaDeliverySjPreviewRows());
      if (previewOrder) {
        if (needsShipment && !orderMatchesRequestedShipment(activeRole.id, previewOrder, shipmentId)) {
          setTask({
            open: true,
            order: null,
            shipmentId,
            row: r,
            busy: false,
            err: EXACT_SJ_REQUIRED_MESSAGE,
          });
          return;
        }
        setTask({ open: true, order: previewOrder, shipmentId, row: r, busy: false, err: "" });
        return;
      }
    }
    setTask({ open: true, order: null, shipmentId, row: r, busy: true, err: "" });
    (async () => {
      try {
        const d = await withTimeout(api.getOrder(r.id), MOBILE_LOAD_TIMEOUT_MS);
        const loaded = d?.order || d;
        if (needsShipment && !orderMatchesRequestedShipment(activeRole.id, loaded, shipmentId)) {
          setTask((prev) => ({
            ...prev,
            order: null,
            busy: false,
            err: EXACT_SJ_REQUIRED_MESSAGE,
          }));
          return;
        }
        setTask((prev) => ({ ...prev, order: loaded, busy: false }));
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
                  {visible.length} / {sjRows.length} entri
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
            {showTable && previewEnabled && (
              <p className="mop-ds-preview-note" data-testid="mds-sj-preview-banner">
                Mode fixture lokal. 3 dummy skenario — tidak menulis produksi.
              </p>
            )}
            {showTable && !busy && !err && (
              <div
                className="mop-schedule-scroll production-sheet production-sheet-viewport mop-ds-sheet-viewport"
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
                          key={pwaDeliverySjRowKey(r)}
                          className="production-sheet-row mop-schedule-row mop-ds-sj-row"
                          data-testid={r.shipment_id ? `mds-row-${r.order_number}-${r.shipment_id}` : `mds-row-${r.order_number}`}
                          data-to={workHref}
                          tabIndex={0}
                          onClick={(event) => activateRow(r, event)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              activateRow(r, event);
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
                              title="Cetak"
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
                          data-testid={sjRows.length === 0 ? "mds-empty" : "mds-filter-empty"}
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
