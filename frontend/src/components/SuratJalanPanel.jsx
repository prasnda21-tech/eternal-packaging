import { useRef, useState } from "react";
import { FileText, Upload } from "lucide-react";
import { formatDate, formatDisplayDateTime } from "../lib/constants";
import { useAuth } from "../lib/auth";
import { storedShipments } from "../lib/shipmentLines";
import { QtyOpsHead, statusClassFor } from "./qtyUi";
import ConfirmActionModal from "./ConfirmActionModal";
import api from "../lib/api";

const ALLOWED_MIME = ["image/jpeg", "image/jpg", "image/png", "application/pdf"];
const MAX_BYTES = 4 * 1024 * 1024;

function sjFiles(order) {
  const fromField = Array.isArray(order?.surat_jalan_files) ? order.surat_jalan_files : [];
  if (fromField.length) return fromField;
  // Legacy: shipment rows without file still readable as history labels.
  return storedShipments(order).map((sh, i) => ({
    id: sh.id || sh.accurate_shipment_id || `sj-legacy-${i}`,
    name: sh.file_name || sh.number || sh.accurate_shipment_id || "Surat Jalan",
    uploaded_by_name: sh.created_by_name || sh.created_by || "",
    uploaded_at: sh.created_at,
    legacy: true,
  }));
}

export default function SuratJalanPanel({
  order,
  canManage,
  busy: busyProp,
  onUpdated,
  footerActions = null,
}) {
  const { user } = useAuth();
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState(null);
  const uploading = busyProp || busy;
  const created = !!order.surat_jalan_created;
  const packingWait = (order.current_stage || 0) < 12 && !created;
  const files = sjFiles(order);

  const pickFile = () => {
    setErr("");
    inputRef.current?.click();
  };

  const onFileChosen = (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!ALLOWED_MIME.includes(file.type)) {
      setErr("Format file tidak didukung. Gunakan JPG, PNG, atau PDF.");
      return;
    }
    if (file.size > MAX_BYTES) {
      setErr("Ukuran file terlalu besar (maks 4MB).");
      return;
    }
    setErr("");
    setConfirm({
      file,
      time: formatDisplayDateTime(new Date().toISOString()),
    });
  };

  const executeUpload = async () => {
    if (!confirm?.file || uploading) return;
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
      });
      onUpdated?.(result?.order || result);
      setConfirm(null);
    } catch (e) {
      const d = e?.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Upload Surat Jalan gagal. Silakan coba kembali."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel packing-panel" data-testid="surat-jalan-panel">
      <QtyOpsHead icon={FileText} title="Surat Jalan">
        <span
          className={`qty-status ${statusClassFor(created ? "SAVED" : packingWait ? "IDLE" : "READY")}`}
          data-testid="surat-jalan-semantic-badge"
        >
          {packingWait ? "Menunggu Packing" : created ? "File tersimpan" : "Menunggu upload"}
        </span>
      </QtyOpsHead>

      {packingWait ? (
        <p className="muted small" data-testid="surat-jalan-wait-note" style={{ padding: "8px 16px 0" }}>
          Surat Jalan dapat di-upload setelah Qty Packing disimpan dan packing sebagian/lengkap dikonfirmasi.
        </p>
      ) : (
        <div className="qty-ops-body" style={{ display: "grid", gap: 10 }}>
          <input
            ref={inputRef}
            type="file"
            accept="image/jpeg,image/png,application/pdf,.jpg,.jpeg,.png,.pdf"
            hidden
            onChange={onFileChosen}
            data-testid="surat-jalan-file-input"
          />

          {!canManage ? (
            <p className="muted small" data-testid="surat-jalan-readonly-note">
              Anda tidak memiliki permission untuk mengupload Surat Jalan.
            </p>
          ) : (
            <button
              type="button"
              className="primary"
              onClick={pickFile}
              disabled={uploading}
              data-testid="surat-jalan-upload-button"
            >
              <Upload size={16} /> Upload File Surat Jalan
            </button>
          )}

          {err && <div className="error" data-testid="surat-jalan-upload-error">{err}</div>}

          {files.length > 0 && (
            <ul className="sj-file-list" data-testid="surat-jalan-file-list" style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 6 }}>
              {files.map((f) => (
                <li key={f.id || f.name} data-testid={`surat-jalan-file-${f.id || f.name}`} style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "baseline" }}>
                  <strong>{f.name}</strong>
                  {(f.uploaded_by_name || f.uploaded_by) && (
                    <span className="muted small">{f.uploaded_by_name || f.uploaded_by}</span>
                  )}
                  {f.uploaded_at && <span className="muted small mono">{formatDate(f.uploaded_at)}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

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
        onConfirm={executeUpload}
      />
    </section>
  );
}
