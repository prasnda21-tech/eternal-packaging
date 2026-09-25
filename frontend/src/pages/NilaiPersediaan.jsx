import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Download, Search, X } from "lucide-react";
import { fmtQty } from "../components/qtyUi";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { formatRupiah } from "../lib/purchaseUi";

const STATUS_LABELS = {
  OK: "OK",
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
const CENTER_CELL = { textAlign: "center", verticalAlign: "middle" };
const CENTER_SORT = { justifyContent: "center", width: "100%" };

function statusLabel(status, source) {
  if (status === "OK" && source === "PUBLISHED_CUSTOMER_PRICING_FALLBACK") {
    return "Valuasi Published (bukan saldo buku)";
  }
  if (status === "OK") return "HPP Rata-Rata / OK";
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

export default function NilaiPersediaan() {
  const { hasPerm } = useAuth();
  const canExport = hasPerm("inventory_valuation.export");
  const canViewOpening = hasPerm("inventory_valuation.view");
  const canPostOpening = hasPerm("inventory_valuation.manage_opening");
  const canViewIssues = hasPerm("inventory_issue.view");
  const canPostIssues = hasPerm("inventory_issue.post");
  const [openingForm, setOpeningForm] = useState({
    effective_date: "",
    master_item_id: "",
    qty: "",
    unit_cost: "",
    base_uom: "PCS",
    snapshot_id: "",
  });
  const [formDirty, setFormDirty] = useState(false);
  const [requestId, setRequestId] = useState("");
  const [openingNotice, setOpeningNotice] = useState("");
  const [postingOpening, setPostingOpening] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview] = useState(null);
  const [book, setBook] = useState(null);
  const [openings, setOpenings] = useState([]);
  const [recovering, setRecovering] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [issues, setIssues] = useState([]);
  const [postingIssueId, setPostingIssueId] = useState("");
  const [issueNotice, setIssueNotice] = useState("");
  const [reportStart, setReportStart] = useState("2026-10-01");
  const [reportEnd, setReportEnd] = useState("2026-10-31");
  const [purchaseReport, setPurchaseReport] = useState(null);
  const [salesReport, setSalesReport] = useState(null);
  const [loadingReports, setLoadingReports] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
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
      setError(typeof detail === "object" ? detail.message : (detail || "Gagal memuat Nilai Persediaan"));
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [q, statusFilter, sortBy, sortDir, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  const loadBook = useCallback(async () => {
    if (!canViewOpening && !canPostOpening) return;
    try {
      const [recon, listed] = await Promise.all([
        api.financialBookReconciliation(),
        api.financialInventoryOpenings(),
      ]);
      setBook(recon);
      setOpenings(listed.items || []);
    } catch {
      setBook(null);
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
    } catch (exception) {
      setIssueNotice(detailMessage(exception, "Antrean HPP gagal dimuat"));
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

  const updateOpening = (patch) => {
    setFormDirty(true);
    setOpeningForm((current) => ({ ...current, ...patch }));
  };

  const ensureRequestId = () => {
    const nextRequestId = requestId || (window.crypto?.randomUUID?.() || `fio-${Date.now()}`);
    if (!requestId) setRequestId(nextRequestId);
    return nextRequestId;
  };

  const detailMessage = (exception, fallback) => {
    const detail = exception.response?.data?.detail;
    return typeof detail === "object" ? (detail.message || detail.code) : (detail || fallback);
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
        ? `Preview siap diposting. Total ${formatRupiah(plan.total)}. Sumber APPROVED_OPENING. Tidak ada jurnal yang ditulis.`
        : (blocker?.message || blocker?.code || "Preview menemukan hambatan."));
    } catch (exception) {
      setPreview(null);
      const message = detailMessage(exception, "Preview opening gagal");
      setError(message);
      setOpeningNotice(message);
    } finally {
      setPreviewing(false);
    }
  });

  const postOpening = (event) => {
    event.preventDefault();
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
        const message = detailMessage(exception, "Posting opening gagal");
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
      setOpeningForm((current) => ({
        ...current,
        snapshot_id: item.id || "",
        effective_date: item.business_date || current.effective_date,
      }));
      setFormDirty(true);
      const line = (item.lines || [])[0];
      setOpeningNotice(
        item.status === "CAPTURED"
          ? `Snapshot saldo sekarang ${item.business_date} tersimpan. Qty ${line ? line.qty : "—"} ${line ? line.base_uom : ""}. Opening harus memakai tanggal ini. Snapshot tidak memundurkan cutoff lama, dan nilai buku tidak diisi dari Published Pricing.`
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
  const summary = data?.summary || {};
  const pageCount = data?.page_count || 1;
  const tableBusy = searchPending || loading;
  const tableView = liveSearchTableView({
    pending: searchPending,
    loading,
    itemCount: items.length,
  });

  return (
    <div className="prod-page master-items-page stock-page inventory-value-page" data-testid="inventory-value-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1 data-testid="inventory-value-title">Nilai Persediaan</h1>
          <p className="subhead" data-testid="inventory-value-formula">
            Nilai manajerial = Stock Fisik × HPP. HPP Published hanya valuasi, bukan saldo buku posted.
          </p>
        </div>
        <div className="stock-heading-actions">
          {canExport && (
            <button
              className="primary"
              onClick={download}
              disabled={exporting}
              data-testid="inventory-value-export"
            >
              <Download size={14} /> {exporting ? "Menyiapkan…" : "Export Excel"}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="signal-strip" data-testid="inventory-value-error" role="alert">
          <div><AlertCircle size={16} /><span>{error}</span></div>
        </div>
      )}

      <div className="inventory-value-summary" data-testid="inventory-value-summary">
        <div className="inventory-value-summary-card is-total">
          <span>Total Nilai Persediaan</span>
          <strong>{formatRupiah(summary.total_inventory_value || 0)}</strong>
        </div>
        <div className="inventory-value-summary-card">
          <span>Item Dinilai</span>
          <strong>{summary.valued_items || 0}</strong>
        </div>
        <div className="inventory-value-summary-card">
          <span>Item Belum Punya HPP</span>
          <strong>{summary.items_without_hpp || 0}</strong>
        </div>
        <div className="inventory-value-summary-card is-problem">
          <span>Item Bermasalah</span>
          <strong>{summary.problem_items || 0}</strong>
        </div>
      </div>
      <p className="muted small" data-testid="inventory-value-summary-scope">
        Total dihitung dari seluruh item, bukan dari halaman yang sedang tampil. Export mengikuti filter aktif.
      </p>

      {book && (
        <div data-testid="financial-book-reconciliation">
          <div className="inventory-value-summary inventory-book-recon">
            <div className="inventory-value-summary-card">
              <span>Opening posted</span>
              <strong>{moneyOrGap(book.opening_value)}</strong>
            </div>
            <div className="inventory-value-summary-card">
              <span>GR posted</span>
              <strong>{moneyOrGap(book.goods_receipt_value)}</strong>
            </div>
            <div className="inventory-value-summary-card">
              <span>COGS posted</span>
              <strong>{moneyOrGap(book.cogs_value)}</strong>
            </div>
            <div className="inventory-value-summary-card is-total">
              <span>Sisa finansial</span>
              <strong data-testid="financial-book-remaining">{moneyOrGap(book.financial_remaining)}</strong>
            </div>
            <div className="inventory-value-summary-card">
              <span>Saldo GL persediaan</span>
              <strong data-testid="financial-book-gl">{moneyOrGap(book.inventory_gl_balance)}</strong>
            </div>
            <div className="inventory-value-summary-card">
              <span>Status rekonsiliasi</span>
              <strong data-testid="financial-book-status">{book.reconciliation_status || "INCOMPLETE"}</strong>
            </div>
          </div>
          <p className="muted small" data-testid="financial-book-identity">
            {`Opening posted + GR posted − COGS posted = sisa finansial. Qty finansial ${book.financial_qty_remaining == null ? "belum terbentuk" : fmtQty(book.financial_qty_remaining)} dari opening ${book.opening_qty == null ? "—" : fmtQty(book.opening_qty)} + GR ${book.goods_receipt_qty == null ? "—" : fmtQty(book.goods_receipt_qty)} − COGS ${book.cogs_qty == null ? "—" : fmtQty(book.cogs_qty)}. `}
            GR historis yang masih aktif: {moneyOrGap(book.historical_gr_overlap)}.
            Sumber GL lain: {moneyOrGap(book.other_gl_total)}.
            Valuasi Published tetap bukan saldo buku.
            {book.pending_cogs_count ? ` Posting HPP pending: ${book.pending_cogs_count}. Status bukan MATCH.` : ""}
            {(book.blocked_stock_receipts || []).length
              ? ` Penerimaan yang sudah mengubah stok tetapi ditolak posting: ${(book.blocked_stock_receipts || []).length}. Status bukan MATCH.`
              : ""}
          </p>
          {book.physical_vs_financial && (
            <p className="muted small" data-testid="financial-book-physical">{book.physical_vs_financial}</p>
          )}
          {(book.blocked_stock_receipts || []).map((row) => (
            <p key={row.receipt_id || row.receipt_number} className="muted small" data-testid="financial-book-blocked-receipt">
              {row.code}: {row.receipt_number || row.receipt_id}. {row.message}
            </p>
          ))}
          {book.item_hpp_scope && (
            <p className="muted small" data-testid="financial-book-hpp-scope">{book.item_hpp_scope}</p>
          )}
        </div>
      )}

      {(canViewOpening || canPostOpening) && (
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
          <div className="prod-filter-row">
            <input
              value={openingForm.effective_date}
              onChange={(event) => updateOpening({ effective_date: event.target.value })}
              placeholder="Tanggal efektif YYYY-MM-DD"
              aria-label="Tanggal efektif opening"
              data-testid="financial-opening-date"
            />
            <input
              value={openingForm.master_item_id}
              onChange={(event) => updateOpening({ master_item_id: event.target.value })}
              placeholder="ID item"
              aria-label="Item opening"
              data-testid="financial-opening-item"
            />
            <input
              value={openingForm.qty}
              onChange={(event) => updateOpening({ qty: event.target.value })}
              placeholder="Qty"
              aria-label="Qty opening"
              inputMode="decimal"
              data-testid="financial-opening-qty"
            />
            <input
              value={openingForm.unit_cost}
              onChange={(event) => updateOpening({ unit_cost: event.target.value })}
              placeholder="Harga pokok disetujui"
              aria-label="Harga pokok opening"
              inputMode="decimal"
              data-testid="financial-opening-cost"
            />
            <input
              value={openingForm.base_uom}
              onChange={(event) => updateOpening({ base_uom: event.target.value })}
              placeholder="Base UOM"
              aria-label="Base UOM"
              data-testid="financial-opening-uom"
            />
            <input
              value={openingForm.snapshot_id}
              onChange={(event) => updateOpening({ snapshot_id: event.target.value })}
              placeholder="ID snapshot, bila rekonstruksi tidak terbukti"
              aria-label="Snapshot opening"
              data-testid="financial-opening-snapshot"
            />
            {canPostOpening && (
              <button className="ghost" type="button" onClick={captureSnapshot} disabled={capturing || postingOpening} data-testid="financial-opening-capture">
                {capturing ? "Mengambil…" : "Ambil snapshot"}
              </button>
            )}
            <button className="ghost" type="button" onClick={previewOpening} disabled={previewing} data-testid="financial-opening-preview-button">
              {previewing ? "Menghitung…" : "Preview"}
            </button>
            {canPostOpening && (
              <button className="primary" type="submit" disabled={postingOpening || capturing || (preview && preview.can_post === false)} data-testid="financial-opening-post">
                {postingOpening ? "Memposting…" : "Posting opening"}
              </button>
            )}
          </div>
          {formDirty && (
            <p className="muted small" data-testid="financial-opening-dirty">Perubahan formulir belum diposting.</p>
          )}
          {openingNotice && (
            <p className="muted small" data-testid="financial-opening-notice">{openingNotice}</p>
          )}
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
                  Snapshot cutoff {row.snapshot_qty == null ? "tidak direka" : fmtQty(row.snapshot_qty)} {row.base_uom}.
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
          {openings.filter((row) => row.status === "POSTED").slice(0, 1).map((row) => (
            <p key={row.id} className="muted small" data-testid="financial-opening-journal">
              Opening posted {row.effective_date}. Status jurnal {row.status}.
              Nomor {row.journal_number || row.posted_journal_id || "—"}.
              Asal {row.journal_origin === "existing_opening_voucher" ? "voucher saldo awal yang sudah ada" : "jurnal opening baru"}.
              Otoritas jurnal {row.journal_authority || "belum diperiksa"}.
              Nilai {formatRupiah(row.total)}.
            </p>
          ))}
          {canPostOpening && openings.filter((row) => row.status === "NEEDS_RECOVERY").map((row) => (
            <button
              key={row.id}
              type="button"
              className="ghost"
              disabled={recovering}
              data-testid="financial-opening-recover"
              onClick={() => recoverOpening(row.client_request_id)}
            >
              Pulihkan {row.client_request_id}
            </button>
          ))}
        </form>
      )}

      {canViewIssues && (
        <section data-testid="cogs-queue" className="inventory-cogs-queue">
          <h2>Antrean HPP penjualan</h2>
          <p className="muted small">Status keuangan terpisah dari status operasional pengiriman. Posting hanya lewat tombol ini.</p>
          {issueNotice && <p className="muted small" data-testid="cogs-queue-notice">{issueNotice}</p>}
          <div className="prod-table-wrap">
            <table className="prod-table" data-testid="cogs-queue-table">
              <thead>
                <tr>
                  <th>Mutasi</th>
                  <th>Tanggal jurnal</th>
                  <th>Qty</th>
                  <th>Nilai</th>
                  <th>Status keuangan</th>
                  <th>Sumber acquisition</th>
                  <th>Jurnal</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {issues.length === 0 && (
                  <tr><td colSpan={8} className="muted small">Tidak ada pengeluaran yang menunggu atau sudah diposting.</td></tr>
                )}
                {issues.map((issue) => (
                  <tr key={issue.movement_id} data-testid={`cogs-row-${issue.movement_id}`}>
                    <td className="mono">{issue.reference_number || issue.movement_id}</td>
                    <td>{issue.journal_date || "—"}</td>
                    <td>{issue.base_qty} {issue.base_uom}</td>
                    <td>{issue.computed_cogs != null ? formatRupiah(issue.computed_cogs) : "—"}</td>
                    <td>{issue.financial_status || issue.status}{issue.blocker_message ? ` — ${issue.blocker_message}` : ""}</td>
                    <td>{(issue.acquisition_sources || []).map((source) => source.acquisition_key).join(", ") || "—"}</td>
                    <td>{issue.accounting_journal_id ? <a href="/buku-besar/jurnal-umum">{issue.accounting_journal_id}</a> : "—"}</td>
                    <td>
                      {canPostIssues && issue.can_post && (
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
        </section>
      )}

      {canViewOpening && (
        <section data-testid="activity-reports" className="inventory-activity-reports">
          <h2>Laporan pembelian dan penjualan</h2>
          <p className="muted small">Filter tanggal adalah tanggal dokumen inklusif (WIB). Bukan saldo historis as-of. Total memakai seluruh hasil filter.</p>
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

      <div className="prod-filters" data-testid="inventory-value-filters">
        <div className="prod-filter-row">
          <form className="search-inline" onSubmit={doSearch}>
            <Search size={14} />
            <input
              type="text"
              value={qInput}
              onChange={(event) => setQInput(event.target.value)}
              placeholder="Cari kode / nama (live)"
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
            <option value="ok">OK</option>
            <option value="missing">Belum Ada HPP</option>
            <option value="problem">Bermasalah</option>
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
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("name")}>Nama</button>
              </th>
              <th className="col-fit" style={CENTER_CELL}>Satuan</th>
              <th className="col-fit num" style={CENTER_CELL}>
                <button type="button" className="prod-th-label is-sort" style={CENTER_SORT} onClick={() => cycleSort("stock_fisik")}>Stock Fisik</button>
              </th>
              <th className="col-fit num" style={CENTER_CELL}>
                <button type="button" className="prod-th-label is-sort" style={CENTER_SORT} onClick={() => cycleSort("current_hpp")}>HPP Rata-Rata</button>
              </th>
              <th className="col-fit num" style={CENTER_CELL}>
                <button type="button" className="prod-th-label is-sort" style={CENTER_SORT} onClick={() => cycleSort("inventory_value")}>Nilai Persediaan</button>
              </th>
              <th className="col-fit" style={CENTER_CELL}>
                <button type="button" className="prod-th-label is-sort" style={CENTER_SORT} onClick={() => cycleSort("hpp_status")}>Status HPP</button>
              </th>
              <th className="col-fit" style={CENTER_CELL}>Sumber</th>
              <th className="col-fit" style={CENTER_CELL}>Posting</th>
              <th className="col-fit num" style={CENTER_CELL}>Opening disetujui</th>
              <th className="col-fit num" style={CENTER_CELL}>Selisih</th>
              <th className="col-text">Hambatan</th>
              <th className="col-fit" style={CENTER_CELL}>Terakhir Update</th>
            </tr>
          </thead>
          <tbody data-testid="inventory-value-tbody" data-search-state={tableView}>
            {tableView === "loading" && (
              <tr data-testid="inventory-value-loading">
                <td colSpan={13} className="muted small">Memuat…</td>
              </tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={13} className="muted small" data-testid="inventory-value-empty">
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
                  className={item.hpp_status === "QTY_MISMATCH" ? "stock-neg" : ""}
                >
                  <td className="col-fit mono" style={CENTER_CELL}>{item.item_code || "—"}</td>
                  <td className="col-text">{item.item_name || "—"}</td>
                  <td className="col-fit" style={CENTER_CELL}>{item.unit || "—"}</td>
                  <td className="col-fit num mono" style={CENTER_CELL}>{fmtQty(item.stock_fisik)}</td>
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
                      {statusLabel(item.hpp_status, item.hpp_source)}
                    </span>
                  </td>
                  <td className="col-fit small" style={CENTER_CELL} data-testid={`inventory-value-source-${key}`}>
                    {sourceLabel(item.hpp_source)}
                    {item.financial_opening_source ? ` · buku ${sourceLabel(item.financial_opening_source)}${item.financial_opening_unit_cost != null ? ` ${formatRupiah(item.financial_opening_unit_cost)}` : ""}` : ""}
                  </td>
                  <td className="col-fit small" style={CENTER_CELL} data-testid={`inventory-value-posting-${key}`}>
                    {POSTING_LABELS[item.financial_posting_status] || item.financial_posting_status || "Belum diposting"}
                  </td>
                  <td className="col-fit num mono" style={CENTER_CELL}>
                    {item.financial_opening_total != null ? formatRupiah(item.financial_opening_total) : "—"}
                  </td>
                  <td className="col-fit num mono" style={CENTER_CELL} title={item.reconciliation_explanation || ""}>
                    {fmtQty(item.reconciliation_difference ?? item.difference)}
                  </td>
                  <td className="col-text small">
                    {item.financial_blocker_message || item.hpp_reason || item.reconciliation_explanation || "—"}
                  </td>
                  <td className="col-fit small nowrap" style={CENTER_CELL}>
                    {item.updated_at ? formatDate(item.updated_at) : "—"}
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
            disabled={page >= pageCount || tableBusy}
            onClick={() => setPage((current) => current + 1)}
          >
            Berikutnya
          </button>
        </div>
      )}

    </div>
  );
}
