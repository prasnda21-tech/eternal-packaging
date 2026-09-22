import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Coins, Download, Search, X } from "lucide-react";
import { fmtQty } from "../components/qtyUi";
import { useDebouncedLiveSearch, liveSearchTableView } from "../hooks/useDebouncedLiveSearch";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { formatRupiah } from "../lib/purchaseUi";

const STATUS_LABELS = {
  OK: "OK",
  MISSING_COST: "Belum Ada HPP",
  NEEDS_COST: "Butuh Harga PO",
  NEEDS_UOM: "Butuh Konversi Satuan",
  QTY_MISMATCH: "Qty Tidak Cocok",
  FAILED: "Gagal",
};

function statusLabel(status) {
  return STATUS_LABELS[status] || status || "Belum Ada HPP";
}

export default function NilaiPersediaan() {
  const { hasPerm } = useAuth();
  const canExport = hasPerm("inventory_valuation.export");
  const canManageOpening = hasPerm("inventory_valuation.manage_opening");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const {
    input: qInput,
    setInput: setQInput,
    q,
    flush: doSearch,
    clear: clearQuery,
    pending: searchPending,
  } = useDebouncedLiveSearch();
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortBy, setSortBy] = useState("name");
  const [sortDir, setSortDir] = useState("asc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [exporting, setExporting] = useState(false);
  const [openingOpen, setOpeningOpen] = useState(false);
  const [openingPreview, setOpeningPreview] = useState(null);
  const [openingLoading, setOpeningLoading] = useState(false);
  const [openingConfirming, setOpeningConfirming] = useState(false);
  const [openingError, setOpeningError] = useState("");
  const [openingRequestId, setOpeningRequestId] = useState("");
  const loadSeq = useRef(0);

  useEffect(() => { setPage(1); }, [q]);

  useEffect(() => {
    if (!searchPending) return;
    setLoading(true);
    setData((previous) => (previous ? { ...previous, items: [] } : previous));
  }, [searchPending]);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError("");
    setData((previous) => (previous ? { ...previous, items: [] } : previous));
    try {
      const response = await api.inventoryValuation({
        q,
        status_filter: statusFilter,
        sort_by: sortBy,
        sort_dir: sortDir,
        page,
        page_size: pageSize,
      });
      if (seq !== loadSeq.current) return;
      setData(response);
    } catch (exception) {
      if (seq !== loadSeq.current) return;
      const detail = exception.response?.data?.detail;
      setError(typeof detail === "object" ? detail.message : (detail || "Gagal memuat Nilai Persediaan"));
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [q, statusFilter, sortBy, sortDir, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  const cycleSort = (column) => {
    if (sortBy === column) setSortDir((direction) => (direction === "asc" ? "desc" : "asc"));
    else {
      setSortBy(column);
      setSortDir(column === "name" ? "asc" : "desc");
    }
    setPage(1);
  };

  const download = async () => {
    setExporting(true);
    setError("");
    try {
      const blob = await api.inventoryValuationExport({
        q,
        status_filter: statusFilter,
        sort_by: sortBy,
        sort_dir: sortDir,
      });
      const url = URL.createObjectURL(new Blob([blob], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }));
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `nilai-persediaan-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (exception) {
      const detail = exception.response?.data?.detail;
      setError(typeof detail === "object" ? detail.message : (detail || "Gagal export Nilai Persediaan"));
    } finally {
      setExporting(false);
    }
  };

  const openOpeningPreview = async () => {
    setOpeningOpen(true);
    setOpeningLoading(true);
    setOpeningError("");
    setOpeningPreview(null);
    setOpeningRequestId(
      globalThis.crypto?.randomUUID?.()
      || `hpp-opening-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    );
    try {
      setOpeningPreview(await api.inventoryValuationOpeningPreview());
    } catch (exception) {
      const detail = exception.response?.data?.detail;
      setOpeningError(typeof detail === "object" ? detail.message : (detail || "Gagal memuat preview HPP awal"));
    } finally {
      setOpeningLoading(false);
    }
  };

  const confirmOpening = async () => {
    if (!openingPreview?.can_confirm || openingConfirming) return;
    const accepted = window.confirm(
      "Anda akan menetapkan HPP awal persediaan berdasarkan Harga Customer Published dan Stock Fisik saat ini.\n\n"
      + "Setelah aktivasi, perubahan Harga Customer tidak akan langsung mengubah HPP Persediaan.\n\n"
      + "HPP berikutnya diperbarui dari transaksi Penerimaan Barang.",
    );
    if (!accepted) return;
    setOpeningConfirming(true);
    setOpeningError("");
    try {
      await api.confirmInventoryValuationOpening({
        preview_token: openingPreview.preview_token,
        client_request_id: openingRequestId,
      });
      setOpeningOpen(false);
      setOpeningPreview(null);
      await load();
    } catch (exception) {
      const detail = exception.response?.data?.detail;
      setOpeningError(typeof detail === "object" ? detail.message : (detail || "Gagal mengaktifkan HPP awal"));
    } finally {
      setOpeningConfirming(false);
    }
  };

  const items = data?.items || [];
  const summary = data?.summary || {};
  const pageCount = data?.page_count || 1;
  const tableBusy = searchPending || loading;
  const tableView = liveSearchTableView({
    pending: searchPending,
    loading,
    itemCount: items.length,
  });

  return (
    <div className="prod-page master-items-page stock-page inventory-value-page" data-testid="inventory-value-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1 data-testid="inventory-value-title">Nilai Persediaan</h1>
          <p className="subhead" data-testid="inventory-value-formula">
            Nilai Persediaan = Stock Fisik × HPP Rata-Rata. HPP bersumber dari PO dan diakui saat Penerimaan Barang.
          </p>
        </div>
        <div className="stock-heading-actions">
          {canManageOpening && !data?.active_version && (
            <button
              className="ghost"
              onClick={openOpeningPreview}
              data-testid="inventory-opening-button"
            >
              <Coins size={14} /> Atur HPP Awal
            </button>
          )}
          {canManageOpening && data?.active_version && (
            <span className="inventory-opening-active" data-testid="inventory-opening-active">
              HPP Persediaan sudah diaktifkan.
            </span>
          )}
          {canExport && (
            <button
              className="primary"
              onClick={download}
              disabled={exporting}
              data-testid="inventory-value-export"
            >
              <Download size={14} /> {exporting ? "Menyiapkan…" : "Export Excel"}
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="signal-strip" data-testid="inventory-value-error" role="alert">
          <div><AlertCircle size={16} /><span>{error}</span></div>
        </div>
      )}

      <div className="inventory-value-summary" data-testid="inventory-value-summary">
        <div className="inventory-value-summary-card is-total">
          <span>Total Nilai Persediaan</span>
          <strong>{formatRupiah(summary.total_inventory_value || 0)}</strong>
        </div>
        <div className="inventory-value-summary-card">
          <span>Item Dinilai</span>
          <strong>{summary.valued_items || 0}</strong>
        </div>
        <div className="inventory-value-summary-card">
          <span>Item Belum Punya HPP</span>
          <strong>{summary.items_without_hpp || 0}</strong>
        </div>
        <div className="inventory-value-summary-card is-problem">
          <span>Item Bermasalah</span>
          <strong>{summary.problem_items || 0}</strong>
        </div>
      </div>

      <div className="prod-filters" data-testid="inventory-value-filters">
        <div className="prod-filter-row">
          <form className="search-inline" onSubmit={doSearch}>
            <Search size={14} />
            <input
              type="text"
              value={qInput}
              onChange={(event) => setQInput(event.target.value)}
              placeholder="Cari kode / nama (live)"
              data-testid="inventory-value-search"
              aria-label="Cari nilai persediaan"
            />
            {q && (
              <button
                type="button"
                className="ghost sm"
                onClick={() => { clearQuery(); setPage(1); }}
                aria-label="Bersihkan"
              >
                <X size={12} />
              </button>
            )}
          </form>
          <select
            value={statusFilter}
            onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }}
            data-testid="inventory-value-status-filter"
          >
            <option value="all">Semua</option>
            <option value="ok">OK</option>
            <option value="missing">Belum Ada HPP</option>
            <option value="problem">Bermasalah</option>
          </select>
          <select
            value={pageSize}
            onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}
            data-testid="inventory-value-page-size"
          >
            <option value={25}>25 / halaman</option>
            <option value={50}>50 / halaman</option>
            <option value={100}>100 / halaman</option>
          </select>
        </div>
      </div>

      <div className="prod-table-wrap master-table-wrap">
        <table className="prod-table master-items-table" data-testid="inventory-value-table">
          <thead>
            <tr>
              <th className="col-fit">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("code")}>Kode</button>
              </th>
              <th className="col-text">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("name")}>Nama</button>
              </th>
              <th className="col-fit">Satuan</th>
              <th className="col-fit num">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("stock_fisik")}>Stock Fisik</button>
              </th>
              <th className="col-fit num">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("current_hpp")}>HPP Rata-Rata</button>
              </th>
              <th className="col-fit num">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("inventory_value")}>Nilai Persediaan</button>
              </th>
              <th className="col-fit">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("hpp_status")}>Status HPP</button>
              </th>
              <th className="col-fit">Terakhir Update</th>
            </tr>
          </thead>
          <tbody data-testid="inventory-value-tbody" data-search-state={tableView}>
            {tableView === "loading" && (
              <tr data-testid="inventory-value-loading">
                <td colSpan={8} className="muted small">Memuat…</td>
              </tr>
            )}
            {tableView === "empty" && (
              <tr>
                <td colSpan={8} className="muted small" data-testid="inventory-value-empty">
                  Tidak ada item.
                </td>
              </tr>
            )}
            {tableView === "results" && items.map((item) => {
              const key = item.accurate_item_id || item.master_item_id;
              const hasHpp = item.current_hpp != null;
              const hasValue = item.inventory_value != null;
              return (
                <tr
                  key={key}
                  data-testid={`inventory-value-row-${key}`}
                  className={item.hpp_status === "QTY_MISMATCH" ? "stock-neg" : ""}
                >
                  <td className="col-fit mono">{item.item_code || "—"}</td>
                  <td className="col-text">{item.item_name || "—"}</td>
                  <td className="col-fit">{item.unit || "—"}</td>
                  <td className="col-fit num mono">{fmtQty(item.stock_fisik)}</td>
                  <td className="col-fit num mono" data-testid={`inventory-value-hpp-${key}`}>
                    {hasHpp ? formatRupiah(item.current_hpp) : "Belum Ada HPP"}
                  </td>
                  <td className="col-fit num mono" data-testid={`inventory-value-amount-${key}`}>
                    {hasValue ? formatRupiah(item.inventory_value) : "—"}
                  </td>
                  <td className="col-fit">
                    <span
                      className={`inventory-value-status is-${String(item.hpp_status || "missing").toLowerCase()}`}
                      title={item.hpp_reason || ""}
                    >
                      {statusLabel(item.hpp_status)}
                    </span>
                  </td>
                  <td className="col-fit small nowrap">
                    {item.updated_at ? formatDate(item.updated_at) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <div className="master-pager" data-testid="inventory-value-pager">
          <button
            className="ghost sm"
            disabled={page <= 1 || tableBusy}
            onClick={() => setPage((current) => current - 1)}
          >
            Sebelumnya
          </button>
          <span className="muted small">
            Halaman {data?.page || page} dari {pageCount} · {data?.total || 0} item
          </span>
          <button
            className="ghost sm"
            disabled={page >= pageCount || tableBusy}
            onClick={() => setPage((current) => current + 1)}
          >
            Berikutnya
          </button>
        </div>
      )}

      {openingOpen && (
        <div
          className="inventory-opening-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !openingConfirming) setOpeningOpen(false);
          }}
          data-testid="inventory-opening-modal"
        >
          <section className="inventory-opening-dialog" role="dialog" aria-modal="true" aria-labelledby="opening-title">
            <div className="inventory-opening-head">
              <div>
                <p className="eyebrow">Persediaan</p>
                <h2 id="opening-title">Atur HPP Awal Persediaan</h2>
                <p className="muted small">
                  HPP awal diambil dari Harga Customer yang sudah Published. Draft tidak digunakan.
                  Setelah dikonfirmasi, HPP Persediaan berikutnya akan mengikuti Penerimaan Barang
                  dengan metode rata-rata.
                </p>
              </div>
              <button
                type="button"
                className="ghost sm"
                onClick={() => setOpeningOpen(false)}
                disabled={openingConfirming}
                aria-label="Tutup"
              >
                <X size={14} />
              </button>
            </div>

            {openingError && (
              <div className="signal-strip" role="alert" data-testid="inventory-opening-error">
                <div><AlertCircle size={16} /><span>{openingError}</span></div>
              </div>
            )}
            {openingLoading && <p className="muted small">Memuat preview HPP Published…</p>}
            {openingPreview && (
              <>
                <div className="inventory-opening-summary">
                  <div><span>Total Item</span><strong>{openingPreview.summary.total_items}</strong></div>
                  <div><span>Siap Digunakan</span><strong>{openingPreview.summary.ready_items}</strong></div>
                  <div><span>Belum Ada HPP Published</span><strong>{openingPreview.summary.missing_published_items}</strong></div>
                  <div><span>Bermasalah</span><strong>{openingPreview.summary.problem_items}</strong></div>
                  <div><span>Total Nilai Opening</span><strong>{formatRupiah(openingPreview.summary.total_opening_value || 0)}</strong></div>
                </div>
                {openingPreview.blocking_message && (
                  <div className="signal-strip" data-testid="inventory-opening-blocked">
                    <div><AlertCircle size={16} /><span>{openingPreview.blocking_message}</span></div>
                  </div>
                )}
                <div className="inventory-opening-table-wrap">
                  <table className="prod-table" data-testid="inventory-opening-table">
                    <thead>
                      <tr>
                        <th>Kode</th>
                        <th>Nama Produk</th>
                        <th>Satuan</th>
                        <th className="num">Stock Fisik</th>
                        <th className="num">HPP Published</th>
                        <th className="num">HPP Awal</th>
                        <th className="num">Nilai Awal</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {openingPreview.rows.map((row) => (
                        <tr key={row.master_item_id}>
                          <td className="mono">{row.item_code || "—"}</td>
                          <td>{row.item_name}</td>
                          <td>{row.unit || "—"}</td>
                          <td className="num mono">{row.stock_fisik == null ? "—" : fmtQty(row.stock_fisik)}</td>
                          <td className="num mono">{row.published_hpp == null ? "—" : formatRupiah(row.published_hpp)}</td>
                          <td className="num mono">{row.opening_hpp == null ? "—" : formatRupiah(row.opening_hpp)}</td>
                          <td className="num mono">{row.opening_value == null ? "—" : formatRupiah(row.opening_value)}</td>
                          <td>
                            <span className={`inventory-opening-status is-${row.status.toLowerCase().replaceAll(" ", "-")}`}>
                              {row.status}
                            </span>
                            {row.message && <small className="muted">{row.message}</small>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="inventory-opening-actions">
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => setOpeningOpen(false)}
                    disabled={openingConfirming}
                  >
                    Batal
                  </button>
                  <button
                    type="button"
                    className="primary"
                    onClick={confirmOpening}
                    disabled={!openingPreview.can_confirm || openingConfirming}
                    data-testid="inventory-opening-confirm"
                  >
                    {openingConfirming ? "Mengaktifkan…" : "Konfirmasi HPP Awal"}
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
