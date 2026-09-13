import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { errDetail, formatRupiah, newIdempotencyKey, todayYmd, validatePurchasePaymentAmount } from "../lib/purchaseUi";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";

export default function PembayaranPembelian() {
  const [searchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_payments.create");
  const queryInvoiceId = searchParams.get("purchase_invoice_id") || "";
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(Boolean(canCreate && (queryInvoiceId || searchParams.get("action") === "create")));
  const [eligible, setEligible] = useState([]);
  const [invoiceId, setInvoiceId] = useState(canCreate ? queryInvoiceId : "");
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
    if (!open || !canCreate) return;
    api.listPurchaseInvoices({ with_outstanding: true }).then((r) => setEligible(r.invoices || [])).catch(() => setEligible([]));
  }, [open, canCreate, ok]);

  useEffect(() => {
    if (!invoiceId) { setInvoice(null); return; }
    api.getPurchaseInvoice(invoiceId).then((r) => {
      const inv = r.invoice;
      setInvoice(inv);
      setAmount(String(inv?.outstanding_amount ?? ""));
      if (Number(inv?.outstanding_amount || 0) <= 0) {
        setErr("Faktur ini tidak memiliki sisa tagihan.");
      }
    }).catch((ex) => setErr(errDetail(ex)));
  }, [invoiceId]);

  const save = async () => {
    if (busy || !invoice || !canCreate) return;
    const outstanding = Number(invoice.outstanding_amount || 0);
    if (outstanding <= 0) {
      setErr("Faktur ini tidak memiliki sisa tagihan.");
      return;
    }
    const feErr = validatePurchasePaymentAmount(amount, outstanding);
    if (feErr) {
      setErr(feErr);
      return;
    }
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
      setInvoice(null);
      setAmount("");
      setNotes("");
      await load();
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
        {canCreate && <button className="primary" data-testid="btn-create-payment" onClick={() => { setOpen(true); setErr(""); }}><Plus size={14} /> Buat Pembayaran</button>}
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

      {open && canCreate && (
        <div className="modal" role="dialog" data-testid="payment-modal">
          <div className="modal-inner" onClick={(e) => e.stopPropagation()}>
            <h3>Pembayaran Pembelian</h3>
            <FormField id="payment-invoice" label="Faktur" isRequired value={invoiceId}>
              <select value={invoiceId} onChange={(e) => { setInvoiceId(e.target.value); setErr(""); }} data-testid="invoice-selector">
                <option value="">Pilih faktur outstanding</option>
                {eligible.map((inv) => <option key={inv.id} value={inv.id}>{inv.invoice_number} · sisa {formatRupiah(inv.outstanding_amount)}</option>)}
              </select>
            </FormField>
            {invoice && Number(invoice.outstanding_amount) > 0 && (
              <div data-testid="payment-readonly">
                <FormProgress
                  percentage={Math.round(([paymentDate, amount].filter(Boolean).length / 2) * 100)}
                  completed={[paymentDate, amount].filter(Boolean).length}
                  total={2}
                />
                <FormSection id="payment-info" title="Informasi Faktur" status="neutral">
                  <p>No Faktur: {invoice.invoice_number}</p>
                  <p>No Pesanan Pembelian: {invoice.po_number}</p>
                  <p>Supplier: {invoice.supplier}</p>
                  <p>Grand Total: {formatRupiah(invoice.grand_total)}</p>
                  <p data-testid="paid-total">Sudah Dibayar: {formatRupiah(invoice.paid_total)}</p>
                  <p data-testid="outstanding">Sisa Tagihan: {formatRupiah(invoice.outstanding_amount)}</p>
                </FormSection>
                <FormSection id="payment-form" title="Detail Pembayaran" status={computeStatus([{ value: paymentDate, isRequired: true }, { value: amount, isRequired: true }])}>
                  <FormField id="payment-date-field" label="Tanggal Pembayaran" isRequired value={paymentDate}>
                    <input type="date" value={paymentDate} onChange={(e) => setPaymentDate(e.target.value)} data-testid="payment-date" />
                  </FormField>
                  <FormField id="payment-amount-field" label="Jumlah Pembayaran" isRequired value={amount}>
                    <input type="number" min="0" step="any" value={amount} onChange={(e) => setAmount(e.target.value)} data-testid="payment-amount" />
                  </FormField>
                  <FormField id="payment-notes-field" label="Catatan" value={notes}>
                    <input value={notes} onChange={(e) => setNotes(e.target.value)} />
                  </FormField>
                </FormSection>
              </div>
            )}
            <div className="delete-modal-actions">
              <button className="ghost" type="button" onClick={() => setOpen(false)} disabled={busy}>Batal</button>
              <button
                className="primary"
                disabled={busy || !invoice || Number(invoice?.outstanding_amount || 0) <= 0}
                onClick={save}
                data-testid="save-payment"
              >
                {busy ? "Menyimpan…" : "Simpan"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
