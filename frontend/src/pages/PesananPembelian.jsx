import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { errDetail, formatRupiah, newIdempotencyKey, todayYmd } from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";

export default function PesananPembelian() {
  const [searchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_orders.create");
  const queryRequestId = searchParams.get("request_id") || "";
  const [view, setView] = useState(searchParams.get("action") === "create" || queryRequestId ? "create" : "list");
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [detail, setDetail] = useState(null);

  const [eligible, setEligible] = useState([]);
  const [requestId, setRequestId] = useState(queryRequestId);
  const [preview, setPreview] = useState(null);
  const [supplier, setSupplier] = useState("");
  const [orderDate, setOrderDate] = useState(todayYmd());
  const [qty, setQty] = useState({});
  const [price, setPrice] = useState({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await api.listPurchaseOrders({ q });
      setRows(res.orders || []);
    } catch (ex) { setErr(errDetail(ex)); }
    finally { setLoading(false); }
  }, [q]);

  useEffect(() => { if (view === "list") load(); }, [load, view]);

  const loadEligible = useCallback(async () => {
    try {
      const res = await api.listPurchaseRequests({ eligible_for_po: true });
      setEligible(res.requests || []);
    } catch { setEligible([]); }
  }, []);

  const loadPreview = useCallback(async (id) => {
    if (!id) { setPreview(null); return; }
    setErr("");
    try {
      const p = await api.purchaseOrderPreview(id);
      setPreview(p);
      const qMap = {}; const pMap = {};
      (p.lines || []).forEach((l) => {
        qMap[l.request_line_id] = l.remaining_order_qty;
        pMap[l.request_line_id] = l.unit_price || "";
      });
      setQty(qMap); setPrice(pMap);
    } catch (ex) { setErr(errDetail(ex)); setPreview(null); }
  }, []);

  useEffect(() => {
    if (view === "create") {
      loadEligible();
      if (requestId) loadPreview(requestId);
    }
  }, [view, requestId, loadEligible, loadPreview]);

  const save = async () => {
    if (!preview) return;
    setBusy(true); setErr("");
    try {
      const lines = (preview.lines || []).map((l) => ({
        request_line_id: l.request_line_id,
        ordered_qty: qty[l.request_line_id],
        unit_price: price[l.request_line_id],
      })).filter((l) => Number(l.ordered_qty) > 0);
      const res = await api.createPurchaseOrder({
        request_id: preview.request_id,
        supplier,
        order_date: orderDate,
        lines,
        idempotency_key: newIdempotencyKey(),
      });
      setDetail(res.order);
      setView("detail");
    } catch (ex) { setErr(errDetail(ex)); }
    finally { setBusy(false); }
  };

  const openDetail = async (id) => {
    try {
      const res = await api.getPurchaseOrder(id);
      setDetail(res.order);
      setView("detail");
    } catch (ex) { setErr(errDetail(ex)); }
  };

  return (
    <div className="prod-page" data-testid="pesanan-pembelian-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="pesanan-pembelian-title">Pesanan Pembelian</h1>
          <p className="subhead">Harga satuan PO adalah sumber HPP. Stok fisik tidak bertambah saat PO dibuat.</p>
        </div>
        {view === "list" && canCreate && (
          <button className="primary" data-testid="btn-create-po" onClick={() => setView("create")}><Plus size={14} /> Buat Pesanan Pembelian</button>
        )}
        {view !== "list" && <button className="ghost" onClick={() => { setView("list"); load(); }}>Kembali ke daftar</button>}
      </div>
      {err && <div className="signal-strip" role="alert"><AlertCircle size={16} /><span>{err}</span></div>}

      {view === "list" && (
        <>
          <div className="prod-filters">
            <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load(); }}>
              <Search size={14} />
              <input value={q} onChange={(e) => setQ(e.target.value)} data-testid="po-search" placeholder="Cari no PB / supplier / permintaan" />
            </form>
          </div>
          <div className="prod-table-wrap">
            <table className="prod-table" data-testid="po-table">
              <thead>
                <tr>
                  <th>No Pesanan Pembelian</th>
                  <th>Permintaan</th>
                  <th>Supplier</th>
                  <th>Tanggal</th>
                  <th className="num">Total</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr><td colSpan={7} className="muted small">Memuat…</td></tr>}
                {!loading && rows.length === 0 && <tr><td colSpan={7} className="muted small">Belum ada pesanan pembelian.</td></tr>}
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{r.po_number}</td>
                    <td className="mono">{r.request_number}</td>
                    <td>{r.supplier}</td>
                    <td>{r.order_date}</td>
                    <td className="num">{formatRupiah(r.grand_total)}</td>
                    <td>{r.status}</td>
                    <td><button className="ghost sm" onClick={() => openDetail(r.id)}>Detail</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {view === "create" && (
        <div className="card" data-testid="po-create-form">
          {!queryRequestId && (
            <label>Permintaan
              <select value={requestId} onChange={(e) => setRequestId(e.target.value)} data-testid="request-selector">
                <option value="">Pilih Permintaan eligible</option>
                {eligible.map((r) => <option key={r.id} value={r.id}>{r.request_number} · sisa {fmtQty(r.total_remaining_order_qty)}</option>)}
              </select>
            </label>
          )}
          {preview && (
            <div className="po-snapshot-card" data-testid="request-snapshot-card">
              <p>No Permintaan: <span data-testid="snapshot-request-number">{preview.request_number}</span></p>
              <label>Supplier<input value={supplier} onChange={(e) => setSupplier(e.target.value)} data-testid="po-supplier" /></label>
              <label>Tanggal<input type="date" value={orderDate} onChange={(e) => setOrderDate(e.target.value)} data-testid="po-date" /></label>
              <table className="prod-table">
                <thead><tr><th>Item</th><th>Sisa</th><th>Qty PO</th><th>Harga satuan</th><th>Subtotal</th></tr></thead>
                <tbody>
                  {(preview.lines || []).map((l) => (
                    <tr key={l.request_line_id}>
                      <td>{l.item_name}</td>
                      <td className="num">{fmtQty(l.remaining_order_qty)}</td>
                      <td><input type="number" min="0" max={l.remaining_order_qty} value={qty[l.request_line_id] ?? ""} onChange={(e) => setQty((p) => ({ ...p, [l.request_line_id]: e.target.value }))} data-testid="qty-input" /></td>
                      <td><input type="number" min="0" value={price[l.request_line_id] ?? ""} onChange={(e) => setPrice((p) => ({ ...p, [l.request_line_id]: e.target.value }))} data-testid="unit-price-input" /></td>
                      <td className="num">{formatRupiah((Number(qty[l.request_line_id]) || 0) * (Number(price[l.request_line_id]) || 0))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button className="primary" disabled={busy} onClick={save} data-testid="save-po">{busy ? "Menyimpan…" : "Simpan Pesanan Pembelian"}</button>
            </div>
          )}
        </div>
      )}

      {view === "detail" && detail && (
        <div className="card" data-testid="po-detail">
          <p><b>No Pesanan Pembelian</b> <span data-testid="detail-po-number">{detail.po_number}</span></p>
          <p>Permintaan: {detail.request_number}</p>
          <p>Supplier: {detail.supplier}</p>
          <p>Total: {formatRupiah(detail.grand_total)}</p>
          <p>Penerimaan: {detail.receiving_status} · Faktur: {detail.invoice_status}</p>
          <table className="prod-table">
            <thead><tr><th>Item</th><th>Qty</th><th>Harga</th><th>Subtotal</th><th>Diterima</th><th>Difaktur</th></tr></thead>
            <tbody>
              {(detail.lines || []).map((l) => (
                <tr key={l.line_id}>
                  <td>{l.item_name}</td>
                  <td className="num">{fmtQty(l.ordered_qty)} {l.unit}</td>
                  <td className="num">{formatRupiah(l.unit_price)}</td>
                  <td className="num">{formatRupiah(l.line_subtotal)}</td>
                  <td className="num">{fmtQty(l.cumulative_received_qty)} / sisa {fmtQty(l.remaining_receivable_qty)}</td>
                  <td className="num">{fmtQty(l.cumulative_invoiced_qty)} / sisa {fmtQty(l.remaining_invoice_qty)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {Number(detail.receiving_progress?.remaining_qty) > 0 && (
            <Link className="ghost" to={`/pembelian/penerimaan-barang?purchase_order_id=${detail.id}&action=receive`}>Penerimaan Barang</Link>
          )}
          {Number(detail.invoice_progress?.remaining_qty) > 0 && hasPerm("purchase_invoices.create") && (
            <Link className="primary" data-testid="btn-buat-faktur" to={`/pembelian/faktur-pembelian?purchase_order_id=${detail.id}&action=create`}>Buat Faktur Pembelian</Link>
          )}
        </div>
      )}
    </div>
  );
}
