import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  CircleHelp,
  Ellipsis,
  FileText,
  Filter,
  Package,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import api from "../lib/api";
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
  return {
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
  const [statusFilter, setStatusFilter] = useState(() => searchParams.get("status") || "all");
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
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerIssueField, setDrawerIssueField] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const detailBtnRef = useRef(null);
  const drawerRef = useRef(null);
  const drawerCloseRef = useRef(null);
  const moreBtnRef = useRef(null);
  const moreMenuRef = useRef(null);
  const submitLockRef = useRef(false);
  const drawerWasOpenRef = useRef(false);

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

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false);
  }, []);

  useEffect(() => {
    if (!drawerOpen) {
      if (drawerWasOpenRef.current) {
        drawerWasOpenRef.current = false;
        detailBtnRef.current?.focus();
      }
      return undefined;
    }
    drawerWasOpenRef.current = true;
    if (drawerIssueField && drawerRef.current) {
      const field = drawerRef.current.querySelector(`[data-so-field="${drawerIssueField}"] input, [data-so-field="${drawerIssueField}"] textarea, [data-so-field="${drawerIssueField}"] select`);
      if (field) field.focus();
      else drawerCloseRef.current?.focus();
    } else {
      drawerCloseRef.current?.focus();
    }
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setDrawerOpen(false);
        return;
      }
      if (event.key !== "Tab" || !drawerRef.current) return;
      const root = drawerRef.current;
      const nodes = root.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      const focusable = Array.from(nodes);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      const inside = root.contains(active);
      if (!inside) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
        return;
      }
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, [drawerOpen, drawerIssueField]);

  useEffect(() => {
    if (viewMode === "create") return undefined;
    setDrawerOpen((open) => (open ? false : open));
    setMoreOpen((open) => (open ? false : open));
  }, [viewMode]);

  useEffect(() => {
    if (!moreOpen) return undefined;
    moreMenuRef.current?.querySelector("button")?.focus();
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setMoreOpen(false);
      moreBtnRef.current?.focus();
    };
    const onPointer = (event) => {
      if (moreBtnRef.current?.contains(event.target) || moreMenuRef.current?.contains(event.target)) return;
      setMoreOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [moreOpen]);

  useEffect(() => {
    const issue = salesOrderIssuePlacement(formErr);
    if (issue?.region === "drawer") {
      setDrawerIssueField(issue.field);
      setDrawerOpen(true);
      return;
    }
    setDrawerIssueField("");
  }, [formErr]);

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
      const params = {};
      if (searchQuery.trim()) params.q = searchQuery.trim();
      if (statusFilter !== "all") params.status = statusFilter;
      const res = await api.listSalesOrders(params, { signal: ac.signal });
      if (ac.signal.aborted) return;
      setOrders(res.orders || []);
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
  }, [searchQuery, statusFilter]);

  const displayedOrders = useMemo(
    () => applySalesOrderColumnFilters(orders, columnFilters),
    [orders, columnFilters]
  );
  const columnFiltersActive = salesOrderColumnFiltersAreActive(columnFilters, statusFilter);
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
    if (statusFilter && statusFilter !== "all") next.set("status", statusFilter);
    const nextStr = next.toString();
    const curStr = searchParams.toString();
    if (nextStr === curStr) return;
    setSearchParams(next, { replace: true });
  }, [searchQuery, statusFilter, viewMode, searchParams, setSearchParams]);

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
        address: o.address || patch.address || "",
        recipient: o.recipient || patch.recipient || "",
        delivery_zone: o.delivery_zone || "",
        sales: o.sales || "",
        payment_terms: o.payment_terms || "",
      };
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
        address: match.address || prev.address,
        recipient: match.recipient || prev.recipient,
        delivery_zone: match.delivery_zone || prev.delivery_zone,
        sales: match.sales || prev.sales,
        payment_terms: match.payment_terms || prev.payment_terms,
      }));
    } else {
      setFormData((prev) => ({ ...prev, outlet: newOutlet }));
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
    setFormData((prev) => ({
      ...prev,
      line_items: [...prev.line_items, line],
    }));
    setLineModes((prev) => ({ ...prev, [line.rowKey]: "master" }));
  };

  const removeLineItem = (index) => {
    if (formData.line_items.length <= 1) return;
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

  const totalQty = useMemo(() => {
    return formData.line_items.reduce((sum, li) => sum + (Number(li.quantity) || 0), 0);
  }, [formData.line_items]);
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
    setDrawerOpen(false);
    setDrawerIssueField("");
    setMoreOpen(false);
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
    } catch (err) {
      const det = err.response?.data?.detail;
      if (typeof det === "object" && det?.message) {
        setFormErr(det.message + (det.existing_po_number ? ` (${det.existing_po_number})` : ""));
      } else {
        setFormErr(det || "Gagal menyimpan Pesanan Penjualan");
      }
    } finally {
      submitLockRef.current = false;
      setFormBusy(false);
    }
  };

  const outletsForSelectedCustomer = useMemo(() => {
    return options.outlets_by_customer?.[formData.customer] || [];
  }, [options.outlets_by_customer, formData.customer]);

  const fieldHasError = (id) => formIssue?.field === id;
  const newCustomer = !formData.customer_id;

  return (
    <div
      className={viewMode === "create"
        ? `sales-order-page so-create-workspace${drawerOpen ? " is-drawer-open" : ""}`
        : "sales-order-page penjualan-page penjualan-workspace"}
      data-testid="sales-order-page"
      data-view={viewMode}
      data-scroll-owner={viewMode === "create" ? "page" : undefined}
      style={viewMode === "create" ? undefined : { padding: "20px 24px", maxWidth: 1200, margin: "0 auto" }}
    >
      {viewMode === "list" && (
      <div className="page-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <div>
          <h1 data-testid="sales-order-title" style={{ fontSize: "1.75rem", fontWeight: 700, margin: 0, color: "#0f172a" }}>
            Pesanan Penjualan
          </h1>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              {canDelete && selectedIds.size > 0 && (
                <button
                  type="button"
                  data-testid="btn-hapus-terpilih"
                  onClick={() => {
                    setDeleteErr("");
                    setDeleteConfirm({
                      ids: Array.from(selectedIds),
                      numbers: orders.filter((o) => selectedIds.has(o.id)).map((o) => o.po_number),
                    });
                  }}
                  style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 14px", borderRadius: 8, background: "#fee2e2", color: "#b91c1c", border: "1px solid #fecaca", cursor: "pointer", fontWeight: 600 }}
                >
                  <Trash2 size={16} /> Hapus Terpilih ({selectedIds.size})
                </button>
              )}
              <button
                className="icon-button"
                onClick={loadOrders}
                disabled={listLoading}
                data-testid="sales-order-refresh-button"
                aria-label="Muat ulang"
                style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: "8px 12px", cursor: "pointer", display: "flex", alignItems: "center" }}
              >
                <RefreshCw size={16} className={listLoading ? "spin" : ""} />
              </button>
              {canCreate && (
                <button
                  className="primary"
                  onClick={() => {
                    resetForm();
                    setViewMode("create");
                  }}
                  data-testid="create-sales-order-btn"
                  style={{ display: "flex", alignItems: "center", gap: 6, padding: "8px 16px", borderRadius: 8, fontWeight: 600, background: "#0284c7", color: "#fff", border: "none", cursor: "pointer" }}
                >
                  <Plus size={16} /> Buat Pesanan Penjualan
                </button>
              )}
        </div>
      </div>
      )}

      {viewMode === "create" && (
        <form className="so-create-form" onSubmit={handleFormSubmit} data-testid="sales-order-form">
          <header className="so-create-header" data-testid="so-create-header">
            <div className="so-create-heading">
              <h1 data-testid="sales-order-title">Buat Pesanan Penjualan</h1>
              <div className="so-create-progress" data-testid="so-create-progress">
                <span data-testid="so-progress-label">{formProgress.label}</span>
                <span
                  className={`so-create-chip${formProgress.complete ? " is-complete" : " is-incomplete"}`}
                  data-testid="so-status-chip"
                >
                  {formProgress.statusLabel}
                </span>
              </div>
              <button
                type="button"
                className="so-create-help"
                title="Struktur data, validasi, dan alur workflow sama persis dengan PO Masuk / Import PO"
                aria-label="Struktur data, validasi, dan alur workflow sama persis dengan PO Masuk / Import PO"
              >
                <CircleHelp size={14} />
              </button>
            </div>
            <div className="so-create-header-actions">
              <button
                type="button"
                ref={detailBtnRef}
                className={`so-create-detail${drawerIssueField ? " is-error" : ""}`}
                data-testid="btn-detail-tambahan"
                aria-expanded={drawerOpen}
                aria-controls="so-extra-drawer"
                onClick={() => (drawerOpen ? closeDrawer() : setDrawerOpen(true))}
              >
                Detail Tambahan
                {drawerIssueField ? <span className="so-create-dot" data-testid="so-drawer-error-dot" /> : null}
              </button>
              <button
                type="button"
                className="so-create-back"
                onClick={() => setViewMode("list")}
                data-testid="back-to-list-btn"
                aria-label="Kembali ke daftar"
              >
                <ArrowLeft size={16} /> Kembali
              </button>
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

          <div className="so-create-fields" data-testid="so-main-fields" data-so-region="main">
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

            <SoField field="customer" label="Customer" required error={fieldHasError("customer")} className="so-create-field-customer">
              <div className="so-create-inline">
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
                <h2>Daftar Item</h2>
                <span data-testid="so-line-count">{formData.line_items.length} item</span>
              </div>
              <button
                type="button"
                className="so-create-help"
                title="Masing-masing baris item memiliki identitas independen (tidak akan dimerge otomatis)."
                aria-label="Masing-masing baris item memiliki identitas independen (tidak akan dimerge otomatis)."
              >
                <CircleHelp size={14} />
              </button>
              <button
                type="button"
                className="so-create-add"
                onClick={addLineItem}
                data-testid="btn-add-line-item"
              >
                <Plus size={14} /> Tambah Baris
              </button>
            </div>

            <div
              className="so-create-lines-scroll"
              data-testid="line-items-table-container"
            >
              <table className="so-create-lines-table">
                <thead>
                  <tr>
                    <th className="so-col-no" scope="col">#</th>
                    <th className="so-col-name">Barang / Jasa <span className="so-req">*</span></th>
                    <th className="so-col-code">Kode</th>
                    <th className="so-col-process">Proses</th>
                    <th className="so-col-qty">Qty <span className="so-req">*</span></th>
                    <th className="so-col-unit">Satuan</th>
                    <th className="so-col-color">Warna</th>
                    <th className="so-col-notes">Catatan Item</th>
                    <th className="so-col-action">Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {formData.line_items.length === 0 ? (
                    <tr>
                      <td colSpan={9} data-testid="line-items-empty-state" className="so-create-empty">
                        Tidak ada barang/jasa yang cocok.
                      </td>
                    </tr>
                  ) : (
                    formData.line_items.map((line, originalIndex) => {
                      const mode = lineModes[line.rowKey] || "master";
                      return (
                        <tr key={line.rowKey} data-testid={`line-item-row-${originalIndex}`}>
                          <td className="so-col-no" data-testid={`line-item-number-${originalIndex}`}>{originalIndex + 1}</td>
                          <td className="so-col-name">
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
                        </td>
                          <td>
                          <input
                            type="text"
                              className="so-line-input"
                            value={line.item_code || ""}
                            onChange={(e) => handleLineChange(originalIndex, "item_code", e.target.value)}
                            placeholder="Kode"
                            data-testid={`line-code-${originalIndex}`}
                          />
                        </td>
                          <td>
                          <select
                              className="so-line-input"
                            value={line.production_process}
                            onChange={(e) => handleLineChange(originalIndex, "production_process", e.target.value)}
                            data-testid={`line-process-${originalIndex}`}
                          >
                            <option value="print">Cetak</option>
                            <option value="non_print">Tidak Cetak</option>
                          </select>
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
                          <td>
                          <input
                            type="text"
                              className="so-line-input"
                            value={line.color || ""}
                            onChange={(e) => handleLineChange(originalIndex, "color", e.target.value)}
                            placeholder="mis. Full color"
                            data-testid={`line-color-${originalIndex}`}
                          />
                        </td>
                          <td>
                          <input
                            type="text"
                              className="so-line-input"
                            value={line.notes || ""}
                            onChange={(e) => handleLineChange(originalIndex, "notes", e.target.value)}
                            placeholder="Catatan..."
                            data-testid={`line-notes-${originalIndex}`}
                          />
                        </td>
                          <td className="so-col-action">
                          <button
                            type="button"
                            onClick={() => removeLineItem(originalIndex)}
                            disabled={formData.line_items.length <= 1}
                            data-testid={`btn-remove-line-${originalIndex}`}
                              aria-label="Hapus baris"
                            title="Hapus baris"
                          >
                            <Trash2 size={16} />
                          </button>
                        </td>
                      </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div className="so-create-actionbar" data-testid="so-create-actionbar">
            <div className="so-create-totals">
              <span data-testid="so-total-items">
                Total Item: <strong>{formData.line_items.length}</strong>
              </span>
              <span data-testid="so-total-qty">
                Total Quantity: <strong>{totalQty.toLocaleString("id-ID")} pcs</strong>
              </span>
            </div>
            <div className="so-create-actions">
              <div className="so-create-more">
            <button
              type="button"
                  ref={moreBtnRef}
                  data-testid="btn-lainnya"
                  aria-haspopup="menu"
                  aria-expanded={moreOpen}
                  onClick={() => setMoreOpen((open) => !open)}
                >
                  <Ellipsis size={16} /> Lainnya
                </button>
                {moreOpen && (
                  <div className="so-create-more-menu" ref={moreMenuRef} role="menu" data-testid="so-more-menu">
                    <button
                      type="button"
                      role="menuitem"
                      data-testid="btn-cancel-form"
                      disabled={formBusy}
              onClick={() => setViewMode("list")}
            >
              Batal
            </button>
                  </div>
                )}
              </div>
            <button
              type="submit"
                className="so-create-save"
              disabled={formBusy}
              data-testid="btn-submit-sales-order"
            >
              {formBusy ? "Menyimpan..." : "Simpan Pesanan Penjualan"}
            </button>
          </div>
          </div>

          {drawerOpen && (
            <>
              <button
                type="button"
                className="so-create-drawer-backdrop"
                data-testid="so-drawer-backdrop"
                aria-label="Tutup detail tambahan"
                onClick={closeDrawer}
              />
              <aside
                id="so-extra-drawer"
                ref={drawerRef}
                className="so-create-drawer"
                role="dialog"
                aria-modal="true"
                aria-labelledby="so-extra-drawer-title"
                data-testid="so-extra-drawer"
                data-so-region="drawer"
              >
                <div className="so-create-drawer-head">
                  <h2 id="so-extra-drawer-title">Detail Tambahan</h2>
                  <button
                    type="button"
                    ref={drawerCloseRef}
                    className="so-create-drawer-x"
                    data-testid="so-drawer-close"
                    aria-label="Tutup detail tambahan"
                    onClick={closeDrawer}
                  >
                    ×
                  </button>
                </div>
                <div className="so-create-drawer-body" data-scroll-owner="drawer">
                  {SALES_ORDER_DRAWER_INPUTS.map((item) => (
                    <SoField key={item.field} field={item.field} label={item.label} error={fieldHasError(item.field)}>
                      <input
                        id={`so-${item.field}`}
                        type={item.type || "text"}
                        className="so-line-input"
                        list={item.list}
                        value={formData[item.field]}
                        onChange={(e) => {
                          if (item.field === "outlet") handleOutletChange(e.target.value);
                          else setFormData((p) => ({ ...p, [item.field]: e.target.value }));
                        }}
                        placeholder={item.placeholder}
                        data-testid={item.testId}
                        aria-invalid={fieldHasError(item.field) || undefined}
                      />
                      {item.field === "outlet" && (
                        <datalist id="outlet-suggestions">
                          {outletsForSelectedCustomer.map((o) => (
                            <option key={o.outlet} value={o.outlet} />
                          ))}
                        </datalist>
                      )}
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
                <div className="so-create-drawer-foot">
                  <button type="button" className="so-drawer-done" data-testid="so-drawer-done" onClick={closeDrawer}>
                    Tutup
                  </button>
                </div>
              </aside>
            </>
          )}
        </form>
      )}

      {/* VIEW: LIST ORDERS */}
      {viewMode === "list" && (
        <section className="panel" style={{ background: "#fff", border: "1px solid #e2e8f0", borderRadius: 10, padding: 20, boxShadow: "0 1px 3px rgba(0,0,0,0.05)" }}>
          {listErr && (
            <div
              className="alert-box error"
              data-testid="list-error-banner"
              style={{ background: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c", padding: "10px 14px", borderRadius: 8, marginBottom: 16, display: "flex", alignItems: "center", gap: 10 }}
            >
              <AlertCircle size={16} />
              <span>{listErr}</span>
            </div>
          )}

          {/* Filter Bar */}
          <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <div style={{ position: "relative", minWidth: 280, flex: "1 1 300px" }}>
              <Search size={16} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#94a3b8" }} />
              <input
                type="text"
                placeholder="Cari No PO / Customer / Outlet / Sales..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") flushSearch(e);
                }}
                data-testid="search-sales-orders-input"
                style={{ width: "100%", padding: "8px 12px 8px 34px", border: "1px solid #cbd5e1", borderRadius: 6, fontSize: "0.9rem", boxSizing: "border-box" }}
              />
              {searchInput && (
                <button
                  type="button"
                  onClick={() => clearSearch()}
                  data-testid="search-sales-orders-clear"
                  style={{ position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: "#94a3b8" }}
                >
                  <X size={14} />
                </button>
              )}
            </div>

            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
              {SALES_ORDER_STATUS_FILTERS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setStatusFilter(tab.id)}
                  data-testid={`filter-status-${tab.id}`}
                  style={{
                    padding: "6px 14px",
                    borderRadius: 6,
                    border: "1px solid",
                    borderColor: statusFilter === tab.id ? "#0284c7" : "#e2e8f0",
                    background: statusFilter === tab.id ? "#e0f2fe" : "#f8fafc",
                    color: statusFilter === tab.id ? "#0369a1" : "#475569",
                    fontWeight: statusFilter === tab.id ? 600 : 500,
                    cursor: "pointer",
                    fontSize: "0.85rem",
                  }}
                >
                  {tab.label}
                </button>
              ))}
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
          </div>

          {/* Orders Table */}
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }}>
              <thead>
                <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                  {canDelete && (
                    <th style={{ padding: "10px 8px", width: 36 }}>
                      <input type="checkbox" data-testid="select-all-orders" checked={displayedOrders.length > 0 && selectedIds.size === displayedOrders.length} onChange={toggleSelectAll} aria-label="Pilih semua baris tampilan" />
                    </th>
                  )}
                  <th style={{ padding: "10px 12px" }}>
                    <SalesOrderHeaderFilter
                      label="No PO"
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
                  </th>
                  <th style={{ padding: "10px 12px" }}>
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
                  <th style={{ padding: "10px 12px" }}>
                    <SalesOrderHeaderFilter
                      label="Customer"
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
                  </th>
                  <th style={{ padding: "10px 12px" }}>
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
                  <th style={{ padding: "10px 12px", textAlign: "right" }}>
                    <SalesOrderHeaderFilter
                      label="Total Qty"
                      testid="so-th-filter-qty"
                      active={salesOrderColumnFilterActive("qty", columnFilters)}
                      alignEnd
                      onClear={() => clearColumnKey("qty")}
                    >
                      <label>Min <input type="number" value={columnFilters.qtyMin} onChange={(e) => patchColumnFilter({ qtyMin: e.target.value })} data-testid="so-filter-qty-min" /></label>
                      <label>Max <input type="number" value={columnFilters.qtyMax} onChange={(e) => patchColumnFilter({ qtyMax: e.target.value })} data-testid="so-filter-qty-max" /></label>
                    </SalesOrderHeaderFilter>
                  </th>
                  <th style={{ padding: "10px 12px", textAlign: "right" }}>
                    <SalesOrderHeaderFilter
                      label="Sisa Kirim"
                      testid="so-th-filter-remain"
                      active={salesOrderColumnFilterActive("remain", columnFilters)}
                      alignEnd
                      onClear={() => clearColumnKey("remain")}
                    >
                      <label>Min <input type="number" value={columnFilters.remainMin} onChange={(e) => patchColumnFilter({ remainMin: e.target.value })} data-testid="so-filter-remain-min" /></label>
                      <label>Max <input type="number" value={columnFilters.remainMax} onChange={(e) => patchColumnFilter({ remainMax: e.target.value })} data-testid="so-filter-remain-max" /></label>
                    </SalesOrderHeaderFilter>
                  </th>
                  <th style={{ padding: "10px 12px" }}>
                    <SalesOrderHeaderFilter
                      label="Status Kirim"
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
                  <th style={{ padding: "10px 12px" }}>
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
                  <th style={{ padding: "10px 12px" }}>
                    <SalesOrderHeaderFilter
                      label="Status / Stage"
                      testid="so-th-filter-stage"
                      active={salesOrderColumnFilterActive("stage", columnFilters, statusFilter)}
                      onClear={() => clearColumnKey("stage")}
                    >
                      <select
                        value={statusFilter}
                        onChange={(e) => setStatusFilter(e.target.value)}
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
                  </th>
                  <th style={{ padding: "10px 12px", textAlign: "center" }}>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {listLoading ? (
                  <tr>
                    <td colSpan={canDelete ? 11 : 10} style={{ padding: 32, textAlign: "center", color: "#64748b" }}>
                      Memuat daftar Pesanan Penjualan...
                    </td>
                  </tr>
                ) : displayedOrders.length === 0 ? (
                  <tr>
                    <td colSpan={canDelete ? 11 : 10} style={{ padding: 40, textAlign: "center", color: "#64748b" }} data-testid="empty-sales-orders">
                      <FileText size={32} style={{ opacity: 0.4, marginBottom: 8 }} />
                      <p style={{ margin: 0, fontWeight: 500 }}>Belum ada Pesanan Penjualan yang sesuai filter.</p>
                      {canCreate && (
                        <button
                          type="button"
                          onClick={() => {
                            resetForm();
                            setViewMode("create");
                          }}
                          style={{ marginTop: 12, padding: "6px 14px", background: "#0284c7", color: "#fff", border: "none", borderRadius: 6, cursor: "pointer", fontWeight: 600, fontSize: "0.85rem" }}
                        >
                          + Buat PO Baru
                        </button>
                      )}
                    </td>
                  </tr>
                ) : (
                  displayedOrders.map((o) => {
                    const isCancelled = !!o.cancelled;
                    const isHold = !!o.hold;
                    const stageLabel = salesOrderStageLabel(o) || `Tahap ${o.current_stage}`;
                    const remainQty = salesOrderRemainingQty(o);
                    const delivStatus = salesOrderDeliveryStatus(o);
                    const canProcessDelivery = !isCancelled && remainQty > 0;

                    return (
                      <tr key={o.id} data-testid={`order-row-${o.id}`} style={{ borderBottom: "1px solid #f1f5f9" }}>
                        {canDelete && (
                          <td style={{ padding: "10px 8px" }}>
                            <input type="checkbox" data-testid={`order-checkbox-${o.id}`} checked={selectedIds.has(o.id)} onChange={() => toggleSelect(o.id)} aria-label={`Pilih ${o.po_number}`} />
                          </td>
                        )}
                        <td style={{ padding: "10px 12px", fontWeight: 600 }}>
                          <Link
                            to={`/po/${o.id}`}
                            state={poListNavState({ po_number: o.po_number, customer: o.customer })}
                            onClick={() => onOpenPo(o.id)}
                            style={{ color: "#0284c7", textDecoration: "none" }}
                            data-testid={`po-link-${o.po_number}`}
                          >
                            {o.po_number}
                          </Link>
                        </td>
                        <td style={{ padding: "10px 12px", color: "#475569" }}>
                          {formatDisplayDate(o.order_date) || "-"}
                        </td>
                        <td style={{ padding: "10px 12px", fontWeight: 500, color: "#1e293b" }}>
                          {o.customer || "-"}
                        </td>
                        <td style={{ padding: "10px 12px", color: "#475569" }}>
                          {o.outlet || "-"}
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 600, color: "#0f172a" }}>
                          {Number(o.total_quantity || 0).toLocaleString("id-ID")} pcs
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 600, color: remainQty > 0 ? "#0284c7" : "#16a34a" }}>
                          {remainQty.toLocaleString("id-ID")} pcs
                        </td>
                        <td style={{ padding: "10px 12px" }}>
                          <span
                            style={{
                              display: "inline-block",
                              padding: "2px 8px",
                              borderRadius: 12,
                              fontSize: "0.75rem",
                              fontWeight: 600,
                              background: (delivStatus === "SELESAI" || delivStatus === "SELESAI PENGIRIMAN") ? "#dcfce7" : (delivStatus === "SEBAGIAN" || delivStatus === "SEBAGIAN DIKIRIM") ? "#e0f2fe" : "#f1f5f9",
                              color: (delivStatus === "SELESAI" || delivStatus === "SELESAI PENGIRIMAN") ? "#166534" : (delivStatus === "SEBAGIAN" || delivStatus === "SEBAGIAN DIKIRIM") ? "#0369a1" : "#475569",
                            }}
                          >
                            {delivStatus}
                          </span>
                        </td>
                        <td style={{ padding: "10px 12px", color: "#475569" }}>
                          {salesOrderSalesName(o) || "-"}
                        </td>
                        <td style={{ padding: "10px 12px" }}>
                          {isCancelled ? (
                            <span className="pj-status-badge" style={{ display: "inline-block", padding: "2px 8px", background: "#fee2e2", color: "#991b1b", borderRadius: 12, fontSize: "0.75rem", fontWeight: 600, border: "1.5px solid #fca5a5" }}>
                              Cancelled
                            </span>
                          ) : isHold ? (
                            <span className="pj-status-badge" style={{ display: "inline-block", padding: "2px 8px", background: "#fef3c7", color: "#92400e", borderRadius: 12, fontSize: "0.75rem", fontWeight: 600, border: "1.5px solid #fcd34d" }}>
                              HOLD
                            </span>
                          ) : (
                            <span className="pj-status-badge" style={{ display: "inline-block", padding: "2px 8px", background: o.current_stage >= 14 ? "#dcfce7" : "#e0f2fe", color: o.current_stage >= 14 ? "#166534" : "#075985", borderRadius: 12, fontSize: "0.75rem", fontWeight: 600, border: o.current_stage >= 14 ? "1.5px solid #86efac" : "1.5px solid #7dd3fc" }}>
                              {stageLabel}
                            </span>
                          )}
                        </td>
                        <td style={{ padding: "10px 12px", textAlign: "center" }}>
                          <div style={{ display: "flex", justifyContent: "center", gap: 6 }}>
                            <Link
                              to={`/po/${o.id}`}
                              state={poListNavState({ po_number: o.po_number, customer: o.customer })}
                              onClick={() => onOpenPo(o.id)}
                              data-testid={`btn-view-po-${o.id}`}
                              style={{ display: "inline-block", padding: "4px 8px", background: "#f1f5f9", color: "#334155", borderRadius: 6, textDecoration: "none", fontSize: "0.8rem", fontWeight: 600 }}
                            >
                              Lihat PO
                            </Link>
                            {canCreate && !isCancelled && (
                              <button
                                type="button"
                                data-testid={`btn-repeat-order-${o.id}`}
                                title="Repeat Order dari PO ini"
                                onClick={() => {
                                  resetForm();
                                  setViewMode("create");
                                  void handleRepeatSpecificPo(o.id);
                                }}
                                style={{ display: "inline-block", padding: "4px 8px", background: "#f8fafc", color: "#0f172a", borderRadius: 6, border: "1px solid #cbd5e1", fontSize: "0.8rem", fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" }}
                              >
                                Repeat Order
                              </button>
                            )}
                            {canProcessDelivery && (
                              <Link
                                to={`/penjualan/pengiriman-pesanan?po_id=${o.id}&action=create`}
                                data-testid={`btn-process-delivery-${o.id}`}
                                title="Proses ke Pengiriman Pesanan"
                                style={{ display: "inline-block", padding: "4px 8px", background: "#e0f2fe", color: "#0369a1", borderRadius: 6, textDecoration: "none", fontSize: "0.8rem", fontWeight: 600, whiteSpace: "nowrap" }}
                              >
                                + Kirim
                              </Link>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </section>
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
