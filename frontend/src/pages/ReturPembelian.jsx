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

const FINANCIAL_PENDING = "PENDING_FINANCIAL_ALLOCATION";

function wholeQty(value) {
  const number = Number(value);
  return Number.isInteger(number) ? number : NaN;
}

function returnableQty(line) {
  const received = Number(line?.received_qty || 0);
  const returned = Number(line?.confirmed_return_qty || 0);
  if (!Number.isFinite(received) || !Number.isFinite(returned)) return 0;
  return Math.max(0, received - returned);
}

function statusLabel(row) {
  const status = String(row?.status || "");
  if (status === "DRAFT") return "Draf";
  if (status === "CONFIRMED") return "Terkonfirmasi";
  if (status === "CANCELLED") return "Dibatalkan";
  if (status === "REVERSED") return "Direversal";
  return status || "—";
}

function financialLabel(status, rowStatus) {
  if (status === FINANCIAL_PENDING || status === "FINANCIAL_VALUE_UNAVAILABLE") return "Menunggu Alokasi Finansial";
  if (status === "FINANCIALIZED") return "Kredit faktur tercatat";
  if (status === "NO_FINANCIAL_EFFECT" || rowStatus === "DRAFT" || !status) return "Belum terkait kredit faktur";
  return status;
}

function receiptLabel(row, receiptsById) {
  const id = row?.purchase_goods_receipt_id || (row?.purchase_goods_receipt_ids || [])[0] || "";
  if (!id) return "—";
  return receiptsById[id]?.receipt_number || id;
}

