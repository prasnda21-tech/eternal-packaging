import { useEffect, useRef, useState } from "react";
import { FileText, Upload } from "lucide-react";
import { formatDate, formatDisplayDateTime } from "../lib/constants";
import { useAuth } from "../lib/auth";
import { QtyOpsHead, statusClassFor } from "./qtyUi";
import ConfirmActionModal from "./ConfirmActionModal";
import SuratJalanPreviewModal from "./SuratJalanPreviewModal";
import api from "../lib/api";
import {
  SJ_VIEW_FILE_PERM,
  SJ_DELETE_FILE_PERM,
  defaultSjOperationalDate,
  downloadSuratJalanFile,
  parseOperationalDate,
  sjHistory,
} from "../lib/suratJalan";

const ALLOWED_MIME = ["image/jpeg", "image/jpg", "image/png", "application/pdf"];
const MAX_BYTES = 4 * 1024 * 1024;

export default function SuratJalanPanel({
  order,
  canManage,
  canViewFile: canViewFileProp,
  canDeleteFile: canDeleteFileProp,
  busy: busyProp,
  onUpdated,
  footerActions = null,
  allowUpload = true,
}) {
  const { user, hasPerm } = useAuth();
  const canViewFile = canViewFileProp ?? hasPerm(SJ_VIEW_FILE_PERM);
  const canDeleteFile = canDeleteFileProp ?? hasPerm(SJ_DELETE_FILE_PERM);
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [file, setFile] = useState(null);
  const [deliveryDate, setDeliveryDate] = useState(() => defaultSjOperationalDate(order));
  const [confirm, setConfirm] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [preview, setPreview] = useState(null);
  const uploading = busyProp || busy;
  const files = sjHistory(order);
  // FILE TERSIMPAN = live/active SJ file only (same evidence Loading uses).
  // History still lists superseded/cancelled SJ for audit.
  const created = files.some((f) => f && f.active !== false && String(f.status || "").toLowerCase() !== "superseded" && String(f.status || "").toLowerCase() !== "cancelled");
  const sjOrderId = order?.id;
  const sjStoredDate = order?.delivery_note_created_at;

  useEffect(() => {
    setDeliveryDate(defaultSjOperationalDate({ delivery_note_created_at: sjStoredDate }));
  }, [sjOrderId, sjStoredDate]);

  const pickFile = () => {
    setErr("");
    inputRef.current?.click();
  };

  const onFileChosen = (e) => {
    const next = e.target.files?.[0];
    e.target.value = "";
    if (!next) return;
    if (!ALLOWED_MIME.includes(next.type) && !/\.(pdf|jpe?g|png)$/i.test(next.name || "")) {
      setErr("Format file tidak didukung. Gunakan JPG, PNG, atau PDF.");
      return;
    }
    if (next.size > MAX_BYTES) {
      setErr("Ukuran file terlalu besar (maks 4MB).");
      return;
    }
    setErr("");
    setFile(next);
  };

  const openConfirm = () => {
    if (!file || uploading) return;
    const date = parseOperationalDate(deliveryDate);
    if (deliveryDate && !date) {
      setErr("Tanggal pengiriman tidak valid.");
      return;
    }
    setErr("");
    setConfirm({ file, time: formatDisplayDateTime(new Date().toISOString()), deliveryDate: date || "" });
  };

  const executeUpload = async () => {
    if (!confirm?.file || uploading) return;
    const date = parseOperationalDate(confirm.deliveryDate || deliveryDate);
    if ((confirm.deliveryDate || deliveryDate) && !date) {
      setErr("Tanggal pengiriman tidak valid.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(r.result);
        r.onerror = () => reject(new Error("Gagal membaca file"));
        r.readAsDataURL(confirm.file);
      });
      const result = await api.addAttachment(order.id, {
        kind: "surat_jalan",
        name: confirm.file.name,
        data_url: dataUrl,
        ...(date ? { delivery_date: date } : {}),
      });
      onUpdated?.(result?.order || result);
      setConfirm(null);
      setFile(null);
    } catch (e) {
      const d = e?.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Upload Surat Jalan gagal. Silakan coba kembali."));
    } finally {
      setBusy(false);
    }
  };

  const executeDelete = async () => {
    const fileId = deleteTarget?.file_id || deleteTarget?.id;
    if (!fileId || uploading) return;
    setBusy(true);
    setErr("");
    try {
      const result = await api.deleteSuratJalanFile(order.id, fileId);
      onUpdated?.(result?.order || result, "delete");
      setDeleteTarget(null);
    } catch (e) {
      const status = e?.response?.status;
      const d = e?.response?.data?.detail;
      if (status === 403) {
        setErr("Anda tidak punya permission untuk menghapus file Surat Jalan.");
      } else if (status === 404) {
        setErr("File Surat Jalan tidak ditemukan.");
      } else {
        setErr(typeof d === "string" ? d : (d?.message || "Gagal menghapus file Surat Jalan."));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel packing-panel" data-testid="surat-jalan-panel">
      <QtyOpsHead icon={FileText} title="Surat Jalan">
        <span
          className={`qty-status ${statusClassFor(created ? "SAVED" : "READY")}`}
          data-testid="surat-jalan-semantic-badge"
        >
          {created ? "File tersimpan" : "Menunggu upload"}
        </span>
      </QtyOpsHead>

      <div className="qty-ops-body" style={{ display: "grid", gap: 10 }}>
        {allowUpload && (
          <input
            ref={inputRef}
            type="file"
            accept="image/jpeg,image/png,application/pdf,.jpg,.jpeg,.png,.pdf"
            hidden
            onChange={onFileChosen}
            data-testid="surat-jalan-file-input"
          />
        )}

        {!canManage ? (
          <p className="muted small" data-testid="surat-jalan-readonly-note">
            Anda tidak memiliki permission untuk mengupload Surat Jalan.
          </p>
        ) : (
          <>
            {allowUpload && <p className="muted small" data-testid="sj-file-only-note">File Surat Jalan</p>}
            <label className="ship-filter-field" data-testid="surat-jalan-date-field">
              <span>Tanggal pengiriman</span>
              <input
                type="date"
                value={deliveryDate}
                onChange={(e) => { setDeliveryDate(e.target.value); setErr(""); }}
                disabled={uploading}
                data-testid="surat-jalan-delivery-date"
              />
              <span className="muted small">Kosong = pengiriman hari ini.</span>
            </label>
            {allowUpload && (
              <>
                <div className="sj-upload-file-row">
                  <button
                    type="button"
                    className="primary"
                    onClick={pickFile}
                    disabled={uploading}
                    data-testid="surat-jalan-upload-button"
                  >
                    <Upload size={16} /> {file ? "Ganti File" : "Pilih File"}
                  </button>
                  {file && <span className="muted small" data-testid="surat-jalan-chosen-name">{file.name}</span>}
                </div>
                <button
                  type="button"
                  className="primary"
                  onClick={openConfirm}
                  disabled={uploading || !file}
                  data-testid="surat-jalan-submit-button"
                >
                  Upload Surat Jalan
                </button>
              </>
            )}
          </>
        )}

        {err && <div className="error" data-testid="surat-jalan-upload-error">{err}</div>}

        {files.length > 0 && (
          <ul className="sj-file-list" data-testid="surat-jalan-file-list" style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 10 }}>
            {files.map((f) => {
              const fileId = f.file_id || f.id;
              return (
                <li key={fileId || f.name} data-testid={`surat-jalan-file-${fileId || f.name}`} className="sj-file-card">
                  <strong>{f.sj_number || f.name}</strong>
                  {f.source === "SYSTEM_GENERATED" && (
                    <span className="muted small" data-testid={`sj-generated-badge-${fileId}`}>Dibuat Sistem</span>
                  )}
                  {(f.active === false || ["superseded", "cancelled", "deleted"].includes(String(f.status || "").toLowerCase())) && (
                    <span className="muted small" data-testid={`sj-history-status-${fileId}`}>
                      {String(f.status || "superseded").toLowerCase() === "cancelled" ? "Dibatalkan (arsip)" : "Superseded (arsip)"}
                    </span>
                  )}
                  {(f.uploaded_by_name || f.uploaded_by) && (
                    <span className="muted small">{f.uploaded_by_name || f.uploaded_by}</span>
                  )}
                  {f.uploaded_at && <span className="muted small mono">{formatDate(f.uploaded_at)}</span>}
                  {canViewFile && fileId && (
                    <div className="sj-file-actions">
                      <button type="button" className="ghost sm" onClick={() => setPreview(f)} data-testid={`sj-lihat-${fileId}`}>
                        Lihat Surat Jalan
                      </button>
                      <button
                        type="button"
                        className="ghost sm"
                        onClick={() => downloadSuratJalanFile(order.id, fileId, f.name).catch((e) => setErr(e.message))}
                        data-testid={`sj-download-${fileId}`}
                      >
                        Download
                      </button>
                      {canDeleteFile && (
                        <button
                          type="button"
                          className="ghost sm"
                          onClick={() => { setErr(""); setDeleteTarget(f); }}
                          disabled={uploading}
                          data-testid={`sj-hapus-${fileId}`}
                        >
                          Hapus File Surat Jalan
                        </button>
                      )}
                    </div>
                  )}
                  {!canViewFile && canDeleteFile && fileId && (
                    <div className="sj-file-actions">
                      <button
                        type="button"
                        className="ghost sm"
                        onClick={() => { setErr(""); setDeleteTarget(f); }}
                        disabled={uploading}
                        data-testid={`sj-hapus-${fileId}`}
                      >
                        Hapus File Surat Jalan
                      </button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {footerActions}
      <ConfirmActionModal
        open={!!confirm}
        stage="Surat Jalan"
        poNumber={order.po_number}
        action={confirm ? `Upload file ${confirm.file.name}` : "Upload Surat Jalan"}
        name={user?.name || user?.email}
        time={confirm?.time}
        busy={uploading}
        error={err}
        confirmLabel="Konfirmasi"
        onCancel={() => { if (!uploading) { setConfirm(null); setErr(""); } }}
        extra={confirm?.deliveryDate ? (
          <p className="muted small" data-testid="sj-confirm-date">Tanggal pengiriman: {confirm.deliveryDate}</p>
        ) : null}
        onConfirm={executeUpload}
      />
      <ConfirmActionModal
        open={!!deleteTarget}
        stage="Surat Jalan"
        poNumber={order.po_number}
        action="Hapus file Surat Jalan ini?"
        name={user?.name || user?.email}
        time={deleteTarget ? formatDisplayDateTime(new Date().toISOString()) : undefined}
        busy={uploading}
        error={err}
        confirmLabel="Hapus File"
        onCancel={() => { if (!uploading) { setDeleteTarget(null); setErr(""); } }}
        onConfirm={executeDelete}
      />
      <SuratJalanPreviewModal
        open={!!preview}
        order={order}
        entries={preview ? [preview] : files}
        canDownload={canViewFile}
        onClose={() => setPreview(null)}
      />
    </section>
  );
}
