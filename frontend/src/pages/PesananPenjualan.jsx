import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  FileText,
  Filter,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import api, { publicApiUrl } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";
import CustomerSearchSelect from "../components/CustomerSearchSelect";
import {
  EMPTY_SALES_ORDER_COLUMN_FILTERS,
  SALES_ORDER_STATUS_FILTERS,
  applySalesOrderColumnFilters,
  salesOrderColumnFilterActive,
  salesOrderColumnFiltersAreActive,
  salesOrderDeliveryStatus,
  salesOrderRemainingQty,
  salesOrderSalesName,
  salesOrderStageLabel,
  uniqueDeliveryStatuses,
} from "../lib/pesananPenjualanColumnFilters";
import "./pesananPenjualan.css";
import {
  REPEAT_NO_PREVIOUS_MESSAGE,
  REPEAT_ORDER_QUERY_PARAM,
  clearRepeatDerivedFormFields,
  mapRepeatSourceToFormPatch,
  resolveRepeatMasterCustomer,
} from "../lib/salesOrderRepeat";
import {
  getPreviewMasterCustomers,
  getPreviewRepeatSource,
  isSalesOrderRepeatPreviewActive,
} from "../lib/salesOrderRepeatPreviewFixtures";
import {
  capturePoOpenFromList,
  poNavStateFromList,
} from "../lib/poListReturn";
import { useDebouncedLiveSearch } from "../hooks/useDebouncedLiveSearch";
import {
  SALES_ORDER_DRAWER_INPUTS,
  lineAfterEntryModeChange,
  lineEntryModeAfterRepeat,
  salesOrderIssuePlacement,
} from "../lib/salesOrderCreateWorkspace";
import {
  SALES_ORDER_PAGE_SIZES,
  canShowAktifSummary,
  formatQuantityByUnit,
  listPageCount,
  listProgressText,
  listRangeLabel,
  orderQuantityLabel,
  periodBounds,
  quantityByUnitFromLines,
  salesOrderListRequest,
} from "../lib/salesOrderListPresentation";

let salesOrderLineKeySeq = 0;

function nextSalesOrderLineKey() {
  salesOrderLineKeySeq += 1;
  return `so-line-${salesOrderLineKeySeq}`;
}

export function createSalesOrderLineItem(overrides = {}) {
  return { ...INITIAL_LINE_ITEM, ...overrides, rowKey: nextSalesOrderLineKey() };
}

/** Same messages and branches as the previous inline submit validation. */
export function validateSalesOrderCreate(formData) {
  if (!String(formData?.customer || "").trim()) {
    return "Customer wajib diisi.";
  }
  if (!formData?.customer_id) {
    if (!String(formData?.customer_phone || "").trim()) {
      return "Pelanggan baru terdeteksi: No. HP / WhatsApp pelanggan wajib diisi.";
    }
    if (!String(formData?.address || "").trim()) {
      return "Pelanggan baru terdeteksi: Alamat lengkap pengiriman wajib diisi.";
    }
  }
  if (!formData?.line_items?.length) {
    return "Minimal harus ada 1 line item.";
  }
  for (let i = 0; i < formData.line_items.length; i += 1) {
    const li = formData.line_items[i];
    if (!String(li?.name || "").trim()) {
      return `Baris #${i + 1}: Nama barang/jasa wajib diisi.`;
    }
    const q = Number(li.quantity);
    if (!Number.isFinite(q) || q <= 0) {
      return `Baris #${i + 1}: Quantity harus angka lebih besar dari 0.`;
    }
  }
  return "";
}

export function salesOrderRequiredChecks(formData) {
  const lines = formData?.line_items || [];
  const linesOk = lines.length > 0 && lines.every((li) => {
    const name = String(li?.name || "").trim();
    const q = Number(li?.quantity);
    return Boolean(name) && Number.isFinite(q) && q > 0;
  });
  const checks = [
    { id: "customer", filled: Boolean(String(formData?.customer || "").trim()) },
    { id: "line_items", filled: linesOk },
  ];
  if (!formData?.customer_id) {
    checks.push(
      { id: "customer_phone", filled: Boolean(String(formData?.customer_phone || "").trim()) },
      { id: "address", filled: Boolean(String(formData?.address || "").trim()) },
    );
  }
  return checks;
}

export function salesOrderProgress(formData) {
  const checks = salesOrderRequiredChecks(formData);
  const completed = checks.filter((item) => item.filled).length;
  const total = checks.length;
  const complete = validateSalesOrderCreate(formData) === "";
  return {
    completed,
    total,
    complete,
    label: `${completed}/${total} wajib`,
    statusLabel: complete ? "Lengkap" : "Belum Lengkap",
  };
}

export function buildSalesOrderCreatePayload(formData) {
  const payload = {
    ...formData,
    po_number: String(formData.po_number || "").trim() || undefined,
    customer: String(formData.customer || "").trim(),
    line_items: (formData.line_items || []).map((li) => {
      const next = {
        ...li,
        name: String(li.name || "").trim(),
        quantity: Number(li.quantity),
        unit: (li.unit || "pcs").trim(),
        production_process: li.production_process === "non_print" ? "non_print" : "print",
      };
      delete next.entry_mode;
      delete next.rowKey;
      return next;
    }),
  };
  delete payload.outlet_id;
  return payload;
}

function getTodayString() {
  const d = new Date();
  return d.toISOString().split("T")[0];
}

function getThreeDaysLaterString() {
  const d = new Date();
  d.setDate(d.getDate() + 3);
  return d.toISOString().split("T")[0];
}

const INITIAL_LINE_ITEM = {
  item_code: "",
  name: "",
  quantity: 1,
  unit: "pcs",
  color: "",
  production_process: "print",
  notes: "",
  accurate_item_id: null,
  master_item_id: null,
};

function SalesOrderThumb({ src }) {
  const [broken, setBroken] = useState(false);
  if (!src || broken) {
    return <span className="so-thumb so-thumb-empty" aria-hidden="true" />;
  }
  return (
    <img
      className="so-thumb"
      src={src}
      alt=""
      loading="lazy"
      onError={() => setBroken(true)}
    />
  );
}

function confirmDiscardSalesOrder() {
  if (typeof window === "undefined" || typeof window.confirm !== "function") return true;
  try {
    return window.confirm("Ada perubahan yang belum disimpan. Tinggalkan formulir ini?");
  } catch {
    return true;
  }
}

function SalesOrderHeaderFilter({ label, testid, active, alignEnd, onClear, children }) {
  return (
    <div className={"so-th-inner" + (alignEnd ? " is-end" : "")}>
      <span className="so-th-label">{label}</span>
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            className={"so-th-filter" + (active ? " is-active" : "")}
            aria-label={`Filter ${label}`}
            data-testid={testid}
            onClick={(e) => e.stopPropagation()}
          >
            <Filter size={11} />
            {active && <span className="so-th-dot" />}
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          className="so-th-popover sales-order-page-popover"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="so-th-pop-label">{label}</div>
          {children}
          {active && (
            <button
              type="button"
              className="so-th-clear"
              onClick={onClear}
              data-testid={`${testid}-clear`}
            >
              Hapus filter
            </button>
          )}
        </PopoverContent>
      </Popover>
    </div>
  );
}

export function filterPoLineItems(lineItems, searchQuery) {
  const q = (searchQuery || "").trim().toLowerCase();
  const list = (lineItems || []).map((line, originalIndex) => ({
    line,
    originalIndex,
  }));
  if (!q) return list;
  return list.filter(({ line }) => {
    const name = String(line?.name || "").toLowerCase();
    const code = String(line?.item_code || "").toLowerCase();
    return name.includes(q) || code.includes(q);
  });
}

function SoField({ field, label, required = false, error = false, className = "", children }) {
  return (
    <div
      className={`so-create-field${className ? ` ${className}` : ""}${error ? " is-error" : ""}`}
      data-so-field={field}
    >
      <label htmlFor={`so-${field}`}>
        {label}
        {required ? <span className="so-req"> *</span> : null}
      </label>
      {children}
    </div>
  );
}

