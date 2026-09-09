import { useCallback, useEffect, useState } from "react";
import { AlertCircle, ChevronDown, ChevronRight, Download, Plus, RefreshCw, Search, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { errDetail, newIdempotencyKey, todayYmd } from "../lib/purchaseUi";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";
import { useDebouncedLiveSearch } from "../hooks/useDebouncedLiveSearch";
import MasterItemSearchSelect from "../components/MasterItemSearchSelect";

function emptyLine() {
  return {
    key: `${Date.now()}-${Math.random()}`,
    master_item_id: "",
    accurate_item_id: "",
    item_name: "",
    item_code: "",
    unit: "pcs",
    received_qty: "",
    notes: "",
  };
}

export default function PenerimaanBarang() {
  const { hasPerm } = useAuth();
  const [searchParams] = useSearchParams();
  const canSync = hasPerm("purchase_receipts.sync");
  const canExport = hasPerm("purchase_receipts.export");
  const canCreate = hasPerm("purchase_receipts.receive");
  const canReceivePo = hasPerm("purchase_receipts.receive") || hasPerm("purchase_receipts.sync");
  const queryPoId = searchParams.get("purchase_order_id") || "";
  const openCreateFromQuery = searchParams.get("action") === "receive" || searchParams.get("action") === "create";
  const [view, setView] = useState(openCreateFromQuery && canCreate ? "create" : "list");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");
  const [syncErr, setSyncErr] = useState("");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery } = useDebouncedLiveSearch();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [openId, setOpenId] = useState(null);
  const [exporting, setExporting] = useState(false);
  const [poReceipts, setPoReceipts] = useState([]);
  const [eligiblePos, setEligiblePos] = useState([]);
  const [recvPoId, setRecvPoId] = useState(queryPoId);
  const [recvPreview, setRecvPreview] = useState(null);
  const [recvQty, setRecvQty] = useState({});
  const [recvDate, setRecvDate] = useState(todayYmd());
  const [recvBusy, setRecvBusy] = useState(false);
  const [recvErr, setRecvErr] = useState("");
  const [recvMsg, setRecvMsg] = useState("");
  const [supplier, setSupplier] = useState("");
  const [notes, setNotes] = useState("");
  const [reference, setReference] = useState("");
  const [lines, setLines] = useState([emptyLine()]);
  const [formErr, setFormErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const d = await api.purchaseReceipts({ q, page, page_size: pageSize });
      setData(d);
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Penerimaan Barang"));
    } finally {
      setLoading(false);
    }
  }, [q, page, pageSize]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { setPage(1); }, [q]);

  useEffect(() => {
    api.listPurchaseGoodsReceipts({}).then((r) => setPoReceipts(r.receipts || [])).catch(() => setPoReceipts([]));
    if (canCreate || canReceivePo) {
      api.listPurchaseOrders({ eligible_for_receipt: true }).then((r) => setEligiblePos(r.orders || [])).catch(() => setEligiblePos([]));
    }
  }, [canCreate, canReceivePo, recvMsg, ok]);

  useEffect(() => {
    if (!recvPoId) { setRecvPreview(null); return; }
    api.purchaseGoodsReceiptPreview(recvPoId).then((p) => {
      setRecvPreview(p);
      const map = {};
      (p.lines || []).forEach((l) => { map[l.purchase_order_line_id] = l.remaining_receivable_qty; });
      setRecvQty(map);
      if (p.supplier) setSupplier(p.supplier);
    }).catch((ex) => setRecvErr(errDetail(ex)));
  }, [recvPoId]);

  const clearSearch = () => {
    clearQuery();
    setPage(1);
  };

  const resetCreateForm = () => {
    setRecvPoId(queryPoId || "");
    setRecvPreview(null);
    setRecvQty({});
    setRecvDate(todayYmd());
    setSupplier("");
    setNotes("");
    setReference("");
    setLines([emptyLine()]);
    setFormErr("");
    setBusy(false);
  };

  const openCreate = () => {
    if (!canCreate) return;
    resetCreateForm();
    setOk("");
    setView("create");
  };

  const backToList = () => {
    setView("list");
    load();
  };

  const doSync = async () => {
    if (!canSync || syncing) return;
    setSyncing(true);
    setSyncErr("");
    setSyncMsg("");
    try {
      const r = await api.purchaseReceiptsSync();
      const bits = [
        `Ditemukan ${r.receipts_found ?? 0}`,
        `baru ${r.created ?? 0}`,
        `diubah ${r.updated ?? 0}`,
        `sama ${r.unchanged ?? 0}`,
        `gagal ${r.failed ?? 0}`,
      ];
      const line = bits.join(" · ");
      if (r.ok === false || (r.failed || 0) > 0) {
        setSyncErr(r.error ? `Sinkronisasi tidak lengkap (${r.error}). ${line}` : `Sinkronisasi tidak lengkap. ${line}`);
      } else {
        setSyncMsg(line);
      }
      await load();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setSyncErr(typeof det === "object" ? det.message : (det || "Gagal sinkronisasi Penerimaan Accurate"));
    } finally {
      setSyncing(false);
    }
  };

  const download = async () => {
    setExporting(true);
    try {
      const blob = await api.purchaseReceiptsExport({ q });
      const url = URL.createObjectURL(new Blob([blob], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `penerimaan-barang-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (ex) {
      alert(ex.response?.data?.detail || "Gagal export");
    } finally {
      setExporting(false);
    }
  };

  const addLine = () => setLines((p) => [...p, emptyLine()]);
  const removeLine = (key) => setLines((p) => (p.length <= 1 ? p : p.filter((l) => l.key !== key)));
  const setLine = (key, patch) => setLines((p) => p.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  const pickItem = (key, it) => {
    if (!it) {
      return setLine(key, { master_item_id: "", accurate_item_id: "", item_name: "", item_code: "", unit: "pcs" });
    }
    setLine(key, {
      master_item_id: it.id || it.master_item_id || "",
      accurate_item_id: it.accurate_item_id || "",
      item_name: it.item_name || "",
      item_code: it.item_code || "",
      unit: it.unit || "pcs",
    });
  };

  const validateCreate = () => {
    if (!recvDate) return "Tanggal penerimaan wajib diisi.";
    if (recvPoId) {
      const qtyLines = (recvPreview?.lines || []).filter((l) => Number(recvQty[l.purchase_order_line_id]) > 0);
      if (!qtyLines.length) return "Minimal satu baris dengan qty diterima > 0.";
      for (const l of qtyLines) {
        const qn = Number(recvQty[l.purchase_order_line_id]);
        if (!Number.isFinite(qn) || qn <= 0) return `Qty diterima '${l.item_name}' tidak valid.`;
      }
      return "";
    }
    if (!String(supplier || "").trim()) return "Supplier wajib diisi.";
    const validLines = lines.filter((l) => (l.item_name || l.master_item_id || l.accurate_item_id) && Number(l.received_qty) > 0);
    if (!validLines.length) return "Minimal satu baris item dengan qty > 0.";
    for (const l of lines) {
      const hasItem = l.item_name || l.master_item_id || l.accurate_item_id;
      if (!hasItem && (l.received_qty === "" || l.received_qty == null)) continue;
      if (!hasItem) return "Setiap baris harus memiliki item.";
      const qn = Number(l.received_qty);
      if (!Number.isFinite(qn) || qn <= 0) return `Qty diterima '${l.item_name || "item"}' harus lebih besar dari 0.`;
    }
    return "";
  };

  const saveCreate = async () => {
    if (busy || !canCreate) return;
    const v = validateCreate();
    if (v) { setFormErr(v); return; }
    setBusy(true);
    setFormErr("");
    setErr("");
    try {
      const payload = recvPoId
        ? {
            purchase_order_id: recvPoId,
            receipt_date: recvDate,
            notes,
            lines: (recvPreview?.lines || []).map((l) => ({
              purchase_order_line_id: l.purchase_order_line_id,
              received_qty: recvQty[l.purchase_order_line_id],
            })).filter((l) => Number(l.received_qty) > 0),
            idempotency_key: newIdempotencyKey(),
          }
        : {
            supplier: supplier.trim(),
            receipt_date: recvDate,
            notes,
            po_number: reference.trim(),
            lines: lines.map((l, idx) => ({
              line_idx: idx,
              master_item_id: l.master_item_id,
              accurate_item_id: l.accurate_item_id,
              item_name: l.item_name,
              item_code: l.item_code,
              unit: l.unit,
              received_qty: l.received_qty,
              notes: l.notes,
            })),
            idempotency_key: newIdempotencyKey(),
          };
      const res = await api.createPurchaseReceipt(payload);
      const num = res.receipt?.receipt_number || "";
      setOk(`Penerimaan ${num} tersimpan`);
      setView("list");
      await load();
    } catch (ex) {
      setFormErr(errDetail(ex));
    } finally {
      setBusy(false);
    }
  };

  const items = data?.items || [];
  const pageCount = data?.page_count || 1;
  const headerComplete = Boolean(recvDate && (recvPoId ? Object.values(recvQty).some((v) => Number(v) > 0) : (supplier && lines.some((l) => Number(l.received_qty) > 0))));

  return (
    <div className="prod-page master-items-page" data-testid="penerimaan-barang-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="penerimaan-title">Penerimaan Barang</h1>
          <p className="subhead">Data penerimaan barang yang disinkronkan dari Accurate dan catatan manual lokal.</p>
          <p className="muted small stock-last-sync" data-testid="penerimaan-last-sync">
            Terakhir sinkronisasi: {data?.last_synced_at ? formatDate(data.last_synced_at) : "belum"}
          </p>
        </div>
        <div className="stock-heading-actions">
          {view === "list" && canCreate && (
            <button className="primary" onClick={openCreate} data-testid="btn-buat-penerimaan">
              <Plus size={14} /> Buat Penerimaan
            </button>
          )}
          {view !== "list" && (
            <button className="ghost" type="button" onClick={backToList} data-testid="btn-cancel-penerimaan">Kembali ke daftar</button>
          )}
          {view === "list" && canSync && (
            <button
              className="primary"
              onClick={doSync}
              disabled={syncing}
              data-testid="penerimaan-sync-button"
            >
              <RefreshCw size={14} className={syncing ? "spin" : ""} />
              {syncing ? "Menyinkronkan…" : "Sinkronkan dari Accurate"}
            </button>
          )}
          {view === "list" && canExport && (
            <button className="ghost" onClick={download} disabled={exporting} data-testid="penerimaan-export-button">
              <Download size={14} /> Export
            </button>
          )}
        </div>
      </div>

      {(err || syncErr || formErr) && (
        <div className="signal-strip" data-testid="penerimaan-error" role="alert">
          <div><AlertCircle size={16} /><span>{formErr || syncErr || err}</span></div>
        </div>
      )}
      {ok && !syncErr && (
        <p className="muted small" data-testid="penerimaan-success">{ok}</p>
      )}
      {syncMsg && !syncErr && (
        <p className="muted small stock-sync-summary" data-testid="penerimaan-sync-summary">{syncMsg}</p>
      )}

      {view === "create" && canCreate && (
        <div className="card" data-testid="penerimaan-create-form">
          <FormProgress
            percentage={Math.round((headerComplete ? 1 : 0) * 100)}
            completed={headerComplete ? 1 : 0}
            total={1}
          />
          <FormSection
            id="recv-header"
            title="Informasi Penerimaan"
            description="Nomor diisi otomatis oleh sistem saat simpan."
            status={computeStatus([{ value: recvDate, isRequired: true }, { value: recvPoId ? "po" : supplier, isRequired: true }])}
          >
            <FormField id="recv-date" label="Tanggal Penerimaan" isRequired value={recvDate}>
              <input type="date" value={recvDate} onChange={(e) => setRecvDate(e.target.value)} data-testid="penerimaan-date" />
            </FormField>
            <FormField id="recv-po" label="Pesanan Pembelian (opsional)" value={recvPoId}>
              <select value={recvPoId} onChange={(e) => { setRecvPoId(e.target.value); setFormErr(""); }} data-testid="penerimaan-po-selector">
                <option value="">Tanpa PO — penerimaan manual</option>
                {eligiblePos.map((o) => <option key={o.id} value={o.id}>{o.po_number} · {o.supplier}</option>)}
              </select>
            </FormField>
            {!recvPoId && (
              <p className="muted small" data-testid="penerimaan-manual-stock-hint">
                Tanpa PO, penerimaan dicatat sebagai dokumen manual dan tidak mengubah stok.
              </p>
            )}
            <FormField id="recv-supplier" label="Supplier" isRequired value={supplier}>
              <input
                value={supplier}
                onChange={(e) => setSupplier(e.target.value)}
                readOnly={Boolean(recvPoId)}
                data-testid="penerimaan-supplier"
                placeholder="Nama supplier"
              />
            </FormField>
            {!recvPoId && (
              <FormField id="recv-ref" label="Referensi / No. PO" value={reference}>
                <input value={reference} onChange={(e) => setReference(e.target.value)} data-testid="penerimaan-reference" />
              </FormField>
            )}
            <FormField id="recv-notes" label="Keterangan" value={notes}>
              <input value={notes} onChange={(e) => setNotes(e.target.value)} data-testid="penerimaan-notes" />
            </FormField>
          </FormSection>

          {recvPoId && recvPreview && (
            <FormSection id="recv-po-lines" title="Qty Diterima per Item PO" status={Object.values(recvQty).some((v) => Number(v) > 0) ? "complete" : "incomplete"}>
              <div className="prod-table-wrap">
                <table className="prod-table" data-testid="penerimaan-po-lines">
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th className="num">Dipesan</th>
                      <th className="num">Sudah diterima</th>
                      <th className="num">Sisa</th>
                      <th className="num">Qty Diterima</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(recvPreview.lines || []).map((l) => (
                      <tr key={l.purchase_order_line_id} data-testid={`po-line-${l.purchase_order_line_id}`}>
                        <td>{l.item_name}</td>
                        <td className="num">{fmtQty(l.ordered_qty)}</td>
                        <td className="num">{fmtQty(l.cumulative_received_qty)}</td>
                        <td className="num">{fmtQty(l.remaining_receivable_qty)}</td>
                        <td className="num">
                          <input
                            type="number"
                            min="0"
                            value={recvQty[l.purchase_order_line_id] ?? ""}
                            onChange={(e) => setRecvQty((p) => ({ ...p, [l.purchase_order_line_id]: e.target.value }))}
                            data-testid="receive-qty-input"
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </FormSection>
          )}

          {!recvPoId && (
            <FormSection
              id="recv-manual-lines"
              title="Item Diterima"
              status={lines.some((l) => Number(l.received_qty) > 0) ? "complete" : "incomplete"}
            >
              <div className="prod-table-wrap">
                <table className="prod-table" data-testid="penerimaan-lines">
                  <thead>
                    <tr>
                      <th>Item</th>
                      <th className="num">Qty Diterima</th>
                      <th>Satuan</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((l, i) => (
                      <tr key={l.key} data-testid={`penerimaan-line-${i}`}>
                        <td>
                          <MasterItemSearchSelect
                            testId={`penerimaan-line-item-${i}`}
                            value={
                              l.accurate_item_id || l.master_item_id
                                ? {
                                    accurate_item_id: l.accurate_item_id,
                                    id: l.master_item_id,
                                    master_item_id: l.master_item_id,
                                    item_name: l.item_name,
                                    item_code: l.item_code,
                                    unit: l.unit,
                                  }
                                : null
                            }
                            onChange={(item) => pickItem(l.key, item)}
                            placeholder="Cari nama / kode produk…"
                          />
                        </td>
                        <td className="num">
                          <input
                            type="number"
                            min="0"
                            value={l.received_qty}
                            onChange={(e) => setLine(l.key, { received_qty: e.target.value })}
                            data-testid={`penerimaan-line-qty-${i}`}
                          />
                        </td>
                        <td>
                          <input value={l.unit} onChange={(e) => setLine(l.key, { unit: e.target.value })} data-testid={`penerimaan-line-unit-${i}`} />
                        </td>
                        <td>
                          <button type="button" className="ghost sm" onClick={() => removeLine(l.key)} data-testid={`penerimaan-line-remove-${i}`}>Hapus</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <button className="ghost" type="button" onClick={addLine} data-testid="penerimaan-add-line">+ Tambah Item</button>
            </FormSection>
          )}

          <div className="delete-modal-actions">
            <button className="ghost" type="button" onClick={backToList} data-testid="penerimaan-form-cancel">Batal</button>
            <button className="primary" disabled={busy} onClick={saveCreate} data-testid="save-penerimaan">
              {busy ? "Menyimpan…" : "Simpan Penerimaan"}
            </button>
          </div>
        </div>
      )}

      {view === "list" && (
        <>
      <div className="prod-filters">
        <div className="prod-filter-row">
          <form className="search-inline" onSubmit={doSearch}>
            <Search size={14} />
            <input
              type="text"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Cari no penerimaan / item (live)"
              data-testid="penerimaan-search"
              aria-label="Cari penerimaan barang live"
            />
            {q && (
              <button type="button" className="ghost sm" onClick={clearSearch} aria-label="Bersihkan">
                <X size={12} />
              </button>
            )}
          </form>
          <select
            value={pageSize}
            onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
            data-testid="penerimaan-page-size"
          >
            <option value={25}>25 / halaman</option>
            <option value={50}>50 / halaman</option>
            <option value={100}>100 / halaman</option>
          </select>
        </div>
      </div>

      <div className="prod-table-wrap master-table-wrap">
        <table className="prod-table master-items-table" data-testid="penerimaan-table">
          <thead>
            <tr>
              <th className="col-fit">Tanggal</th>
              <th className="col-text">No Penerimaan</th>
              <th className="col-fit num">Jumlah Item</th>
              <th className="col-fit num">Total Qty</th>
              <th className="col-fit">Status</th>
              <th className="col-fit">Terakhir Sync</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={6} className="muted small">Memuat…</td></tr>}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={6} className="muted small" data-testid="penerimaan-empty">Belum ada penerimaan.</td>
              </tr>
            )}
            {!loading && items.map((it) => {
              const open = openId === it.accurate_receipt_id;
              return (
                <FragmentRow
                  key={it.accurate_receipt_id}
                  it={it}
                  open={open}
                  onToggle={() => setOpenId(open ? null : it.accurate_receipt_id)}
                />
              );
            })}
          </tbody>
        </table>
      </div>
      {pageCount > 1 && (
        <div className="master-pager" data-testid="penerimaan-pager">
          <button className="ghost sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Sebelumnya</button>
          <span className="muted small">{page} / {pageCount}</span>
          <button className="ghost sm" disabled={page >= pageCount} onClick={() => setPage((p) => p + 1)}>Berikutnya</button>
        </div>
      )}

      <div className="card" data-testid="po-linked-receipts" style={{ marginTop: 24 }}>
        <h2>Penerimaan terhadap Pesanan Pembelian</h2>
        <p className="muted small">Mencatat qty diterima per exact line PO. Tidak mengubah stok fisik Accurate/Stock Saat Ini. Over-receipt ditolak.</p>
        {recvErr && <div className="signal-strip" role="alert"><AlertCircle size={16} /><span>{recvErr}</span></div>}
        {recvMsg && <p className="muted small">{recvMsg}</p>}
        {canReceivePo && (
          <div data-testid="po-receive-form">
            <FormField id="recv-po-card" label="Pesanan Pembelian" isRequired value={recvPoId}>
              <select value={recvPoId} onChange={(e) => setRecvPoId(e.target.value)} data-testid="receive-po-selector">
                <option value="">Pilih PO</option>
                {eligiblePos.map((o) => <option key={o.id} value={o.id}>{o.po_number} · {o.supplier}</option>)}
              </select>
            </FormField>
            {recvPreview && (
              <>
                <FormProgress
                  percentage={Math.round(([recvDate, Object.values(recvQty).some(v => Number(v) > 0)].filter(Boolean).length / 2) * 100)}
                  completed={[recvDate, Object.values(recvQty).some(v => Number(v) > 0)].filter(Boolean).length}
                  total={2}
                />
                <FormSection id="recv-header-card" title="Tanggal Penerimaan" status={computeStatus([{ value: recvDate, isRequired: true }])}>
                  <FormField id="recv-date-card" label="Tanggal diterima" isRequired value={recvDate}>
                    <input type="date" value={recvDate} onChange={(e) => setRecvDate(e.target.value)} />
                  </FormField>
                </FormSection>
                <FormSection id="recv-lines-card" title="Qty Diterima per Item" status={Object.values(recvQty).some(v => Number(v) > 0) ? "complete" : "incomplete"}>
                  <table className="prod-table">
                    <thead><tr><th>Item</th><th>Dipesan</th><th>Sudah diterima</th><th>Sisa</th><th>Qty terima</th></tr></thead>
                    <tbody>
                      {(recvPreview.lines || []).map((l) => (
                        <tr key={l.purchase_order_line_id}>
                          <td>{l.item_name}</td>
                          <td className="num">{fmtQty(l.ordered_qty)}</td>
                          <td className="num">{fmtQty(l.cumulative_received_qty)}</td>
                          <td className="num">{fmtQty(l.remaining_receivable_qty)}</td>
                          <td>
                            <input type="number" min="0" value={recvQty[l.purchase_order_line_id] ?? ""} onChange={(e) => setRecvQty((p) => ({ ...p, [l.purchase_order_line_id]: e.target.value }))} data-testid="receive-qty-input" />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </FormSection>
                <button
                  className="primary"
                  disabled={recvBusy}
                  data-testid="save-po-receipt"
                  onClick={async () => {
                    setRecvBusy(true); setRecvErr(""); setRecvMsg("");
                    try {
                      const poLines = (recvPreview.lines || []).map((l) => ({
                        purchase_order_line_id: l.purchase_order_line_id,
                        received_qty: recvQty[l.purchase_order_line_id],
                      })).filter((l) => Number(l.received_qty) > 0);
                      const res = await api.createPurchaseGoodsReceipt({
                        purchase_order_id: recvPreview.purchase_order_id,
                        receipt_date: recvDate,
                        lines: poLines,
                        idempotency_key: newIdempotencyKey(),
                      });
                      setRecvMsg(`Tercatat ${res.receipt.receipt_number}`);
                      setRecvPoId(recvPreview.purchase_order_id);
                    } catch (ex) { setRecvErr(errDetail(ex)); }
                    finally { setRecvBusy(false); }
                  }}
                >
                  {recvBusy ? "Menyimpan…" : "Catat penerimaan"}
                </button>
              </>
            )}
          </div>
        )}
        <table className="prod-table" data-testid="po-receipts-table">
          <thead><tr><th>No</th><th>PO</th><th>Supplier</th><th>Tanggal</th></tr></thead>
          <tbody>
            {poReceipts.length === 0 && <tr><td colSpan={4} className="muted small">Belum ada penerimaan terhadap PO.</td></tr>}
            {poReceipts.map((r) => (
              <tr key={r.id}>
                <td className="mono">{r.receipt_number}</td>
                <td className="mono">{r.po_number}</td>
                <td>{r.supplier}</td>
                <td>{r.receipt_date}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
        </>
      )}
    </div>
  );
}

function FragmentRow({ it, open, onToggle }) {
  const isManual = String(it.source || "") === "manual";
  return (
    <>
      <tr data-testid={`penerimaan-row-${it.accurate_receipt_id}`}>
        <td className="col-fit nowrap">{it.receipt_date || "—"}</td>
        <td className="col-text">
          <button
            type="button"
            className="prod-th-label is-sort penerimaan-no"
            onClick={onToggle}
            data-testid={`penerimaan-toggle-${it.accurate_receipt_id}`}
          >
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            {it.receipt_number || it.accurate_receipt_id}
          </button>
        </td>
        <td className="col-fit num mono">{it.line_count ?? 0}</td>
        <td className="col-fit num mono">{fmtQty(it.total_qty)}</td>
        <td className="col-fit">{it.valid ? (it.status || "VALID") : (it.status || "INVALID")}</td>
        <td className="col-fit small nowrap">{isManual ? "—" : (it.last_synced_at ? formatDate(it.last_synced_at) : "—")}</td>
      </tr>
      {open && (
        <tr className="penerimaan-detail-row" data-testid={`penerimaan-detail-${it.accurate_receipt_id}`}>
          <td colSpan={6}>
            <p className="muted small" data-testid={`penerimaan-detail-meta-${it.accurate_receipt_id}`}>
              {isManual ? "Sumber: Manual" : "Sumber: Accurate"}
              {it.supplier ? ` · Supplier: ${it.supplier}` : ""}
              {it.po_number ? ` · PO: ${it.po_number}` : ""}
              {it.notes ? ` · ${it.notes}` : ""}
            </p>
            <table className="prod-table penerimaan-lines">
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Nama Item</th>
                  <th>Satuan</th>
                  <th className="num">Qty Diterima</th>
                  <th>Accurate Item ID</th>
                </tr>
              </thead>
              <tbody>
                {(it.lines || []).map((li) => (
                  <tr key={li.accurate_line_id || `${it.accurate_receipt_id}-${li.accurate_item_id}-${li.master_item_id}`}>
                    <td className="mono">{li.item_code || "—"}</td>
                    <td>{li.item_name || "—"}</td>
                    <td>{li.unit || "—"}</td>
                    <td className="num mono">{fmtQty(li.received_qty)}</td>
                    <td className="mono">{li.accurate_item_id || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}
