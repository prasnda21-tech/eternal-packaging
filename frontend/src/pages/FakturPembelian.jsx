import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  Building2,
  CalendarDays,
  ClipboardList,
  CreditCard,
  FileText,
  Hash,
  Plus,
  Search,
  Wallet,
} from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  errDetail,
  formatRupiah,
  friendlyLoadError,
  newIdempotencyKey,
  purchaseStatusPillClass,
  purchasePaymentStatusLabel,
  todayYmd,
  purchaseRowInteractionProps,
  formatPurchaseDateTime,
  PURCHASE_TERM_PRESETS,
  addDaysYmd,
  termPresetFromDays,
  paymentTermLabel,
  dueDateLabel,
  formatPurchaseDateId,
  invoiceAgeLabel,
  canEditPurchaseInvoicePaymentTerms,
} from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";
import PurchasingFlowNav from "./PurchasingFlowNav";
import PurchaseCancelModal from "./PurchaseCancelModal";
import PurchaseActivityHistory from "./PurchaseActivityHistory";
import PurchaseDocuments from "./PurchaseDocuments";
import PurchasePaymentAjukanModal from "./PurchasePaymentAjukanModal";
import "./purchasePages.css";

function accountingLabel(status) {
  const raw = String(status || "not_posted").toLowerCase();
  if (raw === "posted") return "Posted";
  if (raw === "reversed") return "Reversed";
  return "Not Posted";
}

