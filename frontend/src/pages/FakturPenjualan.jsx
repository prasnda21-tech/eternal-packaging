import { useCallback, useEffect, useMemo, useState } from "react";
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
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";
import { invoiceListMatchesFilter, paginateRows, SALES_DEFAULT_PAGE_SIZE } from "../lib/salesWorkspaceList";
import SalesPaginationBar from "../components/salesWorkspace/SalesPaginationBar";
import SalesRowMenu from "../components/salesWorkspace/SalesRowMenu";
import "./salesWorkspacePages.css";

function getTodayString() {
  const d = new Date();
  return d.toISOString().split("T")[0];
}

function formatRupiah(num) {
  const n = Number(num) || 0;
  return "Rp " + n.toLocaleString("id-ID");
}

function invoiceFinancialStatus(inv) {
  const s = String(inv?.financial_status || "draft").toLowerCase();
  if (s === "finalized" || s === "cancelled") return s;
  return "draft";
}

function renderPostedAccountingBadge() {
  return (
    <span
      className="pj-status-badge"
      data-testid="invoice-accounting-badge-posted"
      style={{
        display: "inline-block",
        padding: "3px 10px",
        borderRadius: 9999,
        fontSize: 11,
        fontWeight: 700,
        backgroundColor: "#d1fae5",
        color: "#065f46",
        border: "1.5px solid #a7f3d0",
        marginLeft: 6,
      }}
    >
      Posted Accounting
    </span>
  );
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

  const queryPoId = searchParams.get("po_id") || "";
  const initialMode =
    searchParams.get("action") === "create" || Boolean(queryPoId) ? "create" : "list";
  const [viewMode, setViewMode] = useState(initialMode);

  // List state
  const [invoices, setInvoices] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listErr, setListErr] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [statusChip, setStatusChip] = useState("all");
  const [listPage, setListPage] = useState(1);
  const [listPageSize, setListPageSize] = useState(SALES_DEFAULT_PAGE_SIZE);

  // Checkbox selection
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deleteConfirm, setDeleteConfirm] = useState(null); // {id, number} or {ids: [...], numbers: [...]}
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");

  // Create state
  const [availablePOs, setAvailablePOs] = useState([]);
  const [loadingPOs, setLoadingPOs] = useState(false);
  const [selectedPoId, setSelectedPoId] = useState(queryPoId);
  const [previewData, setPreviewData] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewErr, setPreviewErr] = useState("");

  const [invoiceDate, setInvoiceDate] = useState(getTodayString());
  const [paymentTermDays, setPaymentTermDays] = useState(0);
  const [discountAmount, setDiscountAmount] = useState(0);
  const [ppnPercent, setPpnPercent] = useState(0);
  const [invoiceNotes, setInvoiceNotes] = useState("");
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
  const [detailDiscount, setDetailDiscount] = useState(0);
  const [detailPpn, setDetailPpn] = useState(0);
  const [detailTermDays, setDetailTermDays] = useState(0);
  const [detailDate, setDetailDate] = useState("");
  const [detailNotes, setDetailNotes] = useState("");
  const [detailSaving, setDetailSaving] = useState(false);
  const [finalizeConfirm, setFinalizeConfirm] = useState(false);
  const [cancelConfirm, setCancelConfirm] = useState(false);
  const [postAccountingConfirm, setPostAccountingConfirm] = useState(false);
  const [lifecycleBusy, setLifecycleBusy] = useState(false);

  // Load Invoices List
  const loadInvoices = useCallback(async () => {
    setListLoading(true);
    setListErr("");
    setSelectedIds(new Set()); // reset selection on reload
    try {
      const params = {};
      if (searchQuery.trim()) params.q = searchQuery.trim();
      const res = await api.listSalesInvoices(params);
      setInvoices(res.invoices || []);
    } catch (e) {
      const det = e.response?.data?.detail;
      setListErr(
        typeof det === "object"
          ? det.message
          : det || "Gagal memuat daftar Faktur Penjualan"
      );
    } finally {
      setListLoading(false);
    }
  }, [searchQuery]);

  // Checkbox helpers
  const toggleSelect = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const filteredInvoices = useMemo(
    () => (invoices || []).filter((inv) => invoiceListMatchesFilter(inv, statusChip)),
    [invoices, statusChip]
  );
  const pagedInvoices = useMemo(
    () => paginateRows(filteredInvoices, listPage, listPageSize),
    [filteredInvoices, listPage, listPageSize]
  );
  const draftInvoiceIds = invoices
    .filter((inv) => invoiceFinancialStatus(inv) === "draft")
    .map((inv) => inv.id);

  useEffect(() => {
    setListPage(1);
  }, [searchQuery, statusChip]);
  const toggleSelectAll = () => {
    if (selectedIds.size === draftInvoiceIds.length && draftInvoiceIds.length > 0) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(draftInvoiceIds));
    }
  };

  // Delete single
  const handleDelete = async (id) => {
    setDeleteLoading(true);
    setDeleteErr("");
    try {
      await api.deleteSalesInvoice(id);
      setDeleteConfirm(null);
      loadInvoices();
    } catch (e) {
      const det = e.response?.data?.detail;
      const msg = formatDeleteError(det) || "Gagal menghapus faktur.";
      setDeleteErr(msg);
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
    setDeleteLoading(true);
    setDeleteErr("");
    try {
      const ids = Array.from(selectedIds);
      await api.bulkDeleteSalesInvoices(ids);
      setDeleteConfirm(null);
      setSelectedIds(new Set());
      loadInvoices();
    } catch (e) {
      const det = e.response?.data?.detail;
      const msg = formatDeleteError(det) || "Gagal menghapus faktur.";
      setDeleteErr(msg);
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
        initialPrices[l.line_idx] = l.unit_price || "";
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
    if (viewMode === "list") {
      loadInvoices();
    } else if (viewMode === "create") {
      loadAvailablePOs();
      if (selectedPoId) {
        loadPoPreview(selectedPoId);
      }
    }
  }, [viewMode, loadInvoices, loadAvailablePOs, loadPoPreview, selectedPoId]);

  // Handle PO selector change
  const handleSelectPo = (e) => {
    const pId = e.target.value;
    setSelectedPoId(pId);
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
    const baseDateStr = invoiceDate || getTodayString();
    try {
      const d = new Date(baseDateStr);
      d.setDate(d.getDate() + term);
      return d.toISOString().split("T")[0];
    } catch {
      return baseDateStr;
    }
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
      const p = Math.max(0, Number(rawP) || 0);
      const lineTotal = q * p;
      totalQty += q;
      subtotal += lineTotal;
      return {
        ...l,
        remaining,
        invoiceQty: rawQ !== undefined ? rawQ : remaining,
        numericQty: q,
        unitPrice: rawP !== undefined ? rawP : 0,
        numericPrice: p,
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
    const grandTotal = afterDiscount + ppn;

    const totalRemaining = previewData.lines.reduce(
      (acc, l) => acc + (Number(l.remaining_invoice_qty != null ? l.remaining_invoice_qty : l.quantity) || 0),
      0
    );

    return { lines: computedLines, subtotal, discount: disc, afterDiscount, ppn, ppnPercent: ppnPct, grandTotal, totalQty, totalRemaining, hasErrors, isDiscOver };
  }, [previewData, lineQuantities, linePrices, discountAmount, ppnPercent]);

  // Handle Save Invoice
  const handleSaveInvoice = async (e) => {
    e.preventDefault();
    if (!selectedPoId || !previewData) {
      setFormErr("Silakan pilih Pesanan Penjualan (PO) terlebih dahulu.");
      return;
    }
    if (createCalculations.hasErrors) {
      if (createCalculations.isDiscOver) {
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
        const numP = Number(rawP);
        if (numP < 0) {
          throw new Error(`Harga satuan untuk '${l.item_name}' tidak boleh negatif.`);
        }
        return {
          line_idx: l.line_idx,
          line_id: l.line_id,
          quantity: l.numericQty,
          unit_price: isNaN(numP) ? 0 : numP,
        };
      });

      const payload = {
        po_id: selectedPoId,
        invoice_date: invoiceDate || getTodayString(),
        payment_term_days: Number(paymentTermDays) || 0,
        discount_amount: Number(discountAmount) || 0,
        ppn_percent: Number(ppnPercent) || 0,
        notes: invoiceNotes.trim(),
        lines: linesPayload,
      };

      const res = await api.createSalesInvoice(payload);
      setSuccessMsg(res.message || "Faktur Penjualan berhasil dibuat");
      setTimeout(() => {
        setSearchParams({});
        setViewMode("list");
        loadInvoices();
      }, 900);
    } catch (err) {
      const det = err.response?.data?.detail;
      setFormErr(
        typeof det === "object"
          ? det.message
          : det || err.message || "Gagal menyimpan Faktur Penjualan"
      );
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
      setDetailDate(inv.invoice_date || "");
      setDetailTermDays(inv.payment_term_days || 0);
      setDetailDiscount(inv.discount_amount || 0);
      setDetailPpn(inv.ppn_percent || 0);
      setDetailNotes(inv.notes || "");
      const prices = {};
      (inv.lines || []).forEach((l) => {
        prices[l.line_idx] = l.unit_price != null ? l.unit_price : 0;
      });
      setDetailPrices(prices);
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

  // Detail live calculation
  const detailCalculations = useMemo(() => {
    if (!detailData || !detailData.lines) {
      return { lines: [], subtotal: 0, discount: 0, afterDiscount: 0, ppn: 0, grandTotal: 0, totalQty: 0 };
    }
    let totalQty = 0;
    let subtotal = 0;
    const computedLines = detailData.lines.map((l) => {
      const q = Number(l.quantity) || 0;
      const rawP = detailPrices[l.line_idx];
      const p = Math.max(0, Number(rawP) || 0);
      const lineTotal = q * p;
      totalQty += q;
      subtotal += lineTotal;
      return {
        ...l,
        unitPrice: rawP !== undefined ? rawP : 0,
        numericPrice: p,
        lineTotal,
      };
    });

    const disc = Math.max(0, Number(detailDiscount) || 0);
    const afterDiscount = Math.max(0, subtotal - disc);
    const ppnPct = Math.max(0, Number(detailPpn) || 0);
    const ppn = Math.round(afterDiscount * (ppnPct / 100));
    const grandTotal = afterDiscount + ppn;

    return { lines: computedLines, subtotal, discount: disc, afterDiscount, ppn, ppnPercent: ppnPct, grandTotal, totalQty };
  }, [detailData, detailPrices, detailDiscount, detailPpn]);

  // Handle Update Invoice
  const handleUpdateInvoice = async (e) => {
    e.preventDefault();
    if (!selectedInvoiceId || !detailData) return;
    setDetailSaving(true);
    setDetailErr("");
    setSuccessMsg("");

    try {
      const linesPayload = (detailData.lines || []).map((l) => {
        const rawP = detailPrices[l.line_idx];
        const numP = Number(rawP);
        if (numP < 0) {
          throw new Error(`Harga satuan untuk '${l.item_name}' tidak boleh negatif.`);
        }
        return {
          line_idx: l.line_idx,
          line_id: l.line_id,
          quantity: l.quantity,
          unit_price: isNaN(numP) ? 0 : numP,
        };
      });

      const payload = {
        invoice_date: detailDate,
        payment_term_days: Number(detailTermDays) || 0,
        discount_amount: Number(detailDiscount) || 0,
        ppn_percent: Number(detailPpn) || 0,
        notes: detailNotes.trim(),
        lines: linesPayload,
      };

      const res = await api.updateSalesInvoice(selectedInvoiceId, payload);
      const updated = res.invoice || res;
      setDetailData(updated);
      setSuccessMsg("Perubahan Faktur Penjualan berhasil disimpan");
      setTimeout(() => setSuccessMsg(""), 2500);
    } catch (err) {
      const det = err.response?.data?.detail;
      setDetailErr(
        typeof det === "object"
          ? det.message
          : det || err.message || "Gagal memperbarui Faktur Penjualan"
      );
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
    } catch (err) {
      const det = err.response?.data?.detail;
      setDetailErr(typeof det === "object" ? det.message : det || err.message || "Gagal finalisasi faktur");
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
    } catch (err) {
      const det = err.response?.data?.detail;
      setDetailErr(typeof det === "object" ? det.message : det || err.message || "Gagal posting accounting");
    } finally {
      setLifecycleBusy(false);
    }
  };

  const handleCancelInvoice = async () => {
    if (!selectedInvoiceId) return;
    setLifecycleBusy(true);
    setDetailErr("");
    try {
      const res = await api.cancelSalesInvoice(selectedInvoiceId);
      const updated = res.invoice || res;
      setDetailData(updated);
      setCancelConfirm(false);
      setSuccessMsg("Faktur dibatalkan.");
      setTimeout(() => setSuccessMsg(""), 2500);
    } catch (err) {
      const det = err.response?.data?.detail;
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
  const accountingStatus = String(detailData?.accounting_status || "not_posted").toLowerCase();
  const isAccountingPosted = accountingStatus === "posted" || detailData?.accounting_posted === true;
  const showPostAccounting =
    canPostAccounting &&
    detailStatus === "finalized" &&
    !isAccountingPosted &&
    detailData?.can_post_accounting === true;
  const showAccountingUnconfigured =
    detailStatus === "finalized" &&
    !isAccountingPosted &&
    detailData?.accounting_ready === false;
  const canEditFinancial = canEdit && !financialLocked;

  return (
    <div
      className="page-container penjualan-page penjualan-workspace sales-compact-workspace"
      data-testid="faktur-penjualan-page"
    >
      {/* HEADER SECTION */}
      <div
        className="page-heading sw-page-header"
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {viewMode !== "list" ? (
            <button
              type="button"
              className="ghost sm"
              onClick={() => {
                setSearchParams({});
                setViewMode("list");
              }}
              data-testid="back-to-list-btn"
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "6px 12px",
                borderRadius: 6,
                border: "1px solid #cbd5e1",
                background: "#f8fafc",
                cursor: "pointer",
              }}
            >
              <ArrowLeft size={16} /> Kembali
            </button>
          ) : (
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                background: "#f3e8ff",
                color: "#9333ea",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <FileText size={22} />
            </div>
          )}
          <div>
            <h1 style={{ margin: 0, fontSize: "1.5rem", fontWeight: 700, color: "#0f172a" }}>
              {viewMode === "create"
                ? "Buat Faktur Penjualan"
                : viewMode === "detail"
                ? "Detail Faktur Penjualan"
                : "Faktur Penjualan"}
            </h1>
            <p style={{ margin: "2px 0 0 0", fontSize: "0.85rem", color: "#64748b" }}>
              {viewMode === "create"
                ? "Buat Faktur Penjualan (parsial / penuh) dengan Diskon, PPN & Termin."
                : viewMode === "detail"
                ? `Dokumen Faktur #${detailData?.invoice_number || detailData?.id || selectedInvoiceId}`
                : "Daftar Faktur Penjualan dan status pelunasan pembayaran"}
            </p>
          </div>
        </div>

        {viewMode === "list" && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {canDelete && selectedIds.size > 0 && (
              <button
                type="button"
                data-testid="btn-hapus-terpilih"
                onClick={() => {
                  const ids = Array.from(selectedIds);
                  const numbers = invoices.filter((inv) => selectedIds.has(inv.id)).map((inv) => inv.invoice_number || inv.id);
                  setDeleteErr("");
                  setDeleteConfirm({ ids, numbers });
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "8px 14px",
                  borderRadius: 8,
                  background: "#fee2e2",
                  color: "#b91c1c",
                  border: "1px solid #fecaca",
                  cursor: "pointer",
                  fontWeight: 600,
                  fontSize: "0.9rem",
                }}
              >
                <Trash2 size={16} /> Hapus Terpilih ({selectedIds.size})
              </button>
            )}
            {canCreate && (
          <button
            type="button"
            className="primary"
            onClick={() => {
              setSelectedPoId("");
              setPreviewData(null);
              setSearchParams({ action: "create" });
              setViewMode("create");
            }}
            data-testid="btn-create-invoice"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 16px",
              borderRadius: 8,
              background: "#9333ea",
              color: "#fff",
              border: "none",
              cursor: "pointer",
              fontWeight: 600,
              fontSize: "0.9rem",
            }}
          >
            <Plus size={16} /> + Buat Faktur dari PO
          </button>
            )}
          </div>
        )}
      </div>

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
        <section className="sw-list-panel">
          {listErr && (
            <div className="alert-box error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", display: "flex", gap: 8 }}>
              <AlertCircle size={16} /><span>{listErr}</span>
            </div>
          )}
          <div className="sw-toolbar">
            <div className="sw-search">
              <Search size={16} className="sw-search-icon" />
              <input
                type="text"
                placeholder="Cari No PO / Customer / Sales / Faktur..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") loadInvoices(); }}
                data-testid="search-invoice-input"
              />
            </div>
            <div className="sw-chip-group">
              {[
                { id: "all", label: "Semua" },
                { id: "unpaid", label: "Belum Lunas" },
                { id: "partial", label: "Sebagian" },
                { id: "paid", label: "Lunas" },
                { id: "overdue", label: "Jatuh Tempo" },
              ].map((tab) => (
                <button key={tab.id} type="button" className={`sw-chip${statusChip === tab.id ? " is-active" : ""}`} onClick={() => setStatusChip(tab.id)}>
                  {tab.label}
                </button>
              ))}
            </div>
            <button type="button" className="ghost" onClick={loadInvoices} disabled={listLoading} title="Refresh data" data-testid="refresh-invoice-btn" aria-label="Refresh data">
              <RefreshCw size={14} className={listLoading ? "spin" : ""} />
            </button>
          </div>
          <div className="sw-table-scroll sw-desktop-table">
            <table className="sw-table" data-testid="invoices-table">
              <thead>
                <tr>
                  {canDelete && (
                    <th style={{ width: 40 }}>
                      <input type="checkbox" data-testid="select-all-invoices" checked={draftInvoiceIds.length > 0 && selectedIds.size === draftInvoiceIds.length} onChange={toggleSelectAll} aria-label="Pilih seluruh hasil draft, bukan hanya halaman ini" title="Pilih seluruh hasil draft" />
                    </th>
                  )}
                  <th>Faktur / Tanggal</th>
                  <th>PO / Customer</th>
                  <th className="num">Total Faktur</th>
                  <th className="num sw-hide-tablet">Terbayar</th>
                  <th className="num">Sisa Tagihan</th>
                  <th className="sw-hide-tablet">Jatuh Tempo</th>
                  <th>Status</th>
                  <th className="sw-actions">Aksi</th>
                </tr>
              </thead>
              <tbody>
                {listLoading ? (
                  <tr><td colSpan={canDelete ? 9 : 8}>Memuat daftar Faktur Penjualan...</td></tr>
                ) : filteredInvoices.length === 0 ? (
                  <tr>
                    <td colSpan={canDelete ? 9 : 8} data-testid="empty-invoices">
                      <div className="sw-empty">
                        <FileText size={32} color="#94a3b8" />
                        <div style={{ fontWeight: 600, color: "#1e293b", marginTop: 8 }}>Belum ada Faktur Penjualan</div>
                      </div>
                    </td>
                  </tr>
                ) : (
                  pagedInvoices.rows.map((inv) => (
                    <tr key={inv.id} data-testid={`invoice-row-${inv.id}`} className="is-clickable" onClick={(e) => {
                      if (e.target.closest("input, button, a, [role='menu'], [role='menuitem']")) return;
                      handleOpenDetail(inv.id);
                    }}>
                      {canDelete && (
                        <td>
                          <input type="checkbox" data-testid={`invoice-checkbox-${inv.id}`} checked={selectedIds.has(inv.id)} onChange={() => toggleSelect(inv.id)} aria-label={`Pilih ${inv.invoice_number}`} />
                        </td>
                      )}
                      <td>
                        <span className="sw-primary mono" data-testid={`invoice-number-${inv.id}`}>{inv.invoice_number || "-"}</span>
                        <div className="sw-secondary">{formatDisplayDate(inv.invoice_date || inv.created_at)}</div>
                      </td>
                      <td>
                        <Link to={`/po/${inv.po_id}`} style={{ color: "#0284c7", textDecoration: "none" }}>{inv.po_number || "-"}</Link>
                        <div className="sw-secondary">{inv.customer || "-"}{inv.outlet ? ` · ${inv.outlet}` : ""}</div>
                      </td>
                      <td className="num">{formatRupiah(inv.grand_total)}</td>
                      <td className="num sw-hide-tablet">{formatRupiah(inv.paid_total)}</td>
                      <td className="num">{formatRupiah(inv.outstanding_amount)}</td>
                      <td className="sw-hide-tablet">{formatDisplayDate(inv.due_date) || "-"}</td>
                      <td>
                        {renderFinancialBadge(inv.financial_status)}
                        {(inv.accounting_status === "posted" || inv.accounting_posted === true) && renderPostedAccountingBadge()}
                        <div>{renderPaymentBadge(inv.payment_status)}</div>
                      </td>
                      <td className="sw-actions" onClick={(e) => e.stopPropagation()}>
                        <div style={{ display: "inline-flex", gap: 6 }}>
                          <button type="button" className="sw-icon-btn" onClick={() => handleOpenDetail(inv.id)} data-testid={`view-invoice-btn-${inv.id}`} title={invoiceFinancialStatus(inv) === "draft" ? "Lihat / Edit" : "Lihat"} aria-label={invoiceFinancialStatus(inv) === "draft" ? "Lihat / Edit" : "Lihat"}>
                            <FileText size={14} />
                          </button>
                          <SalesRowMenu
                            testId={`invoice-row-menu-${inv.id}`}
                            items={[
                              {
                                id: "pdf",
                                label: pdfLoadingId === inv.id ? "Memuat..." : "Cetak PDF",
                                testId: `btn-print-pdf-${inv.id}`,
                                onClick: (e) => handlePrintPdf(e, inv.id),
                              },
                              canDelete && invoiceFinancialStatus(inv) === "draft" && {
                                id: "delete",
                                label: "Hapus",
                                danger: true,
                                testId: `btn-delete-invoice-${inv.id}`,
                                onClick: () => {
                                  setDeleteErr("");
                                  setDeleteConfirm({ id: inv.id, number: inv.invoice_number, numbers: [inv.invoice_number] });
                                },
                              },
                            ]}
                          />
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <SalesPaginationBar
            page={pagedInvoices.page}
            pageSize={pagedInvoices.pageSize}
            pageCount={pagedInvoices.pageCount}
            total={pagedInvoices.total}
            start={pagedInvoices.start}
            end={pagedInvoices.end}
            onPageChange={setListPage}
            onPageSizeChange={(size) => { setListPageSize(size); setListPage(1); }}
          />
        </section>
      )}

      {/* VIEW: CREATE */}
      {viewMode === "create" && (
        <div className="sw-form-shell">
          <div className="sw-form-body">
          <p className="sw-required-hint">{[selectedPoId || queryPoId, invoiceDate, (createCalculations?.lines || []).some(l => Number(l.numericQty || 0) > 0)].filter(Boolean).length} dari 3 field wajib lengkap</p>
          <FormProgress
            percentage={Math.round(([selectedPoId || queryPoId, invoiceDate, (createCalculations?.lines || []).some(l => Number(l.numericQty || 0) > 0)].filter(Boolean).length / 3) * 100)}
            completed={[selectedPoId || queryPoId, invoiceDate, (createCalculations?.lines || []).some(l => Number(l.numericQty || 0) > 0)].filter(Boolean).length}
            total={3}
            className="mb-4"
          />

          {/* Step 1: PO Selector Panel (Only shown if opened directly from menu without pre-selected PO) */}
          {!queryPoId && (
            <FormSection
              id="fp-select-po"
              title="1. Pilih Pesanan Penjualan (PO Asal)"
              description="Pilih PO yang eligible untuk dibuatkan faktur penjualan."
              status={selectedPoId ? "complete" : "incomplete"}
            >
              <div style={{ maxWidth: 540 }}>
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

          {/* Step 2: PO Snapshot & Financial Config */}
          {previewLoading ? (
            <div style={{ padding: 32, textAlign: "center", color: "#64748b" }}>Memuat data PO...</div>
          ) : previewData ? (
            <form onSubmit={handleSaveInvoice}>
              <FormSection
                id="fp-snapshot-financial"
                title={queryPoId ? "Informasi PO & Konfigurasi Finansial Faktur" : "2. Informasi PO Snapshot & Konfigurasi Finansial"}
                description="Detail PO asal dan ketentuan termin pembayaran faktur."
                status={computeStatus([{ value: invoiceDate, isRequired: true }])}
                className="panel"
              >
                <div data-testid="po-snapshot-card">
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
                  <div>
                    <span style={{ fontSize: "0.75rem", color: "#64748b", textTransform: "uppercase" }}>No PO</span>
                    <strong data-testid="snapshot-po-number" style={{ display: "block", fontSize: "0.95rem", color: "#0f172a" }}>{previewData.po_number}</strong>
                  </div>
                  <div>
                    <span style={{ fontSize: "0.75rem", color: "#64748b", textTransform: "uppercase" }}>Customer</span>
                    <strong data-testid="snapshot-customer" style={{ display: "block", fontSize: "0.95rem", color: "#0f172a" }}>{previewData.customer}</strong>
                  </div>
                  <div>
                    <span style={{ fontSize: "0.75rem", color: "#64748b", textTransform: "uppercase" }}>Outlet</span>
                    <span data-testid="snapshot-outlet" style={{ display: "block", fontSize: "0.95rem", color: "#334155" }}>{previewData.outlet || "-"}</span>
                  </div>
                  <div>
                    <span style={{ fontSize: "0.75rem", color: "#64748b", textTransform: "uppercase" }}>Sales</span>
                    <span data-testid="snapshot-sales" style={{ display: "block", fontSize: "0.95rem", color: "#334155" }}>{previewData.sales || "-"}</span>
                  </div>
                </div>

                {/* Financial Controls: Tanggal, Termin, Diskon, PPN */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                    gap: 16,
                    marginTop: 18,
                    paddingTop: 16,
                    borderTop: "1px solid #f1f5f9",
                  }}
                >
                  <div>
                    <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "#334155", display: "block", marginBottom: 6 }}>
                      Tanggal Faktur <span style={{ color: "#ef4444" }}>*</span>
                    </label>
                    <input
                      type="date"
                      value={invoiceDate}
                      onChange={(e) => setInvoiceDate(e.target.value)}
                      data-testid="invoice-date-input"
                      style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6 }}
                    />
                  </div>

                  <div>
                    <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "#334155", display: "block", marginBottom: 6 }}>
                      Termin Pembayaran (Hari)
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
                      Jatuh Tempo: <strong>{formatDisplayDate(calculatedDueDate)}</strong>
                    </div>
                  </div>

                  <div>
                    <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "#334155", display: "block", marginBottom: 6 }}>
                      Diskon Nominal (Rp)
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
                </div>
                </div>
              </FormSection>

              {/* Table Line Items with Partial Invoicing Quantity */}
              <FormSection
                id="fp-line-items"
                title="3. Rincian Barang, Kuantitas Faktur & Harga Satuan"
                description="Mendukung faktur parsial. Masukkan jumlah kuantitas yang ditagihkan (maksimal sisa PO) dan harga satuan per baris."
                status={(createCalculations?.lines || []).some(l => Number(l.numericQty || 0) > 0) ? "complete" : "incomplete"}
                className="panel"
              >

                <div className="sw-lines-viewport" style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }}>
                    <thead>
                      <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                        <th style={{ padding: "10px 12px", width: 40 }}>#</th>
                        <th style={{ padding: "10px 12px", minWidth: 200 }}>Barang / Jasa</th>
                        <th style={{ padding: "10px 12px", width: 140, textAlign: "right" }}>Sisa PO</th>
                        <th style={{ padding: "10px 12px", width: 150, textAlign: "right" }}>
                          Qty Faktur <span style={{ color: "#ef4444" }}>*</span>
                        </th>
                        <th style={{ padding: "10px 12px", width: 180, textAlign: "right" }}>
                          Harga Satuan (Rp) <span style={{ color: "#ef4444" }}>*</span>
                        </th>
                        <th style={{ padding: "10px 12px", width: 180, textAlign: "right" }}>Total Line (Rp)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {createCalculations.lines.map((li, index) => (
                        <tr key={li.line_idx} style={{ borderBottom: "1px solid #f1f5f9" }} data-testid={`create-line-${li.line_idx}`}>
                          <td style={{ padding: "10px 12px", color: "#64748b", fontWeight: 600 }}>{index + 1}</td>
                          <td style={{ padding: "10px 12px" }}>
                            <div style={{ fontWeight: 600, color: "#1e293b" }}>{li.item_name}</div>
                            {li.color && <div style={{ fontSize: "0.75rem", color: "#64748b" }}>Warna: {li.color}</div>}
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
                              placeholder="0"
                              data-testid={`unit-price-input-${li.line_idx}`}
                              style={{
                                width: "100%",
                                padding: "6px 10px",
                                border: "1px solid #cbd5e1",
                                borderRadius: 6,
                                fontWeight: 600,
                                textAlign: "right",
                              }}
                            />
                          </td>
                          <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 700, color: "#0f172a" }}>
                            {formatRupiah(li.lineTotal)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Live Calculation Summary Breakdown */}
                <div
                  style={{
                    marginTop: 20,
                    padding: 16,
                    background: "#f8fafc",
                    borderRadius: 8,
                    border: "1px solid #e2e8f0",
                    display: "flex",
                    justifyContent: "flex-end",
                  }}
                >
                  <div style={{ width: 340 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: "0.9rem" }}>
                      <span style={{ color: "#64748b" }}>Subtotal ({createCalculations.totalQty} pcs):</span>
                      <span style={{ fontWeight: 600, color: "#1e293b" }}>{formatRupiah(createCalculations.subtotal)}</span>
                    </div>

                    {createCalculations.discount > 0 && (
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: "0.9rem" }}>
                        <span style={{ color: "#64748b" }}>Diskon:</span>
                        <span style={{ fontWeight: 600, color: "#dc2626" }}>- {formatRupiah(createCalculations.discount)}</span>
                      </div>
                    )}

                    {createCalculations.discount > 0 && (
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: "0.9rem" }}>
                        <span style={{ color: "#64748b" }}>Setelah Diskon (DPP):</span>
                        <span style={{ fontWeight: 600, color: "#1e293b" }}>{formatRupiah(createCalculations.afterDiscount)}</span>
                      </div>
                    )}

                    {createCalculations.ppnPercent > 0 && (
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: "0.9rem" }}>
                        <span style={{ color: "#64748b" }}>PPN ({createCalculations.ppnPercent}%):</span>
                        <span style={{ fontWeight: 600, color: "#1e293b" }}>+ {formatRupiah(createCalculations.ppn)}</span>
                      </div>
                    )}

                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        paddingTop: 8,
                        marginTop: 8,
                        borderTop: "2px solid #cbd5e1",
                        fontSize: "1.1rem",
                        fontWeight: 800,
                        color: "#0f172a",
                      }}
                    >
                      <span>Total Faktur:</span>
                      <span style={{ color: "#9333ea" }}>{formatRupiah(createCalculations.grandTotal)}</span>
                    </div>
                  </div>
                </div>
              </FormSection>

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

              <div className="sw-form-foot" style={{ marginTop: 8 }}>
                <div className="sw-required-hint">
                  Subtotal {formatRupiah(createCalculations.subtotal)} · Diskon {formatRupiah(createCalculations.discount)} · PPN {formatRupiah(createCalculations.ppn)} · Total {formatRupiah(createCalculations.grandTotal)}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setSearchParams({});
                    setViewMode("list");
                  }}
                  disabled={formBusy}
                >
                  Batal
                </button>
                <button
                  type="submit"
                  className="primary"
                  disabled={formBusy || createCalculations.hasErrors || createCalculations.totalRemaining <= 0 || createCalculations.totalQty <= 0}
                  data-testid="save-invoice-btn"
                >
                  {formBusy ? "Menyimpan..." : "Simpan Faktur Penjualan"}
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
        </div>
      )}

      {/* VIEW: DETAIL / EDIT */}
      {viewMode === "detail" && (
        <div>
          {detailLoading ? (
            <div style={{ padding: 40, textAlign: "center", color: "#64748b" }}>Memuat detail Faktur Penjualan...</div>
          ) : detailData ? (
            <form onSubmit={handleUpdateInvoice}>
              {/* Payment Summary Bar */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                  gap: 14,
                  marginBottom: 20,
                }}
              >
                <div style={{ backgroundColor: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: 14 }}>
                  <span style={{ fontSize: 12, color: "#64748b" }}>Total Tagihan Faktur</span>
                  <div style={{ fontSize: 18, fontWeight: 800, color: "#0f172a", marginTop: 4 }}>
                    {formatRupiah(detailData.grand_total)}
                  </div>
                </div>

                <div style={{ backgroundColor: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: 14 }}>
                  <span style={{ fontSize: 12, color: "#64748b" }}>Sudah Diterima (Terbayar)</span>
                  <div style={{ fontSize: 18, fontWeight: 800, color: "#059669", marginTop: 4 }}>
                    {formatRupiah(detailData.paid_total)}
                  </div>
                </div>

                <div style={{ backgroundColor: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: 14 }}>
                  <span style={{ fontSize: 12, color: "#64748b" }}>Sisa Tagihan</span>
                  <div style={{ fontSize: 18, fontWeight: 800, color: detailData.outstanding_amount > 0 ? "#dc2626" : "#059669", marginTop: 4 }}>
                    {formatRupiah(detailData.outstanding_amount)}
                  </div>
                </div>

                <div style={{ backgroundColor: "#fff", border: "1px solid #e2e8f0", borderRadius: 8, padding: 14, display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 12, color: "#64748b" }}>Status Pembayaran</span>
                  <div style={{ marginTop: 4 }}>
                    {renderPaymentBadge(detailData.payment_status)}
                  </div>
                </div>
              </div>

              {/* Snapshot Info Card */}
              <section
                className="panel"
                style={{
                  background: "#fff",
                  border: "1px solid #e2e8f0",
                  borderRadius: 10,
                  padding: 20,
                  marginBottom: 20,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16 }}>
                  <div>
                    <span style={{ fontSize: "0.75rem", color: "#64748b", textTransform: "uppercase" }}>No. Faktur</span>
                    <h2 style={{ margin: "2px 0 0", fontSize: "1.25rem", fontWeight: 700, color: "#9333ea" }} data-testid="detail-invoice-number">
                      {detailData.invoice_number || "-"}
                    </h2>
                    <div style={{ marginTop: 8 }}>
                      {renderFinancialBadge(detailStatus)}
                      {isAccountingPosted && renderPostedAccountingBadge()}
                    </div>
                    {showAccountingUnconfigured && (
                      <p data-testid="invoice-accounting-unconfigured" style={{ margin: "8px 0 0", fontSize: "0.85rem", color: "#b45309" }}>
                        Accounting belum dikonfigurasi.{" "}
                        <Link to="/settings?tab=sales-accounting" style={{ color: "#7c3aed", fontWeight: 600 }}>
                          Buka Pengaturan Akuntansi Penjualan
                        </Link>
                      </p>
                    )}
                  </div>
                  {detailData.outstanding_amount > 0 && canCreate && (
                    <button
                      type="button"
                      onClick={() => navigate(`/penjualan/penerimaan-penjualan?invoice_id=${detailData.id}`)}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "8px 14px",
                        backgroundColor: "#059669",
                        color: "#fff",
                        border: "none",
                        borderRadius: 6,
                        fontWeight: 600,
                        fontSize: 13,
                        cursor: "pointer",
                      }}
                    >
                      <CreditCard size={15} /> + Buat Penerimaan
                    </button>
                  )}
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 16 }}>
                  <div>
                    <span style={{ fontSize: "0.75rem", color: "#64748b", textTransform: "uppercase" }}>No PO</span>
                    <strong style={{ display: "block" }}>{detailData.po_number}</strong>
                  </div>
                  <div>
                    <span style={{ fontSize: "0.75rem", color: "#64748b", textTransform: "uppercase" }}>Customer</span>
                    <strong style={{ display: "block" }}>{detailData.customer}</strong>
                  </div>
                  <div>
                    <span style={{ fontSize: "0.75rem", color: "#64748b", textTransform: "uppercase" }}>Jatuh Tempo</span>
                    <span style={{ display: "block", color: "#334155" }}>{formatDisplayDate(detailData.due_date)}</span>
                  </div>
                </div>

                {/* Editable fields */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                    gap: 16,
                    marginTop: 16,
                    paddingTop: 16,
                    borderTop: "1px solid #f1f5f9",
                  }}
                >
                  <div>
                    <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "#334155", display: "block", marginBottom: 6 }}>
                      Tanggal Faktur
                    </label>
                    <input
                      type="date"
                      value={detailDate}
                      onChange={(e) => setDetailDate(e.target.value)}
                      disabled={!canEditFinancial}
                      style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6 }}
                    />
                  </div>

                  <div>
                    <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "#334155", display: "block", marginBottom: 6 }}>
                      Diskon (Rp)
                    </label>
                    <input
                      type="number"
                      min="0"
                      value={detailDiscount}
                      onChange={(e) => setDetailDiscount(e.target.value)}
                      disabled={!canEditFinancial}
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
                      value={detailPpn}
                      onChange={(e) => setDetailPpn(e.target.value)}
                      disabled={!canEditFinancial}
                      style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6 }}
                    />
                  </div>

                  <div style={{ gridColumn: "1 / -1" }}>
                    <label style={{ fontSize: "0.85rem", fontWeight: 600, color: "#334155", display: "block", marginBottom: 6 }}>
                      Catatan Faktur
                    </label>
                    <input
                      type="text"
                      value={detailNotes}
                      onChange={(e) => setDetailNotes(e.target.value)}
                      disabled={!canEditFinancial}
                      style={{ width: "100%", padding: "8px 12px", border: "1px solid #cbd5e1", borderRadius: 6 }}
                    />
                  </div>
                </div>
              </section>

              {/* Items Table */}
              <section
                className="panel"
                style={{
                  background: "#fff",
                  border: "1px solid #e2e8f0",
                  borderRadius: 10,
                  padding: 20,
                  marginBottom: 20,
                }}
              >
                <header style={{ borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16 }}>
                  <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>Rincian Barang &amp; Harga Satuan</h3>
                </header>

                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.9rem" }}>
                    <thead>
                      <tr style={{ background: "#f8fafc", borderBottom: "2px solid #e2e8f0", color: "#475569" }}>
                        <th style={{ padding: "10px 12px", width: 40 }}>#</th>
                        <th style={{ padding: "10px 12px", minWidth: 200 }}>Barang / Jasa</th>
                        <th style={{ padding: "10px 12px", width: 120, textAlign: "right" }}>Qty</th>
                        <th style={{ padding: "10px 12px", width: 80, textAlign: "center" }}>Unit</th>
                        <th style={{ padding: "10px 12px", width: 180, textAlign: "right" }}>Harga Satuan (Rp)</th>
                        <th style={{ padding: "10px 12px", width: 180, textAlign: "right" }}>Total Line (Rp)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detailCalculations.lines.map((li, index) => (
                        <tr key={li.line_idx} style={{ borderBottom: "1px solid #f1f5f9" }}>
                          <td style={{ padding: "10px 12px", color: "#64748b", fontWeight: 600 }}>{index + 1}</td>
                          <td style={{ padding: "10px 12px", fontWeight: 600, color: "#1e293b" }}>{li.item_name}</td>
                          <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 600 }}>
                            {Number(li.quantity || 0).toLocaleString("id-ID")}
                          </td>
                          <td style={{ padding: "10px 12px", textAlign: "center", color: "#64748b" }}>{li.unit || "pcs"}</td>
                          <td style={{ padding: "8px 12px", textAlign: "right" }}>
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
                              style={{ width: "100%", padding: "6px 10px", border: "1px solid #cbd5e1", borderRadius: 6, textAlign: "right", fontWeight: 600 }}
                            />
                          </td>
                          <td style={{ padding: "10px 12px", textAlign: "right", fontWeight: 700, color: "#0f172a" }}>
                            {formatRupiah(li.lineTotal)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div style={{ marginTop: 16, display: "flex", justifyContent: "flex-end" }}>
                  <div style={{ width: 340 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                      <span style={{ color: "#64748b" }}>Subtotal:</span>
                      <span style={{ fontWeight: 600 }}>{formatRupiah(detailCalculations.subtotal)}</span>
                    </div>
                    {detailCalculations.discount > 0 && (
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                        <span style={{ color: "#64748b" }}>Diskon:</span>
                        <span style={{ fontWeight: 600, color: "#dc2626" }}>- {formatRupiah(detailCalculations.discount)}</span>
                      </div>
                    )}
                    {detailCalculations.ppnPercent > 0 && (
                      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                        <span style={{ color: "#64748b" }}>PPN ({detailCalculations.ppnPercent}%):</span>
                        <span style={{ fontWeight: 600 }}>+ {formatRupiah(detailCalculations.ppn)}</span>
                      </div>
                    )}
                    <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 8, borderTop: "2px solid #cbd5e1", fontWeight: 800 }}>
                      <span>Total Faktur:</span>
                      <span style={{ color: "#9333ea", fontSize: "1.15rem" }}>{formatRupiah(detailCalculations.grandTotal)}</span>
                    </div>
                  </div>
                </div>
              </section>

              {/* Linked Receipts History */}
              <section
                className="panel"
                style={{
                  background: "#fff",
                  border: "1px solid #e2e8f0",
                  borderRadius: 10,
                  padding: 20,
                  marginBottom: 20,
                }}
              >
                <header style={{ borderBottom: "1px solid #f1f5f9", paddingBottom: 12, marginBottom: 16 }}>
                  <h3 style={{ margin: 0, fontSize: "1.1rem", color: "#1e293b" }}>Riwayat Penerimaan Pembayaran</h3>
                </header>

                {(detailData.receipts || []).length === 0 ? (
                  <div style={{ padding: 24, textAlign: "center", color: "#64748b" }}>
                    Belum ada pembayaran yang diterima untuk faktur ini.
                  </div>
                ) : (
                  <table style={{ width: "100%", borderCollapse: "collapse", textAlign: "left", fontSize: "0.85rem" }}>
                    <thead>
                      <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0", color: "#475569" }}>
                        <th style={{ padding: "8px 12px" }}>No. Penerimaan</th>
                        <th style={{ padding: "8px 12px" }}>Tanggal</th>
                        <th style={{ padding: "8px 12px", textAlign: "right" }}>Jumlah Penerimaan</th>
                        <th style={{ padding: "8px 12px", textAlign: "center" }}>Status</th>
                        <th style={{ padding: "8px 12px" }}>Catatan</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detailData.receipts.map((r, idx) => (
                        <tr key={r.id || idx} style={{ borderBottom: "1px solid #f1f5f9" }}>
                          <td style={{ padding: "8px 12px" }}>
                            <span style={{ fontFamily: "monospace", color: "#059669", fontWeight: 700 }}>
                              {r.receipt_number || r.id}
                            </span>
                          </td>
                          <td style={{ padding: "8px 12px", fontWeight: 500 }}>{formatDisplayDate(r.receipt_date)}</td>
                          <td style={{ padding: "8px 12px", textAlign: "right", fontWeight: 700, color: "#059669" }}>
                            {formatRupiah(r.amount)}
                          </td>
                          <td style={{ padding: "8px 12px", textAlign: "center" }}>
                            <span style={{ fontSize: 11, fontWeight: 700, color: r.payment_state === "LUNAS" ? "#065f46" : "#3730a3" }}>
                              {r.payment_state}
                            </span>
                          </td>
                          <td style={{ padding: "8px 12px", color: "#64748b" }}>{r.notes || "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>

              {detailErr && (
                <div className="alert-box error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "10px 14px", borderRadius: 8, marginBottom: 16 }}>
                  {detailErr}
                </div>
              )}

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 12, marginTop: 20 }}>
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
                {canEdit && detailStatus === "draft" && (
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
                {canEdit && detailStatus === "finalized" && detailData.can_cancel !== false && (
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
                {canEditFinancial && (
                  <button
                    type="submit"
                    className="primary"
                    disabled={detailSaving}
                    style={{ padding: "10px 24px", borderRadius: 8, background: detailSaving ? "#94a3b8" : "#9333ea", color: "#fff", border: "none", cursor: detailSaving ? "not-allowed" : "pointer", fontWeight: 600 }}
                  >
                    {detailSaving ? "Menyimpan..." : "Simpan Perubahan"}
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
            <h3 style={{ margin: "0 0 8px", fontSize: "1.1rem" }}>Hapus Faktur Penjualan?</h3>
            <p style={{ margin: "0 0 12px", color: "#64748b", fontSize: "0.9rem" }}>Dokumen berikut akan dihapus:</p>
            <ul data-testid="delete-confirm-numbers" style={{ margin: "0 0 16px", paddingLeft: 18, fontWeight: 700, color: "#9333ea" }}>
              {(deleteConfirm.numbers || [deleteConfirm.number]).filter(Boolean).map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
            {deleteErr && (
              <div data-testid="delete-error" style={{ background: "#fef2f2", color: "#b91c1c", padding: "8px 12px", borderRadius: 8, marginBottom: 12, fontSize: "0.85rem" }}>
                {deleteErr}
              </div>
            )}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button type="button" onClick={() => { setDeleteConfirm(null); setDeleteErr(""); }} disabled={deleteLoading} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer" }}>
                Batal
              </button>
              <button
                type="button"
                data-testid="btn-confirm-delete"
                onClick={() => (deleteConfirm.ids ? handleBulkDelete() : handleDelete(deleteConfirm.id))}
                disabled={deleteLoading}
                style={{ padding: "8px 14px", borderRadius: 8, border: "none", background: "#dc2626", color: "#fff", fontWeight: 600, cursor: "pointer" }}
              >
                {deleteLoading ? "Menghapus..." : "Hapus"}
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
              Finalisasi akan mengunci tanggal, item, harga, diskon, PPN, dan total invoice untuk kebutuhan accounting.
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
