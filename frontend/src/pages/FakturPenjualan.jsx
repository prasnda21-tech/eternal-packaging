import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  CreditCard,
  FileText,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";
import { addSalesInvoiceCalendarDays, formatSalesInvoiceDate, salesInvoiceDateToIso } from "../lib/salesInvoiceDate";
import SalesInvoiceDateField from "../components/SalesInvoiceDateField";
import HeaderSelectFilter from "../components/HeaderSelectFilter";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";
import { INVOICE_DATE_PRESET_OPTIONS, invoiceDatePresetRange } from "../lib/salesDatePresets";
import { clickableDocumentRowProps } from "../lib/clickableListRow";
import { useDebouncedLiveSearch } from "../hooks/useDebouncedLiveSearch";
import {
  buildSalesInvoiceListCacheKey,
  clearSalesInvoiceListCache,
  getSalesInvoiceListCache,
  getSalesInvoiceListScroll,
  invalidateSalesInvoiceListCache,
  isSalesInvoiceListCacheFresh,
  setSalesInvoiceListCache,
  setSalesInvoiceListScroll,
  shouldRevalidateSalesInvoiceListOnFocus,
} from "../lib/salesInvoiceListCache";
import {
  SALES_LIST_DEFAULT_PAGE_SIZE,
  SALES_LIST_PAGE_SIZES,
  SALES_LIST_TABS,
  normalizeSalesListTab,
  parseSalesListPageSize,
  salesFiveTabListMetaFromResponse,
  salesFiveTabListParams,
  salesListTabLabel,
} from "../lib/salesFiveTabs";
import {
  canBusinessCancelInvoice,
  canShowInvoiceCancelUi,
  canShowInvoiceEditUi,
  canRecoverAutoAccounting,
  clearIdempotencyIntent,
  ensureIdempotencyIntent,
  interpretAutoLifecycleResponse,
  interpretBusinessCancelResponse,
  lifecycleChipClass,
  salesDocumentLifecyclePresentation,
  shouldHideLegacyManualAccounting,
} from "../lib/salesLifecycleUi";
import "./salesSubmenuDensity.css";

function getTodayString() {
  const d = new Date();
  return d.toISOString().split("T")[0];
}

function formatRupiah(num) {
  const n = Number(num) || 0;
  return "Rp " + n.toLocaleString("id-ID");
}

const OTHER_CHARGE_ACCOUNT_TYPES = new Set(["EXPS", "OEXP", "OINC", "REVE"]);
const DRAFT_QTY_OVER_MESSAGE = "Qty Faktur melebihi sisa Qty Pesanan Penjualan.";
const FINALIZE_QTY_OVER_MESSAGE = "Qty Faktur melebihi sisa Qty Pesanan Penjualan. Sesuaikan Qty Faktur sebelum Finalisasi.";

function chargeAccountEligible(account) {
  if (!account || !account.id) return false;
  const status = String(account.status || account.account_status || "").trim().toLowerCase();
  if (["inactive", "nonaktif", "disabled", "non-aktif"].includes(status)) return false;
  if (account.is_active === false || account.active === false) return false;
  if (account.is_header === true || account.header === true) return false;
  if (account.postable === false || account.can_post === false) return false;
  return OTHER_CHARGE_ACCOUNT_TYPES.has(String(account.account_type || "").trim().toUpperCase());
}

function chargeAccountLabel(account) {
  if (!account) return "";
  return [account.code || account.account_code, account.name || account.account_name].filter(Boolean).join(" · ");
}

let otherChargeKeySeq = 0;
function blankOtherCharge() {
  otherChargeKeySeq += 1;
  return {
    key: `other-charge-${otherChargeKeySeq}`,
    id: "",
    account_id: "",
    account_code: "",
    account_name: "",
    description: "",
    amount: "",
  };
}

function otherChargeFromStored(row) {
  otherChargeKeySeq += 1;
  return {
    key: row.id || `other-charge-${otherChargeKeySeq}`,
    id: row.id || "",
    account_id: row.account_id || "",
    account_code: row.account_code || "",
    account_name: row.account_name || "",
    description: row.description || "",
    amount: row.amount === 0 || row.amount ? row.amount : "",
  };
}

function otherChargeRowMessage(row, index) {
  const n = index + 1;
  const missing = [];
  if (!String(row?.account_id || "").trim()) missing.push("akun perkiraan");
  if (!String(row?.description || "").trim()) missing.push("keterangan");
  const raw = row?.amount;
  const blank = raw === "" || raw === null || raw === undefined;
  if (blank) missing.push("nominal");
  if (missing.length) {
    return `Baris biaya ${n} belum lengkap: ${missing.join(", ")} wajib diisi.`;
  }
  const amount = Number(String(raw).replace(",", "."));
  if (!Number.isFinite(amount)) return `Baris biaya ${n}: nominal tidak valid.`;
  if (amount < 0) return `Baris biaya ${n}: nominal tidak boleh negatif.`;
  if (amount === 0) return `Baris biaya ${n}: nominal harus lebih besar dari 0.`;
  return "";
}

function summarizeOtherCharges(rows) {
  let total = 0;
  let message = "";
  (rows || []).forEach((row, index) => {
    const rowMessage = otherChargeRowMessage(row, index);
    if (rowMessage) {
      if (!message) message = rowMessage;
      return;
    }
    total += Number(String(row.amount).replace(",", "."));
  });
  return {
    total: Math.round(total * 100) / 100,
    incomplete: Boolean(message),
    message,
  };
}

function otherChargePayload(rows) {
  return (rows || []).map((row) => ({
    id: row.id || undefined,
    account_id: String(row.account_id || "").trim(),
    description: String(row.description || "").trim(),
    amount: row.amount === "" || row.amount === null || row.amount === undefined ? "" : row.amount,
  }));
}

