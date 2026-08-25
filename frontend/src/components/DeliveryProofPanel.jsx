import { useEffect, useRef, useState } from "react";
import { Camera, Trash2, Loader2, CheckCircle2, XCircle, ImagePlus } from "lucide-react";
import api, { fileUrl } from "../lib/api";
import { compressImage } from "../lib/imageCompress";
import { formatDate, formatDisplayDateTime, ROLES } from "../lib/constants";
import { useAuth } from "../lib/auth";
import { confirmableProofCount, proofUploadUserError } from "../lib/deliveryProof";
import ConfirmActionModal from "./ConfirmActionModal";

const MAX_PHOTOS = 5;

// Multi-foto Bukti Kirim: driver can select/capture multiple photos, review the
// staged list (with per-photo note + remove/replace), then confirm upload.
// Files are compressed client-side, then streamed one-by-one to the backend
// which stores each via the storage service (local filesystem in preview, S3/Emergent in production).
export default function DeliveryProofPanel({ order, onUpdated }) {
  const { hasPerm, user } = useAuth();
  const fileInput = useRef(null);
  const cameraInput = useRef(null);

  const [staged, setStaged] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState("");
  const [proofBlobs, setProofBlobs] = useState({});
  const [confirmUpload, setConfirmUpload] = useState(null);

  const canUpload = hasPerm("delivery.update");
  const canDelete = hasPerm("delivery.update");
  const proofs = order.delivery_proofs || [];
  const remainingSlots = Math.max(0, MAX_PHOTOS - proofs.length);
  const stagedNotYetUploaded = staged.filter((s) => s.status !== "done");
  const freeSlotsForNewSelection = Math.max(0, remainingSlots - stagedNotYetUploaded.length);
  const readyCount = confirmableProofCount(staged);
  const doneCount = staged.filter((s) => s.status === "done").length;

  // Load proof images as blob URLs (cookie auth on same origin)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const next = { ...proofBlobs };
      for (const p of proofs) {
        if (next[p.id]) continue;
        try {
          const resp = await fetch(fileUrl(p.storage_path), { credentials: "include" });
          if (!resp.ok) continue;
          const blob = await resp.blob();
          if (cancelled) return;
          next[p.id] = URL.createObjectURL(blob);
        } catch { /* ignore */ }
      }
      if (!cancelled) setProofBlobs(next);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proofs.length]);

  // Cleanup blob URLs
  useEffect(() => () => {
    Object.values(proofBlobs).forEach((u) => URL.revokeObjectURL(u));
    staged.forEach((s) => s.previewUrl && URL.revokeObjectURL(s.previewUrl));
  }, []);  // eslint-disable-line

  const pickGallery = () => fileInput.current?.click();
  const pickCamera = () => cameraInput.current?.click();

  const stageFiles = async (fileList) => {
    setErr("");
    const files = Array.from(fileList || []);
    if (files.length === 0) return;
    if (freeSlotsForNewSelection <= 0) {
      setErr(`Sudah mencapai batas ${MAX_PHOTOS} foto.`);
      return;
    }
    // Limit selection to free slots
    const toStage = files.slice(0, freeSlotsForNewSelection);
    if (files.length > toStage.length) {
      setErr(`Hanya ${toStage.length} foto ditambahkan (batas ${MAX_PHOTOS} foto per PO).`);
    }

    // Compress each and add to staged (streaming so UI updates progressively)
    for (const raw of toStage) {
      const clientId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
      if (!raw.type?.startsWith("image/")) {
        setStaged((prev) => [...prev, { id: clientId, file: raw, previewUrl: null, note: "", status: "error", error: "Bukan file gambar", progress: 0 }]);
        continue;
      }
      if (raw.size > 15 * 1024 * 1024) {
        setStaged((prev) => [...prev, { id: clientId, file: raw, previewUrl: null, note: "", status: "error", error: "File > 15MB — foto ulang", progress: 0 }]);
        continue;
      }
      // Add placeholder while compressing
      setStaged((prev) => [...prev, { id: clientId, file: raw, previewUrl: null, note: "", status: "compressing", progress: 0 }]);
      try {
        const { file: compressed, compressed: didCompress, sizeKB, originalSizeKB } = await compressImage(raw);
        const previewUrl = URL.createObjectURL(compressed);
        setStaged((prev) => prev.map((s) => s.id === clientId
          ? { ...s, file: compressed, previewUrl, status: "ready", sizeKB, originalSizeKB, compressed: didCompress }
          : s));
      } catch (ex) {
        setStaged((prev) => prev.map((s) => s.id === clientId
          ? { ...s, status: "error", error: "Gagal mengompres foto" }
          : s));
      }
    }
    if (fileInput.current) fileInput.current.value = "";
    if (cameraInput.current) cameraInput.current.value = "";
  };

  const removeStaged = (clientId) => {
    setStaged((prev) => {
      const found = prev.find((s) => s.id === clientId);
      if (found?.previewUrl) URL.revokeObjectURL(found.previewUrl);
      return prev.filter((s) => s.id !== clientId);
    });
  };

  const updateStagedNote = (clientId, note) => {
    setStaged((prev) => prev.map((s) => s.id === clientId ? { ...s, note } : s));
  };

  const requestUpload = () => {
    const toUpload = staged.filter((s) => s.status === "ready" || (s.status === "error" && s.file));
    if (toUpload.length === 0) {
      setErr("Belum ada foto siap upload.");
      return;
    }
    setErr("");
    setConfirmUpload({
      count: toUpload.length,
      time: formatDisplayDateTime(new Date().toISOString()),
    });
  };

  const uploadAll = async () => {
    const toUpload = staged.filter((s) => s.status === "ready" || (s.status === "error" && s.file));
    if (toUpload.length === 0) {
      setErr("Belum ada foto siap upload.");
      return;
    }
    setUploading(true);
    setErr("");
    let latestOrder = order;
    let failed = false;
    for (const s of toUpload) {
      setStaged((prev) => prev.map((x) => x.id === s.id ? { ...x, status: "uploading", progress: 0, error: "" } : x));
      try {
        const res = await api.uploadDeliveryProof(latestOrder.id, s.file, s.note || "", (pct) => {
          setStaged((prev) => prev.map((x) => x.id === s.id ? { ...x, progress: pct } : x));
        });
        latestOrder = res.order;
        onUpdated(res.order);
        setStaged((prev) => prev.map((x) => x.id === s.id ? { ...x, status: "done", progress: 100 } : x));
      } catch (ex) {
        const msg = proofUploadUserError(ex);
        setStaged((prev) => prev.map((x) => x.id === s.id ? { ...x, status: "error", error: msg } : x));
        setErr(msg);
        failed = true;
      }
    }
    setUploading(false);
    if (!failed) setConfirmUpload(null);
    // Cleanup successfully uploaded staged items after a short delay
    setTimeout(() => {
      setStaged((prev) => {
        const remaining = prev.filter((s) => s.status !== "done");
        prev.filter((s) => s.status === "done").forEach((s) => s.previewUrl && URL.revokeObjectURL(s.previewUrl));
        return remaining;
      });
    }, 1500);
  };

  const cancelAll = () => {
    if (uploading) return;
    staged.forEach((s) => s.previewUrl && URL.revokeObjectURL(s.previewUrl));
    setStaged([]);
    setErr("");
  };

  const remove = async (p) => {
    if (!window.confirm(`Hapus foto ${p.original_filename}?`)) return;
    try {
      const res = await api.deleteDeliveryProof(order.id, p.id);
      onUpdated(res.order);
      if (proofBlobs[p.id]) {
        URL.revokeObjectURL(proofBlobs[p.id]);
        const next = { ...proofBlobs }; delete next[p.id];
        setProofBlobs(next);
      }
    } catch (ex) {
      setErr(ex.response?.data?.detail || "Gagal menghapus");
    }
  };

  const anyErrors = staged.some((s) => s.status === "error");

  return (
    <section className="panel" data-testid="delivery-proof-panel">
      <header className="panel-head">
        <h3>Foto Bukti Kirim</h3>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span className="muted small">{proofs.length}/{MAX_PHOTOS} foto tersimpan</span>
          <span className={order.delivery_status === "Terkirim" ? "status-ok" : "status-wait"}>
            {order.delivery_status || "Belum Terkirim"}
          </span>
        </div>
      </header>

      {/* Uploaded proofs gallery */}
      {proofs.length === 0 ? (
        <p className="empty">Belum ada foto bukti kirim.</p>
      ) : (
        <div className="proof-grid">
          {proofs.map((p) => (
            <figure key={p.id} className="proof-card" data-testid={`proof-${p.id}`}>
              {proofBlobs[p.id] ? (
                <img src={proofBlobs[p.id]} alt={p.original_filename} loading="lazy" />
              ) : (
                <div className="proof-loading"><Loader2 size={16} className="spin" /></div>
              )}
              <figcaption>
                <strong>{p.uploaded_by_name || p.uploaded_by}</strong>
                <span className="role-tag">{ROLES[p.uploaded_by_role] || p.uploaded_by_role}</span>
                <span className="mono muted">{formatDate(p.uploaded_at)}</span>
                {p.note && <em>{p.note}</em>}
                {canDelete && (
                  <button
                    type="button"
                    className="proof-delete"
                    onClick={() => remove(p)}
                    aria-label="Hapus"
                    data-testid={`proof-delete-${p.id}`}
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </figcaption>
            </figure>
          ))}
        </div>
      )}

      {canUpload && (
        <div className="proof-upload">
          {/* Two entry points: camera (single) and gallery (multi) */}
          <input
            ref={cameraInput}
            type="file"
            accept="image/*"
            capture="environment"
            hidden
            onChange={(e) => stageFiles(e.target.files)}
            data-testid="proof-camera-input"
          />
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            multiple
            hidden
            onChange={(e) => stageFiles(e.target.files)}
            data-testid="proof-file-input"
          />

          <div className="proof-buttons">
            <button
              type="button"
              className="primary"
              onClick={pickCamera}
              disabled={uploading || remainingSlots <= 0}
              data-testid="proof-camera-button"
            >
              <Camera size={16} /> Ambil foto (kamera)
            </button>
            <button
              type="button"
              className="ghost"
              onClick={pickGallery}
              disabled={uploading || remainingSlots <= 0}
              data-testid="proof-gallery-button"
            >
              <ImagePlus size={16} /> Pilih dari galeri
            </button>
          </div>

          {/* Staging area — preview + note + remove per file */}
          {staged.length > 0 && (
            <div className="staged-block" data-testid="staged-block">
              <div className="staged-head">
                <h4>Antrian upload ({staged.length})</h4>
                <div className="staged-actions">
                  <button
                    type="button"
                    className="ghost sm"
                    onClick={cancelAll}
                    disabled={uploading}
                    data-testid="staged-cancel-all"
                  >Batal semua</button>
                  <button
                    type="button"
                    className="primary"
                    onClick={requestUpload}
                    disabled={uploading || readyCount === 0}
                    data-testid="staged-upload-all"
                  >
                    {uploading ? (
                      <><Loader2 size={14} className="spin" /> Mengupload...</>
                    ) : readyCount > 0 ? (
                      <>Konfirmasi upload {readyCount} foto</>
                    ) : doneCount > 0 ? (
                      <>Terunggah {doneCount} foto</>
                    ) : (
                      <>Konfirmasi upload 0 foto</>
                    )}
                  </button>
                </div>
              </div>

              <div className="staged-grid">
                {staged.map((s) => (
                  <div key={s.id} className={`staged-card staged-${s.status}`} data-testid={`staged-${s.id}`}>
                    {s.previewUrl ? (
                      <img src={s.previewUrl} alt="preview" />
                    ) : (
                      <div className="proof-loading"><Loader2 size={16} className="spin" /></div>
                    )}
                    <div className="staged-info">
                      <input
                        type="text"
                        placeholder="Catatan (opsional)"
                        value={s.note}
                        onChange={(e) => updateStagedNote(s.id, e.target.value)}
                        disabled={uploading || s.status === "done"}
                        data-testid={`staged-note-${s.id}`}
                      />
                      <div className="staged-meta">
                        {s.status === "compressing" && <span className="muted"><Loader2 size={12} className="spin" /> Mengompres...</span>}
                        {s.status === "ready" && (
                          <span className="muted mono">
                            {s.sizeKB ? `${s.sizeKB.toFixed(0)} KB` : ""}
                            {s.compressed && s.originalSizeKB ? ` (dari ${s.originalSizeKB.toFixed(0)} KB)` : ""}
                          </span>
                        )}
                        {s.status === "uploading" && (
                          <div className="proof-progress"><div className="proof-progress-bar" style={{ width: `${s.progress}%` }} /></div>
                        )}
                        {s.status === "done" && <span className="status-ok"><CheckCircle2 size={12} /> Terunggah</span>}
                        {s.status === "error" && <span className="status-error"><XCircle size={12} /> {s.error}</span>}
                      </div>
                    </div>
                    {s.status !== "uploading" && s.status !== "done" && (
                      <button
                        type="button"
                        className="staged-remove"
                        onClick={() => removeStaged(s.id)}
                        disabled={uploading}
                        aria-label="Hapus dari antrian"
                        data-testid={`staged-remove-${s.id}`}
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                ))}
              </div>

              {anyErrors && <p className="muted small">Beberapa foto gagal — perbaiki lalu upload ulang.</p>}
            </div>
          )}

          {err && <div className="error" data-testid="proof-error">{err}</div>}
          <p className="muted small">
            Batas {MAX_PHOTOS} foto per PO. Foto dikompres otomatis (~1600px, ~1MB) sebelum dikirim ke storage.
            {remainingSlots > 0 ? ` Sisa slot: ${remainingSlots}.` : " Slot penuh — hapus foto untuk menambah."}
          </p>
        </div>
      )}
      <ConfirmActionModal
        open={!!confirmUpload}
        stage="Terkirim"
        poNumber={order.po_number}
        action={confirmUpload ? `Upload ${confirmUpload.count} foto bukti pengiriman` : "Upload bukti pengiriman"}
        name={user?.name || user?.email}
        time={confirmUpload?.time}
        extra={confirmUpload ? <p className="muted small">Upload {confirmUpload.count} foto bukti pengiriman</p> : null}
        busy={uploading}
        error={err}
        onCancel={() => { if (!uploading) setConfirmUpload(null); }}
        onConfirm={uploadAll}
      />
    </section>
  );
}
