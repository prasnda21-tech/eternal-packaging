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
  confirmDeliveredLoadingBlockReason,
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
  pengirimanSjDisplayLabel,
  proofsForShipment,
  signatureForShipment,
  selectedPengirimanShipmentId,
  deliveryBatchIdentity,
} from "../lib/deliveryProof";
import { deliveryHistoryEntries, displayOrDash, historyTimeLabel } from "../lib/deliveryHistory";
import { newActionId } from "../lib/workflowEntries";
import ConfirmActionModal from "./ConfirmActionModal";
import RescheduleDeliveryModal from "./RescheduleDeliveryModal";
import SignaturePad from "./SignaturePad";

// Multi-foto Bukti Kirim: driver can select/capture multiple photos, review the
// staged list (with per-photo note + remove/replace), then confirm upload.
// Files are compressed client-side, then streamed one-by-one to the backend
// which stores each via the storage service (local filesystem in preview, S3/Emergent in production).
// No business max on photo count per SJ/PO — only min proofs for confirm.
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
  const [proofLoadErrors, setProofLoadErrors] = useState({});
  const [confirmUpload, setConfirmUpload] = useState(null);
  const [confirmDeliver, setConfirmDeliver] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [previewId, setPreviewId] = useState(null);
  // Per-SJ form drafts (name + canvas TTD). Never reuse a sibling SJ's draft/POD name.
  const formDraftsBySjRef = useRef({});
  const formLiveRef = useRef({
    selectionKey,
    recipientName: "",
    signatureDataUrl: null,
    driverSignatureDataUrl: null,
  });
  const activeSelectionKeyRef = useRef(selectionKey);
  const [recipientName, setRecipientName] = useState(() => {
    const initialShip = findDeliveryShipment(order, selectedPengirimanShipmentId(order, urlShipmentId()));
    const scoped = signatureForShipment(order.delivery_recipient_signature, order, initialShip);
    return scoped?.recipient_name || "";
  });
  const [signatureDataUrl, setSignatureDataUrl] = useState(null);
  const [driverSignatureDataUrl, setDriverSignatureDataUrl] = useState(null);
  // Canvas must visibly show draft TTD before gate/confirm may use the data URL.
  const [recipientSigReady, setRecipientSigReady] = useState(false);
  const [driverSigReady, setDriverSigReady] = useState(false);
  const [savingSig, setSavingSig] = useState(false);
  const [sigPadKey, setSigPadKey] = useState(0);
  const [driverPadKey, setDriverPadKey] = useState(0);
  const [deliveryWarehouses, setDeliveryWarehouses] = useState([]);
  const [deliveryWarehouseId, setDeliveryWarehouseId] = useState("");
  const [stockPreflight, setStockPreflight] = useState(null);
  const [stockPreflightErr, setStockPreflightErr] = useState("");
  const [stockPreflightLoading, setStockPreflightLoading] = useState(false);

  useEffect(() => {
    formLiveRef.current = {
      selectionKey,
      recipientName,
      signatureDataUrl,
      driverSignatureDataUrl,
    };
  }, [selectionKey, recipientName, signatureDataUrl, driverSignatureDataUrl]);

  // R2 confirm-delivered requires an explicit warehouse when multiple gudang exist.
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    const branchId = String(order?.branch_id || "").trim();
    const loadWarehouses = typeof api.warehouses === "function"
      ? api.warehouses(branchId ? { branch_id: branchId } : {})
      : Promise.resolve({ items: [] });
    Promise.resolve(loadWarehouses)
      .then((res) => {
        if (cancelled) return;
        const rows = (res?.warehouses || res?.items || res || [])
          .filter((w) => w && w.active !== false && w.id)
          .map((w) => ({
            id: String(w.id),
            code: w.code || "",
            name: w.name || w.code || w.id,
            branch_id: w.branch_id || "",
          }));
        setDeliveryWarehouses(rows);
        setDeliveryWarehouseId((prev) => {
          if (prev && rows.some((r) => r.id === prev)) return prev;
          return rows.length === 1 ? rows[0].id : "";
        });
      })
      .catch(() => {
        if (!cancelled) setDeliveryWarehouses([]);
      });
    return () => { cancelled = true; };
  }, [isOpen, order?.branch_id, order?.id]);

  useEffect(() => {
    const prevKey = activeSelectionKeyRef.current;
    const live = formLiveRef.current;
    if (prevKey && prevKey !== selectionKey) {
      formDraftsBySjRef.current[prevKey] = {
        recipientName: live.recipientName || "",
        signatureDataUrl: live.signatureDataUrl || null,
        driverSignatureDataUrl: live.driverSignatureDataUrl || null,
      };
    }
    activeSelectionKeyRef.current = selectionKey;

    setStaged((prev) => {
      prev.forEach((s) => { if (s.previewUrl) URL.revokeObjectURL(s.previewUrl); });
      return [];
    });
    Object.values(blobCacheRef.current).forEach((u) => { if (u) URL.revokeObjectURL(u); });
    blobCacheRef.current = {};
    setProofBlobs({});
    setProofLoadErrors({});
    setUploading(false);
    setErr("");
    setConfirmUpload(null);
    setConfirmDeliver(null);
    setConfirming(false);
    setDeletingId(null);
    setPreviewId(null);
    setSavingSig(false);
    setSigPadKey((k) => k + 1);
    setDriverPadKey((k) => k + 1);
    setStockPreflight(null);
    setStockPreflightErr("");

    const draft = selectionKey ? formDraftsBySjRef.current[selectionKey] : null;
    const scopedSig = signatureForShipment(order.delivery_recipient_signature, order, activeShipment);
    // Draft data URLs are not "ready" until SignaturePad paints them onto the canvas.
    setRecipientSigReady(false);
    setDriverSigReady(false);
    if (draft) {
      setRecipientName(draft.recipientName || "");
      setSignatureDataUrl(draft.signatureDataUrl || null);
      setDriverSignatureDataUrl(draft.driverSignatureDataUrl || null);
    } else {
      setRecipientName(scopedSig?.recipient_name || "");
      setSignatureDataUrl(null);
      setDriverSignatureDataUrl(null);
    }
  }, [selectionKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const patchActiveDraft = (patch) => {
    const key = activeSelectionKeyRef.current;
    if (!key) return;
    const prev = formDraftsBySjRef.current[key] || {
      recipientName: "",
      signatureDataUrl: null,
      driverSignatureDataUrl: null,
    };
    formDraftsBySjRef.current[key] = { ...prev, ...patch };
  };

  const onRecipientPadChange = (dataUrl) => {
    if (activeSelectionKeyRef.current !== selectionKey) return;
    setSignatureDataUrl(dataUrl || null);
    setRecipientSigReady(!!dataUrl);
    patchActiveDraft({ signatureDataUrl: dataUrl || null });
  };

  const onDriverPadChange = (dataUrl) => {
    if (activeSelectionKeyRef.current !== selectionKey) return;
    setDriverSignatureDataUrl(dataUrl || null);
    setDriverSigReady(!!dataUrl);
    patchActiveDraft({ driverSignatureDataUrl: dataUrl || null });
  };

  const onRecipientRestoreState = (state) => {
    if (activeSelectionKeyRef.current !== selectionKey) return;
    if (state === "ready") {
      setRecipientSigReady(true);
      return;
    }
    if (state === "failed") {
      setRecipientSigReady(false);
      setSignatureDataUrl(null);
      patchActiveDraft({ signatureDataUrl: null });
      return;
    }
    if (state === null && !signatureDataUrl) setRecipientSigReady(false);
  };

  const onDriverRestoreState = (state) => {
    if (activeSelectionKeyRef.current !== selectionKey) return;
    if (state === "ready") {
      setDriverSigReady(true);
      return;
    }
    if (state === "failed") {
      setDriverSigReady(false);
      setDriverSignatureDataUrl(null);
      patchActiveDraft({ driverSignatureDataUrl: null });
      return;
    }
    if (state === null && !driverSignatureDataUrl) setDriverSigReady(false);
  };

  // Hydrate empty input only from the active SJ's saved POD — never a sibling.
  useEffect(() => {
    const scoped = signatureForShipment(order.delivery_recipient_signature, order, activeShipment);
    const n = String(scoped?.recipient_name || "").trim();
    if (!n) return;
    if (activeSelectionKeyRef.current !== selectionKey) return;
    setRecipientName((prev) => (prev && String(prev).trim() ? prev : n));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order.delivery_recipient_signature?.id, order.delivery_recipient_signature?.shipment_id, selectionKey]);

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

  // Server-authoritative shortage preflight when warehouse + SJ are known.
  useEffect(() => {
    if (!isOpen || selectedDelivered || mutationLocked) {
      setStockPreflight(null);
      setStockPreflightErr("");
      return undefined;
    }
    const whId = String(deliveryWarehouseId || "").trim();
    const shipId = String(selectedShipmentKey || "").trim();
    if (!whId || !shipId || !order?.id) {
      setStockPreflight(null);
      setStockPreflightErr("");
      return undefined;
    }
    if (typeof api.deliveryStockPreflight !== "function") {
      setStockPreflight(null);
      setStockPreflightErr("");
      return undefined;
    }
    let cancelled = false;
    setStockPreflightLoading(true);
    setStockPreflightErr("");
    Promise.resolve(api.deliveryStockPreflight(order.id, {
      shipment_id: shipId,
      warehouse_id: whId,
      branch_id: order.branch_id || undefined,
    }))
      .then((res) => {
        if (cancelled) return;
        setStockPreflight(res);
      })
      .catch((ex) => {
        if (cancelled) return;
        const det = ex?.response?.data?.detail;
        setStockPreflight(null);
        setStockPreflightErr(
          typeof det === "object" ? (det.message || JSON.stringify(det)) : (det || "Gagal cek stok gudang"),
        );
      })
      .finally(() => {
        if (!cancelled) setStockPreflightLoading(false);
      });
    return () => { cancelled = true; };
  }, [
    isOpen,
    order?.id,
    order?.branch_id,
    deliveryWarehouseId,
    selectedShipmentKey,
    selectedDelivered,
    mutationLocked,
  ]);

  const canWritePhoto = canUpload && shipmentResolved && batchPhotoReady && !mutationLocked;
  const canDelete = hasPerm("delivery.update");
  const canReschedule = hasPerm("delivery.reschedule") || hasPerm("delivery.reschedule.set_date");
  const canSetDate = hasPerm("delivery.reschedule.set_date");
  const proofs = ((sjOptions.length > 1 && !shipmentResolved) || unmatchedRequested)
    ? []
    : proofsForShipment(order, activeShipment);
  const orderSig = signatureForShipment(order.delivery_recipient_signature, order, activeShipment);
  const driverSig = signatureForShipment(order.delivery_driver_signature, order, activeShipment);
  const driverDisplayName = authoritativeDriverDisplayName(order, user);
  const history = deliveryHistoryEntries(order);
  const historyProofIds = [...new Set(history.flatMap((row) => (
    (row.proof_ids && row.proof_ids.length)
      ? row.proof_ids
      : (row.photos || []).map((proof) => proof?.id).filter(Boolean)
  )))];
  const proofLoadCandidates = (() => {
    const inventory = new Map();
    for (const proof of order.delivery_proofs || []) {
      if (proof?.id) inventory.set(proof.id, proof);
    }
    const wanted = new Map();
    for (const proof of proofs) {
      if (proof?.id) wanted.set(proof.id, proof);
    }
    for (const row of history) {
      for (const proof of row.photos || []) {
        if (proof?.id) wanted.set(proof.id, proof);
      }
      for (const proofId of row.proof_ids || []) {
        const proof = inventory.get(proofId);
        if (proof?.id) wanted.set(proof.id, proof);
      }
    }
    return [...wanted.values()].filter((proof) => proof?.id && proof?.storage_path);
  })();
  const proofLoadKey = proofLoadCandidates
    .map((proof) => `${proof.id}:${proof.storage_path}`)
    .sort()
    .concat(historyProofIds.map((proofId) => `${proofId}:history`))
    .join("|");
  const readyCount = confirmableProofCount(staged);
  const doneCount = staged.filter((s) => s.status === "done").length;
  // Only visibly-restored (or freshly drawn) canvas TTD may satisfy the gate — never a hidden draft.
  const visibleRecipientSig = recipientSigReady ? signatureDataUrl : null;
  const visibleDriverSig = driverSigReady ? driverSignatureDataUrl : null;
  // Button APPEARS after existing 2-photo rule; submit still needs name + both signatures.
  const deliverButtonVisible = canUpload && shipmentResolved && canConfirmDelivered(proofs) && !mutationLocked;
  const deliverEnabled = deliverButtonVisible && canSubmitConfirmDelivered(
    proofs, recipientName, visibleRecipientSig, orderSig, visibleDriverSig, driverSig
  );
  const deliverBlockReason = deliverButtonVisible
    ? (
      confirmDeliveredLoadingBlockReason(order)
      || confirmDeliveredBlockReason(
        proofs, recipientName, visibleRecipientSig, orderSig, visibleDriverSig, driverSig
      )
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
      const loadableIds = new Set(proofLoadCandidates.map((proof) => proof.id));
      const failures = Object.fromEntries(
        historyProofIds.filter((proofId) => !loadableIds.has(proofId)).map((proofId) => [proofId, true])
      );
      for (const p of proofLoadCandidates) {
        if (next[p.id]) continue;
        try {
          const resp = await fetch(fileUrl(p.storage_path), { credentials: "include" });
          if (!resp.ok) {
            failures[p.id] = true;
            continue;
          }
          const blob = await resp.blob();
          if (cancelled) return;
          next[p.id] = URL.createObjectURL(blob);
          blobCacheRef.current = next;
        } catch {
          failures[p.id] = true;
        }
      }
      if (!cancelled) {
        setProofBlobs({ ...blobCacheRef.current });
        setProofLoadErrors(failures);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, proofLoadKey, selectionKey]);

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

    // Compress each and add to staged (streaming so UI updates progressively)
    for (const raw of files) {
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
    const lockedShipId = selectedShipmentKey;
    const lockedSelectionKey = selectionKey;
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
        }, lockedShipId);
        latestOrder = res.order;
        // Always push order update (proofs are server-authoritative), but only mutate
        // staged UI when the user is still on the same SJ.
        onUpdated(res.order);
        if (activeSelectionKeyRef.current === lockedSelectionKey) {
          setStaged((prev) => prev.map((x) => x.id === s.id ? { ...x, status: "done", progress: 100 } : x));
        }
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
    const blocked = confirmDeliveredLoadingBlockReason(order) || confirmDeliveredBlockReason(
      proofs, recipientName, visibleRecipientSig, orderSig, visibleDriverSig, driverSig
    );
    if (blocked) {
      setErr(blocked);
      return;
    }
    if (deliveryWarehouses.length > 0 && !String(deliveryWarehouseId || "").trim()) {
      setErr("Pilih gudang stok sebelum Konfirmasi Terkirim.");
      return;
    }
    if (!selectedShipmentKey) {
      setErr("Pilih Surat Jalan terlebih dahulu.");
      return;
    }
    setErr("");
    // Snapshot active SJ identity + visibly-ready form values so a later SJ switch cannot retarget confirm.
    setConfirmDeliver({
      count: proofs.length,
      time: formatDisplayDateTime(new Date().toISOString()),
      actionId: newActionId(),
      shipmentId: selectedShipmentKey,
      selectionKey,
      recipientName: (recipientName || orderSig?.recipient_name || "").trim(),
      driverName: driverDisplayName,
      warehouseId: String(deliveryWarehouseId || "").trim(),
      signatureDataUrl: visibleRecipientSig,
      driverSignatureDataUrl: visibleDriverSig,
    });
  };

  const ensureSignaturesSaved = async ({
    shipmentId,
    selectionKey: lockedSelectionKey,
    recipientName: lockedName,
    signatureDataUrl: lockedRecipientSig,
    driverSignatureDataUrl: lockedDriverSig,
    orderSig: lockedOrderSig,
  } = {}) => {
    const shipId = String(shipmentId || selectedShipmentKey || "").trim();
    if (!shipId) {
      throw new Error("Pilih Surat Jalan terlebih dahulu.");
    }
    const name = String(lockedName || recipientName || lockedOrderSig?.recipient_name || orderSig?.recipient_name || "").trim();
    const recipientCanvas = lockedRecipientSig !== undefined ? lockedRecipientSig : signatureDataUrl;
    const driverCanvas = lockedDriverSig !== undefined ? lockedDriverSig : driverSignatureDataUrl;
    const scopedSig = lockedOrderSig || orderSig;
    let latest = order;
    const stillActive = () => (
      !lockedSelectionKey || activeSelectionKeyRef.current === lockedSelectionKey
    );
    const recipientImageSaved = scopedSig && scopedSig.id && scopedSig.frozen !== true
      && (scopedSig.signature_storage_path || scopedSig.recipient_signed_at);
    if (!(recipientImageSaved && (!recipientCanvas || scopedSig.recipient_name === name))) {
      if (!recipientCanvas && !recipientImageSaved) {
        throw new Error("Tanda tangan penerima wajib sebelum Konfirmasi Terkirim.");
      }
      if (recipientCanvas) {
        setSavingSig(true);
        try {
          const res = await api.uploadDeliveryRecipientSignature(latest.id, {
            recipientName: name,
            signatureDataUrl: recipientCanvas,
            shipmentId: shipId,
          });
          latest = res.order;
          if (stillActive()) onUpdated(res.order);
        } finally {
          setSavingSig(false);
        }
      }
    }
    const driverShip = findDeliveryShipment(latest, shipId) || activeShipment;
    const driverScoped = signatureForShipment(latest.delivery_driver_signature, latest, driverShip);
    const driverOk = !!(driverScoped && driverScoped.id && driverScoped.frozen !== true
      && (driverScoped.signature_storage_path
        || driverScoped.driver_signature_storage_path
        || driverScoped.driver_signed_at));
    if (!driverOk || driverCanvas) {
      if (!driverCanvas && !driverOk) {
        throw new Error("Tanda tangan driver/pengirim wajib sebelum Konfirmasi Terkirim.");
      }
      if (driverCanvas) {
        setSavingSig(true);
        try {
          const res = await api.uploadDeliveryDriverSignature(latest.id, {
            signatureDataUrl: driverCanvas,
            shipmentId: shipId,
          });
          latest = res.order;
          if (stillActive()) onUpdated(res.order);
        } finally {
          setSavingSig(false);
        }
      }
    }
    return latest;
  };

  const executeDeliver = async () => {
    if (!confirmDeliver || confirming || uploading || savingSig) return;
    const shipId = String(confirmDeliver.shipmentId || selectedShipmentKey || "").trim();
    if (!shipId) {
      setErr("Pilih Surat Jalan terlebih dahulu.");
      return;
    }
    const lockedSelectionKey = confirmDeliver.selectionKey || selectionKey;
    const lockedName = String(confirmDeliver.recipientName || "").trim();
    setConfirming(true);
    setErr("");
    try {
      await ensureSignaturesSaved({
        shipmentId: shipId,
        selectionKey: lockedSelectionKey,
        recipientName: lockedName,
        signatureDataUrl: confirmDeliver.signatureDataUrl,
        driverSignatureDataUrl: confirmDeliver.driverSignatureDataUrl,
        orderSig: signatureForShipment(
          order.delivery_recipient_signature,
          order,
          findDeliveryShipment(order, shipId) || activeShipment,
        ),
      });
      const warehouseId = String(
        confirmDeliver.warehouseId || deliveryWarehouseId || ""
      ).trim();
      const selectedWh = deliveryWarehouses.find((w) => w.id === warehouseId);
      const res = await api.confirmDelivered(order.id, {
        action_id: confirmDeliver.actionId,
        shipment_id: shipId,
        recipient_name: lockedName,
        ...(warehouseId ? {
          warehouse_id: warehouseId,
          branch_id: selectedWh?.branch_id || order.branch_id || undefined,
        } : {}),
      });
      if (activeSelectionKeyRef.current === lockedSelectionKey) {
        onUpdated(res.order);
        setConfirmDeliver(null);
        setSignatureDataUrl(null);
        setDriverSignatureDataUrl(null);
        // Clear draft for confirmed SJ so reload uses server POD name.
        if (lockedSelectionKey) delete formDraftsBySjRef.current[lockedSelectionKey];
      } else {
        onUpdated(res.order);
        setConfirmDeliver(null);
        if (lockedSelectionKey) delete formDraftsBySjRef.current[lockedSelectionKey];
      }
    } catch (ex) {
      setErr(proofUploadUserError(ex));
    } finally {
      setConfirming(false);
    }
  };

  const clearSignature = async () => {
    setSignatureDataUrl(null);
    setRecipientSigReady(false);
    patchActiveDraft({ signatureDataUrl: null });
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
    setDriverSigReady(false);
    patchActiveDraft({ driverSignatureDataUrl: null });
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
          <span className="muted small" data-testid="proof-stored-count">{proofs.length} foto tersimpan</span>
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
                  {pengirimanSjDisplayLabel(opt, { includeDelivered: true })}
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
              <div
                data-testid="pengiriman-selected-sj-id"
                data-shipment-id={activeSjOption.id}
                className="muted small"
              >
                {activeSjOption.date ? `Tanggal: ${activeSjOption.date}` : ""}
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
                      onClick={() => { if (proofBlobs[pid]) setPreviewId(pid); }}
                      disabled={Boolean(proofLoadErrors[pid])}
                      data-testid={`delivery-history-photo-${i}-${pid}`}
                    >
                      {proofBlobs[pid] ? (
                        <img src={proofBlobs[pid]} alt="Bukti foto pengiriman" />
                      ) : proofLoadErrors[pid] ? (
                        <span className="delivery-history-proof-state">Foto tidak tersedia</span>
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
                <img
                  src={proofBlobs[p.id]}
                  alt={p.original_filename}
                  loading="lazy"
                  onClick={() => setPreviewId(p.id)}
                  data-testid={`proof-thumb-${p.id}`}
                />
              ) : proofLoadErrors[p.id] ? (
                <div className="proof-loading proof-unavailable">Foto tidak tersedia</div>
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
              disabled={uploading || !canWritePhoto}
              data-testid="proof-camera-button"
            >
              <Camera size={16} /> Ambil foto (kamera)
            </button>
            <button
              type="button"
              className="ghost"
              onClick={pickGallery}
              disabled={uploading || !canWritePhoto}
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
                value={signatureDataUrl}
                onChange={onRecipientPadChange}
                onRestoreState={onRecipientRestoreState}
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
                  value={driverSignatureDataUrl}
                  onChange={onDriverPadChange}
                  onRestoreState={onDriverRestoreState}
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

          {deliverButtonVisible && deliveryWarehouses.length > 0 && (
            <label className="field" style={{ display: "block", marginTop: 12 }} data-testid="confirm-delivered-warehouse-field">
              <span className="muted small">Gudang stok</span>
              <select
                value={deliveryWarehouseId}
                onChange={(e) => setDeliveryWarehouseId(e.target.value)}
                disabled={confirming || uploading || savingSig}
                data-testid="confirm-delivered-warehouse"
                style={{ display: "block", width: "100%", maxWidth: 360, marginTop: 4 }}
              >
                <option value="">Pilih gudang…</option>
                {deliveryWarehouses.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.code ? `${w.code} — ${w.name}` : w.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {deliverButtonVisible && stockPreflightLoading && (
            <p className="muted small" data-testid="delivery-stock-preflight-loading">
              Mengecek saldo buku gudang…
            </p>
          )}
          {deliverButtonVisible && stockPreflightErr && (
            <div className="signal-strip" role="status" data-testid="delivery-stock-preflight-error">
              <div><AlertCircle size={16} /><span>{stockPreflightErr}</span></div>
            </div>
          )}
          {deliverButtonVisible
            && (stockPreflight?.r2_authority_active || stockPreflight?.warehouse_stock_standalone_active)
            && Array.isArray(stockPreflight.stock_warnings) && (
            <div
              className={`signal-strip${stockPreflight.has_shortage_warning ? "" : " ok"}`}
              role="status"
              data-testid="delivery-stock-shortage-warning"
              style={{ marginTop: 10, display: "block" }}
            >
              {stockPreflight.has_shortage_warning ? (
                <>
                  <p style={{ margin: "0 0 6px", fontWeight: 600 }}>
                    Peringatan kekurangan stok buku (tidak memblokir pengiriman)
                  </p>
                  {(stockPreflight.stock_warnings || []).filter((w) => w.has_shortage).map((w) => (
                    <div
                      key={`${w.master_item_id}-${w.warehouse_id}`}
                      data-testid={`delivery-stock-warning-row-${w.master_item_id}`}
                      style={{ fontSize: 13, marginBottom: 4 }}
                    >
                      <div>
                        Gudang: <strong>{w.warehouse_code ? `${w.warehouse_code} — ${w.warehouse_name}` : (w.warehouse_name || "—")}</strong>
                      </div>
                      <div>
                        Saldo buku saat ini: <strong>{w.stok_tercatat}</strong>
                        {" · "}Qty kirim: <strong>{w.kebutuhan}</strong>
                        {" · "}Kekurangan: <strong>{w.kekurangan}</strong>
                        {" · "}Saldo buku proyeksi: <strong>{w.projected_book}</strong>
                      </div>
                      {w.baseline_status === "unreconciled" && (
                        <div className="muted small">
                          {w.baseline_label || "Saldo awal belum direkonsiliasi"}
                          {w.stok_tercatat_uncertain ? " — saldo belum terverifikasi" : ""}
                        </div>
                      )}
                    </div>
                  ))}
                </>
              ) : (
                <p style={{ margin: 0 }} data-testid="delivery-stock-sufficient">
                  Saldo buku gudang cukup untuk qty Surat Jalan ini.
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
            Upload foto tidak menandai Terkirim. Minimal {MIN_DELIVERY_PROOFS} foto untuk Surat Jalan yang dipilih, lalu isi nama penerima + TTD penerima + TTD driver, kemudian Konfirmasi Terkirim.
            {" "}Tidak ada batas maksimum jumlah foto. Foto dikompres otomatis (~1600px, ~1MB) sebelum dikirim ke storage.
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
            <p className="muted small" data-testid="confirm-warehouse-name">
              Gudang stok:{" "}
              {(() => {
                const wh = deliveryWarehouses.find((w) => w.id === (confirmDeliver.warehouseId || deliveryWarehouseId));
                return wh ? (wh.code ? `${wh.code} — ${wh.name}` : wh.name) : "—";
              })()}
            </p>
            {stockPreflight?.has_shortage_warning && (
              <div data-testid="confirm-modal-stock-warning" style={{ marginTop: 8, fontSize: 13 }}>
                <strong>Peringatan kekurangan stok buku</strong>
                {(stockPreflight.stock_warnings || []).filter((w) => w.has_shortage).map((w) => (
                  <p key={w.master_item_id} className="muted small" style={{ margin: "4px 0 0" }}>
                    {w.warehouse_code || "Gudang"}: buku {w.stok_tercatat} → kirim {w.kebutuhan}
                    {" "}(kurang {w.kekurangan}, proyeksi {w.projected_book}). Tidak memblokir.
                  </p>
                ))}
              </div>
            )}
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
