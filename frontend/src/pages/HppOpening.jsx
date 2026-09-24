import { useRef, useState } from "react";
import { AlertCircle, Download, Upload, X } from "lucide-react";
import { Link } from "react-router-dom";
import api from "../lib/api";
import { errDetail } from "../lib/purchaseUi";
import { fmtQty } from "../components/qtyUi";
import "./purchasePages.css";

function statusClass(status) {
  const s = String(status || "").toUpperCase();
  if (s === "OK") return "pill xs pill-ok";
  if (s === "COST_BASIS_ONLY") return "pill xs pill-muted";
  if (
    s === "MISSING_COST"
    || s === "INVALID_COST"
    || s === "SKU_NOT_FOUND"
    || s === "DUPLICATE"
    || s === "ALREADY_INITIALIZED"
  ) {
    return "pill xs";
  }
  return "pill xs pill-cancel";
}

function fmtMoney(n) {
  if (n == null || n === "") return "—";
  const v = Number(n);
  if (Number.isNaN(v)) return "—";
  return v.toLocaleString("id-ID", { maximumFractionDigits: 2 });
}

export default function HppOpening() {
  const fileRef = useRef(null);
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [preview, setPreview] = useState(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const downloadTemplate = async () => {
    setExporting(true);
    setErr("");
    try {
      const blob = await api.hppOpeningExportTemplate();
      const url = URL.createObjectURL(new Blob([blob], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `hpp-awal-template-${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (ex) {
      setErr(errDetail(ex) || "Gagal unduh template HPP Awal");
    } finally {
      setExporting(false);
    }
  };

  const onPickFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setImporting(true);
    setErr("");
    setMsg("");
    try {
      const data = await api.hppOpeningImportPreview(file);
      setPreview(data);
    } catch (ex) {
      setPreview(null);
      setErr(errDetail(ex) || "Gagal preview import HPP Awal");
    } finally {
      setImporting(false);
    }
  };

  const cancelPreview = () => {
    setPreview(null);
    setMsg("");
  };

  const processImport = async () => {
    if (!preview || !preview.can_process) return;
    setProcessing(true);
    setErr("");
    setMsg("");
    try {
      const result = await api.hppOpeningImportConfirm({
        rows: preview.rows || [],
        filename: preview.filename || "upload.xlsx",
        note: "Import Excel HPP Awal",
        batch_request_id: preview.batch_request_id,
      });
      const ok = result.success_count ?? 0;
      const fail = result.failure_count ?? 0;
      setMsg(
        result.message
          || `HPP Awal selesai: ${ok} sukses, ${fail} gagal. Stock Fisik tidak berubah.`,
      );
      setPreview(null);
    } catch (ex) {
      setErr(errDetail(ex) || "Gagal konfirmasi HPP Awal");
    } finally {
      setProcessing(false);
    }
  };

  const previewRows = preview?.rows || [];

  return (
    <div className="prod-page purchase-page" data-testid="hpp-opening-page">
      <div className="page-heading prod-heading">
        <div className="prod-heading-copy">
          <p className="eyebrow">Persediaan</p>
          <h1>HPP Awal</h1>
          <p className="subhead">
            Inisialisasi harga pokok sekali untuk stock existing.
            Template: KODE | NAMA BARANG | SATUAN | STOCK FISIK | HPP AWAL.
            Isi hanya kolom HPP AWAL (Rupiah per satuan dasar). Matching berdasarkan KODE/SKU.
            Import tidak mengubah Stock Fisik atau stock movements.
          </p>
        </div>
        <div className="stock-heading-actions" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button
            type="button"
            className="ghost"
            onClick={downloadTemplate}
            disabled={exporting}
            data-testid="hpp-opening-download-template"
          >
            <Download size={14} /> {exporting ? "Mengunduh…" : "Download Template"}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            style={{ display: "none" }}
            onChange={onPickFile}
            data-testid="hpp-opening-import-input"
          />
          <button
            type="button"
            className="primary"
            disabled={importing || processing}
            onClick={() => fileRef.current?.click()}
            data-testid="hpp-opening-import-button"
          >
            <Upload size={14} /> {importing ? "Memuat…" : "Import HPP Awal"}
          </button>
          <Link className="ghost" to="/persediaan/stock-saat-ini" data-testid="hpp-opening-back">
            ← Stock Saat Ini
          </Link>
        </div>
      </div>

      {err && (
        <div className="purchase-error-panel" role="alert" data-testid="hpp-opening-error">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={16} color="#a53f2b" />
            <span>{err}</span>
          </div>
        </div>
      )}
      {msg && <p className="purchase-inline-ok" data-testid="hpp-opening-success">{msg}</p>}

      {preview ? (
        <div className="card purchase-detail-card" data-testid="hpp-opening-import-preview">
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
              <span className="label">Siap posting</span>
              <span className="value">{(preview.ok_count ?? 0) + (preview.cost_basis_only_count ?? 0)}</span>
            </div>
            <div>
              <span className="label">Diblokir</span>
              <span className="value">{preview.block_count ?? 0}</span>
            </div>
          </div>
          <p className="muted small">
            Preview tidak mengubah data. Setelah konfirmasi: method RATA-RATA, pengakuan goods_receipt.
            Item invalid tidak diposting.
          </p>
          <div className="table-wrap">
            <table className="prod-table purchase-table" data-testid="hpp-opening-preview-table">
              <thead>
                <tr>
                  <th>Kode</th>
                  <th>Nama</th>
                  <th>Stock Fisik</th>
                  <th>HPP Awal</th>
                  <th>Nilai Awal</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {previewRows.map((r) => (
                  <tr key={`${r.row_number}-${r.item_code}`}>
                    <td className="mono">{r.item_code || "—"}</td>
                    <td>{r.item_name || "—"}</td>
                    <td className="num">{fmtQty(r.physical_qty)}</td>
                    <td className="num">{fmtMoney(r.hpp_awal)}</td>
                    <td className="num">{fmtMoney(r.nilai_awal)}</td>
                    <td>
                      <span className={statusClass(r.status)}>{r.status}</span>
                      {r.message ? <span className="muted small" style={{ marginLeft: 6 }}>{r.message}</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="delete-modal-actions" style={{ marginTop: 12 }}>
            <button type="button" className="ghost" onClick={cancelPreview} disabled={processing} data-testid="hpp-opening-import-cancel">
              <X size={14} /> Batal
            </button>
            <button
              type="button"
              className="primary"
              disabled={processing || !preview.can_process}
              onClick={processImport}
              data-testid="hpp-opening-import-confirm"
            >
              {processing ? "Memproses…" : "Konfirmasi Import HPP Awal"}
            </button>
          </div>
        </div>
      ) : (
        <div className="card purchase-detail-card" data-testid="hpp-opening-empty">
          <p className="muted">
            Unduh template, isi HPP AWAL, lalu import untuk melihat preview sebelum konfirmasi.
          </p>
        </div>
      )}
    </div>
  );
}
