import { useCallback, useEffect, useState } from "react";
import { AlertCircle, ChevronDown, ChevronRight, Download, RefreshCw, Search, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { errDetail, newIdempotencyKey, todayYmd } from "../lib/purchaseUi";
import { FormSection, FormField, FormProgress, computeStatus } from "../components/form-standard";

export default function PenerimaanBarang() {
  const { hasPerm } = useAuth();
  const [searchParams] = useSearchParams();
  const canSync = hasPerm("purchase_receipts.sync");
  const canExport = hasPerm("purchase_receipts.export");
  const canReceive = hasPerm("purchase_receipts.receive") || hasPerm("purchase_receipts.sync");
  const queryPoId = searchParams.get("purchase_order_id") || "";
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");
  const [syncErr, setSyncErr] = useState("");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
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

  useEffect(() => {
    api.listPurchaseGoodsReceipts({}).then((r) => setPoReceipts(r.receipts || [])).catch(() => setPoReceipts([]));
    if (canReceive) {
      api.listPurchaseOrders({ eligible_for_receipt: true }).then((r) => setEligiblePos(r.orders || [])).catch(() => setEligiblePos([]));
    }
  }, [canReceive, recvMsg]);

  useEffect(() => {
    if (!recvPoId) { setRecvPreview(null); return; }
    api.purchaseGoodsReceiptPreview(recvPoId).then((p) => {
      setRecvPreview(p);
      const map = {};
      (p.lines || []).forEach((l) => { map[l.purchase_order_line_id] = l.remaining_receivable_qty; });
      setRecvQty(map);
    }).catch((ex) => setRecvErr(errDetail(ex)));
  }, [recvPoId]);

  const doSearch = (e) => {
    e?.preventDefault?.();
    setPage(1);
    setQ(qInput.trim());
  };
  const clearSearch = () => {
    setQInput("");
    setQ("");
    setPage(1);
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

  const items = data?.items || [];
  const pageCount = data?.page_count || 1;

  return (
    <div className="prod-page master-items-page" data-testid="penerimaan-barang-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Pembelian</p>
          <h1 data-testid="penerimaan-title">Penerimaan Barang</h1>
          <p className="subhead">Data penerimaan barang yang disinkronkan dari Accurate.</p>
          <p className="muted small stock-last-sync" data-testid="penerimaan-last-sync">
            Terakhir sinkronisasi: {data?.last_synced_at ? formatDate(data.last_synced_at) : "belum"}
          </p>
        </div>
        <div className="stock-heading-actions">
          {canSync && (
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
          {canExport && (
            <button className="ghost" onClick={download} disabled={exporting} data-testid="penerimaan-export-button">
              <Download size={14} /> Export
            </button>
          )}
        </div>
      </div>

      {(err || syncErr) && (
        <div className="signal-strip" data-testid="penerimaan-error" role="alert">
          <div><AlertCircle size={16} /><span>{syncErr || err}</span></div>
        </div>
      )}
      {syncMsg && !syncErr && (
        <p className="muted small stock-sync-summary" data-testid="penerimaan-sync-summary">{syncMsg}</p>
      )}

      <div className="prod-filters">
        <div className="prod-filter-row">
          <form className="search-inline" onSubmit={doSearch}>
            <Search size={14} />
            <input
              type="text"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Cari no penerimaan / item"
              data-testid="penerimaan-search"
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
        {canReceive && (
          <div data-testid="po-receive-form">
            <FormField id="recv-po" label="Pesanan Pembelian" isRequired value={recvPoId}>
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
                <FormSection id="recv-header" title="Tanggal Penerimaan" status={computeStatus([{ value: recvDate, isRequired: true }])}>
                  <FormField id="recv-date" label="Tanggal diterima" isRequired value={recvDate}>
                    <input type="date" value={recvDate} onChange={(e) => setRecvDate(e.target.value)} />
                  </FormField>
                </FormSection>
                <FormSection id="recv-lines" title="Qty Diterima per Item" status={Object.values(recvQty).some(v => Number(v) > 0) ? "complete" : "incomplete"}>
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
                      const lines = (recvPreview.lines || []).map((l) => ({
                        purchase_order_line_id: l.purchase_order_line_id,
                        received_qty: recvQty[l.purchase_order_line_id],
                      })).filter((l) => Number(l.received_qty) > 0);
                      const res = await api.createPurchaseGoodsReceipt({
                        purchase_order_id: recvPreview.purchase_order_id,
                        receipt_date: recvDate,
                        lines,
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
    </div>
  );
}

function FragmentRow({ it, open, onToggle }) {
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
        <td className="col-fit small nowrap">{it.last_synced_at ? formatDate(it.last_synced_at) : "—"}</td>
      </tr>
      {open && (
        <tr className="penerimaan-detail-row" data-testid={`penerimaan-detail-${it.accurate_receipt_id}`}>
          <td colSpan={6}>
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
                  <tr key={li.accurate_line_id || `${it.accurate_receipt_id}-${li.accurate_item_id}`}>
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
