import { useEffect, useMemo, useRef, useState } from "react";
import { Camera, ImagePlus } from "lucide-react";
import api from "../../lib/api";
import { formatApiError } from "../../lib/errors";
import { newActionId } from "../../lib/workflowEntries";
import {
  MIN_DELIVERY_PROOFS,
  MAX_PHOTOS,
  DELIVERY_PROOF_ACCEPT,
  canSubmitConfirmDelivered,
  confirmDeliveredBlockReason,
  proofUploadUserError,
  deliveryProofClientError,
  deliveryProofCompressedSizeError,
  isDeliveryProofUploadBusy,
  emptyDeliveryPhotoSlotCount,
  canUploadDeliveryBatchPhoto,
  deliveryBatchPhotoBlockReason,
  deliveryBatchPhotoReadiness,
  findDeliveryShipment,
  isShipmentDelivered,
  pengirimanSjOptions,
  proofsForShipment,
  selectedPengirimanShipmentId,
} from "../../lib/deliveryProof";
import { compressImage } from "../../lib/imageCompress";
import { getCanonicalCustomerName } from "../../lib/canonicalCustomer";
import { deliveryAddressFromOrder, mapsSearchUrl } from "../../lib/mobileWorkspaces";
import { applyLocalSignature } from "../../lib/mobileSignature";
import { fileUrl } from "../../lib/api";
import MobileSignatureModal from "../components/MobileSignatureModal";

