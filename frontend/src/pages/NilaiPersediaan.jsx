import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertCircle, Download, Search, X } from "lucide-react";
import InventoryValueMeaning from "../components/InventoryValueMeaning";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";
import { fmtQty } from "../components/qtyUi";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import {
  acceptValuationFocus,
  inventoryValueMasterItemIdFromSearch,
  inventoryValueReconPath,
  inventoryValueTabFromSearch,
} from "../lib/inventoryValueLink";
import { formatRupiah } from "../lib/purchaseUi";

const TABS = [
  { id: "daftar", label: "Daftar Barang" },
  { id: "opening", label: "Saldo Awal" },
  { id: "hpp", label: "HPP Penjualan" },
  { id: "recon", label: "Rekonsiliasi & Laporan" },
];

const STATUS_LABELS = {
  OK: "Ada harga pokok",
  MISSING_COST: "Belum Ada HPP",
  NEEDS_COST: "Butuh Harga PO",
  NEEDS_UOM: "Butuh Konversi Satuan",
  QTY_MISMATCH: "Qty Tidak Cocok",
  FAILED: "Gagal",
};
const POSTING_LABELS = {
  UNPOSTED: "Belum diposting",
  POSTED: "Posted",
  BLOCKED: "Terblokir",
  BUILDING: "Belum selesai",
  NEEDS_RECOVERY: "Perlu pemulihan",
  REVERSED: "Direversal",
};
const BOOK_STATUS_LABELS = {
  INCOMPLETE: "Belum lengkap",
  BLOCKED: "Perlu diselesaikan",
  MATCH: "Cocok",
};
const CENTER_CELL = { textAlign: "center", verticalAlign: "middle" };
const CENTER_SORT = { justifyContent: "center", width: "100%" };

function statusLabel(status) {
  return STATUS_LABELS[status] || status || "Belum Ada HPP";
}

function sourceLabel(source) {
  if (source === "PUBLISHED_CUSTOMER_PRICING_FALLBACK") return "Published Pricing";
  if (source === "PURCHASING_MOVING_AVERAGE") return "Rata-rata pembelian";
  if (source === "APPROVED_OPENING") return "Opening disetujui";
  return source || "Belum ada sumber";
}

function moneyOrGap(value) {
  if (value == null || value === "") return "Belum ada dasar";
  return formatRupiah(value);
}

function estimateUnknown(note = "Nilai estimasi, bukan saldo buku.") {
  return {
    state: "unknown",
    label: "Nilai estimasi stok",
    value: "Kelengkapan nilai belum diketahui",
    note,
  };
}

/** Coverage uses the API summary for the unfiltered catalog only. */
export function estimateCoverage({ summary, total, loading, failed, filterActive }) {
  const bookNote = "Nilai estimasi, bukan saldo buku.";
  if (loading) {
    return { state: "loading", label: "Nilai estimasi stok", value: "Memuat…", note: bookNote };
  }
  if (failed) {
    return { state: "failed", label: "Nilai estimasi stok", value: "Gagal dimuat", note: bookNote };
  }
  if (Number(total) === 0) {
    return { state: "empty", label: "Nilai estimasi stok", value: "Tidak ada barang", note: bookNote };
  }
  const fields = ["valued_items", "items_without_hpp", "problem_items", "total_inventory_value"];
  const known = summary && fields.every((key) => summary[key] != null && summary[key] !== "");
  if (!known || filterActive) return estimateUnknown(bookNote);
  const valued = Number(summary.valued_items);
  const missing = Number(summary.items_without_hpp);
  const problems = Number(summary.problem_items);
  if (![valued, missing, problems].every((value) => Number.isFinite(value))) return estimateUnknown(bookNote);
  const uncovered = missing > 0 || problems > 0;
  if (!uncovered) {
    if (Number(total) !== valued) return estimateUnknown(bookNote);
    return {
      state: "complete",
      label: "Nilai estimasi stok",
      value: formatRupiah(summary.total_inventory_value),
      note: bookNote,
    };
  }
  if (valued === 0) {
    return {
      state: "unvalued",
      label: "Nilai estimasi stok",
      value: "Belum dapat dihitung",
      note: `Ada barang yang belum punya harga pokok atau stoknya tidak terbukti. ${bookNote}`,
    };
  }
  return {
    state: "partial",
    label: "Estimasi sebagian",
    value: formatRupiah(summary.total_inventory_value),
    note: `Barang tanpa HPP atau stok tidak terbukti belum tercakup. ${bookNote}`,
  };
}

function openingLineIdentity(line) {
  const code = String(line?.item_code || "").trim();
  const name = String(line?.item_name || line?.name || "").trim();
  if (code && name) return `${code} · ${name}`;
  return code || name || "";
}

export function openingHistoryCells(row) {
  const lines = Array.isArray(row?.lines) ? row.lines : [];
  let barang = "—";
  if (lines.length > 1) barang = `${lines.length} barang`;
  else if (lines.length === 1) barang = openingLineIdentity(lines[0]) || "—";
  let qty = "—";
  if (lines.length === 1 && lines[0].qty != null && lines[0].qty !== "") {
    qty = `${fmtQty(lines[0].qty)}${lines[0].base_uom ? ` ${lines[0].base_uom}` : ""}`;
  } else if (lines.length > 1) {
    const units = [...new Set(lines.map((line) => String(line.base_uom || "").trim()).filter(Boolean))];
    const numeric = lines.every((line) => line.qty != null && line.qty !== "" && Number.isFinite(Number(line.qty)));
    if (units.length === 1 && numeric) {
      const sum = lines.reduce((total, line) => total + Number(line.qty), 0);
      qty = `${fmtQty(sum)} ${units[0]}`;
    }
  }
  return {
    date: row?.effective_date || "—",
    barang,
    qty,
    nilai: row?.total != null && row.total !== "" ? formatRupiah(row.total) : "—",
    status: POSTING_LABELS[row?.status] || row?.status || "—",
  };
}

function stockLabel(item) {
  if (!item || item.stock_fisik == null || item.hpp_reason === "STOCK_STATE_UNAVAILABLE") {
    return "Tidak tersedia";
  }
  return fmtQty(item.stock_fisik);
}

function exactMasterItemId(item) {
  if (!item || item.id == null || String(item.id).trim() === "") return "";
  return String(item.id).trim();
}

function issueStatus(issue) {
  return String(issue?.financial_status || issue?.status || "").toLowerCase();
}

function issueGroup(issue) {
  const status = issueStatus(issue);
  if (status === "posted") return "posted";
  if (status === "excluded") return "excluded";
  return "process";
}

function issueStatusLabel(issue) {
  const status = issueStatus(issue);
  if (status === "posted") return "Sudah diposting";
  if (status === "excluded") return "Tidak masuk periode";
  if (status === "blocked") return "Perlu diselesaikan";
  return "Perlu diproses";
}

