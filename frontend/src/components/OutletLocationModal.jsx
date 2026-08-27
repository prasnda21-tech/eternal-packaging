import { useEffect, useState } from "react";
import api from "../lib/api";
import OutletLocationMap from "./OutletLocationMap";

export default function OutletLocationModal({
  open,
  row,
  batchId,
  canEdit = true,
  onClose,
  onSaved,
}) {
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [provider, setProvider] = useState("");
  const [picked, setPicked] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!open || !row) return;
    setName(row.outlet_name || row.outlet || "");
    setQuery("");
    setSuggestions([]);
    setProvider("");
    setErr("");
    if (row.lat != null && row.lng != null) {
      setPicked({
        place_id: row.place_id || row.google_place_id || "",
        formatted_address: row.formatted_address || row.address || "",
        lat: row.lat,
        lng: row.lng,
        name: row.outlet_name || "",
      });
    } else {
      setPicked(null);
    }
  }, [open, row]);

  useEffect(() => {
    if (!open || query.trim().length < 2) {
      setSuggestions([]);
      return undefined;
    }
    const t = setTimeout(async () => {
      try {
        const d = await api.searchDeliveryPlaces(query.trim());
        setProvider(d.provider || "");
        setSuggestions(d.suggestions || []);
        setErr("");
      } catch (e) {
        setSuggestions([]);
        setErr(e.response?.data?.detail || "Gagal mencari alamat");
      }
    }, 280);
    return () => clearTimeout(t);
  }, [open, query]);

  if (!open || !row) return null;

  const selectPlace = async (sug) => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.deliveryPlaceDetails(sug.place_id);
      const p = d.place || {};
      setPicked({
        place_id: p.place_id || p.google_place_id || sug.place_id,
        formatted_address: p.formatted_address || sug.formatted_address || sug.label,
        lat: p.lat,
        lng: p.lng,
        name: p.name || "",
      });
      if (!name && p.name) setName(p.name);
      setQuery("");
      setSuggestions([]);
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat detail lokasi");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!canEdit || !picked || picked.lat == null || picked.lng == null) return;
    setBusy(true);
    setErr("");
    try {
      const saved = await api.saveDeliveryOutlet({
        order_id: row.order_id,
        batch_id: batchId || undefined,
        stop_id: row.stop_id,
        name: name || picked.name || row.customer,
        place_id: picked.place_id,
        google_place_id: picked.place_id,
        formatted_address: picked.formatted_address,
        lat: Number(picked.lat),
        lng: Number(picked.lng),
      });
      onSaved?.(saved.outlet, row);
      onClose?.();
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal menyimpan lokasi");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal" role="dialog" aria-modal="true" data-testid="outlet-location-modal" onClick={busy ? undefined : onClose}>
      <div className="modal-inner confirm-action-inner outlet-loc-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>ATUR LOKASI PENGIRIMAN</h3>
        </div>
        <div className="confirm-action-body outlet-loc-form">
          <label>
            <span>Customer</span>
            <input value={row.customer || ""} readOnly data-testid="outlet-loc-customer" />
          </label>
          <label>
            <span>Nama Outlet / Lokasi</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="outlet-loc-name"
              placeholder="Opsional — contoh: Outlet Jakarta"
            />
          </label>
          <label>
            <span>Cari Outlet / Alamat</span>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              data-testid="outlet-loc-search"
              placeholder="JUS.IN Kelapa Gading atau Jl. Boulevard Raya"
              autoComplete="off"
            />
          </label>
          {!!suggestions.length && (
            <ul className="outlet-place-suggest" data-testid="outlet-loc-suggest">
              {suggestions.map((s) => (
                <li key={s.place_id}>
                  <button type="button" onClick={() => selectPlace(s)} data-testid={`outlet-loc-pick-${s.place_id}`}>
                    {s.label}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {provider === "google" && <p className="muted small">Powered by Google</p>}
          {provider === "mock" && <p className="muted small">Pencarian lokasi preview (mock lokal)</p>}
          <div>
            <span className="muted small">Alamat Terpilih</span>
            <p data-testid="outlet-loc-selected">{picked?.formatted_address || "Belum dipilih"}</p>
          </div>
          <OutletLocationMap
            lat={picked?.lat}
            lng={picked?.lng}
            placeKey={picked?.place_id || ""}
            canEdit={canEdit}
            onMove={(nextLat, nextLng) => {
              setPicked((prev) => (prev ? { ...prev, lat: nextLat, lng: nextLng } : prev));
            }}
          />
          {picked?.lat != null && canEdit && (
            <p className="muted small" data-testid="outlet-loc-marker-hint">Klik peta atau geser marker untuk koreksi titik. Koordinat terisi otomatis.</p>
          )}
          {err && <p className="error" data-testid="outlet-loc-error">{err}</p>}
        </div>
        <div className="delete-modal-actions">
          <button type="button" className="ghost" onClick={onClose} disabled={busy} data-testid="outlet-loc-cancel">Batal</button>
          <button
            type="button"
            className="primary"
            onClick={save}
            disabled={!canEdit || busy || !picked || picked.lat == null}
            data-testid="outlet-loc-save"
          >
            {busy ? "Menyimpan…" : "Simpan"}
          </button>
        </div>
      </div>
    </div>
  );
}
