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
import { loadedRangeLabel, showLoadedDocumentCount } from "../lib/salesSubmenuDensity";
import "./salesSubmenuDensity.css";

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
  const [reverseConfirm, setReverseConfirm] = useState(false);
  const [reverseReason, setReverseReason] = useState("");
  const [reverseErr, setReverseErr] = useState("");
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [invoiceFormTab, setInvoiceFormTab] = useState("items");

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
  const draftInvoiceIds = invoices
    .filter((inv) => invoiceFinancialStatus(inv) === "draft")
    .map((inv) => inv.id);
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
    const grandTotal = afterDiscount + ppn;

    const totalRemaining = previewData.lines.reduce(
      (acc, l) => acc + (Number(l.remaining_invoice_qty != null ? l.remaining_invoice_qty : l.quantity) || 0),
      0
    );

    const priceMissingAny = computedLines.some((line) => line.numericQty > 0 && line.priceMissing);
    return { lines: computedLines, subtotal, discount: disc, afterDiscount, ppn, ppnPercent: ppnPct, grandTotal, totalQty, totalRemaining, hasErrors, isDiscOver, priceMissingAny };
  }, [previewData, lineQuantities, linePrices, discountAmount, ppnPercent]);

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
        const blank = rawP === "" || rawP === null || rawP === undefined;
        const numP = Number(rawP);
        if (blank || Number.isNaN(numP)) {
          throw new Error(`Lengkapi harga jual untuk '${l.item_name}'. Harga kosong tidak dijadikan Rp0.`);
        }
        if (numP < 0) {
          throw new Error(`Harga satuan untuk '${l.item_name}' tidak boleh negatif.`);
        }
        return {
          line_idx: l.line_idx,
          line_id: l.line_id,
          quantity: l.quantity,
          unit_price: numP,
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
  const canReverseAccountingPerm =
    hasPerm("sales_accounting.reverse") || user?.role === "admin";
  const accountingStatus = accountingStatusOf(detailData);
  const isAccountingPosted = accountingStatus === "posted" || detailData?.accounting_posted === true;
  const isAccountingReversed = accountingStatus === "reversed";
  const showPostAccounting =
    canPostAccounting &&
    detailStatus === "finalized" &&
    !isAccountingPosted &&
    !isAccountingReversed &&
    detailData?.can_post_accounting === true;
  const showReverseAccounting =
    canReverseAccountingPerm &&
    detailStatus === "finalized" &&
    isAccountingPosted &&
    detailData?.can_reverse_accounting === true;
  const reverseBlockers = Array.isArray(detailData?.reverse_blockers) ? detailData.reverse_blockers : [];
  const showReverseBlocked =
    canReverseAccountingPerm &&
    detailStatus === "finalized" &&
    isAccountingPosted &&
    !showReverseAccounting &&
    reverseBlockers.length > 0;
  const showAccountingUnconfigured =
    detailStatus === "finalized" &&
    !isAccountingPosted &&
    detailData?.accounting_ready === false;
  const canEditFinancial = canEdit && !financialLocked;

  const showInvoiceCount = showLoadedDocumentCount({
    loading: listLoading,
    error: listErr,
    query: searchQuery,
  });

  return (
    <div
      className={"page-container penjualan-page penjualan-workspace sd-density " + (viewMode === "list" ? "sd-list-mode" : "sd-form-mode")}
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
          {viewMode === "list" && showInvoiceCount && (
            <div className="sd-list-summary" data-testid="invoice-list-count">
              <strong>{invoices.length.toLocaleString("id-ID")}</strong>
              <span>faktur</span>
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

        {viewMode === "list" && (
          <div className="sd-list-header-actions">
            {canDelete && selectedIds.size > 0 && (
              <button
                type="button"
                className="sd-btn-danger"
                data-testid="btn-hapus-terpilih"
                onClick={() => {
                  const ids = Array.from(selectedIds);
                  const numbers = invoices.filter((inv) => selectedIds.has(inv.id)).map((inv) => inv.invoice_number || inv.id);
                  setDeleteErr("");
                  setDeleteConfirm({ ids, numbers });
                }}
              >
                <Trash2 size={16} /> Hapus Terpilih ({selectedIds.size})
              </button>
            )}
            <button
              type="button"
              className="sd-list-refresh"
              onClick={loadInvoices}
              disabled={listLoading}
              title="Refresh data"
              data-testid="refresh-invoice-btn"
            >
              <RefreshCw size={16} className={listLoading ? "spin" : ""} />
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
            <div className="sd-banner is-error" role="alert">
              <AlertCircle size={16} />
              <span>{listErr}</span>
            </div>
          )}

          <div className="sd-list-toolbar">
            <div className="sd-list-search">
              <Search size={16} />
              <input
                type="text"
                placeholder="Cari No PO / Customer / Sales / Faktur..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") loadInvoices();
                }}
                data-testid="search-invoice-input"
              />
            </div>
          </div>

          <div className="sd-list-table-scroll">
            <table className="sd-list-table" data-testid="invoices-table">
              <colgroup>
                {canDelete && <col style={{ width: 36 }} />}
                <col />
                <col style={{ width: 148 }} />
                <col style={{ width: 92 }} />
                <col style={{ width: 118 }} />
                <col style={{ width: 118 }} />
                <col style={{ width: 108 }} />
                <col style={{ width: 108 }} />
                <col style={{ width: 248 }} />
              </colgroup>
              <thead>
                <tr>
                  {canDelete && (
                    <th>
                      <input
                        type="checkbox"
                        data-testid="select-all-invoices"
                        checked={draftInvoiceIds.length > 0 && selectedIds.size === draftInvoiceIds.length}
                        onChange={toggleSelectAll}
                        aria-label="Pilih semua baris tampilan"
                      />
                    </th>
                  )}
                  <th>Pelanggan / No. Faktur</th>
                  <th>No. PO</th>
                  <th>Tanggal</th>
                  <th>Total Faktur</th>
                  <th>Sisa Tagihan</th>
                  <th>Status Faktur</th>
                  <th>Pembayaran</th>
                  <th>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {listLoading ? (
                  <tr>
                    <td colSpan={canDelete ? 9 : 8} className="sd-list-empty">
                      Memuat daftar Faktur Penjualan...
                    </td>
                  </tr>
                ) : invoices.length === 0 ? (
                  <tr>
                    <td colSpan={canDelete ? 9 : 8} className="sd-list-empty" data-testid="empty-invoices">
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                        <FileText size={32} color="#94a3b8" />
                        <div style={{ fontWeight: 600, color: "#1e293b", marginTop: 8 }}>Belum ada Faktur Penjualan</div>
                      </div>
                    </td>
                  </tr>
                ) : (
                  invoices.map((inv) => (
                    <tr key={inv.id} data-testid={`invoice-row-${inv.id}`}>
                      {canDelete && (
                        <td>
                          <input
                            type="checkbox"
                            data-testid={`invoice-checkbox-${inv.id}`}
                            checked={selectedIds.has(inv.id)}
                            onChange={() => toggleSelect(inv.id)}
                            aria-label={`Pilih ${inv.invoice_number}`}
                          />
                        </td>
                      )}
                      <td className="sd-identity">
                        <div className="sd-customer">{inv.customer || "-"}</div>
                        <span className="sd-doc is-static" data-testid={`invoice-number-${inv.id}`}>
                          {inv.invoice_number || "-"}
                        </span>
                        {inv.outlet ? <span className="sd-support">{inv.outlet}</span> : null}
                      </td>
                      <td>
                        <Link to={`/po/${inv.po_id}`} className="sd-doc" title="Buka PO">
                          {inv.po_number || "-"}
                        </Link>
                      </td>
                      <td>{formatDisplayDate(inv.invoice_date || inv.created_at)}</td>
                      <td className="sd-money">{formatRupiah(inv.grand_total)}</td>
                      <td className={"sd-money " + (Number(inv.outstanding_amount) > 0 ? "is-due" : "is-zero")}>
                        {formatRupiah(inv.outstanding_amount)}
                      </td>
                      <td>
                        {renderFinancialBadge(inv.financial_status)}
                        {(invoiceFinancialStatus(inv) !== "draft" || accountingStatusOf(inv) !== "not_posted") &&
                          renderAccountingBadge(accountingStatusOf(inv))}
                      </td>
                      <td>{renderPaymentBadge(inv.payment_status)}</td>
                      <td>
                        <div className="sd-actions">
                        <button
                          type="button"
                          className="sd-act sd-act-view"
                          onClick={() => handleOpenDetail(inv.id)}
                          data-testid={`view-invoice-btn-${inv.id}`}
                        >
                          {invoiceFinancialStatus(inv) === "draft" ? "Lihat / Edit" : "Lihat"}
                        </button>
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
                        {canDelete && invoiceFinancialStatus(inv) === "draft" && (
                          <button
                            type="button"
                            className="sd-act sd-act-delete"
                            data-testid={`btn-delete-invoice-${inv.id}`}
                            onClick={() => {
                              setDeleteErr("");
                              setDeleteConfirm({ id: inv.id, number: inv.invoice_number, numbers: [inv.invoice_number] });
                            }}
                          >
                            Hapus
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
          <footer className="sd-list-footer" data-testid="invoice-list-footer">
            <span>
              {listLoading
                ? "Memuat daftar faktur..."
                : listErr
                ? "Daftar faktur belum dapat ditampilkan"
                : loadedRangeLabel(invoices.length, "faktur")}
            </span>
          </footer>
        </section>
      )}

      {/* VIEW: CREATE */}
      {viewMode === "create" && (
        <div className="sd-form fp-create">
          {/* Step 1: PO Selector Panel (Only shown if opened directly from menu without pre-selected PO) */}
          {!queryPoId && (
            <FormSection
              id="fp-select-po"
              title="Pilih Pesanan Penjualan"
              description="Pilih PO yang eligible untuk dibuatkan faktur penjualan."
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
            <form className="sd-form-fill" data-form-tab={invoiceFormTab} onSubmit={handleSaveInvoice}>
              <div className="sd-form-stage">
              <div className="fp-summary" data-testid="po-snapshot-card">
                <div>
                  <span>Nomor PO</span>
                  <strong data-testid="snapshot-po-number">{previewData.po_number}</strong>
                </div>
                <div>
                  <span>Customer</span>
                  <strong data-testid="snapshot-customer">{previewData.customer}</strong>
                </div>
                <div>
                  <span>Outlet / Cabang</span>
                  <strong data-testid="snapshot-outlet">{previewData.outlet || "-"}</strong>
                </div>
                <div>
                  <span>Sales Representative</span>
                  <strong data-testid="snapshot-sales">{previewData.sales || "-"}</strong>
                </div>
              </div>
              <div className="sd-form-tabs" role="tablist" aria-label="Bagian faktur">
                <button type="button" className={invoiceFormTab === "items" ? "is-active" : ""} onClick={() => setInvoiceFormTab("items")}>Barang & Harga</button>
                <button type="button" className={invoiceFormTab === "terms" ? "is-active" : ""} onClick={() => setInvoiceFormTab("terms")}>Tanggal & Ketentuan</button>
              </div>

              <div className="fp-terms sd-pane-terms">
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

              <div className="fp-items sd-pane-items">
                <div className="sd-lines">
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

                <p className="fp-price-note">Harga mengikuti pesanan penjualan.</p>
                <div className="fp-breakdown" data-testid="invoice-breakdown">
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
                  <strong data-testid="invoice-grand-total">{invoiceCompleteness.totalsReady ? formatRupiah(createCalculations.grandTotal) : "Belum lengkap"}</strong>
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
        <div className="sd-form">
          {detailLoading ? (
            <div className="sd-form-stage" style={{ padding: 40, textAlign: "center", color: "#64748b" }}>Memuat detail Faktur Penjualan...</div>
          ) : detailData ? (
            <form className="sd-form-fill" data-form-tab={invoiceFormTab} onSubmit={handleUpdateInvoice}>
              <div className="sd-form-stage">
              <div className="sd-form-tabs" role="tablist" aria-label="Bagian detail faktur">
                <button type="button" className={invoiceFormTab === "items" ? "is-active" : ""} onClick={() => setInvoiceFormTab("items")}>Barang & Harga</button>
                <button type="button" className={invoiceFormTab === "terms" ? "is-active" : ""} onClick={() => setInvoiceFormTab("terms")}>Tanggal & Ketentuan</button>
              </div>
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
                      {(detailStatus !== "draft" || isAccountingPosted || isAccountingReversed) &&
                        renderAccountingBadge(accountingStatus)}
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

                <div className="sd-lines sd-pane-items">
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
