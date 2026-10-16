import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  ExternalLink,
  FileText,
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
import { formatDisplayDate } from "../lib/constants";
import HeaderSelectFilter from "../components/HeaderSelectFilter";
import SjDeliveryLocationPicker, { sjOverridePayload } from "../components/SjDeliveryLocationPicker";
import { SHIPPING_DATE_PRESET_OPTIONS } from "../lib/salesDatePresets";
import { loadedRangeLabel, showLoadedDocumentCount } from "../lib/salesSubmenuDensity";
import { clickableDocumentRowProps } from "../lib/clickableListRow";
import { listIdentityBranchId, listIdentityUserId } from "../lib/listIdentity";
import { salesDeliveryListCache } from "../lib/smartListCaches";
import { useDebouncedLiveSearch } from "../hooks/useDebouncedLiveSearch";
import "./salesSubmenuDensity.css";

/** Soft disclosure when list length hits the known API `.to_list(1000)` ceiling. */
export const DELIVERY_LIST_API_CAP = 1000;

const DELIVERY_STATUS_TABS = [
  { id: "all", label: "Semua" },
  { id: "SEBAGIAN", label: "Sebagian" },
  { id: "SELESAI", label: "Selesai" },
];

function getTodayString() {
  const d = new Date();
  return d.toISOString().split("T")[0];
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

export default function PengirimanPesanan() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, hasPerm } = useAuth();
  const listUserId = listIdentityUserId(user);
  const listBranchId = listIdentityBranchId(user);

  const canCreate = hasPerm("sales.delivery.create") || hasPerm("sales_order.create") || hasPerm("po.edit") || user?.role === "admin";
  const canPrint = hasPerm("sales.delivery.print") || hasPerm("sales.delivery.view") || hasPerm("sales_order.view") || user?.role === "admin";
  const canDelete = hasPerm("sales.delivery.delete") || hasPerm("sales.delivery.create") || hasPerm("po.edit") || user?.role === "admin";

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
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");
  const [deleteSuccess, setDeleteSuccess] = useState("");
  const requestSeq = useRef(0);
  const prevListUserRef = useRef(listUserId);

  // Selected PO for Create Delivery Form
  const [selectedPoId, setSelectedPoId] = useState(queryPoId);
  const [poSummary, setPoSummary] = useState(null);
  const [poSummaryLoading, setPoSummaryLoading] = useState(false);
  const [poSummaryErr, setPoSummaryErr] = useState("");

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
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailErr, setDetailErr] = useState("");
  const [deliveryFormTab, setDeliveryFormTab] = useState("terms");

  // Available POs for selection dropdown when creating from scratch
  const [availablePOs, setAvailablePOs] = useState([]);
  const [loadingPOs, setLoadingPOs] = useState(false);

  // Load deliveries — display-only SWR (no server pagination; never authorizes qty writes).
  const loadDeliveries = useCallback(async ({ force = false } = {}) => {
    const cacheKey = salesDeliveryListCache.buildKey({
      u: listUserId,
      b: listBranchId,
      q: searchQuery,
      status: statusFilter,
      period: deliveryDatePreset,
      createdBy: createdByFilter,
    });
    const cached = salesDeliveryListCache.get(cacheKey);
    const hasCache = Boolean(cached?.data);
    if (hasCache) {
      setDeliveries(Array.isArray(cached.data.deliveries) ? cached.data.deliveries : []);
      setListShowingCache(true);
      setSelectedIds(new Set());
    } else {
      setDeliveries([]);
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
      const params = {};
      if (searchQuery.trim()) params.q = searchQuery.trim();
      if (statusFilter !== "all") params.status = statusFilter;
      if (deliveryDatePreset && deliveryDatePreset !== "all") {
        params.delivery_date_preset = deliveryDatePreset;
      }
      if (createdByFilter.trim()) params.created_by = createdByFilter.trim();
      const res = await api.listSalesDeliveries(params);
      if (seq !== requestSeq.current) return;
      const rows = res.deliveries || [];
      salesDeliveryListCache.set(cacheKey, { deliveries: rows, res });
      setDeliveries(rows);
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
      }
    } finally {
      if (seq === requestSeq.current) {
        setListLoading(false);
        setListRefreshing(false);
      }
    }
  }, [createdByFilter, deliveryDatePreset, listBranchId, listUserId, searchQuery, statusFilter]);

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

  // Load PO summary when selectedPoId changes
  const loadPoSummary = useCallback(async (poId) => {
    setManualPhone("");
    setPhoneError("");
    setPhoneSource("manual");
    if (!poId) {
      setPoSummary(null);
      setSjLocationOverride(null);
      return;
    }
    setPoSummaryLoading(true);
    setPoSummaryErr("");
    setFormErr("");
    try {
      const data = await api.getPoDeliverySummary(poId);
      setPoSummary(data);
      setSjLocationOverride(null);
      const customerPhone = String(data.customer_phone || "").trim();
      setPhoneSource(customerPhone ? "customer" : "manual");

      // Pre-fill header fields
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
          notes: "",
          line_qtys: initialQtys,
          line_notes: initialNotes,
        };
      });
    } catch (e) {
      const det = e.response?.data?.detail;
      setPoSummaryErr(typeof det === "object" ? det.message : (det || "Gagal memuat rincian PO"));
    } finally {
      setPoSummaryLoading(false);
    }
  }, []);

  // Load detail of a delivery
  const loadDeliveryDetail = useCallback(async (delivId) => {
    if (!delivId) return;
    setDetailLoading(true);
    setDetailErr("");
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
    if (next.toString() === searchParams.toString()) return;
    setSearchParams(next, { replace: true });
  }, [
    viewMode,
    searchQuery,
    statusFilter,
    deliveryDatePreset,
    createdByFilter,
    searchParams,
    setSearchParams,
  ]);

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
    setSearchParams(poId ? { action: "create", po_id: poId } : { action: "create" });
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
    if (!poSummary?.lines) return;
    const qtys = {};
    poSummary.lines.forEach((l) => {
      qtys[l.line_idx] = l.remaining_qty;
    });
    setFormData((prev) => ({ ...prev, line_qtys: qtys }));
  };

  // Clear all line quantities
  const handleClearAllQtys = () => {
    if (!poSummary?.lines) return;
    const qtys = {};
    poSummary.lines.forEach((l) => {
      qtys[l.line_idx] = 0;
    });
    setFormData((prev) => ({ ...prev, line_qtys: qtys }));
  };

  // Total Qty to ship in current form
  const customerPhoneAvailable = String(poSummary?.customer_phone || "").trim();
  const totalShippingQty = useMemo(() => {
    if (!poSummary?.lines) return 0;
    return poSummary.lines.reduce((acc, l) => acc + (formData.line_qtys[l.line_idx] || 0), 0);
  }, [poSummary, formData.line_qtys]);

  // Validate if any line exceeds remaining
  const overshipmentErrors = useMemo(() => {
    if (!poSummary?.lines) return [];
    const errs = [];
    poSummary.lines.forEach((l) => {
      const q = formData.line_qtys[l.line_idx] || 0;
      if (q > l.remaining_qty) {
        errs.push(`Item '${l.name}': kuantitas kirim (${q}) melebihi sisa PO (${l.remaining_qty} ${l.unit})`);
      }
    });
    return errs;
  }, [poSummary, formData.line_qtys]);

  // Submit delivery
  const handleSubmitDelivery = async (e) => {
    e.preventDefault();
    setFormErr("");
    setSuccessMsg("");

    if (!poSummary) {
      setFormErr("Silakan pilih Pesanan Penjualan (PO) terlebih dahulu.");
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

    const customerPhone = String(poSummary.customer_phone || "").trim();
    const source = phoneSource === "customer" && customerPhone ? "customer" : "manual";
    if (source === "manual") {
      const typed = manualPhone.trim();
      if (typed && !recipientWhatsappDigits(typed)) {
        setPhoneError("Nomor WhatsApp penerima tidak valid");
        return;
      }
    }
    setPhoneError("");

    // Build payload lines
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

    const payload = {
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
  const handleDelete = async (id) => {
    if (deleteLoading) return;
    setDeleteLoading(true);
    setDeleteErr("");
    setDeleteSuccess("");
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

  return (
    <div className={"page-container penjualan-page penjualan-workspace sd-density " + (viewMode === "list" ? "sd-list-mode" : "sd-form-mode")}>
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
              <strong>{deliveries.length.toLocaleString("id-ID")}</strong>
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
              title="Segarkan"
              aria-label="Segarkan"
              data-testid="refresh-delivery-btn"
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
                  setDeliveryFormTab("terms");
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

      {/* VIEW: CREATE DELIVERY ORDER */}
      {viewMode === "create" && (
        <form className="sd-form-fill pj-delivery-form" data-form-tab={deliveryFormTab} onSubmit={handleSubmitDelivery}>
          <div className="sd-form-stage">
          <div className="sd-form-tabs" role="tablist" aria-label="Bagian pengiriman">
            <button type="button" className={deliveryFormTab === "terms" ? "is-active" : ""} onClick={() => setDeliveryFormTab("terms")}>Pengiriman</button>
            <button type="button" className={deliveryFormTab === "items" ? "is-active" : ""} onClick={() => setDeliveryFormTab("items")}>Barang</button>
          </div>
          {formErr && (
            <div
              className="alert-box error"
              data-testid="form-error-banner"
              style={{ background: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c", padding: "12px 16px", borderRadius: 8, marginBottom: 16, display: "flex", alignItems: "center", gap: 10 }}
            >
              <AlertCircle size={18} />
              <span>{formErr}</span>
            </div>
          )}

          {/* Section 1: PO Selection & Information */}
          <section className="panel sd-pane-terms" style={{ background: "#fff", border: "1px solid #CBD5E1", borderRadius: 10, padding: 12, marginBottom: 8 }}>
            <header style={{ borderBottom: "1px solid #f1f5f9", paddingBottom: 8, marginBottom: 10 }}>
              <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>1. Data Pesanan Penjualan (PO Asal)</h3>
              <p style={{ margin: "4px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
                Data diisi otomatis dari Pesanan Penjualan dan dapat disesuaikan untuk pengiriman ini.
              </p>
            </header>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10 }}>
              {/* Select PO */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Nomor PO <span style={{ color: "#ef4444" }}>*</span>
                </label>
                {queryPoId ? (
                  <input
                    type="text"
                    readOnly
                    value={poSummary?.po_number ? `${poSummary.po_number}` : queryPoId}
                    data-testid="display-po-number"
                    style={{ width: "100%", padding: "8px 12px", border: "1px solid #CBD5E1", borderRadius: 6, fontSize: "0.95rem", background: "#f8fafc", color: "#334155", fontWeight: 600, boxSizing: "border-box" }}
                  />
                ) : (
                  <select
                    value={selectedPoId}
                    onChange={(e) => handleSelectPO(e.target.value)}
                    disabled={formBusy}
                    data-testid="select-po-for-delivery"
                    style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", background: "#fff" }}
                    required
                  >
                    <option value="">-- Pilih Pesanan Penjualan --</option>
                    {availablePOs.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.po_number} - {p.customer} {p.outlet ? `(${p.outlet})` : ""} [Sisa: {p.delivery_summary?.total_remaining ?? p.total_quantity} pcs]
                      </option>
                    ))}
                  </select>
                )}
                <p style={{ margin: "4px 0 0 0", fontSize: "0.75rem", color: "#64748b" }}>
                  Terkunci berdasarkan PO dan urutan pengiriman.
                </p>
              </div>

              {/* SJ Number Read-Only Indicator */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Nomor SJ
                </label>
                <input
                  type="text"
                  readOnly
                  value={poSummary?.suggested_sj_number ? `${poSummary.suggested_sj_number} (Pengiriman #${poSummary.next_shipment_index})` : "(Pilih PO untuk melihat No SJ)"}
                  data-testid="display-suggested-sj"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #CBD5E1", borderRadius: 6, fontSize: "0.95rem", background: "#f8fafc", color: "#334155", fontWeight: 600, boxSizing: "border-box" }}
                />
                <p style={{ margin: "4px 0 0 0", fontSize: "0.75rem", color: "#64748b" }}>
                  Terkunci berdasarkan PO dan urutan pengiriman.
                </p>
              </div>

              {/* Tanggal Pengiriman */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Tanggal Pengiriman <span style={{ color: "#ef4444" }}>*</span>
                </label>
                <input
                  type="date"
                  value={formData.delivery_date}
                  onChange={(e) => setFormData((p) => ({ ...p, delivery_date: e.target.value }))}
                  data-testid="input-delivery-date"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                  required
                />
              </div>

              {/* Customer Read-Only */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Customer
                </label>
                <input
                  type="text"
                  readOnly
                  value={poSummary?.customer || "-"}
                  data-testid="display-customer"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #CBD5E1", borderRadius: 6, fontSize: "0.95rem", background: "#f8fafc", color: "#334155", boxSizing: "border-box" }}
                />
              </div>

              {/* Outlet */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Outlet / Cabang
                </label>
                <input
                  type="text"
                  value={formData.outlet}
                  onChange={(e) => setFormData((p) => ({ ...p, outlet: e.target.value }))}
                  placeholder="Nama cabang / outlet"
                  data-testid="input-delivery-outlet"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Penerima PIC */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Penerima (PIC)
                </label>
                <input
                  type="text"
                  value={formData.recipient}
                  onChange={(e) => setFormData((p) => ({ ...p, recipient: e.target.value }))}
                  placeholder="Nama penerima di lokasi"
                  data-testid="input-delivery-recipient"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              <div style={{ gridColumn: "1 / -1" }} data-testid="delivery-recipient-phone-field">
                <span style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  No. WhatsApp Penerima
                </span>
                <div role="radiogroup" aria-label="No. WhatsApp Penerima" style={{ display: "flex", flexWrap: "wrap", gap: "8px 16px", marginBottom: 6 }}>
                  <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.9rem", color: customerPhoneAvailable ? "#1e293b" : "#94a3b8" }}>
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
                    Gunakan nomor pelanggan
                  </label>
                  <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.9rem", color: "#1e293b" }}>
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
                    Input nomor manual
                  </label>
                </div>
                {phoneSource === "customer" && customerPhoneAvailable ? (
                  <input
                    type="text"
                    readOnly
                    value={customerPhoneAvailable}
                    data-testid="display-customer-phone"
                    style={{ width: "100%", padding: "8px 12px", border: "1px solid #CBD5E1", borderRadius: 6, fontSize: "0.95rem", background: "#f8fafc", color: "#334155", boxSizing: "border-box" }}
                  />
                ) : (
                  <input
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
                    style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                  />
                )}
                {phoneError ? (
                  <p className="error" data-testid="recipient-phone-error" style={{ margin: "4px 0 0", color: "#b91c1c", fontSize: "0.8rem" }}>{phoneError}</p>
                ) : null}
              </div>

              {/* Sales */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Sales Representative
                </label>
                <input
                  type="text"
                  value={formData.sales}
                  onChange={(e) => setFormData((p) => ({ ...p, sales: e.target.value }))}
                  placeholder="Nama sales"
                  data-testid="input-delivery-sales"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Ekspedisi */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Ekspedisi / Armada
                </label>
                <input
                  type="text"
                  value={formData.expedition}
                  onChange={(e) => setFormData((p) => ({ ...p, expedition: e.target.value }))}
                  placeholder="Internal fleet / nama kurir"
                  data-testid="input-delivery-expedition"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>

              {/* Zona Kirim */}
              <div>
                <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                  Zona Kirim
                </label>
                <input
                  type="text"
                  value={formData.delivery_zone}
                  onChange={(e) => setFormData((p) => ({ ...p, delivery_zone: e.target.value }))}
                  placeholder="mis. Jakarta Barat"
                  data-testid="input-delivery-zone"
                  style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
                />
              </div>
            </div>

            {/* Alamat Lengkap */}
            <div style={{ marginTop: 16 }}>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                Alamat Lengkap Pengiriman
              </label>
              <textarea
                value={formData.address}
                onChange={(e) => setFormData((p) => ({ ...p, address: e.target.value }))}
                placeholder="Alamat tujuan pengiriman..."
                rows={2}
                data-testid="input-delivery-address"
                style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
              />
            </div>

            {poSummary && (
              <SjDeliveryLocationPicker
                poLocation={poSummary.delivery_location || null}
                override={sjLocationOverride}
                onOverrideChange={setSjLocationOverride}
                disabled={formBusy}
              />
            )}

            {/* Catatan */}
            <div style={{ marginTop: 12 }}>
              <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 4 }}>
                Catatan Pengiriman
              </label>
              <input
                type="text"
                value={formData.notes}
                onChange={(e) => setFormData((p) => ({ ...p, notes: e.target.value }))}
                placeholder="Instruksi pengiriman, kontak penerima, nomor resi dll."
                data-testid="input-delivery-notes"
                style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.95rem", boxSizing: "border-box" }}
              />
            </div>
          </section>

          {/* Section 2: Line Items & Quantity Allocation */}
          <section className="panel sd-pane-items" style={{ background: "#fff", border: "1px solid #CBD5E1", borderRadius: 10, padding: 12, marginBottom: 8 }}>
            <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #f1f5f9", paddingBottom: 8, marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
              <div>
                <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>2. Alokasi Kuantitas Pengiriman</h3>
                <p style={{ margin: "2px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
                  Tentukan jumlah qty yang dikirimkan pada Surat Jalan ini. Sisa kuantitas PO akan tetap tersedia untuk pengiriman berikutnya.
                </p>
              </div>
              {poSummary?.lines?.length > 0 && (
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    type="button"
                    onClick={handleFillAllRemaining}
                    data-testid="btn-fill-all-remaining"
                    style={{ padding: "6px 12px", background: "#fff", color: "#166534", border: "1px solid #CBD5E1", borderRadius: 6, fontSize: "0.8rem", fontWeight: 600, cursor: "pointer" }}
                  >
                    Kirim Semua Sisa
                  </button>
                  <button
                    type="button"
                    onClick={handleClearAllQtys}
                    data-testid="btn-clear-all-qtys"
                    style={{ padding: "6px 12px", background: "#f8fafc", color: "#475569", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.8rem", fontWeight: 600, cursor: "pointer" }}
                  >
                    Reset 0
                  </button>
                </div>
              )}
            </header>

            {poSummaryLoading ? (
              <div style={{ padding: 32, textAlign: "center", color: "#64748b" }}>
                Memuat rincian PO...
              </div>
            ) : !poSummary ? (
              <div style={{ padding: 40, textAlign: "center", color: "#64748b" }}>
                <Package size={36} style={{ opacity: 0.4, marginBottom: 8 }} />
                <p style={{ margin: 0, fontWeight: 500 }}>Silakan pilih Nomor PO pada bagian 1 di atas.</p>
              </div>
            ) : (
              <div className="sd-lines">
                <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }}>
                  <thead>
                    <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                      <th style={{ padding: "10px 8px", width: 40 }}>#</th>
                      <th style={{ padding: "10px 8px", minWidth: 220 }}>Barang / Jasa</th>
                      <th style={{ padding: "10px 8px", width: 100 }}>Proses</th>
                      <th style={{ padding: "10px 8px", width: 100, textAlign: "right" }}>Qty PO</th>
                      <th style={{ padding: "10px 8px", width: 110, textAlign: "right" }}>Terkirim</th>
                      <th style={{ padding: "10px 8px", width: 110, textAlign: "right" }}>Sisa PO</th>
                      <th style={{ padding: "10px 8px", width: 140 }}>Qty Kirim Sekarang <span style={{ color: "#ef4444" }}>*</span></th>
                      <th style={{ padding: "10px 8px", minWidth: 160 }}>Catatan Baris</th>
                    </tr>
                  </thead>
                  <tbody>
                    {poSummary.lines.map((line) => {
                      const qtyVal = formData.line_qtys[line.line_idx] ?? 0;
                      const isOver = qtyVal > line.remaining_qty;
                      const isZeroRemaining = line.remaining_qty <= 0;

                      return (
                        <tr key={line.line_idx} data-testid={`delivery-line-row-${line.line_idx}`} style={{ borderBottom: "1px solid #f1f5f9", background: isOver ? "#fef2f2" : "#fff" }}>
                          <td style={{ padding: "10px 8px", color: "#64748b", fontWeight: 600 }}>
                            {line.line_idx + 1}
                          </td>
                          <td style={{ padding: "10px 8px" }}>
                            <div style={{ fontWeight: 600, color: "#1e293b" }}>{line.name}</div>
                            {line.color && <div style={{ fontSize: "0.75rem", color: "#64748b" }}>Warna: {line.color}</div>}
                          </td>
                          <td style={{ padding: "10px 8px" }}>
                            <span style={{ fontSize: "0.75rem", padding: "2px 6px", borderRadius: 4, background: line.production_process === "non_print" ? "#fef3c7" : "#e0f2fe", color: line.production_process === "non_print" ? "#92400e" : "#0369a1", fontWeight: 600 }}>
                              {line.production_process === "non_print" ? "Non-Cetak" : "Cetak"}
                            </span>
                          </td>
                          <td style={{ padding: "10px 8px", textAlign: "right", color: "#475569" }}>
                            {line.ordered_qty.toLocaleString("id-ID")} {line.unit}
                          </td>
                          <td style={{ padding: "10px 8px", textAlign: "right", color: "#64748b" }}>
                            {line.shipped_qty.toLocaleString("id-ID")} {line.unit}
                          </td>
                          <td style={{ padding: "10px 8px", textAlign: "right", fontWeight: 600, color: line.remaining_qty > 0 ? "#0284c7" : "#94a3b8" }}>
                            {line.remaining_qty.toLocaleString("id-ID")} {line.unit}
                          </td>
                          <td style={{ padding: "10px 8px" }}>
                            <input
                              type="number"
                              min="0"
                              max={line.remaining_qty}
                              disabled={isZeroRemaining || formBusy}
                              value={qtyVal}
                              onChange={(e) => handleLineQtyChange(line.line_idx, e.target.value)}
                              data-testid={`input-ship-qty-${line.line_idx}`}
                              style={{
                                width: "100%",
                                padding: "6px 8px",
                                border: "1px solid",
                                borderColor: isOver ? "#ef4444" : "#cbd5e1",
                                borderRadius: 6,
                                boxSizing: "border-box",
                                fontWeight: 600,
                                textAlign: "right",
                                background: isZeroRemaining ? "#f1f5f9" : "#fff",
                              }}
                            />
                            {isOver && (
                              <span style={{ display: "block", fontSize: "0.7rem", color: "#ef4444", marginTop: 2 }}>
                                Max: {line.remaining_qty}
                              </span>
                            )}
                          </td>
                          <td style={{ padding: "10px 8px" }}>
                            <input
                              type="text"
                              placeholder="Catatan item..."
                              disabled={isZeroRemaining || formBusy}
                              value={formData.line_notes[line.line_idx] || ""}
                              onChange={(e) => handleLineNoteChange(line.line_idx, e.target.value)}
                              data-testid={`input-ship-note-${line.line_idx}`}
                              style={{ width: "100%", padding: "6px 8px", border: "1px solid #cbd5e1", borderRadius: 6, boxSizing: "border-box", fontSize: "0.85rem" }}
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

          </section>

          </div>
          <div className="sd-form-footer">
            {poSummary && (
              <div style={{ marginRight: "auto", display: "flex", gap: 16, alignItems: "baseline", flexWrap: "wrap" }}>
                <span style={{ fontSize: 13, color: "#475569" }}>
                  Total Sisa PO: <strong>{poSummary.total_remaining.toLocaleString("id-ID")} pcs</strong>
                </span>
                <span style={{ fontSize: 14, color: totalShippingQty > 0 ? "#14532d" : "#475569", fontWeight: 700 }}>
                  Surat Jalan Ini: <span data-testid="total-delivery-qty">{totalShippingQty.toLocaleString("id-ID")}</span> pcs
                </span>
              </div>
            )}
            <button
              type="button"
              className="ghost"
              onClick={() => {
                setViewMode("list");
                setSearchParams({});
              }}
              disabled={formBusy}
              data-testid="btn-cancel-delivery"
              style={{ padding: "10px 20px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer", fontWeight: 600 }}
            >
              Batal
            </button>
            <button
              type="submit"
              className="primary"
              disabled={formBusy || !poSummary || totalShippingQty <= 0 || overshipmentErrors.length > 0}
              data-testid="btn-submit-delivery-order"
              style={{
                padding: "10px 24px",
                borderRadius: 8,
                background: (!poSummary || totalShippingQty <= 0 || overshipmentErrors.length > 0 || formBusy) ? "#94a3b8" : "#0284c7",
                color: "#fff",
                border: "none",
                cursor: (!poSummary || totalShippingQty <= 0 || overshipmentErrors.length > 0 || formBusy) ? "not-allowed" : "pointer",
                fontWeight: 600,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              {formBusy ? "Memproses..." : "Buat & Terbitkan Surat Jalan"}
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
              className="fp-invoice-tabs"
              role="tablist"
              aria-label="Status pengiriman"
              data-testid="delivery-list-tabs"
            >
              {DELIVERY_STATUS_TABS.map((tab) => {
                const selected = statusFilter === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    className={selected ? "is-active" : ""}
                    onClick={() => setStatusFilter(tab.id)}
                    data-testid={`filter-status-${tab.id}`}
                  >
                    {tab.label}
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
                  <th>No. PO</th>
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
                        {d.outlet ? <span className="sd-support">{d.outlet}</span> : null}
                      </td>
                      <td data-no-row-nav onClick={(e) => e.stopPropagation()}>
                        <Link to={`/po/${d.po_id}`} className="sd-doc" data-testid={`po-link-${d.po_number}`}>
                          {d.po_number}
                        </Link>
                      </td>
                      <td>{formatDisplayDate(d.delivery_date) || "-"}</td>
                      <td className="sd-money">{Number(d.total_qty || 0).toLocaleString("id-ID")} pcs</td>
                      <td>
                        <span
                          className={
                            "pj-status-badge sd-delivery-status"
                            + (String(d.status).toUpperCase() === "SELESAI" ? " is-selesai" : " is-sebagian")
                          }
                          data-testid={`delivery-status-${d.id}`}
                        >
                          {String(d.status).toUpperCase() === "SELESAI" ? "SELESAI" : "SEBAGIAN"}
                        </span>
                      </td>
                      <td className="sd-support-text">
                        {d.created_by_name || d.created_by || "-"}
                      </td>
                      <td data-no-row-nav onClick={(e) => e.stopPropagation()}>
                        <div className="sd-actions">
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
                              className="sd-act sd-act-view"
                              onClick={() => handlePrintPdf(d.id)}
                              data-testid={`btn-print-delivery-${d.id}`}
                              title="Cetak PDF Surat Jalan"
                            >
                              <Printer size={14} /> PDF
                            </button>
                          )}
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
          <footer className="sd-list-footer sd-delivery-list-footer" data-testid="delivery-list-footer">
            <span data-testid="delivery-list-range">
              {listLoading
                ? "Memuat daftar pengiriman..."
                : listErr
                ? "Daftar pengiriman belum dapat ditampilkan"
                : loadedRangeLabel(deliveries.length, "pengiriman")}
            </span>
            {!listLoading && !listErr && deliveries.length >= DELIVERY_LIST_API_CAP ? (
              <span className="sd-list-cap-note" data-testid="delivery-list-api-cap-note">
                Daftar dapat dibatasi API (maks. {DELIVERY_LIST_API_CAP.toLocaleString("id-ID")} baris per permintaan). Sempitkan filter atau pencarian bila perlu.
              </span>
            ) : null}
          </footer>
        </section>
      )}

      {/* DETAIL MODAL */}
      {selectedDeliveryId && detailData && (
        <div
          className="modal-overlay"
          data-testid="delivery-detail-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="delivery-detail-title"
        >
          <div className="modal-content sd-modal-card sd-delivery-detail-card">
            <div className="sd-delivery-detail-header">
              <div className="sd-delivery-detail-heading">
                <div className="sd-delivery-detail-icon" aria-hidden="true">
                  <Truck size={20} />
                </div>
                <div>
                  <h2 id="delivery-detail-title">
                    Detail Surat Jalan: <span>{detailData.delivery?.sj_number}</span>
                  </h2>
                  <span className="sd-delivery-detail-sub">
                    Pengiriman #{detailData.delivery?.shipment_index} dari PO <strong>{detailData.delivery?.po_number}</strong>
                  </span>
                </div>
              </div>
              <button
                type="button"
                className="sd-delivery-detail-close"
                onClick={() => {
                  setSelectedDeliveryId(null);
                  setDetailData(null);
                }}
                data-testid="btn-close-delivery-modal"
                aria-label="Tutup detail pengiriman"
              >
                <X size={20} />
              </button>
            </div>
            <div className="sd-modal-body">

            {/* PO Progress Summary Box */}
            {detailData.po_summary && (
              <div
                style={{
                  background: "#f0f9ff",
                  border: "1px solid #bae6fd",
                  borderRadius: 8,
                  padding: "14px 16px",
                  marginBottom: 16,
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                  gap: 12,
                }}
                data-testid="delivery-po-progress-summary"
              >
                <div>
                  <span style={{ color: "#0369a1", fontSize: "0.75rem", fontWeight: 600, display: "block", textTransform: "uppercase" }}>
                    TOTAL QTY PO
                  </span>
                  <strong style={{ fontSize: "1.1rem", color: "#0c4a6e" }} data-testid="detail-total-po-qty">
                    {Number(detailData.po_summary.total_ordered || 0).toLocaleString("id-ID")} pcs
                  </strong>
                </div>
                <div>
                  <span style={{ color: "#0369a1", fontSize: "0.75rem", fontWeight: 600, display: "block", textTransform: "uppercase" }}>
                    SUDAH DIBUAT SURAT JALAN
                  </span>
                  <strong style={{ fontSize: "1.1rem", color: "#0284c7" }} data-testid="detail-cumulative-sj-qty">
                    {Number(detailData.po_summary.total_shipped || 0).toLocaleString("id-ID")} pcs
                  </strong>
                </div>
                <div>
                  <span style={{ color: "#0369a1", fontSize: "0.75rem", fontWeight: 600, display: "block", textTransform: "uppercase" }}>
                    SISA
                  </span>
                  <strong style={{ fontSize: "1.1rem", color: Number(detailData.po_summary.total_remaining || 0) === 0 ? "#16a34a" : "#ea580c" }} data-testid="detail-remaining-po-qty">
                    {Number(detailData.po_summary.total_remaining || 0).toLocaleString("id-ID")} pcs
                  </strong>
                </div>
                <div>
                  <span style={{ color: "#0369a1", fontSize: "0.75rem", fontWeight: 600, display: "block", textTransform: "uppercase" }}>
                    STATUS
                  </span>
                  <span
                    data-testid="detail-delivery-status"
                    style={{
                      display: "inline-block",
                      marginTop: 2,
                      padding: "3px 10px",
                      background: (detailData.po_summary.total_remaining === 0 || detailData.po_summary.delivery_status === "SELESAI") ? "#dcfce7" : "#e0f2fe",
                      color: (detailData.po_summary.total_remaining === 0 || detailData.po_summary.delivery_status === "SELESAI") ? "#166534" : "#075985",
                      borderRadius: 12,
                      fontSize: "0.8rem",
                      fontWeight: 700,
                    }}
                  >
                    {(detailData.po_summary.total_remaining === 0 || detailData.po_summary.delivery_status === "SELESAI") ? "SELESAI" : "SEBAGIAN"}
                  </span>
                </div>
              </div>
            )}

            {/* Delivery Meta Grid */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12, background: "#f8fafc", padding: 16, borderRadius: 8, marginBottom: 16, fontSize: "0.85rem" }}>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Nomor PO:</span>
                <Link to={`/po/${detailData.delivery?.po_id}`} style={{ color: "#0284c7", fontWeight: 600, textDecoration: "none" }}>
                  {detailData.delivery?.po_number}
                </Link>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Customer:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.customer || "-"}</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Outlet:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.outlet || "-"}</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Tanggal Kirim:</span>
                <strong style={{ color: "#1e293b" }}>{formatDisplayDate(detailData.delivery?.delivery_date) || "-"}</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Penerima (PIC):</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.recipient || "-"}</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Sales:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.sales || "-"}</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Ekspedisi / Zona:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.expedition || "-"} ({detailData.delivery?.delivery_zone || "-"})</strong>
              </div>
              <div>
                <span style={{ color: "#64748b", display: "block" }}>Dibuat Oleh:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.created_by_name || detailData.delivery?.created_by || "-"}</strong>
              </div>
              <div style={{ gridColumn: "1 / -1" }}>
                <span style={{ color: "#64748b", display: "block" }}>Alamat Lengkap:</span>
                <strong style={{ color: "#1e293b" }}>{detailData.delivery?.address || "-"}</strong>
              </div>
              {detailData.delivery?.notes && (
                <div style={{ gridColumn: "1 / -1" }}>
                  <span style={{ color: "#64748b", display: "block" }}>Catatan:</span>
                  <strong style={{ color: "#1e293b" }}>{detailData.delivery?.notes}</strong>
                </div>
              )}
            </div>

            {/* Line Items on this SJ */}
            <div style={{ marginBottom: 20 }}>
              <h4 style={{ margin: "0 0 8px 0", fontSize: "0.95rem", color: "#1e293b" }}>Daftar Barang Dikirim pada Surat Jalan Ini:</h4>
              <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.85rem" }}>
                <thead>
                  <tr style={{ background: "#f1f5f9", borderBottom: "1px solid #cbd5e1", color: "#475569" }}>
                    <th style={{ padding: "8px" }}>#</th>
                    <th style={{ padding: "8px" }}>Barang / Jasa</th>
                    <th style={{ padding: "8px" }}>Proses</th>
                    <th style={{ padding: "8px", textAlign: "right" }}>Qty Kirim</th>
                    <th style={{ padding: "8px" }}>Satuan</th>
                    <th style={{ padding: "8px" }}>Catatan</th>
                  </tr>
                </thead>
                <tbody>
                  {(detailData.delivery?.line_items || []).map((li, idx) => (
                    <tr key={idx} style={{ borderBottom: "1px solid #f1f5f9" }}>
                      <td style={{ padding: "8px", color: "#64748b" }}>{idx + 1}</td>
                      <td style={{ padding: "8px", fontWeight: 600, color: "#1e293b" }}>
                        {li.name}
                        {li.color && <div style={{ fontSize: "0.75rem", color: "#64748b", fontWeight: 400 }}>{li.color}</div>}
                      </td>
                      <td style={{ padding: "8px" }}>
                        <span style={{ fontSize: "0.75rem", padding: "2px 6px", borderRadius: 4, background: li.production_process === "non_print" ? "#fef3c7" : "#e0f2fe", color: li.production_process === "non_print" ? "#92400e" : "#0369a1" }}>
                          {li.production_process === "non_print" ? "Non-Cetak" : "Cetak"}
                        </span>
                      </td>
                      <td style={{ padding: "8px", textAlign: "right", fontWeight: 700, color: "#0f172a" }}>
                        {Number(li.qty || li.quantity || 0).toLocaleString("id-ID")}
                      </td>
                      <td style={{ padding: "8px", color: "#475569" }}>{li.unit || "pcs"}</td>
                      <td style={{ padding: "8px", color: "#64748b" }}>{li.notes || "-"}</td>
                    </tr>
                  ))}
                  <tr style={{ background: "#f8fafc", fontWeight: 700, borderTop: "2px solid #cbd5e1" }}>
                    <td colSpan={3} style={{ padding: "8px", textAlign: "right" }}>Total Kuantitas:</td>
                    <td style={{ padding: "8px", textAlign: "right", color: "#0284c7" }}>
                      {Number(detailData.delivery?.total_qty || 0).toLocaleString("id-ID")}
                    </td>
                    <td colSpan={2} style={{ padding: "8px" }}>pcs</td>
                  </tr>
                </tbody>
              </table>
            </div>

            {/* Shipment History for this PO */}
            {detailData.all_po_deliveries?.length > 1 && (
              <div style={{ marginBottom: 20, padding: 14, background: "#f0fdf4", border: "1px solid #bbf7d0", borderRadius: 8 }}>
                <h4 style={{ margin: "0 0 8px 0", fontSize: "0.9rem", color: "#166534", display: "flex", alignItems: "center", gap: 6 }}>
                  <Clock size={16} /> Riwayat Pengiriman untuk PO {detailData.delivery?.po_number}:
                </h4>
                <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: "0.85rem" }}>
                  {detailData.all_po_deliveries.map((shipment) => (
                    <div
                      key={shipment.id}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        padding: "6px 10px",
                        background: shipment.id === detailData.delivery?.id ? "#dcfce7" : "#fff",
                        border: "1px solid",
                        borderColor: shipment.id === detailData.delivery?.id ? "#86efac" : "#e2e8f0",
                        borderRadius: 6,
                        fontWeight: shipment.id === detailData.delivery?.id ? 600 : 400,
                      }}
                    >
                      <span>
                        Surat Jalan <strong>{shipment.sj_number}</strong> (Kirim #{shipment.shipment_index}) - {formatDisplayDate(shipment.delivery_date)}
                      </span>
                      <span style={{ fontWeight: 700, color: "#15803d" }}>
                        {Number(shipment.total_qty || 0).toLocaleString("id-ID")} pcs
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            </div>

            <div className="sd-modal-footer" data-testid="delivery-detail-footer">
              {canPrint && (
                <button
                  type="button"
                  className="sd-btn-primary"
                  onClick={() => handlePrintPdf(detailData.delivery?.id)}
                  data-testid="btn-print-modal-pdf"
                >
                  <Printer size={16} /> Cetak PDF Surat Jalan
                </button>
              )}
              <button
                type="button"
                className="sd-list-reset"
                onClick={() => {
                  setSelectedDeliveryId(null);
                  setDetailData(null);
                }}
                data-testid="btn-close-modal-bottom"
              >
                Tutup
              </button>
            </div>
          </div>
        </div>
      )}

      {deleteSuccess && (
        <div data-testid="batal-success" style={{ position: "fixed", right: 16, bottom: 16, zIndex: 90, background: "#ecfdf5", color: "#065f46", border: "1px solid #6ee7b7", padding: "10px 14px", borderRadius: 10, maxWidth: 420, boxShadow: "0 8px 24px rgba(15,23,42,0.12)" }}>
          {deleteSuccess}
          <button type="button" onClick={() => setDeleteSuccess("")} style={{ marginLeft: 10, border: "none", background: "transparent", color: "#065f46", fontWeight: 700, cursor: "pointer" }}>Tutup</button>
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
