import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, Download, MoreHorizontal, Plus, RefreshCw, Search, X } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { goodsReceiptWarehouseChoice } from "../lib/physicalStockView";
import {
  errDetail,
  friendlyLoadError,
  newIdempotencyKey,
  purchaseStatusPillClass,
  purchaseRowInteractionProps,
  formatRupiah,
  receiptSourceBadge,
  shortReceivingStatus,
  todayYmd,
} from "../lib/purchaseUi";

function accountingLabel(status) {
  const raw = String(status || "not_posted").toLowerCase();
  if (raw === "posted") return "Posted";
  if (raw === "reversed") return "Reversed";
  return "Not Posted";
}
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";
import { useDebouncedLiveSearch } from "../hooks/useDebouncedLiveSearch";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";
import PurchasingFlowNav from "./PurchasingFlowNav";
import PurchaseActivityHistory from "./PurchaseActivityHistory";
import PurchaseDocuments from "./PurchaseDocuments";
import PurchaseCancelModal from "./PurchaseCancelModal";
import "./purchasePages.css";

function emptyLine() {
  return {
    key: `${Date.now()}-${Math.random()}`,
    master_item_id: "",
    accurate_item_id: "",
    item_name: "",
    item_code: "",
    unit: "pcs",
    received_qty: "",
    notes: "",
  };
}

