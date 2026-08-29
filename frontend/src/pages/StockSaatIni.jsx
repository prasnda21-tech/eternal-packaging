import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Download, Search, Upload, X } from "lucide-react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { fmtQty } from "../components/qtyUi";
import { isAccuratePurchaseSource, purchaseSourceLabel } from "../lib/currentStock";
import ImportPreviewModal from "../components/ImportPreviewModal";

function parseInputQty(raw, { allowNegative = false } = {}) {
  const t = String(raw ?? "").trim();
  if (t === "") return { ok: false, error: "Qty wajib diisi" };
  const n = Number(t.replace(",", "."));
  if (!Number.isFinite(n) || Number.isNaN(n)) return { ok: false, error: "Qty harus angka" };
  if (!allowNegative && n < 0) return { ok: false, error: "Qty tidak boleh negatif" };
  return { ok: true, value: n };
}

function QtyCell({ value, disabled, testid, onSave, allowNegative = false }) {
  const [draft, setDraft] = useState(value ?? 0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { setDraft(value ?? 0); }, [value]);

  const commit = async () => {
    const parsed = parseInputQty(draft, { allowNegative });
    if (!parsed.ok) {
      setErr(parsed.error);
      setDraft(value ?? 0);
      return;
    }
    if (Number(parsed.value) === Number(value ?? 0)) {
      setErr("");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await onSave(parsed.value);
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal simpan"));
      setDraft(value ?? 0);
    } finally {
      setBusy(false);
    }
  };

  if (disabled) return <span className="mono">{fmtQty(value)}</span>;
  return (
    <span className="stock-qty-edit">
      <input
        type="number"
        min={allowNegative ? undefined : "0"}
        step="1"
        value={draft}
        disabled={busy}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
        data-testid={testid}
        className="manual-input"
      />
      {err && <span className="stock-qty-err">{err}</span>}
    </span>
  );
}

