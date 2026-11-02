import OperationalBranchSelect from "../components/OperationalBranchSelect";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  MoreHorizontal,
  CheckCircle2,
  FileText,
  Info,
  Package,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Truck,
  X,
} from "lucide-react";
import api, { publicApiUrl } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDesktopListDate } from "../lib/constants";
import { Popover, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import HeaderSelectFilter from "../components/HeaderSelectFilter";
import HeaderFieldFilter from "../components/HeaderFieldFilter";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";
import CustomerSearchSelect, { CUSTOMER_OPTIONS_LOAD_ERROR } from "../components/CustomerSearchSelect";
import LookupSearchSelect from "../components/LookupSearchSelect";
import SalesInvoiceDateField from "../components/SalesInvoiceDateField";
import StockAllocationEditor from "../components/StockAllocationEditor";
import {
  formatAllocationWarningsList,
  isInventoryLineForAllocation,
  payloadAllocationsFromLine,
  summarizeFormAllocationWarnings,
} from "../lib/stockAllocationForm";
import {
  EMPTY_SALES_ORDER_COLUMN_FILTERS,
  SALES_ORDER_DELIVERY_STATUS_OPTIONS,
  SALES_ORDER_INVOICE_STATUS_OPTIONS,
  SALES_ORDER_PROCESS_LABEL_OPTIONS,
  salesOrderColumnFilterActive,
  salesOrderColumnFilterValidation,
  salesOrderColumnFiltersAreActive,
  salesOrderDeliveryStatus,
  salesOrderRemainingQty,
  salesOrderSalesName,
  salesOrderStageLabel,
} from "../lib/pesananPenjualanColumnFilters";
import "./pesananPenjualan.css";
import "./pesananPenjualanPremium.css";
import "./ux90SalesCreate.css";
import { clickableDocumentRowProps } from "../lib/clickableListRow";
import PoIdentityBadges from "../components/PoIdentityBadges";
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
  lineAfterEntryModeChange,
  lineEntryModeAfterRepeat,
  salesOrderIssuePlacement,
} from "../lib/salesOrderCreateWorkspace";
import {
  SALES_ORDER_DEFAULT_PAGE_SIZE,
  SALES_ORDER_PAGE_SIZES,
  canShowAktifSummary,
  formatQuantityByUnit,
  listDeliveryProgress,
  listPageCount,
  listRangeLabel,
  orderCreatorLabel,
  orderInvoiceStatusLabel,
  orderInvoiceStatusTone,
  orderQuantityLabel,
  periodBounds,
  quantityByUnitFromLines,
  salesOrderListRequest,
  salesOrderListTabFromQuery,
  salesOrderStatusFromQuery,
  salesOrderTabDefaultStatus,
} from "../lib/salesOrderListPresentation";
import {
  SALES_ORDER_DEFAULT_COL_WIDTHS,
  clearSalesOrderColWidths,
  clampSalesOrderColWidth,
  readSalesOrderColWidths,
  salesOrderTableMinWidth,
  writeSalesOrderColWidths,
} from "../lib/salesOrderListColumnWidths";
import {
  SALES_LIST_TABS,
  salesFiveTabListMetaFromResponse,
} from "../lib/salesFiveTabs";
import {
  formatOrderRupiah,
  linePriceNumber,
  lineSellingSubtotal,
  orderSellingTotal,
  priceSourceLabel,
  quoteFailureMessage,
} from "../lib/salesOrderLinePrice";
import {
  buildSalesOrderListCacheKey,
  clearSalesOrderListCache,
  getSalesOrderListCache,
  invalidateSalesOrderListCache,
  setSalesOrderListCache,
  shouldRevalidateSalesOrderListOnFocus,
} from "../lib/salesOrderListCache";
import { getWibTodayPlusDays, getWibTodayString } from "../lib/wibCalendarDate";

let salesOrderLineKeySeq = 0;

function listIdentityUserId(user) {
  return user?.id || user?.email || user?.sub || "";
}

function listIdentityBranchId(user) {
  return JSON.stringify([user?.primary_branch_id || user?.branch_id || "", user?.branch_ids || []]);
}

function nextSalesOrderLineKey() {
  salesOrderLineKeySeq += 1;
  return `so-line-${salesOrderLineKeySeq}`;
}

function SoColResizeHandle({ colKey, onPointerDown }) {
  return (
    <span
      className="so-col-resize"
      role="separator"
      aria-orientation="vertical"
      aria-label={`Ubah lebar kolom ${colKey}`}
      data-testid={`so-resize-${colKey}`}
      onPointerDown={(event) => onPointerDown(event, colKey)}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
      }}
    />
  );
}

