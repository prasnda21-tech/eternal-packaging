import { useEffect, useMemo, useRef, useState } from "react";
import { Camera, Trash2, Loader2, CheckCircle2, XCircle, ImagePlus } from "lucide-react";
import api, { fileUrl } from "../lib/api";
import { compressImage } from "../lib/imageCompress";
import { formatDate, formatDisplayDateTime, ROLES } from "../lib/constants";
import { useAuth } from "../lib/auth";
import {
  confirmableProofCount,
  canConfirmDelivered,
  canSubmitConfirmDelivered,
  confirmDeliveredBlockReason,
  authoritativeDriverDisplayName,
  MIN_DELIVERY_PROOFS,
  proofUploadUserError,
  canUploadDeliveryBatchPhoto,
  deliveryBatchPhotoBlockReason,
  deliveryBatchPhotoReadiness,
  findDeliveryShipment,
  isShipmentDelivered,
  isPengirimanMutationLocked,
  pengirimanSjOptions,
  proofsForShipment,
  selectedPengirimanShipmentId,
  deliveryBatchIdentity,
} from "../lib/deliveryProof";
import { deliveryHistoryEntries, displayOrDash, historyTimeLabel } from "../lib/deliveryHistory";
import { newActionId } from "../lib/workflowEntries";
import ConfirmActionModal from "./ConfirmActionModal";
import RescheduleDeliveryModal from "./RescheduleDeliveryModal";
import SignaturePad from "./SignaturePad";

const MAX_PHOTOS = 5;

// Multi-foto Bukti Kirim: driver can select/capture multiple photos, review the
// staged list (with per-photo note + remove/replace), then confirm upload.
// Files are compressed client-side, then streamed one-by-one to the backend
// which stores each via the storage service (local filesystem in preview, S3/Emergent in production).
function urlShipmentId() {
  if (typeof window === "undefined") return "";
  try {
    return new URLSearchParams(window.location.search).get("shipment") || "";
  } catch {
    return "";
  }
}

function replaceShipmentQuery(shipmentId) {
  if (typeof window === "undefined" || typeof window.history?.replaceState !== "function") return;
  try {
    const url = new URL(window.location.href);
    const sid = String(shipmentId || "").trim();
    if (sid) url.searchParams.set("shipment", sid);
    else url.searchParams.delete("shipment");
    const next = `${url.pathname}${url.search}${url.hash}`;
    const cur = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (next !== cur) window.history.replaceState(window.history.state, "", next);
  } catch {
    /* ignore */
  }
}

