import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Download, Search, X } from "lucide-react";
import { Link } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";

export default function StockSaatIni() {
  const { hasPerm } = useAuth();
  const canUpdate = hasPerm("current_stock.update");
  const canOpname = canUpdate;
  const canExport = hasPerm("current_stock.export");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();
  const [stockFilter, setStockFilter] = useState("all");
  const [sortBy, setSortBy] = useState("name");
  const [sortDir, setSortDir] = useState("asc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [exporting, setExporting] = useState(false);
  const loadSeq = useRef(0);

  useEffect(() => { setPage(1); }, [q]);

  useEffect(() => {
    if (!searchPending) return;
    setLoading(true);
    setData((prev) => (prev ? { ...prev, items: [] } : prev));
  }, [searchPending]);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setErr("");
    setData((prev) => (prev ? { ...prev, items: [] } : prev));
    try {
      const d = await api.currentStock({
        q, stock_filter: stockFilter, sort_by: sortBy, sort_dir: sortDir, page, page_size: pageSize,
      });
      if (seq !== loadSeq.current) return;
      setData(d);
    } catch (ex) {
      if (seq !== loadSeq.current) return;
      const det = ex.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Stock Saat Ini"));
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [q, stockFilter, sortBy, sortDir, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  const clearSearch = () => {
    clearQuery();
    setPage(1);
  };

  const cycleSort = (col) => {
    if (sortBy === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortBy(col);
      setSortDir(col === "name" ? "asc" : "desc");
    }
    setPage(1);
  };

  const download = async () => {
    setExporting(true);
    try {
      const blob = await api.currentStockExport({
        q, stock_filter: stockFilter, sort_by: sortBy, sort_dir: sortDir,
      });
      const url = URL.createObjectURL(new Blob([blob], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `stock-saat-ini-${new Date().toISOString().slice(0, 10)}.xlsx`;
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
  const tableBusy = searchPending || loading;
  const tableView = liveSearchTableView({
    pending: searchPending,
    loading,
    itemCount: items.length,
  });

  return (
    <div className="prod-page master-items-page stock-page" data-testid="stock-saat-ini-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1 data-testid="stock-title">Stock Saat Ini</h1>
          <p className="subhead">Stock Saat Ini = MAX(Fisik − Outstanding SO, 0). Belum Dicetak = MAX(Outstanding − Sudah Dicetak, 0). Cetak hanya pindah bucket — tidak memotong Available dua kali.</p>
        </div>
        <div className="stock-heading-actions">
          {canOpname && (
            <Link className="ghost" to="/persediaan/stock-opname" data-testid="stock-opname-link">
              Stock Opname
            </Link>
          )}
          {canExport && (
            <button className="primary" onClick={download} disabled={exporting} data-testid="stock-export-button">
              <Download size={14} /> Export
            </button>
          )}
        </div>
      </div>

      {err && (
        <div className="signal-strip" data-testid="stock-error" role="alert">
          <div><AlertCircle size={16} /><span>{err}</span></div>
        </div>
      )}

      <div className="prod-filters" data-testid="stock-filters">
        <div className="prod-filter-row">
          <form className="search-inline" onSubmit={doSearch}>
            <Search size={14} />
            <input
              type="text"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Cari kode / nama / Accurate ID (live)"
              data-testid="stock-search"
              aria-label="Cari stock produk live"
            />
            {q && (
              <button type="button" className="ghost sm" onClick={clearSearch} aria-label="Bersihkan">
                <X size={12} />
              </button>
            )}
          </form>
          <select
            value={stockFilter}
            onChange={(e) => { setStockFilter(e.target.value); setPage(1); }}
            data-testid="stock-filter-sign"
          >
            <option value="all">Semua</option>
            <option value="positive">Positif</option>
            <option value="zero">Nol</option>
            <option value="negative">Negatif</option>
          </select>
          <select
            value={pageSize}
            onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
            data-testid="stock-page-size"
          >
            <option value={25}>25 / halaman</option>
            <option value={50}>50 / halaman</option>
            <option value={100}>100 / halaman</option>
          </select>
        </div>
      </div>

      <div className="prod-table-wrap master-table-wrap">
        <table className="prod-table master-items-table" data-testid="stock-table">
          <thead>
            <tr>
              <th className="col-fit">Kode</th>
              <th className="col-text">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("name")}>Nama Item</button>
              </th>
              <th className="col-fit">Satuan</th>
              <th className="col-fit num">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("physical_qty")}>Stock Fisik</button>
              </th>
              <th className="col-fit num">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("outstanding_so_qty")}>Qty Pesanan / Outstanding</button>
              </th>
              <th className="col-fit num">Belum Dicetak</th>
              <th className="col-fit num">Sudah Dicetak</th>
              <th className="col-fit num">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("stock_current")}>Stock Saat Ini</button>
              </th>
              <th className="col-fit">Terakhir Update</th>
            </tr>
          </thead>
          <tbody data-testid="stock-tbody" data-search-state={tableView}>
            {tableView === "loading" && (
              <tr data-testid="stock-loading"><td colSpan={9} className="muted small">Memuat…</td></tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={9} className="muted small" data-testid="stock-empty">Tidak ada item.</td>
              </tr>
            )}
            {tableView === "results" && items.map((it) => (
              <tr
                key={it.accurate_item_id || it.master_item_id}
                data-testid={`stock-row-${it.accurate_item_id || it.master_item_id}`}
                className={Number(it.stock_current) < 0 ? "stock-neg" : ""}
              >
                <td className="col-fit mono">{it.item_code || "—"}</td>
                <td className="col-text">{it.item_name || "—"}</td>
                <td className="col-fit">{it.unit || "—"}</td>
                <td className="col-fit num mono" data-testid={`stock-physical-${it.accurate_item_id || it.master_item_id}`}>{fmtQty(it.physical_qty)}</td>
                <td className="col-fit num mono" data-testid={`stock-outstanding-${it.accurate_item_id || it.master_item_id}`}>{fmtQty(it.outstanding_so_qty)}</td>
                <td className="col-fit num mono" data-testid={`stock-unprinted-${it.accurate_item_id || it.master_item_id}`}>{fmtQty(it.unprinted_qty)}</td>
                <td className="col-fit num mono" data-testid={`stock-printed-${it.accurate_item_id || it.master_item_id}`}>{fmtQty(it.printed_qty)}</td>
                <td className={"col-fit num mono" + (Number(it.stock_current) < 0 ? " stock-neg-val" : "")} data-testid={`stock-current-${it.accurate_item_id || it.master_item_id}`}>
                  {fmtQty(it.stock_current)}
                </td>
                <td className="col-fit small nowrap" data-testid={`stock-updated-${it.accurate_item_id}`}>
                  {it.updated_at ? (
                    <>
                      {formatDate(it.updated_at)}
                      {it.updated_by_name || it.updated_by ? (
                        <span className="muted"> · {it.updated_by_name || it.updated_by}</span>
                      ) : null}
                    </>
                  ) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <div className="master-pager" data-testid="stock-pager">
          <button className="ghost sm" disabled={page <= 1 || tableBusy} onClick={() => setPage((p) => p - 1)}>Sebelumnya</button>
          <span className="muted small">Halaman {data?.page || page} dari {pageCount} · {data?.total || 0} item</span>
          <button className="ghost sm" disabled={page >= pageCount || tableBusy} onClick={() => setPage((p) => p + 1)}>Berikutnya</button>
        </div>
      )}
    </div>
  );
}