function InvoiceStatusCell({ order, canOpen }) {
  const label = orderInvoiceStatusLabel(order);
  const tone = orderInvoiceStatusTone(order);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [rows, setRows] = useState([]);

  const loadRelated = useCallback(async () => {
    if (!order?.id) return;
    setLoading(true);
    setErr("");
    try {
      const res = await api.listSalesInvoices({ po_id: order.id });
      setRows(Array.isArray(res?.invoices) ? res.invoices : []);
    } catch (e) {
      const det = e?.response?.data?.detail;
      setErr(typeof det === "object" ? (det.message || "Gagal memuat faktur") : (det || "Gagal memuat faktur"));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [order?.id]);

  if (!label) return <span className="so-list-muted">—</span>;
  if (!canOpen) {
    return (
      <span className={`so-inv-status is-${tone}`} data-testid={`order-invoice-status-${order.id}`}>
        {label}
      </span>
    );
  }
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) void loadRelated();
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          className={`so-inv-status is-${tone} is-clickable`}
          data-testid={`order-invoice-status-${order.id}`}
          onClick={(e) => e.stopPropagation()}
        >
          {label}
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="so-invoice-status-popover"
        sideOffset={6}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="so-invoice-status-popover-title">Faktur terkait</div>
        {loading ? <div className="so-invoice-status-popover-empty">Memuat…</div> : null}
        {!loading && err ? <div className="so-invoice-status-popover-error" role="alert">{err}</div> : null}
        {!loading && !err && rows.length === 0 ? (
          <div className="so-invoice-status-popover-empty">Belum ada faktur untuk PO ini.</div>
        ) : null}
        {!loading && !err && rows.length > 0 ? (
          <ul className="so-invoice-status-popover-list">
            {rows.map((inv) => (
              <li key={inv.id}>
                <Link
                  to={`/penjualan/faktur-penjualan?invoice_id=${encodeURIComponent(inv.id)}`}
                  data-testid={`order-invoice-link-${inv.id}`}
                  onClick={() => setOpen(false)}
                >
                  <span className="so-invoice-status-num">{inv.invoice_number || inv.id}</span>
                  <span className="so-invoice-status-fin">{inv.financial_status || "draft"}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

export function createSalesOrderLineItem(overrides = {}) {
  const rowKey = overrides.rowKey || nextSalesOrderLineKey();
  return {
    ...INITIAL_LINE_ITEM,
    ...overrides,
    rowKey,
    line_id: overrides.line_id || rowKey,
  };
}

/** Match backend CRM guard: case/whitespace-insensitive role check. */
export function isCrmRole(role) {
  return String(role || "").trim().toLowerCase() === "crm";
}

/** Direct SO create gate — CRM always blocked; other roles keep existing perm rules. */
export function resolveSalesOrderCanCreate({
  role,
  hasSalesOrderCreate = false,
  hasPoEdit = false,
} = {}) {
  return (
    !isCrmRole(role)
    && (Boolean(hasSalesOrderCreate) || Boolean(hasPoEdit) || role === "admin")
  );
}

/** Same messages and branches as the previous inline submit validation. */
/** Create-path: SERVICE + Cetak must pick a valid non-SERVICE induk in this PO. */
export function validateSalesOrderAddonParents(lineItems) {
  const lines = Array.isArray(lineItems) ? lineItems : [];
  const idToIdx = {};
  lines.forEach((li, i) => {
    const id = String(li?.line_id || li?.rowKey || "").trim();
    if (id) idToIdx[id] = i;
  });
  for (let i = 0; i < lines.length; i += 1) {
    const li = lines[i] || {};
    const itype = String(li.item_type || "").trim().toUpperCase();
    const proc = li.production_process === "non_print" ? "non_print" : "print";
    if (itype !== "SERVICE" || proc !== "print") continue;
    const selfId = String(li.line_id || li.rowKey || "").trim();
    const parentId = String(li.parent_line_id || "").trim();
    if (!parentId) {
      return (
        `Baris #${i + 1}: Jasa Cetak (Add On warna) wajib memilih barang induk ` +
        "non-SERVICE dalam PO yang sama."
      );
    }
    if (selfId && parentId === selfId) {
      return `Baris #${i + 1}: Add On tidak boleh merujuk dirinya sendiri sebagai induk.`;
    }
    if (!Object.prototype.hasOwnProperty.call(idToIdx, parentId)) {
      return `Baris #${i + 1}: barang induk tidak ditemukan dalam PO (parent_line_id).`;
    }
    const parentType = String(lines[idToIdx[parentId]]?.item_type || "").trim().toUpperCase();
    if (parentType === "SERVICE") {
      return `Baris #${i + 1}: barang induk Add On tidak boleh bertipe SERVICE.`;
    }
  }
  return "";
}

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
    const mid = String(li?.master_item_id || "").trim();
    const aid = String(li?.accurate_item_id || "").trim();
    if (!mid && !aid) {
      return (
        `Baris #${i + 1}: Master Produk wajib dipilih ` +
        "(master_item_id atau accurate_item_id)."
      );
    }
    const q = Number(li.quantity);
    if (!Number.isFinite(q) || q <= 0) {
      return `Baris #${i + 1}: Quantity harus angka lebih besar dari 0.`;
    }
  }
  const addonErr = validateSalesOrderAddonParents(formData.line_items);
  if (addonErr) return addonErr;
  return "";
}

export function salesOrderRequiredChecks(formData) {
  const lines = formData?.line_items || [];
  const linesOk = lines.length > 0 && lines.every((li) => {
    const name = String(li?.name || "").trim();
    const mid = String(li?.master_item_id || "").trim();
    const aid = String(li?.accurate_item_id || "").trim();
    const q = Number(li?.quantity);
    return Boolean(name) && (Boolean(mid) || Boolean(aid)) && Number.isFinite(q) && q > 0;
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
  const ids = checks.map((item) => item.id);
  const customerAndGoodsOnly = ids.length === 2 && ids[0] === "customer" && ids[1] === "line_items";
  return {
    completed,
    total,
    complete,
    customerAndGoodsOnly,
    label: customerAndGoodsOnly
      ? `${completed}/${total} pelanggan & barang`
      : `${completed}/${total} wajib`,
    statusLabel: complete ? "Lengkap" : "Belum Lengkap",
  };
}

export function buildSalesOrderCreatePayload(formData) {
  const payload = {
    ...formData,
    po_number: String(formData.po_number || "").trim() || undefined,
    customer: String(formData.customer || "").trim(),
    line_items: (formData.line_items || []).map((li) => {
      const price = linePriceNumber(li.unit_price);
      const parentId = String(li.parent_line_id || "").trim();
      const lineId = String(li.line_id || li.rowKey || "").trim();
      const next = {
        ...li,
        name: String(li.name || "").trim(),
        quantity: Number(li.quantity),
        unit: (li.unit || "pcs").trim(),
        production_process: li.production_process === "non_print" ? "non_print" : "print",
        unit_price: price,
        price_source: price === null ? null : (li.price_source === "pengaturan_harga" ? "pengaturan_harga" : "manual"),
        price_review: Boolean(li.price_review),
      };
      if (lineId) next.line_id = lineId;
      else delete next.line_id;
      if (parentId) next.parent_line_id = parentId;
      else delete next.parent_line_id;
      const itemType = String(li.item_type || "").trim().toUpperCase();
      if (itemType) next.item_type = itemType;
      else delete next.item_type;
      const allocs = payloadAllocationsFromLine(li);
      if (allocs && allocs.length) next.stock_allocations = allocs;
      else delete next.stock_allocations;
      delete next.parent_line_idx;
      delete next.entry_mode;
      delete next.rowKey;
      delete next.price_lock;
      delete next.price_message;
      return next;
    }),
  };
  delete payload.outlet_id;
  return payload;
}

function getTodayString() {
  return getWibTodayString();
}

function getThreeDaysLaterString() {
  return getWibTodayPlusDays(3);
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
  unit_price: "",
  price_source: "",
  price_review: false,
  price_lock: false,
  parent_line_id: "",
  item_type: "",
  stock_allocations: [],
};

const CHIP_MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];

function formatChipDate(iso) {
  const match = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return "";
  return `${match[3]}/${match[2]}/${match[1]}`;
}

function formatShipCardDate(iso) {
  const match = String(iso || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return "";
  return `${Number(match[3])} ${CHIP_MONTHS[Number(match[2]) - 1]} ${match[1]}`;
}

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

function salesOrderProgressTone(order, status) {
  if (order?.cancelled) return "is-cancel";
  if (order?.hold) return "is-hold";
  if (!status) status = salesOrderDeliveryStatus(order);
  if (status === "SELESAI" || status === "SELESAI PENGIRIMAN") return "is-done";
  if (status === "DIBATALKAN") return "is-cancel";
  if (status === "SEBAGIAN" || status === "SEBAGIAN DIKIRIM") return "is-open";
  return "is-wait";
}

function confirmDiscardSalesOrder() {
  if (typeof window === "undefined" || typeof window.confirm !== "function") return true;
  try {
    return window.confirm("Ada perubahan yang belum disimpan. Tinggalkan formulir ini?");
  } catch {
    return true;
  }
}

function SalesOrderDeliveryProgress({ order }) {
  const progress = listDeliveryProgress(order);
  const tone = salesOrderProgressTone(order, progress.status);
  return (
    <div className={`so-progress ${tone}`} data-testid={`order-progress-${order.id}`}>
      {progress.ratio != null && (
        <div
          className="so-progress-track"
          role="meter"
          aria-valuemin={0}
          aria-valuemax={progress.ordered || 0}
          aria-valuenow={progress.shipped || 0}
          aria-label={progress.label}
        >
          <span className="so-progress-fill" style={{ width: `${progress.ratio * 100}%` }} />
        </div>
      )}
      <span className="so-progress-label">{progress.label}</span>
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

  // CRM must use Pra PO → Admin verify; never expose direct SO create even with perm overrides.
  const canCreate = resolveSalesOrderCanCreate({
    role: user?.role,
    hasSalesOrderCreate: hasPerm("sales_order.create"),
    hasPoEdit: hasPerm("po.edit"),
  });
  const canDelete = hasPerm("po.delete") || user?.role === "admin";
  const canViewInvoice = hasPerm("sales.invoice.view") || user?.role === "admin";
  const [viewMode, setViewMode] = useState(() =>
    searchParams.get(REPEAT_ORDER_QUERY_PARAM) ? "create" : "list"
  ); // 'list' | 'create'
  const [createTab, setCreateTab] = useState("items");
  const [detailRowKey, setDetailRowKey] = useState("");
  const [editingField, setEditingField] = useState("");
  const [itemPage, setItemPage] = useState(1);
  const itemsPerPage = 10;
  
  // List state
  const [orders, setOrders] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listRefreshing, setListRefreshing] = useState(false);
  const [listErr, setListErr] = useState("");
  const [listSyncErr, setListSyncErr] = useState("");
  const [listShowingCache, setListShowingCache] = useState(false);
  const [colWidths, setColWidths] = useState(() => readSalesOrderColWidths());
  const colResizeRef = useRef(null);
  const listUserId = listIdentityUserId(user);
  const listBranchId = listIdentityBranchId(user);
  const {
    input: searchInput,
    setInput: setSearchInput,
    q: searchQuery,
    flush: flushSearch,
    clear: clearSearch,
  } = useDebouncedLiveSearch(searchParams.get("q") || "");
  const [listTab, setListTab] = useState(() => salesOrderListTabFromQuery({
    tab: searchParams.get("tab"),
    status: searchParams.get("status"),
  }));
  const [statusFilter, setStatusFilter] = useState(() => salesOrderStatusFromQuery({
    tab: searchParams.get("tab"),
    status: searchParams.get("status"),
  }));
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
    return SALES_ORDER_PAGE_SIZES.includes(value) ? value : SALES_ORDER_DEFAULT_PAGE_SIZE;
  });
  const [listMeta, setListMeta] = useState({
    total: null,
    capped: false,
    tabTotal: 0,
    counts: { all: 0, active: 0, completed: 0, pending: 0, cancelled: 0 },
  });
  const [itemImages, setItemImages] = useState({});
  const [columnFilters, setColumnFilters] = useState(() => ({
    ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
  }));
  const [customerFilterDraft, setCustomerFilterDraft] = useState("");
  useEffect(() => {
    setCustomerFilterDraft(columnFilters.customerQ || "");
  }, [columnFilters.customerQ]);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");

  // Options & Auto-fill masters
  const [options, setOptions] = useState({
    customers: [],
    outlets_by_customer: {},
    sales_options: [],
    creator_options: [],
    qty_unit_options: [],
    process_label_options: [],
    master_items: [],
    suggested_po_number: "",
    master_customers_state: "idle",
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
    sales_master_id: "",
    created_by_user: user?.name || user?.email || "",
    expedition: "Internal fleet",
    estimated_delivery: getThreeDaysLaterString(),
    deadline: getThreeDaysLaterString(),
    notes: "",
    line_items: [createSalesOrderLineItem()],
  }));

  const [formBusy, setFormBusy] = useState(false);
  const [formSaved, setFormSaved] = useState(false);
  const [formErr, setFormErr] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const [allocationNotice, setAllocationNotice] = useState("");
  const [warehouses, setWarehouses] = useState([]);
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
    if (
      issue.field === "customer_phone"
      || issue.field === "address"
      || issue.field === "recipient"
      || issue.field === "delivery_zone"
      || issue.field === "expedition"
      || issue.field === "deadline"
    ) {
      setCreateTab("ship");
    } else if (issue.region === "drawer" || issue.field === "notes") {
      setCreateTab("info");
    } else if (issue.region === "lines" || issue.region === "main") {
      setCreateTab("items");
    }
    if (
      issue.field === "customer"
      || issue.field === "outlet"
      || issue.field === "po_number"
      || issue.field === "order_date"
      || issue.field === "estimated_delivery"
      || issue.field === "sales"
    ) {
      setEditingField(issue.field);
    }
    const invalidRow = String(formErr || "").match(/Baris #(\d+)/);
    if (invalidRow) setItemPage(Math.ceil(Number(invalidRow[1]) / itemsPerPage));
  }, [formErr]);

  useEffect(() => {
    const issue = salesOrderIssuePlacement(formErr);
    if (!issue?.field) return;
    const invalidRow = String(formErr || "").match(/Baris #(\d+)/);
      const rowIndex = invalidRow ? Number(invalidRow[1]) - 1 : null;
      const selector = rowIndex == null
        ? `[data-so-field="${issue.field}"] input, [data-so-field="${issue.field}"] textarea, [data-so-field="${issue.field}"] select`
        : (/Quantity/i.test(formErr)
          ? `[data-testid="line-qty-${rowIndex}"]`
          : `[data-testid="line-name-${rowIndex}"], [data-testid="line-item-search-${rowIndex}-input"]`);
      document.querySelector(selector)?.focus();
  }, [formErr, itemPage, createTab, editingField]);

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
  const requestSeq = useRef(0);
  const prevListUserRef = useRef(listUserId);

  const applyOrderListPayload = useCallback((rows, meta, { fromCache = false } = {}) => {
    setOrders(rows);
    setListMeta(meta);
    setListShowingCache(Boolean(fromCache));
    if (!fromCache) setListSyncErr("");
  }, []);

  const loadOptions = useCallback(async ({ applySuggestedPo = false } = {}) => {
    setOptionsLoading(true);
    setOptions((prev) => ({
      ...prev,
      master_customers_state: "loading",
    }));
    try {
      // Guard: tests/mocks may omit salesMasterCatalog or return a non-thenable.
      const catalogPromise = Promise.resolve()
        .then(() => (
          typeof api.salesMasterCatalog === "function"
            ? api.salesMasterCatalog()
            : { items: [] }
        ))
        .then((res) => res || { items: [] })
        .catch(() => ({ items: [] }));
      const [data, catalogRes, warehouseRes] = await Promise.all([
        api.salesOrderOptions(),
        catalogPromise,
        Promise.resolve()
          .then(() => (typeof api.warehouses === "function" ? api.warehouses() : { items: [] }))
          .then((res) => res || { items: [] })
          .catch(() => ({ items: [] })),
      ]);
      const catalog = Array.isArray(catalogRes?.items) ? catalogRes.items : [];
      // sales_options stay for list column filters (historical snapshots only).
      // Create picker uses sales_master_catalog exclusively — no free-text fallback.
      setOptions({
        ...(data || {}),
        sales_options: Array.isArray(data?.sales_options) ? data.sales_options : [],
        sales_master_catalog: catalog,
        sales_catalog_state: "ready",
        master_customers_state: "ready",
      });
      setWarehouses(
        (Array.isArray(warehouseRes?.items) ? warehouseRes.items : [])
          .filter((wh) => wh && wh.active !== false),
      );
      // List mode loads options for header filters (e.g. Sales); only create may seed PO.
      if (applySuggestedPo && data?.suggested_po_number) {
        setFormData((prev) => ({
          ...prev,
          po_number: prev.po_number || data.suggested_po_number,
        }));
      }
    } catch {
      setOptions((prev) => ({
        ...prev,
        sales_master_catalog: Array.isArray(prev.sales_master_catalog) ? prev.sales_master_catalog : [],
        sales_catalog_state: "error",
        master_customers_state: "error",
      }));
    } finally {
      setOptionsLoading(false);
    }
  }, []);

  // Stale-while-revalidate: paint exact-key cache, then full-page refetch same query.
  // Always background-revalidate on cache hit (TTL is not a substitute for tab-activate refresh).
  const loadOrders = useCallback(async ({ force = false } = {}) => {
    const cacheKey = buildSalesOrderListCacheKey({
      userId: listUserId,
      branchId: listBranchId,
      canViewInvoice,
      listTab,
      period,
      q: searchQuery,
      columnFilters,
      page,
      pageSize,
    });

    const validationError = salesOrderColumnFilterValidation(columnFilters);
    if (validationError) {
      setListErr(validationError);
      applyOrderListPayload([], {
        total: 0,
        capped: false,
        tabTotal: 0,
        counts: { all: 0, active: 0, completed: 0, pending: 0, cancelled: 0 },
      }, { fromCache: false });
      setListLoading(false);
      setListRefreshing(false);
      return;
    }

    const cached = getSalesOrderListCache(cacheKey);
    const hasCache = Boolean(cached);

    if (hasCache) {
      applyOrderListPayload(cached.orders, cached.listMeta, { fromCache: true });
      setSelectedIds(new Set());
    } else {
      // Clear rows from a different query, but keep total=null so URL page restore
      // is not clamped to page 1 before the authoritative response arrives.
      applyOrderListPayload([], {
        total: null,
        capped: false,
        tabTotal: 0,
        counts: { all: 0, active: 0, completed: 0, pending: 0, cancelled: 0 },
      }, { fromCache: false });
    }

    const seq = requestSeq.current + 1;
    requestSeq.current = seq;
    loadOrdersAbortRef.current?.abort();
    const ac = new AbortController();
    loadOrdersAbortRef.current = ac;
    if (hasCache) {
      setListRefreshing(true);
      setListLoading(false);
    } else {
      setListLoading(true);
      setListRefreshing(false);
    }
    setListErr("");

    try {
      const bounds = periodBounds(period);
      const params = salesOrderListRequest({
        q: searchQuery,
        listTab,
        dateFrom: bounds.date_from,
        dateTo: bounds.date_to,
        orderDateFrom: columnFilters.orderDateFrom,
        orderDateTo: columnFilters.orderDateTo,
        shipDateFrom: columnFilters.shipDateFrom,
        shipDateTo: columnFilters.shipDateTo,
        customerQ: columnFilters.customerQ,
        sales: columnFilters.sales,
        createdBy: columnFilters.createdBy,
        deliveryStatuses: columnFilters.deliveryStatuses,
        processLabels: columnFilters.processLabels,
        invoiceStatuses: canViewInvoice ? columnFilters.invoiceStatuses : [],
        qtyMin: columnFilters.qtyMin,
        qtyMax: columnFilters.qtyMax,
        qtyUnit: columnFilters.qtyUnit,
        page,
        pageSize,
      });
      const res = await api.listSalesOrders(params, { signal: ac.signal });
      if (seq !== requestSeq.current || ac.signal.aborted) return;
      const rows = res.orders || [];
      const meta = salesFiveTabListMetaFromResponse(res, { rowCount: rows.length });
      setSalesOrderListCache(cacheKey, { orders: rows, listMeta: meta, res });
      applyOrderListPayload(rows, meta, { fromCache: false });
      setSelectedIds(new Set());
    } catch (e) {
      if (seq !== requestSeq.current || ac.signal.aborted || e?.code === "ERR_CANCELED" || e?.name === "CanceledError") {
        return;
      }
      const det = e.response?.data?.detail;
      const message = typeof det === "object"
        ? det.message
        : det || "Gagal memuat daftar Pesanan Penjualan";
      if (hasCache) {
        setListSyncErr(message || "Gagal menyinkronkan daftar. Menampilkan data tersimpan sementara.");
        setListShowingCache(true);
      } else {
        setListErr(message);
      }
    } finally {
      if (seq === requestSeq.current && !ac.signal.aborted) {
        setListLoading(false);
        setListRefreshing(false);
      }
    }
  }, [
    applyOrderListPayload,
    canViewInvoice,
    columnFilters,
    listBranchId,
    listTab,
    listUserId,
    page,
    pageSize,
    period,
    searchQuery,
  ]);

  const reloadOrderListAuthoritative = useCallback(async () => {
    invalidateSalesOrderListCache();
    setListShowingCache(false);
    setListSyncErr("");
    await loadOrders({ force: true });
  }, [loadOrders]);

  const serverPaged = listMeta.total != null && Number.isFinite(Number(listMeta.total));
  const displayedOrders = useMemo(() => {
    if (serverPaged) return orders;
    const start = (page - 1) * pageSize;
    return orders.slice(start, start + pageSize);
  }, [orders, serverPaged, page, pageSize]);
  const listTotal = serverPaged ? Number(listMeta.total) : orders.length;
  const pageCount = listPageCount(listTotal, pageSize);
  const tabDefaultStatus = salesOrderTabDefaultStatus(listTab);
  const columnFiltersActive = salesOrderColumnFiltersAreActive(columnFilters);
  const salesFilterOptions = useMemo(() => {
    const names = Array.isArray(options.sales_options) ? options.sales_options : [];
    return [
      { value: "", label: "Semua" },
      ...names.filter(Boolean).map((name) => ({ value: name, label: name })),
    ];
  }, [options.sales_options]);
  const creatorFilterOptions = useMemo(() => {
    const rows = Array.isArray(options.creator_options) ? options.creator_options : [];
    return [
      { value: "", label: "Semua" },
      ...rows.map((row) => ({
        value: row.value ?? "",
        label: row.label || row.value || "Tidak diketahui",
      })),
    ];
  }, [options.creator_options]);
  const processFilterOptions = useMemo(() => {
    const fromApi = Array.isArray(options.process_label_options)
      ? options.process_label_options
      : [];
    if (fromApi.length) {
      return fromApi.map((label) => ({ value: label, label }));
    }
    return SALES_ORDER_PROCESS_LABEL_OPTIONS;
  }, [options.process_label_options]);
  const qtyUnitFilterOptions = useMemo(() => {
    const units = Array.isArray(options.qty_unit_options) ? options.qty_unit_options : [];
    return units.filter(Boolean);
  }, [options.qty_unit_options]);

  const patchColumnFilter = (patch) => {
    setColumnFilters((prev) => ({ ...prev, ...patch }));
  };
  const clearColumnFilter = (keys) => {
    const list = Array.isArray(keys) ? keys : [keys];
    setColumnFilters((prev) => {
      const next = { ...prev };
      list.forEach((key) => {
        if (Object.prototype.hasOwnProperty.call(EMPTY_SALES_ORDER_COLUMN_FILTERS, key)) {
          next[key] = EMPTY_SALES_ORDER_COLUMN_FILTERS[key];
        }
      });
      return next;
    });
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
    const tabDefault = salesOrderTabDefaultStatus(listTab);
    if (statusFilter && statusFilter !== tabDefault) next.set("status", statusFilter);
    if (listTab && listTab !== "active") next.set("tab", listTab);
    if (period !== "all") next.set("period", period);
    if (page > 1) next.set("page", String(page));
    if (pageSize !== SALES_ORDER_DEFAULT_PAGE_SIZE) next.set("page_size", String(pageSize));
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
  }, [searchQuery, statusFilter, period, pageSize, listTab, columnFilters]);

  useEffect(() => {
    if (viewMode !== "list") return undefined;
    loadOptions();
    return undefined;
  }, [viewMode, loadOptions]);

  useEffect(() => {
    // An empty list before the first response is not a one-page result.
    // Clamping here would drop a restored page from the detail-return URL.
    if (listMeta.total == null && orders.length === 0) return;
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount, listMeta.total, orders.length]);

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
      loadOptions({ applySuggestedPo: true });
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
    node.focus({ preventScroll: true });
    node.scrollIntoView({ block: "nearest", inline: "nearest" });
    focusLineKeyRef.current = "";
  }, [formData.line_items, viewMode]);

  useEffect(() => {
    if (prevListUserRef.current === listUserId) return;
    clearSalesOrderListCache();
    prevListUserRef.current = listUserId;
  }, [listUserId]);

  useEffect(() => {
    if (viewMode === "list") {
      loadOrders();
    }
    return () => {
      loadOrdersAbortRef.current?.abort();
    };
  }, [viewMode, loadOrders]);

  useEffect(() => {
    if (viewMode !== "list") return undefined;
    const onVisible = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      if (!shouldRevalidateSalesOrderListOnFocus()) return;
      loadOrders({ force: false });
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
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

  const applyAutomaticQuote = useCallback((index, identity, quote, failed) => {
    setFormData((prev) => {
      const lines = [...prev.line_items];
      const line = lines[index];
      if (!line) return prev;
      if (String(line.accurate_item_id || "") !== String(identity.accurate_item_id || "")) return prev;
      if (String(line.unit || "") !== String(identity.unit || "")) return prev;
      if (Number(line.quantity) !== Number(identity.quantity)) return prev;
      if (line.price_lock || line.price_source === "manual") return prev;
      if (linePriceNumber(line.unit_price) !== null) return prev;
      const matched = !failed
        && quote?.status === "matched"
        && linePriceNumber(quote.unit_price) !== null;
      if (!matched) {
        lines[index] = {
          ...line,
          unit_price: "",
          price_source: "",
          price_lock: false,
          price_message: quoteFailureMessage(quote, failed),
        };
        return { ...prev, line_items: lines };
      }
      lines[index] = {
        ...line,
        unit_price: String(quote.unit_price),
        price_source: "pengaturan_harga",
        price_review: false,
        price_lock: false,
        price_message: "",
      };
      return { ...prev, line_items: lines };
    });
  }, []);

  const requestSellingQuote = useCallback((index, identity) => {
    if (!identity?.accurate_item_id) return;
    api.salesOrderPriceQuote({
      accurate_item_id: identity.accurate_item_id,
      unit: identity.unit,
      quantity: Number(identity.quantity),
    }).then((quote) => {
      applyAutomaticQuote(index, identity, quote, false);
    }).catch(() => {
      applyAutomaticQuote(index, identity, null, true);
    });
  }, [applyAutomaticQuote]);

  const reviewLinePrice = async (index) => {
    const line = formData.line_items[index];
    if (!line?.accurate_item_id) {
      setFormData((prev) => {
        const lines = [...prev.line_items];
        if (!lines[index]) return prev;
        lines[index] = {
          ...lines[index],
          price_review: true,
          price_message: "Barang belum terhubung ke Pengaturan Harga. Isi manual.",
        };
        return { ...prev, line_items: lines };
      });
      return;
    }
    let quote = null;
    let failed = false;
    try {
      quote = await api.salesOrderPriceQuote({
        accurate_item_id: line.accurate_item_id,
        unit: line.unit,
        quantity: Number(line.quantity),
      });
    } catch {
      failed = true;
    }
    setFormData((prev) => {
      const lines = [...prev.line_items];
      const current = lines[index];
      if (!current) return prev;
      const matched = !failed && quote?.status === "matched" && linePriceNumber(quote?.unit_price) !== null;
      if (matched) {
        lines[index] = {
          ...current,
          unit_price: String(quote.unit_price),
          price_source: "pengaturan_harga",
          price_review: false,
          price_lock: false,
          price_message: "",
        };
      } else {
        lines[index] = {
          ...current,
          price_review: true,
          price_message: quoteFailureMessage(quote, failed),
        };
      }
      return { ...prev, line_items: lines };
    });
    dirtyRef.current = true;
  };

  // Line item handlers
  const handleLineChange = (index, field, value) => {
    let quoteIdentity = null;
    setFormData((prev) => {
      const lines = [...prev.line_items];
      const current = lines[index];
      if (!current) return prev;
      let next = { ...current, [field]: value };
      if (field === "unit_price") {
        next.price_message = "";
        if (String(value).trim() === "") {
          next.price_source = "";
          next.price_lock = false;
          next.price_review = false;
        } else {
          next.price_source = "manual";
          next.price_lock = true;
          next.price_review = false;
        }
      } else if (field === "unit") {
        const hadPrice = linePriceNumber(current.unit_price) !== null;
        next = {
          ...current,
          unit: value,
          stock_allocations: [],
          price_review: hadPrice || current.price_lock || current.price_source === "pengaturan_harga",
          price_source: hadPrice
            ? (current.price_source === "pengaturan_harga" ? "manual" : (current.price_source || "manual"))
            : current.price_source,
        };
        if (next.accurate_item_id && !current.price_review && !next.price_review) {
          quoteIdentity = {
            accurate_item_id: next.accurate_item_id,
            unit: next.unit,
            quantity: Number(next.quantity),
          };
        }
      } else if (field === "quantity") {
        const hadPrice = linePriceNumber(current.unit_price) !== null;
        if (hadPrice || current.price_lock || current.price_source === "pengaturan_harga") {
          next.price_review = true;
        } else if (!current.price_review && current.accurate_item_id) {
          quoteIdentity = {
            accurate_item_id: current.accurate_item_id,
            unit: current.unit,
            quantity: value,
          };
        }
      }
      lines[index] = next;
      return { ...prev, line_items: lines };
    });
    if (quoteIdentity) requestSellingQuote(index, quoteIdentity);
  };

  const handleLineAllocationsChange = (index, nextAllocations) => {
    dirtyRef.current = true;
    setFormData((prev) => {
      const lines = [...prev.line_items];
      if (!lines[index]) return prev;
      lines[index] = { ...lines[index], stock_allocations: nextAllocations };
      return { ...prev, line_items: lines };
    });
  };

  const handleSelectMasterItem = (index, master) => {
    let quoteIdentity = null;
    let openDetailKey = "";
    setFormData((prev) => {
      const lines = [...prev.line_items];
      const current = lines[index];
      if (!current) return prev;
      const hadPrice = linePriceNumber(current.unit_price) !== null;
      if (!master) {
        lines[index] = {
          ...current,
          name: "",
          item_code: "",
          accurate_item_id: null,
          master_item_id: null,
          item_type: "",
          stock_allocations: [],
          price_review: hadPrice || current.price_lock || current.price_source === "pengaturan_harga",
          price_source: hadPrice ? "manual" : "",
        };
      } else {
        const masterProcess = master.production_process === "non_print"
          ? "non_print"
          : master.production_process === "print"
            ? "print"
            : null;
        const masterType = String(master.item_type || "").trim().toUpperCase();
        const nextMasterId = master.id || master.master_item_id || null;
        const nextUnit = master.unit || current.unit || "pcs";
        const masterOrUnitChanged = (
          String(current.master_item_id || "") !== String(nextMasterId || "")
          || String(current.unit || "").toLowerCase() !== String(nextUnit || "").toLowerCase()
        );
        const next = {
          ...current,
          name: master.item_name || current.name,
          item_code: master.item_code || current.item_code,
          unit: nextUnit,
          accurate_item_id: master.accurate_item_id || null,
          master_item_id: nextMasterId,
          item_type: masterType || "",
          price_message: "",
          ...(masterOrUnitChanged ? { stock_allocations: [] } : {}),
          ...(masterProcess ? { production_process: masterProcess } : {}),
        };
        // Inventory / non-SERVICE lines are never Add On children.
        if (masterType && masterType !== "SERVICE") {
          next.parent_line_id = "";
        }
        if (hadPrice || current.price_lock) {
          next.price_review = true;
          if (next.price_source === "pengaturan_harga") next.price_source = "manual";
        } else if (!current.price_review && next.accurate_item_id) {
          quoteIdentity = {
            accurate_item_id: next.accurate_item_id,
            unit: next.unit,
            quantity: next.quantity,
          };
        } else if (!next.accurate_item_id) {
          // Local master: keep master_item_id, no auto-quote / no fake image id.
          next.price_review = true;
          next.price_message = "Barang belum terhubung ke Pengaturan Harga. Isi manual.";
        }
        lines[index] = next;
        // Surface warehouse allocation editor (lives in detail row) for inventory picks.
        if (isInventoryLineForAllocation(next)) {
          openDetailKey = String(next.rowKey || "").trim();
        }
      }
      return { ...prev, line_items: lines };
    });
    if (openDetailKey) setDetailRowKey(openDetailKey);
    if (quoteIdentity) requestSellingQuote(index, quoteIdentity);
  };

  const addLineItem = () => {
    setItemPage(Math.floor(formData.line_items.length / itemsPerPage) + 1);
    const line = createSalesOrderLineItem();
    focusLineKeyRef.current = line.rowKey;
    setDetailRowKey("");
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
    const removed = formData.line_items[index];
    const rowKey = removed?.rowKey;
    const removedLineId = String(removed?.line_id || removed?.rowKey || "").trim();
    setFormData((prev) => ({
      ...prev,
      line_items: prev.line_items
        .filter((_, i) => i !== index)
        .map((li) => {
          const parentId = String(li.parent_line_id || "").trim();
          if (removedLineId && parentId && parentId === removedLineId) {
            return { ...li, parent_line_id: "" };
          }
          return li;
        }),
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
      const next = lineAfterEntryModeChange(lines[index], nextMode);
      if (nextMode === "manual" && (next.price_source === "pengaturan_harga" || linePriceNumber(next.unit_price) !== null)) {
        next.price_source = linePriceNumber(next.unit_price) === null ? "" : "manual";
        next.price_review = true;
      }
      lines[index] = next;
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

  const itemPageCount = Math.max(1, Math.ceil(formData.line_items.length / itemsPerPage));
  const visibleItemPage = Math.min(itemPage, itemPageCount);
  const itemOffset = (visibleItemPage - 1) * itemsPerPage;

  const quantityParts = useMemo(
    () => quantityByUnitFromLines(formData.line_items),
    [formData.line_items],
  );
  const quantityLabel = formatQuantityByUnit(quantityParts);
  const sellingTotal = orderSellingTotal(formData.line_items);
  const sellingIncomplete = (formData.line_items || []).some((line) => linePriceNumber(line.unit_price) === null);
  const formProgress = useMemo(() => salesOrderProgress(formData), [formData]);
  const openSummaryEditor = (field) => {
    setEditingField(field);
    if (field === "customer_phone" || field === "address" || field === "expedition" || field === "notes") {
      setCreateTab("ship");
    }
    window.requestAnimationFrame(() => {
      const node = document.querySelector(
        `[data-so-field="${field}"] input, [data-so-field="${field}"] textarea, [data-so-field="${field}"] select`
      );
      node?.focus();
    });
  };
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
      await reloadOrderListAuthoritative();
    } catch (e) {
      setDeleteErr(formatDeleteError(e.response?.data?.detail));
    } finally {
      setDeleteLoading(false);
    }
  };

  const resetForm = () => {
    submitLockRef.current = false;
    setFormBusy(false);
    setFormSaved(false);
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
      sales_master_id: "",
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
    setCreateTab("items");
    setItemPage(1);
    setEditingField("");
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
    setItemPage(1);
    setRepeatSourceIndicator(sourcePoNumber || "");
    setRepeatConfirm(null);
    setRepeatMsg("");
    dirtyRef.current = true;
  };

  const resetFormRef = useRef(resetForm);
  const handleRepeatSpecificPoRef = useRef(handleRepeatSpecificPo);
  resetFormRef.current = resetForm;
  handleRepeatSpecificPoRef.current = handleRepeatSpecificPo;

  useEffect(() => {
    const rid = (searchParams.get(REPEAT_ORDER_QUERY_PARAM) || "").trim();
    if (!rid || !canCreate) return undefined;
    if (consumedRepeatOrderIdRef.current === rid) return undefined;
    consumedRepeatOrderIdRef.current = rid;
    resetFormRef.current();
    setViewMode("create");
    void handleRepeatSpecificPoRef.current(rid);
    return undefined;
  }, [searchParams, canCreate]);

  const handleFormSubmit = async (e) => {
    e?.preventDefault?.();
    if (submitLockRef.current || formBusy) return;
    setFormErr("");
    setSuccessMsg("");
    setAllocationNotice("");

    const validationError = validateSalesOrderCreate(formData);
    if (validationError) {
      setFormErr(validationError);
      return;
    }

    const softAllocWarnings = summarizeFormAllocationWarnings(formData.line_items);
    if (softAllocWarnings.length) {
      setAllocationNotice(
        softAllocWarnings.map((w) => `Baris #${(w.lineIndex || 0) + 1}: ${w.message}`).join(" · "),
      );
    }

    submitLockRef.current = true;
    setFormBusy(true);
    try {
      const payload = buildSalesOrderCreatePayload(formData);

      const res = await api.createSalesOrder(payload);
      const serverAlloc = formatAllocationWarningsList(res.allocation_warnings);
      const noticeParts = [
        softAllocWarnings.length
          ? softAllocWarnings.map((w) => `Baris #${(w.lineIndex || 0) + 1}: ${w.message}`).join(" · ")
          : "",
        serverAlloc,
      ].filter(Boolean);
      if (noticeParts.length) setAllocationNotice(noticeParts.join(" · "));
      setSuccessMsg(res.message || "Pesanan Penjualan berhasil dibuat!");
      const createdId = res.order?.id;
      // Saving has finished, but this form must never submit the same order again.
      // Keep the synchronous lock until resetForm starts a new order.
      setFormSaved(true);
      setFormBusy(false);
      setTimeout(() => {
        if (createdId) {
          invalidateSalesOrderListCache();
          navigate(`/po/${createdId}`);
        } else {
          setViewMode("list");
          void reloadOrderListAuthoritative();
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
  const openOrder = (order) => {
    onOpenPo(order.id);
    navigate(`/po/${order.id}`, {
      state: poListNavState({ po_number: order.po_number, customer: order.customer }),
    });
  };

  const changeSalesOrderTab = (tab) => {
    if (tab === listTab) return;
    setListTab(tab);
    setStatusFilter(salesOrderTabDefaultStatus(tab));
  };

  const onStatusChange = (value) => {
    // Toolbar HOLD is a process-label shortcut — does not remap five-tab.
    if (!value || value === "__tab__") {
      setStatusFilter(salesOrderTabDefaultStatus(listTab));
      setColumnFilters((prev) => ({
        ...prev,
        processLabels: (prev.processLabels || []).filter((label) => label !== "HOLD"),
      }));
      return;
    }
    if (value === "hold") {
      setStatusFilter("hold");
      setColumnFilters((prev) => {
        const current = Array.isArray(prev.processLabels) ? prev.processLabels : [];
        return {
          ...prev,
          processLabels: current.includes("HOLD") ? current : [...current, "HOLD"],
        };
      });
      return;
    }
    setStatusFilter(value);
  };

  const resetToolbar = () => {
    clearSearch();
    setPeriod("all");
    setColumnFilters({ ...EMPTY_SALES_ORDER_COLUMN_FILTERS });
    setStatusFilter(salesOrderTabDefaultStatus(listTab));
    setPage(1);
  };

  const resetColumnWidths = useCallback(() => {
    clearSalesOrderColWidths();
    setColWidths({ ...SALES_ORDER_DEFAULT_COL_WIDTHS });
  }, []);

  const onColResizePointerDown = useCallback((event, key) => {
    if (event.button != null && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const handleEl = event.currentTarget;
    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startW = colWidths[key] ?? SALES_ORDER_DEFAULT_COL_WIDTHS[key];
    try {
      handleEl.setPointerCapture(pointerId);
    } catch {
      /* ignore unsupported capture */
    }
    document.body.classList.add("so-col-resizing");
    handleEl.classList.add("is-active");

    const onMove = (moveEvent) => {
      if (moveEvent.pointerId !== pointerId) return;
      const nextW = clampSalesOrderColWidth(key, startW + (moveEvent.clientX - startX));
      setColWidths((prev) => {
        if (prev[key] === nextW) return prev;
        return { ...prev, [key]: nextW };
      });
    };
    const onUp = (upEvent) => {
      if (upEvent.pointerId !== pointerId) return;
      handleEl.removeEventListener("pointermove", onMove);
      handleEl.removeEventListener("pointerup", onUp);
      handleEl.removeEventListener("pointercancel", onUp);
      try {
        handleEl.releasePointerCapture(pointerId);
      } catch {
        /* already released */
      }
      handleEl.classList.remove("is-active");
      document.body.classList.remove("so-col-resizing");
      setColWidths((prev) => {
        writeSalesOrderColWidths(prev);
        return prev;
      });
      if (colResizeRef.current?.pointerId === pointerId) colResizeRef.current = null;
    };

    colResizeRef.current = { pointerId, handleEl, onMove, onUp };
    handleEl.addEventListener("pointermove", onMove);
    handleEl.addEventListener("pointerup", onUp);
    handleEl.addEventListener("pointercancel", onUp);
  }, [colWidths]);

  useEffect(() => () => {
    const active = colResizeRef.current;
    if (!active) return;
    active.handleEl.removeEventListener("pointermove", active.onMove);
    active.handleEl.removeEventListener("pointerup", active.onUp);
    active.handleEl.removeEventListener("pointercancel", active.onUp);
    document.body.classList.remove("so-col-resizing");
    colResizeRef.current = null;
  }, []);

  const tableMinWidth = useMemo(
    () => salesOrderTableMinWidth(colWidths, {
      includeCheck: canDelete,
      includeInvoice: canViewInvoice,
    }),
    [colWidths, canDelete, canViewInvoice],
  );

  return (
    <div
      className={viewMode === "create"
        ? "sales-order-page so-create-workspace ux90-so-create"
        : "sales-order-page penjualan-page penjualan-workspace so-list-workspace"}
      data-testid="sales-order-page"
      data-view={viewMode}
      data-scroll-owner={viewMode === "create" ? "workspace" : "list"}
    >
      {viewMode === "list" && (
        <div className="so-list" data-testid="sales-order-list">
          <header className="so-list-header">
            <div className="so-list-heading">
              <h1 data-testid="sales-order-title">Pesanan Penjualan</h1>
              {showAktifCount && (
                <div className="so-list-summary" data-testid="sales-order-aktif-count">
                  <strong>{Number(listMeta.total).toLocaleString("id-ID")}</strong>
                  <span>pesanan aktif</span>
                  {listRefreshing ? <span data-testid="sales-order-list-refreshing"> · menyegarkan</span> : null}
                </div>
              )}
              {!showAktifCount && listRefreshing ? (
                <div className="so-list-summary" data-testid="sales-order-list-refreshing">menyegarkan</div>
              ) : null}
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
                onClick={() => loadOrders({ force: true })}
                disabled={listLoading || listRefreshing}
                data-testid="sales-order-refresh-button"
                aria-label="Muat ulang"
              >
                <RefreshCw size={16} className={(listLoading || listRefreshing) ? "spin" : ""} />
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

          <div className="so-list-chrome">
            <div className="so-list-tabs" role="tablist" aria-label="Klasifikasi pesanan">
              {SALES_LIST_TABS.map((tab) => {
                const count = Number(listMeta.counts?.[tab.id] || 0);
                const selected = listTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    data-testid={`sales-order-tab-${tab.testIdSuffix}`}
                    className={selected ? "is-active" : ""}
                    onClick={() => changeSalesOrderTab(tab.id)}
                  >
                    {tab.label}
                    <span className="so-tab-count" data-testid={`sales-order-tab-count-${tab.testIdSuffix}`}>
                      {count.toLocaleString("id-ID")}
                    </span>
                  </button>
                );
              })}
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
                value={statusSelectValue === "hold" ? "hold" : "__tab__"}
                onChange={(e) => onStatusChange(e.target.value)}
                aria-label="Filter HOLD"
              >
                <option value="__tab__">Semua di tab ini</option>
                <option value="hold">Hanya HOLD</option>
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
                Reset Filter
              </button>
              <button
                type="button"
                className="so-list-reset so-list-reset-widths"
                data-testid="sales-order-reset-col-widths"
                onClick={resetColumnWidths}
              >
                Reset Lebar Kolom
              </button>
            </div>
          </div>

          {listErr && (
            <div className="so-list-error" data-testid="list-error-banner" role="alert">
              <AlertCircle size={16} />
              <span>{listErr}</span>
            </div>
          )}
          {listSyncErr && (
            <div className="so-list-error" data-testid="sales-order-list-sync-error" role="status">
              <AlertCircle size={16} />
              <span>
                {listSyncErr}
                {listShowingCache ? " Data di bawah belum disinkronkan ulang." : ""}
              </span>
            </div>
          )}

          <div className="so-list-table-scroll" data-testid="sales-order-table-scroll">
            <table className="so-list-table" style={{ minWidth: tableMinWidth }}>
              <colgroup>
                {canDelete && <col className="so-col-check" style={{ width: colWidths.check }} />}
                <col className="so-col-customer" style={{ width: colWidths.customer }} />
                <col className="so-col-date" style={{ width: colWidths.orderDate }} />
                <col className="so-col-date" style={{ width: colWidths.shipDate }} />
                <col className="so-col-qty" style={{ width: colWidths.qty }} />
                <col className="so-col-progress" style={{ width: colWidths.progress }} />
                <col className="so-col-status" style={{ width: colWidths.status }} />
                <col className="so-col-sales" style={{ width: colWidths.sales }} />
                <col className="so-col-creator" style={{ width: colWidths.creator }} />
                {canViewInvoice && <col className="so-col-invoice-status" style={{ width: colWidths.invoice }} />}
                <col className="so-col-actions" style={{ width: colWidths.actions }} />
              </colgroup>
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
                  <th className="so-th-resizable">
                    <HeaderFieldFilter
                      label="Pelanggan / No. PO"
                      testid="so-th-filter-customer"
                      active={salesOrderColumnFilterActive("customer", columnFilters)}
                      onClear={() => clearColumnFilter("customerQ")}
                      onApply={() => patchColumnFilter({ customerQ: customerFilterDraft })}
                    >
                      <label>
                        Cari pelanggan / No. PO / outlet
                        <input
                          type="text"
                          data-testid="so-filter-customer"
                          value={customerFilterDraft}
                          onChange={(e) => setCustomerFilterDraft(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              patchColumnFilter({ customerQ: customerFilterDraft });
                            }
                          }}
                          placeholder="Ketik untuk memfilter"
                        />
                      </label>
                    </HeaderFieldFilter>
                    <SoColResizeHandle colKey="customer" onPointerDown={onColResizePointerDown} />
                  </th>
                  <th className="so-th-resizable">
                    <HeaderFieldFilter
                      label="Tanggal PO"
                      testid="so-th-filter-order-date"
                      active={salesOrderColumnFilterActive("orderDate", columnFilters)}
                      error={
                        columnFilters.orderDateFrom
                        && columnFilters.orderDateTo
                        && columnFilters.orderDateFrom > columnFilters.orderDateTo
                          ? "Dari tidak boleh lebih besar dari Sampai"
                          : ""
                      }
                      onClear={() => clearColumnFilter(["orderDateFrom", "orderDateTo"])}
                    >
                      <label>
                        Dari
                        <input
                          type="date"
                          data-testid="so-filter-order-date-from"
                          value={columnFilters.orderDateFrom || ""}
                          onChange={(e) => patchColumnFilter({ orderDateFrom: e.target.value })}
                        />
                      </label>
                      <label>
                        Sampai
                        <input
                          type="date"
                          data-testid="so-filter-order-date-to"
                          value={columnFilters.orderDateTo || ""}
                          onChange={(e) => patchColumnFilter({ orderDateTo: e.target.value })}
                        />
                      </label>
                    </HeaderFieldFilter>
                    <SoColResizeHandle colKey="orderDate" onPointerDown={onColResizePointerDown} />
                  </th>
                  <th className="so-th-resizable">
                    <HeaderFieldFilter
                      label="Tanggal Kirim"
                      testid="so-th-filter-ship-date"
                      active={salesOrderColumnFilterActive("shipDate", columnFilters)}
                      error={
                        columnFilters.shipDateFrom
                        && columnFilters.shipDateTo
                        && columnFilters.shipDateFrom > columnFilters.shipDateTo
                          ? "Dari tidak boleh lebih besar dari Sampai"
                          : ""
                      }
                      onClear={() => clearColumnFilter(["shipDateFrom", "shipDateTo"])}
                    >
                      <label>
                        Dari
                        <input
                          type="date"
                          data-testid="so-filter-ship-date-from"
                          value={columnFilters.shipDateFrom || ""}
                          onChange={(e) => patchColumnFilter({ shipDateFrom: e.target.value })}
                        />
                      </label>
                      <label>
                        Sampai
                        <input
                          type="date"
                          data-testid="so-filter-ship-date-to"
                          value={columnFilters.shipDateTo || ""}
                          onChange={(e) => patchColumnFilter({ shipDateTo: e.target.value })}
                        />
                      </label>
                    </HeaderFieldFilter>
                    <SoColResizeHandle colKey="shipDate" onPointerDown={onColResizePointerDown} />
                  </th>
                  <th className="so-th-resizable">
                    <HeaderFieldFilter
                      label="Qty"
                      testid="so-th-filter-qty"
                      active={salesOrderColumnFilterActive("qty", columnFilters)}
                      error={
                        salesOrderColumnFilterValidation({
                          ...EMPTY_SALES_ORDER_COLUMN_FILTERS,
                          qtyMin: columnFilters.qtyMin,
                          qtyMax: columnFilters.qtyMax,
                        }) || ""
                      }
                      onClear={() => clearColumnFilter(["qtyMin", "qtyMax", "qtyUnit"])}
                    >
                      <label>
                        Minimum
                        <input
                          type="number"
                          min="0"
                          step="1"
                          data-testid="so-filter-qty-min"
                          value={columnFilters.qtyMin || ""}
                          onChange={(e) => patchColumnFilter({ qtyMin: e.target.value })}
                        />
                      </label>
                      <label>
                        Maksimum
                        <input
                          type="number"
                          min="0"
                          step="1"
                          data-testid="so-filter-qty-max"
                          value={columnFilters.qtyMax || ""}
                          onChange={(e) => patchColumnFilter({ qtyMax: e.target.value })}
                        />
                      </label>
                      <label>
                        Satuan
                        <select
                          data-testid="so-filter-qty-unit"
                          value={columnFilters.qtyUnit || ""}
                          onChange={(e) => patchColumnFilter({ qtyUnit: e.target.value })}
                        >
                          <option value="">Otomatis (PO satu satuan)</option>
                          {qtyUnitFilterOptions.map((unit) => (
                            <option key={unit} value={unit}>{unit}</option>
                          ))}
                        </select>
                      </label>
                    </HeaderFieldFilter>
                    <SoColResizeHandle colKey="qty" onPointerDown={onColResizePointerDown} />
                  </th>
                  <th className="so-th-resizable">
                    <HeaderSelectFilter
                      label="Pengiriman"
                      testid="so-th-filter-delivery"
                      options={SALES_ORDER_DELIVERY_STATUS_OPTIONS}
                      value={columnFilters.deliveryStatuses}
                      multi
                      onChange={(next) => patchColumnFilter({ deliveryStatuses: next })}
                    />
                    <SoColResizeHandle colKey="progress" onPointerDown={onColResizePointerDown} />
                  </th>
                  <th className="so-th-status so-th-resizable">
                    <HeaderSelectFilter
                      label="Proses"
                      testid="so-th-filter-stage"
                      options={processFilterOptions}
                      value={columnFilters.processLabels}
                      multi
                      onChange={(next) => {
                        patchColumnFilter({ processLabels: next });
                        setStatusFilter(
                          Array.isArray(next) && next.includes("HOLD")
                            ? "hold"
                            : salesOrderTabDefaultStatus(listTab),
                        );
                      }}
                    />
                    <SoColResizeHandle colKey="status" onPointerDown={onColResizePointerDown} />
                  </th>
                  <th className="so-th-sales so-th-resizable">
                    <HeaderSelectFilter
                      label="Sales"
                      testid="so-th-filter-sales"
                      options={salesFilterOptions}
                      value={columnFilters.sales || ""}
                      emptyValue=""
                      onChange={(next) => patchColumnFilter({ sales: next })}
                    />
                    <SoColResizeHandle colKey="sales" onPointerDown={onColResizePointerDown} />
                  </th>
                  <th className="so-th-resizable">
                    <HeaderSelectFilter
                      label="Dibuat Oleh"
                      testid="so-th-filter-creator"
                      options={creatorFilterOptions}
                      value={columnFilters.createdBy || ""}
                      emptyValue=""
                      onChange={(next) => patchColumnFilter({ createdBy: next })}
                    />
                    <SoColResizeHandle colKey="creator" onPointerDown={onColResizePointerDown} />
                  </th>
                  {canViewInvoice && (
                    <th className="so-th-resizable">
                      <HeaderSelectFilter
                        label="Status Faktur"
                        testid="so-th-filter-invoice"
                        options={SALES_ORDER_INVOICE_STATUS_OPTIONS}
                        value={columnFilters.invoiceStatuses}
                        multi
                        onChange={(next) => patchColumnFilter({ invoiceStatuses: next })}
                      />
                      <SoColResizeHandle colKey="invoice" onPointerDown={onColResizePointerDown} />
                    </th>
                  )}
                  <th className="so-th-resizable">
                    <span className="so-th-label-text">Aksi</span>
                    <SoColResizeHandle colKey="actions" onPointerDown={onColResizePointerDown} />
                  </th>
                </tr>
              </thead>
              <tbody>
                {listLoading ? (
                  <tr>
                    <td colSpan={(canDelete ? 10 : 9) + (canViewInvoice ? 1 : 0)} className="so-list-empty">Memuat daftar Pesanan Penjualan...</td>
                  </tr>
                ) : displayedOrders.length === 0 ? (
                  <tr>
                    <td colSpan={(canDelete ? 10 : 9) + (canViewInvoice ? 1 : 0)} className="so-list-empty" data-testid="empty-sales-orders">
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
                  const stageLabel = salesOrderStageLabel(o);
                  const remainQty = salesOrderRemainingQty(o);
                  const salesName = salesOrderSalesName(o).trim();
                  const canProcessDelivery = !isCancelled && remainQty > 0;
                  return (
                    <tr
                      key={o.id}
                      {...clickableDocumentRowProps({
                        onOpen: () => openOrder(o),
                        className: `so-list-row${selectedIds.has(o.id) ? " is-selected" : ""}`,
                        "data-testid": `order-row-${o.id}`,
                        ariaLabel: `Buka pesanan ${o.po_number || o.id}`,
                      })}
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
                      <td className="so-list-identity">
                        <div className="so-list-identity-stack">
                          <div className="so-list-customer" title={o.customer || undefined}>
                            {o.customer || "—"}
                          </div>
                          <Link
                            to={`/po/${o.id}`}
                            state={poListNavState({ po_number: o.po_number, customer: o.customer })}
                            onClick={(e) => {
                              e.stopPropagation();
                              onOpenPo(o.id);
                            }}
                            className="so-list-po"
                            data-testid={`po-link-${o.po_number}`}
                            title={o.po_number || undefined}
                          >
                            {o.po_number || "—"}
                          </Link>
                          <PoIdentityBadges order={o} testIdPrefix={`so-po-badges-${o.po_number || o.id}`} />
                          {o.outlet ? (
                            <span className="so-list-outlet" title={o.outlet}>{o.outlet}</span>
                          ) : null}
                        </div>
                      </td>
                      <td>{formatDesktopListDate(o.order_date) || "—"}</td>
                      <td>{formatDesktopListDate(o.tanggal_pengiriman) || "—"}</td>
                      <td data-testid={`order-qty-${o.id}`}>{orderQuantityLabel(o)}</td>
                      <td>
                        <SalesOrderDeliveryProgress order={o} />
                      </td>
                      <td className="so-list-status" data-testid={`order-stage-${o.id}`}>
                        {isCancelled ? (
                          <span className="so-list-badge is-cancel">{stageLabel || "—"}</span>
                        ) : isHold ? (
                          <span className="so-list-badge is-hold">{stageLabel || "—"}</span>
                        ) : (
                          <span className={`so-list-badge${o.current_stage >= 14 ? " is-done" : " is-open"}`}>{stageLabel || "—"}</span>
                        )}
                      </td>
                      <td className="so-list-sales" data-testid={`order-sales-${o.id}`} title={salesName || undefined}>
                        {salesName || "—"}
                      </td>
                      <td className="so-list-creator" data-testid={`order-creator-${o.id}`} title={orderCreatorLabel(o)}>
                        {orderCreatorLabel(o)}
                      </td>
                      {canViewInvoice ? (
                        <td className="so-list-invoice-status" onClick={(e) => e.stopPropagation()}>
                          <InvoiceStatusCell order={o} canOpen={canViewInvoice} />
                        </td>
                      ) : null}
                      <td onClick={(e) => e.stopPropagation()}>
                        <div className="so-list-actions">
                          <Link
                            to={`/po/${o.id}`}
                            state={poListNavState({ po_number: o.po_number, customer: o.customer })}
                            onClick={() => onOpenPo(o.id)}
                            className="so-act-view"
                            aria-label={`Lihat PO ${o.po_number}`}
                            data-testid={`btn-view-po-${o.id}`}
                          >
                            Detail <ArrowRight size={14} aria-hidden="true" />
                          </Link>
                          {((canCreate && !isCancelled) || canProcessDelivery) && (
                            <Popover>
                              <PopoverTrigger asChild>
                                <button type="button" className="so-act-more" aria-label={`Aksi pesanan ${o.po_number}`} data-testid={`btn-more-order-${o.id}`}>
                                  <MoreHorizontal size={18} />
                                </button>
                              </PopoverTrigger>
                              <PopoverContent align="end" className="so-order-actions-popover" sideOffset={6}>
                                {canCreate && !isCancelled && (
                                  <button
                                    type="button"
                                    className="so-act-repeat"
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
                                    className="so-act-ship"
                                    data-testid={`btn-process-delivery-${o.id}`}
                                    aria-label="+ Kirim"
                                  >
                                    <span className="so-act-plus" aria-hidden="true">+</span><span>Kirim</span>
                                  </Link>
                                )}
                              </PopoverContent>
                            </Popover>
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
          noValidate
          data-create-tab={createTab}
          data-form-tab={createTab}
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
                <span
                  data-testid="so-progress-label"
                  title={formProgress.customerAndGoodsOnly
                    ? "Hitungan ini hanya pelanggan dan barang. Harga kosong tidak masuk hitungan dan tetap diperingatkan."
                    : "Hitungan wajib: pelanggan, barang, nomor HP, dan alamat. Harga kosong tetap diperingatkan terpisah."}
                >
                  {formProgress.label}
                </span>
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

          <div className="so-create-stage" data-scroll-owner="form" data-testid="so-create-scroll">
            <div className="ux90-so-maincol" data-testid="so-create-maincol">
              <section className="so-create-main" data-so-region="main">
                <div className="ux90-txn-header ux90-so-txn-header so-sum-row" data-testid="so-main-fields">
                  <div className="so-sum-chip is-customer is-editing ux90-txn-field">
                    <SoField field="customer" label="Pelanggan" required error={fieldHasError("customer")} className="so-sum-field">
                      <div className="so-sum-editor">
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
                          customersLoading={optionsLoading || options.master_customers_state === "loading"}
                          loadError={
                            !optionsLoading && options.master_customers_state === "error"
                              ? CUSTOMER_OPTIONS_LOAD_ERROR
                              : ""
                          }
                          onRetryLoad={() => loadOptions({ applySuggestedPo: false })}
                        />
                      </div>
                    </SoField>
                  </div>

                  <div className="so-sum-chip is-po is-editing ux90-txn-field">
                    <SoField field="po_number" label="Nomor PO" className="so-sum-field">
                      <div className="so-sum-editor so-sum-editor-po">
                        <input
                          id="so-po_number"
                          type="text"
                          className="so-line-input"
                          value={formData.po_number}
                          onChange={(e) => setFormData((p) => ({ ...p, po_number: e.target.value }))}
                          placeholder="mis. PO/26/09/26/002"
                          data-testid="input-po-number"
                        />
                        <button type="button" className="so-create-auto" onClick={generatePoNumber} title="Buat Nomor Otomatis" data-testid="btn-generate-po">
                          <Sparkles size={14} /> Auto
                        </button>
                      </div>
                    </SoField>
                  </div>

                  <div className="so-sum-chip is-editing so-sum-chip-date ux90-txn-field">
                    <SoField field="order_date" label="Tanggal PO" className="so-sum-field">
                      <div className="so-sum-editor">
                        <SalesInvoiceDateField
                          valueIso={formData.order_date}
                          onChange={(iso) => setFormData((p) => ({ ...p, order_date: iso }))}
                          testId="input-order-date"
                          calendarLabel="Kalender tanggal PO"
                        />
                      </div>
                    </SoField>
                  </div>

                  {/* Hidden visual ship-card kept for existing editor hook / tests */}
                  <div className="so-ship-card" hidden aria-hidden="true">
                    <span className="so-ship-icon" aria-hidden="true"><Truck size={18} /></span>
                    <div>
                      <strong>Kirim {formatShipCardDate(formData.estimated_delivery) || "—"}</strong>
                      <span>{formData.expedition || "Ekspedisi"} · {formData.address || "Alamat belum diisi"}</span>
                    </div>
                    <button type="button" className="so-sum-pencil" data-testid="so-edit-delivery" aria-label="Ubah pengiriman" onClick={() => openSummaryEditor("expedition")}>
                      <Pencil size={14} />
                    </button>
                  </div>
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
                    Repeat dari PO: {repeatSourceIndicator}. Harga perlu diperiksa.
                  </div>
                ) : null}
                {repeatMsg ? (
                  <div className="so-create-note is-warn" data-testid="repeat-empty-message">{repeatMsg}</div>
                ) : null}

                <div className="ux90-so-doc-body">
                  <nav className="ux90-so-vtabs" role="tablist" aria-label="Bagian pesanan">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={createTab === "items"}
                      className={createTab === "items" ? "is-active" : ""}
                      data-testid="so-tab-items"
                      onClick={() => {
                        document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                        setCreateTab("items");
                      }}
                    >
                      <Package size={18} aria-hidden="true" />
                      <span>Rincian</span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={createTab === "info"}
                      className={createTab === "info" ? "is-active" : ""}
                      data-testid="so-tab-info"
                      onClick={() => {
                        document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                        setCreateTab("info");
                      }}
                    >
                      <Info size={18} aria-hidden="true" />
                      <span>Info</span>
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={createTab === "ship"}
                      className={createTab === "ship" ? "is-active" : ""}
                      data-testid="so-tab-ship"
                      onClick={() => {
                        document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                        setCreateTab("ship");
                        window.requestAnimationFrame(() => {
                          const phone = document.querySelector('[data-testid="input-customer-phone"]');
                          if (phone && typeof phone.focus === "function") phone.focus();
                        });
                      }}
                    >
                      <Truck size={18} aria-hidden="true" />
                      <span>Pengiriman</span>
                      {newCustomer && (!String(formData.customer_phone || "").trim() || !String(formData.address || "").trim()) ? (
                        <span className="ux90-req" title="HP/alamat wajib untuk pelanggan baru" data-testid="so-ship-required-hint">*</span>
                      ) : null}
                    </button>
                  </nav>

                  <div className="ux90-so-pane">
                    <div className="ux90-so-pane-panel ux90-so-pane-items so-create-lines so-items-only" data-so-region="lines" data-pane="items">
                      <div className="so-create-line-toolbar" data-testid="so-line-toolbar">
                        <div className="so-create-line-heading">
                          <h2>Rincian Barang <span className="so-req">*</span></h2>
                          <span data-testid="so-line-count">{formData.line_items.length} item</span>
                          <span data-testid="so-total-items">{formData.line_items.length} jenis</span>
                        </div>
                        <div className="so-create-line-actions">
                          <button
                            type="button"
                            className="so-create-repeat"
                            data-testid="btn-repeat-order"
                            onClick={handleRepeatOrderClick}
                            disabled={!formData.customer_id || repeatBusy || formBusy}
                            title={formData.customer_id ? "Salin detail dari PO valid terakhir customer ini" : "Pilih customer terdaftar terlebih dahulu"}
                          >
                            <RefreshCw size={15} aria-hidden="true" /> {repeatBusy ? "Mencari…" : "Repeat Order"}
                          </button>
                          <button type="button" className="so-create-add" onClick={addLineItem} data-testid="btn-add-line-item">
                            <Plus size={14} /> Tambah barang
                          </button>
                        </div>
                      </div>
                      <div className="so-create-lines-scroll" data-testid="line-items-table-container">
                        <table className="so-create-lines-table">
                          <thead>
                            <tr>
                              <th className="so-col-no" scope="col">No</th>
                              <th className="so-col-name">Produk <span className="so-req">*</span></th>
                              <th className="so-col-warna">Varian / Warna</th>
                              <th className="so-col-proses">Proses</th>
                              <th className="so-col-qty">Qty <span className="so-req">*</span></th>
                              <th className="so-col-unit">Satuan</th>
                              <th className="so-col-price">Harga Jual</th>
                              <th className="so-col-sub">Subtotal</th>
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
                            ) : formData.line_items.slice(itemOffset, itemOffset + itemsPerPage).map((line, pageIndex) => {
                              const originalIndex = itemOffset + pageIndex;
                              const imagePath = itemImages[String(line.accurate_item_id || "").trim()]?.url;
                              const subtotal = lineSellingSubtotal(line.quantity, line.unit_price);
                              const detailStillPresent = formData.line_items.some((item) => item.rowKey === detailRowKey);
                              const activeDetailKey = detailRowKey === ""
                                ? ""
                                : (detailStillPresent ? detailRowKey : formData.line_items[itemOffset]?.rowKey);
                              const detailOpen = activeDetailKey === line.rowKey;
                              const parentLabel = (() => {
                                const pid = String(line.parent_line_id || "").trim();
                                if (!pid) return "";
                                const parent = formData.line_items.find(
                                  (item) => String(item.line_id || item.rowKey || "").trim() === pid,
                                );
                                if (!parent) return "Induk Add On";
                                const pIdx = formData.line_items.indexOf(parent);
                                return `Induk #${pIdx + 1}`;
                              })();
                              const detailSummary = [
                                line.item_code,
                                String(line.item_type || "").toUpperCase() === "SERVICE" ? "Jasa Add On" : "",
                                line.production_process === "non_print" ? "Tidak Cetak" : "",
                                line.color,
                                parentLabel,
                                line.notes,
                              ].filter(Boolean).join(" · ");
                              return (
                                <Fragment key={line.rowKey}>
                                  <tr
                                    className="so-line-main"
                                    data-testid={`line-item-row-${originalIndex}`}
                                  >
                                    <td className="so-col-no" data-testid={`line-item-number-${originalIndex}`}>{originalIndex + 1}</td>
                                    <td className="so-col-name">
                                      <div className="so-line-product">
                                        <SalesOrderThumb src={imagePath ? publicApiUrl(imagePath) : ""} />
                                        <div className="so-line-product-body">
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
                                                  item_type: line.item_type || "",
                                                }
                                                : null
                                            }
                                            onChange={(master) => handleSelectMasterItem(originalIndex, master)}
                                            placeholder="Cari nama / kode barang…"
                                            fetchItems={(params) => api.salesCatalogProducts(params)}
                                          />
                                          <div className="so-line-code">{line.item_code || "Kode barang"}</div>
                                          <button
                                            type="button"
                                            className="so-line-detail-toggle"
                                            aria-expanded={detailOpen}
                                            data-testid={`btn-line-detail-${originalIndex}`}
                                            onClick={() => setDetailRowKey(detailOpen ? "" : line.rowKey)}
                                          >
                                            {detailOpen ? "Tutup detail" : "Detail"} <ChevronDown size={12} aria-hidden="true" />
                                          </button>
                                          {!detailOpen && (
                                            <div className="so-line-detail-summary" data-testid={`line-detail-summary-${originalIndex}`}>
                                              {detailSummary || "Kode, cetak, warna, catatan"}
                                            </div>
                                          )}
                                        </div>
                                      </div>
                                    </td>
                                    <td className="so-col-warna">
                                      <input
                                        type="text"
                                        className="so-line-input"
                                        value={line.color || ""}
                                        onChange={(e) => handleLineChange(originalIndex, "color", e.target.value)}
                                        placeholder="Warna"
                                        data-testid={`line-color-${originalIndex}`}
                                      />
                                    </td>
                                    <td className="so-col-proses">
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
                                    </td>
                                    <td className="so-col-qty">
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
                                    <td className="so-col-unit">
                                      <input
                                        type="text"
                                        className="so-line-input"
                                        value={line.unit || "pcs"}
                                        onChange={(e) => handleLineChange(originalIndex, "unit", e.target.value)}
                                        data-testid={`line-unit-${originalIndex}`}
                                      />
                                    </td>
                                    <td className="so-col-price">
                                      <input
                                        type="text"
                                        inputMode="decimal"
                                        className="so-line-input so-price-input"
                                        value={line.unit_price ?? ""}
                                        onChange={(e) => handleLineChange(originalIndex, "unit_price", e.target.value)}
                                        placeholder="Harga"
                                        data-testid={`line-price-${originalIndex}`}
                                      />
                                      {priceSourceLabel(line) !== "Isi harga jual" && <div className={`so-price-source${line.price_review || line.price_message ? " is-review" : ""}`} data-testid={`line-price-source-${originalIndex}`}>
                                        {priceSourceLabel(line)}
                                      </div>}
                                      {line.price_review && (
                                        <button type="button" className="so-price-review" data-testid={`btn-review-price-${originalIndex}`} onClick={() => reviewLinePrice(originalIndex)}>
                                          Pakai harga Pengaturan Harga
                                        </button>
                                      )}
                                    </td>
                                    <td className="so-col-sub" data-testid={`line-subtotal-${originalIndex}`}>
                                      {formatOrderRupiah(subtotal)}
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
                                  <tr
                                    className="so-line-detail"
                                    hidden={!detailOpen}
                                    data-open={detailOpen ? "true" : "false"}
                                    data-testid={`line-item-detail-${originalIndex}`}
                                  >
                                    <td />
                                    <td colSpan={8}>
                                      <div className="so-line-meta">
                                        <label>
                                          Kode Barang
                                          <input type="text" className="so-line-input" value={line.item_code || ""} onChange={(e) => handleLineChange(originalIndex, "item_code", e.target.value)} data-testid={`line-code-${originalIndex}`} />
                                        </label>
                                        <label>
                                          Barang induk (Add On)
                                          <select
                                            className="so-line-input"
                                            value={String(line.parent_line_id || "")}
                                            onChange={(e) => handleLineChange(originalIndex, "parent_line_id", e.target.value)}
                                            data-testid={`line-parent-${originalIndex}`}
                                            aria-label="Barang induk Add On"
                                          >
                                            <option value="">
                                              {String(line.item_type || "").toUpperCase() === "SERVICE"
                                                && (line.production_process || "print") !== "non_print"
                                                ? "— Wajib pilih induk (bukan Add On kosong) —"
                                                : "— Bukan Add On / tanpa induk —"}
                                            </option>
                                            {formData.line_items.map((candidate, candIdx) => {
                                              if (candIdx === originalIndex) return null;
                                              const candId = String(candidate.line_id || candidate.rowKey || "").trim();
                                              if (!candId) return null;
                                              if (String(candidate.item_type || "").toUpperCase() === "SERVICE") {
                                                return null;
                                              }
                                              const label = String(candidate.name || candidate.item_code || `Baris #${candIdx + 1}`).trim();
                                              return (
                                                <option key={candId} value={candId}>
                                                  #{candIdx + 1} — {label || "Tanpa nama"}
                                                </option>
                                              );
                                            })}
                                          </select>
                                          <span className="so-line-hint" data-testid={`line-addon-hint-${originalIndex}`}>
                                            {String(line.item_type || "").toUpperCase() === "SERVICE"
                                              ? "Add On jasa = pekerjaan warna tambahan (Warna 2/3/4), bukan barang fisik. Wajib pilih induk. Tidak menambah stok."
                                              : "Jika baris ini jasa Cetak (Add On), pilih induk di atas. Warna 1 dikerjakan pada line induk."}
                                          </span>
                                        </label>
                                        <label>
                                          Catatan Item
                                          <input type="text" className="so-line-input" value={line.notes || ""} onChange={(e) => handleLineChange(originalIndex, "notes", e.target.value)} placeholder="Catatan item" data-testid={`line-notes-${originalIndex}`} />
                                        </label>
                                        <div className="so-line-alloc-wrap">
                                          <StockAllocationEditor
                                            line={line}
                                            warehouses={(warehouses || []).filter((wh) => {
                                              const bid = String(formData.operational_branch_id || "").trim();
                                              if (!bid) return true;
                                              return String(wh.branch_id || "") === bid;
                                            })}
                                            onChange={(next) => handleLineAllocationsChange(originalIndex, next)}
                                            disabled={formBusy}
                                            testIdPrefix={`line-alloc-${originalIndex}`}
                                          />
                                        </div>
                                      </div>
                                    </td>
                                  </tr>
                                </Fragment>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                      {itemPageCount > 1 && (
                        <nav className="so-item-pager" aria-label="Halaman barang">
                          <span>Barang {itemOffset + 1}–{Math.min(itemOffset + itemsPerPage, formData.line_items.length)} dari {formData.line_items.length}</span>
                          <button type="button" data-testid="so-item-prev" disabled={visibleItemPage <= 1} onClick={() => setItemPage(visibleItemPage - 1)}>Sebelumnya</button>
                          <strong>{visibleItemPage} / {itemPageCount}</strong>
                          <button type="button" data-testid="so-item-next" disabled={visibleItemPage >= itemPageCount} onClick={() => setItemPage(visibleItemPage + 1)}>Berikutnya</button>
                        </nav>
                      )}
                      {sellingIncomplete ? (
                        <p className="ux90-warn-box" data-testid="so-side-price-warning" role="status">
                          Terdapat barang dengan harga belum diisi. Harga kosong diperbolehkan.
                        </p>
                      ) : null}
                      {allocationNotice ? (
                        <p className="ux90-warn-box" data-testid="so-allocation-warning" role="status">
                          {allocationNotice}
                        </p>
                      ) : null}
                      <div className="ux90-doc-summary" data-testid="so-create-side-panel" aria-label="Ringkasan pesanan" hidden>
                        <div className="ux90-doc-summary-rows">
                          <div>
                            <span>Total Qty</span>
                            <strong data-testid="so-side-total-qty">{quantityLabel}</strong>
                          </div>
                          <div>
                            <span>Sub Total</span>
                            <strong data-testid="so-side-order-total">{formatOrderRupiah(sellingTotal)}</strong>
                          </div>
                          <div className="is-total">
                            <span>Total</span>
                            <strong data-testid="so-doc-total">{formatOrderRupiah(sellingTotal)}</strong>
                          </div>
                        </div>
                        <span hidden data-testid="so-side-est-delivery">{formatShipCardDate(formData.estimated_delivery) || "—"}</span>
                        <span hidden data-testid="so-side-payment-terms">{formData.payment_terms?.trim() || "—"}</span>
                        <span hidden data-testid="so-side-customer">{formData.customer?.trim() || "Belum dipilih"}{formData.outlet ? ` · ${formData.outlet}` : ""}</span>
                        <span hidden data-testid="so-side-customer-phone">{formData.customer_phone?.trim() || "HP belum diisi"}</span>
                        <span hidden data-testid="so-side-customer-address">{formData.address?.trim() || "Alamat belum diisi"}</span>
                        <span hidden data-testid="so-side-ship-plan">{formatShipCardDate(formData.estimated_delivery) || "Tanggal belum diisi"} · {formData.expedition || "Ekspedisi belum diisi"}</span>
                        <button
                          type="button"
                          className="ux90-btn-ghost"
                          style={{ marginTop: 8, height: 32, fontSize: 12 }}
                          data-testid="so-side-edit-customer"
                          onClick={() => {
                            document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
                            setCreateTab("ship");
                            window.requestAnimationFrame(() => {
                              const phone = document.querySelector('[data-testid="input-customer-phone"]');
                              if (phone && typeof phone.focus === "function") phone.focus();
                            });
                          }}
                        >
                          Edit pengiriman
                        </button>
                      </div>
                    </div>

                    <div className="ux90-so-pane-panel ux90-so-pane-info so-create-extra so-info-only" data-so-region="drawer" data-testid="so-extra-fields" data-pane="info">
                      <div className="ux90-so-pane-title">
                        <h3>Informasi Lainnya</h3>
                        {newCustomer ? (
                          <p className="ux90-hint" data-testid="so-new-customer-ship-note">
                            Pelanggan baru: No. HP dan Alamat wajib diisi pada tab Pengiriman sebelum menyimpan.
                          </p>
                        ) : null}
                      </div>
                      <div className="ux90-doc-info-grid ux90-so-info-grid">
                        <SoField field="sales" label="Sales" error={fieldHasError("sales")}>
                          <LookupSearchSelect
                            options={options.sales_master_catalog || []}
                            value={formData.sales_master_id || ""}
                            onChange={(sid, hit) => {
                              setFormData((p) => ({
                                ...p,
                                sales_master_id: sid || "",
                                sales: hit?.sales_name || "",
                              }));
                            }}
                            getOptionValue={(s) => s.id}
                            getOptionLabel={(s) => s.sales_name || ""}
                            getSearchText={(s) => s.sales_name || ""}
                            placeholder="Ketik nama Sales dari Master…"
                            noResultLabel="Tidak ada Sales aktif yang cocok"
                            testId="input-sales"
                            disabled={optionsLoading}
                          />
                          {optionsLoading ? (
                            <p className="ux90-hint" data-testid="so-sales-catalog-loading">Memuat daftar Sales…</p>
                          ) : null}
                          {!optionsLoading && options.sales_catalog_state === "error" ? (
                            <p className="ux90-hint" data-testid="so-sales-catalog-error">
                              Daftar Sales gagal dimuat. Tidak memakai input nama bebas.
                            </p>
                          ) : null}
                          {!optionsLoading && options.sales_catalog_state === "ready" && !(options.sales_master_catalog || []).length ? (
                            <p className="ux90-hint" data-testid="so-sales-catalog-empty">
                              Tidak ada Sales aktif untuk cabang Anda. Lengkapi Master Sales di Pengaturan Sales.
                              Input nama bebas tidak tersedia.
                            </p>
                          ) : null}
                        </SoField>

                        <OperationalBranchSelect value={formData.operational_branch_id} onChange={(id) => setFormData((prev) => ({ ...prev, operational_branch_id: id }))} />
                        <SoField field="outlet" label="Outlet Pelanggan" error={fieldHasError("outlet")}>
                          {outletsForSelectedCustomer.length > 0 ? (
                            <LookupSearchSelect
                              options={[
                                ...outletsForSelectedCustomer,
                                ...(formData.outlet && !outletsForSelectedCustomer.some((o) => o.outlet === formData.outlet)
                                  ? [{ id: formData.outlet, outlet: formData.outlet }]
                                  : []),
                              ]}
                              value={formData.outlet || ""}
                              onChange={(id) => handleOutletChange(id || "")}
                              getOptionValue={(o) => o.outlet || o.id || ""}
                              getOptionLabel={(o) => o.outlet || "Outlet"}
                              getSearchText={(o) => [o.outlet, o.city, o.address].filter(Boolean).join(" ")}
                              placeholder="Cari outlet / cabang…"
                              noResultLabel="Tidak ada hasil yang cocok"
                              testId="input-outlet"
                            />
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

                        <SoField field="payment_terms" label="Termin Pembayaran" error={fieldHasError("payment_terms")}>
                          <input
                            id="so-payment_terms"
                            type="text"
                            className="so-line-input"
                            value={formData.payment_terms}
                            onChange={(e) => setFormData((p) => ({ ...p, payment_terms: e.target.value }))}
                            placeholder="mis. Cash, Net 14, Net 30, COD"
                            data-testid="input-payment-terms"
                            aria-invalid={fieldHasError("payment_terms") || undefined}
                          />
                        </SoField>

                        <SoField field="estimated_delivery" label="Estimasi Pengiriman" error={fieldHasError("estimated_delivery")}>
                          <SalesInvoiceDateField
                            valueIso={formData.estimated_delivery}
                            onChange={(iso) => setFormData((p) => ({ ...p, estimated_delivery: iso }))}
                            testId="input-estimated-delivery"
                            calendarLabel="Kalender estimasi pengiriman"
                          />
                        </SoField>

                        <SoField field="created_by_user" label="User / Pembuat PO" error={fieldHasError("created_by_user")}>
                          <input
                            id="so-created_by_user"
                            type="text"
                            className="so-line-input"
                            value={formData.created_by_user}
                            onChange={(e) => setFormData((p) => ({ ...p, created_by_user: e.target.value }))}
                            placeholder="Nama user pembuat"
                            data-testid="input-created-by-user"
                            aria-invalid={fieldHasError("created_by_user") || undefined}
                          />
                        </SoField>

                        <SoField field="notes" label="Catatan Pesanan" error={fieldHasError("notes")} className="ux90-so-notes-field">
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
                      </div>
                    </div>

                    <div className="ux90-so-pane-panel ux90-so-pane-ship so-ship-panel so-ship-only" data-pane="ship">
                      <div className="ux90-so-pane-title">
                        <h3>Info Pengiriman</h3>
                      </div>
                      <div className="ux90-doc-info-grid ux90-so-ship-grid">
                        <SoField field="deadline" label="Tanggal Pengiriman" error={fieldHasError("deadline")}>
                          <SalesInvoiceDateField
                            valueIso={formData.deadline}
                            onChange={(iso) => setFormData((p) => ({ ...p, deadline: iso }))}
                            testId="input-tanggal-pengiriman"
                            calendarLabel="Kalender tanggal pengiriman"
                          />
                        </SoField>

                        <SoField field="recipient" label="Penerima (PIC)" error={fieldHasError("recipient")}>
                          <input
                            id="so-recipient"
                            type="text"
                            className="so-line-input"
                            value={formData.recipient}
                            onChange={(e) => setFormData((p) => ({ ...p, recipient: e.target.value }))}
                            placeholder="mis. Ibu Rina"
                            data-testid="input-recipient"
                            aria-invalid={fieldHasError("recipient") || undefined}
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

                        {newCustomer ? (
                          <>
                            <SoField field="customer_email" label="Email Pelanggan">
                              <input
                                id="so-customer_email"
                                type="email"
                                className="so-line-input"
                                value={formData.customer_email || ""}
                                onChange={(e) => setFormData((p) => ({ ...p, customer_email: e.target.value }))}
                                placeholder="opsional"
                                data-testid="input-customer-email"
                              />
                            </SoField>
                            <SoField field="customer_city" label="Kota">
                              <input
                                id="so-customer_city"
                                type="text"
                                className="so-line-input"
                                value={formData.customer_city || ""}
                                onChange={(e) => setFormData((p) => ({ ...p, customer_city: e.target.value }))}
                                placeholder="opsional"
                                data-testid="input-customer-city"
                              />
                            </SoField>
                          </>
                        ) : null}

                        <SoField field="address" label="Alamat Lengkap Pengiriman" required={newCustomer} error={fieldHasError("address")} className="so-create-field-address">
                          <textarea
                            id="so-address"
                            className="so-line-input so-create-address"
                            value={formData.address}
                            onChange={(e) => setFormData((p) => ({ ...p, address: e.target.value }))}
                            placeholder="Alamat jalan, gedung, nomor, patokan"
                            rows={3}
                            data-testid="input-address"
                            aria-invalid={fieldHasError("address") || undefined}
                          />
                        </SoField>

                        <SoField field="expedition" label="Ekspedisi / Pengiriman" error={fieldHasError("expedition")}>
                          <input
                            id="so-expedition"
                            type="text"
                            className="so-line-input"
                            value={formData.expedition}
                            onChange={(e) => setFormData((p) => ({ ...p, expedition: e.target.value }))}
                            placeholder="mis. Internal fleet"
                            data-testid="input-expedition"
                            aria-invalid={fieldHasError("expedition") || undefined}
                          />
                        </SoField>

                        <SoField field="delivery_zone" label="Zona Kirim" error={fieldHasError("delivery_zone")}>
                          <input
                            id="so-delivery_zone"
                            type="text"
                            className="so-line-input"
                            value={formData.delivery_zone}
                            onChange={(e) => setFormData((p) => ({ ...p, delivery_zone: e.target.value }))}
                            placeholder="mis. Jakarta Selatan"
                            data-testid="input-delivery-zone"
                            aria-invalid={fieldHasError("delivery_zone") || undefined}
                          />
                        </SoField>
                      </div>
                    </div>
                  </div>
                </div>
              </section>

              <div className="so-order-summary so-ship-only" data-testid="so-order-summary" hidden aria-hidden="true">
                {/* Legacy hook: notes live in Info tab; keep node for older test contracts */}
                <SoField field="notes" label="Catatan Pesanan" error={fieldHasError("notes")}>
                  <textarea
                    id="so-notes-legacy"
                    className="so-line-input so-create-address"
                    value={formData.notes}
                    onChange={(e) => setFormData((p) => ({ ...p, notes: e.target.value }))}
                    placeholder="Catatan khusus pengerjaan, packing, atau instruksi"
                    rows={3}
                    data-testid="input-notes-legacy"
                  />
                </SoField>
              </div>
            </div>
          </div>

          <div className="so-create-actionbar" data-testid="so-create-actionbar">
            <div className="so-footer-totals">
              <div>
                <span>Total Qty</span>
                <strong data-testid="so-total-qty">{quantityLabel}</strong>
              </div>
              <div>
                <span>Total Pesanan</span>
                <strong data-testid="so-order-total">{formatOrderRupiah(sellingTotal)}</strong>
              </div>
            </div>
            <div className="so-footer-actions">
              <button type="button" data-testid="btn-cancel-form" disabled={formBusy} onClick={leaveCreate}>
                Batal
              </button>
              <button type="submit" className="so-create-save" disabled={formBusy || formSaved} data-testid="btn-submit-sales-order">
                {formBusy ? "Menyimpan..." : formSaved ? "Tersimpan" : "Simpan Pesanan"}
              </button>
            </div>
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
                  {formatDesktopListDate(repeatConfirm.source?.source_order_date || repeatConfirm.source?.source_created_at) || "—"}
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