export default function PesananPenjualan() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, hasPerm } = useAuth();

  const canCreate = hasPerm("sales_order.create") || hasPerm("po.edit") || user?.role === "admin";
  const canDelete = hasPerm("po.delete") || user?.role === "admin";
  const [viewMode, setViewMode] = useState(() =>
    searchParams.get(REPEAT_ORDER_QUERY_PARAM) ? "create" : "list"
  ); // 'list' | 'create'
  
  // List state
  const [orders, setOrders] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listErr, setListErr] = useState("");
  const {
    input: searchInput,
    setInput: setSearchInput,
    q: searchQuery,
    flush: flushSearch,
    clear: clearSearch,
  } = useDebouncedLiveSearch(searchParams.get("q") || "");
  const [listTab, setListTab] = useState(() => {
    const tab = searchParams.get("tab");
    const status = searchParams.get("status");
    if (tab === "histori" || status === "histori" || status === "selesai" || status === "cancelled") {
      return "histori";
    }
    return "aktif";
  });
  const [statusFilter, setStatusFilter] = useState(() => {
    const status = searchParams.get("status");
    if (status) return status;
    return searchParams.get("tab") === "histori" ? "histori" : "aktif";
  });
  const [period, setPeriod] = useState(() => {
    const value = searchParams.get("period") || "all";
    return value === "this_month" || value === "last_month" ? value : "all";
  });
  const [page, setPage] = useState(() => {
    const value = Number(searchParams.get("page"));
    return Number.isFinite(value) && value >= 1 ? Math.floor(value) : 1;
  });
  const [pageSize, setPageSize] = useState(() => {
    const value = Number(searchParams.get("page_size"));
    return SALES_ORDER_PAGE_SIZES.includes(value) ? value : 10;
  });
  const [listMeta, setListMeta] = useState({ total: null, capped: false });
  const [itemImages, setItemImages] = useState({});
  const [columnFilters, setColumnFilters] = useState(EMPTY_SALES_ORDER_COLUMN_FILTERS);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");

  // Options & Auto-fill masters
  const [options, setOptions] = useState({
    customers: [],
    outlets_by_customer: {},
    sales_options: [],
    master_items: [],
    suggested_po_number: "",
  });
  const [optionsLoading, setOptionsLoading] = useState(false);

  // Form state
  const [formData, setFormData] = useState(() => ({
    po_number: "",
    order_date: getTodayString(),
    customer: "",
    customer_id: null,
    customer_phone: "",
    customer_address: "",
    customer_pic: "",
    customer_email: "",
    customer_city: "",
    outlet: "",
    recipient: "",
    address: "",
    delivery_zone: "",
    payment_terms: "",
    sales: "",
    created_by_user: user?.name || user?.email || "",
    expedition: "Internal fleet",
    estimated_delivery: getThreeDaysLaterString(),
    deadline: getThreeDaysLaterString(),
    notes: "",
    line_items: [createSalesOrderLineItem()],
  }));

  const [formBusy, setFormBusy] = useState(false);
  const [formErr, setFormErr] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const [lineModes, setLineModes] = useState({});
  const submitLockRef = useRef(false);
  const dirtyRef = useRef(false);
  const focusLineKeyRef = useRef("");
  const skipPageResetRef = useRef(true);

  // Repeat Order (create-form prefill only)
  const [repeatSourceIndicator, setRepeatSourceIndicator] = useState("");
  const [repeatBusy, setRepeatBusy] = useState(false);
  const [repeatMsg, setRepeatMsg] = useState("");
  const [repeatConfirm, setRepeatConfirm] = useState(null);
  const repeatAbortRef = useRef(null);
  const repeatRequestCustomerIdRef = useRef(null);
  const consumedRepeatOrderIdRef = useRef(null);
  const selectedCustomerIdRef = useRef(null);

  useEffect(() => {
    selectedCustomerIdRef.current = formData.customer_id || null;
  }, [formData.customer_id]);

  useEffect(() => {
    const issue = salesOrderIssuePlacement(formErr);
    if (!issue?.field) return undefined;
    const field = document.querySelector(
      `[data-so-field="${issue.field}"] input, [data-so-field="${issue.field}"] textarea, [data-so-field="${issue.field}"] select`
    );
    field?.focus();
    return undefined;
  }, [formErr]);

  useEffect(() => {
    if (viewMode !== "create") return undefined;
    const onBeforeUnload = (event) => {
      if (!dirtyRef.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [viewMode]);

  const clearRepeatState = useCallback(() => {
    setRepeatSourceIndicator("");
    setRepeatMsg("");
    setRepeatConfirm(null);
    repeatAbortRef.current?.abort();
    repeatRequestCustomerIdRef.current = null;
  }, []);

  const loadOrdersAbortRef = useRef(null);

  const loadOptions = useCallback(async () => {
    setOptionsLoading(true);
    try {
      const data = await api.salesOrderOptions();
      setOptions(data || {});
      if (data?.suggested_po_number) {
        setFormData((prev) => ({
          ...prev,
          po_number: prev.po_number || data.suggested_po_number,
        }));
      }
    } catch {
      // Best effort load options
    } finally {
      setOptionsLoading(false);
    }
  }, []);

  const loadOrders = useCallback(async () => {
    loadOrdersAbortRef.current?.abort();
    const ac = new AbortController();
    loadOrdersAbortRef.current = ac;
    setListLoading(true);
    setListErr("");
    try {
      const bounds = periodBounds(period);
      const params = salesOrderListRequest({
        q: searchQuery,
        status: statusFilter,
        dateFrom: bounds.date_from,
        dateTo: bounds.date_to,
        page,
        pageSize,
      });
      const res = await api.listSalesOrders(params, { signal: ac.signal });
      if (ac.signal.aborted) return;
      setOrders(res.orders || []);
      setListMeta({
        total: res.total == null ? null : Number(res.total),
        capped: Boolean(res.capped),
      });
      setSelectedIds(new Set());
    } catch (e) {
      if (e?.code === "ERR_CANCELED" || e?.name === "CanceledError" || ac.signal.aborted) {
        return;
      }
      const det = e.response?.data?.detail;
      setListErr(typeof det === "object" ? det.message : (det || "Gagal memuat daftar Pesanan Penjualan"));
    } finally {
      if (!ac.signal.aborted) {
        setListLoading(false);
      }
    }
  }, [searchQuery, statusFilter, period, page, pageSize]);

  const filteredOrders = useMemo(
    () => applySalesOrderColumnFilters(orders, columnFilters),
    [orders, columnFilters]
  );
  const serverPaged = listMeta.total != null && Number.isFinite(Number(listMeta.total));
  const displayedOrders = useMemo(() => {
    if (serverPaged) return filteredOrders;
    const start = (page - 1) * pageSize;
    return filteredOrders.slice(start, start + pageSize);
  }, [filteredOrders, serverPaged, page, pageSize]);
  const listTotal = serverPaged ? Number(listMeta.total) : filteredOrders.length;
  const pageCount = listPageCount(listTotal, pageSize);
  const tabDefaultStatus = listTab === "histori" ? "histori" : "aktif";
  const columnFiltersActive = salesOrderColumnFiltersAreActive(
    columnFilters,
    statusFilter === tabDefaultStatus ? "all" : statusFilter,
  );
  const deliveryStatusOptions = useMemo(() => uniqueDeliveryStatuses(orders), [orders]);

  const patchColumnFilter = (patch) => {
    setColumnFilters((prev) => ({ ...prev, ...patch }));
  };
  const clearColumnKey = (key) => {
    if (key === "po") patchColumnFilter({ po: "" });
    else if (key === "date") patchColumnFilter({ dateFrom: "", dateTo: "" });
    else if (key === "customer") patchColumnFilter({ customer: "" });
    else if (key === "outlet") patchColumnFilter({ outlet: "" });
    else if (key === "qty") patchColumnFilter({ qtyMin: "", qtyMax: "" });
    else if (key === "remain") patchColumnFilter({ remainMin: "", remainMax: "" });
    else if (key === "delivery") patchColumnFilter({ deliveryStatuses: [] });
    else if (key === "sales") patchColumnFilter({ sales: "" });
    else if (key === "stage") {
      setStatusFilter("all");
      patchColumnFilter({ stageText: "" });
    }
  };
  const clearAllColumnFilters = () => {
    setColumnFilters(EMPTY_SALES_ORDER_COLUMN_FILTERS);
    setStatusFilter("all");
  };

  useEffect(() => {
    setSelectedIds((prev) => {
      if (prev.size === 0) return prev;
      const visible = new Set(displayedOrders.map((o) => o.id));
      const next = new Set([...prev].filter((id) => visible.has(id)));
      if (next.size === prev.size) return prev;
      return next;
    });
  }, [displayedOrders]);

  // Sync filter/search into URL so browser Back restores list presentation state.
  useEffect(() => {
    if (viewMode !== "list") return;
    if (searchParams.get(REPEAT_ORDER_QUERY_PARAM)) return;
    const next = new URLSearchParams();
    if (searchQuery.trim()) next.set("q", searchQuery.trim());
    const tabDefault = listTab === "histori" ? "histori" : "aktif";
    if (statusFilter && statusFilter !== tabDefault) next.set("status", statusFilter);
    if (listTab === "histori") next.set("tab", "histori");
    if (period !== "all") next.set("period", period);
    if (page > 1) next.set("page", String(page));
    if (pageSize !== 10) next.set("page_size", String(pageSize));
    const nextStr = next.toString();
    const curStr = searchParams.toString();
    if (nextStr === curStr) return;
    setSearchParams(next, { replace: true });
  }, [searchQuery, statusFilter, listTab, period, page, pageSize, viewMode, searchParams, setSearchParams]);

  useEffect(() => {
    if (skipPageResetRef.current) {
      skipPageResetRef.current = false;
      return;
    }
    setPage(1);
  }, [searchQuery, statusFilter, period, pageSize, listTab]);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const poListNavState = (extra = {}) => poNavStateFromList({
    pathname: location.pathname,
    search: location.search,
    extra,
  });

  const onOpenPo = (orderId) => {
    capturePoOpenFromList({
      user,
      orderId,
      pathname: location.pathname,
      search: location.search,
      scrollY: typeof window !== "undefined" ? window.scrollY : undefined,
    });
  };
  useEffect(() => {
    if (viewMode === "create") {
      loadOptions();
    }
  }, [viewMode, loadOptions]);

  const imageIds = useMemo(() => {
    const ids = [];
    const seen = new Set();
    (formData.line_items || []).forEach((line) => {
      const id = String(line?.accurate_item_id || "").trim();
      if (!id || seen.has(id)) return;
      seen.add(id);
      ids.push(id);
    });
    return ids;
  }, [formData.line_items]);

  useEffect(() => {
    if (viewMode !== "create" || imageIds.length === 0) {
      setItemImages({});
      return undefined;
    }
    const ac = new AbortController();
    Promise.resolve()
      .then(() => api.salesOrderItemImages(
        { accurate_item_ids: imageIds.join(",") },
        { signal: ac.signal },
      ))
      .then((res) => {
        if (!ac.signal.aborted) setItemImages(res?.images || {});
      })
      .catch((error) => {
        if (error?.code === "ERR_CANCELED" || error?.name === "CanceledError" || ac.signal.aborted) return;
        if (!ac.signal.aborted) setItemImages({});
      });
    return () => ac.abort();
  }, [viewMode, imageIds]);

  useEffect(() => {
    const key = focusLineKeyRef.current;
    if (!key || viewMode !== "create") return;
    const index = formData.line_items.findIndex((line) => line.rowKey === key);
    if (index < 0) return;
    const node = document.querySelector(`[data-testid="line-item-search-${index}-input"]`);
    if (!node) return;
    node.focus();
    focusLineKeyRef.current = "";
  }, [formData.line_items, viewMode]);

  useEffect(() => {
    if (viewMode === "list") {
      loadOrders();
    }
    return () => {
      loadOrdersAbortRef.current?.abort();
    };
  }, [viewMode, loadOrders]);

  // When customer changes, handle autofill suggestions for outlet/address/sales/payment_terms
  const applyMasterCustomerBaseline = (masterMatch, customerName) => {
    const outlets = options.outlets_by_customer?.[customerName] || [];
    let patch = {
      customer: customerName,
      customer_id: masterMatch.id,
      customer_phone: masterMatch.phone || "",
      customer_address: masterMatch.address || "",
      customer_pic: masterMatch.pic || "",
      customer_email: masterMatch.email || "",
      customer_city: masterMatch.city || "",
    };
    if (masterMatch.address) patch.address = masterMatch.address;
    if (masterMatch.pic) patch.recipient = masterMatch.pic;
    if (outlets.length === 1) {
      const o = outlets[0];
      patch = {
        ...patch,
        outlet: o.outlet || "",
        outlet_id: o.id || "",
        address: o.address || patch.address || "",
        recipient: o.recipient || patch.recipient || "",
        delivery_zone: o.delivery_zone || "",
        sales: o.sales || "",
        payment_terms: o.payment_terms || "",
      };
    } else {
      patch.outlet = "";
      patch.outlet_id = "";
    }
    return patch;
  };

  // Free typing never silently binds customer_id.

  const previewRepeatOrderActive = useMemo(
    () => isSalesOrderRepeatPreviewActive({ searchParams, nodeEnv: process.env.NODE_ENV }),
    [searchParams]
  );

  const masterCustomersForSearch = useMemo(() => {
    if (previewRepeatOrderActive) return getPreviewMasterCustomers();
    return options.master_customers || [];
  }, [previewRepeatOrderActive, options.master_customers]);

  const handleCustomerTextChange = (newCustomer) => {
    const hadRepeat = Boolean(repeatSourceIndicator || repeatConfirm);
    clearRepeatState();
    const patch = {
      customer: newCustomer,
      customer_id: null,
      outlet: "",
      outlet_id: "",
    };
    setFormData((prev) => {
      const base = hadRepeat
        ? clearRepeatDerivedFormFields(prev, { keepCustomer: true })
        : prev;
      return {
        ...base,
        ...patch,
        po_number: prev.po_number,
        order_date: prev.order_date,
        created_by_user: prev.created_by_user,
        estimated_delivery: prev.estimated_delivery,
        deadline: prev.deadline,
        expedition: hadRepeat ? "Internal fleet" : prev.expedition,
      };
    });
  };

  const handleCustomerSelect = (masterMatch) => {
    if (!masterMatch?.id) return;
    const hadRepeat = Boolean(repeatSourceIndicator || repeatConfirm);
    clearRepeatState();
    const customerName = String(masterMatch.name || "").trim();
    const patch = applyMasterCustomerBaseline(masterMatch, customerName);
    setFormData((prev) => {
      const base = hadRepeat
        ? clearRepeatDerivedFormFields(prev, { keepCustomer: true })
        : prev;
      return {
        ...base,
        ...patch,
        po_number: prev.po_number,
        order_date: prev.order_date,
        created_by_user: prev.created_by_user,
        estimated_delivery: prev.estimated_delivery,
        deadline: prev.deadline,
        expedition: patch.expedition || (hadRepeat ? "Internal fleet" : prev.expedition) || "Internal fleet",
      };
    });
  };

  const handleCustomerClear = () => {
    const hadRepeat = Boolean(repeatSourceIndicator || repeatConfirm);
    clearRepeatState();
    setFormData((prev) => {
      const base = hadRepeat
        ? clearRepeatDerivedFormFields(prev, { keepCustomer: false })
        : prev;
      return {
        ...base,
        customer: "",
        customer_id: null,
        customer_phone: "",
        customer_address: "",
        customer_pic: "",
        customer_email: "",
        customer_city: "",
        outlet: "",
        outlet_id: "",
        po_number: prev.po_number,
        order_date: prev.order_date,
        created_by_user: prev.created_by_user,
        estimated_delivery: prev.estimated_delivery,
        deadline: prev.deadline,
        expedition: hadRepeat ? "Internal fleet" : prev.expedition,
      };
    });
  };

  // When outlet changes, autofill matching address/recipient if available
  const handleOutletChange = (newOutlet) => {
    const outlets = options.outlets_by_customer?.[formData.customer] || [];
    const match = outlets.find((o) => o.outlet === newOutlet);
    if (match) {
      setFormData((prev) => ({
        ...prev,
        outlet: newOutlet,
        outlet_id: match.id || "",
        address: match.address || prev.address,
        recipient: match.recipient || prev.recipient,
        delivery_zone: match.delivery_zone || prev.delivery_zone,
        sales: match.sales || prev.sales,
        payment_terms: match.payment_terms || prev.payment_terms,
      }));
    } else {
      setFormData((prev) => ({ ...prev, outlet: newOutlet, outlet_id: "" }));
    }
  };

  // Line item handlers
  const handleLineChange = (index, field, value) => {
    setFormData((prev) => {
      const lines = [...prev.line_items];
      lines[index] = { ...lines[index], [field]: value };
      return { ...prev, line_items: lines };
    });
  };

  const handleSelectMasterItem = (index, master) => {
    setFormData((prev) => {
      const lines = [...prev.line_items];
      if (!master) {
        lines[index] = {
          ...lines[index],
          name: "",
          item_code: "",
          accurate_item_id: null,
          master_item_id: null,
        };
      } else {
        lines[index] = {
          ...lines[index],
          name: master.item_name || lines[index].name,
          item_code: master.item_code || lines[index].item_code,
          unit: master.unit || lines[index].unit || "pcs",
          accurate_item_id: master.accurate_item_id || null,
          master_item_id: master.id || master.master_item_id || null,
        };
      }
      return { ...prev, line_items: lines };
    });
  };

  const addLineItem = () => {
    const line = createSalesOrderLineItem();
    focusLineKeyRef.current = line.rowKey;
    dirtyRef.current = true;
    setFormData((prev) => ({
      ...prev,
      line_items: [...prev.line_items, line],
    }));
    setLineModes((prev) => ({ ...prev, [line.rowKey]: "master" }));
  };

  const removeLineItem = (index) => {
    if (formData.line_items.length <= 1) return;
    dirtyRef.current = true;
    const rowKey = formData.line_items[index]?.rowKey;
    setFormData((prev) => ({
      ...prev,
      line_items: prev.line_items.filter((_, i) => i !== index),
    }));
    if (rowKey) {
      setLineModes((prev) => {
        const next = { ...prev };
        delete next[rowKey];
        return next;
      });
    }
  };

  const changeLineMode = (index, nextMode) => {
    const rowKey = formData.line_items[index]?.rowKey;
    if (!rowKey) return;
    const current = lineModes[rowKey] || "master";
    if (current === nextMode) return;
    setLineModes((prev) => ({ ...prev, [rowKey]: nextMode }));
    setFormData((prev) => {
      const lines = [...prev.line_items];
      lines[index] = lineAfterEntryModeChange(lines[index], nextMode);
      return { ...prev, line_items: lines };
    });
  };

  const generatePoNumber = async () => {
    try {
      const data = await api.salesOrderOptions();
      if (data?.suggested_po_number) {
        setFormData((prev) => ({ ...prev, po_number: data.suggested_po_number }));
      }
    } catch {
      // Ignore
    }
  };

  const quantityParts = useMemo(
    () => quantityByUnitFromLines(formData.line_items),
    [formData.line_items],
  );
  const quantityLabel = formatQuantityByUnit(quantityParts);
  const formProgress = useMemo(() => salesOrderProgress(formData), [formData]);
  const formIssue = useMemo(() => salesOrderIssuePlacement(formErr), [formErr]);

  const toggleSelect = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    if (selectedIds.size === displayedOrders.length && displayedOrders.length > 0) setSelectedIds(new Set());
    else setSelectedIds(new Set(displayedOrders.map((o) => o.id)));
  };
  const formatDeleteError = (det) => {
    if (typeof det === "object" && det) {
      const blockers = det.blockers || [];
      if (blockers.length) {
        return `${det.message || "Tidak dapat menghapus."} ${blockers.map((b) => `${b.po_number || b.number || b.id}: ${b.reason}`).join(" ")}`.trim();
      }
      return det.message || JSON.stringify(det);
    }
    return det || "Gagal menghapus.";
  };
  const handleBulkDelete = async () => {
    setDeleteLoading(true);
    setDeleteErr("");
    try {
      await api.bulkDelete(Array.from(selectedIds), "Hapus dari daftar Pesanan Penjualan", "HAPUS");
      setDeleteConfirm(null);
      setSelectedIds(new Set());
      loadOrders();
    } catch (e) {
      setDeleteErr(formatDeleteError(e.response?.data?.detail));
    } finally {
      setDeleteLoading(false);
    }
  };

  const resetForm = () => {
    clearRepeatState();
    setFormData({
      po_number: options.suggested_po_number || "",
      order_date: getTodayString(),
      customer: "",
      customer_id: null,
      customer_phone: "",
      customer_address: "",
      customer_pic: "",
      customer_email: "",
      customer_city: "",
      outlet: "",
      recipient: "",
      address: "",
      delivery_zone: "",
      payment_terms: "",
      sales: "",
      created_by_user: user?.name || user?.email || "",
      expedition: "Internal fleet",
      estimated_delivery: getThreeDaysLaterString(),
      deadline: getThreeDaysLaterString(),
      notes: "",
      line_items: [createSalesOrderLineItem()],
    });
    setFormErr("");
    setSuccessMsg("");
    setLineModes({});
    setItemImages({});
    dirtyRef.current = false;
    focusLineKeyRef.current = "";
  };

  const leaveCreate = () => {
    if (dirtyRef.current && !confirmDiscardSalesOrder()) return;
    dirtyRef.current = false;
    setViewMode("list");
  };

  const handleRepeatOrderClick = async () => {
    const customerId = formData.customer_id;
    if (!customerId) {
      setRepeatMsg("Pilih customer terdaftar terlebih dahulu.");
      return;
    }
    setRepeatMsg("");
    setRepeatBusy(true);
    repeatAbortRef.current?.abort();
    const ac = new AbortController();
    repeatAbortRef.current = ac;
    repeatRequestCustomerIdRef.current = customerId;
    try {
      let data;
      if (previewRepeatOrderActive) {
        // Dev-only in-memory fixture — no network / no writes.
        data = getPreviewRepeatSource(customerId);
      } else {
        data = await api.salesOrderRepeatSource(customerId, { signal: ac.signal });
      }
      if (ac.signal.aborted) return;
      if (repeatRequestCustomerIdRef.current !== customerId) return;
      if (selectedCustomerIdRef.current !== customerId) return;
      if (!data?.found || !data?.source) {
        setRepeatMsg(REPEAT_NO_PREVIOUS_MESSAGE);
        setRepeatConfirm(null);
        return;
      }
      if (String(data.source.customer_id || "").trim() !== String(customerId).trim()) {
        setRepeatMsg(REPEAT_NO_PREVIOUS_MESSAGE);
        return;
      }
      setRepeatConfirm({
        customerId,
        source: data.source,
        mode: "customer",
      });
    } catch (e) {
      if (e?.code === "ERR_CANCELED" || e?.name === "CanceledError" || ac.signal.aborted) {
        return;
      }
      const det = e.response?.data?.detail;
      setRepeatMsg(typeof det === "object" ? det.message : (det || "Gagal memuat pesanan sebelumnya."));
    } finally {
      if (!ac.signal.aborted) setRepeatBusy(false);
    }
  };

  const handleRepeatSpecificPo = async (orderId) => {
    const oid = String(orderId || "").trim();
    if (!oid) return;
    setRepeatMsg("");
    setRepeatBusy(true);
    repeatAbortRef.current?.abort();
    const ac = new AbortController();
    repeatAbortRef.current = ac;
    try {
      if (viewMode !== "create") {
        setViewMode("create");
      }
      const data = await api.salesOrderRepeatSourceByOrder(oid, { signal: ac.signal });
      if (ac.signal.aborted) return;
      if (!data?.found || !data?.source) {
        setRepeatMsg("Pesanan tidak dapat di-repeat (tidak ditemukan atau tidak valid).");
        setRepeatConfirm(null);
        return;
      }
      setRepeatConfirm({
        customerId: data.source.customer_id || null,
        source: data.source,
        mode: "order",
        orderId: oid,
      });
    } catch (e) {
      if (e?.code === "ERR_CANCELED" || e?.name === "CanceledError" || ac.signal.aborted) {
        return;
      }
      const det = e.response?.data?.detail;
      setRepeatMsg(typeof det === "object" ? det.message : (det || "Gagal memuat pesanan untuk Repeat Order."));
    } finally {
      if (!ac.signal.aborted) setRepeatBusy(false);
    }
  };

  const applyRepeatOrder = () => {
    if (!repeatConfirm?.source) return;
    const source = repeatConfirm.source;
    const masters = masterCustomersForSearch || [];
    const master = resolveRepeatMasterCustomer(source, masters);
    const selected = master
      ? { customer_id: master.id, customer: master.name }
      : {
          customer_id: formData.customer_id || source.customer_id || null,
          customer: formData.customer || source.customer || "",
        };
    if (repeatConfirm.mode === "customer") {
      const customerId = formData.customer_id;
      if (!customerId || (repeatConfirm.customerId && repeatConfirm.customerId !== customerId)) {
        setRepeatConfirm(null);
        setRepeatMsg(REPEAT_NO_PREVIOUS_MESSAGE);
        return;
      }
      selected.customer_id = customerId;
      selected.customer = formData.customer;
    }
    const { formPatch, sourcePoNumber } = mapRepeatSourceToFormPatch(source, selected);
    if (!formPatch) {
      setRepeatConfirm(null);
      setRepeatMsg(REPEAT_NO_PREVIOUS_MESSAGE);
      return;
    }
    const selectedMaster =
      (masters || []).find((m) => String(m?.id || "") === String(selected.customer_id || "")) || master;
    const masterPatch = selectedMaster
      ? applyMasterCustomerBaseline(selectedMaster, selected.customer)
      : {};
    if (formPatch.line_items?.length) {
      const modes = {};
      formPatch.line_items = formPatch.line_items.map((li) => {
        const rowKey = nextSalesOrderLineKey();
        modes[rowKey] = lineEntryModeAfterRepeat(li);
        const next = { ...li, rowKey };
        delete next.entry_mode;
        return next;
      });
      setLineModes(modes);
    }
    setFormData((prev) => ({
      ...prev,
      ...masterPatch,
      ...formPatch,
      po_number: prev.po_number,
      created_by_user: prev.created_by_user,
      customer_phone: masterPatch.customer_phone || prev.customer_phone,
      customer_address: masterPatch.customer_address || prev.customer_address,
      customer_pic: masterPatch.customer_pic || prev.customer_pic,
      customer_email: masterPatch.customer_email || prev.customer_email,
      customer_city: masterPatch.customer_city || prev.customer_city,
      order_date: prev.order_date,
      estimated_delivery: prev.estimated_delivery,
      deadline: prev.deadline,
    }));
    setRepeatSourceIndicator(sourcePoNumber || "");
    setRepeatConfirm(null);
    setRepeatMsg("");
    dirtyRef.current = true;
  };

  useEffect(() => {
    const rid = (searchParams.get(REPEAT_ORDER_QUERY_PARAM) || "").trim();
    if (!rid || !canCreate) return;
    if (consumedRepeatOrderIdRef.current === rid) return;
    consumedRepeatOrderIdRef.current = rid;
    resetForm();
    setViewMode("create");
    void handleRepeatSpecificPo(rid);
  }, [searchParams, canCreate]);

  const handleFormSubmit = async (e) => {
    e?.preventDefault?.();
    if (submitLockRef.current || formBusy) return;
    setFormErr("");
    setSuccessMsg("");

    const validationError = validateSalesOrderCreate(formData);
    if (validationError) {
      setFormErr(validationError);
      return;
    }

    submitLockRef.current = true;
    setFormBusy(true);
    try {
      const payload = buildSalesOrderCreatePayload(formData);

      const res = await api.createSalesOrder(payload);
      setSuccessMsg(res.message || "Pesanan Penjualan berhasil dibuat!");
      const createdId = res.order?.id;
      setTimeout(() => {
        if (createdId) {
          navigate(`/po/${createdId}`);
        } else {
          setViewMode("list");
          loadOrders();
        }
      }, 700);
      return;
    } catch (err) {
      const det = err.response?.data?.detail;
      if (typeof det === "object" && det?.message) {
        setFormErr(det.message + (det.existing_po_number ? ` (${det.existing_po_number})` : ""));
      } else {
        setFormErr(det || "Gagal menyimpan Pesanan Penjualan");
      }
    }
    submitLockRef.current = false;
    setFormBusy(false);
  };

  const outletsForSelectedCustomer = useMemo(() => {
    return options.outlets_by_customer?.[formData.customer] || [];
  }, [options.outlets_by_customer, formData.customer]);

  const fieldHasError = (id) => formIssue?.field === id;
  const newCustomer = !formData.customer_id;
  const showAktifCount = canShowAktifSummary({
    tab: listTab,
    status: statusFilter,
    q: searchQuery,
    period,
    columnFiltersActive,
    total: listMeta.total,
    capped: listMeta.capped,
  });
  const statusSelectValue = statusFilter === tabDefaultStatus ? "__tab__" : statusFilter;
  const pageWindowStart = Math.max(1, Math.min(page - 2, pageCount - 4));
  const pageWindowEnd = Math.min(pageCount, pageWindowStart + 4);
  const pageButtons = [];
  for (let n = pageWindowStart; n <= pageWindowEnd; n += 1) pageButtons.push(n);
  const extraFields = SALES_ORDER_DRAWER_INPUTS.filter(
    (item) => item.field !== "estimated_delivery" && item.field !== "notes",
  );

  const openOrder = (order) => {
    onOpenPo(order.id);
    navigate(`/po/${order.id}`, {
      state: poListNavState({ po_number: order.po_number, customer: order.customer }),
    });
  };

  const onStatusChange = (value) => {
    if (!value || value === "__tab__") {
      setStatusFilter(listTab === "histori" ? "histori" : "aktif");
      return;
    }
    setStatusFilter(value);
    if (value === "selesai" || value === "cancelled" || value === "histori") setListTab("histori");
    else if (value === "aktif" || value === "hold" || value === "all") setListTab("aktif");
  };

  const resetToolbar = () => {
    clearSearch();
    setPeriod("all");
    clearAllColumnFilters();
    setStatusFilter(listTab === "histori" ? "histori" : "aktif");
    setPage(1);
  };

  return (
    <div
      className={viewMode === "create"
        ? "sales-order-page so-create-workspace"
        : "sales-order-page penjualan-page penjualan-workspace so-list-workspace"}
      data-testid="sales-order-page"
      data-view={viewMode}
      data-scroll-owner={viewMode === "create" ? "workspace" : "list"}
    >
      {viewMode === "list" && (
        <div className="so-list" data-testid="sales-order-list">
          <header className="so-list-header">
            <div>
              <h1 data-testid="sales-order-title">Pesanan Penjualan</h1>
              <p>Daftar pesanan penjualan dari pelanggan.</p>
            </div>
            <div className="so-list-header-actions">
              {canDelete && selectedIds.size > 0 && (
                <button
                  type="button"
                  data-testid="btn-hapus-terpilih"
                  className="so-list-delete"
                  onClick={() => {
                    setDeleteErr("");
                    setDeleteConfirm({
                      ids: Array.from(selectedIds),
                      numbers: orders.filter((o) => selectedIds.has(o.id)).map((o) => o.po_number),
                    });
                  }}
                >
                  <Trash2 size={16} /> Hapus Terpilih ({selectedIds.size})
                </button>
              )}
              <button
                type="button"
                className="so-list-refresh"
                onClick={loadOrders}
                disabled={listLoading}
                data-testid="sales-order-refresh-button"
                aria-label="Muat ulang"
              >
                <RefreshCw size={16} className={listLoading ? "spin" : ""} />
              </button>
              {canCreate && (
                <button
                  type="button"
                  className="so-list-create"
                  onClick={() => {
                    resetForm();
                    setViewMode("create");
                  }}
                  data-testid="create-sales-order-btn"
                >
                  <Plus size={16} /> Buat Pesanan
                </button>
              )}
            </div>
          </header>

          {showAktifCount && (
            <div className="so-list-summary" data-testid="sales-order-aktif-count">
              <strong>{Number(listMeta.total).toLocaleString("id-ID")}</strong>
              <span>Pesanan aktif</span>
            </div>
          )}

          <div className="so-list-tabs" role="tablist" aria-label="Klasifikasi pesanan">
            <button
              type="button"
              role="tab"
              aria-selected={listTab === "aktif"}
              data-testid="sales-order-tab-aktif"
              className={listTab === "aktif" ? "is-active" : ""}
              onClick={() => {
                setListTab("aktif");
                setStatusFilter("aktif");
              }}
            >
              Aktif
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={listTab === "histori"}
              data-testid="sales-order-tab-histori"
              className={listTab === "histori" ? "is-active" : ""}
              onClick={() => {
                setListTab("histori");
                setStatusFilter("histori");
              }}
            >
              Histori
            </button>
          </div>

          <div className="so-list-toolbar">
            <div className="so-list-search">
              <Search size={16} />
              <input
                type="text"
                placeholder="Cari pelanggan atau nomor PO..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") flushSearch(e);
                }}
                data-testid="search-sales-orders-input"
              />
              {searchInput && (
                <button
                  type="button"
                  onClick={() => clearSearch()}
                  data-testid="search-sales-orders-clear"
                  aria-label="Hapus pencarian"
                >
                  <X size={14} />
                </button>
              )}
            </div>
            <select
              data-testid="sales-order-status-filter"
              value={statusSelectValue}
              onChange={(e) => onStatusChange(e.target.value)}
              aria-label="Status"
            >
              <option value="__tab__">Semua status</option>
              <option value="aktif">Aktif</option>
              <option value="hold">HOLD</option>
              <option value="selesai">Selesai</option>
              <option value="cancelled">Batal</option>
            </select>
            <select
              data-testid="sales-order-period-filter"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              aria-label="Periode"
            >
              <option value="all">Semua periode</option>
              <option value="this_month">Bulan ini</option>
              <option value="last_month">Bulan lalu</option>
            </select>
            <button type="button" className="so-list-reset" data-testid="sales-order-reset-filters" onClick={resetToolbar}>
              Reset
            </button>
            {columnFiltersActive && (
              <button
                type="button"
                className="so-clear-columns"
                onClick={clearAllColumnFilters}
                data-testid="sales-order-clear-column-filters"
              >
                Hapus filter kolom
              </button>
            )}
          </div>

          {listErr && (
            <div className="so-list-error" data-testid="list-error-banner" role="alert">
              <AlertCircle size={16} />
              <span>{listErr}</span>
            </div>
          )}

          <div className="so-list-table-scroll" data-testid="sales-order-table-scroll">
            <table className="so-list-table">
              <thead>
                <tr>
                  {canDelete && (
                    <th className="so-list-check">
                      <input
                        type="checkbox"
                        data-testid="select-all-orders"
                        checked={displayedOrders.length > 0 && selectedIds.size === displayedOrders.length}
                        onChange={toggleSelectAll}
                        aria-label="Pilih semua baris tampilan"
                      />
                    </th>
                  )}
                  <th>
                    <SalesOrderHeaderFilter
                      label="Pelanggan / No. PO"
                      testid="so-th-filter-customer"
                      active={salesOrderColumnFilterActive("customer", columnFilters)}
                      onClear={() => clearColumnKey("customer")}
                    >
                      <input
                        type="text"
                        placeholder="Cari customer"
                        value={columnFilters.customer}
                        onChange={(e) => patchColumnFilter({ customer: e.target.value })}
                        data-testid="so-filter-customer"
                      />
                    </SalesOrderHeaderFilter>
                    <SalesOrderHeaderFilter
                      label="No. PO"
                      testid="so-th-filter-po"
                      active={salesOrderColumnFilterActive("po", columnFilters)}
                      onClear={() => clearColumnKey("po")}
                    >
                      <input
                        type="text"
                        placeholder="Cari No PO"
                        value={columnFilters.po}
                        onChange={(e) => patchColumnFilter({ po: e.target.value })}
                        data-testid="so-filter-po"
                      />
                    </SalesOrderHeaderFilter>
                    <SalesOrderHeaderFilter
                      label="Outlet"
                      testid="so-th-filter-outlet"
                      active={salesOrderColumnFilterActive("outlet", columnFilters)}
                      onClear={() => clearColumnKey("outlet")}
                    >
                      <input
                        type="text"
                        placeholder="Cari outlet"
                        value={columnFilters.outlet}
                        onChange={(e) => patchColumnFilter({ outlet: e.target.value })}
                        data-testid="so-filter-outlet"
                      />
                    </SalesOrderHeaderFilter>
                  </th>
                  <th>
                    <SalesOrderHeaderFilter
                      label="Tanggal PO"
                      testid="so-th-filter-date"
                      active={salesOrderColumnFilterActive("date", columnFilters)}
                      onClear={() => clearColumnKey("date")}
                    >
                      <label>Dari <input type="date" value={columnFilters.dateFrom} onChange={(e) => patchColumnFilter({ dateFrom: e.target.value })} data-testid="so-filter-date-from" /></label>
                      <label>Sampai <input type="date" value={columnFilters.dateTo} onChange={(e) => patchColumnFilter({ dateTo: e.target.value })} data-testid="so-filter-date-to" /></label>
                    </SalesOrderHeaderFilter>
                  </th>
                  <th>Tanggal kirim</th>
                  <th>
                    <SalesOrderHeaderFilter
                      label="Qty"
                      testid="so-th-filter-qty"
                      active={salesOrderColumnFilterActive("qty", columnFilters)}
                      onClear={() => clearColumnKey("qty")}
                    >
                      <label>Min <input type="number" value={columnFilters.qtyMin} onChange={(e) => patchColumnFilter({ qtyMin: e.target.value })} data-testid="so-filter-qty-min" /></label>
                      <label>Max <input type="number" value={columnFilters.qtyMax} onChange={(e) => patchColumnFilter({ qtyMax: e.target.value })} data-testid="so-filter-qty-max" /></label>
                    </SalesOrderHeaderFilter>
                  </th>
                  <th>
                    <SalesOrderHeaderFilter
                      label="Sisa"
                      testid="so-th-filter-remain"
                      active={salesOrderColumnFilterActive("remain", columnFilters)}
                      onClear={() => clearColumnKey("remain")}
                    >
                      <label>Sisa min <input type="number" value={columnFilters.remainMin} onChange={(e) => patchColumnFilter({ remainMin: e.target.value })} data-testid="so-filter-remain-min" /></label>
                      <label>Sisa max <input type="number" value={columnFilters.remainMax} onChange={(e) => patchColumnFilter({ remainMax: e.target.value })} data-testid="so-filter-remain-max" /></label>
                    </SalesOrderHeaderFilter>
                    <SalesOrderHeaderFilter
                      label="Progress"
                      testid="so-th-filter-delivery"
                      active={salesOrderColumnFilterActive("delivery", columnFilters)}
                      onClear={() => clearColumnKey("delivery")}
                    >
                      <div className="so-th-check-list">
                        {deliveryStatusOptions.map((status) => {
                          const checked = columnFilters.deliveryStatuses.includes(status);
                          return (
                            <label key={status} className="so-th-check">
                              <input
                                type="checkbox"
                                checked={checked}
                                data-testid={`so-filter-delivery-${status}`}
                                onChange={() => {
                                  const next = checked
                                    ? columnFilters.deliveryStatuses.filter((s) => s !== status)
                                    : [...columnFilters.deliveryStatuses, status];
                                  patchColumnFilter({ deliveryStatuses: next });
                                }}
                              />
                              {status}
                            </label>
                          );
                        })}
                      </div>
                    </SalesOrderHeaderFilter>
                  </th>
                  <th>
                    <SalesOrderHeaderFilter
                      label="Status"
                      testid="so-th-filter-stage"
                      active={salesOrderColumnFilterActive(
                        "stage",
                        columnFilters,
                        statusFilter === tabDefaultStatus ? "all" : statusFilter,
                      )}
                      onClear={() => clearColumnKey("stage")}
                    >
                      <select
                        value={statusFilter}
                        onChange={(e) => onStatusChange(e.target.value)}
                        data-testid="so-filter-stage-status"
                      >
                        {SALES_ORDER_STATUS_FILTERS.map((tab) => (
                          <option key={tab.id} value={tab.id}>{tab.label}</option>
                        ))}
                      </select>
                      <input
                        type="text"
                        placeholder="Cari status / stage"
                        value={columnFilters.stageText}
                        onChange={(e) => patchColumnFilter({ stageText: e.target.value })}
                        data-testid="so-filter-stage-text"
                      />
                    </SalesOrderHeaderFilter>
                    <SalesOrderHeaderFilter
                      label="Sales"
                      testid="so-th-filter-sales"
                      active={salesOrderColumnFilterActive("sales", columnFilters)}
                      onClear={() => clearColumnKey("sales")}
                    >
                      <input
                        type="text"
                        placeholder="Cari sales"
                        value={columnFilters.sales}
                        onChange={(e) => patchColumnFilter({ sales: e.target.value })}
                        data-testid="so-filter-sales"
                      />
                    </SalesOrderHeaderFilter>
                  </th>
                  <th>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {listLoading ? (
                  <tr>
                    <td colSpan={canDelete ? 8 : 7} className="so-list-empty">Memuat daftar Pesanan Penjualan...</td>
                  </tr>
                ) : displayedOrders.length === 0 ? (
                  <tr>
                    <td colSpan={canDelete ? 8 : 7} className="so-list-empty" data-testid="empty-sales-orders">
                      <FileText size={28} />
                      <p>Belum ada Pesanan Penjualan yang sesuai filter.</p>
                      {canCreate && (
                        <button
                          type="button"
                          onClick={() => {
                            resetForm();
                            setViewMode("create");
                          }}
                        >
                          <Plus size={14} /> Buat Pesanan
                        </button>
                      )}
                    </td>
                  </tr>
                ) : displayedOrders.map((o) => {
                  const isCancelled = !!o.cancelled;
                  const isHold = !!o.hold;
                  const stageLabel = salesOrderStageLabel(o) || (o.current_stage == null ? "" : `Tahap ${o.current_stage}`);
                  const remainQty = salesOrderRemainingQty(o);
                  const canProcessDelivery = !isCancelled && remainQty > 0;
                  return (
                    <tr
                      key={o.id}
                      data-testid={`order-row-${o.id}`}
                      className="so-list-row"
                      onClick={() => openOrder(o)}
                    >
                      {canDelete && (
                        <td className="so-list-check" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            data-testid={`order-checkbox-${o.id}`}
                            checked={selectedIds.has(o.id)}
                            onChange={() => toggleSelect(o.id)}
                            aria-label={`Pilih ${o.po_number}`}
                          />
                        </td>
                      )}
                      <td>
                        <div className="so-list-customer">{o.customer || "—"}</div>
                        <Link
                          to={`/po/${o.id}`}
                          state={poListNavState({ po_number: o.po_number, customer: o.customer })}
                          onClick={(e) => {
                            e.stopPropagation();
                            onOpenPo(o.id);
                          }}
                          className="so-list-po"
                          data-testid={`po-link-${o.po_number}`}
                        >
                          {o.po_number}
                        </Link>
                        {o.outlet ? <div className="so-list-outlet">{o.outlet}</div> : null}
                      </td>
                      <td>{formatDisplayDate(o.order_date) || "—"}</td>
                      <td>{formatDisplayDate(o.tanggal_pengiriman) || "—"}</td>
                      <td data-testid={`order-qty-${o.id}`}>{orderQuantityLabel(o)}</td>
                      <td>{listProgressText(o)}</td>
                      <td>
                        {isCancelled ? (
                          <span className="so-list-badge is-cancel">Cancelled</span>
                        ) : isHold ? (
                          <span className="so-list-badge is-hold">HOLD</span>
                        ) : (
                          <span className={`so-list-badge${o.current_stage >= 14 ? " is-done" : " is-open"}`}>{stageLabel || "—"}</span>
                        )}
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="so-list-actions">
                          <Link
                            to={`/po/${o.id}`}
                            state={poListNavState({ po_number: o.po_number, customer: o.customer })}
                            onClick={() => onOpenPo(o.id)}
                            data-testid={`btn-view-po-${o.id}`}
                          >
                            Lihat PO
                          </Link>
                          {canCreate && !isCancelled && (
                            <button
                              type="button"
                              data-testid={`btn-repeat-order-${o.id}`}
                              onClick={() => {
                                resetForm();
                                setViewMode("create");
                                void handleRepeatSpecificPo(o.id);
                              }}
                            >
                              Repeat Order
                            </button>
                          )}
                          {canProcessDelivery && (
                            <Link
                              to={`/penjualan/pengiriman-pesanan?po_id=${o.id}&action=create`}
                              data-testid={`btn-process-delivery-${o.id}`}
                            >
                              + Kirim
                            </Link>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <footer className="so-list-pager" data-testid="sales-order-pagination">
            <span>{listRangeLabel(listTotal, page, pageSize)}</span>
            <span className="so-list-pager-note">Klik pesanan untuk membuka detail.</span>
            <label>
              Baris per halaman
              <select
                data-testid="sales-order-page-size"
                value={pageSize}
                onChange={(e) => setPageSize(Number(e.target.value))}
              >
                {SALES_ORDER_PAGE_SIZES.map((size) => (
                  <option key={size} value={size}>{size}</option>
                ))}
              </select>
            </label>
            <div className="so-list-pages">
              <button type="button" data-testid="sales-order-page-prev" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>
                Sebelumnya
              </button>
              {pageButtons.map((n) => (
                <button
                  key={n}
                  type="button"
                  data-testid={`sales-order-page-${n}`}
                  aria-current={n === page ? "page" : undefined}
                  className={n === page ? "is-current" : ""}
                  onClick={() => setPage(n)}
                >
                  {n}
                </button>
              ))}
              <button type="button" data-testid="sales-order-page-next" disabled={page >= pageCount} onClick={() => setPage((value) => value + 1)}>
                Berikutnya
              </button>
            </div>
          </footer>
        </div>
      )}

      {viewMode === "create" && (
        <form
          className="so-create-form"
          onSubmit={handleFormSubmit}
          onChange={() => { dirtyRef.current = true; }}
          data-testid="sales-order-form"
        >
          <header className="so-create-header" data-testid="so-create-header">
            <button
              type="button"
              className="so-create-back"
              onClick={leaveCreate}
              data-testid="back-to-list-btn"
            >
              <ArrowLeft size={16} /> Kembali
            </button>
            <div className="so-create-heading">
              <div>
                <h1 data-testid="sales-order-title">Buat Pesanan Penjualan</h1>
                <p>Input pesanan penjualan baru dari pelanggan.</p>
              </div>
              <div className="so-create-progress" data-testid="so-create-progress">
                <span data-testid="so-progress-label">{formProgress.label}</span>
                <span
                  className={`so-create-chip${formProgress.complete ? " is-complete" : " is-incomplete"}`}
                  data-testid="so-status-chip"
                >
                  {formProgress.statusLabel}
                </span>
              </div>
            </div>
          </header>

          {formErr && (
            <div className="so-create-banner is-error" data-testid="form-error-banner" role="alert">
              <AlertCircle size={16} />
              <span>{formErr}</span>
            </div>
          )}
          {successMsg && (
            <div className="so-create-banner is-success" data-testid="form-success-banner">
              <CheckCircle2 size={16} />
              <span>{successMsg}</span>
            </div>
          )}

          <div className="so-create-columns">
            <section className="so-create-main" data-so-region="main">
              <div className="so-create-fields" data-testid="so-main-fields">
                <SoField field="customer" label="Pelanggan" required error={fieldHasError("customer")} className="so-create-field-customer">
                  <CustomerSearchSelect
                    customers={masterCustomersForSearch}
                    value={formData.customer}
                    selectedId={formData.customer_id}
                    onTextChange={handleCustomerTextChange}
                    onSelectCustomer={handleCustomerSelect}
                    onClear={handleCustomerClear}
                    disabled={formBusy}
                    required
                    testId="input-customer"
                  />
                  <div className="so-create-inline">
                    <button
                      type="button"
                      className="so-create-repeat"
                      data-testid="btn-repeat-order"
                      onClick={handleRepeatOrderClick}
                      disabled={!formData.customer_id || repeatBusy || formBusy}
                      title={formData.customer_id ? "Salin detail dari PO valid terakhir customer ini" : "Pilih customer terdaftar terlebih dahulu"}
                    >
                      {repeatBusy ? "Mencari…" : "Repeat Order"}
                    </button>
                  </div>
                  {formData.customer?.trim() && (
                    <div className="so-create-note">
                      {formData.customer_id ? (
                        <span data-testid="badge-customer-registered">✓ Pelanggan Terdaftar di Master</span>
                      ) : (
                        <span data-testid="badge-customer-new">+ Pelanggan Baru (otomatis masuk ke Data Pelanggan saat PO disimpan)</span>
                      )}
                    </div>
                  )}
                  {repeatSourceIndicator ? (
                    <div className="so-create-note" data-testid="repeat-source-indicator">
                      Repeat dari PO: {repeatSourceIndicator}
                    </div>
                  ) : null}
                  {repeatMsg ? (
                    <div className="so-create-note is-warn" data-testid="repeat-empty-message">{repeatMsg}</div>
                  ) : null}
                </SoField>

                <SoField field="outlet" label="Outlet">
                  {outletsForSelectedCustomer.length > 0 ? (
                    <select
                      id="so-outlet"
                      className="so-line-input"
                      value={formData.outlet}
                      onChange={(e) => handleOutletChange(e.target.value)}
                      data-testid="input-outlet"
                    >
                      <option value="">Pilih outlet</option>
                      {outletsForSelectedCustomer.map((outlet) => (
                        <option key={outlet.id || outlet.outlet} value={outlet.outlet}>{outlet.outlet || "Outlet"}</option>
                      ))}
                      {formData.outlet && !outletsForSelectedCustomer.some((outlet) => outlet.outlet === formData.outlet) && (
                        <option value={formData.outlet}>{formData.outlet}</option>
                      )}
                    </select>
                  ) : (
                    <input
                      id="so-outlet"
                      type="text"
                      className="so-line-input"
                      value={formData.outlet}
                      onChange={(e) => handleOutletChange(e.target.value)}
                      placeholder="Outlet / cabang"
                      data-testid="input-outlet"
                    />
                  )}
                </SoField>

                <SoField field="order_date" label="Tanggal PO">
                  <input
                    id="so-order_date"
                    type="date"
                    className="so-line-input"
                    value={formData.order_date}
                    onChange={(e) => setFormData((p) => ({ ...p, order_date: e.target.value }))}
                    data-testid="input-order-date"
                  />
                </SoField>

                <SoField field="estimated_delivery" label="Rencana kirim">
                  <input
                    id="so-estimated_delivery"
                    type="date"
                    className="so-line-input"
                    value={formData.estimated_delivery}
                    onChange={(e) => setFormData((p) => ({ ...p, estimated_delivery: e.target.value }))}
                    data-testid="input-estimated-delivery"
                  />
                </SoField>

                <SoField field="customer_phone" label="No. HP / WhatsApp Pelanggan" required={newCustomer} error={fieldHasError("customer_phone")}>
                  <input
                    id="so-customer_phone"
                    type="text"
                    className="so-line-input"
                    value={formData.customer_phone || ""}
                    onChange={(e) => setFormData((p) => ({ ...p, customer_phone: e.target.value }))}
                    placeholder="mis. 0812-3456-7890"
                    data-testid="input-customer-phone"
                    aria-invalid={fieldHasError("customer_phone") || undefined}
                  />
                </SoField>

                <SoField field="po_number" label="Nomor PO">
                  <div className="so-create-inline">
                    <input
                      id="so-po_number"
                      type="text"
                      className="so-line-input"
                      value={formData.po_number}
                      onChange={(e) => setFormData((p) => ({ ...p, po_number: e.target.value }))}
                      placeholder="mis. PO-2026-001"
                      data-testid="input-po-number"
                    />
                    <button
                      type="button"
                      className="so-create-auto"
                      onClick={generatePoNumber}
                      title="Buat Nomor Otomatis"
                      data-testid="btn-generate-po"
                    >
                      <Sparkles size={14} /> Auto
                    </button>
                  </div>
                </SoField>

                <SoField field="address" label="Alamat Lengkap Pengiriman" required={newCustomer} error={fieldHasError("address")} className="so-create-field-address">
                  <textarea
                    id="so-address"
                    className="so-line-input so-create-address"
                    value={formData.address}
                    onChange={(e) => setFormData((p) => ({ ...p, address: e.target.value }))}
                    placeholder="Alamat jalan, gedung, nomor, patokan"
                    rows={2}
                    data-testid="input-address"
                    aria-invalid={fieldHasError("address") || undefined}
                  />
                </SoField>
              </div>

              <div className="so-create-lines" data-so-region="lines">
                <div className="so-create-line-toolbar" data-testid="so-line-toolbar">
                  <div className="so-create-line-heading">
                    <h2>Daftar Barang</h2>
                    <span data-testid="so-line-count">{formData.line_items.length} item</span>
                  </div>
                  <button
                    type="button"
                    className="so-create-add"
                    onClick={addLineItem}
                    data-testid="btn-add-line-item"
                  >
                    <Plus size={14} /> Tambah barang
                  </button>
                </div>
                <div className="so-create-lines-scroll" data-testid="line-items-table-container" data-scroll-owner="line-items">
                  <table className="so-create-lines-table">
                    <thead>
                      <tr>
                        <th className="so-col-no" scope="col">#</th>
                        <th className="so-col-name">Barang <span className="so-req">*</span></th>
                        <th className="so-col-qty">Qty <span className="so-req">*</span></th>
                        <th className="so-col-unit">Satuan</th>
                        <th className="so-col-action">Aksi</th>
                      </tr>
                    </thead>
                    <tbody>
                      {formData.line_items.length === 0 ? (
                        <tr>
                          <td colSpan={5} data-testid="line-items-empty-state" className="so-create-empty">
                            Tidak ada barang/jasa yang cocok.
                          </td>
                        </tr>
                      ) : formData.line_items.map((line, originalIndex) => {
                        const mode = lineModes[line.rowKey] || "master";
                        const imagePath = itemImages[String(line.accurate_item_id || "").trim()]?.url;
                        return (
                          <tr key={line.rowKey} data-testid={`line-item-row-${originalIndex}`}>
                            <td className="so-col-no" data-testid={`line-item-number-${originalIndex}`}>{originalIndex + 1}</td>
                            <td className="so-col-name">
                              <div className="so-line-product">
                                <SalesOrderThumb src={imagePath ? publicApiUrl(imagePath) : ""} />
                                <div className="so-line-product-body">
                                  <div className="so-line-mode" role="group" aria-label={`Mode barang baris ${originalIndex + 1}`}>
                                    <button
                                      type="button"
                                      aria-pressed={mode === "master"}
                                      data-testid={`line-mode-master-${originalIndex}`}
                                      onClick={() => changeLineMode(originalIndex, "master")}
                                    >
                                      Pilih Master
                                    </button>
                                    <button
                                      type="button"
                                      aria-pressed={mode === "manual"}
                                      data-testid={`line-mode-manual-${originalIndex}`}
                                      onClick={() => changeLineMode(originalIndex, "manual")}
                                    >
                                      Input Manual
                                    </button>
                                  </div>
                                  {mode === "master" ? (
                                    <MasterItemSearchSelect
                                      testId={`line-item-search-${originalIndex}`}
                                      value={
                                        line.accurate_item_id || line.master_item_id
                                          ? {
                                            accurate_item_id: line.accurate_item_id,
                                            id: line.master_item_id,
                                            master_item_id: line.master_item_id,
                                            item_name: line.name,
                                            item_code: line.item_code,
                                            unit: line.unit,
                                          }
                                          : null
                                      }
                                      onChange={(master) => handleSelectMasterItem(originalIndex, master)}
                                      placeholder="Cari nama / kode barang…"
                                    />
                                  ) : (
                                    <input
                                      type="text"
                                      className="so-line-input"
                                      value={line.name}
                                      onChange={(e) => handleLineChange(originalIndex, "name", e.target.value)}
                                      placeholder="Ketik nama bebas (tanpa master)"
                                      data-testid={`line-name-${originalIndex}`}
                                    />
                                  )}
                                  <div className="so-line-meta">
                                    <input
                                      type="text"
                                      className="so-line-input"
                                      value={line.item_code || ""}
                                      onChange={(e) => handleLineChange(originalIndex, "item_code", e.target.value)}
                                      placeholder="Kode"
                                      data-testid={`line-code-${originalIndex}`}
                                    />
                                    <select
                                      className="so-line-input"
                                      value={line.production_process}
                                      onChange={(e) => handleLineChange(originalIndex, "production_process", e.target.value)}
                                      data-testid={`line-process-${originalIndex}`}
                                      aria-label="Proses"
                                    >
                                      <option value="print">Cetak</option>
                                      <option value="non_print">Tidak Cetak</option>
                                    </select>
                                    <input
                                      type="text"
                                      className="so-line-input"
                                      value={line.color || ""}
                                      onChange={(e) => handleLineChange(originalIndex, "color", e.target.value)}
                                      placeholder="Warna"
                                      data-testid={`line-color-${originalIndex}`}
                                    />
                                    <input
                                      type="text"
                                      className="so-line-input"
                                      value={line.notes || ""}
                                      onChange={(e) => handleLineChange(originalIndex, "notes", e.target.value)}
                                      placeholder="Catatan item"
                                      data-testid={`line-notes-${originalIndex}`}
                                    />
                                  </div>
                                </div>
                              </div>
                            </td>
                            <td>
                              <input
                                type="number"
                                min="1"
                                className="so-line-input"
                                value={line.quantity}
                                onChange={(e) => handleLineChange(originalIndex, "quantity", e.target.value)}
                                data-testid={`line-qty-${originalIndex}`}
                                required
                              />
                            </td>
                            <td>
                              <input
                                type="text"
                                className="so-line-input"
                                value={line.unit || "pcs"}
                                onChange={(e) => handleLineChange(originalIndex, "unit", e.target.value)}
                                data-testid={`line-unit-${originalIndex}`}
                              />
                            </td>
                            <td className="so-col-action">
                              <button
                                type="button"
                                onClick={() => removeLineItem(originalIndex)}
                                disabled={formData.line_items.length <= 1}
                                data-testid={`btn-remove-line-${originalIndex}`}
                                aria-label="Hapus baris"
                              >
                                Hapus
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>

            <aside className="so-create-side" data-testid="so-order-summary">
              <div className="so-create-side-body" data-scroll-owner="summary">
                <h2>Ringkasan Pesanan</h2>
                <dl className="so-summary-list">
                  <div><dt>Pelanggan</dt><dd>{formData.customer || "—"}</dd></div>
                  <div><dt>Outlet</dt><dd>{formData.outlet || "—"}</dd></div>
                  <div><dt>Tanggal PO</dt><dd>{formatDisplayDate(formData.order_date) || "—"}</dd></div>
                  <div><dt>Rencana kirim</dt><dd>{formatDisplayDate(formData.estimated_delivery) || "—"}</dd></div>
                </dl>
                <div className="so-summary-qty">
                  <span data-testid="so-total-items">Jumlah jenis barang <strong>{formData.line_items.length}</strong></span>
                  <span data-testid="so-total-qty">Total kuantitas <strong>{quantityLabel}</strong></span>
                </div>
                <SoField field="notes" label="Catatan">
                  <textarea
                    id="so-notes"
                    className="so-line-input so-create-address"
                    value={formData.notes}
                    onChange={(e) => setFormData((p) => ({ ...p, notes: e.target.value }))}
                    placeholder="Catatan khusus pengerjaan, packing, atau instruksi"
                    rows={3}
                    data-testid="input-notes"
                  />
                </SoField>
                <div className="so-create-extra" data-so-region="drawer" data-testid="so-extra-fields">
                  <h3>Detail tambahan</h3>
                  {extraFields.map((item) => (
                    <SoField key={item.field} field={item.field} label={item.label} error={fieldHasError(item.field)}>
                      <input
                        id={`so-${item.field}`}
                        type={item.type || "text"}
                        className="so-line-input"
                        list={item.list}
                        value={formData[item.field]}
                        onChange={(e) => setFormData((p) => ({ ...p, [item.field]: e.target.value }))}
                        placeholder={item.placeholder}
                        data-testid={item.testId}
                        aria-invalid={fieldHasError(item.field) || undefined}
                      />
                      {item.field === "sales" && (
                        <datalist id="sales-suggestions">
                          {options.sales_options?.map((s) => (
                            <option key={s} value={s} />
                          ))}
                        </datalist>
                      )}
                    </SoField>
                  ))}
                </div>
              </div>
              <div className="so-create-actionbar" data-testid="so-create-actionbar">
                <button
                  type="button"
                  data-testid="btn-cancel-form"
                  disabled={formBusy}
                  onClick={leaveCreate}
                >
                  Batal
                </button>
                <button
                  type="submit"
                  className="so-create-save"
                  disabled={formBusy}
                  data-testid="btn-submit-sales-order"
                >
                  {formBusy ? "Menyimpan..." : "Simpan Pesanan"}
                </button>
              </div>
            </aside>
          </div>
        </form>
      )}

      {deleteConfirm && (
        <div className="modal-overlay" data-testid="delete-confirm-modal" style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 80 }}>
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 460 }}>
            <h3 style={{ margin: "0 0 8px" }}>Hapus Pesanan Penjualan?</h3>
            <p style={{ margin: "0 0 12px", color: "#64748b", fontSize: "0.9rem" }}>Dokumen berikut akan dihapus:</p>
            <ul data-testid="delete-confirm-numbers" style={{ margin: "0 0 16px", paddingLeft: 18, fontWeight: 700, color: "#0284c7" }}>
              {(deleteConfirm.numbers || []).map((n) => <li key={n}>{n}</li>)}
            </ul>
            {deleteErr && <div data-testid="delete-error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>{deleteErr}</div>}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={() => { setDeleteConfirm(null); setDeleteErr(""); }} disabled={deleteLoading} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}>Batal</button>
              <button type="button" data-testid="btn-confirm-delete" onClick={handleBulkDelete} disabled={deleteLoading} style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#dc2626", color: "#fff", fontWeight: 600 }}>
                {deleteLoading ? "Menghapus..." : "Hapus"}
              </button>
            </div>
          </div>
        </div>
      )}

      {repeatConfirm && (
        <div className="modal-overlay" data-testid="repeat-confirm-modal" style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 80 }}>
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 460, margin: 16 }}>
            <h3 style={{ margin: "0 0 8px" }}>
              {repeatConfirm.mode === "order" ? "Salin detail dari PO ini?" : "Salin detail dari PO terakhir ini?"}
            </h3>
            <dl style={{ margin: "0 0 16px", fontSize: "0.9rem" }}>
              <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
                <dt style={{ color: "#64748b", minWidth: 110 }}>No PO</dt>
                <dd data-testid="repeat-confirm-po" style={{ margin: 0, fontWeight: 700, color: "#0284c7" }}>{repeatConfirm.source?.source_po_number || "—"}</dd>
              </div>
              <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
                <dt style={{ color: "#64748b", minWidth: 110 }}>Customer</dt>
                <dd data-testid="repeat-confirm-customer" style={{ margin: 0 }}>{formData.customer || repeatConfirm.source?.customer || "—"}</dd>
              </div>
              <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
                <dt style={{ color: "#64748b", minWidth: 110 }}>Tanggal</dt>
                <dd data-testid="repeat-confirm-date" style={{ margin: 0 }}>
                  {formatDisplayDate(repeatConfirm.source?.source_order_date || repeatConfirm.source?.source_created_at) || "—"}
                </dd>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <dt style={{ color: "#64748b", minWidth: 110 }}>Jumlah baris</dt>
                <dd data-testid="repeat-confirm-lines" style={{ margin: 0 }}>{repeatConfirm.source?.line_count ?? (repeatConfirm.source?.line_items || []).length}</dd>
              </div>
            </dl>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                data-testid="btn-repeat-cancel"
                onClick={() => setRepeatConfirm(null)}
                style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}
              >
                Batal
              </button>
              <button
                type="button"
                data-testid="btn-repeat-confirm"
                onClick={applyRepeatOrder}
                style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#0284c7", color: "#fff", fontWeight: 600 }}
              >
                Gunakan Repeat Order
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
