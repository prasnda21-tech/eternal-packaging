import { useEffect, useRef, useState } from "react";
import api from "../lib/api";
import { compressImage } from "../lib/imageCompress";
import { formatDisplayDateTime } from "../lib/constants";
import {
  GEO_UNAVAILABLE_MESSAGE,
  captureVisitGeolocation,
  isValidVisitCoordinate,
  selectedVisitPhotoFiles,
  visitMapsUrl,
  visitPhotoUploadError,
} from "../lib/salesActivity";

export function VisitPhotoComposer({ outletId, required, photos, setPhotos, notify }) {
  const fileRef = useRef(null);
  const cameraRef = useRef(null);
  const [geo, setGeo] = useState(null);
  const [busy, setBusy] = useState(false);

  const captureGeo = async () => {
    const next = await captureVisitGeolocation();
    setGeo(next);
    return next;
  };

  const onCaptureLocation = async () => {
    await captureGeo();
  };

  const onPick = async (fileList) => {
    const files = selectedVisitPhotoFiles(fileList);
    if (!files.length) return;
    if (!outletId) {
      notify?.("Pilih outlet terlebih dahulu");
      return;
    }
    setBusy(true);
    let location = geo;
    if (!location || location.status !== "OK") {
      location = await captureGeo();
    }
    try {
      for (const raw of files.slice(0, 8 - photos.length)) {
        if (!raw.type?.startsWith("image/")) {
          notify?.("Hanya file gambar JPEG, PNG, atau WEBP");
          continue;
        }
        const { file } = await compressImage(raw);
        const form = new FormData();
        form.append("file", file, file.name || raw.name || "visit.jpg");
        form.append("outlet_id", outletId);
        if (location?.status === "OK") {
          form.append("latitude", String(location.latitude));
          form.append("longitude", String(location.longitude));
          if (location.accuracy != null) form.append("accuracy", String(location.accuracy));
          form.append("location_status", "OK");
        } else {
          form.append("location_status", location?.status || "UNAVAILABLE");
        }
        const res = await api.uploadSalesActivityVisitPhoto(form);
        const photo = res.photo;
        const localUrl = URL.createObjectURL(file);
        setPhotos((prev) => [...prev, { ...photo, localUrl }]);
      }
    } catch (err) {
      notify?.(visitPhotoUploadError(err));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
      if (cameraRef.current) cameraRef.current.value = "";
    }
  };

  const remove = async (photo) => {
    try {
      if (photo.id) await api.deleteSalesActivityVisitPhoto(photo.id);
      if (photo.localUrl) URL.revokeObjectURL(photo.localUrl);
      setPhotos((prev) => prev.filter((p) => p.id !== photo.id));
    } catch (err) {
      notify?.(err?.response?.data?.detail || err.message || "Gagal hapus foto");
    }
  };

  const geoDenied = geo && geo.status !== "OK";

  return (
    <div className="sa-visit-photo" data-testid="sa-visit-photo-section">
      <div className="sa-action-row">
        <button type="button" className="primary" disabled={busy || !outletId} onClick={() => cameraRef.current?.click()}>
          Ambil Foto
        </button>
        <button type="button" className="ghost" disabled={busy || !outletId} onClick={() => fileRef.current?.click()}>
          Upload Foto
        </button>
        <button type="button" className="ghost" disabled={busy} onClick={onCaptureLocation} data-testid="sa-visit-geo-retry">
          Ambil Lokasi
        </button>
      </div>
      <input ref={cameraRef} className="sa-file-hidden" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" data-testid="sa-visit-photo-camera" onChange={(e) => onPick(e.target.files)} />
      <input ref={fileRef} className="sa-file-hidden" type="file" accept="image/jpeg,image/png,image/webp" multiple data-testid="sa-visit-photo-input" onChange={(e) => onPick(e.target.files)} />
      {geoDenied && (
        <p className="sa-geo-warn" data-testid="sa-visit-geo-denied">{geo.message || GEO_UNAVAILABLE_MESSAGE}</p>
      )}
      {required && <p className="sa-field-hint">Foto Visit dan lokasi wajib untuk jenis Visit ini.</p>}
      {!outletId && <p className="sa-field-hint">Pilih outlet sebelum mengunggah foto.</p>}
      <VisitPhotoList photos={photos} onRemove={remove} />
    </div>
  );
}

export function VisitPhotoList({ photos, onRemove, showUploader = false }) {
  const [preview, setPreview] = useState(null);
  if (!photos?.length) return null;
  return (
    <>
      <div className="sa-photo-grid" data-testid="sa-visit-photo-list">
        {photos.map((photo) => (
          <VisitPhotoCard key={photo.id} photo={photo} onOpen={() => setPreview(photo)} onRemove={onRemove} showUploader={showUploader} />
        ))}
      </div>
      {preview && <VisitPhotoPreview photo={preview} onClose={() => setPreview(null)} />}
    </>
  );
}

