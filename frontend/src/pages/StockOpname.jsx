import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Download, Search, Upload, X } from "lucide-react";
import { Link } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate } from "../lib/constants";
import { errDetail, friendlyLoadError, purchaseRowInteractionProps } from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";
import { FormSection, FormField, computeStatus } from "../components/form-standard";
import "./purchasePages.css";

function toNum(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function statusClass(status) {
  const s = String(status || "").toUpperCase();
  if (s === "OK") return "pill xs pill-ok";
  if (s === "BLOCK") return "pill xs";
  if (s === "UNCHANGED") return "pill xs pill-muted";
  return "pill xs pill-cancel";
}

export default function StockOpname() {
  const { hasPerm } = useAuth();
  const canUpdate = hasPerm("current_stock.update");
  const [items, setItems] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [unprinted, setUnprinted] = useState("");
  const [printed, setPrinted] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
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
      return rows;
    } catch (ex) {
      setErr(friendlyLoadError(ex, "Gagal memuat item stock opname"));
      return null;
    } finally {
      setLoading(false);
    }
  }, [q]);

  useEffect(() => { load(); }, [load]);

  const selected = useMemo(
    () => items.find((it) => it.master_item_id === selectedId) || null,
    [items, selectedId],
  );

  const pick = (it) => {
    if (preview) return; // avoid conflicting with import preview mode
    setSelectedId(it.master_item_id);
    setUnprinted(String(it.unprinted_qty ?? 0));
    setPrinted(String(it.printed_qty ?? 0));
    setNote("");
    setMsg("");
    setErr("");
  };

  const closeDetail = () => {
    setSelectedId(null);
    setUnprinted("");
    setPrinted("");
    setNote("");
  };

  const countedTotal = toNum(unprinted) + toNum(printed);
  const systemPhysical = toNum(selected?.physical_qty);
  const selisih = countedTotal - systemPhysical;

  const save = async () => {
    if (!selected || !canUpdate) return;
    setBusy(true);
    setMsg("");
    setErr("");
    try {
      const result = await api.applyStockOpname({
        master_item_id: selected.master_item_id,
        counted_unprinted_qty: toNum(unprinted),
        counted_printed_qty: toNum(printed),
        note,
      });
      setMsg(result.message || "Opname tersimpan");
      const next = await load();
      if (Array.isArray(next)) {
        const refreshed = next.find((r) => r.master_item_id === selected.master_item_id);
        if (refreshed) {
          setUnprinted(String(refreshed.unprinted_qty ?? 0));
          setPrinted(String(refreshed.printed_qty ?? 0));
        }
      }
    } catch (ex) {
      setErr(errDetail(ex) || "Gagal simpan opname");
    } finally {
      setBusy(false);
    }
  };

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
    if (!file || !canUpdate) return;
    setImporting(true);
    setErr("");
    setMsg("");
    setSelectedId(null);
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
    if (!preview || !canUpdate || !preview.can_process) return;
    setProcessing(true);
    setErr("");
    setMsg("");
    try {
      const result = await api.stockOpnameImportApply({
        rows: preview.rows || [],
        filename: preview.filename || "upload.xlsx",
        note: "Import Excel Stock Opname",
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
            (KODE | NAMA | SATUAN | STOCK SISTEM | STOCK FISIK HASIL OPNAME).
            Isi hanya kolom fisik opname; matching WAJIB by KODE/SKU.
            Available = MAX(Fisik − Outstanding SO, 0). Belum Dicetak = MAX(Outstanding − Sudah Dicetak, 0).
            Cetak tidak memotong Available dua kali.
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
          {canUpdate && (
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
                  <th>Nama Barang</th>
                  <th className="num">Fisik Sistem</th>
                  <th className="num">Fisik Opname</th>
                  <th className="num">Selisih</th>
                  <th className="num">Sudah Dicetak</th>
                  <th className="num">Outstanding SO</th>
                  <th className="num">Available Baru</th>
                  <th className="cell-status">Status</th>
                </tr>
              </thead>
              <tbody>
                {previewRows.length === 0 && (
                  <tr className="purchase-empty-row">
                    <td colSpan={9} className="muted small">Tidak ada baris di file.</td>
                  </tr>
                )}
                {previewRows.map((r, idx) => (
                  <tr key={`${r.item_code}-${r.row_number || idx}`}>
                    <td className="mono cell-clip">{r.item_code || "—"}</td>
                    <td className="cell-clip" title={r.item_name}>{r.item_name || "—"}</td>
                    <td className="num mono">{r.physical_system == null ? "—" : fmtQty(r.physical_system)}</td>
                    <td className="num mono">{r.physical_opname == null ? "—" : fmtQty(r.physical_opname)}</td>
                    <td className="num mono">{r.selisih == null ? "—" : `${r.selisih > 0 ? "+" : ""}${fmtQty(r.selisih)}`}</td>
                    <td className="num mono">{r.printed_qty == null ? "—" : fmtQty(r.printed_qty)}</td>
                    <td className="num mono">{r.outstanding_so_qty == null ? "—" : fmtQty(r.outstanding_so_qty)}</td>
                    <td className="num mono">{r.available_new == null ? "—" : fmtQty(r.available_new)}</td>
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
              disabled={!canUpdate || processing || !preview.can_process}
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
                <col className="col-qty" />
                <col className="col-qty" />
                <col className="col-qty" />
                <col className="col-avail" />
                <col className="col-date" />
              </colgroup>
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Nama Barang</th>
                  <th className="num">Fisik</th>
                  <th className="num">Belum Dicetak</th>
                  <th className="num">Sudah Dicetak</th>
                  <th className="num">Outstanding SO</th>
                  <th className="cell-status th-wrap">Available</th>
                  <th className="nowrap">Terakhir Opname</th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr className="purchase-empty-row">
                    <td colSpan={8} className="muted small">Memuat…</td>
                  </tr>
                )}
                {!loading && items.length === 0 && (
                  <tr className="purchase-empty-row">
                    <td colSpan={8} className="muted small">Tidak ada item.</td>
                  </tr>
                )}
                {!loading && items.map((it) => {
                  const isSelected = selectedId === it.master_item_id;
                  return (
                    <tr
                      key={it.master_item_id || it.accurate_item_id}
                      {...purchaseRowInteractionProps(() => pick(it), {
                        testId: `stock-opname-row-${it.master_item_id}`,
                        selected: isSelected,
                      })}
                    >
                      <td className="mono cell-clip">{it.item_code || "—"}</td>
                      <td className="cell-clip" title={it.item_name}>{it.item_name || "—"}</td>
                      <td className="num mono">{fmtQty(it.physical_qty)}</td>
                      <td className="num mono">{fmtQty(it.unprinted_qty)}</td>
                      <td className="num mono">{fmtQty(it.printed_qty)}</td>
                      <td className="num mono">{fmtQty(it.outstanding_so_qty)}</td>
                      <td className="cell-status mono">{fmtQty(it.stock_current)}</td>
                      <td className="nowrap muted small">{formatDate(it.last_opname_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {selected && (
            <div className="card purchase-detail-card" data-testid="stock-opname-detail" style={{ marginTop: 12 }}>
              <div className="purchase-detail-meta">
                <div>
                  <span className="label">Nama Barang</span>
                  <span className="value" data-testid="opname-detail-name">{selected.item_name || "—"}</span>
                </div>
                <div>
                  <span className="label">Kode</span>
                  <span className="value mono" data-testid="opname-detail-code">{selected.item_code || "—"}</span>
                </div>
                <div>
                  <span className="label">Stock Fisik Sistem</span>
                  <span className="value mono" data-testid="opname-detail-physical">{fmtQty(selected.physical_qty)}</span>
                </div>
                <div>
                  <span className="label">Belum Dicetak</span>
                  <span className="value mono">{fmtQty(selected.unprinted_qty)}</span>
                </div>
                <div>
                  <span className="label">Sudah Dicetak</span>
                  <span className="value mono">{fmtQty(selected.printed_qty)}</span>
                </div>
                <div>
                  <span className="label">Outstanding SO</span>
                  <span className="value mono">{fmtQty(selected.outstanding_so_qty)}</span>
                </div>
                <div>
                  <span className="label">Available</span>
                  <span className="value mono" data-testid="opname-detail-available">{fmtQty(selected.stock_current)}</span>
                </div>
                <div>
                  <span className="label">Terakhir Opname</span>
                  <span className="value">{formatDate(selected.last_opname_at)}</span>
                </div>
              </div>

              <FormSection
                id="opname-input"
                title="Input Hasil Opname"
                status={computeStatus([
                  { value: unprinted, isRequired: true },
                  { value: printed, isRequired: true },
                ])}
              >
                <div className="purchase-form-grid">
                  <FormField id="opname-unprinted" label="Belum Dicetak hasil fisik" isRequired value={unprinted}>
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={unprinted}
                      onChange={(e) => setUnprinted(e.target.value)}
                      data-testid="stock-opname-unprinted"
                      disabled={!canUpdate || busy}
                    />
                  </FormField>
                  <FormField id="opname-printed" label="Sudah Dicetak hasil fisik" isRequired value={printed}>
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={printed}
                      onChange={(e) => setPrinted(e.target.value)}
                      data-testid="stock-opname-printed"
                      disabled={!canUpdate || busy}
                    />
                  </FormField>
                  <FormField id="opname-total" label="Total fisik hasil opname" value={String(countedTotal)}>
                    <input type="text" readOnly value={fmtQty(countedTotal)} data-testid="stock-opname-total" className="mono" />
                  </FormField>
                  <FormField id="opname-diff" label="Selisih vs stock fisik sistem" value={String(selisih)}>
                    <input
                      type="text"
                      readOnly
                      value={`${selisih > 0 ? "+" : ""}${fmtQty(selisih)}`}
                      data-testid="stock-opname-diff"
                      className="mono"
                    />
                  </FormField>
                  <FormField id="opname-note" label="Catatan" value={note}>
                    <input
                      type="text"
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      data-testid="stock-opname-note"
                      disabled={!canUpdate || busy}
                      placeholder="Opsional"
                    />
                  </FormField>
                </div>
              </FormSection>

              <div className="purchase-form-actions">
                <button type="button" className="ghost" onClick={closeDetail} disabled={busy}>
                  Tutup
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={!canUpdate || busy}
                  onClick={save}
                  data-testid="stock-opname-save"
                >
                  {busy ? "Menyimpan…" : "Simpan Opname"}
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
