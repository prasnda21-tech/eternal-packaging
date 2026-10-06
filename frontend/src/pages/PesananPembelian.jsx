import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, Plus, Search } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { errDetail, formatRupiah, friendlyLoadError, newIdempotencyKey, purchaseStatusPillClass, shortReceivingStatus, todayYmd, purchaseRowInteractionProps } from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";
import PurchasingFlowNav from "./PurchasingFlowNav";
import PurchaseCancelModal from "./PurchaseCancelModal";
import PurchaseActivityHistory from "./PurchaseActivityHistory";
import PurchaseDocuments from "./PurchaseDocuments";
import "./purchasePages.css";

function emptyPoLine() {
  return {
    key: `${Date.now()}-${Math.random()}`,
    request_line_id: "",
    master_item_id: "",
    accurate_item_id: "",
    item_name: "",
    item_code: "",
    unit: "pcs",
    ordered_qty: "",
    unit_price: "",
    remaining_hint: null,
  };
}

export default function PesananPembelian() {
  const [searchParams] = useSearchParams();
  const { hasPerm } = useAuth();
  const canCreate = hasPerm("purchase_orders.create");
  const queryRequestId = searchParams.get("request_id") || "";
  const wantCreate = searchParams.get("action") === "create" || Boolean(queryRequestId);
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

  const [mode, setMode] = useState(queryRequestId ? "from_request" : "direct");
  const [eligible, setEligible] = useState([]);
  const [requestId, setRequestId] = useState(canCreate ? queryRequestId : "");
  const [supplier, setSupplier] = useState("");
  const [orderDate, setOrderDate] = useState(todayYmd());
  const [lines, setLines] = useState([emptyPoLine()]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await api.listPurchaseOrders({ q });
      setRows(res.orders || []);
    } catch (ex) { setErr(friendlyLoadError(ex, "Tidak dapat memuat pesanan. Periksa koneksi server lalu coba lagi.")); }
    finally { setLoading(false); }
  }, [q]);

  useEffect(() => { if (view === "list") load(); }, [load, view]);

  const loadEligible = useCallback(async () => {
    try {
      const res = await api.listPurchaseRequests({ eligible_for_po: true });
      setEligible(res.requests || []);
    } catch { setEligible([]); }
  }, []);

  const applyRequestPrefill = useCallback(async (id) => {
    if (!id) {
      setLines([emptyPoLine()]);
      return;
    }
    setErr("");
    try {
      const p = await api.purchaseOrderPreview(id);
      const next = (p.lines || [])
        .filter((l) => Number(l.remaining_order_qty) > 0)
        .map((l) => ({
          key: l.request_line_id || `${Date.now()}-${Math.random()}`,
          request_line_id: l.request_line_id,
          master_item_id: l.master_item_id || "",
          accurate_item_id: l.accurate_item_id || "",
          item_name: l.item_name || "",
          item_code: l.item_code || "",
          unit: l.unit || "pcs",
          ordered_qty: l.remaining_order_qty,
          unit_price: l.unit_price || "",
          remaining_hint: l.remaining_order_qty,
        }));
      setLines(next.length ? next : [emptyPoLine()]);
    } catch (ex) {
      setErr(errDetail(ex));
      setLines([emptyPoLine()]);
    }
  }, []);

  useEffect(() => {
    if (view !== "create") return;
    if (!canCreate) {
      setView("list");
      setErr("Anda tidak memiliki izin membuat Pesanan Pembelian.");
      return;
    }
    if (mode === "from_request") {
      loadEligible();
      if (requestId) applyRequestPrefill(requestId);
    } else {
      setRequestId("");
      setLines([emptyPoLine()]);
    }
  }, [view, mode, requestId, loadEligible, applyRequestPrefill, canCreate]);

  const setLine = (i, patch) => setLines((p) => p.map((l, idx) => (idx === i ? { ...l, ...patch } : l)));
  const addLine = () => setLines((p) => [...p, emptyPoLine()]);
  const removeLine = (i) => setLines((p) => (p.length <= 1 ? p : p.filter((_, idx) => idx !== i)));

  const pickItem = (i, it) => {
    if (!it) {
      return setLine(i, {
        master_item_id: "",
        accurate_item_id: "",
        item_name: "",
        item_code: "",
        unit: "pcs",
        request_line_id: "",
        remaining_hint: null,
      });
    }
    const mid = it.id || it.master_item_id || "";
    setLine(i, {
      master_item_id: mid,
      accurate_item_id: it.accurate_item_id || "",
      item_name: it.item_name || "",
      item_code: it.item_code || "",
      unit: it.unit || "pcs",
      // swapping item clears PR fulfillment link
      request_line_id: "",
      remaining_hint: null,
    });
  };

  const save = async () => {
    if (busy || !canCreate) return;
    if (!supplier.trim()) {
      setErr("Supplier wajib diisi.");
      return;
    }
    if (lines.some((l) => Number(l.ordered_qty) > 0 && !l.master_item_id)) {
      setErr("Setiap baris harus memilih item dari Master Item.");
      return;
    }
    setBusy(true); setErr(""); setOk("");
    try {
      const payloadLines = lines
        .filter((l) => Number(l.ordered_qty) > 0)
        .map((l) => ({
          request_line_id: mode === "from_request" ? (l.request_line_id || undefined) : undefined,
          master_item_id: l.master_item_id,
          accurate_item_id: l.accurate_item_id,
          item_code: l.item_code,
          item_name: l.item_name,
          unit: l.unit,
          ordered_qty: l.ordered_qty,
          unit_price: l.unit_price,
        }));
      const res = await api.createPurchaseOrder({
        request_id: mode === "from_request" ? requestId : undefined,
        supplier,
        order_date: orderDate,
        lines: payloadLines,
        idempotency_key: newIdempotencyKey(),
      });
      setDetail(res.order);
      setView("detail");
      setOk(`Pesanan Pembelian ${res.order?.po_number || ""} berhasil disimpan.`);
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

  const openCancel = (row) => {
    setCancelErr("");
    setCancelTarget(row);
  };

  const confirmCancel = async () => {
    if (!cancelTarget || cancelBusy) return;
    setCancelBusy(true);
    setCancelErr("");
    try {
      await api.cancelPurchaseOrder(cancelTarget.id);
      setOk(`Pesanan ${cancelTarget.po_number || ""} dihapus dari daftar aktif.`);
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

  const openCreate = () => {
    setMode(queryRequestId ? "from_request" : "direct");
    setRequestId(queryRequestId || "");
    setSupplier("");
    setOrderDate(todayYmd());
    setLines([emptyPoLine()]);
    setView("create");
  };

  return (
    <div className="prod-page purchase-page" data-testid="pesanan-pembelian-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="pesanan-pembelian-title">
            {view === "create" ? "Buat Pesanan Pembelian" : "Pesanan Pembelian"}
          </h1>
          <p className="subhead">
            {view === "create"
              ? "Pilih sumber pesanan dan tentukan barang yang akan dibeli."
              : "Buat pesanan langsung atau berdasarkan permintaan pembelian."}
          </p>
        </div>
        {view === "list" && canCreate && (
          <button className="primary" data-testid="btn-create-po" onClick={openCreate}><Plus size={14} /> Buat Pesanan</button>
        )}
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
      {ok && <p className="purchase-inline-ok" data-testid="po-success">{ok}</p>}

      {view === "list" && (
        <>
          <div className="purchase-search-bar is-toolbar">
            <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load(); }}>
              <Search size={14} />
              <input value={q} onChange={(e) => setQ(e.target.value)} data-testid="po-search" placeholder="Cari no PO / supplier / permintaan" />
            </form>
          </div>
          <div className="purchase-table-wrap">
            <table className="prod-table purchase-table purchase-table-po" data-testid="po-table">
              <colgroup>
                <col className="col-po" />
                <col className="col-supplier" />
                <col className="col-date" />
                <col className="col-money" />
                <col className="col-status-recv" />
                <col className="col-status-inv" />
              </colgroup>
              <thead>
                <tr>
                  <th>No PO</th>
                  <th>Supplier</th>
                  <th>Tanggal</th>
                  <th className="num">Total</th>
                  <th className="cell-status th-wrap">Status Penerimaan</th>
                  <th className="cell-status th-wrap">Status Faktur</th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr className="purchase-empty-row"><td colSpan={6} className="muted small">Memuat…</td></tr>}
                {!loading && rows.length === 0 && (
                  <tr className="purchase-empty-row">
                    <td colSpan={6} className="muted small">Belum ada pesanan pembelian.</td>
                  </tr>
                )}
                {rows.map((r) => (
                  <tr
                    key={r.id}
                    {...purchaseRowInteractionProps(() => openDetail(r.id), { testId: `po-row-${r.id}` })}
                  >
                    <td className="mono cell-clip">
                      {r.po_number}
                      {r.has_early_invoice && <span className="purchase-badge-early" data-testid="badge-early-invoice">Faktur mendahului barang</span>}
                    </td>
                    <td className="cell-clip" title={r.supplier}>{r.supplier}</td>
                    <td className="nowrap">{r.order_date}</td>
                    <td className="num">{formatRupiah(r.grand_total)}</td>
                    <td className="cell-status">
                      <span className={`purchase-status-badge ${purchaseStatusPillClass(r.receiving_status)}`}>
                        {shortReceivingStatus(r.receiving_status)}
                      </span>
                    </td>
                    <td className="cell-status">
                      <span className={`purchase-status-badge ${purchaseStatusPillClass(r.invoice_status)}`}>
                        {r.invoice_status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {view === "create" && (
        <div className="card purchase-detail-card" data-testid="po-create-form">
          <div className="purchase-mode-toggle" role="tablist" aria-label="Mode pesanan">
            <button
              type="button"
              className={mode === "direct" ? "is-active" : ""}
              data-testid="po-mode-direct"
              onClick={() => setMode("direct")}
            >
              Pesanan Langsung
            </button>
            <button
              type="button"
              className={mode === "from_request" ? "is-active" : ""}
              data-testid="po-mode-from-request"
              onClick={() => setMode("from_request")}
            >
              Dari Permintaan
            </button>
          </div>

          {mode === "from_request" && (
            <FormField id="po-request" label="Permintaan" value={requestId}>
              <select value={requestId} onChange={(e) => setRequestId(e.target.value)} data-testid="request-selector">
                <option value="">Pilih permintaan (opsional prefill)</option>
                {eligible.map((r) => <option key={r.id} value={r.id}>{r.request_number} · sisa {fmtQty(r.total_remaining_order_qty)}</option>)}
              </select>
            </FormField>
          )}

          <FormProgress
            percentage={Math.round(([supplier, orderDate, lines.some((l) => l.master_item_id && Number(l.ordered_qty) > 0)].filter(Boolean).length / 3) * 100)}
            completed={[supplier, orderDate, lines.some((l) => l.master_item_id && Number(l.ordered_qty) > 0)].filter(Boolean).length}
            total={3}
          />
          <FormSection
            id="po-header"
            title="Informasi Pesanan"
            status={computeStatus([
              { value: supplier, isRequired: true },
              { value: orderDate, isRequired: true },
            ])}
          >
            <div className="purchase-form-grid">
              <FormField id="po-supplier-field" label="Supplier" isRequired value={supplier}>
                <input value={supplier} onChange={(e) => setSupplier(e.target.value)} data-testid="po-supplier" />
              </FormField>
              <FormField id="po-date-field" label="Tanggal" isRequired value={orderDate}>
                <input type="date" value={orderDate} onChange={(e) => setOrderDate(e.target.value)} data-testid="po-date" />
              </FormField>
            </div>
          </FormSection>
          <FormSection
            id="po-lines"
            title="Detail Item"
            status={lines.some((l) => l.master_item_id && Number(l.ordered_qty) > 0) ? "complete" : "incomplete"}
          >
            <div className="purchase-table-wrap">
              <table className="prod-table purchase-lines-table purchase-lines-with-price" data-testid="po-lines-table">
                <colgroup>
                  <col className="col-item-line" />
                  <col className="col-code-line" />
                  <col className="col-unit-line" />
                  <col className="col-qty-line" />
                  <col className="col-price-line" />
                  <col className="col-action-line" />
                </colgroup>
                <thead>
                  <tr>
                    <th>Item</th>
                    <th>Kode</th>
                    <th>Satuan</th>
                    <th className="num">Qty</th>
                    <th className="num">Harga</th>
                    <th>Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l, i) => (
                    <tr key={l.key}>
                      <td>
                        <MasterItemSearchSelect
                          testId={`po-line-item-${i}`}
                          value={
                            l.master_item_id
                              ? {
                                  id: l.master_item_id,
                                  master_item_id: l.master_item_id,
                                  accurate_item_id: l.accurate_item_id,
                                  item_name: l.item_name,
                                  item_code: l.item_code,
                                  unit: l.unit,
                                }
                              : null
                          }
                          onChange={(item) => pickItem(i, item)}
                          placeholder="Cari Master Item…"
                        />
                        {l.request_line_id && l.remaining_hint != null && (
                          <span className="muted small">Sisa PR: {fmtQty(l.remaining_hint)}</span>
                        )}
                      </td>
                      <td className="mono">{l.item_code || "—"}</td>
                      <td className="unit-cell"><input value={l.unit} readOnly tabIndex={-1} /></td>
                      <td className="qty-cell">
                        <input
                          type="number"
                          min="0"
                          value={l.ordered_qty}
                          onChange={(e) => setLine(i, { ordered_qty: e.target.value })}
                          data-testid="qty-input"
                        />
                      </td>
                      <td className="qty-cell">
                        <input
                          type="number"
                          min="0"
                          value={l.unit_price}
                          onChange={(e) => setLine(i, { unit_price: e.target.value })}
                          data-testid="unit-price-input"
                        />
                      </td>
                      <td className="action-cell">
                        <button type="button" className="ghost sm" onClick={() => removeLine(i)}>Hapus</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <button className="ghost purchase-add-line" type="button" onClick={addLine} data-testid="add-po-line">Tambah baris</button>
          </FormSection>
          <div className="purchase-form-actions">
            <button className="ghost" type="button" onClick={() => setView("list")}>Batal</button>
            <button className="primary" disabled={busy} onClick={save} data-testid="save-po">{busy ? "Menyimpan…" : "Simpan Pesanan"}</button>
          </div>
        </div>
      )}

      {view === "detail" && detail && (
        <div className="card purchase-detail-card" data-testid="po-detail">
          <div className="purchase-detail-meta">
            <div>
              <span className="label">No PO</span>
              <span className="value mono" data-testid="detail-po-number">
                {detail.po_number}
                {detail.has_early_invoice && <span className="purchase-badge-early">Faktur mendahului barang</span>}
              </span>
            </div>
            <div>
              <span className="label">Permintaan</span>
              <span className="value mono">{detail.request_number || "—"}</span>
            </div>
            <div>
              <span className="label">Supplier</span>
              <span className="value">{detail.supplier}</span>
            </div>
            <div>
              <span className="label">Total</span>
              <span className="value">{formatRupiah(detail.grand_total)}</span>
            </div>
            <div>
              <span className="label">Status Penerimaan</span>
              <span className={purchaseStatusPillClass(detail.receiving_status)}>{shortReceivingStatus(detail.receiving_status)}</span>
            </div>
            <div>
              <span className="label">Status Faktur</span>
              <span className={purchaseStatusPillClass(detail.invoice_status)}>{detail.invoice_status}</span>
            </div>
          </div>
          <div className="purchase-table-wrap">
            <table className="prod-table">
              <thead><tr><th>Item</th><th>Kode</th><th className="num">Qty</th><th className="num">Harga</th><th className="num">Subtotal</th><th className="num">Diterima</th><th className="num">Difaktur</th></tr></thead>
              <tbody>
                {(detail.lines || []).map((l) => (
                  <tr key={l.line_id}>
                    <td>{l.item_name}</td>
                    <td className="mono">{l.item_code || "—"}</td>
                    <td className="num">{fmtQty(l.ordered_qty)} {l.unit}</td>
                    <td className="num">{formatRupiah(l.unit_price)}</td>
                    <td className="num">{formatRupiah(l.line_subtotal)}</td>
                    <td className="num">{fmtQty(l.cumulative_received_qty)} / sisa {fmtQty(l.remaining_receivable_qty)}</td>
                    <td className="num">{fmtQty(l.cumulative_invoiced_qty)} / sisa {fmtQty(l.remaining_invoice_qty)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <PurchaseDocuments
            entityType="purchase_order"
            entityId={detail.id}
            canEdit={canCreate}
            hint="Nota / penawaran supplier."
            testId="po-documents"
          />
          <PurchaseActivityHistory doc={detail} testId="po-activity" />
          <div className="purchase-detail-actions">
            {canCreate && (
              <button
                type="button"
                className="ghost danger"
                data-testid="btn-hapus-po-detail"
                disabled={detail.can_cancel === false}
                title={detail.can_cancel === false ? (detail.cancel_block_reason || "") : undefined}
                onClick={() => openCancel(detail)}
              >
                Hapus
              </button>
            )}
            {Number(detail.invoice_progress?.remaining_qty) > 0 && hasPerm("purchase_invoices.create") && (
              <Link className="primary" data-testid="btn-buat-faktur" to={`/pembelian/faktur-pembelian?purchase_order_id=${detail.id}&action=create`}>Buat Faktur</Link>
            )}
            {Number(detail.receiving_progress?.remaining_qty) > 0 && (
              <Link className="ghost" to={`/pembelian/penerimaan-barang?purchase_order_id=${detail.id}&action=receive`}>Terima Barang</Link>
            )}
          </div>
        </div>
      )}

      <PurchaseCancelModal
        open={Boolean(cancelTarget)}
        title="Hapus Pesanan Pembelian?"
        body="Dokumen akan dikeluarkan dari daftar aktif. Riwayat pembatalan tetap tersimpan."
        busy={cancelBusy}
        error={cancelErr}
        onCancel={() => !cancelBusy && setCancelTarget(null)}
        onConfirm={confirmCancel}
      />
    </div>
  );
}
