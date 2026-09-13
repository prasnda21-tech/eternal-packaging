import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { errDetail, formatRupiah, newIdempotencyKey, todayYmd } from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";

export default function FakturPembelian() {
  const [searchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_invoices.create");
  const queryPoId = searchParams.get("purchase_order_id") || "";
  const wantCreate = searchParams.get("action") === "create" || Boolean(queryPoId);
  const [view, setView] = useState(wantCreate && canCreate ? "create" : "list");
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [detail, setDetail] = useState(null);
  const [eligible, setEligible] = useState([]);
  const [poId, setPoId] = useState(canCreate ? queryPoId : "");
  const [preview, setPreview] = useState(null);
  const [invoiceDate, setInvoiceDate] = useState(todayYmd());
  const [qty, setQty] = useState({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setErr("");
    try { setRows((await api.listPurchaseInvoices({ q })).invoices || []); }
    catch (ex) { setErr(errDetail(ex)); }
    finally { setLoading(false); }
  }, [q]);
  useEffect(() => { if (view === "list") load(); }, [load, view]);

  const loadPreview = useCallback(async (id) => {
    if (!id) { setPreview(null); return; }
    try {
      const p = await api.purchaseInvoicePreview(id);
      setPreview(p);
      const map = {};
      (p.lines || []).forEach((l) => { map[l.purchase_order_line_id] = l.remaining_invoice_qty; });
      setQty(map);
    } catch (ex) { setErr(errDetail(ex)); }
  }, []);

  useEffect(() => {
    if (view !== "create") return;
    if (!canCreate) {
      setView("list");
      setErr("Anda tidak memiliki izin membuat Faktur Pembelian.");
      return;
    }
    api.listPurchaseOrders({ eligible_for_invoice: true }).then((r) => setEligible(r.orders || [])).catch(() => setEligible([]));
    if (poId) loadPreview(poId);
  }, [view, poId, loadPreview, canCreate]);

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
    setBusy(true); setErr(""); setOk("");
    try {
      const lines = (preview.lines || []).map((l) => ({
        purchase_order_line_id: l.purchase_order_line_id,
        invoice_qty: qty[l.purchase_order_line_id],
      })).filter((l) => Number(l.invoice_qty) > 0);
      const res = await api.createPurchaseInvoice({
        purchase_order_id: preview.purchase_order_id,
        invoice_date: invoiceDate,
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

  return (
    <div className="prod-page" data-testid="faktur-pembelian-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1>Faktur Pembelian</h1>
          <p className="subhead">Faktur mengikuti harga satuan Pesanan Pembelian. HPP tidak diubah dari faktur.</p>
        </div>
        {view === "list" && canCreate && <button className="primary" data-testid="btn-create-invoice" onClick={() => setView("create")}><Plus size={14} /> Buat Faktur</button>}
        {view !== "list" && <button className="ghost" onClick={() => { setView("list"); load(); }}>Kembali ke daftar</button>}
      </div>
      {err && <div className="signal-strip" role="alert"><AlertCircle size={16} /><span>{err}</span></div>}
      {ok && <p className="muted small" data-testid="invoice-success">{ok}</p>}

      {view === "list" && (
        <>
          <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load(); }}>
            <Search size={14} /><input value={q} onChange={(e) => setQ(e.target.value)} data-testid="search-invoice-input" placeholder="Cari no faktur / PB / supplier" />
          </form>
          <table className="prod-table" data-testid="invoices-table">
            <thead><tr><th>No Faktur</th><th>No PB</th><th>Supplier</th><th className="num">Total</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {loading && <tr><td colSpan={6}>Memuat…</td></tr>}
              {!loading && rows.length === 0 && <tr><td colSpan={6} className="muted">Belum ada faktur.</td></tr>}
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.invoice_number}</td>
                  <td className="mono">{r.po_number}</td>
                  <td>{r.supplier}</td>
                  <td className="num">{formatRupiah(r.grand_total)}</td>
                  <td>{r.payment_status}</td>
                  <td>
                    <button className="ghost sm" onClick={() => openDetail(r.id)}>Detail</button>
                    {Number(r.outstanding_amount) > 0 && hasPerm("purchase_payments.create") && (
                      <Link className="primary sm" to={`/pembelian/pembayaran-pembelian?purchase_invoice_id=${r.id}&action=create`}>Bayar</Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {view === "create" && (
        <div className="card" data-testid="invoice-create-form">
          {!queryPoId && (
            <label>Pesanan Pembelian
              <select value={poId} onChange={(e) => setPoId(e.target.value)} data-testid="po-selector">
                <option value="">Pilih PO eligible</option>
                {eligible.map((o) => <option key={o.id} value={o.id}>{o.po_number} · {o.supplier}</option>)}
              </select>
            </label>
          )}
          {preview && (
            <div className="po-snapshot-card" data-testid="po-snapshot-card">
              <p>No PB: <span data-testid="snapshot-po-number">{preview.po_number}</span> · Supplier: {preview.supplier}</p>
              <FormProgress
                percentage={Math.round(([invoiceDate, Object.values(qty).some(v => Number(v) > 0)].filter(Boolean).length / 2) * 100)}
                completed={[invoiceDate, Object.values(qty).some(v => Number(v) > 0)].filter(Boolean).length}
                total={2}
              />
              <FormSection id="invoice-header" title="Informasi Faktur" status={computeStatus([{ value: invoiceDate, isRequired: true }])}>
                <FormField id="invoice-date" label="Tanggal faktur" isRequired value={invoiceDate}>
                  <input type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} data-testid="invoice-date-input" />
                </FormField>
              </FormSection>
              <FormSection id="invoice-lines" title="Detail Item Faktur" status={Object.values(qty).some(v => Number(v) > 0) ? "complete" : "incomplete"}>
                <table className="prod-table">
                  <thead><tr><th>Item</th><th>Sisa</th><th>Qty faktur</th><th>Harga</th><th>Subtotal</th></tr></thead>
                  <tbody>
                    {(preview.lines || []).map((l) => (
                      <tr key={l.purchase_order_line_id}>
                        <td>{l.item_name}</td>
                        <td className="num">{fmtQty(l.remaining_invoice_qty)}</td>
                        <td><input type="number" min="0" value={qty[l.purchase_order_line_id] ?? ""} onChange={(e) => setQty((p) => ({ ...p, [l.purchase_order_line_id]: e.target.value }))} data-testid="qty-input" /></td>
                        <td className="num">{formatRupiah(l.unit_price)}</td>
                        <td className="num">{formatRupiah((Number(qty[l.purchase_order_line_id]) || 0) * (Number(l.unit_price) || 0))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </FormSection>
              <p data-testid="invoice-grand-total">Grand total: {formatRupiah(totals.grand)}</p>
              <button className="primary" disabled={busy} onClick={save} data-testid="save-invoice-btn">{busy ? "Menyimpan…" : "Simpan Faktur"}</button>
            </div>
          )}
        </div>
      )}

      {view === "detail" && detail && (
        <div className="card" data-testid="invoice-detail">
          <div data-testid="invoice-detail-header">
            <p><b data-testid="detail-invoice-number">{detail.invoice_number}</b></p>
            <p>No Pesanan Pembelian: <span className="mono" data-testid="detail-po-number">{detail.po_number}</span></p>
            <p>Supplier: <span data-testid="detail-supplier">{detail.supplier || "—"}</span></p>
            <p>Tanggal: <span data-testid="detail-invoice-date">{detail.invoice_date || "—"}</span></p>
            <p>Status Pembayaran: <span data-testid="detail-payment-status">{detail.payment_status}</span></p>
          </div>

          <table className="prod-table" data-testid="invoice-detail-lines" style={{ marginTop: 12 }}>
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
                <tr key={l.line_id || l.purchase_order_line_id}>
                  <td>{l.item_name}</td>
                  <td className="num">{fmtQty(l.invoice_qty ?? l.quantity)} {l.unit || ""}</td>
                  <td className="num">{formatRupiah(l.unit_price)}</td>
                  <td className="num">{formatRupiah(l.line_subtotal)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div data-testid="invoice-detail-summary" style={{ marginTop: 12 }}>
            <p>Subtotal: <span data-testid="detail-subtotal">{formatRupiah(detail.subtotal ?? detail.grand_total)}</span></p>
            <p>Grand Total: <span data-testid="detail-grand-total">{formatRupiah(detail.grand_total)}</span></p>
            <p>Total Dibayar: <span data-testid="detail-paid-total">{formatRupiah(detail.paid_total)}</span></p>
            <p>Sisa Tagihan: <span data-testid="detail-outstanding">{formatRupiah(detail.outstanding_amount)}</span></p>
          </div>

          <div data-testid="invoice-payment-history" style={{ marginTop: 16 }}>
            <h3>Riwayat Pembayaran</h3>
            <table className="prod-table">
              <thead>
                <tr>
                  <th>No Pembayaran</th>
                  <th>Tanggal</th>
                  <th className="num">Nominal</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(detail.payments || []).length === 0 && (
                  <tr><td colSpan={4} className="muted small" data-testid="invoice-payments-empty">Belum ada pembayaran.</td></tr>
                )}
                {(detail.payments || []).map((p) => (
                  <tr key={p.id} data-testid={`invoice-payment-row-${p.id}`}>
                    <td className="mono">{p.payment_number}</td>
                    <td>{p.payment_date || "—"}</td>
                    <td className="num">{formatRupiah(p.amount)}</td>
                    <td>{p.payment_state || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {Number(detail.outstanding_amount) > 0 && hasPerm("purchase_payments.create") && (
            <Link className="primary" to={`/pembelian/pembayaran-pembelian?purchase_invoice_id=${detail.id}&action=create`}>Buat Pembayaran</Link>
          )}
        </div>
      )}
    </div>
  );
}