export default function PenerimaanBarang() {
  const { hasPerm } = useAuth();
  const [searchParams] = useSearchParams();
  const canSync = hasPerm("purchase_receipts.sync");
  const canExport = hasPerm("purchase_receipts.export");
  const canCreate = hasPerm("purchase_receipts.receive");
  const canReceivePo = hasPerm("purchase_receipts.receive") || hasPerm("purchase_receipts.sync");
  const canCreateInvoice = hasPerm("purchase_invoices.create");
  const canPostAccounting = hasPerm("purchase.receipt.accounting.post");
  const canReverseAccounting = hasPerm("purchase.receipt.accounting.reverse");
  const [cancelTarget, setCancelTarget] = useState(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelErr, setCancelErr] = useState("");
  const [acctTarget, setAcctTarget] = useState(null);
  const [acctBusy, setAcctBusy] = useState(false);
  const [acctErr, setAcctErr] = useState("");
  const [reverseReason, setReverseReason] = useState("");
  const queryPoId = searchParams.get("purchase_order_id") || "";
  const openCreateFromQuery = searchParams.get("action") === "create";
  const openReceiveFromQuery = searchParams.get("action") === "receive" || Boolean(queryPoId);

  const [mainTab, setMainTab] = useState(openCreateFromQuery ? "history" : "receive-po");
  const [view, setView] = useState(openCreateFromQuery && canCreate ? "create" : "list");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");
  const [syncErr, setSyncErr] = useState("");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery } = useDebouncedLiveSearch();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [openId, setOpenId] = useState(null);
  const [exporting, setExporting] = useState(false);
  const [poReceipts, setPoReceipts] = useState([]);
  const [poOrders, setPoOrders] = useState([]);
  const [poLoading, setPoLoading] = useState(false);
  const [poErr, setPoErr] = useState("");
  const [poQ, setPoQ] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const [sourceFilter, setSourceFilter] = useState("all");

  const [recvPoId, setRecvPoId] = useState("");
  const [recvPreview, setRecvPreview] = useState(null);
  const [recvQty, setRecvQty] = useState({});
  const [recvWarehouseChoice, setRecvWarehouseChoice] = useState({ rows: [], warehouseId: "", blocked: false, explicit: false });
  const [recvWarehouseId, setRecvWarehouseId] = useState("");
  const [recvDate, setRecvDate] = useState(todayYmd());
  const [recvBusy, setRecvBusy] = useState(false);
  const [recvErr, setRecvErr] = useState("");
  const [recvMsg, setRecvMsg] = useState("");
  const [poDetail, setPoDetail] = useState(null);
  const [poDetailLoading, setPoDetailLoading] = useState(false);
  const [receiveOpen, setReceiveOpen] = useState(false);

  const [supplier, setSupplier] = useState("");
  const [notes, setNotes] = useState("");
  const [reference, setReference] = useState("");
  const [lines, setLines] = useState([emptyLine()]);
  const [formErr, setFormErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [manualPoId, setManualPoId] = useState("");
  const [eligiblePos, setEligiblePos] = useState([]);

  const loadHistory = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const d = await api.purchaseReceipts({ q, page, page_size: pageSize });
      setData(d);
    } catch (ex) {
      setErr(friendlyLoadError(ex, "Tidak dapat memuat data penerimaan. Periksa koneksi server lalu coba lagi."));
    } finally {
      setLoading(false);
    }
  }, [q, page, pageSize]);

  const loadPoOrders = useCallback(async () => {
    setPoLoading(true);
    setPoErr("");
    try {
      const res = await api.listPurchaseOrders({ q: poQ || undefined });
      setPoOrders(res.orders || []);
    } catch (ex) {
      setPoErr(friendlyLoadError(ex, "Tidak dapat memuat daftar pesanan. Periksa koneksi server lalu coba lagi."));
    } finally {
      setPoLoading(false);
    }
  }, [poQ]);

  const refreshSideData = useCallback(async () => {
    try {
      const [gr, eligible] = await Promise.all([
        api.listPurchaseGoodsReceipts({ include_cancelled: true }),
        (canCreate || canReceivePo)
          ? api.listPurchaseOrders({ eligible_for_receipt: true })
          : Promise.resolve({ orders: [] }),
      ]);
      setPoReceipts(gr.receipts || []);
      setEligiblePos(eligible.orders || []);
    } catch {
      setPoReceipts([]);
      setEligiblePos([]);
    }
  }, [canCreate, canReceivePo]);

  useEffect(() => {
    if (mainTab === "history" && view === "list") loadHistory();
  }, [loadHistory, mainTab, view]);

  useEffect(() => {
    if (mainTab === "receive-po" && view === "list") loadPoOrders();
  }, [loadPoOrders, mainTab, view]);

  useEffect(() => { setPage(1); }, [q]);

  useEffect(() => {
    refreshSideData();
  }, [refreshSideData, recvMsg, ok]);

  useEffect(() => {
    if (!receiveOpen) return undefined;
    let cancelled = false;
    Promise.all([api.branches(), api.warehouses()])
      .then(([branchRes, warehouseRes]) => {
        if (cancelled) return;
        const choice = goodsReceiptWarehouseChoice(branchRes.items || [], warehouseRes.items || []);
        setRecvWarehouseChoice(choice);
        setRecvWarehouseId(choice.warehouseId || "");
      })
      .catch(() => {
        if (!cancelled) setRecvErr("Gagal memuat gudang penerimaan.");
      });
    return () => { cancelled = true; };
  }, [receiveOpen]);

  useEffect(() => {
    if (!receiveOpen || !recvPoId) {
      if (!receiveOpen) setRecvPreview(null);
      return;
    }
    setRecvErr("");
    api.purchaseGoodsReceiptPreview(recvPoId).then((p) => {
      setRecvPreview(p);
      const map = {};
      (p.lines || []).forEach((l) => { map[l.purchase_order_line_id] = l.remaining_receivable_qty; });
      setRecvQty(map);
    }).catch((ex) => setRecvErr(errDetail(ex)));
  }, [recvPoId, receiveOpen]);

  useEffect(() => {
    if (view !== "create") return;
    if (!manualPoId) {
      setRecvPreview(null);
      return;
    }
    api.purchaseGoodsReceiptPreview(manualPoId).then((p) => {
      setRecvPreview(p);
      const map = {};
      (p.lines || []).forEach((l) => { map[l.purchase_order_line_id] = l.remaining_receivable_qty; });
      setRecvQty(map);
      if (p.supplier) setSupplier(p.supplier);
    }).catch((ex) => setFormErr(errDetail(ex)));
  }, [manualPoId, view]);

  useEffect(() => {
    if (!openReceiveFromQuery || !queryPoId || !canReceivePo) return;
    setMainTab("receive-po");
    setView("list");
    setRecvPoId(queryPoId);
    setRecvDate(todayYmd());
    setRecvQty({});
    setRecvErr("");
    setRecvMsg("");
    setReceiveOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const clearSearch = () => {
    clearQuery();
    setPage(1);
  };

  const resetCreateForm = () => {
    setManualPoId("");
    setRecvPreview(null);
    setRecvQty({});
    setRecvDate(todayYmd());
    setSupplier("");
    setNotes("");
    setReference("");
    setLines([emptyLine()]);
    setFormErr("");
    setBusy(false);
  };

  const openCreate = () => {
    if (!canCreate) return;
    resetCreateForm();
    setOk("");
    setMainTab("direct");
    setView("create");
    setMoreOpen(false);
  };

  const backToList = () => {
    setView("list");
    if (mainTab === "history") loadHistory();
    else if (mainTab === "direct") setMainTab("direct");
    else loadPoOrders();
  };

  const openReceivePo = (poId) => {
    if (!canReceivePo || !poId) return;
    setRecvPoId(poId);
    setRecvDate(todayYmd());
    setRecvQty({});
    setRecvErr("");
    setRecvMsg("");
    setReceiveOpen(true);
  };

  const openPoReceivingDetail = async (poId) => {
    setPoDetailLoading(true);
    setPoErr("");
    try {
      const [res, receiptRes] = await Promise.all([
        api.getPurchaseOrder(poId),
        api.listPurchaseGoodsReceipts({
          purchase_order_id: poId,
          include_cancelled: true,
        }),
      ]);
      setPoDetail(res.order);
      setPoReceipts((current) => [
        ...current.filter(
          (receipt) => String(receipt.purchase_order_id || "") !== String(poId),
        ),
        ...(receiptRes.receipts || []),
      ]);
    } catch (ex) {
      setPoErr(errDetail(ex));
    } finally {
      setPoDetailLoading(false);
    }
  };

  const closeReceive = () => {
    setReceiveOpen(false);
    setRecvPoId("");
    setRecvPreview(null);
    setRecvBusy(false);
  };

  const doSync = async () => {
    if (!canSync || syncing) return;
    setSyncing(true);
    setSyncErr("");
    setSyncMsg("");
    setMoreOpen(false);
    try {
      const r = await api.purchaseReceiptsSync();
      const bits = [
        `Ditemukan ${r.receipts_found ?? 0}`,
        `baru ${r.created ?? 0}`,
        `diubah ${r.updated ?? 0}`,
        `sama ${r.unchanged ?? 0}`,
        `gagal ${r.failed ?? 0}`,
      ];
      const line = bits.join(" · ");
      if (r.ok === false || (r.failed || 0) > 0) {
        setSyncErr(r.error ? `Sinkronisasi tidak lengkap (${r.error}). ${line}` : `Sinkronisasi tidak lengkap. ${line}`);
      } else {
        setSyncMsg("Riwayat penerimaan dari Accurate ditampilkan setelah sinkronisasi. " + line);
      }
      if (mainTab === "history") await loadHistory();
    } catch (ex) {
      setSyncErr(friendlyLoadError(ex, "Gagal sinkronisasi penerimaan dari Accurate. Coba lagi nanti."));
    } finally {
      setSyncing(false);
    }
  };

  const download = async () => {
    setExporting(true);
    setMoreOpen(false);
    try {
      const blob = await api.purchaseReceiptsExport({ q });
      const url = URL.createObjectURL(new Blob([blob], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `penerimaan-barang-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (ex) {
      alert(errDetail(ex) || "Gagal export");
    } finally {
      setExporting(false);
    }
  };

  const addLine = () => setLines((p) => [...p, emptyLine()]);
  const removeLine = (key) => setLines((p) => (p.length <= 1 ? p : p.filter((l) => l.key !== key)));
  const setLine = (key, patch) => setLines((p) => p.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  const pickItem = (key, it) => {
    if (!it) {
      return setLine(key, { master_item_id: "", accurate_item_id: "", item_name: "", item_code: "", unit: "pcs" });
    }
    setLine(key, {
      master_item_id: it.id || it.master_item_id || "",
      accurate_item_id: it.accurate_item_id || "",
      item_name: it.item_name || "",
      item_code: it.item_code || "",
      unit: it.unit || "pcs",
    });
  };

  const validateCreate = () => {
    if (!recvDate) return "Tanggal penerimaan wajib diisi.";
    if (!String(supplier || "").trim()) return "Supplier wajib diisi.";
    const validLines = lines.filter((l) => l.master_item_id && Number(l.received_qty) > 0);
    if (!validLines.length) return "Minimal satu baris Master Item dengan qty > 0.";
    for (const l of lines) {
      if (!l.master_item_id && (l.received_qty === "" || l.received_qty == null)) continue;
      if (!l.master_item_id) return "Setiap baris harus memilih item dari Master Item.";
      const qn = Number(l.received_qty);
      if (!Number.isFinite(qn) || qn <= 0) return `Qty diterima '${l.item_name || "item"}' harus lebih besar dari 0.`;
    }
    return "";
  };

  const saveCreate = async () => {
    if (busy || !canCreate) return;
    const v = validateCreate();
    if (v) { setFormErr(v); return; }
    setBusy(true);
    setFormErr("");
    setErr("");
    try {
      const payload = {
        supplier: supplier.trim(),
        receipt_date: recvDate,
        notes,
        po_number: reference.trim(),
        lines: lines.map((l, idx) => ({
          line_idx: idx,
          master_item_id: l.master_item_id,
          accurate_item_id: l.accurate_item_id,
          item_name: l.item_name,
          item_code: l.item_code,
          unit: l.unit,
          received_qty: l.received_qty,
          notes: l.notes,
        })),
        idempotency_key: newIdempotencyKey(),
      };
      const res = await api.createPurchaseReceipt(payload);
      const num = res.receipt?.receipt_number || "";
      setOk(`Penerimaan ${num} tersimpan`);
      setView("list");
      setMainTab("history");
      await loadHistory();
      await refreshSideData();
    } catch (ex) {
      setFormErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const savePoReceipt = async () => {
    if (recvBusy || !recvPreview) return;
    const qtyLines = (recvPreview.lines || []).map((l) => ({
      purchase_order_line_id: l.purchase_order_line_id,
      received_qty: recvQty[l.purchase_order_line_id],
    })).filter((l) => Number(l.received_qty) > 0);
    if (!qtyLines.length) {
      setRecvErr("Minimal satu baris dengan qty diterima > 0.");
      return;
    }
    if (recvWarehouseChoice.blocked || !recvWarehouseId) {
      setRecvErr(recvWarehouseChoice.explicit
        ? "Pilih gudang sebelum menyimpan penerimaan."
        : "Penerimaan tidak dapat disimpan: belum ada gudang aktif.");
      return;
    }
    setRecvBusy(true);
    setRecvErr("");
    setRecvMsg("");
    try {
      const res = await api.createPurchaseGoodsReceipt({
        purchase_order_id: recvPreview.purchase_order_id,
        receipt_date: recvDate,
        warehouse_id: recvWarehouseId,
        lines: qtyLines,
        idempotency_key: newIdempotencyKey(),
      });
      setRecvMsg(`Penerimaan terhadap PO tercatat: ${res.receipt.receipt_number}`);
      await loadPoOrders();
      await refreshSideData();
      closeReceive();
      setOk(`Penerimaan ${res.receipt.receipt_number} tersimpan`);
    } catch (ex) {
      setRecvErr(errDetail(ex));
    } finally {
      setRecvBusy(false);
    }
  };

  const pageCount = data?.page_count || 1;
  const headerComplete = Boolean(recvDate && supplier && lines.some((l) => l.master_item_id && Number(l.received_qty) > 0));

  const historyRows = useMemo(() => {
    const items = data?.items || [];
    const accurateRows = items.map((it) => ({
      key: `acc-${it.accurate_receipt_id}`,
      kind: String(it.source || "") === "manual" ? "manual" : "accurate",
      date: it.receipt_date,
      number: it.receipt_number || it.accurate_receipt_id,
      supplier: it.supplier || "—",
      po_number: it.po_number || "—",
      line_count: it.line_count ?? (it.lines || []).length,
      total_qty: it.total_qty,
      status: it.valid ? (it.status || "VALID") : (it.status || "INVALID"),
      last_synced_at: it.last_synced_at,
      raw: it,
    }));
    const poRows = (poReceipts || []).map((r) => ({
      key: `po-${r.id}`,
      kind: "po",
      date: r.receipt_date,
      number: r.receipt_number,
      supplier: r.supplier || "—",
      po_number: r.po_number || "—",
      line_count: (r.lines || []).length,
      total_qty: (r.lines || []).reduce((s, l) => s + Number(l.received_qty || 0), 0),
      status: r.status || (r.cancelled ? "CANCELLED" : "ACTIVE"),
      last_synced_at: null,
      raw: r,
    }));
    let merged = [...accurateRows, ...poRows];
    if (sourceFilter === "manual") merged = merged.filter((r) => r.kind === "manual");
    else if (sourceFilter === "accurate") merged = merged.filter((r) => r.kind === "accurate");
    else if (sourceFilter === "po") merged = merged.filter((r) => r.kind === "po");
    merged.sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
    return merged;
  }, [data?.items, poReceipts, sourceFilter]);
  const poDetailReceipts = useMemo(
    () => (poReceipts || []).filter(
      (receipt) => String(receipt.purchase_order_id || "") === String(poDetail?.id || ""),
    ),
    [poDetail?.id, poReceipts],
  );


  const openCancelReceipt = (receipt) => {
    setCancelErr("");
    setCancelTarget(receipt);
  };

  const confirmCancelReceipt = async () => {
    if (!cancelTarget || cancelBusy) return;
    setCancelBusy(true);
    setCancelErr("");
    try {
      const cancelledPoId = cancelTarget.purchase_order_id;
      if (String(cancelTarget.source || "").toLowerCase() === "manual") {
        await api.cancelManualPurchaseReceipt(
          cancelTarget.accurate_receipt_id || cancelTarget.id,
          "",
        );
      } else {
        await api.cancelPurchaseGoodsReceipt(cancelTarget.id, "");
      }
      setOk(`Penerimaan ${cancelTarget.receipt_number || ""} berhasil dibatalkan.`);
      setCancelTarget(null);
      setOpenId(null);
      await refreshSideData();
      if (cancelledPoId && String(poDetail?.id || "") === String(cancelledPoId)) {
        await openPoReceivingDetail(cancelledPoId);
      }
      if (mainTab === "history") await loadHistory();
      if (mainTab === "receive-po") await loadPoOrders();
    } catch (ex) {
      setCancelErr(errDetail(ex));
    } finally {
      setCancelBusy(false);
    }
  };

  const refreshAfterAccounting = async (receipt) => {
    await refreshSideData();
    if (receipt?.purchase_order_id && String(poDetail?.id || "") === String(receipt.purchase_order_id)) {
      await openPoReceivingDetail(receipt.purchase_order_id);
    }
    if (mainTab === "history") await loadHistory();
    if (mainTab === "receive-po") await loadPoOrders();
  };

  const openPostAccounting = (receipt) => {
    setAcctErr("");
    setReverseReason("");
    setAcctTarget({ receipt, action: "post" });
  };

  const openReverseAccounting = (receipt) => {
    setAcctErr("");
    setReverseReason("");
    setAcctTarget({ receipt, action: "reverse" });
  };

  const confirmAccountingAction = async () => {
    if (!acctTarget || acctBusy) return;
    const receipt = acctTarget.receipt;
    if (acctTarget.action === "reverse" && reverseReason.trim().length < 3) {
      setAcctErr("Alasan reversal wajib diisi minimal 3 karakter.");
      return;
    }
    setAcctBusy(true);
    setAcctErr("");
    try {
      if (acctTarget.action === "post") {
        const res = await api.postPurchaseGoodsReceiptAccounting(receipt.id);
        setOk(`Penerimaan ${res.receipt?.receipt_number || receipt.receipt_number || ""} diposting ke accounting.`);
      } else {
        const res = await api.reversePurchaseGoodsReceiptAccounting(receipt.id, reverseReason.trim());
        setOk(`Penerimaan ${res.receipt?.receipt_number || receipt.receipt_number || ""} di-reverse dari accounting.`);
      }
      setAcctTarget(null);
      setReverseReason("");
      await refreshAfterAccounting(receipt);
    } catch (ex) {
      setAcctErr(errDetail(ex));
    } finally {
      setAcctBusy(false);
    }
  };

  return (
    <div className="prod-page master-items-page purchase-page" data-testid="penerimaan-barang-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="penerimaan-title">Penerimaan Barang</h1>
          <p className="subhead">Catat barang yang diterima dari pesanan pembelian atau penerimaan langsung.</p>
        </div>
        <div className="stock-heading-actions purchase-toolbar-actions">
          {view === "list" && canCreate && (
            <button className="primary" onClick={openCreate} data-testid="btn-buat-penerimaan">
              <Plus size={14} /> Penerimaan Langsung
            </button>
          )}
          {view !== "list" && (
            <button className="ghost" type="button" onClick={backToList} data-testid="btn-cancel-penerimaan">Kembali ke daftar</button>
          )}
          {view === "list" && (canSync || canExport) && (
            <div className="purchase-more">
              <button
                type="button"
                className="ghost"
                data-testid="penerimaan-more-button"
                onClick={() => setMoreOpen((v) => !v)}
                aria-expanded={moreOpen}
              >
                <MoreHorizontal size={14} /> Lainnya
              </button>
              {moreOpen && (
                <div className="purchase-more-menu" data-testid="penerimaan-more-menu">
                  {canSync && (
                    <button
                      type="button"
                      onClick={doSync}
                      disabled={syncing}
                      data-testid="penerimaan-sync-button"
                    >
                      <RefreshCw size={14} className={syncing ? "spin" : ""} />
                      {syncing ? "Menyinkronkan…" : "Sinkronkan dari Accurate"}
                    </button>
                  )}
                  {canExport && (
                    <button
                      type="button"
                      onClick={download}
                      disabled={exporting}
                      data-testid="penerimaan-export-button"
                    >
                      <Download size={14} /> Export
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <PurchasingFlowNav />

      {(err || syncErr || formErr) && view === "list" && (
        <div className="purchase-error-panel" data-testid="penerimaan-error" role="alert">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={16} color="#a53f2b" />
            <span>{formErr || syncErr || err}</span>
          </div>
          {err && !formErr && (
            <button
              type="button"
              className="ghost sm"
              data-testid="penerimaan-retry"
              onClick={() => (mainTab === "history" ? loadHistory() : loadPoOrders())}
            >
              Coba Lagi
            </button>
          )}
        </div>
      )}
      {ok && !syncErr && <p className="purchase-inline-ok" data-testid="penerimaan-success">{ok}</p>}
      {syncMsg && !syncErr && (
        <p className="muted small stock-sync-summary" data-testid="penerimaan-sync-summary">{syncMsg}</p>
      )}

      {view === "create" && canCreate && (
        <div className="card purchase-detail-card" data-testid="penerimaan-create-form">
          <h2 className="purchase-form-title">Penerimaan Langsung</h2>
          <p className="purchase-form-helper" data-testid="penerimaan-manual-stock-hint">
            Catat penerimaan barang tanpa mengacu pada PO.
          </p>
          <div className="purchase-blocked-note" data-testid="penerimaan-direct-stock-gap">
            Catatan: penerimaan langsung saat ini dicatat sebagai dokumen saja.
            Untuk penerimaan yang mengikuti pesanan, gunakan tab Terima dari PO.
          </div>
          <FormProgress
            percentage={Math.round((headerComplete ? 1 : 0) * 100)}
            completed={headerComplete ? 1 : 0}
            total={1}
          />
          {formErr && (
            <div className="purchase-error-panel" role="alert" data-testid="penerimaan-form-error">
              <span>{formErr}</span>
            </div>
          )}
          <FormSection
            id="recv-header"
            title="Informasi Penerimaan"
            description="Nomor diisi otomatis saat simpan."
            status={computeStatus([{ value: recvDate, isRequired: true }, { value: supplier, isRequired: true }])}
          >
            <div className="purchase-form-grid">
              <FormField id="recv-date" label="Tanggal Penerimaan" isRequired value={recvDate}>
                <input type="date" value={recvDate} onChange={(e) => setRecvDate(e.target.value)} data-testid="penerimaan-date" />
              </FormField>
              <FormField id="recv-supplier" label="Supplier" isRequired value={supplier}>
                <input
                  value={supplier}
                  onChange={(e) => setSupplier(e.target.value)}
                  data-testid="penerimaan-supplier"
                  placeholder="Nama supplier"
                />
              </FormField>
              <FormField id="recv-ref" label="Referensi (opsional)" value={reference}>
                <input value={reference} onChange={(e) => setReference(e.target.value)} data-testid="penerimaan-reference" />
              </FormField>
              <FormField id="recv-notes" label="Keterangan" value={notes}>
                <input value={notes} onChange={(e) => setNotes(e.target.value)} data-testid="penerimaan-notes" />
              </FormField>
            </div>
          </FormSection>

          <FormSection
            id="recv-manual-lines"
            title="Item Diterima"
            status={lines.some((l) => l.master_item_id && Number(l.received_qty) > 0) ? "complete" : "incomplete"}
          >
            <div className="purchase-table-wrap">
              <table className="prod-table purchase-table purchase-lines-table" data-testid="penerimaan-lines">
                <colgroup>
                  <col className="col-item-line" />
                  <col className="col-code-line" />
                  <col className="col-unit-line" />
                  <col className="col-qty-line" />
                  <col className="col-action-line" />
                </colgroup>
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Kode</th>
                    <th>Satuan</th>
                    <th className="num">Qty</th>
                    <th>Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => (
                    <tr key={l.key} data-testid={`penerimaan-line-${i}`}>
                      <td>
                        <MasterItemSearchSelect
                          testId={`penerimaan-line-item-${i}`}
                          value={
                            l.master_item_id
                              ? {
                                  accurate_item_id: l.accurate_item_id,
                                  id: l.master_item_id,
                                  master_item_id: l.master_item_id,
                                  item_name: l.item_name,
                                  item_code: l.item_code,
                                  unit: l.unit,
                                }
                              : null
                          }
                          onChange={(item) => pickItem(l.key, item)}
                          placeholder="Cari Master Item…"
                        />
                      </td>
                      <td className="mono">{l.item_code || "—"}</td>
                      <td className="unit-cell">
                        <input value={l.unit} readOnly tabIndex={-1} data-testid={`penerimaan-line-unit-${i}`} />
                      </td>
                      <td className="qty-cell">
                        <input
                          type="number"
                          min="0"
                          value={l.received_qty}
                          onChange={(e) => setLine(l.key, { received_qty: e.target.value })}
                          data-testid={`penerimaan-line-qty-${i}`}
                        />
                      </td>
                      <td className="action-cell">
                        <button type="button" className="ghost sm" onClick={() => removeLine(l.key)} data-testid={`penerimaan-line-remove-${i}`}>Hapus</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button className="ghost purchase-add-line" type="button" onClick={addLine} data-testid="penerimaan-add-line">+ Tambah Item</button>
          </FormSection>

          <div className="purchase-form-actions">
            <button className="ghost" type="button" onClick={backToList} data-testid="penerimaan-form-cancel">Batal</button>
            <button className="primary" disabled={busy} onClick={saveCreate} data-testid="save-penerimaan">
              {busy ? "Menyimpan…" : "Simpan Penerimaan"}
            </button>
          </div>
        </div>
      )}

      {view === "list" && (
        <>
          <div className="purchase-tabs" role="tablist" aria-label="Mode penerimaan">
            <button
              type="button"
              role="tab"
              className={`purchase-tab${mainTab === "receive-po" ? " is-active" : ""}`}
              aria-selected={mainTab === "receive-po"}
              data-testid="tab-penerimaan-po"
              onClick={() => { setMainTab("receive-po"); setPoDetail(null); }}
            >
              Terima dari PO
            </button>
            <button
              type="button"
              role="tab"
              className={`purchase-tab${mainTab === "direct" ? " is-active" : ""}`}
              aria-selected={mainTab === "direct"}
              data-testid="tab-penerimaan-langsung"
              onClick={() => { if (canCreate) openCreate(); else setMainTab("direct"); }}
            >
              Penerimaan Langsung
            </button>
            <button
              type="button"
              role="tab"
              className={`purchase-tab${mainTab === "history" ? " is-active" : ""}`}
              aria-selected={mainTab === "history"}
              data-testid="tab-riwayat-penerimaan"
              onClick={() => { setMainTab("history"); setPoDetail(null); }}
            >
              Riwayat
            </button>
          </div>

          {mainTab === "receive-po" && view === "list" && (
            <p className="purchase-tab-helper" data-testid="tab-helper-receive-po">
              Pilih PO dan catat jumlah barang yang benar-benar diterima.
            </p>
          )}
          {mainTab === "history" && view === "list" && (
            <p className="purchase-tab-helper" data-testid="tab-helper-history">
              Lihat seluruh riwayat penerimaan dari PO, manual/langsung, dan Accurate.
            </p>
          )}

          {mainTab === "receive-po" && (
            <div data-testid="po-linked-receipts">
              <h2 className="sr-only" data-testid="po-receipts-heading">Penerimaan terhadap Pesanan Pembelian</h2>
              {poDetail ? (
                <div className="card purchase-detail-card" data-testid="penerimaan-po-detail">
                  <div className="purchase-detail-meta">
                    <div>
                      <span className="label">No PO</span>
                      <span className="value mono">{poDetail.po_number}</span>
                    </div>
                    <div>
                      <span className="label">Supplier</span>
                      <span className="value">{poDetail.supplier || "—"}</span>
                    </div>
                    <div>
                      <span className="label">Tanggal</span>
                      <span className="value">{poDetail.order_date || "—"}</span>
                    </div>
                    <div>
                      <span className="label">Status</span>
                      <span className={purchaseStatusPillClass(poDetail.receiving_status)}>
                        {shortReceivingStatus(poDetail.receiving_status)}
                      </span>
                    </div>
                  </div>
                  <div className="purchase-table-wrap">
                    <table className="prod-table" data-testid="penerimaan-po-detail-lines">
                      <thead>
                        <tr>
                          <th>Item</th>
                          <th>Kode</th>
                          <th>Satuan</th>
                          <th className="num">Qty Pesan</th>
                          <th className="num">Diterima</th>
                          <th className="num">Sisa</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(poDetail.lines || []).map((l) => (
                          <tr key={l.line_id}>
                            <td>{l.item_name}</td>
                            <td className="mono">{l.item_code || "—"}</td>
                            <td>{l.unit || "pcs"}</td>
                            <td className="num">{fmtQty(l.ordered_qty)}</td>
                            <td className="num">{fmtQty(l.cumulative_received_qty)}</td>
                            <td className="num">{fmtQty(l.remaining_receivable_qty)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="purchase-receipt-list" data-testid="penerimaan-po-receipts">
                    <h3 className="purchase-form-title">Penerimaan Barang</h3>
                    {poDetailReceipts.length === 0 ? (
                      <p className="muted small">Belum ada Penerimaan Barang untuk PO ini.</p>
                    ) : poDetailReceipts.map((receipt) => {
                      const cancelled = receipt.cancelled === true || receipt.status === "CANCELLED";
                      return (
                        <div
                          className="purchase-receipt-summary"
                          key={receipt.id}
                          data-testid={`po-detail-receipt-${receipt.id}`}
                        >
                          <div>
                            <span className="mono">{receipt.receipt_number || receipt.id}</span>
                            <span className={`purchase-status-badge ${cancelled ? "pill-cancel" : "pill-ok"}`}>
                              {cancelled ? "DIBATALKAN" : "ACTIVE"}
                            </span>
                          </div>
                          <span className="muted small">{receipt.receipt_date || "—"}</span>
                          <ReceiptAccountingBlock
                            receipt={receipt}
                            testIdPrefix="po-detail"
                            canPostAccounting={canPostAccounting}
                            canReverseAccounting={canReverseAccounting}
                            onPost={openPostAccounting}
                            onReverse={openReverseAccounting}
                          />
                          {canReceivePo && !cancelled && (
                            <button
                              type="button"
                              className="ghost danger"
                              data-testid={`btn-batalkan-po-receipt-${receipt.id}`}
                              disabled={receipt.can_cancel === false || receipt.accounting_status === "posted"}
                              title={
                                receipt.accounting_status === "posted"
                                  ? "Gunakan Reverse Accounting."
                                  : (receipt.can_cancel === false ? (receipt.cancel_block_reason || "") : undefined)
                              }
                              onClick={() => openCancelReceipt(receipt)}
                            >
                              Batalkan Penerimaan
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  <div className="purchase-detail-actions">
                    <button type="button" className="ghost" onClick={() => setPoDetail(null)} data-testid="btn-back-po-list">
                      Kembali ke daftar
                    </button>
                    {canReceivePo && Number(poDetail.receiving_progress?.remaining_qty) > 0 && (
                      <button
                        type="button"
                        className="primary"
                        data-testid={`btn-terima-po-${poDetail.id}`}
                        onClick={() => openReceivePo(poDetail.id)}
                      >
                        Terima Barang
                      </button>
                    )}
                  </div>
                </div>
              ) : (
                <>
                  <div className="purchase-search-bar is-toolbar">
                    <form className="search-inline" onSubmit={(e) => { e.preventDefault(); loadPoOrders(); }}>
                      <Search size={14} />
                      <input
                        value={poQ}
                        onChange={(e) => setPoQ(e.target.value)}
                        placeholder="Cari no PO / supplier"
                        data-testid="penerimaan-po-search"
                      />
                    </form>
                  </div>
                  {poErr && (
                    <div className="purchase-error-panel" role="alert">
                      <span>{poErr}</span>
                      <button type="button" className="ghost sm" onClick={loadPoOrders}>Coba Lagi</button>
                    </div>
                  )}
                  {poDetailLoading && <p className="muted small">Memuat detail…</p>}
                  <div className="purchase-table-wrap">
                    <table className="prod-table purchase-table purchase-table-recv-po" data-testid="penerimaan-po-table">
                      <colgroup>
                        <col className="col-po" />
                        <col className="col-supplier" />
                        <col className="col-date" />
                        <col className="col-qty" />
                        <col className="col-qty" />
                        <col className="col-qty" />
                        <col className="col-status" />
                      </colgroup>
                      <thead>
                        <tr>
                          <th>No PO</th>
                          <th>Supplier</th>
                          <th>Tanggal</th>
                          <th className="num">Qty Pesan</th>
                          <th className="num">Diterima</th>
                          <th className="num">Sisa</th>
                          <th className="cell-status">Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {poLoading && <tr className="purchase-empty-row"><td colSpan={7} className="muted small">Memuat…</td></tr>}
                        {!poLoading && poOrders.length === 0 && (
                          <tr className="purchase-empty-row">
                            <td colSpan={7} className="muted small" data-testid="penerimaan-po-empty">Belum ada pesanan untuk diterima.</td>
                          </tr>
                        )}
                        {!poLoading && poOrders.map((o) => {
                          const prog = o.receiving_progress || {};
                          const status = shortReceivingStatus(o.receiving_status);
                          return (
                            <tr
                              key={o.id}
                              {...purchaseRowInteractionProps(() => openPoReceivingDetail(o.id), {
                                testId: `penerimaan-po-row-${o.id}`,
                              })}
                            >
                              <td className="mono cell-clip">{o.po_number}</td>
                              <td className="cell-clip" title={o.supplier || ""}>{o.supplier || "—"}</td>
                              <td className="nowrap">{o.order_date || "—"}</td>
                              <td className="num">{fmtQty(prog.ordered_qty)}</td>
                              <td className="num">{fmtQty(prog.received_qty)}</td>
                              <td className="num">{fmtQty(prog.remaining_qty)}</td>
                              <td className="cell-status">
                                <span className={`purchase-status-badge ${purchaseStatusPillClass(o.receiving_status)}`}>
                                  {status}
                                </span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}

          {mainTab === "history" && (
            <div data-testid="accurate-receipts-section">
              <h2 className="sr-only" data-testid="accurate-receipts-heading">A. Daftar Penerimaan Accurate / Manual</h2>
              <p className="muted small stock-last-sync" data-testid="penerimaan-last-sync">
                Terakhir sinkronisasi Accurate: {data?.last_synced_at ? formatDate(data.last_synced_at) : "belum"}
              </p>
              <div className="purchase-source-filters" data-testid="penerimaan-source-filters">
                {[
                  { id: "all", label: "Semua" },
                  { id: "po", label: "PO" },
                  { id: "manual", label: "Manual" },
                  { id: "accurate", label: "Accurate" },
                ].map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    className={sourceFilter === f.id ? "is-active" : ""}
                    data-testid={`source-filter-${f.id}`}
                    onClick={() => setSourceFilter(f.id)}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
              <div className="purchase-search-bar is-toolbar">
                <form className="search-inline" onSubmit={doSearch}>
                  <Search size={14} />
                  <input
                    type="text"
                    value={qInput}
                    onChange={(e) => setQInput(e.target.value)}
                    placeholder="Cari no penerimaan / item"
                    data-testid="penerimaan-search"
                    aria-label="Cari penerimaan barang"
                  />
                  {q && (
                    <button type="button" className="ghost sm" onClick={clearSearch} aria-label="Bersihkan">
                      <X size={12} />
                    </button>
                  )}
                </form>
                <select
                  value={pageSize}
                  onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
                  data-testid="penerimaan-page-size"
                >
                  <option value={25}>25 / halaman</option>
                  <option value={50}>50 / halaman</option>
                  <option value={100}>100 / halaman</option>
                </select>
              </div>

              <div className="purchase-table-wrap">
                <table className="prod-table purchase-table purchase-table-history" data-testid="penerimaan-table">
                  <colgroup>
                    <col className="col-receipt" />
                    <col className="col-date" />
                    <col className="col-source" />
                    <col className="col-supplier-po" />
                    <col className="col-qty-wide" />
                    <col className="col-status" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>No Penerimaan</th>
                      <th>Tanggal</th>
                      <th className="cell-source">Sumber</th>
                      <th>Supplier / PO</th>
                      <th className="num">Total Qty</th>
                      <th className="cell-status">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {loading && <tr className="purchase-empty-row"><td colSpan={6} className="muted small">Memuat…</td></tr>}
                    {!loading && historyRows.length === 0 && (
                      <tr className="purchase-empty-row">
                        <td colSpan={6} className="muted small" data-testid="penerimaan-empty">Belum ada riwayat penerimaan.</td>
                      </tr>
                    )}
                    {!loading && historyRows.map((row) => {
                      if (row.kind === "po") {
                        const badge = receiptSourceBadge("po");
                        const supplierPo = row.po_number && row.po_number !== "—"
                          ? `${row.supplier} · ${row.po_number}`
                          : row.supplier;
                        const open = openId === row.key;
                        return (
                          <PoHistoryRow
                            key={row.key}
                            row={row}
                            badge={badge}
                            supplierPo={supplierPo}
                            open={open}
                            onToggle={() => setOpenId(open ? null : row.key)}
                            canCreateInvoice={canCreateInvoice}
                            canCancel={canReceivePo}
                            onCancel={openCancelReceipt}
                            canPostAccounting={canPostAccounting}
                            canReverseAccounting={canReverseAccounting}
                            onPostAccounting={openPostAccounting}
                            onReverseAccounting={openReverseAccounting}
                          />
                        );
                      }
                      const it = row.raw;
                      const open = openId === it.accurate_receipt_id;
                      return (
                        <FragmentRow
                          key={row.key}
                          it={it}
                          open={open}
                          onToggle={() => setOpenId(open ? null : it.accurate_receipt_id)}
                          canCancel={canReceivePo && row.kind === "manual"}
                          onCancel={openCancelReceipt}
                        />
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {pageCount > 1 && sourceFilter !== "po" && (
                <div className="master-pager" data-testid="penerimaan-pager">
                  <button className="ghost sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Sebelumnya</button>
                  <span className="muted small">{page} / {pageCount}</span>
                  <button className="ghost sm" disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)}>Berikutnya</button>
                </div>
              )}

            </div>
          )}
        </>
      )}

      {receiveOpen && (
        <div className="modal" role="dialog" aria-modal="true" data-testid="po-receive-modal" onClick={closeReceive}>
          <div className="modal-inner modal-wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <h3>Terima Barang</h3>
                <p className="purchase-modal-helper">Masukkan jumlah barang yang diterima pada tanggal ini.</p>
              </div>
              <button type="button" className="ghost sm" onClick={closeReceive} aria-label="Tutup">×</button>
            </div>
            <div className="purchase-modal-body" data-testid="po-receive-form">
              {recvErr && (
                <div className="purchase-error-panel" role="alert">
                  <AlertCircle size={16} color="#a53f2b" />
                  <span>{recvErr}</span>
                </div>
              )}
              {recvMsg && <p className="purchase-inline-ok">{recvMsg}</p>}
              {recvPreview ? (
                <>
                  <div className="purchase-detail-meta purchase-recv-meta">
                    <div>
                      <span className="label">No PO</span>
                      <span className="value mono">{recvPreview.po_number}</span>
                    </div>
                    <div>
                      <span className="label">Supplier</span>
                      <span className="value">{recvPreview.supplier || "—"}</span>
                    </div>
                    <div>
                      <span className="label">Tanggal Terima</span>
                      <input type="date" value={recvDate} onChange={(e) => setRecvDate(e.target.value)} />
                    </div>
                    <div>
                      <span className="label">Gudang</span>
                      {recvWarehouseChoice.explicit ? (
                        <select
                          value={recvWarehouseId}
                          onChange={(e) => setRecvWarehouseId(e.target.value)}
                          data-testid="receive-warehouse"
                        >
                          <option value="">Pilih gudang</option>
                          {recvWarehouseChoice.rows.map((row) => (
                            <option key={row.id} value={row.id}>{row.code} — {row.name}</option>
                          ))}
                        </select>
                      ) : (
                        <span className="value" data-testid="receive-warehouse">
                          {recvWarehouseChoice.rows[0]
                            ? `${recvWarehouseChoice.rows[0].code} — ${recvWarehouseChoice.rows[0].name}`
                            : "Belum ada gudang aktif"}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="purchase-table-wrap purchase-modal-table-wrap">
                    <table className="prod-table purchase-table purchase-table-recv-modal">
                      <colgroup>
                        <col className="col-item" />
                        <col className="col-qty" />
                        <col className="col-qty-wide" />
                        <col className="col-qty" />
                        <col className="col-recv-now" />
                      </colgroup>
                      <thead>
                        <tr>
                          <th>Item</th>
                          <th className="num">Qty Pesan</th>
                          <th className="num th-wrap">Sudah Diterima</th>
                          <th className="num">Sisa</th>
                          <th className="num th-wrap">Terima Sekarang</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(recvPreview.lines || []).map((l) => (
                          <tr key={l.purchase_order_line_id}>
                            <td className="cell-clip" title={l.item_name}>{l.item_name}</td>
                            <td className="num">{fmtQty(l.ordered_qty)}</td>
                            <td className="num">{fmtQty(l.cumulative_received_qty)}</td>
                            <td className="num">{fmtQty(l.remaining_receivable_qty)}</td>
                            <td className="qty-cell">
                              <input
                                type="number"
                                min="0"
                                value={recvQty[l.purchase_order_line_id] ?? ""}
                                onChange={(e) => setRecvQty((p) => ({ ...p, [l.purchase_order_line_id]: e.target.value }))}
                                data-testid="receive-qty-input"
                              />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : (
                <p className="muted small">Memuat detail pesanan…</p>
              )}
            </div>
            <div className="purchase-modal-actions">
              <button className="ghost" type="button" onClick={closeReceive} disabled={recvBusy}>Batal</button>
              <button
                className="primary"
                disabled={recvBusy || !recvPreview || recvWarehouseChoice.blocked || (recvWarehouseChoice.explicit && !recvWarehouseId)}
                data-testid="save-po-receipt"
                onClick={savePoReceipt}
              >
                {recvBusy ? "Menyimpan…" : "Simpan Penerimaan"}
              </button>
            </div>
          </div>
        </div>
      )}

      <PurchaseCancelModal
        open={Boolean(cancelTarget)}
        title="Batalkan Penerimaan Barang?"
        body="Dokumen tidak dihapus. Jika stock sudah bertambah, sistem akan menangani reversal secara otomatis."
        busy={cancelBusy}
        error={cancelErr}
        confirmLabel="Batalkan"
        busyLabel="Membatalkan…"
        onCancel={() => {
          if (!cancelBusy) {
            setCancelTarget(null);
          }
        }}
        onConfirm={confirmCancelReceipt}
      />

      {acctTarget?.action === "post" && acctTarget.receipt && (
        <div className="modal" role="dialog" aria-modal="true" data-testid="gr-post-accounting-modal">
          <div className="modal-inner" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>Post Accounting Penerimaan Barang?</h3>
              <button type="button" className="ghost sm" disabled={acctBusy} onClick={() => setAcctTarget(null)}>×</button>
            </div>
            <p className="purchase-modal-helper">Jurnal: Dr Persediaan, Cr Purchase Clearing / GRNI. Tanggal jurnal = tanggal penerimaan.</p>
            <dl className="purchase-detail-meta">
              <div>
                <span className="label">No Penerimaan</span>
                <span className="value mono" data-testid="gr-post-receipt-number">{acctTarget.receipt.receipt_number}</span>
              </div>
              <div>
                <span className="label">Tanggal</span>
                <span className="value" data-testid="gr-post-receipt-date">{acctTarget.receipt.receipt_date || "—"}</span>
              </div>
              <div>
                <span className="label">Nilai Persediaan</span>
                <span className="value" data-testid="gr-post-eligible-amount">{formatRupiah(acctTarget.receipt.accounting_amount)}</span>
              </div>
            </dl>
            {(acctTarget.receipt.accounting_warnings || []).length > 0 && (
              <p className="muted small" data-testid="gr-post-partial-warning">
                {(acctTarget.receipt.accounting_warnings[0] || {}).message
                  || "Sebagian baris tidak dikapitalisasi ke Persediaan."}
              </p>
            )}
            {acctErr ? <p className="purchase-inline-err" data-testid="gr-accounting-error">{acctErr}</p> : null}
            <div className="purchase-modal-actions">
              <button type="button" className="ghost" disabled={acctBusy} onClick={() => setAcctTarget(null)}>Batal</button>
              <button type="button" className="primary" data-testid="btn-confirm-post-gr-accounting" disabled={acctBusy} onClick={confirmAccountingAction}>
                {acctBusy ? "Memproses…" : "Post Accounting"}
              </button>
            </div>
          </div>
        </div>
      )}

      {acctTarget?.action === "reverse" && acctTarget.receipt && (
        <div className="modal" role="dialog" aria-modal="true" data-testid="gr-reverse-accounting-modal">
          <div className="modal-inner" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>Reverse Accounting Penerimaan Barang?</h3>
              <button type="button" className="ghost sm" disabled={acctBusy} onClick={() => setAcctTarget(null)}>×</button>
            </div>
            <p className="purchase-modal-helper">
              Reversal juga membatalkan Penerimaan Barang dan
              mengembalikan stock fisik bila receipt tersebut
              sebelumnya menambah stock.
            </p>
            <label className="purchase-field">
              <span>Alasan</span>
              <textarea
                value={reverseReason}
                onChange={(e) => setReverseReason(e.target.value)}
                data-testid="gr-reverse-reason-input"
                rows={3}
              />
            </label>
            {acctErr ? <p className="purchase-inline-err" data-testid="gr-reverse-accounting-error">{acctErr}</p> : null}
            <div className="purchase-modal-actions">
              <button type="button" className="ghost" disabled={acctBusy} onClick={() => setAcctTarget(null)}>Batal</button>
              <button type="button" className="primary" data-testid="btn-confirm-reverse-gr-accounting" disabled={acctBusy} onClick={confirmAccountingAction}>
                {acctBusy ? "Memproses…" : "Reverse Accounting"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ReceiptAccountingBlock({
  receipt,
  testIdPrefix,
  canPostAccounting,
  canReverseAccounting,
  onPost,
  onReverse,
}) {
  const status = String(receipt?.accounting_status || "not_posted").toLowerCase();
  const warnings = receipt?.accounting_warnings || [];
  const recovering = receipt?.accounting_recovery_required === true;
  return (
    <div className="purchase-detail-meta" data-testid={`${testIdPrefix}-accounting-${receipt.id}`}>
      <div>
        <span className="label">Accounting</span>
        <span className="value" data-testid={`${testIdPrefix}-accounting-status-${receipt.id}`}>{accountingLabel(status)}</span>
      </div>
      {receipt?.accounting_journal_id ? (
        <div>
          <span className="label">Journal</span>
          <span className="value mono" data-testid={`${testIdPrefix}-accounting-journal-${receipt.id}`}>{receipt.accounting_journal_id}</span>
        </div>
      ) : null}
      <div>
        <span className="label">Nilai Persediaan</span>
        <span className="value" data-testid={`${testIdPrefix}-accounting-amount-${receipt.id}`}>{formatRupiah(receipt?.accounting_amount)}</span>
      </div>
      {warnings.length > 0 && (
        <p className="muted small" data-testid={`${testIdPrefix}-accounting-warning-${receipt.id}`}>
          {warnings[0].message || "Sebagian baris tidak dikapitalisasi ke Persediaan."}
        </p>
      )}
      {recovering && (
        <p className="purchase-inline-err" data-testid={`${testIdPrefix}-accounting-recovery-${receipt.id}`}>
          Reversal Accounting belum selesai. Diperlukan pemulihan.
        </p>
      )}
      <div className="purchase-detail-actions">
        {canPostAccounting && status === "not_posted" && !recovering && (
          <button
            type="button"
            className="primary"
            data-testid={`btn-post-gr-accounting-${receipt.id}`}
            disabled={receipt.can_post_accounting === false}
            title={receipt.can_post_accounting === false ? (receipt.accounting_post_block_reason || "") : undefined}
            onClick={(e) => { e.stopPropagation(); onPost?.(receipt); }}
          >
            Post Accounting
          </button>
        )}
        {canReverseAccounting && status === "posted" && !recovering && (
          <button
            type="button"
            className="ghost"
            data-testid={`btn-reverse-gr-accounting-${receipt.id}`}
            disabled={receipt.can_reverse_accounting === false}
            onClick={(e) => { e.stopPropagation(); onReverse?.(receipt); }}
          >
            Reverse Accounting
          </button>
        )}
        {canReverseAccounting && recovering && (
          <button
            type="button"
            className="primary"
            data-testid={`btn-retry-reverse-gr-accounting-${receipt.id}`}
            onClick={(e) => { e.stopPropagation(); onReverse?.(receipt); }}
          >
            Retry Reverse Accounting
          </button>
        )}
      </div>
    </div>
  );
}

function FragmentRow({ it, open, onToggle, canCancel, onCancel }) {
  const isManual = String(it.source || "") === "manual";
  const cancelled = it.cancelled === true || it.status === "CANCELLED";
  const badge = receiptSourceBadge(isManual ? "manual" : "accurate");
  const supplier = it.supplier || "—";
  const po = it.po_number ? String(it.po_number) : "";
  const supplierPo = po ? `${supplier} · ${po}` : supplier;
  return (
    <>
      <tr
        {...purchaseRowInteractionProps(onToggle, {
          testId: `penerimaan-row-${it.accurate_receipt_id}`,
          selected: open,
        })}
      >
        <td className="mono cell-clip">{it.receipt_number || it.accurate_receipt_id}</td>
        <td className="nowrap">{it.receipt_date || "—"}</td>
        <td className="cell-source"><span className={`purchase-status-badge ${badge.className}`}>{badge.label}</span></td>
        <td className="cell-clip" title={supplierPo}>{supplierPo}</td>
        <td className="num mono">{fmtQty(it.total_qty)}</td>
        <td className="cell-status">
          <span className="purchase-status-badge">
            {cancelled ? "DIBATALKAN" : (it.valid ? (it.status || "ACTIVE") : (it.status || "INVALID"))}
          </span>
        </td>
      </tr>
      {open && (
        <tr className="penerimaan-detail-row" data-testid={`penerimaan-detail-${it.accurate_receipt_id}`}>
          <td colSpan={6}>
            <p className="muted small" data-testid={`penerimaan-detail-meta-${it.accurate_receipt_id}`}>
              {isManual ? "Sumber: Manual" : "Sumber: Accurate"}
              {it.supplier ? ` · Supplier: ${it.supplier}` : ""}
              {it.po_number ? ` · PO: ${it.po_number}` : ""}
              {it.notes ? ` · ${it.notes}` : ""}
              {!isManual ? ` · Sync: ${it.last_synced_at ? formatDate(it.last_synced_at) : "—"}` : ""}
            </p>
            <table className="prod-table purchase-table penerimaan-lines">
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Nama Item</th>
                  <th>Satuan</th>
                  <th className="num">Qty Diterima</th>
                </tr>
              </thead>
              <tbody>
                {(it.lines || []).map((li) => (
                  <tr key={li.accurate_line_id || `${it.accurate_receipt_id}-${li.accurate_item_id}-${li.master_item_id}`}>
                    <td className="mono">{li.item_code || "—"}</td>
                    <td>{li.item_name || "—"}</td>
                    <td>{li.unit || "—"}</td>
                    <td className="num mono">{fmtQty(li.received_qty)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <PurchaseDocuments
              entityType="purchase_goods_receipt"
              entityId={it.id || it.accurate_receipt_id}
              canEdit={false}
              hint="Foto / bukti barang diterima."
              testId={`receipt-documents-${it.accurate_receipt_id || it.id || "acc"}`}
            />
            <PurchaseActivityHistory doc={it} testId={`receipt-activity-${it.accurate_receipt_id || it.id || "acc"}`} />
            {isManual && canCancel && !cancelled && (
              <div className="purchase-detail-actions">
                <button
                  type="button"
                  className="ghost danger"
                  data-testid={`btn-batalkan-manual-receipt-${it.accurate_receipt_id}`}
                  onClick={(e) => { e.stopPropagation(); onCancel?.(it); }}
                >
                  Batalkan Penerimaan
                </button>
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function PoHistoryRow({
  row,
  badge,
  supplierPo,
  open,
  onToggle,
  canCreateInvoice,
  canCancel,
  onCancel,
  canPostAccounting,
  canReverseAccounting,
  onPostAccounting,
  onReverseAccounting,
}) {
  const lines = row.raw?.lines || [];
  return (
    <>
      <tr
        {...purchaseRowInteractionProps(onToggle, {
          testId: `po-history-row-${row.raw.id}`,
          selected: open,
        })}
      >
        <td className="mono cell-clip">{row.number}</td>
        <td className="nowrap">{row.date || "—"}</td>
        <td className="cell-source"><span className={`purchase-status-badge ${badge.className}`}>{badge.label}</span></td>
        <td className="cell-clip" title={supplierPo}>{supplierPo}</td>
        <td className="num mono">{fmtQty(row.total_qty)}</td>
        <td className="cell-status"><span className="purchase-status-badge">{row.status}</span></td>
      </tr>
      {open && (
        <tr className="penerimaan-detail-row" data-testid={`po-history-detail-${row.raw.id}`}>
          <td colSpan={6}>
            <table className="prod-table purchase-table penerimaan-lines">
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Nama Item</th>
                  <th>Satuan</th>
                  <th className="num">Qty Diterima</th>
                </tr>
              </thead>
              <tbody>
                {lines.length === 0 && (
                  <tr><td colSpan={4} className="muted small">Tidak ada baris item.</td></tr>
                )}
                {lines.map((li) => (
                  <tr key={li.line_id || `${li.item_name}-${li.received_qty}`}>
                    <td className="mono">{li.item_code || "—"}</td>
                    <td>{li.item_name || "—"}</td>
                    <td>{li.unit || "—"}</td>
                    <td className="num mono">{fmtQty(li.received_qty)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <PurchaseDocuments
              entityType="purchase_goods_receipt"
              entityId={row.raw.id}
              canEdit={canCancel}
              hint="Foto / bukti barang diterima."
              testId={`receipt-documents-${row.raw.id}`}
            />
            <PurchaseActivityHistory doc={row.raw} testId={`receipt-activity-${row.raw.id}`} />
            <ReceiptAccountingBlock
              receipt={row.raw}
              testIdPrefix="po-history"
              canPostAccounting={canPostAccounting}
              canReverseAccounting={canReverseAccounting}
              onPost={onPostAccounting}
              onReverse={onReverseAccounting}
            />
            <div className="purchase-detail-actions">
              {canCancel && (
                <button
                  type="button"
                  className="ghost danger"
                  data-testid={`btn-batalkan-receipt-${row.raw.id}`}
                  disabled={row.raw.can_cancel === false || row.raw.accounting_status === "posted"}
                  title={
                    row.raw.accounting_status === "posted"
                      ? "Gunakan Reverse Accounting."
                      : (row.raw.can_cancel === false ? (row.raw.cancel_block_reason || "") : undefined)
                  }
                  onClick={(e) => { e.stopPropagation(); onCancel?.(row.raw); }}
                >
                  Batalkan
                </button>
              )}
              {canCreateInvoice && (
                <Link
                  className="primary"
                  data-testid={`btn-buat-faktur-receipt-${row.raw.id}`}
                  to={`/pembelian/faktur-pembelian?receipt_id=${row.raw.id}&action=create`}
                  onClick={(e) => e.stopPropagation()}
                >
                  Buat Faktur
                </Link>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
