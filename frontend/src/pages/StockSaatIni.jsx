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
  const canHppOpening = hasPerm("settings.hpp.manage");
  const canExport = hasPerm("current_stock.export");
  const [data, setData] = useState(null);
  const canEditPhysical = Boolean(data?.can_correct_physical_stock);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const { input: qInput, setInput: setQInput, q, flush: doSearch, clear: clearQuery, pending: searchPending } = useDebouncedLiveSearch();
  const [stockFilter, setStockFilter] = useState("all");
  const [sortBy, setSortBy] = useState("name");
  const [sortDir, setSortDir] = useState("asc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [exporting, setExporting] = useState(false);
  const [editing, setEditing] = useState(null);
  const [physicalInput, setPhysicalInput] = useState("");
  const [editRequestId, setEditRequestId] = useState("");
  const [savingPhysical, setSavingPhysical] = useState(false);
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

  const openPhysicalEdit = (item) => {
    setEditing(item);
    setPhysicalInput(String(item.physical_qty ?? 0));
    setEditRequestId(
      globalThis.crypto?.randomUUID?.()
      || `stock-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    );
    setErr("");
  };

  const savePhysical = async () => {
    if (!editing || savingPhysical) return;
    const physicalQty = Number(String(physicalInput).replace(",", "."));
    if (!Number.isFinite(physicalQty) || physicalQty < 0) {
      setErr("Stock Fisik harus berupa angka non-negatif");
      return;
    }
    setSavingPhysical(true);
    setErr("");
    try {
      await api.currentStockUpdate(editing.master_item_id, {
        physical_qty: physicalQty,
        note: "Koreksi manual Stock Fisik dari Stock Saat Ini",
        client_request_id: editRequestId,
      });
      setEditing(null);
      await load();
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal mengubah Stock Fisik"));
    } finally {
      setSavingPhysical(false);
    }
  };

  const handlePhysicalKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      savePhysical();
    } else if (e.key === "Escape") {
      e.preventDefault();
      setEditing(null);
      setErr("");
    }
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
          <p className="subhead" data-testid="stock-formula">
            {data?.stock_engine === "v2"
              ? "Stock Saat Ini = Stock Fisik − Qty Pesanan. Nilai negatif menunjukkan kekurangan stock. Belum Dicetak = MAX(Qty Pesanan − Sudah Dicetak, 0). Sudah Dicetak hanya informasi dan tidak memotong stock dua kali."
              : "(Qty Saat Ini + Pembelian Barang) − Qty PO − Qty Cetak Operator"}
          </p>
        </div>
        <div className="stock-heading-actions">
          {canOpname && (
            <Link className="ghost" to="/persediaan/stock-opname" data-testid="stock-opname-link">
              Stock Opname
            </Link>
          )}
          {canHppOpening && (
            <Link className="ghost" to="/persediaan/hpp-awal" data-testid="hpp-opening-link">
              HPP Awal
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
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("name")}>Nama</button>
              </th>
              <th className="col-fit">Satuan</th>
              {data?.stock_engine === "v2" ? (
                <>
                  <th className="col-fit num">
                    <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("physical_qty")}>Stock Fisik</button>
                  </th>
                  <th className="col-fit num">
                    <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("outstanding_so_qty")}>Qty Pesanan</button>
                  </th>
                  <th className="col-fit num">Belum Dicetak</th>
                  <th className="col-fit num">Sudah Dicetak</th>
                </>
              ) : (
                <>
                  <th className="col-fit num">Qty Saat Ini</th>
                  <th className="col-fit num">Pembelian Barang</th>
                  <th className="col-fit num">
                    <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("qty_po")}>Qty PO</button>
                  </th>
                  <th className="col-fit num">Qty Cetak Operator</th>
                </>
              )}
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
                {data?.stock_engine === "v2" ? (
                  <>
                    <td className="col-fit num mono" data-testid={`stock-physical-${it.accurate_item_id || it.master_item_id}`}>
                      {editing?.master_item_id === it.master_item_id ? (
                        <input
                          className="stock-inline-input"
                          type="number"
                          min="0"
                          step="any"
                          value={physicalInput}
                          onChange={(e) => setPhysicalInput(e.target.value)}
                          onKeyDown={handlePhysicalKeyDown}
                          onBlur={() => { if (!savingPhysical) setEditing(null); }}
                          onFocus={(e) => e.target.select()}
                          disabled={savingPhysical}
                          data-testid={`stock-physical-inline-input-${it.master_item_id}`}
                          autoFocus
                        />
                      ) : canEditPhysical && it.master_item_id ? (
                        <span
                          className="stock-inline-value"
                          role="button"
                          tabIndex={0}
                          title="Klik untuk koreksi Stock Fisik"
                          onClick={() => openPhysicalEdit(it)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") openPhysicalEdit(it);
                          }}
                          data-testid={`stock-physical-inline-value-${it.master_item_id}`}
                        >
                          {fmtQty(it.physical_qty)}
                        </span>
                      ) : fmtQty(it.physical_qty)}
                    </td>
                    <td className="col-fit num mono" data-testid={`stock-outstanding-${it.accurate_item_id || it.master_item_id}`}>{fmtQty(it.outstanding_so_qty)}</td>
                    <td className="col-fit num mono" data-testid={`stock-unprinted-${it.accurate_item_id || it.master_item_id}`}>{fmtQty(it.unprinted_qty)}</td>
                    <td className="col-fit num mono" data-testid={`stock-printed-${it.accurate_item_id || it.master_item_id}`}>{fmtQty(it.printed_qty)}</td>
                  </>
                ) : (
                  <>
                    <td className="col-fit num mono" data-testid={`stock-manual-${it.accurate_item_id || it.master_item_id}`}>{fmtQty(it.manual_current_qty)}</td>
                    <td className="col-fit num mono" data-testid={`stock-purchase-${it.accurate_item_id || it.master_item_id}`}>{fmtQty(it.purchase_qty)}</td>
                    <td className="col-fit num mono" data-testid={`stock-po-${it.accurate_item_id || it.master_item_id}`}>{fmtQty(it.qty_po)}</td>
                    <td className="col-fit num mono" data-testid={`stock-print-${it.accurate_item_id || it.master_item_id}`}>{fmtQty(it.operator_print_qty)}</td>
                  </>
                )}
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