export default function FakturPembelian() {
  const [searchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_invoices.create");
  const canEditTerms = hasPerm("purchase_invoices.edit");
  const canPostAccounting = hasPerm("purchase_accounting.post");
  const canReverseAccounting = hasPerm("purchase_accounting.reverse");
  const queryPoId = searchParams.get("purchase_order_id") || "";
  const queryReceiptId = searchParams.get("receipt_id") || "";
  const queryInvoiceId = searchParams.get("id") || searchParams.get("invoice_id") || "";
  const wantCreate = searchParams.get("action") === "create" || Boolean(queryPoId) || Boolean(queryReceiptId);
  const [view, setView] = useState(wantCreate && canCreate ? "create" : "list");
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [detail, setDetail] = useState(null);
  const [cancelTarget, setCancelTarget] = useState(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelErr, setCancelErr] = useState("");
  const [ajukanOpen, setAjukanOpen] = useState(false);
  const [lastChatRoomId, setLastChatRoomId] = useState("");
  const [acctBusy, setAcctBusy] = useState(false);
  const [acctErr, setAcctErr] = useState("");
  const [postConfirm, setPostConfirm] = useState(false);
  const [reverseConfirm, setReverseConfirm] = useState(false);
  const [reverseReason, setReverseReason] = useState("");

  const [invoiceSource, setInvoiceSource] = useState(queryPoId && !queryReceiptId ? "purchase_order" : "receipt");
  const [eligiblePos, setEligiblePos] = useState([]);
  const [eligibleReceipts, setEligibleReceipts] = useState([]);
  const [poId, setPoId] = useState(canCreate ? queryPoId : "");
  const [receiptId, setReceiptId] = useState(canCreate ? queryReceiptId : "");
  const [preview, setPreview] = useState(null);
  const [invoiceDate, setInvoiceDate] = useState(todayYmd());
  const [termPreset, setTermPreset] = useState("NET30");
  const [paymentTermDays, setPaymentTermDays] = useState(30);
  const [dueDate, setDueDate] = useState(() => addDaysYmd(todayYmd(), 30));
  const [dueManual, setDueManual] = useState(false);
  const [qty, setQty] = useState({});
  const [precedesGoodsReceipt, setPrecedesGoodsReceipt] = useState(false);
  const [busy, setBusy] = useState(false);
  const [eligibleAdvances, setEligibleAdvances] = useState([]);
  const [allocAdvanceId, setAllocAdvanceId] = useState("");
  const [allocAmount, setAllocAmount] = useState("");
  const [allocErr, setAllocErr] = useState("");
  const [allocBusy, setAllocBusy] = useState(false);
  const [editingTerms, setEditingTerms] = useState(false);
  const [editTermPreset, setEditTermPreset] = useState("NET30");
  const [editTermDays, setEditTermDays] = useState(30);
  const [editDueDate, setEditDueDate] = useState("");
  const [editDueManual, setEditDueManual] = useState(false);
  const [editReason, setEditReason] = useState("");
  const [termsBusy, setTermsBusy] = useState(false);
  const [termsErr, setTermsErr] = useState("");
  const visualDemo10 = searchParams.get("visual_demo") === "10";

  /**
   * List fetch: blocking loader only when no cached rows.
   * Soft/background revalidation keeps existing rows (no "Memuat…" flicker).
   * On error, keep cached rows and show a light error.
   */
  const load = useCallback(async (opts = {}) => {
    const hasCached = rowsRef.current.length > 0;
    const soft = opts.soft === true || (opts.soft !== false && hasCached && opts.force !== true);
    if (!soft) setLoading(true);
    setErr("");
    try {
      setRows((await api.listPurchaseInvoices({ q })).invoices || []);
    } catch (ex) {
      setErr(friendlyLoadError(ex, "Tidak dapat memuat faktur. Periksa koneksi server lalu coba lagi."));
    } finally {
      setLoading(false);
    }
  }, [q]);
  useEffect(() => {
    if (view !== "list") return;
    load({ soft: rowsRef.current.length > 0 });
  }, [load, view]);

  const backToList = useCallback(() => {
    setView("list");
    // Soft revalidate via view effect; do not clear rows or force blocking loader.
  }, []);

  useEffect(() => {
    if (!queryInvoiceId || wantCreate) return;
    openDetail(queryInvoiceId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryInvoiceId]);

  const loadPoPreview = useCallback(async (id) => {
    if (!id) { setPreview(null); return; }
    try {
      const p = await api.purchaseInvoicePreview(id);
      setPreview({ ...p, invoice_source: "purchase_order" });
      const map = {};
      (p.lines || []).forEach((l) => { map[l.purchase_order_line_id] = l.remaining_invoice_qty; });
      setQty(map);
      setPrecedesGoodsReceipt(false);
    } catch (ex) { setErr(errDetail(ex)); setPreview(null); }
  }, []);

  const loadReceiptPreview = useCallback(async (id) => {
    if (!id) { setPreview(null); return; }
    try {
      const p = await api.purchaseInvoiceFromReceiptPreview(id);
      setPreview(p);
      const map = {};
      (p.lines || []).forEach((l) => { map[l.purchase_order_line_id] = l.invoice_qty; });
      setQty(map);
      setPrecedesGoodsReceipt(false);
      if (p.purchase_order_id) setPoId(p.purchase_order_id);
    } catch (ex) { setErr(errDetail(ex)); setPreview(null); }
  }, []);

  useEffect(() => {
    if (view !== "create") return;
    if (!canCreate) {
      setView("list");
      setErr("Anda tidak memiliki izin membuat Faktur Pembelian.");
      return;
    }
    api.listPurchaseOrders({ eligible_for_invoice: true }).then((r) => setEligiblePos(r.orders || [])).catch(() => setEligiblePos([]));
    api.listPurchaseGoodsReceipts({}).then((r) => setEligibleReceipts(r.receipts || [])).catch(() => setEligibleReceipts([]));
    if (invoiceSource === "receipt") {
      if (receiptId) loadReceiptPreview(receiptId);
      else setPreview(null);
    } else if (poId) {
      loadPoPreview(poId);
    } else {
      setPreview(null);
    }
  }, [view, invoiceSource, poId, receiptId, loadPoPreview, loadReceiptPreview, canCreate]);

  const earlyNeeded = useMemo(() => {
    if (!preview || invoiceSource !== "purchase_order") return false;
    return (preview.lines || []).some((l) => {
      const qv = Number(qty[l.purchase_order_line_id]) || 0;
      if (qv <= 0) return false;
      const received = Number(l.cumulative_received_qty) || 0;
      const invoiced = Number(l.cumulative_invoiced_qty) || 0;
      return invoiced + qv > received + 1e-9;
    });
  }, [preview, qty, invoiceSource]);

  const totals = useMemo(() => {
    if (!preview) return { grand: 0 };
    let grand = 0;
    (preview.lines || []).forEach((l) => {
      grand += (Number(qty[l.purchase_order_line_id]) || 0) * (Number(l.unit_price) || 0);
    });
    return { grand };
  }, [preview, qty]);

  const createDisplayLines = useMemo(() => {
    const base = preview?.lines || [];
    if (!visualDemo10 || base.length === 0) return base.map((l, idx) => ({ ...l, _rowNo: idx + 1 }));
    const seed = base[0];
    return Array.from({ length: 10 }, (_, i) => ({
      ...seed,
      purchase_order_line_id: i === 0 ? seed.purchase_order_line_id : `${seed.purchase_order_line_id}__visual_${i}`,
      item_name: i === 0 ? seed.item_name : `${seed.item_name} · demo ${i + 1}`,
      item_code: i === 0 ? (seed.item_code || "—") : `${seed.item_code || "DEMO"}-${i + 1}`,
      _visualDemo: i > 0,
      _rowNo: i + 1,
    }));
  }, [preview, visualDemo10]);

  const detailDisplayLines = useMemo(() => {
    const base = detail?.lines || [];
    if (!visualDemo10 || base.length === 0) return base.map((l, idx) => ({ ...l, _rowNo: idx + 1 }));
    const seed = base[0];
    return Array.from({ length: 10 }, (_, i) => ({
      ...seed,
      line_id: i === 0 ? (seed.line_id || `d0`) : `visual_${i}`,
      item_name: i === 0 ? seed.item_name : `${seed.item_name} · demo ${i + 1}`,
      item_code: i === 0 ? (seed.item_code || "—") : `${seed.item_code || "DEMO"}-${i + 1}`,
      _visualDemo: i > 0,
      _rowNo: i + 1,
    }));
  }, [detail, visualDemo10]);

  const createLineCount = createDisplayLines.length;
  const detailLineCount = detailDisplayLines.length;
  const createDisplayGrand = useMemo(() => {
    if (!visualDemo10) return totals.grand;
    return createDisplayLines.reduce((sum, l) => {
      const key = l.purchase_order_line_id;
      const qv = l._visualDemo ? (Number(l.remaining_invoice_qty) || 1) : (Number(qty[key]) || 0);
      return sum + qv * (Number(l.unit_price) || 0);
    }, 0);
  }, [visualDemo10, createDisplayLines, qty, totals.grand]);

  useEffect(() => {
    if (dueManual) return;
    setDueDate(addDaysYmd(invoiceDate, paymentTermDays));
  }, [invoiceDate, paymentTermDays, dueManual]);

  const applyTermPreset = (presetId, { edit = false } = {}) => {
    const preset = PURCHASE_TERM_PRESETS.find((p) => p.id === presetId) || PURCHASE_TERM_PRESETS[0];
    if (edit) {
      setEditTermPreset(preset.id);
      if (preset.days !== null) {
        setEditTermDays(preset.days);
        // Changing termin always recomputes due; manual override only via date input.
        setEditDueManual(false);
        setEditDueDate(addDaysYmd(detail?.invoice_date || invoiceDate, preset.days));
      }
      return;
    }
    setTermPreset(preset.id);
    if (preset.days !== null) {
      setPaymentTermDays(preset.days);
      setDueManual(false);
      setDueDate(addDaysYmd(invoiceDate, preset.days));
    }
  };

  const save = async () => {
    if (busy || !preview || !canCreate) return;
    if (invoiceSource === "purchase_order" && earlyNeeded && !precedesGoodsReceipt) {
      setErr("Centang acknowledgement 'Faktur mendahului penerimaan barang' untuk menyimpan.");
      return;
    }
    if (!invoiceDate) {
      setErr("Tanggal faktur wajib diisi.");
      return;
    }
    if (!dueDate) {
      setErr("Tanggal jatuh tempo wajib diisi.");
      return;
    }
    if (!Number.isFinite(Number(paymentTermDays)) || Number(paymentTermDays) < 0) {
      setErr("Termin pembayaran tidak valid.");
      return;
    }
    setBusy(true); setErr(""); setOk("");
    try {
      const lines = (preview.lines || []).map((l) => ({
        purchase_order_line_id: l.purchase_order_line_id,
        invoice_qty: qty[l.purchase_order_line_id],
      })).filter((l) => Number(l.invoice_qty) > 0);
      const res = await api.createPurchaseInvoice({
        invoice_source: invoiceSource,
        purchase_order_id: preview.purchase_order_id,
        receipt_id: invoiceSource === "receipt" ? (preview.receipt_id || receiptId) : undefined,
        precedes_goods_receipt: invoiceSource === "purchase_order" ? precedesGoodsReceipt : false,
        invoice_date: invoiceDate,
        payment_term_days: Number(paymentTermDays),
        due_date: dueDate,
        lines,
        idempotency_key: newIdempotencyKey(),
      });
      setDetail(res.invoice);
      setView("detail");
      setOk(`Faktur ${res.invoice?.invoice_number || ""} berhasil disimpan.`);
    } catch (ex) { setErr(errDetail(ex)); }
    finally { setBusy(false); }
  };

  const startEditTerms = () => {
    if (!detail) return;
    const days = Number(detail.payment_term_days);
    const preset = termPresetFromDays(Number.isFinite(days) ? days : 0);
    setEditTermPreset(preset);
    setEditTermDays(Number.isFinite(days) ? days : 0);
    setEditDueDate(String(detail.due_date || addDaysYmd(detail.invoice_date, days || 0) || "").slice(0, 10));
    setEditDueManual(false);
    setEditReason("");
    setTermsErr("");
    setEditingTerms(true);
  };

  const saveTerms = async () => {
    if (!detail || termsBusy || !canEditPurchaseInvoicePaymentTerms(detail, canEditTerms)) return;
    if (!editDueDate) {
      setTermsErr("Tanggal jatuh tempo wajib diisi.");
      return;
    }
    if (!Number.isFinite(Number(editTermDays)) || Number(editTermDays) < 0) {
      setTermsErr("Termin pembayaran tidak valid.");
      return;
    }
    if (String(editReason || "").trim().length < 3) {
      setTermsErr("Alasan perubahan wajib diisi (minimal 3 karakter).");
      return;
    }
    setTermsBusy(true);
    setTermsErr("");
    try {
      const res = await api.updatePurchaseInvoicePaymentTerms(detail.id, {
        payment_term_days: Number(editTermDays),
        due_date: editDueDate,
        reason: String(editReason).trim(),
      });
      setDetail(res.invoice);
      setEditingTerms(false);
      setOk("Termin / jatuh tempo diperbarui.");
      // Refresh from API so history survives client state and matches post-reload view.
      const fresh = (await api.getPurchaseInvoice(detail.id)).invoice;
      setDetail(fresh);
      if (view === "list") load({ soft: true });
    } catch (ex) {
      setTermsErr(errDetail(ex));
    } finally {
      setTermsBusy(false);
    }
  };

  const loadEligibleAdvances = useCallback(async (invoice) => {
    if (!invoice?.purchase_order_id) {
      setEligibleAdvances([]);
      return;
    }
    const res = await api.listPurchaseSupplierAdvances({ purchase_order_id: invoice.purchase_order_id });
    const rows = (res.advances || []).filter((row) => row.status_label === "Dibayar" && Number(row.available_amount) > 0);
    setEligibleAdvances(rows);
  }, []);

  const openDetail = async (id) => {
    setErr("");
    try {
      const invoice = (await api.getPurchaseInvoice(id)).invoice;
      setDetail(invoice);
      setEditingTerms(false);
      setView("detail");
      await loadEligibleAdvances(invoice);
    } catch (ex) { setErr(errDetail(ex)); }
  };

  const allocateAdvance = async () => {
    if (allocBusy || !detail) return;
    const selected = eligibleAdvances.find((row) => row.id === allocAdvanceId);
    const amount = Number(allocAmount);
    if (!selected) {
      setAllocErr("Pilih uang muka yang akan digunakan.");
      return;
    }
    if (!(amount > 0)) {
      setAllocErr("Nilai alokasi harus lebih besar dari 0.");
      return;
    }
    if (amount > Number(selected.available_amount) || amount > Number(detail.outstanding_amount)) {
      setAllocErr("Nilai alokasi melebihi uang muka tersedia atau sisa tagihan.");
      return;
    }
    setAllocBusy(true);
    setAllocErr("");
    try {
      await api.createPurchaseSupplierAdvanceAllocation({
        supplier_advance_id: selected.id,
        purchase_invoice_id: detail.id,
        amount,
        idempotency_key: newIdempotencyKey(),
      });
      const invoice = (await api.getPurchaseInvoice(detail.id)).invoice;
      setDetail(invoice);
      setAllocAmount("");
      await loadEligibleAdvances(invoice);
      setOk("Uang muka dialokasikan. Tidak ada pembayaran kas baru.");
    } catch (ex) { setAllocErr(errDetail(ex)); }
    finally { setAllocBusy(false); }
  };

  const openCancel = (row) => {
    setCancelErr("");
    setCancelTarget(row);
  };

  const confirmPostAccounting = async () => {
    if (!detail || acctBusy) return;
    setAcctBusy(true);
    setAcctErr("");
    try {
      const res = await api.postPurchaseInvoiceAccounting(detail.id);
      setDetail(res.invoice);
      setPostConfirm(false);
      setOk(res.message || "Faktur diposting ke accounting.");
    } catch (ex) {
      setAcctErr(errDetail(ex));
    } finally {
      setAcctBusy(false);
    }
  };

  const confirmReverseAccounting = async () => {
    if (!detail || acctBusy) return;
    if (String(reverseReason || "").trim().length < 3) {
      setAcctErr("Alasan reversal wajib diisi minimal 3 karakter.");
      return;
    }
    setAcctBusy(true);
    setAcctErr("");
    try {
      const res = await api.reversePurchaseInvoiceAccounting(detail.id, reverseReason);
      setDetail(res.invoice);
      setReverseConfirm(false);
      setReverseReason("");
      setOk(res.message || "Faktur di-reverse dari accounting.");
      await load({ soft: true });
    } catch (ex) {
      setAcctErr(errDetail(ex));
    } finally {
      setAcctBusy(false);
    }
  };

  const confirmCancel = async () => {
    if (!cancelTarget || cancelBusy) return;
    setCancelBusy(true);
    setCancelErr("");
    try {
      await api.cancelPurchaseInvoice(cancelTarget.id);
      setOk(`Faktur ${cancelTarget.invoice_number || ""} dihapus dari daftar aktif.`);
      setCancelTarget(null);
      if (view === "detail" && detail?.id === cancelTarget.id) {
        setDetail(null);
        setView("list");
      }
      await load({ soft: true, force: false });
    } catch (ex) {
      setCancelErr(errDetail(ex));
    } finally {
      setCancelBusy(false);
    }
  };

  return (
    <div className={`prod-page purchase-page${view === "create" || view === "detail" ? " is-fpb-doc" : ""}`} data-testid="faktur-pembelian-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1>{view === "create" ? "Buat Faktur Pembelian" : "Faktur Pembelian"}</h1>
          <p className="subhead">
            {view === "list"
              ? "Catat tagihan supplier dari penerimaan barang atau dari PO jika faktur datang lebih dahulu."
              : "Catat tagihan supplier dari penerimaan barang atau pesanan pembelian."}
          </p>
        </div>
        {view === "list" && canCreate && (
          <button
            className="primary"
            data-testid="btn-create-invoice"
            onClick={() => setView("create")}
          >
            <Plus size={14} /> Buat Faktur
          </button>
        )}
        {view !== "list" && <button className="ghost" onClick={backToList}>Kembali ke Daftar</button>}
      </div>
      <PurchasingFlowNav />
      {err && (
        <div className="purchase-error-panel" role="alert" data-testid="invoice-list-error">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={16} color="#a53f2b" /><span>{err}</span>
          </div>
          {view === "list" && <button type="button" className="ghost sm" onClick={() => load({ force: true })}>Coba Lagi</button>}
        </div>
      )}
      {ok && <p className="purchase-inline-ok" data-testid="invoice-success">{ok}</p>}

      {view === "list" && (
        <>
          <div className="purchase-search-bar is-toolbar">
            <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load({ soft: rowsRef.current.length > 0 }); }}>
              <Search size={14} /><input value={q} onChange={(e) => setQ(e.target.value)} data-testid="search-invoice-input" placeholder="Cari no faktur / PO / supplier" />
            </form>
          </div>
          <div className="purchase-table-wrap">
            <table className="prod-table purchase-table purchase-table-inv" data-testid="invoices-table">
              <colgroup>
                <col className="col-inv" />
                <col className="col-supplier" />
                <col className="col-age" />
                <col className="col-date" />
                <col className="col-money" />
                <col className="col-money" />
              </colgroup>
              <thead>
                <tr>
                  <th className="cell-inv">No. Faktur</th>
                  <th className="cell-supplier">Supplier</th>
                  <th className="cell-age">Umur Faktur</th>
                  <th className="cell-due">Jatuh Tempo</th>
                  <th className="cell-money">Total</th>
                  <th className="cell-money">Sisa Tagihan</th>
                </tr>
              </thead>
              <tbody>
                {loading && rows.length === 0 && (
                  <tr className="purchase-empty-row" data-testid="invoices-loading-row">
                    <td colSpan={6} className="muted small">Memuat…</td>
                  </tr>
                )}
                {!loading && rows.length === 0 && (
                  <tr className="purchase-empty-row">
                    <td colSpan={6} className="muted">Belum ada faktur.</td>
                  </tr>
                )}
                {rows.map((r) => (
                  <tr
                    key={r.id}
                    {...purchaseRowInteractionProps(() => openDetail(r.id), { testId: `invoice-row-${r.id}` })}
                  >
                    <td className="mono cell-inv" title={r.invoice_number}>{r.invoice_number}</td>
                    <td className="cell-supplier" title={r.supplier}>{r.supplier || "—"}</td>
                    <td className="nowrap cell-age" data-testid={`invoice-age-${r.id}`}>{invoiceAgeLabel(r.invoice_date)}</td>
                    <td className="nowrap cell-due" data-testid={`invoice-due-${r.id}`}>{r.due_date ? formatPurchaseDateId(r.due_date) : dueDateLabel(r.due_date)}</td>
                    <td className="nowrap cell-money" data-testid={`invoice-total-${r.id}`}><span className="inv-money">{formatRupiah(r.grand_total)}</span></td>
                    <td className="nowrap cell-money" data-testid={`invoice-outstanding-${r.id}`}><span className="inv-money">{formatRupiah(r.outstanding_amount)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}


      {view === "create" && (
        <div className="fpb-final" data-testid="invoice-create-form">
          <section className="fpb-meta" data-testid="po-snapshot-card">
            <div className="fpb-meta-fields">
              <div className="fpb-meta-item">
                <span className="fpb-k"><FileText size={12} aria-hidden /> No Faktur</span>
                <span className="fpb-v mono">Otomatis saat simpan</span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><Hash size={12} aria-hidden /> No PO / GR</span>
                <span className="fpb-v mono">
                  <span data-testid="snapshot-po-number">{preview?.po_number || "—"}</span>
                  {preview?.receipt_number ? <span> / {preview.receipt_number}</span> : null}
                </span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><Building2 size={12} aria-hidden /> Pemasok</span>
                <span className="fpb-v" data-testid="create-supplier">{preview?.supplier || "— ambil sumber"}</span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><CalendarDays size={12} aria-hidden /> Tanggal</span>
                <input type="date" className="fpb-meta-input" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} data-testid="invoice-date-input" />
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><CreditCard size={12} aria-hidden /> Syarat Pembayaran</span>
                <span className="fpb-v">{paymentTermLabel(paymentTermDays)}</span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><CalendarDays size={12} aria-hidden /> Jatuh Tempo</span>
                <span className="fpb-v">{dueDateLabel(dueDate)}</span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><Wallet size={12} aria-hidden /> Status Pembayaran</span>
                <span className="fpb-v muted">Draft</span>
              </div>
            </div>
            <div className="fpb-meta-ambil">
              <span className="fpb-k"><ClipboardList size={12} aria-hidden /> Ambil sumber</span>
              <div className="fpb-ambil-row">
                <div className="purchase-mode-toggle" role="tablist" aria-label="Sumber faktur">
                  <button type="button" className={invoiceSource === "receipt" ? "is-active" : ""} data-testid="invoice-source-receipt" onClick={() => { if (invoiceSource !== "receipt") { setInvoiceSource("receipt"); setPreview(null); } else if (receiptId) loadReceiptPreview(receiptId); }}>Penerimaan</button>
                  <button type="button" className={invoiceSource === "purchase_order" ? "is-active" : ""} data-testid="invoice-source-po" onClick={() => { if (invoiceSource !== "purchase_order") { setInvoiceSource("purchase_order"); setPreview(null); } else if (poId) loadPoPreview(poId); }}>Pesanan</button>
                </div>
                {invoiceSource === "receipt" ? (
                  <select value={receiptId} onChange={(e) => setReceiptId(e.target.value)} data-testid="receipt-selector" aria-label="Pilih penerimaan">
                    <option value="">Pilih penerimaan…</option>
                    {eligibleReceipts.map((r) => <option key={r.id} value={r.id}>{r.receipt_number} · {r.po_number || "—"}</option>)}
                  </select>
                ) : (
                  <select value={poId} onChange={(e) => setPoId(e.target.value)} data-testid="po-selector" aria-label="Pilih PO" disabled={Boolean(queryPoId)}>
                    <option value="">Pilih PO…</option>
                    {eligiblePos.map((o) => <option key={o.id} value={o.id}>{o.po_number} · {o.supplier}</option>)}
                  </select>
                )}
              </div>
            </div>
          </section>

          <div className="fpb-body">
            <section className={`fpb-lines${createLineCount > 10 ? " is-scrollable" : ""}`} aria-label="Rincian Barang">
              <div className="fpb-lines-head">
                <h3>Rincian Barang <span className="fpb-lines-count" data-testid="create-line-count">({createLineCount} item)</span></h3>
              </div>
              <div className="fpb-table-scroll">
                <table className="fpb-lines-table">
                  <thead>
                    <tr>
                      <th className="col-no">No</th>
                      <th className="col-name">Nama Barang</th>
                      <th className="col-code">Kode Barang</th>
                      <th className="col-qty num">Qty</th>
                      <th className="col-unit">Satuan</th>
                      <th className="col-price num">Harga (Rp)</th>
                      <th className="col-total num">Total (Rp)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {!preview && (
                      <tr><td colSpan={7} className="muted">Ambil Pesanan atau Penerimaan untuk memuat barang.</td></tr>
                    )}
                    {preview && createDisplayLines.length === 0 && (
                      <tr><td colSpan={7} className="muted">Belum ada baris item.</td></tr>
                    )}
                    {createDisplayLines.map((l) => {
                      const key = l.purchase_order_line_id;
                      const qv = l._visualDemo ? (Number(l.remaining_invoice_qty) || 1) : (Number(qty[key]) || 0);
                      const price = Number(l.unit_price) || 0;
                      return (
                        <tr key={key} className={l._visualDemo ? "is-visual-demo" : undefined}>
                          <td className="col-no num">{l._rowNo}</td>
                          <td className="col-name" title={l.item_name}>{l.item_name || "—"}</td>
                          <td className="col-code mono">{l.item_code || "—"}</td>
                          <td className="col-qty">
                            {l._visualDemo ? (
                              <span className="num">{fmtQty(qv)}</span>
                            ) : (
                              <input
                                type="number"
                                min="0"
                                className="fpb-qty"
                                value={qty[key] ?? ""}
                                title={`Sisa: ${fmtQty(l.remaining_invoice_qty)}`}
                                onChange={(e) => setQty((p) => ({ ...p, [key]: e.target.value }))}
                                data-testid="qty-input"
                              />
                            )}
                          </td>
                          <td className="col-unit">{l.unit || "pcs"}</td>
                          <td className="col-price num">{formatRupiah(price)}</td>
                          <td className="col-total num">{formatRupiah(qv * price)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>

            <aside className="fpb-side">
              <section className="fpb-side-card" data-testid="create-summary">
                <h3>Ringkasan Nilai</h3>
                <dl>
                  <div><dt>Nilai Faktur</dt><dd data-testid="invoice-grand-total">{formatRupiah(createDisplayGrand)}</dd></div>
                  <div><dt>Uang Muka Dialokasikan</dt><dd>Rp 0</dd></div>
                  <div><dt>Pembayaran</dt><dd>Rp 0</dd></div>
                  <div><dt>Sisa Tagihan</dt><dd>{formatRupiah(createDisplayGrand)}</dd></div>
                  <div className="is-total"><dt>Total</dt><dd>{formatRupiah(createDisplayGrand)}</dd></div>
                </dl>
              </section>
              <section className="fpb-side-card" data-testid="create-panel-info">
                <h3>Info Lainnya</h3>
                <div className="fpb-side-fields">
                  <label>Syarat Pembayaran <span className="fpb-req">*</span></label>
                  <div className="purchase-term-row" data-testid="invoice-term-presets">
                    {PURCHASE_TERM_PRESETS.map((p) => (
                      <button key={p.id} type="button" className={`ghost sm${termPreset === p.id ? " is-active" : ""}`} data-testid={`term-preset-${p.id.toLowerCase()}`} onClick={() => applyTermPreset(p.id)}>{p.label}</button>
                    ))}
                  </div>
                  {termPreset === "CUSTOM" && (
                    <input type="number" min="0" value={paymentTermDays} onChange={(e) => { setPaymentTermDays(e.target.value); setDueManual(false); }} data-testid="invoice-term-days-input" />
                  )}
                  <label htmlFor="invoice-due-date-input">Jatuh Tempo <span className="fpb-req">*</span></label>
                  <div className="purchase-due-row">
                    <input id="invoice-due-date-input" type="date" value={dueDate} onChange={(e) => { setDueManual(true); setDueDate(e.target.value); }} data-testid="invoice-due-date-input" />
                    <button type="button" className="ghost sm" data-testid="invoice-due-recalc" onClick={() => { setDueManual(false); setDueDate(addDaysYmd(invoiceDate, paymentTermDays)); }}>Hitung ulang</button>
                  </div>
                  {invoiceSource === "purchase_order" && earlyNeeded && (
                    <label className="purchase-ack" data-testid="early-invoice-ack">
                      <input type="checkbox" checked={precedesGoodsReceipt} onChange={(e) => setPrecedesGoodsReceipt(e.target.checked)} data-testid="precedes-goods-receipt" />
                      <span>Tagihan Dimuka (Mendahului Terima Barang)</span>
                    </label>
                  )}
                </div>
              </section>
            </aside>
          </div>

          <footer className="fpb-actions-bar">
            <div className="fpb-actions-left">
              <button className="ghost" type="button" onClick={() => setView("list")}>Batal</button>
            </div>
            <div className="fpb-actions-right">
              <button className="primary" disabled={busy || !preview || visualDemo10} onClick={save} data-testid="save-invoice-btn" title={visualDemo10 ? "Mode visual_demo: tidak menyimpan." : undefined}>
                {busy ? "Menyimpan…" : "Simpan Faktur"}
              </button>
            </div>
          </footer>
        </div>
      )}

      {view === "detail" && detail && (
        <div className="fpb-final" data-testid="invoice-detail">
          <section className="fpb-meta" data-testid="invoice-detail-header">
            <div className="fpb-meta-fields">
              <div className="fpb-meta-item">
                <span className="fpb-k"><FileText size={12} aria-hidden /> No Faktur</span>
                <span className="fpb-v mono" data-testid="detail-invoice-number">
                  {detail.invoice_number}
                  {detail.precedes_goods_receipt && <span className="purchase-badge-early">Faktur mendahului barang</span>}
                </span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><Hash size={12} aria-hidden /> No PO / GR</span>
                <span className="fpb-v mono">
                  <span data-testid="detail-po-number">{detail.po_number || "—"}</span>
                  {detail.receipt_number ? <span> / {detail.receipt_number}</span> : null}
                </span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><Building2 size={12} aria-hidden /> Pemasok</span>
                <span className="fpb-v" data-testid="detail-supplier">{detail.supplier || "—"}</span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><CalendarDays size={12} aria-hidden /> Tanggal</span>
                <span className="fpb-v" data-testid="detail-invoice-date">{detail.invoice_date || "—"}</span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><CreditCard size={12} aria-hidden /> Syarat Pembayaran</span>
                <span className="fpb-v" data-testid="detail-payment-term">{paymentTermLabel(detail.payment_term_days)}</span>
              </div>
              <div className="fpb-meta-item" data-testid="detail-due-date-block">
                <span className="fpb-k"><CalendarDays size={12} aria-hidden /> Jatuh Tempo</span>
                <span className="fpb-v">
                  <span data-testid="detail-due-date">{dueDateLabel(detail.due_date)}</span>
                  {detail.due_status && detail.due_status !== "LUNAS" ? <span className="muted small" data-testid="detail-due-status"> · {detail.due_status}</span> : null}
                </span>
              </div>
              <div className="fpb-meta-item">
                <span className="fpb-k"><Wallet size={12} aria-hidden /> Status Pembayaran</span>
                <span className={`purchase-status-badge ${purchaseStatusPillClass(detail.payment_status)}`} data-testid="detail-payment-status">{purchasePaymentStatusLabel(detail.payment_status)}</span>
              </div>
            </div>
          </section>

          <div className="fpb-body">
            <section className={`fpb-lines${detailLineCount > 10 ? " is-scrollable" : ""}`} aria-label="Rincian Barang">
              <div className="fpb-lines-head">
                <h3>Rincian Barang <span className="fpb-lines-count" data-testid="detail-line-count">({detailLineCount} item)</span></h3>
              </div>
              <div className="fpb-table-scroll">
                <table className="fpb-lines-table" data-testid="invoice-detail-lines">
                  <thead>
                    <tr>
                      <th className="col-no">No</th>
                      <th className="col-name">Nama Barang</th>
                      <th className="col-code">Kode Barang</th>
                      <th className="col-qty num">Qty</th>
                      <th className="col-unit">Satuan</th>
                      <th className="col-price num">Harga (Rp)</th>
                      <th className="col-total num">Total (Rp)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detailDisplayLines.length === 0 && (
                      <tr><td colSpan={7} className="muted">Tidak ada baris item.</td></tr>
                    )}
                    {detailDisplayLines.map((l) => (
                      <tr key={l.line_id || `${l.item_name}-${l._rowNo}`} className={l._visualDemo ? "is-visual-demo" : undefined}>
                        <td className="col-no num">{l._rowNo}</td>
                        <td className="col-name" title={l.item_name}>{l.item_name || "—"}</td>
                        <td className="col-code mono">{l.item_code || "—"}</td>
                        <td className="col-qty num">{fmtQty(l.invoice_qty || l.quantity)}</td>
                        <td className="col-unit">{l.unit || "pcs"}</td>
                        <td className="col-price num">{formatRupiah(l.unit_price)}</td>
                        <td className="col-total num">{formatRupiah(l.line_subtotal)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <aside className="fpb-side">
              <section className="fpb-side-card" data-testid="invoice-detail-summary">
                <h3>Ringkasan Nilai</h3>
                <dl>
                  <div><dt>Nilai Faktur</dt><dd data-testid="detail-grand-total">{formatRupiah(detail.grand_total)}</dd></div>
                  <div><dt>Uang Muka Dialokasikan</dt><dd data-testid="detail-advance-applied">{formatRupiah(detail.advance_applied_amount || 0)}</dd></div>
                  <div><dt>Pembayaran</dt><dd data-testid="detail-normal-payment">{formatRupiah(detail.paid_total)}</dd></div>
                  <div><dt>Sisa Tagihan</dt><dd data-testid="detail-outstanding">{formatRupiah(detail.outstanding_amount)}</dd></div>
                  <div className="is-total"><dt>Total</dt><dd>{formatRupiah(detail.grand_total)}</dd></div>
                </dl>
              </section>

              <section className="fpb-side-card" data-testid="detail-panel-info">
                <h3>Info Lainnya</h3>
                <div className="fpb-side-fields">
                  <div className="fpb-kv"><span>No. Referensi</span><b className="mono">{[detail.po_number, detail.receipt_number].filter(Boolean).join(" / ") || "—"}</b></div>
                  <div className="fpb-kv"><span>Mata Uang</span><b>IDR</b></div>
                  <div className="fpb-kv"><span>Syarat Pembayaran</span><b>{paymentTermLabel(detail.payment_term_days)}</b></div>
                  <div className="fpb-kv"><span>Jatuh Tempo</span><b>{dueDateLabel(detail.due_date)}</b></div>
                  {detail.notes ? (
                    <div className="fpb-notes">
                      <span>Keterangan</span>
                      <p>{detail.notes}</p>
                    </div>
                  ) : null}
                  {!editingTerms && canEditPurchaseInvoicePaymentTerms(detail, canEditTerms) && (
                    <button type="button" className="ghost sm" data-testid="btn-ubah-termin-jatuh-tempo" onClick={startEditTerms}>Ubah Termin / Jatuh Tempo</button>
                  )}
                  {editingTerms && (
                    <div className="purchase-due-date-edit" data-testid="detail-terms-edit">
                      <div className="purchase-term-row">
                        {PURCHASE_TERM_PRESETS.map((p) => (
                          <button key={p.id} type="button" className={`ghost sm${editTermPreset === p.id ? " is-active" : ""}`} data-testid={`edit-term-preset-${p.id.toLowerCase()}`} disabled={termsBusy} onClick={() => applyTermPreset(p.id, { edit: true })}>{p.label}</button>
                        ))}
                      </div>
                      {editTermPreset === "CUSTOM" && (
                        <input type="number" min="0" value={editTermDays} disabled={termsBusy} onChange={(e) => { setEditTermDays(e.target.value); setEditDueManual(false); setEditDueDate(addDaysYmd(detail.invoice_date, e.target.value)); }} data-testid="edit-term-days-input" />
                      )}
                      <input type="date" value={editDueDate} disabled={termsBusy} onChange={(e) => { setEditDueManual(true); setEditDueDate(e.target.value); }} data-testid="detail-due-date-input" />
                      <input type="text" value={editReason} disabled={termsBusy} onChange={(e) => setEditReason(e.target.value)} placeholder="Alasan perubahan (wajib, min. 3 karakter)" data-testid="edit-terms-reason" required />
                      <div className="fpb-inline-actions">
                        <button type="button" className="primary sm" disabled={termsBusy || String(editReason || "").trim().length < 3} onClick={saveTerms} data-testid="btn-simpan-termin-jatuh-tempo">{termsBusy ? "Menyimpan…" : "Simpan"}</button>
                        <button type="button" className="ghost sm" disabled={termsBusy} onClick={() => setEditingTerms(false)} data-testid="btn-batal-termin-jatuh-tempo">Batal</button>
                      </div>
                    </div>
                  )}
                  {termsErr ? <p className="error" data-testid="terms-edit-error">{termsErr}</p> : null}
                  {!canEditPurchaseInvoicePaymentTerms(detail, canEditTerms) && detail.edit_payment_terms_block_reason ? (
                    <p className="muted small" data-testid="terms-edit-blocked">{detail.edit_payment_terms_block_reason}</p>
                  ) : null}
                </div>
              </section>
            </aside>
          </div>

          <section className="fpb-lower" aria-label="Dokumen dan riwayat aktivitas">
            <div className="fpb-lower-card" data-testid="detail-panel-docs">
              <PurchaseDocuments entityType="purchase_invoice" entityId={detail.id} canEdit={canCreate} testId="inv-documents" />
            </div>
            <div className="fpb-lower-card" data-testid="detail-panel-history">
              <PurchaseActivityHistory doc={detail} testId="invoice-activity" />
            </div>
          </section>

          <section className="fpb-terms-audit" data-testid="invoice-terms-panel" aria-label="Riwayat perubahan termin">
            <div className="fpb-lower-card fpb-terms-audit-card">
              <h4>Riwayat Perubahan Termin</h4>
              {(detail.payment_terms_history || []).length > 0 ? (
                <div className="purchase-table-wrap" data-testid="invoice-terms-history">
                  <table className="fpb-terms-audit-table">
                    <thead>
                      <tr>
                        <th>Waktu</th>
                        <th>Oleh</th>
                        <th>Termin Lama → Baru</th>
                        <th>Jatuh Tempo Lama → Baru</th>
                        <th>Alasan</th>
                      </tr>
                    </thead>
                    <tbody>
                      {[...(detail.payment_terms_history || [])].reverse().map((entry, idx) => (
                        <tr key={`${entry.at || "h"}-${idx}`} data-testid={`terms-history-row-${idx}`}>
                          <td className="nowrap">{formatPurchaseDateTime(entry.at)}</td>
                          <td>{entry?.by?.name || entry?.by?.email || "—"}</td>
                          <td>
                            {paymentTermLabel(entry?.before?.payment_term_days)}
                            {" → "}
                            {paymentTermLabel(entry?.after?.payment_term_days)}
                          </td>
                          <td className="nowrap">
                            {dueDateLabel(entry?.before?.due_date)}
                            {" → "}
                            {dueDateLabel(entry?.after?.due_date)}
                          </td>
                          <td>{entry.reason ? entry.reason : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="muted small" data-testid="invoice-terms-history-empty">Belum ada perubahan termin / jatuh tempo.</p>
              )}
              {(detail.payments || []).length > 0 && (
                <div className="fpb-payment-history-wrap" data-testid="invoice-payment-history">
                  <h5>Riwayat Pengajuan Pembayaran</h5>
                  <div className="purchase-table-wrap">
                    <table className="prod-table">
                      <thead><tr><th>No Bayar</th><th>Tgl Pengajuan</th><th>Status</th><th className="num">Jumlah</th></tr></thead>
                      <tbody>
                        {(detail.payments || []).map((p) => (
                          <tr key={p.id}>
                            <td className="mono">{p.payment_number}</td>
                            <td>{p.submitted_at ? formatPurchaseDateTime(p.submitted_at) : (p.payment_date || "—")}</td>
                            <td>{p.submission_status_label || p.list_status || p.workflow_status || "—"}</td>
                            <td className="num">{formatRupiah(p.amount)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          </section>

          <section className="fpb-extra" aria-label="Informasi sekunder uang muka dan accounting">
            <div className="fpb-lower-card fpb-extra-card" data-testid="gunakan-uang-muka">
              <h4>Gunakan Uang Muka</h4>
              {eligibleAdvances.length === 0 && <p className="muted small">Tidak ada uang muka tersedia untuk pesanan ini.</p>}
              {eligibleAdvances.map((row) => (
                <div key={row.id} data-testid="eligible-advance-row" className="fpb-advance-row">
                  <span>{row.advance_number}</span>
                  <span data-testid="eligible-advance-available">{formatRupiah(row.available_amount)}</span>
                </div>
              ))}
              {eligibleAdvances.length > 0 && (
                <div className="purchase-form-actions">
                  <select value={allocAdvanceId} onChange={(e) => setAllocAdvanceId(e.target.value)} data-testid="alloc-advance-select">
                    <option value="">Pilih uang muka</option>
                    {eligibleAdvances.map((row) => <option key={row.id} value={row.id}>{row.advance_number} — {formatRupiah(row.available_amount)}</option>)}
                  </select>
                  <input type="number" min="1" value={allocAmount} onChange={(e) => setAllocAmount(e.target.value)} data-testid="alloc-amount" placeholder="Nilai alokasi" />
                  <button type="button" className="primary sm" disabled={allocBusy} onClick={allocateAdvance} data-testid="alloc-submit">Alokasikan</button>
                </div>
              )}
              {allocErr && <p data-testid="alloc-error">{allocErr}</p>}
            </div>
            <div className="fpb-lower-card fpb-extra-card" data-testid="invoice-accounting-section">
              <h4>Accounting</h4>
              <div className="fpb-kv"><span>Status</span><b data-testid="invoice-detail-accounting-status">{accountingLabel(detail.accounting_status)}</b></div>
              {detail.accounting_journal_id ? <div className="fpb-kv"><span>Journal</span><b className="mono" data-testid="invoice-detail-accounting-journal">{detail.accounting_journal_id}</b></div> : null}
              {detail.accounting_ready === false && String(detail.accounting_status || "not_posted") === "not_posted" ? (
                <p className="muted small" data-testid="invoice-accounting-not-ready">Pengaturan Akuntansi Pembelian belum siap. Post Accounting dinonaktifkan.</p>
              ) : null}
            </div>
          </section>

          <footer className="fpb-actions-bar purchase-detail-actions">
            <div className="fpb-actions-left">
              <button type="button" className="ghost" onClick={backToList}>Batal</button>
            </div>
            <div className="fpb-actions-right">
              {canPostAccounting && String(detail.accounting_status || "not_posted") === "not_posted" && (
                <button type="button" className="ghost" data-testid="btn-post-invoice-accounting" disabled={acctBusy || detail.can_post_accounting === false} title={detail.can_post_accounting === false ? (detail.accounting_ready === false ? "Pengaturan Akuntansi Pembelian belum dikonfigurasi." : "") : undefined} onClick={() => { setAcctErr(""); setPostConfirm(true); }}>Post Accounting</button>
              )}
              {canReverseAccounting && String(detail.accounting_status || "") === "posted" && (
                <button type="button" className="ghost" data-testid="btn-reverse-invoice-accounting" disabled={acctBusy || detail.can_reverse_accounting === false} title={detail.can_reverse_accounting === false ? "Faktur tidak dapat di-reverse karena masih memiliki pembayaran aktif." : undefined} onClick={() => { setAcctErr(""); setReverseReason(""); setReverseConfirm(true); }}>Reverse Accounting</button>
              )}
              {canCreate && (
                <button type="button" className="ghost danger" data-testid="btn-hapus-inv-detail" disabled={detail.can_cancel === false} title={detail.can_cancel === false ? (detail.cancel_block_reason || "") : undefined} onClick={() => openCancel(detail)}>Hapus</button>
              )}
              {Number(detail.outstanding_amount) > 0 && detail.payment_status !== "LUNAS" && hasPerm("purchase_payments.create") && (
                <button type="button" className="primary" data-testid="btn-ajukan-pembayaran-inv-detail" onClick={() => setAjukanOpen(true)}>Ajukan Pembayaran</button>
              )}
              {Number(detail.outstanding_amount) > 0 && (
                <span className="muted small" data-testid="payment-max">Maksimum pembayaran {formatRupiah(detail.outstanding_amount)}</span>
              )}
              {lastChatRoomId ? (
                <Link className="ghost" data-testid="btn-buka-chat-after-ajukan" to={`/chat/${encodeURIComponent(lastChatRoomId)}`}>Buka Chat</Link>
              ) : null}
            </div>
          </footer>
        </div>
      )}

      <PurchaseCancelModal
        open={Boolean(cancelTarget)}
        title="Hapus Faktur Pembelian?"
        body="Dokumen akan dikeluarkan dari daftar aktif. Riwayat pembatalan tetap tersimpan."
        busy={cancelBusy}
        error={cancelErr}
        onCancel={() => !cancelBusy && setCancelTarget(null)}
        onConfirm={confirmCancel}
      />

      <PurchasePaymentAjukanModal
        open={ajukanOpen && Boolean(detail)}
        invoice={detail}
        onCancel={() => setAjukanOpen(false)}
        onSubmitted={(res) => {
          setAjukanOpen(false);
          const roomId = res?.chat_room_id || res?.payment?.submission_chat_room_id || "";
          setLastChatRoomId(roomId);
          setOk(res?.message || `Pengajuan ${res?.payment?.payment_number || ""} terkirim.`);
          if (detail?.id) {
            api.getPurchaseInvoice(detail.id).then((r) => setDetail(r.invoice)).catch(() => {});
          }
        }}
      />

      {postConfirm && detail && (
        <div className="modal" role="dialog" aria-modal="true" data-testid="invoice-post-accounting-modal">
          <div className="modal-inner" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>Post Accounting?</h3>
              <button type="button" className="ghost sm" disabled={acctBusy} onClick={() => setPostConfirm(false)}>×</button>
            </div>
            <p className="purchase-modal-helper">Jurnal: Dr Purchase Clearing / GRNI, Cr Hutang Usaha. Tanggal jurnal = tanggal faktur.</p>
            <dl className="purchase-detail-meta">
              <div>
                <span className="label">No Faktur</span>
                <span className="value mono">{detail.invoice_number}</span>
              </div>
              <div>
                <span className="label">Grand Total</span>
                <span className="value">{formatRupiah(detail.grand_total)}</span>
              </div>
            </dl>
            {acctErr ? <p className="purchase-inline-err" data-testid="invoice-accounting-error">{acctErr}</p> : null}
            <div className="purchase-modal-actions">
              <button type="button" className="ghost" disabled={acctBusy} onClick={() => setPostConfirm(false)}>Batal</button>
              <button type="button" className="primary" data-testid="btn-confirm-post-invoice-accounting" disabled={acctBusy} onClick={confirmPostAccounting}>
                {acctBusy ? "Memproses…" : "Post Accounting"}
              </button>
            </div>
          </div>
        </div>
      )}

      {reverseConfirm && detail && (
        <div className="modal" role="dialog" aria-modal="true" data-testid="invoice-reverse-accounting-modal">
          <div className="modal-inner" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <h3>Reverse Accounting?</h3>
              <button type="button" className="ghost sm" disabled={acctBusy} onClick={() => { setReverseConfirm(false); setAcctErr(""); }}>×</button>
            </div>
            <p className="purchase-modal-helper">Reversal membalik jurnal faktur dan membatalkan faktur secara operasional. Nomor dokumen tetap tersimpan.</p>
            <label className="label">Alasan reversal</label>
            <textarea
              data-testid="invoice-reverse-reason-input"
              value={reverseReason}
              onChange={(e) => setReverseReason(e.target.value)}
              rows={3}
              placeholder="Minimal 3 karakter"
              style={{ width: "100%", margin: "6px 0 12px" }}
            />
            {acctErr ? <p className="purchase-inline-err" data-testid="invoice-reverse-accounting-error">{acctErr}</p> : null}
            <div className="purchase-modal-actions">
              <button type="button" className="ghost" disabled={acctBusy} onClick={() => { setReverseConfirm(false); setAcctErr(""); }}>Batal</button>
              <button
                type="button"
                className="primary"
                data-testid="btn-confirm-reverse-invoice-accounting"
                disabled={acctBusy || String(reverseReason || "").trim().length < 3}
                onClick={confirmReverseAccounting}
              >
                {acctBusy ? "Memproses…" : "Reverse Accounting"}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
