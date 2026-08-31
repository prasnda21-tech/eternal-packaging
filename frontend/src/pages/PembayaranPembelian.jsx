import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { errDetail, formatRupiah, newIdempotencyKey, todayYmd } from "../lib/purchaseUi";

export default function PembayaranPembelian() {
  const [searchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_payments.create");
  const queryInvoiceId = searchParams.get("purchase_invoice_id") || "";
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(Boolean(queryInvoiceId) || searchParams.get("action") === "create");
  const [eligible, setEligible] = useState([]);
  const [invoiceId, setInvoiceId] = useState(queryInvoiceId);
  const [invoice, setInvoice] = useState(null);
  const [amount, setAmount] = useState("");
  const [paymentDate, setPaymentDate] = useState(todayYmd());
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setErr("");
    try { setRows((await api.listPurchasePayments({ q })).payments || []); }
    catch (ex) { setErr(errDetail(ex)); }
    finally { setLoading(false); }
  }, [q]);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!open) return;
    api.listPurchaseInvoices({ with_outstanding: true }).then((r) => setEligible(r.invoices || [])).catch(() => setEligible([]));
  }, [open]);

  useEffect(() => {
    if (!invoiceId) { setInvoice(null); return; }
    api.getPurchaseInvoice(invoiceId).then((r) => {
      setInvoice(r.invoice);
      setAmount(String(r.invoice?.outstanding_amount ?? ""));
    }).catch((ex) => setErr(errDetail(ex)));
  }, [invoiceId]);

  const save = async () => {
    setBusy(true); setErr(""); setOk("");
    try {
      const res = await api.createPurchasePayment({
        purchase_invoice_id: invoice.id,
        amount,
        payment_date: paymentDate,
        notes,
        idempotency_key: newIdempotencyKey(),
      });
      setOk(`Pembayaran ${res.payment.payment_number} tersimpan · ${res.payment.payment_state}`);
      setOpen(false);
      setInvoiceId("");
      load();
    } catch (ex) { setErr(errDetail(ex)); }
    finally { setBusy(false); }
  };

  return (
    <div className="prod-page" data-testid="pembayaran-pembelian-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1>Pembayaran Pembelian</h1>
          <p className="subhead">DP, pembayaran sebagian, dan pelunasan. Lebih dari sisa tagihan ditolak.</p>
        </div>
        {canCreate && <button className="primary" data-testid="btn-create-payment" onClick={() => setOpen(true)}><Plus size={14} /> Buat Pembayaran</button>}
      </div>
      {err && <div className="signal-strip" role="alert"><AlertCircle size={16} /><span>{err}</span></div>}
      {ok && <p className="muted" data-testid="payment-success">{ok}</p>}

      <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load(); }}>
        <Search size={14} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cari no pembayaran / faktur" />
      </form>
      <table className="prod-table" data-testid="payments-table">
        <thead><tr><th>No Pembayaran</th><th>Faktur</th><th>PB</th><th>Supplier</th><th className="num">Jumlah</th><th>Status</th><th>Tanggal</th></tr></thead>
        <tbody>
          {loading && <tr><td colSpan={7}>Memuat…</td></tr>}
          {!loading && rows.length === 0 && <tr><td colSpan={7} className="muted">Belum ada pembayaran.</td></tr>}
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="mono">{r.payment_number}</td>
              <td className="mono">{r.invoice_number}</td>
              <td className="mono">{r.po_number}</td>
              <td>{r.supplier}</td>
              <td className="num">{formatRupiah(r.amount)}</td>
              <td>{r.payment_state}</td>
              <td>{r.payment_date}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {open && (
        <div className="modal" role="dialog" data-testid="payment-modal">
          <div className="modal-inner" onClick={(e) => e.stopPropagation()}>
            <h3>Pembayaran Pembelian</h3>
            <label>Faktur
              <select value={invoiceId} onChange={(e) => setInvoiceId(e.target.value)} data-testid="invoice-selector">
                <option value="">Pilih faktur outstanding</option>
                {eligible.map((inv) => <option key={inv.id} value={inv.id}>{inv.invoice_number} · sisa {formatRupiah(inv.outstanding_amount)}</option>)}
              </select>
            </label>
            {invoice && (
              <div data-testid="payment-readonly">
                <p>No Faktur: {invoice.invoice_number}</p>
                <p>No Pesanan Pembelian: {invoice.po_number}</p>
                <p>Supplier: {invoice.supplier}</p>
                <p>Grand Total: {formatRupiah(invoice.grand_total)}</p>
                <p data-testid="paid-total">Sudah Dibayar: {formatRupiah(invoice.paid_total)}</p>
                <p data-testid="outstanding">Sisa Tagihan: {formatRupiah(invoice.outstanding_amount)}</p>
                <label>Tanggal Pembayaran<input type="date" value={paymentDate} onChange={(e) => setPaymentDate(e.target.value)} data-testid="payment-date" /></label>
                <label>Jumlah Pembayaran<input type="number" min="0" value={amount} onChange={(e) => setAmount(e.target.value)} data-testid="payment-amount" /></label>
                <label>Catatan<input value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
              </div>
            )}
            <div className="delete-modal-actions">
              <button className="ghost" type="button" onClick={() => setOpen(false)}>Batal</button>
              <button className="primary" disabled={busy || !invoice} onClick={save} data-testid="save-payment">{busy ? "Menyimpan…" : "Simpan"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
