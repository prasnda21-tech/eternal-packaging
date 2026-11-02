import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  ExternalLink,
  FileText,
  Info,
  Package,
  Plus,
  Printer,
  RefreshCw,
  Search,
  Trash2,
  Truck,
  X,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDesktopListDate } from "../lib/constants";
import {
  LOADING_DELETE_PERM,
  MSG_LOADING_DELETE_SJ_UNRESOLVED,
  deletableLoadingConfirmationsForCancelledSj,
  loadingConfirmationsForSj,
} from "../lib/loadingConfirm";
import HeaderSelectFilter from "../components/HeaderSelectFilter";
import SjDeliveryLocationPicker, { sjOverridePayload } from "../components/SjDeliveryLocationPicker";
import { SHIPPING_DATE_PRESET_OPTIONS } from "../lib/salesDatePresets";
import { showLoadedDocumentCount } from "../lib/salesSubmenuDensity";
import { clickableDocumentRowProps } from "../lib/clickableListRow";
import PoIdentityBadges from "../components/PoIdentityBadges";
import { listIdentityBranchId, listIdentityUserId } from "../lib/listIdentity";
import { salesDeliveryListCache } from "../lib/smartListCaches";
import { useDebouncedLiveSearch } from "../hooks/useDebouncedLiveSearch";
import LookupSearchSelect from "../components/LookupSearchSelect";
import SalesInvoiceDateField from "../components/SalesInvoiceDateField";
import { deliveryBelumSjMeta } from "../lib/ux90RemainingMeta";
import {
  buildCombinedAllocationRows,
  buildSourceAllocationsPayload,
  formatSourcePoLabel,
  lineAllocKey,
  validateMultiSourceSelection,
} from "../lib/salesDeliveryMultiSource";
import {
  buildSjSourceAllocationRows,
  financialStatusLabel,
  formatRelationEventLabel,
  fulfillmentRecoveryUi,
  isPartialFailedOneshot,
  partialFailedSources,
  paymentStatusLabel,
  salesDeliveryDocumentStatusLabel,
  sharedProofSummary,
  sjFulfillmentStatusLabels,
  sortRelationEvents,
} from "../lib/salesDocumentRelations";
import "./salesSubmenuDensity.css";
import "./pengirimanPesananList.css";
import "./ux90SalesCreate.css";
import { getWibTodayString } from "../lib/wibCalendarDate";

/** Legacy API-cap constant (PR #605). Kept for tests/compat; UI uses server page+total. */
export const DELIVERY_LIST_API_CAP = 1000;

function getTodayString() {
  return getWibTodayString();
}

const PHONE_BLANKS = new Set(["", "-", "--", "n/a", "na", "none", "null", "tidak ada"]);

/** Same digit rules as backend normalize_whatsapp. Empty/invalid → "". */
export function recipientWhatsappDigits(value) {
  const folded = String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
  if (PHONE_BLANKS.has(folded)) return "";
  let digits = String(value || "").replace(/\D+/g, "");
  if (digits.length < 8) return "";
  if (digits.startsWith("0")) digits = `62${digits.slice(1)}`;
  if (digits.startsWith("8") && digits.length >= 9) digits = `62${digits}`;
  return digits;
}

/** List-row Sales label — uses delivery.sales / sales_name only (never created_by). */
export function deliveryListSalesLabel(row) {
  return String(row?.sales_name || row?.sales || "").trim();
}

/** Counts for status tabs from a full filter-context list (no status param). */
export function computeDeliveryStatusCounts(rows) {
  const list = Array.isArray(rows) ? rows : [];
  let SEBAGIAN = 0;
  let SELESAI = 0;
  for (const d of list) {
    if (String(d?.status || "").toUpperCase() === "SELESAI") SELESAI += 1;
    else SEBAGIAN += 1;
  }
  return { all: list.length, SEBAGIAN, SELESAI };
}

export const DELIVERY_DEFAULT_PAGE_SIZE = 50;
export const DELIVERY_PAGE_SIZES = Object.freeze([10, 25, 50, 100]);

export function parseDeliveryPageSize(raw) {
  const value = Number(raw);
  return DELIVERY_PAGE_SIZES.includes(value) ? value : DELIVERY_DEFAULT_PAGE_SIZE;
}

export function deliveryListRangeLabel(total, page, pageSize) {
  const count = Number(total) || 0;
  const size = Number(pageSize) || DELIVERY_DEFAULT_PAGE_SIZE;
  const current = Number(page) || 1;
  if (count <= 0) return "Menampilkan 0 pengiriman";
  const from = (current - 1) * size + 1;
  const to = Math.min(count, current * size);
  return `Menampilkan ${from.toLocaleString("id-ID")}–${to.toLocaleString("id-ID")} dari ${count.toLocaleString("id-ID")} pengiriman`;
}

export function deliveryStatusCountsFromResponse(res) {
  const raw = res?.counts;
  if (!raw || typeof raw !== "object") return null;
  const num = (v) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };
  return {
    all: num(raw.all),
    SEBAGIAN: num(raw.SEBAGIAN),
    SELESAI: num(raw.SELESAI),
  };
}