function issueItemName(issue) {
  const item = issue?.item;
  if (item && typeof item === "object") return item.item_name || item.name || item.item_code || "";
  return issue?.item_name || "";
}

function bookStatusLabel(status) {
  if (!status) return "Belum lengkap";
  return BOOK_STATUS_LABELS[status] || status;
}

function bookDifference(book) {
  if (!book || book.financial_remaining == null || book.inventory_gl_balance == null) return null;
  const remaining = Number(book.financial_remaining);
  const gl = Number(book.inventory_gl_balance);
  if (!Number.isFinite(remaining) || !Number.isFinite(gl)) return null;
  return remaining - gl;
}

function openingPayload(form, requestId) {
  const payload = {
    client_request_id: requestId,
    effective_date: form.effective_date,
    lines: [{
      master_item_id: form.master_item_id.trim(),
      qty: form.qty,
      unit_cost: form.unit_cost,
      base_uom: form.base_uom.trim(),
      source: "APPROVED_OPENING",
    }],
  };
  if (form.snapshot_id) payload.snapshot_id = form.snapshot_id;
  return payload;
}

function detailMessage(exception, fallback) {
  const detail = exception?.response?.data?.detail;
  return typeof detail === "object" ? (detail.message || detail.code) : (detail || fallback);
}

