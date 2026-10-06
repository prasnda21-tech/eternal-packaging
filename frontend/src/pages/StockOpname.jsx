import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Download, Search, Upload, X } from "lucide-react";
import { Link } from "react-router-dom";
import api from "../lib/api";
import { formatDate } from "../lib/constants";
import { errDetail, friendlyLoadError } from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";
import "./purchasePages.css";

function statusClass(status) {
  const s = String(status || "").toUpperCase();
  if (s === "OK") return "pill xs pill-ok";
  if (s === "BLOCK") return "pill xs";
  if (s === "UNCHANGED") return "pill xs pill-muted";
  return "pill xs pill-cancel";
}

export default function StockOpname() {
  const [items, setItems] = useState([]);
  const [canApply, setCanApply] = useState(false);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const fileRef = useRef(null);
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [preview, setPreview] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const d = await api.stockOpnameItems({ q });
      const rows = d.items || [];
      setItems(rows);
      setCanApply(Boolean(d.can_apply_stock_opname));
      return rows;
    } catch (ex) {
      setErr(friendlyLoadError(ex, "Gagal memuat item stock opname"));
      return null;
    } finally {
      setLoading(false);
    }
  }, [q]);

  useEffect(() => { load(); }, [load]);

  const downloadTemplate = async () => {
    setExporting(true);
    setErr("");
    try {
      const blob = await api.stockOpnameExportTemplate();
      const url = URL.createObjectURL(new Blob([blob], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `stock-opname-template-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (ex) {
      setErr(errDetail(ex) || "Gagal export template");
    } finally {
      setExporting(false);
    }
  };

  const onPickFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !canApply) return;
    setImporting(true);
    setErr("");
    setMsg("");
    try {
      const data = await api.stockOpnameImportPreview(file);
      setPreview(data);
    } catch (ex) {
      setPreview(null);
      setErr(errDetail(ex) || "Gagal preview import opname");
    } finally {
      setImporting(false);
    }
  };

  const cancelPreview = () => {
    setPreview(null);
    setMsg("");
  };

  const processImport = async () => {
    if (!preview || !canApply || !preview.can_process) return;
    setProcessing(true);
    setErr("");
    setMsg("");
    try {
      const result = await api.stockOpnameImportApply({
        rows: preview.rows || [],
        filename: preview.filename || "upload.xlsx",
        note: "Import Excel Stock Opname",
        batch_request_id: preview.batch_request_id,
      });
      const ok = result.success_count ?? 0;
      const fail = result.failure_count ?? result.failed_count ?? 0;
      setMsg(
        result.message
          || `Import opname selesai: ${ok} sukses, ${fail} gagal. Stock Saat Ini ikut ter-refresh.`,
      );
      setPreview(null);
      await load();
    } catch (ex) {
      setErr(errDetail(ex) || "Gagal proses import opname");
    } finally {
      setProcessing(false);
    }
  };

  const previewRows = preview?.rows || [];

  return (
    <div className="prod-page purchase-page" data-testid="stock-opname-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1>Stock Opname</h1>
          <p className="subhead">
            Export template berisi seluruh item aktif Master Barang/Jasa
            (KODE | NAMA BARANG | STOCK SAAT INI SISTEM | STOCK SAAT INI HASIL OPNAME).
            Isi hanya kolom STOCK SAAT INI HASIL OPNAME; matching WAJIB berdasarkan KODE/SKU.
            Opname hanya mengoreksi Stock Saat Ini dan tidak mengubah bucket transaksi.
          </p>
        </div>
        <div className="stock-heading-actions" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            className="ghost"
            onClick={downloadTemplate}
            disabled={exporting}
            data-testid="stock-opname-export-template"
          >
            <Download size={14} /> {exporting ? "Mengunduh…" : "Export Template"}
          </button>
          {canApply && (
            <>
              <input
                ref={fileRef}
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                style={{ display: "none" }}
                onChange={onPickFile}
                data-testid="stock-opname-import-input"
              />
              <button
                type="button"
                className="primary"
                disabled={importing || processing}
                onClick={() => fileRef.current?.click()}
                data-testid="stock-opname-import-button"
              >
                <Upload size={14} /> {importing ? "Memuat…" : "Import Opname"}
              </button>
            </>
          )}
          <Link className="ghost" to="/persediaan/stock-saat-ini" data-testid="stock-opname-back">
            ← Stock Saat Ini
          </Link>
        </div>
      </div>

      {err && (
        <div className="purchase-error-panel" role="alert" data-testid="stock-opname-error">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={16} color="#a53f2b" />
            <span>{err}</span>
          </div>
          <button type="button" className="ghost sm" onClick={load}>Coba Lagi</button>
        </div>
      )}
      {msg && <p className="purchase-inline-ok" data-testid="stock-opname-success">{msg}</p>}

      {preview ? (
        <div className="card purchase-detail-card" data-testid="stock-opname-import-preview">
          <div className="purchase-detail-meta">
            <div>
              <span className="label">File</span>
              <span className="value mono">{preview.filename || "—"}</span>
            </div>
            <div>
              <span className="label">Total baris</span>
              <span className="value">{preview.total_rows ?? previewRows.length}</span>
            </div>
            <div>
              <span className="label">Siap proses</span>
              <span className="value">{preview.ok_count ?? 0}</span>
            </div>
            <div>
              <span className="label">Error / Block</span>
              <span className="value">{(preview.error_count || 0) + (preview.block_count || 0)}</span>
            </div>
          </div>

          <div className="purchase-table-wrap">
            <table className="prod-table purchase-table" data-testid="stock-opname-preview-table">
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Nama</th>
                  <th className="num">Stock Saat Ini Sistem</th>
                  <th className="num">Stock Saat Ini Hasil Opname</th>
                  <th className="num">Selisih</th>
                  <th className="cell-status">Aksi</th>
                  <th className="cell-status">Status</th>
                </tr>
              </thead>
              <tbody>
                {previewRows.length === 0 && (
                  <tr className="purchase-empty-row">
                    <td colSpan={7} className="muted small">Tidak ada baris di file.</td>
                  </tr>
                )}
                {previewRows.map((r, idx) => (
                  <tr key={`${r.item_code}-${r.row_number || idx}`}>
                    <td className="mono cell-clip">{r.item_code || "—"}</td>
                    <td className="cell-clip" title={r.item_name}>{r.item_name || "—"}</td>
                    <td className="num mono">{r.stock_current_system == null ? "—" : fmtQty(r.stock_current_system)}</td>
                    <td className="num mono">{r.stock_current_opname == null ? "—" : fmtQty(r.stock_current_opname)}</td>
                    <td className="num mono">{r.selisih == null ? "—" : `${r.selisih > 0 ? "+" : ""}${fmtQty(r.selisih)}`}</td>
                    <td className="cell-status"><span className="pill xs pill-muted">{r.opname_action || "—"}</span></td>
                    <td className="cell-status">
                      <span className={statusClass(r.status)} title={r.message || ""}>
                        {r.status || "—"}
                      </span>
                      {r.message ? <div className="muted small">{r.message}</div> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="purchase-form-actions">
            <button type="button" className="ghost" onClick={cancelPreview} disabled={processing} data-testid="stock-opname-import-cancel">
              Batal
            </button>
            <button
              type="button"
              className="primary"
              disabled={!canApply || processing || !preview.can_process}
              onClick={processImport}
              data-testid="stock-opname-import-process"
            >
              {processing ? "Memproses…" : "Proses Stock Opname"}
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="purchase-search-bar is-toolbar">
            <form className="search-inline" onSubmit={(e) => { e.preventDefault(); load(); }}>
              <Search size={14} />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Cari kode / nama barang…"
                data-testid="stock-opname-search"
                aria-label="Cari item stock opname"
              />
              {q && (
                <button type="button" className="ghost sm" onClick={() => setQ("")} aria-label="Hapus pencarian">
                  <X size={12} />
                </button>
              )}
            </form>
          </div>

          <div className="purchase-table-wrap">
            <table className="prod-table purchase-table purchase-table-opname" data-testid="stock-opname-table">
              <colgroup>
                <col className="col-code" />
                <col className="col-name" />
                <col className="col-qty" />
                <col className="col-avail" />
                <col className="col-date" />
              </colgroup>
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Nama Barang</th>
                  <th>Satuan</th>
                  <th className="cell-status th-wrap">Stock Saat Ini Sistem</th>
                  <th className="nowrap">Terakhir Opname</th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr className="purchase-empty-row">
                    <td colSpan={5} className="muted small">Memuat…</td>
                  </tr>
                )}
                {!loading && items.length === 0 && (
                  <tr className="purchase-empty-row">
                    <td colSpan={5} className="muted small">Tidak ada item.</td>
                  </tr>
                )}
                {!loading && items.map((it) => (
                    <tr key={it.master_item_id || it.accurate_item_id} data-testid={`stock-opname-row-${it.master_item_id}`}>
                      <td className="mono cell-clip">{it.item_code || "—"}</td>
                      <td className="cell-clip" title={it.item_name}>{it.item_name || "—"}</td>
                      <td>{it.unit || "—"}</td>
                      <td className="cell-status mono">{fmtQty(it.stock_current)}</td>
                      <td className="nowrap muted small">{formatDate(it.last_opname_at)}</td>
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
