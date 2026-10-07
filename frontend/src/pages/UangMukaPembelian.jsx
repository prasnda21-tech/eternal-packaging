import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import {
  errDetail,
  formatRupiah,
  friendlyLoadError,
  newIdempotencyKey,
  purchaseStatusPillClass,
  todayYmd,
} from "../lib/purchaseUi";
import PurchasingFlowNav from "./PurchasingFlowNav";
import "./purchasePages.css";

function statusLabel(row) {
  if (row?.cancelled || row?.status_label === "Dibatalkan") return "Dibatalkan";
  if (row?.status_label === "Draft" || row?.status === "BELUM DIAJUKAN") return "Belum Diajukan";
  return row?.status_label || "—";
}

export default function UangMukaPembelian() {
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_orders.create") || hasPerm("purchase_payments.create");
  const [rows, setRows] = useState([]);
  const [orders, setOrders] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [detail, setDetail] = useState(null);
  const [allocations, setAllocations] = useState([]);
  const [poId, setPoId] = useState("");
  const [amount, setAmount] = useState("");
  const [paymentDate, setPaymentDate] = useState(todayYmd());
  const [notes, setNotes] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await api.listPurchaseSupplierAdvances();
      setRows(res.advances || []);
    } catch (ex) {
      setErr(friendlyLoadError(ex, "Tidak dapat memuat uang muka."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) => [row.advance_number, row.supplier_name, row.supplier, row.po_number]
      .some((value) => String(value || "").toLowerCase().includes(needle)));
  }, [rows, q]);

  const openCreate = async () => {
    setErr("");
    setCreating(true);
    setDetail(null);
    try {
      const res = await api.listPurchaseOrders();
      setOrders(res.orders || []);
    } catch (ex) {
      setErr(errDetail(ex));
    }
  };

  const save = async () => {
    if (busy || !canCreate) return;
    if (!poId) {
      setErr("Pilih Pesanan Pembelian.");
      return;
    }
    if (!(Number(amount) > 0)) {
      setErr("Nilai uang muka harus lebih besar dari 0.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await api.createPurchaseSupplierAdvance({
        purchase_order_id: poId,
        amount: Number(amount),
        payment_date: paymentDate,
        notes,
        idempotency_key: newIdempotencyKey(),
      });
      setCreating(false);
      setPoId("");
      setAmount("");
      setNotes("");
      setOk("Uang muka tercatat. Ajukan lalu konfirmasi dibayar agar saldo tersedia.");
      await load();
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const openDetail = async (row) => {
    setCreating(false);
    setDetail(row);
    setAllocations([]);
    try {
      const res = await api.listPurchaseSupplierAdvanceAllocations({ supplier_advance_id: row.id });
      setAllocations(res.allocations || []);
    } catch (ex) {
      setErr(errDetail(ex));
    }
  };

  const act = async (row, action) => {
    if (busy) return;
    setBusy(true);
    setErr("");
    try {
      if (action === "submit") await api.submitPurchaseSupplierAdvance(row.id);
      if (action === "confirm") await api.confirmPurchaseSupplierAdvance(row.id, { payment_date: row.payment_date });
      if (action === "cancel") await api.cancelPurchaseSupplierAdvance(row.id);
      setOk(action === "confirm" ? "Uang muka dikonfirmasi dibayar." : action === "cancel" ? "Uang muka dibatalkan." : "Uang muka diajukan.");
      await load();
      if (detail?.id === row.id) setDetail(null);
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="prod-page purchase-page" data-testid="uang-muka-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="uang-muka-title">Uang Muka</h1>
          <p className="subhead">Uang muka supplier bersifat opsional dan tidak mengubah stok.</p>
        </div>
        {canCreate && (
          <button className="primary" type="button" data-testid="btn-create-uang-muka" onClick={openCreate}>
            <Plus size={14} /> Uang Muka
          </button>
        )}
      </div>
      <PurchasingFlowNav />
      {err && (
        <div className="purchase-error-panel" role="alert">
          <AlertCircle size={16} color="#a53f2b" /><span>{err}</span>
        </div>
      )}
      {ok && <p className="purchase-inline-ok">{ok}</p>}
      <div className="purchase-search-bar is-toolbar">
        <form className="search-inline" onSubmit={(e) => e.preventDefault()}>
          <Search size={14} />
          <input value={q} onChange={(e) => setQ(e.target.value)} data-testid="uang-muka-search" placeholder="Cari no uang muka / supplier / PO" />
        </form>
      </div>
      <div className="purchase-table-wrap">
        <table className="prod-table" data-testid="uang-muka-table">
          <thead>
            <tr>
              <th>No. Uang Muka</th>
              <th>Tanggal</th>
              <th>Supplier</th>
              <th>No. Pesanan Pembelian</th>
              <th className="num">Nilai Uang Muka</th>
              <th className="num">Sudah Dialokasikan</th>
              <th className="num">Sisa / Tersedia</th>
              <th>Status</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={9}>Memuat…</td></tr>}
            {!loading && visible.length === 0 && <tr><td colSpan={9}>Belum ada uang muka. Pembelian tetap dapat dilanjutkan tanpa uang muka.</td></tr>}
            {visible.map((row) => (
              <tr key={row.id} data-testid="uang-muka-list-row">
                <td className="mono" data-testid="uang-muka-number">{row.advance_number}</td>
                <td>{row.payment_date || "—"}</td>
                <td data-testid="uang-muka-supplier">{row.supplier_name || row.supplier}</td>
                <td className="mono" data-testid="uang-muka-po">{row.po_number}</td>
                <td className="num" data-testid="uang-muka-original">{formatRupiah(row.original_amount)}</td>
                <td className="num" data-testid="uang-muka-allocated">{formatRupiah(row.allocated_amount || 0)}</td>
                <td className="num" data-testid="uang-muka-available">{formatRupiah(row.available_amount || 0)}</td>
                <td><span className={purchaseStatusPillClass(row.status)} data-testid="uang-muka-list-status">{statusLabel(row)}</span></td>
                <td>
                  <button type="button" className="ghost sm" onClick={() => openDetail(row)}>Lihat</button>
                  {row.can_submit && <button type="button" className="ghost sm" onClick={() => act(row, "submit")}>Ajukan</button>}
                  {row.can_confirm_paid && <button type="button" className="ghost sm" onClick={() => act(row, "confirm")}>Konfirmasi Dibayar</button>}
                  {row.can_cancel && <button type="button" className="ghost sm" onClick={() => act(row, "cancel")}>Batalkan</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {creating && (
        <div className="card" data-testid="uang-muka-create" style={{ marginTop: 12 }}>
          <h3>Uang Muka baru</h3>
          <label>
            Pesanan Pembelian
            <select value={poId} onChange={(e) => setPoId(e.target.value)} data-testid="uang-muka-po-select">
              <option value="">Pilih PO</option>
              {orders.map((order) => (
                <option key={order.id} value={order.id}>{order.po_number} — {order.supplier}</option>
              ))}
            </select>
          </label>
          <label>
            Nilai Uang Muka
            <input type="number" min="1" value={amount} onChange={(e) => setAmount(e.target.value)} data-testid="uang-muka-create-amount" />
          </label>
          <label>
            Tanggal
            <input type="date" value={paymentDate} onChange={(e) => setPaymentDate(e.target.value)} />
          </label>
          <label>
            Catatan
            <input value={notes} onChange={(e) => setNotes(e.target.value)} />
          </label>
          <div className="purchase-form-actions">
            <button type="button" className="ghost" onClick={() => setCreating(false)}>Batal</button>
            <button type="button" className="primary" disabled={busy} onClick={save} data-testid="uang-muka-save">Simpan</button>
          </div>
        </div>
      )}
      {detail && (
        <div className="card" data-testid="uang-muka-detail" style={{ marginTop: 12 }}>
          <h3>{detail.advance_number}</h3>
          <p>Supplier: {detail.supplier_name || detail.supplier}</p>
          <p>PO: {detail.po_number}</p>
          <p>Nilai Uang Muka: {formatRupiah(detail.original_amount)}</p>
          <p>Sudah Dialokasikan: {formatRupiah(detail.allocated_amount || 0)}</p>
          <p>Tersedia: {formatRupiah(detail.available_amount || 0)}</p>
          <p>Status: {statusLabel(detail)}</p>
          {(allocations || []).length > 0 && (
            <ul>
              {allocations.map((row) => (
                <li key={row.id}>{row.invoice_number || row.purchase_invoice_id}: {formatRupiah(row.amount)} ({row.status})</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
