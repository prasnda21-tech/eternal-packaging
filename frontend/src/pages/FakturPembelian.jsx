import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { errDetail, formatRupiah, newIdempotencyKey, todayYmd } from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";

export default function FakturPembelian() {
  const [searchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_invoices.create");
  const queryPoId = searchParams.get("purchase_order_id") || "";
  const [view, setView] = useState(searchParams.get("action") === "create" || queryPoId ? "create" : "list");
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [detail, setDetail] = useState(null);
  const [eligible, setEligible] = useState([]);
  const [poId, setPoId] = useState(queryPoId);
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
    api.listPurchaseOrders({ eligible_for_invoice: true }).then((r) => setEligible(r.orders || [])).catch(() => setEligible([]));
    if (poId) loadPreview(poId);
  }, [view, poId, loadPreview]);

  const totals = useMemo(() => {
    if (!preview) return { grand: 0 };
    let grand = 0;
    (preview.lines || []).forEach((l) => {
      grand += (Number(qty[l.purchase_order_line_id]) || 0) * (Number(l.unit_price) || 0);
    });
    return { grand };
  }, [preview, qty]);

  const save = async () => {
    setBusy(true); setErr("");
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
    } catch (ex) { setErr(errDetail(ex)); }
    finally { setBusy(false); }
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
                    <button className="ghost sm" onClick={async () => { setDetail((await api.getPurchaseInvoice(r.id)).invoice); setView("detail"); }}>Detail</button>
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
              <label>Tanggal faktur<input type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} data-testid="invoice-date-input" /></label>
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
              <p data-testid="invoice-grand-total">Grand total: {formatRupiah(totals.grand)}</p>
              <button className="primary" disabled={busy} onClick={save} data-testid="save-invoice-btn">{busy ? "Menyimpan…" : "Simpan Faktur"}</button>
            </div>
          )}
        </div>
      )}

      {view === "detail" && detail && (
        <div className="card" data-testid="invoice-detail">
          <p><b>{detail.invoice_number}</b> · {detail.payment_status}</p>
          <p>PB {detail.po_number} · {detail.supplier}</p>
          <p>Grand total {formatRupiah(detail.grand_total)} · Dibayar {formatRupiah(detail.paid_total)} · Sisa {formatRupiah(detail.outstanding_amount)}</p>
          {Number(detail.outstanding_amount) > 0 && hasPerm("purchase_payments.create") && (
            <Link className="primary" to={`/pembelian/pembayaran-pembelian?purchase_invoice_id=${detail.id}&action=create`}>Buat Pembayaran</Link>
          )}
        </div>
      )}
    </div>
  );
}