function ChargeAccountSearch({ accounts, row, onSelect, disabled, testId }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const selected = (accounts || []).find((account) => account.id === row.account_id);
  const label = selected
    ? chargeAccountLabel(selected)
    : chargeAccountLabel({ code: row.account_code, name: row.account_name });
  const needle = query.trim().toLowerCase();
  const matches = (accounts || []).filter((account) => {
    if (!chargeAccountEligible(account) && account.id !== row.account_id) return false;
    if (!needle) return true;
    return `${account.code || ""} ${account.name || ""}`.toLowerCase().includes(needle);
  }).slice(0, 12);

  return (
    <div className="fp-account-pick">
      <input
        type="text"
        value={open ? query : label}
        disabled={disabled}
        placeholder="Cari kode atau nama akun"
        data-testid={testId}
        onFocus={() => {
          if (disabled) return;
          setOpen(true);
          setQuery("");
        }}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onBlur={() => {
          window.setTimeout(() => setOpen(false), 150);
        }}
      />
      {open && !disabled && (
        <ul className="fp-account-results" data-testid={`${testId}-results`}>
          {matches.length === 0 ? (
            <li className="is-empty">Tidak ada akun yang cocok</li>
          ) : matches.map((account) => (
            <li key={account.id}>
              <button
                type="button"
                onMouseDown={(event) => {
                  event.preventDefault();
                  onSelect(account);
                  setOpen(false);
                  setQuery("");
                }}
              >
                {chargeAccountLabel(account)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function OtherChargesEditor({ rows, accounts, disabled, showErrors, onChange, idPrefix }) {
  return (
    <div className="fp-charges" data-testid={`${idPrefix}-other-charges`}>
      <div className="fp-charges-head">
        <strong>Biaya Lainnya</strong>
        <button
          type="button"
          data-testid={`${idPrefix}-add-other-charge`}
          disabled={disabled}
          onClick={() => onChange([...(rows || []), blankOtherCharge()])}
        >
          <Plus size={14} /> Tambah Biaya
        </button>
      </div>
      <p className="fp-charges-note">Ditagihkan ke pelanggan. Saat posting, nominal dikredit ke akun yang dipilih.</p>
      {(rows || []).length === 0 ? (
        <div className="fp-charges-empty">Belum ada biaya lainnya.</div>
      ) : (rows || []).map((row, index) => {
        const message = showErrors ? otherChargeRowMessage(row, index) : "";
        return (
          <div key={row.key} className="fp-charge-row" data-testid={`${idPrefix}-other-charge-${index}`}>
            <label>
              Akun Perkiraan
              <ChargeAccountSearch
                accounts={accounts}
                row={row}
                disabled={disabled}
                testId={`${idPrefix}-charge-account-${index}`}
                onSelect={(account) => onChange((rows || []).map((item) => (
                  item.key === row.key
                    ? {
                      ...item,
                      account_id: account.id,
                      account_code: account.code || "",
                      account_name: account.name || "",
                    }
                    : item
                )))}
              />
            </label>
            <label>
              Keterangan biaya
              <input
                type="text"
                value={row.description}
                disabled={disabled}
                placeholder="Keterangan untuk pelanggan"
                data-testid={`${idPrefix}-charge-description-${index}`}
                onChange={(event) => onChange((rows || []).map((item) => (
                  item.key === row.key ? { ...item, description: event.target.value } : item
                )))}
              />
            </label>
            <label>
              Nominal
              <input
                type="number"
                min="0"
                step="any"
                value={row.amount}
                disabled={disabled}
                data-testid={`${idPrefix}-charge-amount-${index}`}
                onChange={(event) => onChange((rows || []).map((item) => (
                  item.key === row.key ? { ...item, amount: event.target.value } : item
                )))}
              />
            </label>
            <button
              type="button"
              className="fp-charge-remove"
              data-testid={`${idPrefix}-remove-other-charge-${index}`}
              disabled={disabled}
              onClick={() => onChange((rows || []).filter((item) => item.key !== row.key))}
            >
              Hapus
            </button>
            {message && <div className="fp-charge-error" data-testid={`${idPrefix}-charge-error-${index}`}>{message}</div>}
          </div>
        );
      })}
    </div>
  );
}

function invoiceFinancialStatus(inv) {
  const s = String(inv?.financial_status || "draft").toLowerCase();
  if (s === "finalized" || s === "cancelled") return s;
  return "draft";
}

function accountingStatusOf(inv) {
  return String(inv?.accounting_status || "not_posted").toLowerCase();
}

function renderAccountingBadge(status) {
  const s = String(status || "not_posted").toLowerCase();
  const map = {
    posted: { label: "Posted", bg: "#d1fae5", color: "#065f46", border: "#a7f3d0" },
    reversed: { label: "Reversed", bg: "#ede9fe", color: "#5b21b6", border: "#ddd6fe" },
    not_posted: { label: "Belum Diposting", bg: "#f8fafc", color: "#475569", border: "#cbd5e1" },
  };
  const t = map[s] || map.not_posted;
  return (
    <span
      className="pj-status-badge"
      data-testid={`invoice-accounting-badge-${s === "not_posted" ? "not-posted" : s}`}
      style={{
        display: "inline-block",
        padding: "3px 10px",
        borderRadius: 9999,
        fontSize: 11,
        fontWeight: 700,
        backgroundColor: t.bg,
        color: t.color,
        border: `1.5px solid ${t.border}`,
        marginLeft: 6,
      }}
    >
      {t.label}
    </span>
  );
}

function receiptAccountingLabel(status) {
  const s = String(status || "not_posted").toLowerCase();
  if (s === "posted") return "Posted";
  if (s === "reversed") return "Reversed";
  return "Belum Diposting";
}

function renderFinancialBadge(status) {
  const s = invoiceFinancialStatus({ financial_status: status });
  const map = {
    draft: { label: "Draft", bg: "#f1f5f9", color: "#334155", border: "#cbd5e1" },
    finalized: { label: "Finalized", bg: "#dbeafe", color: "#1e40af", border: "#93c5fd" },
    cancelled: { label: "Cancelled", bg: "#fee2e2", color: "#991b1b", border: "#fecaca" },
  };
  const t = map[s];
  return (
    <span
      className="pj-status-badge"
      data-testid={`invoice-financial-badge-${s}`}
      style={{
        display: "inline-block",
        padding: "3px 10px",
        borderRadius: 9999,
        fontSize: 11,
        fontWeight: 700,
        backgroundColor: t.bg,
        color: t.color,
        border: `1.5px solid ${t.border}`,
      }}
    >
      {t.label}
    </span>
  );
}

function formatDeleteError(det) {
  if (typeof det === "object" && det) {
    const blockers = det.blockers || [];
    if (blockers.length) {
      const lines = blockers.map((b) => `${b.number || b.po_number || b.id}: ${b.reason}`).join(" ");
      return `${det.message || "Tidak dapat menghapus."} ${lines}`.trim();
    }
    return det.message || JSON.stringify(det);
  }
  return det || "Gagal menghapus.";
}

function deleteBlockerIds(det, fallbackIds) {
  const blockers = det && typeof det === "object" && Array.isArray(det.blockers) ? det.blockers : [];
  const ids = blockers.map((blocker) => blocker.id).filter(Boolean);
  return ids.length ? ids : fallbackIds;
}

const EMPTY_INVOICE_COLUMN_FILTERS = {
  datePreset: "all",
  financialStatus: "",
  accountingStatus: "",
  paymentStatus: "",
};

const INVOICE_PAGE_SIZES = [...SALES_LIST_PAGE_SIZES];
const INVOICE_FINANCIAL_FILTER_VALUES = new Set(["", "draft", "finalized", "cancelled"]);
const INVOICE_ACCOUNTING_FILTER_VALUES = new Set(["", "not_posted", "posted", "reversed"]);
const INVOICE_PAYMENT_FILTER_VALUES = new Set(["", "BELUM DIBAYAR", "DP", "LUNAS"]);
const INVOICE_DATE_PRESET_VALUES = new Set(INVOICE_DATE_PRESET_OPTIONS.map((opt) => opt.value));

const INVOICE_POSTING_OPTIONS = [
  { value: "", label: "Semua" },
  { value: "not_posted", label: "Belum diposting" },
  { value: "posted", label: "Sudah diposting" },
  { value: "reversed", label: "Dibalik" },
];

const INVOICE_PAYMENT_OPTIONS = [
  { value: "", label: "Semua" },
  { value: "BELUM DIBAYAR", label: "Belum dibayar" },
  { value: "DP", label: "DP" },
  { value: "LUNAS", label: "Lunas" },
];

function invoiceFiltersActive(filters, query) {
  if (String(query || "").trim()) return true;
  const f = filters || EMPTY_INVOICE_COLUMN_FILTERS;
  if (f.datePreset && f.datePreset !== "all") return true;
  if (String(f.financialStatus || "").trim()) return true;
  if (String(f.accountingStatus || "").trim()) return true;
  if (String(f.paymentStatus || "").trim()) return true;
  return false;
}

function invoiceColumnFiltersFromSearchParams(params) {
  const datePresetRaw = String(params.get("date_preset") || "all").trim() || "all";
  const datePreset = INVOICE_DATE_PRESET_VALUES.has(datePresetRaw) ? datePresetRaw : "all";
  const financialStatus = String(params.get("financial_status") || "").trim();
  const accountingStatus = String(params.get("accounting_status") || "").trim();
  const paymentStatus = String(params.get("payment_status") || "").trim().toUpperCase();
  return {
    datePreset,
    financialStatus: INVOICE_FINANCIAL_FILTER_VALUES.has(financialStatus) ? financialStatus : "",
    accountingStatus: INVOICE_ACCOUNTING_FILTER_VALUES.has(accountingStatus) ? accountingStatus : "",
    paymentStatus: INVOICE_PAYMENT_FILTER_VALUES.has(paymentStatus) ? paymentStatus : "",
  };
}

function listIdentityUserId(user) {
  return user?.id || user?.email || user?.sub || "";
}

function listIdentityBranchId(user) {
  return user?.branch_id || user?.active_branch_id || user?.branchId || "";
}

function invoiceDeletable(inv, { allowBusinessCancel = false } = {}) {
  if (allowBusinessCancel) {
    const progress = String(inv?.business_cancel_progress || "").toLowerCase();
    if (progress === "complete" || progress === "completed") return false;
    if (String(inv?.financial_status || "").toLowerCase() === "cancelled") return false;
    if (String(inv?.list_tab || "").toLowerCase() === "cancelled") return false;
    return true;
  }
  return inv?.can_delete === true;
}

function invoiceListParams({ searchQuery, listTab, columnFilters, page, pageSize }) {
  const range = invoiceDatePresetRange(columnFilters.datePreset || "all");
  return salesFiveTabListParams({
    listTab,
    page,
    pageSize,
    q: searchQuery,
    extra: {
      date_from: range.date_from || undefined,
      date_to: range.date_to || undefined,
      financial_status: columnFilters.financialStatus || undefined,
      accounting_status: columnFilters.accountingStatus || undefined,
      payment_status: columnFilters.paymentStatus || undefined,
    },
  });
}

function LifecycleStatusChip({ doc, testId }) {
  const presentation = salesDocumentLifecyclePresentation(doc || {});
  if (!presentation) return null;
  return (
    <span
      className={`sd-lifecycle-chip ${lifecycleChipClass(presentation.tone)}`}
      data-testid={testId}
      title={presentation.reason || presentation.label}
    >
      {presentation.label}
      {presentation.reason ? ` · ${presentation.reason}` : ""}
    </span>
  );
}

function invoiceFinancialLabel(status) {
  const s = invoiceFinancialStatus({ financial_status: status });
  if (s === "finalized") return "Final";
  if (s === "cancelled") return "Dibatalkan";
  return "Draft";
}

function invoicePostingLabel(status) {
  const s = String(status || "not_posted").toLowerCase();
  if (s === "posted") return "Sudah diposting";
  if (s === "reversed") return "Dibalik";
  return "Belum diposting";
}

function invoicePaymentLabel(status) {
  const s = String(status || "BELUM DIBAYAR").toUpperCase();
  if (s === "LUNAS") return "Lunas";
  if (s === "DP") return "DP";
  return "Belum dibayar";
}

function invoiceRangeLabel({ total, tabTotal, page, pageSize, tabLabel, filtered }) {
  const safeTotal = Number(total) || 0;
  const safeTab = Number(tabTotal) || 0;
  const start = safeTotal === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, safeTotal);
  const fmt = (value) => Number(value || 0).toLocaleString("id-ID");
  if (!filtered) {
    if (safeTab <= 0) return `Menampilkan 0 faktur pada tab ${tabLabel}`;
    return `Menampilkan ${fmt(start)}–${fmt(end)} dari ${fmt(safeTab)} faktur pada tab ${tabLabel}`;
  }
  const result = safeTotal <= 0
    ? "Menampilkan 0 hasil filter"
    : `Menampilkan ${fmt(start)}–${fmt(end)} dari ${fmt(safeTotal)} hasil filter`;
  return `${result} · ${fmt(safeTab)} faktur pada tab ${tabLabel}`;
}

export default function FakturPenjualan() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, hasPerm } = useAuth();

  const canCreate =
    hasPerm("sales.invoice.create") ||
    hasPerm("sales_order.create") ||
    hasPerm("po.edit") ||
    user?.role === "admin";
  const canEdit =
    hasPerm("sales.invoice.edit") ||
    hasPerm("sales.invoice.create") ||
    hasPerm("sales_order.create") ||
    hasPerm("po.edit") ||
    user?.role === "admin";
  const canDelete =
    hasPerm("sales.invoice.delete") ||
    hasPerm("sales.invoice.create") ||
    hasPerm("po.edit") ||
    user?.role === "admin";
  const canCancelBusiness = canBusinessCancelInvoice(hasPerm);
  const canPrint =
    hasPerm("sales.invoice.view") ||
    hasPerm("sales_order.view") ||
    hasPerm("po.view") ||
    hasPerm("sales.delivery.view") ||
    user?.role === "admin";

  const queryPoId = searchParams.get("po_id") || "";
  const queryInvoiceId = searchParams.get("invoice_id") || "";
  const initialMode =
    searchParams.get("action") === "create" || Boolean(queryPoId) ? "create" : "list";
  const [viewMode, setViewMode] = useState(initialMode);

  // List state
  const [invoices, setInvoices] = useState([]);
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
  const [listTab, setListTab] = useState(() => normalizeSalesListTab(searchParams.get("tab") || "active"));
  const [page, setPage] = useState(() => {
    const value = Number(searchParams.get("page"));
    return Number.isFinite(value) && value >= 1 ? Math.floor(value) : 1;
  });
  const [pageSize, setPageSize] = useState(() => parseSalesListPageSize(
    searchParams.get("page_size"),
    { fallback: SALES_LIST_DEFAULT_PAGE_SIZE },
  ));
  const [columnFilters, setColumnFilters] = useState(() => invoiceColumnFiltersFromSearchParams(searchParams));
  const [listMeta, setListMeta] = useState({
    total: 0,
    tabTotal: 0,
    capped: false,
    counts: { all: 0, active: 0, completed: 0, pending: 0, cancelled: 0 },
  });
  const requestSeq = useRef(0);
  const loadAbortRef = useRef(null);
  const tableScrollRef = useRef(null);
  const activeListCacheKeyRef = useRef("");
  const lastScrollTopRef = useRef(0);
  const pendingScrollRestoreKeyRef = useRef("");
  const skipPageResetRef = useRef(true);
  const createIdempotencyRef = useRef({ signature: "", key: "" });
  const editIdempotencyRef = useRef({ signature: "", key: "" });
  const cancelIdempotencyRef = useRef({ signature: "", key: "" });
  const listUserId = listIdentityUserId(user);
  const listBranchId = listIdentityBranchId(user);

  // Checkbox selection
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deleteConfirm, setDeleteConfirm] = useState(null); // {id, number} or {ids: [...], numbers: [...]}
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");
  const [deleteHold, setDeleteHold] = useState(false);
  const [deleteBlockedIds, setDeleteBlockedIds] = useState([]);

  // Create state
  const [availablePOs, setAvailablePOs] = useState([]);
  const [loadingPOs, setLoadingPOs] = useState(false);
  const [selectedPoId, setSelectedPoId] = useState(queryPoId);
  const [previewData, setPreviewData] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewErr, setPreviewErr] = useState("");

  const [invoiceDate, setInvoiceDate] = useState(getTodayString());
  const createDateRef = useRef(null);
  const [paymentTermDays, setPaymentTermDays] = useState(0);
  const [discountAmount, setDiscountAmount] = useState(0);
  const [ppnPercent, setPpnPercent] = useState(0);
  const [invoiceNotes, setInvoiceNotes] = useState("");
  const [otherCharges, setOtherCharges] = useState([]);
  const [chargeAccounts, setChargeAccounts] = useState([]);
  const [showChargeErrors, setShowChargeErrors] = useState(false);
  const [lineQuantities, setLineQuantities] = useState({});
  const [linePrices, setLinePrices] = useState({});
  const [formBusy, setFormBusy] = useState(false);
  const [formErr, setFormErr] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  // Detail / Edit state
  const [selectedInvoiceId, setSelectedInvoiceId] = useState(null);
  const [detailData, setDetailData] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailErr, setDetailErr] = useState("");
  const [detailPrices, setDetailPrices] = useState({});
  const [detailQuantities, setDetailQuantities] = useState({});
  const [detailDiscount, setDetailDiscount] = useState(0);
  const [detailPpn, setDetailPpn] = useState(0);
  const [detailTermDays, setDetailTermDays] = useState(0);
  const [detailDate, setDetailDate] = useState("");
  const detailDateRef = useRef(null);
  const [detailNotes, setDetailNotes] = useState("");
  const [detailCharges, setDetailCharges] = useState([]);
  const [showDetailChargeErrors, setShowDetailChargeErrors] = useState(false);
  const [detailSaving, setDetailSaving] = useState(false);
  const [finalizeConfirm, setFinalizeConfirm] = useState(false);
  const [cancelConfirm, setCancelConfirm] = useState(false);
  const [postAccountingConfirm, setPostAccountingConfirm] = useState(false);
  const [reverseConfirm, setReverseConfirm] = useState(false);
  const [reverseReason, setReverseReason] = useState("");
  const [reverseErr, setReverseErr] = useState("");
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [createLifecycleCapability, setCreateLifecycleCapability] = useState(null);
  const [invoiceFormTab, setInvoiceFormTab] = useState("items");

  const clearInvoiceSelection = useCallback(() => {
    setSelectedIds(new Set());
    setDeleteConfirm(null);
    setDeleteErr("");
    setDeleteHold(false);
    setDeleteBlockedIds([]);
  }, []);

  const resetInvoiceListPosition = useCallback(() => {
    setPage(1);
    clearInvoiceSelection();
  }, [clearInvoiceSelection]);

  const rememberTableScroll = useCallback(() => {
    // While a restore is in flight, ignore transient scrollTop=0 from remounts
    // so we do not clobber the saved offset for this query key.
    if (pendingScrollRestoreKeyRef.current) return;
    const key = activeListCacheKeyRef.current;
    const node = tableScrollRef.current;
    if (!key) return;
    const top = node ? node.scrollTop : lastScrollTopRef.current;
    if (node) lastScrollTopRef.current = node.scrollTop;
    setSalesInvoiceListScroll(key, top);
  }, []);

  const restoreTableScroll = useCallback((key) => {
    if (!key) return;
    pendingScrollRestoreKeyRef.current = key;
    const top = getSalesInvoiceListScroll(key);
    if (!(top > 0)) {
      pendingScrollRestoreKeyRef.current = "";
      return;
    }
    const apply = () => {
      const node = tableScrollRef.current;
      if (!node || pendingScrollRestoreKeyRef.current !== key) return false;
      // Wait until the table can actually scroll; otherwise the browser clamps to 0.
      if (node.scrollHeight <= node.clientHeight + 1) return false;
      node.scrollTop = top;
      const landed = node.scrollTop;
      lastScrollTopRef.current = landed;
      // Require a real land; remounts can leave scrollTop at 0 after a false success.
      return landed > 0 && Math.abs(landed - top) <= Math.max(4, top * 0.05);
    };
    const schedule = (attempt) => {
      window.requestAnimationFrame(() => {
        if (pendingScrollRestoreKeyRef.current !== key) return;
        if (apply()) {
          // Confirm one more frame later in case row remount resets scrollTop.
          window.requestAnimationFrame(() => {
            if (pendingScrollRestoreKeyRef.current !== key) return;
            if (apply()) pendingScrollRestoreKeyRef.current = "";
            else if (attempt < 8) schedule(attempt + 1);
          });
          return;
        }
        if (attempt < 8) schedule(attempt + 1);
      });
    };
    if (apply()) {
      schedule(0);
      return;
    }
    schedule(0);
  }, []);

  const applyInvoiceListPayload = useCallback((rows, meta, { fromCache = false } = {}) => {
    setInvoices(rows);
    setListMeta(meta);
    setListShowingCache(Boolean(fromCache));
    if (!fromCache) setListSyncErr("");
  }, []);

  // Load Invoices List — stale-while-revalidate when cache hits; blank only for cold queries.
  const loadInvoices = useCallback(async ({ force = false } = {}) => {
    const cacheKey = buildSalesInvoiceListCacheKey({
      userId: listUserId,
      branchId: listBranchId,
      listTab,
      q: searchQuery,
      columnFilters,
      page,
      pageSize,
    });
    rememberTableScroll();
    activeListCacheKeyRef.current = cacheKey;

    const cached = getSalesInvoiceListCache(cacheKey);
    const hasCache = Boolean(cached);
    const fresh = isSalesInvoiceListCacheFresh(cached);

    if (hasCache) {
      applyInvoiceListPayload(cached.invoices, cached.listMeta, { fromCache: true });
      restoreTableScroll(cacheKey);
      clearInvoiceSelection();
      if (!force && fresh) {
        setListLoading(false);
        setListRefreshing(false);
        setListErr("");
        return;
      }
    } else {
      // Never show rows from a different query while waiting for a cold fetch.
      applyInvoiceListPayload([], {
        total: 0,
        tabTotal: 0,
        capped: false,
        counts: { all: 0, active: 0, completed: 0, pending: 0, cancelled: 0 },
      }, { fromCache: false });
    }

    const seq = requestSeq.current + 1;
    requestSeq.current = seq;
    loadAbortRef.current?.abort();
    const ac = new AbortController();
    loadAbortRef.current = ac;
    if (hasCache) {
      setListRefreshing(true);
      setListLoading(false);
    } else {
      setListLoading(true);
      setListRefreshing(false);
    }
    setListErr("");
    setSelectedIds(new Set());
    try {
      const params = invoiceListParams({ searchQuery, listTab, columnFilters, page, pageSize });
      const res = await api.listSalesInvoices(params, { signal: ac.signal });
      if (seq !== requestSeq.current || ac.signal.aborted) return;
      const rows = res.invoices || [];
      const meta = salesFiveTabListMetaFromResponse(res, { rowCount: rows.length });
      setSalesInvoiceListCache(cacheKey, { invoices: rows, listMeta: meta, res });
      applyInvoiceListPayload(rows, meta, { fromCache: false });
      restoreTableScroll(cacheKey);
    } catch (e) {
      if (seq !== requestSeq.current || ac.signal.aborted || e?.code === "ERR_CANCELED" || e?.name === "CanceledError") {
        return;
      }
      const det = e.response?.data?.detail;
      const message = typeof det === "object"
        ? det.message
        : det || "Gagal memuat daftar Faktur Penjualan";
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
    applyInvoiceListPayload,
    clearInvoiceSelection,
    columnFilters,
    listBranchId,
    listTab,
    listUserId,
    page,
    pageSize,
    rememberTableScroll,
    restoreTableScroll,
    searchQuery,
  ]);

  const reloadInvoiceListAuthoritative = useCallback(async () => {
    invalidateSalesInvoiceListCache();
    setListShowingCache(false);
    setListSyncErr("");
    await loadInvoices({ force: true });
  }, [loadInvoices]);

  // Checkbox helpers — current page, drafts that the backend marks deletable
  const pageDeletableIds = invoices
    .filter((inv) => invoiceDeletable(inv, { allowBusinessCancel: canCancelBusiness }))
    .map((inv) => inv.id);
  const toggleSelect = (inv) => {
    if (!invoiceDeletable(inv, { allowBusinessCancel: canCancelBusiness })) return;
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(inv.id)) next.delete(inv.id);
      else next.add(inv.id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    if (pageDeletableIds.length > 0 && pageDeletableIds.every((id) => selectedIds.has(id))) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(pageDeletableIds));
    }
  };

  const changeInvoiceTab = (tab) => {
    const next = normalizeSalesListTab(tab);
    if (next === listTab) return;
    setListTab(next);
    setColumnFilters((prev) => ({
      ...prev,
      financialStatus: next === "cancelled"
        ? (prev.financialStatus === "cancelled" ? "cancelled" : "")
        : (prev.financialStatus === "draft" || prev.financialStatus === "finalized" ? prev.financialStatus : ""),
    }));
    resetInvoiceListPosition();
  };

  const patchColumnFilter = (patch) => {
    setColumnFilters((prev) => ({ ...prev, ...patch }));
    resetInvoiceListPosition();
  };

  const resetColumnFilters = () => {
    setColumnFilters(EMPTY_INVOICE_COLUMN_FILTERS);
    clearSearch();
    resetInvoiceListPosition();
  };

  const openDeleteConfirm = (payload) => {
    setDeleteErr("");
    setDeleteHold(false);
    setDeleteBlockedIds([]);
    setDeleteConfirm(payload);
  };

  const rejectDeleteConfirm = (det, fallbackIds) => {
    setDeleteErr(formatDeleteError(det) || "Gagal menghapus faktur.");
    setDeleteBlockedIds(deleteBlockerIds(det, fallbackIds));
    setDeleteHold(true);
  };

  const removeDeleteCandidate = (id) => {
    setDeleteConfirm((prev) => {
      if (!prev?.ids) return prev;
      const items = prev.ids.map((itemId, index) => ({
        id: itemId,
        number: prev.numbers?.[index],
      })).filter((item) => item.id !== id);
      return { ids: items.map((item) => item.id), numbers: items.map((item) => item.number) };
    });
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  };

  // Hapus: business-cancel when opt-in cancel perm; else legacy delete. No silent fallthrough.
  const handleDelete = async (id) => {
    setDeleteLoading(true);
    setDeleteErr("");
    try {
      if (canCancelBusiness) {
        const key = ensureIdempotencyIntent(cancelIdempotencyRef, `cancel-invoice|${id}`, { prefix: "inv-cancel" });
        const res = await api.businessCancelSalesInvoice(id, {
          reason: "Batalkan dari daftar Faktur Penjualan",
          idempotency_key: key,
        });
        const interpreted = interpretBusinessCancelResponse(res);
        if (!interpreted.ok) {
          setDeleteErr(interpreted.message);
          // Keep modal + same idempotency key. Refresh list only to surface Pending status.
          if (interpreted.pending) reloadInvoiceListAuthoritative();
          return;
        }
        clearIdempotencyIntent(cancelIdempotencyRef);
        setDeleteConfirm(null);
        setDeleteHold(false);
        setDeleteBlockedIds([]);
        reloadInvoiceListAuthoritative();
        return;
      }
      await api.deleteSalesInvoice(id);
      setDeleteConfirm(null);
      setDeleteHold(false);
      setDeleteBlockedIds([]);
      reloadInvoiceListAuthoritative();
    } catch (e) {
      const statusCode = e.response?.status;
      const det = e.response?.data?.detail;
      if (canCancelBusiness) {
        const interpreted = interpretBusinessCancelResponse({}, { httpErrorBody: det, statusCode });
        setDeleteErr(interpreted.message);
        // Fail closed: do not fall through to legacy hard delete.
        return;
      }
      rejectDeleteConfirm(det, [id]);
    } finally {
      setDeleteLoading(false);
    }
  };

  // Bulk delete

  const [pdfLoadingId, setPdfLoadingId] = useState(null);

  const handlePrintPdf = async (e, invoiceId) => {
    if (e) e.stopPropagation();
    if (!invoiceId) return;
    setPdfLoadingId(invoiceId);
    try {
      const blob = await api.downloadSalesInvoicePdf(invoiceId);
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank");
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (err) {
      const det = err.response?.data?.detail;
      const msg = typeof det === "object" ? det?.message || JSON.stringify(det) : det || "Gagal memuat PDF Faktur Penjualan.";
      setListErr(msg);
      if (viewMode === "detail") setDetailErr(msg);
    } finally {
      setPdfLoadingId(null);
    }
  };

  const handleBulkDelete = async () => {
    const ids = deleteConfirm?.ids || Array.from(selectedIds);
    setDeleteLoading(true);
    setDeleteErr("");
    try {
      if (canCancelBusiness) {
        for (const id of ids) {
          const key = ensureIdempotencyIntent(cancelIdempotencyRef, `cancel-invoice|${id}`, { prefix: "inv-cancel" });
          const res = await api.businessCancelSalesInvoice(id, {
            reason: "Batalkan terpilih dari daftar Faktur Penjualan",
            idempotency_key: key,
          });
          const interpreted = interpretBusinessCancelResponse(res);
          if (!interpreted.ok) {
            setDeleteErr(interpreted.message);
            if (interpreted.pending) reloadInvoiceListAuthoritative();
            return;
          }
          clearIdempotencyIntent(cancelIdempotencyRef);
        }
        setDeleteConfirm(null);
        setDeleteHold(false);
        setDeleteBlockedIds([]);
        setSelectedIds(new Set());
        reloadInvoiceListAuthoritative();
        return;
      }
      await api.bulkDeleteSalesInvoices(ids);
      setDeleteConfirm(null);
      setDeleteHold(false);
      setDeleteBlockedIds([]);
      setSelectedIds(new Set());
      reloadInvoiceListAuthoritative();
    } catch (e) {
      const statusCode = e.response?.status;
      const det = e.response?.data?.detail;
      if (canCancelBusiness) {
        const interpreted = interpretBusinessCancelResponse({}, { httpErrorBody: det, statusCode });
        setDeleteErr(interpreted.message);
        return;
      }
      rejectDeleteConfirm(det, ids);
    } finally {
      setDeleteLoading(false);
    }
  };

  // Load Available POs for selection
  const loadAvailablePOs = useCallback(async () => {
    if (!canCreate) return;
    setLoadingPOs(true);
    try {
      const res = await api.listSalesOrders({ status: "aktif" });
      setAvailablePOs((res.orders || []).filter((o) => !o.cancelled));
    } catch {
      // Best effort load
    } finally {
      setLoadingPOs(false);
    }
  }, [canCreate]);

  // Load PO preview when creating invoice from PO
  const loadPoPreview = useCallback(async (poId) => {
    if (!poId) {
      setPreviewData(null);
      return;
    }
    setPreviewLoading(true);
    setPreviewErr("");
    setFormErr("");
    try {
      const data = await api.getPoInvoicePreview(poId);
      setPreviewData(data);
      const initialPrices = {};
      const initialQtys = {};
      (data.lines || []).forEach((l) => {
        initialPrices[l.line_idx] = l.unit_price === null || l.unit_price === undefined || l.unit_price === "" ? "" : l.unit_price;
        initialQtys[l.line_idx] = l.remaining_invoice_qty != null ? l.remaining_invoice_qty : l.quantity;
      });
      setLinePrices(initialPrices);
      setLineQuantities(initialQtys);
    } catch (e) {
      const det = e.response?.data?.detail;
      setPreviewErr(
        typeof det === "object"
          ? det.message
          : det || "Gagal memuat data PO untuk Faktur"
      );
    } finally {
      setPreviewLoading(false);
    }
  }, []);

  useEffect(() => {
    const nextCount = Math.max(1, Math.ceil((Number(listMeta.total) || 0) / pageSize) || 1);
    if (page > nextCount) setPage(nextCount);
  }, [page, pageSize, listMeta.total]);

  useEffect(() => {
    if (!deleteHold || !deleteConfirm) return;
    const ids = deleteConfirm.ids || (deleteConfirm.id ? [deleteConfirm.id] : []);
    if (!ids.length) return;
    const stillBlocked = ids.some((id) => deleteBlockedIds.includes(id));
    if (!stillBlocked) {
      setDeleteHold(false);
      setDeleteErr("");
      setDeleteBlockedIds([]);
    }
  }, [deleteHold, deleteConfirm, deleteBlockedIds]);

  useEffect(() => {
    if (viewMode === "list") {
      loadInvoices();
    } else if (viewMode === "create") {
      loadAvailablePOs();
      if (selectedPoId) {
        loadPoPreview(selectedPoId);
      }
      api.salesInvoiceLifecycleCapability()
        .then((res) => setCreateLifecycleCapability(res?.lifecycle_capability || res || null))
        .catch(() => setCreateLifecycleCapability(null));
    }
  }, [viewMode, loadInvoices, loadAvailablePOs, loadPoPreview, selectedPoId]);

  // Settled search / filter changes reset page — not every keystroke.
  useEffect(() => {
    if (skipPageResetRef.current) {
      skipPageResetRef.current = false;
      return;
    }
    setPage(1);
    clearInvoiceSelection();
  }, [searchQuery, listTab, pageSize, columnFilters, clearInvoiceSelection]);

  // Sync list presentation into URL so workspace remount restores the same view.
  useEffect(() => {
    if (viewMode !== "list") return;
    if (searchParams.get("action") === "create") return;
    if (searchParams.get("invoice_id") || searchParams.get("po_id")) return;
    const next = new URLSearchParams();
    if (searchQuery.trim()) next.set("q", searchQuery.trim());
    if (listTab && listTab !== "active") next.set("tab", listTab);
    if (page > 1) next.set("page", String(page));
    if (pageSize !== SALES_LIST_DEFAULT_PAGE_SIZE) next.set("page_size", String(pageSize));
    if (columnFilters.datePreset && columnFilters.datePreset !== "all") {
      next.set("date_preset", columnFilters.datePreset);
    }
    if (columnFilters.financialStatus) next.set("financial_status", columnFilters.financialStatus);
    if (columnFilters.accountingStatus) next.set("accounting_status", columnFilters.accountingStatus);
    if (columnFilters.paymentStatus) next.set("payment_status", columnFilters.paymentStatus);
    const nextStr = next.toString();
    if (nextStr === searchParams.toString()) return;
    setSearchParams(next, { replace: true });
  }, [
    searchQuery,
    listTab,
    page,
    pageSize,
    columnFilters,
    viewMode,
    searchParams,
    setSearchParams,
  ]);

  // Drop financial cache when session identity changes (same user after re-login handled in auth logout).
  const prevListUserRef = useRef(listUserId);
  useEffect(() => {
    if (prevListUserRef.current === listUserId) return;
    clearSalesInvoiceListCache();
    prevListUserRef.current = listUserId;
  }, [listUserId]);

  useEffect(() => {
    if (viewMode !== "list") return undefined;
    const onVisible = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      if (!shouldRevalidateSalesInvoiceListOnFocus()) return;
      loadInvoices({ force: false });
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [viewMode, loadInvoices]);

  useEffect(() => () => {
    rememberTableScroll();
  }, [rememberTableScroll]);

  // Retry scroll restore after rows paint / SWR refresh when first attempt ran early.
  useEffect(() => {
    if (viewMode !== "list" || listLoading || !invoices.length) return undefined;
    const key = pendingScrollRestoreKeyRef.current;
    if (!key) return undefined;
    const id = window.requestAnimationFrame(() => {
      restoreTableScroll(key);
    });
    return () => window.cancelAnimationFrame(id);
  }, [viewMode, listLoading, listRefreshing, invoices, restoreTableScroll]);

  useEffect(() => {
    if (viewMode === "list") return undefined;
    let cancelled = false;
    api.salesInvoiceChargeAccounts()
      .then((res) => {
        if (!cancelled) setChargeAccounts(res.items || []);
      })
      .catch(() => {
        if (!cancelled) setChargeAccounts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [viewMode]);

  // Handle PO selector change
  const handleSelectPo = (e) => {
    const pId = e.target.value;
    setSelectedPoId(pId);
    setOtherCharges([]);
    setShowChargeErrors(false);
    if (pId) {
      setSearchParams({ action: "create", po_id: pId });
      loadPoPreview(pId);
    } else {
      setSearchParams({ action: "create" });
      setPreviewData(null);
    }
  };

  // Due Date calculation helper
  const calculatedDueDate = useMemo(() => {
    const term = Math.max(0, Number(paymentTermDays) || 0);
    const baseDateStr = invoiceDate;
    if (!baseDateStr) return "";
    return addSalesInvoiceCalendarDays(baseDateStr, term);
  }, [invoiceDate, paymentTermDays]);

  // Live calculation of line totals and grand total for Create form
  const createCalculations = useMemo(() => {
    if (!previewData || !previewData.lines) {
      return { lines: [], subtotal: 0, discount: 0, afterDiscount: 0, ppn: 0, grandTotal: 0, totalQty: 0, hasErrors: false };
    }
    let totalQty = 0;
    let subtotal = 0;
    let hasErrors = false;

    const computedLines = previewData.lines.map((l) => {
      const remaining = Number(l.remaining_invoice_qty != null ? l.remaining_invoice_qty : l.quantity) || 0;
      const rawQ = lineQuantities[l.line_idx];
      const q = rawQ !== undefined && rawQ !== "" ? Math.max(0, Number(rawQ) || 0) : remaining;
      const isQtyOver = q > remaining;
      if (isQtyOver) hasErrors = true;

      const rawP = linePrices[l.line_idx];
      const priceMissing = rawP === "" || rawP === null || rawP === undefined || Number.isNaN(Number(rawP));
      const p = priceMissing ? null : Math.max(0, Number(rawP));
      const lineTotal = p === null ? null : q * p;
      totalQty += q;
      if (lineTotal !== null) subtotal += lineTotal;
      if (q > 0 && priceMissing) hasErrors = true;
      return {
        ...l,
        remaining,
        invoiceQty: rawQ !== undefined ? rawQ : remaining,
        numericQty: q,
        unitPrice: priceMissing ? "" : rawP,
        numericPrice: p === null ? 0 : p,
        priceMissing,
        lineTotal,
        isQtyOver,
      };
    });

    const disc = Math.max(0, Number(discountAmount) || 0);
    const isDiscOver = disc > subtotal;
    if (isDiscOver) hasErrors = true;

    const afterDiscount = Math.max(0, subtotal - disc);
    const ppnPct = Math.max(0, Number(ppnPercent) || 0);
    const ppn = Math.round(afterDiscount * (ppnPct / 100));
    const chargeSummary = summarizeOtherCharges(otherCharges);
    const grandTotal = chargeSummary.incomplete ? null : afterDiscount + ppn + chargeSummary.total;

    const totalRemaining = previewData.lines.reduce(
      (acc, l) => acc + (Number(l.remaining_invoice_qty != null ? l.remaining_invoice_qty : l.quantity) || 0),
      0
    );

    const priceMissingAny = computedLines.some((line) => line.numericQty > 0 && line.priceMissing);
    return {
      lines: computedLines,
      subtotal,
      discount: disc,
      afterDiscount,
      ppn,
      ppnPercent: ppnPct,
      chargesTotal: chargeSummary.total,
      chargesIncomplete: chargeSummary.incomplete,
      chargesMessage: chargeSummary.message,
      grandTotal,
      totalQty,
      totalRemaining,
      hasErrors,
      isDiscOver,
      priceMissingAny,
    };
  }, [previewData, lineQuantities, linePrices, discountAmount, ppnPercent, otherCharges]);

  const invoiceCompleteness = useMemo(() => {
    const lines = createCalculations?.lines || [];
    const poOk = Boolean(selectedPoId || queryPoId);
    const dateOk = Boolean(invoiceDate);
    const qtyOk = lines.some((line) => Number(line.numericQty || 0) > 0);
    const priceOk = qtyOk && !createCalculations.priceMissingAny;
    const limitsOk = !lines.some((line) => line.isQtyOver) && !createCalculations.isDiscOver;
    const checks = [poOk, dateOk, qtyOk, priceOk, limitsOk];
    const completed = checks.filter(Boolean).length;
    return {
      percentage: Math.round((completed / checks.length) * 100),
      completed,
      total: checks.length,
      complete: completed === checks.length,
      totalsReady: Boolean(previewData) && !createCalculations.priceMissingAny,
    };
  }, [createCalculations, selectedPoId, queryPoId, invoiceDate, previewData]);

  // Handle Save Invoice
  const handleSaveInvoice = async (e) => {
    e.preventDefault();
    if (!selectedPoId || !previewData) {
      setFormErr("Silakan pilih Pesanan Penjualan (PO) terlebih dahulu.");
      return;
    }
    if (createCalculations.chargesIncomplete) {
      setShowChargeErrors(true);
      setInvoiceFormTab("terms");
      setFormErr(createCalculations.chargesMessage);
      return;
    }
    const createDate = createDateRef.current?.read();
    if (!createDate?.ok) {
      setInvoiceFormTab("terms");
      setFormErr(createDate?.message || "Tanggal faktur wajib diisi.");
      return;
    }
    if (createCalculations.hasErrors) {
      if (createCalculations.priceMissingAny) {
        setFormErr("Lengkapi harga jual yang belum tersedia. Harga kosong tidak dijadikan Rp0.");
      } else if (createCalculations.isDiscOver) {
        setFormErr(`Diskon (${formatRupiah(createCalculations.discount)}) tidak boleh melebihi subtotal (${formatRupiah(createCalculations.subtotal)}).`);
      } else {
        setFormErr("Terdapat jumlah kuantitas faktur yang melebihi sisa PO.");
      }
      return;
    }

    setFormBusy(true);
    setFormErr("");
    setSuccessMsg("");

    try {
      const linesPayload = (createCalculations.lines || []).map((l) => {
        const rawP = linePrices[l.line_idx];
        const blank = rawP === "" || rawP === null || rawP === undefined;
        const numP = Number(rawP);
        if (l.numericQty > 0 && (blank || Number.isNaN(numP))) {
          throw new Error(`Lengkapi harga jual untuk '${l.item_name}'. Harga kosong tidak dijadikan Rp0.`);
        }
        if (!blank && numP < 0) {
          throw new Error(`Harga satuan untuk '${l.item_name}' tidak boleh negatif.`);
        }
        return {
          line_idx: l.line_idx,
          line_id: l.line_id,
          quantity: l.numericQty,
          unit_price: blank ? null : numP,
        };
      });

      const signature = [
        selectedPoId,
        createDate.iso,
        Number(paymentTermDays) || 0,
        Number(discountAmount) || 0,
        Number(ppnPercent) || 0,
        invoiceNotes.trim(),
        JSON.stringify(otherChargePayload(otherCharges)),
        JSON.stringify(linesPayload),
      ].join("|");
      const idempotencyKey = ensureIdempotencyIntent(createIdempotencyRef, signature, { prefix: "inv-create" });
      const payload = {
        po_id: selectedPoId,
        invoice_date: createDate.iso,
        payment_term_days: Number(paymentTermDays) || 0,
        discount_amount: Number(discountAmount) || 0,
        ppn_percent: Number(ppnPercent) || 0,
        notes: invoiceNotes.trim(),
        other_charges: otherChargePayload(otherCharges),
        lines: linesPayload,
        idempotency_key: idempotencyKey,
      };

      try {
        const res = await api.createSalesInvoice(payload);
        const interpreted = interpretAutoLifecycleResponse(res);
        if (interpreted.mode === "auto" && !interpreted.ok) {
          setFormErr(interpreted.message);
          if (interpreted.document) setDetailData(interpreted.document);
          return;
        }
        clearIdempotencyIntent(createIdempotencyRef);
        setSuccessMsg(interpreted.message || res.message || "Faktur Penjualan berhasil dibuat");
        setTimeout(() => {
          setSearchParams({});
          setViewMode("list");
          reloadInvoiceListAuthoritative();
        }, 900);
      } catch (err) {
        const det = err.response?.data?.detail;
        if (det && typeof det === "object" && ("financial_complete" in det || "auto_accounting_status" in det)) {
          const interpreted = interpretAutoLifecycleResponse({}, { httpErrorBody: det });
          setFormErr(interpreted.message);
          if (interpreted.document) {
            setSelectedInvoiceId(interpreted.document.id || selectedInvoiceId);
            setDetailData(interpreted.document);
          }
          return;
        }
        setFormErr(
          typeof det === "object"
            ? det.message
            : det || err.message || "Gagal menyimpan Faktur Penjualan"
        );
      }
    } catch (err) {
      setFormErr(err.message || "Gagal menyimpan Faktur Penjualan");
    } finally {
      setFormBusy(false);
    }
  };

  // Open Detail / Edit
  const handleOpenDetail = async (invId) => {
    setSelectedInvoiceId(invId);
    setViewMode("detail");
    setDetailLoading(true);
    setDetailErr("");
    setFormErr("");
    setSuccessMsg("");
    try {
      const res = await api.getSalesInvoice(invId);
      const inv = res.invoice || res;
      setDetailData(inv);
      setDetailDate(salesInvoiceDateToIso(inv.invoice_date) || "");
      setDetailTermDays(inv.payment_term_days || 0);
      setDetailDiscount(inv.discount_amount || 0);
      setDetailPpn(inv.ppn_percent || 0);
      setDetailNotes(inv.notes || "");
      setDetailCharges((inv.other_charges || []).map(otherChargeFromStored));
      setShowDetailChargeErrors(false);
      const prices = {};
      const qtys = {};
      (inv.lines || []).forEach((l) => {
        prices[l.line_idx] = l.unit_price != null ? l.unit_price : 0;
        qtys[l.line_idx] = l.quantity != null ? l.quantity : "";
      });
      setDetailPrices(prices);
      setDetailQuantities(qtys);
    } catch (e) {
      const det = e.response?.data?.detail;
      setDetailErr(
        typeof det === "object"
          ? det.message
          : det || "Gagal memuat detail Faktur Penjualan"
      );
    } finally {
      setDetailLoading(false);
    }
  };

  useEffect(() => {
    if (!queryInvoiceId || viewMode === "create") return;
    if (selectedInvoiceId === queryInvoiceId && viewMode === "detail") return;
    handleOpenDetail(queryInvoiceId);
    // Open once per invoice_id deep link.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryInvoiceId]);

  // Detail live calculation
  const detailCalculations = useMemo(() => {
    if (!detailData || !detailData.lines) {
      return { lines: [], subtotal: 0, discount: 0, afterDiscount: 0, ppn: 0, grandTotal: 0, totalQty: 0 };
    }
    let totalQty = 0;
    let subtotal = 0;
    const computedLines = detailData.lines.map((l) => {
      const rawQ = detailQuantities[l.line_idx];
      const q = rawQ !== undefined && rawQ !== "" ? Math.max(0, Number(rawQ) || 0) : (Number(l.quantity) || 0);
      const maxQty = Number.isFinite(Number(l.max_qty)) ? Number(l.max_qty) : null;
      const rawP = detailPrices[l.line_idx];
      const p = Math.max(0, Number(rawP) || 0);
      const lineTotal = q * p;
      totalQty += q;
      subtotal += lineTotal;
      return {
        ...l,
        numericQty: q,
        maxQty,
        qtyOver: maxQty !== null && q > maxQty,
        unitPrice: rawP !== undefined ? rawP : 0,
        numericPrice: p,
        lineTotal,
      };
    });

    const disc = Math.max(0, Number(detailDiscount) || 0);
    const afterDiscount = Math.max(0, subtotal - disc);
    const ppnPct = Math.max(0, Number(detailPpn) || 0);
    const ppn = Math.round(afterDiscount * (ppnPct / 100));
    const chargeSummary = summarizeOtherCharges(detailCharges);
    const grandTotal = chargeSummary.incomplete ? null : afterDiscount + ppn + chargeSummary.total;

    return {
      lines: computedLines,
      subtotal,
      discount: disc,
      afterDiscount,
      ppn,
      ppnPercent: ppnPct,
      chargesTotal: chargeSummary.total,
      chargesIncomplete: chargeSummary.incomplete,
      chargesMessage: chargeSummary.message,
      grandTotal,
      totalQty,
    };
  }, [detailData, detailPrices, detailQuantities, detailDiscount, detailPpn, detailCharges]);

  // Handle Update Invoice
  const handleUpdateInvoice = async (e) => {
    e.preventDefault();
    if (!selectedInvoiceId || !detailData) return;
    if (detailCalculations.chargesIncomplete) {
      setShowDetailChargeErrors(true);
      setInvoiceFormTab("terms");
      setDetailErr(detailCalculations.chargesMessage);
      return;
    }
    const detailDateRead = detailDateRef.current?.read();
    if (!detailDateRead?.ok) {
      setInvoiceFormTab("terms");
      setDetailErr(detailDateRead?.message || "Tanggal faktur wajib diisi.");
      return;
    }
    setDetailSaving(true);
    setDetailErr("");
    setSuccessMsg("");

    try {
      const linesPayload = (detailCalculations.lines || []).map((l) => {
        const rawP = detailPrices[l.line_idx];
        const blank = rawP === "" || rawP === null || rawP === undefined;
        const numP = Number(rawP);
        if (blank || Number.isNaN(numP)) {
          throw new Error(`Lengkapi harga jual untuk '${l.item_name}'. Harga kosong tidak dijadikan Rp0.`);
        }
        if (numP < 0) {
          throw new Error(`Harga satuan untuk '${l.item_name}' tidak boleh negatif.`);
        }
        const qty = Number(l.numericQty);
        if (!Number.isInteger(qty) || qty <= 0) {
          throw new Error("Qty Faktur harus berupa bilangan bulat lebih dari 0.");
        }
        return {
          line_idx: l.line_idx,
          line_id: l.line_id,
          quantity: qty,
          unit_price: numP,
        };
      });

      const signature = [
        selectedInvoiceId,
        detailDateRead.iso,
        Number(detailTermDays) || 0,
        Number(detailDiscount) || 0,
        Number(detailPpn) || 0,
        detailNotes.trim(),
        JSON.stringify(otherChargePayload(detailCharges)),
        JSON.stringify(linesPayload),
      ].join("|");
      // New edit attempt (changed signature) gets a new key; retries reuse.
      const idempotencyKey = ensureIdempotencyIntent(editIdempotencyRef, signature, { prefix: "inv-edit" });
      const payload = {
        invoice_date: detailDateRead.iso,
        payment_term_days: Number(detailTermDays) || 0,
        discount_amount: Number(detailDiscount) || 0,
        ppn_percent: Number(detailPpn) || 0,
        notes: detailNotes.trim(),
        other_charges: otherChargePayload(detailCharges),
        lines: linesPayload,
        idempotency_key: idempotencyKey,
      };

      const res = await api.updateSalesInvoice(selectedInvoiceId, payload);
      const interpreted = interpretAutoLifecycleResponse(res);
      const updated = interpreted.document || res.invoice || res;
      setDetailData(updated);
      const savedQtys = {};
      (updated.lines || []).forEach((l) => {
        savedQtys[l.line_idx] = l.quantity != null ? l.quantity : "";
      });
      setDetailQuantities(savedQtys);
      setDetailCharges((updated.other_charges || []).map(otherChargeFromStored));
      setShowDetailChargeErrors(false);
      if (interpreted.mode === "auto" && !interpreted.ok) {
        setDetailErr(interpreted.message);
        invalidateSalesInvoiceListCache();
      } else {
        clearIdempotencyIntent(editIdempotencyRef);
        setSuccessMsg(interpreted.message || "Perubahan Faktur Penjualan berhasil disimpan");
        setTimeout(() => setSuccessMsg(""), 2500);
        invalidateSalesInvoiceListCache();
      }
    } catch (err) {
      const det = err.response?.data?.detail;
      if (det && typeof det === "object" && det.code === "FAKTUR_QTY_MELEBIHI_SISA_PO") {
        setDetailErr(DRAFT_QTY_OVER_MESSAGE);
      } else if (det && typeof det === "object" && ("financial_complete" in det || "auto_accounting_status" in det)) {
        const interpreted = interpretAutoLifecycleResponse({}, { httpErrorBody: det });
        if (interpreted.document) setDetailData(interpreted.document);
        setDetailErr(interpreted.message);
        invalidateSalesInvoiceListCache();
      } else {
        setDetailErr(
          typeof det === "object"
            ? det.message
            : det || err.message || "Gagal memperbarui Faktur Penjualan"
        );
      }
    } finally {
      setDetailSaving(false);
    }
  };

  const handleFinalizeInvoice = async () => {
    if (!selectedInvoiceId) return;
    setLifecycleBusy(true);
    setDetailErr("");
    try {
      const res = await api.finalizeSalesInvoice(selectedInvoiceId);
      const updated = res.invoice || res;
      setDetailData(updated);
      setFinalizeConfirm(false);
      setSuccessMsg("Faktur berhasil difinalisasi. Field keuangan terkunci.");
      setTimeout(() => setSuccessMsg(""), 2500);
      invalidateSalesInvoiceListCache();
    } catch (err) {
      const det = err.response?.data?.detail;
      if (det && typeof det === "object" && det.code === "FAKTUR_QTY_MELEBIHI_SISA_PO") {
        setDetailErr(FINALIZE_QTY_OVER_MESSAGE);
      } else {
        setDetailErr(typeof det === "object" ? det.message : det || err.message || "Gagal finalisasi faktur");
      }
    } finally {
      setLifecycleBusy(false);
    }
  };

  const handlePostAccounting = async () => {
    if (!selectedInvoiceId) return;
    setLifecycleBusy(true);
    setDetailErr("");
    try {
      const res = await api.postSalesInvoiceAccounting(selectedInvoiceId);
      const updated = res.invoice || res;
      setDetailData(updated);
      setPostAccountingConfirm(false);
      setSuccessMsg("Faktur diposting ke accounting.");
      setTimeout(() => setSuccessMsg(""), 2500);
      invalidateSalesInvoiceListCache();
    } catch (err) {
      const det = err.response?.data?.detail;
      setDetailErr(typeof det === "object" ? det.message : det || err.message || "Gagal posting accounting");
    } finally {
      setLifecycleBusy(false);
    }
  };

  const handleReverseAccounting = async () => {
    if (!selectedInvoiceId) return;
    setLifecycleBusy(true);
    setReverseErr("");
    setDetailErr("");
    try {
      const res = await api.reverseSalesInvoiceAccounting(selectedInvoiceId, { reason: reverseReason });
      const updated = res.invoice || res;
      setDetailData(updated);
      setReverseConfirm(false);
      setReverseReason("");
      setSuccessMsg("Faktur di-reverse dari accounting.");
      setTimeout(() => setSuccessMsg(""), 2500);
      invalidateSalesInvoiceListCache();
    } catch (err) {
      const det = err.response?.data?.detail;
      const msg = typeof det === "object" ? det.message : det || err.message || "Gagal reverse accounting";
      setReverseErr(msg);
      setDetailErr(msg);
    } finally {
      setLifecycleBusy(false);
    }
  };

  const handleCancelInvoice = async () => {
    if (!selectedInvoiceId) return;
    setLifecycleBusy(true);
    setDetailErr("");
    try {
      if (canCancelDetailBusiness) {
        const key = ensureIdempotencyIntent(
          cancelIdempotencyRef,
          `cancel-invoice-detail|${selectedInvoiceId}`,
          { prefix: "inv-cancel" },
        );
        const res = await api.businessCancelSalesInvoice(selectedInvoiceId, {
          reason: "Batalkan dari detail Faktur Penjualan",
          idempotency_key: key,
        });
        const interpreted = interpretBusinessCancelResponse(res);
        if (!interpreted.ok) {
          setDetailErr(interpreted.message);
          if (interpreted.document) setDetailData(interpreted.document);
          if (interpreted.pending) reloadInvoiceListAuthoritative();
          return;
        }
        clearIdempotencyIntent(cancelIdempotencyRef);
        if (interpreted.document) setDetailData(interpreted.document);
        setCancelConfirm(false);
        setSuccessMsg(interpreted.message || "Faktur dibatalkan.");
        setTimeout(() => setSuccessMsg(""), 2500);
        reloadInvoiceListAuthoritative();
        return;
      }
      const res = await api.cancelSalesInvoice(selectedInvoiceId);
      const updated = res.invoice || res;
      setDetailData(updated);
      setCancelConfirm(false);
      setSuccessMsg("Faktur dibatalkan.");
      setTimeout(() => setSuccessMsg(""), 2500);
      invalidateSalesInvoiceListCache();
    } catch (err) {
      const statusCode = err.response?.status;
      const det = err.response?.data?.detail;
      if (canCancelDetailBusiness) {
        const interpreted = interpretBusinessCancelResponse({}, { httpErrorBody: det, statusCode });
        setDetailErr(interpreted.message || (typeof det === "object" ? det.message : det) || "Gagal membatalkan faktur");
        if (interpreted.document) setDetailData(interpreted.document);
        return;
      }
      setDetailErr(typeof det === "object" ? det.message : det || err.message || "Gagal membatalkan faktur");
    } finally {
      setLifecycleBusy(false);
    }
  };

  const renderPaymentBadge = (status) => {
    const s = String(status || "BELUM DIBAYAR").toUpperCase();
    let bg = "#fef3c7";
    let color = "#92400e";
    let border = "#fde68a";

    if (s === "LUNAS") {
      bg = "#d1fae5";
      color = "#065f46";
      border = "#a7f3d0";
    } else if (s === "DP") {
      bg = "#e0e7ff";
      color = "#3730a3";
      border = "#c7d2fe";
    }

    return (
      <span
        style={{
          display: "inline-block",
          padding: "3px 10px",
          borderRadius: 9999,
          fontSize: 11,
          fontWeight: 700,
          backgroundColor: bg,
          color,
          border: `1px solid ${border}`,
        }}
      >
        {s}
      </span>
    );
  };

  const detailStatus = invoiceFinancialStatus(detailData);
  const financialLocked = Boolean(detailData) && detailStatus !== "draft";
  const canPostAccounting =
    hasPerm("sales_accounting.post") || user?.role === "admin";
  const canReverseAccountingPerm =
    hasPerm("sales_accounting.reverse") || user?.role === "admin";
  const accountingStatus = accountingStatusOf(detailData);
  const isAccountingPosted = accountingStatus === "posted" || detailData?.accounting_posted === true;
  const isAccountingReversed = accountingStatus === "reversed";
  const detailLifecycleCapability = detailData?.lifecycle_capability || createLifecycleCapability;
  const hideLegacyManualAccounting = shouldHideLegacyManualAccounting(detailLifecycleCapability);
  const hideLegacyOnCreate = shouldHideLegacyManualAccounting(createLifecycleCapability);
  const showPostAccounting =
    !hideLegacyManualAccounting &&
    canPostAccounting &&
    detailStatus === "finalized" &&
    !isAccountingPosted &&
    !isAccountingReversed &&
    detailData?.can_post_accounting === true;
  const showReverseAccounting =
    !hideLegacyManualAccounting &&
    canReverseAccountingPerm &&
    detailStatus === "finalized" &&
    isAccountingPosted &&
    detailData?.can_reverse_accounting === true;
  const reverseBlockers = Array.isArray(detailData?.reverse_blockers) ? detailData.reverse_blockers : [];
  const showReverseBlocked =
    !hideLegacyManualAccounting &&
    canReverseAccountingPerm &&
    detailStatus === "finalized" &&
    isAccountingPosted &&
    !showReverseAccounting &&
    reverseBlockers.length > 0;
  const showAccountingUnconfigured =
    detailStatus === "finalized" &&
    !isAccountingPosted &&
    detailData?.accounting_ready === false;
  // Finalized revision unlocks when edit_revision capability + sales.invoice.edit + no active receipts.
  const canReviseInvoice = canShowInvoiceEditUi(hasPerm, {
    capability: detailLifecycleCapability,
    document: detailData,
  });
  const canEditFinancial = (canEdit && !financialLocked) || canReviseInvoice;
  // Business-cancel (posted OK) vs legacy cancel (unposted + no receipts only).
  const canCancelDetailBusiness = canShowInvoiceCancelUi(hasPerm, {
    capability: detailLifecycleCapability,
    document: detailData,
  });
  const canCancelDetailLegacy =
    !canCancelDetailBusiness
    && canEdit
    && detailStatus === "finalized"
    && detailData?.can_cancel === true;

  const filtersActive = invoiceFiltersActive(columnFilters, searchQuery);
  const reserveCheckboxColumn = canDelete || canCancelBusiness;
  const showDeleteControls = (canDelete || canCancelBusiness) && listTab !== "cancelled";
  const tabLabel = salesListTabLabel(listTab);
  const pageCount = Math.max(1, Math.ceil((Number(listMeta.total) || 0) / pageSize) || 1);
  const allPageDeletableSelected = pageDeletableIds.length > 0 && pageDeletableIds.every((id) => selectedIds.has(id));
  const showRecoverAuto = canRecoverAutoAccounting(detailData, { hasPerm, kind: "invoice" });
  const financialStatusOptions = listTab === "cancelled"
    ? [{ value: "", label: "Semua" }, { value: "cancelled", label: "Dibatalkan" }]
    : [
        { value: "", label: "Semua" },
        { value: "draft", label: "Draft" },
        { value: "finalized", label: "Final" },
      ];
  const statusFilterGroups = [
    {
      key: "financial",
      label: "Status Faktur",
      testid: "fp-th-filter-status-financial",
      options: financialStatusOptions,
      value: columnFilters.financialStatus || "",
      emptyValue: "",
    },
    {
      key: "accounting",
      label: "Posting Akuntansi",
      testid: "fp-th-filter-status-accounting",
      options: INVOICE_POSTING_OPTIONS,
      value: columnFilters.accountingStatus || "",
      emptyValue: "",
    },
  ];
  const columnCount = reserveCheckboxColumn ? 9 : 8;

  return (
    <div
      className={"page-container penjualan-page penjualan-workspace sd-density fp-invoice-page " + (viewMode === "list" ? "sd-list-mode" : "sd-form-mode")}
      data-testid="faktur-penjualan-page"
    >
      <header className={viewMode === "list" ? "sd-list-header" : "sd-form-header"}>
        <div className={viewMode === "list" ? "sd-list-heading" : "sd-form-heading"}>
          {viewMode !== "list" && (
            <button
              type="button"
              className="sd-back"
              onClick={() => {
                setSearchParams({});
                setViewMode("list");
              }}
              data-testid="back-to-list-btn"
            >
              <ArrowLeft size={16} /> Kembali
            </button>
          )}
          <h1>
            {viewMode === "create"
              ? "Buat Faktur Penjualan"
              : viewMode === "detail"
              ? "Detail Faktur Penjualan"
              : "Faktur Penjualan"}
          </h1>
          {viewMode === "list" && (!listLoading || invoices.length > 0) && !listErr && (
            <div className="sd-list-summary" data-testid="invoice-list-count">
              <strong>{(filtersActive ? listMeta.total : listMeta.tabTotal).toLocaleString("id-ID")}</strong>
              <span>{filtersActive ? "hasil filter" : "pada tab"}</span>
              {listRefreshing ? <span data-testid="invoice-list-refreshing"> · menyegarkan</span> : null}
            </div>
          )}
          {viewMode === "detail" && (
            <span className="sd-support">
              {detailData?.invoice_number || detailData?.id || selectedInvoiceId || ""}
            </span>
          )}
          {viewMode === "create" && (
            <>
              <span
                className={"fp-chip " + (invoiceCompleteness.complete ? "is-complete" : "is-incomplete")}
                data-testid="invoice-completeness"
              >
                {invoiceCompleteness.complete ? "Lengkap" : "Belum lengkap"}
              </span>
              <FormProgress
                percentage={invoiceCompleteness.percentage}
                completed={invoiceCompleteness.completed}
                total={invoiceCompleteness.total}
                className="fp-progress-sr"
              />
            </>
          )}
        </div>
        {viewMode === "detail" && detailData && Number(detailData.outstanding_amount) > 0 && canCreate && (
          <button
            type="button"
            className="fp-header-action"
            data-testid="btn-buat-penerimaan"
            onClick={() => navigate(`/penjualan/penerimaan-penjualan?invoice_id=${detailData.id}`)}
          >
            <CreditCard size={15} /> + Buat Penerimaan
          </button>
        )}

        {viewMode === "list" && (
          <div className="sd-list-header-actions">
            {showDeleteControls && selectedIds.size > 0 && (
              <button
                type="button"
                className="sd-btn-danger"
                data-testid="btn-hapus-terpilih"
                onClick={() => {
                  const ids = Array.from(selectedIds);
                  const numbers = invoices.filter((inv) => selectedIds.has(inv.id)).map((inv) => inv.invoice_number || inv.id);
                  openDeleteConfirm({ ids, numbers });
                }}
              >
                <Trash2 size={16} /> Hapus Terpilih ({selectedIds.size})
              </button>
            )}
            <button
              type="button"
              className="sd-list-refresh"
              onClick={() => loadInvoices({ force: true })}
              disabled={listLoading || listRefreshing}
              title="Refresh data"
              data-testid="refresh-invoice-btn"
            >
              <RefreshCw size={16} className={(listLoading || listRefreshing) ? "spin" : ""} />
            </button>
            {canCreate && (
              <button
                type="button"
                className="sd-btn-primary"
                onClick={() => {
                  setSelectedPoId("");
                  setPreviewData(null);
                  setInvoiceFormTab("items");
                  setSearchParams({ action: "create" });
                  setViewMode("create");
                }}
                data-testid="btn-create-invoice"
              >
                <Plus size={16} /> Buat Faktur dari PO
              </button>
            )}
          </div>
        )}
      </header>

      {/* SUCCESS BANNER */}
      {successMsg && (
        <div
          className="alert-box success"
          data-testid="success-alert"
          style={{
            background: "#ecfdf5",
            border: "1px solid #a7f3d0",
            color: "#047857",
            padding: "12px 16px",
            borderRadius: 8,
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <CheckCircle2 size={18} />
            <span>{successMsg}</span>
          </div>
          <button
            type="button"
            onClick={() => setSuccessMsg("")}
            style={{ background: "none", border: "none", cursor: "pointer", color: "#047857" }}
          >
            <X size={16} />
          </button>
        </div>
      )}

      {/* VIEW: LIST */}
      {viewMode === "list" && (
        <section className="sd-list-panel">
          {listErr && (
            <div className="sd-banner is-error" role="alert" data-testid="invoice-list-error">
              <AlertCircle size={16} />
              <span>{listErr}</span>
            </div>
          )}

          <div className="sd-list-toolbar fp-invoice-toolbar">
            <div className="fp-invoice-tabs" role="tablist" aria-label="Klasifikasi faktur">
              {SALES_LIST_TABS.map((tab) => {
                const count = Number(listMeta.counts?.[tab.id] || 0);
                const selected = listTab === tab.id;
                const legacyTestId = tab.id === "active"
                  ? "tab-faktur-aktif"
                  : tab.id === "cancelled"
                    ? "tab-faktur-dibatalkan"
                    : `tab-faktur-${tab.testIdSuffix}`;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    className={selected ? "is-active" : ""}
                    data-testid={legacyTestId}
                    onClick={() => changeInvoiceTab(tab.id)}
                  >
                    {tab.label}
                    <span
                      className="fp-tab-count"
                      data-testid={tab.id === "active" ? "tab-count-active" : tab.id === "cancelled" ? "tab-count-cancelled" : `tab-count-${tab.id}`}
                    >
                      {count.toLocaleString("id-ID")}
                    </span>
                  </button>
                );
              })}
            </div>
            <div className="sd-list-search">
              <Search size={16} />
              <input
                type="text"
                placeholder="Cari no PO, pelanggan, sales, atau faktur..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") flushSearch(e);
                }}
                data-testid="search-invoice-input"
              />
            </div>
            <button
              type="button"
              className="sd-list-reset"
              data-testid="reset-invoice-filters"
              onClick={resetColumnFilters}
              disabled={!filtersActive}
            >
              Reset Filter
            </button>
          </div>

          {listSyncErr && (
            <div className="sd-banner is-error" role="status" data-testid="invoice-list-sync-error">
              {listSyncErr}
              {listShowingCache ? " Data di bawah belum disinkronkan ulang." : ""}
            </div>
          )}
          <div
            className="sd-list-table-scroll"
            ref={tableScrollRef}
            onScroll={rememberTableScroll}
            data-testid="invoice-list-scroll"
          >
            <table className="sd-list-table" data-testid="invoices-table">
              <colgroup>
                {reserveCheckboxColumn && <col style={{ width: 36 }} />}
                <col style={{ width: "24%" }} />
                <col style={{ width: 188 }} />
                <col style={{ width: 108 }} />
                <col style={{ width: 128 }} />
                <col style={{ width: 128 }} />
                <col style={{ width: 132 }} />
                <col style={{ width: 124 }} />
                <col style={{ width: 248 }} />
              </colgroup>
              <thead>
                <tr>
                  {reserveCheckboxColumn && (
                    <th>
                      {showDeleteControls ? (
                        <input
                          type="checkbox"
                          data-testid="select-all-invoices"
                          checked={allPageDeletableSelected}
                          onChange={toggleSelectAll}
                          aria-label="Pilih semua faktur yang dapat dihapus pada halaman ini"
                        />
                      ) : null}
                    </th>
                  )}
                  <th>Pelanggan / No. Faktur</th>
                  <th>No. PO</th>
                  <th>
                    <HeaderSelectFilter
                      label="Tanggal"
                      testid="fp-th-filter-date"
                      options={INVOICE_DATE_PRESET_OPTIONS}
                      value={columnFilters.datePreset || "all"}
                      emptyValue="all"
                      onChange={(next) => patchColumnFilter({ datePreset: next })}
                    />
                  </th>
                  <th>Total Faktur</th>
                  <th>Sisa Tagihan</th>
                  <th>
                    <HeaderSelectFilter
                      label="Status"
                      testid="fp-th-filter-status"
                      groups={statusFilterGroups}
                      onGroupChange={(key, next) => {
                        if (key === "financial") patchColumnFilter({ financialStatus: next });
                        else if (key === "accounting") patchColumnFilter({ accountingStatus: next });
                      }}
                      onClearGroups={() => patchColumnFilter({
                        financialStatus: "",
                        accountingStatus: "",
                      })}
                    />
                  </th>
                  <th>
                    <HeaderSelectFilter
                      label="Pembayaran"
                      testid="fp-th-filter-payment"
                      options={INVOICE_PAYMENT_OPTIONS}
                      value={columnFilters.paymentStatus || ""}
                      emptyValue=""
                      onChange={(next) => patchColumnFilter({ paymentStatus: next })}
                    />
                  </th>
                  <th>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {listLoading && invoices.length === 0 ? (
                  <tr>
                    <td colSpan={columnCount} className="sd-list-empty">
                      Memuat daftar Faktur Penjualan...
                    </td>
                  </tr>
                ) : invoices.length === 0 ? (
                  <tr>
                    <td colSpan={columnCount} className="sd-list-empty" data-testid="empty-invoices">
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                        <FileText size={32} color="#94a3b8" />
                        <div style={{ fontWeight: 600, color: "#1e293b", marginTop: 8 }}>
                          {filtersActive
                            ? "Tidak ada faktur yang cocok dengan filter"
                            : listTab === "cancelled"
                            ? "Belum ada faktur dibatalkan"
                            : "Belum ada faktur aktif"}
                        </div>
                      </div>
                    </td>
                  </tr>
                ) : (
                  invoices.map((inv) => {
                    const deletable = invoiceDeletable(inv, { allowBusinessCancel: canCancelBusiness });
                    const draftEditable = listTab === "active" && invoiceFinancialStatus(inv) === "draft" && canEdit;
                    return (
                    <tr
                      key={inv.id}
                      {...clickableDocumentRowProps({
                        onOpen: () => handleOpenDetail(inv.id),
                        "data-testid": `invoice-row-${inv.id}`,
                        ariaLabel: `Buka faktur ${inv.invoice_number || inv.id}`,
                      })}
                    >
                      {reserveCheckboxColumn && (
                        <td data-no-row-nav onClick={(e) => e.stopPropagation()}>
                          {showDeleteControls && deletable ? (
                            <input
                              type="checkbox"
                              data-testid={`invoice-checkbox-${inv.id}`}
                              checked={selectedIds.has(inv.id)}
                              onChange={() => toggleSelect(inv)}
                              aria-label={`Pilih ${inv.invoice_number}`}
                            />
                          ) : null}
                        </td>
                      )}
                      <td className="sd-identity">
                        <div className="sd-customer">{inv.customer || "-"}</div>
                        <span className="sd-doc is-static" data-testid={`invoice-number-${inv.id}`}>
                          {inv.invoice_number || "-"}
                        </span>
                        {inv.outlet ? <span className="sd-support">{inv.outlet}</span> : null}
                      </td>
                      <td className="fp-po-cell" data-no-row-nav onClick={(e) => e.stopPropagation()}>
                        <Link to={`/po/${inv.po_id}`} className="sd-doc" title={inv.po_number || "Buka PO"}>
                          {inv.po_number || "-"}
                        </Link>
                      </td>
                      <td>{formatSalesInvoiceDate(inv.invoice_date || inv.created_at) || "-"}</td>
                      <td className="sd-money">{formatRupiah(inv.grand_total)}</td>
                      <td className={"sd-money " + (Number(inv.outstanding_amount) > 0 ? "is-due" : "is-zero")}>
                        {formatRupiah(inv.outstanding_amount)}
                      </td>
                      <td className="fp-status-cell">
                        <span className="fp-status-main">{invoiceFinancialLabel(inv.financial_status)}</span>
                        <span className="fp-status-sub">{invoicePostingLabel(accountingStatusOf(inv))}</span>
                        <LifecycleStatusChip doc={inv} testId={`invoice-lifecycle-${inv.id}`} />
                      </td>
                      <td className={"fp-pay-text is-" + String(inv.payment_status || "BELUM DIBAYAR").toLowerCase().replace(/\s+/g, "-")}>
                        {invoicePaymentLabel(inv.payment_status)}
                      </td>
                      <td data-no-row-nav onClick={(e) => e.stopPropagation()}>
                        <div className="sd-actions">
                        <button
                          type="button"
                          className="sd-act sd-act-view"
                          onClick={() => handleOpenDetail(inv.id)}
                          data-testid={`view-invoice-btn-${inv.id}`}
                        >
                          {draftEditable ? "Lihat / Edit" : "Lihat"}
                        </button>
                        {canPrint && (
                          <button
                            type="button"
                            className="sd-act sd-act-view"
                            onClick={(e) => handlePrintPdf(e, inv.id)}
                            data-testid={`btn-print-pdf-${inv.id}`}
                            disabled={pdfLoadingId === inv.id}
                            title="Cetak PDF Faktur Penjualan"
                          >
                            {pdfLoadingId === inv.id ? "Memuat..." : "Cetak PDF"}
                          </button>
                        )}
                        {showDeleteControls && deletable && (
                          <button
                            type="button"
                            className="sd-act sd-act-delete"
                            data-testid={`btn-delete-invoice-${inv.id}`}
                            onClick={() => openDeleteConfirm({
                              id: inv.id,
                              number: inv.invoice_number,
                              numbers: [inv.invoice_number],
                            })}
                          >
                            Hapus
                          </button>
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
          <footer className="sd-list-footer" data-testid="invoice-list-footer">
            <span data-testid="invoice-list-range">
              {listLoading && invoices.length === 0
                ? "Memuat daftar faktur..."
                : listErr
                ? "Daftar faktur belum dapat ditampilkan"
                : invoiceRangeLabel({
                    total: listMeta.total,
                    tabTotal: listMeta.tabTotal,
                    page,
                    pageSize,
                    tabLabel,
                    filtered: filtersActive,
                  })}
            </span>
            <div className="fp-pager">
              <button
                type="button"
                data-testid="invoice-page-prev"
                disabled={(listLoading && invoices.length === 0) || page <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                Sebelumnya
              </button>
              <span data-testid="invoice-page-indicator">{page} / {pageCount}</span>
              <label className="fp-page-size">
                <span>Baris</span>
                <select
                  aria-label="Jumlah baris per halaman"
                  data-testid="invoice-page-size"
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(Number(e.target.value));
                    resetInvoiceListPosition();
                  }}
                >
                  {INVOICE_PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>{size}</option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                data-testid="invoice-page-next"
                disabled={(listLoading && invoices.length === 0) || page >= pageCount}
                onClick={() => setPage((current) => current + 1)}
              >
                Berikutnya
              </button>
            </div>
          </footer>
        </section>
      )}

      {/* VIEW: CREATE */}
      {viewMode === "create" && (
        <div
          className="sd-form fp-create"
          data-testid="invoice-create-lifecycle"
          data-legacy-manual={hideLegacyOnCreate ? "required" : "hidden"}
        >
          {/* Step 1: PO Selector Panel (Only shown if opened directly from menu without pre-selected PO) */}
          {!queryPoId && (
            <FormSection
              id="fp-select-po"
              title="Pilih Pesanan Penjualan"
              description="Satu PO bisa difakturkan sebagian berkali-kali. Pilih PO, lalu sesuaikan sisa qty."
              status={computeStatus([{ value: selectedPoId, isRequired: true }])}
              className="fp-po-section"
            >
              <div className="fp-po-select">
                <FormField id="fp-po-selector-field" label="Nomor PO" isRequired value={selectedPoId}>
                  <select
                    value={selectedPoId}
                    onChange={handleSelectPo}
                    disabled={previewLoading || formBusy}
                    data-testid="po-selector"
                    style={{
                      width: "100%",
                      padding: "8px 12px",
                      border: "1px solid #cbd5e1",
                      borderRadius: 6,
                      fontSize: "0.95rem",
                    }}
                  >
                    <option value="">-- Pilih PO --</option>
                    {availablePOs.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.po_number} — {p.customer} ({Number(p.total_quantity || 0).toLocaleString("id-ID")} pcs)
                      </option>
                    ))}
                  </select>
                </FormField>
              </div>
            </FormSection>
          )}

          {previewLoading ? (
            <div className="sd-form-stage" style={{ padding: 32, textAlign: "center", color: "#64748b" }}>Memuat data PO...</div>
          ) : previewData ? (
            <form className="sd-form-fill fp-create-fill" data-form-tab={invoiceFormTab} onSubmit={handleSaveInvoice}>
              <div className="sd-form-stage">
              <div className="fp-summary" data-testid="po-snapshot-card">
                <div>
                  <span>Nomor PO</span>
                  <strong data-testid="snapshot-po-number">{previewData.po_number}</strong>
                </div>
                <div>
                  <span>Pelanggan</span>
                  <strong data-testid="snapshot-customer">{previewData.customer}</strong>
                </div>
                <div>
                  <span>Outlet / Cabang</span>
                  <strong data-testid="snapshot-outlet">{previewData.outlet || "-"}</strong>
                </div>
                <div>
                  <span>Sisa qty dapat difakturkan</span>
                  <strong data-testid="snapshot-remaining-qty" className={createCalculations.totalRemaining > 0 ? "is-due" : "is-paid"}>
                    {Number(createCalculations.totalRemaining || 0).toLocaleString("id-ID")} pcs
                  </strong>
                </div>
              </div>
              <p className="fp-multi-invoice-hint" data-testid="multi-invoice-hint">
                Faktur parsial diperbolehkan. Qty faktur tidak boleh melebihi sisa per baris.
              </p>

              <div className="fp-terms fp-terms-essential" data-testid="invoice-essential-terms">
                  <div>
                    <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "#334155", display: "block", marginBottom: 6 }}>
                      Tanggal Faktur <span style={{ color: "#ef4444" }}>*</span>
                    </label>
                    <SalesInvoiceDateField
                      ref={createDateRef}
                      valueIso={invoiceDate}
                      onChange={setInvoiceDate}
                      testId="invoice-date-input"
                    />
                  </div>

                  <div>
                    <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "#334155", display: "block", marginBottom: 6 }}>
                      Termin (Hari)
                    </label>
                    <input
                      type="number"
                      min="0"
                      value={paymentTermDays}
                      onChange={(e) => setPaymentTermDays(e.target.value)}
                      data-testid="invoice-term-input"
                      placeholder="0"
                      style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6 }}
                    />
                    <div style={{ fontSize: "0.75rem", color: "#64748b", marginTop: 4 }}>
                      Jatuh Tempo: <strong>{formatSalesInvoiceDate(calculatedDueDate) || "-"}</strong>
                    </div>
                  </div>

                  <div>
                    <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "#334155", display: "block", marginBottom: 6 }}>
                      Diskon (Rp)
                    </label>
                    <input
                      type="number"
                      min="0"
                      value={discountAmount}
                      onChange={(e) => setDiscountAmount(e.target.value)}
                      data-testid="invoice-discount-input"
                      placeholder="0"
                      style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6 }}
                    />
                  </div>

                  <div>
                    <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "#334155", display: "block", marginBottom: 6 }}>
                      PPN (%)
                    </label>
                    <input
                      type="number"
                      min="0"
                      max="100"
                      step="any"
                      value={ppnPercent}
                      onChange={(e) => setPpnPercent(e.target.value)}
                      data-testid="invoice-ppn-input"
                      placeholder="0"
                      style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6 }}
                    />
                  </div>
              </div>

              <div className="sd-form-tabs" role="tablist" aria-label="Bagian faktur">
                <button type="button" className={invoiceFormTab === "items" ? "is-active" : ""} onClick={() => setInvoiceFormTab("items")}>Barang & Harga</button>
                <button type="button" className={invoiceFormTab === "terms" ? "is-active" : ""} onClick={() => setInvoiceFormTab("terms")}>Catatan & Biaya</button>
              </div>

              <div className="fp-terms sd-pane-terms">
                  <div style={{ gridColumn: "1 / -1" }}>
                    <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "#334155", display: "block", marginBottom: 6 }}>
                      Catatan Faktur (Opsional)
                    </label>
                    <input
                      type="text"
                      placeholder="Catatan tambahan faktur penjualan..."
                      value={invoiceNotes}
                      onChange={(e) => setInvoiceNotes(e.target.value)}
                      data-testid="invoice-notes-input"
                      style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6 }}
                    />
                  </div>
                  <OtherChargesEditor
                    rows={otherCharges}
                    accounts={chargeAccounts}
                    disabled={formBusy}
                    showErrors={showChargeErrors}
                    onChange={setOtherCharges}
                    idPrefix="create"
                  />
              </div>

              <div className="fp-items sd-pane-items">
                <div className="sd-lines sd-lines-scroll" data-testid="invoice-lines-scroll">
                  <table className="fp-lines">
                    <thead>
                      <tr>
                        <th>Barang</th>
                        <th>Sisa dapat difakturkan</th>
                        <th>Qty faktur</th>
                        <th>Harga satuan</th>
                        <th>Total baris</th>
                      </tr>
                    </thead>
                    <tbody>
                      {createCalculations.lines.map((li) => (
                        <tr key={li.line_idx} data-testid={`create-line-${li.line_idx}`}>
                          <td>
                            <div className="fp-item-name">{li.item_name}</div>
                            {li.color && <div className="fp-item-meta">Warna: {li.color}</div>}
                          </td>
                          <td style={{ padding: "10px 12px", textAlign: "right", color: "#64748b" }}>
                            <strong>{Number(li.remaining).toLocaleString("id-ID")}</strong> {li.unit || "pcs"}
                          </td>
                          <td style={{ padding: "8px 12px", textAlign: "right" }}>
                            <input
                              type="number"
                              min="0"
                              max={li.remaining}
                              value={lineQuantities[li.line_idx] !== undefined ? lineQuantities[li.line_idx] : li.remaining}
                              onChange={(e) => {
                                const val = e.target.value;
                                setLineQuantities((prev) => ({ ...prev, [li.line_idx]: val }));
                              }}
                              data-testid={`qty-input-${li.line_idx}`}
                              className={li.isQtyOver ? "is-over" : undefined}
                              style={{
                                width: 120,
                                padding: "6px 10px",
                                border: `1px solid ${li.isQtyOver ? "#ef4444" : "#cbd5e1"}`,
                                borderRadius: 6,
                                fontWeight: 600,
                                textAlign: "right",
                                background: li.isQtyOver ? "#fef2f2" : "#fff",
                              }}
                            />
                            {li.isQtyOver && (
                              <div style={{ fontSize: "0.75rem", color: "#ef4444", marginTop: 2 }}>Maks {li.remaining}</div>
                            )}
                          </td>
                          <td style={{ padding: "8px 12px", textAlign: "right" }}>
                            <input
                              type="number"
                              min="0"
                              step="any"
                              value={linePrices[li.line_idx] !== undefined ? linePrices[li.line_idx] : ""}
                              onChange={(e) => {
                                const val = e.target.value;
                                setLinePrices((prev) => ({ ...prev, [li.line_idx]: val }));
                              }}
                              readOnly={Boolean(li.price_from_order)}
                              placeholder={li.price_from_order ? "" : "Isi harga"}
                              data-testid={`unit-price-input-${li.line_idx}`}
                              style={{
                                width: "100%",
                                padding: "6px 10px",
                                border: "1px solid #CBD5E1",
                                borderRadius: 6,
                                fontWeight: 600,
                                textAlign: "right",
                                background: li.price_from_order ? "#f8fafc" : "#fff",
                              }}
                            />
                            {li.price_from_order && (
                              <div style={{ fontSize: "0.75rem", color: "#166534", marginTop: 2 }}>Harga dari pesanan</div>
                            )}
                            {li.priceMissing && li.numericQty > 0 && (
                              <div style={{ fontSize: "0.75rem", color: "#b45309", marginTop: 2 }}>Lengkapi harga jual</div>
                            )}
                          </td>
                          <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 700, color: "#0f172a" }} data-testid={`line-total-${li.line_idx}`}>
                            {li.lineTotal === null ? "—" : formatRupiah(li.lineTotal)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <p className="fp-price-note">Harga mengikuti pesanan penjualan. Sesuaikan qty faktur bila perlu (multi-faktur per PO).</p>
              </div>

              <div className="fp-breakdown fp-breakdown-sticky" data-testid="invoice-breakdown">
                  <div>
                    <span>Subtotal</span>
                    <strong>{invoiceCompleteness.totalsReady ? formatRupiah(createCalculations.subtotal) : "Belum lengkap"}</strong>
                  </div>
                  <div>
                    <span>Diskon</span>
                    <strong>{formatRupiah(createCalculations.discount)}</strong>
                  </div>
                  <div>
                    <span>PPN ({createCalculations.ppnPercent}%)</span>
                    <strong>{invoiceCompleteness.totalsReady ? formatRupiah(createCalculations.ppn) : "Belum lengkap"}</strong>
                  </div>
                  <div>
                    <span>Biaya lainnya</span>
                    <strong data-testid="invoice-other-charges-total">
                      {createCalculations.chargesIncomplete ? "Belum lengkap" : formatRupiah(createCalculations.chargesTotal)}
                    </strong>
                  </div>
                  <div className="fp-breakdown-remaining">
                    <span>Sisa qty PO</span>
                    <strong data-testid="invoice-remaining-qty-summary">
                      {Number(createCalculations.totalRemaining || 0).toLocaleString("id-ID")} pcs
                    </strong>
                  </div>
              </div>

              {createCalculations.totalRemaining <= 0 && (
                <div
                  style={{
                    padding: "12px 16px",
                    backgroundColor: "#fef3c7",
                    border: "1px solid #fde68a",
                    color: "#92400e",
                    borderRadius: 8,
                    marginBottom: 16,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                  }}
                  data-testid="fully-invoiced-banner"
                >
                  <AlertCircle size={18} />
                  <span>Pesanan Penjualan ini sudah difakturkan sepenuhnya (100%). Tidak ada sisa kuantitas untuk difakturkan.</span>
                </div>
              )}

              {formErr && (
                <div
                  className="alert-box error"
                  data-testid="create-error-alert"
                  style={{
                    background: "#fef2f2",
                    border: "1px solid #fecaca",
                    color: "#b91c1c",
                    padding: "10px 14px",
                    borderRadius: 8,
                    marginBottom: 16,
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                  }}
                >
                  <AlertCircle size={16} />
                  <span>{formErr}</span>
                </div>
              )}

              </div>
              <div className="invoice-actions" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, paddingRight: 96 }}>
                <div className="fp-total">
                  <span>Total faktur</span>
                  <strong data-testid="invoice-grand-total">{invoiceCompleteness.totalsReady && createCalculations.grandTotal !== null ? formatRupiah(createCalculations.grandTotal) : "Belum lengkap"}</strong>
                </div>
                <div className="fp-action-buttons">
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setSearchParams({});
                    setViewMode("list");
                  }}
                  disabled={formBusy}
                  style={{ padding: "10px 20px", borderRadius: 8, border: "1px solid #CBD5E1", background: "#fff", cursor: "pointer", fontWeight: 600 }}
                >
                  Batal
                </button>
                <button
                  type="submit"
                  className="primary"
                  disabled={formBusy || createCalculations.hasErrors || createCalculations.totalRemaining <= 0 || createCalculations.totalQty <= 0}
                  data-testid="save-invoice-btn"
                  style={{
                    padding: "10px 24px",
                    borderRadius: 8,
                    background: formBusy || createCalculations.hasErrors || createCalculations.totalRemaining <= 0 || createCalculations.totalQty <= 0 ? "#94a3b8" : "#166534",
                    color: "#fff",
                    border: "none",
                    cursor: formBusy || createCalculations.hasErrors || createCalculations.totalRemaining <= 0 || createCalculations.totalQty <= 0 ? "not-allowed" : "pointer",
                    fontWeight: 600,
                  }}
                >
                  {formBusy ? "Menyimpan..." : "Simpan Faktur"}
                </button>
                </div>
              </div>
            </form>
          ) : (
            <div style={{ padding: 40, textAlign: "center", color: "#64748b" }}>
              Silakan pilih Pesanan Penjualan pada bagian 1 di atas untuk memulai.
            </div>
          )}
        </div>
      )}

      {/* VIEW: DETAIL / EDIT */}
      {viewMode === "detail" && (
        <div className="sd-form fp-detail">
          {detailLoading ? (
            <div className="sd-form-stage" style={{ padding: 40, textAlign: "center", color: "#64748b" }}>Memuat detail Faktur Penjualan...</div>
          ) : detailData ? (
            <form className="sd-form-fill" data-form-tab={invoiceFormTab} onSubmit={handleUpdateInvoice}>
              <div className="sd-form-stage">
              <div className="fp-summary" data-testid="detail-summary">
                <div>
                  <span>No. Faktur</span>
                  <strong data-testid="detail-invoice-number">{detailData.invoice_number || "-"}</strong>
                </div>
                <div>
                  <span>Status Faktur</span>
                  <div className="fp-detail-badges">
                    {renderFinancialBadge(detailStatus)}
                    {(detailStatus !== "draft" || isAccountingPosted || isAccountingReversed) &&
                      renderAccountingBadge(accountingStatus)}
                  </div>
                </div>
                <div>
                  <span>No PO</span>
                  <strong data-testid="detail-po-number">
                    {detailData.po_id ? (
                      <Link to={`/po/${encodeURIComponent(detailData.po_id)}`} data-testid="detail-po-link">
                        {detailData.po_number || detailData.po_id}
                      </Link>
                    ) : (detailData.po_number || "-")}
                  </strong>
                </div>
                <div>
                  <span>Customer</span>
                  <strong data-testid="detail-customer">{detailData.customer}</strong>
                </div>
                <div>
                  <span>Sales</span>
                  <strong data-testid="detail-sales">{detailData.sales || "-"}</strong>
                </div>
                <div>
                  <span>Tanggal Faktur</span>
                  <strong data-testid="detail-invoice-date">{formatSalesInvoiceDate(detailData.invoice_date || detailDate) || "-"}</strong>
                </div>
                <div>
                  <span>Jatuh Tempo</span>
                  <strong data-testid="detail-due-date">{formatSalesInvoiceDate(detailData.due_date) || "-"}</strong>
                </div>
                <div>
                  <span>Total Tagihan</span>
                  <strong data-testid="detail-grand-total">{formatRupiah(detailData.grand_total)}</strong>
                </div>
                <div>
                  <span>Sudah Diterima (AR)</span>
                  <strong data-testid="detail-paid-total">{formatRupiah(detailData.paid_total)}</strong>
                </div>
                <div>
                  <span>Sisa Tagihan (AR)</span>
                  <strong
                    data-testid="detail-outstanding-amount"
                    className={Number(detailData.outstanding_amount) > 0 ? "is-due" : "is-paid"}
                  >
                    {formatRupiah(detailData.outstanding_amount)}
                  </strong>
                </div>
                <div>
                  <span>Status Pembayaran (AR)</span>
                  <div className="fp-detail-badges" data-testid="detail-payment-status">
                    {renderPaymentBadge(detailData.payment_status)}
                  </div>
                </div>
              </div>
              {showAccountingUnconfigured && (
                <p data-testid="invoice-accounting-unconfigured" className="fp-detail-note">
                  Accounting belum dikonfigurasi.{" "}
                  <Link to="/settings?tab=sales-accounting">Buka Pengaturan Akuntansi Penjualan</Link>
                </p>
              )}
              <div className="sd-form-tabs" role="tablist" aria-label="Bagian detail faktur">
                <button type="button" className={invoiceFormTab === "items" ? "is-active" : ""} onClick={() => setInvoiceFormTab("items")}>Barang & Harga</button>
                <button type="button" className={invoiceFormTab === "terms" ? "is-active" : ""} onClick={() => setInvoiceFormTab("terms")}>Tanggal & Ketentuan</button>
              </div>

              <div className="fp-terms sd-pane-terms">
                <div>
                  <label>Tanggal Faktur</label>
                  <SalesInvoiceDateField
                    key={selectedInvoiceId || "detail"}
                    ref={detailDateRef}
                    valueIso={detailDate}
                    onChange={setDetailDate}
                    disabled={!canEditFinancial}
                    testId="invoice-detail-date-input"
                  />
                </div>
                <div>
                  <label>Diskon (Rp)</label>
                  <input
                    type="number"
                    min="0"
                    value={detailDiscount}
                    onChange={(e) => setDetailDiscount(e.target.value)}
                    disabled={!canEditFinancial}
                  />
                </div>
                <div>
                  <label>PPN (%)</label>
                  <input
                    type="number"
                    min="0"
                    max="100"
                    step="any"
                    value={detailPpn}
                    onChange={(e) => setDetailPpn(e.target.value)}
                    disabled={!canEditFinancial}
                  />
                </div>
                <div className="fp-terms-wide">
                  <label>Catatan Faktur</label>
                  <input
                    type="text"
                    value={detailNotes}
                    onChange={(e) => setDetailNotes(e.target.value)}
                    disabled={!canEditFinancial}
                  />
                </div>
                <OtherChargesEditor
                  rows={detailCharges}
                  accounts={chargeAccounts}
                  disabled={!canEditFinancial || detailSaving}
                  showErrors={showDetailChargeErrors}
                  onChange={setDetailCharges}
                  idPrefix="detail"
                />
              </div>

              <div className="fp-items sd-pane-items">
                <div className="sd-lines">
                  <table className="fp-detail-lines">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Barang / Jasa</th>
                        <th className="is-num">Qty</th>
                        <th className="is-center">Unit</th>
                        <th className="is-num">Harga Satuan (Rp)</th>
                        <th className="is-num">Total Line (Rp)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detailCalculations.lines.map((li, index) => (
                        <tr key={li.line_idx}>
                          <td>{index + 1}</td>
                          <td className="fp-item-name">{li.item_name}</td>
                          <td className="is-num">
                            {canEditFinancial ? (
                              <>
                                <input
                                  type="number"
                                  min={1}
                                  max={li.maxQty ?? undefined}
                                  step="1"
                                  value={detailQuantities[li.line_idx] !== undefined ? detailQuantities[li.line_idx] : li.numericQty}
                                  onChange={(e) => {
                                    const val = e.target.value;
                                    setDetailQuantities((prev) => ({ ...prev, [li.line_idx]: val }));
                                  }}
                                  data-testid={`detail-qty-input-${li.line_idx}`}
                                />
                                <div className="fp-item-meta" data-testid={`detail-qty-cap-${li.line_idx}`}>
                                  <div>Qty PO: {Number(li.po_qty || 0).toLocaleString("id-ID")}</div>
                                  <div>Sudah difakturkan lainnya: {Number(li.other_active_invoiced_qty || 0).toLocaleString("id-ID")}</div>
                                  <div>Maksimum Faktur ini: {Number(li.max_qty || 0).toLocaleString("id-ID")}</div>
                                </div>
                                {li.qtyOver && (
                                  <div className="fp-item-meta" data-testid={`detail-qty-over-${li.line_idx}`} style={{ color: "#b91c1c" }}>
                                    {FINALIZE_QTY_OVER_MESSAGE}
                                  </div>
                                )}
                              </>
                            ) : (
                              <span data-testid={`detail-qty-text-${li.line_idx}`}>
                                {Number(li.numericQty || li.quantity || 0).toLocaleString("id-ID")}
                              </span>
                            )}
                          </td>
                          <td className="is-center">{li.unit || "pcs"}</td>
                          <td className="is-num">
                            <input
                              type="number"
                              min="0"
                              step="any"
                              value={detailPrices[li.line_idx] !== undefined ? detailPrices[li.line_idx] : ""}
                              onChange={(e) => {
                                const val = e.target.value;
                                setDetailPrices((prev) => ({ ...prev, [li.line_idx]: val }));
                              }}
                              disabled={!canEditFinancial}
                            />
                          </td>
                          <td className="is-num">{formatRupiah(li.lineTotal)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="fp-breakdown">
                  <div>
                    <span>Subtotal</span>
                    <strong>{formatRupiah(detailCalculations.subtotal)}</strong>
                  </div>
                  {detailCalculations.discount > 0 && (
                    <div>
                      <span>Diskon</span>
                      <strong>- {formatRupiah(detailCalculations.discount)}</strong>
                    </div>
                  )}
                  {detailCalculations.ppnPercent > 0 && (
                    <div>
                      <span>PPN ({detailCalculations.ppnPercent}%)</span>
                      <strong>+ {formatRupiah(detailCalculations.ppn)}</strong>
                    </div>
                  )}
                  <div>
                    <span>Biaya lainnya</span>
                    <strong data-testid="detail-other-charges-total">
                      {detailCalculations.chargesIncomplete ? "Belum lengkap" : formatRupiah(detailCalculations.chargesTotal || 0)}
                    </strong>
                  </div>
                  <div>
                    <span>Total Faktur</span>
                    <strong>{detailCalculations.grandTotal === null ? "Belum lengkap" : formatRupiah(detailCalculations.grandTotal)}</strong>
                  </div>
                </div>
              </div>

              <section className="fp-detail-receipts">
                <h3>Riwayat Penerimaan Pembayaran</h3>
                {(detailData.receipts || []).length === 0 ? (
                  <div className="fp-detail-empty">Belum ada pembayaran yang diterima untuk faktur ini.</div>
                ) : (
                  <div className="sd-lines">
                    <table className="fp-detail-lines">
                      <thead>
                        <tr>
                          <th>No. Penerimaan</th>
                          <th>Tanggal</th>
                          <th className="is-num">Jumlah Penerimaan</th>
                          <th className="is-center">Status</th>
                          <th>Catatan</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detailData.receipts.map((r, idx) => (
                          <tr key={r.id || idx}>
                            <td className="fp-receipt-no">
                              <Link
                                data-testid={`detail-receipt-link-${r.id || idx}`}
                                to={`/penjualan/penerimaan-penjualan?receipt_id=${encodeURIComponent(r.id || "")}`}
                              >
                                {r.receipt_number || r.id}
                              </Link>
                            </td>
                            <td>{formatSalesInvoiceDate(r.receipt_date) || formatDisplayDate(r.receipt_date)}</td>
                            <td className="is-num">{formatRupiah(r.amount)}</td>
                            <td className="is-center">{r.payment_state}</td>
                            <td>{r.notes || "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              {detailErr && (
                <div className="alert-box error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "10px 14px", borderRadius: 8, marginBottom: 16 }}>
                  {detailErr}
                </div>
              )}

              </div>
              <div className="invoice-actions" style={{ display: "flex", justifyContent: "flex-end", gap: 12, paddingRight: 96 }}>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setSearchParams({});
                    setViewMode("list");
                    loadInvoices();
                  }}
                  style={{ padding: "10px 20px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer", fontWeight: 600 }}
                >
                  Kembali ke Daftar
                </button>
                <button
                  type="button"
                  onClick={(e) => handlePrintPdf(e, selectedInvoiceId)}
                  data-testid="btn-print-pdf-detail"
                  disabled={pdfLoadingId === selectedInvoiceId}
                  title="Cetak PDF Faktur Penjualan"
                  style={{
                    padding: "10px 20px",
                    borderRadius: 8,
                    border: "1px solid #0284c7",
                    background: "#f0f9ff",
                    color: "#0284c7",
                    cursor: pdfLoadingId === selectedInvoiceId ? "wait" : "pointer",
                    fontWeight: 600,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <FileText size={16} />
                  {pdfLoadingId === selectedInvoiceId ? "Memuat PDF..." : "Cetak PDF"}
                </button>
                {canEdit && detailStatus === "draft" && !hideLegacyManualAccounting && (
                  <button
                    type="button"
                    data-testid="btn-finalize-invoice"
                    disabled={lifecycleBusy}
                    onClick={() => setFinalizeConfirm(true)}
                    style={{ padding: "10px 24px", borderRadius: 8, background: "#1d4ed8", color: "#fff", border: "none", cursor: "pointer", fontWeight: 600 }}
                  >
                    Finalisasi Invoice
                  </button>
                )}
                {(canCancelDetailBusiness || canCancelDetailLegacy) && (
                  <button
                    type="button"
                    data-testid="btn-cancel-invoice"
                    disabled={lifecycleBusy}
                    onClick={() => setCancelConfirm(true)}
                    style={{ padding: "10px 24px", borderRadius: 8, background: "#fff", color: "#b91c1c", border: "1px solid #fecaca", cursor: "pointer", fontWeight: 600 }}
                  >
                    Batalkan Faktur
                  </button>
                )}
                {showRecoverAuto && (
                  <button
                    type="button"
                    data-testid="btn-recover-auto-invoice"
                    disabled={lifecycleBusy}
                    onClick={async () => {
                      if (!selectedInvoiceId) return;
                      setLifecycleBusy(true);
                      setDetailErr("");
                      try {
                        const res = await api.recoverAutoSalesInvoiceAccounting(selectedInvoiceId);
                        const interpreted = interpretAutoLifecycleResponse(res);
                        if (interpreted.document) setDetailData(interpreted.document);
                        if (!interpreted.ok) setDetailErr(interpreted.message);
                        else {
                          setSuccessMsg(interpreted.message || "Pemulihan akuntansi berhasil");
                          setTimeout(() => setSuccessMsg(""), 2500);
                        }
                        invalidateSalesInvoiceListCache();
                      } catch (err) {
                        const det = err.response?.data?.detail;
                        const interpreted = interpretAutoLifecycleResponse({}, { httpErrorBody: det });
                        if (interpreted.document) setDetailData(interpreted.document);
                        setDetailErr(interpreted.message || (typeof det === "object" ? det.message : det) || "Pemulihan gagal");
                      } finally {
                        setLifecycleBusy(false);
                      }
                    }}
                    style={{ padding: "10px 24px", borderRadius: 8, background: "#b45309", color: "#fff", border: "none", cursor: "pointer", fontWeight: 600 }}
                  >
                    Pulihkan Posting
                  </button>
                )}
                {showPostAccounting && (
                  <button
                    type="button"
                    data-testid="btn-post-accounting"
                    disabled={lifecycleBusy}
                    onClick={() => setPostAccountingConfirm(true)}
                    style={{ padding: "10px 24px", borderRadius: 8, background: "#047857", color: "#fff", border: "none", cursor: "pointer", fontWeight: 600 }}
                  >
                    Post ke Accounting
                  </button>
                )}
                {showReverseAccounting && (
                  <button
                    type="button"
                    data-testid="btn-reverse-accounting"
                    disabled={lifecycleBusy}
                    onClick={() => {
                      setReverseErr("");
                      setReverseReason("");
                      setReverseConfirm(true);
                    }}
                    style={{ padding: "10px 24px", borderRadius: 8, background: "#6d28d9", color: "#fff", border: "none", cursor: "pointer", fontWeight: 600 }}
                  >
                    Reverse Accounting
                  </button>
                )}
                {showReverseBlocked && (
                  <div data-testid="invoice-reverse-blockers" style={{ flex: "1 1 100%", background: "#fff7ed", color: "#9a3412", padding: "10px 14px", borderRadius: 8, fontSize: "0.85rem" }}>
                    Faktur tidak dapat di-reverse karena masih memiliki penerimaan aktif.
                    <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                      {reverseBlockers.map((b) => (
                        <li key={b.id || b.number}>
                          {b.number || b.id} — {receiptAccountingLabel(b.accounting_status)}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {canEditFinancial && (
                  <button
                    type="submit"
                    className="primary"
                    data-testid={canReviseInvoice ? "btn-edit-invoice" : "btn-save-invoice"}
                    disabled={detailSaving}
                    style={{ padding: "10px 24px", borderRadius: 8, background: detailSaving ? "#94a3b8" : "#9333ea", color: "#fff", border: "none", cursor: detailSaving ? "not-allowed" : "pointer", fontWeight: 600 }}
                  >
                    {detailSaving ? "Menyimpan..." : (canReviseInvoice ? "Simpan Edit Faktur" : "Simpan Perubahan")}
                  </button>
                )}
              </div>
            </form>
          ) : (
            <div style={{ padding: 40, textAlign: "center", color: "#64748b" }}>Faktur tidak ditemukan.</div>
          )}
        </div>
      )}

      {deleteConfirm && (
        <div
          className="modal-overlay"
          data-testid="delete-confirm-modal"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15,23,42,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 80,
          }}
        >
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 460, boxShadow: "0 20px 40px rgba(0,0,0,0.15)" }}>
            <h3 style={{ margin: "0 0 8px", fontSize: "1.1rem" }}>
              {canCancelBusiness ? "Batalkan Faktur Penjualan?" : "Hapus Faktur Penjualan?"}
            </h3>
            <p style={{ margin: "0 0 12px", color: "#64748b", fontSize: "0.9rem" }}>
              {canCancelBusiness
                ? "Dokumen berikut akan dibatalkan. Riwayat tetap tersimpan."
                : "Dokumen berikut akan dihapus:"}
            </p>
            <ul data-testid="delete-confirm-numbers" style={{ margin: "0 0 16px", paddingLeft: 18, fontWeight: 700, color: "#9333ea" }}>
              {(deleteConfirm.ids || [deleteConfirm.id]).filter(Boolean).map((id, index) => {
                const number = (deleteConfirm.numbers || [deleteConfirm.number])[index] || id;
                const blocked = deleteHold && deleteBlockedIds.includes(id);
                return (
                  <li key={id}>
                    {number}
                    {blocked && deleteConfirm.ids && (
                      <button
                        type="button"
                        data-testid={`delete-remove-${id}`}
                        onClick={() => removeDeleteCandidate(id)}
                        style={{ marginLeft: 8, fontWeight: 600, fontSize: 12, border: "1px solid #fecaca", background: "#fff", color: "#991b1b", borderRadius: 6, cursor: "pointer" }}
                      >
                        Keluarkan
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
            {deleteErr && (
              <div data-testid="delete-error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>
                {deleteErr}
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={() => { setDeleteConfirm(null); setDeleteErr(""); setDeleteHold(false); setDeleteBlockedIds([]); }} disabled={deleteLoading} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer" }}>
                Batal
              </button>
              <button
                type="button"
                data-testid="btn-confirm-delete"
                onClick={() => (deleteConfirm.ids ? handleBulkDelete() : handleDelete(deleteConfirm.id))}
                disabled={deleteLoading || deleteHold || !(deleteConfirm.ids ? deleteConfirm.ids.length : deleteConfirm.id)}
                style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#dc2626", color: "#fff", fontWeight: 600, cursor: deleteHold ? "not-allowed" : "pointer", opacity: deleteHold ? 0.45 : 1 }}
              >
                {deleteLoading
                  ? (canCancelBusiness ? "Membatalkan..." : "Menghapus...")
                  : (canCancelBusiness ? "Batalkan" : "Hapus")}
              </button>
            </div>
          </div>
        </div>
      )}

      {finalizeConfirm && (
        <div
          className="modal-overlay"
          data-testid="finalize-confirm-modal"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15,23,42,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 80,
          }}
        >
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 480, boxShadow: "0 20px 40px rgba(0,0,0,0.15)" }}>
            <h3 style={{ margin: "0 0 8px", fontSize: "1.1rem" }}>Finalisasi Invoice?</h3>
            <p style={{ margin: "0 0 16px", color: "#334155", fontSize: "0.9rem" }}>
              Finalisasi akan mengunci tanggal, item, harga, diskon, PPN, biaya lainnya, dan total invoice untuk kebutuhan accounting.
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={() => setFinalizeConfirm(false)} disabled={lifecycleBusy} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer" }}>
                Batal
              </button>
              <button
                type="button"
                data-testid="btn-confirm-finalize"
                onClick={handleFinalizeInvoice}
                disabled={lifecycleBusy}
                style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#1d4ed8", color: "#fff", fontWeight: 600, cursor: "pointer" }}
              >
                {lifecycleBusy ? "Memproses..." : "Finalisasi"}
              </button>
            </div>
          </div>
        </div>
      )}

      {postAccountingConfirm && detailData && (
        <div
          className="modal-overlay"
          data-testid="post-accounting-confirm-modal"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15,23,42,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 80,
          }}
        >
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 480, boxShadow: "0 20px 40px rgba(0,0,0,0.15)" }}>
            <h3 style={{ margin: "0 0 8px", fontSize: "1.1rem" }}>Post ke Accounting?</h3>
            <dl data-testid="post-accounting-summary" style={{ margin: "0 0 16px", display: "grid", gridTemplateColumns: "1fr auto", gap: "8px 16px", fontSize: "0.9rem" }}>
              <dt>Piutang</dt>
              <dd data-testid="post-accounting-receivable" style={{ margin: 0, fontWeight: 700 }}>{formatRupiah(detailData.grand_total)}</dd>
              <dt>Pendapatan</dt>
              <dd data-testid="post-accounting-revenue" style={{ margin: 0, fontWeight: 700 }}>{formatRupiah(detailData.after_discount)}</dd>
              <dt>PPN Keluaran</dt>
              <dd data-testid="post-accounting-ppn" style={{ margin: 0, fontWeight: 700 }}>{formatRupiah(detailData.ppn_amount)}</dd>
            </dl>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={() => setPostAccountingConfirm(false)} disabled={lifecycleBusy} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer" }}>
                Batal
              </button>
              <button
                type="button"
                data-testid="btn-confirm-post-accounting"
                onClick={handlePostAccounting}
                disabled={lifecycleBusy}
                style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#047857", color: "#fff", fontWeight: 600, cursor: "pointer" }}
              >
                {lifecycleBusy ? "Memproses..." : "Post ke Accounting"}
              </button>
            </div>
          </div>
        </div>
      )}

      {reverseConfirm && detailData && (
        <div
          className="modal-overlay"
          data-testid="reverse-accounting-confirm-modal"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15,23,42,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 80,
          }}
        >
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 520, boxShadow: "0 20px 40px rgba(0,0,0,0.15)" }}>
            <h3 style={{ margin: "0 0 8px", fontSize: "1.1rem" }}>Reverse Accounting?</h3>
            <p style={{ margin: "0 0 12px", color: "#334155", fontSize: "0.9rem" }}>
              Reversal akan membalik jurnal Faktur dan membatalkan Faktur.
              Qty Faktur akan kembali tersedia untuk pembuatan Faktur pengganti.
            </p>
            <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600, color: "#334155", marginBottom: 6 }}>
              Alasan reversal
            </label>
            <textarea
              data-testid="reverse-reason-input"
              value={reverseReason}
              onChange={(e) => setReverseReason(e.target.value)}
              rows={3}
              placeholder="Minimal 3 karakter"
              style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6, marginBottom: 12, resize: "vertical" }}
            />
            {reverseErr && (
              <div data-testid="reverse-accounting-error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>
                {reverseErr}
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button
                type="button"
                onClick={() => { setReverseConfirm(false); setReverseErr(""); }}
                disabled={lifecycleBusy}
                style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer" }}
              >
                Batal
              </button>
              <button
                type="button"
                data-testid="btn-confirm-reverse-accounting"
                onClick={handleReverseAccounting}
                disabled={lifecycleBusy || String(reverseReason || "").trim().length < 3}
                style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#6d28d9", color: "#fff", fontWeight: 600, cursor: "pointer" }}
              >
                {lifecycleBusy ? "Memproses..." : "Reverse Accounting"}
              </button>
            </div>
          </div>
        </div>
      )}

      {cancelConfirm && (
        <div
          className="modal-overlay"
          data-testid="cancel-confirm-modal"
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15,23,42,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 80,
          }}
        >
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 460, boxShadow: "0 20px 40px rgba(0,0,0,0.15)" }}>
            <h3 style={{ margin: "0 0 8px", fontSize: "1.1rem" }}>Batalkan Faktur?</h3>
            <p style={{ margin: "0 0 16px", color: "#64748b", fontSize: "0.9rem" }}>
              Faktur finalized akan menjadi Cancelled dan tetap tidak dapat dihapus atau diubah. Tidak membuat jurnal.
            </p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={() => setCancelConfirm(false)} disabled={lifecycleBusy} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer" }}>
                Tutup
              </button>
              <button
                type="button"
                data-testid="btn-confirm-cancel-invoice"
                onClick={handleCancelInvoice}
                disabled={lifecycleBusy}
                style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#dc2626", color: "#fff", fontWeight: 600, cursor: "pointer" }}
              >
                {lifecycleBusy ? "Memproses..." : "Batalkan Faktur"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