export default function PengirimanPesanan() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, hasPerm } = useAuth();
  const listUserId = listIdentityUserId(user);
  const listBranchId = listIdentityBranchId(user);

  const canCreate = hasPerm("sales.delivery.create") || hasPerm("sales_order.create") || hasPerm("po.edit") || user?.role === "admin";
  const canPrint = hasPerm("sales.delivery.print") || hasPerm("sales.delivery.view") || hasPerm("sales_order.view") || user?.role === "admin";
  const canDelete = hasPerm("sales.delivery.delete") || hasPerm("sales.delivery.create") || hasPerm("po.edit") || user?.role === "admin";
  const canDeleteLoading = hasPerm(LOADING_DELETE_PERM);
  const canOneshotLoading = hasPerm("loading.confirm_match") || user?.role === "admin";
  const canOneshotPod = hasPerm("delivery.update") || user?.role === "admin";

  // Navigation mode: 'list' | 'create' | 'detail'
  const queryPoId = searchParams.get("po_id") || "";
  const initialMode = (searchParams.get("action") === "create" || !!queryPoId) ? "create" : "list";
  const [viewMode, setViewMode] = useState(initialMode);
  
  // List state — no server pagination; cache keyed by filter identity only.
  const [deliveries, setDeliveries] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listRefreshing, setListRefreshing] = useState(false);
  const [listErr, setListErr] = useState("");
  const [listSyncErr, setListSyncErr] = useState("");
  const [listShowingCache, setListShowingCache] = useState(false);
  const {
    input: searchInput,
    setInput: setSearchInput,
    q: searchQuery,
    flush: flushSearch,
    clear: clearSearch,
  } = useDebouncedLiveSearch(searchParams.get("q") || "");
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get("status") || "all");
  const [deliveryDatePreset, setDeliveryDatePreset] = useState(() => searchParams.get("period") || "all");
  const [createdByFilter, setCreatedByFilter] = useState(() => searchParams.get("created_by") || "");
  const [createdByOptions, setCreatedByOptions] = useState([]);
  const [page, setPage] = useState(() => {
    const value = Number(searchParams.get("page"));
    return Number.isFinite(value) && value >= 1 ? Math.floor(value) : 1;
  });
  const [pageSize, setPageSize] = useState(() => parseDeliveryPageSize(searchParams.get("page_size")));
  const [listTotal, setListTotal] = useState(0);
  /** null = loading/unavailable (not zero). From backend counts (filter context sans status). */
  const [statusCounts, setStatusCounts] = useState(null);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");
  const [deleteSuccess, setDeleteSuccess] = useState("");
  /** After soft-cancel SJ: related Loading still active (manual Hapus; never auto-cancel). */
  const [loadingFollowUp, setLoadingFollowUp] = useState(null);
  const [loadingFollowUpBusyId, setLoadingFollowUpBusyId] = useState("");
  const [loadingFollowUpErr, setLoadingFollowUpErr] = useState("");
  const [loadingFollowUpOk, setLoadingFollowUpOk] = useState("");
  const [loadingFollowUpDeleteConfirm, setLoadingFollowUpDeleteConfirm] = useState(null);
  const requestSeq = useRef(0);
  const prevListUserRef = useRef(listUserId);

  // Selected PO for Create Delivery Form
  const [selectedPoId, setSelectedPoId] = useState(queryPoId);
  const [poSummary, setPoSummary] = useState(null);
  const [poSummaryLoading, setPoSummaryLoading] = useState(false);
  const [poSummaryErr, setPoSummaryErr] = useState("");
  const [multiPoMode, setMultiPoMode] = useState(false);
  const [selectedPoIds, setSelectedPoIds] = useState(() => (queryPoId ? [queryPoId] : []));
  const [poSummariesById, setPoSummariesById] = useState({});

  // Create Form State
  const [formData, setFormData] = useState({
    delivery_date: getTodayString(),
    outlet: "",
    recipient: "",
    address: "",
    delivery_zone: "",
    sales: "",
    expedition: "Internal fleet",
    notes: "",
    line_qtys: {}, // { [line_idx]: number }
    line_notes: {}, // { [line_idx]: string }
  });
  const [phoneSource, setPhoneSource] = useState("manual");
  const [manualPhone, setManualPhone] = useState("");
  const [phoneError, setPhoneError] = useState("");
  const [formBusy, setFormBusy] = useState(false);
  const [formErr, setFormErr] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const [sjLocationOverride, setSjLocationOverride] = useState(null);

  // Detail Modal / View State
  const [selectedDeliveryId, setSelectedDeliveryId] = useState(null);
  const [detailData, setDetailData] = useState(null);
  const [oneshotBusy, setOneshotBusy] = useState("");
  const [oneshotErr, setOneshotErr] = useState("");
  const [oneshotOk, setOneshotOk] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailErr, setDetailErr] = useState("");
  const [deliveryFormTab, setDeliveryFormTab] = useState("items");

  // Available POs for selection dropdown when creating from scratch
  const [availablePOs, setAvailablePOs] = useState([]);
  const [loadingPOs, setLoadingPOs] = useState(false);

  // Load deliveries — display-only SWR with server pagination (never authorizes qty writes).
  const loadDeliveries = useCallback(async ({ force = false } = {}) => {
    const cacheKey = salesDeliveryListCache.buildKey({
      u: listUserId,
      b: listBranchId,
      q: searchQuery,
      status: statusFilter,
      period: deliveryDatePreset,
      createdBy: createdByFilter,
      page,
      pageSize,
    });
    const cached = salesDeliveryListCache.get(cacheKey);
    const hasCache = Boolean(cached?.data);
    if (hasCache) {
      const cachedRows = Array.isArray(cached.data.deliveries) ? cached.data.deliveries : [];
      setDeliveries(cachedRows);
      setListTotal(Number(cached.data.total) || cachedRows.length);
      if (cached.data.statusCounts) setStatusCounts(cached.data.statusCounts);
      setListShowingCache(true);
      setSelectedIds(new Set());
    } else {
      setDeliveries([]);
      setStatusCounts(null);
      setListShowingCache(false);
    }

    const seq = requestSeq.current + 1;
    requestSeq.current = seq;
    if (hasCache) {
      setListRefreshing(true);
      setListLoading(false);
    } else {
      setListLoading(true);
      setListRefreshing(false);
    }
    setListErr("");
    try {
      const listParams = {
        page,
        page_size: pageSize,
      };
      if (searchQuery.trim()) listParams.q = searchQuery.trim();
      if (deliveryDatePreset && deliveryDatePreset !== "all") {
        listParams.delivery_date_preset = deliveryDatePreset;
      }
      if (createdByFilter.trim()) listParams.created_by = createdByFilter.trim();
      if (statusFilter !== "all") listParams.status = statusFilter;

      const res = await api.listSalesDeliveries(listParams);
      if (seq !== requestSeq.current) return;
      const rows = res.deliveries || [];
      const total = Number(res.total);
      const nextTotal = Number.isFinite(total) ? total : rows.length;
      const nextCounts = deliveryStatusCountsFromResponse(res);
      const safePage = Math.max(1, Number(res.page) || page);
      if (safePage !== page) setPage(safePage);
      salesDeliveryListCache.set(cacheKey, {
        deliveries: rows,
        res,
        total: nextTotal,
        statusCounts: nextCounts,
      });
      setDeliveries(rows);
      setListTotal(nextTotal);
      setStatusCounts(nextCounts);
      setListShowingCache(false);
      setListSyncErr("");
      setSelectedIds(new Set());
    } catch (e) {
      if (seq !== requestSeq.current) return;
      const det = e.response?.data?.detail;
      const message = typeof det === "object" ? det.message : (det || "Gagal memuat daftar Pengiriman Pesanan");
      if (hasCache) {
        setListSyncErr(message || "Gagal menyinkronkan daftar. Menampilkan data tersimpan sementara.");
        setListShowingCache(true);
      } else {
        setListErr(message);
        setStatusCounts(null);
        setListTotal(0);
      }
    } finally {
      if (seq === requestSeq.current) {
        setListLoading(false);
        setListRefreshing(false);
      }
    }
  }, [createdByFilter, deliveryDatePreset, listBranchId, listUserId, page, pageSize, searchQuery, statusFilter]);

  const reloadDeliveryListAuthoritative = useCallback(async () => {
    salesDeliveryListCache.invalidate();
    setListShowingCache(false);
    setListSyncErr("");
    await loadDeliveries({ force: true });
  }, [loadDeliveries]);

  const loadDeliveryFilterOptions = useCallback(async () => {
    try {
      const data = await api.salesDeliveryFilterOptions();
      setCreatedByOptions(Array.isArray(data?.created_by_options) ? data.created_by_options : []);
    } catch {
      setCreatedByOptions([]);
    }
  }, []);

  const resetDeliveryFilters = () => {
    clearSearch();
    setStatusFilter("all");
    setDeliveryDatePreset("all");
    setCreatedByFilter("");
    setPage(1);
  };

  const createdBySelectOptions = useMemo(() => ([
    { value: "", label: "Semua" },
    ...createdByOptions.map((item) => ({
      value: item.value,
      label: item.label || item.value,
    })),
  ]), [createdByOptions]);

  // Load active POs with remaining quantity
  const loadActivePOs = useCallback(async () => {
    if (!canCreate) return;
    setLoadingPOs(true);
    try {
      const res = await api.listSalesOrders({ status: "aktif" });
      const activeList = (res.orders || []).filter(
        (o) => !o.cancelled && (!o.delivery_summary || o.delivery_summary.total_remaining > 0)
      );
      setAvailablePOs(activeList);
    } catch {
      // Best effort load POs
    } finally {
      setLoadingPOs(false);
    }
  }, [canCreate]);

  const poSummaryReqRef = useRef(0);

  // Load PO summary when selectedPoId changes
  const loadPoSummary = useCallback(async (poId) => {
    setManualPhone("");
    setPhoneError("");
    setPhoneSource("manual");
    if (!poId) {
      poSummaryReqRef.current += 1;
      setPoSummary(null);
      setSjLocationOverride(null);
      setFormData((prev) => ({ ...prev, notes: "" }));
      return;
    }
    const reqId = ++poSummaryReqRef.current;
    setPoSummaryLoading(true);
    setPoSummaryErr("");
    setFormErr("");
    try {
      const data = await api.getPoDeliverySummary(poId);
      if (reqId !== poSummaryReqRef.current) return;
      setPoSummary(data);
      setSjLocationOverride(null);
      const customerPhone = String(data.customer_phone || "").trim();
      setPhoneSource(customerPhone ? "customer" : "manual");

      // Pre-fill header fields; Keterangan from orders.notes (API summary).
      setFormData((prev) => {
        const initialQtys = {};
        const initialNotes = {};
        (data.lines || []).forEach((l) => {
          initialQtys[l.line_idx] = l.remaining_qty;
          initialNotes[l.line_idx] = "";
        });

        return {
          ...prev,
          delivery_date: prev.delivery_date || getTodayString(),
          outlet: data.outlet || "",
          recipient: data.recipient || "",
          address: data.address || "",
          delivery_zone: data.delivery_zone || "",
          sales: data.sales || "",
          expedition: data.expedition || "Internal fleet",
          notes: String(data.notes || "").trim(),
          line_qtys: initialQtys,
          line_notes: initialNotes,
        };
      });
    } catch (e) {
      if (reqId !== poSummaryReqRef.current) return;
      const det = e.response?.data?.detail;
      setPoSummaryErr(typeof det === "object" ? det.message : (det || "Gagal memuat rincian PO"));
    } finally {
      if (reqId === poSummaryReqRef.current) setPoSummaryLoading(false);
    }
  }, []);

  // Load detail of a delivery
  const loadDeliveryDetail = useCallback(async (delivId) => {
    if (!delivId) return;
    setDetailLoading(true);
    setDetailErr("");
    setOneshotErr("");
    setOneshotOk("");
    try {
      const data = await api.getSalesDelivery(delivId);
      setDetailData(data);
    } catch (e) {
      const det = e.response?.data?.detail;
      setDetailErr(typeof det === "object" ? det.message : (det || "Gagal memuat rincian Pengiriman Pesanan"));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const handleOneshotLoadingAll = useCallback(async () => {
    const sjId = detailData?.delivery?.id;
    if (!sjId || oneshotBusy) return;
    setOneshotBusy("loading");
    setOneshotErr("");
    setOneshotOk("");
    try {
      const actionId = `sj-load-${sjId}-${Date.now()}`;
      const res = await api.salesDeliveryLoadingConfirmAll(sjId, { action_id: actionId });
      setOneshotOk(res?.idempotent ? "Loading semua source sudah lengkap (idempotent)." : "Loading semua source berhasil.");
      await loadDeliveryDetail(sjId);
    } catch (e) {
      const det = e.response?.data?.detail;
      const msg = typeof det === "object" ? (det.message || JSON.stringify(det)) : (det || e.message || "One-shot Loading gagal");
      setOneshotErr(msg);
      if (detailData?.delivery?.id) await loadDeliveryDetail(detailData.delivery.id);
    } finally {
      setOneshotBusy("");
    }
  }, [detailData, oneshotBusy, loadDeliveryDetail]);

  const handleOneshotPodAll = useCallback(async () => {
    const sjId = detailData?.delivery?.id;
    if (!sjId || oneshotBusy) return;
    setOneshotBusy("pod");
    setOneshotErr("");
    setOneshotOk("");
    try {
      const actionId = `sj-pod-${sjId}-${Date.now()}`;
      const res = await api.salesDeliveryConfirmDeliveredAll(sjId, { action_id: actionId });
      setOneshotOk(res?.idempotent ? "POD semua source sudah lengkap (idempotent)." : "Konfirmasi diterima semua source berhasil.");
      await loadDeliveryDetail(sjId);
    } catch (e) {
      const det = e.response?.data?.detail;
      const msg = typeof det === "object" ? (det.message || JSON.stringify(det)) : (det || e.message || "One-shot POD gagal");
      setOneshotErr(msg);
      if (detailData?.delivery?.id) await loadDeliveryDetail(detailData.delivery.id);
    } finally {
      setOneshotBusy("");
    }
  }, [detailData, oneshotBusy, loadDeliveryDetail]);

  const handleFulfillmentReconcile = useCallback(async () => {
    const sjId = detailData?.delivery?.id;
    if (!sjId || oneshotBusy) return;
    setOneshotBusy("reconcile");
    setOneshotErr("");
    setOneshotOk("");
    try {
      const res = await api.salesDeliveryFulfillmentReconcile(sjId, {
        healer_action_id: `heal-${sjId}-${Date.now()}`,
      });
      setOneshotOk(
        res?.status === "complete"
          ? "Reconcile: semua source complete — fence dibersihkan."
          : `Reconcile selesai · status ${res?.status || "updated"}.`,
      );
      await loadDeliveryDetail(sjId);
    } catch (e) {
      const det = e.response?.data?.detail;
      setOneshotErr(typeof det === "object" ? (det.message || JSON.stringify(det)) : (det || e.message || "Reconcile gagal"));
      if (detailData?.delivery?.id) await loadDeliveryDetail(detailData.delivery.id);
    } finally {
      setOneshotBusy("");
    }
  }, [detailData, oneshotBusy, loadDeliveryDetail]);

  const handleFulfillmentResume = useCallback(async () => {
    const sjId = detailData?.delivery?.id;
    if (!sjId || oneshotBusy) return;
    const gate = fulfillmentRecoveryUi(detailData?.oneshot_fulfillment, detailData?.delivery);
    if (!gate.resumeAvailable || gate.manual || gate.inProgress) return;
    const kind = gate.resumeKind || "loading";
    setOneshotBusy("resume");
    setOneshotErr("");
    setOneshotOk("");
    try {
      const res = await api.salesDeliveryFulfillmentResume(sjId, {
        action_id: `resume-${kind}-${sjId}-${Date.now()}`,
        kind,
      });
      setOneshotOk(
        res?.ok
          ? (res?.idempotent ? "Resume: sudah lengkap (idempotent)." : "Lanjutkan sisa berhasil.")
          : `Resume status: ${res?.status || "selesai"}`,
      );
      await loadDeliveryDetail(sjId);
    } catch (e) {
      const det = e.response?.data?.detail;
      setOneshotErr(typeof det === "object" ? (det.message || JSON.stringify(det)) : (det || e.message || "Resume gagal"));
      if (detailData?.delivery?.id) await loadDeliveryDetail(detailData.delivery.id);
    } finally {
      setOneshotBusy("");
    }
  }, [detailData, oneshotBusy, loadDeliveryDetail]);

  const openDeliveryDetail = useCallback((delivery) => {
    if (!delivery?.id) return;
    setSelectedDeliveryId(delivery.id);
    loadDeliveryDetail(delivery.id);
  }, [loadDeliveryDetail]);

  useEffect(() => {
    if (viewMode === "list") {
      loadDeliveries();
      loadDeliveryFilterOptions();
    }
  }, [viewMode, loadDeliveries, loadDeliveryFilterOptions]);

  useEffect(() => {
    if (viewMode !== "list") return;
    if (searchParams.get("action") === "create" || searchParams.get("po_id")) return;
    const next = new URLSearchParams();
    if (searchQuery.trim()) next.set("q", searchQuery.trim());
    if (statusFilter && statusFilter !== "all") next.set("status", statusFilter);
    if (deliveryDatePreset && deliveryDatePreset !== "all") next.set("period", deliveryDatePreset);
    if (createdByFilter.trim()) next.set("created_by", createdByFilter.trim());
    if (page > 1) next.set("page", String(page));
    if (pageSize !== DELIVERY_DEFAULT_PAGE_SIZE) next.set("page_size", String(pageSize));
    if (next.toString() === searchParams.toString()) return;
    setSearchParams(next, { replace: true });
  }, [
    viewMode,
    searchQuery,
    statusFilter,
    deliveryDatePreset,
    createdByFilter,
    page,
    pageSize,
    searchParams,
    setSearchParams,
  ]);

  useEffect(() => {
    setPage(1);
  }, [searchQuery, statusFilter, deliveryDatePreset, createdByFilter, pageSize]);

  useEffect(() => {
    const pageCount = Math.max(1, Math.ceil((Number(listTotal) || 0) / pageSize) || 1);
    if (page > pageCount) setPage(pageCount);
  }, [page, pageSize, listTotal]);

  useEffect(() => {
    if (prevListUserRef.current === listUserId) return;
    salesDeliveryListCache.clear();
    prevListUserRef.current = listUserId;
  }, [listUserId]);

  useEffect(() => {
    if (viewMode !== "list") return undefined;
    const onVisible = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      if (!salesDeliveryListCache.shouldRevalidateOnFocus()) return;
      loadDeliveries({ force: false });
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [viewMode, loadDeliveries]);

  useEffect(() => {
    if (viewMode === "create") {
      loadActivePOs();
      if (selectedPoId) {
        loadPoSummary(selectedPoId);
      }
    }
  }, [viewMode, selectedPoId, loadActivePOs, loadPoSummary]);

  useEffect(() => {
    const qPo = searchParams.get("po_id");
    const qAct = searchParams.get("action");
    if (qAct === "create") {
      setViewMode("create");
      if (qPo) {
        setSelectedPoId(qPo);
      }
    }
  }, [searchParams]);

  // Handle PO selection from dropdown
  const handleSelectPO = (poId) => {
    setSelectedPoId(poId);
    setSelectedPoIds(poId ? [poId] : []);
    setSearchParams(poId ? { action: "create", po_id: poId } : { action: "create" });
  };

  const combinedAllocRows = useMemo(
    () => (multiPoMode ? buildCombinedAllocationRows(poSummariesById) : []),
    [multiPoMode, poSummariesById],
  );

  const toggleMultiPoId = async (poId, checked) => {
    const next = checked
      ? Array.from(new Set([...selectedPoIds, poId]))
      : selectedPoIds.filter((id) => id !== poId);
    setSelectedPoIds(next);
    if (!checked) {
      setPoSummariesById((prev) => {
        const copy = { ...prev };
        delete copy[poId];
        return copy;
      });
      return;
    }
    try {
      const data = await api.getPoDeliverySummary(poId);
      setPoSummariesById((prev) => ({ ...prev, [poId]: data }));
      setFormData((prev) => {
        const qtys = { ...prev.line_qtys };
        const notes = { ...prev.line_notes };
        (data.lines || []).forEach((l) => {
          const key = lineAllocKey(data.po_id, l.line_idx);
          qtys[key] = l.remaining_qty;
          notes[key] = "";
        });
        const first = Object.keys(prev.line_qtys || {}).length === 0;
        return {
          ...prev,
          outlet: first ? (data.outlet || prev.outlet) : prev.outlet,
          recipient: first ? (data.recipient || prev.recipient) : prev.recipient,
          address: first ? (data.address || prev.address) : prev.address,
          delivery_zone: first ? (data.delivery_zone || prev.delivery_zone) : prev.delivery_zone,
          sales: first ? (data.sales || prev.sales) : prev.sales,
          expedition: first ? (data.expedition || prev.expedition || "Internal fleet") : prev.expedition,
          line_qtys: qtys,
          line_notes: notes,
        };
      });
      if (!selectedPoId) setSelectedPoId(poId);
    } catch (e) {
      const det = e.response?.data?.detail;
      setFormErr(typeof det === "object" ? det.message : (det || "Gagal memuat ringkasan PO"));
      setSelectedPoIds((prev) => prev.filter((id) => id !== poId));
    }
  };

  // Change shipped qty for a specific line item
  const handleLineQtyChange = (lineIdx, rawVal) => {
    const val = parseInt(rawVal, 10);
    const num = isNaN(val) ? 0 : Math.max(0, val);
    setFormData((prev) => ({
      ...prev,
      line_qtys: {
        ...prev.line_qtys,
        [lineIdx]: num,
      },
    }));
  };

  // Change notes for a specific line item
  const handleLineNoteChange = (lineIdx, val) => {
    setFormData((prev) => ({
      ...prev,
      line_notes: {
        ...prev.line_notes,
        [lineIdx]: val,
      },
    }));
  };

  // Fill all remaining quantities
  const handleFillAllRemaining = () => {
    if (multiPoMode) {
      const qtys = {};
      combinedAllocRows.forEach((row) => {
        qtys[row.key] = row.remaining_qty;
      });
      setFormData((prev) => ({ ...prev, line_qtys: qtys }));
      return;
    }
    if (!poSummary?.lines) return;
    const qtys = {};
    poSummary.lines.forEach((l) => {
      qtys[l.line_idx] = l.remaining_qty;
    });
    setFormData((prev) => ({ ...prev, line_qtys: qtys }));
  };

  // Clear all line quantities
  const handleClearAllQtys = () => {
    if (multiPoMode) {
      const qtys = {};
      combinedAllocRows.forEach((row) => {
        qtys[row.key] = 0;
      });
      setFormData((prev) => ({ ...prev, line_qtys: qtys }));
      return;
    }
    if (!poSummary?.lines) return;
    const qtys = {};
    poSummary.lines.forEach((l) => {
      qtys[l.line_idx] = 0;
    });
    setFormData((prev) => ({ ...prev, line_qtys: qtys }));
  };

  // Total Qty to ship in current form
  const customerPhoneAvailable = String(
    (multiPoMode
      ? (poSummariesById[selectedPoIds[0]] || poSummary)?.customer_phone
      : poSummary?.customer_phone) || "",
  ).trim();
  const totalShippingQty = useMemo(() => {
    if (multiPoMode) {
      return combinedAllocRows.reduce((acc, row) => acc + (formData.line_qtys[row.key] || 0), 0);
    }
    if (!poSummary?.lines) return 0;
    return poSummary.lines.reduce((acc, l) => acc + (formData.line_qtys[l.line_idx] || 0), 0);
  }, [multiPoMode, combinedAllocRows, poSummary, formData.line_qtys]);

  // Validate if any line exceeds remaining
  const overshipmentErrors = useMemo(() => {
    if (multiPoMode) {
      const errs = [];
      combinedAllocRows.forEach((row) => {
        const q = formData.line_qtys[row.key] || 0;
        if (q > row.remaining_qty) {
          errs.push(
            `Item '${row.name}' (${row.source_po_number}): kuantitas kirim (${q}) melebihi sisa (${row.remaining_qty} ${row.unit})`,
          );
        }
      });
      return errs;
    }
    if (!poSummary?.lines) return [];
    const errs = [];
    poSummary.lines.forEach((l) => {
      const q = formData.line_qtys[l.line_idx] || 0;
      if (q > l.remaining_qty) {
        errs.push(`Item '${l.name}': kuantitas kirim (${q}) melebihi sisa PO (${l.remaining_qty} ${l.unit})`);
      }
    });
    return errs;
  }, [multiPoMode, combinedAllocRows, poSummary, formData.line_qtys]);

  // Submit delivery
  const handleSubmitDelivery = async (e) => {
    e.preventDefault();
    setFormErr("");
    setSuccessMsg("");

    if (multiPoMode) {
      const mergeErr = validateMultiSourceSelection(poSummariesById);
      if (mergeErr) {
        setFormErr(mergeErr);
        return;
      }
    } else if (!poSummary) {
      setFormErr("Silakan pilih Pesanan Penjualan (PO) terlebih dahulu.");
      return;
    }

    if (!String(formData.delivery_date || "").trim()) {
      setFormErr("Tanggal pengiriman wajib diisi (DD/MM/YYYY).");
      return;
    }

    if (totalShippingQty <= 0) {
      setFormErr("Kuantitas pengiriman sekarang harus lebih besar dari 0.");
      return;
    }

    if (overshipmentErrors.length > 0) {
      setFormErr(overshipmentErrors.join("; "));
      return;
    }

    const anchorSummary = multiPoMode
      ? (poSummariesById[selectedPoIds[0]] || Object.values(poSummariesById)[0])
      : poSummary;
    const customerPhone = String(anchorSummary?.customer_phone || "").trim();
    const source = phoneSource === "customer" && customerPhone ? "customer" : "manual";
    if (source === "manual") {
      const typed = manualPhone.trim();
      if (typed && !recipientWhatsappDigits(typed)) {
        setPhoneError("Nomor WhatsApp penerima tidak valid");
        return;
      }
    }
    setPhoneError("");

    let payload;
    if (multiPoMode) {
      const sourceAllocations = buildSourceAllocationsPayload(
        combinedAllocRows,
        formData.line_qtys,
        formData.line_notes,
      );
      payload = {
        po_id: anchorSummary?.po_id,
        delivery_date: formData.delivery_date,
        outlet: formData.outlet,
        recipient: formData.recipient,
        address: formData.address,
        delivery_zone: formData.delivery_zone,
        sales: formData.sales,
        expedition: formData.expedition,
        notes: formData.notes,
        source_allocations: sourceAllocations,
        recipient_phone_source: source,
      };
    } else {
      const lineItemsPayload = [];
      poSummary.lines.forEach((l) => {
        const q = formData.line_qtys[l.line_idx] || 0;
        if (q > 0) {
          lineItemsPayload.push({
            line_idx: l.line_idx,
            line_id: l.line_id,
            qty: q,
            notes: formData.line_notes[l.line_idx] || "",
          });
        }
      });
      payload = {
        po_id: poSummary.po_id,
        delivery_date: formData.delivery_date,
        outlet: formData.outlet,
        recipient: formData.recipient,
        address: formData.address,
        delivery_zone: formData.delivery_zone,
        sales: formData.sales,
        expedition: formData.expedition,
        notes: formData.notes,
        line_items: lineItemsPayload,
        recipient_phone_source: source,
      };
    }
    if (source === "manual") payload.recipient_phone = manualPhone.trim();
    const locationOverride = sjOverridePayload(sjLocationOverride);
    if (locationOverride) payload.delivery_location = locationOverride;

    setFormBusy(true);
    try {
      const res = await api.createSalesDelivery(payload);
      setSuccessMsg(res.message || "Pengiriman Pesanan berhasil dibuat");
      
      // Auto open detail for the newly created delivery
      const newDelivId = res.delivery?.id;
      if (newDelivId) {
        setSelectedDeliveryId(newDelivId);
        loadDeliveryDetail(newDelivId);
      }

      // Refresh list
      void reloadDeliveryListAuthoritative();
      setViewMode("list");
      setSearchParams({});
      setMultiPoMode(false);
      setPoSummariesById({});
      setSelectedPoIds([]);
    } catch (err) {
      const det = err.response?.data?.detail;
      setFormErr(typeof det === "object" ? det.message : (det || "Gagal membuat Pengiriman Pesanan"));
    } finally {
      setFormBusy(false);
    }
  };

  // Open PDF directly
  const handlePrintPdf = (deliveryId) => {
    const url = api.salesDeliveryPdfUrl(deliveryId);
    window.open(url, "_blank");
  };

  const toggleSelect = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    if (selectedIds.size === deliveries.length && deliveries.length > 0) setSelectedIds(new Set());
    else setSelectedIds(new Set(deliveries.map((d) => d.id)));
  };
  const formatDeleteError = (det) => {
    if (typeof det === "object" && det) {
      const blockers = det.blockers || [];
      if (blockers.length) {
        return `${det.message || "Tidak dapat menghapus."} ${blockers.map((b) => `${b.number || b.id}: ${b.reason}`).join(" ")}`.trim();
      }
      return det.message || JSON.stringify(det);
    }
    return det || "Gagal menghapus.";
  };
  const clearLoadingFollowUpUi = () => {
    setLoadingFollowUp(null);
    setLoadingFollowUpErr("");
    setLoadingFollowUpOk("");
    setLoadingFollowUpDeleteConfirm(null);
    setLoadingFollowUpBusyId("");
  };

  const formatLoadingDeleteError = (err) => {
    const status = err?.response?.status;
    const detail = formatDeleteError(err?.response?.data?.detail);
    if (status === 403) {
      return detail || "Tidak punya permission loading.delete.";
    }
    if (status === 404) {
      return detail || "Konfirmasi Loading tidak ditemukan.";
    }
    if (status === 409) {
      return detail || "Konflik data — coba lagi. Loading mungkin sudah berubah.";
    }
    if (detail && /tidak dapat dibuktikan|ambigu|hilang/i.test(detail)) {
      return detail;
    }
    return detail || err?.message || "Gagal menghapus Loading terkait.";
  };

  const refreshLoadingFollowUp = async (orderId, sjId, sjNumber) => {
    if (!orderId || !sjId) {
      clearLoadingFollowUpUi();
      return;
    }
    try {
      const payload = await api.getOrder(orderId, { view: "operational" });
      const order = payload?.order || payload;
      const confirmations = deletableLoadingConfirmationsForCancelledSj(order, sjId);
      const related = loadingConfirmationsForSj(order, sjId);
      // Unresolved SJ identity: related Loading stays fail-closed (no Hapus CTA).
      const unresolvedBlocked = related.filter((c) => {
        if (confirmations.some((d) => String(d.id) === String(c.id))) return false;
        const token = String(c?.shipment_id || c?.surat_jalan_id || c?.sales_delivery_id || "").trim();
        if (!token) return false;
        const ships = Array.isArray(order?.shipments) ? order.shipments : [];
        const exact = ships.filter((sh) => String(sh?.id || "").trim() === token);
        if (exact.length === 1) return false;
        const alias = ships.filter((sh) => (
          String(sh?.sales_delivery_id || "").trim() === token
          || String(sh?.sj_number || "").trim() === token
          || String(sh?.number || "").trim() === token
        ));
        return alias.length !== 1;
      });
      if (!confirmations.length && !unresolvedBlocked.length) {
        clearLoadingFollowUpUi();
        return;
      }
      setLoadingFollowUp({
        orderId,
        sjId,
        sjNumber: sjNumber || sjId,
        confirmations,
        unresolvedBlocked,
      });
      setLoadingFollowUpDeleteConfirm(null);
    } catch (_) {
      /* follow-up is advisory; cancel already succeeded */
      clearLoadingFollowUpUi();
    }
  };

  const requestDeleteRelatedLoading = (confirmation) => {
    if (!loadingFollowUp || !confirmation?.id || !canDeleteLoading) return;
    if (loadingFollowUpBusyId) return;
    // Prevent opening a second Hapus confirm while one is already open.
    if (loadingFollowUpDeleteConfirm) return;
    setLoadingFollowUpErr("");
    setLoadingFollowUpOk("");
    setLoadingFollowUpDeleteConfirm({
      id: confirmation.id,
      sjNumber: confirmation.sj_number || confirmation.surat_jalan_name || loadingFollowUp.sjNumber,
    });
  };

  const handleDeleteRelatedLoading = async () => {
    const confirmationId = loadingFollowUpDeleteConfirm?.id;
    const orderId = loadingFollowUp?.orderId;
    const sjId = loadingFollowUp?.sjId;
    const sjNumber = loadingFollowUp?.sjNumber;
    if (!orderId || !confirmationId) return;
    if (loadingFollowUpBusyId) return;
    if (!canDeleteLoading) {
      setLoadingFollowUpErr("Tidak punya permission loading.delete.");
      return;
    }
    setLoadingFollowUpBusyId(String(confirmationId));
    setLoadingFollowUpErr("");
    setLoadingFollowUpOk("");
    try {
      await api.deleteLoadingConfirm(orderId, confirmationId);
      setLoadingFollowUpOk("Konfirmasi Loading terkait dihapus.");
      setLoadingFollowUpDeleteConfirm(null);
      // Refresh Loading list from latest operational order source.
      await refreshLoadingFollowUp(orderId, sjId, sjNumber);
    } catch (e) {
      setLoadingFollowUpErr(formatLoadingDeleteError(e));
    } finally {
      setLoadingFollowUpBusyId("");
    }
  };

  const handleDelete = async (id) => {
    if (deleteLoading) return;
    setDeleteLoading(true);
    setDeleteErr("");
    setDeleteSuccess("");
    clearLoadingFollowUpUi();
    try {
      const result = await api.deleteSalesDelivery(id);
      setDeleteConfirm(null);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      if (result?.soft_cancelled || result?.cancelled) {
        setDeleteSuccess(result?.message || "Surat Jalan dibatalkan. Stok dan qty pesanan telah dikoreksi.");
        const orderId = result?.order_id || result?.po_id;
        const sjId = result?.id || id;
        await refreshLoadingFollowUp(orderId, sjId, result?.sj_number);
      } else if (result?.deleted) {
        setDeleteSuccess("Surat Jalan dihapus.");
      } else {
        setDeleteSuccess("Pembatalan berhasil.");
      }
      await void reloadDeliveryListAuthoritative();
      if (selectedPoId) {
        // Refresh create-form remaining qty if user still has a PO open.
        try {
          const summary = await api.getPoDeliverySummary(selectedPoId);
          setPoSummary(summary);
        } catch (_) {
          /* ignore summary refresh errors after cancel */
        }
      }
    } catch (e) {
      setDeleteErr(formatDeleteError(e.response?.data?.detail) || e.message || "Gagal membatalkan.");
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleBulkDelete = async () => {
    if (deleteLoading) return;
    setDeleteLoading(true);
    setDeleteErr("");
    setDeleteSuccess("");
    try {
      const ids = deleteConfirm?.ids || Array.from(selectedIds);
      await api.bulkDeleteSalesDeliveries(ids);
      setDeleteConfirm(null);
      setSelectedIds(new Set());
      setDeleteSuccess("Pembatalan massal selesai.");
      await void reloadDeliveryListAuthoritative();
    } catch (e) {
      setDeleteErr(formatDeleteError(e.response?.data?.detail) || e.message || "Gagal membatalkan.");
    } finally {
      setDeleteLoading(false);
    }
  };

  const showDeliveryCount = showLoadedDocumentCount({
    loading: listLoading,
    error: listErr,
    query: searchQuery,
  }) && statusFilter === "all"
    && deliveryDatePreset === "all"
    && !createdByFilter;

  const deliveryCountLabel = statusCounts == null
    ? listTotal
    : (statusFilter === "all" ? statusCounts.all : (statusCounts[statusFilter] ?? listTotal));
  const pageCount = Math.max(1, Math.ceil((Number(listTotal) || 0) / pageSize) || 1);
  const pageWindowStart = Math.max(1, page - 2);
  const pageWindowEnd = Math.min(pageCount, pageWindowStart + 4);
  const pageButtons = [];
  for (let n = pageWindowStart; n <= pageWindowEnd; n += 1) pageButtons.push(n);

  return (
    <div
      className={
        "page-container penjualan-page penjualan-workspace sd-density "
        + (viewMode === "list" ? "sd-list-mode sd-delivery-list" : "sd-form-mode")
        + (viewMode === "create" ? " ux90-pj-delivery-host" : "")
      }
      data-testid="pengiriman-pesanan-page"
    >
      <header className={viewMode === "list" ? "sd-list-header" : "sd-form-header"}>
        <div className={viewMode === "list" ? "sd-list-heading" : "sd-form-heading"}>
          {viewMode === "create" && (
            <button
              type="button"
              className="sd-back"
              onClick={() => {
                setViewMode("list");
                setSearchParams({});
              }}
              data-testid="btn-back-to-delivery-list"
            >
              <ArrowLeft size={16} /> Kembali
            </button>
          )}
          <h1>{viewMode === "create" ? "Buat Pengiriman Pesanan" : "Pengiriman Pesanan"}</h1>
          {viewMode === "list" && showDeliveryCount && (
            <div className="sd-list-summary" data-testid="delivery-list-count">
              <strong>{Number(deliveryCountLabel).toLocaleString("id-ID")}</strong>
              <span>pengiriman</span>
              {listRefreshing ? <span data-testid="delivery-list-refreshing"> · menyegarkan</span> : null}
            </div>
          )}
          {viewMode === "list" && !showDeliveryCount && listRefreshing ? (
            <div className="sd-list-summary" data-testid="delivery-list-refreshing">menyegarkan</div>
          ) : null}
        </div>
        {viewMode === "list" && (
          <div className="sd-list-header-actions">
            {canDelete && selectedIds.size > 0 && (
              <button
                type="button"
                className="sd-btn-danger"
                data-testid="btn-batal-terpilih"
                onClick={() => {
                  setDeleteErr("");
                  setDeleteConfirm({
                    ids: Array.from(selectedIds),
                    numbers: deliveries.filter((d) => selectedIds.has(d.id)).map((d) => d.sj_number),
                  });
                }}
              >
                <Trash2 size={16} /> Batal Terpilih ({selectedIds.size})
              </button>
            )}
            <button
              type="button"
              className="sd-list-refresh"
              onClick={() => { void reloadDeliveryListAuthoritative(); }}
              disabled={listLoading || listRefreshing}
              data-testid="refresh-delivery-btn"
              aria-label="Muat ulang"
              title="Muat ulang"
            >
              <RefreshCw size={16} className={(listLoading || listRefreshing) ? "spin" : ""} />
            </button>
            {canCreate && (
              <button
                type="button"
                className="sd-btn-primary"
                onClick={() => {
                  setViewMode("create");
                  setSelectedPoId("");
                  setDeliveryFormTab("items");
                  setSearchParams({ action: "create" });
                }}
                data-testid="btn-new-delivery-order"
              >
                <Plus size={16} /> Buat Pengiriman
              </button>
            )}
          </div>
        )}
      </header>

      {/* SUCCESS BANNER */}
      {successMsg && (
        <div
          className="alert-box success"
          data-testid="delivery-success-banner"
          style={{ background: "#ecfdf5", border: "1px solid #a7f3d0", color: "#047857", padding: "12px 16px", borderRadius: 8, marginBottom: 16, display: "flex", alignItems: "center", justifyContent: "space-between" }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <CheckCircle2 size={18} />
            <span>{successMsg}</span>
          </div>
          <button type="button" onClick={() => setSuccessMsg("")} style={{ background: "none", border: "none", cursor: "pointer", color: "#047857" }}>
            <X size={16} />
          </button>
        </div>
      )}

      {/* VIEW: CREATE DELIVERY — Eternal UX 9.0 allocation workspace */}
      {viewMode === "create" && (
        <form
          className="sd-form-fill pj-delivery-form ux90-create-shell ux90-pj-delivery"
          data-form-tab={deliveryFormTab}
          onSubmit={handleSubmitDelivery}
          style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}
        >
          {formErr ? (
            <div className="alert-box error" data-testid="form-error-banner" style={{ margin: "8px 12px 0", flex: "0 0 auto" }}>
              <AlertCircle size={16} />
              <span>{formErr}</span>
            </div>
          ) : null}

          <div className="ux90-workspace">
            <div className="ux90-main ux90-delivery-doc">
              {/* Accurate-style document header: label-left rows, two columns */}
              <div className="ux90-delivery-doc-header" data-ux90-region="delivery-info" data-testid="delivery-header-fields">
                <div className="ux90-delivery-doc-col is-left">
                  <div className="ux90-dlrow ux90-field-po">
                    <label htmlFor="delivery-po-lookup">Ambil dari PO <span className="ux90-req">*</span></label>
                    <div className="ux90-dlrow-control">
                      {queryPoId && !multiPoMode ? (
                        <input
                          id="delivery-po-lookup"
                          className="ux90-input"
                          type="text"
                          readOnly
                          value={poSummary?.po_number ? `${poSummary.po_number}` : queryPoId}
                          data-testid="display-po-number"
                        />
                      ) : multiPoMode ? (
                        <div data-testid="multi-po-picker" className="ux90-multi-po-picker">
                          {(availablePOs || []).slice(0, 40).map((p) => (
                            <label key={p.id} className="ux90-multi-po-picker-row">
                              <input
                                type="checkbox"
                                checked={selectedPoIds.includes(p.id)}
                                disabled={formBusy}
                                onChange={(ev) => toggleMultiPoId(p.id, ev.target.checked)}
                                data-testid={`multi-po-check-${p.id}`}
                              />
                              <span>{p.po_number} · {p.customer}</span>
                            </label>
                          ))}
                          {!availablePOs.length ? <span className="ux90-hint">Tidak ada PO aktif dengan sisa qty.</span> : null}
                        </div>
                      ) : (
                        <LookupSearchSelect
                          options={availablePOs}
                          value={selectedPoId}
                          onChange={(id) => handleSelectPO(id || "")}
                          getOptionValue={(p) => p.id}
                          getOptionLabel={(p) => String(p.po_number || p.id || "")}
                          getOptionMeta={(p) => [
                            p.customer,
                            p.outlet || null,
                            deliveryBelumSjMeta(p),
                          ].filter(Boolean).join(" · ")}
                          getSearchText={(p) => [p.po_number, p.customer, p.outlet].filter(Boolean).join(" ")}
                          placeholder="Ketik nomor PO atau customer…"
                          noResultLabel="Tidak ada hasil yang cocok"
                          disabled={formBusy}
                          testId="select-po-for-delivery"
                        />
                      )}
                      {!queryPoId ? (
                        <label style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 6, fontSize: 12 }}>
                          <input
                            type="checkbox"
                            checked={multiPoMode}
                            disabled={formBusy}
                            onChange={(ev) => {
                              const on = ev.target.checked;
                              setMultiPoMode(on);
                              setFormErr("");
                              if (on) {
                                setPoSummary(null);
                                setSelectedPoIds([]);
                                setPoSummariesById({});
                                void loadActivePOs();
                              } else {
                                setPoSummariesById({});
                                setSelectedPoIds(selectedPoId ? [selectedPoId] : []);
                              }
                            }}
                            data-testid="toggle-multi-po-mode"
                          />
                          Gabungkan beberapa PO dalam 1 SJ
                        </label>
                      ) : null}
                    </div>
                  </div>
                  <div className="ux90-dlrow">
                    <label htmlFor="input-delivery-date">Tanggal <span className="ux90-req">*</span></label>
                    <div className="ux90-dlrow-control ux90-delivery-date-control">
                      <SalesInvoiceDateField
                        valueIso={formData.delivery_date}
                        onChange={(iso) => setFormData((p) => ({ ...p, delivery_date: iso }))}
                        disabled={formBusy}
                        testId="input-delivery-date"
                        calendarLabel="Kalender tanggal pengiriman"
                      />
                    </div>
                  </div>
                  <div className="ux90-dlrow">
                    <label htmlFor="so-delivery-customer">Pelanggan</label>
                    <div className="ux90-dlrow-control">
                      <input
                        id="so-delivery-customer"
                        className="ux90-input"
                        type="text"
                        readOnly
                        value={
                          multiPoMode
                            ? (Object.values(poSummariesById)[0]?.customer || "-")
                            : (poSummary?.customer || "-")
                        }
                        data-testid="display-customer"
                      />
                    </div>
                  </div>
                </div>
                <div className="ux90-delivery-doc-col is-right">
                  <div className="ux90-dlrow">
                    <label htmlFor="so-delivery-sj">Nomor SJ</label>
                    <div className="ux90-dlrow-control">
                      <input
                        id="so-delivery-sj"
                        className="ux90-input"
                        type="text"
                        readOnly
                        value={
                          multiPoMode
                            ? (Object.values(poSummariesById)[0]?.suggested_sj_number
                              ? `${Object.values(poSummariesById)[0].suggested_sj_number} (multi-PO)`
                              : "(Pilih PO)")
                            : (poSummary?.suggested_sj_number
                              ? `${poSummary.suggested_sj_number} (#${poSummary.next_shipment_index})`
                              : "(Pilih PO)")
                        }
                        data-testid="display-suggested-sj"
                      />
                    </div>
                  </div>
                  <div className="ux90-dlrow">
                    <label htmlFor="so-delivery-outlet">Outlet / Cabang</label>
                    <div className="ux90-dlrow-control">
                      <input
                        id="so-delivery-outlet"
                        className="ux90-input"
                        type="text"
                        value={formData.outlet}
                        onChange={(e) => setFormData((p) => ({ ...p, outlet: e.target.value }))}
                        placeholder="Nama cabang / outlet"
                        data-testid="input-delivery-outlet"
                      />
                    </div>
                  </div>
                </div>
              </div>

              <div className="ux90-delivery-doc-body">
                {/* Accurate-style vertical icon tabs beside workspace */}
                <div className="ux90-delivery-vtabs ux90-form-tabs" role="tablist" aria-label="Bagian pengiriman" data-testid="delivery-form-tabs">
                  <button
                    type="button"
                    role="tab"
                    className={deliveryFormTab === "items" ? "is-active" : ""}
                    aria-selected={deliveryFormTab === "items"}
                    title="Rincian Pengiriman"
                    onClick={() => setDeliveryFormTab("items")}
                    data-testid="delivery-tab-items"
                  >
                    <FileText size={16} aria-hidden="true" />
                    <span>Rincian</span>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    className={deliveryFormTab === "terms" ? "is-active" : ""}
                    aria-selected={deliveryFormTab === "terms"}
                    title="Informasi Pengiriman"
                    onClick={() => setDeliveryFormTab("terms")}
                    data-testid="delivery-tab-terms"
                  >
                    <Info size={16} aria-hidden="true" />
                    <span>Info</span>
                  </button>
                </div>

                <div className="ux90-delivery-doc-panel">
              <div
                className="ux90-table-wrap"
                data-ux90-region="delivery-lines"
                hidden={deliveryFormTab !== "items"}
                style={deliveryFormTab !== "items" ? { display: "none" } : undefined}
              >
                <div className="ux90-table-head">
                  {(multiPoMode ? combinedAllocRows.length > 0 : poSummary?.lines?.length > 0) ? (
                    <div className="ux90-table-actions">
                      <button type="button" onClick={handleFillAllRemaining} data-testid="btn-fill-all-remaining">+ Tambah Semua Sisa</button>
                      <button type="button" className="is-muted" onClick={handleClearAllQtys} data-testid="btn-clear-all-qtys">Clear</button>
                    </div>
                  ) : <span />}
                  <h3>Alokasi Qty Surat Jalan <span className="ux90-req">*</span></h3>
                </div>
                <div className="ux90-table-scroll" data-testid="delivery-lines-scroll">
                  {poSummaryLoading && !multiPoMode ? (
                    <div style={{ padding: 24, textAlign: "center", color: "#64748b" }}>Memuat rincian PO...</div>
                  ) : multiPoMode ? (
                    combinedAllocRows.length === 0 ? (
                      <div className="ux90-empty-alloc" data-testid="delivery-multi-po-empty-hint">
                        <Package size={28} style={{ opacity: 0.4 }} />
                        <p style={{ margin: 0 }}>Centang minimal 2 PO di atas untuk alokasi gabungan.</p>
                      </div>
                    ) : (
                      <table className="ux90-table" data-testid="multi-po-alloc-table">
                        <thead>
                          <tr>
                            <th>#</th>
                            <th>PO Sumber</th>
                            <th>Produk</th>
                            <th className="is-num">Sisa</th>
                            <th className="is-num">Qty Kirim <span className="ux90-req">*</span></th>
                            <th>Catatan</th>
                          </tr>
                        </thead>
                        <tbody>
                          {combinedAllocRows.map((row, idx) => {
                            const qtyVal = formData.line_qtys[row.key] ?? 0;
                            const isOver = qtyVal > row.remaining_qty;
                            const isZeroRemaining = row.remaining_qty <= 0;
                            return (
                              <tr key={row.key} data-testid={`multi-delivery-line-${row.key}`} style={{ background: isOver ? "#fef2f2" : undefined }}>
                                <td>{idx + 1}</td>
                                <td style={{ fontWeight: 600 }}>{row.source_po_number}</td>
                                <td>{row.name}</td>
                                <td className="is-num">{row.remaining_qty.toLocaleString("id-ID")} {row.unit}</td>
                                <td className="is-num">
                                  <input
                                    className={`ux90-qty-input${isOver ? " is-over" : ""}`}
                                    type="number"
                                    min="0"
                                    max={row.remaining_qty}
                                    disabled={isZeroRemaining || formBusy}
                                    value={qtyVal}
                                    onChange={(e) => handleLineQtyChange(row.key, e.target.value)}
                                    data-testid={`input-multi-ship-qty-${row.key}`}
                                  />
                                </td>
                                <td>
                                  <input
                                    className="ux90-input"
                                    type="text"
                                    disabled={isZeroRemaining || formBusy}
                                    value={formData.line_notes[row.key] || ""}
                                    onChange={(e) => handleLineNoteChange(row.key, e.target.value)}
                                  />
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    )
                  ) : !poSummary ? (
                    <div className="ux90-empty-alloc" data-testid="delivery-po-empty-hint">
                      <Package size={28} style={{ opacity: 0.4 }} />
                      <p style={{ margin: 0 }}>Pilih Nomor PO di atas untuk mengisi qty pengiriman.</p>
                    </div>
                  ) : (
                    <table className="ux90-table">
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>Produk</th>
                          <th className="is-num">Qty PO</th>
                          <th className="is-num">Sudah Dikirim</th>
                          <th className="is-num">Sisa</th>
                          <th className="is-num">Qty Kirim <span className="ux90-req">*</span></th>
                          <th>Catatan</th>
                        </tr>
                      </thead>
                      <tbody>
                        {poSummary.lines.map((line) => {
                          const qtyVal = formData.line_qtys[line.line_idx] ?? 0;
                          const isOver = qtyVal > line.remaining_qty;
                          const isZeroRemaining = line.remaining_qty <= 0;
                          return (
                            <tr key={line.line_idx} data-testid={`delivery-line-row-${line.line_idx}`} style={{ background: isOver ? "#fef2f2" : undefined }}>
                              <td>{line.line_idx + 1}</td>
                              <td>
                                <div style={{ fontWeight: 600 }}>{line.name}</div>
                                {line.color ? <div className="ux90-item-meta">Warna: {line.color}</div> : null}
                                <div className="ux90-item-meta">{line.production_process === "non_print" ? "Non-Cetak" : "Cetak"}</div>
                              </td>
                              <td className="is-num">{line.ordered_qty.toLocaleString("id-ID")} {line.unit}</td>
                              <td className="is-num">{line.shipped_qty.toLocaleString("id-ID")} {line.unit}</td>
                              <td className="is-num" style={{ fontWeight: 600, color: line.remaining_qty > 0 ? "#0284c7" : "#94a3b8" }}>
                                {line.remaining_qty.toLocaleString("id-ID")} {line.unit}
                              </td>
                              <td className="is-num">
                                <input
                                  className={`ux90-qty-input${isOver ? " is-over" : ""}`}
                                  type="number"
                                  min="0"
                                  max={line.remaining_qty}
                                  disabled={isZeroRemaining || formBusy}
                                  value={qtyVal}
                                  onChange={(e) => handleLineQtyChange(line.line_idx, e.target.value)}
                                  data-testid={`input-ship-qty-${line.line_idx}`}
                                />
                                {isOver ? <span className="ux90-inline-err">Max: {line.remaining_qty}</span> : null}
                              </td>
                              <td>
                                <input
                                  className="ux90-input"
                                  type="text"
                                  placeholder="Catatan item..."
                                  disabled={isZeroRemaining || formBusy}
                                  value={formData.line_notes[line.line_idx] || ""}
                                  onChange={(e) => handleLineNoteChange(line.line_idx, e.target.value)}
                                  data-testid={`input-ship-note-${line.line_idx}`}
                                />
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>

              {deliveryFormTab === "items" ? (
                <div className="ux90-doc-summary is-inline is-delivery-compact" data-testid="delivery-qty-summary">
                  <div className="ux90-doc-summary-rows">
                    <div>
                      <span>Total Qty SJ</span>
                      <strong data-testid="total-delivery-qty">{totalShippingQty.toLocaleString("id-ID")} pcs</strong>
                    </div>
                    <div>
                      <span>Sisa PO Sebelum SJ Ini</span>
                      <strong data-testid="delivery-po-remaining-before">
                        {poSummary ? Number(poSummary.total_remaining || 0).toLocaleString("id-ID") : "—"} pcs
                      </strong>
                    </div>
                    <div className="is-total">
                      <span>Sisa setelah SJ</span>
                      <strong>
                        {poSummary
                          ? Math.max(0, Number(poSummary.total_remaining || 0) - totalShippingQty).toLocaleString("id-ID")
                          : "—"}{" "}
                        pcs
                      </strong>
                    </div>
                  </div>
                </div>
              ) : null}

              <div
                className="ux90-doc-pane"
                data-testid="delivery-side-panel"
                hidden={deliveryFormTab !== "terms"}
                aria-hidden={deliveryFormTab !== "terms"}
              >
                  <div data-testid="delivery-shipping-panel">
                    <div className="ux90-doc-info-grid">
                      <div className="ux90-field">
                        <span>Penerima (PIC)</span>
                        <input
                          className="ux90-input"
                          type="text"
                          value={formData.recipient}
                          onChange={(e) => setFormData((p) => ({ ...p, recipient: e.target.value }))}
                          placeholder="Nama penerima di lokasi"
                          data-testid="input-delivery-recipient"
                        />
                      </div>
                      <div className="ux90-field" data-testid="delivery-recipient-phone-field">
                        <span>No. Telepon / WhatsApp</span>
                        <div role="radiogroup" aria-label="No. WhatsApp Penerima" className="ux90-phone-radios">
                          <label style={{ color: customerPhoneAvailable ? "#1e293b" : "#94a3b8" }}>
                            <input
                              type="radio"
                              name="recipient-phone-source"
                              value="customer"
                              checked={phoneSource === "customer"}
                              disabled={!customerPhoneAvailable || formBusy}
                              onChange={() => {
                                if (!customerPhoneAvailable) return;
                                setPhoneSource("customer");
                                setPhoneError("");
                              }}
                              data-testid="phone-source-customer"
                            />
                            Nomor pelanggan
                          </label>
                          <label>
                            <input
                              type="radio"
                              name="recipient-phone-source"
                              value="manual"
                              checked={phoneSource !== "customer"}
                              disabled={formBusy}
                              onChange={() => {
                                setPhoneSource("manual");
                                setPhoneError("");
                              }}
                              data-testid="phone-source-manual"
                            />
                            Manual
                          </label>
                        </div>
                        {phoneSource === "customer" && customerPhoneAvailable ? (
                          <input className="ux90-input" type="text" readOnly value={customerPhoneAvailable} data-testid="display-customer-phone" />
                        ) : (
                          <input
                            className="ux90-input"
                            type="tel"
                            value={manualPhone}
                            onChange={(e) => {
                              setManualPhone(e.target.value);
                              if (phoneError) setPhoneError("");
                            }}
                            placeholder="08… atau +62…"
                            disabled={formBusy}
                            data-testid="input-recipient-phone"
                            autoComplete="tel"
                          />
                        )}
                        {phoneError ? <p className="error" data-testid="recipient-phone-error" style={{ margin: "4px 0 0", color: "#b91c1c", fontSize: 12 }}>{phoneError}</p> : null}
                      </div>
                      <div className="ux90-field">
                        <span>Ekspedisi</span>
                        <input
                          className="ux90-input"
                          type="text"
                          value={formData.expedition}
                          onChange={(e) => setFormData((p) => ({ ...p, expedition: e.target.value }))}
                          placeholder="Internal fleet / nama kurir"
                          data-testid="input-delivery-expedition"
                        />
                      </div>
                      <div className="ux90-field">
                        <span>Zona / Wilayah</span>
                        <input
                          className="ux90-input"
                          type="text"
                          value={formData.delivery_zone}
                          onChange={(e) => setFormData((p) => ({ ...p, delivery_zone: e.target.value }))}
                          placeholder="mis. Jakarta Barat"
                          data-testid="input-delivery-zone"
                        />
                      </div>
                      <div className="ux90-field">
                        <span>Outlet / Cabang</span>
                        <input
                          className="ux90-input"
                          type="text"
                          value={formData.outlet}
                          onChange={(e) => setFormData((p) => ({ ...p, outlet: e.target.value }))}
                          data-testid="input-delivery-outlet-tab"
                        />
                      </div>
                      <div className="ux90-field">
                        <span>Sales</span>
                        <input
                          className="ux90-input"
                          type="text"
                          value={formData.sales}
                          onChange={(e) => setFormData((p) => ({ ...p, sales: e.target.value }))}
                          data-testid="input-delivery-sales"
                        />
                        <input type="hidden" data-testid="input-delivery-sales-tab" value={formData.sales} readOnly />
                      </div>
                      <div className="ux90-field is-span-all">
                        <span>Alamat Pengiriman</span>
                        <textarea
                          className="ux90-input"
                          value={formData.address}
                          onChange={(e) => setFormData((p) => ({ ...p, address: e.target.value }))}
                          placeholder="Alamat tujuan pengiriman..."
                          rows={2}
                          data-testid="input-delivery-address"
                        />
                      </div>
                      {poSummary ? (
                        <div className="ux90-field is-span-all is-loc" data-testid="delivery-location-override">
                          <SjDeliveryLocationPicker
                            poLocation={poSummary.delivery_location || null}
                            override={sjLocationOverride}
                            onOverrideChange={setSjLocationOverride}
                            disabled={formBusy}
                            compact
                          />
                        </div>
                      ) : null}
                      <div className="ux90-field is-span-all">
                        <span>Catatan Pengiriman</span>
                        <textarea
                          className="ux90-input ux90-notes-multiline"
                          rows={2}
                          value={formData.notes}
                          onChange={(e) => setFormData((p) => ({ ...p, notes: e.target.value }))}
                          placeholder="Instruksi, kontak, nomor resi…"
                          data-testid="input-delivery-notes"
                        />
                      </div>
                    </div>
                    <p className="ux90-doc-hint" data-testid="delivery-other-info">
                      Satu PO dapat memiliki beberapa Surat Jalan. Membuat SJ menerbitkan dokumen pengiriman — bukan konfirmasi barang diterima.
                    </p>
                  </div>
              </div>
                </div>
              </div>
            </div>
          </div>

          <div className="ux90-footer sd-form-footer">
            <button
              type="button"
              className="ux90-btn-ghost"
              onClick={() => {
                setViewMode("list");
                setSearchParams({});
              }}
              disabled={formBusy}
              data-testid="btn-cancel-delivery"
            >
              Batal
            </button>
            <button
              type="submit"
              className="ux90-btn-primary primary"
              disabled={formBusy || !poSummary || totalShippingQty <= 0 || overshipmentErrors.length > 0}
              data-testid="btn-submit-delivery-order"
            >
              {formBusy ? "Memproses..." : "Simpan / Terbitkan SJ"}
            </button>
          </div>
        </form>
      )}

      {/* VIEW: LIST DELIVERIES */}
      {viewMode === "list" && (
        <section className="sd-list-panel">
          {listErr && (
            <div className="sd-banner is-error" data-testid="list-error-banner" role="alert">
              <AlertCircle size={16} />
              <span>{listErr}</span>
            </div>
          )}
          {listSyncErr && (
            <div className="sd-banner is-warning" data-testid="delivery-list-sync-error" role="status">
              <AlertCircle size={16} />
              <span>
                {listSyncErr}
                {listShowingCache ? " Data di bawah belum disinkronkan ulang." : ""}
              </span>
            </div>
          )}

          <div className="sd-list-chrome">
            <div
              className="sd-delivery-tabs"
              role="tablist"
              aria-label="Status pengiriman"
              data-testid="delivery-list-tabs"
            >
              {[
                { id: "all", label: "Semua" },
                { id: "SEBAGIAN", label: "Sebagian" },
                { id: "SELESAI", label: "Selesai" },
              ].map((tab) => {
                const selected = statusFilter === tab.id;
                const count = statusCounts == null ? null : Number(statusCounts[tab.id] ?? 0);
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    className={selected ? "is-active" : ""}
                    onClick={() => setStatusFilter(tab.id)}
                    data-testid={`filter-status-${tab.id}`}
                    aria-label={count == null ? tab.label : `${tab.label} ${count}`}
                  >
                    {tab.label}
                    {count == null ? null : (
                      <span className="sd-delivery-tab-count" data-testid={`filter-status-count-${tab.id}`}>
                        {count.toLocaleString("id-ID")}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            <div className="sd-list-toolbar">
              <div className="sd-list-search">
                <Search size={16} />
                <input
                  type="text"
                  placeholder="Cari No SJ / No PO / Customer / Outlet / Sales..."
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") flushSearch(e);
                  }}
                  data-testid="search-deliveries-input"
                />
                {searchInput && (
                  <button type="button" onClick={() => clearSearch()} aria-label="Hapus pencarian">
                    <X size={14} />
                  </button>
                )}
              </div>
              <button
                type="button"
                className="sd-list-reset"
                data-testid="reset-delivery-filters"
                onClick={resetDeliveryFilters}
                disabled={
                  !searchInput
                  && !searchQuery
                  && statusFilter === "all"
                  && deliveryDatePreset === "all"
                  && !createdByFilter
                }
              >
                Reset Filter
              </button>
            </div>
          </div>

          <div className="sd-list-table-scroll">
            <table className="sd-list-table">
              <colgroup>
                {canDelete && <col style={{ width: 36 }} />}
                <col />
                <col style={{ width: 140 }} />
                <col style={{ width: 100 }} />
                <col style={{ width: 90 }} />
                <col style={{ width: 110 }} />
                <col style={{ width: 120 }} />
                <col style={{ width: 230 }} />
              </colgroup>
              <thead>
                <tr>
                  {canDelete && (
                    <th>
                      <input type="checkbox" data-testid="select-all-deliveries" checked={deliveries.length > 0 && selectedIds.size === deliveries.length} onChange={toggleSelectAll} aria-label="Pilih semua baris tampilan" />
                    </th>
                  )}
                  <th>Pelanggan / No. SJ</th>
                  <th>No. PO (jumlah)</th>
                  <th>
                    <HeaderSelectFilter
                      label="Tgl Kirim"
                      testid="sj-th-filter-ship-date"
                      options={SHIPPING_DATE_PRESET_OPTIONS}
                      value={deliveryDatePreset}
                      emptyValue="all"
                      onChange={setDeliveryDatePreset}
                    />
                  </th>
                  <th>Qty</th>
                  <th>Status</th>
                  <th>
                    <HeaderSelectFilter
                      label="Dibuat Oleh"
                      testid="sj-th-filter-created-by"
                      options={createdBySelectOptions}
                      value={createdByFilter}
                      emptyValue=""
                      onChange={setCreatedByFilter}
                    />
                  </th>
                  <th>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {listLoading ? (
                  <tr>
                    <td colSpan={canDelete ? 8 : 7} className="sd-list-empty">
                      Memuat daftar Pengiriman Pesanan...
                    </td>
                  </tr>
                ) : deliveries.length === 0 ? (
                  <tr>
                    <td colSpan={canDelete ? 8 : 7} className="sd-list-empty" data-testid="empty-deliveries">
                      <Truck size={32} aria-hidden="true" />
                      <p>Belum ada data Pengiriman Pesanan yang sesuai.</p>
                      {canCreate && (
                        <button
                          type="button"
                          className="sd-btn-primary"
                          onClick={() => {
                            setViewMode("create");
                            setSelectedPoId("");
                            setDeliveryFormTab("terms");
                            setSearchParams({ action: "create" });
                          }}
                          data-testid="btn-empty-create-delivery"
                        >
                          <Plus size={16} /> Buat Pengiriman Pertama
                        </button>
                      )}
                    </td>
                  </tr>
                ) : (
                  deliveries.map((d) => (
                    <tr
                      key={d.id}
                      {...clickableDocumentRowProps({
                        onOpen: () => openDeliveryDetail(d),
                        className: selectedIds.has(d.id) ? "is-selected" : "",
                        "data-testid": `delivery-row-${d.id}`,
                        ariaLabel: `Buka pengiriman ${d.sj_number || d.id}`,
                      })}
                    >
                      {canDelete && (
                        <td data-no-row-nav onClick={(e) => e.stopPropagation()}>
                          <input type="checkbox" data-testid={`delivery-checkbox-${d.id}`} checked={selectedIds.has(d.id)} onChange={() => toggleSelect(d.id)} aria-label={`Pilih ${d.sj_number}`} />
                        </td>
                      )}
                      <td className="sd-identity">
                        <div className="sd-customer">{d.customer || "-"}</div>
                        <span className="sd-doc is-static" data-testid={`sj-number-${d.sj_number}`}>{d.sj_number}</span>
                        {(d.is_multi_source || (Array.isArray(d.source_po_ids) && d.source_po_ids.length > 1)) ? (
                          <span
                            className="pj-status-badge"
                            style={{ marginLeft: 6 }}
                            data-testid={`sj-multipo-badge-${d.id}`}
                          >
                            multi-PO · {Array.isArray(d.source_po_ids) && d.source_po_ids.length
                              ? d.source_po_ids.length
                              : "2+"} sumber
                          </span>
                        ) : null}
                        {d.outlet ? <span className="sd-support">{d.outlet}</span> : null}
                        <span className="sd-support sd-sales" data-testid={`delivery-sales-${d.id}`}>
                          Sales · {deliveryListSalesLabel(d) || "—"}
                        </span>
                      </td>
                      <td data-no-row-nav onClick={(e) => e.stopPropagation()}>
                        <Link to={`/po/${d.po_id}`} className="sd-doc" data-testid={`po-link-${d.po_number}`}>
                          {d.po_number}
                        </Link>
                        <PoIdentityBadges order={d} testIdPrefix={`pp-po-${d.po_number || d.id}`} />
                      </td>
                      <td>{formatDesktopListDate(d.delivery_date) || "-"}</td>
                      <td className="sd-qty-cell">{Number(d.total_qty || 0).toLocaleString("id-ID")} pcs</td>
                      <td>
                        <span
                          className={
                            "pj-status-badge sd-delivery-status"
                            + (String(d.status).toUpperCase() === "SELESAI" ? " is-selesai" : " is-sebagian")
                          }
                          data-testid={`delivery-status-${d.id}`}
                          title="Status dokumen/alokasi SJ — bukan bukti Loading/POD"
                        >
                          {salesDeliveryDocumentStatusLabel(d.status)}
                        </span>
                      </td>
                      <td className="sd-creator-cell">
                        {d.created_by_name || d.created_by || "-"}
                      </td>
                      <td data-no-row-nav onClick={(e) => e.stopPropagation()}>
                        <div className="sd-actions">
                          <div className="sd-actions-main">
                            <button
                              type="button"
                              className="sd-act sd-act-view"
                              onClick={() => openDeliveryDetail(d)}
                              data-testid={`btn-view-delivery-${d.id}`}
                            >
                              Lihat
                            </button>
                            {canPrint && (
                              <button
                                type="button"
                                className="sd-act sd-act-pdf"
                                onClick={() => handlePrintPdf(d.id)}
                                data-testid={`btn-print-delivery-${d.id}`}
                                title="Cetak PDF Surat Jalan"
                              >
                                <Printer size={14} /> PDF
                              </button>
                            )}
                          </div>
                          {canDelete && !d.is_cancelled && String(d.status || "").toUpperCase() !== "DIBATALKAN" && (
                            <button
                              type="button"
                              className="sd-act sd-act-delete"
                              data-testid={`btn-batal-delivery-${d.id}`}
                              onClick={() => {
                                setDeleteErr("");
                                setDeleteSuccess("");
                                setDeleteConfirm({
                                  id: d.id,
                                  number: d.sj_number,
                                  numbers: [d.sj_number],
                                });
                              }}
                            >
                              Batal
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <footer className="sd-list-footer sd-delivery-pager" data-testid="delivery-list-footer">
            <span data-testid="delivery-list-range">
              {listLoading && deliveries.length === 0
                ? "Memuat daftar pengiriman..."
                : listErr
                ? "Daftar pengiriman belum dapat ditampilkan"
                : deliveryListRangeLabel(listTotal, page, pageSize)}
            </span>
            <span className="sd-list-footer-note">Klik baris untuk membuka detail.</span>
            <label className="sd-delivery-page-size">
              Baris per halaman
              <select
                data-testid="delivery-page-size"
                value={pageSize}
                onChange={(e) => setPageSize(parseDeliveryPageSize(e.target.value))}
                disabled={listLoading && deliveries.length === 0}
              >
                {DELIVERY_PAGE_SIZES.map((size) => (
                  <option key={size} value={size}>{size}</option>
                ))}
              </select>
            </label>
            <div className="sd-delivery-pages">
              <button
                type="button"
                data-testid="delivery-page-prev"
                disabled={page <= 1 || (listLoading && deliveries.length === 0)}
                onClick={() => setPage((value) => Math.max(1, value - 1))}
              >
                Sebelumnya
              </button>
              {pageButtons.map((n) => (
                <button
                  key={n}
                  type="button"
                  data-testid={`delivery-page-${n}`}
                  aria-current={n === page ? "page" : undefined}
                  className={n === page ? "is-current" : ""}
                  onClick={() => setPage(n)}
                >
                  {n}
                </button>
              ))}
              <button
                type="button"
                data-testid="delivery-page-next"
                disabled={page >= pageCount || (listLoading && deliveries.length === 0)}
                onClick={() => setPage((value) => value + 1)}
              >
                Berikutnya
              </button>
            </div>
          </footer>
        </section>
      )}

      {/* DETAIL MODAL — sticky header/footer; body scrolls (presentation only) */}
      {selectedDeliveryId && detailData && (
        <div
          className="modal-overlay sd-delivery-detail-modal"
          data-testid="delivery-detail-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="delivery-detail-title"
        >
          <div className="modal-content sd-modal-card sd-delivery-detail-panel">
            <header className="sd-delivery-detail-head">
              <div className="sd-delivery-detail-title">
                <div className="sd-delivery-detail-icon" aria-hidden="true">
                  <Truck size={18} />
                </div>
                <div className="sd-delivery-detail-title-copy">
                  <h2 id="delivery-detail-title">
                    Detail Surat Jalan: <span>{detailData.delivery?.sj_number}</span>
                  </h2>
                  <span className="sd-delivery-detail-sub">
                    Pengiriman #{detailData.delivery?.shipment_index} · {formatSourcePoLabel(detailData.delivery)}
                    {detailData.delivery?.is_multi_source ? " (multi-PO)" : ` · PO ${detailData.delivery?.po_number}`}
                  </span>
                </div>
              </div>
              <button
                type="button"
                className="sd-delivery-detail-x"
                onClick={() => {
                  setSelectedDeliveryId(null);
                  setDetailData(null);
                }}
                data-testid="btn-close-delivery-modal"
                aria-label="Tutup"
              >
                <X size={20} />
              </button>
            </header>

            <div className="sd-delivery-detail-body" data-testid="delivery-detail-body">
              {(() => {
                const sjStatus = sjFulfillmentStatusLabels(
                  detailData.delivery,
                  detailData.slice_fulfillment,
                  detailData.oneshot_fulfillment,
                );
                const sourceRows = buildSjSourceAllocationRows({
                  delivery: detailData.delivery,
                  sliceInfo: detailData.slice_fulfillment,
                  sourcePoSummaries: detailData.source_po_summaries,
                });
                const sourceTotal = sourceRows.reduce((acc, r) => acc + Number(r.allocated_qty || 0), 0);
                const isMulti = Boolean(
                  detailData.delivery?.is_multi_source
                  || (Array.isArray(detailData.delivery?.source_po_ids) && detailData.delivery.source_po_ids.length > 1)
                  || sourceRows.length > 1,
                );
                return (
                  <>
                    <div
                      className="sd-delivery-detail-meta"
                      data-testid="delivery-detail-header-meta"
                      style={{ marginBottom: 12 }}
                    >
                      <div>
                        <span className="sd-delivery-detail-meta-label">Nomor SJ:</span>
                        <strong data-testid="detail-sj-number">{detailData.delivery?.sj_number || "-"}</strong>
                        {isMulti ? (
                          <span className="pj-status-badge" style={{ marginLeft: 8 }} data-testid="detail-multi-po-badge">
                            multi-PO
                          </span>
                        ) : null}
                      </div>
                      <div>
                        <span className="sd-delivery-detail-meta-label">Customer:</span>
                        <strong data-testid="detail-sj-customer">{detailData.delivery?.customer || "-"}</strong>
                      </div>
                      <div>
                        <span className="sd-delivery-detail-meta-label">Outlet / Cabang:</span>
                        <strong data-testid="detail-sj-outlet">{detailData.delivery?.outlet || "-"}</strong>
                      </div>
                      <div>
                        <span className="sd-delivery-detail-meta-label">Status dokumen:</span>
                        <span
                          data-testid="detail-delivery-status"
                          className={
                            "sd-delivery-detail-status" +
                            (sjStatus.documentLabel === "Penerimaan selesai" ? " is-done" : " is-partial")
                          }
                        >
                          {sjStatus.documentLabel}
                        </span>
                      </div>
                    </div>

                    <div
                      className="sd-delivery-detail-kpi"
                      data-testid="delivery-sj-kpi-summary"
                    >
                      <div>
                        <span className="sd-delivery-detail-kpi-label">TOTAL QTY SJ</span>
                        <strong className="is-shipped" data-testid="detail-total-sj-qty">
                          {Number(sjStatus.totalQty || 0).toLocaleString("id-ID")} pcs
                        </strong>
                      </div>
                      <div>
                        <span className="sd-delivery-detail-kpi-label">JUMLAH PO SUMBER</span>
                        <strong data-testid="detail-source-po-count">
                          {Number(sjStatus.sourcePoCount || 0).toLocaleString("id-ID")} PO
                        </strong>
                      </div>
                      <div>
                        <span className="sd-delivery-detail-kpi-label">QTY DITERIMA</span>
                        <strong
                          className={Number(sjStatus.deliveredQty || 0) > 0 ? "is-ok" : "is-remain"}
                          data-testid="detail-delivered-qty"
                        >
                          {Number(sjStatus.deliveredQty || 0).toLocaleString("id-ID")} pcs
                        </strong>
                      </div>
                      <div>
                        <span className="sd-delivery-detail-kpi-label">LOADING / POD</span>
                        <span data-testid="detail-loading-pod-status" className="sd-delivery-detail-status is-partial">
                          {sjStatus.loadingLabel} · {sjStatus.podLabel}
                        </span>
                      </div>
                    </div>

                    {/* Keep legacy test ids as aliases of SJ metrics (not primary-PO ordered qty). */}
                    <div hidden aria-hidden="true">
                      <span data-testid="detail-total-po-qty">{Number(sjStatus.totalQty || 0)}</span>
                      <span data-testid="delivery-po-progress-summary">{sjStatus.documentLabel}</span>
                      <span data-testid="detail-cumulative-sj-qty">{Number(sjStatus.totalQty || 0)}</span>
                      <span data-testid="detail-remaining-po-qty">
                        {Math.max(0, Number(sjStatus.totalQty || 0) - Number(sjStatus.deliveredQty || 0))}
                      </span>
                    </div>

                    <div data-testid="delivery-source-po-list" style={{ marginBottom: 12 }}>
                      <div className="sd-delivery-detail-meta-label" style={{ marginBottom: 6 }}>
                        Daftar PO Sumber
                      </div>
                      <div className="ux90-table-wrap">
                        <table className="ux90-table" data-testid="sj-source-alloc-table" style={{ fontSize: 12 }}>
                          <thead>
                            <tr>
                              <th>No. PO</th>
                              <th>Produk</th>
                              <th className="is-num">Qty dialokasikan</th>
                              <th className="is-num">Loading</th>
                              <th className="is-num">Diterima</th>
                              <th className="is-num">Sisa belum diterima</th>
                              <th>Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {sourceRows.length === 0 ? (
                              <tr>
                                <td colSpan={7} className="muted">Belum ada alokasi sumber</td>
                              </tr>
                            ) : (
                              sourceRows.map((row) => (
                                <tr key={row.key} data-testid={`sj-source-row-${row.key}`}>
                                  <td>
                                    <Link
                                      to={`/po/${encodeURIComponent(row.source_po_id)}`}
                                      className="sd-delivery-detail-po-link"
                                      data-testid={`sj-source-po-link-${row.source_po_id}`}
                                    >
                                      {row.source_po_number}
                                    </Link>
                                  </td>
                                  <td>{row.product}</td>
                                  <td className="is-num">{Number(row.allocated_qty || 0).toLocaleString("id-ID")}</td>
                                  <td className="is-num">{Number(row.loading_qty || 0).toLocaleString("id-ID")}</td>
                                  <td className="is-num">{Number(row.delivered_qty || 0).toLocaleString("id-ID")}</td>
                                  <td className="is-num">{Number(row.remaining_undelivered || 0).toLocaleString("id-ID")}</td>
                                  <td>{row.status}</td>
                                </tr>
                              ))
                            )}
                          </tbody>
                          <tfoot>
                            <tr data-testid="sj-source-alloc-total">
                              <td colSpan={2}><strong>Total Qty SJ</strong></td>
                              <td className="is-num">
                                <strong>{Number(sourceTotal || sjStatus.totalQty || 0).toLocaleString("id-ID")}</strong>
                              </td>
                              <td colSpan={4} />
                            </tr>
                          </tfoot>
                        </table>
                      </div>
                      {!isMulti && detailData.po_summary ? (
                        <p className="ux90-hint" data-testid="detail-anchor-po-qty-hint" style={{ margin: "6px 0 0" }}>
                          Qty ordered PO anchor ({detailData.delivery?.po_number || "PO"}):{" "}
                          {Number(detailData.po_summary.total_ordered || 0).toLocaleString("id-ID")} pcs
                          {" · sisa kirim PO "}
                          {Number(detailData.po_summary.total_remaining || 0).toLocaleString("id-ID")} pcs
                        </p>
                      ) : null}
                    </div>
                  </>
                );
              })()}

              {(() => {
                const partialFailed = isPartialFailedOneshot(
                  detailData.oneshot_fulfillment,
                  detailData.delivery,
                );
                const recovery = fulfillmentRecoveryUi(
                  detailData.oneshot_fulfillment,
                  detailData.delivery,
                );
                const pf = partialFailedSources(detailData.slice_fulfillment);
                const proof = sharedProofSummary(detailData.oneshot_fulfillment)
                  || sharedProofSummary({ shared_pod_proof: detailData.delivery?.shared_pod_proof });
                const relatedInvoices = Array.isArray(detailData.related_invoices)
                  ? detailData.related_invoices
                  : [];
                const hist = sortRelationEvents(detailData.relation_history || []);
                const oneshotBlocked = partialFailed
                  || recovery.manual
                  || recovery.inProgress
                  || detailData.oneshot_fulfillment?.oneshot_loading_available === false;
                const oneshotPodBlocked = partialFailed
                  || recovery.manual
                  || recovery.inProgress
                  || detailData.oneshot_fulfillment?.oneshot_pod_available === false;
                return (
                  <>
                    {(partialFailed || recovery.manual || recovery.inProgress || recovery.resumeAvailable) ? (
                      <div
                        className="alert-box"
                        data-testid="sj-partial-failed-banner"
                        style={{
                          marginBottom: 12,
                          background: recovery.manual ? "#fef2f2" : "#fff7ed",
                          border: recovery.manual ? "1px solid #fca5a5" : "1px solid #fdba74",
                          color: recovery.manual ? "#991b1b" : "#9a3412",
                        }}
                      >
                        <AlertCircle size={14} />
                        <span data-testid="sj-recovery-status-text">
                          {recovery.complete ? (
                            <>Fulfillment gabungan: complete.</>
                          ) : recovery.manual ? (
                            <>Perlu pemeriksaan (manual review). Resume otomatis dinonaktifkan.</>
                          ) : recovery.inProgress ? (
                            <>Fulfillment sedang berjalan / fence aktif. Tindakan berisiko dinonaktifkan.</>
                          ) : (
                            <>
                              Fulfillment one-shot: {recovery.status || "partial_failed"}
                              {detailData.oneshot_fulfillment?.fulfillment_commit_pending
                                || detailData.delivery?.fulfillment_commit_pending
                                ? " / commit pending"
                                : ""}
                              .
                            </>
                          )}
                          {" "}
                          {recovery.completeSources.length ? (
                            <>Complete: {recovery.completeSources.join(", ")}. </>
                          ) : pf.ok.length ? (
                            <>Berhasil: {pf.ok.map((s) => s.label).join(", ")}. </>
                          ) : null}
                          {recovery.pendingSources.length ? (
                            <>Pending: {recovery.pendingSources.join(", ")}. </>
                          ) : pf.pending.length ? (
                            <>Belum selesai: {pf.pending.map((s) => `${s.label} (${s.state})`).join(", ")}. </>
                          ) : null}
                          {recovery.ambiguousSources.length ? (
                            <>Ambigu: {recovery.ambiguousSources.join(", ")}. </>
                          ) : null}
                          {recovery.reason ? <> {recovery.reason}</> : null}
                        </span>
                        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                          {(recovery.inProgress || partialFailed) && !recovery.manual ? (
                            <button
                              type="button"
                              className="btn-secondary"
                              data-testid="sj-fulfillment-reconcile"
                              disabled={Boolean(oneshotBusy)}
                              onClick={handleFulfillmentReconcile}
                            >
                              {oneshotBusy === "reconcile" ? "Reconcile…" : "Reconcile fence"}
                            </button>
                          ) : null}
                          {recovery.resumeAvailable && !recovery.manual && !recovery.inProgress ? (
                            <button
                              type="button"
                              className="btn-primary"
                              data-testid="sj-fulfillment-resume"
                              disabled={Boolean(oneshotBusy)}
                              title={recovery.reason || "Lanjutkan hanya source pending"}
                              onClick={handleFulfillmentResume}
                            >
                              {oneshotBusy === "resume"
                                ? "Melanjutkan sisa…"
                                : `Lanjutkan sisa (${recovery.resumeKind || "loading"})`}
                            </button>
                          ) : null}
                          {recovery.manual ? (
                            <span className="muted" data-testid="sj-manual-review-flag">Perlu pemeriksaan — gunakan workflow per-PO</span>
                          ) : null}
                        </div>
                      </div>
                    ) : null}

                    {relatedInvoices.length > 0 ? (
                      <div data-testid="sj-related-invoices" style={{ marginBottom: 12 }}>
                        <div className="sd-delivery-detail-meta-label" style={{ marginBottom: 6 }}>Faktur terkait SJ</div>
                        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                          {relatedInvoices.map((inv) => (
                            <li key={inv.id}>
                              <Link
                                to={`/penjualan/faktur-penjualan?invoice_id=${encodeURIComponent(inv.id)}`}
                                data-testid={`sj-rel-inv-link-${inv.id}`}
                              >
                                {inv.invoice_number || inv.id}
                              </Link>
                              {" · "}
                              {financialStatusLabel(inv.financial_status)}
                              {" · "}
                              {paymentStatusLabel(inv.payment_status)}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}

                    {proof ? (
                      <div data-testid="sj-shared-proof-summary" style={{ marginBottom: 12, fontSize: 13 }}>
                        <div className="sd-delivery-detail-meta-label" style={{ marginBottom: 4 }}>Bukti POD bersama</div>
                        <div>
                          Ref: <strong>{proof.reference}</strong>
                          {" · Penerima: "}
                          {proof.recipient_name}
                          {" · Foto: "}
                          {proof.photo_count}
                          {proof.has_signature ? " · ada tanda tangan" : ""}
                        </div>
                      </div>
                    ) : null}

                    {detailData.delivery?.is_multi_source || (detailData.slice_fulfillment?.sources || []).length > 1 ? (
                      <div
                        className="sd-delivery-detail-kpi"
                        data-testid="sj-oneshot-fulfillment-panel"
                        style={{ marginBottom: 12, display: "block" }}
                      >
                        <div className="sd-delivery-detail-meta-label" style={{ marginBottom: 8 }}>
                          Fulfillment SJ gabungan
                          {" · "}
                          <strong data-testid="sj-combined-status">
                            {detailData.slice_fulfillment?.complete
                              ? "PENERIMAAN SELESAI"
                              : detailData.slice_fulfillment?.loading_complete && !detailData.slice_fulfillment?.pod_complete
                                ? "LOADING SELESAI · POD BELUM"
                                : "SEBAGIAN / BELUM LENGKAP"}
                          </strong>
                          {detailData.oneshot_fulfillment?.fulfillment_last_status ? (
                            <span className="muted" data-testid="sj-fulfillment-last-status">
                              {" · last: "}
                              {detailData.oneshot_fulfillment.fulfillment_last_status}
                            </span>
                          ) : null}
                        </div>
                        {/* Compact mirror of source rows for legacy test hooks — visually collapsed. */}
                        <table className="ux90-table" data-testid="sj-slice-status-table" hidden aria-hidden="true">
                          <tbody>
                            {(detailData.slice_fulfillment?.sources || []).map((s) => (
                              <tr key={s.source_po_id}>
                                <td>{s.po_number || s.source_po_id}</td>
                                <td>{Number(s.slice_qty || 0)}</td>
                                <td>{s.loading_complete ? "OK" : "Parsial"}</td>
                                <td>{s.delivered ? "OK" : "Belum"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {detailData.oneshot_fulfillment?.reason ? (
                          <p className="ux90-hint" data-testid="sj-oneshot-blocked-reason" style={{ margin: "0 0 8px" }}>
                            One-shot gabungan: {detailData.oneshot_fulfillment.reason}
                          </p>
                        ) : null}
                        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                          <button
                            type="button"
                            className="btn-secondary"
                            data-testid="sj-loading-confirm-all"
                            disabled={
                              Boolean(oneshotBusy)
                              || !canOneshotLoading
                              || oneshotBlocked
                            }
                            title={
                              oneshotBlocked
                                ? (detailData.oneshot_fulfillment?.reason
                                  || (partialFailed
                                    ? "One-shot dinonaktifkan karena recovery diperlukan"
                                    : "One-shot Loading tidak tersedia"))
                                : "Konfirmasi Loading semua source PO"
                            }
                            onClick={handleOneshotLoadingAll}
                          >
                            {oneshotBusy === "loading" ? "Memproses Loading…" : "Konfirmasi Loading Semua"}
                          </button>
                          <button
                            type="button"
                            className="btn-primary"
                            data-testid="sj-confirm-delivered-all"
                            disabled={
                              Boolean(oneshotBusy)
                              || !canOneshotPod
                              || oneshotPodBlocked
                            }
                            title={
                              oneshotPodBlocked
                                ? (detailData.oneshot_fulfillment?.reason
                                  || (partialFailed
                                    ? "One-shot dinonaktifkan karena recovery diperlukan"
                                    : "One-shot POD tidak tersedia"))
                                : "Konfirmasi diterima semua source PO"
                            }
                            onClick={handleOneshotPodAll}
                          >
                            {oneshotBusy === "pod" ? "Memproses POD…" : "Konfirmasi Diterima Semua"}
                          </button>
                        </div>
                        {oneshotErr ? (
                          <div className="alert-box error" data-testid="sj-oneshot-error" style={{ marginTop: 8 }}>
                            <AlertCircle size={14} /> <span>{oneshotErr}</span>
                          </div>
                        ) : null}
                        {oneshotOk ? (
                          <div className="alert-box success" data-testid="sj-oneshot-success" style={{ marginTop: 8 }}>
                            <CheckCircle2 size={14} /> <span>{oneshotOk}</span>
                          </div>
                        ) : null}
                      </div>
                    ) : null}

                    <details data-testid="sj-relation-history" style={{ fontSize: 12, marginBottom: 12 }}>
                      <summary>Relasi &amp; Kejadian (read-only)</summary>
                      {hist.length === 0 ? (
                        <p className="muted" style={{ margin: "8px 0 0" }}>Belum tercatat</p>
                      ) : (
                        <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                          {hist.map((ev, idx) => (
                            <li key={`${ev.at}-${ev.kind}-${idx}`}>
                              <span className="muted">{ev.at || "Belum tercatat"}</span>
                              {" · "}
                              {formatRelationEventLabel(ev)}
                            </li>
                          ))}
                        </ul>
                      )}
                    </details>
                  </>
                );
              })()}

              <div className="sd-delivery-detail-meta">
                <div>
                  <span className="sd-delivery-detail-meta-label">
                    {detailData.delivery?.is_multi_source ? "PO anchor:" : "Nomor PO:"}
                  </span>
                  <Link to={`/po/${detailData.delivery?.po_id}`} className="sd-delivery-detail-po-link">
                    {detailData.delivery?.po_number}
                  </Link>
                </div>
                <div>
                  <span className="sd-delivery-detail-meta-label">Tanggal Kirim:</span>
                  <strong>{formatDesktopListDate(detailData.delivery?.delivery_date) || "-"}</strong>
                </div>
                <div>
                  <span className="sd-delivery-detail-meta-label">Penerima (PIC):</span>
                  <strong>{detailData.delivery?.recipient || "-"}</strong>
                </div>
                <div>
                  <span className="sd-delivery-detail-meta-label">Sales:</span>
                  <strong>{detailData.delivery?.sales || "-"}</strong>
                </div>
                <div>
                  <span className="sd-delivery-detail-meta-label">Ekspedisi / Zona:</span>
                  <strong>{detailData.delivery?.expedition || "-"} ({detailData.delivery?.delivery_zone || "-"})</strong>
                </div>
                <div>
                  <span className="sd-delivery-detail-meta-label">Dibuat Oleh:</span>
                  <strong>{detailData.delivery?.created_by_name || detailData.delivery?.created_by || "-"}</strong>
                </div>
                <div className="sd-delivery-detail-meta-wide">
                  <span className="sd-delivery-detail-meta-label">Alamat Lengkap:</span>
                  <strong>{detailData.delivery?.address || "-"}</strong>
                </div>
                {detailData.delivery?.notes && (
                  <div className="sd-delivery-detail-meta-wide">
                    <span className="sd-delivery-detail-meta-label">Catatan:</span>
                    <strong>{detailData.delivery?.notes}</strong>
                  </div>
                )}
              </div>

              <div className="sd-delivery-detail-items">
                <h4>Daftar Barang Dikirim pada Surat Jalan Ini:</h4>
                <table className="sd-delivery-detail-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>PO Sumber</th>
                      <th>Barang / Jasa</th>
                      <th>Proses</th>
                      <th className="num">Qty Kirim</th>
                      <th>Satuan</th>
                      <th>Catatan</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(detailData.delivery?.line_items || []).map((li, idx) => (
                      <tr key={li.line_id || `${li.source_po_id || "line"}-${idx}`}>
                        <td className="muted">{idx + 1}</td>
                        <td>
                          {li.source_po_id ? (
                            <Link to={`/po/${encodeURIComponent(li.source_po_id)}`} className="sd-delivery-detail-po-link">
                              {li.source_po_number || li.source_po_id}
                            </Link>
                          ) : (detailData.delivery?.po_number || "-")}
                        </td>
                        <td className="item-name">
                          {li.name}
                          {li.color && <div className="item-color">{li.color}</div>}
                        </td>
                        <td>
                          <span className={"sd-delivery-detail-process" + (li.production_process === "non_print" ? " is-nonprint" : "")}>
                            {li.production_process === "non_print" ? "Non-Cetak" : "Cetak"}
                          </span>
                        </td>
                        <td className="num qty">
                          {Number(li.qty || li.quantity || 0).toLocaleString("id-ID")}
                        </td>
                        <td>{li.unit || "pcs"}</td>
                        <td className="muted">{li.notes || "-"}</td>
                      </tr>
                    ))}
                    <tr className="sd-delivery-detail-total-row">
                      <td colSpan={4} className="num-label">Total Qty SJ:</td>
                      <td className="num total-qty" data-testid="detail-items-total-sj-qty">
                        {Number(detailData.delivery?.total_qty || 0).toLocaleString("id-ID")}
                      </td>
                      <td colSpan={2}>pcs</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              {detailData.all_po_deliveries?.length > 1 && (
                <div className="sd-delivery-detail-history">
                  <h4>
                    <Clock size={16} /> Riwayat Pengiriman untuk PO {detailData.delivery?.po_number}:
                  </h4>
                  <div className="sd-delivery-detail-history-list">
                    {detailData.all_po_deliveries.map((shipment) => (
                      <div
                        key={shipment.id}
                        className={
                          "sd-delivery-detail-history-row" +
                          (shipment.id === detailData.delivery?.id ? " is-current" : "")
                        }
                      >
                        <span>
                          Surat Jalan <strong>{shipment.sj_number}</strong> (Kirim #{shipment.shipment_index}) - {formatDesktopListDate(shipment.delivery_date)}
                        </span>
                        <span className="qty">
                          {Number(shipment.total_qty || 0).toLocaleString("id-ID")} pcs
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <footer className="sd-delivery-detail-foot">
              {canPrint && (
                <button
                  type="button"
                  className="sd-delivery-detail-print"
                  onClick={() => handlePrintPdf(detailData.delivery?.id)}
                  data-testid="btn-print-modal-pdf"
                >
                  <Printer size={16} /> Cetak PDF Surat Jalan
                </button>
              )}
              <button
                type="button"
                className="sd-delivery-detail-close"
                onClick={() => {
                  setSelectedDeliveryId(null);
                  setDetailData(null);
                }}
                data-testid="btn-close-modal-bottom"
              >
                Tutup
              </button>
            </footer>
          </div>
        </div>
      )}

      {deleteSuccess && (
        <div data-testid="batal-success" style={{ position: "fixed", right: 16, bottom: 16, zIndex: 90, background: "#ecfdf5", color: "#065f46", border: "1px solid #6ee7b7", padding: "10px 14px", borderRadius: 10, maxWidth: 420, boxShadow: "0 8px 24px rgba(15,23,42,0.12)" }}>
          {deleteSuccess}
          <button type="button" onClick={() => setDeleteSuccess("")} style={{ marginLeft: 10, border: "none", background: "transparent", color: "#065f46", fontWeight: 700, cursor: "pointer" }}>Tutup</button>
        </div>
      )}

      {loadingFollowUp && (
        <div
          className="modal-overlay"
          data-testid="loading-followup-modal"
          style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 85 }}
        >
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 520 }}>
            <h3 style={{ margin: "0 0 8px" }}>Loading terkait masih aktif</h3>
            <p style={{ margin: "0 0 12px", color: "#64748b", fontSize: "0.9rem" }}>
              Surat Jalan <strong>{loadingFollowUp.sjNumber}</strong> sudah dibatalkan.
              Konfirmasi Loading berikut belum dihapus otomatis — hapus manual jika perlu.
              Loading milik Surat Jalan lain tidak ditampilkan.
            </p>
            <ul data-testid="loading-followup-list" style={{ margin: "0 0 16px", paddingLeft: 0, listStyle: "none", display: "grid", gap: 8 }}>
              {(loadingFollowUp.confirmations || []).map((c) => {
                const qty = (c.lines || []).reduce(
                  (s, ln) => s + Math.max(0, Number(ln.qty || ln.quantity || 0)),
                  0,
                );
                const busy = loadingFollowUpBusyId === String(c.id);
                return (
                  <li
                    key={c.id}
                    data-testid={`loading-followup-row-${c.id}`}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 12,
                      border: "1px solid #e2e8f0",
                      borderRadius: 8,
                      padding: "10px 12px",
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 700 }}>{c.sj_number || c.surat_jalan_name || loadingFollowUp.sjNumber}</div>
                      <div className="muted" style={{ fontSize: "0.85rem" }}>
                        {c.id} · qty {qty.toLocaleString("id-ID")}
                      </div>
                    </div>
                    {canDeleteLoading ? (
                      <button
                        type="button"
                        data-testid={`btn-hapus-loading-followup-${c.id}`}
                        disabled={Boolean(loadingFollowUpBusyId) || Boolean(loadingFollowUpDeleteConfirm)}
                        onClick={() => requestDeleteRelatedLoading(c)}
                        style={{
                          padding: "8px 12px",
                          borderRadius: 8,
                          border: "none",
                          background: busy ? "#94a3b8" : "#dc2626",
                          color: "#fff",
                          fontWeight: 600,
                          cursor: (loadingFollowUpBusyId || loadingFollowUpDeleteConfirm) ? "not-allowed" : "pointer",
                        }}
                      >
                        {busy ? "Menghapus..." : "Hapus Loading terkait"}
                      </button>
                    ) : (
                      <span className="muted" data-testid="loading-followup-no-perm" style={{ fontSize: "0.85rem" }}>
                        Butuh permission loading.delete
                      </span>
                    )}
                  </li>
                );
              })}
              {(loadingFollowUp.unresolvedBlocked || []).map((c) => (
                <li
                  key={`unresolved-${c.id}`}
                  data-testid={`loading-followup-unresolved-${c.id}`}
                  style={{
                    border: "1px solid #fecaca",
                    borderRadius: 8,
                    padding: "10px 12px",
                    background: "#fef2f2",
                  }}
                >
                  <div style={{ fontWeight: 700 }}>{c.sj_number || c.surat_jalan_name || loadingFollowUp.sjNumber}</div>
                  <div className="muted" style={{ fontSize: "0.85rem", marginBottom: 4 }}>{c.id}</div>
                  <div data-testid="loading-followup-unresolved-msg" style={{ color: "#b91c1c", fontSize: "0.85rem" }}>
                    {MSG_LOADING_DELETE_SJ_UNRESOLVED}
                  </div>
                </li>
              ))}
            </ul>
            {loadingFollowUpDeleteConfirm && (
              <div
                data-testid="loading-followup-delete-confirm"
                style={{ background: "#fff7ed", border: "1px solid #fdba74", borderRadius: 8, padding: "12px 14px", marginBottom: 12 }}
              >
                <p style={{ margin: "0 0 10px", fontSize: "0.9rem" }}>
                  Hapus konfirmasi Loading <strong>{loadingFollowUpDeleteConfirm.id}</strong> untuk Surat Jalan{" "}
                  <strong>{loadingFollowUpDeleteConfirm.sjNumber}</strong>?
                </p>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                  <button
                    type="button"
                    data-testid="btn-cancel-loading-followup-delete"
                    disabled={Boolean(loadingFollowUpBusyId)}
                    onClick={() => setLoadingFollowUpDeleteConfirm(null)}
                    style={{ padding: "8px 12px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}
                  >
                    Batal
                  </button>
                  <button
                    type="button"
                    data-testid="btn-confirm-loading-followup-delete"
                    disabled={Boolean(loadingFollowUpBusyId)}
                    onClick={() => handleDeleteRelatedLoading()}
                    style={{
                      padding: "8px 12px",
                      borderRadius: 8,
                      border: "none",
                      background: loadingFollowUpBusyId ? "#94a3b8" : "#dc2626",
                      color: "#fff",
                      fontWeight: 600,
                      cursor: loadingFollowUpBusyId ? "not-allowed" : "pointer",
                    }}
                  >
                    {loadingFollowUpBusyId ? "Menghapus..." : "Ya, hapus Loading"}
                  </button>
                </div>
              </div>
            )}
            {loadingFollowUpErr && (
              <div data-testid="loading-followup-error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>
                {loadingFollowUpErr}
              </div>
            )}
            {loadingFollowUpOk && (
              <div data-testid="loading-followup-success" style={{ background: "#ecfdf5", color: "#065f46", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>
                {loadingFollowUpOk}
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button
                type="button"
                data-testid="btn-close-loading-followup"
                disabled={Boolean(loadingFollowUpBusyId)}
                onClick={() => clearLoadingFollowUpUi()}
                style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}
              >
                Tutup
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteConfirm && (
        <div className="modal-overlay" data-testid="batal-confirm-modal" style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 80 }}>
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 460 }}>
            <h3 style={{ margin: "0 0 8px" }}>Batalkan Surat Jalan?</h3>
            <p style={{ margin: "0 0 12px", color: "#64748b", fontSize: "0.9rem" }}>
              Stok dan qty pesanan akan dikoreksi otomatis. Dokumen, tanda tangan, dan foto tetap tersimpan sebagai riwayat. Tidak perlu mengisi alasan.
            </p>
            <ul data-testid="batal-confirm-numbers" style={{ margin: "0 0 16px", paddingLeft: 18, fontWeight: 700, color: "#0369a1" }}>
              {(deleteConfirm.numbers || [deleteConfirm.number]).filter(Boolean).map((n) => <li key={n}>{n}</li>)}
            </ul>
            {deleteErr && <div data-testid="batal-error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>{deleteErr}</div>}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={() => { setDeleteConfirm(null); setDeleteErr(""); }} disabled={deleteLoading} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}>Tutup</button>
              <button
                type="button"
                data-testid="btn-confirm-batal"
                onClick={() => (deleteConfirm.ids ? handleBulkDelete() : handleDelete(deleteConfirm.id))}
                disabled={deleteLoading}
                style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#dc2626", color: "#fff", fontWeight: 600 }}
              >
                {deleteLoading ? "Membatalkan..." : "Batal"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