export default function NilaiPersediaan() {
  const { hasPerm } = useAuth();
  const canExport = hasPerm("inventory_valuation.export");
  const canViewOpening = hasPerm("inventory_valuation.view");
  const canPostOpening = hasPerm("inventory_valuation.manage_opening");
  const canViewIssues = hasPerm("inventory_issue.view");
  const canPostIssues = hasPerm("inventory_issue.post");
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = inventoryValueTabFromSearch(searchParams.toString());
  const focusId = inventoryValueMasterItemIdFromSearch(searchParams.toString());
  const [focus, setFocus] = useState(null);
  const [focusLoading, setFocusLoading] = useState(false);
  const [focusError, setFocusError] = useState("");

  const selectTab = (nextTab) => {
    const next = new URLSearchParams(searchParams);
    if (nextTab === "daftar") next.delete("tab");
    else next.set("tab", nextTab);
    setSearchParams(next, { replace: true });
  };
  const [openingForm, setOpeningForm] = useState({
    effective_date: "",
    master_item_id: "",
    qty: "",
    unit_cost: "",
    base_uom: "PCS",
    snapshot_id: "",
  });
  const [selectedItem, setSelectedItem] = useState(null);
  const [capturedLine, setCapturedLine] = useState(null);
  const [formDirty, setFormDirty] = useState(false);
  const [requestId, setRequestId] = useState("");
  const [openingNotice, setOpeningNotice] = useState("");
  const [postingOpening, setPostingOpening] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState(null);
  const [book, setBook] = useState(null);
  const [bookError, setBookError] = useState("");
  const [openings, setOpenings] = useState([]);
  const [recovering, setRecovering] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [issues, setIssues] = useState([]);
  const [issuesError, setIssuesError] = useState("");
  const [postingIssueId, setPostingIssueId] = useState("");
  const [issueNotice, setIssueNotice] = useState("");
  const [reportStart, setReportStart] = useState("2026-10-01");
  const [reportEnd, setReportEnd] = useState("2026-10-31");
  const [purchaseReport, setPurchaseReport] = useState(null);
  const [salesReport, setSalesReport] = useState(null);
  const [loadingReports, setLoadingReports] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [valuationError, setValuationError] = useState("");
  const [error, setError] = useState("");
  const [drawer, setDrawer] = useState(null);
  const {
    input: qInput,
    setInput: setQInput,
    q,
    flush: doSearch,
    clear: clearQuery,
    pending: searchPending,
  } = useDebouncedLiveSearch();
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortBy, setSortBy] = useState("name");
  const [sortDir, setSortDir] = useState("asc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [exporting, setExporting] = useState(false);
  const loadSeq = useRef(0);
  const actionGuard = useRef(false);

  useEffect(() => { setPage(1); }, [q]);

  useEffect(() => {
    if (!searchPending) return;
    setLoading(true);
    setData((previous) => (previous ? { ...previous, items: [] } : previous));
  }, [searchPending]);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError("");
    setValuationError("");
    setData((previous) => (previous ? { ...previous, items: [] } : previous));
    try {
      const response = await api.inventoryValuation({
        q,
        status_filter: statusFilter,
        sort_by: sortBy,
        sort_dir: sortDir,
        page,
        page_size: pageSize,
      });
      if (seq !== loadSeq.current) return;
      setData(response);
    } catch (exception) {
      if (seq !== loadSeq.current) return;
      const detail = exception.response?.data?.detail;
      const message = typeof detail === "object" ? detail.message : (detail || "Gagal memuat Nilai Persediaan");
      setValuationError(message);
      setError(message);
      setData(null);
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [q, statusFilter, sortBy, sortDir, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!focusId) {
      setFocus(null);
      setFocusError("");
      setFocusLoading(false);
      return undefined;
    }
    let cancelled = false;
    setFocusLoading(true);
    setFocusError("");
    api.inventoryValuationItem(focusId)
      .then((payload) => {
        if (!cancelled) setFocus(acceptValuationFocus(payload, focusId));
      })
      .catch((exception) => {
        if (cancelled) return;
        setFocus(null);
        setFocusError(detailMessage(exception, "Item nilai persediaan gagal dimuat"));
      })
      .finally(() => {
        if (!cancelled) setFocusLoading(false);
      });
    return () => { cancelled = true; };
  }, [focusId]);

  const loadBook = useCallback(async () => {
    if (!canViewOpening && !canPostOpening) return;
    try {
      const [recon, listed] = await Promise.all([
        api.financialBookReconciliation(),
        api.financialInventoryOpenings(),
      ]);
      setBook(recon);
      setBookError("");
      setOpenings(listed.items || []);
    } catch (exception) {
      setBook(null);
      setOpenings([]);
      setBookError(detailMessage(exception, "Rekonsiliasi gagal dimuat"));
    }
  }, [canViewOpening, canPostOpening]);

  useEffect(() => { loadBook(); }, [loadBook]);

  useEffect(() => {
    if (!formDirty) return undefined;
    const onBeforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [formDirty]);

  const loadIssues = useCallback(async () => {
    if (!canViewIssues) return;
    try {
      const response = await api.inventoryIssues();
      setIssues(response.items || []);
      setIssuesError("");
    } catch (exception) {
      setIssues([]);
      setIssuesError(detailMessage(exception, "Antrean HPP gagal dimuat"));
    }
  }, [canViewIssues]);

  useEffect(() => { loadIssues(); }, [loadIssues]);

  const cycleSort = (column) => {
    if (sortBy === column) setSortDir((direction) => (direction === "asc" ? "desc" : "asc"));
    else {
      setSortBy(column);
      setSortDir(column === "name" ? "asc" : "desc");
    }
    setPage(1);
  };

  const download = async () => {
    setExporting(true);
    setError("");
    try {
      const blob = await api.inventoryValuationExport({
        q,
        status_filter: statusFilter,
        sort_by: sortBy,
        sort_dir: sortDir,
      });
      const url = URL.createObjectURL(new Blob([blob], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `nilai-persediaan-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (exception) {
      const detail = exception.response?.data?.detail;
      setError(typeof detail === "object" ? detail.message : (detail || "Gagal export Nilai Persediaan"));
    } finally {
      setExporting(false);
    }
  };

  const updateOpening = (patch, itemChanged = false) => {
    setFormDirty(true);
    setPreview(null);
    if (itemChanged) setCapturedLine(null);
    setOpeningForm((current) => ({
      ...current,
      ...patch,
      ...(itemChanged ? { snapshot_id: "" } : {}),
    }));
  };

  const pickOpeningItem = (item) => {
    const nextId = exactMasterItemId(item);
    setSelectedItem(nextId ? item : null);
    updateOpening({
      master_item_id: nextId,
      ...(item?.unit ? { base_uom: String(item.unit) } : {}),
    }, nextId !== openingForm.master_item_id);
  };

  const ensureRequestId = () => {
    const nextRequestId = requestId || (window.crypto?.randomUUID?.() || `fio-${Date.now()}`);
    if (!requestId) setRequestId(nextRequestId);
    return nextRequestId;
  };

  const runExclusive = async (work) => {
    if (actionGuard.current) return;
    actionGuard.current = true;
    try {
      await work();
    } finally {
      actionGuard.current = false;
    }
  };

  const previewOpening = () => runExclusive(async () => {
    const nextRequestId = ensureRequestId();
    setPreviewing(true);
    setOpeningNotice("");
    setError("");
    try {
      const plan = await api.previewFinancialInventoryOpening(openingPayload(openingForm, nextRequestId));
      setPreview(plan);
      const blocker = (plan.blockers || [])[0];
      setOpeningNotice(plan.can_post
        ? `Periksa saldo awal siap. Total ${formatRupiah(plan.total)}. Sumber APPROVED_OPENING. Tidak ada jurnal yang ditulis.`
        : (blocker?.message || blocker?.code || "Pemeriksaan menemukan hambatan."));
    } catch (exception) {
      setPreview(null);
      const message = detailMessage(exception, "Pemeriksaan saldo awal gagal");
      setError(message);
      setOpeningNotice(message);
    } finally {
      setPreviewing(false);
    }
  });

  const postOpening = (event) => {
    event.preventDefault();
    if (!preview || preview.can_post === false) return;
    return runExclusive(async () => {
      const nextRequestId = ensureRequestId();
      setPostingOpening(true);
      setOpeningNotice("");
      setError("");
      try {
        const saved = await api.postFinancialInventoryOpening(openingPayload(openingForm, nextRequestId));
        const item = saved.item || saved;
        if (item.status === "POSTED") setFormDirty(false);
        setOpeningNotice(
          item.status === "POSTED"
            ? `Opening posted. Jurnal ${item.journal_number || item.posted_journal_id || ""}. Asal ${item.journal_origin || "jurnal"}. Sumber APPROVED_OPENING, bukan HPP Published.`
            : (item.blocker_message || item.status || "Opening tidak selesai")
        );
        await load();
        await loadBook();
      } catch (exception) {
        const message = detailMessage(exception, "Posting saldo awal gagal");
        setError(message);
        setOpeningNotice(message);
      } finally {
        setPostingOpening(false);
      }
    });
  };

  const captureSnapshot = () => runExclusive(async () => {
    const nextRequestId = ensureRequestId();
    setCapturing(true);
    setOpeningNotice("");
    setError("");
    try {
      const saved = await api.captureFinancialOpeningSnapshot({
        client_request_id: `${nextRequestId}-snapshot`,
        lines: [{
          master_item_id: openingForm.master_item_id.trim(),
          base_uom: openingForm.base_uom.trim(),
        }],
      });
      const item = saved.item || saved;
      const line = (item.lines || [])[0] || null;
      setPreview(null);
      setCapturedLine(line ? { ...line, captured_at: item.captured_at || item.boundary_at || "" } : null);
      setOpeningForm((current) => ({
        ...current,
        snapshot_id: item.id || "",
        effective_date: item.business_date || current.effective_date,
      }));
      setFormDirty(true);
      setOpeningNotice(
        item.status === "CAPTURED"
          ? `Snapshot saldo sekarang tersimpan. Qty snapshot ${line ? line.qty : "—"}. Tanggal mengikuti waktu capture dan tidak dimundurkan. Nilai tidak diisi dari Published Pricing.`
          : (item.blocker_message || "Snapshot tidak tersimpan")
      );
    } catch (exception) {
      setOpeningNotice(detailMessage(exception, "Snapshot gagal"));
      setError(detailMessage(exception, "Snapshot gagal"));
    } finally {
      setCapturing(false);
    }
  });

  const postIssue = (movementId) => runExclusive(async () => {
    setPostingIssueId(movementId);
    setIssueNotice("");
    setError("");
    try {
      const saved = await api.postInventoryIssue(movementId);
      const item = saved.item || saved;
      if (item.financial_status === "posted" || item.status === "posted") {
        setIssueNotice(`HPP ${movementId} posted. Jurnal ${item.accounting_journal_id || ""}.`);
      } else {
        setIssueNotice(item.blocker_message || "Posting HPP tidak selesai");
      }
      await loadIssues();
      await loadBook();
    } catch (exception) {
      const message = detailMessage(exception, "Posting HPP gagal");
      setIssueNotice(message);
      setError(message);
    } finally {
      setPostingIssueId("");
    }
  });

  const loadReports = async () => {
    setLoadingReports(true);
    setError("");
    try {
      const params = { start_date: reportStart, end_date: reportEnd, page: 1, page_size: 50 };
      const [purchases, sales] = await Promise.all([
        api.purchaseActivityReport(params),
        api.salesActivityReport(params),
      ]);
      setPurchaseReport(purchases);
      setSalesReport(sales);
    } catch (exception) {
      setError(detailMessage(exception, "Laporan gagal dimuat"));
    } finally {
      setLoadingReports(false);
    }
  };

  const recoverOpening = (clientRequestId) => runExclusive(async () => {
    setRecovering(true);
    setError("");
    try {
      const saved = await api.recoverFinancialInventoryOpening({ client_request_id: clientRequestId });
      const item = saved.item || saved;
      setOpeningNotice(
        item.status === "POSTED"
          ? `Opening dipulihkan. Jurnal ${item.journal_number || item.posted_journal_id || ""}.`
          : (item.blocker_message || item.status || "Pemulihan tidak selesai")
      );
      await loadBook();
    } catch (exception) {
      setError(detailMessage(exception, "Pemulihan opening gagal"));
    } finally {
      setRecovering(false);
    }
  });

  const items = data?.items || [];
  const summary = data?.summary;
  const pageCount = data?.page_count || 1;
  const tableBusy = searchPending || loading;
  const tableView = liveSearchTableView({
    pending: searchPending,
    loading,
    itemCount: items.length,
  });
  const estimate = estimateCoverage({
    summary,
    total: data?.total,
    loading: loading || searchPending,
    failed: Boolean(valuationError),
    filterActive: Boolean(String(q || "").trim()) || statusFilter !== "all",
  });
  const summaryCount = (key) => {
    if (estimate.state === "loading") return "Memuat…";
    if (estimate.state === "failed") return "Gagal dimuat";
    if (estimate.state === "empty") return "0";
    if (estimate.state === "unknown" || !summary || summary[key] == null) return "—";
    return String(summary[key]);
  };
  const physicalRow = items.find((row) => row.master_item_id === openingForm.master_item_id);
  const issueGroups = {
    process: issues.filter((issue) => issueGroup(issue) === "process"),
    posted: issues.filter((issue) => issueGroup(issue) === "posted"),
    excluded: issues.filter((issue) => issueGroup(issue) === "excluded"),
  };

  return (
    <div className="prod-page master-items-page stock-page inventory-value-page" data-testid="inventory-value-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1 data-testid="inventory-value-title">Nilai Persediaan</h1>
          <p className="subhead" data-testid="inventory-value-formula">
            Nilai estimasi stok, bukan saldo buku posted.
          </p>
        </div>
      </div>

      <div className="inventory-value-tabs" role="tablist" aria-label="Nilai Persediaan" data-testid="inventory-value-tabs">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            role="tab"
            aria-selected={tab === entry.id}
            className={tab === entry.id ? "is-active" : ""}
            data-testid={`inventory-tab-${entry.id}`}
            onClick={() => selectTab(entry.id)}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {focusId && (
        <section className="inventory-value-panel inventory-value-focus" data-testid="inventory-value-focus" data-master-item-id={focusId}>
          <p data-testid="inventory-value-focus-id">ID master {focusId}</p>
          {focus?.item ? (
            <p data-testid="inventory-value-focus-match">{focus.item.item_code || "—"} · {focus.item.item_name || "—"}</p>
          ) : null}
          <InventoryValueMeaning
            physicalText={focus?.item ? stockLabel(focus.item) : "Tidak tersedia"}
            item={focus?.item}
            covered={Boolean(focus?.covered)}
            loading={focusLoading}
            reason={focusError || (!focus?.covered ? focus?.reason : "")}
            testIdPrefix="inventory-focus"
            reconTo={inventoryValueReconPath(focusId)}
          />
        </section>
      )}

      {error && (
        <div className="signal-strip" data-testid="inventory-value-error" role="alert">
          <div><AlertCircle size={16} /><span>{error}</span></div>
        </div>
      )}

      {tab === "daftar" && (
        <section className="inventory-value-panel" data-testid="inventory-panel-daftar">
          <div className="inventory-value-summary is-estimate" data-testid="inventory-value-summary">
            <div className="inventory-value-summary-card is-total">
              <span data-testid="inventory-estimate-label">{estimate.label}</span>
              <strong data-testid="inventory-estimate-total">{estimate.value}</strong>
            </div>
            <div className="inventory-value-summary-card">
              <span>Barang tanpa harga pokok</span>
              <strong data-testid="inventory-estimate-missing">{summaryCount("items_without_hpp")}</strong>
            </div>
            <div className="inventory-value-summary-card is-problem">
              <span>Barang perlu diperiksa</span>
              <strong data-testid="inventory-estimate-review">{summaryCount("problem_items")}</strong>
            </div>
          </div>
          <p className="muted small" data-testid="inventory-value-summary-scope">
            {estimate.note}
          </p>

          <div className="prod-filters" data-testid="inventory-value-filters">
            <div className="prod-filter-row">
              <form className="search-inline" onSubmit={doSearch}>
                <Search size={14} />
                <input
                  type="text"
                  value={qInput}
                  onChange={(event) => setQInput(event.target.value)}
                  placeholder="Cari kode / nama"
                  data-testid="inventory-value-search"
                  aria-label="Cari nilai persediaan"
                />
                {q && (
                  <button
                    type="button"
                    className="ghost sm"
                    onClick={() => { clearQuery(); setPage(1); }}
                    aria-label="Bersihkan"
                  >
                    <X size={12} />
                  </button>
                )}
              </form>
              <select
                value={statusFilter}
                onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }}
                data-testid="inventory-value-status-filter"
              >
                <option value="all">Semua</option>
                <option value="ok">Ada harga pokok</option>
                <option value="missing">Belum Ada HPP</option>
                <option value="problem">Perlu diperiksa</option>
              </select>
              <select
                value={pageSize}
                onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}
                data-testid="inventory-value-page-size"
              >
                <option value={25}>25 / halaman</option>
                <option value={50}>50 / halaman</option>
                <option value={100}>100 / halaman</option>
              </select>
              {canExport && (
                <button
                  className="primary"
                  type="button"
                  onClick={download}
                  disabled={exporting}
                  data-testid="inventory-value-export"
                >
                  <Download size={14} /> {exporting ? "Menyiapkan…" : "Export Excel"}
                </button>
              )}
            </div>
          </div>

          <div className="prod-table-wrap master-table-wrap">
            <table className="prod-table master-items-table" data-testid="inventory-value-table">
              <thead>
                <tr>
                  <th className="col-fit" style={CENTER_CELL}>
                    <button type="button" className="prod-th-label is-sort" style={CENTER_SORT} onClick={() => cycleSort("code")}>Kode</button>
                  </th>
                  <th className="col-text">
                    <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("name")}>Nama Barang</button>
                  </th>
                  <th className="col-fit" style={CENTER_CELL}>Satuan</th>
                  <th className="col-fit num" style={CENTER_CELL}>
                    <button type="button" className="prod-th-label is-sort" style={CENTER_SORT} onClick={() => cycleSort("stock_fisik")}>Stok Fisik</button>
                  </th>
                  <th className="col-fit num" style={CENTER_CELL}>
                    <button type="button" className="prod-th-label is-sort" style={CENTER_SORT} onClick={() => cycleSort("current_hpp")}>Harga Pokok</button>
                  </th>
                  <th className="col-fit num" style={CENTER_CELL}>
                    <button type="button" className="prod-th-label is-sort" style={CENTER_SORT} onClick={() => cycleSort("inventory_value")}>Nilai Estimasi</button>
                  </th>
                  <th className="col-fit" style={CENTER_CELL}>
                    <button type="button" className="prod-th-label is-sort" style={CENTER_SORT} onClick={() => cycleSort("hpp_status")}>Status</button>
                  </th>
                  <th className="col-fit" style={CENTER_CELL}>Detail</th>
                </tr>
              </thead>
              <tbody data-testid="inventory-value-tbody" data-search-state={tableView}>
                {tableView === "loading" && (
                  <tr data-testid="inventory-value-loading">
                    <td colSpan={8} className="muted small">Memuat…</td>
                  </tr>
                )}
                {tableView === "empty" && (
                  <tr>
                    <td colSpan={8} className="muted small" data-testid="inventory-value-empty">
                      Tidak ada item.
                    </td>
                  </tr>
                )}
                {tableView === "results" && items.map((item) => {
                  const key = item.accurate_item_id || item.master_item_id;
                  const hasHpp = item.current_hpp != null;
                  const hasValue = item.inventory_value != null;
                  return (
                    <tr
                      key={key}
                      data-testid={`inventory-value-row-${key}`}
                      data-master-item-id={item.master_item_id || ""}
                      className={[
                        item.hpp_status === "QTY_MISMATCH" ? "stock-neg" : "",
                        item.master_item_id && item.master_item_id === focusId ? "is-focused" : "",
                      ].filter(Boolean).join(" ")}
                    >
                      <td className="col-fit mono" style={CENTER_CELL}>{item.item_code || "—"}</td>
                      <td className="col-text">{item.item_name || "—"}</td>
                      <td className="col-fit" style={CENTER_CELL}>{item.unit || "—"}</td>
                      <td className="col-fit num mono" style={CENTER_CELL} data-testid={`inventory-value-stock-${key}`}>
                        {stockLabel(item)}
                      </td>
                      <td className="col-fit num mono" style={CENTER_CELL} data-testid={`inventory-value-hpp-${key}`}>
                        {hasHpp ? formatRupiah(item.current_hpp) : "Belum Ada HPP"}
                      </td>
                      <td className="col-fit num mono" style={CENTER_CELL} data-testid={`inventory-value-amount-${key}`}>
                        {hasValue ? formatRupiah(item.inventory_value) : "—"}
                      </td>
                      <td className="col-fit" style={CENTER_CELL}>
                        <span
                          className={`inventory-value-status is-${String(item.hpp_status || "missing").toLowerCase()}`}
                          title={item.hpp_reason || ""}
                        >
                          {statusLabel(item.hpp_status)}
                        </span>
                      </td>
                      <td className="col-fit" style={CENTER_CELL}>
                        <button
                          type="button"
                          className="ghost sm"
                          data-testid={`inventory-value-detail-${key}`}
                          onClick={() => setDrawer({ kind: "item", item })}
                        >
                          Detail
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {pageCount > 1 && (
            <div className="master-pager" data-testid="inventory-value-pager">
              <button
                className="ghost sm"
                type="button"
                disabled={page <= 1 || tableBusy}
                onClick={() => setPage((current) => current - 1)}
              >
                Sebelumnya
              </button>
              <span className="muted small">
                Halaman {data?.page || page} dari {pageCount} · {data?.total || 0} item
              </span>
              <button
                className="ghost sm"
                type="button"
                disabled={page >= pageCount || tableBusy}
                onClick={() => setPage((current) => current + 1)}
              >
                Berikutnya
              </button>
            </div>
          )}
        </section>
      )}

      {tab === "opening" && (
        <section className="inventory-value-panel" data-testid="inventory-panel-opening">
          {(canViewOpening || canPostOpening) ? (
            <>
              <form
                className="prod-filters inventory-opening-form"
                onSubmit={(event) => {
                  if (!canPostOpening) {
                    event.preventDefault();
                    return;
                  }
                  postOpening(event);
                }}
                data-testid="financial-opening-form"
              >
                <div className="prod-filter-row inventory-opening-actions">
                  {canPostOpening && (
                    <button className="ghost" type="button" onClick={captureSnapshot} disabled={capturing || postingOpening} data-testid="financial-opening-capture">
                      {capturing ? "Mengambil…" : "Ambil snapshot saldo sekarang"}
                    </button>
                  )}
                  <button className="ghost" type="button" onClick={previewOpening} disabled={previewing} data-testid="financial-opening-preview-button">
                    {previewing ? "Memeriksa…" : "Periksa saldo awal"}
                  </button>
                  {canPostOpening && (
                    <button className="primary" type="submit" disabled={postingOpening || capturing || !preview || preview.can_post === false} data-testid="financial-opening-post">
                      {postingOpening ? "Memposting…" : "Posting saldo awal"}
                    </button>
                  )}
                </div>
                <div className="inventory-opening-fields">
                  <label>
                    Barang
                    <MasterItemSearchSelect
                      value={selectedItem}
                      onChange={pickOpeningItem}
                      testId="financial-opening-item-search"
                      placeholder="Cari kode / nama barang"
                    />
                  </label>
                  <p data-testid="financial-opening-selected">
                    {selectedItem
                      ? `${selectedItem.item_code || "—"} · ${selectedItem.item_name || selectedItem.name || "—"} · ${selectedItem.unit || openingForm.base_uom || "—"}`
                      : "Belum ada barang terpilih."}
                  </p>
                  <label>
                    Qty opening
                    <input
                      value={openingForm.qty}
                      onChange={(event) => updateOpening({ qty: event.target.value })}
                      placeholder="Qty"
                      aria-label="Qty opening"
                      inputMode="decimal"
                      data-testid="financial-opening-qty"
                    />
                  </label>
                  <label>
                    Harga pokok
                    <input
                      value={openingForm.unit_cost}
                      onChange={(event) => updateOpening({ unit_cost: event.target.value })}
                      placeholder="Harga pokok disetujui"
                      aria-label="Harga pokok opening"
                      inputMode="decimal"
                      data-testid="financial-opening-cost"
                    />
                  </label>
                  <label>
                    Satuan
                    <input
                      value={openingForm.base_uom}
                      onChange={(event) => updateOpening({ base_uom: event.target.value })}
                      placeholder="Satuan"
                      aria-label="Satuan opening"
                      data-testid="financial-opening-uom"
                    />
                  </label>
                  <label>
                    Tanggal saldo awal
                    <input
                      value={openingForm.effective_date}
                      onChange={(event) => updateOpening({ effective_date: event.target.value })}
                      readOnly={Boolean(openingForm.snapshot_id)}
                      placeholder="YYYY-MM-DD"
                      aria-label="Tanggal saldo awal"
                      data-testid="financial-opening-date"
                    />
                  </label>
                </div>
                <p className="muted small" data-testid="financial-opening-qty-compare">
                  Stok fisik saat ini: {physicalRow ? stockLabel(physicalRow) : "Tidak tersedia"}.
                  Qty snapshot: {capturedLine && capturedLine.qty != null ? fmtQty(capturedLine.qty) : "Tidak tersedia"}.
                  Qty opening: {openingForm.qty === "" ? "—" : openingForm.qty}.
                </p>
                <p className="muted small">
                  Snapshot memakai waktu capture yang sebenarnya. Tanggal tidak dimundurkan dari halaman ini. Harga tidak diisi dari Published Pricing.
                </p>
                {openingForm.snapshot_id && (
                  <p className="muted small" data-testid="financial-opening-snapshot">
                    Snapshot {openingForm.snapshot_id}.
                    {capturedLine?.captured_at ? ` Waktu capture ${capturedLine.captured_at}.` : " Waktu capture mengikuti snapshot yang tersimpan."}
                  </p>
                )}
                {formDirty && (
                  <p className="muted small" data-testid="financial-opening-dirty">Perubahan formulir belum diposting.</p>
                )}
                {openingNotice && (
                  <p className="muted small" data-testid="financial-opening-notice">{openingNotice}</p>
                )}
              </form>
              <div className="inventory-panel-scroll">
                {preview && (
                  <div className="inventory-opening-preview" data-testid="financial-opening-preview">
                    <p data-testid="financial-opening-boundary">
                      {preview.historical_reconstruction
                        ? "Jalur rekonstruksi historis. Bukan snapshot saldo sekarang."
                        : `Jalur snapshot saldo sekarang${preview.snapshot_business_date ? ` ${preview.snapshot_business_date}` : ""}. Tanggal efektif harus sama dengan tanggal snapshot.`}
                      {" "}Sumber {preview.book_value_source || "APPROVED_OPENING"}. Tanggal efektif {preview.effective_date} ({preview.timezone}).
                      Total {formatRupiah(preview.total)}. {preview.can_post ? "Siap diposting." : "Belum dapat diposting."}
                    </p>
                    {(preview.snapshots || []).map((row) => (
                      <p key={row.master_item_id}>
                        Qty snapshot {row.snapshot_qty == null ? "tidak tersedia" : fmtQty(row.snapshot_qty)} {row.base_uom}.
                        Event sebelum cutoff {row.events_before_cutoff}. Event setelah cutoff {row.events_after_cutoff}.
                      </p>
                    ))}
                    {(preview.blockers || []).map((row, index) => (
                      <p key={`${row.code}-${index}`} data-testid="financial-opening-blocker">
                        {row.code}: {row.message}
                        {row.boundary_kind === "historical_reconstruction" ? " Rekonstruksi historis." : ""}
                        {row.boundary_kind === "current_balance" ? " Snapshot saldo sekarang." : ""}
                        {row.receipt_id ? ` Penerimaan ${row.receipt_number || row.receipt_id}.` : ""}
                        {row.valid_snapshot_rule ? ` ${row.valid_snapshot_rule}` : ""}
                      </p>
                    ))}
                  </div>
                )}
                <div data-testid="financial-opening-list">
                  <h2>Riwayat saldo awal</h2>
                  {bookError && (
                    <p data-testid="financial-opening-history-error" role="alert">Gagal dimuat.</p>
                  )}
                  {!bookError && openings.length === 0 && (
                    <p data-testid="financial-opening-history-empty">Belum ada saldo awal.</p>
                  )}
                  {!bookError && openings.length > 0 && (
                    <div className="prod-table-wrap">
                      <table className="prod-table" data-testid="financial-opening-history">
                        <thead>
                          <tr>
                            <th>Tanggal</th>
                            <th>Barang</th>
                            <th>Qty</th>
                            <th>Nilai</th>
                            <th>Status</th>
                            <th>Detail</th>
                          </tr>
                        </thead>
                        <tbody>
                          {openings.map((row) => {
                            const cells = openingHistoryCells(row);
                            return (
                              <tr key={row.id} data-testid={`financial-opening-row-${row.id}`}>
                                <td>{cells.date}</td>
                                <td>{cells.barang}</td>
                                <td>{cells.qty}</td>
                                <td>{cells.nilai}</td>
                                <td>{cells.status}</td>
                                <td>
                                  <button
                                    type="button"
                                    className="ghost sm"
                                    data-testid={`financial-opening-detail-${row.id}`}
                                    onClick={() => setDrawer({ kind: "opening", opening: row })}
                                  >
                                    Detail
                                  </button>
                                  {canPostOpening && row.status === "NEEDS_RECOVERY" && (
                                    <button
                                      type="button"
                                      className="ghost sm"
                                      disabled={recovering}
                                      data-testid="financial-opening-recover"
                                      onClick={() => recoverOpening(row.client_request_id)}
                                    >
                                      Pulihkan
                                    </button>
                                  )}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <p className="muted small">Anda tidak memiliki akses saldo awal.</p>
          )}
        </section>
      )}

      {tab === "hpp" && (
        <section className="inventory-value-panel" data-testid="inventory-panel-hpp">
          {canViewIssues ? (
            <div className="inventory-panel-scroll" data-testid="cogs-queue">
              {issuesError && <p className="muted small" data-testid="cogs-queue-error">{issuesError}</p>}
              {issueNotice && <p className="muted small" data-testid="cogs-queue-notice">{issueNotice}</p>}
              {[
                ["process", "Perlu diproses"],
                ["posted", "Sudah diposting"],
                ["excluded", "Tidak masuk periode"],
              ].map(([group, label]) => (
                <div key={group} data-testid={`cogs-group-${group}`}>
                  <h2>{label}</h2>
                  <div className="prod-table-wrap">
                    <table className="prod-table" data-testid={`cogs-table-${group}`}>
                      <thead>
                        <tr>
                          <th>Nomor dokumen</th>
                          <th>Tanggal</th>
                          <th>Barang</th>
                          <th>Qty</th>
                          <th>Nilai HPP</th>
                          <th>Status</th>
                          <th>Tindakan</th>
                        </tr>
                      </thead>
                      <tbody>
                        {issueGroups[group].length === 0 && (
                          <tr><td colSpan={7} className="muted small">Tidak ada.</td></tr>
                        )}
                        {issueGroups[group].map((issue) => (
                          <tr key={issue.movement_id} data-testid={`cogs-row-${issue.movement_id}`}>
                            <td className="mono">{issue.reference_number || issue.movement_id}</td>
                            <td>{issue.journal_date || "—"}</td>
                            <td>{issueItemName(issue) || "—"}</td>
                            <td>{issue.base_qty} {issue.base_uom}</td>
                            <td>{issue.computed_cogs != null ? formatRupiah(issue.computed_cogs) : "—"}</td>
                            <td>{issueStatusLabel(issue)}</td>
                            <td>
                              <button
                                type="button"
                                className="ghost sm"
                                data-testid={`cogs-detail-${issue.movement_id}`}
                                onClick={() => setDrawer({ kind: "issue", issue })}
                              >
                                Detail
                              </button>
                              {group !== "excluded" && canPostIssues && issue.can_post && (
                                <button
                                  type="button"
                                  className="primary"
                                  disabled={postingIssueId === issue.movement_id}
                                  data-testid={`cogs-post-${issue.movement_id}`}
                                  onClick={() => postIssue(issue.movement_id)}
                                >
                                  {postingIssueId === issue.movement_id ? "Memposting…" : "Posting HPP"}
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted small">Anda tidak memiliki akses HPP penjualan.</p>
          )}
        </section>
      )}

      {tab === "recon" && (
        <section className="inventory-value-panel" data-testid="inventory-panel-recon">
          {(canViewOpening || canPostOpening) ? (
            <div className="inventory-panel-scroll">
              {bookError && (
                <p data-testid="financial-book-error" role="alert">{bookError}. Gagal dimuat.</p>
              )}
              {book && !bookError && (
                <div data-testid="financial-book-reconciliation">
                  <p data-testid="financial-book-identity">
                    Saldo awal diposting + Penerimaan diposting − HPP diposting = Sisa nilai buku.
                  </p>
                  <div className="inventory-value-summary inventory-book-recon">
                    <div className="inventory-value-summary-card">
                      <span>Saldo awal diposting</span>
                      <strong>{moneyOrGap(book.opening_value)}</strong>
                    </div>
                    <div className="inventory-value-summary-card">
                      <span>Penerimaan diposting</span>
                      <strong>{moneyOrGap(book.goods_receipt_value)}</strong>
                    </div>
                    <div className="inventory-value-summary-card">
                      <span>HPP diposting</span>
                      <strong>{moneyOrGap(book.cogs_value)}</strong>
                    </div>
                    <div className="inventory-value-summary-card is-total">
                      <span>Sisa nilai buku</span>
                      <strong data-testid="financial-book-remaining">{moneyOrGap(book.financial_remaining)}</strong>
                    </div>
                    <div className="inventory-value-summary-card">
                      <span>Saldo GL</span>
                      <strong data-testid="financial-book-gl">{moneyOrGap(book.inventory_gl_balance)}</strong>
                    </div>
                    <div className="inventory-value-summary-card">
                      <span>Selisih</span>
                      <strong data-testid="financial-book-difference">{moneyOrGap(bookDifference(book))}</strong>
                    </div>
                    <div className="inventory-value-summary-card">
                      <span>Kelengkapan</span>
                      <strong data-testid="financial-book-status">{bookStatusLabel(book.reconciliation_status)}</strong>
                    </div>
                  </div>
                  <p className="muted small">Nilai estimasi stok ada di Daftar Barang dan bukan saldo buku.</p>
                  <details data-testid="financial-book-detail">
                    <summary>Detail rekonsiliasi</summary>
                    <p data-testid="financial-book-status-code">Kode {book.reconciliation_status || "—"}.</p>
                    <p>
                      Qty finansial {book.financial_qty_remaining == null ? "belum terbentuk" : fmtQty(book.financial_qty_remaining)} dari saldo awal {book.opening_qty == null ? "—" : fmtQty(book.opening_qty)} + penerimaan {book.goods_receipt_qty == null ? "—" : fmtQty(book.goods_receipt_qty)} − HPP {book.cogs_qty == null ? "—" : fmtQty(book.cogs_qty)}.
                    </p>
                    <p>GR historis yang masih aktif: {moneyOrGap(book.historical_gr_overlap)}. Sumber GL lain: {moneyOrGap(book.other_gl_total)}.</p>
                    {book.pending_cogs_count ? <p>Posting HPP yang masih menunggu: {book.pending_cogs_count}.</p> : null}
                    {book.physical_vs_financial && (
                      <p data-testid="financial-book-physical">{book.physical_vs_financial}</p>
                    )}
                    {(book.blockers || []).map((row, index) => (
                      <p key={`${row.code || "blocker"}-${index}`} data-testid="financial-book-blocker">
                        {row.code}: {row.message}
                      </p>
                    ))}
                    {(book.blocked_stock_receipts || []).map((row) => (
                      <p key={row.receipt_id || row.receipt_number} data-testid="financial-book-blocked-receipt">
                        {row.code}: {row.receipt_number || row.receipt_id}. {row.message}
                      </p>
                    ))}
                    {book.item_hpp_scope && (
                      <p data-testid="financial-book-hpp-scope">{book.item_hpp_scope}</p>
                    )}
                  </details>
                </div>
              )}

              {canViewOpening && (
                <section data-testid="activity-reports" className="inventory-activity-reports">
                  <h2>Laporan pembelian dan penjualan</h2>
                  <div className="prod-filter-row">
                    <input value={reportStart} onChange={(event) => setReportStart(event.target.value)} aria-label="Tanggal mulai laporan" data-testid="activity-report-start" />
                    <input value={reportEnd} onChange={(event) => setReportEnd(event.target.value)} aria-label="Tanggal akhir laporan" data-testid="activity-report-end" />
                    <button type="button" className="ghost" onClick={loadReports} disabled={loadingReports} data-testid="activity-report-load">
                      {loadingReports ? "Memuat…" : "Muat laporan"}
                    </button>
                    <a href="/buku-besar/laba-rugi">Laba/Rugi dari jurnal posted</a>
                  </div>
                  {purchaseReport && (
                    <p data-testid="purchase-report-total">Pembelian {purchaseReport.filtered_count} baris, nilai {formatRupiah(purchaseReport.total_amount || 0)}. Halaman ini {purchaseReport.items?.length || 0} baris.</p>
                  )}
                  {salesReport && (
                    <p data-testid="sales-report-total">Penjualan {salesReport.filtered_count} baris, nilai {formatRupiah(salesReport.total_amount || 0)}.</p>
                  )}
                  <div className="prod-table-wrap">
                    <table className="prod-table" data-testid="purchase-report-table">
                      <thead>
                        <tr><th>Supplier</th><th>Dokumen</th><th>Tanggal</th><th>Qty</th><th>Nilai</th><th>Penerimaan</th><th>Faktur</th><th>Bayar</th><th>Posting</th></tr>
                      </thead>
                      <tbody>
                        {(purchaseReport?.items || []).map((row) => (
                          <tr key={`${row.document_id}-${row.master_item_id}`}>
                            <td>{row.supplier || "—"}</td>
                            <td>{row.document_number}</td>
                            <td>{row.date}</td>
                            <td>{row.qty} {row.uom}</td>
                            <td>{row.amount != null ? formatRupiah(row.amount) : "—"}</td>
                            <td>{row.receipt_status}</td>
                            <td>{(row.invoice_numbers || []).join(", ") || "—"}</td>
                            <td>{(row.payment_numbers || []).join(", ") || "—"}</td>
                            <td>{row.stock_posting_status}{row.journal_id ? <a href="/buku-besar/jurnal-umum"> {row.journal_number || row.journal_id}</a> : ""}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="prod-table-wrap">
                    <table className="prod-table" data-testid="sales-report-table">
                      <thead>
                        <tr><th>Customer</th><th>Dokumen</th><th>Tanggal</th><th>Qty</th><th>Nilai</th><th>Pengiriman</th><th>Faktur</th><th>Terima</th><th>Posting</th></tr>
                      </thead>
                      <tbody>
                        {(salesReport?.items || []).map((row) => (
                          <tr key={`${row.document_id}-${row.item_name}`}>
                            <td>{row.customer || "—"}</td>
                            <td>{row.document_number}</td>
                            <td>{row.date}</td>
                            <td>{row.qty}</td>
                            <td>{row.amount != null ? formatRupiah(row.amount) : "—"}</td>
                            <td>{row.delivery_status || "—"}</td>
                            <td>{row.invoice_status || "—"}</td>
                            <td>{(row.receipt_numbers || []).join(", ") || "—"}</td>
                            <td>{row.posting_status}{row.journal_id ? <a href="/buku-besar/jurnal-umum"> {row.journal_number || row.journal_id}</a> : ""}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
            </div>
          ) : (
            <p className="muted small">Anda tidak memiliki akses rekonsiliasi.</p>
          )}
        </section>
      )}

      {drawer?.kind === "opening" && (
        <aside className="inventory-detail-drawer" data-testid="financial-opening-drawer" role="dialog" aria-label="Detail saldo awal">
          <div className="inventory-detail-drawer-head">
            <strong>Saldo awal {drawer.opening.effective_date || "—"}</strong>
            <button type="button" className="ghost sm" onClick={() => setDrawer(null)} data-testid="financial-opening-drawer-close">Tutup</button>
          </div>
          <div className="inventory-detail-drawer-body">
            <p data-testid="financial-opening-journal">
              Status {drawer.opening.status || "—"}. {POSTING_LABELS[drawer.opening.status] || ""}.
              Otoritas jurnal {drawer.opening.journal_authority || "belum diperiksa"}.
            </p>
            <p>ID {drawer.opening.id || "—"}.</p>
            <p>Nomor jurnal {drawer.opening.journal_number || drawer.opening.posted_journal_id || "—"}.</p>
            <p>
              Sumber jurnal {drawer.opening.journal_origin === "existing_opening_voucher" ? "voucher saldo awal yang sudah ada" : (drawer.opening.journal_origin || "—")}.
            </p>
            <p>Akun {drawer.opening.inventory_account_id || "—"}.</p>
            <p>Snapshot {drawer.opening.snapshot_id || "—"}.</p>
            <p>{drawer.opening.blocker_message || drawer.opening.reversal_reason || "Tidak ada blocker."}</p>
            {(drawer.opening.lines || []).map((line, index) => (
              <p key={line.line_id || line.master_item_id || index} data-testid="financial-opening-line">
                {openingLineIdentity(line) || "—"} · ID {line.master_item_id || "—"} · Qty {line.qty == null ? "—" : fmtQty(line.qty)} {line.base_uom || ""} · Nilai {line.total != null ? formatRupiah(line.total) : "—"}.
              </p>
            ))}
            {drawer.opening.status === "REVERSED" && (
              <p data-testid="financial-opening-reversed">Direversal. {drawer.opening.reversal_reason || drawer.opening.blocker_message || ""}</p>
            )}
          </div>
        </aside>
      )}

      {drawer?.kind === "item" && (
        <aside className="inventory-detail-drawer" data-testid="inventory-value-drawer" role="dialog" aria-label="Detail barang">
          <div className="inventory-detail-drawer-head">
            <strong>{drawer.item.item_code || "—"} · {drawer.item.item_name || "—"}</strong>
            <button type="button" className="ghost sm" onClick={() => setDrawer(null)} data-testid="inventory-value-drawer-close">Tutup</button>
          </div>
          <div className="inventory-detail-drawer-body">
            <p data-testid={`inventory-value-source-${drawer.item.accurate_item_id || drawer.item.master_item_id}`}>
              Sumber harga: {sourceLabel(drawer.item.hpp_source)}.
              {drawer.item.hpp_source === "PUBLISHED_CUSTOMER_PRICING_FALLBACK" ? " Valuasi Published (bukan saldo buku)." : ""}
              {drawer.item.financial_opening_source ? ` Buku ${sourceLabel(drawer.item.financial_opening_source)}${drawer.item.financial_opening_unit_cost != null ? ` ${formatRupiah(drawer.item.financial_opening_unit_cost)}` : ""}.` : ""}
            </p>
            <p data-testid={`inventory-value-posting-${drawer.item.accurate_item_id || drawer.item.master_item_id}`}>
              Status jurnal: {POSTING_LABELS[drawer.item.financial_posting_status] || drawer.item.financial_posting_status || "Belum diposting"}.
              {drawer.item.financial_journal_id ? ` Jurnal ${drawer.item.financial_journal_id}.` : ""}
            </p>
            <InventoryValueMeaning
              physicalText={stockLabel(drawer.item)}
              item={drawer.item}
              covered
              testIdPrefix="inventory-drawer"
              reconTo={inventoryValueReconPath(drawer.item.master_item_id)}
            />
            <p>Selisih: {drawer.item.reconciliation_difference == null && drawer.item.difference == null ? "Tidak tersedia" : fmtQty(drawer.item.reconciliation_difference ?? drawer.item.difference)}.</p>
            <p>Opening disetujui: {drawer.item.financial_opening_total != null ? formatRupiah(drawer.item.financial_opening_total) : "—"}.</p>
            <p>Terakhir update: {drawer.item.updated_at ? formatDate(drawer.item.updated_at) : "—"}.</p>
            <p data-testid="inventory-value-technical">
              {drawer.item.financial_blocker_message || drawer.item.hpp_reason || drawer.item.reconciliation_explanation || "Tidak ada pesan teknis."}
              {drawer.item.hpp_status ? ` Kode status ${drawer.item.hpp_status}.` : ""}
            </p>
          </div>
        </aside>
      )}

      {drawer?.kind === "issue" && (
        <aside className="inventory-detail-drawer" data-testid="cogs-detail-drawer" role="dialog" aria-label="Detail HPP">
          <div className="inventory-detail-drawer-head">
            <strong>{drawer.issue.reference_number || drawer.issue.movement_id}</strong>
            <button type="button" className="ghost sm" onClick={() => setDrawer(null)}>Tutup</button>
          </div>
          <div className="inventory-detail-drawer-body">
            <p>Mutasi {drawer.issue.movement_id}. Status {issueStatus(drawer.issue) || "—"}.</p>
            <p data-testid={`cogs-sources-${drawer.issue.movement_id}`}>
              Sumber acquisition: {(drawer.issue.acquisition_sources || []).map((source) => source.acquisition_key).join(", ") || "—"}.
            </p>
            <p>{drawer.issue.blocker_code ? `${drawer.issue.blocker_code}: ` : ""}{drawer.issue.blocker_message || "Tidak ada pesan teknis."}</p>
            <p>{drawer.issue.accounting_journal_id ? <a href="/buku-besar/jurnal-umum">{drawer.issue.accounting_journal_id}</a> : "Belum ada jurnal."}</p>
            {drawer.issue.hpp_method ? <p>Metode {drawer.issue.hpp_method}.</p> : null}
          </div>
        </aside>
      )}
    </div>
  );
}