export default function StockSaatIni() {
  const { hasPerm } = useAuth();
  const canUpdate = hasPerm("current_stock.update");
  const canExport = hasPerm("current_stock.export");
  const canImport = hasPerm("current_stock.import");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [stockFilter, setStockFilter] = useState("all");
  const [sortBy, setSortBy] = useState("name");
  const [sortDir, setSortDir] = useState("asc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [exporting, setExporting] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const d = await api.currentStock({
        q, stock_filter: stockFilter, sort_by: sortBy, sort_dir: sortDir, page, page_size: pageSize,
      });
      setData(d);
    } catch (ex) {
      const det = ex.response?.data?.detail;
      setErr(typeof det === "object" ? det.message : (det || "Gagal memuat Stock Saat Ini"));
    } finally {
      setLoading(false);
    }
  }, [q, stockFilter, sortBy, sortDir, page, pageSize]);

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

  const cycleSort = (col) => {
    if (sortBy === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortBy(col);
      setSortDir(col === "name" ? "asc" : "desc");
    }
    setPage(1);
  };

  const patchRow = async (item, fields) => {
    const id = item.master_item_id || item.accurate_item_id;
    const r = await api.currentStockUpdate(id, fields);
    const next = r.item;
    setData((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        items: (prev.items || []).map((row) => (
          row.master_item_id === next.master_item_id
          || row.accurate_item_id === next.accurate_item_id
            ? next
            : row
        )),
      };
    });
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
  const purchaseAccurate = isAccuratePurchaseSource(data?.purchase_source);
  const sourceLabel = purchaseSourceLabel(data?.purchase_source);

  return (
    <div className="prod-page master-items-page stock-page" data-testid="stock-saat-ini-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1 data-testid="stock-title">Stock Saat Ini</h1>
          <p className="subhead">(Qty Saat Ini + Pembelian Barang) − Qty PO − Qty Cetak Operator</p>
        </div>
        <div className="stock-heading-actions">
          {canImport && (
            <button type="button" className="ghost" onClick={() => setImportOpen(true)} data-testid="stock-import-button">
              <Upload size={14} /> Impor Stock
            </button>
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
              placeholder="Cari kode / nama"
              data-testid="stock-search"
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
              <th className="col-fit num">Qty Saat Ini</th>
              <th className="col-fit num">
                Pembelian Barang
                <span className="stock-src muted" data-testid="stock-purchase-source-header">{sourceLabel}</span>
              </th>
              <th className="col-fit num">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("qty_po")}>Qty PO</button>
              </th>
              <th className="col-fit num">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("operator_print_qty")}>Qty Cetak Operator</button>
              </th>
              <th className="col-fit num">
                <button type="button" className="prod-th-label is-sort" onClick={() => cycleSort("stock_current")}>Stock Saat Ini</button>
              </th>
              <th className="col-fit">Terakhir Update</th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={9} className="muted small">Memuat…</td></tr>}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={9} className="muted small" data-testid="stock-empty">Tidak ada item.</td>
              </tr>
            )}
            {!loading && items.map((it) => (
              <tr
                key={it.accurate_item_id || it.master_item_id}
                data-testid={`stock-row-${it.accurate_item_id || it.master_item_id}`}
                className={Number(it.stock_current) < 0 ? "stock-neg" : ""}
              >
                <td className="col-fit mono">{it.item_code || "—"}</td>
                <td className="col-text">{it.item_name || "—"}</td>
                <td className="col-fit">{it.unit || "—"}</td>
                <td className="col-fit num">
                  <QtyCell
                    value={it.manual_current_qty}
                    disabled={!canUpdate}
                    allowNegative
                    testid={`stock-manual-${it.accurate_item_id}`}
                    onSave={(v) => patchRow(it, { manual_current_qty: v })}
                  />
                </td>
                <td className="col-fit num">
                  {purchaseAccurate ? (
                    <span className="stock-purchase-cell">
                      <span className="mono" data-testid={`stock-purchase-${it.accurate_item_id}`}>{fmtQty(it.purchase_qty)}</span>
                      <span className="stock-src muted" title="Berdasarkan Penerimaan Barang">Accurate</span>
                    </span>
                  ) : (
                    <span className="stock-purchase-cell">
                      <QtyCell
                        value={it.purchase_qty}
                        disabled={!canUpdate}
                        testid={`stock-purchase-${it.accurate_item_id}`}
                        onSave={(v) => patchRow(it, { manual_purchase_qty: v })}
                      />
                      <span className="stock-src muted">Manual</span>
                    </span>
                  )}
                </td>
                <td className="col-fit num mono">{fmtQty(it.qty_po)}</td>
                <td className="col-fit num mono">{fmtQty(it.operator_print_qty)}</td>
                <td className={"col-fit num mono" + (Number(it.stock_current) < 0 ? " stock-neg-val" : "")} data-testid={`stock-current-${it.accurate_item_id}`}>
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
          <button className="ghost sm" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)}>Sebelumnya</button>
          <span className="muted small">Halaman {data?.page || page} dari {pageCount} · {data?.total || 0} item</span>
          <button className="ghost sm" disabled={page >= pageCount || loading} onClick={() => setPage((p) => p + 1)}>Berikutnya</button>
        </div>
      )}

      <ImportPreviewModal
        open={importOpen}
        title="Impor Stock Saat Ini"
        testid="stock-import-modal"
        applyLabel="Sesuaikan Stock Terpilih"
        confirmText="Sesuaikan stock untuk baris terpilih? Nilai file menjadi Stock Saat Ini (bukan +/-)."
        columns={[
          { key: "item_name", label: "Item" },
          { key: "item_code", label: "Kode" },
          { key: "stock_system", label: "Stock System", num: true, render: (r) => (r.stock_system == null ? "—" : fmtQty(r.stock_system)) },
          { key: "stock_file", label: "Stock File", num: true, render: (r) => (r.stock_file == null ? "—" : fmtQty(r.stock_file)) },
          { key: "delta", label: "Selisih", num: true, render: (r) => (r.delta == null ? "—" : fmtQty(r.delta)) },
          { key: "status", label: "Status", render: (r) => r.status + (r.reason ? ` — ${r.reason}` : "") },
        ]}
        onClose={() => setImportOpen(false)}
        onPreviewFile={(file) => api.currentStockImportPreview(file)}
        onApply={async (selected) => {
          const result = await api.currentStockImportApply(selected);
          await load();
          return result;
        }}
      />
    </div>
  );
}