function newClientId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random()}`;
}

function urlShipmentId() {
  if (typeof window === "undefined") return "";
  try {
    return new URLSearchParams(window.location.search).get("shipment") || "";
  } catch {
    return "";
  }
}

export default function MobileDeliveryWorkspace({ order, canEdit, onUpdated }) {
  const sjOptions = useMemo(() => pengirimanSjOptions(order), [order]);
  const [sjId, setSjId] = useState(() => selectedPengirimanShipmentId(order, urlShipmentId()));
  const sjIdsKey = sjOptions.map((o) => o.id).join("|");
  useEffect(() => {
    const next = selectedPengirimanShipmentId(order, sjId || urlShipmentId());
    if (next && next !== sjId) setSjId(next);
  }, [sjIdsKey, order, sjId]);
  const activeShipment = findDeliveryShipment(order, sjId) || sjOptions[0]?.shipment || null;
  const activeSjOption = sjOptions.find((o) => o.id === (activeShipment?.id || sjId)) || sjOptions[0] || null;
  const selectedDelivered = isShipmentDelivered(order, activeShipment);
  const delivered = selectedDelivered || order?.delivery_status === "Terkirim" || !!order?.terkirim_confirmed;
  const proofs = proofsForShipment(order, activeShipment);
  const address = deliveryAddressFromOrder(order);
  const maps = mapsSearchUrl(address);
  const cameraRef = useRef(null);
  const galleryRef = useRef(null);
  const pickerIntentRef = useRef({ type: "add" });
  const pickingRef = useRef(false);
  const [recipientName, setRecipientName] = useState(order?.delivery_recipient_signature?.recipient_name || "");
  const [localSig, setLocalSig] = useState({ recipient: null, driver: null });
  const [modal, setModal] = useState({ open: false, role: null });
  const [picker, setPicker] = useState(null);
  const [preview, setPreview] = useState(null);
  const [localSlots, setLocalSlots] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const orderSig = order?.delivery_recipient_signature;
  const driverSig = order?.delivery_driver_signature;
  const canMutate = canEdit && !delivered;
  const batchPhotoReady = canUploadDeliveryBatchPhoto(order, activeShipment);
  const batchPhotoBlockReason = deliveryBatchPhotoBlockReason(order, activeShipment);
  const batchReadiness = deliveryBatchPhotoReadiness(order, activeShipment);
  const canWritePhoto = canMutate && batchPhotoReady;
  const photoBusy = isDeliveryProofUploadBusy(localSlots);
  const canConfirm = canSubmitConfirmDelivered(
    proofs,
    recipientName,
    localSig.recipient,
    orderSig,
    localSig.driver,
    driverSig,
  ) && batchPhotoReady && canMutate && !photoBusy;
  const emptySlots = canWritePhoto ? emptyDeliveryPhotoSlotCount(proofs.length, localSlots.length) : 0;

  const closePicker = () => setPicker(null);

  const openPicker = (intent) => {
    if (!canWritePhoto || photoBusy) return;
    pickerIntentRef.current = intent || { type: "add" };
    setPicker(pickerIntentRef.current);
  };

  const triggerInput = (kind) => {
    closePicker();
    const el = kind === "gallery" ? galleryRef.current : cameraRef.current;
    if (el) {
      el.value = "";
      el.click();
    }
  };

  const patchSlot = (clientId, patch) => {
    setLocalSlots((prev) => prev.map((s) => (s.id === clientId ? { ...s, ...patch } : s)));
  };

  const dropSlot = (clientId) => {
    setLocalSlots((prev) => {
      const found = prev.find((s) => s.id === clientId);
      if (found?.previewUrl) URL.revokeObjectURL(found.previewUrl);
      return prev.filter((s) => s.id !== clientId);
    });
  };

  const addPhoto = async (file, intent) => {
    if (!file || !canMutate) return;
    if (!canWritePhoto) {
      setErr(batchPhotoBlockReason || "Foto pengiriman belum dapat diinput untuk batch ini.");
      return;
    }
    if (pickingRef.current) return;
    pickingRef.current = true;
    const clientId = newClientId();
    const previewUrl = URL.createObjectURL(file);
    const replaceId = intent?.type === "replace" ? intent.proofId : null;
    setLocalSlots((prev) => [...prev, {
      id: clientId,
      previewUrl,
      status: "selecting",
      error: "",
      file,
      replaceId,
    }]);
    setErr("");
    const clientErr = deliveryProofClientError(file);
    if (clientErr) {
      patchSlot(clientId, { status: "error", error: clientErr });
      pickingRef.current = false;
      return;
    }
    patchSlot(clientId, { status: "uploading" });
    try {
      const compressed = await compressImage(file);
      const uploadFile = compressed?.file || file;
      const sizeErr = deliveryProofCompressedSizeError(uploadFile);
      if (sizeErr) {
        patchSlot(clientId, { status: "error", error: sizeErr });
        return;
      }
      const currentProofs = order?.delivery_proofs || [];
      let latestOrder = order;
      if (replaceId && currentProofs.length >= MAX_PHOTOS) {
        const del = await api.deleteDeliveryProof(order.id, replaceId);
        latestOrder = del.order;
        onUpdated(latestOrder);
        patchSlot(clientId, { replaceId: null });
      }
      const res = await api.uploadDeliveryProof(order.id, uploadFile, "", undefined, activeShipment?.id || sjId);
      latestOrder = res.order;
      onUpdated(latestOrder);
      if (replaceId && currentProofs.length < MAX_PHOTOS) {
        const del = await api.deleteDeliveryProof(order.id, replaceId);
        latestOrder = del.order;
        onUpdated(latestOrder);
      }
      dropSlot(clientId);
    } catch (e) {
      const msg = proofUploadUserError(e);
      patchSlot(clientId, { status: "error", error: msg });
      setErr(msg);
    } finally {
      pickingRef.current = false;
      if (cameraRef.current) cameraRef.current.value = "";
      if (galleryRef.current) galleryRef.current.value = "";
    }
  };

  const onFileChange = (event) => {
    const file = event.target.files?.[0];
    const intent = pickerIntentRef.current;
    pickerIntentRef.current = { type: "add" };
    addPhoto(file, intent);
  };

  const retrySlot = (slot) => {
    if (!slot?.file || photoBusy) return;
    dropSlot(slot.id);
    addPhoto(slot.file, slot.replaceId ? { type: "replace", proofId: slot.replaceId } : { type: "add" });
  };

  const removeProof = async (proof) => {
    if (!proof?.id || !canMutate || photoBusy) return;
    setBusy(true);
    setErr("");
    try {
      const res = await api.deleteDeliveryProof(order.id, proof.id);
      onUpdated(res.order);
    } catch (e) {
      setErr(proofUploadUserError(e));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    const blocked = confirmDeliveredBlockReason(
      proofs, recipientName, localSig.recipient, orderSig, localSig.driver, driverSig,
    );
    if (blocked || !canConfirm || photoBusy) {
      setErr(blocked || "Konfirmasi terkirim belum memenuhi syarat.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      let latest = order;
      if (localSig.recipient) {
        const res = await api.uploadDeliveryRecipientSignature(latest.id, {
          recipientName,
          signatureDataUrl: localSig.recipient,
          shipmentId: activeShipment?.id || sjId,
        });
        latest = res.order;
        onUpdated(latest);
      }
      if (localSig.driver) {
        const res = await api.uploadDeliveryDriverSignature(latest.id, {
          signatureDataUrl: localSig.driver,
          shipmentId: activeShipment?.id || sjId,
        });
        latest = res.order;
        onUpdated(latest);
      }
      const res = await api.confirmDelivered(order.id, {
        action_id: newActionId(),
        shipment_id: activeShipment?.id || sjId,
      });
      onUpdated(res.order);
      setLocalSig({ recipient: null, driver: null });
    } catch (e) {
      setErr(formatApiError(e, proofUploadUserError(e)));
    } finally {
      setBusy(false);
    }
  };

  const pickerTitle = picker?.type === "replace" ? "Ganti Bukti Pengiriman" : "Tambah Bukti Pengiriman";

  return (
    <section data-testid="mobile-ws-delivery">
      <div className="mop-ws-header"><h2>PENGIRIMAN</h2></div>
      {sjOptions.length > 0 && (
        <label className="mop-field" data-testid="mobile-pengiriman-sj-select-wrap">
          <span>Surat Jalan</span>
          <select
            value={activeSjOption?.id || sjId || ""}
            onChange={(e) => setSjId(e.target.value)}
            data-testid="mobile-pengiriman-sj-select"
          >
            {sjOptions.map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.number} · {opt.id}{opt.delivered ? " · Terkirim" : ""}
              </option>
            ))}
          </select>
        </label>
      )}
      {activeSjOption && (
        <div className="mop-file" data-testid="mobile-pengiriman-selected-sj-card">
          <span>Surat Jalan</span>
          <strong data-testid="mobile-pengiriman-selected-sj-number">{activeSjOption.number}</strong>
          <p data-testid="mobile-pengiriman-selected-sj-qty">Qty {Number(activeSjOption.qty || 0).toLocaleString("id-ID")} pcs</p>
          <p data-testid="mobile-pengiriman-selected-loading">
            Loading {Number(batchReadiness.loadedTotal || 0).toLocaleString("id-ID")} / {Number(batchReadiness.requiredTotal || activeSjOption.qty || 0).toLocaleString("id-ID")}
          </p>
          <p data-testid="mobile-pengiriman-selected-status">
            {selectedDelivered ? "Terkirim" : (batchReadiness.allowed ? "Siap dikirim" : "Belum siap")}
          </p>
        </div>
      )}
      <div className="mop-file">
        <span>Customer</span>
        <strong className="mop-card-title" style={{ display: "block" }} data-testid="mobile-delivery-customer">{getCanonicalCustomerName(order)}</strong>
        <p>{address || "Alamat tidak tersedia"}</p>
        {maps ? (
          <a className="mop-btn-primary" style={{ display: "flex", alignItems: "center", justifyContent: "center", textDecoration: "none" }} href={maps} target="_blank" rel="noreferrer">
            Buka Maps
          </a>
        ) : null}
      </div>
      <h3>Bukti Pengiriman</h3>
      <div className="mop-photos">
        {proofs.map((p, i) => (
          <div className="mop-photo" key={p.id || i} data-testid={`mobile-delivery-proof-${p.id || i}`}>
            {p.storage_path ? (
              <img src={fileUrl(p.storage_path)} alt={`Bukti pengiriman ${i + 1}`} />
            ) : (
              <span>Foto {i + 1}</span>
            )}
            <div className="mop-photo-actions">
              <button type="button" onClick={() => setPreview({ src: fileUrl(p.storage_path), alt: `Bukti pengiriman ${i + 1}` })} data-testid={`mobile-proof-view-${p.id || i}`}>
                Lihat
              </button>
              {canMutate && (
                <>
                  <button type="button" disabled={photoBusy || busy} onClick={() => openPicker({ type: "replace", proofId: p.id })} data-testid={`mobile-proof-replace-${p.id}`}>
                    Ganti
                  </button>
                  <button type="button" disabled={photoBusy || busy} onClick={() => removeProof(p)} data-testid={`mobile-proof-delete-${p.id}`}>
                    Hapus
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
        {localSlots.map((slot) => (
          <div className={`mop-photo mop-photo-local mop-photo-${slot.status}`} key={slot.id} data-testid={`mobile-delivery-local-${slot.id}`}>
            {slot.previewUrl ? <img src={slot.previewUrl} alt="Pratinjau bukti pengiriman" /> : null}
            <span className="mop-photo-status">
              {slot.status === "selecting" && "Memilih..."}
              {slot.status === "uploading" && "Mengunggah..."}
              {slot.status === "error" && (slot.error || "Gagal unggah")}
            </span>
            {slot.status === "error" && canMutate && (
              <div className="mop-photo-actions">
                <button type="button" onClick={() => setPreview({ src: slot.previewUrl, alt: "Pratinjau bukti pengiriman" })}>Lihat</button>
                <button type="button" onClick={() => retrySlot(slot)} data-testid={`mobile-proof-retry-${slot.id}`}>Coba lagi</button>
                <button type="button" onClick={() => dropSlot(slot.id)}>Hapus</button>
              </div>
            )}
          </div>
        ))}
        {Array.from({ length: emptySlots }, (_, i) => {
          const n = proofs.length + localSlots.length + i + 1;
          return (
            <button
              type="button"
              className="mop-photo-add"
              key={`empty-${n}`}
              onClick={() => openPicker({ type: "add" })}
              disabled={photoBusy || busy}
              data-testid={i === 0 ? "mobile-add-photo" : `mobile-add-photo-${n}`}
              aria-label="Tambah Foto"
            >
              <span className="mop-photo-add-plus" aria-hidden="true">+</span>
              <span>Tambah Foto</span>
            </button>
          );
        })}
      </div>
      <input
        ref={cameraRef}
        type="file"
        accept={DELIVERY_PROOF_ACCEPT}
        capture="environment"
        hidden
        onChange={onFileChange}
        data-testid="mobile-delivery-camera-input"
        aria-hidden="true"
      />
      <input
        ref={galleryRef}
        type="file"
        accept={DELIVERY_PROOF_ACCEPT}
        hidden
        onChange={onFileChange}
        data-testid="mobile-delivery-gallery-input"
        aria-label="Pilih foto dari galeri"
      />
      <label className="mop-field">
        <span>Nama Penerima</span>
        <input value={recipientName} onChange={(e) => setRecipientName(e.target.value)} disabled={!canMutate} data-testid="mobile-recipient-name" />
      </label>
      <div className="mop-file">
        <strong>TTD PENERIMA</strong>
        {(localSig.recipient || orderSig?.signature_storage_path) ? <p>TTD Tersimpan ✓</p> : null}
        <button type="button" className="mop-btn-ghost" style={{ width: "100%", marginTop: 8 }} disabled={!canMutate} onClick={() => setModal({ open: true, role: "recipient" })}>
          Buat TTD Penerima
        </button>
      </div>
      <div className="mop-file">
        <strong>TTD DRIVER</strong>
        {(localSig.driver || driverSig?.signature_storage_path || driverSig?.driver_signature_storage_path) ? <p>TTD Tersimpan ✓</p> : null}
        <button type="button" className="mop-btn-ghost" style={{ width: "100%", marginTop: 8 }} disabled={!canMutate} onClick={() => setModal({ open: true, role: "driver" })}>
          Buat TTD Driver
        </button>
      </div>
      {canMutate && !canWritePhoto && batchPhotoBlockReason && (
        <p className="mop-muted" data-testid="mobile-proof-batch-readiness-hint">
          {batchPhotoBlockReason}
        </p>
      )}
      {err && (
        <div className="mop-error" data-testid="mobile-delivery-error">
          {err}
        </div>
      )}
      <button type="button" className="mop-btn-primary" disabled={!canConfirm || busy || photoBusy} onClick={confirm} data-testid="mobile-confirm-delivered">
        Konfirmasi Terkirim
      </button>
      {picker && canMutate && (
        <div className="mop-sheet-backdrop" data-testid="mobile-photo-picker-sheet" onClick={closePicker}>
          <div className="mop-sheet" role="dialog" aria-modal="true" aria-label={pickerTitle} onClick={(e) => e.stopPropagation()}>
            <h3>{pickerTitle}</h3>
            <button type="button" className="mop-sheet-choice" onClick={() => triggerInput("camera")} data-testid="mobile-photo-camera">
              <Camera size={20} aria-hidden="true" />
              Ambil Foto
            </button>
            <button type="button" className="mop-sheet-choice" onClick={() => triggerInput("gallery")} data-testid="mobile-photo-gallery">
              <ImagePlus size={20} aria-hidden="true" />
              Pilih dari Galeri
            </button>
            <button type="button" className="mop-btn-ghost" style={{ width: "100%", marginTop: 8 }} onClick={closePicker} data-testid="mobile-photo-picker-cancel">
              Batal
            </button>
          </div>
        </div>
      )}
      {preview?.src && (
        <div className="mop-photo-lightbox" data-testid="mobile-delivery-photo-preview" onClick={() => setPreview(null)}>
          <img src={preview.src} alt={preview.alt || "Pratinjau bukti pengiriman"} />
        </div>
      )}
      <MobileSignatureModal
        open={modal.open}
        role={modal.role}
        readOnly={delivered}
        onCancel={() => setModal({ open: false, role: null })}
        onSave={(dataUrl) => {
          setLocalSig((cur) => applyLocalSignature(cur, modal.role, dataUrl));
          setModal({ open: false, role: null });
        }}
      />
    </section>
  );
}
