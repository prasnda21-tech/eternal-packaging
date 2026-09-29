import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
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
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";
import { showLoadedDocumentCount } from "../lib/salesSubmenuDensity";
import "./salesSubmenuDensity.css";
import { useDocumentList, DocumentFilters, DocumentSummary, DocumentPager, ColumnFilter, DocumentActions, useDetailDrawer } from "./SalesDocumentControls";
import { moneyText } from "./salesDocumentsView";

function getTodayString() {
  const d = new Date();
  return d.toISOString().split("T")[0];
}

function formatRupiah(num) {
  const n = Number(num) || 0;
  return "Rp " + n.toLocaleString("id-ID");
}

import { filterEligibleInvoices, resolvePreselectedInvoiceId, computeReceiptPreview } from "../lib/salesReceipts";
import SalesReceiptProofViewer from "./SalesReceiptProofViewer";
import {
  RECEIPT_PROOF_ACCEPT,
  buildReceiptProofPayload,
  validateReceiptProofFile,
} from "../lib/salesReceiptPaymentProof";

export default function PenerimaanPenjualan() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { user, hasPerm } = useAuth();

  const canCreate =
    hasPerm("sales.receipt.create") ||
    hasPerm("sales.invoice.create") ||
    hasPerm("sales_order.create") ||
    hasPerm("po.edit") ||
    user?.role === "admin";
  const canDelete =
    hasPerm("sales.receipt.delete") ||
    hasPerm("sales.receipt.create") ||
    hasPerm("po.edit") ||
    user?.role === "admin";
  const canPostAccounting = hasPerm("sales.receipt.accounting.post") || user?.role === "admin";
  const canReverseAccounting = hasPerm("sales.receipt.accounting.reverse") || user?.role === "admin";

  const queryInvoiceId = searchParams.get("invoice_id") || "";

  // List State
  const [receipts, setReceipts] = useState([]);
  const [listLoading, setListLoading] = useState(false);
  const [listErr, setListErr] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteErr, setDeleteErr] = useState("");

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
  const creditRequestRef = useRef({ key: "", signature: "" });

  // Detail Modal State
  const [detailReceipt, setDetailReceipt] = useState(null);
  const [accountingTarget, setAccountingTarget] = useState(null);
  const [accountingBusy, setAccountingBusy] = useState(false);
  const [accountingErr, setAccountingErr] = useState("");

  // Load receipts list
  const loadReceipts = useCallback(async () => {
    setListLoading(true);
    setListErr("");
    try {
      const params = {};
      if (searchQuery.trim()) params.q = searchQuery.trim();
      const res = await api.listSalesReceipts(params);
      setReceipts(res.receipts || []);
      setSelectedIds(new Set());
    } catch (e) {
      const det = e.response?.data?.detail;
      setListErr(typeof det === "object" ? det.message : det || "Gagal memuat daftar Penerimaan Penjualan");
    } finally {
      setListLoading(false);
    }
  }, [searchQuery]);

  const documentList = useDocumentList(receipts, "receipt", searchQuery);
  const visibleKey = documentList.visible.map(row => row.id).join('|');
  useEffect(() => setSelectedIds(new Set()), [visibleKey]);
  const deletableReceipts = useMemo(
    () => documentList.visible.filter((r) => r.can_delete !== false),
    [documentList.visible]
  );
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
      await api.deleteSalesReceipt(id);
      setDeleteConfirm(null);
      setSelectedIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      loadReceipts();
    } catch (e) {
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
      await api.bulkDeleteSalesReceipts(ids);
      setDeleteConfirm(null);
      setSelectedIds(new Set());
      loadReceipts();
    } catch (e) {
      setDeleteErr(formatDeleteError(e.response?.data?.detail));
    } finally {
      setDeleteLoading(false);
    }
  };

  // Load unpaid / partially paid invoices for the dropdown
  const accountingLabel = (status) => {
    const raw = String(status || "not_posted").toLowerCase();
    if (raw === "posted") return "Posted";
    if (raw === "reversed") return "Reversed";
    return "Belum Diposting";
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
      loadReceipts();
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
      const payload = {
        invoice_id: selectedInvoiceId,
        receipt_date: receiptDate || getTodayString(),
        amount: numAmount,
        allocated_amount: previewCalc.allocated,
        notes: receiptNotes.trim(),
        cash_bank_id: cashBankId,
        payment_proof,
      };
      const res = await api.createSalesReceipt(payload);
      window.dispatchEvent(new CustomEvent("eternal:sales-receipt-saved"));
      setSuccessMsg(res.message || "Penerimaan Penjualan berhasil disimpan!");
      setTimeout(() => {
        setSuccessMsg("");
        handleCloseCreateModal();
        loadReceipts();
      }, 900);
    } catch (err) {
      const det = err.response?.data?.detail;
      setCreateErr(typeof det === "object" ? det.message : det || err.message || "Gagal menyimpan Penerimaan Penjualan");
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
      loadReceipts();
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
      loadReceipts();
    } catch (error) {
      const detail = error?.response?.data?.detail;
      setCreditMsg(typeof detail === "object" ? detail.message : detail || "Pembalikan pemakaian gagal.");
    } finally {
      setCreditBusy(false);
    }
  };

  const drawerRef = useDetailDrawer(Boolean(detailReceipt), () => setDetailReceipt(null), Boolean(proofViewerReceipt || accountingTarget) || creditBusy);
  const showReceiptCount = showLoadedDocumentCount({
    loading: listLoading,
    error: listErr,
    query: searchQuery,
  });

  return (
    <div
      className="page-container penjualan-page penjualan-workspace sd-density sd-list-mode doc-premium"
      data-testid="penerimaan-penjualan-page"
    >
      <header className="sd-list-header">
        <div className="sd-list-heading">
          <h1>Penerimaan Penjualan</h1>
          {showReceiptCount && (
            <div className="sd-list-summary" data-testid="receipt-list-count">
              <strong>{receipts.length.toLocaleString("id-ID")}</strong>
              <span>penerimaan</span>
            </div>
          )}
        </div>
        <div className="sd-list-header-actions">
          {canDelete && selectedIds.size > 0 && (
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
            onClick={loadReceipts}
            disabled={listLoading}
            title="Segarkan"
            aria-label="Segarkan"
          >
            <RefreshCw size={16} className={listLoading ? "animate-spin" : ""} />
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

      <DocumentSummary list={documentList} loading={listLoading} error={listErr} />
      <div className="sd-list-toolbar">
        <div className="sd-list-search">
          <Search size={16} />
          <input
            type="text"
            className="form-input"
            data-testid="search-receipt-input"
            placeholder="Cari nomor penerimaan, no faktur, no PO, customer..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
        <DocumentFilters list={documentList} />
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
              {canDelete && <col style={{ width: 36 }} />}
              <col />
              <col style={{ width: 108 }} />
              <col style={{ width: 130 }} />
              <col style={{ width: 110 }} />
              <col style={{ width: 110 }} />
              <col style={{ width: 140 }} />
              <col style={{ width: 108 }} />
            </colgroup>
            <thead>
              <tr>
                {canDelete && (
                  <th>
                    <input type="checkbox" data-testid="select-all-receipts" checked={deletableReceipts.length > 0 && selectedIds.size === deletableReceipts.length} onChange={toggleSelectAll} aria-label="Pilih semua baris tampilan" />
                  </th>
                )}
                <th><ColumnFilter list={documentList} field="identity">Pelanggan / No. Penerimaan</ColumnFilter></th>
                <th><ColumnFilter list={documentList} field="receipt_date">Tanggal</ColumnFilter></th>
                <th><ColumnFilter list={documentList} field="amount">Diterima / dialokasikan</ColumnFilter></th>
                <th><ColumnFilter list={documentList} field="payment_state">Status Faktur</ColumnFilter></th>
                <th><ColumnFilter list={documentList} field="accounting_status">Accounting</ColumnFilter></th>
                <th><ColumnFilter list={documentList} field="cash_bank_name">Kas / Bank</ColumnFilter></th>
                <th>Aksi</th>
              </tr>
            </thead>
            <tbody>
              {listLoading ? (
                <tr>
                  <td colSpan={canDelete ? 8 : 7} className="sd-list-empty">
                    Memuat data penerimaan penjualan...
                  </td>
                </tr>
              ) : documentList.filtered.length === 0 ? (
                <tr>
                  <td colSpan={canDelete ? 8 : 7} className="sd-list-empty">
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
                      <FileText size={36} color="#9ca3af" />
                      <span style={{ fontWeight: 600, color: "#374151" }}>Tidak ada penerimaan pada hasil ini</span>
                      <span style={{ fontSize: 12, color: "#6b7280" }}>
                        Penerimaan pembayaran dari Faktur Penjualan akan tercatat di sini.
                      </span>
                    </div>
                  </td>
                </tr>
              ) : (
                documentList.visible.map((r, idx) => (
                  <tr
                    key={r.id || idx}
                    style={{
                      borderBottom: "1px solid #f3f4f6",
                      backgroundColor: idx % 2 === 0 ? "#fff" : "#fafafa",
                    }}
                  >
                    {canDelete && (
                      <td style={{ padding: "12px 8px" }}>
                        {r.can_delete !== false && (
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
                        onClick={async () => {
                          setAccountingErr("");
                          try {
                            const fresh = await api.getSalesReceipt(r.id);
                            setDetailReceipt(fresh.receipt || fresh);
                          } catch {
                            setDetailReceipt(r);
                          }
                        }}
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
                    <td className="sd-money is-zero">{moneyText(r.amount)}<span className="sd-support">Alokasi awal {moneyText(r.allocated_amount ?? r.amount)}</span></td>
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
                    </td>
                    <td style={{ padding: "12px 16px", color: "#374151" }} data-testid={`receipt-cash-bank-${r.id}`}>
                      {r.cash_bank_name || "-"}
                    </td>
                    <td>
                      <div className="sd-actions">
                        <button type="button" className="sd-act sd-act-view" onClick={async () => { setAccountingErr(""); try { const fresh = await api.getSalesReceipt(r.id); setDetailReceipt(fresh.receipt || fresh); } catch { setDetailReceipt(r); } }}>Detail</button>
                        <DocumentActions>
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
                        {canPostAccounting && r.can_post_accounting && (
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
                            Post Accounting
                          </button>
                        )}
                        {canReverseAccounting && r.can_reverse_accounting && (
                          <button
                            type="button"
                            className="sd-act sd-act-delete"
                            data-testid={`btn-reverse-accounting-${r.id}`}
                            onClick={() => {
                              setAccountingErr("");
                              setAccountingTarget({ action: "reverse", receipt: r });
                            }}
                          >
                            Reverse
                          </button>
                        )}
                        {canDelete && r.can_delete !== false && (
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
                        </DocumentActions>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <footer className="sd-list-footer" data-testid="receipt-list-footer">
            <DocumentPager list={documentList} loading={listLoading} error={listErr} />
          </footer>
      </div>

      {/* CREATE RECEIPT MODAL */}
      {isModalOpen && (
        <div
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
            className="sd-modal-card"
            onClick={(e) => e.stopPropagation()}
            style={{
              backgroundColor: "#fff",
              borderRadius: 10,
              width: "100%",
              maxWidth: 580,
              maxHeight: "calc(100vh - 48px)",
              display: "flex",
              flexDirection: "column",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)",
              overflow: "hidden",
            }}
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

            {/* Modal Body */}
            <form className="sd-modal-form" onSubmit={handleSaveReceipt} style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
              <div
                className="sd-modal-body"
                data-testid="receipt-modal-body"
                style={{ padding: "20px 20px 28px", overflowY: "auto", flex: "1 1 auto", minHeight: 0 }}
              >
                {createErr && (
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
                    <span>{createErr}</span>
                  </div>
                )}

                {successMsg && (
                  <div
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

                <FormProgress
                  percentage={Math.round(([selectedInvoiceId, Number(receiptAmount) > 0, receiptDate, cashBankId, proofFile].filter(Boolean).length / 5) * 100)}
                  completed={[selectedInvoiceId, Number(receiptAmount) > 0, receiptDate, cashBankId, proofFile].filter(Boolean).length}
                  total={5}
                  className="mb-4"
                />

                {/* 1. Pilih Faktur Penjualan */}
                <FormSection
                  id="sr-invoice-section"
                  title="1. Pilih Faktur Penjualan"
                  description="Pilih faktur yang memiliki sisa tagihan."
                  status={selectedInvoiceId ? "complete" : "incomplete"}
                  className="fs-section-scroll-safe"
                >
                  <div style={{ marginBottom: 16 }}>
                    <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                      Pilih Faktur Penjualan *
                    </label>
                    <select
                      className="form-select"
                      data-testid="select-receipt-invoice"
                      value={selectedInvoiceId}
                      onChange={(e) => setSelectedInvoiceId(e.target.value)}
                      disabled={createBusy || invoicesLoading}
                      style={{
                        width: "100%",
                        fontSize: 13,
                      }}
                    >
                      <option value="">-- Pilih Faktur Penjualan --</option>
                      {invoicesWithBalance.map((inv) => (
                        <option key={inv.id} value={inv.id}>
                          {inv.customer} — PO {inv.po_number} (Sisa: {formatRupiah(inv.outstanding_amount || inv.grand_total)})
                        </option>
                      ))}
                    </select>
                    {invoicesWithBalance.length === 0 && !invoicesLoading && (
                      <p data-testid="no-eligible-invoices-hint" style={{ margin: "6px 0 0 0", fontSize: 12, color: "#64748b" }}>
                        Tidak ada faktur dengan sisa tagihan aktif (semua faktur telah lunas atau belum memiliki nilai tagihan).
                      </p>
                    )}
                  </div>

                  {/* Summary Card Faktur */}
                  {selectedInvoice && (
                    <div
                      data-testid="selected-invoice-summary-card"
                      style={{
                        backgroundColor: "#f8fafc",
                        border: "1px solid #e2e8f0",
                        borderRadius: 8,
                        padding: 14,
                        marginBottom: 16,
                      }}
                    >
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 12, fontSize: 13 }}>
                        <div>
                          <span style={{ color: "#64748b" }}>Pelanggan:</span>
                          <div data-testid="summary-customer" style={{ fontWeight: 600, color: "#1e293b" }}>{selectedInvoice.customer || "-"}</div>
                        </div>
                        <div>
                          <span style={{ color: "#64748b" }}>No. Faktur:</span>
                          <div data-testid="summary-invoice-number" style={{ fontWeight: 600, color: "#1e293b" }}>{selectedInvoice.invoice_number || "-"}</div>
                        </div>
                        <div>
                          <span style={{ color: "#64748b" }}>No. PO:</span>
                          <div data-testid="summary-po-number" style={{ fontWeight: 600, color: "#1e293b" }}>{selectedInvoice.po_number}</div>
                        </div>
                        <div>
                          <span style={{ color: "#64748b" }}>Total Faktur:</span>
                          <div style={{ fontWeight: 600, color: "#1e293b" }}>{formatRupiah(selectedInvoice.grand_total)}</div>
                        </div>
                        <div>
                          <span style={{ color: "#64748b" }}>Sisa Tagihan Saat Ini:</span>
                          <div data-testid="summary-outstanding-amount" style={{ fontWeight: 700, color: "#dc2626" }}>
                            {formatRupiah(selectedInvoice.outstanding_amount)}
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </FormSection>

                {/* 2. Detail Pembayaran */}
                <FormSection
                  id="sr-payment-section"
                  title="2. Detail Penerimaan Pembayaran"
                  description="Masukkan nominal pembayaran, tanggal, dan metode transfer."
                  status={computeStatus([{ value: receiptAmount, isRequired: true }, { value: receiptDate, isRequired: true }, { value: cashBankId, isRequired: true }])}
                  className="fs-section-scroll-safe"
                >
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                      <label style={{ fontSize: 12, fontWeight: 600, color: "#374151", margin: 0 }}>
                        Jumlah Penerimaan (Rp) *
                      </label>
                    {selectedInvoice && selectedInvoice.outstanding_amount > 0 && (
                      <button
                        type="button"
                        onClick={handlePayFull}
                        style={{
                          background: "none",
                          border: "none",
                          color: "#059669",
                          fontSize: 11,
                          fontWeight: 600,
                          cursor: "pointer",
                          textDecoration: "underline",
                        }}
                      >
                        Bayar Sisa Tagihan (Lunas)
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
                    style={{
                      width: "100%",
                      fontSize: 14,
                      fontWeight: 600,
                    }}
                  />
                </div>

                <div style={{ marginBottom: 16 }} data-testid="receipt-allocation-field">
                  <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                    Alokasi ke faktur (Rp)
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
                    style={{ width: "100%", fontSize: 14 }}
                  />
                  {previewCalc && previewCalc.amount > 0 && (
                    <p data-testid="receipt-allocation-total" style={{ margin: "6px 0 0", fontSize: 12, color: "#0f766e" }}>
                      Total dialokasikan: {formatRupiah(previewCalc.allocationExceedsOutstanding || previewCalc.allocationExceedsReceived ? 0 : previewCalc.allocated)}
                    </p>
                  )}
                  {previewCalc && previewCalc.customerCredit > 0 && (
                    <p data-testid="receipt-customer-credit-remainder" style={{ margin: "4px 0 0", fontSize: 12, fontWeight: 700, color: "#0f766e" }}>
                      Sisa menjadi saldo pelanggan: {formatRupiah(previewCalc.customerCredit)}
                    </p>
                  )}
                </div>

                {canPostAccounting && selectedInvoice && (
                  <div style={{ marginBottom: 16 }} data-testid="receipt-use-credit">
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
                        Gunakan Saldo
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

                {/* 3. Tanggal Penerimaan */}
                <div style={{ marginBottom: 16 }}>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                    Tanggal Penerimaan *
                  </label>
                  <input
                    type="date"
                    className="form-input"
                    data-testid="input-receipt-date"
                    value={receiptDate}
                    onChange={(e) => setReceiptDate(e.target.value)}
                    disabled={createBusy}
                    style={{
                      width: "100%",
                      fontSize: 13,
                    }}
                  />
                </div>

                <div style={{ marginBottom: 16 }}>
                  <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                    Kas / Bank *
                  </label>
                  <select
                    className="form-select"
                    data-testid="select-receipt-cash-bank"
                    value={cashBankId}
                    onChange={(e) => setCashBankId(e.target.value)}
                    disabled={createBusy || cashBanksLoading}
                    style={{ width: "100%", fontSize: 13 }}
                  >
                    <option value="">-- Pilih Kas / Bank --</option>
                    {cashBanks.map((acc) => (
                      <option key={acc.id} value={acc.id}>
                        {acc.name}
                      </option>
                    ))}
                  </select>
                  {cashBanks.length === 0 && !cashBanksLoading && (
                    <p data-testid="no-cash-bank-hint" style={{ margin: "6px 0 0 0", fontSize: 12, color: "#64748b" }}>
                      Belum ada Kas/Bank aktif. Tambahkan di menu Kas &amp; Bank terlebih dahulu.
                    </p>
                  )}
                </div>

                {/* 4. Catatan */}
                <div style={{ marginBottom: 16 }}>
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
                    style={{
                      width: "100%",
                      fontSize: 13,
                    }}
                  />
                </div>

                {/* 5. Bukti Payment */}
                <div style={{ marginBottom: 18 }} data-testid="receipt-payment-proof-field">
                  <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#374151", marginBottom: 6 }}>
                    Bukti Payment *
                  </label>
                  <p style={{ margin: "0 0 8px 0", fontSize: 12, color: "#64748b" }}>
                    Unggah JPG, JPEG, PNG, atau PDF. Nominal pembayaran tetap mengikuti jumlah penerimaan di atas — bukan dari file.
                  </p>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                    <label
                      className={"primary btn-file" + (createBusy ? " disabled" : "")}
                      data-testid="receipt-proof-upload-label"
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        cursor: createBusy ? "not-allowed" : "pointer",
                        opacity: createBusy ? 0.6 : 1,
                      }}
                    >
                      <input
                        type="file"
                        accept={RECEIPT_PROOF_ACCEPT}
                        disabled={createBusy}
                        onChange={handleProofFileChange}
                        data-testid="input-receipt-payment-proof"
                        style={{ display: "none" }}
                      />
                      <FileText size={14} /> {proofFile ? "Ganti File" : "Pilih File"}
                    </label>
                    {proofFile && (
                      <>
                        <span className="muted small" data-testid="receipt-proof-filename" style={{ fontSize: 13, color: "#0f172a" }}>
                          {proofFile.name}
                        </span>
                        <button
                          type="button"
                          className="ghost sm"
                          data-testid="btn-clear-receipt-proof"
                          disabled={createBusy}
                          onClick={() => { setProofFile(null); setProofErr(""); }}
                        >
                          Hapus
                        </button>
                      </>
                    )}
                  </div>
                  {proofErr && (
                    <div className="error" data-testid="receipt-proof-error" style={{ marginTop: 8, fontSize: 12, color: "#b91c1c" }}>
                      {proofErr}
                    </div>
                  )}
                </div>

                {/* Live Preview Bar */}
                {previewCalc && previewCalc.amount > 0 && !previewCalc.allocationExceedsOutstanding && !previewCalc.allocationExceedsReceived && (
                  <div
                    style={{
                      backgroundColor: "#ecfdf5",
                      border: "1px solid #a7f3d0",
                      borderRadius: 6,
                      padding: 12,
                      marginBottom: 18,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <div>
                      <div style={{ fontSize: 11, color: "#065f46" }}>Status Baru Setelah Penerimaan:</div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: "#065f46" }}>
                        {previewCalc.newState === "LUNAS" ? "LUNAS (100% Terbayar)" : `DP (Sisa: ${formatRupiah(previewCalc.newOutstanding)})`}
                      </div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{ fontSize: 11, color: "#065f46" }}>Total Terbayar:</div>
                      <div style={{ fontSize: 13, fontWeight: 700, color: "#065f46" }}>
                        {formatRupiah(previewCalc.newPaid)}
                      </div>
                    </div>
                  </div>
                )}
                </FormSection>
              </div>

              {/* Modal Actions */}
              <div
                className="sd-modal-footer"
                data-testid="receipt-modal-footer"
                style={{
                  display: "flex",
                  justifyContent: "flex-end",
                  gap: 10,
                  padding: "12px 20px 16px",
                  borderTop: "1px solid #f1f5f9",
                  backgroundColor: "#fff",
                  flex: "0 0 auto",
                }}
              >
                <button
                  type="button"
                  data-testid="btn-cancel-receipt-modal"
                  className="btn btn-secondary"
                  onClick={handleCloseCreateModal}
                  disabled={createBusy}
                  style={{
                    padding: "8px 16px",
                    borderRadius: 6,
                    fontSize: 13,
                    cursor: "pointer",
                  }}
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
                    backgroundColor: "#059669",
                    borderColor: "#059669",
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
            <h3 style={{ margin: "0 0 8px" }}>Hapus Penerimaan Penjualan?</h3>
            <p style={{ margin: "0 0 12px", color: "#64748b", fontSize: "0.9rem" }}>Dokumen berikut akan dihapus:</p>
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
                {deleteLoading ? "Menghapus..." : "Hapus"}
              </button>
            </div>
          </div>
        </div>
      )}

      {detailReceipt && (
        <div
          className="modal-overlay"
          data-testid="receipt-detail-modal"
          ref={drawerRef} role="dialog" aria-modal="true" aria-label="Detail Penerimaan Penjualan" tabIndex={-1}
          style={{ position: "fixed", inset: 0, background: "rgba(15,23,42,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 80 }}
        >
          <div style={{ background: "#fff", borderRadius: 12, padding: 24, width: "100%", maxWidth: 480 }}>
            <h3 style={{ margin: "0 0 8px" }}>Detail Penerimaan</h3>
            <p data-testid="receipt-detail-number" style={{ margin: "0 0 16px", fontFamily: "monospace", fontWeight: 700, color: "#059669" }}>
              {detailReceipt.receipt_number}
            </p>
            <dl style={{ margin: "0 0 16px", display: "grid", gridTemplateColumns: "1fr auto", gap: "8px 16px", fontSize: "0.9rem" }}>
              <dt>Accounting</dt>
              <dd data-testid="receipt-detail-accounting-status" style={{ margin: 0, fontWeight: 700 }}>{accountingLabel(detailReceipt.accounting_status)}</dd>
              <dt>Kas / Bank</dt>
              <dd data-testid="receipt-detail-cash-bank" style={{ margin: 0 }}>{detailReceipt.cash_bank_name || "-"}</dd>
              <dt>Jumlah</dt>
              <dd style={{ margin: 0, fontWeight: 700 }}>{formatRupiah(detailReceipt.amount)}</dd>
              <dt>Faktur</dt>
              <dd style={{ margin: 0, fontFamily: "monospace" }}>{detailReceipt.invoice_number || "-"}</dd>
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
            {Array.isArray(detailReceipt.customer_credit_history) && detailReceipt.customer_credit_history.length > 0 && (
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
            {detailReceipt.has_payment_proof && (
              <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                <button type="button" data-testid="btn-lihat-bukti" onClick={() => setProofViewerReceipt(detailReceipt)} style={{ border: "none", background: "#0f766e", color: "#fff", borderRadius: 6, padding: "6px 10px" }}>
                  Lihat Bukti
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
                  style={{ border: "1px solid #cbd5e1", background: "#fff", borderRadius: 6, padding: "6px 10px" }}
                >
                  Unduh Bukti
                </button>
              </div>
            )}
            {canPostAccounting && detailReceipt.can_post_accounting && (
              <button
                type="button"
                data-testid="btn-detail-post-accounting"
                onClick={() => {
                  setAccountingErr("");
                  if (!detailReceipt.cash_bank_id) loadCashBanks();
                  setAccountingTarget({ action: "post", receipt: detailReceipt, cash_bank_id: detailReceipt.cash_bank_id || "" });
                }}
                style={{ marginRight: 8, padding: "8px 14px", borderRadius: 8, border: "none", background: "#047857", color: "#fff", fontWeight: 600 }}
              >
                Post Accounting
              </button>
            )}
            {canReverseAccounting && detailReceipt.can_reverse_accounting && (
              <button
                type="button"
                data-testid="btn-detail-reverse-accounting"
                onClick={() => {
                  setAccountingErr("");
                  setAccountingTarget({ action: "reverse", receipt: detailReceipt });
                }}
                style={{ marginRight: 8, padding: "8px 14px", borderRadius: 8, border: "1px solid #fecaca", background: "#fff", color: "#b91c1c", fontWeight: 600 }}
              >
                Reverse
              </button>
            )}
            <button type="button" onClick={() => setDetailReceipt(null)} style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#fff" }}>
              Tutup
            </button>
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
              {accountingTarget.action === "reverse" ? "Reverse Accounting?" : "Post ke Accounting?"}
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
                    ? "Reverse Accounting"
                    : "Post ke Accounting"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

