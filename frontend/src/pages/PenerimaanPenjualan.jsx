import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import {
  AlertCircle,
  CheckCircle2,
  CreditCard,
  FileText,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
  ArrowRight,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDisplayDate } from "../lib/constants";
import { formatSalesInvoiceDate } from "../lib/salesInvoiceDate";
import { FormSection, computeStatus } from "../components/form-standard";
import SalesInvoiceDateField from "../components/SalesInvoiceDateField";
import LookupSearchSelect from "../components/LookupSearchSelect";
import { showLoadedDocumentCount } from "../lib/salesSubmenuDensity";
import "./ux90SalesCreate.css";
import {
  SALES_LIST_DEFAULT_PAGE_SIZE,
  SALES_LIST_PAGE_SIZES,
  SALES_LIST_TABS,
  normalizeSalesListTab,
  salesFiveTabListMetaFromResponse,
  salesFiveTabListParams,
  salesFiveTabRangeLabel,
  salesListTabLabel,
} from "../lib/salesFiveTabs";
import {
  canShowReceiptCancelUi,
  canShowReceiptEditUi,
  canRecoverAutoAccounting,
  clearIdempotencyIntent,
  ensureIdempotencyIntent,
  interpretAutoLifecycleResponse,
  interpretBusinessCancelResponse,
  interpretReceiptUpdateResponse,
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

import { filterEligibleInvoices, resolvePreselectedInvoiceId, computeReceiptPreview } from "../lib/salesReceipts";
import { clickableDocumentRowProps } from "../lib/clickableListRow";
import SalesReceiptProofViewer from "./SalesReceiptProofViewer";
import {
  RECEIPT_PROOF_ACCEPT,
  buildReceiptProofPayload,
  validateReceiptProofFile,
} from "../lib/salesReceiptPaymentProof";
import { listIdentityBranchId, listIdentityUserId } from "../lib/listIdentity";
import { salesReceiptListCache } from "../lib/smartListCaches";

export default function PenerimaanPenjualan() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, hasPerm } = useAuth();
  const listUserId = listIdentityUserId(user);
  const listBranchId = listIdentityBranchId(user);

  const canCreate =
    hasPerm("sales.receipt.create") ||
    hasPerm("sales.invoice.create") ||
    hasPerm("sales_order.create") ||
    hasPerm("po.edit") ||
    user?.role === "admin";
  const canPostAccounting = hasPerm("sales.receipt.accounting.post") || user?.role === "admin";
  const canReverseAccounting = hasPerm("sales.receipt.accounting.reverse") || user?.role === "admin";

  const queryInvoiceId = searchParams.get("invoice_id") || "";
  const queryReceiptId = searchParams.get("receipt_id") || "";

  // List State
  const [receipts, setReceipts] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listRefreshing, setListRefreshing] = useState(false);
  const [listErr, setListErr] = useState("");
  const [listSyncErr, setListSyncErr] = useState("");
  const [listShowingCache, setListShowingCache] = useState(false);
  const [searchQuery, setSearchQuery] = useState(() => searchParams.get("q") || "");
  const [listTab, setListTab] = useState(() => normalizeSalesListTab(searchParams.get("tab") || "active"));
  const [page, setPage] = useState(() => {
    const value = Number(searchParams.get("page"));
    return Number.isFinite(value) && value >= 1 ? Math.floor(value) : 1;
  });
  const [pageSize, setPageSize] = useState(() => {
    const value = Number(searchParams.get("page_size"));
    return SALES_LIST_PAGE_SIZES.includes(value) ? value : SALES_LIST_DEFAULT_PAGE_SIZE;
  });
  const [listMeta, setListMeta] = useState({
    total: null,
    tabTotal: 0,
    capped: false,
    counts: { all: 0, active: 0, completed: 0, pending: 0, cancelled: 0 },
  });
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");
  const createIdempotencyRef = useRef({ signature: "", key: "" });
  const cancelIdempotencyRef = useRef({ signature: "", key: "" });
  const requestSeq = useRef(0);
  const skipPageResetRef = useRef(true);
  const prevListUserRef = useRef(listUserId);

  // Modal / Create State
  const [isModalOpen, setIsModalOpen] = useState(Boolean(queryInvoiceId) || searchParams.get("action") === "create");
  const [invoicesWithBalance, setInvoicesWithBalance] = useState([]);
  const [invoicesLoading, setInvoicesLoading] = useState(false);
  const [selectedInvoiceId, setSelectedInvoiceId] = useState("");
  const [selectedInvoice, setSelectedInvoice] = useState(null);
  const [receiptAmount, setReceiptAmount] = useState("");
  const [allocationAmount, setAllocationAmount] = useState("");
  const [allocationTouched, setAllocationTouched] = useState(false);
  const [receiptDate, setReceiptDate] = useState(getTodayString());
  const [receiptNotes, setReceiptNotes] = useState("");
  const [proofFile, setProofFile] = useState(null);
  const [proofThumbUrl, setProofThumbUrl] = useState("");
  const [proofErr, setProofErr] = useState("");
  const [cashBankId, setCashBankId] = useState("");
  const [cashBanks, setCashBanks] = useState([]);
  const [cashBanksLoading, setCashBanksLoading] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [createErr, setCreateErr] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const [creditOffer, setCreditOffer] = useState(null);
  const [creditOfferErr, setCreditOfferErr] = useState("");
  const [creditPanelOpen, setCreditPanelOpen] = useState(false);
  const [creditSourceId, setCreditSourceId] = useState("");
  const [creditUseAmount, setCreditUseAmount] = useState("");
  const [creditBusy, setCreditBusy] = useState(false);
  const [creditMsg, setCreditMsg] = useState("");
  const [proofViewerReceipt, setProofViewerReceipt] = useState(null);
  const [detailProofThumbUrl, setDetailProofThumbUrl] = useState("");
  const creditRequestRef = useRef({ key: "", signature: "" });

  // Detail Modal State
  const [detailReceipt, setDetailReceipt] = useState(null);
  const [detailEditMode, setDetailEditMode] = useState(false);
  const [editReceiptDate, setEditReceiptDate] = useState("");
  const [editAmount, setEditAmount] = useState("");
  const [editAllocated, setEditAllocated] = useState("");
  const [editCashBankId, setEditCashBankId] = useState("");
  const [editNotes, setEditNotes] = useState("");
  const [editBusy, setEditBusy] = useState(false);
  const [editErr, setEditErr] = useState("");
  const [editSuccess, setEditSuccess] = useState("");
  const editIdempotencyRef = useRef({ signature: "", key: "" });
  const [accountingTarget, setAccountingTarget] = useState(null);
  const [accountingBusy, setAccountingBusy] = useState(false);
  const [accountingErr, setAccountingErr] = useState("");
  const [receiptLifecycleCapability, setReceiptLifecycleCapability] = useState(null);

  const detailCapability = detailReceipt?.lifecycle_capability || receiptLifecycleCapability;
  // List Batalkan: capability READY + opt-in perm (document eligibility checked per row).
  const canCancelBusiness = canShowReceiptCancelUi(hasPerm, {
    capability: receiptLifecycleCapability,
    document: null,
  });
  // Detail Edit: perm + edit_capability + posted edit_revision READY when applicable.
  const canEditReceipt = canShowReceiptEditUi(hasPerm, {
    capability: detailCapability,
    document: detailReceipt,
  });
  const canCancelDetail = canShowReceiptCancelUi(hasPerm, {
    capability: detailCapability,
    document: detailReceipt,
  });

  const hideLegacyManualAccounting = shouldHideLegacyManualAccounting(detailCapability);
  const showLegacyPostReverse = !hideLegacyManualAccounting;

  const beginReceiptEdit = () => {
    if (!detailReceipt || !canEditReceipt) return;
    const cap = detailReceipt.edit_capability || {};
    if (!cap.editable) {
      setEditErr(cap.reason || "Penerimaan tidak dapat diubah.");
      return;
    }
    setEditReceiptDate(String(detailReceipt.receipt_date || getTodayString()).slice(0, 10));
    setEditAmount(String(detailReceipt.amount ?? ""));
    setEditAllocated(String(detailReceipt.allocated_amount ?? detailReceipt.amount ?? ""));
    setEditCashBankId(String(detailReceipt.cash_bank_id || ""));
    setEditNotes(String(detailReceipt.notes || ""));
    setEditErr("");
    setEditSuccess("");
    setDetailEditMode(true);
    if (!cashBanks.length) {
      api.salesReceiptCashBankOptions().then((res) => {
        setCashBanks(res.items || res || []);
      }).catch(() => {});
    }
  };

  const handleSaveReceiptEdit = async (e) => {
    if (e) e.preventDefault();
    if (editBusy) return;
    if (!detailReceipt?.id || !canEditReceipt) return;
    const cap = detailReceipt.edit_capability || {};
    if (!cap.editable) {
      setEditErr(cap.reason || "Penerimaan tidak dapat diubah.");
      return;
    }
    const amountNum = Number(editAmount);
    const allocatedNum = Number(editAllocated);
    if (!editReceiptDate) {
      setEditErr("Tanggal penerimaan wajib diisi.");
      return;
    }
    if (!(amountNum > 0)) {
      setEditErr("Jumlah penerimaan harus lebih besar dari 0.");
      return;
    }
    if (!editCashBankId) {
      setEditErr("Kas/Bank wajib dipilih.");
      return;
    }
    setEditBusy(true);
    setEditErr("");
    setEditSuccess("");
    const signature = [
      detailReceipt.id,
      editReceiptDate,
      amountNum,
      allocatedNum,
      editCashBankId,
      editNotes.trim(),
    ].join("|");
    const idempotencyKey = ensureIdempotencyIntent(editIdempotencyRef, signature, { prefix: "rcpt-edit" });
    const payload = {
      receipt_date: editReceiptDate,
      amount: amountNum,
      allocated_amount: allocatedNum,
      cash_bank_id: editCashBankId,
      notes: editNotes.trim(),
      idempotency_key: idempotencyKey,
    };
    try {
      const res = await api.updateSalesReceipt(detailReceipt.id, payload);
      const interpreted = interpretReceiptUpdateResponse(res);
      if (!interpreted.ok) {
        setEditErr(interpreted.message);
        if (interpreted.document) setDetailReceipt(interpreted.document);
        return;
      }
      clearIdempotencyIntent(editIdempotencyRef);
      setDetailReceipt(interpreted.document || res.receipt);
      setDetailEditMode(false);
      setEditSuccess(interpreted.message);
      void reloadReceiptListAuthoritative();
    } catch (err) {
      const det = err.response?.data?.detail;
      const body = typeof det === "object" && det ? det : { message: det || "Gagal menyimpan koreksi penerimaan." };
      const interpreted = interpretReceiptUpdateResponse({}, { httpErrorBody: body });
      setEditErr(interpreted.message || body.message || "Gagal menyimpan koreksi penerimaan.");
      if (body.receipt) setDetailReceipt(body.receipt);
    } finally {
      setEditBusy(false);
    }
  };

  const resetReceiptListPosition = useCallback(() => {
    setPage(1);
    setSelectedIds(new Set());
  }, []);

  const changeReceiptTab = (tab) => {
    const next = normalizeSalesListTab(tab);
    if (next === listTab) return;
    setListTab(next);
    resetReceiptListPosition();
  };

  const applyReceiptListPayload = useCallback((rows, meta, { fromCache = false } = {}) => {
    setReceipts(rows);
    setListMeta(meta);
    setListShowingCache(Boolean(fromCache));
    if (!fromCache) setListSyncErr("");
  }, []);

  // Load receipts — SWR display cache + full-page refetch of the same query.
  const loadReceipts = useCallback(async ({ force = false } = {}) => {
    const cacheKey = salesReceiptListCache.buildKey({
      u: listUserId,
      b: listBranchId,
      tab: listTab,
      q: searchQuery,
      page,
      pageSize,
    });
    const cached = salesReceiptListCache.get(cacheKey);
    const hasCache = Boolean(cached?.data);
    if (hasCache) {
      applyReceiptListPayload(
        cached.data.receipts || [],
        cached.data.listMeta || {
          total: null,
          tabTotal: 0,
          capped: false,
          counts: { all: 0, active: 0, completed: 0, pending: 0, cancelled: 0 },
        },
        { fromCache: true },
      );
      setSelectedIds(new Set());
    } else {
      applyReceiptListPayload([], {
        total: null,
        tabTotal: 0,
        capped: false,
        counts: { all: 0, active: 0, completed: 0, pending: 0, cancelled: 0 },
      }, { fromCache: false });
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
      const params = salesFiveTabListParams({
        listTab,
        page,
        pageSize,
        q: searchQuery,
      });
      const res = await api.listSalesReceipts(params);
      if (seq !== requestSeq.current) return;
      const rows = res.receipts || [];
      const meta = salesFiveTabListMetaFromResponse(res, { rowCount: rows.length });
      salesReceiptListCache.set(cacheKey, { receipts: rows, listMeta: meta, res });
      applyReceiptListPayload(rows, meta, { fromCache: false });
      setSelectedIds(new Set());
    } catch (e) {
      if (seq !== requestSeq.current) return;
      const det = e.response?.data?.detail;
      const message = typeof det === "object" ? det.message : det || "Gagal memuat daftar Penerimaan Penjualan";
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
  }, [applyReceiptListPayload, listBranchId, listTab, listUserId, page, pageSize, searchQuery]);

  const reloadReceiptListAuthoritative = useCallback(async () => {
    salesReceiptListCache.invalidate();
    setListShowingCache(false);
    setListSyncErr("");
    await loadReceipts({ force: true });
  }, [loadReceipts]);

  useEffect(() => {
    if (skipPageResetRef.current) {
      skipPageResetRef.current = false;
      return;
    }
    setPage(1);
  }, [searchQuery, listTab, pageSize]);

  useEffect(() => {
    if (prevListUserRef.current === listUserId) return;
    salesReceiptListCache.clear();
    prevListUserRef.current = listUserId;
  }, [listUserId]);

  useEffect(() => {
    const onVisible = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      if (!salesReceiptListCache.shouldRevalidateOnFocus()) return;
      loadReceipts({ force: false });
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [loadReceipts]);

  // URL sync placed after modal state exists — keep list query for rememberSearch.
  useEffect(() => {
    if (isModalOpen || queryInvoiceId || queryReceiptId) return;
    if (searchParams.get("action") === "create") return;
    const next = new URLSearchParams();
    if (searchQuery.trim()) next.set("q", searchQuery.trim());
    if (listTab && listTab !== "active") next.set("tab", listTab);
    if (page > 1) next.set("page", String(page));
    if (pageSize !== SALES_LIST_DEFAULT_PAGE_SIZE) next.set("page_size", String(pageSize));
    if (next.toString() === searchParams.toString()) return;
    setSearchParams(next, { replace: true });
  }, [
    searchQuery,
    listTab,
    page,
    pageSize,
    isModalOpen,
    queryInvoiceId,
    queryReceiptId,
    searchParams,
    setSearchParams,
  ]);

  const receiptDeletable = useCallback((r) => {
    if (canCancelBusiness) {
      const progress = String(r?.business_cancel_progress || "").toLowerCase();
      if (progress === "complete" || progress === "completed") return false;
      if (String(r?.list_tab || "").toLowerCase() === "cancelled") return false;
      if (String(r?.financial_status || "").toLowerCase() === "cancelled") return false;
      return true;
    }
    return r?.can_delete !== false;
  }, [canCancelBusiness]);

  const deletableReceipts = useMemo(
    () => receipts.filter((r) => receiptDeletable(r)),
    [receipts, receiptDeletable]
  );
  // Soft-cancel only — never expose hard-delete Hapus when activating Edit/Batalkan.
  const showDeleteControls = canCancelBusiness && listTab !== "cancelled";
  const pageCount = Math.max(1, Math.ceil((Number(listMeta.total) || 0) / pageSize) || 1);
  const tabLabel = salesListTabLabel(listTab);
  const toggleSelect = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    if (deletableReceipts.length > 0 && selectedIds.size === deletableReceipts.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(deletableReceipts.map((r) => r.id)));
    }
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
    setDeleteLoading(true);
    setDeleteErr("");
    try {
      if (canCancelBusiness) {
        const key = ensureIdempotencyIntent(cancelIdempotencyRef, `cancel-receipt|${id}`, { prefix: "rcpt-cancel" });
        const res = await api.businessCancelSalesReceipt(id, {
          reason: "Batalkan dari daftar Penerimaan Penjualan",
          idempotency_key: key,
        });
        const interpreted = interpretBusinessCancelResponse(res);
        if (!interpreted.ok) {
          setDeleteErr(interpreted.message);
          // Keep modal + same idempotency key. Refresh list only to surface Pending status.
          if (interpreted.pending) void reloadReceiptListAuthoritative();
          return;
        }
        clearIdempotencyIntent(cancelIdempotencyRef);
        setDeleteConfirm(null);
        setSelectedIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        void reloadReceiptListAuthoritative();
        return;
      }
      await api.deleteSalesReceipt(id);
      setDeleteConfirm(null);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      void reloadReceiptListAuthoritative();
    } catch (e) {
      if (canCancelBusiness) {
        const interpreted = interpretBusinessCancelResponse({}, {
          httpErrorBody: e.response?.data?.detail,
          statusCode: e.response?.status,
        });
        setDeleteErr(interpreted.message);
        return;
      }
      setDeleteErr(formatDeleteError(e.response?.data?.detail));
    } finally {
      setDeleteLoading(false);
    }
  };


  const [pdfLoadingId, setPdfLoadingId] = useState(null);

  const handlePrintPdf = async (e, receiptId) => {
    if (e) e.stopPropagation();
    if (!receiptId) return;
    setPdfLoadingId(receiptId);
    try {
      const blob = await api.downloadSalesReceiptPdf(receiptId);
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank");
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (err) {
      const det = err.response?.data?.detail;
      const msg = typeof det === "object" ? det?.message || JSON.stringify(det) : det || "Gagal memuat PDF Penerimaan Penjualan.";
      setListErr(msg);
    } finally {
      setPdfLoadingId(null);
    }
  };

  const handleBulkDelete = async () => {
    setDeleteLoading(true);
    setDeleteErr("");
    try {
      const ids = deleteConfirm?.ids || Array.from(selectedIds);
      if (canCancelBusiness) {
        for (const id of ids) {
          const key = ensureIdempotencyIntent(cancelIdempotencyRef, `cancel-receipt|${id}`, { prefix: "rcpt-cancel" });
          const res = await api.businessCancelSalesReceipt(id, {
            reason: "Batalkan terpilih dari daftar Penerimaan Penjualan",
            idempotency_key: key,
          });
          const interpreted = interpretBusinessCancelResponse(res);
          if (!interpreted.ok) {
            setDeleteErr(interpreted.message);
            if (interpreted.pending) void reloadReceiptListAuthoritative();
            return;
          }
          clearIdempotencyIntent(cancelIdempotencyRef);
        }
        setDeleteConfirm(null);
        setSelectedIds(new Set());
        void reloadReceiptListAuthoritative();
        return;
      }
      await api.bulkDeleteSalesReceipts(ids);
      setDeleteConfirm(null);
      setSelectedIds(new Set());
      void reloadReceiptListAuthoritative();
    } catch (e) {
      if (canCancelBusiness) {
        const interpreted = interpretBusinessCancelResponse({}, {
          httpErrorBody: e.response?.data?.detail,
          statusCode: e.response?.status,
        });
        setDeleteErr(interpreted.message);
        return;
      }
      setDeleteErr(formatDeleteError(e.response?.data?.detail));
    } finally {
      setDeleteLoading(false);
    }
  };

  // Load unpaid / partially paid invoices for the dropdown
  const accountingLabel = (status) => {
    const raw = String(status || "not_posted").toLowerCase();
    if (raw === "posted") return "Sudah posting";
    if (raw === "reversed") return "Dibalik";
    return "Belum posting";
  };

  const loadCashBanks = useCallback(async () => {
    setCashBanksLoading(true);
    try {
      const res = await api.salesReceiptCashBankOptions();
      setCashBanks(res.items || []);
    } catch {
      setCashBanks([]);
    } finally {
      setCashBanksLoading(false);
    }
  }, []);

  const loadInvoicesForReceipt = useCallback(async () => {
    if (!canCreate) return;
    setInvoicesLoading(true);
    try {
      const res = await api.listSalesInvoices({});
      const allInvoices = res.invoices || [];
      // Filter invoices strictly by outstanding_amount > 0 (no bypass)
      const eligible = filterEligibleInvoices(allInvoices);
      setInvoicesWithBalance(eligible);
      if (queryInvoiceId) {
        const preselected = resolvePreselectedInvoiceId(eligible, queryInvoiceId);
        setSelectedInvoiceId(preselected);
      }
    } catch {
      // Best effort
    } finally {
      setInvoicesLoading(false);
    }
  }, [canCreate, queryInvoiceId]);

  useEffect(() => {
    loadReceipts();
  }, [loadReceipts]);

  useEffect(() => {
    let cancelled = false;
    api.salesReceiptLifecycleCapability()
      .then((res) => {
        if (!cancelled) setReceiptLifecycleCapability(res?.lifecycle_capability || res || null);
      })
      .catch(() => {
        if (!cancelled) setReceiptLifecycleCapability(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (isModalOpen) {
      loadInvoicesForReceipt();
      loadCashBanks();
    }
  }, [isModalOpen, loadInvoicesForReceipt, loadCashBanks]);

  // When selected invoice changes, fetch its full detail
  useEffect(() => {
    if (!selectedInvoiceId) {
      setSelectedInvoice(null);
      return;
    }
    let isMounted = true;
    (async () => {
      try {
        const res = await api.getSalesInvoice(selectedInvoiceId);
        if (isMounted) {
          const inv = res.invoice || res;
          setSelectedInvoice(inv);
          if (!receiptAmount && inv.outstanding_amount > 0) {
            setReceiptAmount(String(inv.outstanding_amount));
            setAllocationAmount(String(inv.outstanding_amount));
            setAllocationTouched(false);
          }
        }
      } catch {
        // Handled
      }
    })();
    return () => {
      isMounted = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedInvoiceId]);

  // Live preview calculation for receipt modal
  const previewCalc = useMemo(() => {
    return computeReceiptPreview(
      selectedInvoice,
      receiptAmount,
      allocationTouched ? allocationAmount : undefined,
    );
  }, [selectedInvoice, receiptAmount, allocationAmount, allocationTouched]);

  useEffect(() => {
    if (!selectedInvoiceId || !canPostAccounting) {
      setCreditOffer(null);
      setCreditOfferErr("");
      return undefined;
    }
    let active = true;
    setCreditOffer(null);
    setCreditOfferErr("");
    api.listSalesCustomerCredit({ invoice_id: selectedInvoiceId })
      .then((res) => {
        if (active) setCreditOffer(res);
      })
      .catch((error) => {
        if (!active) return;
        setCreditOffer(null);
        setCreditOfferErr(error?.response?.data?.detail || "Saldo pelanggan gagal dimuat.");
      });
    return () => {
      active = false;
    };
  }, [selectedInvoiceId, canPostAccounting]);

  const handleOpenCreateModal = (invId = "") => {
    setSelectedInvoiceId(invId || "");
    setReceiptAmount("");
    setAllocationAmount("");
    setAllocationTouched(false);
    setCreditPanelOpen(false);
    setReceiptDate(getTodayString());
    setReceiptNotes("");
    setProofFile(null);
    setProofErr("");
    setCashBankId("");
    setCreateErr("");
    setIsModalOpen(true);
  };

  const openReceiptDetail = useCallback(async (receipt) => {
    if (!receipt?.id) return;
    setAccountingErr("");
    setDetailEditMode(false);
    setEditErr("");
    setEditSuccess("");
    clearIdempotencyIntent(editIdempotencyRef);
    try {
      const fresh = await api.getSalesReceipt(receipt.id);
      setDetailReceipt(fresh.receipt || fresh);
    } catch {
      setDetailReceipt(receipt);
    }
  }, []);

  useEffect(() => {
    if (!queryReceiptId || isModalOpen) return;
    if (detailReceipt?.id === queryReceiptId) return;
    openReceiptDetail({ id: queryReceiptId });
  }, [queryReceiptId, isModalOpen, detailReceipt?.id, openReceiptDetail]);

  const handleCloseCreateModal = () => {
    setIsModalOpen(false);
    setSelectedInvoiceId("");
    setSelectedInvoice(null);
    setReceiptAmount("");
    setAllocationAmount("");
    setAllocationTouched(false);
    setCreditPanelOpen(false);
    setReceiptNotes("");
    setProofFile(null);
    setProofErr("");
    setCashBankId("");
    setCreateErr("");
    setSearchParams({});
  };

  const handleConfirmAccounting = async () => {
    const target = accountingTarget;
    if (!target?.receipt?.id) return;
    if (target.action === "post" && !target.receipt.cash_bank_id && !target.cash_bank_id) {
      setAccountingErr("Silakan pilih Kas/Bank terlebih dahulu.");
      return;
    }
    setAccountingBusy(true);
    setAccountingErr("");
    try {
      const res = target.action === "reverse"
        ? await api.reverseSalesReceiptAccounting(target.receipt.id)
        : await api.postSalesReceiptAccounting(
            target.receipt.id,
            target.cash_bank_id ? { cash_bank_id: target.cash_bank_id } : {}
          );
      const updated = res.receipt || res;
      setAccountingTarget(null);
      setSuccessMsg(res.message || (target.action === "reverse"
        ? "Penerimaan di-reverse dari accounting."
        : "Penerimaan diposting ke accounting."));
      if (detailReceipt?.id === target.receipt.id) setDetailReceipt(updated);
      void reloadReceiptListAuthoritative();
      setTimeout(() => setSuccessMsg(""), 2500);
    } catch (e) {
      const det = e.response?.data?.detail;
      setAccountingErr(typeof det === "object" ? det.message : det || "Gagal memproses accounting.");
    } finally {
      setAccountingBusy(false);
    }
  };

  const handleProofFileChange = (e) => {
    const f = e.target.files?.[0] || null;
    e.target.value = "";
    setProofErr("");
    if (!f) {
      setProofFile(null);
      return;
    }
    const check = validateReceiptProofFile(f);
    if (!check.ok) {
      setProofFile(null);
      setProofErr(check.error);
      return;
    }
    setProofFile(f);
  };

  useEffect(() => {
    if (!proofFile || !String(proofFile.type || "").startsWith("image/")) {
      setProofThumbUrl("");
      return undefined;
    }
    const url = URL.createObjectURL(proofFile);
    setProofThumbUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [proofFile]);

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    setDetailProofThumbUrl("");
    const receipt = detailReceipt;
    const ct = String(receipt?.payment_proof?.content_type || "");
    if (!receipt?.id || !receipt?.has_payment_proof || !ct.startsWith("image/")) {
      return undefined;
    }
    api.downloadSalesReceiptPaymentProof(receipt.id, false)
      .then((file) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(file);
        setDetailProofThumbUrl(objectUrl);
      })
      .catch(() => {
        if (active) setDetailProofThumbUrl("");
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [detailReceipt]);

  // Close create modal on Escape key
  useEffect(() => {
    if (!isModalOpen) return;
    const handleKeyDown = (e) => {
      if (e.key === "Escape") {
        handleCloseCreateModal();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isModalOpen]);

  const handlePayFull = () => {
    if (selectedInvoice && selectedInvoice.outstanding_amount > 0) {
      setReceiptAmount(String(selectedInvoice.outstanding_amount));
      setAllocationAmount(String(selectedInvoice.outstanding_amount));
      setAllocationTouched(false);
    }
  };

  const handleSaveReceipt = async (e) => {
    e.preventDefault();
    if (!selectedInvoiceId) {
      setCreateErr("Silakan pilih Faktur Penjualan terlebih dahulu.");
      return;
    }
    const numAmount = Number(receiptAmount);
    if (isNaN(numAmount) || numAmount <= 0) {
      setCreateErr("Jumlah penerimaan harus lebih besar dari 0.");
      return;
    }
    if (!cashBankId) {
      setCreateErr("Silakan pilih Kas/Bank terlebih dahulu.");
      return;
    }
    if (previewCalc?.allocationExceedsOutstanding) {
      setCreateErr(`Alokasi (${formatRupiah(previewCalc.allocated)}) melebihi sisa tagihan (${formatRupiah(previewCalc.outstandingBefore)}).`);
      return;
    }
    if (previewCalc?.allocationExceedsReceived) {
      setCreateErr("Total alokasi tidak boleh melebihi jumlah diterima.");
      return;
    }
    if (!previewCalc?.allocated || previewCalc.allocated <= 0) {
      setCreateErr("Alokasi ke faktur harus lebih besar dari 0.");
      return;
    }
    const proofCheck = validateReceiptProofFile(proofFile);
    if (!proofCheck.ok) {
      setProofErr(proofCheck.error);
      setCreateErr(proofCheck.error);
      return;
    }

    setCreateBusy(true);
    setCreateErr("");
    setProofErr("");
    setSuccessMsg("");

    try {
      const payment_proof = await buildReceiptProofPayload(proofFile);
      const signature = [
        selectedInvoiceId,
        receiptDate || getTodayString(),
        numAmount,
        previewCalc.allocated,
        cashBankId,
        receiptNotes.trim(),
        payment_proof?.name || "",
      ].join("|");
      const idempotencyKey = ensureIdempotencyIntent(createIdempotencyRef, signature, { prefix: "rcpt-create" });
      const payload = {
        invoice_id: selectedInvoiceId,
        receipt_date: receiptDate || getTodayString(),
        amount: numAmount,
        allocated_amount: previewCalc.allocated,
        notes: receiptNotes.trim(),
        cash_bank_id: cashBankId,
        payment_proof,
        idempotency_key: idempotencyKey,
      };
      try {
        const res = await api.createSalesReceipt(payload);
        const interpreted = interpretAutoLifecycleResponse(res);
        if (interpreted.mode === "auto" && !interpreted.ok) {
          setCreateErr(interpreted.message);
          return;
        }
        clearIdempotencyIntent(createIdempotencyRef);
        window.dispatchEvent(new CustomEvent("eternal:sales-receipt-saved"));
        setSuccessMsg(interpreted.message || res.message || "Penerimaan Penjualan berhasil disimpan!");
        setTimeout(() => {
          setSuccessMsg("");
          handleCloseCreateModal();
          void reloadReceiptListAuthoritative();
        }, 900);
      } catch (err) {
        const det = err.response?.data?.detail;
        if (det && typeof det === "object" && ("financial_complete" in det || "auto_accounting_status" in det)) {
          const interpreted = interpretAutoLifecycleResponse({}, { httpErrorBody: det });
          setCreateErr(interpreted.message);
          return;
        }
        setCreateErr(typeof det === "object" ? det.message : det || err.message || "Gagal menyimpan Penerimaan Penjualan");
      }
    } catch (err) {
      setCreateErr(err.message || "Gagal menyimpan Penerimaan Penjualan");
    } finally {
      setCreateBusy(false);
    }
  };

  const handleApplyCredit = async () => {
    const source = (creditOffer?.sources || []).find((row) => row.receipt_id === creditSourceId);
    const useAmount = Number(creditUseAmount);
    if (!selectedInvoiceId || !source) {
      setCreditMsg("Pilih sumber saldo dan faktur tujuan.");
      return;
    }
    if (!useAmount || useAmount <= 0) {
      setCreditMsg("Nominal pemakaian saldo harus lebih besar dari 0.");
      return;
    }
    setCreditBusy(true);
    setCreditMsg("");
    try {
      const signature = `${source.receipt_id}|${selectedInvoiceId}|${useAmount}`;
      if (creditRequestRef.current.signature !== signature) {
        creditRequestRef.current = {
          signature,
          key: `credit-${typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : signature}`,
        };
      }
      const res = await api.applySalesCustomerCredit({
        source_receipt_id: source.receipt_id,
        invoice_id: selectedInvoiceId,
        amount: useAmount,
        idempotency_key: creditRequestRef.current.key,
      });
      creditRequestRef.current = { key: "", signature: "" };
      setCreditMsg(res.message || "Saldo pelanggan dipakai.");
      const refreshed = await api.getSalesInvoice(selectedInvoiceId);
      setSelectedInvoice(refreshed.invoice || refreshed);
      const offer = await api.listSalesCustomerCredit({ invoice_id: selectedInvoiceId });
      setCreditOffer(offer);
      setCreditOfferErr("");
      void reloadReceiptListAuthoritative();
    } catch (error) {
      const detail = error?.response?.data?.detail;
      setCreditMsg(typeof detail === "object" ? detail.message : detail || "Pemakaian saldo gagal.");
    } finally {
      setCreditBusy(false);
    }
  };

  const handleReverseCredit = async (applicationId) => {
    setCreditBusy(true);
    setCreditMsg("");
    try {
      const res = await api.reverseSalesCustomerCredit(applicationId);
      setCreditMsg(res.message || "Pemakaian saldo dibalik.");
      if (detailReceipt?.id) {
        const fresh = await api.getSalesReceipt(detailReceipt.id);
        setDetailReceipt(fresh.receipt || fresh);
      }
      void reloadReceiptListAuthoritative();
    } catch (error) {
      const detail = error?.response?.data?.detail;
      setCreditMsg(typeof detail === "object" ? detail.message : detail || "Pembalikan pemakaian gagal.");
    } finally {
      setCreditBusy(false);
    }
  };

  const showReceiptCount = showLoadedDocumentCount({
    loading: listLoading,
    error: listErr,
    query: searchQuery,
  });

  return (
    <div
      className="page-container penjualan-page penjualan-workspace sd-density sd-list-mode"
      data-testid="penerimaan-penjualan-page"
    >
      <header className="sd-list-header">
        <div className="sd-list-heading">
          <h1>Penerimaan Penjualan</h1>
          {showReceiptCount && (
            <div className="sd-list-summary" data-testid="receipt-list-count">
              <strong>{Number(listMeta.tabTotal || listMeta.total || receipts.length).toLocaleString("id-ID")}</strong>
              <span>penerimaan · {tabLabel}</span>
              {listRefreshing ? <span data-testid="receipt-list-refreshing"> · menyegarkan</span> : null}
            </div>
          )}
          {!showReceiptCount && listRefreshing ? (
            <div className="sd-list-summary" data-testid="receipt-list-refreshing">menyegarkan</div>
          ) : null}
        </div>
        <div className="sd-list-header-actions">
          {showDeleteControls && selectedIds.size > 0 && (
            <button
              type="button"
              className="sd-btn-danger"
              data-testid="btn-hapus-terpilih"
              onClick={() => {
                setDeleteErr("");
                setDeleteConfirm({
                  ids: Array.from(selectedIds),
                  numbers: receipts.filter((r) => selectedIds.has(r.id)).map((r) => r.receipt_number),
                });
              }}
            >
              <Trash2 size={15} /> Hapus Terpilih ({selectedIds.size})
            </button>
          )}
          <button
            type="button"
            className="sd-list-refresh"
            onClick={() => loadReceipts({ force: true })}
            disabled={listLoading || listRefreshing}
            title="Segarkan"
            aria-label="Segarkan"
          >
            <RefreshCw size={16} className={(listLoading || listRefreshing) ? "animate-spin" : ""} />
          </button>
          {canCreate && (
            <button
              type="button"
              className="sd-btn-primary"
              data-testid="btn-create-receipt"
              onClick={() => handleOpenCreateModal()}
            >
              <Plus size={16} />
              Buat Penerimaan
            </button>
          )}
        </div>
      </header>

      <div className="sd-list-chrome">
        <div className="fp-invoice-tabs" role="tablist" aria-label="Klasifikasi penerimaan" data-testid="receipt-list-tabs">
          {SALES_LIST_TABS.map((tab) => {
            const count = Number(listMeta.counts?.[tab.id] || 0);
            const selected = listTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={selected}
                className={selected ? "is-active" : ""}
                data-testid={`tab-receipt-${tab.testIdSuffix}`}
                onClick={() => changeReceiptTab(tab.id)}
              >
                {tab.label}
                <span className="fp-tab-count" data-testid={`tab-receipt-count-${tab.testIdSuffix}`}>
                  {count.toLocaleString("id-ID")}
                </span>
              </button>
            );
          })}
        </div>

        <div className="sd-list-toolbar">
          <div className="sd-list-search">
            <Search size={16} />
            <input
              type="text"
              className="form-input"
              data-testid="search-receipt-input"
              placeholder="Cari no penerimaan, faktur, PO, pelanggan..."
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                resetReceiptListPosition();
              }}
            />
          </div>
        </div>
      </div>

      {listErr && (
        <div
          style={{
            padding: "10px 14px",
            backgroundColor: "#fef2f2",
            border: "1px solid #fecaca",
            color: "#b91c1c",
            borderRadius: 6,
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
          }}
        >
          <AlertCircle size={16} />
          <span>{listErr}</span>
        </div>
      )}
      {listSyncErr && (
        <div
          data-testid="receipt-list-sync-error"
          role="status"
          style={{
            padding: "10px 14px",
            backgroundColor: "#fffbeb",
            border: "1px solid #fde68a",
            color: "#92400e",
            borderRadius: 6,
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
          }}
        >
          <AlertCircle size={16} />
          <span>
            {listSyncErr}
            {listShowingCache ? " Data di bawah belum disinkronkan ulang." : ""}
          </span>
        </div>
      )}

      {successMsg && !isModalOpen && (
        <div
          data-testid="receipt-accounting-success"
          style={{
            padding: "10px 14px",
            backgroundColor: "#ecfdf5",
            border: "1px solid #a7f3d0",
            color: "#065f46",
            borderRadius: 6,
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
          }}
        >
          <CheckCircle2 size={16} />
          <span>{successMsg}</span>
        </div>
      )}

      {accountingErr && !accountingTarget && (
        <div
          data-testid="receipt-accounting-error"
          style={{
            padding: "10px 14px",
            backgroundColor: "#fef2f2",
            border: "1px solid #fecaca",
            color: "#b91c1c",
            borderRadius: 6,
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
          }}
        >
          <AlertCircle size={16} />
          <span>{accountingErr}</span>
        </div>
      )}

      {/* TABLE DATA */}
      <div className="sd-list-panel">
        <div className="sd-list-table-scroll">
          <table className="sd-list-table">
            <colgroup>
              {showDeleteControls && <col style={{ width: 36 }} />}
              <col />
              <col style={{ width: 108 }} />
              <col style={{ width: 130 }} />
              <col style={{ width: 110 }} />
              <col style={{ width: 110 }} />
              <col style={{ width: 140 }} />
              <col style={{ width: 220 }} />
            </colgroup>
            <thead>
              <tr>
                {showDeleteControls && (
                  <th>
                    <input type="checkbox" data-testid="select-all-receipts" checked={deletableReceipts.length > 0 && selectedIds.size === deletableReceipts.length} onChange={toggleSelectAll} aria-label="Pilih semua baris tampilan" />
                  </th>
                )}
                <th>Pelanggan / No. Penerimaan</th>
                <th>Tanggal</th>
                <th>Jumlah</th>
                <th>Status Faktur</th>
                <th>Jurnal</th>
                <th>Kas / Bank</th>
                <th>Aksi</th>
              </tr>
            </thead>
            <tbody>
              {listLoading ? (
                <tr>
                  <td colSpan={showDeleteControls ? 8 : 7} className="sd-list-empty">
                    Memuat data penerimaan penjualan...
                  </td>
                </tr>
              ) : receipts.length === 0 ? (
                <tr>
                  <td colSpan={showDeleteControls ? 8 : 7} className="sd-list-empty" data-testid="empty-receipts">
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
                      <FileText size={36} color="#9ca3af" />
                      <span style={{ fontWeight: 600, color: "#374151" }}>
                        Belum ada Penerimaan pada tab {tabLabel}
                      </span>
                      <span style={{ fontSize: 12, color: "#6b7280" }}>
                        {listMeta.capped
                          ? "Hasil dibatasi server. Sempitkan pencarian atau filter."
                          : "Penerimaan pembayaran dari Faktur Penjualan akan tercatat di sini."}
                      </span>
                    </div>
                  </td>
                </tr>
              ) : (
                receipts.map((r, idx) => {
                  const lifecycle = salesDocumentLifecyclePresentation(r);
                  const deletable = receiptDeletable(r);
                  return (
                  <tr
                    key={r.id || idx}
                    {...clickableDocumentRowProps({
                      onOpen: () => openReceiptDetail(r),
                      "data-testid": `receipt-row-${r.id}`,
                      ariaLabel: `Buka penerimaan ${r.receipt_number || r.id}`,
                    })}
                    style={{
                      borderBottom: "1px solid #f3f4f6",
                      backgroundColor: idx % 2 === 0 ? "#fff" : "#fafafa",
                    }}
                  >
                    {showDeleteControls && (
                      <td style={{ padding: "12px 8px" }} data-no-row-nav onClick={(e) => e.stopPropagation()}>
                        {deletable && (
                          <input type="checkbox" data-testid={`receipt-checkbox-${r.id}`} checked={selectedIds.has(r.id)} onChange={() => toggleSelect(r.id)} aria-label={`Pilih ${r.receipt_number}`} />
                        )}
                      </td>
                    )}
                    <td className="sd-identity">
                      <div className="sd-customer">{r.customer || "-"}</div>
                      <button
                        type="button"
                        className="sd-doc"
                        data-testid={`receipt-number-${r.id}`}
                        onClick={() => openReceiptDetail(r)}
                      >
                        {r.receipt_number || "-"}
                      </button>
                      <span className="sd-support">
                        {r.invoice_number || "-"}
                        {r.po_number ? ` · ${r.po_number}` : ""}
                        {r.notes ? ` · ${r.notes}` : ""}
                      </span>
                    </td>
                    <td>{formatDisplayDate(r.receipt_date)}</td>
                    <td className="sd-money is-zero">{formatRupiah(r.amount)}</td>
                    <td style={{ padding: "12px 16px", textAlign: "center" }}>
                      <span
                        className="pj-status-badge"
                        style={{
                          display: "inline-block",
                          padding: "3px 10px",
                          borderRadius: 9999,
                          fontSize: 11,
                          fontWeight: 700,
                          backgroundColor: r.payment_state === "LUNAS" ? "#d1fae5" : "#e0e7ff",
                          color: r.payment_state === "LUNAS" ? "#065f46" : "#3730a3",
                          border: `1.5px solid ${r.payment_state === "LUNAS" ? "#a7f3d0" : "#c7d2fe"}`,
                        }}
                      >
                        {r.payment_state || "DP"}
                      </span>
                    </td>
                    <td style={{ padding: "12px 16px", textAlign: "center" }}>
                      <span
                        data-testid={`receipt-accounting-status-${r.id}`}
                        className="pj-status-badge"
                        style={{
                          display: "inline-block",
                          padding: "3px 10px",
                          borderRadius: 9999,
                          fontSize: 11,
                          fontWeight: 700,
                          backgroundColor: r.accounting_status === "posted" ? "#d1fae5" : r.accounting_status === "reversed" ? "#f3f4f6" : "#fef3c7",
                          color: r.accounting_status === "posted" ? "#065f46" : r.accounting_status === "reversed" ? "#374151" : "#92400e",
                          border: `1.5px solid ${r.accounting_status === "posted" ? "#a7f3d0" : r.accounting_status === "reversed" ? "#e5e7eb" : "#fde68a"}`,
                        }}
                      >
                        {accountingLabel(r.accounting_status)}
                      </span>
                      {lifecycle && (
                        <div
                          className={`sd-lifecycle-chip ${lifecycleChipClass(lifecycle.tone)}`}
                          data-testid={`receipt-lifecycle-${r.id}`}
                          title={lifecycle.reason || lifecycle.label}
                        >
                          {lifecycle.label}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: "12px 16px", color: "#374151" }} data-testid={`receipt-cash-bank-${r.id}`}>
                      {r.cash_bank_name || "-"}
                    </td>
                    <td data-no-row-nav onClick={(e) => e.stopPropagation()}>
                      <div className="sd-actions">
                        <button
                          type="button"
                          className="sd-act sd-act-view"
                          onClick={(e) => handlePrintPdf(e, r.id)}
                          data-testid={`btn-print-pdf-${r.id}`}
                          disabled={pdfLoadingId === r.id}
                          title="Cetak PDF Penerimaan Penjualan"
                        >
                          {pdfLoadingId === r.id ? "Memuat..." : "Cetak PDF"}
                        </button>
                        {showLegacyPostReverse && canPostAccounting && r.can_post_accounting && (
                          <button
                            type="button"
                            className="sd-act sd-btn-primary"
                            data-testid={`btn-post-accounting-${r.id}`}
                            onClick={() => {
                              setAccountingErr("");
                              if (!r.cash_bank_id) loadCashBanks();
                              setAccountingTarget({ action: "post", receipt: r, cash_bank_id: r.cash_bank_id || "" });
                            }}
                          >
                            Posting
                          </button>
                        )}
                        {showLegacyPostReverse && canReverseAccounting && r.can_reverse_accounting && (
                          <button
                            type="button"
                            className="sd-act sd-act-delete"
                            data-testid={`btn-reverse-accounting-${r.id}`}
                            onClick={() => {
                              setAccountingErr("");
                              setAccountingTarget({ action: "reverse", receipt: r });
                            }}
                          >
                            Balik
                          </button>
                        )}
                        {showDeleteControls && deletable && (
                          <button
                            type="button"
                            className="sd-act sd-act-delete"
                            data-testid={`btn-delete-receipt-${r.id}`}
                            onClick={() => {
                              setDeleteErr("");
                              setDeleteConfirm({
                                id: r.id,
                                number: r.receipt_number,
                                numbers: [r.receipt_number],
                              });
                            }}
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
        <footer className="sd-list-footer" data-testid="receipt-list-footer">
          <span data-testid="receipt-list-range">
            {listLoading
              ? "Memuat daftar penerimaan..."
              : listErr
              ? "Daftar penerimaan belum dapat ditampilkan"
              : salesFiveTabRangeLabel({
                  total: listMeta.total,
                  page,
                  pageSize,
                  tabLabel,
                  noun: "penerimaan",
                  filtered: Boolean(searchQuery.trim()),
                  capped: listMeta.capped,
                })}
          </span>
          <div className="fp-pager">
            <button
              type="button"
              data-testid="receipt-page-prev"
              disabled={listLoading || page <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
            >
              Sebelumnya
            </button>
            <span data-testid="receipt-page-indicator">{page} / {pageCount}</span>
            <label className="fp-page-size">
              <span>Baris</span>
              <select
                aria-label="Jumlah baris per halaman"
                data-testid="receipt-page-size"
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  resetReceiptListPosition();
                }}
              >
                {SALES_LIST_PAGE_SIZES.map((size) => (
                  <option key={size} value={size}>{size}</option>
                ))}
              </select>
            </label>
            <button
              type="button"
              data-testid="receipt-page-next"
              disabled={listLoading || page >= pageCount}
              onClick={() => setPage((current) => current + 1)}
            >
              Berikutnya
            </button>
          </div>
        </footer>
      </div>

      {/* CREATE RECEIPT MODAL — Eternal UX 9.0 wide payment modal */}
      {isModalOpen && (
        <div
          className="ux90-sr-modal"
          data-testid="receipt-create-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="receipt-modal-title"
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              handleCloseCreateModal();
            }
          }}
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: "rgba(0, 0, 0, 0.45)",
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "center",
            zIndex: 1000,
            padding: "24px 16px",
            overflowY: "auto",
          }}
        >
          <div
            className="sd-modal-card ux90-sr-card"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div
              style={{
                padding: "16px 20px",
                borderBottom: "1px solid #e5e7eb",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                backgroundColor: "#f9fafb",
                flexShrink: 0,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <CreditCard size={20} color="#059669" />
                <h2 id="receipt-modal-title" style={{ fontSize: 16, fontWeight: 700, margin: 0, color: "#111827" }}>
                  Catat Penerimaan Penjualan
                </h2>
              </div>
              <button
                type="button"
                data-testid="btn-close-receipt-modal"
                aria-label="Tutup"
                onClick={handleCloseCreateModal}
                style={{
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  color: "#9ca3af",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  padding: 4,
                  borderRadius: 4,
                }}
              >
                <X size={20} />
              </button>
            </div>

            {/* Modal Body — UX 9.0 wide payment */}
            <form className="sd-modal-form" onSubmit={handleSaveReceipt} style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
              <div className="sd-modal-body ux90-sr-body" data-testid="receipt-modal-body">
                {createErr && (
                  <div style={{ padding: "10px 14px", backgroundColor: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c", borderRadius: 6, marginBottom: 12, display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                    <AlertCircle size={16} />
                    <span>{createErr}</span>
                  </div>
                )}
                {successMsg && (
                  <div style={{ padding: "10px 14px", backgroundColor: "#ecfdf5", border: "1px solid #a7f3d0", color: "#065f46", borderRadius: 6, marginBottom: 12, display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                    <CheckCircle2 size={16} />
                    <span>{successMsg}</span>
                  </div>
                )}

                <div className="ux90-sr-grid">
                  <div>
                    <FormSection
                      id="sr-invoice-section"
                      title="Informasi Faktur"
                      description="Hanya faktur yang masih punya sisa tagihan."
                      status={selectedInvoiceId ? "complete" : "incomplete"}
                      className="fs-section-scroll-safe"
                    >
                      <div style={{ marginBottom: 8 }}>
                        <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                          Faktur Penjualan *
                        </label>
                        <LookupSearchSelect
                          options={invoicesWithBalance}
                          value={selectedInvoiceId}
                          onChange={(id) => setSelectedInvoiceId(id || "")}
                          getOptionValue={(inv) => inv.id}
                          getOptionLabel={(inv) => String(inv.invoice_number || inv.id || "")}
                          getOptionMeta={(inv) => [
                            inv.customer,
                            `Sisa ${formatRupiah(inv.outstanding_amount || inv.grand_total)}`,
                          ].filter(Boolean).join(" · ")}
                          getSearchText={(inv) => [inv.invoice_number, inv.customer, inv.po_number].filter(Boolean).join(" ")}
                          placeholder={invoicesLoading ? "Memuat faktur…" : "Ketik nomor faktur atau customer…"}
                          noResultLabel="Tidak ada hasil yang cocok"
                          disabled={createBusy || invoicesLoading}
                          overModal
                          testId="select-receipt-invoice"
                        />
                        {invoicesWithBalance.length === 0 && !invoicesLoading && (
                          <p data-testid="no-eligible-invoices-hint" style={{ margin: "6px 0 0 0", fontSize: 12, color: "#64748b" }}>
                            Tidak ada faktur dengan sisa tagihan aktif (semua faktur telah lunas atau belum memiliki nilai tagihan).
                          </p>
                        )}
                      </div>

                      {selectedInvoice && (
                        <div data-testid="selected-invoice-summary-card" className="sr-money-summary">
                          <div className="sr-invoice-meta" style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 8, marginBottom: 8, fontSize: 12 }}>
                            <div>
                              <span style={{ color: "#64748b" }}>Pelanggan</span>
                              <div data-testid="summary-customer" style={{ fontWeight: 600 }}>{selectedInvoice.customer || "-"}</div>
                            </div>
                            <div>
                              <span style={{ color: "#64748b" }}>No. Faktur</span>
                              <div data-testid="summary-invoice-number" style={{ fontWeight: 600 }}>{selectedInvoice.invoice_number || "-"}</div>
                            </div>
                            <div>
                              <span style={{ color: "#64748b" }}>No. PO</span>
                              <div data-testid="summary-po-number" style={{ fontWeight: 600 }}>{selectedInvoice.po_number}</div>
                            </div>
                          </div>
                          <div className="ux90-money-triad sr-money-triad" data-testid="receipt-money-triad">
                            <div>
                              <span>Total Tagihan</span>
                              <strong data-testid="summary-grand-total">{formatRupiah(selectedInvoice.grand_total)}</strong>
                            </div>
                            <div>
                              <span>Sudah Dibayar</span>
                              <strong data-testid="summary-paid-total" className="is-paid">{formatRupiah(selectedInvoice.paid_total)}</strong>
                            </div>
                            <div className="is-due-box is-due">
                              <span>Sisa Tagihan</span>
                              <strong data-testid="summary-outstanding-amount" className="is-due">
                                {formatRupiah(selectedInvoice.outstanding_amount)}
                              </strong>
                            </div>
                          </div>
                        </div>
                      )}
                    </FormSection>

                    <FormSection
                      id="sr-payment-section"
                      title="Informasi Pembayaran"
                      description="Isi jumlah diterima, lalu pilih kas atau bank."
                      status={computeStatus([{ value: receiptAmount, isRequired: true }, { value: receiptDate, isRequired: true }, { value: cashBankId, isRequired: true }])}
                      className="fs-section-scroll-safe"
                    >
                      <div className="ux90-pay-grid sr-payment-grid">
                        <div>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                            <label style={{ fontSize: 12, fontWeight: 600, color: "#374151", margin: 0 }}>
                              Jumlah diterima (Rp) *
                            </label>
                            {selectedInvoice && selectedInvoice.outstanding_amount > 0 && (
                              <button
                                type="button"
                                onClick={handlePayFull}
                                data-testid="btn-pay-full-outstanding"
                                style={{ background: "none", border: "none", color: "#059669", fontSize: 11, fontWeight: 600, cursor: "pointer", textDecoration: "underline" }}
                              >
                                Isi sisa tagihan
                              </button>
                            )}
                          </div>
                          <input
                            type="number"
                            className="form-input"
                            data-testid="input-receipt-amount"
                            placeholder="0"
                            min="1"
                            value={receiptAmount}
                            onChange={(e) => {
                              setReceiptAmount(e.target.value);
                              if (!allocationTouched) setAllocationAmount("");
                            }}
                            disabled={createBusy || !selectedInvoice}
                            style={{ width: "100%", fontSize: 14, fontWeight: 600, textAlign: "right" }}
                          />
                        </div>

                        <div data-testid="receipt-allocation-field">
                          <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                            Dipakai untuk faktur ini (Rp)
                          </label>
                          <input
                            type="number"
                            className="form-input"
                            data-testid="input-receipt-allocation"
                            min="0"
                            value={allocationTouched ? allocationAmount : (previewCalc ? String(previewCalc.allocated || "") : allocationAmount)}
                            onChange={(e) => {
                              setAllocationTouched(true);
                              setAllocationAmount(e.target.value);
                            }}
                            disabled={createBusy || !selectedInvoice}
                            style={{ width: "100%", fontSize: 14, textAlign: "right" }}
                          />
                          {previewCalc && previewCalc.amount > 0 && (
                            <p data-testid="receipt-allocation-total" style={{ margin: "6px 0 0", fontSize: 12, color: "#0f766e" }}>
                              Dipakai: {formatRupiah(previewCalc.allocationExceedsOutstanding || previewCalc.allocationExceedsReceived ? 0 : previewCalc.allocated)}
                            </p>
                          )}
                          {previewCalc && previewCalc.customerCredit > 0 && (
                            <p data-testid="receipt-customer-credit-remainder" style={{ margin: "4px 0 0", fontSize: 12, fontWeight: 700, color: "#0f766e" }}>
                              Sisa menjadi saldo pelanggan: {formatRupiah(previewCalc.customerCredit)}
                            </p>
                          )}
                          {previewCalc && previewCalc.allocationExceedsOutstanding && (
                            <p data-testid="receipt-allocation-over-hint" style={{ margin: "4px 0 0", fontSize: 12, fontWeight: 600, color: "#b91c1c" }}>
                              Melebihi sisa tagihan. Turunkan nominal atau alokasi.
                            </p>
                          )}
                        </div>

                        <div>
                          <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                            Tanggal penerimaan *
                          </label>
                          <SalesInvoiceDateField
                            valueIso={receiptDate}
                            onChange={setReceiptDate}
                            disabled={createBusy}
                            testId="input-receipt-date"
                            calendarLabel="Kalender tanggal penerimaan"
                          />
                        </div>
                        <div>
                          <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                            Kas / Bank *
                          </label>
                          <LookupSearchSelect
                            options={cashBanks}
                            value={cashBankId}
                            onChange={(id) => setCashBankId(id || "")}
                            getOptionValue={(acc) => acc.id}
                            getOptionLabel={(acc) => String(acc.name || acc.id || "")}
                            getOptionMeta={(acc) => [acc.account_code, acc.bank_name, acc.account_number].filter(Boolean).join(" · ")}
                            getSearchText={(acc) => [acc.name, acc.account_code, acc.bank_name, acc.account_number].filter(Boolean).join(" ")}
                            placeholder={cashBanksLoading ? "Memuat Kas/Bank…" : "Ketik nama rekening atau akun…"}
                            noResultLabel="Tidak ada hasil yang cocok"
                            disabled={createBusy || cashBanksLoading}
                            overModal
                            testId="select-receipt-cash-bank"
                          />
                          {cashBanks.length === 0 && !cashBanksLoading && (
                            <p data-testid="no-cash-bank-hint" style={{ margin: "6px 0 0 0", fontSize: 12, color: "#64748b" }}>
                              Belum ada Kas/Bank aktif. Tambahkan di menu Kas &amp; Bank terlebih dahulu.
                            </p>
                          )}
                        </div>
                      </div>

                      {canPostAccounting && selectedInvoice && (
                        <div style={{ marginTop: 12 }} data-testid="receipt-use-credit">
                          {creditOfferErr && <p data-testid="credit-load-error" style={{ color: "#b91c1c", fontSize: 12 }}>{String(creditOfferErr)}</p>}
                          {creditOffer?.blocked_reason && (
                            <p data-testid="credit-blocked-reason" style={{ fontSize: 12, color: "#92400e" }}>{creditOffer.blocked_reason}</p>
                          )}
                          {creditOffer?.loaded && Number(creditOffer.available) > 0 && (
                            <button
                              type="button"
                              data-testid="btn-gunakan-saldo"
                              onClick={() => {
                                setCreditPanelOpen(true);
                                const first = creditOffer.sources?.[0];
                                setCreditSourceId(first?.receipt_id || "");
                                setCreditUseAmount(String(Math.min(Number(first?.available) || 0, Number(selectedInvoice.outstanding_amount) || 0)));
                              }}
                              style={{ border: "none", background: "#047857", color: "#fff", borderRadius: 6, padding: "6px 10px", fontSize: 12 }}
                            >
                              Gunakan Saldo Customer
                            </button>
                          )}
                          {creditPanelOpen && creditOffer?.loaded && (
                            <div data-testid="credit-use-panel" style={{ marginTop: 8, padding: 10, border: "1px solid #99f6e4", borderRadius: 8, background: "#fff" }}>
                              <div style={{ fontSize: 12, marginBottom: 6 }}>Saldo tersedia: {formatRupiah(creditOffer.available)}</div>
                              <div style={{ fontSize: 12, marginBottom: 6 }}>Faktur tujuan: {selectedInvoice.invoice_number}</div>
                              <select data-testid="select-credit-source" value={creditSourceId} onChange={(e) => setCreditSourceId(e.target.value)} style={{ width: "100%", marginBottom: 6 }}>
                                {(creditOffer.sources || []).map((row) => (
                                  <option key={row.receipt_id} value={row.receipt_id}>
                                    {row.receipt_number} · {formatRupiah(row.available)}
                                  </option>
                                ))}
                              </select>
                              <input data-testid="input-credit-use-amount" type="number" value={creditUseAmount} onChange={(e) => setCreditUseAmount(e.target.value)} style={{ width: "100%", marginBottom: 6 }} />
                              <div style={{ fontSize: 12, marginBottom: 6 }}>
                                Sisa faktur: {formatRupiah(Math.max(0, Number(selectedInvoice.outstanding_amount || 0) - Number(creditUseAmount || 0)))}
                              </div>
                              <button type="button" data-testid="btn-confirm-gunakan-saldo" disabled={creditBusy} onClick={handleApplyCredit} style={{ border: "none", background: "#047857", color: "#fff", borderRadius: 6, padding: "6px 10px" }}>
                                {creditBusy ? "Memakai saldo..." : "Konfirmasi pakai saldo"}
                              </button>
                              {creditMsg && <p data-testid="credit-use-result" style={{ fontSize: 12 }}>{creditMsg}</p>}
                            </div>
                          )}
                        </div>
                      )}

                      <div style={{ marginTop: 12 }}>
                        <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                          Catatan Pembayaran
                        </label>
                        <input
                          type="text"
                          className="form-input"
                          data-testid="input-receipt-notes"
                          placeholder="Contoh: DP 50% via BCA / Pelunasan cash"
                          value={receiptNotes}
                          onChange={(e) => setReceiptNotes(e.target.value)}
                          disabled={createBusy}
                          style={{ width: "100%", fontSize: 13 }}
                        />
                      </div>
                    </FormSection>
                  </div>

                  <div>
                    <FormSection
                      id="sr-allocation-summary"
                      title="Alokasi Pembayaran"
                      description="Ringkasan nominal yang dialokasikan ke faktur terpilih."
                      status={previewCalc && previewCalc.amount > 0 ? "complete" : "incomplete"}
                      className="fs-section-scroll-safe"
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 8 }}>
                        <span style={{ fontSize: 12, color: "#64748b" }}>Jumlah dialokasikan</span>
                        {previewCalc && previewCalc.newState === "LUNAS" ? (
                          <span className="ux90-badge-lunas" data-testid="receipt-lunas-badge">Lunas</span>
                        ) : null}
                      </div>
                      <div style={{ fontSize: 20, fontWeight: 700, marginBottom: 10 }} data-testid="receipt-allocation-side-amount">
                        {previewCalc ? formatRupiah(previewCalc.allocationExceedsOutstanding || previewCalc.allocationExceedsReceived ? 0 : previewCalc.allocated) : "Rp 0"}
                      </div>
                      {previewCalc && previewCalc.amount > 0 && !previewCalc.allocationExceedsOutstanding && !previewCalc.allocationExceedsReceived && (
                        <div
                          style={{ backgroundColor: "#ecfdf5", border: "1px solid #a7f3d0", borderRadius: 6, padding: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}
                        >
                          <div>
                            <div style={{ fontSize: 11, color: "#065f46" }}>Status Baru Setelah Penerimaan</div>
                            <div style={{ fontSize: 13, fontWeight: 700, color: "#065f46" }}>
                              {previewCalc.newState === "LUNAS" ? "LUNAS (100% Terbayar)" : `DP (Sisa: ${formatRupiah(previewCalc.newOutstanding)})`}
                            </div>
                          </div>
                          <div style={{ textAlign: "right" }}>
                            <div style={{ fontSize: 11, color: "#065f46" }}>Total Terbayar</div>
                            <div style={{ fontSize: 13, fontWeight: 700, color: "#065f46" }}>{formatRupiah(previewCalc.newPaid)}</div>
                          </div>
                        </div>
                      )}
                    </FormSection>

                    <div style={{ marginTop: 12 }} data-testid="receipt-payment-proof-field">
                      <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                        Bukti Pembayaran *
                      </label>
                      <p style={{ margin: "0 0 8px 0", fontSize: 12, color: "#64748b" }}>
                        JPG, JPEG, PNG, atau PDF (maks. 6MB). Nominal mengikuti jumlah penerimaan — bukan dari file.
                      </p>
                      {proofFile ? (
                        <div className="ux90-proof-drop receipt-proof-row" data-testid="receipt-proof-compact-row">
                          <div className="receipt-proof-thumb" aria-hidden="true">
                            {proofThumbUrl ? (
                              <img src={proofThumbUrl} alt="" data-testid="receipt-proof-thumb-img" />
                            ) : (
                              <FileText size={22} data-testid="receipt-proof-thumb-pdf" />
                            )}
                          </div>
                          <div className="receipt-proof-meta" style={{ flex: 1, minWidth: 0 }}>
                            <span className="receipt-proof-name" data-testid="receipt-proof-filename" title={proofFile.name}>
                              {proofFile.name}
                            </span>
                          </div>
                          <div className="receipt-proof-actions" style={{ display: "flex", gap: 6 }}>
                            <label className={"ghost sm btn-file" + (createBusy ? " disabled" : "")} data-testid="receipt-proof-upload-label" style={{ cursor: createBusy ? "not-allowed" : "pointer", opacity: createBusy ? 0.6 : 1, margin: 0 }}>
                              <input type="file" accept={RECEIPT_PROOF_ACCEPT} disabled={createBusy} onChange={handleProofFileChange} data-testid="input-receipt-payment-proof" style={{ display: "none" }} />
                              Ganti
                            </label>
                            <button type="button" className="ghost sm" data-testid="btn-clear-receipt-proof" disabled={createBusy} onClick={() => { setProofFile(null); setProofErr(""); }}>
                              Hapus
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="ux90-proof-drop receipt-proof-row" data-testid="receipt-proof-empty-row">
                          <div className="receipt-proof-thumb" aria-hidden="true"><FileText size={22} /></div>
                          <div className="receipt-proof-meta" style={{ flex: 1 }}>
                            <span className="receipt-proof-name" style={{ fontWeight: 500, color: "#64748b" }}>Belum ada file — seret atau pilih</span>
                          </div>
                          <div className="receipt-proof-actions">
                            <label
                              className={"primary btn-file" + (createBusy ? " disabled" : "")}
                              data-testid="receipt-proof-upload-label"
                              style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: createBusy ? "not-allowed" : "pointer", opacity: createBusy ? 0.6 : 1, margin: 0 }}
                            >
                              <input type="file" accept={RECEIPT_PROOF_ACCEPT} disabled={createBusy} onChange={handleProofFileChange} data-testid="input-receipt-payment-proof" style={{ display: "none" }} />
                              Pilih File
                            </label>
                          </div>
                        </div>
                      )}
                      {proofErr && (
                        <div className="error" data-testid="receipt-proof-error" style={{ marginTop: 8, fontSize: 12, color: "#b91c1c" }}>
                          {proofErr}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              <div className="sd-modal-footer" data-testid="receipt-modal-footer" style={{ flex: "0 0 auto" }}>
                <button
                  type="button"
                  data-testid="btn-cancel-receipt-modal"
                  className="btn btn-secondary"
                  onClick={handleCloseCreateModal}
                  disabled={createBusy}
                  style={{ padding: "8px 16px", borderRadius: 6, fontSize: 13, cursor: "pointer" }}
                >
                  Batal
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  data-testid="btn-submit-receipt"
                  disabled={createBusy || !selectedInvoice || previewCalc?.allocationExceedsOutstanding || previewCalc?.allocationExceedsReceived}
                  style={{
                    padding: "8px 18px",
                    borderRadius: 6,
                    fontSize: 13,
                    fontWeight: 600,
                    backgroundColor: "#166534",
                    borderColor: "#166534",
                    color: "#fff",
                    cursor: createBusy || !selectedInvoice ? "not-allowed" : "pointer",
                  }}
                >
                  {createBusy ? "Menyimpan..." : "Simpan Penerimaan"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {deleteConfirm && (
        <div className="modal-overlay" data-testid="delete-confirm-modal" style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 80 }}>
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 460 }}>
            <h3 style={{ margin: "0 0 8px" }}>
              {canCancelBusiness ? "Batalkan Penerimaan Penjualan?" : "Hapus Penerimaan Penjualan?"}
            </h3>
            <p style={{ margin: "0 0 12px", color: "#64748b", fontSize: "0.9rem" }}>
              {canCancelBusiness
                ? "Dokumen berikut akan dibatalkan. Riwayat tetap tersimpan."
                : "Dokumen berikut akan dihapus:"}
            </p>
            <ul data-testid="delete-confirm-numbers" style={{ margin: "0 0 16px", paddingLeft: 18, fontWeight: 700, color: "#059669" }}>
              {(deleteConfirm.numbers || [deleteConfirm.number]).filter(Boolean).map((n) => <li key={n}>{n}</li>)}
            </ul>
            {deleteErr && <div data-testid="delete-error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>{deleteErr}</div>}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={() => { setDeleteConfirm(null); setDeleteErr(""); }} disabled={deleteLoading} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}>Batal</button>
              <button
                type="button"
                data-testid="btn-confirm-delete"
                onClick={() => (deleteConfirm.ids ? handleBulkDelete() : handleDelete(deleteConfirm.id))}
                disabled={deleteLoading}
                style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#dc2626", color: "#fff", fontWeight: 600 }}
              >
                {deleteLoading
                  ? (canCancelBusiness ? "Membatalkan..." : "Menghapus...")
                  : (canCancelBusiness ? "Batalkan" : "Hapus")}
              </button>
            </div>
          </div>
        </div>
      )}

      {detailReceipt && (
        <div
          className="modal-overlay sd-receipt-detail-overlay"
          data-testid="receipt-detail-modal"
          style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 80 }}
        >
          <div className="sd-receipt-detail-card" style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 520, maxHeight: "calc(100dvh - 24px)", overflow: "auto" }}>
            <h3 style={{ margin: "0 0 8px" }}>{detailEditMode ? "Edit Penerimaan" : "Detail Penerimaan"}</h3>
            <p data-testid="receipt-detail-number" style={{ margin: "0 0 8px", fontFamily: "monospace", fontWeight: 700, color: "#059669" }}>
              {detailReceipt.receipt_number}
            </p>
            {!detailEditMode && !canEditReceipt && !canCancelDetail && (
              <p data-testid="receipt-mutation-frozen-note" className="sd-receipt-frozen-note">
                Koreksi penerimaan dilakukan melalui Admin Keuangan/Direktur sesuai SOP. Edit dan batalkan memerlukan permission serta capability backend yang aktif.
              </p>
            )}
            {!detailEditMode ? (
              <dl className="sd-receipt-detail-grid" data-testid="receipt-detail-summary" style={{ margin: "0 0 16px", display: "grid", gridTemplateColumns: "1fr auto", gap: "8px 16px", fontSize: "0.9rem" }}>
                <dt>Pelanggan</dt>
                <dd data-testid="receipt-detail-customer" style={{ margin: 0, fontWeight: 650 }}>{detailReceipt.customer || "-"}</dd>
                <dt>Jurnal</dt>
                <dd data-testid="receipt-detail-accounting-status" style={{ margin: 0, fontWeight: 700 }}>{accountingLabel(detailReceipt.accounting_status)}</dd>
                <dt>Tanggal</dt>
                <dd data-testid="receipt-detail-date" style={{ margin: 0 }}>
                  {formatSalesInvoiceDate(detailReceipt.receipt_date) || formatDisplayDate(detailReceipt.receipt_date) || detailReceipt.receipt_date || "-"}
                </dd>
                <dt>Kas / Bank</dt>
                <dd data-testid="receipt-detail-cash-bank" style={{ margin: 0 }}>{detailReceipt.cash_bank_name || "-"}</dd>
                <dt>Jumlah</dt>
                <dd data-testid="receipt-detail-amount" style={{ margin: 0, fontWeight: 700 }}>{formatRupiah(detailReceipt.amount)}</dd>
                <dt>Faktur</dt>
                <dd style={{ margin: 0, fontFamily: "monospace" }}>
                  {detailReceipt.invoice_id ? (
                    <Link
                      data-testid="receipt-detail-invoice-link"
                      to={`/penjualan/faktur-penjualan?invoice_id=${encodeURIComponent(detailReceipt.invoice_id)}`}
                      onClick={() => setDetailReceipt(null)}
                    >
                      {detailReceipt.invoice_number || detailReceipt.invoice_id}
                    </Link>
                  ) : (detailReceipt.invoice_number || "-")}
                </dd>
                <dt>No. PO</dt>
                <dd style={{ margin: 0, fontFamily: "monospace" }}>
                  {detailReceipt.po_id ? (
                    <Link data-testid="receipt-detail-po-link" to={`/po/${encodeURIComponent(detailReceipt.po_id)}`} onClick={() => setDetailReceipt(null)}>
                      {detailReceipt.po_number || detailReceipt.po_id}
                    </Link>
                  ) : (detailReceipt.po_number || "-")}
                </dd>
                <dt>Dialokasikan</dt>
                <dd style={{ margin: 0 }}>{formatRupiah(detailReceipt.allocated_amount ?? detailReceipt.amount)}</dd>
                {Number(detailReceipt.customer_credit_amount) > 0 && (
                  <>
                    <dt>Saldo pelanggan</dt>
                    <dd data-testid="receipt-detail-credit" style={{ margin: 0 }}>
                      {detailReceipt.customer_credit?.state === "available"
                        ? `Tersedia ${formatRupiah(detailReceipt.customer_credit.available)}`
                        : (detailReceipt.customer_credit?.reason || "Tidak tersedia")}
                    </dd>
                  </>
                )}
              </dl>
            ) : (
              <form data-testid="receipt-edit-form" onSubmit={handleSaveReceiptEdit} style={{ marginBottom: 16, display: "grid", gap: 10 }}>
                <label style={{ fontSize: 13 }}>
                  Tanggal
                  <input data-testid="receipt-edit-date" type="date" value={editReceiptDate} onChange={(e) => setEditReceiptDate(e.target.value)} required style={{ display: "block", width: "100%", marginTop: 4, padding: 8, borderRadius: 8, border: "1px solid #cbd5e1" }} />
                </label>
                <label style={{ fontSize: 13 }}>
                  Jumlah
                  <input data-testid="receipt-edit-amount" type="number" min="0" step="0.01" value={editAmount} onChange={(e) => setEditAmount(e.target.value)} required style={{ display: "block", width: "100%", marginTop: 4, padding: 8, borderRadius: 8, border: "1px solid #cbd5e1" }} />
                </label>
                <label style={{ fontSize: 13 }}>
                  Dialokasikan
                  <input data-testid="receipt-edit-allocated" type="number" min="0" step="0.01" value={editAllocated} onChange={(e) => setEditAllocated(e.target.value)} required style={{ display: "block", width: "100%", marginTop: 4, padding: 8, borderRadius: 8, border: "1px solid #cbd5e1" }} />
                </label>
                <label style={{ fontSize: 13 }}>
                  Kas / Bank
                  <select data-testid="receipt-edit-cash-bank" value={editCashBankId} onChange={(e) => setEditCashBankId(e.target.value)} required style={{ display: "block", width: "100%", marginTop: 4, padding: 8, borderRadius: 8, border: "1px solid #cbd5e1" }}>
                    <option value="">Pilih Kas/Bank</option>
                    {cashBanks.map((b) => (
                      <option key={b.id} value={b.id}>{b.name}</option>
                    ))}
                  </select>
                </label>
                <label style={{ fontSize: 13 }}>
                  Catatan
                  <textarea data-testid="receipt-edit-notes" value={editNotes} onChange={(e) => setEditNotes(e.target.value)} rows={2} style={{ display: "block", width: "100%", marginTop: 4, padding: 8, borderRadius: 8, border: "1px solid #cbd5e1" }} />
                </label>
                <p style={{ margin: 0, fontSize: 12, color: "#64748b" }}>
                  Faktur {detailReceipt.invoice_number} terkunci. Identitas dokumen tidak diubah.
                </p>
              </form>
            )}
            {editErr && (
              <div data-testid="receipt-edit-error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>
                {editErr}
              </div>
            )}
            {editSuccess && (
              <div data-testid="receipt-edit-success" style={{ background: "#ecfdf5", color: "#047857", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>
                {editSuccess}
              </div>
            )}
            {!detailEditMode && Array.isArray(detailReceipt.customer_credit_history) && detailReceipt.customer_credit_history.length > 0 && (
              <div data-testid="receipt-credit-history" style={{ marginBottom: 12, fontSize: 12 }}>
                <strong>Riwayat saldo</strong>
                <ul style={{ margin: "6px 0 0", paddingLeft: 16 }}>
                  {detailReceipt.customer_credit_history.map((row) => (
                    <li key={row.id} data-testid={`credit-history-${row.id}`}>
                      {row.invoice_number} · {formatRupiah(row.amount)} · {row.financial_status === "cancelled" ? "dibalik" : "dipakai"}
                      {canReverseAccounting && row.financial_status === "active" && row.status === "posted" && (
                        <button type="button" data-testid={`btn-reverse-credit-${row.id}`} disabled={creditBusy} onClick={() => handleReverseCredit(row.id)} style={{ marginLeft: 8 }}>
                          Balikkan Pemakaian Saldo
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
                {creditMsg && <p>{creditMsg}</p>}
              </div>
            )}
            {!detailEditMode && detailReceipt.has_payment_proof && (
              <div className="receipt-proof-row" data-testid="receipt-detail-proof-row" style={{ marginBottom: 12 }}>
                <div className="receipt-proof-thumb receipt-proof-thumb--sm" aria-hidden="true">
                  {detailProofThumbUrl ? (
                    <img src={detailProofThumbUrl} alt="" data-testid="receipt-detail-proof-thumb-img" />
                  ) : (
                    <FileText size={18} data-testid="receipt-detail-proof-icon" />
                  )}
                </div>
                <div className="receipt-proof-meta">
                  <span
                    className="receipt-proof-name"
                    data-testid="receipt-detail-proof-filename"
                    title={detailReceipt.payment_proof?.name || "bukti-pembayaran"}
                  >
                    {detailReceipt.payment_proof?.name || "bukti-pembayaran"}
                  </span>
                </div>
                <div className="receipt-proof-actions">
                  <button
                    type="button"
                    data-testid="btn-lihat-bukti"
                    onClick={() => setProofViewerReceipt(detailReceipt)}
                    style={{ border: "none", background: "#0f766e", color: "#fff", borderRadius: 6, padding: "6px 10px", fontSize: 12 }}
                  >
                    Lihat
                  </button>
                  <button
                    type="button"
                    data-testid="btn-unduh-bukti"
                    onClick={async () => {
                      const file = await api.downloadSalesReceiptPaymentProof(detailReceipt.id, true);
                      const url = URL.createObjectURL(file);
                      const link = document.createElement("a");
                      link.href = url;
                      link.download = detailReceipt.payment_proof?.name || "bukti-pembayaran";
                      document.body.appendChild(link);
                      link.click();
                      link.remove();
                      URL.revokeObjectURL(url);
                    }}
                    style={{ border: "1px solid #cbd5e1", background: "#fff", borderRadius: 6, padding: "6px 10px", fontSize: 12 }}
                  >
                    Unduh
                  </button>
                </div>
              </div>
            )}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {!detailEditMode && canEditReceipt && (
                <button type="button" data-testid="btn-edit-receipt" onClick={beginReceiptEdit} style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#0f766e", color: "#fff", fontWeight: 600 }}>
                  Edit
                </button>
              )}
              {!detailEditMode && canCancelDetail && (
                <button
                  type="button"
                  data-testid="btn-cancel-receipt-detail"
                  onClick={() => {
                    setDeleteErr("");
                    setDeleteConfirm({
                      ids: [detailReceipt.id],
                      numbers: [detailReceipt.receipt_number],
                      id: detailReceipt.id,
                      number: detailReceipt.receipt_number,
                    });
                  }}
                  style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #fecaca", background: "#fff", color: "#b91c1c", fontWeight: 600 }}
                >
                  Batalkan
                </button>
              )}
              {detailEditMode && (
                <>
                  <button type="button" data-testid="btn-save-receipt-edit" disabled={editBusy} onClick={handleSaveReceiptEdit} style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#047857", color: "#fff", fontWeight: 600, opacity: editBusy ? 0.6 : 1 }}>
                    {editBusy ? "Menyimpan..." : "Simpan"}
                  </button>
                  <button type="button" data-testid="btn-cancel-receipt-edit" disabled={editBusy} onClick={() => { setDetailEditMode(false); setEditErr(""); }} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}>
                    Batal Edit
                  </button>
                </>
              )}
              {!detailEditMode && showLegacyPostReverse && canPostAccounting && detailReceipt.can_post_accounting && (
                <button
                  type="button"
                  data-testid="btn-detail-post-accounting"
                  onClick={() => {
                    setAccountingErr("");
                    if (!detailReceipt.cash_bank_id) loadCashBanks();
                    setAccountingTarget({ action: "post", receipt: detailReceipt, cash_bank_id: detailReceipt.cash_bank_id || "" });
                  }}
                  style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#047857", color: "#fff", fontWeight: 600 }}
                >
                  Posting
                </button>
              )}
              {!detailEditMode && showLegacyPostReverse && canReverseAccounting && detailReceipt.can_reverse_accounting && (
                <button
                  type="button"
                  data-testid="btn-detail-reverse-accounting"
                  onClick={() => {
                    setAccountingErr("");
                    setAccountingTarget({ action: "reverse", receipt: detailReceipt });
                  }}
                  style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #fecaca", background: "#fff", color: "#b91c1c", fontWeight: 600 }}
                >
                  Balik
                </button>
              )}
              {!detailEditMode && canRecoverAutoAccounting(detailReceipt, { hasPerm, kind: "receipt" }) && (
                <button
                  type="button"
                  data-testid="btn-recover-auto-receipt"
                  onClick={async () => {
                    setAccountingErr("");
                    try {
                      const res = await api.recoverAutoSalesReceiptAccounting(detailReceipt.id);
                      const interpreted = interpretAutoLifecycleResponse(res);
                      if (interpreted.document) setDetailReceipt(interpreted.document);
                      if (!interpreted.ok) setAccountingErr(interpreted.message);
                      else {
                        setSuccessMsg(interpreted.message || "Pemulihan akuntansi berhasil");
                        void reloadReceiptListAuthoritative();
                      }
                    } catch (err) {
                      const det = err.response?.data?.detail;
                      const interpreted = interpretAutoLifecycleResponse({}, { httpErrorBody: det });
                      if (interpreted.document) setDetailReceipt(interpreted.document);
                      setAccountingErr(interpreted.message || (typeof det === "object" ? det.message : det) || "Pemulihan gagal");
                    }
                  }}
                  style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#b45309", color: "#fff", fontWeight: 600 }}
                >
                  Pulihkan Posting
                </button>
              )}
              <button
                type="button"
                data-testid="btn-close-receipt-detail"
                onClick={() => { setDetailReceipt(null); setDetailEditMode(false); setEditErr(""); setEditSuccess(""); }}
                style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}
              >
                Tutup
              </button>
            </div>
          </div>
        </div>
      )}

      {proofViewerReceipt && (
        <SalesReceiptProofViewer
          receipt={proofViewerReceipt}
          onClose={() => setProofViewerReceipt(null)}
        />
      )}

      {accountingTarget && (
        <div
          className="modal-overlay"
          data-testid={accountingTarget.action === "reverse" ? "reverse-accounting-confirm-modal" : "post-accounting-confirm-modal"}
          style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 90 }}
        >
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 480 }}>
            <h3 style={{ margin: "0 0 8px" }}>
              {accountingTarget.action === "reverse" ? "Balik jurnal?" : "Posting jurnal?"}
            </h3>
            <p style={{ margin: "0 0 12px", color: "#64748b", fontSize: "0.9rem" }}>
              {accountingTarget.action === "reverse"
                ? "Jurnal akan di-reverse (Dr Piutang / Cr Kas-Bank). Penerimaan tidak dihapus dan tidak lagi dihitung pada sisa tagihan faktur."
                : "Jurnal: Dr Kas/Bank / Cr Piutang Usaha. Penerimaan operasional tetap tersimpan."}
            </p>
            <dl data-testid="receipt-accounting-summary" style={{ margin: "0 0 16px", display: "grid", gridTemplateColumns: "1fr auto", gap: "8px 16px", fontSize: "0.9rem" }}>
              <dt>No. Penerimaan</dt>
              <dd style={{ margin: 0, fontFamily: "monospace", fontWeight: 700 }}>{accountingTarget.receipt.receipt_number}</dd>
              <dt>Jumlah</dt>
              <dd style={{ margin: 0, fontWeight: 700 }}>{formatRupiah(accountingTarget.receipt.amount)}</dd>
              <dt>Kas / Bank</dt>
              <dd style={{ margin: 0 }}>{accountingTarget.receipt.cash_bank_name || "-"}</dd>
            </dl>
            {accountingTarget.action === "post" && !accountingTarget.receipt.cash_bank_id && (
              <div style={{ marginBottom: 16 }}>
                <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>Kas / Bank *</label>
                <select
                  className="form-select"
                  data-testid="select-post-cash-bank"
                  value={accountingTarget.cash_bank_id || ""}
                  onChange={(e) => setAccountingTarget((prev) => ({ ...prev, cash_bank_id: e.target.value }))}
                  style={{ width: "100%", fontSize: 13 }}
                >
                  <option value="">-- Pilih Kas / Bank --</option>
                  {cashBanks.map((acc) => (
                    <option key={acc.id} value={acc.id}>{acc.name}</option>
                  ))}
                </select>
              </div>
            )}
            {accountingErr && (
              <div data-testid="accounting-confirm-error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>
                {accountingErr}
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button
                type="button"
                onClick={() => { setAccountingTarget(null); setAccountingErr(""); }}
                disabled={accountingBusy}
                style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}
              >
                Batal
              </button>
              <button
                type="button"
                data-testid={accountingTarget.action === "reverse" ? "btn-confirm-reverse-accounting" : "btn-confirm-post-accounting"}
                onClick={handleConfirmAccounting}
                disabled={accountingBusy}
                style={{
                  padding: "8px 14px",
                  borderRadius: 8,
                  border: "none",
                  background: accountingTarget.action === "reverse" ? "#dc2626" : "#047857",
                  color: "#fff",
                  fontWeight: 600,
                }}
              >
                {accountingBusy
                  ? "Memproses..."
                  : accountingTarget.action === "reverse"
                    ? "Balik jurnal"
                    : "Posting"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