function formatReturnDate(value) {
  const text = String(value || "").slice(0, 10);
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return text || "—";
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return new Intl.DateTimeFormat("id-ID", {
    day: "2-digit",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

function warehouseName(id, warehouses) {
  const row = (warehouses || []).find((item) => String(item.id) === String(id || ""));
  return row?.name || row?.warehouse_name || "";
}

function invoiceNumberLabel(credit, numbersById) {
  const named = String(credit?.invoice_number || credit?.purchase_invoice_number || "").trim();
  if (named) return named;
  const id = String(credit?.purchase_invoice_id || "").trim();
  const resolved = id ? String(numbersById[id] || "").trim() : "";
  if (resolved) return resolved;
  return id ? "Faktur Pembelian" : "—";
}

export default function ReturPembelian() {
  const { hasPerm } = useAuth();
  const canMutate = hasPerm("purchase_receipts.receive") || hasPerm("purchase_receipts.sync");
  const [rows, setRows] = useState([]);
  const [receipts, setReceipts] = useState([]);
  const [warehouses, setWarehouses] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [detail, setDetail] = useState(null);
  const [finance, setFinance] = useState(null);
  const [invoiceNumbers, setInvoiceNumbers] = useState({});
  const [receiptId, setReceiptId] = useState("");
  const [receipt, setReceipt] = useState(null);
  const [qtyByLine, setQtyByLine] = useState({});
  const [returnDate, setReturnDate] = useState(todayYmd());
  const [reason, setReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const [ret, rec, warehouseResult] = await Promise.all([
        api.listPurchaseReturns({ include_cancelled: true }),
        api.listPurchaseGoodsReceipts(),
        api.warehouses().catch(() => ({ items: [] })),
      ]);
      setRows(ret.returns || []);
      setReceipts(rec.receipts || []);
      setWarehouses(warehouseResult.items || warehouseResult.warehouses || []);
    } catch (ex) {
      setErr(friendlyLoadError(ex, "Tidak dapat memuat retur pembelian."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const receiptsById = useMemo(() => {
    const map = {};
    receipts.forEach((row) => { map[row.id] = row; });
    return map;
  }, [receipts]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) => [
      row.return_number,
      row.supplier_name,
      row.supplier,
      row.po_number,
      receiptLabel(row, receiptsById),
    ].some((value) => String(value || "").toLowerCase().includes(needle)));
  }, [rows, q, receiptsById]);

  const openCreate = () => {
    setErr("");
    setOk("");
    setCreating(true);
    setDetail(null);
    setFinance(null);
    setReceiptId("");
    setReceipt(null);
    setQtyByLine({});
    setReturnDate(todayYmd());
    setReason("");
  };

  const chooseReceipt = async (id) => {
    setReceiptId(id);
    setReceipt(null);
    setQtyByLine({});
    if (!id) return;
    setBusy(true);
    setErr("");
    try {
      const res = await api.getPurchaseGoodsReceipt(id);
      const doc = res.receipt || null;
      setReceipt(doc);
      const next = {};
      (doc?.lines || []).forEach((line) => {
        next[line.line_id] = "";
      });
      setQtyByLine(next);
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (busy || !canMutate) return;
    if (!receipt?.id) {
      setErr("Pilih penerimaan barang sumber.");
      return;
    }
    const lines = [];
    for (const line of receipt.lines || []) {
      const raw = qtyByLine[line.line_id];
      if (raw === "" || raw == null) continue;
      const qty = wholeQty(raw);
      const remain = returnableQty(line);
      const name = line.item_name || line.item_code || "barang";
      if (!Number.isInteger(qty) || qty <= 0) {
        setErr(`Qty retur ${name} harus lebih besar dari 0.`);
        return;
      }
      if (qty > remain) {
        setErr(`Qty retur ${name} melebihi qty yang masih dapat diretur (${remain}).`);
        return;
      }
      lines.push({
        purchase_goods_receipt_id: receipt.id,
        purchase_goods_receipt_line_id: line.line_id,
        return_qty: qty,
      });
    }
    if (!lines.length) {
      setErr("Isi qty retur lebih besar dari 0 pada minimal satu baris.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const res = await api.createPurchaseReturn({
        return_date: returnDate,
        reason,
        idempotency_key: newIdempotencyKey(),
        lines,
      });
      setCreating(false);
      setOk("Draf retur pembelian tercatat.");
      await load();
      if (res.return) await openDetail(res.return);
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const openDetail = async (row) => {
    setCreating(false);
    setErr("");
    setDetail(row);
    setFinance(null);
    setInvoiceNumbers({});
    try {
      const [doc, fin] = await Promise.all([
        api.getPurchaseReturn(row.id),
        api.getPurchaseReturnFinancial(row.id),
      ]);
      setDetail(doc.return || row);
      setFinance(fin);
      const ids = [...new Set((fin?.credits || []).map((credit) => String(credit.purchase_invoice_id || "").trim()).filter(Boolean))];
      const pairs = await Promise.all(ids.map(async (id) => {
        if ((fin.credits || []).some((credit) => String(credit.purchase_invoice_id || "") === id && (credit.invoice_number || credit.purchase_invoice_number))) {
          return [id, ""];
        }
        try {
          const invoiceDoc = await api.getPurchaseInvoice(id);
          const invoice = invoiceDoc.invoice || invoiceDoc;
          return [id, String(invoice.invoice_number || "").trim()];
        } catch {
          return [id, ""];
        }
      }));
      setInvoiceNumbers(Object.fromEntries(pairs));
    } catch (ex) {
      setErr(errDetail(ex));
    }
  };

  const act = async (action) => {
    if (busy || !detail || !canMutate) return;
    setBusy(true);
    setErr("");
    try {
      const res = action === "confirm"
        ? await api.confirmPurchaseReturn(detail.id, { idempotency_key: newIdempotencyKey() })
        : await api.cancelPurchaseReturn(detail.id, action === "reverse" ? "Pembatalan retur terkonfirmasi" : "");
      setOk(action === "confirm" ? "Retur pembelian dikonfirmasi." : action === "reverse" ? "Retur terkonfirmasi dibatalkan." : "Draf retur dibatalkan.");
      await load();
      if (res.return) await openDetail(res.return);
    } catch (ex) {
      setErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const closeDetail = () => {
    setDetail(null);
    setFinance(null);
    setInvoiceNumbers({});
  };

  const financeStatus = finance?.financial_status || detail?.financial_status;
  const readOnly = detail && (detail.status === "CANCELLED" || detail.status === "REVERSED");
  const detailWarehouseId = (detail?.lines || [])[0]?.warehouse_id || "";
  const detailWarehouse = warehouseName(detailWarehouseId, warehouses);

  return (
    <div className="prod-page purchase-page" data-testid="retur-pembelian-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="retur-pembelian-title">Retur Pembelian</h1>
          <p className="subhead">Retur mengikuti penerimaan barang. Stok fisik berubah hanya setelah konfirmasi.</p>
        </div>
        {canMutate && (
          <button className="primary" type="button" data-testid="btn-create-retur" onClick={openCreate}>
            <Plus size={14} /> Retur
          </button>
        )}
      </div>
      <PurchasingFlowNav />
      {err && (
        <div className="purchase-error-panel" role="alert" data-testid="retur-error">
          <AlertCircle size={16} color="#a53f2b" /><span>{err}</span>
        </div>
      )}
      {ok && <p className="purchase-inline-ok" data-testid="retur-ok">{ok}</p>}
      <div className="purchase-search-bar is-toolbar">
        <form className="search-inline" onSubmit={(e) => e.preventDefault()}>
          <Search size={14} />
          <input value={q} onChange={(e) => setQ(e.target.value)} data-testid="retur-search" placeholder="Cari no retur / supplier / PO / penerimaan" />
        </form>
      </div>
      {loading && <p className="muted small" data-testid="retur-loading">Memuat retur pembelian…</p>}
      <div className="purchase-table-wrap">
        <table className="prod-table purchase-table purchase-table-retur" data-testid="retur-table">
          <thead>
            <tr>
              <th>Nomor Retur</th>
              <th>Tanggal</th>
              <th>Supplier</th>
              <th>Penerimaan</th>
              <th>Pesanan Pembelian</th>
              <th>Status</th>
              <th>Status Finansial</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {!loading && visible.length === 0 && (
              <tr><td colSpan={8} className="muted small" data-testid="retur-empty">Belum ada retur pembelian.</td></tr>
            )}
            {visible.map((row) => (
              <tr key={row.id} data-testid="retur-list-row">
                <td className="mono" data-testid="retur-number">{row.return_number}</td>
                <td>{row.return_date || "—"}</td>
                <td data-testid="retur-supplier">{row.supplier_name || row.supplier || "—"}</td>
                <td className="mono" data-testid="retur-receipt">{receiptLabel(row, receiptsById)}</td>
                <td className="mono" data-testid="retur-po">{row.po_number || "—"}</td>
                <td><span className={purchaseStatusPillClass(row.status)} data-testid="retur-list-status">{statusLabel(row)}</span></td>
                <td data-testid="retur-list-financial">{financialLabel(row.financial_status, row.status)}</td>
                <td>
                  <button type="button" className="ghost sm" data-testid="retur-open" onClick={() => openDetail(row)}>Detail</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {creating && (
        <div className="card" data-testid="retur-create" style={{ marginTop: 12 }}>
          <h3>Retur baru</h3>
          <label>
            Penerimaan Barang
            <select value={receiptId} onChange={(e) => chooseReceipt(e.target.value)} data-testid="retur-receipt-select">
              <option value="">Pilih penerimaan</option>
              {receipts.map((row) => (
                <option key={row.id} value={row.id}>{row.receipt_number || row.id} · {row.po_number || "PO"} · {row.supplier || ""}</option>
              ))}
            </select>
          </label>
          <label>
            Tanggal
            <input type="date" value={returnDate} onChange={(e) => setReturnDate(e.target.value)} data-testid="retur-date" />
          </label>
          <label>
            Alasan
            <input value={reason} onChange={(e) => setReason(e.target.value)} data-testid="retur-reason" />
          </label>
          {receipt && (
            <div className="purchase-table-wrap">
              <table className="prod-table purchase-table purchase-table-excel" data-testid="retur-create-lines">
                <thead>
                  <tr>
                    <th>Barang</th>
                    <th className="num">Diterima</th>
                    <th className="num">Sudah diretur</th>
                    <th className="num">Masih dapat diretur</th>
                    <th>Satuan</th>
                    <th className="num">Qty retur</th>
                  </tr>
                </thead>
                <tbody>
                  {(receipt.lines || []).map((line) => {
                    const remain = returnableQty(line);
                    return (
                      <tr key={line.line_id} data-testid="retur-create-line">
                        <td>{line.item_code} {line.item_name}</td>
                        <td className="num" data-testid="retur-received-qty">{line.received_qty}</td>
                        <td className="num" data-testid="retur-already-returned">{line.confirmed_return_qty || 0}</td>
                        <td className="num" data-testid="retur-returnable-qty">{remain}</td>
                        <td>{line.unit || "PCS"}</td>
                        <td className="num">
                          <input
                            type="number"
                            min="0"
                            step="1"
                            disabled={remain <= 0}
                            value={qtyByLine[line.line_id] ?? ""}
                            data-testid={`retur-qty-${line.line_id}`}
                            onChange={(e) => setQtyByLine((prev) => ({ ...prev, [line.line_id]: e.target.value }))}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button type="button" className="ghost" onClick={() => setCreating(false)}>Tutup</button>
            <button type="button" className="primary" disabled={busy} onClick={save} data-testid="retur-save">Simpan draf</button>
          </div>
        </div>
      )}

      {detail && (
        <div className="card purchase-detail-card" data-testid="retur-detail">
          <div className="retur-detail-head">
            <div>
              <p className="eyebrow">Retur Pembelian</p>
              <h3 data-testid="retur-detail-number">{detail.return_number}</h3>
            </div>
            <div className="retur-detail-head-actions">
              <span className={purchaseStatusPillClass(detail.status)} data-testid="retur-detail-status">{statusLabel(detail)}</span>
              <button type="button" className="ghost sm" data-testid="retur-detail-close" onClick={closeDetail}>Tutup Detail</button>
            </div>
          </div>
          <h4>Informasi Dokumen</h4>
          <div className="purchase-detail-meta" data-testid="retur-detail-meta">
            <div>
              <span className="label">Tanggal</span>
              <span className="value" data-testid="retur-detail-date">{formatReturnDate(detail.return_date)}</span>
            </div>
            <div>
              <span className="label">Supplier</span>
              <span className="value retur-wrap" data-testid="retur-detail-supplier">{detail.supplier_name || detail.supplier || "—"}</span>
            </div>
            <div>
              <span className="label">Pesanan Pembelian</span>
              <span className="value mono" data-testid="retur-detail-po">{detail.po_number || "—"}</span>
            </div>
            <div>
              <span className="label">Penerimaan Barang</span>
              <span className="value mono" data-testid="retur-detail-receipt">{receiptLabel(detail, receiptsById)}</span>
            </div>
            <div>
              <span className="label">Gudang</span>
              <span className="value" data-testid="retur-detail-warehouse">{detailWarehouse || "—"}</span>
            </div>
            <div>
              <span className="label">Status Finansial</span>
              <span className="value" data-testid="retur-detail-financial">{financialLabel(financeStatus, detail.status)}</span>
            </div>
          </div>
          {financeStatus === FINANCIAL_PENDING && (
            <p data-testid="retur-pending-allocation">Menunggu Alokasi Finansial. Faktur tidak dipilih otomatis.</p>
          )}
          {(finance?.credits || []).length > 0 && (
            <div className="purchase-detail-meta" data-testid="retur-credits">
              {(finance.credits || []).map((credit) => (
                <div key={credit.id} data-testid="retur-credit-row">
                  <span className="label">Kredit Retur</span>
                  <span className="value">{formatRupiah(credit.credit_amount)}</span>
                  <span className="label">Diterapkan ke faktur</span>
                  <span className="value mono" data-testid="retur-credit-invoice">{invoiceNumberLabel(credit, invoiceNumbers)} · {formatRupiah(credit.applied_to_invoice_amount || 0)}</span>
                  {Number(credit.supplier_credit_amount) > 0 && (
                    <span className="value">Kredit supplier {formatRupiah(credit.supplier_credit_amount)}</span>
                  )}
                </div>
              ))}
            </div>
          )}
          {(finance?.supplier_credits || []).length > 0 && (
            <div className="purchase-detail-meta" data-testid="retur-supplier-credits">
              {(finance.supplier_credits || []).map((credit) => (
                <div key={credit.id}>
                  <span className="label">Kredit Supplier</span>
                  <span className="value">{formatRupiah(credit.original_amount)} · sisa {formatRupiah(credit.available_amount || 0)}</span>
                </div>
              ))}
            </div>
          )}
          <div className="purchase-table-wrap">
            <table className="prod-table purchase-table purchase-table-excel" data-testid="retur-detail-lines">
              <thead>
                <tr>
                  <th>Barang</th>
                  <th className="num">Qty Diterima</th>
                  <th className="num">Qty Retur</th>
                  <th>Satuan</th>
                  <th>Status Finansial</th>
                </tr>
              </thead>
              <tbody>
                {(detail.lines || []).map((line) => (
                  <tr key={line.line_id} data-testid="retur-detail-line">
                    <td className="retur-item-cell">
                      <span className="retur-item-code" data-testid="retur-detail-item-code">{line.item_code || "—"}</span>
                      <span className="retur-item-name" title={line.item_name || ""} data-testid="retur-detail-item">{line.item_name || "—"}</span>
                    </td>
                    <td className="num">{line.received_qty}</td>
                    <td className="num" data-testid="retur-detail-qty">{line.return_qty}</td>
                    <td>{line.unit || "PCS"}</td>
                    <td>{financialLabel(line.financial_status, detail.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {canMutate && detail.status === "DRAFT" && (
            <div className="purchase-detail-actions">
              <button type="button" className="primary" disabled={busy} data-testid="retur-confirm" onClick={() => act("confirm")}>Konfirmasi Retur</button>
              <button type="button" className="ghost" disabled={busy} data-testid="retur-cancel" onClick={() => act("cancel")}>Batalkan</button>
            </div>
          )}
          {canMutate && detail.status === "CONFIRMED" && (
            <div className="purchase-detail-actions">
              <p className="muted small">Retur terkonfirmasi. Pembatalan mengikuti proses reversal yang ada.</p>
              <button type="button" className="ghost" disabled={busy} data-testid="retur-reverse" onClick={() => act("reverse")}>Batalkan Retur</button>
            </div>
          )}
          {readOnly && <p className="muted small" data-testid="retur-readonly">Dokumen ini hanya dapat dilihat.</p>}
        </div>
      )}
    </div>
  );
}
