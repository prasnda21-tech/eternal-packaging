import { useEffect, useLayoutEffect, useRef, useState } from "react";
import api from "../lib/api";
import {
  mapsLinkMessage,
  parseGoogleMapsUrlRecord,
} from "../lib/googleMapsLink";
import {
  applyMapsLinkSelection,
  applyPlacesSelection,
  buildSaveDeliveryOutletPayload,
} from "../lib/outletLocationSelection";
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
  const [mapsLink, setMapsLink] = useState("");
  const [mapsStatus, setMapsStatus] = useState("");
  const [mapsMessage, setMapsMessage] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [provider, setProvider] = useState("");
  const [picked, setPicked] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [suggestBox, setSuggestBox] = useState(null);
  const searchInputRef = useRef(null);

  useEffect(() => {
    if (!open || !row) return;
    setName(row.outlet_name || row.outlet || "");
    setQuery("");
    setMapsLink("");
    setMapsStatus("");
    setMapsMessage("");
    setSuggestions([]);
    setProvider("");
    setErr("");
    if (row.lat != null && row.lng != null) {
      setPicked({
        place_id: row.place_id || row.google_place_id || "",
        selection_key: row.place_id || row.google_place_id || `maps-link:${row.lat},${row.lng}`,
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

  useLayoutEffect(() => {
    if (!open || !suggestions.length) {
      setSuggestBox(null);
      return undefined;
    }
    const syncSuggestBox = () => {
      const input = searchInputRef.current;
      if (!input) {
        setSuggestBox(null);
        return;
      }
      const r = input.getBoundingClientRect();
      setSuggestBox({
        top: Math.round(r.bottom + 4),
        left: Math.round(r.left),
        width: Math.max(Math.round(r.width), 220),
      });
    };
    syncSuggestBox();
    window.addEventListener("resize", syncSuggestBox);
    document.addEventListener("scroll", syncSuggestBox, true);
    return () => {
      window.removeEventListener("resize", syncSuggestBox);
      document.removeEventListener("scroll", syncSuggestBox, true);
    };
  }, [open, suggestions, query]);

  if (!open || !row) return null;

  const selectPlace = async (sug) => {
    setBusy(true);
    setErr("");
    try {
      const d = await api.deliveryPlaceDetails(sug.place_id);
      const p = d.place || {};
      const next = applyPlacesSelection({
        place_id: p.place_id || p.google_place_id || sug.place_id,
        formatted_address: p.formatted_address || sug.formatted_address || sug.label,
        lat: p.lat,
        lng: p.lng,
        name: p.name || "",
      }, name);
      setPicked(next.picked);
      setName(next.name);
      setQuery("");
      setSuggestions([]);
      setMapsStatus("");
      setMapsMessage("");
    } catch (e) {
      setErr(e.response?.data?.detail || "Gagal memuat detail lokasi");
    } finally {
      setBusy(false);
    }
  };

  const applyPastedMapsLink = async () => {
    if (!canEdit || busy) return;
    const inspected = parseGoogleMapsUrlRecord(mapsLink);
    if (inspected.status === "EMPTY") {
      setMapsStatus("");
      setMapsMessage("");
      return;
    }
    if (inspected.status === "OK") {
      const next = applyMapsLinkSelection(inspected, name, picked);
      setPicked(next.picked);
      setMapsStatus("SUCCESS");
      setMapsMessage(mapsLinkMessage("SUCCESS"));
      setErr("");
      return;
    }
    if (inspected.status !== "NEEDS_RESOLVE") {
      setMapsStatus(inspected.status);
      setMapsMessage(mapsLinkMessage(inspected.status));
      return;
    }

    const previous = picked;
    setBusy(true);
    setMapsStatus("RESOLVING");
    setMapsMessage(mapsLinkMessage("RESOLVING"));
    setErr("");
    try {
      const resolved = await api.resolveDeliveryMapsLink(inspected.url);
      const lat = Number(resolved.lat);
      const lng = Number(resolved.lng);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
        setMapsStatus("NO_COORDINATES");
        setMapsMessage(mapsLinkMessage("NO_COORDINATES"));
        setPicked(previous);
        return;
      }
      const next = applyMapsLinkSelection({
        lat,
        lng,
        formatted_address: resolved.formatted_address,
      }, name, previous);
      setPicked(next.picked);
      setMapsStatus("SUCCESS");
      setMapsMessage(mapsLinkMessage("SUCCESS"));
    } catch (e) {
      const detail = e.response?.data?.detail;
      const code = typeof detail === "object" && detail?.code ? detail.code : "RESOLVE_FAILURE";
      setMapsStatus(code);
      setMapsMessage((typeof detail === "object" && detail?.message) || mapsLinkMessage(code));
      setPicked(previous);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!canEdit || !picked || picked.lat == null || picked.lng == null) return;
    setBusy(true);
    setErr("");
    try {
      const saved = await api.saveDeliveryOutlet(buildSaveDeliveryOutletPayload({
        row,
        batchId,
        name,
        picked,
      }));
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
          <label className="outlet-loc-search-field">
            <span>Cari Outlet / Alamat</span>
            <input
              ref={searchInputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              data-testid="outlet-loc-search"
              placeholder="JUS.IN Kelapa Gading atau Jl. Boulevard Raya"
              autoComplete="off"
            />
          </label>
          {provider === "google" && <p className="muted small">Powered by Google</p>}
          {provider === "mock" && <p className="muted small">Pencarian lokasi preview (mock lokal)</p>}
          <p className="outlet-loc-or" data-testid="outlet-loc-or">ATAU</p>
          <label>
            <span>Link Google Maps</span>
            <div className="outlet-maps-link-row">
              <input
                value={mapsLink}
                onChange={(e) => {
                  setMapsLink(e.target.value);
                  if (mapsStatus) {
                    setMapsStatus("");
                    setMapsMessage("");
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    e.stopPropagation();
                    applyPastedMapsLink();
                  }
                }}
                data-testid="outlet-loc-maps-link"
                placeholder="Tempel link Google Maps di sini"
                autoComplete="off"
              />
              <button
                type="button"
                className="ghost"
                onClick={applyPastedMapsLink}
                disabled={!canEdit || busy}
                data-testid="outlet-loc-use-maps-link"
              >
                Gunakan Lokasi
              </button>
            </div>
          </label>
          {mapsMessage && (
            <p
              className={mapsStatus === "SUCCESS" ? "muted small" : "error"}
              data-testid="outlet-loc-maps-status"
            >
              {mapsMessage}
            </p>
          )}
          <div>
            <span className="muted small">Alamat Terpilih</span>
            <p data-testid="outlet-loc-selected">{picked?.formatted_address || "Belum dipilih"}</p>
          </div>
          <OutletLocationMap
            lat={picked?.lat}
            lng={picked?.lng}
            placeKey={picked?.selection_key || picked?.place_id || ""}
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
      {!!suggestions.length && (
        <ul
          className="outlet-place-suggest"
          data-testid="outlet-loc-suggest"
          style={suggestBox || undefined}
          onClick={(e) => e.stopPropagation()}
        >
          {suggestions.map((s) => (
            <li key={s.place_id}>
              <button type="button" onClick={() => selectPlace(s)} data-testid={`outlet-loc-pick-${s.place_id}`}>
                <span className="outlet-place-primary">{s.label}</span>
                {s.formatted_address && s.formatted_address !== s.label ? (
                  <span className="outlet-place-secondary">{s.formatted_address}</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
