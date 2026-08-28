import { useEffect, useRef, useState } from "react";
import { Camera, Trash2, Loader2, CheckCircle2, XCircle, ImagePlus } from "lucide-react";
import api, { fileUrl } from "../lib/api";
import { compressImage } from "../lib/imageCompress";
import { formatDate, formatDisplayDateTime, ROLES } from "../lib/constants";
import { useAuth } from "../lib/auth";
import { confirmableProofCount, canConfirmDelivered, MIN_DELIVERY_PROOFS, proofUploadUserError } from "../lib/deliveryProof";
import { deliveryHistoryEntries, displayOrDash, historyTimeLabel } from "../lib/deliveryHistory";
import { newActionId } from "../lib/workflowEntries";
import ConfirmActionModal from "./ConfirmActionModal";
import RescheduleDeliveryModal from "./RescheduleDeliveryModal";

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
  const [confirmDeliver, setConfirmDeliver] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [previewId, setPreviewId] = useState(null);

  const canUpload = hasPerm("delivery.update");
  const canDelete = hasPerm("delivery.update");
  const canReschedule = hasPerm("delivery.reschedule") || hasPerm("delivery.reschedule.set_date");
  const canSetDate = hasPerm("delivery.reschedule.set_date");
  const proofs = order.delivery_proofs || [];
  const history = deliveryHistoryEntries(order);
  const remainingSlots = Math.max(0, MAX_PHOTOS - proofs.length);
  const stagedNotYetUploaded = staged.filter((s) => s.status !== "done");
  const freeSlotsForNewSelection = Math.max(0, remainingSlots - stagedNotYetUploaded.length);
  const readyCount = confirmableProofCount(staged);
  const doneCount = staged.filter((s) => s.status === "done").length;
  const deliverEnabled = canUpload && canConfirmDelivered(proofs) && order.delivery_status !== "Terkirim";

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

  const openDeliverConfirm = () => {
    if (!deliverEnabled || confirming) return;
    setErr("");
    setConfirmDeliver({
      count: proofs.length,
      time: formatDisplayDateTime(new Date().toISOString()),
      actionId: newActionId(),
    });
  };

  const executeDeliver = async () => {
    if (!confirmDeliver || confirming || uploading) return;
    setConfirming(true);
    setErr("");
    try {
      const res = await api.confirmDelivered(order.id, { action_id: confirmDeliver.actionId });
      onUpdated(res.order);
      setConfirmDeliver(null);
    } catch (ex) {
      setErr(proofUploadUserError(ex));
    } finally {
      setConfirming(false);
    }
  };

  return (
    <section className="panel" data-testid="delivery-proof-panel">
      <header className="panel-head">
        <h3>Bukti Kirim</h3>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span className="muted small">{proofs.length}/{MAX_PHOTOS} foto tersimpan</span>
          <span className={order.delivery_status === "Terkirim" ? "status-ok" : "status-wait"}>
            {order.delivery_status || "Belum Terkirim"}
          </span>
        </div>
      </header>

      {history.length > 0 && (
        <div className="delivery-history" data-testid="delivery-history">
          {history.map((row, i) => {
            const photoIds = (row.proof_ids && row.proof_ids.length)
              ? row.proof_ids
              : (row.photos || []).map((p) => p.id).filter(Boolean);
            return (
              <article key={row.id || i} className="delivery-history-entry" data-testid={`delivery-history-row-${i}`}>
                <dl className="delivery-history-dl">
                  <dt>Nama Driver</dt><dd data-testid={`delivery-history-driver-${i}`}>{displayOrDash(row.driver_name)}</dd>
                  <dt>Barang / Item</dt><dd data-testid={`delivery-history-item-${i}`}>{displayOrDash(row.item)}</dd>
                  <dt>Qty</dt><dd className="mono" data-testid={`delivery-history-qty-${i}`}>{row.qty != null ? Number(row.qty).toLocaleString("id-ID") : "—"}</dd>
                  <dt>Waktu</dt><dd className="mono" data-testid={`delivery-history-time-${i}`}>{historyTimeLabel(row.time)}</dd>
                  <dt>Plat Kendaraan</dt><dd data-testid={`delivery-history-plate-${i}`}>{displayOrDash(row.vehicle_plate)}</dd>
                </dl>
                <div className="delivery-history-photos">
                  {photoIds.length === 0 ? (
                    <span className="muted small">—</span>
                  ) : photoIds.map((pid) => (
                    <button
                      type="button"
                      key={pid}
                      onClick={() => setPreviewId(pid)}
                      data-testid={`delivery-history-photo-${i}-${pid}`}
                    >
                      {proofBlobs[pid] ? (
                        <img src={proofBlobs[pid]} alt="Bukti foto pengiriman" />
                      ) : (
                        <span className="proof-loading"><Loader2 size={14} className="spin" /></span>
                      )}
                    </button>
                  ))}
                </div>
              </article>
            );
          })}
        </div>
      )}

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
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
            <button
              type="button"
              className="primary"
              onClick={openDeliverConfirm}
              disabled={!deliverEnabled || confirming || uploading}
              data-testid="confirm-delivered-button"
            >
              Konfirmasi Terkirim
            </button>
            {canReschedule && order.delivery_status !== "Terkirim" && (
              <button
                type="button"
                className="ghost"
                onClick={() => setRescheduleOpen(true)}
                disabled={confirming || uploading}
                data-testid="reschedule-delivery-button"
              >
                Jadwalkan Ulang
              </button>
            )}
          </div>
          <p className="muted small">
            Upload foto tidak menandai Terkirim. Minimal {MIN_DELIVERY_PROOFS} foto, lalu Konfirmasi Terkirim.
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
      <ConfirmActionModal
        open={!!confirmDeliver}
        title="KONFIRMASI PENGIRIMAN"
        stage="Terkirim"
        poNumber={order.po_number}
        customer={order.customer_name || order.customer}
        name={user?.name || user?.email}
        time={confirmDeliver?.time}
        extra={confirmDeliver ? <p className="muted small" data-testid="confirm-proof-count">Bukti Kirim: {confirmDeliver.count} foto</p> : null}
        busy={confirming}
        error={err}
        confirmLabel="Konfirmasi Terkirim"
        onCancel={() => { if (!confirming) setConfirmDeliver(null); }}
        onConfirm={executeDeliver}
      />
      <RescheduleDeliveryModal
        open={rescheduleOpen}
        order={order}
        canSetDate={canSetDate}
        currentDate={order.effective_delivery_date || order.scheduled_delivery_date || order.deadline}
        onCancel={() => setRescheduleOpen(false)}
        onDone={(next) => { setRescheduleOpen(false); if (next) onUpdated(next); }}
      />
      {previewId && proofBlobs[previewId] && (
        <div className="tracking-lightbox" data-testid="delivery-history-lightbox" onClick={() => setPreviewId(null)}>
          <img src={proofBlobs[previewId]} alt="Bukti foto pengiriman" />
        </div>
      )}
    </section>
  );
}
