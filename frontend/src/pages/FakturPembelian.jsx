import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  buildPurchaseLifecycle,
  dueDateLabel,
  dueStatusPillClass,
  errDetail,
  formatPurchaseDate,
  formatPurchaseDateTime,
  formatRupiah,
  friendlyLoadError,
  invoiceDueDisplayStatus,
  newIdempotencyKey,
  purchaseRowInteractionProps,
  purchaseStatusPillClass,
  todayYmd,
} from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";
import PurchasingFlowNav from "./PurchasingFlowNav";
import PurchaseCancelModal from "./PurchaseCancelModal";
import PurchaseActivityHistory from "./PurchaseActivityHistory";
import PurchaseDocuments from "./PurchaseDocuments";
import PurchaseLifecycleStrip from "./PurchaseLifecycleStrip";
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
  const [dueDate, setDueDate] = useState("");
  const [qty, setQty] = useState({});
  const [precedesGoodsReceipt, setPrecedesGoodsReceipt] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setErr("");
    try { setRows((await api.listPurchaseInvoices({ q })).invoices || []); }
    catch (ex) { setErr(friendlyLoadError(ex, "Tidak dapat memuat faktur. Periksa koneksi server lalu coba lagi.")); }
    finally { setLoading(false); }
  }, [q]);
  useEffect(() => { if (view === "list") load(); }, [load, view]);

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

  const save = async () => {
    if (busy || !preview || !canCreate) return;
    if (invoiceSource === "purchase_order" && earlyNeeded && !precedesGoodsReceipt) {
      setErr("Centang acknowledgement 'Faktur mendahului penerimaan barang' untuk menyimpan.");
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
        due_date: dueDate || null,
        lines,
        idempotency_key: newIdempotencyKey(),
      });
      setDetail(res.invoice); setView("detail");
      setOk(`Faktur ${res.invoice?.invoice_number || ""} berhasil disimpan.`);
    } catch (ex) { setErr(errDetail(ex)); }
    finally { setBusy(false); }
  };

  const openDetail = async (id) => {
    setErr("");
    try {
      setDetail((await api.getPurchaseInvoice(id)).invoice);
      setView("detail");
    } catch (ex) { setErr(errDetail(ex)); }
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
      await load();
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
        setView("list");
        setDetail(null);
      }
      await load();
    } catch (ex) {
      setCancelErr(errDetail(ex));
    } finally {
      setCancelBusy(false);
    }
  };

  return (
    <div className="prod-page purchase-page" data-testid="faktur-pembelian-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1>{view === "create" ? "Buat Faktur Pembelian" : "Faktur Pembelian"}</h1>
          <p className="subhead">
            {view === "create"
              ? "Pilih sumber faktur dari penerimaan barang atau dari PO jika faktur datang lebih dahulu."
              : "Catat tagihan supplier dari penerimaan barang atau dari PO jika faktur datang lebih dahulu."}
          </p>
        </div>
        {view === "list" && canCreate && <button className="primary" data-testid="btn-create-invoice" onClick={() => setView("create")}><Plus size={14} /> Buat Faktur</button>}
        {view !== "list" && <button className="ghost" onClick={() => { setView("list"); load(); }}>Kembali ke daftar</button>}
      </div>
      <PurchasingFlowNav />
      {err && (
        <div className="purchase-error-panel" role="alert">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={16} color="#a53f2b" /><span>{err}</span>
          </div>
          {view === "list" && <button type="button" className="ghost sm" onClick={load}>Coba Lagi</button>}
        </div>
      )}
      {ok && <p className="purchase-inline-ok" data-testid="invoice-success">{ok}</p>}

      {view === "list" && (
        <>
          <div className="purchase-search-bar is-toolbar">
            <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load(); }}>
              <Search size={14} /><input value={q} onChange={(e) => setQ(e.target.value)} data-testid="search-invoice-input" placeholder="Cari no faktur / PO / supplier" />
            </form>
          </div>
          <div className="purchase-table-wrap">
            <table className="prod-table purchase-table purchase-table-inv" data-testid="invoices-table">
              <thead>
                <tr>
                  <th>No Faktur</th>
                  <th>Supplier</th>
                  <th>Tanggal Faktur</th>
                  <th>Jatuh Tempo</th>
                  <th className="num">Grand Total</th>
                  <th className="num">Dibayar</th>
                  <th className="num">Sisa</th>
                  <th className="cell-status">Status Pembayaran</th>
                  <th className="cell-status">Due status</th>
                  <th>Referensi</th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr className="purchase-empty-row"><td colSpan={10} className="muted small">Memuat…</td></tr>}
                {!loading && rows.length === 0 && (
                  <tr className="purchase-empty-row">
                    <td colSpan={10} className="muted">Belum ada faktur.</td>
                  </tr>
                )}
                {rows.map((r) => {
                  const refParts = [r.po_number, r.receipt_number].filter(Boolean);
                  const refLabel = refParts.length ? refParts.join(" / ") : "—";
                  const dueStatus = invoiceDueDisplayStatus(r);
                  return (
                    <tr
                      key={r.id}
                      {...purchaseRowInteractionProps(() => openDetail(r.id), { testId: `invoice-row-${r.id}` })}
                    >
                      <td className="mono cell-clip">
                        {r.invoice_number}
                        {r.precedes_goods_receipt && <span className="purchase-badge-early">Faktur mendahului barang</span>}
                      </td>
                      <td className="cell-clip" title={r.supplier}>{r.supplier}</td>
                      <td className="nowrap">{formatPurchaseDate(r.invoice_date)}</td>
                      <td className="nowrap">{dueDateLabel(r.due_date)}</td>
                      <td className="num">{formatRupiah(r.grand_total)}</td>
                      <td className="num">{formatRupiah(r.paid_total)}</td>
                      <td className="num">{formatRupiah(r.outstanding_amount)}</td>
                      <td className="cell-status">
                        <span className={`purchase-status-badge ${purchaseStatusPillClass(r.payment_status)}`}>
                          {r.payment_status}
                        </span>
                      </td>
                      <td className="cell-status">
                        <span className={`purchase-status-badge ${dueStatusPillClass(dueStatus)}`}>
                          {dueStatus}
                        </span>
                      </td>
                      <td className="mono cell-clip" title={refLabel}>{refLabel}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {view === "create" && (
        <div className="card purchase-detail-card" data-testid="invoice-create-form">
          <div className="purchase-mode-toggle" role="tablist" aria-label="Sumber faktur">
            <button
              type="button"
              className={invoiceSource === "receipt" ? "is-active" : ""}
              data-testid="invoice-source-receipt"
              onClick={() => { setInvoiceSource("receipt"); setPreview(null); }}
            >
              Penerimaan Barang
            </button>
            <button
              type="button"
              className={invoiceSource === "purchase_order" ? "is-active" : ""}
              data-testid="invoice-source-po"
              onClick={() => { setInvoiceSource("purchase_order"); setPreview(null); }}
            >
              Pesanan Pembelian
            </button>
          </div>

          {invoiceSource === "receipt" && (
            <FormField id="inv-receipt" label="Penerimaan Barang" isRequired value={receiptId}>
              <select value={receiptId} onChange={(e) => setReceiptId(e.target.value)} data-testid="receipt-selector">
                <option value="">Pilih penerimaan</option>
                {eligibleReceipts.map((r) => (
                  <option key={r.id} value={r.id}>{r.receipt_number} · {r.po_number || "—"} · {r.supplier || ""}</option>
                ))}
              </select>
            </FormField>
          )}

          {invoiceSource === "purchase_order" && !queryPoId && (
            <FormField id="inv-po" label="Pesanan Pembelian" isRequired value={poId}>
              <select value={poId} onChange={(e) => setPoId(e.target.value)} data-testid="po-selector">
                <option value="">Pilih PO</option>
                {eligiblePos.map((o) => <option key={o.id} value={o.id}>{o.po_number} · {o.supplier}</option>)}
              </select>
            </FormField>
          )}

          {preview && (
            <div className="po-snapshot-card" data-testid="po-snapshot-card">
              <div className="purchase-detail-meta">
                <div>
                  <span className="label">No PO</span>
                  <span className="value mono" data-testid="snapshot-po-number">{preview.po_number}</span>
                </div>
                {preview.receipt_number && (
                  <div>
                    <span className="label">Penerimaan</span>
                    <span className="value mono">{preview.receipt_number}</span>
                  </div>
                )}
                <div>
                  <span className="label">Supplier</span>
                  <span className="value">{preview.supplier}</span>
                </div>
              </div>
              <FormProgress
                percentage={Math.round(([invoiceDate, Object.values(qty).some(v => Number(v) > 0)].filter(Boolean).length / 2) * 100)}
                completed={[invoiceDate, Object.values(qty).some(v => Number(v) > 0)].filter(Boolean).length}
                total={2}
              />
              <FormSection id="invoice-header" title="Informasi Faktur" status={computeStatus([{ value: invoiceDate, isRequired: true }])}>
                <div className="purchase-form-grid">
                  <FormField id="invoice-date" label="Tanggal Faktur" isRequired value={invoiceDate}>
                    <input type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} data-testid="invoice-date-input" />
                  </FormField>
                  <FormField id="invoice-due-date" label="Tanggal Jatuh Tempo (opsional)" value={dueDate}>
                    <input type="date" value={dueDate} onChange={(e) => setDueDate(e.target.value)} data-testid="invoice-due-date-input" />
                  </FormField>
                </div>
              </FormSection>
              <FormSection id="invoice-lines" title="Detail Item" status={Object.values(qty).some(v => Number(v) > 0) ? "complete" : "incomplete"}>
                <div className="purchase-table-wrap">
                  <table className="prod-table">
                    <thead>
                      <tr>
                        <th>Item</th>
                        <th className="num">Sisa</th>
                        {invoiceSource === "purchase_order" && <th className="num">Diterima</th>}
                        <th className="num">Qty faktur</th>
                        <th className="num">Harga</th>
                        <th className="num">Subtotal</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(preview.lines || []).map((l) => (
                        <tr key={l.purchase_order_line_id}>
                          <td>{l.item_name}</td>
                          <td className="num">{fmtQty(l.remaining_invoice_qty)}</td>
                          {invoiceSource === "purchase_order" && (
                            <td className="num">{fmtQty(l.cumulative_received_qty)}</td>
                          )}
                          <td>
                            <input
                              type="number"
                              min="0"
                              value={qty[l.purchase_order_line_id] ?? ""}
                              onChange={(e) => setQty((p) => ({ ...p, [l.purchase_order_line_id]: e.target.value }))}
                              data-testid="qty-input"
                            />
                          </td>
                          <td className="num">{formatRupiah(l.unit_price)}</td>
                          <td className="num">{formatRupiah((Number(qty[l.purchase_order_line_id]) || 0) * (Number(l.unit_price) || 0))}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </FormSection>

              {invoiceSource === "purchase_order" && earlyNeeded && (
                <label className="purchase-ack" data-testid="early-invoice-ack">
                  <input
                    type="checkbox"
                    checked={precedesGoodsReceipt}
                    onChange={(e) => setPrecedesGoodsReceipt(e.target.checked)}
                    data-testid="precedes-goods-receipt"
                  />
                  <span>
                    Faktur mendahului penerimaan barang
                    <span className="hint">Gunakan jika faktur supplier diterima sebelum barang.</span>
                  </span>
                </label>
              )}

              <p data-testid="invoice-grand-total"><b>Grand total:</b> {formatRupiah(totals.grand)}</p>
              <div className="purchase-form-actions">
                <button className="ghost" type="button" onClick={() => setView("list")}>Batal</button>
                <button className="primary" disabled={busy} onClick={save} data-testid="save-invoice-btn">{busy ? "Menyimpan…" : "Simpan Faktur"}</button>
              </div>
            </div>
          )}
        </div>
      )}

      {view === "detail" && detail && (
        <div className="card purchase-detail-card" data-testid="invoice-detail">
          <PurchaseLifecycleStrip
            stages={buildPurchaseLifecycle({
              order: { id: detail.purchase_order_id, po_number: detail.po_number },
              receipt: detail.receipt_id
                ? { id: detail.receipt_id, receipt_number: detail.receipt_number }
                : null,
              invoice: detail,
              payment: (detail.payments || [])[0],
            })}
          />
          <div className="purchase-detail-meta" data-testid="invoice-detail-header">
            <div>
              <span className="label">No Faktur</span>
              <span className="value mono" data-testid="detail-invoice-number">
                {detail.invoice_number}
                {detail.precedes_goods_receipt && <span className="purchase-badge-early">Faktur mendahului barang</span>}
              </span>
            </div>
            <div>
              <span className="label">Supplier</span>
              <span className="value" data-testid="detail-supplier">{detail.supplier || "—"}</span>
            </div>
            <div>
              <span className="label">Tanggal Faktur</span>
              <span className="value" data-testid="detail-invoice-date">{formatPurchaseDate(detail.invoice_date)}</span>
            </div>
            <div>
              <span className="label">Jatuh Tempo</span>
              <span className="value" data-testid="detail-due-date">{dueDateLabel(detail.due_date)}</span>
            </div>
            <div>
              <span className="label">Grand Total</span>
              <span className="value" data-testid="detail-grand-total">{formatRupiah(detail.grand_total)}</span>
            </div>
            <div>
              <span className="label">Sudah Dibayar</span>
              <span className="value">{formatRupiah(detail.paid_total)}</span>
            </div>
            <div>
              <span className="label">Sisa Tagihan</span>
              <span className="value" data-testid="detail-outstanding">{formatRupiah(detail.outstanding_amount)}</span>
            </div>
            <div>
              <span className="label">Payment Status</span>
              <span className={purchaseStatusPillClass(detail.payment_status)} data-testid="detail-payment-status">{detail.payment_status}</span>
            </div>
            <div>
              <span className="label">Accounting Status</span>
              <span className="value" data-testid="invoice-detail-accounting-status">{accountingLabel(detail.accounting_status)}</span>
            </div>
            <div>
              <span className="label">Due status</span>
              <span className={dueStatusPillClass(invoiceDueDisplayStatus(detail))} data-testid="detail-due-status">
                {invoiceDueDisplayStatus(detail)}
              </span>
            </div>
          </div>

          <div className="purchase-detail-meta" data-testid="invoice-cross-document-links">
            <div>
              <span className="label">Pesanan Pembelian</span>
              {detail.purchase_order_id ? (
                <Link className="value mono purchase-doc-link" data-testid="detail-po-number" to={`/pembelian/pesanan-pembelian?id=${encodeURIComponent(detail.purchase_order_id)}`}>
                  {detail.po_number || detail.purchase_order_id}
                </Link>
              ) : <span className="value">—</span>}
            </div>
            <div>
              <span className="label">Penerimaan Barang</span>
              {detail.receipt_id ? (
                <Link className="value mono purchase-doc-link" to={`/pembelian/penerimaan-barang?id=${encodeURIComponent(detail.receipt_id)}`}>
                  {detail.receipt_number || detail.receipt_id}
                </Link>
              ) : <span className="value">—</span>}
            </div>
          </div>

          <div className="purchase-table-wrap">
            <table className="prod-table" data-testid="invoice-detail-lines">
              <thead>
                <tr>
                  <th>Barang</th>
                  <th className="num">Qty</th>
                  <th className="num">Harga</th>
                  <th className="num">Subtotal</th>
                </tr>
              </thead>
              <tbody>
                {(detail.lines || []).length === 0 && (
                  <tr><td colSpan={4} className="muted small">Tidak ada baris item.</td></tr>
                )}
                {(detail.lines || []).map((l) => (
                  <tr key={l.line_id || `${l.item_name}-${l.invoice_qty}`}>
                    <td>{l.item_name}</td>
                    <td className="num">{fmtQty(l.invoice_qty || l.quantity)} {l.unit || ""}</td>
                    <td className="num">{formatRupiah(l.unit_price)}</td>
                    <td className="num">{formatRupiah(l.line_subtotal)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="purchase-detail-meta" style={{ marginTop: 12 }} data-testid="invoice-accounting-section">
            {detail.accounting_journal_id ? (
              <div>
                <span className="label">Journal</span>
                <span className="value mono" data-testid="invoice-detail-accounting-journal">{detail.accounting_journal_id}</span>
              </div>
            ) : null}
            {detail.accounting_ready === false && String(detail.accounting_status || "not_posted") === "not_posted" ? (
              <div>
                <span className="label">Kesiapan</span>
                <span className="value muted" data-testid="invoice-accounting-not-ready">
                  Pengaturan Akuntansi Pembelian belum siap. Post Accounting dinonaktifkan.
                </span>
              </div>
            ) : null}
          </div>
          <div className="purchase-detail-actions" style={{ marginTop: 8 }}>
            {canPostAccounting && String(detail.accounting_status || "not_posted") === "not_posted" && (
              <button
                type="button"
                className="primary"
                data-testid="btn-post-invoice-accounting"
                disabled={acctBusy || detail.can_post_accounting === false}
                title={detail.can_post_accounting === false ? (detail.accounting_ready === false ? "Pengaturan Akuntansi Pembelian belum dikonfigurasi." : "") : undefined}
                onClick={() => { setAcctErr(""); setPostConfirm(true); }}
              >
                Post Accounting
              </button>
            )}
            {canReverseAccounting && String(detail.accounting_status || "") === "posted" && (
              <button
                type="button"
                className="ghost"
                data-testid="btn-reverse-invoice-accounting"
                disabled={acctBusy || detail.can_reverse_accounting === false}
                title={detail.can_reverse_accounting === false ? "Faktur tidak dapat di-reverse karena masih memiliki pembayaran aktif." : undefined}
                onClick={() => { setAcctErr(""); setReverseReason(""); setReverseConfirm(true); }}
              >
                Reverse Accounting
              </button>
            )}
          </div>

          {(detail.payments || []).length > 0 && (
            <div className="purchase-table-wrap" style={{ marginTop: 12 }} data-testid="invoice-payment-history">
              <table className="prod-table">
                <thead>
                  <tr>
                    <th>No Bayar</th>
                    <th>Tgl Pengajuan</th>
                    <th>Status Pengajuan</th>
                    <th className="num">Jumlah</th>
                  </tr>
                </thead>
                <tbody>
                  {(detail.payments || []).map((p) => (
                    <tr key={p.id}>
                      <td className="mono">
                        <Link className="purchase-doc-link" to={`/pembelian/pembayaran-pembelian?id=${encodeURIComponent(p.id)}`}>
                          {p.payment_number}
                        </Link>
                      </td>
                      <td>{p.submitted_at ? formatPurchaseDateTime(p.submitted_at) : (p.payment_date || "—")}</td>
                      <td>{p.submission_status_label || p.list_status || p.workflow_status || "—"}</td>
                      <td className="num">{formatRupiah(p.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <PurchaseDocuments
            entityType="purchase_invoice"
            entityId={detail.id}
            canEdit={canCreate}
            hint="Invoice supplier / lampiran faktur."
            testId="inv-documents"
          />
          <PurchaseActivityHistory doc={detail} testId="invoice-activity" />
          <div className="purchase-detail-actions">
            {canCreate && (
              <button
                type="button"
                className="ghost danger"
                data-testid="btn-hapus-inv-detail"
                disabled={detail.can_cancel === false}
                title={detail.can_cancel === false ? (detail.cancel_block_reason || "") : undefined}
                onClick={() => openCancel(detail)}
              >
                Hapus
              </button>
            )}
            {Number(detail.outstanding_amount) > 0 && hasPerm("purchase_payments.create") && (
              <button
                type="button"
                className="primary"
                data-testid="btn-ajukan-pembayaran-inv-detail"
                onClick={() => setAjukanOpen(true)}
              >
                Ajukan Pembayaran
              </button>
            )}
            {lastChatRoomId ? (
              <Link className="ghost" data-testid="btn-buka-chat-after-ajukan" to={`/chat/${encodeURIComponent(lastChatRoomId)}`}>
                Buka Chat
              </Link>
            ) : null}
          </div>
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