export default function DeliveryProofPanel({ order, onUpdated, isOpen = true }) {
  const { hasPerm, user } = useAuth();
  const fileInput = useRef(null);
  const cameraInput = useRef(null);
  const blobCacheRef = useRef({});
  const sjOptions = useMemo(() => pengirimanSjOptions(order), [order]);
  const [sjId, setSjId] = useState(() => selectedPengirimanShipmentId(order, urlShipmentId()));
  const sjIdsKey = sjOptions.map((o) => o.id).join("|");
  useEffect(() => {
    const hint = urlShipmentId();
    if (hint) {
      const next = selectedPengirimanShipmentId(order, hint);
      if (next !== sjId) setSjId(next);
      return;
    }
    const next = selectedPengirimanShipmentId(order, sjId);
    if (next !== sjId) setSjId(next);
  }, [sjIdsKey, order, sjId]);

  const urlWant = urlShipmentId();
  const unmatchedRequested = Boolean(urlWant) && !selectedPengirimanShipmentId(order, urlWant);
  const lookupId = unmatchedRequested ? urlWant : sjId;
  const activeShipment = findDeliveryShipment(order, lookupId);
  const selectedShipmentKey = deliveryBatchIdentity(activeShipment);
  const activeSjOption = sjOptions.find((o) => (
    o.id === sjId
    || (activeShipment && (o.shipment === activeShipment || o.id === activeShipment.id || o.id === selectedShipmentKey))
  )) || null;
  const selectionKey = unmatchedRequested ? `unmatched:${urlWant}` : (selectedShipmentKey || sjId || "");

  const [staged, setStaged] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [err, setErr] = useState("");
  const [proofBlobs, setProofBlobs] = useState({});
  const [confirmUpload, setConfirmUpload] = useState(null);
  const [confirmDeliver, setConfirmDeliver] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [previewId, setPreviewId] = useState(null);
  const [recipientName, setRecipientName] = useState(
    () => order.delivery_recipient_signature?.recipient_name || ""
  );
  const [signatureDataUrl, setSignatureDataUrl] = useState(null);
  const [driverSignatureDataUrl, setDriverSignatureDataUrl] = useState(null);
  const [savingSig, setSavingSig] = useState(false);
  const [sigPadKey, setSigPadKey] = useState(0);
  const [driverPadKey, setDriverPadKey] = useState(0);

  useEffect(() => {
    const n = order.delivery_recipient_signature?.recipient_name;
    if (n && !recipientName) setRecipientName(n);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order.delivery_recipient_signature?.id]);

  useEffect(() => {
    setStaged((prev) => {
      prev.forEach((s) => { if (s.previewUrl) URL.revokeObjectURL(s.previewUrl); });
      return [];
    });
    Object.values(blobCacheRef.current).forEach((u) => { if (u) URL.revokeObjectURL(u); });
    blobCacheRef.current = {};
    setProofBlobs({});
    setUploading(false);
    setErr("");
    setConfirmUpload(null);
    setConfirmDeliver(null);
    setConfirming(false);
    setDeletingId(null);
    setPreviewId(null);
    setSignatureDataUrl(null);
    setDriverSignatureDataUrl(null);
    setSavingSig(false);
    setSigPadKey((k) => k + 1);
    setDriverPadKey((k) => k + 1);
    setRecipientName(order.delivery_recipient_signature?.recipient_name || "");
  }, [selectionKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const canUpload = hasPerm("delivery.update");
  const shipmentResolved = Boolean(activeShipment) && Boolean(selectedShipmentKey);
  const batchPhotoReady = shipmentResolved ? canUploadDeliveryBatchPhoto(order, activeShipment) : false;
  const batchPhotoBlockReason = !shipmentResolved
    ? (unmatchedRequested
      ? "Surat Jalan pada tautan tidak ditemukan."
      : (sjOptions.length > 1 ? "Pilih Surat Jalan terlebih dahulu." : "Surat Jalan belum dipilih."))
    : deliveryBatchPhotoBlockReason(order, activeShipment);
  const batchReadiness = shipmentResolved
    ? deliveryBatchPhotoReadiness(order, activeShipment)
    : { allowed: false, loadedTotal: 0, requiredTotal: 0, lines: [] };
  const selectedDelivered = isShipmentDelivered(order, activeShipment);
  const mutationLocked = isPengirimanMutationLocked(order, activeShipment);
  const canWritePhoto = canUpload && shipmentResolved && batchPhotoReady && !mutationLocked;
  const canDelete = hasPerm("delivery.update");
  const canReschedule = hasPerm("delivery.reschedule") || hasPerm("delivery.reschedule.set_date");
  const canSetDate = hasPerm("delivery.reschedule.set_date");
  const proofs = ((sjOptions.length > 1 && !shipmentResolved) || unmatchedRequested)
    ? []
    : proofsForShipment(order, activeShipment);
  const orderSig = order.delivery_recipient_signature;
  const driverSig = order.delivery_driver_signature;
  const driverDisplayName = authoritativeDriverDisplayName(order, user);
  const history = deliveryHistoryEntries(order);
  const remainingSlots = Math.max(0, MAX_PHOTOS - proofs.length);
  const photoLimitHint = sjOptions.length > 1
    ? `Maksimal ${MAX_PHOTOS} foto untuk Surat Jalan ini`
    : `Batas ${MAX_PHOTOS} foto per PO`;
  const stagedNotYetUploaded = staged.filter((s) => s.status !== "done");
  const freeSlotsForNewSelection = Math.max(0, remainingSlots - stagedNotYetUploaded.length);
  const readyCount = confirmableProofCount(staged);
  const doneCount = staged.filter((s) => s.status === "done").length;
  // Button APPEARS after existing 2-photo rule; submit still needs name + both signatures.
  const deliverButtonVisible = canUpload && shipmentResolved && canConfirmDelivered(proofs) && !mutationLocked;
  const deliverEnabled = deliverButtonVisible && canSubmitConfirmDelivered(
    proofs, recipientName, signatureDataUrl, orderSig, driverSignatureDataUrl, driverSig
  );
  const deliverBlockReason = deliverButtonVisible
    ? confirmDeliveredBlockReason(
      proofs, recipientName, signatureDataUrl, orderSig, driverSignatureDataUrl, driverSig
    )
    : null;

  const onSelectSj = (nextId) => {
    const resolved = selectedPengirimanShipmentId(order, nextId);
    setSjId(resolved);
    replaceShipmentQuery(resolved);
  };

  // Load proof images as blob URLs (cookie auth on same origin).
  // Preview-only: skip while the Pengiriman accordion is collapsed so Detail PO
  // does not fetch blobs until the panel is actually visible. Cached blob URLs
  // stay in local state across collapse/expand.
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    (async () => {
      const next = { ...blobCacheRef.current };
      for (const p of proofs) {
        if (next[p.id]) continue;
        try {
          const resp = await fetch(fileUrl(p.storage_path), { credentials: "include" });
          if (!resp.ok) continue;
          const blob = await resp.blob();
          if (cancelled) return;
          next[p.id] = URL.createObjectURL(blob);
          blobCacheRef.current = next;
        } catch { /* ignore */ }
      }
      if (!cancelled) setProofBlobs({ ...blobCacheRef.current });
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, proofs.length, selectionKey]);

  // Cleanup blob URLs
  useEffect(() => () => {
    Object.values(blobCacheRef.current).forEach((u) => { if (u) URL.revokeObjectURL(u); });
    blobCacheRef.current = {};
    staged.forEach((s) => s.previewUrl && URL.revokeObjectURL(s.previewUrl));
  }, []);  // eslint-disable-line

  const pickGallery = () => fileInput.current?.click();
  const pickCamera = () => cameraInput.current?.click();

  const stageFiles = async (fileList) => {
    setErr("");
    if (!canWritePhoto) {
      setErr(batchPhotoBlockReason || "Foto pengiriman belum dapat diinput untuk batch ini.");
      return;
    }
    const files = Array.from(fileList || []);
    if (files.length === 0) return;
    if (freeSlotsForNewSelection <= 0) {
      setErr(`Sudah mencapai batas ${MAX_PHOTOS} foto.`);
      return;
    }
    // Limit selection to free slots
    const toStage = files.slice(0, freeSlotsForNewSelection);
    if (files.length > toStage.length) {
      setErr(`Hanya ${toStage.length} foto ditambahkan (${photoLimitHint}).`);
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
    if (!selectedShipmentKey) {
      setErr("Pilih Surat Jalan terlebih dahulu.");
      return;
    }
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
        }, selectedShipmentKey);
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
    if (!p?.id || deletingId || uploading) return;
    if (!window.confirm(`Hapus foto ${p.original_filename}?`)) return;
    setDeletingId(p.id);
    setErr("");
    try {
      const res = await api.deleteDeliveryProof(order.id, p.id);
      onUpdated(res.order);
      if (proofBlobs[p.id]) {
        URL.revokeObjectURL(proofBlobs[p.id]);
        const next = { ...proofBlobs }; delete next[p.id];
        setProofBlobs(next);
      }
    } catch (ex) {
      const d = ex.response?.data?.detail;
      setErr(typeof d === "string" ? d : (d?.message || "Gagal menghapus"));
    } finally {
      setDeletingId(null);
    }
  };

  const anyErrors = staged.some((s) => s.status === "error");

  const openDeliverConfirm = () => {
    if (!deliverButtonVisible || confirming) return;
    const blocked = confirmDeliveredBlockReason(
      proofs, recipientName, signatureDataUrl, orderSig, driverSignatureDataUrl, driverSig
    );
    if (blocked) {
      setErr(blocked);
      return;
    }
    setErr("");
    setConfirmDeliver({
      count: proofs.length,
      time: formatDisplayDateTime(new Date().toISOString()),
      actionId: newActionId(),
      recipientName: (recipientName || orderSig?.recipient_name || "").trim(),
      driverName: driverDisplayName,
    });
  };

  const ensureSignaturesSaved = async () => {
    if (!selectedShipmentKey) {
      throw new Error("Pilih Surat Jalan terlebih dahulu.");
    }
    let latest = order;
    const name = (recipientName || orderSig?.recipient_name || "").trim();
    const recipientImageSaved = orderSig && orderSig.id && orderSig.frozen !== true
      && (orderSig.signature_storage_path || orderSig.recipient_signed_at);
    if (!(recipientImageSaved && (!signatureDataUrl || orderSig.recipient_name === name))) {
      if (!signatureDataUrl && !recipientImageSaved) {
        throw new Error("Tanda tangan penerima wajib sebelum Konfirmasi Terkirim.");
      }
      if (signatureDataUrl) {
        setSavingSig(true);
        try {
          const res = await api.uploadDeliveryRecipientSignature(latest.id, {
            recipientName: name,
            signatureDataUrl,
            shipmentId: selectedShipmentKey,
          });
          latest = res.order;
          onUpdated(res.order);
        } finally {
          setSavingSig(false);
        }
      }
    }
    const driverSaved = latest.delivery_driver_signature
      && latest.delivery_driver_signature.id
      && latest.delivery_driver_signature.frozen !== true
      && (latest.delivery_driver_signature.signature_storage_path
        || latest.delivery_driver_signature.driver_signature_storage_path
        || latest.delivery_driver_signature.driver_signed_at);
    if (!driverSaved || driverSignatureDataUrl) {
      if (!driverSignatureDataUrl && !driverSaved) {
        throw new Error("Tanda tangan driver/pengirim wajib sebelum Konfirmasi Terkirim.");
      }
      if (driverSignatureDataUrl) {
        setSavingSig(true);
        try {
          const res = await api.uploadDeliveryDriverSignature(latest.id, {
            signatureDataUrl: driverSignatureDataUrl,
            shipmentId: selectedShipmentKey,
          });
          latest = res.order;
          onUpdated(res.order);
        } finally {
          setSavingSig(false);
        }
      }
    }
    return latest;
  };

  const executeDeliver = async () => {
    if (!confirmDeliver || confirming || uploading || savingSig) return;
    if (!selectedShipmentKey) {
      setErr("Pilih Surat Jalan terlebih dahulu.");
      return;
    }
    setConfirming(true);
    setErr("");
    try {
      await ensureSignaturesSaved();
      const res = await api.confirmDelivered(order.id, {
        action_id: confirmDeliver.actionId,
        shipment_id: selectedShipmentKey,
        recipient_name: (recipientName || orderSig?.recipient_name || "").trim(),
      });
      onUpdated(res.order);
      setConfirmDeliver(null);
      setSignatureDataUrl(null);
      setDriverSignatureDataUrl(null);
    } catch (ex) {
      setErr(proofUploadUserError(ex));
    } finally {
      setConfirming(false);
    }
  };

  const clearSignature = async () => {
    setSignatureDataUrl(null);
    setSigPadKey((k) => k + 1);
    if (orderSig && orderSig.id && orderSig.frozen !== true) {
      try {
        const res = await api.clearDeliveryRecipientSignature(order.id);
        onUpdated(res.order);
      } catch (ex) {
        setErr(proofUploadUserError(ex));
      }
    }
  };

  const clearDriverSignature = async () => {
    setDriverSignatureDataUrl(null);
    setDriverPadKey((k) => k + 1);
    if (driverSig && driverSig.id && driverSig.frozen !== true) {
      try {
        const res = await api.clearDeliveryDriverSignature(order.id);
        onUpdated(res.order);
      } catch (ex) {
        setErr(proofUploadUserError(ex));
      }
    }
  };

  return (
    <section className="panel" data-testid="delivery-proof-panel">
      <header className="panel-head">
        <h3>Bukti Kirim</h3>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span className="muted small">{proofs.length}/{MAX_PHOTOS} foto tersimpan</span>
          <span
            className={selectedDelivered ? "status-ok" : "status-wait"}
            data-testid="pengiriman-selected-status"
          >
            {selectedDelivered ? "SJ Terkirim" : (order.delivery_status || "Belum Terkirim")}
          </span>
        </div>
      </header>

      {sjOptions.length > 0 && (
        <div data-testid="pengiriman-sj-select-wrap" style={{ padding: "0 16px 10px" }}>
          <label style={{ display: "grid", gap: 4 }}>
            <span className="muted small">Surat Jalan</span>
            <select
              value={activeSjOption?.id || sjId || ""}
              onChange={(e) => onSelectSj(e.target.value)}
              data-testid="pengiriman-sj-select"
            >
              {(sjOptions.length > 1 || !activeSjOption) && (
                <option value="">Pilih Surat Jalan</option>
              )}
              {sjOptions.map((opt) => (
                <option key={opt.id} value={opt.id}>
                  {opt.number} · {opt.id}{opt.date ? ` · ${opt.date}` : ""}{opt.delivered ? " · Terkirim" : ""}
                </option>
              ))}
            </select>
          </label>
          {activeSjOption && (
            <div
              data-testid="pengiriman-selected-sj-card"
              style={{
                display: "grid",
                gap: 6,
                marginTop: 8,
                background: "#f8fafc",
                padding: "10px 12px",
                borderRadius: 6,
                fontSize: "0.85rem",
                border: "1px solid #e2e8f0",
              }}
            >
              <div data-testid="pengiriman-selected-sj-number">
                <strong>Surat Jalan:</strong> {activeSjOption.number}
              </div>
              <div data-testid="pengiriman-selected-sj-id" className="muted small">
                Batch: {activeSjOption.id}
                {activeSjOption.date ? ` · ${activeSjOption.date}` : ""}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8 }}>
                <div>
                  <span style={{ color: "#64748b", fontSize: "0.75rem", display: "block" }}>QTY SJ</span>
                  <strong data-testid="pengiriman-selected-sj-qty">{Number(activeSjOption.qty || 0).toLocaleString("id-ID")} pcs</strong>
                </div>
                <div>
                  <span style={{ color: "#64748b", fontSize: "0.75rem", display: "block" }}>LOADING</span>
                  <strong data-testid="pengiriman-selected-loading-qty">
                    {Number(batchReadiness.loadedTotal || 0).toLocaleString("id-ID")} / {Number(batchReadiness.requiredTotal || activeSjOption.qty || 0).toLocaleString("id-ID")}
                  </strong>
                </div>
                <div>
                  <span style={{ color: "#64748b", fontSize: "0.75rem", display: "block" }}>STATUS</span>
                  <strong data-testid="pengiriman-selected-ready-status">
                    {selectedDelivered ? "Terkirim" : (batchReadiness.allowed ? "Siap dikirim" : "Belum siap")}
                  </strong>
                </div>
              </div>
              {Array.isArray(batchReadiness.lines) && batchReadiness.lines.length > 0 && (
                <ul data-testid="pengiriman-selected-sj-lines" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                  {batchReadiness.lines.map((ln) => (
                    <li key={`${ln.lineIdx}-${ln.name}`}>
                      {ln.name}: {Number(ln.required || 0).toLocaleString("id-ID")} pcs
                      {ln.loaded != null ? ` · loaded ${Number(ln.loaded || 0).toLocaleString("id-ID")}` : ""}
                    </li>
                  ))}
                </ul>
              )}
              {!selectedDelivered && !batchReadiness.allowed && batchReadiness.reason && (
                <p className="muted small" data-testid="pengiriman-selected-gate-reason">{batchReadiness.reason}</p>
              )}
            </div>
          )}
        </div>
      )}

      {unmatchedRequested && (
        <p className="muted small" data-testid="pengiriman-sj-unmatched" style={{ padding: "0 16px 10px" }}>
          Surat Jalan pada tautan tidak ditemukan. Pilih Surat Jalan yang valid.
        </p>
      )}
      {sjOptions.length > 1 && !activeSjOption && !unmatchedRequested && (
        <p className="muted small" data-testid="pengiriman-sj-required" style={{ padding: "0 16px 10px" }}>
          Pilih Surat Jalan terlebih dahulu.
        </p>
      )}

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
                    disabled={!!deletingId}
                    aria-label="Hapus"
                    data-testid={`proof-delete-${p.id}`}
                  >
                    {deletingId === p.id ? <Loader2 size={13} className="spin" /> : <Trash2 size={13} />}
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

          {!canWritePhoto && batchPhotoBlockReason && (
            <p className="muted small" data-testid="proof-batch-readiness-hint">
              {batchPhotoBlockReason}
            </p>
          )}

          <div className="proof-buttons">
            <button
              type="button"
              className="primary"
              onClick={pickCamera}
              disabled={uploading || remainingSlots <= 0 || !canWritePhoto}
              data-testid="proof-camera-button"
            >
              <Camera size={16} /> Ambil foto (kamera)
            </button>
            <button
              type="button"
              className="ghost"
              onClick={pickGallery}
              disabled={uploading || remainingSlots <= 0 || !canWritePhoto}
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

          {canConfirmDelivered(proofs) && !mutationLocked && (
            <div className="recipient-signature-block" data-testid="recipient-signature-block" style={{ marginTop: 16 }}>
              <h4 style={{ margin: "0 0 8px" }}>Tanda Tangan Penerima</h4>
              <label className="field" style={{ display: "block", marginBottom: 10 }}>
                <span className="lbl">Nama Penerima</span>
                <input
                  type="text"
                  value={recipientName}
                  onChange={(e) => setRecipientName(e.target.value)}
                  placeholder="Nama penerima barang"
                  disabled={confirming || uploading || savingSig}
                  data-testid="recipient-name-input"
                  autoComplete="name"
                  style={{ width: "100%", marginTop: 4 }}
                />
              </label>
              <SignaturePad
                key={sigPadKey}
                height={160}
                disabled={confirming || uploading || savingSig}
                onChange={setSignatureDataUrl}
              />
              {(signatureDataUrl || (orderSig && !orderSig.frozen)) && (
                <button
                  type="button"
                  className="ghost sm"
                  onClick={clearSignature}
                  disabled={confirming || uploading || savingSig}
                  data-testid="recipient-signature-clear"
                  style={{ marginTop: 4 }}
                >
                  Hapus / Ulangi Tanda Tangan
                </button>
              )}
              {orderSig?.recipient_signed_at && !orderSig.frozen && (
                <p className="muted small" data-testid="recipient-signature-saved-hint">
                  Tanda tangan tersimpan {formatDisplayDateTime(orderSig.recipient_signed_at)}
                </p>
              )}

              <h4 style={{ margin: "16px 0 8px" }}>TTD Driver / Pengirim</h4>
              <p className="muted small" data-testid="driver-name-display">
                Nama driver: <strong>{driverDisplayName || "—"}</strong>
              </p>
              <div data-testid="driver-signature-block">
                <SignaturePad
                  key={driverPadKey}
                  height={160}
                  disabled={confirming || uploading || savingSig}
                  onChange={setDriverSignatureDataUrl}
                />
              </div>
              {(driverSignatureDataUrl || (driverSig && !driverSig.frozen)) && (
                <button
                  type="button"
                  className="ghost sm"
                  onClick={clearDriverSignature}
                  disabled={confirming || uploading || savingSig}
                  data-testid="driver-signature-clear"
                  style={{ marginTop: 4 }}
                >
                  Hapus / Ulangi Tanda Tangan Driver
                </button>
              )}
              {driverSig?.driver_signed_at && !driverSig.frozen && (
                <p className="muted small" data-testid="driver-signature-saved-hint">
                  Tanda tangan driver tersimpan {formatDisplayDateTime(driverSig.driver_signed_at)}
                </p>
              )}
            </div>
          )}

          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
            {deliverButtonVisible && (
              <button
                type="button"
                className="primary"
                onClick={openDeliverConfirm}
                disabled={!deliverEnabled || confirming || uploading || savingSig}
                data-testid="confirm-delivered-button"
              >
                {confirming || savingSig ? "Memproses..." : "Konfirmasi Terkirim"}
              </button>
            )}
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
          {deliverButtonVisible && deliverBlockReason && (
            <p className="muted small" data-testid="confirm-delivered-hint">{deliverBlockReason}</p>
          )}
          <p className="muted small" data-testid="proof-photo-limit-hint">
            Upload foto tidak menandai Terkirim. Minimal {MIN_DELIVERY_PROOFS} foto, lalu isi nama penerima + TTD penerima + TTD driver, kemudian Konfirmasi Terkirim.
            {" "}{photoLimitHint}. Foto dikompres otomatis (~1600px, ~1MB) sebelum dikirim ke storage.
            {remainingSlots > 0 ? ` Sisa slot: ${remainingSlots}.` : " Slot penuh — hapus foto untuk menambah."}
          </p>
        </div>
      )}
      <ConfirmActionModal
        open={!!confirmUpload}
        stage="Pengiriman"
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
        stage="Pengiriman"
        poNumber={order.po_number}
        customer={order.customer_name || order.customer}
        name={user?.name || user?.email}
        time={confirmDeliver?.time}
        extra={confirmDeliver ? (
          <div>
            <p className="muted small" data-testid="confirm-selected-sj">Surat Jalan: {activeSjOption?.number || sjId}</p>
            <p className="muted small" data-testid="confirm-proof-count">Bukti Kirim: {confirmDeliver.count} foto</p>
            <p className="muted small" data-testid="confirm-recipient-name">Penerima: {confirmDeliver.recipientName}</p>
            <p className="muted small" data-testid="confirm-driver-name">Driver: {confirmDeliver.driverName || "—"}</p>
          </div>
        ) : null}
        busy={confirming || savingSig}
        error={err}
        confirmLabel="Konfirmasi Terkirim"
        onCancel={() => { if (!confirming && !savingSig) setConfirmDeliver(null); }}
        onConfirm={executeDeliver}
      />
      <RescheduleDeliveryModal
        open={rescheduleOpen}
        order={order}
        canSetDate={canSetDate}
        currentDate={order.tanggal_pengiriman || order.effective_delivery_date || order.scheduled_delivery_date || order.delivery_deadline_manual}
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
