import { useCallback, useEffect, useState } from "react";
import { AlertCircle, ChevronDown, ChevronRight, Download, RefreshCw, Search, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";

export default function PenerimaanBarang() {
  const { hasPerm } = useAuth();
  const canSync = hasPerm("purchase_receipts.sync");
  const canExport = hasPerm("purchase_receipts.export");
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