function VisitPhotoCard({ photo, onOpen, onRemove, showUploader }) {
  const src = useAuthImage(photo);
  const maps = visitMapsUrl(photo.latitude, photo.longitude);
  return (
    <article className="sa-photo-card" data-testid={`sa-visit-photo-${photo.id}`}>
      <button type="button" className="sa-photo-thumb" onClick={onOpen} data-testid="sa-visit-photo-thumb">
        {src ? <img src={src} alt={photo.original_filename || "Foto visit"} /> : <span className="muted">Foto</span>}
      </button>
      <div className="sa-photo-meta">
        <p>{photo.original_filename || "Foto visit"}</p>
        <p data-testid="sa-visit-photo-time">Visit: {formatDisplayDateTime(photo.visit_at)}</p>
        <p className="muted">Uploaded: {formatDisplayDateTime(photo.uploaded_at)}</p>
        {showUploader && photo.uploaded_by_name ? <p className="muted">{photo.uploaded_by_name}</p> : null}
        {isValidVisitCoordinate(photo.latitude, photo.longitude) ? (
          <>
            <p data-testid="sa-visit-photo-coords">{photo.latitude}, {photo.longitude}{photo.location_accuracy_meters != null ? ` · ±${Math.round(photo.location_accuracy_meters)}m` : ""}</p>
            {maps && <a className="linkish" href={maps} target="_blank" rel="noreferrer" data-testid="sa-visit-maps-link">Buka di Maps</a>}
          </>
        ) : (
          <p className="muted">Lokasi tidak tersedia</p>
        )}
        {onRemove && (
          <button type="button" className="ghost sm" onClick={() => onRemove(photo)}>Hapus foto</button>
        )}
      </div>
    </article>
  );
}

export function VisitPhotoPreview({ photo, onClose }) {
  const src = useAuthImage(photo);
  const maps = visitMapsUrl(photo.latitude, photo.longitude);
  return (
    <div className="sa-photo-modal" role="dialog" data-testid="sa-visit-photo-preview" onClick={onClose}>
      <div className="sa-photo-modal-inner" onClick={(e) => e.stopPropagation()}>
        <div className="sa-tab-head">
          <h3>Foto Visit</h3>
          <button type="button" className="ghost sm" onClick={onClose} aria-label="Tutup">X</button>
        </div>
        {src ? <img className="sa-photo-large" src={src} alt={photo.original_filename || "Foto visit"} /> : <p className="muted">Memuat foto...</p>}
        <p>Visit: {formatDisplayDateTime(photo.visit_at)}</p>
        <p className="muted">Uploaded: {formatDisplayDateTime(photo.uploaded_at)}</p>
        {photo.uploaded_by_name ? <p className="muted">{photo.uploaded_by_name}</p> : null}
        {isValidVisitCoordinate(photo.latitude, photo.longitude) ? (
          <>
            <p>{photo.latitude}, {photo.longitude}{photo.location_accuracy_meters != null ? ` · ±${Math.round(photo.location_accuracy_meters)}m` : ""}</p>
            {maps && <a className="primary" href={maps} target="_blank" rel="noreferrer" data-testid="sa-visit-maps-link">Buka di Maps</a>}
          </>
        ) : (
          <p className="sa-geo-warn">{GEO_UNAVAILABLE_MESSAGE}</p>
        )}
      </div>
    </div>
  );
}

export function HistoricalEvidence({ activity }) {
  const type = String(activity?.evidence_type || "").trim();
  const link = String(activity?.evidence_link || "").trim();
  if (!type && !link) return null;
  return (
    <div className="sa-historical-evidence" data-testid="sa-historical-evidence">
      <h4>Evidence Historis</h4>
      <p className="muted">Data impor lama. Bukan foto visit.</p>
      {type ? <p>Evidence Type: {type}</p> : null}
      {link ? <p>Evidence Link: {/^https?:/i.test(link) ? <a className="linkish" href={link} target="_blank" rel="noreferrer">{link}</a> : link}</p> : null}
    </div>
  );
}

function useAuthImage(photo) {
  const [src, setSrc] = useState(photo?.localUrl || "");
  useEffect(() => {
    if (photo?.localUrl) {
      setSrc(photo.localUrl);
      return undefined;
    }
    if (!photo?.id) return undefined;
    let revoke = "";
    let cancelled = false;
    fetch(api.salesActivityVisitPhotoUrl(photo.id), { credentials: "include" })
      .then((r) => (r.ok ? r.blob() : null))
      .then((blob) => {
        if (!blob || cancelled) return;
        revoke = URL.createObjectURL(blob);
        setSrc(revoke);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [photo?.id, photo?.localUrl]);
  return src;
}
